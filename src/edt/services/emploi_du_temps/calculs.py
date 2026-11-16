"""Préparer, lancer et lire un calcul (onglet « Générer »), en langage courant.

Au-dessus de `edt.services.generation` (qui orchestre le fil de calcul),
ce module ajoute ce que l'écran doit dire :

- le **récapitulatif** de ce qui va être placé (besoins, séances, groupes,
  enseignants, salles) ;
- les **vérifications préalables** du solveur
  (`raccords.pre_verifier_semestre` : charges, salles, séances sans place
  possible, verrous contradictoires, besoins impossibles à déplier),
  groupées par gravité : une erreur empêche de lancer, un avertissement non ;
- les **règles** et leur **niveau** (`regles_proposees`) : nécessaire,
  important ou facultatif pour chaque règle modifiable, les six règles qui
  définissent le problème restant toujours nécessaires ; préremplis avec
  les niveaux du dernier calcul du semestre (`niveaux_proposes`), lus dans
  les champs `niveau_<CODE>` (`lire_niveaux`). L'écran ne propose pas
  d'ignorer une règle ; un calcul enregistré qui en ignorait reste lisible ;
- les **règles personnalisées** actives du semestre
  (`bloc_regles_du_semestre`, voir `regles_personnalisees`) : un niveau par
  règle pour ce calcul ou « ignorer pour ce calcul », champs
  `niveau_regle_<id>` et `ignorer_regle_<id>`, transmis aux
  pré-vérifications et au moteur (`OptionsSolveur.niveaux_regles`) ; une
  relance reprend ceux du calcul d'origine (`SuiviCalcul.niveaux_regles`) ;
- la **pause déjeuner** à trois modes (`dejeuner_mode`) : horaires
  déterminés (plage réservée de X à Y), pause libre bornée (au moins N
  minutes d'un seul tenant entre X et Y) ou pas de pause ; un choix qui
  diffère des paramètres y est enregistré au lancement, avant le calcul
  (`appliquer_dejeuner`) ;
- les **options** : temps limite court ou long, recherche d'un emploi du
  temps plus confortable (objectif), **autre solution** (nouvelle graine,
  sans partir des placements de la version active) ;
- le **résultat** d'un calcul en phrases : succès (séances placées,
  séances datées, replacées et à programmer avec leur raison,
  avertissements, indicateurs, bilan de qualité du moteur), **arrêté avec
  la meilleure solution conservée** (un succès), **refusé** par les
  vérifications préalables (avec la liste), impossible (prouvé par la
  recherche), pas trouvé dans le temps imparti, arrêté sans solution,
  erreur (identifiant du calcul et nom du journal).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import random
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.db.moteur import transaction_immediate
from edt.domaine.contraintes import (
    CATALOGUE,
    CodeContrainte,
    Niveau,
    Niveaux,
    niveaux_par_defaut,
    normaliser_niveaux,
)
from edt.domaine.enumerations import EtatCalcul, Gravite, OrigineOccurrence, StatutOccurrence
from edt.domaine.heures import minutes_vers_hhmm, minutes_vers_texte, texte_vers_minutes
from edt.domaine.parametres import (
    DEJEUNER_AUCUN,
    DEJEUNER_FIXE,
    DEJEUNER_LIBRE,
    MODES_DEJEUNER,
    ValeurParametre,
    defauts_dejeuner,
)
from edt.domaine.parametres import definition as definition_parametre
from edt.erreurs import (
    ErreurCalculEnCours,
    ErreurDonneesInvalides,
    ErreurEDT,
    ErreurIntrouvable,
    ErreurRegleMetier,
)
from edt.services import generation
from edt.services.emploi_du_temps import (
    memoire,
    presentation,
    raccords,
    regles_personnalisees,
)
from edt.services.emploi_du_temps.identifiants import (
    apercu,
    entier_ecrit,
    obtenir,
)
from edt.services.emploi_du_temps.versions import (
    ResumeSemestre,
    annee_du_semestre,
    exiger_semestre,
)
from edt.services.instantane import charger_instantane
from edt.services.parametres import ecrire_parametres, lire_parametres
from edt.solveur.contrat import OptionsSolveur
from edt.verification.contrat import Anomalie

journal = logging.getLogger(__name__)

_GRAINE_MAX = 2**31 - 1
_hasard = random.SystemRandom()


class Issue:
    """Issues d'un calcul telles que l'écran les distingue."""

    EN_COURS = "en_cours"
    REUSSI = "reussi"
    REFUSE = "refuse"
    IMPOSSIBLE = "impossible"
    TEMPS_DEPASSE = "temps_depasse"
    ARRETE = "arrete"
    ERREUR = "erreur"


_ISSUE_PAR_ETAT = {
    EtatCalcul.EN_COURS: Issue.EN_COURS,
    EtatCalcul.REUSSI: Issue.REUSSI,
    EtatCalcul.INFAISABLE: Issue.IMPOSSIBLE,
    EtatCalcul.TEMPS_DEPASSE: Issue.TEMPS_DEPASSE,
    EtatCalcul.ARRETE: Issue.ARRETE,
    EtatCalcul.ERREUR: Issue.ERREUR,
}


PREFIXE_NIVEAU = "niveau_"
"""Préfixe des champs qui portent le niveau d'une règle : `niveau_PAUSE=facultatif`."""

NIVEAUX_PROPOSES = tuple(Niveau)
"""Niveaux proposés pour une règle modifiable, du plus exigeant au moins exigeant."""

NOTES_DES_REGLES = {
    CodeContrainte.DEJEUNER: "Ne s'applique pas sans pause déjeuner (voir « Pause déjeuner »).",
}
"""Précision affichée sous certaines règles du tableau."""

CHAMP_MODE_DEJEUNER = "dejeuner"
CHAMP_DEBUT_DEJEUNER = "dejeuner_debut"
CHAMP_FIN_DEJEUNER = "dejeuner_fin"
CHAMP_DUREE_DEJEUNER = "dejeuner_duree"
_CHAMP_DU_PARAMETRE = {
    "dejeuner_debut_min": CHAMP_DEBUT_DEJEUNER,
    "dejeuner_fin_min": CHAMP_FIN_DEJEUNER,
    "dejeuner_duree_min": CHAMP_DUREE_DEJEUNER,
}
"""Champ du formulaire qui porte chaque paramètre du déjeuner (pour y afficher son message)."""

LIBELLES_DEJEUNER = dict(MODES_DEJEUNER)
"""Libellé de chaque mode, celui du paramètre `dejeuner_mode` (comme l'écran des paramètres)."""

BORNES_DUREE_DEJEUNER = (
    definition_parametre("dejeuner_duree_min").minimum,
    definition_parametre("dejeuner_duree_min").maximum,
)
"""Durée minimale permise pour la pause libre (minutes), bornes du paramètre."""


