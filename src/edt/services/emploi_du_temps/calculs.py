"""Préparer, lancer et lire un calcul (onglet « Générer »), en langage courant.

Au-dessus de `edt.services.generation` (qui orchestre le fil de calcul),
ce module ajoute ce que l'écran doit dire :

- le **récapitulatif** de ce qui va être placé (besoins, séances, groupes,
  enseignants, salles) ;
- les **vérifications préalables** du solveur
  (`raccords.pre_verifier_semestre` : charges, salles, séances sans place
  possible, verrous contradictoires, besoins impossibles à déplier),
  groupées par gravité : une erreur empêche de lancer, un avertissement non ;
- les **options** : temps limite court ou long, recherche d'un emploi du
  temps plus confortable (objectif), **autre solution** (nouvelle graine,
  sans partir des placements de la version active) ;
- le **résultat** d'un calcul en phrases : succès (séances placées,
  séances datées, replacées et à programmer avec leur raison,
  avertissements, indicateurs), **refusé** par les vérifications préalables
  (avec la liste), impossible (prouvé par la recherche), pas trouvé dans le
  temps imparti, arrêté, erreur (identifiant du calcul et nom du journal).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import random
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.contraintes import CATALOGUE, normaliser_codes
from edt.domaine.enumerations import EtatCalcul, Gravite, StatutOccurrence
from edt.erreurs import ErreurEDT, ErreurIntrouvable, ErreurRegleMetier
from edt.services import generation
from edt.services.emploi_du_temps import memoire, presentation, raccords
from edt.services.emploi_du_temps.versions import ResumeSemestre, exiger_semestre
from edt.services.instantane import charger_instantane
from edt.services.parametres import lire_parametres
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


@dataclass(frozen=True)
class ContrainteProposee:
    """Une case à cocher : contrainte du catalogue, cochée si elle est active."""

    code: str
    libelle: str
    description: str
    active: bool


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
    `motif` : pourquoi les pré-vérifications du lot solveur n'ont pas tourné.
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
            "emploi du temps. Corriger les points ci-dessous, ou décocher la contrainte concernée.",
            details,
        )
        self.pre_verification = pre_verification


@dataclass(frozen=True)
class OptionsFormulaire:
    """Valeurs par défaut du formulaire de lancement."""

    optimiser: bool
    temps_court_s: int
    temps_long_s: int

    @property
    def temps_court_texte(self) -> str:
        """« 1 minute »."""
        return presentation.duree_secondes(self.temps_court_s)

    @property
    def temps_long_texte(self) -> str:
        """« 5 minutes »."""
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
    - `datees` : bilan des séances datées (succès).
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


def contraintes_proposees(desactivees: Iterable[str] = ()) -> tuple[ContrainteProposee, ...]:
    """Les contraintes du catalogue, dans son ordre, cochées sauf celles de `desactivees`."""
    eteintes = normaliser_codes(desactivees)
    return tuple(
        ContrainteProposee(
            code=str(code),
            libelle=entree.libelle,
            description=entree.description,
            active=code not in eteintes and entree.active_par_defaut,
        )
        for code, entree in CATALOGUE.items()
    )


def desactivees_depuis_cochees(cochees: Iterable[str]) -> frozenset[str]:
    """Contraintes décochées, à partir des codes cochés dans le formulaire (codes vérifiés)."""
    actives = normaliser_codes(cochees)
    return frozenset(str(code) for code in CATALOGUE if code not in actives)


def pre_verifier(
    session: Session,
    semestre_id: int,
    desactivees: Iterable[str] = (),
    recap: Recapitulatif | None = None,
) -> PreVerification:
    """Vérifications préalables pour un calcul avec ces contraintes décochées.

    Les pré-vérifications du solveur couvrent aussi les besoins impossibles à
    déplier ; ceux du récapitulatif ne servent que si elles manquaient.
    """
    desactivees = normaliser_codes(desactivees)
    recap = recap or recapitulatif(session, semestre_id)
    problemes = (recap.erreur,) if recap.erreur else ()
    if not recap.erreur and not recap.nb_besoins:
        problemes += (MESSAGE_AUCUN_BESOIN,)
    codes = tuple(sorted(str(code) for code in desactivees))
    try:
        anomalies = memoire.memoriser(
            session,
            "pre_verifier_semestre",
            (semestre_id, codes),
            lambda: raccords.pre_verifier_semestre(session, semestre_id, codes),
        )
    except raccords.ErreurRaccordIndisponible as erreur:
        problemes += tuple(
            message
            for code, message in recap.problemes
            if code not in {str(c) for c in desactivees}
        )
        return PreVerification(problemes=problemes, motif=erreur.message)
    except ErreurEDT as erreur:
        journal.warning("Pré-vérifications du semestre %d impossibles : %s", semestre_id, erreur)
        return PreVerification(problemes=problemes, motif=erreur.message, niveau_motif="erreur")
    return PreVerification(
        problemes=problemes,
        erreurs=tuple(a for a in anomalies if a.gravite is Gravite.ERREUR),
        avertissements=tuple(a for a in anomalies if a.gravite is Gravite.AVERTISSEMENT),
        informations=tuple(a for a in anomalies if a.gravite is Gravite.INFORMATION),
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
# Lancement
# --------------------------------------------------------------------------- #


def demarrer_calcul(
    session: Session,
    semestre_id: int,
    *,
    contraintes_cochees: Iterable[str],
    longue: bool = False,
    optimiser: bool | None = None,
    autre_solution: bool = False,
) -> int:
    """Vérifie, puis lance un calcul en arrière-plan ; renvoie son identifiant.

    Lève `ErreurPreVerification` si une vérification préalable relève une
    erreur, `ErreurCalculEnCours` si un calcul tourne déjà.
    """
    exiger_semestre(session, semestre_id)
    desactivees = desactivees_depuis_cochees(contraintes_cochees)
    verification = pre_verifier(session, semestre_id, desactivees)
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
        desactivees=desactivees,
        longue=longue,
        optimiser=optimiser,
        graine=_nouvelle_graine() if autre_solution else 0,
        utiliser_indices=not autre_solution,
    )
    return generation.lancer_calcul(semestre_id, options)


def relancer_calcul(
    session: Session, calcul_id: int, *, longue: bool | None = None, autre_solution: bool = False
) -> int:
    """Relance un calcul avec les mêmes contraintes ; plus longtemps et/ou avec une autre graine.

    `longue=None` garde la durée du calcul d'origine. `autre_solution` tire
    une nouvelle graine **et** ne part pas des placements de la version
    active (sinon le moteur, guidé par eux, peut redonner le même emploi du
    temps). Mêmes erreurs que `demarrer_calcul`.
    """
    calcul = session.get(m.Calcul, calcul_id)
    if calcul is None:
        raise ErreurIntrouvable(f"Le calcul n° {calcul_id} n'existe pas.")
    parametres = dict(calcul.parametres or {})
    desactivees = frozenset(calcul.contraintes_desactivees or ())
    verification = pre_verifier(session, calcul.semestre_id, desactivees)
    if verification.bloquante:
        raise ErreurPreVerification(verification)
    if longue is None:
        longue = _etait_long(session, parametres)
    graine = _nouvelle_graine() if autre_solution else int(parametres.get("graine", 0))
    options = _options(
        session,
        desactivees=desactivees,
        longue=longue,
        optimiser=bool(parametres.get("optimiser", False)),
        graine=graine,
        utiliser_indices=not autre_solution and bool(parametres.get("utiliser_indices", True)),
    )
    journal.info(
        "Relance du calcul %d (longue=%s, autre solution=%s)", calcul_id, longue, autre_solution
    )
    return generation.lancer_calcul(calcul.semestre_id, options)


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

    titre, phrase, precision = _phrases(suivi, issue, semestre, temps, limite_texte, version_active)
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
    )


def _phrases(
    suivi: generation.SuiviCalcul,
    issue: str,
    semestre: ResumeSemestre,
    temps: str,
    limite: str,
    version_active: bool,
) -> tuple[str, str, str | None]:
    message = (suivi.message or "").strip()
    if issue == Issue.EN_COURS:
        phrase = f"Recherche d'un emploi du temps pour le {semestre.libelle.lower()}. Temps écoulé : {temps}"
        phrase += f" (au plus {limite})." if limite else "."
        if suivi.arret_demande:
            phrase += " L'arrêt est demandé : le calcul s'interrompt dès que possible."
        return "Calcul en cours", phrase, None
    if issue == Issue.REUSSI:
        phrase = f"Un emploi du temps a été trouvé en {temps}."
        if suivi.version_id is None:
            phrase += " La version produite a depuis été supprimée."
        elif version_active:
            phrase += " Il est maintenant la version active du semestre."
        else:
            phrase += " Il est enregistré comme une version du semestre (pas la version active)."
        return "Emploi du temps généré", phrase, None
    if issue == Issue.REFUSE:
        return (
            "Calcul non lancé : impossibilités relevées avant la recherche",
            "Les vérifications préalables ont relevé des situations qu'aucun emploi du temps ne "
            "peut satisfaire : la recherche n'a pas été lancée et aucun emploi du temps n'a été "
            "enregistré. Corriger les points ci-dessous (ou décocher la contrainte concernée), "
            "puis relancer.",
            None,
        )
    if issue == Issue.IMPOSSIBLE:
        # Le message du moteur explique déjà, en phrases, que c'est prouvé et quoi faire.
        return (
            "Impossible avec ces contraintes",
            message
            or "Il est certain qu'aucun emploi du temps ne peut respecter toutes les "
            "contraintes cochées avec les données actuelles. Aucun emploi du temps n'a été "
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
    if message in (generation.MESSAGE_ERREUR_INATTENDUE, generation.MESSAGE_INTERROMPU, ""):
        phrase = (
            "Une erreur inattendue a interrompu le calcul. Aucun emploi du temps n'a été "
            "enregistré. Pour obtenir de l'aide, communiquer les références ci-dessous."
        )
        if message == generation.MESSAGE_INTERROMPU:
            phrase = (
                "Le calcul a été interrompu par un arrêt de l'application. Aucun emploi du temps "
                "n'a été enregistré. Il peut être relancé."
            )
        return "Le calcul n'a pas pu aboutir", phrase, None
    return (
        "Le calcul n'a pas pu aboutir",
        "Le calcul s'est arrêté sur un problème dans les données. Aucun emploi du temps n'a été "
        "enregistré.",
        message,
    )


# --------------------------------------------------------------------------- #
# Interne
# --------------------------------------------------------------------------- #


def _options(
    session: Session,
    *,
    desactivees: Iterable[str],
    longue: bool,
    optimiser: bool | None,
    graine: int,
    utiliser_indices: bool = True,
) -> OptionsSolveur:
    options = OptionsSolveur.depuis_parametres(
        lire_parametres(session),
        contraintes_desactivees=desactivees,
        relance_longue=longue,
        graine=graine,
    )
    if optimiser is not None and optimiser != options.optimiser:
        options = dataclasses.replace(options, optimiser=optimiser)
    if utiliser_indices != options.utiliser_indices:
        options = dataclasses.replace(options, utiliser_indices=utiliser_indices)
    return options


def _etait_long(session: Session, parametres: dict) -> bool:
    """Vrai si le calcul a utilisé le temps limite long (relance « 5 minutes »)."""
    limite = parametres.get("temps_limite_s")
    reglages = lire_parametres(session)
    return (
        limite is not None
        and float(limite) >= reglages.temps_limite_long_s > reglages.temps_limite_s
    )


def _nouvelle_graine() -> int:
    return _hasard.randint(1, _GRAINE_MAX)
