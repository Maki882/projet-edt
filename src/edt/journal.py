"""Configuration unique de la journalisation (module `logging`).

Un seul endroit configure les journaux de tout le processus :

- la console (flux d'erreur standard) ;
- le fichier tournant `<EDT_LOG_DIR>/edt.log` ;
- un fichier par calcul dans `<EDT_LOG_DIR>/generations/`, ouvert par
  `ouvrir_journal_calcul()` pendant la durée du calcul.

Chaque ligne porte la date, le niveau, le module et, quand il existe,
l'identifiant de la requête web (`req=...`) ou du calcul (`calcul=...`).
Ces identifiants vivent dans des variables de contexte : le code métier n'a
rien à passer, il écrit simplement `journal = logging.getLogger(__name__)`.

Convention pour tout le projet : un journal par module, jamais de `print`
dans les services, `journal.exception(...)` dans tout bloc `except` qui ne
relance pas l'erreur.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from edt.config import Configuration, charger_configuration
from edt.erreurs import ErreurConfiguration

FORMAT = "%(asctime)s %(levelname)-8s [%(contexte)s] %(name)s : %(message)s"
NOM_FICHIER = "edt.log"
DOSSIER_CALCULS = "generations"
TAILLE_MAX_OCTETS = 5 * 1024 * 1024
NB_ARCHIVES = 5

#: Identifiant de la requête web en cours de traitement (posé par l'intergiciel).
contexte_requete: ContextVar[str | None] = ContextVar("edt_id_requete", default=None)
#: Identifiant du calcul en cours dans ce fil d'exécution (posé par `services.generation`).
contexte_calcul: ContextVar[int | None] = ContextVar("edt_id_calcul", default=None)

_PREFIXE_JOURNAL_CALCUL = "edt.calcul."
_MARQUE = "_edt_socle"
_etat: dict[str, object] = {"signature": None}

journal = logging.getLogger(__name__)


class _FiltreContexte(logging.Filter):
    """Ajoute à chaque enregistrement le champ `contexte` utilisé par le format."""

    def filter(self, record: logging.LogRecord) -> bool:
        id_calcul = contexte_calcul.get()
        id_requete = contexte_requete.get()
        if id_calcul is not None:
            record.contexte = f"calcul={id_calcul}"
        elif record.name.startswith(_PREFIXE_JOURNAL_CALCUL):
            record.contexte = "calcul=" + record.name.removeprefix(_PREFIXE_JOURNAL_CALCUL)
        elif id_requete is not None:
            record.contexte = f"req={id_requete}"
        else:
            record.contexte = "-"
        return True


class _FiltreCalcul(logging.Filter):
    """Ne laisse passer que les enregistrements du calcul `id_calcul`.

    Un enregistrement appartient au calcul s'il est émis dans le fil du calcul
    (variable de contexte) ou sur le journal nommé `edt.calcul.<id>` (cas d'un
    rappel exécuté dans un autre fil, par exemple le journal de CP-SAT).
    """

    def __init__(self, id_calcul: int) -> None:
        super().__init__()
        self._id_calcul = id_calcul
        self._nom = f"{_PREFIXE_JOURNAL_CALCUL}{id_calcul}"

    def filter(self, record: logging.LogRecord) -> bool:
        return contexte_calcul.get() == self._id_calcul or record.name == self._nom


class _ConsoleCourante(logging.StreamHandler):
    """Écrit sur le `sys.stderr` du moment (et non celui du jour de la configuration).

    Les lanceurs de tests remplacent `sys.stderr` : garder une référence figée
    ferait écrire dans un flux fermé.
    """

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _valeur) -> None:
        # Le flux est toujours relu dans `sys.stderr` : l'affectation est sans effet.
        return


def _creer_dossier(dossier: Path) -> None:
    try:
        dossier.mkdir(parents=True, exist_ok=True)
    except OSError as erreur:
        raise ErreurConfiguration(
            f"Impossible de créer le dossier des journaux « {dossier} » : {erreur}. "
            "Vérifier la variable EDT_LOG_DIR et les droits d'écriture."
        ) from erreur


def _retirer_gestionnaires() -> None:
    racine = logging.getLogger()
    for gestionnaire in list(racine.handlers):
        if getattr(gestionnaire, _MARQUE, False):
            racine.removeHandler(gestionnaire)
            gestionnaire.close()


def configurer_journalisation(config: Configuration | None = None) -> Path:
    """Configure la journalisation du processus et renvoie le chemin de `edt.log`.

    L'appel est idempotent : rappelé avec la même configuration, il ne fait
    rien ; avec une configuration différente (autre dossier, autre niveau),
    il remplace les gestionnaires précédents.
    """
    config = config or charger_configuration()
    dossier = config.log_dir
    chemin = dossier / NOM_FICHIER
    signature = (str(dossier.resolve()), config.log_level)
    if _etat["signature"] == signature:
        return chemin

    _creer_dossier(dossier)
    _retirer_gestionnaires()

    formateur = logging.Formatter(FORMAT)
    filtre = _FiltreContexte()

    console = _ConsoleCourante()
    fichier = RotatingFileHandler(
        chemin, maxBytes=TAILLE_MAX_OCTETS, backupCount=NB_ARCHIVES, encoding="utf-8"
    )
    racine = logging.getLogger()
    for gestionnaire in (console, fichier):
        gestionnaire.setFormatter(formateur)
        gestionnaire.addFilter(filtre)
        setattr(gestionnaire, _MARQUE, True)
        racine.addHandler(gestionnaire)
    racine.setLevel(config.log_level)

    _etat["signature"] = signature
    journal.debug("Journalisation configurée : %s (niveau %s)", chemin, config.log_level)
    return chemin


def reinitialiser_journalisation() -> None:
    """Retire et ferme les gestionnaires posés par `configurer_journalisation`.

    Sert aux tests (libérer les fichiers d'un dossier temporaire) et avant une
    reconfiguration complète.
    """
    _retirer_gestionnaires()
    _etat["signature"] = None


@dataclass
class JournalCalcul:
    """Journal dédié d'un calcul : un fichier dans `<EDT_LOG_DIR>/generations/`.

    À utiliser comme gestionnaire de contexte dans le fil du calcul : à
    l'entrée, la variable de contexte du calcul est posée et le fichier
    reçoit tout ce qui est journalisé pendant le calcul ; à la sortie, le
    fichier est fermé.
    """

    id_calcul: int
    chemin: Path
    journal: logging.Logger
    _gestionnaire: logging.Handler
    _jeton: object | None = None

    def __enter__(self) -> JournalCalcul:
        self._jeton = contexte_calcul.set(self.id_calcul)
        return self

    def __exit__(self, *_exc) -> None:
        self.fermer()

    def fermer(self) -> None:
        """Ferme le fichier et retire le contexte du calcul (sans effet si déjà fait)."""
        logging.getLogger().removeHandler(self._gestionnaire)
        self._gestionnaire.close()
        if self._jeton is not None:
            contexte_calcul.reset(self._jeton)  # type: ignore[arg-type]
            self._jeton = None


def ouvrir_journal_calcul(id_calcul: int, config: Configuration | None = None) -> JournalCalcul:
    """Crée le journal dédié du calcul `id_calcul` et renvoie son descripteur.

    Le fichier s'appelle `calcul-<id sur 6 chiffres>-<date>-<heure>.log`. Il
    reçoit, en plus de `edt.log`, tout enregistrement émis dans le fil du
    calcul ou sur le journal `edt.calcul.<id>` (voir `journal_calcul_courant`).
    """
    config = config or charger_configuration()
    dossier = config.log_dir / DOSSIER_CALCULS
    _creer_dossier(dossier)
    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    chemin = dossier / f"calcul-{id_calcul:06d}-{horodatage}.log"

    gestionnaire = logging.FileHandler(chemin, encoding="utf-8")
    gestionnaire.setFormatter(logging.Formatter(FORMAT))
    gestionnaire.addFilter(_FiltreContexte())
    gestionnaire.addFilter(_FiltreCalcul(id_calcul))
    gestionnaire.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(gestionnaire)

    return JournalCalcul(
        id_calcul=id_calcul,
        chemin=chemin,
        journal=logging.getLogger(f"{_PREFIXE_JOURNAL_CALCUL}{id_calcul}"),
        _gestionnaire=gestionnaire,
    )


def journal_calcul_courant() -> logging.Logger:
    """Renvoie le journal du calcul en cours dans ce fil d'exécution.

    À appeler dans le fil du calcul (par exemple au début de `resoudre`) puis
    à conserver : les messages écrits sur ce journal arrivent dans le fichier
    du calcul même s'ils sont émis plus tard depuis un autre fil (rappels du
    solveur). Hors de tout calcul, renvoie le journal générique `edt.calcul`.
    """
    id_calcul = contexte_calcul.get()
    if id_calcul is None:
        return logging.getLogger("edt.calcul")
    return logging.getLogger(f"{_PREFIXE_JOURNAL_CALCUL}{id_calcul}")
