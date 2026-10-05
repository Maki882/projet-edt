"""Règles partagées par le moteur, les pré-vérifications et la couche datée.

Ce module ne connaît ni CP-SAT ni la base : il lit l'instantané et répond à
des questions simples, avec les mêmes définitions que le vérificateur.

Définitions
-----------
- deux séances **coexistent** si elles ont lieu le même jour : même jour de
  la semaine et semaines actives qui se croisent (semaine type), ou même date
  (couche datée) ;
- elles **se chevauchent** si, en plus, leurs plages `[debut, fin[` se
  recoupent ;
- elles **partagent une personne** si elles ont un enseignant non fictif en
  commun ou si leurs besoins sont en conflit de groupes ;
- une indisponibilité est **projetée** sur la semaine type sous la forme
  d'une `PlageInterdite` : un jour, une plage horaire et les semaines du
  semestre où elle s'applique ce jour-là (toutes pour une indisponibilité
  récurrente, celles de la période pour une indisponibilité datée). Seules
  les indisponibilités dures (`dure` vrai) comptent.

`Regles` regroupe les contraintes actives d'un calcul et met en cache les
recherches répétées (personnes partagées, salles compatibles, plages
interdites).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte, codes_actifs, normaliser_codes
from edt.domaine.enumerations import JOURS, CibleIndisponibilite
from edt.domaine.heures import MINUTES_PAR_JOUR, ecart, plage_vers_texte, se_chevauchent
from edt.domaine.instantane import Besoin, Indisponibilite, Instantane, Salle, SeanceAPlacer
from edt.erreurs import ErreurDonneesInvalides

C = CodeContrainte


@dataclass(frozen=True)
class PlageInterdite:
    """Indisponibilité projetée sur un jour de la semaine type."""

    jour: int
    debut: int
    fin: int
    semaines: frozenset[int]
    indisponibilite: Indisponibilite


@dataclass(frozen=True)
class Exigence:
    """Une exigence de salle d'un besoin et le nombre de salles qui la satisfont seule."""

    code: CodeContrainte
    libelle: str
    nb_salles: int


@dataclass(frozen=True)
class SallesCompatibles:
    """Salles qui réunissent toutes les exigences actives d'un besoin, et le détail par exigence."""

    salles: tuple[Salle, ...]
    exigences: tuple[Exigence, ...]


def projeter_indisponibilite(
    instantane: Instantane, indisponibilite: Indisponibilite
) -> tuple[PlageInterdite, ...]:
    """Projette une indisponibilité sur la semaine type (voir l'en-tête du module).

    Remplace, en attendant, la fonction du même nom prévue dans le domaine.
    Une période datée hors du semestre ne donne aucune plage. Une
    indisponibilité incohérente lève `ErreurDonneesInvalides` : mieux vaut
    refuser le calcul que l'ignorer en silence.
    """
    i = indisponibilite
    if not 0 <= i.debut < i.fin <= MINUTES_PAR_JOUR:
        raise ErreurDonneesInvalides(
            f"L'indisponibilité n° {i.id} a une plage horaire invalide ({i.debut} à {i.fin} "
            "minutes) : corriger la saisie avant de lancer un calcul."
        )
    if i.jour is not None:
        if not 0 <= i.jour <= 6:
            raise ErreurDonneesInvalides(
                f"L'indisponibilité n° {i.id} porte un jour invalide ({i.jour!r})."
            )
        toutes = frozenset(semaine.numero for semaine in instantane.semaines)
        return (PlageInterdite(i.jour, i.debut, i.fin, toutes, i),)
    if i.date_debut is None or i.date_fin is None or i.date_fin < i.date_debut:
        raise ErreurDonneesInvalides(
            f"L'indisponibilité n° {i.id} n'a ni jour de la semaine ni période datée valide."
        )
    semaines_par_jour: dict[int, set[int]] = {}
    for semaine in instantane.semaines:
        jour = max(semaine.lundi, i.date_debut)
        dernier = min(semaine.dimanche, i.date_fin)
        while jour <= dernier:
            semaines_par_jour.setdefault(jour.weekday(), set()).add(semaine.numero)
            jour += dt.timedelta(days=1)
    return tuple(
        PlageInterdite(jour, i.debut, i.fin, frozenset(numeros), i)
        for jour, numeros in sorted(semaines_par_jour.items())
    )


