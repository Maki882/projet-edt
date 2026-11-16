"""Catalogue des cas limites, produit à partir des tests eux-mêmes.

Un cas limite est un test qui porte le marqueur `cas_limite` (sur la fonction,
sur sa classe ou par `pytestmark` dans le module) et dont la docstring suit la
forme « Cas : … Risque : … ». Ce module lit les fichiers de `tests/unitaires/`
et `tests/integration/` sans les exécuter (module `ast`), extrait de chaque
docstring le cas et le risque, et produit le catalogue en Markdown : par
famille, par composant, par fichier, une ligne par fonction de test (une
fonction paramétrée compte une fois).

Le catalogue occupe, dans `docs/banque-de-tests.md`, la partie comprise entre
les marqueurs `DEBUT` et `FIN`. `tests/unitaires/socle/test_catalogue.py`
vérifie que cette partie est identique à ce que produit le module.

Usage, depuis la racine du dépôt :

    python -m tests.outils.catalogue            # affiche le catalogue
    python -m tests.outils.catalogue --ecrire   # met à jour docs/banque-de-tests.md

Limite connue : un marqueur posé dynamiquement (`request.applymarker`, hook de
collecte, `pytest.param(..., marks=...)`) n'est pas vu.
"""

from __future__ import annotations

import argparse
import ast
import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RACINE = Path(__file__).resolve().parents[2]
"""Racine du dépôt."""

DOCUMENT = Path("docs") / "banque-de-tests.md"
"""Document qui reçoit le catalogue, relatif à la racine du dépôt."""

COMMANDE = "python -m tests.outils.catalogue --ecrire"
"""Commande qui régénère le catalogue dans le document."""

DEBUT = (
    f"<!-- catalogue : début de la partie générée par `{COMMANDE}`, ne pas modifier à la main -->"
)
"""Marqueur d'ouverture de la partie générée."""

FIN = "<!-- catalogue : fin de la partie générée -->"
"""Marqueur de fermeture de la partie générée."""

FAMILLES: dict[str, str] = {"unitaires": "Tests unitaires", "integration": "Tests d'intégration"}
"""Dossier de `tests/` de chaque famille, et son titre dans le catalogue (dans cet ordre)."""

SANS_DOCSTRING = "*docstring « Cas : … Risque : … » absente ou incomplète*"
"""Texte affiché à la place d'un cas ou d'un risque introuvable."""

_CAS = re.compile(r"\bCas\s*:\s*")
_RISQUE = re.compile(r"\bRisque\s*:\s*")
_FIN_DE_PARAGRAPHE = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class CasLimite:
    """Un test marqué `cas_limite`, avec ce que dit sa docstring."""

    famille: str
    """Dossier de la famille (`unitaires` ou `integration`)."""
    composant: str
    """Premier sous-dossier de la famille (`socle`, `solveur`…)."""
    fichier: str
    """Chemin du fichier, relatif à la racine, séparateurs `/`."""
    nom: str
    """Nom de la fonction de test, préfixé de sa classe éventuelle (`Classe.test_x`)."""
    ligne: int
    """Ligne de la définition."""
    cas: str | None
    """Texte du « Cas », sur une ligne ; `None` s'il est absent ou vide."""
    risque: str | None
    """Texte du « Risque », sur une ligne ; `None` s'il est absent ou vide."""

    @property
    def identifiant(self) -> str:
        """Identifiant pytest (`fichier::nom`, `fichier::Classe::nom`)."""
        return f"{self.fichier}::{self.nom.replace('.', '::')}"

    @property
    def complet(self) -> bool:
        """Vrai si la docstring donne un cas et un risque."""
        return self.cas is not None and self.risque is not None


# --------------------------------------------------------------------------- #
# Lecture des tests
# --------------------------------------------------------------------------- #


def _est_marque_cas_limite(expression: ast.expr) -> bool:
    """Vrai pour `pytest.mark.cas_limite` ou `mark.cas_limite`, appelé ou non."""
    if isinstance(expression, ast.Call):
        expression = expression.func
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "cas_limite"
        and (
            (isinstance(expression.value, ast.Attribute) and expression.value.attr == "mark")
            or (isinstance(expression.value, ast.Name) and expression.value.id == "mark")
        )
    )


def _pytestmark_cas_limite(corps: list[ast.stmt]) -> bool:
    """Vrai si `pytestmark` (module ou classe) contient le marqueur, seul ou dans une liste."""
    for instruction in corps:
        if isinstance(instruction, ast.Assign):
            cibles, valeur = instruction.targets, instruction.value
        elif isinstance(instruction, ast.AnnAssign) and instruction.value is not None:
            cibles, valeur = [instruction.target], instruction.value
        else:
            continue
        if not any(isinstance(c, ast.Name) and c.id == "pytestmark" for c in cibles):
            continue
        elements = valeur.elts if isinstance(valeur, ast.List | ast.Tuple) else [valeur]
        if any(_est_marque_cas_limite(e) for e in elements):
            return True
    return False


