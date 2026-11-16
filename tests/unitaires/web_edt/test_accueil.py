"""Accueil : la prochaine étape calculée par le tableau de bord, sur une base temporaire."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from edt.db import modeles as m
from edt.db.moteur import obtenir_moteur
from edt.services import etat
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


@pytest.mark.cas_limite
def test_le_schema_n_est_recompare_au_modele_que_s_il_change(base, monkeypatch):
    """Cas : l'accueil et `/sante` diagnostiquent la base à chaque affichage ; puis un autre
    processus modifie une table.

    Risque : la comparaison complète du schéma au modèle refaite à chaque page (des dizaines
    de millisecondes), ou, mémorisée, un schéma périmé qui ne serait plus signalé.
    """
    appels = []
    comparer = etat.ecarts_schema
    monkeypatch.setattr(etat, "ecarts_schema", lambda moteur: appels.append(1) or comparer(moteur))

    assert etat.diagnostiquer_base().initialisee
    assert etat.diagnostiquer_base().initialisee
    assert len(appels) == 1

    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql("ALTER TABLE besoin ADD COLUMN ancien_champ VARCHAR(10)")
    perime = etat.diagnostiquer_base()
    assert len(appels) == 2 and perime.schema_perime and not perime.initialisee
