"""Entrées du menu de navigation, déclarées par les modules de routes.

Chaque module de `edt.web.routes` peut exposer `MENU`, une liste
d'`EntreeMenu`. La navigation du gabarit de base est construite à partir de
toutes les entrées, regroupées par section.

Sections et plages d'ordre convenues (pour que les écrans des différents
lots se rangent sans se concerter) :

| Section           | Ordre     | Contenu                                         |
|-------------------|-----------|-------------------------------------------------|
| (aucune)          | 0 - 99    | accueil                                         |
| Référentiels      | 100 - 199 | campus, salles, enseignants, groupes, matières  |
| Planification     | 200 - 299 | besoins, indisponibilités, calendrier, calcul   |
| Emploi du temps   | 300 - 399 | vues, anomalies, séances à programmer           |
| Données           | 400 - 499 | import, export, jeux de données                 |
| Configuration     | 500 - 599 | paramètres, grille, listes configurables        |
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

SECTION_REFERENTIELS = "Référentiels"
SECTION_PLANIFICATION = "Planification"
SECTION_EMPLOI_DU_TEMPS = "Emploi du temps"
SECTION_DONNEES = "Données"
SECTION_CONFIGURATION = "Configuration"


@dataclass(frozen=True)
class EntreeMenu:
    """Un lien du menu.

    - `libelle` : texte affiché ;
    - `url` : chemin absolu de la page, par exemple `/salles` ;
    - `section` : titre du groupe dans le menu (vide = en tête, sans titre) ;
    - `ordre` : position ; les sections sont triées par leur plus petit ordre.
    """

    libelle: str
    url: str
    section: str = ""
    ordre: int = 100

    def est_active(self, chemin: str) -> bool:
        """Vrai si la page affichée (`chemin`) est cette entrée ou l'une de ses sous-pages."""
        if self.url == "/":
            return chemin == "/"
        return chemin == self.url or chemin.startswith(self.url.rstrip("/") + "/")


@dataclass(frozen=True)
class SectionMenu:
    """Un groupe d'entrées du menu, prêt à afficher."""

    titre: str
    entrees: tuple[EntreeMenu, ...]


def construire_menu(entrees: Iterable[EntreeMenu]) -> tuple[SectionMenu, ...]:
    """Regroupe les entrées par section, triées par ordre puis par libellé."""
    par_section: dict[str, list[EntreeMenu]] = {}
    for entree in entrees:
        par_section.setdefault(entree.section, []).append(entree)
    sections = [
        SectionMenu(titre, tuple(sorted(groupe, key=lambda e: (e.ordre, e.libelle))))
        for titre, groupe in par_section.items()
    ]
    return tuple(sorted(sections, key=lambda s: (s.entrees[0].ordre, s.titre)))
