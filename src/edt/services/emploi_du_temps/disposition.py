"""Disposition des blocs dans la grille d'un emploi du temps (fonction pure, testée seule).

La grille a un axe vertical en minutes et une colonne par jour. Chaque
séance devient un bloc positionné **à la minute près**, en pourcentages de
la hauteur de l'axe et de la largeur de sa colonne :

- **axe** : de l'ouverture à la fermeture (paramètres), élargi pour
  contenir toute séance qui en sort (un DST de 8 h à 12 h quand la grille
  ouvre à 8 h 30), puis arrondi à l'heure pleine ; une graduation par heure ;
- **séances simultanées** : dans une colonne, les séances qui se recoupent
  forment une grappe (fermeture transitive du chevauchement). Chaque séance
  de la grappe reçoit la première « voie » libre (une voie est libre quand
  la séance qui l'occupe est finie) ; la grappe est découpée en autant de
  voies que nécessaire et chaque bloc prend `1 / nb_voies` de la largeur.
  Un bloc s'élargit ensuite vers la droite sur les voies voisines qui
  restent libres pendant toute sa durée. Deux demi-groupes en TP au même
  moment s'affichent donc côte à côte, chacun sur la moitié de la colonne,
  sans jamais se recouvrir ;
- plages semi-ouvertes : une séance qui finit à 10 h 30 ne gêne pas celle
  qui commence à 10 h 30.

Le résultat ne dépend que des plages données (ordre de tri stable par
début, fin décroissante puis clé) : la même vue s'affiche toujours pareil.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from edt.erreurs import ErreurDonneesInvalides

MINUTES_PAR_HEURE = 60


@dataclass(frozen=True)
class Plage:
    """Ce qu'il faut savoir d'une séance pour la placer : sa clé, sa colonne, son horaire."""

    cle: str
    colonne: int
    debut: int
    fin: int


@dataclass(frozen=True)
class Axe:
    """Axe horaire de la grille, en minutes depuis minuit."""

    debut: int
    fin: int
    graduations: tuple[int, ...]

    @property
    def duree(self) -> int:
        """Étendue de l'axe, en minutes."""
        return self.fin - self.debut

    def position(self, minutes: int) -> float:
        """Position d'une heure sur l'axe, en pourcentage de sa hauteur (0 en haut)."""
        return round((minutes - self.debut) * 100 / self.duree, 4)

    def hauteur(self, debut: int, fin: int) -> float:
        """Hauteur d'une plage, en pourcentage de la hauteur de l'axe."""
        return round((fin - debut) * 100 / self.duree, 4)


@dataclass(frozen=True)
class Position:
    """Place d'un bloc : pourcentages de la colonne et voie occupée dans sa grappe."""

    cle: str
    haut: float
    hauteur: float
    gauche: float
    largeur: float
    voie: int
    nb_voies: int


@dataclass(frozen=True)
class Disposition:
    """Axe de la grille et position de chaque bloc, par clé."""

    axe: Axe
    positions: dict[str, Position]


def calculer_axe(ouverture: int, fermeture: int, plages: Iterable[Plage] = ()) -> Axe:
    """Axe qui couvre l'ouverture, la fermeture et toutes les plages, arrondi à l'heure."""
    if ouverture >= fermeture:
        raise ErreurDonneesInvalides(
            "L'heure d'ouverture doit précéder l'heure de fermeture pour afficher la grille."
        )
    debut, fin = ouverture, fermeture
    for plage in plages:
        debut = min(debut, plage.debut)
        fin = max(fin, plage.fin)
    debut = (debut // MINUTES_PAR_HEURE) * MINUTES_PAR_HEURE
    fin = min(-(-fin // MINUTES_PAR_HEURE) * MINUTES_PAR_HEURE, 24 * MINUTES_PAR_HEURE)
    graduations = tuple(range(debut, fin + 1, MINUTES_PAR_HEURE))
    return Axe(debut=debut, fin=fin, graduations=graduations)


def disposer(plages: Iterable[Plage], ouverture: int, fermeture: int) -> Disposition:
    """Calcule l'axe et la position de chaque plage (voir la règle en tête de module)."""
    plages = list(plages)
    cles = [plage.cle for plage in plages]
    if len(cles) != len(set(cles)):
        raise ErreurDonneesInvalides("Deux séances de la même vue portent la même clé.")
    for plage in plages:
        if plage.debut >= plage.fin:
            raise ErreurDonneesInvalides(
                f"La séance {plage.cle} a une fin qui ne suit pas son début."
            )
    axe = calculer_axe(ouverture, fermeture, plages)
    par_colonne: dict[int, list[Plage]] = defaultdict(list)
    for plage in plages:
        par_colonne[plage.colonne].append(plage)
    positions: dict[str, Position] = {}
    for colonne in par_colonne.values():
        positions.update(disposer_colonne(colonne, axe))
    return Disposition(axe=axe, positions=positions)


def disposer_colonne(plages: Sequence[Plage], axe: Axe) -> dict[str, Position]:
    """Positions des plages d'une même colonne (jour), côte à côte quand elles se recoupent."""
    triees = sorted(plages, key=lambda p: (p.debut, -p.fin, p.cle))
    positions: dict[str, Position] = {}
    grappe: list[Plage] = []
    fin_grappe = None
    for plage in triees:
        if fin_grappe is not None and plage.debut >= fin_grappe:
            positions.update(_disposer_grappe(grappe, axe))
            grappe = []
            fin_grappe = None
        grappe.append(plage)
        fin_grappe = plage.fin if fin_grappe is None else max(fin_grappe, plage.fin)
    if grappe:
        positions.update(_disposer_grappe(grappe, axe))
    return positions


def _disposer_grappe(grappe: list[Plage], axe: Axe) -> dict[str, Position]:
    """Répartit une grappe de plages qui se recoupent sur des voies parallèles."""
    fins_des_voies: list[int] = []
    voie_de: dict[str, int] = {}
    for plage in grappe:
        for indice, fin in enumerate(fins_des_voies):
            if fin <= plage.debut:
                voie_de[plage.cle] = indice
                fins_des_voies[indice] = plage.fin
                break
        else:
            voie_de[plage.cle] = len(fins_des_voies)
            fins_des_voies.append(plage.fin)
    nb_voies = len(fins_des_voies)

    occupants: dict[int, list[Plage]] = defaultdict(list)
    for plage in grappe:
        occupants[voie_de[plage.cle]].append(plage)

    def voie_libre(voie: int, debut: int, fin: int) -> bool:
        return all(autre.fin <= debut or autre.debut >= fin for autre in occupants[voie])

    positions = {}
    for plage in grappe:
        voie = voie_de[plage.cle]
        etendue = 1
        while voie + etendue < nb_voies and voie_libre(voie + etendue, plage.debut, plage.fin):
            etendue += 1
        positions[plage.cle] = Position(
            cle=plage.cle,
            haut=axe.position(plage.debut),
            hauteur=axe.hauteur(plage.debut, plage.fin),
            gauche=round(voie * 100 / nb_voies, 4),
            largeur=round(etendue * 100 / nb_voies, 4),
            voie=voie,
            nb_voies=nb_voies,
        )
    return positions
