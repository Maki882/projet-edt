"""Résultats des tests : regroupement des comptes rendus de pytest et historique sur disque.

Regroupement (`Regroupement`)
-----------------------------
Le greffon (`edt.services.banc_tests.greffon`) écrit un compte rendu par phase
de chaque cas. Les cas paramétrés d'une même fonction (`test_x[a]`,
`test_x[b]`) sont regroupés en un seul résultat de test :

- **échec** si l'un des cas échoue (préparation, test ou nettoyage) ;
- sinon **ignoré** si tous les cas sont ignorés (la première raison est gardée ;
  un échec attendu compte comme ignoré, avec sa raison) ;
- sinon **réussi** (des cas ignorés à côté de cas réussis sont comptés à part).

Une fonction est terminée quand tous ses cas listés à la collecte ont rendu
leur dernière phase.

Historique (`lire_historique`, `mettre_a_jour`)
----------------------------------------------
Fichier JSON `<EDT_LOG_DIR>/tests/resultats.json`, hors de la base : charger un
établissement fictif remet la base à zéro, l'historique des tests survit. Il
garde, pour chaque test, son dernier résultat et sa date ; pour chaque groupe
(famille, composant, fichier), la date de sa dernière exécution complète ; et
le bilan de la dernière exécution. Écriture atomique (fichier temporaire puis
remplacement). Lecture tolérante : un fichier illisible est signalé au journal
et à l'écran (`Historique.erreur`), jamais une erreur ; il est mis de côté
(`resultats-illisible.json`) à la prochaine écriture.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from edt.config import charger_configuration
from edt.erreurs import ErreurEDT

journal = logging.getLogger(__name__)

REUSSI = "reussi"
ECHEC = "echec"
IGNORE = "ignore"
JAMAIS = "jamais"
EN_COURS = "en_cours"
EN_ATTENTE = "en_attente"

LIBELLES_ETATS: dict[str, str] = {
    REUSSI: "Réussi",
    ECHEC: "Échec",
    IGNORE: "Ignoré",
    JAMAIS: "Jamais lancé",
    EN_COURS: "En cours",
    EN_ATTENTE: "En attente",
}
"""Libellé affiché de chaque état."""

ETATS_ENREGISTRES = (REUSSI, ECHEC, IGNORE)
"""États qu'un résultat enregistré peut prendre."""

FICHIER_RESULTATS = "resultats.json"
FICHIER_ILLISIBLE = "resultats-illisible.json"
FICHIER_JOURNAL = "derniere-execution.log"
"""Journal complet de la dernière exécution (sortie de pytest), à côté des résultats."""

VERSION_FORMAT = 1
NB_ESSAIS_REMPLACEMENT = 5
"""Sous Windows, un lecteur qui tient le fichier ouvert empêche son remplacement : on réessaie."""


def dossier_resultats() -> Path:
    """Dossier des résultats : `<EDT_LOG_DIR>/tests`."""
    return charger_configuration().log_dir / "tests"


def maintenant() -> dt.datetime:
    """Heure locale, à la seconde (remplacée par les tests qui veulent une date fixe)."""
    return dt.datetime.now().replace(microsecond=0)


# --------------------------------------------------------------------------- #
# Résultat d'un test et regroupement des cas
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResultatTest:
    """Dernier résultat d'une fonction de test, tous cas confondus.

    `raison` : pourquoi le test est ignoré ; `detail` : message de l'échec
    (assertion, trace), pour le bloc replié de l'écran.
    """

    etat: str
    duree_s: float
    le: dt.datetime
    nb_cas: int = 1
    nb_echecs: int = 0
    nb_ignores: int = 0
    raison: str | None = None
    detail: str | None = None

    def vers_dict(self) -> dict:
        """Forme enregistrée dans le fichier JSON."""
        return {
            "etat": self.etat,
            "duree_s": round(self.duree_s, 3),
            "le": self.le.isoformat(timespec="seconds"),
            "nb_cas": self.nb_cas,
            "nb_echecs": self.nb_echecs,
            "nb_ignores": self.nb_ignores,
            "raison": self.raison,
            "detail": self.detail,
        }

    @classmethod
    def depuis_dict(cls, donnees: object) -> ResultatTest:
        """Relit un résultat enregistré ; `ValueError` s'il est mal formé."""
        if not isinstance(donnees, dict) or donnees.get("etat") not in ETATS_ENREGISTRES:
            raise ValueError(f"résultat mal formé : {str(donnees)[:100]}")

        def texte(nom: str) -> str | None:
            valeur = donnees.get(nom)
            return valeur if isinstance(valeur, str) else None

        return cls(
            etat=donnees["etat"],
            duree_s=float(donnees.get("duree_s") or 0.0),
            le=dt.datetime.fromisoformat(str(donnees["le"])),
            nb_cas=int(donnees.get("nb_cas") or 1),
            nb_echecs=int(donnees.get("nb_echecs") or 0),
            nb_ignores=int(donnees.get("nb_ignores") or 0),
            raison=texte("raison"),
            detail=texte("detail"),
        )


