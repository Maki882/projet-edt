"""Scénario c : exporter les données d'un établissement puis les réimporter dans une base neuve."""

from __future__ import annotations

import pytest

from edt.db import modeles as m
from edt.db.initialisation import initialiser_base
from edt.db.moteur import session_base
from edt.services import echange
from edt.services.echange import FichierEnvoye, OptionsImport

from .conftest import compter

TABLES = (m.Salle, m.Enseignant, m.Groupe, m.Matiere, m.Besoin)


@pytest.mark.integration
def test_export_puis_import_dans_une_base_neuve(jeu_minimal, client, environnement, aujourdhui):
    """Scénario : on déménage les données d'une installation vers une autre.

    Étapes :
    1. Télécharger le classeur des données depuis le site (GET /export/donnees.xlsx).
    2. Créer une seconde base, vide, à côté de la première.
    3. Y importer le classeur (le même service que l'écran « Import »).
    4. Comparer le nombre de salles, enseignants, groupes, matières et besoins.

    Ce que cela prouve : le format d'échange est réellement réimportable ; rien ne se perd
    entre l'export web, la lecture du classeur, la validation et l'écriture en base.
    """
    reponse = client.get("/export/donnees.xlsx")
    assert reponse.status_code == 200 and len(reponse.content) > 0

    neuve = f"sqlite:///{(environnement / 'neuve.db').as_posix()}"
    initialiser_base(aujourdhui=aujourdhui, url=neuve)
    with session_base(neuve) as session:
        rapport = echange.appliquer_import(
            session, [FichierEnvoye("donnees.xlsx", reponse.content)], OptionsImport()
        )
    assert not rapport.a_des_rejets, rapport.signalements

    for table in TABLES:
        assert compter(table, url=neuve) == compter(table), table.__name__
    assert compter(m.Besoin, url=neuve) == jeu_minimal.nb_besoins