@dataclass(frozen=True)
class RegleProposee:
    """Une ligne du tableau « Règles » : une règle du catalogue et son niveau.

    - `niveau` : niveau proposé (dernier calcul, saisie réaffichée ou défaut) ;
    - `niveau_defaut` : niveau du catalogue, signalé discrètement ;
    - `modifiable` : faux pour une règle qui définit le problème, toujours
      nécessaire et sans choix ;
    - `note` : précision sur la règle (la pause déjeuner).
    """

    code: str
    libelle: str
    description: str
    niveau: Niveau
    niveau_defaut: Niveau
    modifiable: bool
    note: str | None = None

    @property
    def champ(self) -> str:
        """Nom du champ du formulaire : `niveau_<CODE>`."""
        return PREFIXE_NIVEAU + self.code


@dataclass(frozen=True)
class Recapitulatif:
    """Ce qu'un calcul du semestre aurait à placer.

    `erreur` est renseignée quand les données du semestre ne peuvent pas
    être lues telles quelles (le message dit quoi corriger).
    """

    semestre: ResumeSemestre
    nb_besoins: int = 0
    nb_seances: int = 0
    nb_groupes: int = 0
    nb_enseignants: int = 0
    nb_salles: int = 0
    problemes: tuple[tuple[str, str], ...] = ()
    erreur: str | None = None


@dataclass(frozen=True)
class PreVerification:
    """Vérifications préalables d'un calcul, groupées par gravité.

    `problemes` : besoins impossibles à planifier tels qu'ils sont saisis,
    dont la contrainte concernée est active (ils empêchent le calcul).
    `motif` : pourquoi les pré-vérifications du solveur n'ont pas tourné.
    """

    problemes: tuple[str, ...] = ()
    erreurs: tuple[Anomalie, ...] = ()
    avertissements: tuple[Anomalie, ...] = ()
    informations: tuple[Anomalie, ...] = ()
    motif: str | None = None
    niveau_motif: str = "attention"

    @property
    def bloquante(self) -> bool:
        """Vrai si le calcul ne doit pas être lancé."""
        return bool(self.problemes or self.erreurs)

    @property
    def rien_a_placer(self) -> bool:
        """Vrai si le seul point bloquant est un semestre sans séance à placer (rien n'est faux)."""
        messages = [*self.problemes, *(anomalie.message for anomalie in self.erreurs)]
        return bool(messages) and all(
            message in (MESSAGE_AUCUN_BESOIN, generation.MESSAGE_SANS_SEANCE)
            for message in messages
        )

    @property
    def vide(self) -> bool:
        """Vrai si rien n'est à signaler et que les pré-vérifications ont tourné."""
        return not (
            self.problemes or self.erreurs or self.avertissements or self.informations or self.motif
        )


MESSAGE_AUCUN_BESOIN = (
    "Ce semestre n'a encore aucun besoin d'enseignement : il n'y a rien à placer. Saisir ou "
    "importer d'abord les besoins."
)


class ErreurPreVerification(ErreurRegleMetier):
    """Le calcul n'est pas lancé : une vérification préalable a relevé une erreur."""

    def __init__(self, pre_verification: PreVerification) -> None:
        details = [*pre_verification.problemes, *(a.message for a in pre_verification.erreurs)]
        super().__init__(
            "Le calcul n'a pas été lancé : des vérifications préalables empêchent de trouver un "
            "emploi du temps. Corriger les points ci-dessous, puis relancer.",
            details,
        )
        self.pre_verification = pre_verification


@dataclass(frozen=True)
class ChoixDejeuner:
    """Bloc « Pause déjeuner » du formulaire : mode, heures « HH:MM », durée minimale.

    `mode` : `DEJEUNER_FIXE`, `DEJEUNER_LIBRE` ou `DEJEUNER_AUCUN`. Heures et
    durée sont des textes : celles des paramètres à l'affichage, celles
    saisies à l'envoi (réaffichées telles quelles si elles sont refusées).
    """

    mode: str
    debut: str
    fin: str
    duree: str


class ErreurSaisieDejeuner(ErreurDonneesInvalides):
    """Pause déjeuner mal saisie : rien n'est enregistré et le calcul n'est pas lancé.

    `champs` associe à chaque champ fautif du formulaire son message ;
    `saisie` est ce qui a été reçu (à réafficher), `None` si le choix est illisible.
    """

    def __init__(self, champs: dict[str, str], saisie: ChoixDejeuner | None) -> None:
        self.champs = dict(champs)
        self.saisie = saisie
        super().__init__(
            "Le calcul n'a pas été lancé : la pause déjeuner est à corriger (voir sous les "
            "champs concernés).",
            tuple(self.champs.values()),
        )


class ErreurSaisieNiveaux(ErreurDonneesInvalides):
    """Niveaux de règles illisibles ou interdits : rien n'est lancé ni enregistré.

    `niveaux` : niveaux complets à réafficher (les choix valides sont gardés,
    les autres reviennent au niveau par défaut).
    """

    def __init__(self, message: str, niveaux: Niveaux) -> None:
        super().__init__(f"Le calcul n'a pas été lancé : {message[:1].lower()}{message[1:]}")
        self.niveaux = niveaux


@dataclass(frozen=True)
class OptionsFormulaire:
    """Valeurs par défaut du formulaire de lancement."""

    optimiser: bool
    temps_court_s: int
    temps_long_s: int

    @property
    def temps_court_texte(self) -> str:
        """Temps limite court lisible (« 5 minutes » avec les réglages par défaut)."""
        return presentation.duree_secondes(self.temps_court_s)

    @property
    def temps_long_texte(self) -> str:
        """Temps limite long lisible (« 15 minutes » avec les réglages par défaut)."""
        return presentation.duree_secondes(self.temps_long_s)


@dataclass(frozen=True)
class SeanceNonPlacee:
    """Une séance datée « à programmer » à la fin d'un calcul : quoi, quand, pourquoi."""

    besoin: str
    date: dt.date | None
    horaire: str
    raison: str


@dataclass(frozen=True)
class BilanDatees:
    """Bilan des séances datées produites à la fin d'un calcul réussi.

    `erreur` : l'instanciation a échoué (la semaine type est enregistrée, les
    séances datées peuvent être recalculées depuis l'emploi du temps).
    """

    nb_occurrences: int = 0
    nb_planifiees: int = 0
    nb_replacees: int = 0
    nb_a_programmer: int = 0
    a_programmer: tuple[SeanceNonPlacee, ...] = ()
    erreur: bool = False

    @classmethod
    def lire(cls, donnees: object) -> BilanDatees | None:
        """Bilan lu dans `statistiques["instanciation"]` (`RapportInstanciation.vers_dict()`)."""
        if not isinstance(donnees, dict):
            return None
        if donnees.get("erreur"):
            return cls(erreur=True)
        a_programmer = []
        for entree in donnees.get("a_programmer") or ():
            date = entree.get("date_origine")
            a_programmer.append(
                SeanceNonPlacee(
                    besoin=str(entree.get("besoin", "")),
                    date=dt.date.fromisoformat(date) if date else None,
                    horaire=str(entree.get("horaire", "")),
                    raison=str(entree.get("raison", "")),
                )
            )
        return cls(
            nb_occurrences=int(donnees.get("nb_occurrences", 0)),
            nb_planifiees=int(donnees.get("nb_planifiees", 0)),
            nb_replacees=int(donnees.get("nb_replacees", 0)),
            nb_a_programmer=int(donnees.get("nb_a_programmer", 0)),
            a_programmer=tuple(a_programmer),
        )