def fonction_du_cas(identifiant: str) -> str:
    """Clé de la fonction d'un cas : `…::test_x[a-1]` donne `…::test_x`."""
    return identifiant.split("[", 1)[0]


@dataclass
class _Cas:
    issue: str = "passed"
    duree: float = 0.0
    raison: str | None = None
    detail: str | None = None
    fini: bool = False


@dataclass
class Regroupement:
    """Accumule les événements du greffon et rend un résultat par fonction terminée.

    `termines` liste les fonctions terminées dans l'ordre où elles l'ont été ;
    `en_cours` est la fonction dont un cas tourne ; `erreurs_collecte` les
    fichiers qui n'ont pas pu être chargés (identifiant, détail) ; `code` le
    code de sortie de pytest, une fois connu.
    """

    horloge: Callable[[], dt.datetime] | None = None
    attendus: dict[str, list[str]] = field(default_factory=dict)
    cas: dict[str, _Cas] = field(default_factory=dict)
    termines: list[str] = field(default_factory=list)
    resultats: dict[str, ResultatTest] = field(default_factory=dict)
    erreurs_collecte: list[tuple[str, str]] = field(default_factory=list)
    en_cours: str | None = None
    collecte_recue: bool = False
    code: int | None = None

    @property
    def nb_cas_collectes(self) -> int:
        """Nombre de cas retenus par pytest."""
        return sum(len(liste) for liste in self.attendus.values())

    def ajouter(self, evenement: dict) -> list[str]:
        """Prend en compte un événement ; renvoie les fonctions qu'il termine."""
        genre = evenement.get("type")
        if genre == "collecte":
            self.collecte_recue = True
            for identifiant in evenement.get("ids") or []:
                self.attendus.setdefault(fonction_du_cas(identifiant), []).append(identifiant)
            return []
        if genre == "erreur_collecte":
            self.erreurs_collecte.append(
                (str(evenement.get("id", "")), str(evenement.get("detail", "")))
            )
            return []
        if genre == "debut":
            self.en_cours = fonction_du_cas(str(evenement.get("id", "")))
            return []
        if genre == "fin":
            self.code = int(evenement.get("code", -1))
            self.en_cours = None
            return []
        if genre != "rapport":
            journal.warning("Événement de test inconnu, ignoré : %s", str(evenement)[:200])
            return []
        return self._rapport(evenement)

    def _rapport(self, evenement: dict) -> list[str]:
        identifiant = str(evenement.get("id", ""))
        cas = self.cas.setdefault(identifiant, _Cas())
        cas.duree += float(evenement.get("duree") or 0.0)
        issue = evenement.get("issue")
        if issue == "failed":
            cas.issue = "failed"
            if cas.detail is None:
                phase = evenement.get("phase")
                prefixe = "" if phase == "call" else f"[échec pendant la phase « {phase} »]\n"
                cas.detail = prefixe + str(evenement.get("detail") or "")
        elif issue == "skipped" and cas.issue != "failed":
            cas.issue = "skipped"
            raison = evenement.get("raison") or ""
            if "attendu" in evenement:
                raison = f"échec attendu : {evenement['attendu'] or raison}"
            cas.raison = cas.raison or str(raison) or None
        if evenement.get("phase") != "teardown":
            return []
        cas.fini = True
        fonction = fonction_du_cas(identifiant)
        attendus = self.attendus.get(fonction) or [identifiant]
        if fonction in self.resultats or not all(
            (c := self.cas.get(i)) is not None and c.fini for i in attendus
        ):
            return []
        self.resultats[fonction] = self._resultat(attendus)
        self.termines.append(fonction)
        return [fonction]

    def _resultat(self, identifiants: list[str]) -> ResultatTest:
        liste = [self.cas[i] for i in identifiants]
        echecs = [c for c in liste if c.issue == "failed"]
        ignores = [c for c in liste if c.issue == "skipped"]
        if echecs:
            etat = ECHEC
        elif len(ignores) == len(liste):
            etat = IGNORE
        else:
            etat = REUSSI
        detail = None
        if echecs:
            morceaux = [
                (f"--- {identifiant}\n" if len(liste) > 1 else "") + (cas.detail or "")
                for identifiant, cas in zip(identifiants, liste, strict=True)
                if cas.issue == "failed"
            ]
            detail = "\n\n".join(morceaux)
        return ResultatTest(
            etat=etat,
            duree_s=sum(c.duree for c in liste),
            le=self.horloge() if self.horloge is not None else maintenant(),
            nb_cas=len(liste),
            nb_echecs=len(echecs),
            nb_ignores=len(ignores),
            raison=next((c.raison for c in ignores if c.raison), None) if ignores else None,
            detail=detail,
        )


