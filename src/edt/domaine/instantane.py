"""Instantané d'un semestre à planifier : LE contrat commun au vérificateur et au solveur.

Un `Instantane` est une photographie figée (dataclasses immuables, aucune
dépendance à l'ORM) de tout ce qu'il faut pour placer ou vérifier les
séances d'un semestre. Il est construit par
`edt.services.instantane.charger_instantane` ; le vérificateur
(`edt.verification`) et le solveur (`edt.solveur`) ne lisent rien d'autre.

Vocabulaire
-----------
- **Besoin** : la demande (matière, type, groupes, enseignants, durée,
  exigences de salle, volume). Les exigences y sont déjà *résolues* :
  effectif total, besoin d'accessibilité, types de salle acceptés.
- **SeanceAPlacer** : une séance hebdomadaire issue du dépliage d'un besoin
  (`edt.domaine.depliage`), avec ses **semaines actives**. Deux séances dont
  les semaines actives sont disjointes ne se gênent jamais : elles peuvent
  partager créneau, salle, enseignant et groupe.
- **Placement** : la réponse pour une séance (jour, début, fin, salle).
- **Occurrence** : une séance datée (couche calendrier).

Conventions
-----------
- heures en minutes depuis minuit, plages semi-ouvertes `[debut, fin[` ;
- jours de 0 (lundi) à 6 (dimanche) ;
- semaines désignées par leur numéro relatif au semestre (dès 1) ;
- identifiant d'une séance : `"<besoin_id>-<rang>"` (voir `id_seance`) ;
- un enseignant `fictif` n'entre dans **aucune** contrainte d'enseignant
  (unicité, disponibilité, maxima, pause, déjeuner, trajet) ;
- une séance requiert une salle accessible si l'un de ses groupes porte le
  drapeau « salle accessible requise » ou est en conflit avec un groupe qui
  le porte (calculé au chargement : `Besoin.accessibilite_requise`) ;
- `types_salle_acceptes` vide signifie « tous les types » ;
- sur la **semaine type**, seules les indisponibilités **récurrentes**
  comptent (`indisponibilites_recurrentes`) ; les indisponibilités **datées**
  relèvent de la couche datée, comme les jours fermés : l'occurrence qui
  tombe dessus est replacée ou passe « à programmer » ;
- les jours fermés ne concernent que la couche datée (`JOUR_FERME`), sauf
  une semaine entièrement fermée, qui est retirée des semaines actives par
  le dépliage.

Règles personnalisées et réglages
---------------------------------
- `regles` : les règles **actives** de l'année dont la période recoupe le
  semestre, résolues (`edt.domaine.regles.RegleResolue`) : valeurs
  converties, semaines couvertes, séances et personnes concernées, phrase.
  Le solveur et le vérificateur reçoivent en plus un `dict[int, Niveau]`
  (identifiant de règle → niveau retenu pour le calcul) : une règle absente
  de ce dictionnaire est ignorée pour ce calcul.
- `emplois` : l'emploi du temps de chaque **personne** (groupe feuille avec
  les séances de sa lignée, enseignant non fictif avec les siennes) :
  définition unique des trous, des indicateurs et des règles de sorte
  « emploi du temps ».
- `reglages` : pour chaque séance, ses jours permis (standard et règles
  retenues par axe, campus compris) ; pour chaque personne, sa pause déjeuner
  et sa pause minimale, chacune avec sa source (une règle ou le paramètre
  global). Le solveur et le vérificateur lisent ces valeurs et non plus
  directement `Parametres.plage_dejeuner`, `fenetre_dejeuner`, `pause_min` ni
  `Besoin.samedi_autorise` (conservés comme façades).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cached_property

from edt.domaine.calendrier import Semaine, semaine_de
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import (
    Accessibilite,
    CibleIndisponibilite,
    DemiJournee,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    StatutEnseignant,
    StatutOccurrence,
)
from edt.domaine.parametres import Parametres
from edt.domaine.regles import (
    Emploi,
    NaturePersonne,
    Personne,
    Reglages,
    RegleResolue,
    resoudre_reglages,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable


def id_seance(besoin_id: int, rang: int) -> str:
    """Identifiant stable d'une séance à placer : `"<besoin_id>-<rang>"`."""
    return f"{besoin_id}-{rang}"


