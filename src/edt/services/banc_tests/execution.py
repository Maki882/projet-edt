"""Exécution des tests dans un sous-processus pytest, suivie en direct.

    execution = lancer(cible, racine)        # rend la main tout de suite
    suivi = execution.suivi()                # à interroger chaque seconde
    demander_arret()                         # bouton « Arrêter »
    attendre(execution)                      # ligne de commande et tests

Déroulement
-----------
1. Garde-fous, dans l'ordre : lancement permis (`EDT_TESTS_LANCEMENT`), pytest
   installé, aucune exécution en cours (dans ce processus ou un autre : fichier
   `execution-en-cours.json` du dossier des résultats), aucun calcul d'emploi
   du temps en cours.
2. Un fil d'arrière-plan lance `sys.executable -m pytest <chemins du
   catalogue>` depuis la racine du dépôt, avec le greffon du banc
   (`-p edt.services.banc_tests.greffon`), sans cache ni couleur, et sans les
   tests de charge pour un lancement par groupe (`-m "not perf"`).
3. L'environnement du sous-processus est assaini (`environnement_assaini`) :
   aucune variable `EDT_*`, `PYTEST_*` ni `PYTHONPATH` héritée ; base et
   journaux dans un dossier temporaire propre à l'exécution, effacé à la fin :
   un test fautif ne peut pas toucher la base réelle.
4. Le fil lit au fur et à mesure le fichier des événements du greffon : chaque
   test passe à son résultat dès que tous ses cas sont terminés.
5. Arrêt demandé, ou durée maximale (`EDT_TESTS_DUREE_MAX_S`, 900 s par défaut)
   dépassée : le processus et ses enfants sont arrêtés par leur numéro exact
   (groupe de processus sous Linux, `taskkill /T` sous Windows).
6. À la fin, les résultats des tests terminés, les dates des groupes couverts
   en entier et le bilan sont écrits dans l'historique
   (`edt.services.banc_tests.resultats`) ; la sortie complète de pytest reste
   dans `derniere-execution.log`.

Serveur arrêté pendant une exécution : le fichier `execution-en-cours.json`
désigne un processus qui n'existe plus. Le prochain lecteur (page, lancement)
le retire et note au bilan une exécution « interrompue » : l'état « en cours »
ne reste jamais bloqué.
"""

from __future__ import annotations

import atexit
import contextlib
import datetime as dt
import importlib.util
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import edt
from edt.erreurs import ErreurConfiguration, ErreurRegleMetier
from edt.services.banc_tests import resultats as res
from edt.services.banc_tests.catalogue import Catalogue, Cible
from edt.services.banc_tests.greffon import VARIABLE_EVENEMENTS

journal = logging.getLogger(__name__)

VARIABLE_DUREE_MAX = "EDT_TESTS_DUREE_MAX_S"
DUREE_MAX_DEFAUT_S = 900
VARIABLE_LANCEMENT = "EDT_TESTS_LANCEMENT"
GREFFON = "edt.services.banc_tests.greffon"
FICHIER_VERROU = "execution-en-cours.json"
PAS_LECTURE_S = 0.2
"""Intervalle de lecture des événements et de surveillance du processus."""
DELAI_ARRET_S = 3.0
"""Sous Linux, délai laissé au processus après SIGTERM avant SIGKILL."""
CODES_PYTEST_NORMAUX = (0, 1, 5)
"""Codes de sortie de pytest d'une exécution allée à son terme (5 : aucun test retenu)."""


class ErreurTestsEnCours(ErreurRegleMetier):
    """Une exécution des tests est déjà en cours : une seule à la fois.

    `meme_cible` : la demande vise la même cible que l'exécution en cours (double clic).
    """

    def __init__(self, message: str, *, meme_cible: bool = False) -> None:
        super().__init__(message)
        self.meme_cible = meme_cible


class ErreurLancementImpossible(ErreurRegleMetier):
    """Le lancement des tests est refusé : désactivé, pytest absent ou calcul en cours."""


# --------------------------------------------------------------------------- #
# Réglages et disponibilité
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reglages:
    """Réglages du banc, lus dans l'environnement du serveur."""

    duree_max_s: int
    lancement_permis: bool


