"""Préparer, lancer et lire un calcul (onglet « Générer »), en langage courant.

Au-dessus de `edt.services.generation` (qui orchestre le fil de calcul),
ce module ajoute ce que l'écran doit dire :

- le **récapitulatif** de ce qui va être placé (besoins, séances, groupes,
  enseignants, salles) et les besoins impossibles à planifier tels quels ;
- les **vérifications préalables**, groupées par gravité : une erreur
  empêche de lancer, un avertissement non. Elles réunissent les besoins
  impossibles à déplier (toujours connus) et les pré-vérifications du lot
  solveur (`raccords.pre_verifier_semestre`, signalées comme absentes tant
  qu'elles ne sont pas livrées) ;
- les **options** : temps limite court ou long, recherche d'un emploi du
  temps plus confortable (objectif), autre solution (nouvelle graine) ;
- le **résultat** d'un calcul en phrases : succès (indicateurs, séances à
  programmer), impossible (prouvé), pas trouvé dans le temps imparti,
  arrêté, erreur (identifiant du calcul et nom du journal à communiquer).
"""

from __future__ import annotations

import dataclasses
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
from edt.services.emploi_du_temps import presentation, raccords
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
class ResultatCalcul:
    """Un calcul, raconté pour l'écran."""

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

    @property
    def en_cours(self) -> bool:
        """Vrai tant que le calcul tourne."""
        return self.issue == Issue.EN_COURS


# --------------------------------------------------------------------------- #
# Préparation
# --------------------------------------------------------------------------- #


def recapitulatif(session: Session, semestre_id: int) -> Recapitulatif:
    """Nombre de besoins, séances, groupes, enseignants et salles qu'un calcul aurait à placer."""
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
    """Vérifications préalables pour un calcul avec ces contraintes décochées."""
    desactivees = normaliser_codes(desactivees)
    recap = recap or recapitulatif(session, semestre_id)
    problemes = tuple(
        message for code, message in recap.problemes if code not in {str(c) for c in desactivees}
    )
    if recap.erreur:
        problemes = (recap.erreur, *problemes)
    try:
        anomalies = raccords.pre_verifier_semestre(session, semestre_id, sorted(desactivees))
    except raccords.ErreurRaccordIndisponible as erreur:
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
    )
    return generation.lancer_calcul(semestre_id, options)


def relancer_calcul(
    session: Session, calcul_id: int, *, longue: bool | None = None, autre_solution: bool = False
) -> int:
    """Relance un calcul avec les mêmes contraintes ; plus longtemps et/ou avec une autre graine.

    `longue=None` garde la durée du calcul d'origine. Mêmes erreurs que `demarrer_calcul`.
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
            controle = raccords.controler(raccords.verifier_version, session, suivi.version_id)

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
    # Messages génériques de l'orchestration : déjà dits par la phrase, inutile de les répéter.
    defauts = set(generation._MESSAGES_PAR_DEFAUT.values())
    precision = message if message and message not in defauts else None
    if issue == Issue.EN_COURS:
        phrase = f"Recherche d'un emploi du temps pour le {semestre.libelle.lower()}. Temps écoulé : {temps}"
        phrase += f" (au plus {limite})." if limite else "."
        if suivi.arret_demande:
            phrase += " L'arrêt est demandé : le calcul s'interrompt dès que possible."
        return "Calcul en cours", phrase, None
    if issue == Issue.REUSSI:
        phrase = f"Un emploi du temps a été trouvé en {temps}."
        phrase += (
            " Il est maintenant la version active du semestre."
            if version_active
            else " Il est enregistré comme une nouvelle version du semestre."
        )
        return "Emploi du temps généré", phrase, None
    if issue == Issue.IMPOSSIBLE:
        return (
            "Impossible avec ces contraintes",
            "Il est certain qu'aucun emploi du temps ne peut respecter toutes les contraintes "
            "cochées avec les données actuelles. Aucun emploi du temps n'a été enregistré. Pour "
            "avancer : relire les vérifications préalables, corriger les données (besoins, "
            "indisponibilités, salles) ou décocher une contrainte pour voir laquelle bloque.",
            precision,
        )
    if issue == Issue.TEMPS_DEPASSE:
        phrase = "Aucun emploi du temps n'a été trouvé"
        phrase += f" en {limite}." if limite else " dans le temps imparti."
        phrase += (
            " Ce n'est pas une preuve qu'il n'en existe pas : une recherche plus longue peut "
            "aboutir. Aucun emploi du temps n'a été enregistré."
        )
        return "Pas trouvé dans le temps imparti", phrase, precision
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
) -> OptionsSolveur:
    options = OptionsSolveur.depuis_parametres(
        lire_parametres(session),
        contraintes_desactivees=desactivees,
        relance_longue=longue,
        graine=graine,
    )
    if optimiser is not None and optimiser != options.optimiser:
        options = dataclasses.replace(options, optimiser=optimiser)
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
