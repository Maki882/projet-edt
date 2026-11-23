"""Cahier de recette (livret 4) : parties générées à partir des fiches et des campagnes.

Le livret `docs/livrets/4-cahier-de-recette.md` mêle un texte écrit à la main et
des parties générées, chacune encadrée par deux marqueurs HTML (`debut_de_bloc`,
`fin_de_bloc`). Ce module les produit à partir de trois sources :

- `docs/livrets/recette/fiches.toml` : les fiches de recette, écrites à la main
  (lues par `tomllib`) ; chaque fiche cite ses tests d'intégration par leur
  identifiant pytest (`tests/integration/<composant>/<fichier>.py::<fonction>`),
  ou décrit un essai manuel ; la liste `sans_fiche` déclare les fonctionnalités
  réalisées qui n'ont pas de fiche, avec leur raison ;
- `docs/livrets/recette/campagnes.json` : les campagnes enregistrées, c'est-à-dire
  les résultats réels d'une exécution de la famille des tests d'intégration,
  repris du fichier de résultats que l'application écrit
  (`<EDT_LOG_DIR>/tests/resultats.json`, format défini dans
  `src/edt/services/banc_tests/resultats.py`) ;
- les tests eux-mêmes (lus par `ast`, sans les exécuter) et le livret 1, dont
  sont lues les fonctionnalités réalisées (O1 à O11, U1 à U13, P1 à P4) et les
  cas d'usage.

Enregistrer une campagne ne garde que les tests d'intégration dont le résultat
est daté entre le début et la fin de la dernière exécution (champ `derniere`) ;
les autres entrées sont écartées et listées, jamais comptées. Rien de ce qui
désigne une machine n'est gardé : ni message d'échec (il contient des chemins),
ni chemin absolu dans une raison d'abandon (remplacé par « … »). La machine est
désignée par son rôle (`--nom`).

Verdict d'une fiche : « Non conforme » si l'un de ses tests a échoué dans une
campagne ; sinon « Conforme » si chacun de ses tests a réussi dans au moins une
campagne (un test ignoré sur un système, pour une raison de système, est dit
avec sa raison) ; sinon « Non exécutée » (une seule règle : `verdict_des_tests`).
Une fiche d'essai manuel prend aussi le verdict de son essai, le moins favorable
l'emportant. Un essai de suivi (`[fiche.suivi]`), fait après la recette sur un code
corrigé, est affiché avec son verdict sans changer celui de la fiche. Une fiche sans test ni essai est « Non exécutée », jamais
« Conforme ». Une clé inconnue de `fiches.toml`, une exécution partielle donnée
pour une campagne ou un état de test inconnu sont des erreurs qui disent quoi
faire.

Usage, depuis la racine du dépôt :

    python -m tests.outils.recette              # bilan et contrôles
    python -m tests.outils.recette --ecrire     # met à jour le livret 4
    python -m tests.outils.recette --campagne <resultats.json> --nom "Serveur de démonstration" \\
        --systeme "Linux (noyau 7.0)" --python 3.14.4 --processeurs 2 --version 1.0.0

`tests/unitaires/socle/test_recette.py` vérifie que les parties générées sont à
jour et que les contrôles (`problemes`) ne relèvent rien.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import difflib
import json
import logging
import re
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from tests.outils.classement import fonctions_de_test, inventorier, premiere_phrase

logger = logging.getLogger(__name__)

RACINE = Path(__file__).resolve().parents[2]
"""Racine du dépôt."""

LIVRET = Path("docs") / "livrets" / "4-cahier-de-recette.md"
"""Livret qui reçoit les parties générées, relatif à la racine."""

FICHES = Path("docs") / "livrets" / "recette" / "fiches.toml"
"""Fiches de recette, écrites à la main."""

CAMPAGNES = Path("docs") / "livrets" / "recette" / "campagnes.json"
"""Campagnes enregistrées."""

LIVRET_FONCTIONNEL = Path("docs") / "livrets" / "1-specifications-fonctionnelles.md"
"""Livret 1, source des fonctionnalités et des cas d'usage."""

DOSSIER_INTEGRATION = "tests/integration/"
"""Préfixe des identifiants des tests d'intégration."""

COMMANDE = "python -m tests.outils.recette --ecrire"
"""Commande qui régénère les parties générées du livret."""

FORMAT_CAMPAGNES = 1
"""Version du format de `campagnes.json`."""

CONFORME, NON_CONFORME, NON_EXECUTEE = "Conforme", "Non conforme", "Non exécutée"
VERDICTS = (CONFORME, NON_CONFORME, NON_EXECUTEE)
"""Verdicts possibles d'une fiche."""

FAMILLES: dict[str, str] = {
    "BOUT": "Parcours de bout en bout",
    "O": "Fonctionnalités obligatoires",
    "U": "Fonctionnalités utiles",
    "P": "Fonctionnalités optionnelles réalisées",
    "EXP": "Exploitation et robustesse",
}
"""Famille de fiches (préfixe de l'identifiant) et son titre, dans l'ordre du livret."""

_IDENTIFIANT_FICHE = re.compile(r"^REC-(BOUT|EXP|[OUP]\d+)-(\d+)$")
_FONCTIONNALITE = re.compile(
    r"^\|\s*([OUP]\d+)\s*\|\s*([^|]+?)\s*\|.*\|\s*(Réalisée|Prévue)\s*\|\s*$"
)
_CAS = re.compile(r"^#### Cas (\d+)\. (.+?)\s*$")
_CHEMIN_ABSOLU = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:home|Users|root|tmp|var|opt|srv|mnt)/)\S*")


class ErreurRecette(Exception):
    """Source illisible ou incohérente ; le message nomme le fichier, l'élément et le remède."""


# --------------------------------------------------------------------------- #
# Sources : tests, livret 1, fiches, campagnes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FichierIntegration:
    """Un fichier de tests d'intégration et ses fonctions de test."""

    chemin: str
    """Chemin relatif à la racine (`tests/integration/socle/test_web.py`)."""
    composant: str
    description: str | None
    tests: tuple[str, ...]
    """Identifiants pytest des fonctions de test, dans l'ordre du fichier."""
    charge: frozenset[str]
    """Identifiants marqués `perf` (tests de charge, lancés à part)."""


def _fonctions_nommees(corps: list[ast.stmt], prefixe: str) -> list[tuple[str, ast.AST]]:
    """Fonctions `test_*` du module et de ses classes `Test*`, nommées comme pytest (`Classe::f`)."""
    trouvees: list[tuple[str, ast.AST]] = []
    for noeud in corps:
        if isinstance(noeud, ast.ClassDef) and noeud.name.startswith("Test"):
            trouvees += _fonctions_nommees(noeud.body, f"{prefixe}{noeud.name}::")
        elif isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef):
            if noeud.name.startswith("test_"):
                trouvees.append((prefixe + noeud.name, noeud))
    return trouvees


def lire_tests(racine: Path) -> dict[str, FichierIntegration]:
    """Fichiers de `tests/integration/`, par chemin, lus sans les exécuter."""
    dossier = racine / DOSSIER_INTEGRATION
    if not dossier.is_dir():
        raise ErreurRecette(f"{DOSSIER_INTEGRATION} introuvable sous {racine}")
    fichiers: dict[str, FichierIntegration] = {}
    for fichier in sorted(dossier.rglob("test_*.py")):
        chemin = fichier.relative_to(racine).as_posix()
        try:
            arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=chemin)
        except SyntaxError as erreur:
            raise ErreurRecette(f"{chemin} : fichier de tests illisible ({erreur.msg})") from erreur
        marques = {id(noeud): m for noeud, m in fonctions_de_test(arbre)}
        tests, charge = [], set()
        for nom, noeud in _fonctions_nommees(arbre.body, ""):
            tests.append(f"{chemin}::{nom}")
            if "perf" in marques.get(id(noeud), frozenset()):
                charge.add(f"{chemin}::{nom}")
        relatif = fichier.relative_to(dossier).parts
        fichiers[chemin] = FichierIntegration(
            chemin=chemin,
            composant=relatif[0] if len(relatif) > 1 else "(racine)",
            description=premiere_phrase(ast.get_docstring(arbre)),
            tests=tuple(tests),
            charge=frozenset(charge),
        )
    logger.debug("%d fichiers de tests d'intégration lus", len(fichiers))
    return fichiers


