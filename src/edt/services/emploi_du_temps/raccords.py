"""Raccords vers les fonctions des autres lots : le SEUL endroit où les écrans les appellent.

Les pré-vérifications, le vérificateur et l'instanciation datée sont écrits
par d'autres lots. Leurs noms et signatures sont fixés :

- `edt.services.generation.pre_verifier_semestre(session, semestre_id,
  contraintes_desactivees=()) -> list[Anomalie]` ;
- `edt.services.verification.verifier_version(session, version_id,
  contraintes_actives=None) -> RapportVerification` ;
- `edt.services.verification.verifier_occurrences_version(session,
  version_id, date_debut=None, date_fin=None, contraintes_actives=None)
  -> RapportVerification` ;
- `edt.services.verification.verifier_modification(session, version_id,
  seance_id, jour, debut, fin, salle_id) -> RapportVerification` (ce que
  donnerait la semaine type si la séance était placée ainsi, sans rien
  enregistrer ; seulement les anomalies qui concernent cette séance) ;
- `edt.services.instanciation.instancier_version(session, version_id)
  -> RapportInstanciation`.

Chaque fonction est importée **au moment de l'appel**. Si le module ou la
fonction n'existe pas encore, ou si elle lève `ErreurNonLivre`, le raccord
lève `ErreurRaccordIndisponible` (une `ErreurNonLivre`), journalisée en
avertissement, avec une phrase affichable telle quelle. Jamais de liste vide
à la place : un écran qui reçoit cette erreur affiche un encart clair
(voir `controler`). Une erreur d'import *à l'intérieur* d'un module livré
n'est pas maquillée : elle remonte telle quelle.

Le `RapportVerification` est lu une seule fois ici et ramené à une
`Verification` (anomalies, comptes par gravité, indicateurs normalisés) :
si le contrat évolue, seul ce module change.
"""

from __future__ import annotations

import dataclasses
import importlib
import logging
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurEDT, ErreurNonLivre
from edt.verification.contrat import Anomalie

journal = logging.getLogger(__name__)

MODULE_GENERATION = "edt.services.generation"
MODULE_VERIFICATION = "edt.services.verification"
MODULE_INSTANCIATION = "edt.services.instanciation"

MESSAGE_VERIFICATION = (
    "Les vérifications automatiques ne sont pas encore disponibles dans cette version."
)
MESSAGE_PRE_VERIFICATION = (
    "Les vérifications préalables au calcul ne sont pas encore disponibles dans cette version."
)
MESSAGE_INSTANCIATION = (
    "La production des séances datées (semaine type appliquée au calendrier) n'est pas encore "
    "disponible dans cette version."
)
MESSAGE_ERREUR_INATTENDUE = (
    "La vérification automatique a rencontré une erreur inattendue. L'incident est enregistré "
    "dans le journal ; la référence de la page permet de le retrouver."
)


class ErreurRaccordIndisponible(ErreurNonLivre):
    """La fonction d'un autre lot n'est pas encore disponible (module, fonction ou lot non livré).

    `fonction` est le nom complet de la fonction attendue (pour le journal) ;
    le message est une phrase pour l'utilisateur.
    """

    def __init__(self, fonction: str, message: str) -> None:
        super().__init__(message)
        self.fonction = fonction


# --------------------------------------------------------------------------- #
# Résultats normalisés
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Indicateurs:
    """Indicateurs de qualité d'une semaine type (valeurs `None` quand ils sont inconnus).

    - `pourcentage_seances_placees` : séances placées / séances demandées ;
    - `pourcentage_utilisation_salles` : minutes occupées / minutes offertes ;
    - `changements_campus` : changements de campus dans une journée (groupes
      et enseignants), comptés semaine par semaine sur tout le semestre ;
    - `trous_creneaux` : créneaux libres entre deux séances d'un groupe ;
    - `nb_semaines` : semaines du semestre (pour une moyenne hebdomadaire).
    """

    pourcentage_seances_placees: float | None = None
    pourcentage_utilisation_salles: float | None = None
    changements_campus: int | None = None
    trous_creneaux: int | None = None
    nb_semaines: int | None = None

    @property
    def changements_campus_par_semaine(self) -> float | None:
        """Moyenne hebdomadaire des changements de campus, si le nombre de semaines est connu."""
        if self.changements_campus is None or not self.nb_semaines:
            return None
        return round(self.changements_campus / self.nb_semaines, 1)


