"""Emploi du temps témoin : placement glouton qui prouve la faisabilité d'un jeu fictif.

Le générateur ne crée pas les besoins « à l'aveugle » : il place chaque
séance hebdomadaire d'un besoin au moment où il le crée, et n'écrit le
besoin en base que si toutes ses séances ont trouvé une place. L'ensemble
des places forme un emploi du temps **témoin** valide ; il n'est pas écrit
en base (le solveur doit trouver sa propre solution), il est renvoyé dans
le rapport.

Règles respectées (sémantique exacte du projet : `docs/verification.md`,
« Définitions communes », et `docs/architecture.md`, « Sémantique des
contraintes ») :

- deux séances *coexistent* si elles ont lieu le même jour et que leurs
  semaines actives se croisent (masques de bits) ; elles se *chevauchent* si
  en plus leurs plages `[début, fin[` se recoupent ;
- deux séances qui coexistent et partagent une salle, un enseignant non
  fictif ou des groupes en conflit sont séparées d'au moins la pause (et
  d'au moins le temps de trajet si leurs salles sont sur deux sites) ; la
  pause est aussi appliquée aux salles, ce qui est plus strict que nécessaire ;
- une séance non imposée commence sur un début de créneau, tient dans la
  plage d'ouverture et dans sa demi-journée, ne recoupe pas le déjeuner, a
  lieu du lundi au vendredi (samedi matin si son type l'autorise) ; une
  séance à créneau imposé prend exactement ce créneau ;
- la salle convient (capacité, type, équipements, accessibilité, site ou
  salle imposés) : ce filtre est fait par l'appelant (`Demande.salles`) ;
- aucune séance pendant une indisponibilité récurrente d'un enseignant,
  d'une salle ou d'un groupe lié à l'un de ses groupes.

Règles personnalisées (`docs/regles-personnalisees.md`) : l'appelant les
traduit en exigences simples que le moteur respecte sans connaître les
règles elles-mêmes :

- jours permis de chaque demande (`Demande.jours`, standard
  `Parametres.jours_travailles` et règles `JOURS_TRAVAILLES`) et plages
  interdites (`Demande.interdits` : `PLAGE_HORAIRE`, `CRENEAUX_INTERDITS`) ;
- salles déjà filtrées pour `CAMPUS` et `SALLES` ; plages interdites d'un
  enseignant (`EnseignantTemoin.interdits`) ;
- pause minimale de la demande (`Demande.pause`, la plus grande des pauses
  de ses personnes, `PAUSE_MINIMALE`) : deux séances qui partagent une
  personne sont séparées de la plus grande des deux pauses ;
- pour les groupes feuilles suivis (`Demande.personnes`) : pause déjeuner
  libre (`PAUSE_DEJEUNER`, ou le paramètre global en mode libre) et
  maximum de minutes par jour (`MAX_PAR_JOUR`), mesurés prudemment sur
  l'union des séances du jour qui ont au moins une semaine en commun avec
  la séance examinée.

Les enseignants sont choisis pendant le placement dans un **vivier** par
discipline (le moins chargé d'abord, sous un plafond hebdomadaire). Quand
aucun enseignant du vivier ne peut prendre un besoin, un enseignant
permanent est recruté et le placement est retenté une fois.

Ce module ne connaît ni l'ORM ni la base : il travaille sur des
identifiants entiers (salles, groupes, sites) et des clés d'enseignant.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from edt.domaine.enumerations import SAMEDI, DemiJournee, StatutEnseignant
from edt.domaine.parametres import Parametres

journal = logging.getLogger(__name__)

NB_JOURS = 7
ESSAIS_BARRETTE = 4
"""Recrutements tentés au plus pour qu'une barrette trouve un créneau commun."""

Occupation = tuple[int, int, int, int, int, int]
"""Place occupée dans une ressource : (début, fin, masque des semaines, site, numéro unique,
pause minimale exigée autour d'elle)."""

RAISONS: dict[str, str] = {
    "salle": "aucune salle compatible n'est libre aux créneaux où les groupes le sont",
    "groupes": "aucun créneau où tous les groupes concernés (et les groupes en conflit) sont libres",
    "enseignant": "aucun enseignant de la discipline n'est disponible à ces créneaux",
    "creneau": "aucun créneau de la grille ne peut accueillir cette durée",
}


# --------------------------------------------------------------------------- #
# Grille
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CreneauGrille:
    """Créneau de la grille horaire (minutes depuis minuit)."""

    jour: int
    debut: int
    fin: int
    demi_journee: DemiJournee