def decomposer_id_seance(identifiant: str) -> tuple[int, int]:
    """Inverse de `id_seance` : renvoie `(besoin_id, rang)`.

    Seuls des chiffres décimaux ASCII sont admis de part et d'autre du tiret :
    `isdigit()` accepterait « ² » ou des chiffres d'autres écritures, puis
    `int()` lèverait une `ValueError` brute. Tout autre texte est une
    `ErreurDonneesInvalides` qui nomme l'identifiant.
    """
    besoin, _, rang = str(identifiant).partition("-")
    if not all(partie.isascii() and partie.isdecimal() for partie in (besoin, rang)):
        raise ErreurDonneesInvalides(
            f"Identifiant de séance invalide : {identifiant!r} (attendu « <besoin>-<rang> », "
            "deux nombres entiers)."
        )
    return int(besoin), int(rang)


# --------------------------------------------------------------------------- #
# Référentiels
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Creneau:
    """Créneau de la grille horaire."""

    jour: int
    debut: int
    fin: int
    demi_journee: DemiJournee


@dataclass(frozen=True)
class Campus:
    """Site d'enseignement."""

    id: int
    code: str
    nom: str


@dataclass(frozen=True)
class Trajet:
    """Temps de trajet (minutes) entre deux campus, symétrique."""

    campus_a_id: int
    campus_b_id: int
    duree: int


@dataclass(frozen=True)
class Reference:
    """Entrée d'une liste configurable (type de salle, équipement), pour nommer les choses."""

    id: int
    code: str
    libelle: str


@dataclass(frozen=True)
class Salle:
    """Salle de cours."""

    id: int
    code: str
    campus_id: int
    capacite: int
    type_salle_id: int
    equipements: frozenset[int]
    accessibilite: Accessibilite

    def est_accessible(self, inconnue_acceptee: bool) -> bool:
        """Vrai si la salle convient à un groupe qui requiert l'accessibilité."""
        if self.accessibilite is Accessibilite.OUI:
            return True
        return inconnue_acceptee and self.accessibilite is Accessibilite.INCONNU


@dataclass(frozen=True)
class Enseignant:
    """Enseignant. `fictif` : exclu de toutes les contraintes d'enseignant."""

    id: int
    identifiant: str
    nom: str
    statut: StatutEnseignant
    max_minutes_jour: int | None
    max_minutes_semaine: int | None
    fictif: bool


@dataclass(frozen=True)
class Groupe:
    """Groupe d'étudiants."""

    id: int
    code: str
    libelle: str
    nature: NatureGroupe
    parent_id: int | None
    effectif: int
    accessibilite_requise: bool


@dataclass(frozen=True)
class Indisponibilite:
    """Indisponibilité d'une ressource, récurrente (`jour`) ou datée (`date_debut`..`date_fin`)."""

    id: int
    cible: CibleIndisponibilite
    ressource_id: int
    jour: int | None
    date_debut: dt.date | None
    date_fin: dt.date | None
    debut: int
    fin: int
    motif: str
    dure: bool = True
    poids: int = 0

    @property
    def recurrente(self) -> bool:
        """Vrai pour la forme récurrente (chaque semaine, le même jour)."""
        return self.jour is not None

    def concerne(self, jour: dt.date) -> bool:
        """Vrai si l'indisponibilité s'applique à la date `jour` (couche datée)."""
        if self.jour is not None:
            return jour.weekday() == self.jour
        return (
            self.date_debut is not None
            and self.date_fin is not None
            and self.date_debut <= jour <= self.date_fin
        )


@dataclass(frozen=True)
class JourFerme:
    """Fermeture du calendrier sur `[date_debut, date_fin]`.

    `groupes` vaut `None` pour une fermeture globale ; sinon c'est l'ensemble
    des groupes concernés (le groupe désigné et tous ses descendants).
    """

    date_debut: dt.date
    date_fin: dt.date
    libelle: str
    nature: NatureJourFerme
    groupes: frozenset[int] | None = None

    def concerne(self, jour: dt.date, groupes: Iterable[int], ponctuelle: bool = False) -> bool:
        """Vrai si une séance des groupes `groupes` ne peut pas avoir lieu le `jour`.

        Une séance `ponctuelle` (saisie à la main) reste permise pendant une
        période d'examens ou une semaine spéciale.
        """
        if not self.date_debut <= jour <= self.date_fin:
            return False
        if ponctuelle and self.nature.admet_ponctuels:
            return False
        return self.groupes is None or not self.groupes.isdisjoint(groupes)