@dataclass(frozen=True)
class Fonctionnalite:
    """Une ligne des tableaux de fonctionnalités du livret 1."""

    code: str
    libelle: str
    realisee: bool


def lire_livret_fonctionnel(racine: Path) -> tuple[list[Fonctionnalite], dict[int, str]]:
    """Fonctionnalités (dans l'ordre du livret 1) et titres des cas d'usage, par numéro."""
    chemin = racine / LIVRET_FONCTIONNEL
    try:
        texte = chemin.read_text(encoding="utf-8")
    except OSError as erreur:
        raise ErreurRecette(f"{LIVRET_FONCTIONNEL.as_posix()} illisible ({erreur})") from erreur
    fonctionnalites, cas = [], {}
    for ligne in texte.splitlines():
        if trouve := _FONCTIONNALITE.match(ligne):
            code, libelle, etat = trouve.groups()
            fonctionnalites.append(Fonctionnalite(code, libelle, etat == "Réalisée"))
        elif trouve := _CAS.match(ligne):
            cas[int(trouve.group(1))] = trouve.group(2)
    if not fonctionnalites or not cas:
        raise ErreurRecette(
            f"{LIVRET_FONCTIONNEL.as_posix()} : tableaux des fonctionnalités (« | O1 | … | "
            "Réalisée | ») ou titres « #### Cas N. … » introuvables ; la lecture de "
            "tests/outils/recette.py est à adapter au livret"
        )
    return fonctionnalites, cas


@dataclass(frozen=True)
class EssaiManuel:
    """Essai fait à la main, pour ce qu'un test par HTTP ne peut pas vérifier."""

    date: dt.date
    environnement: str
    constat: str
    verdict: str
    reserve: str | None = None
    preuves: tuple[tuple[str, str], ...] = ()
    """Images de preuve : (chemin relatif à `docs/livrets/`, légende)."""


@dataclass(frozen=True)
class Fiche:
    """Une fiche de recette."""

    id: str
    titre: str
    fonctionnalites: tuple[str, ...]
    cas: tuple[int, ...]
    objectif: str
    depart: str
    etapes: tuple[str, ...]
    attendu: tuple[str, ...]
    tests: tuple[str, ...]
    remarque: str | None
    essai: EssaiManuel | None
    suivi: EssaiManuel | None = None
    """Essai fait après la recette, sur un code corrigé : affiché, sans changer le verdict."""
    anomalie: str | None = None
    """Pour une fiche non conforme : l'anomalie de la section 6 qui la suit (« ANO-21 »)."""

    @property
    def famille(self) -> str:
        """`BOUT`, `O`, `U`, `P` ou `EXP`."""
        prefixe = self.id.split("-")[1]
        return prefixe if prefixe in ("BOUT", "EXP") else prefixe[0]

    @property
    def rang(self) -> tuple[int, int, int]:
        """Clé de tri : famille, numéro de fonctionnalité, numéro de fiche."""
        _, partie, numero = self.id.split("-")
        fonction = int(partie[1:]) if partie not in ("BOUT", "EXP") else 0
        return list(FAMILLES).index(self.famille), fonction, int(numero)


@dataclass(frozen=True)
class SansFiche:
    """Fonctionnalité réalisée déclarée sans fiche, avec sa raison."""

    fonctionnalite: str
    raison: str


CLES_FICHE = frozenset(
    {
        "id",
        "titre",
        "fonctionnalites",
        "cas",
        "objectif",
        "depart",
        "etapes",
        "attendu",
        "tests",
        "remarque",
        "anomalie",
        "essai",
        "suivi",
    }
)
"""Clés permises dans une table `[[fiche]]` de `fiches.toml`."""

CLES_ESSAI = frozenset({"date", "environnement", "constat", "verdict", "reserve", "preuves"})
"""Clés permises dans une table `[fiche.essai]`."""

CLES_PREUVE = frozenset({"image", "legende"})
"""Clés permises dans une preuve d'essai manuel."""

CLES_SANS_FICHE = frozenset({"fonctionnalite", "raison"})
"""Clés permises dans une table `[[sans_fiche]]`."""

_ANOMALIE = re.compile(r"^ANO-\d+$")


def _cles_connues(table: dict, permises: frozenset[str], ou: str) -> None:
    """Lève `ErreurRecette` si la table porte une clé inconnue (faute de frappe, par exemple)."""
    inconnues = sorted(set(table) - permises)
    if inconnues:
        raise ErreurRecette(
            f"{FICHES.as_posix()} : {ou} : clé(s) inconnue(s) {', '.join(inconnues)} ; clés "
            f"permises : {', '.join(sorted(permises))} (voir l'en-tête du fichier)"
        )


def _texte(table: dict, cle: str, ou: str, obligatoire: bool = True) -> str | None:
    valeur = table.get(cle)
    if valeur is None and not obligatoire:
        return None
    if not isinstance(valeur, str) or not valeur.strip():
        raise ErreurRecette(f"{FICHES.as_posix()} : {ou} : « {cle} » manquant ou vide")
    return " ".join(valeur.split())


def _liste(table: dict, cle: str, ou: str, sorte: type = str) -> tuple:
    valeur = table.get(cle, [])
    if not isinstance(valeur, list) or not all(isinstance(v, sorte) for v in valeur):
        raise ErreurRecette(f"{FICHES.as_posix()} : {ou} : « {cle} » doit être une liste")
    return tuple(" ".join(v.split()) if isinstance(v, str) else v for v in valeur)


def _essai(table: object, ou: str) -> EssaiManuel | None:
    if table is None:
        return None
    if not isinstance(table, dict):
        raise ErreurRecette(f"{FICHES.as_posix()} : {ou} : « essai » doit être une table")
    _cles_connues(table, CLES_ESSAI, f"{ou}, essai")
    date = table.get("date")
    if not isinstance(date, dt.date):
        raise ErreurRecette(f"{FICHES.as_posix()} : {ou} : date de l'essai absente (AAAA-MM-JJ)")
    verdict = _texte(table, "verdict", ou)
    if verdict not in (CONFORME, NON_CONFORME):
        raise ErreurRecette(
            f"{FICHES.as_posix()} : {ou} : verdict de l'essai « {verdict} » : "
            f"« {CONFORME} » ou « {NON_CONFORME} » attendu"
        )
    preuves = []
    for preuve in table.get("preuves", []):
        if not isinstance(preuve, dict):
            raise ErreurRecette(f"{FICHES.as_posix()} : {ou} : preuve mal formée")
        _cles_connues(preuve, CLES_PREUVE, f"{ou}, preuve")
        preuves.append((_texte(preuve, "image", ou), _texte(preuve, "legende", ou)))
    return EssaiManuel(
        date=date,
        environnement=_texte(table, "environnement", ou),
        constat=_texte(table, "constat", ou),
        verdict=verdict,
        reserve=_texte(table, "reserve", ou, obligatoire=False),
        preuves=tuple(preuves),
    )


