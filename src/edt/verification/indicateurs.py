"""Indicateurs de qualité d'une semaine type : `calculer_indicateurs(instantane, placements, anomalies)`.

Fonction pure, sans base. Les définitions exactes sont dans
`docs/verification.md` (section « Indicateurs ») ; en résumé :

- **séances placées** : séances de l'instantané qui ont au moins un
  placement, rapportées au nombre de séances ;
- **anomalies bloquantes** : nombre d'anomalies de gravité « erreur », par code ;
- **utilisation des salles** : minutes occupées (durée placée × nombre de
  semaines actives) rapportées aux minutes offertes (durée totale des
  créneaux de la grille × nombre de semaines du semestre), globalement et
  par salle ;
- les quatre termes de l'objectif (préférences) sont **comptés semaine par
  semaine sur tout le semestre** : une situation qui se répète sur
  15 semaines compte 15 fois. Diviser par `nb_semaines` donne une moyenne
  hebdomadaire. Ce sont : les **trous** des groupes (en minutes, par
  demi-journée et par journée, et en créneaux libres ; la pause déjeuner
  est retirée des trous en minutes : en mode fixe la plage réservée, en mode
  libre les N premières minutes libres de la fenêtre de chaque journée, en
  mode aucun rien ; les créneaux libres ne dépendent pas du mode), les **changements
  de campus** dans une journée (par groupe et par enseignant), les séances
  du **samedi** et les séances sur les **créneaux extrêmes**.

L'emploi du temps d'un groupe, pour les trous et les changements de campus,
est celui de ses étudiants : les séances du groupe et de ses ascendants
(classe, promotion). Il est calculé pour les groupes **feuilles** de l'arbre
(sans sous-groupe), ce qui évite de compter deux fois une même journée.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte, Niveau, Niveaux
from edt.domaine.enumerations import SAMEDI
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Creneau, Instantane, Placement
from edt.domaine.regles import NaturePersonne, Personne
from edt.verification.contexte import Element, valider_groupes
from edt.verification.contrat import Anomalie
from edt.verification.semaine_type import construire_elements, valider_placements, verifier

journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class Indicateurs:
    """Indicateurs d'une semaine type (valeurs figées, sérialisables avec `vers_dict`).

    Les pourcentages valent `None` quand ils n'ont pas de sens (aucune
    séance à placer, aucune minute offerte par la grille).
    """

    nb_seances: int
    nb_seances_placees: int
    pourcentage_seances_placees: float | None
    nb_anomalies_bloquantes: int
    anomalies_bloquantes_par_code: tuple[tuple[CodeContrainte, int], ...]
    nb_semaines: int
    pourcentage_utilisation_salles: float | None
    utilisation_par_salle: tuple[tuple[int, float | None], ...]
    trous_minutes_demi_journee: int
    trous_minutes_journee: int
    trous_creneaux: int
    changements_campus_groupes: int
    changements_campus_enseignants: int
    seances_samedi: int
    seances_creneaux_extremes: int
    valeur_objectif: int
    entorses_par_regle: tuple[tuple[int, int], ...] = ()
    """Nombre d'anomalies (toutes gravités) de chaque règle personnalisée : (id, nombre)."""

    def vers_dict(self) -> dict[str, object]:
        """Représentation JSON (clés en texte, codes de contrainte en texte)."""
        return {
            "nb_seances": self.nb_seances,
            "nb_seances_placees": self.nb_seances_placees,
            "pourcentage_seances_placees": self.pourcentage_seances_placees,
            "nb_anomalies_bloquantes": self.nb_anomalies_bloquantes,
            "anomalies_bloquantes_par_code": {
                str(code): nombre for code, nombre in self.anomalies_bloquantes_par_code
            },
            "nb_semaines": self.nb_semaines,
            "pourcentage_utilisation_salles": self.pourcentage_utilisation_salles,
            "utilisation_par_salle": {
                str(salle): pourcentage for salle, pourcentage in self.utilisation_par_salle
            },
            "trous_minutes_demi_journee": self.trous_minutes_demi_journee,
            "trous_minutes_journee": self.trous_minutes_journee,
            "trous_creneaux": self.trous_creneaux,
            "changements_campus_groupes": self.changements_campus_groupes,
            "changements_campus_enseignants": self.changements_campus_enseignants,
            "seances_samedi": self.seances_samedi,
            "seances_creneaux_extremes": self.seances_creneaux_extremes,
            "valeur_objectif": self.valeur_objectif,
            "entorses_par_regle": {
                str(regle_id): nombre for regle_id, nombre in self.entorses_par_regle
            },
        }


