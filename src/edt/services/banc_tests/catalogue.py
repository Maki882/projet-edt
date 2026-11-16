"""Catalogue des tests du projet, lu dans les fichiers de `tests/` sans les exécuter.

Le catalogue range chaque fonction de test par famille (`unitaires`,
`integration`), composant (premier sous-dossier de la famille) et fichier. Il
est lu par analyse du code (module `ast`) : aucun test n'est importé, et le
paquet `tests` peut être absent de l'installation (le catalogue est alors vide
et l'écran le dit).

Racine du dépôt (`trouver_racine`), dans cet ordre :

1. la variable `EDT_TESTS_RACINE` ;
2. le dossier courant, s'il contient `tests/unitaires` et `pyproject.toml` ;
3. la racine déduite de l'emplacement du code source (`src/edt/...`).

Le catalogue est gardé en mémoire tant que les fichiers de tests ne changent
pas (mêmes noms, mêmes dates de modification, mêmes tailles).

Une cible (`Cible`) est ce qui est demandé à lancer : toute une famille, un
composant, un fichier ou un test. Elle est toujours cherchée dans le
catalogue (`resoudre_cible`) ; seuls les chemins du catalogue vont à pytest,
jamais un texte reçu de l'utilisateur.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from edt.erreurs import ErreurIntrouvable

journal = logging.getLogger(__name__)

VARIABLE_RACINE = "EDT_TESTS_RACINE"
"""Variable d'environnement qui désigne la racine du dépôt (dossier qui contient `tests/`)."""

FAMILLES: dict[str, str] = {"unitaires": "Tests unitaires", "integration": "Tests d'intégration"}
"""Dossier de `tests/` de chaque famille, et son titre."""

DESCRIPTIONS_COMPOSANTS: dict[str, str] = {
    "socle": "domaine, base, configuration, journaux, ligne de commande, serveur",
    "solveur": "préparation du problème, modèle CP-SAT, moteur, séances datées",
    "verification": "vérificateur indépendant : règles, indicateurs, séances datées",
    "echange": "import et export : gabarit, XLSX, CSV, iCal",
    "synthese": "générateur d'établissements fictifs et emploi du temps de référence",
    "web_donnees": "écrans de l'espace « Données »",
    "web_edt": "écrans Générer, Emploi du temps, Points à traiter, accueil",
    "exploitation": "scripts de déploiement, systemd, nginx, sauvegarde",
    "parcours": "six scénarios de bout en bout, comme un utilisateur",
    "banc_tests": "lancement des tests depuis l'application (ces deux onglets)",
    "assistant": "assistant en langage courant : outils, limites, conversations, propositions",
}
"""Ce que vérifie chaque composant, en quelques mots (affiché sous son nom)."""

CIBLE_FAMILLE = "famille"
"""Valeur de cible qui désigne toute la famille."""

_FIN_DE_PHRASE = re.compile(r"(?<=[.?!])\s")


@dataclass(frozen=True)
class TestCatalogue:
    """Une fonction de test (ses cas paramétrés comptent pour un seul test).

    - `fonction` : nom de la fonction, préfixé de sa classe éventuelle (`Classe.test_x`) ;
    - `nom` : nom lisible (« Salle trop petite » pour `test_salle_trop_petite`) ;
    - `resume` : première phrase de la docstring, ou chaîne vide ;
    - `perf` : test de charge (marqueur `perf`), exclu des lancements par groupe.
    """

    __test__ = False  # nom commençant par « Test » : pytest ne doit pas le collecter

    famille: str
    composant: str
    fichier: str
    fonction: str
    nom: str
    resume: str
    perf: bool
    ligne: int

    @property
    def cle(self) -> str:
        """Identifiant pytest sans paramètres (`tests/…/test_x.py::test_f`, `…::Classe::test_f`)."""
        return f"{self.fichier}::{self.fonction.replace('.', '::')}"


