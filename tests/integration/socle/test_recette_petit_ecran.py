"""Recette de la consultation sur téléphone : ce qu'un test par HTTP peut prouver.

Le client de test lit le HTML et les feuilles de style servies, sans moteur de
rendu ni JavaScript. Il prouve que chaque page principale se déclare adaptée à
la largeur de l'appareil, que les feuilles servies contiennent les règles pour
petits écrans prévues par le code, et que les pages portent les éléments que
ces règles réarrangent. Le rendu réel (menu repliable, sous-menu qui
défile, grille lisible à la largeur d'un téléphone) reste un essai manuel dans
un navigateur ou sur un téléphone.
"""

from __future__ import annotations

import re

from tests.outils.verification.versions import enregistrer_version

from edt.db.moteur import session_base

VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'

ECRANS_DE_DONNEES = ("/salles", "/enseignants")


def _regles_petit_ecran(feuille: str, seuil: str) -> str:
    """Le corps du bloc « @media (max-width: <seuil>) » d'une feuille de style."""
    debut = feuille.index(f"@media (max-width: {seuil}) {{")
    profondeur = 0
    for position in range(feuille.index("{", debut), len(feuille)):
        profondeur += {"{": 1, "}": -1}.get(feuille[position], 0)
        if profondeur == 0:
            return feuille[debut:position]
    raise AssertionError(f"bloc @media (max-width: {seuil}) non refermé")


def _onglets(page: str) -> list[str]:
    barre = re.search(r'<nav class="onglets".*?</nav>', page, re.S).group(0)
    return re.findall(r'<li class="onglet[^"]*">\s*<a href="([^"]+)"', barre)


def test_pages_principales_adaptees_a_la_largeur_du_telephone(jeu_minimal, client):
    """L'utilisateur ouvre chaque onglet et deux écrans de données sur son téléphone.

    Les six pages des onglets et les écrans « Salles » et « Enseignants » répondent et
    portent la balise viewport, sans laquelle un téléphone affiche la page de bureau
    réduite au lieu d'appliquer les règles pour petits écrans.
    """
    with session_base() as session:
        enregistrer_version(session, jeu_minimal.semestre_id)
    onglets = _onglets(client.get("/").text)
    assert len(onglets) == 6, onglets

    for adresse in (*onglets, *ECRANS_DE_DONNEES):
        page = client.get(adresse)
        assert page.status_code == 200, adresse
        assert VIEWPORT in page.text, adresse
        assert '<link rel="stylesheet" href="/static/css/app.css' in page.text, adresse


def test_regles_pour_petits_ecrans_servies_et_elements_presents(jeu_minimal, client):
    """L'utilisateur consulte le menu et la grille de l'emploi du temps sur un téléphone.

    Jusqu'à 58rem, le bouton Menu replie les onglets et la sous-navigation
    devient une bande défilante. Jusqu'à 64rem, le panneau de la séance passe
    sous la grille. Les pages et les feuilles servies contiennent les commandes
    et les règles correspondantes.
    """
    with session_base() as session:
        version = enregistrer_version(session, jeu_minimal.semestre_id)

    app = client.get("/static/css/app.css")
    assert app.status_code == 200
    menu = _regles_petit_ecran(app.text, "58rem")
    for regle in (
        '.bouton-mobile:not([hidden])[aria-expanded="false"] + :is(.onglets, .edt-barre-outils)',
        "display: none",
        ".onglets > ul",
        "grid-template-columns: repeat(2, minmax(0, 1fr))",
        ".sous-navigation",
        "overflow-x: auto",
    ):
        assert regle in menu, regle
    edt = client.get("/static/css/edt.css")
    assert edt.status_code == 200
    grille = _regles_petit_ecran(edt.text, "64rem")
    for regle in (".edt-mise-en-page", "flex-direction: column", ".edt-panneau"):
        assert regle in grille, regle

    donnees = client.get("/salles").text
    assert 'aria-controls="navigation-principale" aria-expanded="false"' in donnees
    assert '<nav class="onglets" id="navigation-principale"' in donnees
    assert '<nav class="sous-navigation"' in donnees
    emploi = client.get("/emploi-du-temps", params={"version_id": version}).text
    assert '<link rel="stylesheet" href="/static/css/edt.css' in emploi
    assert 'aria-controls="edt-filtres" aria-expanded="false"' in emploi
    assert '<form class="edt-barre-outils" id="edt-filtres"' in emploi
    for classe in ("edt-mise-en-page", "edt-grille", "edt-panneau"):
        assert f'class="{classe}' in emploi, classe