class Grille:
    """Grille horaire et règles HORAIRES, JOURS et DEJEUNER des séances non imposées."""

    def __init__(self, creneaux: Iterable[CreneauGrille], parametres: Parametres) -> None:
        self.parametres = parametres
        self._par_jour: dict[int, list[CreneauGrille]] = {}
        for creneau in sorted(creneaux, key=lambda c: (c.jour, c.debut)):
            self._par_jour.setdefault(creneau.jour, []).append(creneau)
        self._fin_demi_journee: dict[tuple[int, DemiJournee], int] = {}
        for jour, liste in self._par_jour.items():
            for creneau in liste:
                cle = (jour, creneau.demi_journee)
                self._fin_demi_journee[cle] = max(self._fin_demi_journee.get(cle, 0), creneau.fin)
        self._debuts: dict[tuple[int, int], tuple[int, ...]] = {}

    def debuts(self, jour: int, duree: int) -> tuple[int, ...]:
        """Débuts de créneau du `jour` où une séance de `duree` minutes respecte la grille.

        La séance doit tenir dans la plage d'ouverture et dans la demi-journée
        de son créneau de départ, sans recouper la plage de déjeuner quand elle
        est fixe (en mode libre, la pause se vérifie personne par personne).
        """
        cle = (jour, duree)
        if cle not in self._debuts:
            p = self.parametres
            plage = p.plage_dejeuner
            dejeuner = plage is not None and plage[0] < plage[1]
            possibles = []
            for creneau in self._par_jour.get(jour, ()):
                fin = creneau.debut + duree
                if creneau.debut < p.ouverture_min or fin > p.fermeture_min:
                    continue
                if fin > self._fin_demi_journee[(jour, creneau.demi_journee)]:
                    continue
                if dejeuner and creneau.debut < p.dejeuner_fin_min and p.dejeuner_debut_min < fin:
                    continue
                possibles.append(creneau.debut)
            self._debuts[cle] = tuple(possibles)
        return self._debuts[cle]

    def demi_journee(self, jour: int, debut: int) -> int:
        """0 pour une séance qui commence le matin, 1 l'après-midi (créneau de la grille,
        ou, pour un début hors grille, avant ou après la fin du déjeuner)."""
        for creneau in self._par_jour.get(jour, ()):
            if creneau.debut == debut:
                return 0 if creneau.demi_journee is DemiJournee.MATIN else 1
        return 0 if debut < self.parametres.dejeuner_fin_min else 1

    def minutes_ouvrees(self) -> int:
        """Minutes de créneaux des jours travaillés : la capacité hebdomadaire d'un groupe."""
        travailles = set(self.parametres.jours_travailles)
        return sum(
            creneau.fin - creneau.debut
            for jour, liste in self._par_jour.items()
            if jour in travailles
            for creneau in liste
        )


# --------------------------------------------------------------------------- #
# Ressources et demandes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SalleTemoin:
    """Salle vue par le témoin. `accessible` : convient à un groupe qui requiert l'accessibilité."""

    id: int
    code: str
    campus_id: int
    capacite: int
    type_code: str
    equipements: frozenset[str]
    accessible: bool


@dataclass(frozen=True)
class EnseignantTemoin:
    """Enseignant du vivier : identité fictive, discipline, disponibilités, plafond.

    `indispos` : plages récurrentes `(jour, début, fin)` où il ne peut pas
    enseigner (écrites en base comme indisponibilités). `interdits` : plages
    `(jour, début, fin)` exclues par une règle personnalisée (`PLAGE_HORAIRE`,
    `CRENEAUX_INTERDITS`), qui ne sont pas des indisponibilités. `plafond` :
    minutes hebdomadaires au plus (mesure prudente : toutes les séances de la
    semaine type, quelles que soient leurs semaines).
    """

    cle: int
    discipline: str
    statut: StatutEnseignant
    civilite: str | None
    nom: str
    prenom: str | None
    indispos: tuple[tuple[int, int, int], ...] = ()
    plafond: int = 15 * 60
    fictif: bool = False
    interdits: tuple[tuple[int, int, int], ...] = ()

    def disponible(self, jour: int, debut: int, fin: int) -> bool:
        """Vrai si aucune indisponibilité récurrente ni plage interdite ne recoupe la plage."""
        return not any(
            j == jour and debut < f and d < fin for j, d, f in self.indispos + self.interdits
        )