def lire_fiches(racine: Path) -> tuple[list[Fiche], list[SansFiche]]:
    """Fiches (triées dans l'ordre du livret) et fonctionnalités déclarées sans fiche."""
    chemin = racine / FICHES
    try:
        donnees = tomllib.loads(chemin.read_text(encoding="utf-8"))
    except OSError as erreur:
        raise ErreurRecette(f"{FICHES.as_posix()} illisible ({erreur})") from erreur
    except tomllib.TOMLDecodeError as erreur:
        raise ErreurRecette(f"{FICHES.as_posix()} : TOML invalide ({erreur})") from erreur
    fiches: list[Fiche] = []
    vus: set[str] = set()
    for position, table in enumerate(donnees.get("fiche", []), start=1):
        ou = f"fiche n° {position}"
        identifiant = _texte(table, "id", ou)
        ou = f"fiche {identifiant}"
        if not _IDENTIFIANT_FICHE.match(identifiant):
            raise ErreurRecette(
                f"{FICHES.as_posix()} : {ou} : identifiant invalide (REC-BOUT-n, REC-O5-n, "
                "REC-U3-n, REC-P2-n ou REC-EXP-n attendu)"
            )
        if identifiant in vus:
            raise ErreurRecette(f"{FICHES.as_posix()} : {ou} : identifiant en double")
        vus.add(identifiant)
        _cles_connues(table, CLES_FICHE, ou)
        anomalie = _texte(table, "anomalie", ou, obligatoire=False)
        if anomalie is not None and not _ANOMALIE.match(anomalie):
            raise ErreurRecette(
                f"{FICHES.as_posix()} : {ou} : anomalie « {anomalie} » : ANO-n attendu"
            )
        fiche = Fiche(
            id=identifiant,
            titre=_texte(table, "titre", ou),
            fonctionnalites=_liste(table, "fonctionnalites", ou),
            cas=_liste(table, "cas", ou, int),
            objectif=_texte(table, "objectif", ou),
            depart=_texte(table, "depart", ou),
            etapes=_liste(table, "etapes", ou),
            attendu=_liste(table, "attendu", ou),
            tests=_liste(table, "tests", ou),
            remarque=_texte(table, "remarque", ou, obligatoire=False),
            essai=_essai(table.get("essai"), ou),
            suivi=_essai(table.get("suivi"), f"{ou}, suivi"),
            anomalie=anomalie,
        )
        if not fiche.etapes or not fiche.attendu:
            raise ErreurRecette(f"{FICHES.as_posix()} : {ou} : étapes ou résultat attendu vides")
        partie = identifiant.split("-")[1]
        if partie not in ("BOUT", "EXP") and partie not in fiche.fonctionnalites:
            raise ErreurRecette(
                f"{FICHES.as_posix()} : {ou} : la fonctionnalité {partie} de l'identifiant "
                "doit figurer dans « fonctionnalites »"
            )
        fiches.append(fiche)
    for i, t in enumerate(donnees.get("sans_fiche", []), start=1):
        _cles_connues(t, CLES_SANS_FICHE, f"sans_fiche n° {i}")
    inconnues = sorted(set(donnees) - {"fiche", "sans_fiche"})
    if inconnues:
        raise ErreurRecette(
            f"{FICHES.as_posix()} : table(s) inconnue(s) {', '.join(inconnues)} ; "
            "tables permises : [[fiche]] et [[sans_fiche]]"
        )
    sans_fiche = [
        SansFiche(
            _texte(t, "fonctionnalite", f"sans_fiche n° {i}"),
            _texte(t, "raison", f"sans_fiche n° {i}"),
        )
        for i, t in enumerate(donnees.get("sans_fiche", []), start=1)
    ]
    fiches.sort(key=lambda f: f.rang)
    logger.debug("%d fiches et %d fonctionnalités sans fiche lues", len(fiches), len(sans_fiche))
    return fiches, sans_fiche


@dataclass(frozen=True)
class Resultat:
    """Résultat d'un test dans une campagne."""

    etat: str
    """`reussi`, `echec` ou `ignore`."""
    duree_s: float
    raison: str | None = None


@dataclass
class Campagne:
    """Une exécution enregistrée de la famille des tests d'intégration."""

    nom: str
    systeme: str
    python: str
    processeurs: str
    version: str
    debut: dt.datetime
    fin: dt.datetime
    resultats: dict[str, Resultat]
    ecartes: list[dict[str, str]] = field(default_factory=list)
    """Entrées du fichier de résultats non retenues : `test`, `le`, `motif`."""

    def vers_dict(self) -> dict:
        """Forme enregistrée dans `campagnes.json`."""
        return {
            "nom": self.nom,
            "systeme": self.systeme,
            "python": self.python,
            "processeurs": self.processeurs,
            "version": self.version,
            "debut": self.debut.isoformat(timespec="seconds"),
            "fin": self.fin.isoformat(timespec="seconds"),
            "resultats": {
                cle: {"etat": r.etat, "duree_s": r.duree_s, "raison": r.raison}
                for cle, r in sorted(self.resultats.items())
            },
            "ecartes": self.ecartes,
        }

    def compte(self, etat: str) -> int:
        """Nombre de tests dans l'état donné."""
        return sum(r.etat == etat for r in self.resultats.values())


def lire_campagnes(racine: Path) -> list[Campagne]:
    """Campagnes enregistrées, dans l'ordre du fichier ; absent : aucune."""
    chemin = racine / CAMPAGNES
    if not chemin.is_file():
        logger.warning("Aucune campagne enregistrée : %s absent", CAMPAGNES.as_posix())
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        campagnes = []
        for brut in donnees["campagnes"]:
            campagnes.append(
                Campagne(
                    nom=brut["nom"],
                    systeme=brut["systeme"],
                    python=brut["python"],
                    processeurs=brut["processeurs"],
                    version=brut["version"],
                    debut=dt.datetime.fromisoformat(brut["debut"]),
                    fin=dt.datetime.fromisoformat(brut["fin"]),
                    resultats={
                        cle: Resultat(_etat(r["etat"], cle), float(r["duree_s"]), r.get("raison"))
                        for cle, r in brut["resultats"].items()
                    },
                    ecartes=list(brut.get("ecartes", [])),
                )
            )
    except (OSError, ValueError, KeyError, TypeError) as erreur:
        raise ErreurRecette(
            f"{CAMPAGNES.as_posix()} illisible ({type(erreur).__name__} : {erreur}) ; "
            "l'enregistrer de nouveau par --campagne"
        ) from erreur
    return campagnes


ETATS = ("reussi", "echec", "ignore")
"""États qu'un résultat de campagne peut prendre."""


def _etat(etat: object, cle: str) -> str:
    """L'état lu, ou `ValueError` s'il est inconnu."""
    if etat not in ETATS:
        raise ValueError(f"état inconnu « {etat} » pour {cle}")
    return str(etat)


def _neutraliser(texte: str | None) -> str | None:
    """Raison sans chemin absolu (remplacé par « … »)."""
    if texte is None:
        return None
    propre = _CHEMIN_ABSOLU.sub("…", texte)
    if propre != texte:
        logger.info("Chemin absolu retiré d'une raison : %s", propre)
    return propre


