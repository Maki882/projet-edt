"""Cahier de recette (livret 4) : parties générées à jour, fiches valides, couverture, outil.

Contrôles du dépôt réel :

1. les parties générées du livret sont identiques à ce que produit `tests.outils.recette` ;
2. chaque test cité par une fiche existe et se trouve dans `tests/integration/` ;
3. chaque fonctionnalité réalisée du livret 1 a une fiche, ou est déclarée sans fiche ;
4. chaque fiche a sa ligne au tableau de recette, avec un lien vers sa fiche détaillée ;
5. chaque fichier de tests d'intégration figure à l'annexe B du livret ;
6. chaque test d'une campagne existe encore dans le dépôt, ou le livret signale l'écart.

Puis l'outil lui-même, éprouvé sur un dépôt miniature écrit dans `tmp_path` :
enregistrement d'une campagne, verdicts, erreurs claires.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from tests.outils.recette import (
    BLOCS,
    CAMPAGNES,
    COMMANDE,
    CONFORME,
    FICHES,
    LIVRET,
    LIVRET_FONCTIONNEL,
    NON_CONFORME,
    NON_EXECUTEE,
    ErreurRecette,
    Resultat,
    ancre,
    blocs_perimes,
    citations_invalides,
    debut_de_bloc,
    fin_de_bloc,
    fonctionnalites_sans_fiche,
    lien_de_fiche,
    lire,
    main,
    partie_generee,
    problemes,
    resultats_disparus,
    titre_de_fiche,
    verdict,
)

RACINE = Path(__file__).resolve().parents[3]
"""Racine du dépôt."""


@pytest.fixture(scope="module")
def recette():
    """Les sources du cahier, lues une fois pour les contrôles du dépôt réel."""
    return lire(RACINE)


@pytest.fixture(scope="module")
def livret() -> str:
    """Texte du livret 4."""
    return (RACINE / LIVRET).read_text(encoding="utf-8")


# --- 1 à 6. Le dépôt réel ------------------------------------------------------


def test_les_parties_generees_du_livret_sont_a_jour(recette, livret):
    """Chaque partie générée du livret 4 est celle que produit l'outil."""
    perimes = blocs_perimes(livret, recette)
    if perimes:
        pytest.fail(
            f"{LIVRET.as_posix()} n'est plus à jour ; lancer, depuis la racine du dépôt : "
            f"{COMMANDE}\n" + "\n".join(perimes),
            pytrace=False,
        )


def test_chaque_test_cite_existe_dans_les_tests_d_integration(recette):
    """Un test renommé, supprimé ou hors de `tests/integration/` est signalé avec sa fiche."""
    assert len(recette.fiches) > 30, (
        f"seulement {len(recette.fiches)} fiches lues : lecture cassée ?"
    )
    fautes = citations_invalides(recette)
    assert not fautes, f"{FICHES.as_posix()} : test(s) introuvable(s) :\n" + "\n".join(
        f"  - {f}" for f in fautes
    )


def test_chaque_fonctionnalite_realisee_a_une_fiche(recette):
    """Les fonctionnalités réalisées sont lues dans le livret 1 lui-même (O1 à O11, U1 à U13, P1 à P4)."""
    realisees = [f.code for f in recette.realisees]
    assert len(realisees) >= 28, (
        f"{LIVRET_FONCTIONNEL.as_posix()} : seulement {len(realisees)} fonctionnalités réalisées "
        "lues : tableau du livret 1 changé ?"
    )
    fautes = fonctionnalites_sans_fiche(recette)
    assert not fautes, "\n".join(f"  - {f}" for f in fautes)