@dataclass(frozen=True)
class FichierCatalogue:
    """Un fichier de tests et ses fonctions ; `erreur` dit pourquoi il n'a pas pu être lu."""

    famille: str
    composant: str
    chemin: str
    tests: tuple[TestCatalogue, ...]
    erreur: str | None = None

    @property
    def nom(self) -> str:
        """Nom du fichier (`test_base.py`)."""
        return self.chemin.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Catalogue:
    """Tous les tests lus sous `racine/tests` ; `racine` vaut `None` si aucun dépôt n'est trouvé.

    `absence` explique pourquoi le catalogue est vide (dossier introuvable…).
    """

    racine: Path | None
    fichiers: tuple[FichierCatalogue, ...] = ()
    absence: str | None = None
    _par_cle: dict[str, TestCatalogue] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        for fichier in self.fichiers:
            for test in fichier.tests:
                self._par_cle[test.cle] = test

    def fichiers_de(self, famille: str | None = None) -> list[FichierCatalogue]:
        """Les fichiers (d'une famille), dans l'ordre des chemins."""
        return [f for f in self.fichiers if famille is None or f.famille == famille]

    def tests(self, famille: str | None = None) -> list[TestCatalogue]:
        """Les tests (d'une famille), dans l'ordre des fichiers."""
        return [t for f in self.fichiers_de(famille) for t in f.tests]

    def composants(self, famille: str) -> list[str]:
        """Les composants d'une famille, par ordre alphabétique."""
        return sorted({f.composant for f in self.fichiers_de(famille)})

    def test(self, cle: str) -> TestCatalogue | None:
        """Le test de clé `cle`, ou `None`."""
        return self._par_cle.get(cle)


@dataclass(frozen=True)
class Cible:
    """Ce qu'une exécution lance, toujours tiré du catalogue.

    - `cle` : valeur de la cible (`famille`, `composant:socle`, `fichier:<chemin>`,
      `test:<clé>`), reprise par le formulaire ;
    - `famille` : famille visée, `None` pour les deux (ligne de commande) ;
    - `libelle` : texte pour l'écran (« le fichier test_base.py (socle) ») ;
    - `chemins` : arguments donnés à pytest, relatifs à la racine ;
    - `tests` : clés des tests du catalogue couverts (sans les tests de charge
      pour un lancement par groupe) ;
    - `groupes` : groupes entièrement couverts (`famille:…`, `composant:…`,
      `fichier:…`), dont la date de dernière exécution complète est notée si
      l'exécution va à son terme ;
    - `exclure_perf` : lancement par groupe, sans les tests de charge ;
    - `charge` : un test de charge lancé seul (refusé depuis le site).
    """

    cle: str
    famille: str | None
    libelle: str
    chemins: tuple[str, ...]
    tests: tuple[str, ...]
    groupes: tuple[str, ...]
    exclure_perf: bool
    charge: bool = False


# --------------------------------------------------------------------------- #
# Racine du dépôt
# --------------------------------------------------------------------------- #


def _est_depot(dossier: Path) -> bool:
    return (dossier / "tests" / "unitaires").is_dir() and (dossier / "pyproject.toml").is_file()


def trouver_racine() -> tuple[Path | None, str | None]:
    """Racine du dépôt, ou `None` et la phrase qui explique pourquoi elle manque."""
    variable = os.environ.get(VARIABLE_RACINE, "").strip()
    if variable:
        racine = Path(variable).resolve()
        if (racine / "tests").is_dir():
            return racine, None
        return None, (
            f"Le dossier désigné par {VARIABLE_RACINE} ({racine}) ne contient pas de dossier "
            "« tests » : corriger la variable ou la retirer."
        )
    courant = Path.cwd()
    if _est_depot(courant):
        return courant.resolve(), None
    depuis_le_code = Path(__file__).resolve().parents[4]  # src/edt/services/banc_tests/
    if _est_depot(depuis_le_code):
        return depuis_le_code, None
    return None, (
        "Les fichiers de tests ne sont pas présents dans cette installation (dossier « tests » "
        f"introuvable). Lancer le serveur depuis la racine du dépôt, ou régler {VARIABLE_RACINE}."
    )


# --------------------------------------------------------------------------- #
# Lecture des fichiers
# --------------------------------------------------------------------------- #


def _est_marque(expression: ast.expr, marqueur: str) -> bool:
    """Vrai pour `pytest.mark.<marqueur>` ou `mark.<marqueur>`, appelé ou non."""
    if isinstance(expression, ast.Call):
        expression = expression.func
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == marqueur
        and (
            (isinstance(expression.value, ast.Attribute) and expression.value.attr == "mark")
            or (isinstance(expression.value, ast.Name) and expression.value.id == "mark")
        )
    )


def _pytestmark(corps: list[ast.stmt], marqueur: str) -> bool:
    """Vrai si `pytestmark` (module ou classe) porte le marqueur, seul ou dans une liste."""
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
        if any(_est_marque(e, marqueur) for e in elements):
            return True
    return False


def nom_lisible(fonction: str) -> str:
    """`test_salle_trop_petite` donne « Salle trop petite » ; la classe éventuelle est ôtée."""
    court = fonction.rsplit(".", 1)[-1].removeprefix("test_").removeprefix("test")
    mots = court.replace("_", " ").strip() or fonction
    return mots[0].upper() + mots[1:]