class Regles:
    """Contraintes actives d'un calcul et recherches mises en cache sur l'instantané."""

    def __init__(self, instantane: Instantane, contraintes_desactivees: Iterable[str] = ()) -> None:
        self.instantane = instantane
        self.desactivees = normaliser_codes(contraintes_desactivees)
        self.actives = codes_actifs(self.desactivees)
        self._partages: dict[tuple[int, int], tuple[bool, bool]] = {}
        self._plages: dict[tuple[CibleIndisponibilite, int], tuple[PlageInterdite, ...]] = {}
        self._salles: dict[int, SallesCompatibles] = {}
        self._reels: dict[int, frozenset[int]] = {}
        self._lies: dict[int, frozenset[int]] = {}

    def active(self, code: CodeContrainte) -> bool:
        """Vrai si la contrainte `code` s'applique à ce calcul."""
        return code in self.actives

    # -- personnes ------------------------------------------------------------

    def enseignants_reels(self, besoin: Besoin) -> frozenset[int]:
        """Enseignants non fictifs du besoin (les seuls soumis aux contraintes d'enseignant)."""
        reels = self._reels.get(besoin.id)
        if reels is None:
            reels = frozenset(
                e for e in besoin.enseignants if not self.instantane.enseignant(e).fictif
            )
            self._reels[besoin.id] = reels
        return reels

    def groupes_lies(self, besoin: Besoin) -> frozenset[int]:
        """Groupes du besoin et tous les groupes en conflit avec l'un d'eux."""
        lies = self._lies.get(besoin.id)
        if lies is None:
            lies = frozenset().union(*(self.instantane.groupes_lies(g) for g in besoin.groupes))
            self._lies[besoin.id] = lies
        return lies

    def partage(self, a: Besoin, b: Besoin) -> tuple[bool, bool]:
        """`(enseignant commun non fictif, groupes en conflit)` pour deux besoins."""
        cle = (a.id, b.id) if a.id <= b.id else (b.id, a.id)
        resultat = self._partages.get(cle)
        if resultat is None:
            resultat = (
                not self.enseignants_reels(a).isdisjoint(self.enseignants_reels(b)),
                not self.groupes_lies(a).isdisjoint(b.groupes),
            )
            self._partages[cle] = resultat
        return resultat

    # -- indisponibilités -------------------------------------------------------

    def plages_interdites(
        self, cible: CibleIndisponibilite, ressource_id: int
    ) -> tuple[PlageInterdite, ...]:
        """Indisponibilités dures de la ressource, projetées sur la semaine type."""
        cle = (cible, ressource_id)
        plages = self._plages.get(cle)
        if plages is None:
            plages = tuple(
                plage
                for indisponibilite in self.instantane.indisponibilites_de(cible, ressource_id)
                if indisponibilite.dure
                for plage in projeter_indisponibilite(self.instantane, indisponibilite)
            )
            self._plages[cle] = plages
        return plages

    def plages_des_personnes(
        self, besoin: Besoin
    ) -> list[tuple[CodeContrainte, str, PlageInterdite]]:
        """Plages interdites à une séance du besoin par ses enseignants et ses groupes.

        Chaque entrée est `(code de la contrainte, nom de la personne, plage)` ;
        seules les contraintes de disponibilité actives sont prises en compte.
        """
        entrees: list[tuple[CodeContrainte, str, PlageInterdite]] = []
        if self.active(C.DISPO_ENSEIGNANT):
            for enseignant_id in sorted(self.enseignants_reels(besoin)):
                nom = self.instantane.enseignant(enseignant_id).nom
                for plage in self.plages_interdites(CibleIndisponibilite.ENSEIGNANT, enseignant_id):
                    entrees.append((C.DISPO_ENSEIGNANT, nom, plage))
        if self.active(C.DISPO_GROUPE):
            for groupe_id in sorted(self.groupes_lies(besoin)):
                plages = self.plages_interdites(CibleIndisponibilite.GROUPE, groupe_id)
                if plages:
                    nom = f"le groupe {self.instantane.groupe(groupe_id).code}"
                    entrees.extend((C.DISPO_GROUPE, nom, plage) for plage in plages)
        return entrees

    def indisponible_le(
        self, cible: CibleIndisponibilite, ressource_id: int, date: dt.date, debut: int, fin: int
    ) -> Indisponibilite | None:
        """Première indisponibilité dure de la ressource qui recoupe `[debut, fin[` à cette date."""
        for indisponibilite in self.instantane.indisponibilites_de(cible, ressource_id):
            if (
                indisponibilite.dure
                and indisponibilite.concerne(date)
                and se_chevauchent(debut, fin, indisponibilite.debut, indisponibilite.fin)
            ):
                return indisponibilite
        return None

    # -- salles -----------------------------------------------------------------

    def salles_compatibles(self, besoin: Besoin) -> SallesCompatibles:
        """Salles qui satisfont les exigences actives du besoin, sans regarder les horaires.

        Exigences : salle ou campus imposé et types acceptés (`TYPE_SALLE`),
        capacité (`CAPACITE`), équipements (`EQUIPEMENT`), accessibilité
        (`ACCESSIBILITE`). Les salles sortent triées par capacité puis par
        identifiant, pour un modèle reproductible.
        """
        resultat = self._salles.get(besoin.id)
        if resultat is not None:
            return resultat
        instantane = self.instantane
        tests = []
        if self.active(C.TYPE_SALLE):
            if besoin.salle_imposee_id is not None:
                tests.append(
                    (C.TYPE_SALLE, "la salle imposée", lambda s: s.id == besoin.salle_imposee_id)
                )
            if besoin.campus_impose_id is not None:
                tests.append(
                    (
                        C.TYPE_SALLE,
                        "sur le campus imposé",
                        lambda s: s.campus_id == besoin.campus_impose_id,
                    )
                )
            if besoin.types_salle_acceptes:
                tests.append(
                    (
                        C.TYPE_SALLE,
                        "d'un type de salle accepté",
                        lambda s: s.type_salle_id in besoin.types_salle_acceptes,
                    )
                )
        if self.active(C.CAPACITE):
            tests.append(
                (
                    C.CAPACITE,
                    f"d'au moins {besoin.effectif} places",
                    lambda s: s.capacite >= besoin.effectif,
                )
            )
        if self.active(C.EQUIPEMENT) and besoin.equipements_requis:
            tests.append(
                (
                    C.EQUIPEMENT,
                    "avec tous les équipements requis",
                    lambda s: besoin.equipements_requis <= s.equipements,
                )
            )
        if self.active(C.ACCESSIBILITE) and besoin.accessibilite_requise:
            tests.append((C.ACCESSIBILITE, "accessibles", instantane.salle_accessible))
        salles = tuple(
            sorted(
                (s for s in instantane.salles if all(test(s) for _, _, test in tests)),
                key=lambda s: (s.capacite, s.id),
            )
        )
        exigences = tuple(
            Exigence(code, libelle, sum(1 for s in instantane.salles if test(s)))
            for code, libelle, test in tests
        )
        resultat = SallesCompatibles(salles, exigences)
        self._salles[besoin.id] = resultat
        return resultat

    # -- paires -----------------------------------------------------------------

    def codes_violes(
        self,
        besoin_a: Besoin,
        debut_a: int,
        fin_a: int,
        salle_a: int | None,
        besoin_b: Besoin,
        debut_b: int,
        fin_b: int,
        salle_b: int | None,
    ) -> list[CodeContrainte]:
        """Contraintes de paire violées par deux séances qui coexistent.

        Les appelants garantissent la coexistence (même jour et semaines
        communes, ou même date). Sans salle connue, les règles de salle et de
        trajet ne sont pas évaluées. Deux séances d'une même personne sur
        deux campus doivent être séparées du temps de trajet : si elles se
        chevauchent, cette règle est violée elle aussi.
        """
        violes: list[CodeContrainte] = []
        chevauchement = se_chevauchent(debut_a, fin_a, debut_b, fin_b)
        if (
            chevauchement
            and salle_a is not None
            and salle_a == salle_b
            and self.active(C.SALLE_UNIQUE)
        ):
            violes.append(C.SALLE_UNIQUE)
        enseignant, groupe = self.partage(besoin_a, besoin_b)
        if not (enseignant or groupe):
            return violes
        if chevauchement:
            if enseignant and self.active(C.ENSEIGNANT_UNIQUE):
                violes.append(C.ENSEIGNANT_UNIQUE)
            if groupe and self.active(C.GROUPE_UNIQUE):
                violes.append(C.GROUPE_UNIQUE)
        libre = ecart(debut_a, fin_a, debut_b, fin_b)
        if (
            not chevauchement
            and self.active(C.PAUSE)
            and libre < self.instantane.parametres.pause_min
        ):
            violes.append(C.PAUSE)
        if self.active(C.CAMPUS_TRAJET) and salle_a is not None and salle_b is not None:
            campus_a = self.instantane.salle(salle_a).campus_id
            campus_b = self.instantane.salle(salle_b).campus_id
            if campus_a != campus_b and libre < self.instantane.trajet(campus_a, campus_b):
                violes.append(C.CAMPUS_TRAJET)
        return violes