def lire_cas_et_risque(docstring: str | None) -> tuple[str | None, str | None]:
    """Extrait le cas et le risque d'une docstring « Cas : … Risque : … ».

    Le cas va de « Cas : » au premier « Risque : » qui le suit ; le risque va
    jusqu'à la fin de son paragraphe. Les retours à la ligne et les espaces
    multiples sont réduits à une espace. Une partie absente ou vide vaut `None`.
    """
    if not docstring:
        return None, None
    debut_cas = _CAS.search(docstring)
    debut_risque = _RISQUE.search(docstring, debut_cas.end() if debut_cas else 0)
    cas = risque = None
    if debut_cas:
        fin_cas = debut_risque.start() if debut_risque else len(docstring)
        cas = " ".join(docstring[debut_cas.end() : fin_cas].split()) or None
    if debut_risque:
        suite = docstring[debut_risque.end() :]
        risque = " ".join(_FIN_DE_PARAGRAPHE.split(suite, maxsplit=1)[0].split()) or None
    return cas, risque


def _cas_du_fichier(racine: Path, fichier: Path, famille: str) -> list[CasLimite]:
    """Tests `cas_limite` d'un fichier, dans l'ordre du fichier."""
    chemin = fichier.relative_to(racine).as_posix()
    try:
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=chemin)
    except SyntaxError as erreur:
        raise SyntaxError(f"{chemin} : fichier de tests illisible ({erreur.msg})") from erreur
    parties = fichier.relative_to(racine / "tests" / famille).parts
    composant = parties[0] if len(parties) > 1 else "(racine)"
    tout_le_module = _pytestmark_cas_limite(arbre.body)

    def fonctions(
        corps: list[ast.stmt], prefixe: str, herite: bool
    ) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, bool]]:
        trouvees = []
        for noeud in corps:
            if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef):
                if noeud.name.startswith("test"):
                    marque = herite or any(_est_marque_cas_limite(d) for d in noeud.decorator_list)
                    trouvees.append((prefixe + noeud.name, noeud, marque))
            elif isinstance(noeud, ast.ClassDef) and noeud.name.startswith("Test"):
                marque_classe = (
                    herite
                    or any(_est_marque_cas_limite(d) for d in noeud.decorator_list)
                    or _pytestmark_cas_limite(noeud.body)
                )
                trouvees += fonctions(noeud.body, f"{prefixe}{noeud.name}.", marque_classe)
        return trouvees

    resultat = []
    for nom, noeud, marque in fonctions(arbre.body, "", tout_le_module):
        if not marque:
            continue
        cas, risque = lire_cas_et_risque(ast.get_docstring(noeud))
        resultat.append(CasLimite(famille, composant, chemin, nom, noeud.lineno, cas, risque))
    return resultat


def analyser(racine: Path = RACINE) -> list[CasLimite]:
    """Tous les tests `cas_limite` de `tests/unitaires/` et `tests/integration/`.

    Ordre : famille (unitaires puis intégration), composant, fichier, puis ordre
    du fichier. Un dossier de famille absent ne donne aucun cas.
    """
    trouves: list[CasLimite] = []
    for famille in FAMILLES:
        dossier = racine / "tests" / famille
        if not dossier.is_dir():
            logger.warning("Dossier de tests absent : %s", dossier)
            continue
        fichiers = sorted({*dossier.rglob("test_*.py"), *dossier.rglob("*_test.py")})
        for fichier in fichiers:
            trouves += _cas_du_fichier(racine, fichier, famille)
    trouves.sort(key=lambda c: (list(FAMILLES).index(c.famille), c.composant, c.fichier))
    logger.debug("%d cas limites lus sous %s", len(trouves), racine / "tests")
    return trouves


def incomplets(cas: list[CasLimite]) -> list[CasLimite]:
    """Les cas dont la docstring ne donne pas de « Cas » ou pas de « Risque »."""
    return [c for c in cas if not c.complet]


# --------------------------------------------------------------------------- #
# Production du Markdown
# --------------------------------------------------------------------------- #


def nom_lisible(nom: str) -> str:
    """`test_salle_trop_petite` donne « Salle trop petite » ; la classe éventuelle est ôtée."""
    court = nom.rsplit(".", 1)[-1]
    court = court.removeprefix("test_").removeprefix("test")
    mots = court.replace("_", " ").strip() or nom
    return mots[0].upper() + mots[1:]


def _cellule(texte: str | None) -> str:
    """Texte prêt pour une cellule de tableau Markdown."""
    if texte is None:
        return SANS_DOCSTRING
    return texte.replace("|", "\\|")


