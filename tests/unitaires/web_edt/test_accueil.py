"""Accueil : la prochaine étape calculée par le tableau de bord, sur une base temporaire."""

from __future__ import annotations

from sqlalchemy import delete, select

from edt.db import modeles as m
from edt.services.emploi_du_temps.tableau_de_bord import tableau_de_bord


def test_donnees_incompletes_l_etape_mene_a_l_ecran_qui_manque(session, aujourdhui):
    type_salle = session.scalars(select(m.TypeSalle)).first()
    campus = m.Campus(code="NDC", nom="Notre-Dame-des-Champs")
    session.add(m.Salle(code="N001", campus=campus, capacite=30, type_salle=type_salle))
    session.flush()

    etape = tableau_de_bord(session, aujourdhui).prochaine_etape

    assert (etape.lien, etape.libelle_lien) == ("/groupes", "Saisir les groupes")
    assert "les groupes manquent" in etape.phrase


def test_sans_besoin_l_etape_mene_aux_besoins(jeu_minimal, session, aujourdhui_fige):
    session.execute(delete(m.Besoin))
    session.flush()

    etape = tableau_de_bord(session, aujourdhui_fige).prochaine_etape

    assert etape.lien == "/besoins"
    assert ("/import", "Importer des besoins") in etape.autres_liens