def lire_reglages() -> Reglages:
    """Lit `EDT_TESTS_DUREE_MAX_S` et `EDT_TESTS_LANCEMENT` ; `ErreurConfiguration` si invalides."""
    brut = os.environ.get(VARIABLE_DUREE_MAX, "").strip() or str(DUREE_MAX_DEFAUT_S)
    if not (brut.isascii() and brut.isdigit()) or int(brut) < 1:
        raise ErreurConfiguration(
            f"{VARIABLE_DUREE_MAX} doit être un nombre entier de secondes, au moins 1 "
            f"(valeur lue : {brut!r})."
        )
    lancement = os.environ.get(VARIABLE_LANCEMENT, "").strip().lower() or "oui"
    if lancement not in ("oui", "non"):
        raise ErreurConfiguration(
            f"{VARIABLE_LANCEMENT} doit valoir « oui » ou « non » (valeur lue : {lancement!r})."
        )
    return Reglages(duree_max_s=int(brut), lancement_permis=lancement == "oui")


def pytest_installe() -> bool:
    """Vrai si l'interpréteur du serveur (celui du sous-processus) sait importer pytest."""
    return importlib.util.find_spec("pytest") is not None


def raison_indisponible(catalogue: Catalogue) -> str | None:
    """Pourquoi les boutons de lancement sont désactivés, ou `None` s'ils sont permis.

    Le calcul en cours n'est pas regardé ici (il est vérifié au lancement).
    """
    if not lire_reglages().lancement_permis:
        return (
            "Le lancement des tests est désactivé sur ce serveur "
            f"({VARIABLE_LANCEMENT}=non) : les derniers résultats restent consultables."
        )
    if catalogue.racine is None or not catalogue.fichiers:
        return catalogue.absence or "Aucun test n'est disponible dans cette installation."
    if not pytest_installe():
        return (
            "Les tests ne peuvent pas être lancés : l'outil de test (pytest) n'est pas installé "
            "dans l'environnement du serveur. Installer les dépendances de développement du "
            "projet (extra « dev »)."
        )
    return None


def calcul_en_cours() -> bool:
    """Vrai si un calcul d'emploi du temps tourne (les tests attendent qu'il se termine).

    Sans base, ou sans table des calculs, aucun calcul ne peut tourner.
    """
    from sqlalchemy import inspect

    from edt.config import charger_configuration
    from edt.db.modeles import Calcul
    from edt.db.moteur import chemin_base, obtenir_moteur
    from edt.services import generation

    url = charger_configuration().db_url
    fichier = chemin_base(url)
    if fichier is not None and not fichier.is_file():
        return False
    if Calcul.__tablename__ not in inspect(obtenir_moteur(url)).get_table_names():
        return False
    return generation.calcul_en_cours() is not None


# --------------------------------------------------------------------------- #
# État d'une exécution
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Suivi:
    """Photographie d'une exécution, pour l'affichage.

    - `total` : nombre de tests prévus (connu exactement après la collecte) ;
    - `termines` : tests terminés, dans l'ordre (`resultats` donne leur résultat) ;
    - `test_en_cours` : clé du test qui tourne, ou `None` ;
    - `issue` : `None` tant que l'exécution tourne, puis l'une des issues de
      `edt.services.banc_tests.resultats` ;
    - `tests_cible` : clés des tests du catalogue couverts par la cible.
    """

    id: str
    cle_cible: str
    libelle: str
    famille: str | None
    debut: dt.datetime
    total: int
    termines: tuple[str, ...]
    resultats: dict[str, res.ResultatTest]
    test_en_cours: str | None
    tests_cible: frozenset[str]
    arret_demande: bool
    ecoule_s: float
    duree_max_s: int
    issue: str | None = None
    message: str = ""

    @property
    def en_cours(self) -> bool:
        """Vrai tant que l'exécution n'est pas terminée."""
        return self.issue is None

    def compte(self, etat: str) -> int:
        """Nombre de tests terminés dans l'état `etat`."""
        return sum(1 for r in self.resultats.values() if r.etat == etat)