# --------------------------------------------------------------------------- #
# Demande et réponse
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Besoin:
    """Demande d'enseignement, exigences résolues."""

    id: int
    libelle: str
    matiere_id: int
    matiere_code: str
    type_seance_id: int
    type_seance_code: str
    samedi_autorise: bool
    duree: int
    groupes: frozenset[int]
    enseignants: frozenset[int]
    effectif: int
    accessibilite_requise: bool
    types_salle_acceptes: frozenset[int] = frozenset()
    equipements_requis: frozenset[int] = frozenset()
    salle_imposee_id: int | None = None
    campus_impose_id: int | None = None
    jour_impose: int | None = None
    debut_impose: int | None = None
    mode_volume: ModeVolume = ModeVolume.PAR_SEMAINE
    nombre: int = 1
    max_par_semaine: int = 1
    date_debut: dt.date | None = None
    date_fin: dt.date | None = None
    date_ponctuelle: dt.date | None = None

    @property
    def ponctuel(self) -> bool:
        """Vrai pour une séance datée saisie à la main."""
        return self.mode_volume is ModeVolume.PONCTUEL


@dataclass(frozen=True)
class Placement:
    """Place d'une séance dans la semaine type.

    `salle_id` vaut `None` seulement pour une séance importée ou verrouillée
    dont la salle reste à définir ; une solution du solveur a toujours une salle.
    """

    seance_id: str
    jour: int
    debut: int
    fin: int
    salle_id: int | None


@dataclass(frozen=True)
class SeanceAPlacer:
    """Séance hebdomadaire à placer.

    - `semaines` : numéros des semaines où la séance a lieu ;
    - `verrou` : placement imposé par un verrou (contrainte `VERROU`) ;
    - `indice` : placement de la version précédente, à donner comme point de
      départ au solveur (sans obligation).
    """

    id: str
    besoin: Besoin
    rang: int
    semaines: frozenset[int]
    verrou: Placement | None = None
    indice: Placement | None = None

    @property
    def duree(self) -> int:
        """Durée de la séance, en minutes."""
        return self.besoin.duree

    @property
    def jour_impose(self) -> int | None:
        """Jour imposé : celui de la date pour une séance ponctuelle, sinon celui du besoin."""
        if self.besoin.date_ponctuelle is not None:
            return self.besoin.date_ponctuelle.weekday()
        return self.besoin.jour_impose

    @property
    def debut_impose(self) -> int | None:
        """Heure de début imposée, si le besoin en fixe une."""
        return self.besoin.debut_impose


@dataclass(frozen=True)
class ProblemeDepliage:
    """Besoin qui ne peut pas être entièrement déplié en séances.

    `code` est la contrainte que le problème empêche de respecter (`VOLUME`
    ou `JOUR_FERME`) : un calcul est refusé tant que cette contrainte est active.
    """

    besoin_id: int
    code: CodeContrainte
    message: str


@dataclass(frozen=True)
class Occurrence:
    """Séance datée (couche calendrier).

    `id` est l'identifiant en base, ou `None` pour une occurrence candidate
    pas encore enregistrée. `seance_id` relie à la séance hebdomadaire
    d'origine quand il y en a une.
    """

    id: int | None
    besoin_id: int
    seance_id: str | None
    date: dt.date
    debut: int
    fin: int
    salle_id: int | None
    statut: StatutOccurrence = StatutOccurrence.PLANIFIEE


