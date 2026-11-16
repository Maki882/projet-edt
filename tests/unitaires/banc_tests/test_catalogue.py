"""Catalogue des tests lu sans les exécuter, racine du dépôt et cibles tirées du catalogue."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.outils.banc_tests.depot import (
    CLE_CHARGE,
    CLE_ECHEC,
    CLE_METHODE,
    CLE_PARAMETRE,
    CLE_REUSSI,
    EXEMPLE,
)

from edt.erreurs import ErreurIntrouvable
from edt.services.banc_tests import catalogue as cat


def test_catalogue_lu_par_famille_composant_et_fichier(depot: Path):
    catalogue = cat.lire_catalogue()

    assert catalogue.racine == depot.resolve()
    assert catalogue.composants("unitaires") == ["demo"]
    assert catalogue.composants("integration") == ["casse", "demo", "lent"]
    cles = [t.cle for t in catalogue.tests("unitaires")]
    assert cles == [
        CLE_REUSSI,
        CLE_ECHEC,
        f"{EXEMPLE}::test_ignore",
        CLE_PARAMETRE,
        CLE_CHARGE,
        CLE_METHODE,
    ]
    reussi = catalogue.test(CLE_REUSSI)
    assert reussi.nom == "Reussi"
    assert reussi.resume == "Réussit toujours."  # première phrase seulement
    assert catalogue.test(CLE_METHODE).nom == "Methode"
    assert [t.perf for t in catalogue.tests("unitaires")] == [False] * 4 + [True, False]


def test_catalogue_garde_en_memoire_puis_relit_un_fichier_modifie(depot: Path):
    premier = cat.lire_catalogue()
    assert cat.lire_catalogue() is premier

    fichier = depot / EXEMPLE
    fichier.write_text(
        fichier.read_text(encoding="utf-8") + "\n\ndef test_ajoute():\n    pass\n", encoding="utf-8"
    )

    relu = cat.lire_catalogue()
    assert relu is not premier
    assert relu.test(f"{EXEMPLE}::test_ajoute") is not None


def test_marqueur_perf_par_pytestmark_et_par_classe(tmp_path: Path):
    racine = tmp_path / "depot"
    (racine / "tests" / "unitaires" / "x").mkdir(parents=True)
    (racine / "tests" / "unitaires" / "x" / "test_a.py").write_text(
        "import pytest\npytestmark = [pytest.mark.perf]\n\ndef test_un():\n    pass\n",
        encoding="utf-8",
    )
    (racine / "tests" / "unitaires" / "x" / "test_b.py").write_text(
        "import pytest\n\n@pytest.mark.perf\nclass TestLourd:\n    def test_deux(self):\n        pass\n\n"
        "def test_trois():\n    pass\n",
        encoding="utf-8",
    )

    tests = {t.fonction: t.perf for t in cat.lire_catalogue(racine).tests("unitaires")}

    assert tests == {"test_un": True, "TestLourd.test_deux": True, "test_trois": False}


@pytest.mark.cas_limite
def test_fichier_de_tests_illisible_liste_avec_son_erreur(depot: Path):
    """Cas : un fichier de tests contient une erreur de syntaxe (modification en cours).

    Risque : la page des tests répond 500, ou le fichier disparaît sans explication."""
    (depot / "tests" / "unitaires" / "demo" / "test_faux.py").write_text(
        "def test_(:\n", encoding="utf-8"
    )

    catalogue = cat.lire_catalogue()

    faux = next(f for f in catalogue.fichiers if f.nom == "test_faux.py")
    assert faux.tests == () and "illisible" in faux.erreur
    assert catalogue.test(CLE_REUSSI) is not None  # les autres fichiers restent lus


@pytest.mark.cas_limite
def test_dossier_de_tests_absent_donne_un_catalogue_vide_explique(tmp_path, monkeypatch):
    """Cas : installation sans dossier « tests » (code déployé sans les tests), ou variable
    EDT_TESTS_RACINE fausse.

    Risque : erreur 500 sur les deux onglets, ou lancement de pytest sur un dossier vide."""
    monkeypatch.setenv(cat.VARIABLE_RACINE, str(tmp_path / "nulle-part"))

    catalogue = cat.lire_catalogue()

    assert catalogue.racine is None and catalogue.fichiers == ()
    assert cat.VARIABLE_RACINE in catalogue.absence
    with pytest.raises(ErreurIntrouvable, match="EDT_TESTS_RACINE"):
        cat.resoudre_cible(catalogue, cat.CIBLE_FAMILLE, "unitaires")


def test_racine_trouvee_depuis_le_dossier_courant(depot: Path, monkeypatch):
    monkeypatch.delenv(cat.VARIABLE_RACINE)
    monkeypatch.chdir(depot)
    (depot / "tests" / "unitaires").mkdir(exist_ok=True)

    racine, absence = cat.trouver_racine()

    assert racine == depot.resolve() and absence is None


def test_cibles_tirees_du_catalogue(depot: Path):
    catalogue = cat.lire_catalogue()

    famille = cat.resoudre_cible(catalogue, "famille", "unitaires")
    assert famille.chemins == ("tests/unitaires",)
    assert famille.exclure_perf and CLE_CHARGE not in famille.tests
    assert "famille:unitaires" in famille.groupes and f"fichier:{EXEMPLE}" in famille.groupes

    fichier = cat.resoudre_cible(catalogue, f"fichier:{EXEMPLE}", "unitaires")
    assert fichier.chemins == (EXEMPLE,) and fichier.groupes == (f"fichier:{EXEMPLE}",)

    charge = cat.resoudre_cible(catalogue, f"test:{CLE_CHARGE}", "unitaires")
    assert charge.chemins == (CLE_CHARGE,) and not charge.exclure_perf  # lancé seul : permis

    composant = cat.resoudre_cible(catalogue, "composant:demo", None)  # les deux familles
    assert composant.chemins == ("tests/unitaires/demo", "tests/integration/demo")


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("cible", "famille"),
    [
        ("composant:inconnu", "unitaires"),
        ("composant:lent", "unitaires"),  # existe, mais dans l'autre famille
        ("fichier:../../../etc/passwd", "unitaires"),
        ("fichier:tests/unitaires/demo", "unitaires"),
        (f"test:{EXEMPLE}::test_inconnu", "unitaires"),
        ("test:tests/unitaires/demo/test_exemple.py::test_reussi --rootdir=/", "unitaires"),
        ("-p os", "unitaires"),
        ("", "unitaires"),
        ("famille", "fonctionnels"),
    ],
)
def test_cible_inconnue_refusee(depot: Path, cible: str, famille: str):
    """Cas : formulaire trafiqué, page restée ouverte après la suppression d'un test, option
    de pytest glissée dans la cible.

    Risque : un texte reçu du navigateur passe tel quel dans la commande pytest."""
    catalogue = cat.lire_catalogue()

    with pytest.raises(ErreurIntrouvable):
        cat.resoudre_cible(catalogue, cible, famille)


def test_premiere_phrase_et_nom_lisible():
    assert cat.premiere_phrase("Une phrase. Une autre.\n\nSecond paragraphe.") == "Une phrase."
    assert cat.premiere_phrase("Sur deux\nlignes sans point") == "Sur deux lignes sans point"
    assert cat.premiere_phrase(None) == ""
    assert cat.premiere_phrase("Lit `docs/x.md` en entier.") == "Lit docs/x.md en entier."
    assert cat.nom_lisible("test_salle_trop_petite") == "Salle trop petite"
    assert cat.nom_lisible("TestGroupe.test_x") == "X"
