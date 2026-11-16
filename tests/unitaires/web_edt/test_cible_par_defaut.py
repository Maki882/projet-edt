"""Emploi du temps ouvert par défaut : une classe dont la grille est remplie."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.emploi_du_temps import vues
from edt.services.instanciation import instancier_version
from edt.services.jeux import charger_jeu_fictif

DATES_DE_DEMONSTRATION = (dt.date(2026, 11, 16), dt.date(2026, 12, 14))


@pytest.mark.cas_limite
def test_classe_par_defaut_du_jeu_moyen_remplie_aux_dates_de_demonstration(base):
    """Cas : grille ouverte sans choix sur l'établissement fictif « moyen » (emploi du temps de
    référence et séances datées), en semaine datée le 16/11/2026 et le 14/12/2026.

    Risque : la page s'ouvre sur la première classe par ordre alphabétique, une classe
    d'apprentis presque vide (cinq séances dans la semaine) : la démonstration commence sur
    une grille creuse. Les classes A3-G1 à A3-G6 en montrent sept chaque semaine (leurs
    séances et celles de la promotion ; celles des demi-groupes sont repliées par défaut).
    """
    with session_base() as session:
        charger_jeu_fictif(session, "moyen", enregistrer_reference=True)
        session.commit()
    with session_base() as session:
        version = session.scalar(
            select(m.VersionEdt)
            .join(m.Semestre, m.Semestre.id == m.VersionEdt.semestre_id)
            .where(m.Semestre.code == "S1")
        )
        instancier_version(session, version.id)
        session.commit()
        annee = session.get(m.Semestre, version.semestre_id).annee_scolaire_id

        cible = vues.cible_par_defaut(session, annee, version.id)

        groupe = session.get(m.Groupe, cible.id)
        assert groupe.nature == "classe" and "apprenti" not in groupe.libelle.lower()
        for date in DATES_DE_DEMONSTRATION:
            vue = vues.construire_vue(
                session,
                version_id=version.id,
                cible=cible,
                couche=vues.Couche.DATE,
                date=date,
                verifier=False,
            )
            assert len(vue.blocs) >= 7, (groupe.code, date, len(vue.blocs))