def premiere_phrase(docstring: str | None) -> str:
    """Première phrase du premier paragraphe d'une docstring, sur une ligne, sans accents graves."""
    if not docstring:
        return ""
    paragraphe = " ".join(docstring.strip().split("\n\n", 1)[0].replace("`", "").split())
    return _FIN_DE_PHRASE.split(paragraphe, maxsplit=1)[0]


def _lire_fichier(racine: Path, fichier: Path, famille: str) -> FichierCatalogue:
    chemin = fichier.relative_to(racine).as_posix()
    parties = fichier.relative_to(racine / "tests" / famille).parts
    composant = parties[0] if len(parties) > 1 else "(racine)"
    try:
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=chemin)
    except (SyntaxError, UnicodeDecodeError, OSError) as erreur:
        journal.warning("Fichier de tests illisible, ignoré du catalogue : %s (%s)", chemin, erreur)
        return FichierCatalogue(famille, composant, chemin, (), f"Fichier illisible : {erreur}")

    tests: list[TestCatalogue] = []

    def parcourir(corps: list[ast.stmt], prefixe: str, perf_herite: bool) -> None:
        for noeud in corps:
            if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef):
                if noeud.name.startswith("test"):
                    perf = perf_herite or any(_est_marque(d, "perf") for d in noeud.decorator_list)
                    tests.append(
                        TestCatalogue(
                            famille=famille,
                            composant=composant,
                            fichier=chemin,
                            fonction=prefixe + noeud.name,
                            nom=nom_lisible(noeud.name),
                            resume=premiere_phrase(ast.get_docstring(noeud)),
                            perf=perf,
                            ligne=noeud.lineno,
                        )
                    )
            elif isinstance(noeud, ast.ClassDef) and noeud.name.startswith("Test"):
                perf_classe = (
                    perf_herite
                    or any(_est_marque(d, "perf") for d in noeud.decorator_list)
                    or _pytestmark(noeud.body, "perf")
                )
                parcourir(noeud.body, f"{prefixe}{noeud.name}.", perf_classe)

    parcourir(arbre.body, "", _pytestmark(arbre.body, "perf"))
    return FichierCatalogue(famille, composant, chemin, tuple(tests))


def _fichiers_de_tests(racine: Path) -> list[tuple[str, Path]]:
    trouves: list[tuple[str, Path]] = []
    for famille in FAMILLES:
        dossier = racine / "tests" / famille
        if dossier.is_dir():
            fichiers = {*dossier.rglob("test_*.py"), *dossier.rglob("*_test.py")}
            trouves += [(famille, f) for f in sorted(fichiers)]
    return trouves


def _empreinte(fichiers: Iterable[tuple[str, Path]]) -> tuple:
    empreinte = []
    for _, fichier in fichiers:
        try:
            etat = fichier.stat()
        except OSError:  # supprimé entre la liste et la lecture : la lecture le dira
            empreinte.append((str(fichier), None, None))
            continue
        empreinte.append((str(fichier), etat.st_mtime_ns, etat.st_size))
    return tuple(empreinte)


_memoire_verrou = threading.Lock()
_memoire: dict[Path, tuple[tuple, Catalogue]] = {}


def lire_catalogue(racine: Path | None = None) -> Catalogue:
    """Le catalogue des tests de `racine` (par défaut `trouver_racine()`), gardé en mémoire.

    Ne lève pas : un dépôt introuvable donne un catalogue vide avec sa raison
    (`absence`) ; un fichier illisible est listé avec son erreur.
    """
    if racine is None:
        racine, absence = trouver_racine()
        if racine is None:
            journal.info("Catalogue des tests vide : %s", absence)
            return Catalogue(None, (), absence)
    fichiers = _fichiers_de_tests(racine)
    if not fichiers:
        absence = (
            f"Aucun fichier de tests trouvé sous {racine / 'tests'} (dossiers « unitaires » et "
            "« integration »)."
        )
        journal.info("Catalogue des tests vide : %s", absence)
        return Catalogue(racine, (), absence)
    empreinte = _empreinte(fichiers)
    with _memoire_verrou:
        memorise = _memoire.get(racine)
        if memorise is not None and memorise[0] == empreinte:
            return memorise[1]
    catalogue = Catalogue(racine, tuple(_lire_fichier(racine, f, fam) for fam, f in fichiers))
    journal.info(
        "Catalogue des tests lu sous %s : %d fichiers, %d tests",
        racine,
        len(catalogue.fichiers),
        len(catalogue.tests()),
    )
    with _memoire_verrou:
        _memoire[racine] = (empreinte, catalogue)
    return catalogue


