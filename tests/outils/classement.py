"""Classement automatique des tests en « unitaire » ou « intégration ».

Règle appliquée (elle fait foi, voir `tests/README.md`) :

- Test d'INTÉGRATION : il traverse plusieurs couches de l'application. Soit il
  passe par une interface (requête HTTP par le client de test : `TestClient`,
  `AsyncClient`, `ASGITransport`, y compris par les fixtures `client` ou
  `site` ; ligne de commande par `CliRunner`, y compris par une fixture
  `lanceur` ; autre processus : `subprocess`, `multiprocessing`), soit il
  enchaîne tout le calcul par le service de génération (`lancer_calcul`,
  `attendre_calcul`, y compris par une aide ou une fixture).
- Test UNITAIRE : tout le reste. Il exerce un seul module par ses fonctions
  Python, au besoin sur une base SQLite temporaire ; le solveur appelé
  directement sur des données en mémoire reste unitaire.

Méthode : le module lit le code des tests sans l'exécuter (module `ast`). Pour
chaque fonction de test, il rassemble les noms qu'elle met en jeu, en suivant :

- ses fixtures, résolues comme le fait pytest (module du test, puis
  `conftest.py` du dossier et des dossiers parents, fixtures importées dans un
  `conftest.py`), y compris les fixtures automatiques (`autouse=True`) ;
- les fonctions d'aide du même module ;
- les aides importées d'autres modules de `tests/` (par exemple `tests/outils/`),
  récursivement.

Si l'un de ces noms figure dans `PREUVES`, le test est d'intégration ; sinon il
est unitaire.

Limite connue : une fixture obtenue par `request.getfixturevalue(nom)` avec un
nom calculé à l'exécution n'est pas suivie.

Le module produit aussi les nombres de tests et les tableaux de fichiers des
documents `tests/README.md`, `tests/unitaires/README.md` et
`tests/integration/README.md` (constante `BLOCS`). Chaque partie générée est
encadrée par deux marqueurs HTML (`debut_de_bloc`, `fin_de_bloc`) ;
`tests/test_rangement.py` vérifie qu'elle est identique à ce que produit le
module. Les nombres comptent des fonctions de test (une fonction paramétrée
compte une fois). Pour chaque fichier, la description est la première phrase
de la docstring du module ; pour chaque composant, le titre est la première
phrase de la docstring de son `__init__.py`, sinon le nom du dossier.

Usage, depuis la racine du dépôt :

    python -m tests.outils.classement            # totaux, puis tests mal rangés
    python -m tests.outils.classement --ecrire   # met à jour les trois documents

Sans option, le programme affiche le nombre de tests par côté et par
composant, puis la liste des tests mal rangés ; il sort avec le code 1 s'il y
en a. Avec `--ecrire`, il réécrit les parties générées des documents et sort
avec le code 1 si un marqueur manque ou si un fichier de tests n'a pas de
docstring de module.
"""

from __future__ import annotations

import argparse
import ast
import logging
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

UNITAIRE = "unitaire"
"""Côté des tests unitaires."""

INTEGRATION = "integration"
"""Côté des tests d'intégration."""

DOSSIERS: dict[str, str] = {UNITAIRE: "unitaires", INTEGRATION: "integration"}
"""Dossier de `tests/` qui reçoit chaque côté."""

PREUVES: dict[str, frozenset[str]] = {
    "requête HTTP": frozenset({"TestClient", "AsyncClient", "ASGITransport"}),
    "ligne de commande": frozenset({"CliRunner"}),
    "autre processus": frozenset({"subprocess", "Popen", "multiprocessing"}),
    "calcul complet": frozenset({"lancer_calcul", "attendre_calcul"}),
}
"""Noms qui rendent un test d'intégration, groupés par nature de preuve.

Les fixtures `client`, `site` et `lanceur` n'y figurent pas en tant que noms :
elles sont reconnues parce que leur propre code utilise `TestClient` ou
`CliRunner`, que le classement suit.
"""

Fonction = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


@dataclass(frozen=True)
class Verdict:
    """Classement d'une fonction de test.

    - `cote` : `UNITAIRE` ou `INTEGRATION` ;
    - `raisons` : natures de preuve trouvées (clés de `PREUVES`), vide si unitaire ;
    - `preuves` : noms précis trouvés (par exemple `TestClient`), vide si unitaire.
    """

    cote: str
    raisons: tuple[str, ...]
    preuves: tuple[str, ...]


