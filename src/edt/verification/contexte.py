"""Contexte d'une vérification : vue uniforme des séances placées et outils communs aux règles.

Les deux couches (semaine type et séances datées) se ramènent à une liste
d'`Element` : une séance placée, avec son besoin, son jour, sa plage
horaire, sa salle et, selon la couche, ses semaines actives ou sa date.
Les règles (`regles_paires`, `regles_seance`, `regles_enseignants`) ne
connaissent que cette vue et le `Contexte` ; seuls `semaine_type` et
`datee` savent d'où viennent les éléments.

Le contexte porte l'instantané, les codes actifs et des index calculés une
fois par vérification (campus, enseignants non fictifs par besoin,
groupes liés, indisponibilités à vérifier, libellés des séances).
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from edt.domaine.contraintes import CodeContrainte, Niveau, codes_par_defaut, normaliser_codes
from edt.domaine.enumerations import SAMEDI, CibleIndisponibilite, Gravite
from edt.domaine.heures import MINUTES_PAR_JOUR
from edt.domaine.instantane import (
    Besoin,
    Campus,
    Indisponibilite,
    Instantane,
    Placement,
    Salle,
)
from edt.domaine.regles import (
    NaturePersonne,
    Personne,
    ReglageDejeuner,
    ReglagePause,
    RegleResolue,
    Source,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.verification.contrat import Anomalie
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
    - `imposee` : créneau imposé (début imposé, besoin ponctuel, ou verrou
      resté à sa place : voir `creneau_impose`) ;
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


def creneau_impose(besoin: Besoin, verrou: Placement | None, jour: int, debut: int) -> bool:
    """Vrai si une séance placée le `jour` à `debut` est sur un créneau **imposé**.

    Le créneau est imposé par le besoin (début imposé ou séance ponctuelle), ou
    par un verrou **à condition d'être resté à sa place** (même jour de la
    semaine, même début) : le verrou fixe la place de la semaine type, pas une
    place choisie ensuite à la main. Une séance verrouillée déplacée ailleurs
    n'a donc plus droit aux écarts admis (information au lieu d'erreur) de
    `DEJEUNER`, `JOURS` et `HORAIRES` (a), (b), (c).
    """
    if besoin.debut_impose is not None or besoin.ponctuel:
        return True
    return verrou is not None and (verrou.jour, verrou.debut) == (jour, debut)


def valider_indisponibilite(indisponibilite: Indisponibilite) -> None:
    """Lève `ErreurDonneesInvalides` si l'indisponibilité ne décrit pas une plage vérifiable."""
    defauts: list[str] = []
    if indisponibilite.jour is not None:
        if not _est_entier(indisponibilite.jour) or not 0 <= indisponibilite.jour <= 6:
            defauts.append(f"jour {indisponibilite.jour!r} hors de 0 (lundi) à 6 (dimanche)")
    elif indisponibilite.date_debut is None or indisponibilite.date_fin is None:
        defauts.append("ni jour de la semaine ni période datée complète")
    elif indisponibilite.date_fin < indisponibilite.date_debut:
        defauts.append("la fin de la période précède son début")
    defauts.extend(defauts_de_plage(None, indisponibilite.debut, indisponibilite.fin))
    if defauts:
        raise ErreurDonneesInvalides(
            f"L'indisponibilité n° {indisponibilite.id} ({indisponibilite.cible.libelle.lower()} "
            f"n° {indisponibilite.ressource_id}) est incohérente : vérification impossible.",
            details=defauts,
        )


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
        -1 if anomalie.regle_id is None else anomalie.regle_id,
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
        self,
        instantane: Instantane,
        codes: frozenset[CodeContrainte],
        *,
        datee: bool,
        niveaux_regles: dict[int, Niveau] | None = None,
    ) -> None:
        self.instantane = instantane
        self.codes = codes
        self.datee = datee
        self.niveaux_regles: dict[int, Niveau] = dict(niveaux_regles or {})
        self._personnes: dict[int, tuple[Personne, ...]] = {}
        self._besoins_vises: dict[int, frozenset[int]] = {}
        self._connues = frozenset(emploi.personne for emploi in instantane.emplois)
        self._feuilles = frozenset(instantane.groupes_feuilles)
        self.parametres = instantane.parametres
        self.toutes_semaines = frozenset(semaine.numero for semaine in instantane.semaines)
        self._campus = {campus.id: campus for campus in instantane.campus}
        self._besoins: dict[int, Besoin] | None = None
        self._seances_par_besoin = Counter(seance.besoin.id for seance in instantane.seances)
        self._enseignants_reels: dict[int, frozenset[int]] = {}
        self._groupes_lies: dict[int, frozenset[int]] = {}
        self._libelles: dict[tuple[int, str | None], str] = {}
        self._indisponibilites: dict[
            tuple[CibleIndisponibilite, int], tuple[Indisponibilite, ...]
        ] = {}
        self._types_salle = frozenset(reference.id for reference in instantane.types_salle)
        self._equipements = frozenset(reference.id for reference in instantane.equipements)
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

    def besoin(self, besoin_id: int) -> Besoin | None:
        """Besoin de l'instantané, ou `None` s'il n'y est pas (placement d'une séance retirée).

        Pour nommer la séance dans le message qui signale ce placement : un
        besoin absent n'est pas une erreur de l'entrée, c'est l'anomalie même.
        """
        if self._besoins is None:
            self._besoins = {besoin.id: besoin for besoin in self.instantane.besoins}
        return self._besoins.get(besoin_id)

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

    # -- personnes et réglages ---------------------------------------------------

    def personnes_de(self, element: Element) -> tuple[Personne, ...]:
        """Personnes (groupes feuilles, enseignants non fictifs) dont l'emploi du temps a la séance.

        Même définition que `Instantane.emplois` (groupe feuille : séances de
        sa lignée), calculée à partir du besoin pour couvrir aussi une séance
        datée sans séance hebdomadaire. Seules les personnes qui ont un emploi
        du temps dans l'instantané sont rendues.
        """
        besoin = element.besoin
        valeur = self._personnes.get(besoin.id)
        if valeur is None:
            feuilles = sorted(
                feuille for groupe in besoin.groupes for feuille in self._feuilles_de(groupe)
            )
            personnes = [Personne(NaturePersonne.GROUPE, f) for f in dict.fromkeys(feuilles)]
            personnes += [
                Personne(NaturePersonne.ENSEIGNANT, e)
                for e in sorted(self.enseignants_reels(besoin))
            ]
            valeur = tuple(p for p in personnes if p in self._connues)
            self._personnes[besoin.id] = valeur
        return valeur

    def _feuilles_de(self, groupe: int) -> tuple[int, ...]:
        if groupe in self._feuilles:
            return (groupe,)
        return tuple(f for f in self.instantane.feuilles(groupe) if f in self._feuilles)

    def dejeuner_de(self, personne: Personne) -> ReglageDejeuner | None:
        """Réglage du déjeuner d'une personne (`None` si elle n'a pas d'emploi du temps)."""
        return self.instantane.reglages.dejeuner.get(personne)

    def pause_de(self, personne: Personne) -> ReglagePause | None:
        """Réglage de la pause d'une personne (`None` si elle n'a pas d'emploi du temps)."""
        return self.instantane.reglages.pause.get(personne)

    def entorses_jours(self, element: Element) -> tuple[Source, ...]:
        """Sources (standard ou règles) enfreintes par le jour de la séance placée.

        Les jours permis viennent de `Instantane.reglages` (standard et règles
        retenues par axe, campus de la salle compris). Pour une séance datée,
        `jours_de(seance, date)` ne retient que les règles de jours qui
        couvrent la date : hors de leur période, le standard reprend sa place,
        case « samedi autorisé » comprise. Une séance datée sans séance
        hebdomadaire n'a que le standard : jours travaillés, plus le samedi si
        son type de séance l'autorise.
        """
        campus = None if element.salle_id is None else self.salle(element.salle_id).campus_id
        if element.seance_id is not None and element.seance_id in self.instantane.reglages.jours:
            jours = self.instantane.reglages.jours_de(element.seance_id, element.date)
            return jours.entorses(element.jour, campus)
        standard = set(self.instantane.jours_travailles)
        if element.besoin.samedi_autorise:
            standard.add(SAMEDI)
        if element.jour in standard:
            return ()
        return (Source(contrainte=CodeContrainte.JOURS),)

    def semaines_regle(self, regle: RegleResolue, element: Element) -> frozenset[int] | None:
        """Semaines où la règle vaut pour la séance placée ; vide si elle ne vaut jamais.

        Semaine type : semaines actives de la séance couvertes par la règle.
        Couche datée : `None` si la date est couverte (`couvre_date`), vide sinon.
        """
        if element.date is None:
            return (element.semaines or frozenset()) & regle.semaines
        return None if self.couvre_date(regle, element.date) else frozenset()

    def couvre_date(self, regle: RegleResolue, jour: dt.date) -> bool:
        """Vrai si la période de la règle couvre la date : au jour près (`RegleResolue.couvre`),
        dans une semaine du semestre que la règle couvre."""
        semaine = self.instantane.semaine_du(jour)
        return semaine is not None and semaine.numero in regle.semaines and regle.couvre(jour)

    def restreindre(self, element: Element, regle: RegleResolue) -> Element | None:
        """La séance placée ramenée à la période de la règle, ou `None` si la règle ne la couvre pas.

        Semaine type : ses semaines actives sont réduites à celles de la règle.
        Couche datée : inchangée si la date est couverte.
        """
        if element.date is not None:
            return element if self.couvre_date(regle, element.date) else None
        actives = (element.semaines or frozenset()) & regle.semaines
        if not actives:
            return None
        return element if actives == element.semaines else replace(element, semaines=actives)

    def besoins_vises(self, regle: RegleResolue) -> frozenset[int]:
        """Besoins dont les séances sont concernées par une règle de sorte séance.

        Toutes les séances d'un besoin sont concernées ensemble (la portée
        porte sur les groupes, l'enseignant, le type, la matière ou le besoin) ;
        raisonner par besoin couvre aussi une séance datée sans séance
        hebdomadaire. Pour une portée campus, toutes : le lieu se vérifie à part.
        """
        valeur = self._besoins_vises.get(regle.id)
        if valeur is None:
            valeur = frozenset(
                seance.besoin.id for seance in self.instantane.seances if seance.id in regle.seances
            )
            self._besoins_vises[regle.id] = valeur
        return valeur

    def campus_de(self, element: Element) -> int | None:
        """Campus de la salle de la séance placée, `None` sans salle."""
        return None if element.salle_id is None else self.salle(element.salle_id).campus_id

    def nom_personne(self, personne: Personne) -> str:
        """« le groupe A1-G1 » ou le nom de l'enseignant."""
        if personne.nature is NaturePersonne.ENSEIGNANT:
            return self.nom_enseignant(personne.id)
        return f"le groupe {self.code_groupe(personne.id)}"

    def nommer_groupes(self, groupes: Iterable[int]) -> tuple[list[int], list[int]]:
        """Groupes à nommer pour un ensemble de groupes feuilles, et tous les groupes en cause.

        Un ascendant dont toutes les feuilles sont dans l'ensemble est nommé à
        leur place (une séance de promotion sans pause : « le groupe A1 »).
        Renvoie (groupes nommés, groupes en cause : feuilles et ascendants nommés).
        """
        feuilles = set(groupes)
        couverts: set[int] = set()
        nommes: list[int] = []
        candidats = sorted(
            {a for f in feuilles for a in self.instantane.ascendants(f)},
            key=lambda g: len(self.instantane.lignee(g)),
        )
        for ascendant in candidats:
            sous_feuilles = set(self._feuilles_de(ascendant))
            if sous_feuilles and sous_feuilles <= feuilles and not sous_feuilles <= couverts:
                if not any(a in nommes for a in self.instantane.ascendants(ascendant)):
                    nommes.append(ascendant)
                    couverts |= sous_feuilles
        nommes += sorted(feuilles - couverts)
        return nommes, sorted(feuilles | set(nommes))

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

    def indisponibilites_dures(
        self, cible: CibleIndisponibilite, ressource_id: int
    ) -> tuple[Indisponibilite, ...]:
        """Indisponibilités dures de la ressource à vérifier dans cette couche, validées.

        Semaine type : les **récurrentes** seulement
        (`Instantane.indisponibilites_recurrentes`) ; chacune interdit sa plage
        de son jour toutes les semaines, quelles que soient les semaines
        actives de la séance. Les indisponibilités datées relèvent de la couche
        datée (l'occurrence qui tombe dessus est replacée ou passe « à
        programmer »). Couche datée : toutes (`Instantane.indisponibilites_de`),
        à évaluer à la date avec `Indisponibilite.concerne`. Celles dont `dure`
        est faux (préférences des versions suivantes) sont écartées.
        """
        cle = (cible, ressource_id)
        valeur = self._indisponibilites.get(cle)
        if valeur is None:
            if self.datee:
                toutes = self.instantane.indisponibilites_de(cible, ressource_id)
            else:
                toutes = self.instantane.indisponibilites_recurrentes(cible, ressource_id)
            valeur = tuple(indisponibilite for indisponibilite in toutes if indisponibilite.dure)
            for indisponibilite in valeur:
                valider_indisponibilite(indisponibilite)
            self._indisponibilites[cle] = valeur
        return valeur

    # -- listes configurables ---------------------------------------------------

    def nom_type_salle(self, type_salle_id: int) -> str:
        """« « Amphithéâtre » » ; « n° 3 » si l'instantané ne porte pas la liste des types.

        Un instantané chargé depuis la base porte toujours la liste
        (`Instantane.types_salle`) ; un instantané construit à la main (tests,
        solveur) peut s'en passer : le message montre alors le numéro.
        """
        if type_salle_id in self._types_salle:
            return f"« {self.instantane.libelle_type_salle(type_salle_id)} »"
        return f"n° {type_salle_id}"

    def nom_equipement(self, equipement_id: int) -> str:
        """« « Vidéoprojecteur » » ; « n° 4 » si l'instantané ne porte pas la liste des équipements."""
        if equipement_id in self._equipements:
            return f"« {self.instantane.libelle_equipement(equipement_id)} »"
        return f"n° {equipement_id}"

    # -- textes -----------------------------------------------------------------

    def libelle(self, element: Element) -> str:
        """Nom de la séance placée pour un message (voir `libelle_seance`)."""
        return self.libelle_seance(element.besoin, element.seance_id)

    def libelle_seance(self, besoin: Besoin, seance_id: str | None) -> str:
        """Nom d'une séance pour un message, qui cite toujours ses groupes.

        Le libellé d'un besoin chargé depuis la base finit déjà par ses groupes
        (« CM Mathématiques - A1 », convention de `edt.services.instantane`) :
        il est repris tel quel. Sinon les codes des groupes sont ajoutés
        (« Mathématiques TD (A1-G1) »). Le rang est précisé quand le besoin a
        plusieurs séances dans le semestre : « CM Physique - A1 (n° 2) »,
        « Mathématiques TD n° 2 (A1-G1) ».
        """
        cle = (besoin.id, seance_id)
        valeur = self._libelles.get(cle)
        if valeur is None:
            rang = ""
            if seance_id is not None and self._seances_par_besoin[besoin.id] > 1:
                rang = seance_id.partition("-")[2]
            codes = ", ".join(sorted(self.code_groupe(groupe) for groupe in besoin.groupes))
            if not codes or besoin.libelle.endswith(f" - {codes}"):
                valeur = besoin.libelle + (f" (n° {rang})" if rang else "")
            else:
                valeur = f"{besoin.libelle}{f' n° {rang}' if rang else ''} ({codes})"
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
        regle: RegleResolue | None = None,
    ) -> Anomalie:
        """Construit une anomalie : identifiants triés et dédoublonnés, repris des éléments.

        Avec `regle`, l'anomalie porte son identifiant, son code et sa phrase
        (le code de l'anomalie est alors `REGLE`).
        """
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
            regle_id=None if regle is None else regle.id,
            regle_code=None if regle is None else regle.code,
            regle_phrase=None if regle is None else regle.phrase,
        )
