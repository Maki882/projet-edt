"""Contexte d'une vérification : vue uniforme des séances placées et outils communs aux règles.

Les deux couches (semaine type et séances datées) se ramènent à une liste
d'`Element` : une séance placée, avec son besoin, son jour, sa plage
horaire, sa salle et, selon la couche, ses semaines actives ou sa date.
Les règles (`regles_paires`, `regles_seance`, `regles_enseignants`) ne
connaissent que cette vue et le `Contexte` ; seuls `semaine_type` et
`datee` savent d'où viennent les éléments.

Le contexte porte l'instantané, les codes actifs et des index calculés une
fois par vérification (campus, enseignants non fictifs par besoin,
groupes liés, indisponibilités projetées, libellés des séances).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte, codes_par_defaut, normaliser_codes
from edt.domaine.enumerations import CibleIndisponibilite, Gravite
from edt.domaine.heures import MINUTES_PAR_JOUR
from edt.domaine.instantane import Besoin, Campus, Indisponibilite, Instantane, Salle
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.verification.contrat import Anomalie
from edt.verification.indisponibilites import PlageInterdite, projeter, valider_indisponibilite
from edt.verification.messages import (
    date_texte,
    enumerer,
    majuscule,
    nom_jour,
    plage,
    semaines_texte,
)

ORDRE_GRAVITE: dict[Gravite, int] = {
    Gravite.ERREUR: 0,
    Gravite.AVERTISSEMENT: 1,
    Gravite.INFORMATION: 2,
}
ORDRE_CODES: dict[CodeContrainte, int] = {code: rang for rang, code in enumerate(CodeContrainte)}


@dataclass(frozen=True)
class Element:
    """Une séance placée, vue commune aux deux couches.

    - semaine type : `seance_id` et `semaines` renseignés, `date` à `None` ;
    - couche datée : `date` renseignée, `semaines` à `None` ; `occurrence_id`
      vaut `None` pour une occurrence candidate pas encore enregistrée ;
    - `position` : rang dans la liste reçue (départage les doublons) ;
    - `imposee` : créneau imposé (début imposé, besoin ponctuel ou verrou) ;
    - `jour_impose` / `date_imposee` : ce que le besoin impose au placement.
    """

    besoin: Besoin
    jour: int
    debut: int
    fin: int
    salle_id: int | None
    position: int
    seance_id: str | None = None
    occurrence_id: int | None = None
    date: dt.date | None = None
    semaines: frozenset[int] | None = None
    imposee: bool = False
    jour_impose: int | None = None
    date_imposee: dt.date | None = None

    @property
    def cle_jour(self) -> int | dt.date:
        """Ce qui doit être égal pour que deux éléments aient lieu le même jour."""
        return self.date if self.date is not None else self.jour

    @property
    def duree(self) -> int:
        """Durée placée, en minutes."""
        return self.fin - self.debut


def resoudre_codes(contraintes_actives: Iterable[str] | None) -> frozenset[CodeContrainte]:
    """Codes à vérifier : ceux du catalogue actifs par défaut, ou ceux fournis (validés)."""
    if contraintes_actives is None:
        return codes_par_defaut()
    if isinstance(contraintes_actives, str):
        raise ErreurDonneesInvalides(
            "Les contraintes à vérifier se donnent sous forme de liste de codes "
            f'(par exemple ["SALLE_UNIQUE"]), pas d\'un texte seul (lu : {contraintes_actives!r}).'
        )
    return normaliser_codes(contraintes_actives)


def defauts_de_plage(jour: int | None, debut: object, fin: object) -> list[str]:
    """Défauts de structure d'une plage placée (jour, début, fin) ; liste vide si elle est saine."""
    defauts: list[str] = []
    if jour is not None and (not _est_entier(jour) or not 0 <= jour <= 6):
        defauts.append(f"jour {jour!r} hors de 0 (lundi) à 6 (dimanche)")
    bornes_valides = True
    for nom, valeur in (("début", debut), ("fin", fin)):
        if not _est_entier(valeur) or not 0 <= valeur <= MINUTES_PAR_JOUR:
            defauts.append(f"{nom} {valeur!r} hors de la journée (0 à 1440 minutes)")
            bornes_valides = False
    if bornes_valides and fin <= debut:  # type: ignore[operator]
        defauts.append(f"la fin ({fin}) ne suit pas le début ({debut})")
    return defauts