def test_chaque_fiche_a_sa_ligne_au_tableau_de_recette_et_son_lien(recette, livret):
    """Le tableau de la section 4 a une ligne par fiche, dont le lien mène à la fiche détaillée."""
    tableau = partie_generee(livret, "recette-tableau")
    fiches = partie_generee(livret, "recette-fiches")
    assert tableau is not None and fiches is not None, "marqueurs du tableau ou des fiches absents"
    fautes = []
    for fiche in recette.fiches:
        titre = titre_de_fiche(fiche)
        if f"| {lien_de_fiche(fiche)} |" not in tableau or f"(#{ancre(titre)})" not in tableau:
            fautes.append(f"{fiche.id} : ligne absente du tableau de recette")
        if f"#### {titre}\n" not in fiches:
            fautes.append(f"{fiche.id} : fiche détaillée absente de l'annexe A")
    assert not fautes, f"lancer {COMMANDE} :\n" + "\n".join(f"  - {f}" for f in fautes)


def test_chaque_fichier_de_tests_d_integration_figure_a_l_annexe_b(recette, livret):
    """L'annexe B du livret nomme chaque fichier `test_*.py` de `tests/integration/`."""
    annexe = partie_generee(livret, "recette-resultats-par-fichier")
    assert annexe is not None, "marqueurs de l'annexe B absents du livret"
    absents = [
        f.chemin
        for f in recette.fichiers.values()
        if f"| **{f.chemin.rsplit('/', 1)[-1]}** :" not in annexe
    ]
    assert not absents, f"fichier(s) absent(s) de l'annexe B ; lancer {COMMANDE} :\n" + "\n".join(
        f"  - {a}" for a in absents
    )


def test_chaque_test_des_campagnes_existe_encore_ou_est_signale(recette, livret):
    """Un résultat de campagne sur un test disparu doit être signalé dans la synthèse du livret."""
    assert recette.campagnes, (
        f"aucune campagne dans {CAMPAGNES.as_posix()} : enregistrer une campagne par "
        "python -m tests.outils.recette --campagne <resultats.json> --nom …"
    )
    synthese = partie_generee(livret, "recette-synthese") or ""
    non_signales = [
        f"{nom} : {test}"
        for nom, tests in resultats_disparus(recette).items()
        for test in tests
        if test.removeprefix("tests/integration/") not in synthese
    ]
    assert not non_signales, (
        "résultat(s) de campagne sur un test qui n'existe plus, non signalé(s) dans le livret ; "
        f"enregistrer de nouveau la campagne, ou lancer {COMMANDE} :\n"
        + "\n".join(f"  - {x}" for x in non_signales)
    )


# --- 7. L'outil, sur un dépôt miniature ----------------------------------------


def _ecrire(racine: Path, chemin: str | Path, texte: str) -> Path:
    fichier = racine / chemin
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(textwrap.dedent(texte), encoding="utf-8")
    return fichier


LIVRET_1 = """
| N° | Fonctionnalité | Ce que l'utilisateur peut faire | État |
|---|---|---|---|
| O1 | Décrire l'établissement | Créer. | Réalisée |
| U1 | Vérifier avant | Voir. | Réalisée |
| P5 | Réaménager | Plus tard. | Prévue |

#### Cas 1. Préparer un semestre
"""

FICHES_MINI = """
[[fiche]]
id = "REC-O1-1"
titre = "Créer une salle"
fonctionnalites = ["O1"]
cas = [1]
objectif = "Montrer."
depart = "Base vide."
etapes = ["Ouvrir."]
attendu = ["« Salle créée »."]
tests = ["tests/integration/alpha/test_un.py::test_a", "tests/integration/alpha/test_un.py::test_b"]

[[fiche]]
id = "REC-U1-1"
titre = "Vérifier"
fonctionnalites = ["U1"]
objectif = "Montrer."
depart = "Base vide."
etapes = ["Ouvrir."]
attendu = ["Rien."]
tests = []

[fiche.essai]
date = 2026-11-23
environnement = "Navigateur."
constat = "Vu."
verdict = "Conforme"
"""