class Vivier:
    """Enseignants par discipline ; `recruter` en ajoute un quand le placement l'exige."""

    def __init__(self, fabrique: Callable[[int, str, StatutEnseignant], EnseignantTemoin]):
        self._fabrique = fabrique
        self.enseignants: list[EnseignantTemoin] = []
        self._par_discipline: dict[str, list[EnseignantTemoin]] = {}
        self.nb_recrutes = 0

    def ajouter(self, discipline: str, statut: StatutEnseignant) -> EnseignantTemoin:
        """Crée (par la fabrique) et ajoute un enseignant ; renvoie l'enseignant créé."""
        enseignant = self._fabrique(len(self.enseignants), discipline, statut)
        self.enseignants.append(enseignant)
        self._par_discipline.setdefault(discipline, []).append(enseignant)
        return enseignant

    def ajouter_existant(self, enseignant: EnseignantTemoin) -> None:
        """Ajoute un enseignant déjà construit (enseignant fictif, par exemple)."""
        self.enseignants.append(enseignant)

    def remplacer(self, enseignant: EnseignantTemoin) -> None:
        """Remplace l'enseignant de même clé (par exemple pour lui ajouter des plages interdites)."""
        for liste in (self.enseignants, self._par_discipline.get(enseignant.discipline, [])):
            for rang, existant in enumerate(liste):
                if existant.cle == enseignant.cle:
                    liste[rang] = enseignant

    def de(self, discipline: str) -> list[EnseignantTemoin]:
        """Enseignants de la discipline (liste vide si aucun)."""
        return self._par_discipline.get(discipline, [])

    def recruter(self, discipline: str) -> EnseignantTemoin:
        """Recrute un enseignant permanent de la discipline (journalisé)."""
        self.nb_recrutes += 1
        enseignant = self.ajouter(discipline, StatutEnseignant.PERMANENT)
        journal.debug(
            "Témoin : recrutement d'un enseignant de %s (clé %d)", discipline, enseignant.cle
        )
        return enseignant


@dataclass(frozen=True)
class Demande:
    """Un besoin à placer, exigences résolues.

    - `seances` : `(rang, masque des semaines actives)` de chaque séance ;
    - `lies` : groupes de la séance et tous les groupes en conflit avec eux ;
    - `salles` : salles compatibles, dans l'ordre de préférence ;
    - `jours` : jours permis (standard et règles de jours déjà résolus) ;
    - `interdits` : plages `(jour, début, fin)` que la séance ne doit pas recouper ;
    - `pause` : pause minimale exigée autour de la séance (`None` = paramètre global) ;
    - `personnes` : groupes feuilles dont l'emploi du temps contient la séance
      (le moteur ne suit que ceux qui ont un déjeuner libre ou un maximum par jour) ;
    - `jour_impose`, `debut_impose` : créneau imposé (peut sortir de la grille) ;
    - `options` : pour une séance ponctuelle dont la date reste à choisir,
      les `(jour, masque)` possibles ; le résultat dit laquelle est retenue ;
    - `enseignants_fixes` : enseignants imposés (`()` = aucun) ; sinon
      `nb_enseignants` enseignants sont choisis dans le vivier `discipline` ;
    - `freres` : groupes frères (même parent) ; les créneaux où l'un d'eux a
      déjà cours sont préférés, ce qui regroupe par exemple les TP des deux
      demi-groupes d'une classe et laisse la classe libre le reste du temps ;
    - `proches` : groupes de la demande et leurs ascendants ; les créneaux
      qu'ils occupent déjà sur d'autres semaines (semaines disjointes) sont
      préférés, ce qui enchaîne les matières courtes sur un même créneau.
    """

    cle: int
    libelle: str
    groupes: tuple[int, ...]
    lies: frozenset[int]
    salles: tuple[SalleTemoin, ...]
    duree: int
    seances: tuple[tuple[int, int], ...]
    jours: tuple[int, ...] = (0, 1, 2, 3, 4)
    interdits: tuple[tuple[int, int, int], ...] = ()
    pause: int | None = None
    personnes: tuple[int, ...] = ()
    jour_impose: int | None = None
    debut_impose: int | None = None
    options: tuple[tuple[int, int], ...] = ()
    discipline: str | None = None
    nb_enseignants: int = 0
    enseignants_fixes: tuple[EnseignantTemoin, ...] | None = None
    freres: tuple[int, ...] = ()
    proches: tuple[int, ...] = ()


@dataclass(frozen=True)
class Place:
    """Place d'une séance dans le témoin."""

    rang: int
    jour: int
    debut: int
    fin: int
    salle: SalleTemoin
    masque: int