def _est_entier(valeur: object) -> bool:
    return isinstance(valeur, int) and not isinstance(valeur, bool)


def cle_naturelle_seance(identifiant: str) -> tuple[int, int, int, str]:
    """Clé de tri des identifiants de séance : « 2-1 » avant « 10-1 »."""
    besoin, _, rang = identifiant.partition("-")
    if besoin.isdigit() and rang.isdigit():
        return (0, int(besoin), int(rang), "")
    return (1, 0, 0, identifiant)


def cle_chronologique(element: Element) -> tuple:
    """Ordre de mention de séances placées dans un message, indépendant de l'ordre reçu.

    Date, jour, heures, puis séance, besoin, occurrence et salle pour départager
    deux séances aux mêmes horaires.
    """
    return (
        element.date or dt.date.min,
        element.jour,
        element.debut,
        element.fin,
        cle_naturelle_seance(element.seance_id or ""),
        element.besoin.id,
        -1 if element.occurrence_id is None else element.occurrence_id,
        -1 if element.salle_id is None else element.salle_id,
    )


def cle_tri(anomalie: Anomalie) -> tuple:
    """Clé du tri déterministe des anomalies renvoyées."""
    return (
        ORDRE_GRAVITE[anomalie.gravite],
        ORDRE_CODES[anomalie.code],
        anomalie.date or dt.date.min,
        -1 if anomalie.jour is None else anomalie.jour,
        tuple(cle_naturelle_seance(seance) for seance in anomalie.seances),
        anomalie.occurrences,
        anomalie.besoins,
        anomalie.salles,
        anomalie.enseignants,
        anomalie.groupes,
        anomalie.message,
    )