def calculer_indicateurs(
    instantane: Instantane,
    placements: Sequence[Placement],
    anomalies: Sequence[Anomalie] | None = None,
    *,
    niveaux: Niveaux | None = None,
    niveaux_regles: Mapping[int, Niveau] | None = None,
) -> Indicateurs:
    """Calcule les indicateurs d'une semaine type.

    `anomalies` : résultat de `verifier` déjà calculé ; s'il est absent, la
    vérification est lancée avec `niveaux` (par défaut : toutes les
    contraintes actives par défaut, en dur). Les anomalies « bloquantes »
    comptées sont celles de gravité « erreur » : avec des niveaux, une entorse
    à une règle importante ou facultative n'en fait pas partie. Les
    placements d'une séance inconnue sont ignorés ici (le vérificateur les
    signale) ; une séance placée deux fois compte deux fois dans les mesures
    d'occupation, comme dans le vérificateur. Lève `ErreurDonneesInvalides` si
    un placement est mal formé ou si l'arbre des groupes boucle (les trous et
    changements de campus des groupes n'auraient plus de sens).
    """
    placements = tuple(placements)
    valider_placements(placements)
    if anomalies is None:
        # `verifier` valide l'arbre des groupes (`Contexte`) : inutile de le refaire ici.
        anomalies = verifier(instantane, placements, niveaux=niveaux, niveaux_regles=niveaux_regles)
    else:
        valider_groupes(instantane)
    elements, inconnus = construire_elements(instantane, placements)
    if inconnus:
        journal.debug("Indicateurs : %d placement(s) de séance inconnue ignoré(s).", len(inconnus))

    parametres = instantane.parametres
    nb_seances = len(instantane.seances)
    nb_placees = len({element.seance_id for element in elements})
    bloquantes = Counter(anomalie.code for anomalie in anomalies if anomalie.bloquante)
    par_code = tuple((code, bloquantes[code]) for code in CodeContrainte if bloquantes[code] > 0)
    global_salles, par_salle = _utilisation_salles(instantane, elements)
    trous_demi, trous_jour, trous_creneaux = _trous(instantane, elements)
    campus_groupes, campus_enseignants = _changements_campus(instantane, elements)
    samedi = sum(_poids(element) for element in elements if element.jour == SAMEDI)
    extremes = _creneaux_extremes(instantane, elements)

    return Indicateurs(
        nb_seances=nb_seances,
        nb_seances_placees=nb_placees,
        pourcentage_seances_placees=_pourcentage(nb_placees, nb_seances),
        nb_anomalies_bloquantes=sum(bloquantes.values()),
        anomalies_bloquantes_par_code=par_code,
        nb_semaines=len(instantane.semaines),
        pourcentage_utilisation_salles=global_salles,
        utilisation_par_salle=par_salle,
        trous_minutes_demi_journee=trous_demi,
        trous_minutes_journee=trous_jour,
        trous_creneaux=trous_creneaux,
        changements_campus_groupes=campus_groupes,
        changements_campus_enseignants=campus_enseignants,
        seances_samedi=samedi,
        seances_creneaux_extremes=extremes,
        entorses_par_regle=tuple(
            sorted(
                Counter(
                    a.regle_id
                    for a in anomalies
                    if a.code is CodeContrainte.REGLE and a.regle_id is not None
                ).items()
            )
        ),
        valeur_objectif=(
            parametres.poids_trous * trous_creneaux
            + parametres.poids_changement_campus * (campus_groupes + campus_enseignants)
            + parametres.poids_samedi * samedi
            + parametres.poids_creneaux_extremes * extremes
        ),
    )


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #


def _pourcentage(numerateur: int, denominateur: int) -> float | None:
    return None if denominateur == 0 else 100.0 * numerateur / denominateur