def regrouper(
    evenements: list[dict], horloge: Callable[[], dt.datetime] | None = None
) -> Regroupement:
    """Regroupe une liste d'événements ; renvoie le `Regroupement` rempli."""
    regroupement = Regroupement(horloge=horloge)
    for evenement in evenements:
        regroupement.ajouter(evenement)
    return regroupement


# --------------------------------------------------------------------------- #
# Historique sur disque
# --------------------------------------------------------------------------- #

ISSUE_TERMINEE = "terminee"
ISSUE_ARRETEE = "arretee"
ISSUE_TEMPS_DEPASSE = "temps_depasse"
ISSUE_ERREUR = "erreur"
ISSUE_INTERROMPUE = "interrompue"
ISSUES = (ISSUE_TERMINEE, ISSUE_ARRETEE, ISSUE_TEMPS_DEPASSE, ISSUE_ERREUR, ISSUE_INTERROMPUE)


@dataclass(frozen=True)
class BilanExecution:
    """Bilan d'une exécution terminée (ou interrompue), gardé dans l'historique."""

    libelle: str
    famille: str | None
    debut: dt.datetime
    fin: dt.datetime
    issue: str
    message: str
    nb_tests: int = 0
    nb_reussis: int = 0
    nb_echecs: int = 0
    nb_ignores: int = 0

    def vers_dict(self) -> dict:
        """Forme enregistrée dans le fichier JSON."""
        return {
            "libelle": self.libelle,
            "famille": self.famille,
            "debut": self.debut.isoformat(timespec="seconds"),
            "fin": self.fin.isoformat(timespec="seconds"),
            "issue": self.issue,
            "message": self.message,
            "nb_tests": self.nb_tests,
            "nb_reussis": self.nb_reussis,
            "nb_echecs": self.nb_echecs,
            "nb_ignores": self.nb_ignores,
        }

    @classmethod
    def depuis_dict(cls, donnees: object) -> BilanExecution:
        """Relit un bilan enregistré ; `ValueError` s'il est mal formé."""
        if not isinstance(donnees, dict) or donnees.get("issue") not in ISSUES:
            raise ValueError("bilan de la dernière exécution mal formé")
        famille = donnees.get("famille")
        return cls(
            libelle=str(donnees.get("libelle", "")),
            famille=famille if isinstance(famille, str) else None,
            debut=dt.datetime.fromisoformat(str(donnees["debut"])),
            fin=dt.datetime.fromisoformat(str(donnees["fin"])),
            issue=donnees["issue"],
            message=str(donnees.get("message", "")),
            nb_tests=int(donnees.get("nb_tests") or 0),
            nb_reussis=int(donnees.get("nb_reussis") or 0),
            nb_echecs=int(donnees.get("nb_echecs") or 0),
            nb_ignores=int(donnees.get("nb_ignores") or 0),
        )


@dataclass
class Historique:
    """Contenu du fichier des résultats.

    - `tests` : dernier résultat de chaque test (clé du catalogue) ;
    - `groupes` : date de la dernière exécution complète de chaque groupe ;
    - `derniere` : bilan de la dernière exécution ;
    - `erreur` : phrase à afficher si le fichier était illisible (en tout ou partie).
    """

    tests: dict[str, ResultatTest] = field(default_factory=dict)
    groupes: dict[str, dt.datetime] = field(default_factory=dict)
    derniere: BilanExecution | None = None
    erreur: str | None = None

    def vers_dict(self) -> dict:
        """Forme enregistrée dans le fichier JSON."""
        return {
            "version": VERSION_FORMAT,
            "tests": {cle: r.vers_dict() for cle, r in sorted(self.tests.items())},
            "groupes": {
                cle: date.isoformat(timespec="seconds")
                for cle, date in sorted(self.groupes.items())
            },
            "derniere": self.derniere.vers_dict() if self.derniere else None,
        }