@dataclass
class Execution:
    """Une exécution des tests (dans ce processus)."""

    id: str
    cible: Cible
    racine: Path
    dossier: Path
    duree_max_s: int
    debut: dt.datetime
    regroupement: res.Regroupement = field(default_factory=res.Regroupement)
    arret: threading.Event = field(default_factory=threading.Event)
    verrou: threading.Lock = field(default_factory=threading.Lock)
    fil: threading.Thread | None = None
    processus: subprocess.Popen | None = None
    depart: float = field(default_factory=time.monotonic)
    fin: float | None = None

    issue: str | None = None
    message: str = ""
    supplementaires: dict[str, res.ResultatTest] = field(default_factory=dict)
    """Résultats posés par le service (fichier non chargé, test interrompu par la durée maximale)."""

    @property
    def en_cours(self) -> bool:
        """Vrai tant que le fil de l'exécution tourne."""
        return self.issue is None

    def suivi(self) -> Suivi:
        """État courant, copié sous verrou (le fil d'exécution le modifie)."""
        with self.verrou:
            reg = self.regroupement
            resultats = {**reg.resultats, **self.supplementaires}
            termines = tuple(reg.termines) + tuple(
                cle for cle in self.supplementaires if cle not in reg.resultats
            )
            total = len(reg.attendus) if reg.collecte_recue else len(self.cible.tests)
            return Suivi(
                id=self.id,
                cle_cible=self.cible.cle,
                libelle=self.cible.libelle,
                famille=self.cible.famille,
                debut=self.debut,
                total=max(total, len(termines)),
                termines=termines,
                resultats=resultats,
                test_en_cours=reg.en_cours if self.issue is None else None,
                tests_cible=frozenset(self.cible.tests),
                arret_demande=self.arret.is_set(),
                ecoule_s=(self.fin or time.monotonic()) - self.depart,
                duree_max_s=self.duree_max_s,
                issue=self.issue,
                message=self.message,
            )


_verrou = threading.RLock()
"""Réentrant : `lancer` le tient pendant qu'il examine le fichier de verrou, qui le reprend."""
_courante: Execution | None = None
"""Exécution en cours, ou la dernière de ce processus."""


def execution_courante() -> Execution | None:
    """L'exécution en cours dans ce processus, ou la dernière terminée, ou `None`."""
    with _verrou:
        return _courante


def execution(identifiant: str) -> Execution | None:
    """L'exécution `identifiant` si c'est celle que ce processus garde, sinon `None`."""
    with _verrou:
        return _courante if _courante is not None and _courante.id == identifiant else None


# --------------------------------------------------------------------------- #
# Verrou entre processus (serveur et ligne de commande)
# --------------------------------------------------------------------------- #


def _lire_verrou(chemin: Path) -> dict | None:
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as erreur:
        journal.warning("Fichier %s illisible (%s) : tenu pour abandonné", chemin, erreur)
        return {}
    return donnees if isinstance(donnees, dict) else {}


def _proprietaire_vivant(info: dict) -> bool:
    from edt.services.generation import _processus_vivant

    pid = info.get("pid")
    if not isinstance(pid, int):
        return False
    if pid == os.getpid():
        with _verrou:
            return (
                _courante is not None
                and _courante.id == info.get("execution")
                and _courante.en_cours
            )
    return _processus_vivant(pid)


def _noter_interruption(info: dict, dossier: Path) -> None:
    """Bilan d'une exécution abandonnée par son processus (serveur arrêté, commande tuée)."""
    try:
        debut = dt.datetime.fromisoformat(str(info.get("debut")))
    except ValueError:
        debut = res.maintenant()
    libelle = str(info.get("libelle") or "des tests")
    bilan = res.BilanExecution(
        libelle=libelle,
        famille=info.get("famille") if isinstance(info.get("famille"), str) else None,
        debut=debut,
        fin=res.maintenant(),
        issue=res.ISSUE_INTERROMPUE,
        message=(
            f"L'exécution de {libelle} a été interrompue (serveur ou commande arrêté pendant "
            "les tests) : ses résultats n'ont pas été enregistrés. La relancer."
        ),
    )
    journal.warning(
        "Exécution des tests abandonnée par le processus %s : %s", info.get("pid"), libelle
    )

    def modifier(historique: res.Historique) -> None:
        historique.derniere = bilan

    res.mettre_a_jour(modifier, dossier)


