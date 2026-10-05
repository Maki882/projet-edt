"""Générateur d'instantanés aléatoires mais satisfiables par construction.

Principe : on fabrique d'abord, en secret, un emploi du temps valide (chaque
séance est posée sur un créneau où son groupe, son enseignant et une salle
compatible sont libres, sans changement de campus dans la demi-journée) ;
les besoins sont ensuite déduits de ce qui a pu être posé. L'instantané
obtenu admet donc toujours au moins une solution : l'emploi du temps caché,
renvoyé avec lui.

Contenu d'une promotion (environ 53 séances par semaine, 30 h par étudiant) :

- 1 promotion, 4 classes, 8 demi-groupes ;
- cours magistraux en amphithéâtre pour la promotion, TD par classe, TP par
  demi-groupe en salle équipée (samedi matin permis) ;
- un devoir surveillé au jour et à l'heure imposés pour une promotion sur deux ;
- des matières courtes : deux besoins sur deux moitiés du semestre, qui
  partagent créneau et salle dans l'emploi du temps caché ;
- deux campus à 60 minutes l'un de l'autre, salles accessibles ou non, une
  classe qui requiert l'accessibilité pour une promotion sur quatre ;
- indisponibilités récurrentes et datées d'enseignants et de salles, maxima
  journaliers pour une partie des enseignants.

Le générateur est déterministe : même taille et même graine, même instantané.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
from collections import defaultdict

from tests.solveur.fabrique import C3, CRENEAUX_JOUR, JEUDI, RENTREE, SAMEDI, Fabrique, H

from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    DemiJournee,
    ModeVolume,
    NatureGroupe,
)
from edt.domaine.instantane import Instantane, Placement

TYPE_AMPHI, TYPE_TD, TYPE_TP = 1, 2, 3
EQUIPEMENT_TP = 1
SEANCES_PAR_JOUR_MAX = 3
"""Séances par jour d'un enseignant dans l'emploi du temps caché (permet un maximum de 6 h)."""


@dataclasses.dataclass
class _Fiche:
    """Un futur besoin et les places de ses séances dans l'emploi du temps caché."""

    groupes: tuple[int, ...]
    enseignant: int | None
    type_salle: int
    places: list[tuple[int, int, int]] = dataclasses.field(default_factory=list)
    duree: int = 2 * H
    samedi: bool = False
    impose: bool = False
    deux_moities: bool = False
    equipements: tuple[int, ...] = ()


class _Cache:
    """Emploi du temps caché en cours de construction."""

    def __init__(self, fabrique: Fabrique, alea: random.Random) -> None:
        self.fabrique = fabrique
        self.alea = alea
        self.lies: dict[int, set[int]] = defaultdict(set)
        self.exigeants: set[int] = set()
        self.salles: list[dict] = []
        self.occupe_groupe: set[tuple[int, int, int]] = set()
        self.occupe_enseignant: set[tuple[int, int, int]] = set()
        self.occupe_salle: set[tuple[int, int, int]] = set()
        self.charge_jour: dict[tuple[int, int], int] = defaultdict(int)
        self.campus_groupe: dict[tuple[int, int, DemiJournee], set[int]] = defaultdict(set)
        self.campus_enseignant: dict[tuple[int, int, DemiJournee], set[int]] = defaultdict(set)

    def salle(self, campus: int, type_salle: int, capacite: int, accessible: bool) -> None:
        identifiant = self.fabrique.salle(
            capacite=capacite,
            campus=campus,
            type_salle=type_salle,
            equipements=(EQUIPEMENT_TP,) if type_salle == TYPE_TP else (),
            accessibilite=Accessibilite.OUI if accessible else Accessibilite.NON,
        )
        self.salles.append(
            {
                "id": identifiant,
                "campus": campus,
                "type": type_salle,
                "capacite": capacite,
                "accessible": accessible,
            }
        )

    def groupes_lies(self, groupes: tuple[int, ...]) -> set[int]:
        return set(groupes).union(*(self.lies[g] for g in groupes))

    def poser(self, fiche: _Fiche, effectif: int, creneaux: list[tuple[int, int]]) -> bool:
        """Pose une séance de la fiche sur le premier créneau valide de `creneaux`."""
        lies = self.groupes_lies(fiche.groupes)
        accessible = not lies.isdisjoint(self.exigeants)
        nb_creneaux = 2 if fiche.duree > 2 * H else 1
        for jour, rang in creneaux:
            rangs = range(rang, rang + nb_creneaux)
            demi = CRENEAUX_JOUR[rang][2]
            if any((g, jour, r) in self.occupe_groupe for g in lies for r in rangs):
                continue
            enseignant = fiche.enseignant
            if enseignant is not None and (
                any((enseignant, jour, r) in self.occupe_enseignant for r in rangs)
                or self.charge_jour[(enseignant, jour)] >= SEANCES_PAR_JOUR_MAX
            ):
                continue
            salles = [
                salle
                for salle in self.salles
                if salle["type"] == fiche.type_salle
                and salle["capacite"] >= effectif
                and (salle["accessible"] or not accessible)
                and not any((salle["id"], jour, r) in self.occupe_salle for r in rangs)
                and all(self.campus_groupe[(g, jour, demi)] <= {salle["campus"]} for g in lies)
                and (
                    enseignant is None
                    or self.campus_enseignant[(enseignant, jour, demi)] <= {salle["campus"]}
                )
            ]
            if not salles:
                continue
            salle = self.alea.choice(salles)
            for r in rangs:
                self.occupe_groupe.update((g, jour, r) for g in fiche.groupes)
                self.occupe_salle.add((salle["id"], jour, r))
                if enseignant is not None:
                    self.occupe_enseignant.add((enseignant, jour, r))
            for groupe in fiche.groupes:
                self.campus_groupe[(groupe, jour, demi)].add(salle["campus"])
            if enseignant is not None:
                self.charge_jour[(enseignant, jour)] += 1
                self.campus_enseignant[(enseignant, jour, demi)].add(salle["campus"])
            fiche.places.append((jour, rang, salle["id"]))
            return True
        return False