@dataclass(frozen=True)
class Import:
    """Nom importé depuis un autre module de `tests/`.

    `nom` est le nom d'origine dans `fichier`, ou `None` si le nom local
    désigne le module lui-même (`from tests.outils.x import module`).
    """

    fichier: Path
    nom: str | None


class Module:
    """Un fichier Python de `tests/`, analysé une fois pour toutes."""

    def __init__(self, chemin: Path, racine: Path) -> None:
        self.chemin = chemin
        self.arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        self.fonctions: dict[str, Fonction] = {}
        """Fonctions et classes de premier niveau, par nom."""
        self.fixtures: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        """Fixtures définies dans le module (à tout niveau), par nom."""
        self.automatiques: list[str] = []
        """Fixtures déclarées avec `autouse=True`."""
        self.imports: dict[str, Import] = {}
        """Noms locaux importés d'un autre module de `tests/`."""
        self.noms_externes: dict[str, set[str]] = {}
        """Noms locaux importés hors de `tests/`, avec les noms que porte l'import."""

        for noeud in self.arbre.body:
            if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self.fonctions[noeud.name] = noeud
            elif isinstance(noeud, ast.Import | ast.ImportFrom):
                self._noter_import(noeud, racine)
        for noeud in ast.walk(self.arbre):
            if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef) and _est_fixture(noeud):
                self.fixtures[noeud.name] = noeud
                if _est_automatique(noeud):
                    self.automatiques.append(noeud.name)

    def _noter_import(self, noeud: ast.Import | ast.ImportFrom, racine: Path) -> None:
        """Range chaque nom importé : vers un module de `tests/` ou vers l'extérieur."""
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                parties = alias.name.split(".")
                fichier = (
                    _fichier_module(racine.joinpath(*parties)) if parties[0] == "tests" else None
                )
                if fichier is not None and alias.asname:
                    self.imports[alias.asname] = Import(fichier, None)
                else:
                    self.noms_externes[alias.asname or parties[0]] = set(parties)
            return

        base = noeud.module or ""
        if noeud.level:  # import relatif : résolu depuis le dossier du module
            dossier = self.chemin.parent
            for _ in range(noeud.level - 1):
                dossier = dossier.parent
            cible: Path | None = dossier.joinpath(*base.split(".")) if base else dossier
        elif base.split(".")[0] == "tests":
            cible = racine.joinpath(*base.split("."))
        else:
            cible = None

        for alias in noeud.names:
            local = alias.asname or alias.name
            if cible is not None:
                sous_module = cible / f"{alias.name}.py"
                if sous_module.is_file():
                    self.imports[local] = Import(sous_module, None)
                    continue
                fichier = _fichier_module(cible)
                if fichier is not None:
                    self.imports[local] = Import(fichier, alias.name)
                    continue
            self.noms_externes[local] = {alias.name, *base.split(".")}


def _fichier_module(chemin: Path) -> Path | None:
    """Fichier d'un module donné par son chemin sans extension (`x.py` ou `x/__init__.py`)."""
    for candidat in (chemin.with_suffix(".py"), chemin / "__init__.py"):
        if candidat.is_file():
            return candidat
    return None


def _decorateurs(fonction: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.expr]:
    """Décorateurs de la fonction, sans l'appel éventuel (`@f(...)` donne `f`)."""
    for decorateur in fonction.decorator_list:
        yield decorateur.func if isinstance(decorateur, ast.Call) else decorateur