# --------------------------------------------------------------------------- #
# Grille horaire
# --------------------------------------------------------------------------- #


def limites_demi_journee(instantane: Instantane) -> dict[tuple[int, int], int]:
    """Pour chaque `(jour, début de créneau)`, la fin du dernier créneau de sa demi-journée."""
    fins: dict[tuple[int, object], int] = {}
    for creneau in instantane.creneaux:
        cle = (creneau.jour, creneau.demi_journee)
        fins[cle] = max(fins.get(cle, 0), creneau.fin)
    limites: dict[tuple[int, int], int] = {}
    for creneau in sorted(instantane.creneaux, key=lambda c: (c.jour, c.debut, c.fin)):
        limites.setdefault(
            (creneau.jour, creneau.debut), fins[(creneau.jour, creneau.demi_journee)]
        )
    return limites


def blocs_de_la_grille(instantane: Instantane) -> list[tuple[int, int, int]]:
    """Demi-journées de la grille : `(jour, début du premier créneau, fin du dernier)`."""
    bornes: dict[tuple[int, object], tuple[int, int]] = {}
    for creneau in instantane.creneaux:
        cle = (creneau.jour, creneau.demi_journee)
        debut, fin = bornes.get(cle, (creneau.debut, creneau.fin))
        bornes[cle] = (min(debut, creneau.debut), max(fin, creneau.fin))
    return sorted((jour, debut, fin) for (jour, _), (debut, fin) in bornes.items())


# --------------------------------------------------------------------------- #
# Textes
# --------------------------------------------------------------------------- #


def nom_seance(seance: SeanceAPlacer) -> str:
    """Désignation lisible d'une séance : « libellé du besoin » (séance 12-1)."""
    return f"« {seance.besoin.libelle} » (séance {seance.id})"


def nom_creneau(jour: int, debut: int, fin: int) -> str:
    """« le lundi 8 h 30 - 10 h 30 »."""
    return f"le {JOURS[jour]} {plage_vers_texte(debut, fin)}"


def enumerer(elements: Iterable[str], maximum: int = 5) -> str:
    """« a, b et c », abrégée après `maximum` éléments (« a, b, c et 4 autres »)."""
    liste = list(elements)
    if len(liste) > maximum:
        reste = len(liste) - maximum
        liste = liste[:maximum] + [f"{reste} autre{'s' if reste > 1 else ''}"]
    if len(liste) <= 1:
        return "".join(liste)
    return ", ".join(liste[:-1]) + " et " + liste[-1]
