"""Scénario f : un établissement fictif réaliste (jeu « petit ») est calculé et vérifié."""

from __future__ import annotations

import pytest
from tests.outils.parcours.aides import calculer, compter

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services import jeux
from edt.services.parametres import ecrire_parametre
from edt.services.verification import verifier_occurrences_version, verifier_version


@pytest.mark.integration
def test_jeu_petit_calcule_et_verifie(base):
    """Scénario : le jeu fictif « petit » (une promotion, deux classes) est chargé puis calculé.

    Étapes :
    1. Charger le jeu avec le service utilisé par l'écran « Jeux de démonstration ».
    2. Lancer un vrai calcul sur chacun de ses semestres.
    3. Vérifier la semaine type et les séances datées : aucune anomalie bloquante.

    Ce que cela prouve : le générateur de données fictives, le solveur et le vérificateur
    s'accordent sur des données plus riches que le jeu minimal (deux sites, salles spécialisées,
    vacataires, absences d'enseignants, partiels).
    """
    with session_base() as session:
        rapport = jeux.charger_jeu_fictif(session, "petit", graine=0)
    with session_base() as session:
        # Un emploi du temps valide suffit, pas le meilleur : pas d'optimisation (plus court).
        ecrire_parametre(session, "objectif_actif", False)
    assert rapport.semestres and compter(m.Besoin) == rapport.nb_besoins

    for semestre_id in rapport.semestres.values():
        version = calculer(semestre_id).version_id
        assert compter(m.SeanceHebdo, m.SeanceHebdo.version_id == version) > 0
        with session_base() as session:
            semaine_type = verifier_version(session, version)
            datees = verifier_occurrences_version(session, version)
        assert semaine_type.valide, [a.message for a in semaine_type.anomalies if a.bloquante]
        assert datees.valide, [a.message for a in datees.anomalies if a.bloquante]