def _est_fixture(fonction: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Vrai si la fonction porte `@pytest.fixture` ou `@fixture`, avec ou sans arguments."""
    return any(
        (isinstance(d, ast.Attribute) and d.attr == "fixture")
        or (isinstance(d, ast.Name) and d.id == "fixture")
        for d in _decorateurs(fonction)
    )


def _est_automatique(fonction: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Vrai si la fixture est déclarée avec `autouse=True`."""
    for decorateur in fonction.decorator_list:
        if isinstance(decorateur, ast.Call):
            for argument in decorateur.keywords:
                if (
                    argument.arg == "autouse"
                    and isinstance(argument.value, ast.Constant)
                    and argument.value.value
                ):
                    return True
    return False


def _marqueurs(expressions: list[ast.expr]) -> set[str]:
    """Noms des marqueurs `pytest.mark.<nom>` (ou `mark.<nom>`), appelés ou non, d'une liste."""
    noms: set[str] = set()
    for expression in expressions:
        if isinstance(expression, ast.Call):
            expression = expression.func
        if isinstance(expression, ast.Attribute) and (
            (isinstance(expression.value, ast.Attribute) and expression.value.attr == "mark")
            or (isinstance(expression.value, ast.Name) and expression.value.id == "mark")
        ):
            noms.add(expression.attr)
    return noms


def _marqueurs_pytestmark(corps: list[ast.stmt]) -> set[str]:
    """Marqueurs de `pytestmark` (module ou classe), seul ou en liste."""
    noms: set[str] = set()
    for instruction in corps:
        if isinstance(instruction, ast.Assign):
            cibles, valeur = instruction.targets, instruction.value
        elif isinstance(instruction, ast.AnnAssign) and instruction.value is not None:
            cibles, valeur = [instruction.target], instruction.value
        else:
            continue
        if any(isinstance(c, ast.Name) and c.id == "pytestmark" for c in cibles):
            noms |= _marqueurs(
                valeur.elts if isinstance(valeur, ast.List | ast.Tuple) else [valeur]
            )
    return noms


def fonctions_de_test(
    arbre: ast.Module,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, frozenset[str]]]:
    """Fonctions `test_*` d'un module, à tout niveau, avec les marqueurs qu'elles portent.

    Les marqueurs comprennent ceux de la fonction, de ses classes englobantes
    et du `pytestmark` du module ou des classes. Un marqueur posé à
    l'exécution (`pytest.param(..., marks=...)`, hook de collecte) n'est pas vu.
    """
    trouvees: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, frozenset[str]]] = []

    def parcourir(noeud: ast.AST, herites: frozenset[str]) -> None:
        for enfant in ast.iter_child_nodes(noeud):
            marques = herites
            if isinstance(enfant, ast.ClassDef):
                marques |= _marqueurs(enfant.decorator_list) | _marqueurs_pytestmark(enfant.body)
            elif isinstance(enfant, ast.FunctionDef | ast.AsyncFunctionDef):
                marques |= _marqueurs(enfant.decorator_list)
                if enfant.name.startswith("test_"):
                    trouvees.append((enfant, marques))
            parcourir(enfant, marques)

    parcourir(arbre, frozenset(_marqueurs_pytestmark(arbre.body)))
    return trouvees


def _noms_utilises(noeud: ast.AST) -> set[str]:
    """Noms et attributs cités dans le code du nœud (`a.b(c)` donne `a`, `b`, `c`)."""
    noms: set[str] = set()
    for element in ast.walk(noeud):
        if isinstance(element, ast.Name):
            noms.add(element.id)
        elif isinstance(element, ast.Attribute):
            noms.add(element.attr)
    return noms