def _poids(element: Element) -> int:
    """Nombre de semaines où l'élément a lieu."""
    return len(element.semaines or ())


def _minutes_libres(debut: int, fin: int, occupes: Iterable[tuple[int, int]]) -> int:
    """Minutes de `[debut, fin[` qui ne sont couvertes par aucune plage de `occupes`."""
    libre = fin - debut
    curseur = debut
    for debut_occupe, fin_occupe in sorted(occupes):
        debut_occupe, fin_occupe = max(debut_occupe, curseur), min(fin_occupe, fin)
        if debut_occupe < fin_occupe:
            libre -= fin_occupe - debut_occupe
            curseur = fin_occupe
    return libre


def _configurations(elements: list[Element]) -> dict[tuple[int, ...], int]:
    """Regroupe les semaines par ensemble d'éléments actifs : {indices actifs : nb de semaines}."""
    semaines = sorted(set().union(*(element.semaines or () for element in elements)))
    configurations: Counter[tuple[int, ...]] = Counter()
    for semaine in semaines:
        actifs = tuple(
            i for i, element in enumerate(elements) if semaine in (element.semaines or ())
        )
        configurations[actifs] += 1
    return dict(configurations)


def _journees_des_personnes(
    instantane: Instantane, elements: list[Element]
) -> tuple[list[tuple[Personne, list[Element]]], list[tuple[Personne, list[Element]]]]:
    """Journées de chaque groupe feuille et de chaque enseignant non fictif.

    Emploi du temps de chaque personne : `Instantane.emplois` (définition
    unique : un groupe feuille suit ses séances et celles de ses ascendants).
    Renvoie, pour les groupes puis pour les enseignants, des couples
    (personne, séances placées d'un jour), un par personne et par jour.
    """
    par_seance: dict[str, list[Element]] = defaultdict(list)
    for element in elements:
        if element.seance_id is not None:
            par_seance[element.seance_id].append(element)
    groupes: list[tuple[Personne, list[Element]]] = []
    enseignants: list[tuple[Personne, list[Element]]] = []
    for emploi in instantane.emplois:
        jours: dict[int, list[Element]] = defaultdict(list)
        places = sorted(
            (e for seance in emploi.seances for e in par_seance.get(seance, ())),
            key=lambda e: e.position,
        )
        for element in places:
            jours[element.jour].append(element)
        cible = groupes if emploi.personne.nature is NaturePersonne.GROUPE else enseignants
        cible.extend((emploi.personne, jours[jour]) for jour in sorted(jours))
    return groupes, enseignants


# --------------------------------------------------------------------------- #
# Indicateurs
# --------------------------------------------------------------------------- #


def _utilisation_salles(
    instantane: Instantane, elements: list[Element]
) -> tuple[float | None, tuple[tuple[int, float | None], ...]]:
    offertes = sum(creneau.fin - creneau.debut for creneau in instantane.creneaux) * len(
        instantane.semaines
    )
    occupees: Counter[int] = Counter()
    for element in elements:
        if element.salle_id is not None:
            occupees[element.salle_id] += element.duree * _poids(element)
    par_salle = tuple(
        (salle.id, _pourcentage(occupees[salle.id], offertes))
        for salle in sorted(instantane.salles, key=lambda s: s.id)
    )
    total = _pourcentage(sum(occupees.values()), offertes * len(instantane.salles))
    return total, par_salle