@pytest.fixture
def depot(tmp_path: Path) -> Path:
    """Dépôt miniature : deux tests d'intégration, un livret 1, deux fiches, un livret 4 vide."""
    _ecrire(
        tmp_path,
        "tests/integration/alpha/test_un.py",
        '''
        """Vérifie alpha."""
        import pytest

        def test_a():
            pass

        @pytest.mark.perf
        def test_b():
            pass

        class TestBeta:
            def test_m(self):
                pass
        ''',
    )
    _ecrire(tmp_path, "tests/integration/alpha/__init__.py", '"""Composant alpha."""\n')
    _ecrire(tmp_path, LIVRET_FONCTIONNEL, LIVRET_1)
    _ecrire(tmp_path, FICHES, FICHES_MINI)
    _ecrire(
        tmp_path,
        LIVRET,
        "# Livret\n\n" + "".join(f"{debut_de_bloc(n)}\n{fin_de_bloc(n)}\n\n" for n in BLOCS),
    )
    return tmp_path


DEBUT, FIN = "2026-11-23T10:00:00", "2026-11-23T10:05:00"
"""Bornes de l'exécution des fichiers de résultats du dépôt miniature."""


def _resultats(tmp_path: Path, tests: dict, famille: str = "integration") -> Path:
    """Fichier de résultats ; son bilan compte les tests d'intégration datés de l'exécution."""
    executes = sum(
        cle.startswith("tests/integration/") and DEBUT <= r["le"] <= FIN for cle, r in tests.items()
    )
    contenu = {
        "version": 1,
        "tests": tests,
        "groupes": {},
        "derniere": {
            "libelle": "x",
            "famille": famille,
            "debut": DEBUT,
            "fin": FIN,
            "issue": "terminee",
            "message": "",
            "nb_tests": executes,
        },
    }
    fichier = tmp_path / "resultats.json"
    fichier.write_text(json.dumps(contenu), encoding="utf-8")
    return fichier


def _resultat(etat: str, le: str = "2026-11-23T10:01:00", raison: str | None = None) -> dict:
    return {
        "etat": etat,
        "duree_s": 1.5,
        "le": le,
        "raison": raison,
        "detail": "C:\\Users\\x\\f.py",
    }


def test_enregistrer_une_campagne_ecarte_les_vieux_resultats_et_les_chemins(depot, capsys):
    fichier = _resultats(
        depot,
        {
            "tests/integration/alpha/test_un.py::test_a": _resultat(
                "ignore", raison="absent de C:\\Users\\moi\\outil"
            ),
            "tests/integration/alpha/test_un.py::test_vieux": _resultat(
                "reussi", "2026-11-16T10:00:00"
            ),
            "tests/unitaires/beta/test_x.py::test_c": _resultat("reussi"),
        },
    )
    options = [
        "--nom",
        "Poste",
        "--systeme",
        "Windows 11",
        "--python",
        "3.11",
        "--version",
        "1.0.0",
    ]
    assert main(["--racine", str(depot), "--campagne", str(fichier), *options]) == 0
    texte = (depot / CAMPAGNES).read_text(encoding="utf-8")
    enregistre = json.loads(texte)["campagnes"][0]
    assert list(enregistre["resultats"]) == ["tests/integration/alpha/test_un.py::test_a"]
    assert (
        enregistre["resultats"]["tests/integration/alpha/test_un.py::test_a"]["raison"]
        == "absent de …"
    )
    assert enregistre["ecartes"][0]["test"].endswith("::test_vieux")
    assert "Users" not in texte and "detail" not in texte
    assert "ajoutée (1 tests, 1 entrée(s) écartée(s))" in capsys.readouterr().out

    # Une autre campagne s'ajoute après ; réenregistrer la première la remplace à sa place.
    autre = [*options[:1], "Autre", *options[2:]]
    assert main(["--racine", str(depot), "--campagne", str(fichier), *autre]) == 0
    assert main(["--racine", str(depot), "--campagne", str(fichier), *options]) == 0
    noms = [
        c["nom"] for c in json.loads((depot / CAMPAGNES).read_text(encoding="utf-8"))["campagnes"]
    ]
    assert noms == ["Poste", "Autre"]