class Classeur:
    """Analyse tous les fichiers Python de `<racine>/tests` et classe leurs tests."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        self.dossier_tests = racine / "tests"
        self.modules: dict[Path, Module] = {
            fichier: Module(fichier, racine) for fichier in sorted(self.dossier_tests.rglob("*.py"))
        }
        self._memo: dict[tuple[Path, str], set[str]] = {}

    def _conftests(self, fichier: Path) -> list[Module]:
        """`conftest.py` qui s'appliquent au fichier, du plus proche au plus lointain."""
        trouves: list[Module] = []
        dossier = fichier.parent
        while True:
            conftest = dossier / "conftest.py"
            if conftest in self.modules and conftest != fichier:
                trouves.append(self.modules[conftest])
            if dossier == self.dossier_tests or dossier == dossier.parent:
                return trouves
            dossier = dossier.parent

    def _fixture_importee(self, module: Module, nom: str) -> tuple[Module, ast.AST] | None:
        """Fixture `nom` importée dans `module` depuis un autre module de `tests/`."""
        importe = module.imports.get(nom)
        if importe is None or importe.nom is None:
            return None
        cible = self.modules.get(importe.fichier)
        if cible is not None and importe.nom in cible.fixtures:
            return cible, cible.fixtures[importe.nom]
        return None

    def _fixture(self, module: Module, nom: str) -> tuple[Module, ast.AST] | None:
        """Définition de la fixture `nom` vue depuis `module`, comme pytest la résout."""
        for portee in (module, *self._conftests(module.chemin)):
            if nom in portee.fixtures:
                return portee, portee.fixtures[nom]
            importee = self._fixture_importee(portee, nom)
            if importee is not None:
                return importee
        return None

    def noms_atteints(
        self, module: Module, noeud: ast.AST, nom: str, pile: frozenset = frozenset()
    ) -> set[str]:
        """Tous les noms que met en jeu `noeud` (une fonction de `module`), aides et fixtures comprises."""
        cle = (module.chemin, nom)
        if cle in self._memo:
            return self._memo[cle]
        if cle in pile:  # récursion entre aides : déjà en cours d'exploration
            return set()
        pile = pile | {cle}

        noms = _noms_utilises(noeud)
        atteints = set(noms)
        for n in noms:  # `TestClient` importé de fastapi compte aussi par les noms de son module
            atteints |= module.noms_externes.get(n, set())

        parametres: list[str] = []
        if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef):
            parametres = [
                a.arg for a in noeud.args.posonlyargs + noeud.args.args + noeud.args.kwonlyargs
            ]
        automatiques = [
            *module.automatiques,
            *(n for c in self._conftests(module.chemin) for n in c.automatiques),
        ]
        for parametre in [*parametres, *automatiques]:
            fixture = self._fixture(module, parametre)
            if fixture is not None and fixture[1] is not noeud:
                atteints |= self.noms_atteints(fixture[0], fixture[1], parametre, pile)

        for n in noms:
            if n in module.fonctions and module.fonctions[n] is not noeud:
                atteints |= self.noms_atteints(module, module.fonctions[n], n, pile)
            elif n in module.imports:
                atteints |= self._noms_importes(module.imports[n], noms, pile)

        self._memo[cle] = atteints
        return atteints

    def _noms_importes(self, importe: Import, noms: set[str], pile: frozenset) -> set[str]:
        """Noms atteints par une aide importée (ou, pour un module importé, par ses fonctions citées)."""
        cible = self.modules.get(importe.fichier)
        if cible is None:
            return set()
        if importe.nom is None:  # module importé : ses fonctions citées (`module.aide()`) sont suivies
            atteints: set[str] = set()
            for n in noms & cible.fonctions.keys():
                atteints |= self.noms_atteints(cible, cible.fonctions[n], n, pile)
            return atteints
        if importe.nom in cible.fonctions:
            return self.noms_atteints(cible, cible.fonctions[importe.nom], importe.nom, pile)
        if importe.nom in cible.fixtures:
            return self.noms_atteints(cible, cible.fixtures[importe.nom], importe.nom, pile)
        if importe.nom in cible.imports:  # ré-export : l'import du module cible est suivi
            return self._noms_importes(cible.imports[importe.nom], noms | {importe.nom}, pile)
        return set()

    def classer(self) -> dict[str, Verdict]:
        """Classe chaque fonction de test des fichiers `test_*.py`.

        La clé est l'identifiant pytest sans paramètres, relatif à la racine :
        `tests/unitaires/socle/test_base.py::test_nom`.
        """
        classement: dict[str, Verdict] = {}
        for fichier, module in self.modules.items():
            if not fichier.name.startswith("test_"):
                continue
            for noeud, _ in fonctions_de_test(module.arbre):
                noms = self.noms_atteints(module, noeud, noeud.name)
                raisons = tuple(nature for nature, preuves in PREUVES.items() if noms & preuves)
                preuves = tuple(sorted(noms & frozenset().union(*PREUVES.values())))
                identifiant = f"{fichier.relative_to(self.racine).as_posix()}::{noeud.name}"
                classement[identifiant] = Verdict(
                    INTEGRATION if raisons else UNITAIRE, raisons, preuves
                )
        return classement


def classer(racine: Path) -> dict[str, Verdict]:
    """Classe tous les tests du dépôt situé dans `racine` (identifiant du test → verdict)."""
    return Classeur(racine).classer()


def cote_du_dossier(identifiant: str) -> str | None:
    """Côté annoncé par le dossier du test (`tests/unitaires/...` ou `tests/integration/...`), sinon `None`."""
    parties = identifiant.split("::")[0].split("/")
    if len(parties) < 3 or parties[0] != "tests":
        return None
    for cote, dossier in DOSSIERS.items():
        if parties[1] == dossier:
            return cote
    return None


def composant(identifiant: str) -> str:
    """Composant d'un test rangé : `tests/<côté>/<composant>/...` donne `<composant>`."""
    parties = identifiant.split("::")[0].split("/")
    return parties[2] if len(parties) > 3 else "(racine)"


