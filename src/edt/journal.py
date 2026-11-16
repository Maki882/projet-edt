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

Robustesse : un fichier journal impossible à ouvrir est une
`ErreurConfiguration` (chemin, variable et droits nommés), jamais une trace ;
le fichier tournant survit à un autre processus qui écrit le même fichier
(commande `edt` lancée pendant que le service tourne) ; les journaux de
calcul les plus anciens sont purgés (`NB_JOURNAUX_CALCUL` conservés).
"""

from __future__ import annotations

import logging
import os
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
NB_JOURNAUX_CALCUL = 50
"""Journaux de calcul conservés dans `generations/` : au-delà, les plus anciens sont supprimés."""

#: Identifiant de la requête web en cours de traitement (posé par l'intergiciel).
contexte_requete: ContextVar[str | None] = ContextVar("edt_id_requete", default=None)
#: Identifiant du calcul en cours dans ce fil d'exécution (posé par `services.generation`).
contexte_calcul: ContextVar[int | None] = ContextVar("edt_id_calcul", default=None)

_PREFIXE_JOURNAL_CALCUL = "edt.calcul."
_MARQUE = "_edt_socle"
_etat: dict[str, object] = {"signature": None, "panne": None}

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


class _FichierTournant(RotatingFileHandler):
    """Fichier tournant qui supporte qu'un autre processus écrive le même fichier.

    - la rotation commence par renommer le fichier courant ; si ce renommage
      est refusé (fichier tenu par un autre processus, cas de Windows), les
      archives restent intactes, l'écriture continue dans le fichier courant,
      une ligne d'avertissement y est écrite une fois, et la rotation est
      retentée à la ligne suivante ;
    - si un autre processus a tourné ou supprimé le fichier (le chemin désigne
      un autre fichier, ou plus rien), le flux est rouvert sur le chemin, comme
      le fait `WatchedFileHandler` : aucune ligne ne part dans un fichier délié.
      Une réouverture impossible passe par `handleError`, comme toute erreur
      d'écriture : elle ne remonte jamais dans le code qui journalise.
    """

    def __init__(self, *arguments, **options) -> None:
        super().__init__(*arguments, **options)
        self._empreinte = self._empreinte_actuelle()
        self._rotation_refusee = False

    def _empreinte_actuelle(self) -> tuple[int, int] | None:
        try:
            infos = os.stat(self.baseFilename)
        except OSError:
            return None
        return infos.st_dev, infos.st_ino

    def emit(self, record: logging.LogRecord) -> None:
        if self._empreinte_actuelle() == self._empreinte:
            super().emit(record)
            return
        # Le flux est fermé ici et rouvert par `RotatingFileHandler.emit`, sous sa protection.
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        super().emit(record)
        self._empreinte = self._empreinte_actuelle()

    def rotate(self, source: str, dest: str) -> None:
        """Renomme `source` en `dest`, en remplaçant `dest` s'il existe (Windows compris)."""
        os.replace(source, dest)

    def doRollover(self) -> None:  # noqa: N802 (nom imposé par logging)
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        en_rotation = f"{self.baseFilename}.rotation"
        try:
            # Premier geste : libérer le fichier courant. Tant qu'il est refusé, aucune
            # archive ne bouge.
            self.rotate(self.baseFilename, en_rotation)
        except OSError as erreur:
            self.stream = self._open()
            self._empreinte = self._empreinte_actuelle()
            if not self._rotation_refusee:
                self._rotation_refusee = True
                self._ecrire_avertissement(
                    "Rotation de %s impossible (%s) : écriture poursuivie dans ce fichier, "
                    "nouvel essai à chaque ligne.",
                    self.baseFilename,
                    erreur,
                )
            return
        self._rotation_refusee = False
        try:
            for rang in range(self.backupCount - 1, 0, -1):
                archive = f"{self.baseFilename}.{rang}"
                if os.path.exists(archive):
                    self.rotate(archive, f"{self.baseFilename}.{rang + 1}")
            self.rotate(en_rotation, f"{self.baseFilename}.1")
        finally:
            self.stream = self._open()
            self._empreinte = self._empreinte_actuelle()

    def _ecrire_avertissement(self, message: str, *arguments: object) -> None:
        """Écrit un avertissement directement dans le fichier courant, hors de la chaîne `logging`.

        Passer par `journal.warning` rappellerait ce gestionnaire pendant sa propre rotation.
        """
        avis = journal.makeRecord(
            journal.name, logging.WARNING, __file__, 0, message, arguments, None
        )
        avis.contexte = "-"
        self.stream.write(self.format(avis) + self.terminator)