def test_un_resultat_date_de_la_fin_de_l_execution_est_retenu(depot):
    """La fin de l'exécution est comprise dans l'exécution."""
    fichier = _resultats(
        depot, {"tests/integration/alpha/test_un.py::test_a": _resultat("reussi", FIN)}
    )
    options = ["--nom", "P", "--systeme", "S", "--python", "3", "--version", "1"]
    assert main(["--racine", str(depot), "--campagne", str(fichier), *options]) == 0
    assert "tests/integration/alpha/test_un.py::test_a" in lire(depot).campagnes[0].resultats


def test_une_campagne_illisible_ou_incomplete_est_refusee_en_le_disant(depot, capsys):
    illisible = depot / "illisible.json"
    illisible.write_text("{pas du json", encoding="utf-8")
    options = ["--nom", "P", "--systeme", "S", "--python", "3", "--version", "1"]
    assert main(["--racine", str(depot), "--campagne", str(illisible), *options]) == 1
    assert "illisible.json : fichier de résultats illisible" in capsys.readouterr().err

    arretee = _resultats(depot, {"tests/integration/alpha/test_un.py::test_a": _resultat("reussi")})
    contenu = json.loads(arretee.read_text(encoding="utf-8"))
    contenu["derniere"]["issue"] = "arretee"
    arretee.write_text(json.dumps(contenu), encoding="utf-8")
    assert main(["--racine", str(depot), "--campagne", str(arretee), *options]) == 1
    assert "n'est pas terminée" in capsys.readouterr().err

    # Une exécution des tests unitaires n'est pas une campagne de recette.
    unitaires = _resultats(
        depot, {"tests/integration/alpha/test_un.py::test_a": _resultat("reussi")}, "unitaires"
    )
    assert main(["--racine", str(depot), "--campagne", str(unitaires), *options]) == 1
    assert "pas toute la famille des tests d'intégration" in capsys.readouterr().err

    # Une exécution partielle (un seul fichier) non plus : son bilan ne compte pas les résultats.
    partielle = _resultats(
        depot, {"tests/integration/alpha/test_un.py::test_a": _resultat("reussi")}
    )
    contenu = json.loads(partielle.read_text(encoding="utf-8"))
    contenu["derniere"]["nb_tests"] = 5
    partielle.write_text(json.dumps(contenu), encoding="utf-8")
    assert main(["--racine", str(depot), "--campagne", str(partielle), *options]) == 1
    assert "python -m edt tests --famille integration" in capsys.readouterr().err

    # Un état de test inconnu est refusé en le nommant.
    inconnu = _resultats(depot, {"tests/integration/alpha/test_un.py::test_a": _resultat("perdu")})
    assert main(["--racine", str(depot), "--campagne", str(inconnu), *options]) == 1
    assert "état inconnu « perdu »" in capsys.readouterr().err


def test_un_echec_est_affiche_partout(depot):
    """Un test en échec apparaît dans la synthèse, le tableau, la fiche, l'annexe B et le bilan."""
    fichier = _resultats(
        depot,
        {
            "tests/integration/alpha/test_un.py::test_a": _resultat("echec"),
            "tests/integration/alpha/test_un.py::test_b": _resultat("reussi"),
        },
    )
    options = ["--nom", "Serveur", "--systeme", "Linux", "--python", "3.14", "--version", "1.0.0"]
    assert main(["--racine", str(depot), "--campagne", str(fichier), *options]) == 0
    assert main(["--racine", str(depot), "--ecrire"]) == 0
    texte = (depot / LIVRET).read_text(encoding="utf-8")
    assert "| Linux | 3.14 | 2 | 1 | 1 | 0 | 5 min 00 s |" in partie_generee(
        texte, "recette-synthese"
    )
    assert "| 2 | 1/2, 1 échec | Non conforme |" in partie_generee(texte, "recette-tableau")
    assert "1 réussi sur 2 ; 1 échec." in partie_generee(texte, "recette-fiches")
    annexe = partie_generee(texte, "recette-resultats-par-fichier")
    assert "| 1/3, 3 s, 1 échec | Non conforme |" in annexe
    assert "1 échec" in partie_generee(texte, "recette-bilan")
    assert "**Fiche non conforme.** REC-O1-1, « Créer une salle »" in partie_generee(
        texte, "recette-synthese"
    )


