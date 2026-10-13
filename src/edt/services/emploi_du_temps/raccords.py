"""Raccords vers les fonctions des autres lots : le SEUL endroit où les écrans les appellent.

Les pré-vérifications, le vérificateur et l'instanciation datée sont écrits
par d'autres lots ; tous sont livrés. Leurs noms et signatures :

- `edt.services.generation.pre_verifier_semestre(session, semestre_id,
  contraintes_desactivees=(), *, niveaux=None, version_base_id=None,
  tolerer_violations_heritees=False) -> list[Anomalie]` (`niveaux` prime
  sur `contraintes_desactivees`) ;
- `edt.services.verification.verifier_version(session, version_id,
  contraintes_actives=None) -> RapportVerification` ;
- `edt.services.verification.verifier_occurrences_version(session,
  version_id, date_debut=None, date_fin=None, contraintes_actives=None)
  -> RapportVerification` ;
- `edt.services.verification.verifier_modification(session, version_id,
  seance_id, jour, debut, fin, salle_id, *, contraintes_actives=None)
  -> RapportVerification` (les anomalies que le déplacement de la séance
  introduit ou aggrave, sans rien enregistrer) ;
- `edt.services.instanciation.instancier_version(session, version_id,
  contraintes_desactivees=None) -> RapportInstanciation`.

Chaque fonction est importée **au moment de l'appel** : le raccord reste un
garde-fou. Si le module ou la fonction venait à manquer, ou si elle lève
`ErreurNonLivre`, le raccord
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

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurEDT, ErreurNonLivre
from edt.services.emploi_du_temps import memoire
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
    decochees: tuple[Anomalie, ...] = ()
    contraintes_decochees: tuple[str, ...] = ()

    @property
    def disponible(self) -> bool:
        """Vrai si la vérification a tourné."""
        return self.verification is not None

    @property
    def nb_ecarts_decoches(self) -> int:
        """Erreurs sur des contraintes décochées lors du calcul de la version (tolérées)."""
        return sum(1 for a in self.decochees if a.gravite is Gravite.ERREUR)


# --------------------------------------------------------------------------- #
# Raccords
# --------------------------------------------------------------------------- #


def pre_verifier_semestre(
    session: Session,
    semestre_id: int,
    contraintes_desactivees: Iterable[str] = (),
    *,
    niveaux: Mapping[str, str] | None = None,
    niveaux_regles: Mapping[int, object] | None = None,
) -> tuple[Anomalie, ...]:
    """Pré-vérifications d'un semestre avant un calcul (charges, salles accessibles, verrous...).

    `niveaux` (niveau de chaque règle, clé absente : règle ignorée) prime sur
    `contraintes_desactivees`.
    """
    arguments: dict[str, object] = {"contraintes_desactivees": tuple(contraintes_desactivees)}
    if niveaux is not None:
        arguments = {"niveaux": dict(niveaux)}
    if niveaux_regles is not None:
        arguments["niveaux_regles"] = dict(niveaux_regles)
    anomalies = _appeler(
        MODULE_GENERATION,
        "pre_verifier_semestre",
        MESSAGE_PRE_VERIFICATION,
        session,
        semestre_id,
        **arguments,
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
    contraintes_actives: Iterable[str] | None = None,
) -> Verification:
    """Anomalies qu'introduirait la séance `seance_id` placée ainsi (rien n'est enregistré).

    `contraintes_actives` : codes à vérifier **en dur** (niveaux ignorés). Une
    retouche n'en passe pas : `None` laisse le vérificateur appliquer les
    niveaux du calcul de la version, règles ignorées exclues.
    """
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
        contraintes_actives=contraintes_actives,
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
# Contrôles d'une version pour les écrans de consultation
# --------------------------------------------------------------------------- #


def contraintes_desactivees_de(session: Session, version_id: int) -> frozenset[str]:
    """Contraintes décochées lors du calcul qui a produit la version (aucune pour un import)."""
    codes = session.scalar(
        select(m.Calcul.contraintes_desactivees)
        .join(m.VersionEdt, m.VersionEdt.calcul_id == m.Calcul.id)
        .where(m.VersionEdt.id == version_id)
    )
    return frozenset(str(code) for code in codes or ())


def controler_version(session: Session, version_id: int) -> Controle:
    """Contrôle de la semaine type d'une version, mémorisé tant que la base ne change pas.

    La vérification porte sur toutes les contraintes ; les anomalies sur une
    contrainte décochée lors du calcul de la version sont mises à part
    (`Controle.decochees`) : elles sont tolérées, mais dites.
    """
    controle = memoire.memoriser(
        session,
        "verifier_version",
        (version_id,),
        lambda: controler(verifier_version, session, version_id),
    )
    return _separer(controle, contraintes_desactivees_de(session, version_id))


def controler_occurrences(
    session: Session, version_id: int, date_debut=None, date_fin=None
) -> Controle:
    """Contrôle des séances datées d'une version (ou d'une période), mémorisé ; voir `controler_version`.

    Toutes les séances datées de la version sont vérifiées une fois (résultat
    mémorisé, partagé par « À traiter », l'accueil et chaque semaine
    affichée) ; une période ne garde que les anomalies qui la concernent :
    date de l'anomalie ou date d'une séance qu'elle cite.
    """
    complet, dates = memoire.memoriser(
        session,
        "verifier_occurrences_version",
        (version_id,),
        lambda: _controle_date_complet(session, version_id),
    )
    controle = complet
    if (date_debut is not None or date_fin is not None) and complet.verification is not None:
        gardees = tuple(
            anomalie
            for anomalie in complet.verification.anomalies
            if _dans_la_periode(anomalie, dates, date_debut, date_fin)
        )
        controle = dataclasses.replace(
            complet,
            verification=Verification(
                anomalies=gardees, indicateurs=complet.verification.indicateurs
            ),
        )
    return _separer(controle, contraintes_desactivees_de(session, version_id))


def _controle_date_complet(session: Session, version_id: int) -> tuple[Controle, dict]:
    """Vérification de toutes les séances datées, et date de chaque séance citée."""
    controle = controler(verifier_occurrences_version, session, version_id)
    dates: dict[int, object] = {}
    if controle.verification is not None:
        citees = sorted({o for a in controle.verification.anomalies for o in a.occurrences})
        for debut in range(0, len(citees), 500):
            lot = citees[debut : debut + 500]
            dates.update(
                session.execute(
                    select(m.Occurrence.id, m.Occurrence.date).where(m.Occurrence.id.in_(lot))
                ).all()
            )
    return controle, dates


def _dans_la_periode(anomalie: Anomalie, dates: dict, date_debut, date_fin) -> bool:
    jours = [anomalie.date] if anomalie.date is not None else []
    jours.extend(dates[o] for o in anomalie.occurrences if o in dates)
    return any(
        (date_debut is None or date_debut <= jour) and (date_fin is None or jour <= date_fin)
        for jour in jours
    )


def _separer(controle: Controle, desactivees: frozenset[str]) -> Controle:
    """Met à part les anomalies sur des contraintes décochées lors du calcul de la version."""
    if controle.verification is None or not desactivees:
        return dataclasses.replace(controle, contraintes_decochees=tuple(sorted(desactivees)))
    actives = tuple(a for a in controle.verification.anomalies if str(a.code) not in desactivees)
    decochees = tuple(a for a in controle.verification.anomalies if str(a.code) in desactivees)
    return dataclasses.replace(
        controle,
        verification=Verification(anomalies=actives, indicateurs=controle.verification.indicateurs),
        decochees=decochees,
        contraintes_decochees=tuple(sorted(desactivees)),
    )


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