def _illisible(chemin: Path, cause: object) -> Historique:
    journal.warning("Fichier des résultats des tests illisible (%s) : %s", chemin, cause)
    return Historique(
        erreur=(
            f"Le fichier des résultats des tests ({chemin.name}) est illisible ({cause}). Les "
            "résultats précédents ne sont pas affichés ; la prochaine exécution le remplace "
            f"(l'ancien est gardé sous le nom {FICHIER_ILLISIBLE})."
        )
    )


def lire_historique(dossier: Path | None = None) -> Historique:
    """Relit l'historique ; absent : vide ; illisible : vide avec `erreur` (jamais d'exception)."""
    chemin = (dossier or dossier_resultats()) / FICHIER_RESULTATS
    try:
        brut = chemin.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Historique()
    except (OSError, UnicodeDecodeError) as erreur:
        return _illisible(chemin, erreur)
    try:
        donnees = json.loads(brut)
    except json.JSONDecodeError as erreur:
        return _illisible(chemin, f"contenu JSON invalide, ligne {erreur.lineno}")
    if not isinstance(donnees, dict) or not isinstance(donnees.get("tests", {}), dict):
        return _illisible(chemin, "structure inattendue")

    historique = Historique()
    mal_formes = 0
    for cle, valeur in donnees.get("tests", {}).items():
        try:
            historique.tests[str(cle)] = ResultatTest.depuis_dict(valeur)
        except (ValueError, TypeError, KeyError):
            mal_formes += 1
    groupes = donnees.get("groupes")
    for cle, valeur in (groupes if isinstance(groupes, dict) else {}).items():
        try:
            historique.groupes[str(cle)] = dt.datetime.fromisoformat(str(valeur))
        except ValueError:
            mal_formes += 1
    if donnees.get("derniere") is not None:
        try:
            historique.derniere = BilanExecution.depuis_dict(donnees["derniere"])
        except (ValueError, TypeError, KeyError):
            mal_formes += 1
    if mal_formes:
        journal.warning("%d entrée(s) mal formée(s) ignorée(s) dans %s", mal_formes, chemin)
        historique.erreur = (
            f"{mal_formes} résultat(s) enregistré(s) illisible(s) ont été ignorés ; ils seront "
            "remplacés à la prochaine exécution des tests concernés."
        )
    return historique


def _ecrire_atomique(chemin: Path, texte: str) -> None:
    descripteur, temporaire = tempfile.mkstemp(
        dir=chemin.parent, prefix=".resultats-", suffix=".tmp"
    )
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8") as flux:
            flux.write(texte)
            flux.flush()
            os.fsync(flux.fileno())
        for essai in range(NB_ESSAIS_REMPLACEMENT):
            try:
                os.replace(temporaire, chemin)
                return
            except PermissionError:
                if essai == NB_ESSAIS_REMPLACEMENT - 1:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(temporaire):
            os.remove(temporaire)


_verrou_ecriture = threading.Lock()


def mettre_a_jour(modifier: Callable[[Historique], None], dossier: Path | None = None) -> None:
    """Relit l'historique, applique `modifier`, puis l'écrit d'un bloc.

    Un fichier illisible est mis de côté (`resultats-illisible.json`) avant
    d'être remplacé. Une écriture impossible lève `ErreurEDT` (dossier en
    lecture seule, disque plein), avec le chemin en cause.
    """
    dossier = dossier or dossier_resultats()
    chemin = dossier / FICHIER_RESULTATS
    with _verrou_ecriture:
        try:
            dossier.mkdir(parents=True, exist_ok=True)
            historique = lire_historique(dossier)
            if historique.erreur and not historique.tests and chemin.exists():
                os.replace(chemin, dossier / FICHIER_ILLISIBLE)
                journal.warning(
                    "Fichier des résultats illisible mis de côté : %s", FICHIER_ILLISIBLE
                )
            historique.erreur = None
            modifier(historique)
            _ecrire_atomique(
                chemin, json.dumps(historique.vers_dict(), ensure_ascii=False, indent=1)
            )
        except OSError as erreur:
            journal.error("Écriture des résultats des tests impossible (%s) : %s", chemin, erreur)
            raise ErreurEDT(
                f"Les résultats des tests n'ont pas pu être enregistrés dans {chemin} ({erreur}). "
                "Vérifier que le dossier des journaux (EDT_LOG_DIR) est accessible en écriture."
            ) from erreur