def destination(identifiant: str, verdict: Verdict) -> str:
    """Dossier où le test devrait vivre d'après son verdict (même composant)."""
    return f"tests/{DOSSIERS[verdict.cote]}/{composant(identifiant)}/"


def mal_ranges(classement: dict[str, Verdict]) -> list[str]:
    """Tests de `tests/unitaires/` ou `tests/integration/` dont le verdict contredit le dossier."""
    return sorted(
        identifiant
        for identifiant, verdict in classement.items()
        if cote_du_dossier(identifiant) not in (None, verdict.cote)
    )


def expliquer(identifiant: str, verdict: Verdict) -> str:
    """Une ligne lisible : test fautif, preuve trouvée (ou son absence) et dossier de destination."""
    if verdict.cote == INTEGRATION:
        preuve = (
            f"preuve d'intégration : {', '.join(verdict.raisons)} ({', '.join(verdict.preuves)})"
        )
    else:
        preuve = "aucune preuve d'intégration (ni client HTTP, ni CliRunner, ni processus, ni calcul complet)"
    return f"{identifiant} : {preuve} ; à déplacer dans {destination(identifiant, verdict)}"


# --------------------------------------------------------------------------- #
# Nombres et tableaux des documents de tests
# --------------------------------------------------------------------------- #

COMMANDE = "python -m tests.outils.classement --ecrire"
"""Commande qui régénère les parties générées des documents de tests."""

TITRES_DES_FAMILLES: dict[str, str] = {UNITAIRE: "Unitaires", INTEGRATION: "Intégration"}
"""Titre de colonne de chaque côté dans la vue d'ensemble (dans cet ordre)."""

CONTROLE = Path("tests") / "test_rangement.py"
"""Contrôle du rangement, compté à part : il n'appartient à aucune famille."""

SANS_DOCSTRING = "**docstring de module absente** (sa première phrase décrit le fichier)"
"""Texte affiché à la place de la description d'un fichier de tests sans docstring."""

_FIN_DE_PHRASE = re.compile(r"\.(?=\s|$)")


@dataclass(frozen=True)
class FichierDeTests:
    """Un fichier `test_*.py` d'une famille, avec ses nombres de fonctions de test."""

    cote: str
    """`UNITAIRE` ou `INTEGRATION`."""
    composant: str
    """Premier sous-dossier de la famille (`socle`, `solveur`…), `(racine)` sinon."""
    chemin: str
    """Chemin relatif au dossier de la famille, séparateurs `/` (`socle/test_base.py`)."""
    description: str | None
    """Première phrase de la docstring du module, sans le point final ; `None` sans docstring."""
    fonctions: int
    """Nombre de fonctions `test_*` du fichier."""
    cas_limite: int
    """Fonctions marquées `cas_limite`."""
    perf: int
    """Fonctions marquées `perf`."""


@dataclass(frozen=True)
class Inventaire:
    """Ce que les documents de tests affichent : fichiers, titres des composants, contrôle."""

    fichiers: tuple[FichierDeTests, ...]
    """Fichiers des deux familles, triés par côté, composant puis chemin."""
    titres: dict[tuple[str, str], str]
    """Titre lisible de chaque couple (côté, composant) présent."""
    controle: int
    """Nombre de fonctions de test de `tests/test_rangement.py`."""


def premiere_phrase(docstring: str | None) -> str | None:
    """Première phrase du premier paragraphe, sur une ligne, sans le point final.

    La phrase s'arrête au premier point suivi d'une espace ou de la fin du
    paragraphe (`test_x.py` ou `1.5` ne la coupent pas). `None` si la
    docstring est absente ou vide.
    """
    if not docstring or not docstring.strip():
        return None
    paragraphe = " ".join(re.split(r"\n\s*\n", docstring.strip(), maxsplit=1)[0].split())
    return _FIN_DE_PHRASE.split(paragraphe, maxsplit=1)[0].strip() or None


def titre_par_defaut(composant: str) -> str:
    """Titre d'un composant dont le `__init__.py` n'a pas de docstring : `banc_tests` donne « Banc tests »."""
    mots = composant.replace("_", " ").strip() or composant
    return mots[0].upper() + mots[1:]


def _arbre(fichier: Path) -> ast.Module:
    """Arbre syntaxique d'un fichier Python ; une erreur de syntaxe nomme le fichier."""
    try:
        return ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
    except SyntaxError as erreur:
        raise SyntaxError(f"{fichier} : fichier illisible ({erreur.msg})") from erreur


