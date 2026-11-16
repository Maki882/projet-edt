"""Chargement d'un établissement fictif et remise à zéro de la base.

Fonctions appelées par l'écran de configuration et par la commande
`python -m edt jeu`. Le contenu des jeux est décrit dans
`docs/donnees-fictives.md` ; la génération elle-même est dans
`edt.synthese.generateur`.

- `tailles_disponibles()` : les tailles proposées, avec leur description ;
- `charger_jeu_fictif(session, taille, ...)` : charge un jeu dans la base de
  la session, qui doit être initialisée et vide de référentiels ; ne valide
  pas la transaction (convention des services) ;
- `remettre_a_zero(...)` : supprime toutes les données et recrée la base
  avec ses données par défaut ;
- `recharger_jeu_fictif(taille, ...)` : remise à zéro facultative puis
  chargement, dans ses propres sessions. C'est la fonction à appeler depuis
  une route web : une remise à zéro supprime les tables et ne doit pas se
  faire pendant qu'une session de requête est ouverte sur la base.

Toutes refusent d'agir pendant un calcul (`ErreurCalculEnCours`) : un calcul
lit la base pendant plusieurs secondes et ne doit pas la voir changer.

Un seul chargement ou une seule remise à zéro à la fois dans le processus :
un verrou non bloquant les sérialise, et une seconde demande pendant la
première est refusée tout de suite (`ErreurChargementEnCours`) au lieu de
supprimer et recréer les tables en même temps qu'elle.
`operation_en_cours()` dit si l'une d'elles tourne (pour expliquer une base
momentanément occupée). Le verrou est propre au processus ; pour qu'une
commande lancée dans un autre processus (`edt jeu`, avec ou sans `--reset`)
soit vue du serveur, l'opération pose aussi un **fichier marqueur** à côté de
la base (`edt.db.operation`, numéro du processus et action), retiré à la fin.
Un marqueur laissé par un processus arrêté sans le retirer est ignoré dès que
ce processus n'existe plus, et dans tous les cas passé `DUREE_MARQUEUR_S`.
Limite : `init-db --reset` recrée la base sans passer par ce service, donc
sans marqueur.

Un échec du chargement après la remise à zéro (panne ou refus) est dit tel
quel : la base a été vidée et aucun jeu n'est chargé (`ErreurRegleMetier`,
trace au journal), au lieu d'une exception brute ou d'un refus qui laisse
croire que les données d'avant sont toujours là. Le jeu lui-même s'écrit dans
une seule transaction : il est chargé en entier ou pas du tout.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from edt.config import charger_configuration
from edt.db.initialisation import (
    RapportInitialisation,
    base_initialisee,
    exiger_schema_a_jour,
    initialiser_base,
    tables_manquantes,
)
from edt.db.moteur import chemin_base, obtenir_moteur, session_base, transaction_immediate
from edt.erreurs import ErreurCalculEnCours, ErreurEDT, ErreurRegleMetier
from edt.services.generation import _processus_vivant, calcul_en_cours
from edt.synthese.configuration import CONFIGURATIONS, ConfigurationJeu, configuration_nommee
from edt.synthese.generateur import generer_jeu, valider_demande
from edt.synthese.rapport import RapportJeu

journal = logging.getLogger(__name__)

_verrou_base = threading.Lock()
"""Pris pendant tout chargement de jeu fictif ou toute remise à zéro (non réentrant)."""

DUREE_MARQUEUR_S = 600.0
"""Âge au-delà duquel un fichier marqueur d'opération est tenu pour périmé (processus mort)."""


class ErreurChargementEnCours(ErreurRegleMetier):
    """Un chargement de jeu fictif ou une remise à zéro tourne déjà : un seul à la fois."""


def fichier_marqueur(url: str | None = None) -> Path | None:
    """Fichier posé à côté de la base pendant une opération (`<base>.operation`), ou `None`."""
    chemin = chemin_base(url or charger_configuration().db_url)
    return None if chemin is None else chemin.with_name(chemin.name + ".operation")