@dataclass(frozen=True)
class Resultat:
    """Issue du placement d'une demande : places et enseignants, ou raison de l'échec."""

    cle: int
    places: tuple[Place, ...] = ()
    enseignants: tuple[EnseignantTemoin, ...] = ()
    raison: str | None = None
    option: int = -1

    @property
    def place(self) -> bool:
        """Vrai si toutes les séances de la demande ont trouvé une place."""
        return self.raison is None


@dataclass
class _Jeton:
    """Ce qu'il faut pour annuler une occupation."""

    occupation: Occupation
    listes: list[list[Occupation]]
    lies: frozenset[int]
    jour: int
    duree: int
    enseignants: tuple[int, ...] = field(default_factory=tuple)
    emplois: list[list[tuple[int, int, int, int]]] = field(default_factory=list)
    plage: tuple[int, int, int, int] = (0, 0, 0, 0)


# --------------------------------------------------------------------------- #
# Moteur
# --------------------------------------------------------------------------- #


class Temoin:
    """État d'un emploi du temps témoin en construction (un semestre).

    `placer` et `placer_barrette` engagent les places trouvées ; une demande
    qui échoue ne laisse aucune trace.
    """

    def __init__(
        self,
        grille: Grille,
        trajets: Mapping[tuple[int, int], int],
        vivier: Vivier,
        rng: random.Random,
        indispos_groupes: Mapping[int, Sequence[tuple[int, int, int]]] | None = None,
        indispos_salles: Mapping[int, Sequence[tuple[int, int, int]]] | None = None,
        dejeuners: Mapping[int, tuple[int, int, int]] | None = None,
        maxima_jour: Mapping[int, int] | None = None,
    ) -> None:
        """`dejeuners` : groupe feuille -> (début, fin, durée) d'une pause déjeuner libre ;
        `maxima_jour` : groupe feuille -> minutes de cours par jour au plus. Avec un
        déjeuner global en mode libre, chaque enseignant a aussi sa pause libre."""
        self.grille = grille
        self._pause = grille.parametres.pause_min
        self._trajet_defaut = grille.parametres.trajet_defaut_min
        self._trajets = {(min(a, b), max(a, b)): duree for (a, b), duree in trajets.items()}
        self._vivier = vivier
        self._rng = rng
        self._indispo_groupe = _indexer(indispos_groupes or {})
        self._indispo_salle = _indexer(indispos_salles or {})
        self._occ_salle: dict[int, list[list[Occupation]]] = {}
        self._occ_groupe: dict[int, list[list[Occupation]]] = {}
        self._occ_enseignant: dict[int, list[list[Occupation]]] = {}
        self._charge_jour: dict[int, list[int]] = {}
        self._charge_demi: dict[int, list[int]] = {}
        self._charge_enseignant: dict[int, int] = {}
        self._numero = 0
        self._dejeuners = dict(dejeuners or {})
        self._maxima = dict(maxima_jour or {})
        self._emploi: dict[int, list[list[tuple[int, int, int, int]]]] = {
            feuille: [[] for _ in range(NB_JOURS)] for feuille in {*self._dejeuners, *self._maxima}
        }
        self._dejeuner_enseignants = grille.parametres.fenetre_dejeuner

    # -- API ------------------------------------------------------------------

    def placer(self, demande: Demande) -> Resultat:
        """Place toutes les séances de la demande, ou aucune (avec la raison)."""
        if not demande.salles:
            return Resultat(
                demande.cle,
                raison="aucune salle de l'établissement ne remplit ses exigences "
                "(capacité, type, équipements, accessibilité, site)",
            )
        compteur: Counter[str] = Counter()
        resultat = self._essayer(demande, compteur)
        if resultat.place or not (demande.discipline and compteur["enseignant"]):
            return resultat
        self._vivier.recruter(demande.discipline)
        return self._essayer(demande, Counter())

    def placer_barrette(self, demandes: Sequence[Demande]) -> list[Resultat]:
        """Place des demandes en parallèle, au même créneau, chacune avec sa salle et son enseignant.

        Toutes les demandes doivent avoir une seule séance, la même durée et
        les mêmes semaines. Sans créneau commun, elles sont placées séparément.
        """
        if len(demandes) <= 1:
            return [self.placer(demande) for demande in demandes]
        reference = demandes[0]
        homogene = all(
            len(d.seances) == 1
            and d.duree == reference.duree
            and d.seances[0][1] == reference.seances[0][1]
            and not d.options
            and d.jour_impose is None
            and d.debut_impose is None
            and d.salles
            for d in demandes
        )
        if homogene:
            for essai in range(ESSAIS_BARRETTE + 1):
                resultats, manque = self._essayer_barrette(demandes)
                if resultats is not None:
                    return resultats
                if not manque or essai == ESSAIS_BARRETTE:
                    break
                for discipline in sorted(manque):
                    self._vivier.recruter(discipline)
            journal.info(
                "Témoin : pas de créneau commun pour la barrette « %s » (%d groupes), "
                "séances placées séparément",
                reference.libelle,
                len(demandes),
            )
        return [self.placer(demande) for demande in demandes]

    # -- placement d'une demande ----------------------------------------------

    def _essayer(self, demande: Demande, compteur: Counter[str]) -> Resultat:
        pool = self._pool(demande)
        (rang1, masque1), *autres = demande.seances
        candidats = self._candidats(demande, masque1, frozenset(), demande.groupes)
        if not candidats:
            compteur["creneau"] += 1
        for jour, debut, fin, masque, option in candidats:
            trouve = self._trouver(
                demande, jour, debut, fin, masque, demande.enseignants_fixes, pool, compteur
            )
            if trouve is None:
                continue
            salle, enseignants = trouve
            jetons = [self._occuper(demande, jour, debut, fin, masque, salle, enseignants)]
            places = [Place(rang1, jour, debut, fin, salle, masque)]
            jours_pris = {jour}
            for rang, masque_rang in autres:
                for j, d, f, m, _ in self._candidats(
                    demande, masque_rang, frozenset(jours_pris), demande.groupes
                ):
                    suite = self._trouver(demande, j, d, f, m, enseignants, None, compteur)
                    if suite is not None:
                        jetons.append(self._occuper(demande, j, d, f, m, suite[0], enseignants))
                        places.append(Place(rang, j, d, f, suite[0], m))
                        jours_pris.add(j)
                        break
                else:
                    break
            if len(places) == len(demande.seances):
                return Resultat(demande.cle, tuple(places), enseignants, None, option)
            for jeton in reversed(jetons):
                self._liberer(jeton)
        return Resultat(demande.cle, raison=_raison(compteur))

    def _essayer_barrette(
        self, demandes: Sequence[Demande]
    ) -> tuple[list[Resultat] | None, set[str]]:
        """Cherche un créneau commun ; renvoie les résultats, ou les disciplines en manque."""
        pools = [self._pool(d) for d in demandes]
        tous_groupes = tuple(sorted({g for d in demandes for g in d.groupes}))
        reference = demandes[0]
        manque: set[str] = set()
        for jour, debut, fin, masque, _ in self._candidats(
            reference, reference.seances[0][1], frozenset(), tous_groupes
        ):
            jetons: list[_Jeton] = []
            resultats: list[Resultat] = []
            for demande, pool in zip(demandes, pools, strict=True):
                compteur: Counter[str] = Counter()
                trouve = self._trouver(
                    demande, jour, debut, fin, masque, demande.enseignants_fixes, pool, compteur
                )
                if trouve is None:
                    if compteur["enseignant"] and demande.discipline:
                        manque.add(demande.discipline)
                    break
                salle, enseignants = trouve
                jetons.append(self._occuper(demande, jour, debut, fin, masque, salle, enseignants))
                resultats.append(
                    Resultat(
                        demande.cle,
                        (Place(demande.seances[0][0], jour, debut, fin, salle, masque),),
                        enseignants,
                    )
                )
            else:
                return resultats, manque
            for jeton in reversed(jetons):
                self._liberer(jeton)
        return None, manque

    def _pool(self, demande: Demande) -> list[EnseignantTemoin] | None:
        """Vivier trié (le moins chargé d'abord) si les enseignants restent à choisir."""
        if demande.enseignants_fixes is not None or not demande.discipline:
            return None
        return sorted(
            self._vivier.de(demande.discipline),
            key=lambda e: (self._charge_enseignant.get(e.cle, 0), e.cle),
        )

    def _candidats(
        self,
        demande: Demande,
        masque: int,
        jours_pris: frozenset[int],
        groupes: tuple[int, ...],
    ) -> list[tuple[int, int, int, int, int]]:
        """Créneaux possibles `(jour, début, fin, masque, option)`, les plus prometteurs d'abord.

        Ordre : jours de semaine avant le samedi, jours sans autre séance du
        même besoin d'abord, créneaux déjà utilisés par un groupe frère ou par
        les mêmes groupes sur d'autres semaines, demi-journées déjà entamées
        pour les groupes (les demi-journées entières restent libres pour les
        séances de 4 h), jours où les groupes sont le moins chargés, puis un
        tirage (déterministe) pour départager.
        """
        bruts: list[tuple[int, int, int, int]] = []
        if demande.options:
            for option, (jour, masque_option) in enumerate(demande.options):
                for debut in self._debuts(demande, jour):
                    bruts.append((jour, debut, masque_option, option))
        else:
            if demande.jour_impose is not None:
                jours: Iterable[int] = (demande.jour_impose,)
            else:
                jours = demande.jours
            for jour in jours:
                for debut in self._debuts(demande, jour):
                    bruts.append((jour, debut, masque, -1))
        bruts = [
            (jour, debut, m, option)
            for jour, debut, m, option in bruts
            if jour in demande.jours
            and not any(
                j == jour and debut < f and d < debut + demande.duree
                for j, d, f in demande.interdits
            )
        ]
        tries = []
        for jour, debut, masque_candidat, option in bruts:
            charge = max(
                (self._charge_jour[g][jour] for g in groupes if g in self._charge_jour), default=0
            )
            seul = not self._creneau_partage(
                demande, jour, debut, debut + demande.duree, masque_candidat
            )
            demi = 2 * jour + self.grille.demi_journee(jour, debut)
            entamee = any(self._charge_demi[g][demi] for g in groupes if g in self._charge_demi)
            cle = (
                jour == SAMEDI,
                jour in jours_pris,
                seul,
                not entamee,
                charge,
                self._rng.random(),
            )
            tries.append((cle, (jour, debut, debut + demande.duree, masque_candidat, option)))
        tries.sort(key=lambda element: element[0])
        return [candidat for _, candidat in tries]

    def _creneau_partage(
        self, demande: Demande, jour: int, debut: int, fin: int, masque: int
    ) -> bool:
        """Vrai si le créneau est déjà utilisé par un groupe frère (quelles que soient les
        semaines) ou par un groupe proche sur des semaines disjointes."""
        for frere in demande.freres:
            jours = self._occ_groupe.get(frere)
            if jours and any(d < fin and debut < f for d, f, _, _, _, _ in jours[jour]):
                return True
        for groupe in demande.proches:
            jours = self._occ_groupe.get(groupe)
            if jours and any(
                not m & masque and d < fin and debut < f for d, f, m, _, _, _ in jours[jour]
            ):
                return True
        return False

    def _debuts(self, demande: Demande, jour: int) -> tuple[int, ...]:
        if demande.debut_impose is not None:
            return (demande.debut_impose,)
        return self.grille.debuts(jour, demande.duree)

    def _trouver(
        self,
        demande: Demande,
        jour: int,
        debut: int,
        fin: int,
        masque: int,
        fixes: tuple[EnseignantTemoin, ...] | None,
        pool: list[EnseignantTemoin] | None,
        compteur: Counter[str],
    ) -> tuple[SalleTemoin, tuple[EnseignantTemoin, ...]] | None:
        """Première salle libre (dans l'ordre de préférence) où groupes et enseignants le sont aussi."""
        if not self._personnes_ok(demande, jour, debut, fin, masque):
            compteur["groupes"] += 1
            return None
        groupes_ok: dict[int, bool] = {}
        enseignants_ok: dict[int, tuple[EnseignantTemoin, ...] | None] = {}
        salle_libre = False
        for salle in demande.salles:
            if not self._salle_libre(salle, jour, debut, fin, masque):
                continue
            salle_libre = True
            campus = salle.campus_id
            if campus not in groupes_ok:
                groupes_ok[campus] = self._groupes_libres(demande, jour, debut, fin, masque, campus)
            if not groupes_ok[campus]:
                continue
            if campus not in enseignants_ok:
                enseignants_ok[campus] = self._choisir_enseignants(
                    demande, jour, debut, fin, masque, campus, fixes, pool
                )
            enseignants = enseignants_ok[campus]
            if enseignants is None:
                continue
            return salle, enseignants
        if not salle_libre:
            compteur["salle"] += 1
        elif not any(groupes_ok.values()):
            compteur["groupes"] += 1
        else:
            compteur["enseignant"] += 1
        return None

    # -- vérifications élémentaires -------------------------------------------

    def _trajet(self, campus_a: int, campus_b: int) -> int:
        if campus_a == campus_b:
            return 0
        return self._trajets.get(
            (min(campus_a, campus_b), max(campus_a, campus_b)), self._trajet_defaut
        )

    def _libre(
        self,
        occupations: list[Occupation],
        debut: int,
        fin: int,
        masque: int,
        campus: int,
        pause: int,
    ) -> bool:
        """Vrai si la plage respecte pause et trajet avec toutes les occupations qui coexistent.

        La pause retenue entre deux séances est la plus grande des deux pauses exigées.
        """
        for d, f, m, c, _, pause_occupation in occupations:
            if not m & masque:
                continue
            requise = max(pause, pause_occupation)
            ecart = requise if c == campus else max(requise, self._trajet(c, campus))
            if debut < f + ecart and d < fin + ecart:
                return False
        return True

    def _salle_libre(
        self, salle: SalleTemoin, jour: int, debut: int, fin: int, masque: int
    ) -> bool:
        if _bloque(self._indispo_salle, salle.id, jour, debut, fin):
            return False
        jours = self._occ_salle.get(salle.id)
        return jours is None or self._libre(
            jours[jour], debut, fin, masque, salle.campus_id, self._pause
        )

    def _groupes_libres(
        self, demande: Demande, jour: int, debut: int, fin: int, masque: int, campus: int
    ) -> bool:
        pause = self._pause_de(demande)
        for groupe in demande.lies:
            if _bloque(self._indispo_groupe, groupe, jour, debut, fin):
                return False
            jours = self._occ_groupe.get(groupe)
            if jours is not None and not self._libre(
                jours[jour], debut, fin, masque, campus, pause
            ):
                return False
        return True

    def _pause_de(self, demande: Demande) -> int:
        return self._pause if demande.pause is None else max(self._pause, demande.pause)

    def _personnes_ok(self, demande: Demande, jour: int, debut: int, fin: int, masque: int) -> bool:
        """Déjeuner libre et maximum par jour des groupes feuilles suivis de la demande."""
        for feuille in demande.personnes:
            jours = self._emploi.get(feuille)
            if jours is None:
                continue
            plages = [(d, f) for d, f, _, m in jours[jour] if m & masque] + [(debut, fin)]
            maximum = self._maxima.get(feuille)
            if maximum is not None and _duree_union(plages) > maximum:
                return False
            fenetre = self._dejeuners.get(feuille)
            if fenetre is not None and not dejeuner_libre_respecte(plages, fenetre):
                return False
        return True

    def _enseignant_libre(
        self,
        enseignant: EnseignantTemoin,
        jour: int,
        debut: int,
        fin: int,
        masque: int,
        campus: int,
        pause: int | None = None,
    ) -> bool:
        if enseignant.fictif:
            return True
        if not enseignant.disponible(jour, debut, fin):
            return False
        jours = self._occ_enseignant.get(enseignant.cle)
        if jours is None:
            return True
        if self._dejeuner_enseignants is not None and not dejeuner_libre_respecte(
            [(d, f) for d, f, m, *_ in jours[jour] if m & masque] + [(debut, fin)],
            self._dejeuner_enseignants,
        ):
            return False
        return self._libre(
            jours[jour], debut, fin, masque, campus, self._pause if pause is None else pause
        )

    def _choisir_enseignants(
        self,
        demande: Demande,
        jour: int,
        debut: int,
        fin: int,
        masque: int,
        campus: int,
        fixes: tuple[EnseignantTemoin, ...] | None,
        pool: list[EnseignantTemoin] | None,
    ) -> tuple[EnseignantTemoin, ...] | None:
        pause = self._pause_de(demande)
        if fixes is not None:
            libres = all(
                self._enseignant_libre(e, jour, debut, fin, masque, campus, pause) for e in fixes
            )
            return fixes if libres else None
        if pool is None:
            return ()
        total = demande.duree * len(demande.seances)
        choisis: list[EnseignantTemoin] = []
        for enseignant in pool:
            if self._charge_enseignant.get(enseignant.cle, 0) + total > enseignant.plafond:
                continue
            if self._enseignant_libre(enseignant, jour, debut, fin, masque, campus, pause):
                choisis.append(enseignant)
                if len(choisis) == demande.nb_enseignants:
                    return tuple(choisis)
        return None

    # -- occupation ------------------------------------------------------------

    def _occuper(
        self,
        demande: Demande,
        jour: int,
        debut: int,
        fin: int,
        masque: int,
        salle: SalleTemoin,
        enseignants: tuple[EnseignantTemoin, ...],
    ) -> _Jeton:
        self._numero += 1
        occupation: Occupation = (
            debut,
            fin,
            masque,
            salle.campus_id,
            self._numero,
            self._pause_de(demande),
        )
        listes = [_jours(self._occ_salle, salle.id)[jour]]
        listes += [_jours(self._occ_groupe, g)[jour] for g in demande.groupes]
        reels = tuple(e.cle for e in enseignants if not e.fictif)
        listes += [_jours(self._occ_enseignant, cle)[jour] for cle in reels]
        for liste in listes:
            liste.append(occupation)
        duree = fin - debut
        demi = 2 * jour + self.grille.demi_journee(jour, debut)
        for groupe in demande.lies:
            self._charge_jour.setdefault(groupe, [0] * NB_JOURS)[jour] += duree
            self._charge_demi.setdefault(groupe, [0] * (2 * NB_JOURS))[demi] += duree
        for cle in reels:
            self._charge_enseignant[cle] = self._charge_enseignant.get(cle, 0) + duree
        plage = (debut, fin, self._numero, masque)
        emplois = [self._emploi[f][jour] for f in demande.personnes if f in self._emploi]
        for emploi in emplois:
            emploi.append(plage)
        return _Jeton(occupation, listes, demande.lies, jour, duree, reels, emplois, plage)

    def _liberer(self, jeton: _Jeton) -> None:
        for liste in jeton.listes:
            liste.remove(jeton.occupation)
        for emploi in jeton.emplois:
            emploi.remove(jeton.plage)
        demi = 2 * jeton.jour + self.grille.demi_journee(jeton.jour, jeton.occupation[0])
        for groupe in jeton.lies:
            self._charge_jour[groupe][jeton.jour] -= jeton.duree
            self._charge_demi[groupe][demi] -= jeton.duree
        for cle in jeton.enseignants:
            self._charge_enseignant[cle] -= jeton.duree