def execution_ailleurs(dossier: Path | None = None) -> dict | None:
    """Exécution menée par un autre processus (ligne de commande), ou `None`.

    Renvoie `{"libelle", "debut", "pid"}`. Un fichier de verrou abandonné est
    retiré au passage et l'interruption notée au bilan.
    """
    dossier = dossier or res.dossier_resultats()
    chemin = dossier / FICHIER_VERROU
    info = _lire_verrou(chemin)
    if info is None:
        return None
    if _proprietaire_vivant(info):
        if info.get("pid") == os.getpid():
            return None
        return {
            "libelle": info.get("libelle", ""),
            "debut": info.get("debut"),
            "pid": info.get("pid"),
        }
    with contextlib.suppress(FileNotFoundError):
        chemin.unlink()
        _noter_interruption(info, dossier)
    return None


def _prendre_verrou(dossier: Path, execution: Execution) -> None:
    chemin = dossier / FICHIER_VERROU
    contenu = json.dumps(
        {
            "pid": os.getpid(),
            "execution": execution.id,
            "libelle": execution.cible.libelle,
            "famille": execution.cible.famille,
            "debut": execution.debut.isoformat(timespec="seconds"),
        },
        ensure_ascii=False,
    )
    for _ in range(2):
        try:
            descripteur = os.open(chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if execution_ailleurs(dossier) is not None or chemin.exists():
                raise ErreurTestsEnCours(
                    "Des tests sont déjà en cours d'exécution dans un autre processus (ligne de "
                    "commande) : attendre leur fin, puis relancer."
                ) from None
            continue
        with os.fdopen(descripteur, "w", encoding="utf-8") as flux:
            flux.write(contenu)
        return
    raise ErreurTestsEnCours("Des tests sont déjà en cours d'exécution : attendre leur fin.")


def _rendre_verrou(dossier: Path, execution: Execution) -> None:
    chemin = dossier / FICHIER_VERROU
    info = _lire_verrou(chemin)
    if info is not None and info.get("execution") == execution.id:
        chemin.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Lancement
# --------------------------------------------------------------------------- #


def lancer(cible: Cible, racine: Path, dossier: Path | None = None) -> Execution:
    """Lance l'exécution de `cible` en arrière-plan et la renvoie tout de suite.

    Lève `ErreurLancementImpossible` (lancement désactivé, pytest absent, calcul
    en cours) ou `ErreurTestsEnCours` (une exécution tourne déjà ; `meme_cible`
    dit si c'est la même demande, par exemple un double clic).
    """
    global _courante
    reglages = lire_reglages()
    if not reglages.lancement_permis:
        raise ErreurLancementImpossible(
            f"Le lancement des tests est désactivé sur ce serveur ({VARIABLE_LANCEMENT}=non)."
        )
    if not pytest_installe():
        raise ErreurLancementImpossible(
            "Les tests ne peuvent pas être lancés : pytest n'est pas installé dans "
            "l'environnement du serveur. Installer l'extra « dev » du projet."
        )
    dossier = dossier or res.dossier_resultats()
    with _verrou:
        if _courante is not None and _courante.en_cours:
            meme = _courante.cible.cle == cible.cle and _courante.cible.famille == cible.famille
            raise ErreurTestsEnCours(
                "Une exécution des tests est déjà en cours : attendre sa fin ou l'arrêter.",
                meme_cible=meme,
            )
        if calcul_en_cours():
            raise ErreurLancementImpossible(
                "Un calcul d'emploi du temps est en cours : les tests attendent sa fin pour ne "
                "pas le ralentir. Relancer les tests quand le calcul est terminé."
            )
        nouvelle = Execution(
            id=secrets.token_hex(4),
            cible=cible,
            racine=racine,
            dossier=dossier,
            duree_max_s=reglages.duree_max_s,
            debut=res.maintenant(),
        )
        dossier.mkdir(parents=True, exist_ok=True)
        _prendre_verrou(dossier, nouvelle)
        nouvelle.fil = threading.Thread(
            target=_executer, args=(nouvelle,), name=f"tests-{nouvelle.id}", daemon=True
        )
        _courante = nouvelle
        nouvelle.fil.start()
    journal.info("Exécution des tests %s lancée : %s", nouvelle.id, cible.libelle)
    return nouvelle


@atexit.register
def _arreter_a_la_sortie() -> None:
    """Arrête le sous-processus en cours quand ce processus se termine normalement.

    Le sous-processus a son propre groupe (pour être arrêté avec ses enfants) :
    sans cela, il survivrait à un arrêt du serveur par Ctrl+C. Un arrêt brutal
    (processus tué) ne passe pas ici ; sous systemd, l'arrêt du service arrête
    tout son groupe de contrôle, sous-processus compris.
    """
    courante = _courante
    if courante is not None and courante.en_cours and courante.processus is not None:
        journal.warning("Arrêt du processus : exécution des tests %s interrompue", courante.id)
        _tuer(courante.processus)


def demander_arret() -> Execution | None:
    """Demande l'arrêt de l'exécution en cours ; renvoie l'exécution (ou `None`)."""
    courante = execution_courante()
    if courante is not None and courante.en_cours:
        courante.arret.set()
        journal.info("Arrêt demandé pour l'exécution des tests %s", courante.id)
    return courante


def attendre(
    execution: Execution,
    delai_s: float | None = None,
    rappel: Callable[[str, res.ResultatTest], None] | None = None,
) -> Suivi:
    """Attend la fin (au plus `delai_s` s) ; `rappel(cle, resultat)` pour chaque test terminé."""
    echeance = None if delai_s is None else time.monotonic() + delai_s
    vus = 0
    while True:
        suivi = execution.suivi()
        if rappel is not None:
            for cle in suivi.termines[vus:]:
                rappel(cle, suivi.resultats[cle])
            vus = len(suivi.termines)
        if not suivi.en_cours or (echeance is not None and time.monotonic() >= echeance):
            return suivi
        if execution.fil is not None:
            execution.fil.join(PAS_LECTURE_S)


# --------------------------------------------------------------------------- #
# Sous-processus
# --------------------------------------------------------------------------- #


def dossier_du_code(racine: Path) -> Path:
    """Dossier à mettre dans `PYTHONPATH` : `racine/src` s'il porte le paquet, sinon celui du code chargé."""
    source = racine / "src"
    if (source / "edt" / "__init__.py").is_file():
        return source
    return Path(edt.__file__).resolve().parent.parent


def environnement_assaini(temporaire: Path, evenements: Path, racine: Path) -> dict[str, str]:
    """Environnement du sous-processus : rien d'`EDT_*` hérité, base et journaux temporaires."""
    env = {
        nom: valeur
        for nom, valeur in os.environ.items()
        if not nom.upper().startswith(("EDT_", "PYTEST_")) and nom.upper() != "PYTHONPATH"
    }
    dossier_temp = temporaire / "temp"
    dossier_temp.mkdir(exist_ok=True)
    env.update(
        EDT_DB_URL=f"sqlite:///{(temporaire / 'edt.db').as_posix()}",
        EDT_LOG_DIR=str(temporaire / "journaux"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=str(dossier_du_code(racine)),
        TMPDIR=str(dossier_temp),
        TEMP=str(dossier_temp),
        TMP=str(dossier_temp),
    )
    env[VARIABLE_EVENEMENTS] = str(evenements)
    return env


def commande_pytest(cible: Cible, racine: Path, temporaire: Path) -> list[str]:
    """Ligne de commande du sous-processus (chemins tirés du catalogue seulement)."""
    commande = [
        sys.executable,
        "-m",
        "pytest",
        *cible.chemins,
        "-p",
        GREFFON,
        "-p",
        "no:cacheprovider",
        "--color=no",
        "-q",
        "-rfEs",
        "--continue-on-collection-errors",
        f"--rootdir={racine}",
        f"--basetemp={temporaire / 'pytest'}",
    ]
    if cible.exclure_perf:
        commande += ["-m", "not perf"]
    return commande


def _tuer(processus: subprocess.Popen) -> None:
    """Arrête le processus et tous ses enfants, par leur numéro exact."""
    if processus.poll() is not None:
        return
    pid = processus.pid
    journal.info("Arrêt du processus de tests %d et de ses enfants", pid)
    if os.name == "nt":
        resultat = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=30
        )
        if resultat.returncode != 0 and processus.poll() is None:
            journal.warning("taskkill a échoué (%s) : arrêt direct", resultat.stderr.strip())
            processus.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)
        try:
            processus.wait(DELAI_ARRET_S)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)
    try:
        processus.wait(30)
    except subprocess.TimeoutExpired:
        journal.error("Le processus de tests %d ne s'arrête pas", pid)