@dataclass(frozen=True)
class Verification:
    """Résultat d'une vérification, lu dans le `RapportVerification` du vérificateur."""

    anomalies: tuple[Anomalie, ...]
    indicateurs: Indicateurs | None = None

    @property
    def nb_erreurs(self) -> int:
        """Anomalies bloquantes (contrainte violée)."""
        return sum(1 for a in self.anomalies if a.gravite is Gravite.ERREUR)

    @property
    def nb_avertissements(self) -> int:
        """Situations douteuses qui n'invalident pas l'emploi du temps."""
        return sum(1 for a in self.anomalies if a.gravite is Gravite.AVERTISSEMENT)

    @property
    def nb_informations(self) -> int:
        """Écarts admis, signalés pour information."""
        return sum(1 for a in self.anomalies if a.gravite is Gravite.INFORMATION)

    @property
    def par_code(self) -> dict[str, int]:
        """Nombre d'anomalies par code de contrainte."""
        return dict(Counter(str(a.code) for a in self.anomalies))

    @property
    def valide(self) -> bool:
        """Vrai si aucune anomalie n'est une erreur."""
        return self.nb_erreurs == 0

    def erreurs(self) -> tuple[Anomalie, ...]:
        """Les anomalies bloquantes seulement."""
        return tuple(a for a in self.anomalies if a.gravite is Gravite.ERREUR)


@dataclass(frozen=True)
class Controle:
    """Une vérification qui a tourné, ou la raison pour laquelle elle n'a pas pu tourner.

    Les écrans affichent `motif` dans un encart (`niveau` : « attention » si
    la fonction n'est pas encore livrée, « erreur » si elle a échoué) : une
    vérification absente n'est jamais présentée comme « aucune anomalie ».
    """

    verification: Verification | None = None
    motif: str | None = None
    niveau: str = "attention"

    @property
    def disponible(self) -> bool:
        """Vrai si la vérification a tourné."""
        return self.verification is not None


# --------------------------------------------------------------------------- #
# Raccords
# --------------------------------------------------------------------------- #


def pre_verifier_semestre(
    session: Session, semestre_id: int, contraintes_desactivees: Iterable[str] = ()
) -> tuple[Anomalie, ...]:
    """Pré-vérifications d'un semestre avant un calcul (charges, salles accessibles, verrous...)."""
    anomalies = _appeler(
        MODULE_GENERATION,
        "pre_verifier_semestre",
        MESSAGE_PRE_VERIFICATION,
        session,
        semestre_id,
        contraintes_desactivees=tuple(contraintes_desactivees),
    )
    return tuple(anomalies)


def verifier_version(
    session: Session, version_id: int, contraintes_actives: Iterable[str] | None = None
) -> Verification:
    """Vérification de la semaine type d'une version, avec ses indicateurs."""
    rapport = _appeler(
        MODULE_VERIFICATION,
        "verifier_version",
        MESSAGE_VERIFICATION,
        session,
        version_id,
        contraintes_actives=contraintes_actives,
    )
    return lire_rapport(rapport)


def verifier_occurrences_version(
    session: Session,
    version_id: int,
    date_debut=None,
    date_fin=None,
    contraintes_actives: Iterable[str] | None = None,
) -> Verification:
    """Vérification des séances datées d'une version, éventuellement sur une période."""
    rapport = _appeler(
        MODULE_VERIFICATION,
        "verifier_occurrences_version",
        MESSAGE_VERIFICATION,
        session,
        version_id,
        date_debut=date_debut,
        date_fin=date_fin,
        contraintes_actives=contraintes_actives,
    )
    return lire_rapport(rapport)


def verifier_modification(
    session: Session,
    version_id: int,
    seance_id: str,
    jour: int,
    debut: int,
    fin: int,
    salle_id: int | None,
) -> Verification:
    """Anomalies qu'aurait la séance `seance_id` placée ainsi (rien n'est enregistré)."""
    rapport = _appeler(
        MODULE_VERIFICATION,
        "verifier_modification",
        MESSAGE_VERIFICATION,
        session,
        version_id,
        seance_id,
        jour,
        debut,
        fin,
        salle_id,
    )
    return lire_rapport(rapport)


def instancier_version(session: Session, version_id: int) -> Any:
    """Produit les séances datées d'une version (table `occurrence`) ; renvoie le rapport brut."""
    return _appeler(
        MODULE_INSTANCIATION, "instancier_version", MESSAGE_INSTANCIATION, session, version_id
    )


def controler(fonction: Callable[..., Verification], *args, **kwargs) -> Controle:
    """Appelle un raccord de vérification pour un écran de consultation, sans jamais planter.

    - fonction pas encore livrée : `Controle` avec le message d'indisponibilité ;
    - refus métier (`ErreurEDT`) : `Controle` avec le message de l'erreur ;
    - toute autre exception : trace complète dans le journal, `Controle` avec
      un message d'erreur inattendue (l'écran reste utilisable).
    """
    try:
        return Controle(verification=fonction(*args, **kwargs))
    except ErreurRaccordIndisponible as erreur:
        return Controle(motif=erreur.message, niveau="attention")
    except ErreurEDT as erreur:
        journal.warning("Vérification impossible : %s", erreur)
        return Controle(motif=erreur.message, niveau="erreur")
    except Exception:
        journal.exception("Erreur inattendue pendant une vérification (%s)", fonction.__name__)
        return Controle(motif=MESSAGE_ERREUR_INATTENDUE, niveau="erreur")


