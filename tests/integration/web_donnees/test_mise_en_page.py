"""Mise en page commune : cinq onglets, sous-navigation, onglet vide masqué, panneau Assistant."""

from __future__ import annotations

import re


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