def operation_en_cours(url: str | None = None) -> bool:
    """Vrai si un chargement de jeu fictif ou une remise à zéro tourne, ici ou ailleurs.

    « Ailleurs » : un autre processus, toujours vivant, a posé le fichier
    marqueur il y a moins de `DUREE_MARQUEUR_S` secondes. Un marqueur dont le
    processus n'existe plus (arrêté sans le retirer) est ignoré.
    """
    if _verrou_base.locked():
        return True
    marqueur = fichier_marqueur(url)
    if marqueur is None:
        return False
    try:
        age = time.time() - marqueur.stat().st_mtime
        contenu = marqueur.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False  # pas de marqueur (ou disparu entre-temps)
    if age > DUREE_MARQUEUR_S:
        journal.warning(
            "Marqueur d'opération %s vieux de %.0f s : tenu pour périmé et ignoré", marqueur, age
        )
        return False
    pid = _pid_du_marqueur(contenu)
    # Le verrou n'est pas tenu ici : un marqueur à notre numéro est un reste, pas une opération.
    if pid is not None and (pid == os.getpid() or not _processus_vivant(pid)):
        journal.warning(
            "Marqueur d'opération %s laissé par le processus %d, qui ne l'exécute plus : ignoré",
            marqueur,
            pid,
        )
        return False
    return True


def _pid_du_marqueur(contenu: str) -> int | None:
    """Le numéro de processus écrit en tête du marqueur, ou `None` s'il est illisible."""
    debut = contenu.split(maxsplit=1)[0] if contenu.strip() else ""
    if not (debut.isascii() and debut.isdigit() and len(debut) <= 10):
        journal.warning("Marqueur d'opération illisible (%r) : tenu pour actif", contenu[:40])
        return None
    return int(debut)


@contextmanager
def _seul_a_la_fois(action: str, url: str | None = None) -> Iterator[None]:
    """Exécute le bloc sous le verrou ; refuse tout de suite si une autre opération le tient.

    Pose le fichier marqueur pour les autres processus et le retire à la fin.
    """
    if not _verrou_base.acquire(blocking=False):
        journal.warning(
            "Demande refusée (%s) : un chargement ou une remise à zéro est en cours", action
        )
        raise ErreurChargementEnCours(
            f"Impossible de {action} : un chargement de jeu de données ou une remise à zéro "
            "est déjà en cours. Attendre quelques secondes, puis recharger la page."
        )
    marqueur = fichier_marqueur(url)
    try:
        if marqueur is not None:
            try:
                marqueur.write_text(f"{os.getpid()} {action}", encoding="utf-8")
            except OSError as erreur:
                journal.warning("Marqueur d'opération %s non écrit : %s", marqueur, erreur)
                marqueur = None
        yield
    finally:
        if marqueur is not None:
            try:
                marqueur.unlink(missing_ok=True)
            except OSError as erreur:
                journal.warning("Marqueur d'opération %s non retiré : %s", marqueur, erreur)
        _verrou_base.release()


@dataclass(frozen=True)
class TailleJeu:
    """Une taille de jeu proposée à l'utilisateur."""

    nom: str
    description: str
    nb_promotions: int
    nb_classes: int
    nb_salles: int
    semestres: tuple[str, ...]


_DESCRIPTIONS = {
    "petit": "Une promotion de 1re année, deux classes, le semestre 1 (essais rapides).",
    "moyen": "Une promotion complète de 3e année avec apprentis, langues, partiels et stage.",
    "grand": "L'établissement complet : cinq années, trois sites, les deux semestres.",
}