@dataclass(frozen=True)
class ResultatCalcul:
    """Un calcul, raconté pour l'écran.

    - `avertissements` : situations tendues relevées avant ou pendant la
      recherche (le calcul a pu réussir malgré elles) ;
    - `impossibilites` : raisons d'un refus par les vérifications préalables ;
    - `nb_seances` : séances de la semaine type placées (succès) ;
    - `datees` : bilan des séances datées (succès) ;
    - `contraintes_desactivees` : règles ignorées, présentes seulement dans un
      calcul enregistré sans niveaux de règles ;
    - `regles` : règles dont le niveau s'écarte du défaut, en phrases
      (`presentation.libelles_niveaux`), vide si tout est par défaut ;
    - `entorses` : entorses aux règles importantes ou facultatives, une
      phrase par niveau (`statistiques["entorses"]`, fourni par le moteur) ;
    - `regles_personnalisees` : règles personnalisées prises à un autre niveau
      que celui qu'elles avaient au calcul, ou ignorées (`SuiviCalcul.regles`) ;
      `remarque_regles` dit quand la comparaison n'a pu se faire qu'au niveau
      actuel des règles ;
    - `entorses_regles` : entorses par règle personnalisée
      (`statistiques["entorses_regles"]`) ;
    - `qualite` : bilan de qualité du moteur, en phrases (trous, samedis,
      amélioration possible), sans ce que les compteurs disent déjà ;
    - `arrete_avec_solution` : l'utilisateur a arrêté le calcul et la
      meilleure solution trouvée a été conservée (état « réussi »).
    """

    suivi: generation.SuiviCalcul
    semestre: ResumeSemestre
    issue: str
    titre: str
    phrase: str
    precision: str | None
    contraintes_desactivees: tuple[str, ...]
    temps_texte: str
    limite_texte: str
    relance_texte: str
    longue: bool
    optimiser: bool
    version_active: bool
    controle: raccords.Controle | None
    nb_a_programmer: int
    journal_nom: str | None
    avertissements: tuple[str, ...] = ()
    impossibilites: tuple[str, ...] = ()
    nb_seances: int | None = None
    datees: BilanDatees | None = None
    qualite: str | None = None
    arrete_avec_solution: bool = False
    regles: tuple[str, ...] = ()
    entorses: tuple[str, ...] = ()
    regles_personnalisees: tuple[str, ...] = ()
    remarque_regles: str | None = None
    entorses_regles: tuple[str, ...] = ()

    @property
    def en_cours(self) -> bool:
        """Vrai tant que le calcul tourne."""
        return self.issue == Issue.EN_COURS


# --------------------------------------------------------------------------- #
# Préparation
# --------------------------------------------------------------------------- #


def recapitulatif(session: Session, semestre_id: int) -> Recapitulatif:
    """Nombre de besoins, séances, groupes, enseignants et salles qu'un calcul aurait à placer.

    Mémorisé tant que la base ne change pas (`memoire`) : l'écran « Générer »
    le redemande à chaque affichage.
    """
    exiger_semestre(session, semestre_id)
    return memoire.memoriser(
        session, "recapitulatif", (semestre_id,), lambda: _recapitulatif(session, semestre_id)
    )


def _recapitulatif(session: Session, semestre_id: int) -> Recapitulatif:
    semestre = exiger_semestre(session, semestre_id)
    try:
        instantane = charger_instantane(session, semestre_id)
    except ErreurEDT as erreur:
        journal.warning("Récapitulatif du semestre %d impossible : %s", semestre_id, erreur)
        return Recapitulatif(semestre=semestre, erreur=str(erreur))
    groupes = {g for besoin in instantane.besoins for g in besoin.groupes}
    enseignants = {
        e
        for besoin in instantane.besoins
        for e in besoin.enseignants
        if not instantane.enseignant(e).fictif
    }
    return Recapitulatif(
        semestre=semestre,
        nb_besoins=len(instantane.besoins),
        nb_seances=len(instantane.seances),
        nb_groupes=len(groupes),
        nb_enseignants=len(enseignants),
        nb_salles=len(instantane.salles),
        problemes=tuple((str(p.code), p.message) for p in instantane.problemes_depliage),
    )


def niveaux_proposes(dernier: generation.SuiviCalcul | None) -> Niveaux:
    """Niveaux à préremplir : ceux du dernier calcul du semestre, sinon les niveaux par défaut.

    Toujours complet. Une règle absente des niveaux enregistrés (règle ignorée)
    reprend son niveau par défaut : l'écran ne propose pas d'ignorer une règle.
    """
    niveaux = niveaux_par_defaut()
    if dernier is not None:
        for code, retenu in dernier.niveaux.items():
            if code in CATALOGUE and CATALOGUE[code].modifiable:
                niveaux[CodeContrainte(code)] = Niveau(retenu)
    return niveaux


def regles_proposees(
    niveaux: Mapping[CodeContrainte, Niveau] | None = None,
) -> tuple[RegleProposee, ...]:
    """Le tableau « Règles », dans l'ordre du catalogue, avec le niveau proposé de chaque règle.

    Une règle absente de `niveaux` prend son niveau par défaut ; une règle
    non modifiable est toujours nécessaire.
    """
    niveaux = niveaux or {}
    return tuple(
        RegleProposee(
            code=str(code),
            libelle=entree.libelle,
            description=entree.description,
            niveau=Niveau(niveaux.get(code, entree.niveau_defaut))
            if entree.modifiable
            else Niveau.NECESSAIRE,
            niveau_defaut=entree.niveau_defaut,
            modifiable=entree.modifiable,
            note=NOTES_DES_REGLES.get(code),
        )
        for code, entree in CATALOGUE.items()
        if code is not CodeContrainte.REGLE  # chaque règle personnalisée porte son propre niveau
    )


