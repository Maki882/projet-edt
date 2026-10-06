"""Règles partagées par le moteur, les pré-vérifications et la couche datée.

Ce module ne connaît ni CP-SAT ni la base : il lit l'instantané et répond à
des questions simples, avec les mêmes définitions que le vérificateur
(`docs/architecture.md`, « Sémantique des contraintes »).

Définitions
-----------
- deux séances **coexistent** si elles ont lieu le même jour : même jour de
  la semaine et semaines actives qui se croisent (semaine type), ou même date
  (couche datée) ;
- elles **se chevauchent** si, en plus, leurs plages `[debut, fin[` se
  recoupent ;
- elles **partagent une personne** si elles ont un enseignant non fictif en
  commun ou si leurs besoins sont en conflit de groupes ;
- sur la **semaine type**, seules les indisponibilités **récurrentes** et
  dures (`dure` vrai) comptent : chacune interdit, toutes les semaines, la
  plage `[debut, fin[` de son jour, quelles que soient les semaines actives
  de la séance (`Regles.recurrentes`). Les indisponibilités **datées**
  relèvent de la couche datée, comme les jours fermés
  (`Regles.indisponible_le`).

`Regles` regroupe les contraintes actives d'un calcul et met en cache les
recherches répétées (personnes partagées, salles compatibles,
indisponibilités).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte, codes_actifs, normaliser_codes
from edt.domaine.enumerations import JOURS, SAMEDI, CibleIndisponibilite
from edt.domaine.heures import (
    MINUTES_PAR_JOUR,
    duree_vers_texte,
    ecart,
    plage_vers_texte,
    se_chevauchent,
)
from edt.domaine.instantane import Besoin, Indisponibilite, Instantane, Salle, SeanceAPlacer
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable

C = CodeContrainte

TestSalle = Callable[[Salle], bool]

PAS_HORS_GRILLE = 30
"""Pas des débuts proposés (minutes) quand la contrainte `HORAIRES` est désactivée."""


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


class Regles:
    """Contraintes actives d'un calcul et recherches mises en cache sur l'instantané."""

    def __init__(self, instantane: Instantane, contraintes_desactivees: Iterable[str] = ()) -> None:
        self.instantane = instantane
        self.desactivees = normaliser_codes(contraintes_desactivees)
        self.actives = codes_actifs(self.desactivees)
        self._partages: dict[tuple[int, int], tuple[bool, bool]] = {}
        self._recurrentes: dict[tuple[CibleIndisponibilite, int], tuple[Indisponibilite, ...]] = {}
        self._salles: dict[int, SallesCompatibles] = {}
        self._reels: dict[int, frozenset[int]] = {}
        self._lies: dict[int, frozenset[int]] = {}
        self._limites = limites_demi_journee(instantane)

    def active(self, code: CodeContrainte) -> bool:
        """Vrai si la contrainte `code` s'applique à ce calcul."""
        return code in self.actives

    # -- jours et horaires --------------------------------------------------------

    def jour_permis(self, jour: int, besoin: Besoin) -> bool:
        """Règle `JOURS` : un jour de la grille, et le samedi seulement s'il est autorisé."""
        if not self.instantane.debuts_de_creneau(jour):
            return False
        return jour != SAMEDI or besoin.samedi_autorise

    def debuts_a_examiner(self, besoin: Besoin, jour: int) -> tuple[int, ...]:
        """Heures de début envisageables ce jour-là, avant toute règle.

        L'heure imposée par le besoin s'il y en a une ; sinon les débuts de
        créneau de la grille ; sinon, quand `HORAIRES` est désactivée, un
        début toutes les `PAS_HORS_GRILLE` minutes en plus de ceux de la grille.
        """
        if besoin.debut_impose is not None:
            return (besoin.debut_impose,)
        grille = self.instantane.debuts_de_creneau(jour)
        if self.active(C.HORAIRES):
            return grille
        return tuple(sorted(set(range(0, MINUTES_PAR_JOUR, PAS_HORS_GRILLE)) | set(grille)))

    def refus_horaire(
        self, besoin: Besoin, duree: int, jour: int, minute: int
    ) -> tuple[CodeContrainte, str] | None:
        """Raison pour laquelle la séance ne peut pas commencer à cette heure, sinon `None`.

        Règles `HORAIRES` (ouverture, fermeture, demi-journée du créneau de
        début) et `DEJEUNER` (seulement si la pause déjeuner a des horaires
        déterminés : `Parametres.plage_dejeuner`). Une heure imposée par le
        besoin en dispense : la séance peut alors sortir de la grille. Aucune
        séance ne passe minuit.
        """
        parametres = self.instantane.parametres
        fin = minute + duree
        if fin > MINUTES_PAR_JOUR:
            return C.HORAIRES, "la séance finirait après minuit"
        if besoin.debut_impose is not None:
            return None
        if self.active(C.HORAIRES) and (
            minute < parametres.ouverture_min
            or fin > parametres.fermeture_min
            or fin > self._limites.get((jour, minute), 0)
        ):
            return (
                C.HORAIRES,
                f"la séance de {duree_vers_texte(duree)} ne tient pas dans la demi-journée ou "
                "dans les heures d'ouverture",
            )
        dejeuner = parametres.plage_dejeuner
        if (
            self.active(C.DEJEUNER)
            and dejeuner is not None
            and se_chevauchent(minute, fin, *dejeuner)
        ):
            return C.DEJEUNER, "la pause déjeuner"
        return None

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

    def recurrentes(
        self, cible: CibleIndisponibilite, ressource_id: int
    ) -> tuple[Indisponibilite, ...]:
        """Indisponibilités récurrentes et dures de la ressource : celles de la semaine type.

        Une plage horaire ou un jour incohérent lève `ErreurDonneesInvalides` :
        mieux vaut refuser le calcul qu'ignorer l'indisponibilité en silence.
        """
        cle = (cible, ressource_id)
        plages = self._recurrentes.get(cle)
        if plages is None:
            plages = tuple(
                indisponibilite
                for indisponibilite in self.instantane.indisponibilites_recurrentes(
                    cible, ressource_id
                )
                if indisponibilite.dure
            )
            for plage in plages:
                if not (0 <= plage.jour <= 6 and 0 <= plage.debut < plage.fin <= MINUTES_PAR_JOUR):
                    raise ErreurDonneesInvalides(
                        f"L'indisponibilité n° {plage.id} est incohérente (jour {plage.jour}, "
                        f"de {plage.debut} à {plage.fin} minutes) : corriger la saisie avant "
                        "de lancer un calcul."
                    )
            self._recurrentes[cle] = plages
        return plages

    def recurrentes_des_personnes(
        self, besoin: Besoin
    ) -> list[tuple[CodeContrainte, str, Indisponibilite]]:
        """Indisponibilités récurrentes des enseignants et des groupes d'un besoin.

        Chaque entrée est `(code de la contrainte, nom de la personne,
        indisponibilité)` ; seules les contraintes de disponibilité actives
        sont prises en compte. Groupes : ceux du besoin et tous les groupes liés.
        """
        entrees: list[tuple[CodeContrainte, str, Indisponibilite]] = []
        if self.active(C.DISPO_ENSEIGNANT):
            for enseignant_id in sorted(self.enseignants_reels(besoin)):
                nom = self.instantane.enseignant(enseignant_id).nom
                for plage in self.recurrentes(CibleIndisponibilite.ENSEIGNANT, enseignant_id):
                    entrees.append((C.DISPO_ENSEIGNANT, nom, plage))
        if self.active(C.DISPO_GROUPE):
            for groupe_id in sorted(self.groupes_lies(besoin)):
                plages = self.recurrentes(CibleIndisponibilite.GROUPE, groupe_id)
                if plages:
                    nom = f"le groupe {self.instantane.groupe(groupe_id).code}"
                    entrees.extend((C.DISPO_GROUPE, nom, plage) for plage in plages)
        return entrees

    def indisponible_le(
        self, cible: CibleIndisponibilite, ressource_id: int, date: dt.date, debut: int, fin: int
    ) -> Indisponibilite | None:
        """Première indisponibilité dure, récurrente ou datée, qui recoupe `[debut, fin[` ce jour-là.

        C'est la règle de la couche datée.
        """
        for indisponibilite in self.instantane.indisponibilites_de(cible, ressource_id):
            if (
                indisponibilite.dure
                and indisponibilite.concerne(date)
                and se_chevauchent(debut, fin, indisponibilite.debut, indisponibilite.fin)
            ):
                return indisponibilite
        return None

    # -- salles -----------------------------------------------------------------

    @staticmethod
    def _libelle(chercher: Callable[[int], str], identifiant: int, prefixe: str) -> str:
        """Libellé d'une référence pour un message, ou son numéro si la liste ne la connaît pas."""
        try:
            return chercher(identifiant)
        except ErreurIntrouvable:
            return f"{prefixe}{identifiant}"

    def exigences_de_salle(self, besoin: Besoin) -> list[tuple[CodeContrainte, str, TestSalle]]:
        """Exigences actives du besoin sur sa salle : `(code, libellé, test sur une salle)`.

        Salle ou campus imposé et types acceptés (`TYPE_SALLE`), capacité
        (`CAPACITE`), équipements (`EQUIPEMENT`), accessibilité (`ACCESSIBILITE`).
        Le libellé complète la phrase « n salles sont ... ».
        """
        instantane = self.instantane
        tests: list[tuple[CodeContrainte, str, TestSalle]] = []
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
                types = enumerer(
                    sorted(
                        self._libelle(instantane.libelle_type_salle, type_id, "type n° ")
                        for type_id in besoin.types_salle_acceptes
                    ),
                    conjonction="ou",
                )
                tests.append(
                    (
                        C.TYPE_SALLE,
                        f"du type demandé ({types})",
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
            equipements = enumerer(
                sorted(
                    self._libelle(instantane.libelle_equipement, equipement_id, "équipement n° ")
                    for equipement_id in besoin.equipements_requis
                )
            )
            tests.append(
                (
                    C.EQUIPEMENT,
                    f"équipées de tout le matériel requis ({equipements})",
                    lambda s: besoin.equipements_requis <= s.equipements,
                )
            )
        if self.active(C.ACCESSIBILITE) and besoin.accessibilite_requise:
            tests.append((C.ACCESSIBILITE, "accessibles", instantane.salle_accessible))
        return tests

    def salles_compatibles(self, besoin: Besoin) -> SallesCompatibles:
        """Salles qui satisfont les exigences actives du besoin, sans regarder les horaires.

        Les salles sortent triées par capacité puis par identifiant, pour un
        modèle reproductible.
        """
        resultat = self._salles.get(besoin.id)
        if resultat is None:
            tests = self.exigences_de_salle(besoin)
            salles = tuple(
                sorted(
                    (s for s in self.instantane.salles if all(test(s) for _, _, test in tests)),
                    key=lambda s: (s.capacite, s.id),
                )
            )
            exigences = tuple(
                Exigence(code, libelle, sum(1 for s in self.instantane.salles if test(s)))
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
        trajet ne sont pas évaluées. Comme dans le vérificateur, la pause et
        le trajet ne concernent que deux séances qui ne se chevauchent pas :
        un chevauchement relève des contraintes d'unicité.
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
            return violes
        libre = ecart(debut_a, fin_a, debut_b, fin_b)
        if self.active(C.PAUSE) and libre < self.instantane.parametres.pause_min:
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


def enumerer(elements: Iterable[str], maximum: int = 5, conjonction: str = "et") -> str:
    """« a, b et c », abrégée après `maximum` éléments (« a, b, c et 4 autres »)."""
    liste = list(elements)
    if len(liste) > maximum:
        reste = len(liste) - maximum
        liste = liste[:maximum] + [f"{reste} autre{'s' if reste > 1 else ''}"]
    if len(liste) <= 1:
        return "".join(liste)
    return ", ".join(liste[:-1]) + f" {conjonction} " + liste[-1]
