"""Le témoin est un emploi du temps valide, aligné sur l'instantané officiel du semestre."""

from __future__ import annotations

import pytest

from edt.services.instantane import charger_instantane, charger_placements

from .controle_temoin import violations


@pytest.mark.parametrize("nom_jeu", ["jeu_petit", "jeu_moyen"])
def test_temoin_respecte_les_contraintes_dures(request, nom_jeu):
    jeu = request.getfixturevalue(nom_jeu)
    with jeu.session() as session:
        for code, semestre_id in jeu.rapport.semestres.items():
            instantane = charger_instantane(session, semestre_id)
            assert violations(instantane, jeu.rapport.temoin_par_semestre[code]) == []


@pytest.mark.parametrize("nom_jeu", ["jeu_petit", "jeu_moyen"])
def test_instantane_sans_probleme_et_memes_seances_que_le_temoin(request, nom_jeu):
    jeu = request.getfixturevalue(nom_jeu)
    with jeu.session() as session:
        for code, semestre_id in jeu.rapport.semestres.items():
            instantane = charger_instantane(session, semestre_id)
            assert instantane.problemes_depliage == ()
            assert {s.id for s in instantane.seances} == set(jeu.rapport.temoin_par_semestre[code])
            # La version de référence n'est pas active : le solveur ne reçoit aucun indice.
            assert instantane.version_base_id is None
            assert all(s.indice is None and s.verrou is None for s in instantane.seances)


def test_version_de_reference_reprend_le_temoin(jeu_moyen):
    rapport = jeu_moyen.rapport
    with jeu_moyen.session() as session:
        for code, version_id in rapport.versions_reference.items():
            assert set(charger_placements(session, version_id)) == set(rapport.placements(code))


def test_controle_detecte_une_violation(jeu_petit):
    """Le contrôle n'est pas complaisant : deux séances d'un même groupe superposées sont vues."""
    rapport = jeu_petit.rapport
    temoin = dict(rapport.temoin_par_semestre["S1"])
    with jeu_petit.session() as session:
        instantane = charger_instantane(session, rapport.semestres["S1"])
        cm, td = (
            next(s for s in instantane.seances if s.besoin.type_seance_code == code)
            for code in ("CM", "TD")
        )
        temoin[td.id] = temoin[cm.id]._replace(salle_id=temoin[td.id].salle_id)
        assert any("en même temps" in v for v in violations(instantane, temoin))
