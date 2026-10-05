"""Disposition des blocs dans la grille (fonction pure) et mises en forme françaises."""

from __future__ import annotations

import pytest

from edt.erreurs import ErreurDonneesInvalides
from edt.services.emploi_du_temps import presentation
from edt.services.emploi_du_temps.disposition import Plage, calculer_axe, disposer

H = 60
OUVERTURE, FERMETURE = 8 * H + 30, 18 * H


def _se_recouvrent(a, b) -> bool:
    """Vrai si deux positions se recouvrent à la fois en hauteur et en largeur."""
    verticalement = a.haut < b.haut + b.hauteur and b.haut < a.haut + a.hauteur
    horizontalement = a.gauche < b.gauche + b.largeur and b.gauche < a.gauche + a.largeur
    return verticalement and horizontalement


def test_deux_seances_simultanees_cote_a_cote():
    tp_a = Plage("tp-a", 0, 8 * H + 30, 10 * H + 30)
    tp_b = Plage("tp-b", 0, 8 * H + 30, 10 * H + 30)

    positions = disposer([tp_a, tp_b], OUVERTURE, FERMETURE).positions

    a, b = positions["tp-a"], positions["tp-b"]
    assert (a.nb_voies, b.nb_voies) == (2, 2)
    assert {a.gauche, b.gauche} == {0.0, 50.0}
    assert a.largeur == b.largeur == 50.0
    assert a.haut == b.haut and a.hauteur == b.hauteur
    assert not _se_recouvrent(a, b)


def test_une_voie_liberee_est_reutilisee_et_un_bloc_s_elargit_sur_la_voie_libre():
    long_ = Plage("cm", 0, 8 * H + 30, 12 * H + 30)
    premier = Plage("td-1", 0, 8 * H + 30, 10 * H + 30)
    second = Plage("td-2", 0, 10 * H + 30, 12 * H + 30)
    apres = Plage("tp", 0, 13 * H + 45, 15 * H + 45)

    positions = disposer([second, apres, premier, long_], OUVERTURE, FERMETURE).positions

    assert positions["cm"].voie == 0 and positions["cm"].largeur == 50.0
    assert positions["td-1"].voie == positions["td-2"].voie == 1  # la voie libérée sert à nouveau
    assert positions["tp"].largeur == 100.0 and positions["tp"].nb_voies == 1  # autre grappe
    blocs = list(positions.values())
    assert not any(_se_recouvrent(x, y) for i, x in enumerate(blocs) for y in blocs[i + 1 :])


def test_un_bloc_s_etend_sur_les_voies_voisines_libres():
    a = Plage("a", 0, 8 * H, 9 * H)
    b = Plage("b", 0, 8 * H, 10 * H)
    c = Plage("c", 0, 9 * H, 10 * H)
    d = Plage("d", 0, 8 * H, 8 * H + 30)

    positions = disposer([a, b, c, d], OUVERTURE, FERMETURE).positions

    assert positions["b"].nb_voies == 3 and positions["b"].voie == 0
    # « c » commence quand « a » (voie 1) et « d » (voie 2) sont finies : il reprend la
    # voie 1 et s'étend sur la voie 2, restée libre pendant toute sa durée.
    assert positions["c"].voie == 1
    assert positions["c"].gauche == pytest.approx(100 / 3, abs=1e-3)
    assert positions["c"].largeur == pytest.approx(200 / 3, abs=1e-3)
    blocs = list(positions.values())
    assert not any(_se_recouvrent(x, y) for i, x in enumerate(blocs) for y in blocs[i + 1 :])


def test_seances_contigues_et_jours_differents_ne_se_genent_pas():
    matin = Plage("matin", 0, 8 * H + 30, 10 * H + 30)
    suite = Plage("suite", 0, 10 * H + 30, 12 * H + 30)  # commence à la fin de la précédente
    mardi = Plage("mardi", 1, 8 * H + 30, 10 * H + 30)

    positions = disposer([matin, suite, mardi], OUVERTURE, FERMETURE).positions

    assert all(p.largeur == 100.0 and p.nb_voies == 1 for p in positions.values())


def test_seance_hors_grille_elargit_l_axe_et_reste_a_la_minute_pres():
    dst = Plage("dst", 0, 8 * H, 12 * H)  # DST de 8 h à 12 h, la grille ouvre à 8 h 30

    disposition = disposer([dst], OUVERTURE, FERMETURE)

    axe = disposition.axe
    assert (axe.debut, axe.fin) == (8 * H, 18 * H)
    assert axe.graduations[0] == 8 * H and axe.graduations[-1] == 18 * H
    position = disposition.positions["dst"]
    assert position.haut == 0.0
    assert position.hauteur == pytest.approx(240 * 100 / 600, abs=1e-3)


def test_axe_arrondi_a_l_heure_et_positions_a_la_minute():
    axe = calculer_axe(OUVERTURE, FERMETURE, [Plage("tard", 2, 17 * H, 19 * H + 15)])
    assert (axe.debut, axe.fin) == (8 * H, 20 * H)
    assert axe.position(8 * H + 30) == pytest.approx(30 * 100 / 720, abs=1e-3)


def test_donnees_incoherentes_refusees():
    with pytest.raises(ErreurDonneesInvalides):
        disposer([Plage("x", 0, 600, 600)], OUVERTURE, FERMETURE)
    with pytest.raises(ErreurDonneesInvalides):
        disposer([Plage("x", 0, 600, 660), Plage("x", 1, 600, 660)], OUVERTURE, FERMETURE)
    with pytest.raises(ErreurDonneesInvalides):
        calculer_axe(18 * H, 8 * H)


def test_mises_en_forme_francaises():
    assert presentation.duree_secondes(0.2) == "moins d'une seconde"
    assert presentation.duree_secondes(65) == "1 min 05 s"
    assert presentation.duree_secondes(300) == "5 minutes"
    assert presentation.duree_secondes(60) == "1 minute"
    assert presentation.plages_de_semaines([2, 3, 4, 5, 8, 10, 11]) == "2 à 5, 8 et 10 à 11"
    assert presentation.texte_semaines([1, 3], 18) == "Semaines 1 et 3 (2 sur 18)"
    assert presentation.pourcentage(87.5) == "87,5 %"
    assert presentation.pourcentage(100.0) == "100 %"
    assert presentation.indice_couleur("IM.1101") == presentation.indice_couleur("IM.1101")
    assert 0 <= presentation.indice_couleur("II.1101") < presentation.NB_COULEURS
    assert presentation.couleur_valide("#2f6fad") == "#2f6fad"
    assert presentation.couleur_valide("red;background:url(x)") is None


def test_nombre_de_voies_par_jour_pour_elargir_les_colonnes():
    lundi = [Plage(f"td-{i}", 0, 8 * H + 30, 10 * H + 30) for i in range(9)]  # 9 classes en TD
    mardi = [Plage("cm", 1, 8 * H + 30, 10 * H + 30)]

    disposition = disposer([*lundi, *mardi], OUVERTURE, FERMETURE)

    assert disposition.voies_de(0) == 9
    assert disposition.voies_de(1) == 1
    assert disposition.voies_de(4) == 1  # jour sans séance
    assert all(
        disposition.positions[p.cle].largeur == pytest.approx(100 / 9, abs=1e-3) for p in lundi
    )