def campagne_depuis_resultats(
    fichier: Path, nom: str, systeme: str, python: str, processeurs: str, version: str
) -> Campagne:
    """Campagne tirée d'un fichier de résultats de l'application.

    Ne garde que les tests d'intégration datés entre le début et la fin de la
    dernière exécution ; les autres entrées d'intégration sont écartées avec
    leur motif. Lève `ErreurRecette` si le fichier est illisible ou si la
    dernière exécution n'est pas une exécution terminée.
    """
    try:
        donnees = json.loads(fichier.read_text(encoding="utf-8"))
        derniere = donnees["derniere"]
        debut = dt.datetime.fromisoformat(derniere["debut"])
        fin = dt.datetime.fromisoformat(derniere["fin"])
        issue = derniere["issue"]
        tests = donnees["tests"]
    except (OSError, ValueError, KeyError, TypeError) as erreur:
        raise ErreurRecette(
            f"{fichier.name} : fichier de résultats illisible ({type(erreur).__name__} : {erreur}) ;"
            " attendu : <EDT_LOG_DIR>/tests/resultats.json écrit par l'application"
        ) from erreur
    if issue != "terminee":
        raise ErreurRecette(
            f"{fichier.name} : la dernière exécution n'est pas terminée (issue « {issue} ») : "
            "relancer toute la famille des tests d'intégration"
        )
    resultats: dict[str, Resultat] = {}
    ecartes: list[dict[str, str]] = []
    for cle, brut in sorted(tests.items()):
        if not cle.startswith(DOSSIER_INTEGRATION):
            continue
        try:
            le = dt.datetime.fromisoformat(brut["le"])
            etat = _etat(brut["etat"], cle)
            duree = round(float(brut.get("duree_s") or 0.0), 3)
            dans_l_execution = debut <= le <= fin
        except (ValueError, KeyError, TypeError) as erreur:
            raise ErreurRecette(
                f"{fichier.name} : résultat de {cle} illisible ({erreur})"
            ) from erreur
        if not dans_l_execution:
            ecartes.append(
                {
                    "test": cle,
                    "le": le.isoformat(timespec="seconds"),
                    "motif": "résultat daté hors de l'exécution enregistrée",
                }
            )
            continue
        raison = _neutraliser(brut.get("raison")) if etat == "ignore" else None
        resultats[cle] = Resultat(etat, duree, raison)
    remede = "relancer `python -m edt tests --famille integration`"
    if not resultats:
        raise ErreurRecette(
            f"{fichier.name} : aucun test d'intégration dans l'exécution enregistrée : {remede}"
        )
    if derniere.get("famille") != "integration" or derniere.get("nb_tests") != len(resultats):
        raise ErreurRecette(
            f"{fichier.name} : la dernière exécution est « {derniere.get('libelle')} » "
            f"({derniere.get('nb_tests')} tests), pas toute la famille des tests d'intégration "
            f"({len(resultats)} résultats datés de l'exécution) : {remede}"
        )
    if ecartes:
        logger.warning(
            "%d entrée(s) écartée(s) du fichier de résultats, dont %s (%s)",
            len(ecartes),
            ecartes[0]["test"],
            ecartes[0]["motif"],
        )
    return Campagne(nom, systeme, python, processeurs, version, debut, fin, resultats, ecartes)


def ecrire_campagnes(racine: Path, campagnes: list[Campagne]) -> None:
    """Écrit `campagnes.json` (lisible, trié, fin de ligne Unix)."""
    texte = json.dumps(
        {"format": FORMAT_CAMPAGNES, "campagnes": [c.vers_dict() for c in campagnes]},
        ensure_ascii=False,
        indent=1,
    )
    chemin = racine / CAMPAGNES
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- #
# Recette assemblée, contrôles et verdicts
# --------------------------------------------------------------------------- #


@dataclass
class Recette:
    """Toutes les sources lues."""

    fichiers: dict[str, FichierIntegration]
    fonctionnalites: list[Fonctionnalite]
    cas: dict[int, str]
    fiches: list[Fiche]
    sans_fiche: list[SansFiche]
    campagnes: list[Campagne]
    titres: dict[str, str]
    """Titre de chaque composant d'intégration."""

    @property
    def tests(self) -> set[str]:
        """Identifiants de tous les tests d'intégration du dépôt."""
        return {t for f in self.fichiers.values() for t in f.tests}

    @property
    def realisees(self) -> list[Fonctionnalite]:
        """Fonctionnalités réalisées du livret 1."""
        return [f for f in self.fonctionnalites if f.realisee]


def lire(racine: Path = RACINE) -> Recette:
    """Lit toutes les sources ; `ErreurRecette` si l'une est illisible."""
    fonctionnalites, cas = lire_livret_fonctionnel(racine)
    fiches, sans_fiche = lire_fiches(racine)
    titres = {
        composant: titre
        for (cote, composant), titre in inventorier(racine).titres.items()
        if cote == "integration"
    }
    return Recette(
        fichiers=lire_tests(racine),
        fonctionnalites=fonctionnalites,
        cas=cas,
        fiches=fiches,
        sans_fiche=sans_fiche,
        campagnes=lire_campagnes(racine),
        titres=titres,
    )


def citations_invalides(recette: Recette) -> list[str]:
    """Tests cités par une fiche qui n'existent pas ou ne sont pas dans `tests/integration/`."""
    connus = recette.tests
    fautes = []
    for fiche in recette.fiches:
        for test in fiche.tests:
            if not test.startswith(DOSSIER_INTEGRATION):
                fautes.append(f"{fiche.id} : {test} n'est pas dans {DOSSIER_INTEGRATION}")
            elif test not in connus:
                fautes.append(f"{fiche.id} : test inconnu {test} (renommé ou supprimé ?)")
        doublons = [t for t, n in Counter(fiche.tests).items() if n > 1]
        fautes += [f"{fiche.id} : test cité deux fois {t}" for t in doublons]
    return fautes


def fiches_sans_preuve(recette: Recette) -> list[str]:
    """Fiches sans test automatisé ni essai manuel."""
    return [
        f"{f.id} : ni test automatisé ni essai manuel"
        for f in recette.fiches
        if not f.tests and f.essai is None
    ]


def fonctionnalites_sans_fiche(recette: Recette) -> list[str]:
    """Fonctionnalités réalisées sans fiche ni déclaration, et déclarations incohérentes."""
    codes = {f.code for f in recette.fonctionnalites}
    couvertes = {code for f in recette.fiches for code in f.fonctionnalites}
    declarees = {s.fonctionnalite for s in recette.sans_fiche}
    fautes = [
        f"{f.code} ({f.libelle}) : fonctionnalité réalisée sans fiche ; écrire une fiche ou la "
        f"déclarer dans [[sans_fiche]] de {FICHES.as_posix()} avec sa raison"
        for f in recette.realisees
        if f.code not in couvertes and f.code not in declarees
    ]
    fautes += [
        f"{code} : déclarée sans fiche mais citée par une fiche ; retirer la déclaration"
        for code in sorted(declarees & couvertes)
    ]
    fautes += [
        f"{fiche.id} : fonctionnalité inconnue du livret 1 : {code}"
        for fiche in recette.fiches
        for code in fiche.fonctionnalites
        if code not in codes
    ]
    fautes += [
        f"{fiche.id} : cas d'usage inconnu du livret 1 : {n}"
        for fiche in recette.fiches
        for n in fiche.cas
        if n not in recette.cas
    ]
    return fautes


def resultats_disparus(recette: Recette) -> dict[str, list[str]]:
    """Par campagne, les tests enregistrés qui n'existent plus dans le dépôt."""
    connus = recette.tests
    return {
        c.nom: sorted(t for t in c.resultats if t not in connus)
        for c in recette.campagnes
        if any(t not in connus for t in c.resultats)
    }


def problemes(recette: Recette) -> list[str]:
    """Tous les défauts qui empêchent de produire un livret juste."""
    return (
        citations_invalides(recette)
        + fiches_sans_preuve(recette)
        + fonctionnalites_sans_fiche(recette)
    )


@dataclass(frozen=True)
class Obtenu:
    """Résultat d'une fiche dans une campagne."""

    campagne: Campagne
    reussis: int
    echecs: int
    ignores: dict[str, int]
    """Raison d'abandon → nombre de tests ignorés."""
    absents: int


def obtenus(fiche: Fiche, campagnes: list[Campagne]) -> list[Obtenu]:
    """Résultats de la fiche dans chaque campagne."""
    liste = []
    for c in campagnes:
        resultats = [c.resultats.get(t) for t in fiche.tests]
        ignores = Counter(r.raison or "sans raison" for r in resultats if r and r.etat == "ignore")
        liste.append(
            Obtenu(
                campagne=c,
                reussis=sum(1 for r in resultats if r and r.etat == "reussi"),
                echecs=sum(1 for r in resultats if r and r.etat == "echec"),
                ignores=dict(ignores),
                absents=sum(1 for r in resultats if r is None),
            )
        )
    return liste


def verdict(fiche: Fiche, campagnes: list[Campagne]) -> str:
    """Verdict de la fiche (règle en tête du module)."""
    verdicts = [verdict_des_tests(fiche.tests, campagnes)] if fiche.tests else []
    if fiche.essai is not None:
        verdicts.append(fiche.essai.verdict)
    # Sans test ni essai, la liste est vide : « Non exécutée », jamais « Conforme ».
    return _pire(verdicts)


