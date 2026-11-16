"""Relation de conflit entre groupes : hiérarchie, chevauchement déclaré, étudiant commun."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from edt.db.modeles import Groupe
from edt.erreurs import ErreurDonneesInvalides
from edt.services.conflits_groupes import calculer_conflits_groupes, conflits_groupes

# Arbre : promotion 1 > classes 2 et 3 > demi-groupes 4, 5 (de 2) et 6 (de 3).
# Groupes hors arbre : 7 (langue) et 8 (option).
PARENTS = {1: None, 2: 1, 3: 1, 4: 2, 5: 2, 6: 3, 7: None, 8: None}


def en_conflit(conflits, a, b) -> bool:
    return (min(a, b), max(a, b)) in conflits


def test_un_groupe_est_en_conflit_avec_ses_ascendants_pas_avec_ses_freres():
    conflits = calculer_conflits_groupes(PARENTS)

    assert en_conflit(conflits, 4, 2)  # demi-groupe et sa classe
    assert en_conflit(conflits, 4, 1)  # demi-groupe et sa promotion
    assert not en_conflit(conflits, 4, 5)  # deux demi-groupes de la même classe
    assert not en_conflit(conflits, 2, 3)  # deux classes de la même promotion
    assert not en_conflit(conflits, 4, 6)
    assert not en_conflit(conflits, 7, 1)  # un groupe hors arbre, sans autre information


def test_un_chevauchement_declare_vaut_pour_toute_la_lignee():
    conflits = calculer_conflits_groupes(PARENTS, chevauchements=[(7, 2)])

    for groupe in (2, 1, 4, 5):  # la classe, son ascendant, ses descendants
        assert en_conflit(conflits, 7, groupe)
    assert not en_conflit(conflits, 7, 3)
    assert not en_conflit(conflits, 7, 6)


def test_un_etudiant_commun_met_en_conflit_ses_groupes_et_leurs_ascendants():
    # L'étudiant 100 est inscrit dans le demi-groupe 4 et dans l'option 8.
    conflits = calculer_conflits_groupes(PARENTS, inscriptions=[(100, 4), (100, 8)])

    for groupe in (4, 2, 1):
        assert en_conflit(conflits, 8, groupe)
    assert not en_conflit(conflits, 8, 5)  # l'autre demi-groupe n'a pas cet étudiant
    assert not en_conflit(conflits, 8, 3)


def test_les_paires_sont_ordonnees_et_sans_doublon():
    conflits = calculer_conflits_groupes(PARENTS, chevauchements=[(7, 2), (2, 7)])
    assert all(a < b for a, b in conflits)


def test_un_groupe_inconnu_est_refuse():
    with pytest.raises(ErreurDonneesInvalides):
        calculer_conflits_groupes(PARENTS, chevauchements=[(7, 99)])


@pytest.mark.cas_limite
def test_une_boucle_de_groupes_est_refusee_avec_le_remede():
    """Cas : deux groupes sont chacun le parent de l'autre (base modifiée hors de l'écran).

    Risque : message qui constate la boucle sans dire quel champ corriger ni où.
    """
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        calculer_conflits_groupes({1: 2, 2: 1})
    message = erreur.value.message
    assert "boucle autour du groupe n° 1" in message
    assert "corriger son groupe parent (Données › Groupes)" in message


def test_relation_du_jeu_minimal_lue_dans_la_base(jeu_minimal, session):
    ids = dict(session.execute(select(Groupe.code, Groupe.id)).all())
    conflits = conflits_groupes(session, jeu_minimal.annee_scolaire_id)

    def lies(code_a: str, code_b: str) -> bool:
        return en_conflit(conflits, ids[code_a], ids[code_b])

    assert lies("A1", "A1-G1a")  # hiérarchie
    assert not lies("A1-G1", "A1-G2")
    assert lies("A1-ANG-B2", "A1-G2b")  # chevauchement déclaré avec la classe, donc sa lignée
    assert lies("A1-PMR", "A1-G1a")  # étudiant commun
    assert lies("A1-PMR", "A1")
    assert not lies("A1-PMR", "A1-G1b")
    assert not lies("A1-PMR", "A1-G2")