def inventorier(racine: Path) -> Inventaire:
    """Lit les fichiers de tests des deux familles (sans les exécuter) et compte leurs fonctions."""
    fichiers: list[FichierDeTests] = []
    titres: dict[tuple[str, str], str] = {}
    for cote, dossier_famille in DOSSIERS.items():
        dossier = racine / "tests" / dossier_famille
        if not dossier.is_dir():
            logger.warning("Dossier de tests absent : %s", dossier)
            continue
        for fichier in sorted(dossier.rglob("test_*.py")):
            relatif = fichier.relative_to(dossier)
            composant = relatif.parts[0] if len(relatif.parts) > 1 else "(racine)"
            arbre = _arbre(fichier)
            tests = fonctions_de_test(arbre)
            fichiers.append(
                FichierDeTests(
                    cote=cote,
                    composant=composant,
                    chemin=relatif.as_posix(),
                    description=premiere_phrase(ast.get_docstring(arbre)),
                    fonctions=len(tests),
                    cas_limite=sum("cas_limite" in marques for _, marques in tests),
                    perf=sum("perf" in marques for _, marques in tests),
                )
            )
            if (cote, composant) not in titres:
                init = dossier / composant / "__init__.py"
                titre = premiere_phrase(ast.get_docstring(_arbre(init))) if init.is_file() else None
                titres[cote, composant] = titre or titre_par_defaut(composant)
    fichiers.sort(key=lambda f: (list(DOSSIERS).index(f.cote), f.composant, f.chemin))
    controle = racine / CONTROLE
    nombre_controle = len(fonctions_de_test(_arbre(controle))) if controle.is_file() else 0
    logger.debug("%d fichiers de tests inventoriés sous %s", len(fichiers), racine / "tests")
    return Inventaire(tuple(fichiers), titres, nombre_controle)


def sans_docstring(inventaire: Inventaire) -> list[str]:
    """Fichiers de tests sans docstring de module (chemins relatifs à la racine)."""
    return [
        f"tests/{DOSSIERS[f.cote]}/{f.chemin}" for f in inventaire.fichiers if f.description is None
    ]


def _nombre(n: int) -> str:
    """Nombre groupé par milliers avec une espace (`1 286`)."""
    return f"{n:,}".replace(",", " ")


def _fonctions(n: int) -> str:
    """« 1 fonction », « 12 fonctions »."""
    return f"{_nombre(n)} fonction" + ("s" if n > 1 else "")


def _cellule(texte: str | None) -> str:
    """Texte prêt pour une cellule de tableau Markdown."""
    return SANS_DOCSTRING if texte is None else texte.replace("|", "\\|")


def produire_vue_d_ensemble(inventaire: Inventaire) -> str:
    """Tableau des fonctions de test par composant et par famille, pour `tests/README.md`."""
    cotes = list(TITRES_DES_FAMILLES)
    par_composant: Counter[tuple[str, str]] = Counter()
    totaux: Counter[tuple[str, str]] = Counter()
    for f in inventaire.fichiers:
        par_composant[f.composant, f.cote] += f.fonctions
        totaux[f.cote, "fonctions"] += f.fonctions
        totaux[f.cote, "cas_limite"] += f.cas_limite
        totaux[f.cote, "perf"] += f.perf

    lignes = ["| Composant | " + " | ".join(TITRES_DES_FAMILLES.values()) + " | Total |"]
    lignes.append("|---|" + "---:|" * (len(cotes) + 1))
    for composant in sorted({f.composant for f in inventaire.fichiers}):
        valeurs = [par_composant[composant, c] for c in cotes]
        cellules = " | ".join(_nombre(v) if v else "—" for v in valeurs)
        lignes.append(f"| `{composant}` | {cellules} | {_nombre(sum(valeurs))} |")
    for libelle, cle in (
        ("**Total**", "fonctions"),
        ("dont cas limites (`cas_limite`)", "cas_limite"),
        ("dont tests de charge (`perf`)", "perf"),
    ):
        valeurs = [totaux[c, cle] for c in cotes]
        cellules = [_nombre(v) for v in [*valeurs, sum(valeurs)]]
        if cle == "fonctions":
            cellules = [f"**{c}**" for c in cellules]
        lignes.append(f"| {libelle} | " + " | ".join(cellules) + " |")
    lignes += [
        "",
        f"S'y ajoute le contrôle du rangement, `{CONTROLE.as_posix()}` : "
        f"{_fonctions(inventaire.controle)}.",
    ]
    return "\n".join(lignes) + "\n"