def verdict_des_tests(tests: Iterable[str], campagnes: list[Campagne]) -> str:
    """Règle unique du verdict d'un ensemble de tests : un échec dans une campagne donne
    « Non conforme » ; sinon chaque test réussi dans au moins une campagne donne « Conforme » ;
    sinon « Non exécutée ». Un ensemble vide est « Non exécutée »."""
    tests = list(tests)
    if not tests:
        return NON_EXECUTEE
    etats = [[c.resultats[t].etat for c in campagnes if t in c.resultats] for t in tests]
    if any("echec" in e for e in etats):
        return NON_CONFORME
    if all("reussi" in e for e in etats):
        return CONFORME
    return NON_EXECUTEE


def _pire(verdicts: list[str]) -> str:
    for possible in (NON_CONFORME, NON_EXECUTEE, CONFORME):
        if possible in verdicts:
            return possible
    return NON_EXECUTEE


# --------------------------------------------------------------------------- #
# Production du Markdown
# --------------------------------------------------------------------------- #


def _date(moment: dt.date) -> str:
    return moment.strftime("%d/%m/%Y")


def _heure(moment: dt.datetime) -> str:
    return f"{moment.hour} h {moment.minute:02d}"


def _duree(secondes: float) -> str:
    """`297` donne « 4 min 57 s » ; `12.4` donne « 12 s »."""
    secondes = round(secondes)
    minutes, reste = divmod(secondes, 60)
    return f"{minutes} min {reste:02d} s" if minutes else f"{reste} s"


def _pourcentage(partie: int, tout: int) -> str:
    return f"{100 * partie / tout:.1f}".replace(".", ",") + " %" if tout else "—"


def _pluriel(n: int, mot: str, pluriel: str | None = None) -> str:
    return f"{n} {mot if n <= 1 else (pluriel or mot + 's')}"


def _court(test: str) -> str:
    """Identifiant sans le préfixe `tests/integration/`."""
    return test.removeprefix(DOSSIER_INTEGRATION)


def _cellule(texte: str) -> str:
    return texte.replace("|", "\\|")


