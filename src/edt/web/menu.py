"""Entrées du menu de navigation, déclarées par les modules de routes.

Chaque module de `edt.web.routes` peut exposer `MENU`, une liste
d'`EntreeMenu`. La navigation du gabarit de base est construite à partir de
toutes les entrées : **cinq onglets** dans un ordre fixe, chacun regroupant
les entrées de certaines sections.

| Onglet           | Sections rangées dans l'onglet                          |
|------------------|---------------------------------------------------------|
| Accueil          | (aucune section)                                        |
| Données          | Référentiels, Planification, Configuration, Données     |
| Générer          | Générer                                                 |
| Emploi du temps  | Emploi du temps                                         |
| À traiter        | À traiter                                               |

Un onglet sans aucune entrée n'est pas affiché. Dans un onglet, les entrées
sont regroupées sous des titres parlants (`GroupeMenu`), affichés dans la
sous-navigation : pour l'onglet Données, « Établissement » (section
Référentiels), « Besoins et disponibilités » (Planification), « Règles et
calendrier » (Configuration), « Import et export » (Données). Une entrée peut
choisir son groupe avec le champ `groupe` (par exemple « Jeux de données »).

Sections et plages d'ordre convenues (pour que les écrans des différents
lots se rangent sans se concerter) :

| Section           | Ordre     | Contenu                                         |
|-------------------|-----------|-------------------------------------------------|
| (aucune)          | 0 - 99    | accueil                                         |
| Référentiels      | 100 - 199 | campus, salles, enseignants, groupes, matières  |
| Planification     | 200 - 299 | besoins, indisponibilités                       |
| Emploi du temps   | 300 - 399 | vues, versions                                  |
| Données           | 400 - 499 | import, export, jeux de données                 |
| Configuration     | 500 - 599 | calendrier, grille, paramètres                  |
| Générer           | 600 - 699 | lancement et suivi des calculs                  |
| À traiter         | 700 - 799 | anomalies, séances à programmer                 |

Une entrée dont la section n'est rangée dans aucun onglet est placée dans
l'onglet Données (sous un groupe portant le nom de sa section) et le fait est
journalisé : elle reste accessible, et l'anomalie est visible.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

journal = logging.getLogger(__name__)

SECTION_REFERENTIELS = "Référentiels"
SECTION_PLANIFICATION = "Planification"
SECTION_EMPLOI_DU_TEMPS = "Emploi du temps"
SECTION_DONNEES = "Données"
SECTION_CONFIGURATION = "Configuration"
SECTION_GENERER = "Générer"
SECTION_A_TRAITER = "À traiter"

ONGLET_ACCUEIL = "Accueil"
ONGLET_DONNEES = "Données"
ONGLET_GENERER = "Générer"
ONGLET_EMPLOI_DU_TEMPS = "Emploi du temps"
ONGLET_A_TRAITER = "À traiter"

GROUPE_ETABLISSEMENT = "Établissement"
GROUPE_BESOINS = "Besoins et disponibilités"
GROUPE_REGLES = "Règles et calendrier"
GROUPE_IMPORT_EXPORT = "Import et export"
GROUPE_JEUX = "Jeux de données"

ONGLETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (ONGLET_ACCUEIL, ("",)),
    (
        ONGLET_DONNEES,
        (SECTION_REFERENTIELS, SECTION_PLANIFICATION, SECTION_CONFIGURATION, SECTION_DONNEES),
    ),
    (ONGLET_GENERER, (SECTION_GENERER,)),
    (ONGLET_EMPLOI_DU_TEMPS, (SECTION_EMPLOI_DU_TEMPS,)),
    (ONGLET_A_TRAITER, (SECTION_A_TRAITER,)),
)
"""Les cinq onglets, dans leur ordre d'affichage, et les sections qu'ils rangent."""

GROUPE_PAR_SECTION: dict[str, str] = {
    SECTION_REFERENTIELS: GROUPE_ETABLISSEMENT,
    SECTION_PLANIFICATION: GROUPE_BESOINS,
    SECTION_CONFIGURATION: GROUPE_REGLES,
    SECTION_DONNEES: GROUPE_IMPORT_EXPORT,
}
"""Titre de groupe affiché par défaut pour une section (sinon : le nom de la section)."""

ORDRE_DES_GROUPES: tuple[str, ...] = (
    GROUPE_ETABLISSEMENT,
    GROUPE_BESOINS,
    GROUPE_REGLES,
    GROUPE_IMPORT_EXPORT,
    GROUPE_JEUX,
)
"""Ordre fixe des groupes connus ; les autres suivent, par ordre de leur première entrée."""


@dataclass(frozen=True)
class EntreeMenu:
    """Un lien du menu.

    - `libelle` : texte affiché ;
    - `url` : chemin absolu de la page, par exemple `/salles` ;
    - `section` : section de l'entrée, qui décide de son onglet (vide = Accueil) ;
    - `ordre` : position ; les sections sont triées par leur plus petit ordre ;
    - `groupe` : titre du groupe de la sous-navigation (vide = celui de la section).
    """

    libelle: str
    url: str
    section: str = ""
    ordre: int = 100
    groupe: str = ""

    def est_active(self, chemin: str) -> bool:
        """Vrai si la page affichée (`chemin`) est cette entrée ou l'une de ses sous-pages."""
        if self.url == "/":
            return chemin == "/"
        return chemin == self.url or chemin.startswith(self.url.rstrip("/") + "/")

    @property
    def titre_groupe(self) -> str:
        """Titre du groupe de sous-navigation où ranger l'entrée."""
        return self.groupe or GROUPE_PAR_SECTION.get(self.section, self.section)