# --------------------------------------------------------------------------- #
# L'instantané
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Instantane:
    """Tout ce qu'il faut pour planifier ou vérifier un semestre.

    `conflits_groupes` contient les paires `(a, b)` avec `a < b` de groupes
    qui ne peuvent pas avoir cours en même temps (voir
    `edt.services.conflits_groupes`) ; utiliser `groupes_en_conflit`.

    `types_salle` et `equipements` sont les listes configurables, utiles pour
    nommer un type ou un équipement dans un message (`libelle_type_salle`,
    `libelle_equipement`).

    `regles` sont les règles personnalisées actives résolues (voir le module) ;
    `emplois` et `reglages` en découlent, calculés une fois à la demande.
    """

    semestre_id: int
    libelle: str
    date_debut: dt.date
    date_fin: dt.date
    semaines: tuple[Semaine, ...]
    creneaux: tuple[Creneau, ...]
    campus: tuple[Campus, ...]
    trajets: tuple[Trajet, ...]
    salles: tuple[Salle, ...]
    enseignants: tuple[Enseignant, ...]
    groupes: tuple[Groupe, ...]
    conflits_groupes: frozenset[tuple[int, int]]
    indisponibilites: tuple[Indisponibilite, ...]
    jours_fermes: tuple[JourFerme, ...]
    parametres: Parametres
    besoins: tuple[Besoin, ...]
    seances: tuple[SeanceAPlacer, ...]
    problemes_depliage: tuple[ProblemeDepliage, ...] = ()
    version_base_id: int | None = field(default=None)
    types_salle: tuple[Reference, ...] = ()
    equipements: tuple[Reference, ...] = ()
    regles: tuple[RegleResolue, ...] = ()

    # -- accès par identifiant ------------------------------------------------

    @cached_property
    def _salles(self) -> dict[int, Salle]:
        return {salle.id: salle for salle in self.salles}

    @cached_property
    def _enseignants(self) -> dict[int, Enseignant]:
        return {enseignant.id: enseignant for enseignant in self.enseignants}

    @cached_property
    def _groupes(self) -> dict[int, Groupe]:
        return {groupe.id: groupe for groupe in self.groupes}

    @cached_property
    def _besoins(self) -> dict[int, Besoin]:
        return {besoin.id: besoin for besoin in self.besoins}

    @cached_property
    def _seances(self) -> dict[str, SeanceAPlacer]:
        return {seance.id: seance for seance in self.seances}

    @cached_property
    def _semaines(self) -> dict[int, Semaine]:
        return {semaine.numero: semaine for semaine in self.semaines}

    def salle(self, salle_id: int) -> Salle:
        """Salle d'identifiant `salle_id` (erreur si elle n'est pas dans l'instantané)."""
        return _trouver(self._salles, salle_id, "Salle")

    def enseignant(self, enseignant_id: int) -> Enseignant:
        """Enseignant d'identifiant `enseignant_id`."""
        return _trouver(self._enseignants, enseignant_id, "Enseignant")

    def groupe(self, groupe_id: int) -> Groupe:
        """Groupe d'identifiant `groupe_id`."""
        return _trouver(self._groupes, groupe_id, "Groupe")

    def besoin(self, besoin_id: int) -> Besoin:
        """Besoin d'identifiant `besoin_id`."""
        return _trouver(self._besoins, besoin_id, "Besoin")

    def seance(self, seance_id: str) -> SeanceAPlacer:
        """Séance à placer d'identifiant `seance_id`."""
        return _trouver(self._seances, seance_id, "Séance")

    def semaine(self, numero: int) -> Semaine:
        """Semaine de numéro `numero` (relatif au semestre, dès 1)."""
        return _trouver(self._semaines, numero, "Semaine")

    # -- calendrier -------------------------------------------------------------

    def date_de(self, numero_semaine: int, jour: int) -> dt.date:
        """Date du jour `jour` (0 = lundi) de la semaine `numero_semaine`."""
        return self.semaine(numero_semaine).date_du(jour)

    def semaine_du(self, jour: dt.date) -> Semaine | None:
        """Semaine du semestre qui contient la date, ou `None` hors semestre."""
        return semaine_de(jour, self.semaines)

    def fermeture(self, jour: dt.date, besoin: Besoin) -> JourFerme | None:
        """Première fermeture qui interdit une séance de `besoin` à la date `jour`, sinon `None`."""
        for ferme in self.jours_fermes:
            if ferme.concerne(jour, besoin.groupes, ponctuelle=besoin.ponctuel):
                return ferme
        return None

    @cached_property
    def _debuts(self) -> dict[int, tuple[int, ...]]:
        debuts: dict[int, list[int]] = {}
        for creneau in self.creneaux:
            debuts.setdefault(creneau.jour, []).append(creneau.debut)
        return {jour: tuple(sorted(valeurs)) for jour, valeurs in debuts.items()}

    def debuts_de_creneau(self, jour: int) -> tuple[int, ...]:
        """Heures de début des créneaux de la grille pour `jour` (vide si aucun créneau)."""
        return self._debuts.get(jour, ())

    @property
    def jours_travailles(self) -> frozenset[int]:
        """Jours travaillés de l'établissement (le standard des jours, voir `reglages`)."""
        return frozenset(self.parametres.jours_travailles)

    # -- personnes, emplois du temps, règles ------------------------------------

    @cached_property
    def _lignees(self) -> dict[int, tuple[int, ...]]:
        parents = {groupe.id: groupe.parent_id for groupe in self.groupes}
        lignees: dict[int, tuple[int, ...]] = {}
        for groupe_id in parents:
            lignee, courant = [groupe_id], parents[groupe_id]
            while courant is not None and courant in parents and courant not in lignee:
                lignee.append(courant)
                courant = parents[courant]
            lignees[groupe_id] = tuple(lignee)
        return lignees

    def lignee(self, groupe_id: int) -> tuple[int, ...]:
        """Le groupe puis ses ascendants, jusqu'à la racine (demi-groupe, classe, promotion)."""
        return self._lignees.get(groupe_id) or (groupe_id,)

    def ascendants(self, groupe_id: int) -> tuple[int, ...]:
        """Les ascendants du groupe, du plus proche au plus lointain."""
        return self.lignee(groupe_id)[1:]

    @cached_property
    def _descendants(self) -> dict[int, frozenset[int]]:
        descendants: dict[int, set[int]] = {groupe.id: set() for groupe in self.groupes}
        for groupe_id, lignee in self._lignees.items():
            for ascendant in lignee[1:]:
                descendants[ascendant].add(groupe_id)
        return {g: frozenset(d) for g, d in descendants.items()}

    def descendants(self, groupe_id: int) -> frozenset[int]:
        """Tous les groupes du sous-arbre du groupe (lui exclu)."""
        return self._descendants.get(groupe_id, frozenset())

    @cached_property
    def groupes_feuilles(self) -> tuple[int, ...]:
        """Groupes qui ne sont le parent d'aucun autre : les personnes « groupe »."""
        parents = {groupe.parent_id for groupe in self.groupes if groupe.parent_id is not None}
        return tuple(sorted(groupe.id for groupe in self.groupes if groupe.id not in parents))

    def feuilles(self, groupe_id: int) -> tuple[int, ...]:
        """Groupes feuilles du sous-arbre du groupe (lui-même s'il est une feuille)."""
        feuilles = set(self.groupes_feuilles)
        return tuple(
            sorted(g for g in (self.descendants(groupe_id) | {groupe_id}) if g in feuilles)
        )

    @cached_property
    def emplois(self) -> tuple[Emploi, ...]:
        """L'emploi du temps de chaque personne (définition unique, voir le module).

        Un groupe feuille suit ses séances et celles de ses ascendants ; un
        enseignant non fictif suit les siennes (seulement s'il en a). Deux
        demi-groupes sans séance propre ont le même emploi du temps.
        """
        par_groupe: dict[int, set[str]] = {}
        par_enseignant: dict[int, set[str]] = {}
        for seance in self.seances:
            for groupe in seance.besoin.groupes:
                par_groupe.setdefault(groupe, set()).add(seance.id)
            for enseignant in seance.besoin.enseignants:
                par_enseignant.setdefault(enseignant, set()).add(seance.id)
        emplois = [
            Emploi(
                personne=Personne(NaturePersonne.GROUPE, feuille),
                seances=frozenset(s for g in self.lignee(feuille) for s in par_groupe.get(g, ())),
                lignee=self.lignee(feuille),
            )
            for feuille in self.groupes_feuilles
        ]
        emplois.extend(
            Emploi(
                personne=Personne(NaturePersonne.ENSEIGNANT, enseignant.id),
                seances=frozenset(par_enseignant[enseignant.id]),
            )
            for enseignant in sorted(self.enseignants, key=lambda e: e.id)
            if not enseignant.fictif and enseignant.id in par_enseignant
        )
        return tuple(emplois)

    @cached_property
    def _emplois(self) -> dict[Personne, Emploi]:
        return {emploi.personne: emploi for emploi in self.emplois}

    def emploi(self, personne: Personne) -> Emploi:
        """L'emploi du temps d'une personne ; erreur si elle n'en a pas."""
        return _trouver(self._emplois, personne, "Personne")

    def personnes_de(self, seance_id: str) -> tuple[Personne, ...]:
        """Personnes dont l'emploi du temps contient la séance."""
        return tuple(e.personne for e in self.emplois if seance_id in e.seances)

    @cached_property
    def _regles(self) -> dict[int, RegleResolue]:
        return {regle.id: regle for regle in self.regles}

    def regle(self, regle_id: int) -> RegleResolue:
        """Règle personnalisée résolue d'identifiant `regle_id`."""
        return _trouver(self._regles, regle_id, "Règle")

    @cached_property
    def reglages(self) -> Reglages:
        """Jours permis par séance, déjeuner et pause par personne (voir `edt.domaine.regles`)."""
        return resoudre_reglages(self)

    # -- groupes ----------------------------------------------------------------

    @cached_property
    def _groupes_lies(self) -> dict[int, frozenset[int]]:
        lies: dict[int, set[int]] = {groupe.id: {groupe.id} for groupe in self.groupes}
        for a, b in self.conflits_groupes:
            lies.setdefault(a, {a}).add(b)
            lies.setdefault(b, {b}).add(a)
        return {groupe_id: frozenset(valeurs) for groupe_id, valeurs in lies.items()}

    def groupes_lies(self, groupe_id: int) -> frozenset[int]:
        """Le groupe lui-même et tous les groupes en conflit avec lui."""
        return self._groupes_lies.get(groupe_id, frozenset({groupe_id}))

    def groupes_en_conflit(self, groupe_a: int, groupe_b: int) -> bool:
        """Vrai si les deux groupes ne peuvent pas avoir cours en même temps (ou sont le même)."""
        return groupe_b in self.groupes_lies(groupe_a)

    def besoins_en_conflit_de_groupes(self, besoin_a: Besoin, besoin_b: Besoin) -> bool:
        """Vrai si un groupe de `besoin_a` est en conflit avec un groupe de `besoin_b`."""
        return any(
            not self.groupes_lies(groupe).isdisjoint(besoin_b.groupes)
            for groupe in besoin_a.groupes
        )

    # -- lieux ------------------------------------------------------------------

    @cached_property
    def _trajets(self) -> dict[tuple[int, int], int]:
        return {
            (min(t.campus_a_id, t.campus_b_id), max(t.campus_a_id, t.campus_b_id)): t.duree
            for t in self.trajets
        }

    def trajet(self, campus_a_id: int, campus_b_id: int) -> int:
        """Temps de trajet en minutes entre deux campus (0 pour le même campus).

        Sans ligne dans la matrice, c'est le paramètre `trajet_defaut_min` qui s'applique.
        """
        if campus_a_id == campus_b_id:
            return 0
        cle = (min(campus_a_id, campus_b_id), max(campus_a_id, campus_b_id))
        return self._trajets.get(cle, self.parametres.trajet_defaut_min)

    @cached_property
    def _types_salle(self) -> dict[int, Reference]:
        return {reference.id: reference for reference in self.types_salle}

    @cached_property
    def _equipements(self) -> dict[int, Reference]:
        return {reference.id: reference for reference in self.equipements}

    def libelle_type_salle(self, type_salle_id: int) -> str:
        """Libellé d'un type de salle (pour les messages), ex. « Amphithéâtre »."""
        return _trouver(self._types_salle, type_salle_id, "Type de salle").libelle

    def libelle_equipement(self, equipement_id: int) -> str:
        """Libellé d'un équipement (pour les messages), ex. « Vidéoprojecteur »."""
        return _trouver(self._equipements, equipement_id, "Équipement").libelle

    def salle_accessible(self, salle: Salle) -> bool:
        """Vrai si la salle convient à un groupe qui requiert l'accessibilité."""
        return salle.est_accessible(self.parametres.accessibilite_inconnue_acceptee)

    # -- indisponibilités -------------------------------------------------------

    @cached_property
    def _indisponibilites(
        self,
    ) -> dict[tuple[CibleIndisponibilite, int], tuple[Indisponibilite, ...]]:
        index: dict[tuple[CibleIndisponibilite, int], list[Indisponibilite]] = {}
        for indisponibilite in self.indisponibilites:
            cle = (indisponibilite.cible, indisponibilite.ressource_id)
            index.setdefault(cle, []).append(indisponibilite)
        return {cle: tuple(valeurs) for cle, valeurs in index.items()}

    def indisponibilites_recurrentes(
        self, cible: CibleIndisponibilite, ressource_id: int
    ) -> tuple[Indisponibilite, ...]:
        """Indisponibilités récurrentes de la ressource : les seules à vérifier sur la semaine type.

        Chacune interdit, toutes les semaines, la plage `[debut, fin[` du jour `jour`.
        """
        return tuple(
            indisponibilite
            for indisponibilite in self.indisponibilites_de(cible, ressource_id)
            if indisponibilite.recurrente
        )

    def indisponibilites_de(
        self, cible: CibleIndisponibilite, ressource_id: int
    ) -> tuple[Indisponibilite, ...]:
        """Toutes les indisponibilités de la ressource, récurrentes et datées (couche datée).

        Sur une date donnée, utiliser `Indisponibilite.concerne(date)`.
        """
        return self._indisponibilites.get((cible, ressource_id), ())


def _trouver(index: dict, cle, nom: str):
    try:
        return index[cle]
    except KeyError:
        raise ErreurIntrouvable(f"{nom} {cle!r} absent(e) de l'instantané du semestre.") from None