def produire_famille(inventaire: Inventaire, cote: str) -> str:
    """Nombres et tableau des fichiers de chaque composant d'une famille, pour son `README.md`."""
    fichiers = [f for f in inventaire.fichiers if f.cote == cote]
    composants = sorted({f.composant for f in fichiers})
    total = sum(f.fonctions for f in fichiers)
    lignes = [
        f"Ce dossier compte **{_fonctions(total)} de test** dans "
        f"{_nombre(len(composants))} composant{'s' if len(composants) > 1 else ''}, dont "
        f"{_nombre(sum(f.cas_limite for f in fichiers))} marquées `cas_limite` et "
        f"{_nombre(sum(f.perf for f in fichiers))} marquées `perf`."
    ]
    for composant in composants:
        du_composant = [f for f in fichiers if f.composant == composant]
        nombre = sum(f.fonctions for f in du_composant)
        lignes += [
            "",
            f"### `{composant}/` : {inventaire.titres[cote, composant]} ({_fonctions(nombre)})",
            "",
            "| Fichier | Ce qu'il vérifie | Fonctions |",
            "|---|---|---:|",
        ]
        lignes += [
            f"| [`{f.chemin}`]({f.chemin}) | {_cellule(f.description)} | {_nombre(f.fonctions)} |"
            for f in du_composant
        ]
    return "\n".join(lignes) + "\n"


def _produire_unitaires(inventaire: Inventaire) -> str:
    """Partie générée de `tests/unitaires/README.md`."""
    return produire_famille(inventaire, UNITAIRE)


def _produire_integration(inventaire: Inventaire) -> str:
    """Partie générée de `tests/integration/README.md`."""
    return produire_famille(inventaire, INTEGRATION)


BLOCS: dict[str, tuple[Path, Callable[[Inventaire], str]]] = {
    "tests-vue-d-ensemble": (Path("tests") / "README.md", produire_vue_d_ensemble),
    "tests-unitaires": (Path("tests") / DOSSIERS[UNITAIRE] / "README.md", _produire_unitaires),
    "tests-integration": (
        Path("tests") / DOSSIERS[INTEGRATION] / "README.md",
        _produire_integration,
    ),
}
"""Parties générées : nom du bloc → (document relatif à la racine, producteur du contenu)."""


def debut_de_bloc(nom: str) -> str:
    """Marqueur d'ouverture de la partie générée `nom`."""
    return (
        f"<!-- {nom} : début de la partie générée par `{COMMANDE}`, ne pas modifier à la main -->"
    )


def fin_de_bloc(nom: str) -> str:
    """Marqueur de fermeture de la partie générée `nom`."""
    return f"<!-- {nom} : fin de la partie générée -->"


def _bornes(document: str, nom: str) -> tuple[int, int] | None:
    """Positions des deux marqueurs du bloc `nom`, ou `None` s'ils manquent ou sont inversés."""
    debut, fin = document.find(debut_de_bloc(nom)), document.find(fin_de_bloc(nom))
    if debut < 0 or fin < debut:
        return None
    return debut + len(debut_de_bloc(nom)), fin


def partie_generee(document: str, nom: str) -> str | None:
    """Texte compris entre les marqueurs du bloc `nom` (sans eux), ou `None` s'ils manquent."""
    bornes = _bornes(document, nom)
    if bornes is None:
        return None
    return document[bornes[0] : bornes[1]].strip("\n") + "\n"


def remplacer(document: str, nom: str, contenu: str) -> str:
    """Le document avec la partie générée `nom` remplacée par `contenu`.

    Tout ce qui se trouve entre les deux marqueurs est remplacé, y compris des
    restes de conflit de fusion. Lève `ValueError` si les marqueurs manquent
    ou sont dans le désordre.
    """
    bornes = _bornes(document, nom)
    if bornes is None:
        raise ValueError(
            f"marqueurs du bloc « {nom} » introuvables : ajouter les lignes "
            f"« {debut_de_bloc(nom)} » et « {fin_de_bloc(nom)} » à l'endroit voulu"
        )
    return f"{document[: bornes[0]]}\n\n{contenu}\n{document[bornes[1] :]}"