class _Lecteur:
    """Lit les lignes complètes ajoutées au fichier des événements depuis la dernière lecture."""

    def __init__(self, chemin: Path) -> None:
        self.chemin = chemin
        self.position = 0
        self.reste = b""

    def lire(self) -> list[dict]:
        try:
            with self.chemin.open("rb") as flux:
                flux.seek(self.position)
                morceau = flux.read()
        except FileNotFoundError:
            return []
        self.position += len(morceau)
        lignes = (self.reste + morceau).split(b"\n")
        self.reste = lignes.pop()
        evenements = []
        for ligne in lignes:
            if not ligne.strip():
                continue
            try:
                evenements.append(json.loads(ligne.decode("utf-8")))
            except (UnicodeDecodeError, ValueError) as erreur:
                journal.warning(
                    "Événement de test illisible, ignoré (%s) : %r", erreur, ligne[:200]
                )
        return evenements


def _lancer_processus(execution: Execution, temporaire: Path, sortie) -> subprocess.Popen:
    evenements = temporaire / "evenements.jsonl"
    evenements.touch()
    options: dict = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    commande = commande_pytest(execution.cible, execution.racine, temporaire)
    journal.info("Commande des tests : %s", " ".join(commande[1:]))
    return subprocess.Popen(
        commande,
        cwd=execution.racine,
        env=environnement_assaini(temporaire, evenements, execution.racine),
        stdin=subprocess.DEVNULL,
        stdout=sortie,
        stderr=subprocess.STDOUT,
        **options,
    )