# --------------------------------------------------------------------------- #
# Cibles
# --------------------------------------------------------------------------- #


def cle_groupe_famille(famille: str) -> str:
    """Clé du groupe « toute la famille »."""
    return f"famille:{famille}"


def cle_groupe_composant(famille: str, composant: str) -> str:
    """Clé du groupe « un composant d'une famille »."""
    return f"composant:{famille}/{composant}"


def cle_groupe_fichier(chemin: str) -> str:
    """Clé du groupe « un fichier »."""
    return f"fichier:{chemin}"


def _sans_perf(fichiers: Iterable[FichierCatalogue]) -> tuple[str, ...]:
    return tuple(t.cle for f in fichiers for t in f.tests if not t.perf)


def resoudre_cible(catalogue: Catalogue, cible: str, famille: str | None) -> Cible:
    """La cible `cible` cherchée dans le catalogue ; `ErreurIntrouvable` si elle n'y est pas.

    `famille` restreint la recherche (écran d'une famille) ; `None` (ligne de
    commande) accepte les deux familles : `famille` les lance toutes deux, un
    composant présent des deux côtés est lancé des deux côtés.
    """
    if famille is not None and famille not in FAMILLES:
        raise ErreurIntrouvable(f"Famille de tests inconnue : « {famille} ».")
    if catalogue.racine is None or not catalogue.fichiers:
        raise ErreurIntrouvable(catalogue.absence or "Aucun test n'est disponible.")
    familles = [famille] if famille is not None else list(FAMILLES)
    texte = (cible or "").strip()

    if texte == CIBLE_FAMILLE:
        fichiers = [f for fam in familles for f in catalogue.fichiers_de(fam)]
        groupes = [cle_groupe_famille(fam) for fam in familles]
        groupes += [
            cle_groupe_composant(fam, c) for fam in familles for c in catalogue.composants(fam)
        ]
        groupes += [cle_groupe_fichier(f.chemin) for f in fichiers]
        libelle = (
            f"tous les {FAMILLES[famille].lower()}"
            if famille
            else "tous les tests unitaires et d'intégration"
        )
        return Cible(
            CIBLE_FAMILLE,
            famille,
            libelle,
            tuple(f"tests/{fam}" for fam in familles),
            _sans_perf(fichiers),
            tuple(groupes),
            True,
        )

    if texte.startswith("composant:"):
        nom = texte.removeprefix("composant:")
        couverts = [fam for fam in familles if nom in catalogue.composants(fam)]
        if not couverts or nom == "(racine)":
            raise ErreurIntrouvable(f"Composant de tests inconnu : « {nom[:100]} ».")
        fichiers = [f for fam in couverts for f in catalogue.fichiers_de(fam) if f.composant == nom]
        groupes = [cle_groupe_composant(fam, nom) for fam in couverts]
        groupes += [cle_groupe_fichier(f.chemin) for f in fichiers]
        return Cible(
            texte,
            famille,
            f"le composant {nom}",
            tuple(f"tests/{fam}/{nom}" for fam in couverts),
            _sans_perf(fichiers),
            tuple(groupes),
            True,
        )

    if texte.startswith("fichier:"):
        chemin = texte.removeprefix("fichier:")
        fichier = next((f for f in catalogue.fichiers_de(famille) if f.chemin == chemin), None)
        if fichier is None:
            raise ErreurIntrouvable(f"Fichier de tests inconnu : « {chemin[:200]} ».")
        return Cible(
            texte,
            fichier.famille,
            f"le fichier {fichier.nom} ({fichier.composant})",
            (fichier.chemin,),
            _sans_perf([fichier]),
            (cle_groupe_fichier(fichier.chemin),),
            True,
        )

    if texte.startswith("test:"):
        cle = texte.removeprefix("test:")
        test = catalogue.test(cle)
        if test is None or test.famille not in familles:
            raise ErreurIntrouvable(f"Test inconnu : « {cle[:200]} ».")
        return Cible(
            texte,
            test.famille,
            f"le test « {test.nom} »",
            (test.cle,),
            (test.cle,),
            (),
            False,
            charge=test.perf,
        )

    raise ErreurIntrouvable(
        f"Demande de lancement illisible : « {texte[:100]} ». Choisir un test, un fichier, un "
        "composant ou toute la famille dans la liste."
    )
