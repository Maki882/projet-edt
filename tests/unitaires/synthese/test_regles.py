"""Règles personnalisées des jeux fictifs : présence, phrases, validité, respect par le témoin."""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest
from sqlalchemy import select
from tests.outils.synthese.controle_temoin import violations_regles

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.regles import EtatRegle, OrigineRegle, valider_parametres
from edt.services.instantane import charger_instantane
from edt.services.parametres import ecrire_parametre, lire_parametres
from edt.services.regles import lister
from edt.synthese import PETIT
from edt.synthese.generateur import generer_jeu
from edt.synthese.temoin import dejeuner_libre_respecte
from edt.verification import verifier

ACTIVE, INACTIVE, PROPOSEE = "active", "inactive", "proposee"

ATTENDUES = {
    "moyen": {
        ("JOURS_TRAVAILLES", ACTIVE): 1,
        ("PAUSE_DEJEUNER", ACTIVE): 1,
        ("PAUSE_MINIMALE", ACTIVE): 1,
        ("CRENEAUX_INTERDITS", ACTIVE): 1,
        ("CAMPUS", ACTIVE): 1,
        ("SALLES", ACTIVE): 1,
        ("CAMPUS_PAR_JOUR", ACTIVE): 1,
        ("MAX_PAR_JOUR", ACTIVE): 1,
        ("PLAGE_HORAIRE", ACTIVE): 1,
        ("JOURNEES_LIBRES", ACTIVE): 2,
        ("REPARTITION_MATIERE", INACTIVE): 1,
        ("ORDRE_SEMAINE", PROPOSEE): 1,
        ("JOURNEES_LIBRES", PROPOSEE): 1,
    },
    "grand": {
        ("JOURS_TRAVAILLES", ACTIVE): 2,
        ("PAUSE_DEJEUNER", ACTIVE): 1,
        ("PAUSE_MINIMALE", ACTIVE): 1,
        ("CRENEAUX_INTERDITS", ACTIVE): 1,
        ("CAMPUS", ACTIVE): 2,
        ("SALLES", ACTIVE): 1,
        ("CAMPUS_PAR_JOUR", ACTIVE): 1,
        ("MAX_PAR_JOUR", ACTIVE): 1,
        ("PLAGE_HORAIRE", ACTIVE): 1,
        ("JOURNEES_LIBRES", ACTIVE): 2,
        ("REPARTITION_MATIERE", INACTIVE): 1,
        ("ORDRE_SEMAINE", PROPOSEE): 1,
        ("JOURNEES_LIBRES", PROPOSEE): 1,
    },
}


def _details(jeu):
    with jeu.session() as session:
        annee_id = session.scalar(select(m.AnneeScolaire.id))
        return lister(session, annee_id)


@pytest.mark.parametrize("nom_jeu", ["jeu_moyen", "jeu_grand"])
def test_regles_attendues_par_type_et_par_etat(request, nom_jeu):
    jeu = request.getfixturevalue(nom_jeu)
    details = _details(jeu)
    obtenues = Counter((d.type.value, d.etat.value) for d in details)
    assert obtenues == Counter(ATTENDUES[nom_jeu.removeprefix("jeu_")])
    assert [d.code for d in details] == [f"R{n:03d}" for n in range(1, len(details) + 1)]
    for detail in details:
        regle = detail.regle
        attendue = OrigineRegle.BASE if regle.etat is EtatRegle.PROPOSEE else OrigineRegle.SAISIE
        assert regle.origine is attendue
    assert [r.code for r in jeu.rapport.regles] == [d.code for d in details]


@pytest.mark.parametrize("nom_jeu", ["jeu_petit", "jeu_moyen", "jeu_grand"])
def test_phrases_et_parametres_valides(request, nom_jeu):
    jeu = request.getfixturevalue(nom_jeu)
    with jeu.session() as session:
        parametres = lire_parametres(session)
    phrases = {r.code: r.phrase for r in jeu.rapport.regles}
    for detail in _details(jeu):
        regle = detail.regle
        assert detail.phrase == phrases[detail.code]
        assert detail.phrase[0].isupper() and detail.phrase.endswith("."), detail.phrase
        assert (
            valider_parametres(regle.type, regle.parametres, parametres, portee=regle.portee.nature)
            == regle.parametres
        )


@pytest.mark.parametrize("nom_jeu", ["jeu_petit", "jeu_moyen", "jeu_grand"])
def test_temoin_respecte_les_regles_actives(request, nom_jeu):
    jeu = request.getfixturevalue(nom_jeu)
    with jeu.session() as session:
        for code, semestre_id in jeu.rapport.semestres.items():
            instantane = charger_instantane(session, semestre_id)
            assert instantane.regles, code
            assert violations_regles(instantane, jeu.rapport.temoin_par_semestre[code]) == []


def test_periode_d_une_regle(jeu_moyen):
    """La pause déjeuner de A3-G2 ne vaut que pour le semestre 1."""
    with jeu_moyen.session() as session:
        par_semestre = {
            code: {r.type.value for r in charger_instantane(session, sid).regles}
            for code, sid in jeu_moyen.rapport.semestres.items()
        }
    assert "PAUSE_DEJEUNER" in par_semestre["S1"]
    assert "PAUSE_DEJEUNER" not in par_semestre["S2"]


def test_controle_des_regles_detecte_un_ecart(jeu_moyen):
    """Le contrôle n'est pas complaisant : un CM déplacé le vendredi après-midi est vu."""
    rapport = jeu_moyen.rapport
    temoin = dict(rapport.temoin_par_semestre["S1"])
    with jeu_moyen.session() as session:
        instantane = charger_instantane(session, rapport.semestres["S1"])
        cm = next(s for s in instantane.seances if s.besoin.type_seance_code == "CM")
        temoin[cm.id] = temoin[cm.id]._replace(jour=4, debut=13 * 60 + 45, fin=15 * 60 + 45)
        assert any("créneau interdit" in v for v in violations_regles(instantane, temoin))


def test_dejeuner_libre_respecte():
    fenetre = (11 * 60 + 45, 14 * 60 + 15, 75)
    matin, apres_midi = (10 * 60 + 45, 12 * 60 + 45), (13 * 60 + 45, 15 * 60 + 45)
    assert dejeuner_libre_respecte([], fenetre)
    assert dejeuner_libre_respecte([matin], fenetre)
    assert not dejeuner_libre_respecte([matin, apres_midi], fenetre)


@pytest.mark.cas_limite
def test_dejeuner_global_en_mode_libre(base_vierge):
    """Cas : pause déjeuner réglée en mode libre pour tout l'établissement avant le chargement.
    Risque : témoin qui prive une personne de sa pause (jeu invalide dès sa création)."""
    with session_base(base_vierge) as session:
        ecrire_parametre(session, "dejeuner_mode", "libre")
        ecrire_parametre(session, "dejeuner_debut_min", 11 * 60 + 45)
        ecrire_parametre(session, "dejeuner_fin_min", 14 * 60 + 15)
        ecrire_parametre(session, "dejeuner_duree_min", 75)
    with session_base(base_vierge) as session:
        rapport = generer_jeu(session, dataclasses.replace(PETIT, nom="libre"), graine=1)
        instantane = charger_instantane(session, rapport.semestres["S1"])
        placements = rapport.placements("S1")
        assert rapport.besoins_ecartes == ()
        assert violations_regles(instantane, rapport.temoin_par_semestre["S1"]) == []
        assert not [a for a in verifier(instantane, placements) if a.bloquante]
