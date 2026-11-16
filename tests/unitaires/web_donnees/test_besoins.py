"""Charge hebdomadaire par groupe et groupes des besoins, calculées par le service des enseignements."""

from __future__ import annotations

from edt.db.moteur import session_base
from edt.services.referentiels.enseignements import charges_hebdomadaires, groupes_des_besoins


def test_charge_hebdomadaire_par_groupe(ids):
    with session_base() as session:
        charges = {c.code: c for c in charges_hebdomadaires(session, ids.semestre)}
    classe = charges["A1-G1"]
    assert classe.heritees == charges["A1"].propres  # elle suit les cours de sa promotion
    assert classe.ponctuelles == 2  # DST et partiel de la promotion
    assert classe.total == round(classe.propres + classe.heritees, 1)


def test_groupes_des_besoins_et_charges_restreintes(ids):
    with session_base() as session:
        groupes = groupes_des_besoins(session, [1])  # CM de la promotion
        charges = charges_hebdomadaires(session, ids.semestre, {ids.classe_g1})
    assert ids.promo in groupes and ids.classe_g1 in groupes  # avec ses sous-groupes
    assert [c.code for c in charges] == ["A1-G1"]
