"""Accès au site : jetons signés, mots de passe, droits de la consultation, menu du rôle."""

from __future__ import annotations

import dataclasses

import pytest

from edt.config import DEFAUTS, charger_configuration
from edt.web.acces import (
    DUREE_SESSION_S,
    ROLE_CONSULTATION,
    ROLE_GESTION,
    adresse_de_retour,
    autorise_en_consultation,
    creer_jeton,
    lire_jeton,
    menu_du_role,
    role_du_mot_de_passe,
)
from edt.web.menu import (
    SECTION_EMPLOI_DU_TEMPS,
    SECTION_REFERENTIELS,
    EntreeMenu,
    construire_menu,
)

MAINTENANT = 1_800_000_000.0


@pytest.fixture
def config():
    """Configuration avec les deux mots de passe (valeurs d'essai, pas des secrets)."""
    return charger_configuration(
        {
            **DEFAUTS,
            "EDT_MDP_GESTION": "essai-gestion",
            "EDT_MDP_CONSULTATION": "essai-consultation",
        }
    )


@pytest.mark.parametrize("role", [ROLE_GESTION, ROLE_CONSULTATION])
def test_un_jeton_cree_redonne_son_role_jusqu_a_son_expiration(config, role):
    jeton = creer_jeton(config, role, maintenant=MAINTENANT)

    assert lire_jeton(config, jeton, maintenant=MAINTENANT) == role
    assert lire_jeton(config, jeton, maintenant=MAINTENANT + DUREE_SESSION_S) == role
    assert lire_jeton(config, jeton, maintenant=MAINTENANT + DUREE_SESSION_S + 1) is None


@pytest.mark.cas_limite
def test_un_jeton_falsifie_ou_mal_forme_ne_donne_aucun_role(config):
    """Cas : un visiteur fabrique ou retouche son cookie (rôle changé, expiration repoussée).
    Risque : l'accès en consultation se change en gestion sans le mot de passe.
    """
    jeton = creer_jeton(config, ROLE_CONSULTATION, maintenant=MAINTENANT)
    _, expiration, signature = jeton.split(".")

    assert lire_jeton(config, f"gestion.{expiration}.{signature}", maintenant=MAINTENANT) is None
    plus_tard = int(expiration) + 10 * DUREE_SESSION_S
    assert lire_jeton(config, f"consultation.{plus_tard}.{signature}", MAINTENANT) is None
    for faux in ("", "gestion", "a.b", "a.b.c.d", f"gestion.{expiration}.", jeton + "0"):
        assert lire_jeton(config, faux, maintenant=MAINTENANT) is None


def test_changer_un_mot_de_passe_deconnecte_tout_le_monde(config):
    jeton = creer_jeton(config, ROLE_GESTION, maintenant=MAINTENANT)
    nouvelle = dataclasses.replace(config, mdp_gestion="nouveau-mot-de-passe")

    assert lire_jeton(nouvelle, jeton, maintenant=MAINTENANT) is None


def test_un_jeton_de_consultation_ne_vaut_plus_sans_mot_de_passe_de_consultation(config):
    jeton = creer_jeton(config, ROLE_CONSULTATION, maintenant=MAINTENANT)
    # Même clé de signature, mais la consultation n'est plus ouverte.
    sans = dataclasses.replace(config, mdp_consultation="")
    jeton_sans = creer_jeton(sans, ROLE_CONSULTATION, maintenant=MAINTENANT)

    assert lire_jeton(sans, jeton, maintenant=MAINTENANT) is None
    assert lire_jeton(sans, jeton_sans, maintenant=MAINTENANT) is None


def test_le_mot_de_passe_decide_du_role(config):
    assert role_du_mot_de_passe(config, "essai-gestion") == ROLE_GESTION
    assert role_du_mot_de_passe(config, "essai-consultation") == ROLE_CONSULTATION
    for faux in ("", "essai-gestio", "essai-gestion ", "ESSAI-GESTION"):
        assert role_du_mot_de_passe(config, faux) is None


def test_sans_mot_de_passe_de_consultation_un_mot_de_passe_vide_n_ouvre_rien(config):
    sans = dataclasses.replace(config, mdp_consultation="")

    assert role_du_mot_de_passe(sans, "") is None
    assert role_du_mot_de_passe(sans, "essai-consultation") is None


@pytest.mark.parametrize(
    ("methode", "chemin", "attendu"),
    [
        ("GET", "/emploi-du-temps", True),
        ("HEAD", "/emploi-du-temps", True),
        ("GET", "/emploi-du-temps/seances/3/panneau", True),
        ("GET", "/emploi-du-temps/occurrences/7/panneau", True),
        ("GET", "/export/emploi-du-temps.csv", True),
        ("GET", "/export/emploi-du-temps.ics", True),
        ("GET", "/emploi-du-temps/seances/3/formulaire", False),
        ("POST", "/emploi-du-temps/seances/3/verrou", False),
        ("POST", "/emploi-du-temps/occurrences/7/deplacer", False),
        ("GET", "/emploi-du-temps-bis", False),
        ("GET", "/export/donnees.xlsx", False),
        ("GET", "/export", False),
        ("GET", "/", False),
        ("GET", "/calculs", False),
        ("GET", "/salles", False),
        ("POST", "/assistant/messages", False),
        ("GET", "/tests/unitaires", False),
    ],
)
def test_la_consultation_ne_sert_que_la_lecture_de_l_emploi_du_temps(methode, chemin, attendu):
    assert autorise_en_consultation(methode, chemin) is attendu


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("suite", "attendu"),
    [
        ("/emploi-du-temps?groupe=3", "/emploi-du-temps?groupe=3"),
        ("", "/"),
        (None, "/"),
        ("https://exemple.org/", "/"),
        ("//exemple.org/", "/"),
        ("/\\exemple.org", "/"),
        ("/emploi\r\nSet-Cookie: x", "/"),
        ("emploi-du-temps", "/"),
    ],
)
def test_apres_connexion_on_ne_revient_que_sur_une_page_du_site(suite, attendu):
    """Cas : l'adresse de retour de la connexion vient de l'adresse demandée (« suite »).
    Risque : un lien piégé renvoie, après la connexion, vers un autre site ou injecte un en-tête.
    """
    assert adresse_de_retour(suite) == attendu


def test_le_menu_de_la_consultation_ne_garde_que_les_pages_permises():
    menu = construire_menu(
        [
            EntreeMenu("Accueil", "/", ordre=0),
            EntreeMenu("Salles", "/salles", section=SECTION_REFERENTIELS, ordre=120),
            EntreeMenu(
                "Emploi du temps", "/emploi-du-temps", section=SECTION_EMPLOI_DU_TEMPS, ordre=300
            ),
        ]
    )

    complet = menu_du_role(menu, ROLE_GESTION)
    consultation = menu_du_role(menu, ROLE_CONSULTATION)

    assert complet == menu
    assert [e.url for onglet in consultation for e in onglet.entrees] == ["/emploi-du-temps"]