def tailles_disponibles() -> tuple[TailleJeu, ...]:
    """Les tailles de jeu fictif, de la plus petite à la plus grande."""
    return tuple(
        TailleJeu(
            nom=nom,
            description=_DESCRIPTIONS.get(nom, ""),
            nb_promotions=len(config.promotions),
            nb_classes=sum(p.nb_classes + p.nb_classes_apprentis for p in config.promotions),
            nb_salles=sum(salles.total for _, salles in config.salles),
            semestres=config.semestres,
        )
        for nom, config in CONFIGURATIONS.items()
    )


def charger_jeu_fictif(
    session: Session,
    taille: str | ConfigurationJeu = "moyen",
    graine: int = 0,
    *,
    annee_debut: int | None = None,
    enregistrer_reference: bool = False,
) -> RapportJeu:
    """Charge un établissement fictif dans la base de `session` et renvoie le rapport.

    La base doit être initialisée et vide de référentiels (`ErreurRegleMetier`
    sinon) ; une taille inconnue lève `ErreurDonneesInvalides`. Refusé
    pendant un calcul et pendant un autre chargement ou une remise à zéro
    (`ErreurChargementEnCours`). Ne valide pas la transaction.

    Écrivain long : la transaction de `session` s'ouvre en `BEGIN IMMEDIATE`
    (`transaction_immediate`), le contrôle « base vide » se fait donc sous le
    verrou d'écriture ; `session` ne doit pas encore avoir exécuté de requête
    (`ErreurConfiguration` sinon).
    """
    with _seul_a_la_fois("charger un jeu fictif"):
        return _charger(session, taille, graine, annee_debut, enregistrer_reference)


def _charger(
    session: Session,
    taille: str | ConfigurationJeu,
    graine: int,
    annee_debut: int | None,
    enregistrer_reference: bool,
    *,
    base_recreee: bool = False,
) -> RapportJeu:
    """Charge le jeu dans la transaction de `session` (sous `_seul_a_la_fois`).

    `base_recreee` : la même opération vient de recréer la base à partir du
    modèle (remise à zéro) ; le schéma n'est pas recontrôlé, et le calcul en
    cours l'a été juste avant la remise à zéro.
    """
    # Verrou d'écriture pris d'emblée : un second chargement (autre processus) attend son tour
    # puis trouve la base garnie, au lieu de lire puis d'échouer « database is locked ».
    transaction_immediate(session)
    if not base_recreee:
        moteur = session.get_bind()
        exiger_schema_a_jour(moteur)  # schéma périmé : le remède est « --reset », pas « init-db »
        if tables_manquantes(moteur):
            raise ErreurRegleMetier(
                "La base n'est pas initialisée : lancer d'abord « python -m edt init-db », "
                "ou demander la remise à zéro."
            )
        _refuser_pendant_un_calcul("charger un jeu fictif")  # tables présentes : contrôle direct
    configuration = taille if isinstance(taille, ConfigurationJeu) else configuration_nommee(taille)
    journal.info("Chargement du jeu fictif « %s » (graine %d) demandé", configuration.nom, graine)
    return generer_jeu(
        session,
        configuration,
        graine,
        annee_debut=annee_debut,
        enregistrer_reference=enregistrer_reference,
    )


def remettre_a_zero(
    *, aujourdhui: dt.date | None = None, url: str | None = None
) -> RapportInitialisation:
    """Supprime toutes les données puis recrée la base et ses données par défaut.

    `aujourdhui` choisit l'année scolaire recréée (défaut : la date du jour) ;
    `url` désigne la base (défaut : `EDT_DB_URL`). Refusé pendant un calcul
    et pendant un chargement ou une autre remise à zéro.
    """
    with _seul_a_la_fois("remettre la base à zéro", url):
        return _remettre_a_zero(aujourdhui, url, "remettre la base à zéro")