def _pluriel(nombre: int) -> str:
    """« 1 cas », « 12 cas » : le mot est invariable, le nombre est groupé par milliers."""
    return f"{nombre:,}".replace(",", " ") + " cas"


def produire(cas: list[CasLimite]) -> str:
    """Le catalogue en Markdown (sans les marqueurs), terminé par un saut de ligne."""
    lignes: list[str] = []
    par_famille = Counter(c.famille for c in cas)
    par_composant = Counter((c.famille, c.composant) for c in cas)
    composants = sorted({c.composant for c in cas})

    lignes.append(
        f"Le catalogue compte {_pluriel(len(cas))} : "
        + " et ".join(
            f"{_pluriel(par_famille[f])} en {titre.lower()}" for f, titre in FAMILLES.items()
        )
        + "."
    )
    lignes += ["", "| Composant | " + " | ".join(FAMILLES.values()) + " |"]
    lignes.append("|---|" + "---:|" * len(FAMILLES))
    for composant in composants:
        valeurs = " | ".join(str(par_composant[f, composant]) for f in FAMILLES)
        lignes.append(f"| `{composant}` | {valeurs} |")
    total = " | ".join(f"**{par_famille[f]}**" for f in FAMILLES)
    lignes.append(f"| **Total** | {total} |")

    for famille, titre in FAMILLES.items():
        de_la_famille = [c for c in cas if c.famille == famille]
        if not de_la_famille:
            continue
        lignes += ["", f"### {titre} ({_pluriel(len(de_la_famille))})"]
        for composant in sorted({c.composant for c in de_la_famille}):
            du_composant = [c for c in de_la_famille if c.composant == composant]
            lignes += ["", f"#### `{composant}` ({_pluriel(len(du_composant))})"]
            for fichier in sorted({c.fichier for c in du_composant}):
                lien = "../" + fichier
                lignes += ["", f"[`{fichier}`]({lien})", "", "| Test | Cas | Risque |"]
                lignes.append("|---|---|---|")
                for c in (c for c in du_composant if c.fichier == fichier):
                    lignes.append(
                        f"| {nom_lisible(c.nom)} | {_cellule(c.cas)} | {_cellule(c.risque)} |"
                    )
    return "\n".join(lignes) + "\n"


def partie_generee(document: str) -> str | None:
    """Texte compris entre les marqueurs (sans eux), ou `None` s'ils manquent."""
    debut, fin = document.find(DEBUT), document.find(FIN)
    if debut < 0 or fin < debut:
        return None
    return document[debut + len(DEBUT) : fin].strip("\n") + "\n"


def remplacer(document: str, catalogue: str) -> str:
    """Le document avec sa partie générée remplacée par `catalogue`.

    Lève `ValueError` si les marqueurs manquent ou sont dans le désordre.
    """
    debut, fin = document.find(DEBUT), document.find(FIN)
    if debut < 0 or fin < debut:
        raise ValueError(
            f"marqueurs du catalogue introuvables dans {DOCUMENT.as_posix()} : "
            f"ajouter les lignes « {DEBUT} » et « {FIN} » à l'endroit voulu"
        )
    return f"{document[: debut + len(DEBUT)]}\n\n{catalogue}\n{document[fin:]}"


def main(argv: list[str] | None = None) -> int:
    """Affiche le catalogue, ou l'écrit dans le document avec `--ecrire`."""
    analyseur = argparse.ArgumentParser(
        prog="python -m tests.outils.catalogue",
        description="Catalogue des cas limites, lu dans les docstrings des tests.",
    )
    analyseur.add_argument(
        "--ecrire",
        action="store_true",
        help=f"mettre à jour la partie générée de {DOCUMENT.as_posix()}",
    )
    analyseur.add_argument(
        "--racine", type=Path, default=RACINE, help="racine du dépôt (par défaut : ce dépôt)"
    )
    options = analyseur.parse_args(argv)

    cas = analyser(options.racine)
    catalogue = produire(cas)
    for c in incomplets(cas):
        print(f"docstring sans « Cas » ou sans « Risque » : {c.identifiant}", file=sys.stderr)

    if not options.ecrire:
        print(catalogue, end="")
        return 0

    chemin = options.racine / DOCUMENT
    ancien = chemin.read_text(encoding="utf-8")
    try:
        nouveau = remplacer(ancien, catalogue)
    except ValueError as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 1
    if nouveau == ancien:
        print(f"{DOCUMENT.as_posix()} : catalogue déjà à jour ({_pluriel(len(cas))}).")
        return 0
    chemin.write_text(nouveau, encoding="utf-8", newline="\n")
    logger.info("Catalogue écrit dans %s (%d cas)", chemin, len(cas))
    print(f"{DOCUMENT.as_posix()} : catalogue mis à jour ({_pluriel(len(cas))}).")
    return 0


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8")
    sys.exit(main())
