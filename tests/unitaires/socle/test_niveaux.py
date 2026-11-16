"""Niveaux des contraintes (nécessaire, important, facultatif) : contrat du catalogue."""

from __future__ import annotations

import pytest

from edt.domaine.contraintes import (
    CATALOGUE,
    POIDS_NIVEAU,
    CodeContrainte,
    Niveau,
    niveau,
    niveaux_depuis_desactivees,
    niveaux_par_defaut,
    normaliser_niveaux,
)
from edt.erreurs import ErreurDonneesInvalides

TOUJOURS_NECESSAIRES = {
    "SALLE_UNIQUE",
    "ENSEIGNANT_UNIQUE",
    "GROUPE_UNIQUE",
    "VERROU",
    "JOUR_FERME",
    "VOLUME",
    "REGLE",
}
IMPORTANTES = {"PAUSE", "DEJEUNER", "EQUIPEMENT", "MAX_ENSEIGNANT"}


def test_les_niveaux_du_plus_exigeant_au_moins_exigeant():
    assert [n.value for n in Niveau] == ["necessaire", "important", "facultatif"]
    assert [n.libelle for n in Niveau] == ["Nécessaire", "Important", "Facultatif"]
    assert all(n.description.endswith(".") for n in Niveau)
    assert niveau("important") is Niveau.IMPORTANT
    with pytest.raises(ErreurDonneesInvalides):
        niveau("souple")


def test_chaque_contrainte_a_un_niveau_par_defaut_et_un_caractere_modifiable():
    defauts = niveaux_par_defaut()
    assert set(defauts) == set(CodeContrainte)
    for code, entree in CATALOGUE.items():
        assert defauts[code] is entree.niveau_defaut
        assert entree.modifiable is (code.value not in TOUJOURS_NECESSAIRES)
        attendu = Niveau.IMPORTANT if code.value in IMPORTANTES else Niveau.NECESSAIRE
        assert entree.niveau_defaut is attendu, code
    # Ce qui définit le problème est nécessaire et le reste.
    assert all(
        CATALOGUE[CodeContrainte(c)].niveau_defaut is Niveau.NECESSAIRE
        for c in TOUJOURS_NECESSAIRES
    )


def test_normaliser_niveaux_complete_les_choix_par_les_defauts():
    niveaux = normaliser_niveaux({"PAUSE": "facultatif", "CAPACITE": "important"})

    assert set(niveaux) == set(CodeContrainte)
    assert niveaux[CodeContrainte.PAUSE] is Niveau.FACULTATIF
    assert niveaux[CodeContrainte.CAPACITE] is Niveau.IMPORTANT
    assert niveaux[CodeContrainte.DEJEUNER] is Niveau.IMPORTANT  # défaut, non choisi
    assert niveaux[CodeContrainte.VERROU] is Niveau.NECESSAIRE
    assert normaliser_niveaux({}) == niveaux_par_defaut()


@pytest.mark.parametrize("code", sorted(TOUJOURS_NECESSAIRES))
def test_une_contrainte_qui_definit_le_probleme_refuse_un_autre_niveau(code):
    for autre in ("important", "facultatif"):
        with pytest.raises(ErreurDonneesInvalides, match="définit le problème"):
            normaliser_niveaux({code: autre})
    assert normaliser_niveaux({code: "necessaire"})[CodeContrainte(code)] is Niveau.NECESSAIRE


def test_normaliser_niveaux_refuse_les_inconnus():
    with pytest.raises(ErreurDonneesInvalides, match="Contrainte inconnue"):
        normaliser_niveaux({"INCONNUE": "necessaire"})
    with pytest.raises(ErreurDonneesInvalides, match="Niveau inconnu"):
        normaliser_niveaux({"PAUSE": "obligatoire"})


def test_niveaux_depuis_desactivees_retire_les_cles():
    niveaux = niveaux_depuis_desactivees(["PAUSE", "CAPACITE"])

    assert CodeContrainte.PAUSE not in niveaux  # clé absente = contrainte ignorée
    assert CodeContrainte.CAPACITE not in niveaux
    assert set(niveaux) == set(CodeContrainte) - {CodeContrainte.PAUSE, CodeContrainte.CAPACITE}
    assert niveaux[CodeContrainte.DEJEUNER] is Niveau.IMPORTANT
    assert niveaux_depuis_desactivees() == niveaux_par_defaut()
    with pytest.raises(ErreurDonneesInvalides):
        niveaux_depuis_desactivees(["INCONNUE"])


def test_poids_des_niveaux_souples():
    assert set(POIDS_NIVEAU) == {Niveau.IMPORTANT, Niveau.FACULTATIF}
    assert POIDS_NIVEAU[Niveau.IMPORTANT] > POIDS_NIVEAU[Niveau.FACULTATIF] > 10  # plus qu'un trou