def _remettre_a_zero(
    aujourdhui: dt.date | None, url: str | None, action: str
) -> RapportInitialisation:
    """Remise à zéro refusée pendant un calcul ; `action` nomme la demande dans le refus.

    Les connexions du moteur sont ensuite renouvelées : aucune ne garde un état
    de la base d'avant la suppression des tables.
    """
    _exiger_aucun_calcul(action, url)
    journal.warning("Remise à zéro de la base demandée : toutes les données seront supprimées")
    rapport = initialiser_base(reset=True, aujourdhui=aujourdhui, url=url)
    obtenir_moteur(url).dispose()
    return rapport


def recharger_jeu_fictif(
    taille: str | ConfigurationJeu = "moyen",
    graine: int = 0,
    *,
    reinitialiser: bool = False,
    aujourdhui: dt.date | None = None,
    annee_debut: int | None = None,
    enregistrer_reference: bool = False,
    url: str | None = None,
) -> RapportJeu:
    """Remet la base à zéro si demandé, puis y charge un jeu fictif (sessions propres).

    Sans `reinitialiser`, la base doit déjà être initialisée et vide de
    référentiels. Une taille inconnue, une graine ou une année de début hors
    bornes sont refusées avant toute action destructrice. Le jeu est validé en
    base si la génération réussit, rien n'est écrit sinon. Refusé pendant un
    calcul, et pendant un autre chargement ou une remise à zéro
    (`ErreurChargementEnCours`) : la remise à zéro et le chargement se font
    sous le même verrou, sans interruption. Après la remise à zéro, le schéma
    tout juste recréé à partir du modèle n'est pas recontrôlé.
    """
    configuration = taille if isinstance(taille, ConfigurationJeu) else configuration_nommee(taille)
    # Une graine ou une année de début hors bornes sont refusées ici, AVANT la remise à
    # zéro : la génération les refuserait aussi, mais une fois les données effacées.
    valider_demande(graine, annee_debut)
    with _seul_a_la_fois("charger un jeu fictif", url):
        if reinitialiser:
            _remettre_a_zero(aujourdhui, url, "charger un jeu fictif")
        try:
            with session_base(url) as session:
                return _charger(
                    session,
                    configuration,
                    graine,
                    annee_debut,
                    enregistrer_reference,
                    base_recreee=reinitialiser,
                )
        except Exception as erreur:
            if not reinitialiser:
                raise  # rien n'a été écrit : le refus ou la panne remonte tel quel
            # Les données d'avant sont déjà effacées : le message doit le dire, même pour un refus.
            if isinstance(erreur, ErreurEDT):
                journal.warning(
                    "Chargement du jeu « %s » refusé après la remise à zéro : %s",
                    configuration.nom,
                    erreur.message,
                )
                cause = erreur.message
            else:
                journal.exception(
                    "Chargement du jeu « %s » en échec après la remise à zéro", configuration.nom
                )
                cause = f"{type(erreur).__name__} : {erreur}"
            raise ErreurRegleMetier(
                f"La base a été remise à zéro, mais le chargement du jeu « {configuration.nom} » "
                f"a échoué ({cause}) : aucun jeu n'est chargé, la base est vide avec ses données "
                "par défaut. Réessayer le chargement ; la trace est dans le journal."
            ) from erreur


def _exiger_aucun_calcul(action: str, url: str | None = None) -> None:
    if not base_initialisee(obtenir_moteur(url)):
        # Base neuve ou vide de tables : aucun calcul ne peut y tourner, et interroger
        # la table « calcul » planterait (« no such table »).
        journal.info("Base non initialisée : aucun calcul à attendre avant de %s", action)
        return
    _refuser_pendant_un_calcul(action)


def _refuser_pendant_un_calcul(action: str) -> None:
    """Lève `ErreurCalculEnCours` si un calcul tourne ; la base doit avoir ses tables."""
    suivi = calcul_en_cours()
    if suivi is not None:
        raise ErreurCalculEnCours(
            f"Impossible de {action} : un calcul d'emploi du temps est en cours "
            f"(calcul n°{suivi.id}). Attendre sa fin ou l'arrêter."
        )
