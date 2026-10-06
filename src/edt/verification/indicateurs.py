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
  demi-journée et par journée, et en créneaux libres ; la plage du déjeuner
  n'en est retirée que si ses horaires sont déterminés, sinon un créneau
  libre à midi est un trou comme un autre), les **changements
  de campus** dans une journée (par groupe et par enseignant), les séances
  du **samedi** et les séances sur les **créneaux extrêmes**.

L'emploi du temps d'un groupe, pour les trous et les changements de campus,
est celui de ses étudiants : les séances du groupe et de ses ascendants
(classe, promotion). On le calcule pour les groupes **feuilles** de l'arbre
(sans sous-groupe), ce qui évite de compter deux fois une même journée.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte, Niveaux
from edt.domaine.enumerations import SAMEDI
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Creneau, Instantane, Placement
from edt.erreurs import ErreurDonneesInvalides
from edt.verification.contexte import Element
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
        }


def calculer_indicateurs(
    instantane: Instantane,
    placements: Sequence[Placement],
    anomalies: Sequence[Anomalie] | None = None,
    *,
    niveaux: Niveaux | None = None,
) -> Indicateurs:
    """Calcule les indicateurs d'une semaine type.

    `anomalies` : résultat de `verifier` déjà calculé ; s'il est absent, la
    vérification est lancée avec `niveaux` (par défaut : toutes les
    contraintes actives par défaut, en dur). Les anomalies « bloquantes »
    comptées sont celles de gravité « erreur » : avec des niveaux, une entorse
    à une règle importante ou facultative n'en fait pas partie. Les
    placements d'une séance inconnue sont ignorés ici (le vérificateur les
    signale) ; une séance placée deux fois compte deux fois dans les mesures
    d'occupation, comme dans le vérificateur.
    """
    placements = tuple(placements)
    valider_placements(instantane, placements)
    if anomalies is None:
        anomalies = verifier(instantane, placements, niveaux=niveaux)
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


def _ascendants(instantane: Instantane, groupe_id: int) -> set[int]:
    lignee: set[int] = set()
    courant = instantane.groupe(groupe_id).parent_id
    while courant is not None:
        if courant == groupe_id or courant in lignee:
            raise ErreurDonneesInvalides(
                f"L'arbre des groupes contient une boucle autour du groupe {groupe_id}."
            )
        lignee.add(courant)
        courant = instantane.groupe(courant).parent_id
    return lignee


def _journees_des_personnes(
    instantane: Instantane, elements: list[Element]
) -> tuple[list[list[Element]], list[list[Element]]]:
    """Séances de chaque (groupe feuille, jour) et de chaque (enseignant non fictif, jour)."""
    parents = {groupe.parent_id for groupe in instantane.groupes if groupe.parent_id is not None}
    par_groupe: dict[int, list[Element]] = defaultdict(list)
    par_enseignant: dict[int, list[Element]] = defaultdict(list)
    for element in elements:
        for groupe in element.besoin.groupes:
            par_groupe[groupe].append(element)
        for enseignant in element.besoin.enseignants:
            if not instantane.enseignant(enseignant).fictif:
                par_enseignant[enseignant].append(element)

    def par_jour(seances: Iterable[Element]) -> list[list[Element]]:
        jours: dict[int, list[Element]] = defaultdict(list)
        for element in sorted(set(seances), key=lambda e: e.position):
            jours[element.jour].append(element)
        return [jours[jour] for jour in sorted(jours)]

    journees_groupes: list[list[Element]] = []
    for groupe in sorted(g.id for g in instantane.groupes if g.id not in parents):
        lignee = {groupe} | _ascendants(instantane, groupe)
        journees_groupes.extend(par_jour(e for g in lignee for e in par_groupe.get(g, ())))
    journees_enseignants: list[list[Element]] = []
    for enseignant in sorted(par_enseignant):
        journees_enseignants.extend(par_jour(par_enseignant[enseignant]))
    return journees_groupes, journees_enseignants


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
    # Plage réservée au déjeuner, retirée des trous ; aucune sans horaires déterminés.
    # La séparation matin / après-midi reste `dejeuner_debut_min` dans les deux cas.
    dejeuner = [parametres.plage_dejeuner] if parametres.plage_dejeuner else []
    creneaux_par_jour: dict[int, list[Creneau]] = defaultdict(list)
    for creneau in instantane.creneaux:
        creneaux_par_jour[creneau.jour].append(creneau)

    demi_journees = journees = creneaux_libres = 0
    groupes, _ = _journees_des_personnes(instantane, elements)
    for journee in groupes:
        for actifs, nb_semaines in _configurations(journee).items():
            seances = [journee[i] for i in actifs]
            plages = [(e.debut, e.fin) for e in seances]
            premier, dernier = min(p[0] for p in plages), max(p[1] for p in plages)
            journees += nb_semaines * _minutes_libres(premier, dernier, [*plages, *dejeuner])
            for moitie in (
                [p for p in plages if p[0] < parametres.dejeuner_debut_min],
                [p for p in plages if p[0] >= parametres.dejeuner_debut_min],
            ):
                if moitie:
                    debut, fin = min(p[0] for p in moitie), max(p[1] for p in moitie)
                    demi_journees += nb_semaines * _minutes_libres(debut, fin, [*moitie, *dejeuner])
            creneaux_libres += nb_semaines * sum(
                1
                for creneau in creneaux_par_jour.get(seances[0].jour, ())
                if premier <= creneau.debut
                and creneau.fin <= dernier
                and not any(se_chevauchent(creneau.debut, creneau.fin, d, f) for d, f in plages)
            )
    return demi_journees, journees, creneaux_libres


def _changements_campus(instantane: Instantane, elements: list[Element]) -> tuple[int, int]:
    campus_des_salles = {salle.id: salle.campus_id for salle in instantane.salles}
    groupes, enseignants = _journees_des_personnes(instantane, elements)

    def compter(journees: list[list[Element]]) -> int:
        total = 0
        for journee in journees:
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