def generer(
    nb_seances: int,
    graine: int = 0,
    nb_semaines: int = 12,
    part_verrouillee: float = 0.0,
    indices: bool = False,
    nb_campus: int = 2,
) -> tuple[Instantane, dict[str, Placement]]:
    """Instantané satisfiable d'environ `nb_seances` séances, et son emploi du temps caché.

    - `part_verrouillee` : part des séances verrouillées à leur place cachée ;
    - `indices` : donne la place cachée de chaque séance comme indice ;
    - `nb_campus` : 1 ou 2.

    Le nombre exact de séances est `len(instantane.seances)` (au plus `nb_seances`).
    """
    alea = random.Random(graine)
    fabrique = Fabrique(nb_semaines=nb_semaines)
    cache = _Cache(fabrique, alea)
    campus = [fabrique.campus("N")]
    if nb_campus > 1:
        campus.append(fabrique.campus("L"))
        fabrique.trajet(campus[0], campus[1], 60)

    nb_promos = -(-nb_seances // 52)
    for numero in range(max(2, round(nb_promos * 0.5))):
        cache.salle(campus[numero % len(campus)], TYPE_AMPHI, 150, numero % 3 != 2)
    for numero in range(round(nb_promos * 3) + 1):
        cache.salle(campus[numero % len(campus)], TYPE_TD, 36, numero % 3 != 2)
    for numero in range(round(nb_promos * 1.5) + 1):
        cache.salle(campus[numero % len(campus)], TYPE_TP, 20, numero % 3 != 2)

    enseignants: list[int] = []
    charge: dict[int, int] = defaultdict(int)

    def enseignant(nb: int) -> int:
        """Un enseignant auquel il reste de la place, ou un nouveau."""
        libres = [e for e in enseignants if charge[e] + nb <= 8]
        if libres and alea.random() < 0.8:
            choisi = alea.choice(libres)
        else:
            choisi = fabrique.enseignant(max_jour=6 * H if len(enseignants) % 3 == 0 else None)
            enseignants.append(choisi)
        charge[choisi] += nb
        return choisi

    semaine = [(jour, rang) for jour in range(5) for rang in range(4)]
    fiches: list[tuple[_Fiche, int, int]] = []  # (fiche, effectif, nombre demandé)
    for promo_numero in range(nb_promos):
        promo = fabrique.groupe(f"P{promo_numero}", 120, nature=NatureGroupe.PROMOTION)
        classes, demis = [], []
        for c in range(4):
            classe = fabrique.groupe(
                f"P{promo_numero}-C{c}",
                30,
                parent=promo,
                accessibilite=promo_numero % 4 == 0 and c == 0,
            )
            classes.append(classe)
            if promo_numero % 4 == 0 and c == 0:
                cache.exigeants.add(classe)
            for d in range(2):
                demi = fabrique.groupe(
                    f"P{promo_numero}-C{c}-G{d}", 15, parent=classe, nature=NatureGroupe.DEMI_GROUPE
                )
                demis.append(demi)
                cache.lies[demi] |= {classe, promo}
                cache.lies[classe] |= {demi, promo}
                cache.lies[promo] |= {demi, classe}

        if promo_numero % 2 == 0:
            fiches.append((_Fiche((promo,), None, TYPE_AMPHI, duree=4 * H, impose=True), 120, 1))
        for nombre in (2, 2, 1):
            fiches.append((_Fiche((promo,), enseignant(nombre), TYPE_AMPHI), 120, nombre))
        for numero, classe in enumerate(classes):
            for nombre in (2, 2, 2, 1):
                fiches.append((_Fiche((classe,), enseignant(nombre), TYPE_TD), 30, nombre))
            moities = (promo_numero + numero) % 3 == 0
            fiches.append((_Fiche((classe,), enseignant(1), TYPE_TD, deux_moities=moities), 30, 1))
        for demi in demis:
            fiches.append(
                (
                    _Fiche(
                        (demi,),
                        enseignant(2),
                        TYPE_TP,
                        samedi=True,
                        equipements=(EQUIPEMENT_TP,),
                    ),
                    15,
                    2,
                )
            )

    # Emploi du temps caché : les séances imposées d'abord, puis les autres dans le désordre.
    ordre = sorted(range(len(fiches)), key=lambda i: (not fiches[i][0].impose, alea.random()))
    for position in ordre:
        fiche, effectif, nombre = fiches[position]
        for _ in range(nombre):
            if fiche.impose:
                creneaux = [(JEUDI, 2)]
            else:
                creneaux = alea.sample(semaine, len(semaine))
                if fiche.samedi and alea.random() < 0.15:
                    creneaux = [(SAMEDI, alea.randrange(2)), *creneaux]
            cache.poser(fiche, effectif, creneaux)

    # Besoins, dans l'ordre de création, jusqu'au nombre de séances demandé.
    solution: dict[str, tuple[int, int, int]] = {}
    moitie = RENTREE + dt.timedelta(days=7 * (nb_semaines // 2))
    restant = nb_seances
    for fiche, effectif, _ in fiches:
        places = sorted(fiche.places)
        cout = 2 if fiche.deux_moities else 1
        places = places[: restant // cout]
        if not places:
            continue
        commun = {
            "enseignants": [] if fiche.enseignant is None else [fiche.enseignant],
            "duree": fiche.duree,
            "effectif": effectif,
            "types_salle_acceptes": frozenset({fiche.type_salle}),
            "equipements_requis": frozenset(fiche.equipements),
            "samedi_autorise": fiche.samedi,
        }
        if fiche.impose:
            commun.update(jour_impose=JEUDI, debut_impose=C3)
        if fiche.deux_moities:
            fenetres = [
                (RENTREE, moitie - dt.timedelta(days=3)),
                (moitie, fabrique.date_fin),
            ]
            for debut, fin in fenetres:
                nb = len(fabrique_semaines(debut, fin))
                besoin = fabrique.besoin(
                    fiche.groupes,
                    nombre=nb,
                    mode_volume=ModeVolume.TOTAL,
                    max_par_semaine=1,
                    date_debut=debut,
                    date_fin=fin,
                    **commun,
                )
                solution[f"{besoin}-1"] = places[0]
        else:
            besoin = fabrique.besoin(fiche.groupes, nombre=len(places), **commun)
            for rang, place in enumerate(places, start=1):
                solution[f"{besoin}-{rang}"] = place
        restant -= cout * len(places)

    _ajouter_indisponibilites(fabrique, cache, alea, enseignants, nb_semaines)

    caches = {
        seance_id: Placement(seance_id, jour, CRENEAUX_JOUR[rang][0], -1, salle)
        for seance_id, (jour, rang, salle) in solution.items()
    }
    for seance_id, place in caches.items():
        if indices:
            fabrique.indice(seance_id, place.jour, place.debut, place.salle_id)
        if alea.random() < part_verrouillee:
            fabrique.verrou(seance_id, place.jour, place.debut, place.salle_id)
    instantane = fabrique.instantane()
    return instantane, {
        seance.id: dataclasses.replace(
            caches[seance.id], fin=caches[seance.id].debut + seance.duree
        )
        for seance in instantane.seances
    }


def fabrique_semaines(debut: dt.date, fin: dt.date) -> list[dt.date]:
    """Lundis des semaines qui recoupent `[debut, fin]`."""
    lundi = debut - dt.timedelta(days=debut.weekday())
    lundis = []
    while lundi <= fin:
        lundis.append(lundi)
        lundi += dt.timedelta(days=7)
    return lundis


def _ajouter_indisponibilites(
    fabrique: Fabrique,
    cache: _Cache,
    alea: random.Random,
    enseignants: list[int],
    nb_semaines: int,
) -> None:
    """Indisponibilités compatibles avec l'emploi du temps caché (jours et salles inoccupés)."""
    for enseignant in enseignants:
        libres = [
            jour
            for jour in range(5)
            if not any((enseignant, jour, rang) in cache.occupe_enseignant for rang in range(4))
        ]
        if not libres:
            continue
        tirage = alea.random()
        jour = alea.choice(libres)
        if tirage < 0.4:
            fabrique.indisponibilite(CibleIndisponibilite.ENSEIGNANT, enseignant, jour=jour)
        elif tirage < 0.6:
            date = RENTREE + dt.timedelta(days=7 * alea.randrange(nb_semaines) + jour)
            fabrique.indisponibilite(CibleIndisponibilite.ENSEIGNANT, enseignant, date_debut=date)
    for salle in cache.salles[::7]:
        for jour in range(5):
            if not any((salle["id"], jour, rang) in cache.occupe_salle for rang in (2, 3)):
                fabrique.indisponibilite(
                    CibleIndisponibilite.SALLE, salle["id"], jour=jour, debut=13 * H, fin=19 * H
                )
                break