def produire_synthese(recette: Recette) -> str:
    """Section 3 : résultats par campagne, par composant, et état des fiches."""
    lignes: list[str] = []
    if not recette.campagnes:
        return "Aucune campagne enregistrée.\n"
    charge = {t for f in recette.fichiers.values() for t in f.charge}
    executes = {t for c in recette.campagnes for t in c.resultats}
    jamais = sorted(t for t in recette.tests - charge if t not in executes)
    phrase = (
        f"Le dépôt compte {_pluriel(len(recette.tests), 'fonction')} de test d'intégration, dont "
        f"{_pluriel(len(charge), 'test')} de charge, lancés à part ; les campagnes enregistrées "
        f"en ont exécuté {len(recette.tests & executes)}."
    )
    if jamais:
        phrase += (
            " "
            + ("Un test ajouté" if len(jamais) == 1 else f"{len(jamais)} tests ajoutés")
            + " au dépôt après les "
            "campagnes n'y "
            + ("figure" if len(jamais) == 1 else "figurent")
            + " pas : "
            + ", ".join(f"`{_court(t)}`" for t in jamais)
            + " (non exécuté"
            + ("s" if len(jamais) > 1 else "")
            + " dans les campagnes)."
        )
    lignes += [
        phrase,
        "",
        "| Campagne | Date et heures | Système | Python | Tests | Réussis | Échecs | Ignorés | Durée |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for c in recette.campagnes:
        lignes.append(
            f"| {c.nom} (EDT {c.version}, {c.processeurs} processeurs) | {_date(c.debut)}, "
            f"{_heure(c.debut)} à {_heure(c.fin)} | {c.systeme} | {c.python} | "
            f"{len(c.resultats)} | {c.compte('reussi')} | {c.compte('echec')} | "
            f"{c.compte('ignore')} | {_duree((c.fin - c.debut).total_seconds())} |"
        )
    lignes += [
        "",
        "La durée va de la première à la dernière heure enregistrées par l'application pour "
        "l'exécution.",
    ]
    for c in recette.campagnes:
        ignores = Counter(
            r.raison or "sans raison" for r in c.resultats.values() if r.etat == "ignore"
        )
        for raison, n in sorted(ignores.items()):
            lignes += [
                "",
                f"{c.nom} : {_pluriel(n, 'test ignoré', 'tests ignorés')}, raison "
                f"enregistrée « {raison} » :",
            ]
            lignes += [
                f"- `{_court(t)}`"
                for t, r in sorted(c.resultats.items())
                if r.etat == "ignore" and (r.raison or "sans raison") == raison
            ]
        if c.ecartes:
            lignes += [
                "",
                f"{c.nom} : "
                + (
                    "1 entrée du fichier de résultats écartée, non comptée"
                    if len(c.ecartes) == 1
                    else f"{len(c.ecartes)} entrées du fichier de résultats écartées, non comptées"
                )
                + " dans les nombres ci-dessus :",
            ]
            lignes += [
                f"- `{_court(e['test'])}` (résultat du {_date(dt.datetime.fromisoformat(e['le']))}"
                f" : {e['motif']})"
                for e in c.ecartes
            ]
    disparus = resultats_disparus(recette)
    for nom, tests in disparus.items():
        lignes += [
            "",
            f"{nom} : {_pluriel(len(tests), 'résultat porte', 'résultats portent')} "
            "sur un test qui n'existe plus dans le dépôt :",
        ]
        lignes += [f"- `{_court(t)}`" for t in tests]

    # Par composant.
    cites = {t for f in recette.fiches for t in f.tests}
    lignes += [
        "",
        "**Par composant.** Nombre de fonctions de test d'intégration du dépôt, tests de charge "
        "compris, tests cités par au moins une fiche, et tests réussis dans chaque campagne.",
        "",
        "| Composant | Tests | Dont charge | Cités | "
        + " | ".join(f"Réussis : {c.nom.lower()}" for c in recette.campagnes)
        + " |",
        "|---|---:|---:|---:|" + "---:|" * len(recette.campagnes),
    ]
    composants = sorted({f.composant for f in recette.fichiers.values()})
    totaux = Counter()
    for composant in composants:
        tests = [t for f in recette.fichiers.values() if f.composant == composant for t in f.tests]
        charge = sum(len(f.charge) for f in recette.fichiers.values() if f.composant == composant)
        valeurs = [len(tests), charge, sum(t in cites for t in tests)]
        valeurs += [
            sum(c.resultats.get(t, Resultat("", 0)).etat == "reussi" for t in tests)
            for c in recette.campagnes
        ]
        for i, v in enumerate(valeurs):
            totaux[i] += v
        lignes.append(f"| `{composant}` | " + " | ".join(str(v) for v in valeurs) + " |")
    lignes.append(
        "| **Total** | " + " | ".join(f"**{totaux[i]}**" for i in range(len(totaux))) + " |"
    )

    # Fiches.
    verdicts = Counter(verdict(f, recette.campagnes) for f in recette.fiches)
    manuelles = sum(1 for f in recette.fiches if f.essai is not None)
    total_tests = len(recette.tests)
    lignes += [
        "",
        f"**Fiches.** Le cahier compte {_pluriel(len(recette.fiches), 'fiche')}, dont "
        f"{_pluriel(manuelles, 'fiche')} d'essai manuel : "
        + ", ".join(f"{verdicts[v]} « {v} »" for v in VERDICTS)
        + f". Les fiches citent {len(cites)} des {total_tests} tests d'intégration du dépôt "
        f"({_pourcentage(len(cites), total_tests)}) ; les autres figurent à l'annexe B, "
        "rangés par fichier.",
    ]
    non_conformes = [f for f in recette.fiches if verdict(f, recette.campagnes) == NON_CONFORME]
    if non_conformes:
        details = [
            f"{f.id}, « {f.titre} »"
            + (f", suivie par l'anomalie {f.anomalie} (section 6)" if f.anomalie else "")
            + (
                f" ; essai de suivi du {_date(f.suivi.date)}, après la recette : "
                f"« {f.suivi.verdict} »"
                if f.suivi is not None
                else ""
            )
            for f in non_conformes
        ]
        lignes += [
            "",
            f"**{'Fiche non conforme' if len(details) == 1 else 'Fiches non conformes'}.** "
            + " ; ".join(details)
            + ". Le verdict d'ensemble est donné à la section 8.",
        ]
    else:
        lignes += ["", "Aucune fiche non conforme. Le verdict d'ensemble est donné à la section 8."]
    return "\n".join(lignes) + "\n"


def _ligne_obtenue(o: Obtenu, total: int) -> str:
    reussis = "réussi" if o.reussis <= 1 else "réussis"
    texte = f"{o.campagne.nom}, {_date(o.campagne.debut)} : {o.reussis} {reussis} sur {total}"
    morceaux = []
    if o.echecs:
        morceaux.append(_pluriel(o.echecs, "échec"))
    for raison, n in o.ignores.items():
        morceaux.append(f"{_pluriel(n, 'ignoré')} ({raison})")
    if o.absents:
        morceaux.append(_pluriel(o.absents, "non exécuté"))
    return texte + (" ; " + " ; ".join(morceaux) if morceaux else "") + "."


def produire_fiche(fiche: Fiche, recette: Recette) -> str:
    """Une fiche, en Markdown."""
    libelles = {f.code: f.libelle for f in recette.fonctionnalites}
    lignes = [f"#### {titre_de_fiche(fiche)}", ""]
    if fiche.fonctionnalites:
        entete = "Fonctionnalités : " if len(fiche.fonctionnalites) > 1 else "Fonctionnalité : "
        entete += " ; ".join(f"{c} {libelles.get(c, '?')}" for c in fiche.fonctionnalites)
    else:
        entete = "Exploitation et robustesse, sans fonctionnalité du livret 1"
    if fiche.cas:
        entete += ". Cas d'usage : " + ", ".join(str(n) for n in fiche.cas)
    lignes += [f"*{entete}.*", ""]
    lignes += [
        f"**Objectif.** {fiche.objectif}",
        "",
        f"**Conditions de départ.** {fiche.depart}",
        "",
    ]
    lignes += ["**Étapes.**", ""]
    lignes += [f"{i}. {etape}" for i, etape in enumerate(fiche.etapes, start=1)]
    lignes += ["", "**Résultat attendu.**", ""]
    lignes += [f"- {a}" for a in fiche.attendu]
    lignes.append("")
    if fiche.remarque:
        lignes += [f"**Remarque.** {fiche.remarque}", ""]
    if fiche.tests:
        lignes.append(
            f"**Tests automatisés ({len(fiche.tests)}).** "
            + " · ".join(f"`{_court(t)}`" for t in fiche.tests)
        )
        lignes.append("")
        obtenu = " ".join(
            _ligne_obtenue(o, len(fiche.tests)) for o in obtenus(fiche, recette.campagnes)
        )
        resultat = f"**Résultat obtenu.** {obtenu or 'aucune campagne enregistrée.'}"
        if fiche.essai is None:
            resultat += f" **Verdict : {verdict(fiche, recette.campagnes)}.**"
        lignes += [resultat, ""]
    if fiche.essai is not None:
        e = fiche.essai
        lignes += [
            f"**Essai manuel du {_date(e.date)}.** {e.environnement}",
            "",
            f"**Constat.** {e.constat}",
            "",
        ]
        if e.reserve:
            lignes += [f"**Réserve.** {e.reserve}", ""]
        if len(e.preuves) == 1:
            image, legende = e.preuves[0]
            lignes += [f"![{legende}]({image})", "", f"*Figure. {legende}*", ""]
        elif e.preuves:
            # Plusieurs preuves : côte à côte dans un même paragraphe, avec une seule légende.
            cotes = ["à gauche", "au milieu", "à droite"] if len(e.preuves) == 3 else []
            cotes = cotes or (["à gauche", "à droite"] if len(e.preuves) == 2 else [])
            morceaux = []
            for position, (_, legende) in enumerate(e.preuves):
                texte = legende.rstrip(".")
                texte = texte if position == 0 else texte[0].lower() + texte[1:]
                morceaux.append(f"{texte} ({cotes[position]})" if cotes else texte)
            lignes += [" ".join(f"![{legende}]({image})" for image, legende in e.preuves), ""]
            lignes += [f"*Figure. {' ; '.join(morceaux)}.*", ""]
        lignes += [f"**Verdict : {verdict(fiche, recette.campagnes)}.**"]
    if fiche.suivi is not None:
        s = fiche.suivi
        lignes += [
            "",
            f"**Essai de suivi du {_date(s.date)}, après la recette.** {s.environnement}",
            "",
            f"**Constat.** {s.constat}",
            "",
        ]
        if s.reserve:
            lignes += [f"**Réserve.** {s.reserve}", ""]
        lignes += [
            f"**Verdict de l'essai de suivi : {s.verdict}.** Il ne change pas le verdict de la "
            "fiche, établi sur la version recettée.",
        ]
    return "\n".join(lignes).rstrip("\n") + "\n"


def ancre(titre: str) -> str:
    """Ancre d'un titre, calculée comme GitHub et `docs/livrets/produire_pdf.py`."""
    return re.sub(r"[^\w\- ]", "", titre.strip().lower()).replace(" ", "-")


def titre_de_fiche(fiche: Fiche) -> str:
    """Titre de la fiche détaillée (annexe A), sans les dièses."""
    return f"{fiche.id}. {fiche.titre}"


def _insecable(identifiant: str) -> str:
    """Identifiant à traits d'union insécables (U+2011) : il ne se coupe pas à l'impression."""
    return identifiant.replace("-", "\u2011")


def lien_de_fiche(fiche: Fiche) -> str:
    """Lien du tableau vers la fiche détaillée ; ses tirets insécables gardent l'identifiant sur
    une ligne à l'impression."""
    identifiant = _insecable(fiche.id)
    return f"[{identifiant}](#{ancre(titre_de_fiche(fiche))})"


def entete_de_campagne(campagne: Campagne) -> str:
    """En-tête de colonne court d'une campagne : le premier mot de son nom (« Serveur »)."""
    return campagne.nom.split()[0]


def _cellule_obtenue(o: Obtenu, fiche: Fiche) -> str:
    """« 7/7 », « 6/8, 2 ignorés » : le résultat d'une fiche dans une campagne, pour le tableau."""
    if not fiche.tests:
        return "—"
    texte = f"{o.reussis}/{len(fiche.tests)}"
    for nombre, mot in ((o.echecs, "échec"), (sum(o.ignores.values()), "ignoré")):
        if nombre:
            texte += f", {_pluriel(nombre, mot)}"
    if o.absents:
        texte += f", {_pluriel(o.absents, 'non exécuté')}"
    return texte


def produire_tableau(recette: Recette) -> str:
    """Section 4 : le tableau de recette, une ligne par fiche, par famille."""
    lignes: list[str] = []
    entetes = " | ".join(entete_de_campagne(c) for c in recette.campagnes)
    for numero, (famille, titre) in enumerate(FAMILLES.items(), start=1):
        lignes += [
            f"### 4.{numero} {titre}",
            "",
            f"| Fiche | Fonction. | Ce qui est vérifié | Tests | {entetes} | Verdict |",
            "|---|---|---|---|" + "---|" * len(recette.campagnes) + "---|",
        ]
        for f in (x for x in recette.fiches if x.famille == famille):
            tests = str(len(f.tests)) if f.tests else ""
            if f.essai is not None:
                # Cellule courte : la date de l'essai reste dans la fiche détaillée.
                tests = f"{tests} et essai manuel" if tests else "essai manuel"
            cellules = " | ".join(_cellule_obtenue(o, f) for o in obtenus(f, recette.campagnes))
            lignes.append(
                f"| {lien_de_fiche(f)} | {', '.join(f.fonctionnalites) or '—'} "
                f"| {_cellule(f.titre)} | {tests} | {cellules} | {verdict(f, recette.campagnes)}"
                + (f" ; suivi {f.suivi.verdict.lower()}" if f.suivi is not None else "")
                + " |"
            )
        lignes.append("")
    return "\n".join(lignes).rstrip("\n") + "\n"


def produire_fiches(recette: Recette) -> str:
    """Annexe A : les fiches détaillées ; chaque famille commence sur une nouvelle page (`---`)."""
    lignes: list[str] = []
    for numero, (famille, titre) in enumerate(FAMILLES.items(), start=1):
        de_la_famille = [f for f in recette.fiches if f.famille == famille]
        if numero > 1:
            lignes += ["---", ""]
        lignes += [f"### A.{numero} {titre}", ""]
        if not de_la_famille:
            lignes += ["Aucune fiche.", ""]
        for fiche in de_la_famille:
            lignes += [produire_fiche(fiche, recette)]
    texte = "\n".join(lignes).rstrip("\n") + "\n"
    # Les légendes des preuves sont numérotées dans l'ordre du livret (« *Figure 1. …* »).
    morceaux = texte.split("*Figure. ")
    return morceaux[0] + "".join(
        f"*Figure {numero}. {suite}" for numero, suite in enumerate(morceaux[1:], start=1)
    )


def produire_matrice(recette: Recette) -> str:
    """Section 5 : fonctionnalités et cas d'usage du livret 1, fiches qui les couvrent."""
    declarees = {s.fonctionnalite for s in recette.sans_fiche}
    lignes = [
        "| N° | Fonctionnalité | Fiches | Tests | Verdict |",
        "|---|---|---|---:|---|",
    ]
    for f in recette.realisees:
        fiches = [x for x in recette.fiches if f.code in x.fonctionnalites]
        tests = {t for x in fiches for t in x.tests}
        if fiches:
            etat = _pire([verdict(x, recette.campagnes) for x in fiches])
        else:
            etat = "Sans fiche (section 7)" if f.code in declarees else "**Sans fiche**"
        lignes.append(
            f"| {f.code} | {_cellule(f.libelle)} | {', '.join(_insecable(x.id) for x in fiches) or '—'} | "
            f"{len(tests)} | {etat} |"
        )
    exploitation = [x for x in recette.fiches if not x.fonctionnalites]
    if exploitation:
        tests = {t for x in exploitation for t in x.tests}
        etat = _pire([verdict(x, recette.campagnes) for x in exploitation])
        lignes.append(
            f"| — | Exploitation et robustesse (hors livret 1) | "
            f"{', '.join(_insecable(x.id) for x in exploitation)} | {len(tests)} | {etat} |"
        )
    lignes += ["", "| Cas d'usage du livret 1 | Fiches |", "|---|---|"]
    for numero, titre in sorted(recette.cas.items()):
        fiches = [_insecable(x.id) for x in recette.fiches if numero in x.cas]
        lignes.append(f"| Cas {numero}. {_cellule(titre)} | {', '.join(fiches) or '—'} |")
    return "\n".join(lignes) + "\n"


def produire_reserves(recette: Recette) -> str:
    """Section 7 : fonctionnalités réalisées déclarées sans fiche."""
    if not recette.sans_fiche:
        return "Chaque fonctionnalité réalisée du livret 1 a au moins une fiche.\n"
    libelles = {f.code: f.libelle for f in recette.fonctionnalites}
    return (
        "\n".join(
            f"- **{s.fonctionnalite} {libelles.get(s.fonctionnalite, '?')}** : {s.raison}"
            for s in recette.sans_fiche
        )
        + "\n"
    )


def produire_bilan(recette: Recette) -> str:
    """Section 8 : les nombres du verdict d'ensemble."""
    verdicts = Counter(verdict(f, recette.campagnes) for f in recette.fiches)
    lignes = []
    for c in recette.campagnes:
        lignes.append(
            f"- {c.nom}, {_date(c.debut)} : "
            + _pluriel(
                len(c.resultats), "test d'intégration exécuté", "tests d'intégration exécutés"
            )
            + ", "
            f"{_pluriel(c.compte('reussi'), 'réussi')}, {_pluriel(c.compte('echec'), 'échec')}, "
            f"{_pluriel(c.compte('ignore'), 'ignoré')}."
        )
    realisees = recette.realisees
    couvertes = {code for f in recette.fiches for code in f.fonctionnalites}
    lignes.append(
        f"- {_pluriel(len(recette.fiches), 'fiche')} : "
        + ", ".join(f"{verdicts[v]} « {v} »" for v in VERDICTS)
        + "."
    )
    lignes.append(
        f"- {sum(f.code in couvertes for f in realisees)} sur "
        f"{_pluriel(len(realisees), 'fonctionnalité réalisée', 'fonctionnalités réalisées')} "
        "ont au moins une fiche"
        + (
            f" ; {_pluriel(len(recette.sans_fiche), 'est déclarée', 'sont déclarées')} sans fiche "
            "(section 7)."
            if recette.sans_fiche
            else "."
        )
    )
    return "\n".join(lignes) + "\n"


def produire_annexe(recette: Recette) -> str:
    """Annexe B : chaque fichier de tests d'intégration, ses nombres et son verdict."""
    cites = {t for f in recette.fiches for t in f.tests}
    executes = {t for c in recette.campagnes for t in c.resultats}
    entetes = " | ".join(c.nom for c in recette.campagnes)
    lignes = [
        "Pour chaque campagne : tests réussis sur tests du fichier, puis durée cumulée des tests. "
        "Les tests de charge (« charge ») ne partent pas avec la famille : ils se lancent par une "
        "commande de l'administrateur.",
    ]
    for composant in sorted({f.composant for f in recette.fichiers.values()}):
        lignes += [
            "",
            f"#### `{composant}` : {recette.titres.get(composant, composant)}",
            "",
            f"| Fichier : ce qu'il vérifie | Tests | Cités | {entetes} | Verdict |",
            "|---|---:|---:|" + "---|" * len(recette.campagnes) + "---|",
        ]
        for f in (x for x in recette.fichiers.values() if x.composant == composant):
            nom = f.chemin.rsplit("/", 1)[-1]
            cellules = []
            for c in recette.campagnes:
                resultats = [c.resultats[t] for t in f.tests if t in c.resultats]
                texte = (
                    f"{sum(r.etat == 'reussi' for r in resultats)}/{len(f.tests)}, "
                    f"{_duree(sum(r.duree_s for r in resultats))}"
                )
                ignores = sum(r.etat == "ignore" for r in resultats)
                echecs = sum(r.etat == "echec" for r in resultats)
                if echecs:
                    texte += f", {_pluriel(echecs, 'échec')}"
                if ignores:
                    texte += f", {ignores} ignoré" + ("s" if ignores > 1 else "")
                cellules.append(texte)
            executables = [t for t in f.tests if t not in f.charge]
            # Tests ajoutés au dépôt après les campagnes : dits à part, jamais comptés en échec.
            jamais = [t for t in executables if t not in executes]
            executables = [t for t in executables if t in executes]
            precisions = []
            if f.charge:
                precisions.append(f"{len(f.charge)} charge")
            if jamais:
                precisions.append(
                    f"{len(jamais)} non exécuté"
                    + ("s" if len(jamais) > 1 else "")
                    + " dans les campagnes"
                )
            if not executables:
                etat = "Charge, lancé à part" if not jamais else "Non exécuté dans les campagnes"
            else:
                etat = verdict_des_tests(executables, recette.campagnes)
                if precisions:
                    etat += f" ({', '.join(precisions)})"
            description = _cellule(f.description or "docstring de module absente")
            lignes.append(
                f"| **{nom}** : {description} | {len(f.tests)} | "
                f"{sum(t in cites for t in f.tests)} | {' | '.join(cellules)} | {etat} |"
            )
    return "\n".join(lignes) + "\n"


BLOCS: dict[str, Callable[[Recette], str]] = {
    "recette-synthese": produire_synthese,
    "recette-tableau": produire_tableau,
    "recette-matrice": produire_matrice,
    "recette-reserves": produire_reserves,
    "recette-bilan": produire_bilan,
    "recette-fiches": produire_fiches,
    "recette-resultats-par-fichier": produire_annexe,
}
"""Parties générées du livret, dans l'ordre du livret."""


def debut_de_bloc(nom: str) -> str:
    """Marqueur d'ouverture de la partie générée `nom`."""
    return (
        f"<!-- {nom} : début de la partie générée par `{COMMANDE}`, ne pas modifier à la main -->"
    )


def fin_de_bloc(nom: str) -> str:
    """Marqueur de fermeture de la partie générée `nom`."""
    return f"<!-- {nom} : fin de la partie générée -->"


def _bornes(document: str, nom: str) -> tuple[int, int] | None:
    debut, fin = document.find(debut_de_bloc(nom)), document.find(fin_de_bloc(nom))
    if debut < 0 or fin < debut:
        return None
    return debut + len(debut_de_bloc(nom)), fin


def partie_generee(document: str, nom: str) -> str | None:
    """Texte compris entre les marqueurs du bloc `nom`, ou `None` s'ils manquent."""
    bornes = _bornes(document, nom)
    return None if bornes is None else document[bornes[0] : bornes[1]].strip("\n") + "\n"


def remplacer(document: str, nom: str, contenu: str) -> str:
    """Le document avec le bloc `nom` remplacé ; `ValueError` si ses marqueurs manquent."""
    bornes = _bornes(document, nom)
    if bornes is None:
        raise ValueError(
            f"marqueurs du bloc « {nom} » introuvables dans {LIVRET.as_posix()} : ajouter les "
            f"lignes « {debut_de_bloc(nom)} » et « {fin_de_bloc(nom)} » à l'endroit voulu"
        )
    return f"{document[: bornes[0]]}\n\n{contenu}\n{document[bornes[1] :]}"


def blocs_perimes(document: str, recette: Recette) -> list[str]:
    """Explication de chaque bloc du livret qui diffère de ce que produit le module."""
    perimes = []
    for nom, produire in BLOCS.items():
        actuelle = partie_generee(document, nom)
        if actuelle is None:
            perimes.append(f"bloc « {nom} » : marqueurs absents")
            continue
        attendue = produire(recette)
        if actuelle == attendue:
            continue
        ecarts = [
            ligne
            for ligne in difflib.unified_diff(
                actuelle.splitlines(), attendue.splitlines(), lineterm="", n=0
            )
            if ligne[:1] in "+-" and ligne[:3] not in ("+++", "---")
        ]
        perimes.append(
            f"bloc « {nom} » périmé ; premiers écarts :\n"
            + "\n".join(f"    {x[:160]}" for x in ecarts[:8])
        )
    return perimes


def bilan(recette: Recette) -> str:
    """Résumé affiché par la commande sans option."""
    verdicts = Counter(verdict(f, recette.campagnes) for f in recette.fiches)
    cites = {t for f in recette.fiches for t in f.tests}
    lignes = [
        f"{_pluriel(len(recette.fiches), 'fiche')} : "
        + ", ".join(f"{verdicts[v]} {v.lower()}" for v in VERDICTS),
        f"{_pluriel(len(cites), 'test cité', 'tests cités')} sur "
        f"{_pluriel(len(recette.tests), 'test')} d'intégration "
        f"({_pluriel(len(recette.fichiers), 'fichier')})",
    ]
    for c in recette.campagnes:
        lignes.append(
            f"campagne « {c.nom} » du {_date(c.debut)} : {_pluriel(len(c.resultats), 'test')}, "
            f"{_pluriel(c.compte('reussi'), 'réussi')}, {_pluriel(c.compte('echec'), 'échec')}, "
            f"{_pluriel(c.compte('ignore'), 'ignoré')}, "
            f"{_pluriel(len(c.ecartes), 'entrée écartée', 'entrées écartées')}"
        )
    for nom, tests in resultats_disparus(recette).items():
        lignes.append(f"campagne « {nom} » : {len(tests)} test(s) disparu(s) du dépôt (signalés)")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    """Bilan, écriture du livret (`--ecrire`) ou enregistrement d'une campagne (`--campagne`)."""
    analyseur = argparse.ArgumentParser(
        prog="python -m tests.outils.recette",
        description="Cahier de recette : fiches, campagnes et parties générées du livret 4.",
    )
    analyseur.add_argument("--ecrire", action="store_true", help="mettre à jour le livret 4")
    analyseur.add_argument("--campagne", type=Path, help="fichier de résultats à enregistrer")
    analyseur.add_argument("--nom", help="rôle de la machine (« Serveur de démonstration »)")
    analyseur.add_argument("--systeme", help="système d'exploitation (« Linux (noyau 7.0) »)")
    analyseur.add_argument("--python", help="version de Python (« 3.14.4 »)")
    analyseur.add_argument("--processeurs", default="?", help="nombre de processeurs logiques")
    analyseur.add_argument("--version", help="version de l'application (« 1.0.0 »)")
    analyseur.add_argument(
        "--racine", type=Path, default=RACINE, help="racine du dépôt (par défaut : ce dépôt)"
    )
    options = analyseur.parse_args(argv)
    racine = options.racine

    try:
        if options.campagne is not None:
            manquants = [
                o for o in ("nom", "systeme", "python", "version") if not getattr(options, o)
            ]
            if manquants:
                analyseur.error(
                    "--campagne demande aussi " + ", ".join(f"--{m}" for m in manquants)
                )
            nouvelle = campagne_depuis_resultats(
                options.campagne,
                options.nom,
                options.systeme,
                options.python,
                options.processeurs,
                options.version,
            )
            campagnes = lire_campagnes(racine)
            rang = next((i for i, c in enumerate(campagnes) if c.nom == nouvelle.nom), None)
            remplacee = rang is not None
            if rang is not None:
                campagnes[rang] = nouvelle  # garde sa place : l'ordre des colonnes ne change pas
            else:
                campagnes.append(nouvelle)
            ecrire_campagnes(racine, campagnes)
            logger.info("Campagne %s enregistrée dans %s", nouvelle.nom, CAMPAGNES.as_posix())
            print(
                f"{CAMPAGNES.as_posix()} : campagne « {nouvelle.nom} » "
                f"{'remplacée' if remplacee else 'ajoutée'} ({len(nouvelle.resultats)} tests, "
                f"{len(nouvelle.ecartes)} entrée(s) écartée(s)). Lancer ensuite : {COMMANDE}"
            )
            return 0

        recette = lire(racine)
    except ErreurRecette as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 1

    defauts = problemes(recette)
    for defaut in defauts:
        print(f"Erreur : {defaut}", file=sys.stderr)
    if not options.ecrire:
        print(bilan(recette))
        return 1 if defauts else 0
    if defauts:
        print("Livret non écrit : corriger d'abord les erreurs ci-dessus.", file=sys.stderr)
        return 1

    chemin = racine / LIVRET
    ancien = chemin.read_text(encoding="utf-8")
    nouveau = ancien
    try:
        for nom, produire in BLOCS.items():
            nouveau = remplacer(nouveau, nom, produire(recette))
    except ValueError as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 1
    if nouveau == ancien:
        print(f"{LIVRET.as_posix()} : parties générées déjà à jour.")
        return 0
    chemin.write_text(nouveau, encoding="utf-8", newline="\n")
    logger.info("Parties générées écrites dans %s", chemin)
    print(f"{LIVRET.as_posix()} : parties générées mises à jour.")
    print(bilan(recette))
    return 0


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