def test_les_classes_de_tests_et_les_tests_disparus_sont_vus(depot):
    """Une méthode de classe `Test*` est un test ; un test disparu d'une campagne est relevé."""
    assert "tests/integration/alpha/test_un.py::TestBeta::test_m" in lire(depot).tests
    fichier = _resultats(
        depot, {"tests/integration/alpha/test_un.py::test_disparu": _resultat("reussi")}
    )
    options = ["--nom", "Serveur", "--systeme", "S", "--python", "3", "--version", "1"]
    assert main(["--racine", str(depot), "--campagne", str(fichier), *options]) == 0
    assert resultats_disparus(lire(depot)) == {
        "Serveur": ["tests/integration/alpha/test_un.py::test_disparu"]
    }


def test_verdicts_et_ecriture_du_livret(depot, capsys):
    fichier = _resultats(
        depot,
        {
            "tests/integration/alpha/test_un.py::test_a": _resultat("reussi"),
            "tests/integration/alpha/test_un.py::test_b": _resultat(
                "ignore", raison="Linux seulement"
            ),
        },
    )
    options = ["--nom", "Serveur", "--systeme", "Linux", "--python", "3.14", "--version", "1.0.0"]
    assert main(["--racine", str(depot), "--campagne", str(fichier), *options]) == 0
    recette = lire(depot)
    fiche, manuelle = recette.fiches
    assert verdict(fiche, recette.campagnes) == NON_EXECUTEE  # test_b jamais réussi
    assert verdict(manuelle, recette.campagnes) == CONFORME

    recette.campagnes[0].resultats["tests/integration/alpha/test_un.py::test_b"] = (
        recette.campagnes[0].resultats["tests/integration/alpha/test_un.py::test_a"]
    )
    assert verdict(fiche, recette.campagnes) == CONFORME
    recette.campagnes[0].resultats["tests/integration/alpha/test_un.py::test_a"] = Resultat(
        "echec", 1.0
    )
    assert verdict(fiche, recette.campagnes) == NON_CONFORME

    assert main(["--racine", str(depot), "--ecrire"]) == 0
    texte = (depot / LIVRET).read_text(encoding="utf-8")
    assert "#### REC-O1-1. Créer une salle" in texte
    assert "1 réussi sur 2 ; 1 ignoré (Linux seulement)." in texte
    assert "**Verdict : Non exécutée.**" in texte
    assert "| **test_un.py** : Vérifie alpha | 3 | 2 |" in texte
    assert blocs_perimes(texte, lire(depot)) == []
    capsys.readouterr()
    assert main(["--racine", str(depot), "--ecrire"]) == 0
    assert "déjà à jour" in capsys.readouterr().out


def test_un_essai_manuel_non_conforme_est_compte_partout(depot):
    """Une fiche d'essai manuel « Non conforme » rend la fiche non conforme, et la synthèse, le
    tableau de recette, la matrice et le bilan la comptent."""
    fiches = depot / FICHES
    fiches.write_text(
        FICHES_MINI.replace('verdict = "Conforme"', 'verdict = "Non conforme"'), encoding="utf-8"
    )
    fichier = _resultats(depot, {"tests/integration/alpha/test_un.py::test_a": _resultat("reussi")})
    options = ["--nom", "Serveur", "--systeme", "Linux", "--python", "3.14", "--version", "1.0.0"]
    assert main(["--racine", str(depot), "--campagne", str(fichier), *options]) == 0
    recette = lire(depot)
    manuelle = next(f for f in recette.fiches if f.essai is not None)
    assert verdict(manuelle, recette.campagnes) == NON_CONFORME

    assert main(["--racine", str(depot), "--ecrire"]) == 0
    texte = (depot / LIVRET).read_text(encoding="utf-8")
    synthese = partie_generee(texte, "recette-synthese")
    tableau = partie_generee(texte, "recette-tableau")
    matrice = partie_generee(texte, "recette-matrice")
    bilan = partie_generee(texte, "recette-bilan")
    assert "1 « Non conforme »" in synthese and "1 « Non conforme »" in bilan
    assert "| essai manuel | — | Non conforme |" in tableau
    assert "| U1 | Vérifier avant | REC‑U1‑1 | 0 | Non conforme |" in matrice


