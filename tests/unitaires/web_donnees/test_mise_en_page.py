"""Mise en page commune, sans client web : construction du menu à onglets et blocs du gabarit de base."""

from __future__ import annotations

import logging

import pytest
from jinja2 import BaseLoader, DictLoader, TemplateNotFound

from edt.web.gabarits import gabarits
from edt.web.menu import (
    GROUPE_JEUX,
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
            EntreeMenu("Points à traiter", "/a-traiter", section=SECTION_A_TRAITER, ordre=350),
            EntreeMenu("Vues", "/emploi-du-temps", section=SECTION_EMPLOI_DU_TEMPS, ordre=300),
            EntreeMenu("Salles", "/salles", section=SECTION_REFERENTIELS, ordre=110),
            EntreeMenu("Accueil", "/", ordre=0),
        ]
    )
    assert [onglet.titre for onglet in menu] == [
        "Accueil",
        "Données",
        "Générer",
        "Emploi du temps",
    ]  # « Points à traiter » est rangé dans « Générer » ; les onglets de tests, vides, sont omis


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
                EntreeMenu("Lancer", "/calculs", section=SECTION_GENERER, ordre=250),
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
    assert "Points à traiter" not in [onglet.titre for onglet in menu]


PANNEAU = "assistant/_panneau.html"
ENFANT = (
    '{% extends "base.html" %}'
    '{% block styles %}<link rel="stylesheet" href="/static/ecran.css">{% endblock %}'
    '{% block scripts %}<script src="/static/ecran.js"></script>{% endblock %}'
)


class _SansPanneau(BaseLoader):
    """Les gabarits de l'application, moins le panneau de l'assistant."""

    def get_source(self, environment, template):
        if template == PANNEAU:
            raise TemplateNotFound(template)
        return gabarits.env.loader.get_source(environment, template)


class _AvecPanneau(BaseLoader):
    """Les gabarits de l'application, avec un panneau de l'assistant d'essai."""

    def get_source(self, environment, template):
        if template == PANNEAU:
            return DictLoader({PANNEAU: '<aside id="assistant"></aside>'}).get_source(
                environment, template
            )
        return gabarits.env.loader.get_source(environment, template)


def _rendre(chargeur: BaseLoader) -> str:
    environnement = gabarits.env.overlay(loader=chargeur, cache_size=0)
    return environnement.from_string(ENFANT).render(
        menu=(), chemin="/", messages=[], id_requete=None
    )


def test_blocs_styles_et_scripts_du_gabarit_de_base_et_panneau_de_l_assistant():
    page = _rendre(_AvecPanneau())
    assert page.index("/static/ecran.css") < page.index("</head>")
    # Le panneau est inclus après le pied de page, avant les scripts propres à l'écran.
    assert page.index("</footer>") < page.index('<aside id="assistant">')
    assert page.index('<aside id="assistant">') < page.index("/static/ecran.js")


@pytest.mark.cas_limite
def test_gabarit_de_base_sans_panneau_de_l_assistant():
    """Cas : le gabarit du panneau de l'assistant est absent (fonction retirée du déploiement).

    Risque : toutes les pages en erreur 500 « gabarit introuvable ».
    """
    page = _rendre(_SansPanneau())
    assert "</footer>" in page and "/static/ecran.js" in page and 'id="assistant"' not in page