def _creer_dossier(dossier: Path) -> None:
    try:
        dossier.mkdir(parents=True, exist_ok=True)
    except OSError as erreur:
        raise ErreurConfiguration(
            f"Impossible de créer le dossier des journaux « {dossier} » : {erreur}. "
            "Vérifier la variable EDT_LOG_DIR et les droits d'écriture."
        ) from erreur


def _ouvrir_fichier_tournant(chemin: Path) -> _FichierTournant:
    try:
        return _FichierTournant(
            chemin, maxBytes=TAILLE_MAX_OCTETS, backupCount=NB_ARCHIVES, encoding="utf-8"
        )
    except OSError as erreur:
        raise ErreurConfiguration(
            f"Impossible d'ouvrir le fichier journal « {chemin} » : {erreur}. Vérifier la "
            "variable EDT_LOG_DIR et les droits d'écriture (un dossier ou un fichier d'un autre "
            "utilisateur porte peut-être ce nom)."
        ) from erreur


def _ordre_journal_calcul(fichier: Path) -> tuple[str, str]:
    """Clé chronologique d'un journal de calcul : la date et l'heure portées par son nom.

    Nom `calcul-<numéro>-<date>-<heure>.log` : la date prime sur le numéro, qui repart
    de 1 après une remise à zéro de la base. Un nom d'une autre forme passe pour le
    plus ancien.
    """
    morceaux = fichier.stem.split("-", 2)
    return (morceaux[2] if len(morceaux) == 3 else "", fichier.name)


def _purger_journaux_calcul(dossier: Path, garder: int) -> int:
    """Supprime les journaux de calcul les plus anciens au-delà de `garder` ; renvoie le nombre."""
    fichiers = sorted(
        (f for f in dossier.glob("calcul-*.log") if f.is_file()),
        key=_ordre_journal_calcul,
        reverse=True,
    )
    supprimes = 0
    for fichier in fichiers[garder:]:
        try:
            fichier.unlink()
            supprimes += 1
        except OSError as erreur:
            journal.warning("Journal de calcul %s non supprimé : %s", fichier.name, erreur)
    if supprimes:
        journal.info(
            "Journaux de calcul : %d fichier(s) ancien(s) supprimé(s) de %s (%d conservés)",
            supprimes,
            dossier,
            garder,
        )
    return supprimes


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
    fichier = _ouvrir_fichier_tournant(chemin)
    _retirer_gestionnaires()

    formateur = logging.Formatter(FORMAT)
    filtre = _FiltreContexte()

    console = _ConsoleCourante()
    racine = logging.getLogger()
    for gestionnaire in (console, fichier):
        gestionnaire.setFormatter(formateur)
        gestionnaire.addFilter(filtre)
        setattr(gestionnaire, _MARQUE, True)
        racine.addHandler(gestionnaire)
    racine.setLevel(config.log_level)

    _etat["signature"] = signature
    _etat["panne"] = None
    journal.debug("Journalisation configurée : %s (niveau %s)", chemin, config.log_level)
    return chemin


def journalisation_de_secours(panne: str, config: Configuration | None = None) -> None:
    """Repli sur la console seule quand le fichier journal est inutilisable.

    Sert à `python -m edt doctor`, qui doit pouvoir diagnostiquer un dossier
    de journaux défaillant : la panne est mémorisée (`panne_journalisation`)
    et affichée dans son bilan.
    """
    config = config or charger_configuration()
    _retirer_gestionnaires()
    console = _ConsoleCourante()
    console.setFormatter(logging.Formatter(FORMAT))
    console.addFilter(_FiltreContexte())
    setattr(console, _MARQUE, True)
    racine = logging.getLogger()
    racine.addHandler(console)
    racine.setLevel(config.log_level)
    _etat["signature"] = None
    _etat["panne"] = panne
    journal.error("Journalisation en repli sur la console : %s", panne)


def panne_journalisation() -> str | None:
    """Message de la panne qui a imposé le repli console, sinon `None`."""
    panne = _etat["panne"]
    return str(panne) if panne else None


def reinitialiser_journalisation() -> None:
    """Retire et ferme les gestionnaires posés par `configurer_journalisation`.

    Sert aux tests (libérer les fichiers d'un dossier temporaire) et avant une
    reconfiguration complète.
    """
    _retirer_gestionnaires()
    _etat["signature"] = None
    _etat["panne"] = None


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
    _purger_journaux_calcul(dossier, max(NB_JOURNAUX_CALCUL - 1, 0))
    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    chemin = dossier / f"calcul-{id_calcul:06d}-{horodatage}.log"

    try:
        gestionnaire = logging.FileHandler(chemin, encoding="utf-8")
    except OSError as erreur:
        raise ErreurConfiguration(
            f"Impossible d'ouvrir le journal du calcul « {chemin} » : {erreur}. Vérifier la "
            "variable EDT_LOG_DIR, les droits d'écriture et l'espace disque."
        ) from erreur
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
