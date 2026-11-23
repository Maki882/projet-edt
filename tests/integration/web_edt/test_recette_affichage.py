"""Adaptation des écrans à un petit écran, vérifiable sans navigateur.

Le client HTTP n'exécute ni CSS ni JavaScript : le rendu réel au téléphone ne se vérifie
qu'à l'écran. Ce test vérifie ce dont ce rendu dépend : la largeur de la page suit celle de
l'appareil, et les feuilles de style servies contiennent leurs règles pour les écrans étroits.
"""

from __future__ import annotations

import re


def _regle_media(feuille: str, condition: str) -> str:
    """Le contenu du bloc `@media <condition> { … }` d'une feuille de style (accolades comprises)."""
    debut = feuille.index(f"@media {condition}")
    profondeur = 0
    for position in range(feuille.index("{", debut), len(feuille)):
        profondeur += {"{": 1, "}": -1}.get(feuille[position], 0)
        if profondeur == 0:
            return feuille[debut : position + 1]
    raise AssertionError(f"bloc @media {condition} non fermé")


def test_ecrans_prevus_pour_un_telephone(version_generee, client):
    """L'utilisateur ouvre l'accueil, Générer, l'emploi du temps et les points à traiter sur
    un téléphone : chaque page se met à la largeur de l'appareil, et les feuilles de style
    qu'elle charge prévoient l'écran étroit (menu repliable, grille qui défile dans son
    cadre, panneau de séance sous la grille)."""
    for adresse in ("/", "/calculs", "/emploi-du-temps", "/a-traiter"):
        page = client.get(adresse).text
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page, (
            adresse
        )
        assert re.search(r'href="/static/css/app\.css\?v=[^"]+"', page), adresse
        if adresse != "/":
            assert re.search(r'href="/static/css/edt\.css\?v=[^"]+"', page), adresse

    app = client.get("/static/css/app.css")
    assert app.status_code == 200 and "text/css" in app.headers["content-type"]
    menu = _regle_media(app.text, "(max-width: 58rem)")
    assert ".bouton-mobile" in menu
    assert 'aria-expanded="false"' in menu

    edt = client.get("/static/css/edt.css")
    assert edt.status_code == 200
    filtres = _regle_media(edt.text, "screen and (max-width: 58rem)")
    assert re.search(r"\.edt-barre-outils\s*\{[^}]*display: grid", filtres)
    etroit = _regle_media(edt.text, "(max-width: 64rem)")
    mise_en_page = re.search(r"\.edt-mise-en-page\s*\{([^}]*)\}", etroit)
    assert mise_en_page is not None
    assert "flex-direction: column" in mise_en_page.group(1)
    assert "align-items: stretch" in mise_en_page.group(1)
    assert re.search(r"\.edt-panneau\s*\{[^}]*width: 100%", etroit)