# --------------------------------------------------------------------------- #
# Lecture du rapport du vérificateur
# --------------------------------------------------------------------------- #


def lire_rapport(rapport: Any) -> Verification:
    """Ramène un `RapportVerification` à une `Verification` (anomalies et indicateurs)."""
    anomalies = tuple(rapport.anomalies)
    for anomalie in anomalies:
        if not isinstance(anomalie, Anomalie):
            raise TypeError(
                f"Le vérificateur a renvoyé un objet qui n'est pas une Anomalie : {anomalie!r}"
            )
    return Verification(
        anomalies=anomalies,
        indicateurs=lire_indicateurs(getattr(rapport, "indicateurs", None)),
    )


def lire_indicateurs(objet: Any) -> Indicateurs | None:
    """Indicateurs normalisés à partir de l'objet du vérificateur (ou `None`).

    Noms attendus (ceux de `edt.verification.indicateurs.Indicateurs`) :
    `pourcentage_seances_placees`, `pourcentage_utilisation_salles`,
    `changements_campus_groupes`, `changements_campus_enseignants`,
    `trous_creneaux`, `nb_semaines`. Un nom absent est journalisé (pas de
    valeur inventée) et l'indicateur reste inconnu.
    """
    if objet is None:
        return None
    valeurs = _en_dictionnaire(objet)
    manquants = [
        cle
        for cle in (
            "pourcentage_seances_placees",
            "pourcentage_utilisation_salles",
            "changements_campus_groupes",
            "changements_campus_enseignants",
            "trous_creneaux",
            "nb_semaines",
        )
        if cle not in valeurs
    ]
    if manquants:
        journal.warning(
            "Indicateurs du vérificateur incomplets : %s absent(s) (clés reçues : %s)",
            ", ".join(manquants),
            ", ".join(sorted(valeurs)),
        )
    groupes = valeurs.get("changements_campus_groupes")
    enseignants = valeurs.get("changements_campus_enseignants")
    changements = None
    if groupes is not None or enseignants is not None:
        changements = int(groupes or 0) + int(enseignants or 0)
    return Indicateurs(
        pourcentage_seances_placees=_nombre(valeurs.get("pourcentage_seances_placees")),
        pourcentage_utilisation_salles=_nombre(valeurs.get("pourcentage_utilisation_salles")),
        changements_campus=changements,
        trous_creneaux=_entier(valeurs.get("trous_creneaux")),
        nb_semaines=_entier(valeurs.get("nb_semaines")),
    )


def _en_dictionnaire(objet: Any) -> dict[str, Any]:
    if isinstance(objet, Mapping):
        return dict(objet)
    vers_dict = getattr(objet, "vers_dict", None)
    if callable(vers_dict):
        return dict(vers_dict())
    if dataclasses.is_dataclass(objet) and not isinstance(objet, type):
        return {champ.name: getattr(objet, champ.name) for champ in dataclasses.fields(objet)}
    return dict(vars(objet))


def _nombre(valeur: Any) -> float | None:
    return None if valeur is None else float(valeur)


def _entier(valeur: Any) -> int | None:
    return None if valeur is None else int(valeur)


# --------------------------------------------------------------------------- #
# Résolution tardive
# --------------------------------------------------------------------------- #


def _fonction(module: str, nom: str, message: str) -> Callable[..., Any]:
    """La fonction `module.nom`, ou `ErreurRaccordIndisponible` si elle n'existe pas encore."""
    complet = f"{module}.{nom}"
    try:
        espace = importlib.import_module(module)
    except ModuleNotFoundError as erreur:
        if erreur.name != module:
            raise  # module livré mais cassé : ce n'est pas un raccord en attente
        journal.warning("Raccord indisponible : le module %s n'existe pas encore", module)
        raise ErreurRaccordIndisponible(complet, message) from erreur
    fonction = getattr(espace, nom, None)
    if not callable(fonction):
        journal.warning("Raccord indisponible : %s n'existe pas encore", complet)
        raise ErreurRaccordIndisponible(complet, message)
    return fonction


def _appeler(module: str, nom: str, message: str, *args: Any, **kwargs: Any) -> Any:
    fonction = _fonction(module, nom, message)
    try:
        return fonction(*args, **kwargs)
    except ErreurRaccordIndisponible:
        raise
    except ErreurNonLivre as erreur:
        journal.warning("Raccord indisponible : %s.%s n'est pas livré (%s)", module, nom, erreur)
        raise ErreurRaccordIndisponible(f"{module}.{nom}", message) from erreur
