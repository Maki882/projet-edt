"""Mise en page commune : cinq onglets, sous-navigation, onglet vide masqué, panneau Assistant."""

from __future__ import annotations

import logging
import re

from edt.web.gabarits import gabarits
from edt.web.menu import (
    GROUPE_JEUX,
    ONGLET_A_TRAITER,
    ONGLET_DONNEES,
    ONGLET_GENERER,
    SECTION_A_TRAITER,
    SECTION_CONFIGURATION,
    SECTION_DONNEES,
    SECTION_EMPLOI_DU_TEMPS,
    SECTION_GENERER,
    SECTION_PLANIFICATION,
    SECTION_REFERENTIELS,
    EntreeMenu,
    construire_menu,
    onglet_actif,
)


def test_onglets_dans_l_ordre_fixe_et_onglet_vide_omis():
    menu = construire_menu(
        [
            EntreeMenu("À traiter", "/a-traiter", section=SECTION_A_TRAITER, ordre=700),
            EntreeMenu("Vues", "/emploi-du-temps", section=SECTION_EMPLOI_DU_TEMPS, ordre=300),
            EntreeMenu("Salles", "/salles", section=SECTION_REFERENTIELS, ordre=110),
            EntreeMenu("Accueil", "/", ordre=0),
        ]
    )
    assert [onglet.titre for onglet in menu] == [
        "Accueil",
        "Données",
        "Emploi du temps",
        "À traiter",
    ]  # « Générer » n'a aucune entrée : il n'est pas affiché


def test_groupes_de_l_onglet_donnees_et_section_inconnue(caplog):
    with caplog.at_level(logging.WARNING, logger="edt.web.menu"):
        menu = construire_menu(
            [
                EntreeMenu("Paramètres", "/parametres", section=SECTION_CONFIGURATION, ordre=520),
                EntreeMenu("Import", "/import", section=SECTION_DONNEES, ordre=400),
                EntreeMenu("Jeux", "/jeux", section=SECTION_DONNEES, ordre=480, groupe=GROUPE_JEUX),
                EntreeMenu("Besoins", "/besoins", section=SECTION_PLANIFICATION, ordre=200),
                EntreeMenu("Salles", "/salles", section=SECTION_REFERENTIELS, ordre=110),
                EntreeMenu("Mystère", "/mystere", section="Section inconnue", ordre=900),
                EntreeMenu("Lancer", "/calculs", section=SECTION_GENERER, ordre=600),
            ]
        )
    donnees = next(onglet for onglet in menu if onglet.titre == ONGLET_DONNEES)
    assert [groupe.titre for groupe in donnees.groupes] == [
        "Établissement",
        "Besoins et disponibilités",
        "Règles et calendrier",
        "Import et export",
        "Jeux de données",
        "Section inconnue",
    ]
    assert "Section inconnue" in caplog.text  # rangée quand même, et signalée
    assert onglet_actif(menu, "/salles/12").titre == ONGLET_DONNEES
    assert onglet_actif(menu, "/calculs").titre == ONGLET_GENERER
    assert onglet_actif(menu, "/n-existe-pas") is None
    assert ONGLET_A_TRAITER not in [onglet.titre for onglet in menu]


def test_page_donnees_affiche_onglets_et_sous_navigation(site):
    page = site.get("/salles").text

    onglets = re.search(r'<nav class="onglets".*?</nav>', page, re.S).group(0)
    titres = re.findall(r'<li class="onglet[^"]*">\s*<a href="[^"]*"[^>]*>([^<]+)</a>', onglets)
    assert titres[:2] == ["Accueil", "Données"]
    assert 'href="/campus" class="actif" aria-current="true">Données</a>' in page

    sous_navigation = re.search(r'<nav class="sous-navigation".*?</nav>', page, re.S).group(0)
    for titre in ("Établissement", "Besoins et disponibilités", "Règles et calendrier"):
        assert f"<h2>{titre}</h2>" in sous_navigation
    assert "Jeux de données" in sous_navigation
    assert '<a href="/salles" class="actif" aria-current="page">Salles</a>' in sous_navigation


def test_accueil_sans_sous_navigation_mais_avec_menu_deroulant(site):
    page = site.get("/").text
    assert '<nav class="sous-navigation"' not in page
    assert 'class="menu-deroulant"' in page
    assert '<a href="/besoins">Besoins</a>' in page  # toutes les pages restent accessibles


def test_panneau_assistant_present_sur_toutes_les_pages(site):
    for adresse in ("/", "/salles", "/parametres", "/jeux", "/n-existe-pas"):
        page = site.get(adresse).text
        assert 'id="bouton-assistant"' in page and 'aria-controls="assistant"' in page
        assert '<aside class="assistant" id="assistant"' in page
        assert "Bientôt" in page and "langage courant" in page
        assert re.search(r'<textarea id="assistant-question"[^>]*disabled', page)
    assert "Données fictives" not in page  # plus de bandeau sur les données


def test_blocs_styles_et_scripts_du_gabarit_de_base():
    enfant = gabarits.env.from_string(
        '{% extends "base.html" %}'
        '{% block styles %}<link rel="stylesheet" href="/static/ecran.css">{% endblock %}'
        '{% block scripts %}<script src="/static/ecran.js"></script>{% endblock %}'
    )
    page = enfant.render(menu=(), chemin="/", messages=[], id_requete=None)
    assert page.index("/static/ecran.css") < page.index("</head>")
    assert page.index("/static/ecran.js") > page.index('id="assistant"')


def test_cinq_onglets_de_l_application_et_import_export_dans_donnees(site):
    page = site.get("/besoins").text

    onglets = re.search(r'<nav class="onglets".*?</nav>', page, re.S).group(0)
    titres = re.findall(r'<li class="onglet[^"]*">\s*<a href="[^"]*"[^>]*>([^<]+)', onglets)
    assert [t.strip() for t in titres] == [
        "Accueil",
        "Données",
        "Générer",
        "Emploi du temps",
        "À traiter",
    ]
    sous_navigation = re.search(r'<nav class="sous-navigation".*?</nav>', page, re.S).group(0)
    assert "<h2>Import et export</h2>" in sous_navigation
    assert '<a href="/import">Import</a>' in sous_navigation
    assert '<a href="/export">Export</a>' in sous_navigation


def test_compteur_charge_dans_l_onglet_a_traiter(site):
    page = site.get("/salles").text
    onglet = re.search(r'<a href="/a-traiter"[^>]*>À traiter(.*?)</a>', page, re.S)
    assert onglet is not None
    compteur = onglet.group(1)
    assert 'hx-get="/a-traiter/compteur"' in compteur and 'hx-trigger="load"' in compteur
    assert 'hx-swap="outerHTML"' in compteur and "data-erreur-silencieuse" in compteur
    # Sans JavaScript, la balise reste vide : l'onglet est un lien ordinaire.
    assert re.search(r"<span [^>]*hx-get=\"/a-traiter/compteur\"[^>]*></span>", compteur)
    assert page.count('hx-get="/a-traiter/compteur"') == 1  # seulement dans cet onglet