def test_erreurs_claires_sur_des_fiches_fautives(depot, capsys):
    fiches = depot / FICHES
    fiches.write_text(
        FICHES_MINI.replace("test_un.py::test_b", "test_un.py::test_inconnu")
        .replace('"REC-U1-1"', '"REC-O1-2"')
        .replace('fonctionnalites = ["U1"]', 'fonctionnalites = ["O1"]'),
        encoding="utf-8",
    )
    assert main(["--racine", str(depot)]) == 1
    erreurs = capsys.readouterr().err
    assert "REC-O1-1 : test inconnu tests/integration/alpha/test_un.py::test_inconnu" in erreurs
    assert "U1 (Vérifier avant) : fonctionnalité réalisée sans fiche" in erreurs

    fiches.write_text(FICHES_MINI.replace('"REC-U1-1"', '"REC-X-1"'), encoding="utf-8")
    with pytest.raises(ErreurRecette, match="fiche REC-X-1 : identifiant invalide"):
        lire(depot)
    fiches.write_text(FICHES_MINI.replace('etapes = ["Ouvrir."]\n', "", 1), encoding="utf-8")
    with pytest.raises(ErreurRecette, match="REC-O1-1 : étapes ou résultat attendu vides"):
        lire(depot)

    # Une clé mal orthographiée n'est jamais ignorée en silence.
    fiches.write_text(FICHES_MINI.replace("tests = [", "test = [", 1), encoding="utf-8")
    with pytest.raises(ErreurRecette, match="fiche REC-O1-1 : clé.s. inconnue.s. test ;"):
        lire(depot)
    fiches.write_text(FICHES_MINI.replace("constat =", "constate =", 1), encoding="utf-8")
    with pytest.raises(ErreurRecette, match="fiche REC-U1-1, essai : clé.s. inconnue.s. constate"):
        lire(depot)

    # Une fiche sans test ni essai est signalée, et n'est jamais « Conforme ».
    sans_preuve = FICHES_MINI.split("[fiche.essai]")[0]
    fiches.write_text(sans_preuve, encoding="utf-8")
    recette = lire(depot)
    vide = next(f for f in recette.fiches if f.id == "REC-U1-1")
    assert verdict(vide, recette.campagnes) == NON_EXECUTEE
    assert "REC-U1-1 : ni test automatisé ni essai manuel" in problemes(recette)

    # Test cité deux fois, identifiant en double, cas d'usage inconnu.
    double = FICHES_MINI.replace(
        'tests = ["tests/integration/alpha/test_un.py::test_a",',
        'tests = ["tests/integration/alpha/test_un.py::test_a", '
        '"tests/integration/alpha/test_un.py::test_a",',
    ).replace("cas = [1]", "cas = [9]")
    fiches.write_text(double, encoding="utf-8")
    fautes = problemes(lire(depot))
    assert any("test cité deux fois" in f for f in fautes)
    assert any("cas d'usage inconnu du livret 1 : 9" in f for f in fautes)
    fiches.write_text(FICHES_MINI.replace('"REC-U1-1"', '"REC-O1-1"'), encoding="utf-8")
    with pytest.raises(ErreurRecette, match="REC-O1-1 : identifiant en double"):
        lire(depot)


def test_les_controles_de_l_outil_ne_relevent_rien(recette):
    """Citations, fiches sans preuve et couverture : les contrôles de l'outil ne relèvent rien."""
    fautes = problemes(recette)
    assert not fautes, f"{FICHES.as_posix()} :\n" + "\n".join(f"  - {f}" for f in fautes)
