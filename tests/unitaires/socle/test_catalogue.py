"""Catalogue des cas limites de `docs/banque-de-tests.md` (générateur `tests.outils.catalogue`).

Trois contrôles :

1. la partie générée du document est identique à ce que produit le générateur ;
2. chaque test `cas_limite` a une docstring avec un « Cas » et un « Risque » ;
3. le générateur lui-même, éprouvé sur de petits fichiers écrits dans `tmp_path`.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from tests.outils.catalogue import (
    COMMANDE,
    DEBUT,
    DOCUMENT,
    FIN,
    SANS_DOCSTRING,
    analyser,
    incomplets,
    lire_cas_et_risque,
    main,
    nom_lisible,
    partie_generee,
    produire,
    remplacer,
)

RACINE = Path(__file__).resolve().parents[3]
"""Racine du dépôt."""

DOCSTRINGS_A_COMPLETER = frozenset(
    {
        "tests/unitaires/synthese/test_performance.py::test_jeu_grand_en_moins_d_une_minute",
        "tests/integration/web_edt/test_calculs.py"
        "::test_suivi_sans_rechargement_second_lancement_refuse_puis_arret",
    }
)
"""Tests `cas_limite` sans docstring « Cas : … Risque : … » au moment où le contrôle a été posé.

Ils figurent dans le catalogue avec la mention `SANS_DOCSTRING`. Toute autre
absence fait échouer `test_chaque_cas_limite_a_un_cas_et_un_risque`. Un test
complété peut être retiré de cette liste.
"""


# --- 1. Le document est à jour ------------------------------------------------


def test_le_catalogue_du_document_est_a_jour():
    """La partie générée de `docs/banque-de-tests.md` est celle que produit le générateur."""
    document = (RACINE / DOCUMENT).read_text(encoding="utf-8")
    actuelle = partie_generee(document)
    assert actuelle is not None, (
        f"marqueurs du catalogue absents de {DOCUMENT.as_posix()} : « {DEBUT} » puis « {FIN} »"
    )
    attendue = produire(analyser(RACINE))
    if actuelle != attendue:
        lignes_actuelles, lignes_attendues = set(actuelle.splitlines()), attendue.splitlines()
        ecarts = [ligne for ligne in lignes_attendues if ligne not in lignes_actuelles]
        ecarts += [f"(en trop) {ligne}" for ligne in sorted(lignes_actuelles - {*lignes_attendues})]
        pytest.fail(
            f"le catalogue de {DOCUMENT.as_posix()} n'est plus à jour ; lancer, depuis la racine "
            f"du dépôt : {COMMANDE}\nPremiers écarts :\n"
            + "\n".join(f"  {ligne[:160]}" for ligne in ecarts[:10]),
            pytrace=False,
        )


# --- 2. Chaque cas limite dit son cas et son risque ----------------------------


def test_chaque_cas_limite_a_un_cas_et_un_risque():
    """Un test `cas_limite` sans « Cas : … » ou sans « Risque : … » dans sa docstring est signalé."""
    cas = analyser(RACINE)
    assert len(cas) > 100, f"seulement {len(cas)} cas limites trouvés : analyse cassée ?"
    fautifs = [
        c.identifiant for c in incomplets(cas) if c.identifiant not in DOCSTRINGS_A_COMPLETER
    ]
    assert not fautifs, (
        f"{len(fautifs)} test(s) `cas_limite` sans docstring « Cas : … Risque : … » complète :\n"
        + "\n".join(f"  - {f}" for f in fautifs)
    )


# --- 3. Le générateur ---------------------------------------------------------


def _ecrire(racine: Path, chemin: str, code: str) -> None:
    """Écrit un fichier Python sous `racine`, dossiers compris."""
    fichier = racine / chemin
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(textwrap.dedent(code), encoding="utf-8")


@pytest.fixture
def depot(tmp_path: Path) -> Path:
    """Dépôt miniature : un module marqué en entier, des marqueurs par test et par classe."""
    _ecrire(
        tmp_path,
        "tests/unitaires/alpha/test_un.py",
        '''
        import pytest

        @pytest.mark.cas_limite
        def test_salle_trop_petite():
            """Cas : une salle de 10 places
            pour 30 étudiants.

            Risque : séance placée | sans alerte."""

        def test_ordinaire():
            """Cas : pas marqué. Risque : aucun."""

        @pytest.mark.perf
        @pytest.mark.cas_limite()
        def test_sans_risque():
            """Cas : seulement le cas."""

        class TestGroupe:
            pytestmark = [pytest.mark.cas_limite]

            def test_dans_la_classe(self):
                """Cas : méthode. Risque : oubliée."""
        ''',
    )
    _ecrire(
        tmp_path,
        "tests/integration/beta/test_deux.py",
        '''
        import pytest

        pytestmark = [pytest.mark.perf, pytest.mark.cas_limite]

        def test_tout_le_module():
            """Cas : marqué par le module. Risque : ignoré."""

        def aide():
            """Pas un test."""
        ''',
    )
    return tmp_path


def test_le_generateur_trouve_les_marqueurs_de_fonction_de_classe_et_de_module(depot):
    cas = analyser(depot)

    assert [c.identifiant for c in cas] == [
        "tests/unitaires/alpha/test_un.py::test_salle_trop_petite",
        "tests/unitaires/alpha/test_un.py::test_sans_risque",
        "tests/unitaires/alpha/test_un.py::TestGroupe::test_dans_la_classe",
        "tests/integration/beta/test_deux.py::test_tout_le_module",
    ]
    assert [(c.famille, c.composant) for c in cas] == [
        ("unitaires", "alpha"),
        ("unitaires", "alpha"),
        ("unitaires", "alpha"),
        ("integration", "beta"),
    ]
    premier = cas[0]
    assert premier.cas == "une salle de 10 places pour 30 étudiants."
    assert premier.risque == "séance placée | sans alerte."
    assert [c.identifiant for c in incomplets(cas)] == [
        "tests/unitaires/alpha/test_un.py::test_sans_risque"
    ]


def test_le_generateur_produit_une_ligne_par_test_rangee_par_famille_composant_fichier(depot):
    texte = produire(analyser(depot))

    assert texte.startswith("Le catalogue compte 4 cas : 3 cas en tests unitaires et 1 cas en ")
    assert "| `alpha` | 3 | 0 |" in texte
    assert "| `beta` | 0 | 1 |" in texte
    assert texte.index("### Tests unitaires (3 cas)") < texte.index("#### `alpha` (3 cas)")
    assert texte.index("#### `alpha`") < texte.index("### Tests d'intégration (1 cas)")
    assert "[`tests/unitaires/alpha/test_un.py`](../tests/unitaires/alpha/test_un.py)" in texte
    assert (
        "| Salle trop petite | une salle de 10 places pour 30 étudiants. "
        "| séance placée \\| sans alerte. |"
    ) in texte
    assert f"| Sans risque | seulement le cas. | {SANS_DOCSTRING} |" in texte
    assert "Ordinaire" not in texte
    assert texte == produire(analyser(depot)), "le catalogue doit être déterministe"


def test_ecrire_remplace_seulement_la_partie_generee_et_signale_les_marqueurs_absents(
    depot, capsys
):
    document = depot / DOCUMENT
    document.parent.mkdir()
    document.write_text(
        f"# Titre\n\nIntro.\n\n{DEBUT}\nancien\n{FIN}\n\nSuite.\n", encoding="utf-8"
    )

    assert main(["--ecrire", "--racine", str(depot)]) == 0
    texte = document.read_text(encoding="utf-8")
    assert texte.startswith(f"# Titre\n\nIntro.\n\n{DEBUT}\n\nLe catalogue compte 4 cas")
    assert texte.endswith(f"\n{FIN}\n\nSuite.\n")
    assert "ancien" not in texte
    assert partie_generee(texte) == produire(analyser(depot))
    assert "catalogue mis à jour (4 cas)" in capsys.readouterr().out

    assert main(["--ecrire", "--racine", str(depot)]) == 0
    assert "déjà à jour" in capsys.readouterr().out

    document.write_text("# Titre sans marqueurs\n", encoding="utf-8")
    assert main(["--ecrire", "--racine", str(depot)]) == 1
    assert "marqueurs du catalogue introuvables" in capsys.readouterr().err
    with pytest.raises(ValueError, match="introuvables"):
        remplacer("rien", "catalogue")


def test_lecture_du_cas_et_du_risque():
    assert lire_cas_et_risque(None) == (None, None)
    assert lire_cas_et_risque("Vérifie autre chose.") == (None, None)
    assert lire_cas_et_risque("Cas : a  b\n c. Risque : d.") == ("a b c.", "d.")
    assert lire_cas_et_risque("Cas : a.\n\nRisque : d.\n\nNote annexe.") == ("a.", "d.")
    assert lire_cas_et_risque("Cas :\nRisque : d.") == (None, "d.")


def test_nom_lisible():
    assert nom_lisible("test_salle_trop_petite") == "Salle trop petite"
    assert nom_lisible("TestGroupe.test_un_cas") == "Un cas"