class Contexte:
    """Instantané, codes actifs et index partagés par les règles d'une vérification."""

    def __init__(
        self, instantane: Instantane, codes: frozenset[CodeContrainte], *, datee: bool
    ) -> None:
        self.instantane = instantane
        self.codes = codes
        self.datee = datee
        self.parametres = instantane.parametres
        self.toutes_semaines = frozenset(semaine.numero for semaine in instantane.semaines)
        self._campus = {campus.id: campus for campus in instantane.campus}
        self._seances_par_besoin = Counter(seance.besoin.id for seance in instantane.seances)
        self._enseignants_reels: dict[int, frozenset[int]] = {}
        self._groupes_lies: dict[int, frozenset[int]] = {}
        self._libelles: dict[tuple[int, str | None], str] = {}
        self._plages: dict[tuple[CibleIndisponibilite, int], tuple[PlageInterdite, ...]] = {}
        self._datees: dict[tuple[CibleIndisponibilite, int], tuple[Indisponibilite, ...]] = {}
        self._limites_demi_journee = self._calculer_limites_demi_journee()

    # -- codes ------------------------------------------------------------------

    def actif(self, code: CodeContrainte) -> bool:
        """Vrai si la contrainte `code` est à vérifier."""
        return code in self.codes

    # -- ressources -------------------------------------------------------------

    def salle(self, salle_id: int) -> Salle:
        """Salle de l'instantané (erreur métier si elle est inconnue)."""
        return self.instantane.salle(salle_id)

    def campus(self, campus_id: int) -> Campus:
        """Campus de l'instantané (erreur métier s'il est inconnu)."""
        try:
            return self._campus[campus_id]
        except KeyError:
            raise ErreurIntrouvable(
                f"Campus {campus_id!r} absent de l'instantané du semestre."
            ) from None

    def enseignants_reels(self, besoin: Besoin) -> frozenset[int]:
        """Enseignants non fictifs du besoin : les seuls soumis aux contraintes d'enseignant."""
        valeur = self._enseignants_reels.get(besoin.id)
        if valeur is None:
            valeur = frozenset(
                enseignant
                for enseignant in besoin.enseignants
                if not self.instantane.enseignant(enseignant).fictif
            )
            self._enseignants_reels[besoin.id] = valeur
        return valeur

    def groupes_lies(self, besoin: Besoin) -> frozenset[int]:
        """Groupes du besoin et tous les groupes en conflit avec l'un d'eux."""
        valeur = self._groupes_lies.get(besoin.id)
        if valeur is None:
            valeur = frozenset().union(
                *(self.instantane.groupes_lies(groupe) for groupe in besoin.groupes)
            )
            self._groupes_lies[besoin.id] = valeur
        return valeur

    def groupes_en_conflit(self, besoin_a: Besoin, besoin_b: Besoin) -> tuple[int, ...]:
        """Groupes des deux besoins qui participent à un conflit entre eux (triés)."""
        lies = self.instantane.groupes_lies
        groupes = {g for g in besoin_a.groupes if not lies(g).isdisjoint(besoin_b.groupes)}
        groupes |= {g for g in besoin_b.groupes if not lies(g).isdisjoint(besoin_a.groupes)}
        return tuple(sorted(groupes))

    def ressources(self, element: Element) -> dict[str, tuple[int, ...]]:
        """Toutes les ressources d'une séance placée (champs `salles`, `enseignants`, `groupes`)."""
        return {
            "salles": () if element.salle_id is None else (element.salle_id,),
            "enseignants": tuple(sorted(element.besoin.enseignants)),
            "groupes": tuple(sorted(element.besoin.groupes)),
        }

    # -- temps ------------------------------------------------------------------

    def coexistent(self, a: Element, b: Element) -> bool:
        """Vrai si les deux éléments ont lieu le même jour (et une même semaine en semaine type)."""
        if a.cle_jour != b.cle_jour:
            return False
        if a.semaines is None or b.semaines is None:
            return True
        return not a.semaines.isdisjoint(b.semaines)

    def semaines_communes(self, a: Element, b: Element) -> frozenset[int] | None:
        """Semaines actives communes (semaine type) ; `None` dans la couche datée."""
        if a.semaines is None or b.semaines is None:
            return None
        return a.semaines & b.semaines

    def limite_demi_journee(self, jour: int, debut: int) -> int | None:
        """Fin du dernier créneau de la demi-journée du créneau qui commence à `debut` le `jour`.

        `None` si aucun créneau de la grille ne commence à cette heure ce jour-là.
        """
        return self._limites_demi_journee.get((jour, debut))

    def _calculer_limites_demi_journee(self) -> dict[tuple[int, int], int]:
        fins: dict[tuple[int, object], int] = {}
        for creneau in self.instantane.creneaux:
            cle = (creneau.jour, creneau.demi_journee)
            fins[cle] = max(fins.get(cle, creneau.fin), creneau.fin)
        limites: dict[tuple[int, int], int] = {}
        for creneau in self.instantane.creneaux:
            fin = fins[(creneau.jour, creneau.demi_journee)]
            cle = (creneau.jour, creneau.debut)
            limites[cle] = max(limites.get(cle, fin), fin)
        return limites

    # -- indisponibilités -------------------------------------------------------

    def plages_interdites(
        self, cible: CibleIndisponibilite, ressource_id: int
    ) -> tuple[PlageInterdite, ...]:
        """Indisponibilités dures de la ressource projetées sur la semaine type."""
        cle = (cible, ressource_id)
        valeur = self._plages.get(cle)
        if valeur is None:
            valeur = tuple(
                plage_interdite
                for indisponibilite in self.instantane.indisponibilites_de(cible, ressource_id)
                if indisponibilite.dure
                for plage_interdite in projeter(self.instantane, indisponibilite)
            )
            self._plages[cle] = valeur
        return valeur

    def indisponibilites_dures(
        self, cible: CibleIndisponibilite, ressource_id: int
    ) -> tuple[Indisponibilite, ...]:
        """Indisponibilités dures de la ressource, validées (couche datée)."""
        cle = (cible, ressource_id)
        valeur = self._datees.get(cle)
        if valeur is None:
            valeur = tuple(
                indisponibilite
                for indisponibilite in self.instantane.indisponibilites_de(cible, ressource_id)
                if indisponibilite.dure
            )
            for indisponibilite in valeur:
                valider_indisponibilite(indisponibilite)
            self._datees[cle] = valeur
        return valeur

    # -- textes -----------------------------------------------------------------

    def libelle(self, element: Element) -> str:
        """Nom de la séance placée pour un message (voir `libelle_seance`)."""
        return self.libelle_seance(element.besoin, element.seance_id)

    def libelle_seance(self, besoin: Besoin, seance_id: str | None) -> str:
        """Nom d'une séance pour un message : « Mathématiques TD (A1-G1) ».

        Le rang (« Mathématiques TD n° 2 (A1-G1) ») est ajouté quand le besoin a
        plusieurs séances dans le semestre.
        """
        cle = (besoin.id, seance_id)
        valeur = self._libelles.get(cle)
        if valeur is None:
            rang = ""
            if seance_id is not None and self._seances_par_besoin[besoin.id] > 1:
                rang = f" n° {seance_id.partition('-')[2]}"
            codes = sorted(self.code_groupe(groupe) for groupe in besoin.groupes)
            groupes = f" ({', '.join(codes)})" if codes else ""
            valeur = f"{besoin.libelle}{rang}{groupes}"
            self._libelles[cle] = valeur
        return valeur

    def quand(self, element: Element) -> str:
        """« le mardi » (semaine type) ou « le mardi 6 octobre 2026 » (couche datée)."""
        if element.date is not None:
            return f"le {date_texte(element.date)}"
        return f"le {nom_jour(element.jour)}"

    def moment(self, element: Element) -> str:
        """« le mardi de 10 h 45 à 12 h 45 », avec la date dans la couche datée."""
        return f"{self.quand(element)} {plage(element.debut, element.fin)}"

    def precision_semaines(self, semaines: frozenset[int] | None) -> str:
        """« (semaines 3 à 7) » quand la situation ne vaut pas pour tout le semestre, sinon « »."""
        if semaines is None or not semaines or semaines >= self.toutes_semaines:
            return ""
        return f" ({semaines_texte(semaines)})"

    def nom_enseignant(self, enseignant_id: int) -> str:
        """Nom affichable de l'enseignant."""
        return self.instantane.enseignant(enseignant_id).nom

    def code_groupe(self, groupe_id: int) -> str:
        """Code affichable du groupe."""
        return self.instantane.groupe(groupe_id).code

    def personnes(self, enseignants: Iterable[int], groupes: Sequence[int]) -> tuple[str, bool]:
        """Sujet d'une phrase désignant des personnes, et vrai s'il est au pluriel.

        Exemples : (« Mme Martin », faux), (« Le groupe A1-G1 », faux),
        (« Mme Martin et les groupes A1 et A1-G1 », vrai).
        """
        morceaux = sorted(self.nom_enseignant(enseignant) for enseignant in enseignants)
        if len(groupes) == 1:
            morceaux.append(f"le groupe {self.code_groupe(groupes[0])}")
        elif groupes:
            codes = sorted(self.code_groupe(groupe) for groupe in groupes)
            morceaux.append(f"les groupes {enumerer(codes)}")
        pluriel = len(morceaux) > 1 or len(groupes) > 1
        return majuscule(enumerer(morceaux)), pluriel

    # -- anomalies --------------------------------------------------------------

    def reperes(self, element: Element) -> dict[str, object]:
        """Champ `jour` (semaine type) ou `date` (couche datée) d'une anomalie sur cet élément."""
        if element.date is not None:
            return {"date": element.date}
        return {"jour": element.jour}

    def anomalie(
        self,
        code: CodeContrainte,
        gravite: Gravite,
        message: str,
        elements: Iterable[Element] = (),
        *,
        salles: Iterable[int] = (),
        enseignants: Iterable[int] = (),
        groupes: Iterable[int] = (),
        besoins: Iterable[int] = (),
        seances: Iterable[str] = (),
        occurrences: Iterable[int] = (),
        jour: int | None = None,
        date: dt.date | None = None,
    ) -> Anomalie:
        """Construit une anomalie : identifiants triés et dédoublonnés, repris des éléments."""
        elements = tuple(elements)
        seances_concernees = set(seances) | {e.seance_id for e in elements if e.seance_id}
        occurrences = set(occurrences) | {
            e.occurrence_id for e in elements if e.occurrence_id is not None
        }
        return Anomalie(
            code=code,
            gravite=gravite,
            message=message,
            seances=tuple(sorted(seances_concernees, key=cle_naturelle_seance)),
            occurrences=tuple(sorted(occurrences)),
            salles=tuple(sorted(set(salles))),
            enseignants=tuple(sorted(set(enseignants))),
            groupes=tuple(sorted(set(groupes))),
            jour=jour,
            date=date,
            besoins=tuple(sorted(set(besoins) | {e.besoin.id for e in elements})),
        )