def niveaux_saisis(champs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Les choix `niveau_<CODE>` d'un formulaire ou d'une adresse, `{code: valeur}`, non vérifiés."""
    return {
        nom[len(PREFIXE_NIVEAU) :]: str(valeur).strip()
        for nom, valeur in champs
        if nom.startswith(PREFIXE_NIVEAU)
        and not nom.startswith(regles_personnalisees.PREFIXE_NIVEAU_REGLE)
    }


def bloc_regles_du_semestre(
    session: Session,
    semestre_id: int,
    *,
    choix: Mapping[int, Niveau | None] | None = None,
    dernier: generation.SuiviCalcul | None = None,
) -> regles_personnalisees.BlocRegles:
    """Le tableau des règles personnalisées du semestre pour la page Générer.

    `choix` : saisie à réafficher ; sinon les niveaux du dernier calcul
    (`SuiviCalcul.niveaux_regles`), sinon ceux des règles.
    """
    semestre = exiger_semestre(session, semestre_id)
    precedents = dernier.niveaux_regles if choix is None and dernier is not None else None
    return regles_personnalisees.bloc_regles(
        session,
        semestre,
        annee_du_semestre(session, semestre_id),
        choix=choix,
        precedents=precedents,
    )


def lire_choix_regles(
    session: Session, semestre_id: int, champs: Mapping[str, str]
) -> tuple[dict[int, Niveau | None], regles_personnalisees.BlocRegles]:
    """Choix reçus pour les règles personnalisées du semestre, et le tableau qui les valide.

    Lève `regles_personnalisees.ErreurSaisieRegles` pour un choix illisible.
    """
    bloc = bloc_regles_du_semestre(session, semestre_id)
    return regles_personnalisees.lire_choix_regles(champs, bloc.regles), bloc


def lire_niveaux(saisis: Mapping[str, str]) -> Niveaux:
    """Niveaux complets d'après les choix reçus : les règles non citées gardent leur défaut.

    Lève `ErreurSaisieNiveaux` (avec les niveaux à réafficher) pour une règle
    inconnue, un niveau inconnu, ou un niveau autre que « nécessaire » sur une
    règle qui définit le problème (`normaliser_niveaux`).
    """
    try:
        return normaliser_niveaux(saisis)
    except ErreurDonneesInvalides as erreur:
        journal.info("Niveaux de règles refusés : %s", erreur.message)
        raise ErreurSaisieNiveaux(erreur.message, _niveaux_valides(saisis)) from erreur


def lire_niveaux_lisibles(saisis: Mapping[str, str]) -> Niveaux:
    """Les niveaux à réafficher quand le lancement est refusé pour une autre raison.

    Comme `lire_niveaux`, sans lever : un choix refusé reprend son niveau par
    défaut (il sera dit au lancement suivant).
    """
    return _niveaux_valides(saisis)


def _niveaux_valides(saisis: Mapping[str, str]) -> Niveaux:
    """Les niveaux par défaut, complétés des seuls choix valides de `saisis` (pour réafficher)."""
    niveaux = niveaux_par_defaut()
    for code, valeur in saisis.items():
        try:
            retenus = normaliser_niveaux({code: valeur})
        except ErreurDonneesInvalides:
            continue  # choix refusé : déjà dit par le message de `lire_niveaux`
        niveaux[CodeContrainte(code)] = retenus[CodeContrainte(code)]
    return niveaux


def pre_verifier(
    session: Session,
    semestre_id: int,
    niveaux: Mapping[CodeContrainte, Niveau] | None = None,
    recap: Recapitulatif | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> PreVerification:
    """Vérifications préalables pour un calcul avec ces niveaux de règles (défaut : par défaut).

    Les niveaux sont transmis tels quels aux pré-vérifications du solveur
    (clé absente : règle ignorée, seulement pour la relance d'un calcul
    enregistré sans niveaux), avec ceux des règles personnalisées (`None` :
    chacune à son niveau ; absente : ignorée). Elles couvrent aussi les besoins
    impossibles à déplier et le semestre sans séance à placer ; les problèmes
    du récapitulatif ne servent que si elles n'ont pas pu tourner.
    """
    niveaux = dict(niveaux) if niveaux is not None else niveaux_par_defaut()
    ignorees = {str(code) for code in CATALOGUE if code not in niveaux}
    recap = recap or recapitulatif(session, semestre_id)
    problemes = (recap.erreur,) if recap.erreur else ()
    if not recap.erreur and not recap.nb_besoins:
        problemes += (MESSAGE_AUCUN_BESOIN,)
    cle = tuple(sorted((str(code), str(retenu)) for code, retenu in niveaux.items()))
    cle_regles = regles_personnalisees.cle_niveaux_regles(niveaux_regles)
    try:
        anomalies = memoire.memoriser(
            session,
            "pre_verifier_semestre",
            (semestre_id, cle, cle_regles),
            lambda: raccords.pre_verifier_semestre(
                session, semestre_id, niveaux=niveaux, niveaux_regles=niveaux_regles
            ),
        )
    except raccords.ErreurRaccordIndisponible as erreur:
        problemes += tuple(message for code, message in recap.problemes if code not in ignorees)
        return PreVerification(problemes=problemes, motif=erreur.message)
    except ErreurEDT as erreur:
        journal.warning("Pré-vérifications du semestre %d impossibles : %s", semestre_id, erreur)
        return PreVerification(problemes=problemes, motif=erreur.message, niveau_motif="erreur")
    return PreVerification(
        # Un semestre sans besoin n'a aucune séance : les pré-vérifications le disent déjà.
        problemes=tuple(p for p in problemes if p != MESSAGE_AUCUN_BESOIN),
        erreurs=tuple(a for a in anomalies if a.gravite is Gravite.ERREUR),
        avertissements=tuple(a for a in anomalies if a.gravite is Gravite.AVERTISSEMENT),
        informations=tuple(a for a in anomalies if a.gravite is Gravite.INFORMATION),
    )


@dataclass(frozen=True)
class ResumesReglages:
    """Résumé d'une ligne de chaque section repliable du formulaire « Générer ».

    Affiché sur la section repliée : l'utilisateur voit les réglages sans
    ouvrir la section. Ne contient jamais « (par défaut) », marque réservée
    aux boutons du tableau des règles.
    """

    regles: str
    regles_personnalisees: str
    dejeuner: str
    duree_options: str


def resumer_reglages(
    regles: Iterable[RegleProposee],
    bloc: regles_personnalisees.BlocRegles,
    dejeuner: ChoixDejeuner,
    options: OptionsFormulaire,
    choix: Mapping[str, object],
) -> ResumesReglages:
    """Les quatre résumés du formulaire, d'après ce qu'il affiche (préremplissage ou saisie)."""
    return ResumesReglages(
        regles=_resume_regles(tuple(regles)),
        regles_personnalisees=_resume_regles_personnalisees(bloc),
        dejeuner=_resume_dejeuner(dejeuner),
        duree_options=_resume_duree_options(options, choix),
    )


def _resume_regles(regles: tuple[RegleProposee, ...]) -> str:
    modifiables = [regle for regle in regles if regle.modifiable]
    ecarts = [regle for regle in modifiables if regle.niveau is not regle.niveau_defaut]
    reglables = presentation.pluriel(len(modifiables), "règle réglable", "règles réglables")
    fixes = presentation.pluriel(
        len(regles) - len(modifiables), "toujours nécessaire", "toujours nécessaires"
    )
    if not ecarts:
        return f"{reglables}, toutes à leur niveau par défaut ; {fixes}"
    details = " ; ".join(f"{regle.libelle} : {regle.niveau.libelle.lower()}" for regle in ecarts)
    return f"{reglables}, dont {len(ecarts)} à un autre niveau que par défaut ({details}) ; {fixes}"


def _resume_regles_personnalisees(bloc: regles_personnalisees.BlocRegles) -> str:
    if bloc.erreur:
        return "Règles personnalisées illisibles (voir ci-dessous)"
    if not bloc.regles:
        return "Aucune règle personnalisée active"
    actives = presentation.pluriel(len(bloc.regles), "règle active", "règles actives")
    ignorees = sum(1 for regle in bloc.regles if regle.ignoree)
    autres = sum(
        1 for regle in bloc.regles if not regle.ignoree and regle.niveau is not regle.niveau_regle
    )
    if not ignorees and not autres:
        if len(bloc.regles) == 1:
            return f"{actives}, à son niveau habituel"
        return f"{actives}, toutes à leur niveau habituel"
    parties = []
    if ignorees:
        parties.append(presentation.pluriel(ignorees, "ignorée", "ignorées") + " pour ce calcul")
    if autres:
        parties.append(
            presentation.pluriel(
                autres, "à un autre niveau que le sien", "à un autre niveau que le leur"
            )
        )
    return f"{actives} : " + ", ".join(parties)


def _heure_lisible(texte: str) -> str:
    """« 12:45 » devient « 12 h 45 » ; une saisie illisible (réaffichée après refus) reste telle."""
    try:
        return minutes_vers_texte(texte_vers_minutes(texte))
    except ErreurDonneesInvalides:
        return texte


def _resume_dejeuner(dejeuner: ChoixDejeuner) -> str:
    libelle = LIBELLES_DEJEUNER.get(dejeuner.mode, dejeuner.mode)
    debut, fin = _heure_lisible(dejeuner.debut), _heure_lisible(dejeuner.fin)
    if dejeuner.mode == DEJEUNER_FIXE:
        return f"{libelle}, de {debut} à {fin}"
    if dejeuner.mode == DEJEUNER_LIBRE:
        return f"{libelle} : au moins {dejeuner.duree} min entre {debut} et {fin}"
    return libelle


def _resume_duree_options(options: OptionsFormulaire, choix: Mapping[str, object]) -> str:
    longue = choix.get("duree") == "long"
    resume = (
        f"Recherche jusqu'à {options.temps_long_texte if longue else options.temps_court_texte}"
    )
    resume += (
        " ; emploi du temps plus confortable"
        if choix.get("optimiser")
        else " ; sans recherche de confort"
    )
    if choix.get("autre_solution"):
        resume += " ; autre solution que la précédente"
    return resume


def nb_retouches_version_active(session: Session, semestre_id: int) -> int:
    """Séances datées retouchées à la main (déplacées ou annulées) dans la version active.

    Un nouveau calcul crée une autre version sans ces retouches : l'écran le
    dit avant le lancement.
    """
    return (
        session.scalar(
            select(func.count())
            .select_from(m.Occurrence)
            .join(m.VersionEdt, m.VersionEdt.id == m.Occurrence.version_id)
            .where(
                m.VersionEdt.semestre_id == semestre_id,
                m.VersionEdt.active.is_(True),
                m.Occurrence.origine == OrigineOccurrence.MANUELLE,
            )
        )
        or 0
    )


def options_formulaire(session: Session) -> OptionsFormulaire:
    """Valeurs par défaut du formulaire, tirées des paramètres globaux."""
    parametres = lire_parametres(session)
    return OptionsFormulaire(
        optimiser=parametres.objectif_actif,
        temps_court_s=parametres.temps_limite_s,
        temps_long_s=parametres.temps_limite_long_s,
    )


# --------------------------------------------------------------------------- #
# Pause déjeuner
# --------------------------------------------------------------------------- #


def choix_dejeuner(session: Session) -> ChoixDejeuner:
    """Pause déjeuner enregistrée dans les paramètres, telle que le formulaire la préremplit."""
    parametres = lire_parametres(session)
    return ChoixDejeuner(
        mode=parametres.dejeuner_mode,
        debut=minutes_vers_hhmm(parametres.dejeuner_debut_min),
        fin=minutes_vers_hhmm(parametres.dejeuner_fin_min),
        duree=str(parametres.dejeuner_duree_min),
    )


def lire_saisie_dejeuner(
    mode: str | None, debut: str | None, fin: str | None, duree: str | None = None
) -> ChoixDejeuner | None:
    """Bloc « Pause déjeuner » reçu du formulaire, ou `None` s'il n'a pas été envoyé.

    `mode` vaut `DEJEUNER_FIXE`, `DEJEUNER_LIBRE` ou `DEJEUNER_AUCUN` ; toute
    autre valeur lève `ErreurSaisieDejeuner`. Les heures et la durée absentes
    (champs masqués, donc non envoyés) deviennent des textes vides.
    """
    if mode is None:
        return None
    if mode not in (DEJEUNER_FIXE, DEJEUNER_LIBRE, DEJEUNER_AUCUN):
        journal.info("Pause déjeuner refusée : choix « %s » inconnu", apercu(mode))
        libelles = " ou ".join(f"« {libelle} »" for _, libelle in MODES_DEJEUNER)
        raise ErreurSaisieDejeuner({CHAMP_MODE_DEJEUNER: f"Choisir {libelles}."}, None)
    return ChoixDejeuner(mode, (debut or "").strip(), (fin or "").strip(), (duree or "").strip())


def changements_dejeuner(session: Session, choix: ChoixDejeuner) -> dict[str, ValeurParametre]:
    """Paramètres à modifier pour appliquer `choix` : seulement ceux qui changent.

    - « Pas de pause » : seul le mode change ; heures et durée enregistrées
      sont conservées (ignorées tant que le mode reste « aucun ») ;
    - « Horaires déterminés » : les deux heures, lisibles (« 12:45 »,
      « 12 h 45 »), la plage non vide et dans les heures d'ouverture ;
    - « Pause libre bornée » : en plus, la durée minimale (en minutes, dans
      les bornes du paramètre) doit tenir dans la fenêtre.

    Toute saisie fautive lève `ErreurSaisieDejeuner`, un message par champ
    (ceux de `defauts_dejeuner` pour la cohérence des heures et de la durée).
    """
    courants = lire_parametres(session)
    voulus: dict[str, ValeurParametre] = {"dejeuner_mode": choix.mode}
    if choix.mode != DEJEUNER_AUCUN:
        erreurs: dict[str, str] = {}
        for cle, nom, exemple, texte in (
            ("dejeuner_debut_min", "de début", "12:45", choix.debut),
            ("dejeuner_fin_min", "de fin", "13:45", choix.fin),
        ):
            champ = _CHAMP_DU_PARAMETRE[cle]
            if not texte:
                erreurs[champ] = f"Indiquer l'heure {nom} du déjeuner, par exemple {exemple}."
                continue
            try:
                voulus[cle] = texte_vers_minutes(texte)
            except ErreurDonneesInvalides as erreur:
                journal.debug("Heure %s du déjeuner illisible : %s", nom, erreur.message)
                erreurs[champ] = (
                    f"Heure {nom} du déjeuner illisible (« {apercu(texte)} ») : l'écrire par "
                    f"exemple {exemple}."
                )
        duree = courants.dejeuner_duree_min
        if choix.mode == DEJEUNER_LIBRE:
            lue = _duree_dejeuner(choix.duree)
            if isinstance(lue, str):
                erreurs[CHAMP_DUREE_DEJEUNER] = lue
            else:
                duree = voulus["dejeuner_duree_min"] = lue
        if not erreurs:
            defauts = defauts_dejeuner(
                int(voulus["dejeuner_debut_min"]),
                int(voulus["dejeuner_fin_min"]),
                courants.ouverture_min,
                courants.fermeture_min,
                mode=choix.mode,
                duree=duree,
            )
            erreurs = {_CHAMP_DU_PARAMETRE[cle]: message for cle, message in defauts.items()}
        if erreurs:
            journal.info("Pause déjeuner refusée : %s", " ".join(erreurs.values()))
            raise ErreurSaisieDejeuner(erreurs, choix)
    return {cle: valeur for cle, valeur in voulus.items() if getattr(courants, cle) != valeur}


def _duree_dejeuner(texte: str) -> int | str:
    """La durée minimale de la pause libre en minutes, ou le message qui dit quoi corriger."""
    minimum, maximum = BORNES_DUREE_DEJEUNER
    attendu = f"un nombre de minutes entre {minimum} et {maximum}, par exemple 60"
    if not texte:
        return f"Indiquer la durée minimale de la pause : {attendu}."
    duree = entier_ecrit(texte)
    if duree is None:
        if texte.isascii() and texte.isdigit():  # plus de chiffres qu'aucune durée
            return f"Durée minimale hors bornes : indiquer {attendu}."
        return f"Durée minimale illisible (« {apercu(texte)} ») : indiquer {attendu}."
    if not minimum <= duree <= maximum:
        return f"Durée minimale de {duree} min hors bornes : indiquer {attendu}."
    return duree


def appliquer_dejeuner(session: Session, choix: ChoixDejeuner) -> dict[str, ValeurParametre]:
    """Enregistre la pause déjeuner choisie si elle diffère des paramètres ; renvoie les changements.

    Les changements sont validés aussitôt, dans leur propre transaction sur
    la même base que `session` : le calcul lancé ensuite lit la base dans son
    propre fil et doit les voir, comme le vérificateur et les calculs
    suivants. `session` ne doit donc pas avoir d'écriture en attente.

    Lève `ErreurSaisieDejeuner` (rien n'est enregistré), ou
    `ErreurCalculEnCours` : les réglages ne changent pas sous un calcul qui
    tourne.
    """
    changements = changements_dejeuner(session, choix)
    if not changements:
        journal.debug("Pause déjeuner identique aux paramètres : rien à enregistrer")
        return {}
    if generation.calcul_en_cours() is not None:
        raise ErreurCalculEnCours(
            "Un calcul est déjà en cours : attendre sa fin ou l'arrêter avant de changer la "
            "pause déjeuner."
        )
    with Session(session.get_bind()) as transaction, transaction.begin():
        # Lit puis écrit : verrou pris d'emblée, pour attendre un import en cours au lieu
        # d'échouer « database is locked » (voir edt.web.dependances).
        transaction_immediate(transaction)
        ecrire_parametres(transaction, changements)
    journal.info(
        "Pause déjeuner réglée depuis l'écran « Générer » (%s)",
        ", ".join(f"{cle} = {valeur!r}" for cle, valeur in changements.items()),
    )
    return changements


# --------------------------------------------------------------------------- #
# Lancement
# --------------------------------------------------------------------------- #


def demarrer_calcul(
    session: Session,
    semestre_id: int,
    *,
    niveaux: Niveaux | None = None,
    longue: bool = False,
    optimiser: bool | None = None,
    autre_solution: bool = False,
    dejeuner: ChoixDejeuner | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> int:
    """Règle la pause déjeuner, vérifie, puis lance un calcul en arrière-plan ; renvoie son id.

    `niveaux` : niveaux des règles, validés (`lire_niveaux`) ; défaut : les
    niveaux par défaut. `niveaux_regles` : niveau de chaque règle
    personnalisée pour ce calcul (une règle absente est ignorée) ; `None` :
    chacune à son niveau. `dejeuner` (bloc du formulaire) est d'abord enregistré dans les
    paramètres s'il en diffère (`appliquer_dejeuner`) : les vérifications
    préalables et le calcul en tiennent compte, et le réglage reste même si
    le calcul est ensuite refusé. `None` laisse la pause déjeuner telle quelle.

    Lève `ErreurSaisieDejeuner` si la pause déjeuner est mal saisie,
    `ErreurPreVerification` si une vérification préalable relève une
    erreur, `ErreurCalculEnCours` si un calcul tourne déjà.
    """
    exiger_semestre(session, semestre_id)
    if dejeuner is not None:
        appliquer_dejeuner(session, dejeuner)
    niveaux = niveaux if niveaux is not None else niveaux_par_defaut()
    verification = pre_verifier(session, semestre_id, niveaux, niveaux_regles=niveaux_regles)
    if verification.bloquante:
        journal.info("Calcul du semestre %d refusé par les vérifications préalables", semestre_id)
        raise ErreurPreVerification(verification)
    if verification.motif:
        journal.warning(
            "Calcul du semestre %d lancé sans les pré-vérifications du solveur : %s",
            semestre_id,
            verification.motif,
        )
    options = _options(
        session,
        niveaux=niveaux,
        longue=longue,
        optimiser=optimiser,
        graine=_nouvelle_graine() if autre_solution else 0,
        utiliser_indices=not autre_solution,
        niveaux_regles=niveaux_regles,
    )
    return generation.lancer_calcul(semestre_id, options)


SEANCES_DATEES_A_PRODUIRE = (
    "La semaine type a été enregistrée, mais pas ses séances datées : les produire depuis l'écran "
    "« Emploi du temps » (bouton « Produire les séances datées »)."
)
"""Ce que l'écran dit d'une version enregistrée sans ses séances datées (instanciation en échec,
calcul interrompu juste après l'enregistrement)."""

CALCUL_DEJA_TERMINE = "Ce calcul est déjà terminé : rien à arrêter."
"""Ce que l'écran dit d'une demande d'arrêt sur un calcul déjà fini (deux onglets ouverts)."""


def arreter_calcul(calcul_id: int) -> tuple[generation.SuiviCalcul, bool]:
    """Demande l'arrêt d'un calcul ; renvoie son état et vrai s'il tournait encore.

    Faux pour un calcul déjà terminé (réussi, impossible, arrêté…) : la
    demande est sans effet et l'écran doit le dire, pas annoncer un arrêt.
    Lève `ErreurIntrouvable` pour un calcul inconnu.
    """
    suivi = generation.demander_arret(calcul_id)
    if not suivi.en_cours:
        journal.info("Arrêt demandé pour le calcul %d, déjà terminé : sans effet", calcul_id)
    return suivi, suivi.en_cours


@dataclass(frozen=True)
class Relance:
    """Un calcul relancé, et les règles personnalisées que le calcul d'origine ne connaissait pas.

    `regles_nouvelles` : codes des règles créées ou activées depuis, appliquées
    à leur propre niveau (à dire à l'utilisateur).
    """

    calcul_id: int
    regles_nouvelles: tuple[str, ...] = ()


def relancer_calcul(
    session: Session, calcul_id: int, *, longue: bool | None = None, autre_solution: bool = False
) -> Relance:
    """Relance un calcul avec les mêmes règles ; plus longtemps et/ou avec une autre graine.

    Les niveaux sont ceux du calcul d'origine (`SuiviCalcul.niveaux` ; des
    règles ignorées à l'origine le restent), de même que ceux des règles
    personnalisées ; une règle créée ou activée depuis s'applique à son
    propre niveau, jamais ignorée en silence
    (`regles_personnalisees.niveaux_pour_la_relance`). `longue=None` garde la durée
    du calcul d'origine. `autre_solution` tire
    une nouvelle graine **et** ne part pas des placements de la version
    active (sinon le moteur, guidé par eux, peut redonner le même emploi du
    temps). Mêmes erreurs que `demarrer_calcul`.
    """
    calcul = obtenir(session, m.Calcul, calcul_id)
    if calcul is None:
        raise ErreurIntrouvable(f"Le calcul n° {calcul_id} n'existe pas.")
    parametres = dict(calcul.parametres or {})
    origine = generation.etat_calcul(calcul_id)
    niveaux = dict(origine.niveaux)
    niveaux_regles, nouvelles = regles_personnalisees.niveaux_pour_la_relance(
        session,
        origine,
        exiger_semestre(session, calcul.semestre_id),
        annee_du_semestre(session, calcul.semestre_id),
    )
    if nouvelles:
        journal.info(
            "Relance du calcul %d : règles nouvelles appliquées à leur niveau : %s",
            calcul_id,
            ", ".join(nouvelles),
        )
    verification = pre_verifier(session, calcul.semestre_id, niveaux, niveaux_regles=niveaux_regles)
    if verification.bloquante:
        raise ErreurPreVerification(verification)
    if longue is None:
        longue = _etait_long(session, parametres)
    graine = _nouvelle_graine() if autre_solution else int(parametres.get("graine", 0))
    options = _options(
        session,
        niveaux=niveaux,
        longue=longue,
        optimiser=bool(parametres.get("optimiser", False)),
        graine=graine,
        utiliser_indices=not autre_solution and bool(parametres.get("utiliser_indices", True)),
        niveaux_regles=niveaux_regles,
    )
    journal.info(
        "Relance du calcul %d (longue=%s, autre solution=%s)", calcul_id, longue, autre_solution
    )
    return Relance(generation.lancer_calcul(calcul.semestre_id, options), nouvelles)


# --------------------------------------------------------------------------- #
# Résultat
# --------------------------------------------------------------------------- #


def resultat_calcul(
    session: Session, calcul_id: int, *, indicateurs: bool = True
) -> ResultatCalcul:
    """Le calcul `calcul_id` raconté en phrases (voir l'en-tête du module)."""
    suivi = generation.etat_calcul(calcul_id)
    calcul = session.get(m.Calcul, calcul_id)
    if calcul is None:
        raise ErreurIntrouvable(f"Le calcul n° {calcul_id} n'existe pas.")
    parametres = dict(calcul.parametres or {})
    semestre = exiger_semestre(session, suivi.semestre_id)
    limite = float(parametres.get("temps_limite_s") or 0) or None
    longue = _etait_long(session, parametres)
    issue = _ISSUE_PAR_ETAT[suivi.etat]
    statistiques = suivi.statistiques or {}
    if issue == Issue.IMPOSSIBLE and statistiques.get("refuse"):
        issue = Issue.REFUSE
    temps = presentation.duree_secondes(suivi.temps_ecoule_s)
    limite_texte = presentation.duree_secondes(limite) if limite else ""
    relance = presentation.duree_secondes(lire_parametres(session).temps_limite_long_s)

    version_active = False
    controle = None
    nb_a_programmer = 0
    if suivi.version_id is not None:
        version = session.get(m.VersionEdt, suivi.version_id)
        version_active = bool(version and version.active)
        nb_a_programmer = (
            session.scalar(
                select(func.count())
                .select_from(m.Occurrence)
                .where(
                    m.Occurrence.version_id == suivi.version_id,
                    m.Occurrence.statut == StatutOccurrence.A_PROGRAMMER,
                )
            )
            or 0
        )
        if indicateurs and issue == Issue.REUSSI:
            controle = raccords.controler_version(session, suivi.version_id)

    arrete_avec_solution = issue == Issue.REUSSI and bool(
        statistiques.get("arrete_par_utilisateur")
    )
    ecarts_regles, remarque_regles = regles_personnalisees.libelles_regles_du_calcul(session, suivi)
    titre, phrase, precision = _phrases(
        suivi, issue, semestre, temps, limite_texte, version_active, arrete_avec_solution
    )
    return ResultatCalcul(
        suivi=suivi,
        semestre=semestre,
        issue=issue,
        titre=titre,
        phrase=phrase,
        precision=precision,
        contraintes_desactivees=presentation.libelles_contraintes(suivi.contraintes_desactivees),
        temps_texte=temps,
        limite_texte=limite_texte,
        relance_texte=relance,
        longue=longue,
        optimiser=bool(parametres.get("optimiser", False)),
        version_active=version_active,
        controle=controle,
        nb_a_programmer=nb_a_programmer,
        journal_nom=Path(suivi.fichier_journal).name if suivi.fichier_journal else None,
        avertissements=tuple(suivi.avertissements),
        impossibilites=tuple(statistiques.get("impossibilites") or ()),
        nb_seances=int(statistiques["nb_seances"])
        if issue == Issue.REUSSI and "nb_seances" in statistiques
        else None,
        datees=BilanDatees.lire(statistiques.get("instanciation"))
        if issue == Issue.REUSSI
        else None,
        qualite=qualite_du_moteur(suivi.message) if issue == Issue.REUSSI else None,
        arrete_avec_solution=arrete_avec_solution,
        regles=presentation.libelles_niveaux(
            suivi.niveaux, dejeuner_actif=dejeuner_actif_du_calcul(session, suivi)
        ),
        entorses=_entorses(calcul_id, statistiques, suivi.niveaux),
        regles_personnalisees=ecarts_regles,
        remarque_regles=remarque_regles,
        entorses_regles=regles_personnalisees.libelles_entorses_regles(
            session, suivi, statistiques, annee_du_semestre(session, semestre.id)
        ),
    )


def dejeuner_actif_du_calcul(session: Session, suivi: generation.SuiviCalcul) -> bool:
    """Vrai si une pause déjeuner s'appliquait au calcul (pour citer ou non la règle `DEJEUNER`).

    Limite : le calcul n'enregistre pas la pause déjeuner ; elle est lue dans
    les paramètres courants, qui ont pu changer depuis.
    """
    return lire_parametres(session).dejeuner_actif


def relire_saisie_dejeuner(
    session: Session, mode: str | None, debut: str | None, fin: str | None, duree: str | None
) -> tuple[ChoixDejeuner | None, dict[str, str]]:
    """Le bloc « Pause déjeuner » reçu, à réafficher, et ses erreurs par champ, sans rien enregistrer.

    Sert quand un autre champ fait refuser le lancement : la saisie n'est
    pas perdue et ses propres fautes sont déjà dites. `(None, {})` si le
    bloc n'a pas été envoyé.
    """
    try:
        choix = lire_saisie_dejeuner(mode, debut, fin, duree)
        if choix is not None:
            changements_dejeuner(session, choix)
    except ErreurSaisieDejeuner as erreur:
        return erreur.saisie, erreur.champs
    return choix, {}


ENTORSES_ILLISIBLES = (
    "Le décompte des entorses de ce calcul est illisible : le journal du calcul le détaille.",
)


def _entorses(calcul_id: int, statistiques: dict, niveaux: Niveaux) -> tuple[str, ...]:
    """Entorses aux règles souples, en phrases, d'après `statistiques["entorses"]` (code → nombre).

    Rien si le moteur n'en a pas compté ; un décompte illisible est dit, pas tu.
    """
    brut = statistiques.get("entorses")
    if not brut:
        return ()
    try:
        entorses = {str(code): int(nombre) for code, nombre in dict(brut).items()}
    except (TypeError, ValueError) as erreur:
        journal.warning("Calcul %d : entorses illisibles (%r) : %s", calcul_id, brut, erreur)
        return ENTORSES_ILLISIBLES
    return presentation.libelles_entorses(entorses, niveaux)


PHRASES_DE_QUALITE = (
    "Bilan sur l'ensemble du semestre",
    "On ne peut pas faire mieux",
    "L'amélioration s'est arrêtée",
    "Le temps a manqué pour l'améliorer",
)
"""Débuts des phrases du message du moteur qui parlent de la qualité de la solution.

Les autres phrases (séances placées, séances datées, contraintes ignorées,
arrêt demandé) sont déjà dites par les compteurs et les encarts de l'écran.
"""


def qualite_du_moteur(message: str | None) -> str | None:
    """Les phrases de qualité du message de réussite du moteur, ou `None`."""
    if not message:
        return None
    # Une phrase commence après un point, par une majuscule ou un nombre
    # (« 202 séance(s) datée(s) : ... ») ; « 1.5 s » n'a pas d'espace après le point.
    phrases = re.split(r"(?<=\.)\s+(?=[A-ZÀ-Þ0-9])", message.strip())
    gardees = [p for p in phrases if p.startswith(PHRASES_DE_QUALITE)]
    return " ".join(gardees) or None


def _phrases(
    suivi: generation.SuiviCalcul,
    issue: str,
    semestre: ResumeSemestre,
    temps: str,
    limite: str,
    version_active: bool,
    arrete_avec_solution: bool = False,
) -> tuple[str, str, str | None]:
    message = (suivi.message or "").strip()
    if issue == Issue.EN_COURS:
        # Le temps écoulé est dit une seule fois, sous la phrase (gabarit du suivi).
        phrase = f"Recherche d'un emploi du temps pour le {semestre.libelle.lower()}."
        if suivi.premiere_solution_s is not None and not suivi.arret_demande:
            phrase += (
                " Un emploi du temps est déjà trouvé (après "
                f"{presentation.duree_secondes(suivi.premiere_solution_s)}) ; le calcul cherche à "
                "l'améliorer. Vous pouvez l'arrêter : le meilleur sera gardé."
            )
        if suivi.arret_demande:
            phrase += " L'arrêt est demandé : le calcul s'interrompt dès que possible."
        return "Calcul en cours", phrase, None
    if issue == Issue.REUSSI:
        phrase = f"Un emploi du temps a été trouvé en {temps}."
        titre = "Emploi du temps généré"
        if arrete_avec_solution:
            titre = "Calcul arrêté : meilleure solution conservée"
            phrase = (
                "Calcul arrêté à votre demande : la meilleure solution trouvée a été conservée "
                f"(après {temps} de recherche)."
            )
        if suivi.version_id is None:
            phrase += " La version produite a depuis été supprimée."
        elif version_active:
            phrase += " Il est maintenant la version active du semestre."
        else:
            phrase += " Il est enregistré comme une version du semestre (pas la version active)."
        return titre, phrase, None
    if issue == Issue.REFUSE:
        return (
            "Calcul non lancé : points bloquants relevés avant la recherche",
            "Les vérifications préalables ont relevé des situations qu'aucun emploi du temps ne "
            "peut satisfaire : la recherche n'a pas été lancée et aucun emploi du temps n'a été "
            "enregistré. Corriger les points ci-dessous, puis relancer.",
            None,
        )
    if issue == Issue.IMPOSSIBLE:
        # Le message du moteur explique déjà, en phrases, que c'est prouvé et quoi faire.
        return (
            "Impossible avec ces contraintes",
            message
            or "Il est certain qu'aucun emploi du temps ne peut respecter toutes les "
            "règles nécessaires avec les données actuelles. Aucun emploi du temps n'a été "
            "enregistré.",
            None,
        )
    if issue == Issue.TEMPS_DEPASSE:
        phrase = "Aucun emploi du temps n'a été trouvé"
        phrase += f" en {limite}." if limite else " dans le temps imparti."
        phrase += (
            " Ce n'est pas une preuve qu'il n'en existe pas : une recherche plus longue peut "
            "aboutir. Aucun emploi du temps n'a été enregistré."
        )
        return "Pas trouvé dans le temps imparti", phrase, None
    if issue == Issue.ARRETE:
        return (
            "Calcul arrêté",
            "Le calcul a été arrêté à votre demande. Aucun emploi du temps n'a été enregistré.",
            None,
        )
    # Le service ajoute au message d'erreur la phrase sur une version enregistrée sans ses
    # séances datées : le début du message reconnaît le cas, la version le complète.
    enregistre = (
        f" {SEANCES_DATEES_A_PRODUIRE}"
        if suivi.version_id is not None
        else " Aucun emploi du temps n'a été enregistré."
    )
    if message.startswith(generation.MESSAGE_INTERROMPU):
        phrase = "Le calcul a été interrompu par un arrêt de l'application." + enregistre
        return "Le calcul n'a pas pu aboutir", phrase + " Il peut être relancé.", None
    if not message or message.startswith(generation.MESSAGE_ERREUR_INATTENDUE):
        phrase = (
            "Une erreur inattendue a interrompu le calcul."
            + enregistre
            + " Pour obtenir de l'aide, communiquer les références ci-dessous."
        )
        return "Le calcul n'a pas pu aboutir", phrase, None
    return (
        "Le calcul n'a pas pu aboutir",
        "Le calcul s'est arrêté avant d'aboutir (voir la précision)." + enregistre,
        message,
    )


# --------------------------------------------------------------------------- #
# Interne
# --------------------------------------------------------------------------- #


def _options(
    session: Session,
    *,
    niveaux: Mapping[CodeContrainte, Niveau],
    longue: bool,
    optimiser: bool | None,
    graine: int,
    utiliser_indices: bool = True,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> OptionsSolveur:
    options = OptionsSolveur.depuis_parametres(
        lire_parametres(session),
        niveaux=niveaux,
        niveaux_regles=niveaux_regles,
        relance_longue=longue,
        graine=graine,
    )
    if optimiser is not None and optimiser != options.optimiser:
        options = dataclasses.replace(options, optimiser=optimiser)
    if utiliser_indices != options.utiliser_indices:
        options = dataclasses.replace(options, utiliser_indices=utiliser_indices)
    return options


def _etait_long(session: Session, parametres: dict) -> bool:
    """Vrai si le calcul a utilisé le temps limite long (`temps_limite_long_s`)."""
    limite = parametres.get("temps_limite_s")
    reglages = lire_parametres(session)
    return (
        limite is not None
        and float(limite) >= reglages.temps_limite_long_s > reglages.temps_limite_s
    )


def _nouvelle_graine() -> int:
    return _hasard.randint(1, _GRAINE_MAX)
