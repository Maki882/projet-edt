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

Usage, depuis la racine du dépôt :

    python -m tests.outils.classement

Le programme affiche le nombre de tests par côté et par composant, puis la
liste des tests mal rangés ; il sort avec le code 1 s'il y en a.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

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
        if importe.nom is None:  # module importé : on suit ses fonctions citées (`module.aide()`)
            atteints: set[str] = set()
            for n in noms & cible.fonctions.keys():
                atteints |= self.noms_atteints(cible, cible.fonctions[n], n, pile)
            return atteints
        if importe.nom in cible.fonctions:
            return self.noms_atteints(cible, cible.fonctions[importe.nom], importe.nom, pile)
        if importe.nom in cible.fixtures:
            return self.noms_atteints(cible, cible.fixtures[importe.nom], importe.nom, pile)
        if importe.nom in cible.imports:  # ré-export : on suit l'import du module cible
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
            for noeud in ast.walk(module.arbre):
                if isinstance(
                    noeud, ast.FunctionDef | ast.AsyncFunctionDef
                ) and noeud.name.startswith("test_"):
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


def main(argv: list[str]) -> int:
    """Affiche les totaux par côté et par composant, puis les tests mal rangés (code 1 s'il y en a)."""
    racine = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parents[2]
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:]))