def blocs_perimes(racine: Path, inventaire: Inventaire | None = None) -> list[str]:
    """Explication de chaque bloc dont le document diffère de ce que produit le module."""
    inventaire = inventaire or inventorier(racine)
    perimes: list[str] = []
    for nom, (document, produire) in BLOCS.items():
        chemin = racine / document
        texte = chemin.read_text(encoding="utf-8") if chemin.is_file() else ""
        actuelle = partie_generee(texte, nom)
        if actuelle is None:
            perimes.append(f"{document.as_posix()} : marqueurs du bloc « {nom} » absents")
            continue
        attendue = produire(inventaire)
        if actuelle == attendue:
            continue
        lignes_actuelles, lignes_attendues = set(actuelle.splitlines()), attendue.splitlines()
        ecarts = [
            f"(attendu) {ligne}" for ligne in lignes_attendues if ligne not in lignes_actuelles
        ]
        ecarts += [f"(en trop) {ligne}" for ligne in sorted(lignes_actuelles - {*lignes_attendues})]
        perimes.append(
            f"{document.as_posix()} : bloc « {nom} » périmé ; premiers écarts :\n"
            + "\n".join(f"    {ligne[:160]}" for ligne in ecarts[:8])
        )
    return perimes


def ecrire(racine: Path) -> int:
    """Réécrit les parties générées ; code 1 si un marqueur ou une docstring de module manque."""
    inventaire = inventorier(racine)
    code = 0
    for nom, (document, produire) in BLOCS.items():
        chemin = racine / document
        ancien = chemin.read_text(encoding="utf-8")
        try:
            nouveau = remplacer(ancien, nom, produire(inventaire))
        except ValueError as erreur:
            print(f"Erreur : {document.as_posix()} : {erreur}", file=sys.stderr)
            code = 1
            continue
        if nouveau == ancien:
            print(f"{document.as_posix()} : bloc « {nom} » déjà à jour.")
            continue
        chemin.write_text(nouveau, encoding="utf-8", newline="\n")
        logger.info("Bloc %s réécrit dans %s", nom, chemin)
        print(f"{document.as_posix()} : bloc « {nom} » mis à jour.")
    manquantes = sans_docstring(inventaire)
    if manquantes:
        print(
            "Erreur : fichier(s) de tests sans docstring de module (écrire en tête du fichier "
            "une docstring dont la première phrase dit ce qu'il vérifie, puis relancer "
            f"{COMMANDE}) :\n" + "\n".join(f"  - {f}" for f in manquantes),
            file=sys.stderr,
        )
        code = 1
    return code


def main(argv: list[str] | None = None) -> int:
    """Sans option : totaux et tests mal rangés (code 1 s'il y en a) ; `--ecrire` : documents."""
    analyseur = argparse.ArgumentParser(
        prog="python -m tests.outils.classement",
        description="Classement des tests en unitaires et intégration, et nombres des documents.",
    )
    analyseur.add_argument(
        "racine",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="racine du dépôt (par défaut : ce dépôt)",
    )
    analyseur.add_argument(
        "--ecrire",
        action="store_true",
        help="mettre à jour les nombres et tableaux de tests/README.md, "
        "tests/unitaires/README.md et tests/integration/README.md",
    )
    options = analyseur.parse_args(argv)
    racine = options.racine.resolve()
    if options.ecrire:
        return ecrire(racine)

    classement = classer(racine)
    ranges = {i: v for i, v in classement.items() if cote_du_dossier(i) is not None}

    par_cote = Counter(v.cote for v in ranges.values())
    par_composant = Counter((composant(i), v.cote) for i, v in ranges.items())
    print(f"Fonctions de test rangées : {len(ranges)}")
    print(f"  unitaires       : {par_cote[UNITAIRE]}")
    print(f"  d'intégration   : {par_cote[INTEGRATION]}")
    print()
    print(f"{'composant':<14}{'unitaires':>11}{'intégration':>13}")
    for nom in sorted({c for c, _ in par_composant}):
        print(f"{nom:<14}{par_composant[nom, UNITAIRE]:>11}{par_composant[nom, INTEGRATION]:>13}")

    fautifs = mal_ranges(classement)
    print()
    if not fautifs:
        print("Aucun test mal rangé.")
        return 0
    print(f"{len(fautifs)} test(s) mal rangé(s) :")
    for identifiant in fautifs:
        print(f"  - {expliquer(identifiant, classement[identifiant])}")
    return 1


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8")
    sys.exit(main())