def _executer(execution: Execution) -> None:
    """Corps du fil d'arrière-plan : lance, suit, arrête au besoin, enregistre."""
    temporaire = Path(tempfile.mkdtemp(prefix="edt-tests-"))
    issue, code = res.ISSUE_ERREUR, None
    try:
        with (execution.dossier / res.FICHIER_JOURNAL).open("w", encoding="utf-8") as sortie:
            processus = _lancer_processus(execution, temporaire, sortie)
            execution.processus = processus
            lecteur = _Lecteur(temporaire / "evenements.jsonl")
            arrete = depasse = False
            while processus.poll() is None:
                _absorber(execution, lecteur.lire())
                if execution.arret.is_set():
                    arrete = True
                    _tuer(processus)
                elif time.monotonic() - execution.depart > execution.duree_max_s:
                    depasse = True
                    _tuer(processus)
                else:
                    time.sleep(PAS_LECTURE_S)
            _absorber(execution, lecteur.lire())
            code = processus.returncode
        issue = _conclure(execution, code, arrete=arrete, depasse=depasse)
    except Exception as erreur:
        journal.exception("Exécution des tests %s en erreur", execution.id)
        with execution.verrou:
            execution.message = (
                f"L'exécution des tests a échoué ({type(erreur).__name__} : {erreur}). Le "
                "journal du serveur donne le détail."
            )
    finally:
        try:
            _enregistrer(execution, issue, code)
        except Exception:
            journal.exception("Résultats de l'exécution %s non enregistrés", execution.id)
        _rendre_verrou(execution.dossier, execution)
        try:
            shutil.rmtree(temporaire)
        except OSError as erreur:
            journal.warning("Dossier temporaire des tests non effacé : %s (%s)", temporaire, erreur)
        with execution.verrou:
            execution.fin = time.monotonic()
            execution.issue = issue

        journal.info("Exécution des tests %s terminée : %s", execution.id, issue)