@dataclass(frozen=True)
class SectionMenu:
    """Un groupe d'entrées du menu, prêt à afficher.

    `titre` est le titre affiché ; `sections` rappelle de quelles sections
    viennent ses entrées (repris dans l'attribut HTML `data-section`).
    """

    titre: str
    entrees: tuple[EntreeMenu, ...]
    sections: tuple[str, ...] = ()


GroupeMenu = SectionMenu
"""Nom d'usage d'un groupe de la sous-navigation."""


@dataclass(frozen=True)
class Onglet:
    """Un onglet de la navigation principale et ses groupes d'entrées."""

    titre: str
    groupes: tuple[SectionMenu, ...]

    @property
    def entrees(self) -> tuple[EntreeMenu, ...]:
        """Toutes les entrées de l'onglet, dans l'ordre d'affichage."""
        return tuple(entree for groupe in self.groupes for entree in groupe.entrees)

    @property
    def url(self) -> str:
        """Adresse de l'onglet : celle de sa première entrée."""
        return self.entrees[0].url

    @property
    def a_sous_navigation(self) -> bool:
        """Vrai si l'onglet a plusieurs entrées : la sous-navigation latérale est affichée."""
        return len(self.entrees) > 1

    @property
    def a_menu_deroulant(self) -> bool:
        """Vrai si le menu déroulant de l'onglet apprend quelque chose (pas pour « Accueil » seul)."""
        return self.a_sous_navigation or self.entrees[0].libelle != self.titre

    def est_actif(self, chemin: str) -> bool:
        """Vrai si la page affichée appartient à l'une des entrées de l'onglet."""
        return any(entree.est_active(chemin) for entree in self.entrees)


def _trier(entrees: Iterable[EntreeMenu]) -> tuple[EntreeMenu, ...]:
    return tuple(sorted(entrees, key=lambda e: (e.ordre, e.libelle)))


def _grouper(entrees: list[EntreeMenu]) -> tuple[SectionMenu, ...]:
    par_titre: dict[str, list[EntreeMenu]] = {}
    for entree in entrees:
        par_titre.setdefault(entree.titre_groupe, []).append(entree)
    groupes = [
        SectionMenu(
            titre,
            _trier(liste),
            tuple(dict.fromkeys(entree.section for entree in liste)),
        )
        for titre, liste in par_titre.items()
    ]

    def cle(groupe: SectionMenu) -> tuple[int, int, str]:
        if groupe.titre in ORDRE_DES_GROUPES:
            return (0, ORDRE_DES_GROUPES.index(groupe.titre), "")
        return (1, groupe.entrees[0].ordre, groupe.titre)

    return tuple(sorted(groupes, key=cle))


def construire_menu(entrees: Iterable[EntreeMenu]) -> tuple[Onglet, ...]:
    """Range les entrées dans les cinq onglets ; les onglets vides sont omis."""
    onglet_de_section = {section: titre for titre, sections in ONGLETS for section in sections}
    par_onglet: dict[str, list[EntreeMenu]] = {titre: [] for titre, _ in ONGLETS}
    for entree in entrees:
        titre = onglet_de_section.get(entree.section)
        if titre is None:
            journal.warning(
                "Entrée de menu « %s » (%s) : section « %s » rangée dans aucun onglet ; "
                "placée dans l'onglet %s (voir edt.web.menu.ONGLETS).",
                entree.libelle,
                entree.url,
                entree.section,
                ONGLET_DONNEES,
            )
            titre = ONGLET_DONNEES
        par_onglet[titre].append(entree)
    return tuple(Onglet(titre, _grouper(liste)) for titre, liste in par_onglet.items() if liste)


def onglet_actif(menu: Iterable[Onglet], chemin: str) -> Onglet | None:
    """Onglet de la page affichée, ou `None` (page d'erreur, page hors menu)."""
    for onglet in menu:
        if onglet.est_actif(chemin):
            return onglet
    return None