def dejeuner_libre_respecte(
    plages: Iterable[tuple[int, int]], fenetre: tuple[int, int, int]
) -> bool:
    """Vrai si les plages d'une journée laissent `durée` minutes libres d'un seul tenant
    dans `[début, fin]` de la fenêtre (toujours vrai sans séance dans la fenêtre)."""
    debut_fenetre, fin_fenetre, duree = fenetre
    dedans = sorted(
        (max(d, debut_fenetre), min(f, fin_fenetre))
        for d, f in plages
        if d < fin_fenetre and debut_fenetre < f
    )
    if not dedans:
        return True
    curseur, plus_long = debut_fenetre, 0
    for d, f in dedans:
        plus_long = max(plus_long, d - curseur)
        curseur = max(curseur, f)
    return max(plus_long, fin_fenetre - curseur) >= duree


def _duree_union(plages: Iterable[tuple[int, int]]) -> int:
    """Minutes couvertes par l'union des plages (deux plages superposées comptent une fois)."""
    total, fin_courante = 0, -1
    for debut, fin in sorted(plages):
        if debut >= fin_courante:
            total += fin - debut
            fin_courante = fin
        elif fin > fin_courante:
            total += fin - fin_courante
            fin_courante = fin
    return total


def _jours(index: dict[int, list[list[Occupation]]], cle: int) -> list[list[Occupation]]:
    jours = index.get(cle)
    if jours is None:
        jours = [[] for _ in range(NB_JOURS)]
        index[cle] = jours
    return jours


def _indexer(
    plages: Mapping[int, Sequence[tuple[int, int, int]]],
) -> dict[int, dict[int, list[tuple[int, int]]]]:
    """`ressource -> [(jour, début, fin)]` devient `ressource -> jour -> [(début, fin)]`."""
    index: dict[int, dict[int, list[tuple[int, int]]]] = {}
    for ressource, liste in plages.items():
        for jour, debut, fin in liste:
            index.setdefault(ressource, {}).setdefault(jour, []).append((debut, fin))
    return index


def _bloque(
    index: dict[int, dict[int, list[tuple[int, int]]]], cle: int, jour: int, debut: int, fin: int
) -> bool:
    jours = index.get(cle)
    if jours is None:
        return False
    return any(debut < f and d < fin for d, f in jours.get(jour, ()))


def _raison(compteur: Counter[str]) -> str:
    """Raison lisible d'un échec : la cause la plus fréquente, avec les décomptes."""
    if not compteur:
        return RAISONS["creneau"]
    cause, _ = max(compteur.items(), key=lambda element: (element[1], element[0]))
    details = ", ".join(f"{cle} : {nombre}" for cle, nombre in sorted(compteur.items()))
    return f"{RAISONS[cause]} (créneaux écartés par cause : {details})"