def _absorber(execution: Execution, evenements: list[dict]) -> None:
    if evenements:
        with execution.verrou:
            for evenement in evenements:
                execution.regroupement.ajouter(evenement)


def _conclure(execution: Execution, code: int | None, *, arrete: bool, depasse: bool) -> str:
    """Issue et message de l'exécution ; pose les résultats que pytest n'a pas pu rendre."""
    reg = execution.regroupement
    with execution.verrou:
        maintenant = res.maintenant()
        catalogues = set(execution.cible.tests)
        # Fichier qui n'a pas pu être chargé : chacun de ses tests est un échec, avec la cause.
        hors_fichier: list[str] = []
        for identifiant, detail in reg.erreurs_collecte:
            touches = [cle for cle in catalogues if cle.split("::", 1)[0] == identifiant]
            if not touches:
                hors_fichier.append(identifiant)
            for cle in touches:
                if cle not in reg.resultats:
                    execution.supplementaires[cle] = res.ResultatTest(
                        etat=res.ECHEC,
                        duree_s=0.0,
                        le=maintenant,
                        detail=f"Le fichier de ce test n'a pas pu être chargé :\n{detail}",
                    )
        if depasse and reg.en_cours and reg.en_cours not in reg.resultats:
            execution.supplementaires[reg.en_cours] = res.ResultatTest(
                etat=res.ECHEC,
                duree_s=time.monotonic() - execution.depart,
                le=maintenant,
                detail=(
                    "Test interrompu : la durée maximale de l'exécution "
                    f"({execution.duree_max_s} s, réglage {VARIABLE_DUREE_MAX}) a été atteinte "
                    "pendant ce test."
                ),
            )
        nb = len(reg.termines) + len(execution.supplementaires)
        if arrete:
            issue = res.ISSUE_ARRETEE
            message = (
                f"Exécution arrêtée à la demande après {nb} test(s) ; les résultats des tests "
                "terminés sont enregistrés."
            )
        elif depasse:
            issue = res.ISSUE_TEMPS_DEPASSE
            message = (
                f"Exécution arrêtée : durée maximale de {execution.duree_max_s} s atteinte "
                f"(réglage {VARIABLE_DUREE_MAX}). Les résultats des tests terminés sont "
                "enregistrés."
            )
        elif code not in CODES_PYTEST_NORMAUX or (hors_fichier and not reg.termines):
            issue = res.ISSUE_ERREUR
            cause = f" ; chargement impossible : {', '.join(hors_fichier)}" if hors_fichier else ""
            message = (
                f"Les tests n'ont pas pu aller à leur terme (code de sortie {code}{cause}). Le "
                "journal complet de l'exécution dit pourquoi."
            )
        elif code == 5 or not reg.collecte_recue or not reg.attendus:
            issue = res.ISSUE_TERMINEE
            message = "Aucun test à lancer dans cette sélection" + (
                " : ses tests sont des tests de charge, à lancer un par un."
                if execution.cible.exclure_perf
                else "."
            )
        else:
            issue = res.ISSUE_TERMINEE
            message = "Exécution terminée."
        execution.message = message
        return issue


def _enregistrer(execution: Execution, issue: str, code: int | None) -> None:
    """Écrit les résultats, les dates des groupes couverts en entier et le bilan."""
    suivi = execution.suivi()
    fin = res.maintenant()
    complete = issue == res.ISSUE_TERMINEE and code in CODES_PYTEST_NORMAUX
    bilan = res.BilanExecution(
        libelle=execution.cible.libelle,
        famille=execution.cible.famille,
        debut=execution.debut,
        fin=fin,
        issue=issue,
        message=execution.message,
        nb_tests=len(suivi.resultats),
        nb_reussis=suivi.compte(res.REUSSI),
        nb_echecs=suivi.compte(res.ECHEC),
        nb_ignores=suivi.compte(res.IGNORE),
    )

    def modifier(historique: res.Historique) -> None:
        historique.tests.update(suivi.resultats)
        if complete:
            for groupe in execution.cible.groupes:
                historique.groupes[groupe] = fin
        historique.derniere = bilan

    res.mettre_a_jour(modifier, execution.dossier)