def _trous(instantane: Instantane, elements: list[Element]) -> tuple[int, int, int]:
    parametres = instantane.parametres
    # Pause déjeuner retirée des trous en minutes, selon le déjeuner de chaque groupe feuille
    # (`Instantane.reglages` : standard ou règle `PAUSE_DEJEUNER`) : en mode fixe, la plage
    # réservée ; en mode libre, les N premières minutes libres de la fenêtre (calculées par
    # journée) ; en mode aucun, rien. La séparation matin / après-midi reste
    # `dejeuner_debut_min` dans tous les cas.
    reglages = instantane.reglages.dejeuner
    creneaux_par_jour: dict[int, list[Creneau]] = defaultdict(list)
    for creneau in instantane.creneaux:
        creneaux_par_jour[creneau.jour].append(creneau)

    demi_journees = journees = creneaux_libres = 0
    groupes, _ = _journees_des_personnes(instantane, elements)
    for personne, journee in groupes:
        reglage = reglages.get(personne)
        plage_fixe = None if reglage is None else reglage.plage
        fenetre = None if reglage is None else reglage.fenetre
        for actifs, nb_semaines in _configurations(journee).items():
            seances = [journee[i] for i in actifs]
            plages = [(e.debut, e.fin) for e in seances]
            premier, dernier = min(p[0] for p in plages), max(p[1] for p in plages)
            if fenetre is not None:
                pause = _pause_libre(fenetre, premier, dernier, plages)
            else:
                pause = [plage_fixe] if plage_fixe else []
            journees += nb_semaines * _minutes_libres(premier, dernier, [*plages, *pause])
            for moitie in (
                [p for p in plages if p[0] < parametres.dejeuner_debut_min],
                [p for p in plages if p[0] >= parametres.dejeuner_debut_min],
            ):
                if moitie:
                    debut, fin = min(p[0] for p in moitie), max(p[1] for p in moitie)
                    demi_journees += nb_semaines * _minutes_libres(debut, fin, [*moitie, *pause])
            creneaux_libres += nb_semaines * sum(
                1
                for creneau in creneaux_par_jour.get(seances[0].jour, ())
                if premier <= creneau.debut
                and creneau.fin <= dernier
                and not any(se_chevauchent(creneau.debut, creneau.fin, d, f) for d, f in plages)
            )
    return demi_journees, journees, creneaux_libres


def _pause_libre(
    fenetre: tuple[int, int, int], premier: int, dernier: int, plages: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Plages de la pause d'une journée en mode libre, à retirer des trous en minutes.

    Les N premières minutes libres (dans l'ordre chronologique) de la fenêtre
    `[X, Y]` restreinte à la journée `[premier, dernier[` : c'est la pause ;
    au-delà, le temps libre est un trou ordinaire.
    """
    x, y, reste = fenetre
    pause: list[tuple[int, int]] = []
    curseur, fin_fenetre = max(x, premier), min(y, dernier)
    for debut, fin in sorted([*plages, (fin_fenetre, fin_fenetre)]):
        if reste <= 0 or curseur >= fin_fenetre:
            break
        libre_fin = min(debut, fin_fenetre)
        if libre_fin > curseur:
            prise = min(reste, libre_fin - curseur)
            pause.append((curseur, curseur + prise))
            reste -= prise
        curseur = max(curseur, fin)
    return pause


def _changements_campus(instantane: Instantane, elements: list[Element]) -> tuple[int, int]:
    campus_des_salles = {salle.id: salle.campus_id for salle in instantane.salles}
    groupes, enseignants = _journees_des_personnes(instantane, elements)

    def compter(journees: list[tuple[Personne, list[Element]]]) -> int:
        total = 0
        for _, journee in journees:
            for actifs, nb_semaines in _configurations(journee).items():
                suite = [
                    campus_des_salles[e.salle_id]
                    for e in sorted(
                        (journee[i] for i in actifs), key=lambda e: (e.debut, e.fin, e.position)
                    )
                    if e.salle_id is not None
                ]
                total += nb_semaines * sum(
                    1 for avant, apres in zip(suite, suite[1:], strict=False) if avant != apres
                )
        return total

    return compter(groupes), compter(enseignants)


def _creneaux_extremes(instantane: Instantane, elements: list[Element]) -> int:
    extremes: dict[int, tuple[Creneau, Creneau]] = {}
    for jour in {creneau.jour for creneau in instantane.creneaux}:
        du_jour = [creneau for creneau in instantane.creneaux if creneau.jour == jour]
        extremes[jour] = (
            min(du_jour, key=lambda c: (c.debut, c.fin)),
            max(du_jour, key=lambda c: (c.fin, c.debut)),
        )
    total = 0
    for element in elements:
        if element.jour not in extremes:
            continue
        if any(
            se_chevauchent(element.debut, element.fin, creneau.debut, creneau.fin)
            for creneau in extremes[element.jour]
        ):
            total += _poids(element)
    return total
