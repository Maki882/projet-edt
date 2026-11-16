"""Scénario a : parcours complet d'un utilisateur dans l'application web."""

from __future__ import annotations

import csv
import io

import pytest
from tests.outils.parcours.aides import DELAI, compter

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services import generation
from edt.services.verification import verifier_occurrences_version, verifier_version


@pytest.mark.integration
def test_parcours_web_du_calcul_a_l_export(jeu_minimal, client):
    """Scénario : un responsable génère l'emploi du temps depuis le site, le consulte et l'exporte.

    Étapes :
    1. Il envoie le formulaire « Générer » (POST /calculs) ; le vrai solveur calcule.
    2. La page des versions montre une version « Active ».
    3. La page « Emploi du temps » affiche des séances dans la grille.
    4. Le vérificateur ne trouve aucune anomalie bloquante (semaine type et séances datées).
    5. Les exports CSV et iCal contiennent autant de séances que la base.

    Ce que cela prouve : toute la chaîne web -> services -> solveur -> vérificateur -> base
    fonctionne ensemble, et ce qui est exporté correspond à ce qui est enregistré.
    """
    semestre = jeu_minimal.semestre_id
    envoi = client.post("/calculs", data={"semestre_id": semestre}, follow_redirects=False)
    assert envoi.status_code == 303, envoi.text  # redirection vers la page du calcul
    calcul_id = int(envoi.headers["location"].rsplit("/", 1)[1])
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi, suivi.message
    version = suivi.version_id

    versions = client.get(f"/versions?semestre_id={semestre}")
    assert versions.status_code == 200 and ">Active</span>" in versions.text
    grille = client.get(f"/emploi-du-temps?version_id={version}")
    assert grille.status_code == 200 and 'class="edt-bloc' in grille.text

    with session_base() as session:
        assert verifier_version(session, version).valide
        assert verifier_occurrences_version(session, version).valide

    nb_seances = compter(m.SeanceHebdo, m.SeanceHebdo.version_id == version)
    texte_csv = client.get(f"/export/emploi-du-temps.csv?version_id={version}").text
    lignes = list(csv.DictReader(io.StringIO(texte_csv.lstrip("﻿")), delimiter=";"))
    texte_ical = client.get(f"/export/emploi-du-temps.ics?version_id={version}").text
    assert nb_seances > 0
    assert len(lignes) == nb_seances
    assert texte_ical.count("BEGIN:VEVENT") == nb_seances
