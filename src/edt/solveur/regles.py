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

`Regles` regroupe les contraintes d'un calcul avec leur **niveau**
(`edt.domaine.contraintes.Niveau`) et met en cache les recherches répétées
(personnes partagées, salles compatibles, indisponibilités).

Règles personnalisées et réglages par personne
-----------------------------------------------
`Regles` reçoit aussi les niveaux des **règles personnalisées** du calcul
(`niveaux_regles`, voir `edt.solveur.personnalisees.appliquer_niveaux`) et
lit `Instantane.reglages` : les jours permis de chaque séance (standard
`jours_travailles` et règles `JOURS_TRAVAILLES` retenues, campus compris), la
pause déjeuner et la pause minimale de chaque **personne** (groupe feuille ou
enseignant, `Instantane.emplois`), chaque valeur avec sa **source** (une règle
ou le paramètre global). Toute violation est rendue comme une `Violation`,
imputée à sa source : au code du catalogue pour le standard, à la règle pour
une règle (code `REGLE`). Le niveau qui décide « dur ou souple » est celui de
la source (`dure_violation`, `poids_violation`).

Niveaux
-------
Une contrainte **présente** dans les niveaux est examinée (`active`). Elle est
**dure** (`dure`) si son niveau est « nécessaire » : rien ne la viole. Elle est
**souple** (`souple`) sinon : une entorse est permise mais coûte `poids` dans
l'objectif du moteur, et un coût dans le replacement. Les méthodes qui
éliminent une possibilité (`refus_horaire`, `salles_compatibles`) ne le font
que pour les contraintes dures ; les violations des contraintes souples sont
rendues à part (`entorses_horaire`, `SallesCompatibles.entorses`), à charge
pour l'appelant de les compter.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from edt.domaine.contraintes import (
    CATALOGUE,
    POIDS_NIVEAU,
    CodeContrainte,
    Niveau,
    Niveaux,
    niveaux_depuis_desactivees,
    normaliser_codes,
)
from edt.domaine.enumerations import JOURS, SAMEDI, CibleIndisponibilite
from edt.domaine.heures import (
    MINUTES_PAR_JOUR,
    duree_vers_texte,
    ecart,
    plage_vers_texte,
    se_chevauchent,
)
from edt.domaine.instantane import Besoin, Indisponibilite, Instantane, Salle, SeanceAPlacer
from edt.domaine.regles import (
    FamilleRegle,
    NaturePersonne,
    Personne,
    ReglageDejeuner,
    RegleResolue,
    ReglesJours,
    SorteRegle,
    Source,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.solveur.personnalisees import Choix, appliquer_niveaux, enfreint, regles_de_seance

C = CodeContrainte

Imputation = CodeContrainte | int
"""À qui une entorse est imputée : une contrainte du catalogue, ou une règle (son identifiant)."""


@dataclass(frozen=True)
class Violation:
    """Une contrainte enfreinte : le code du catalogue et, pour une règle personnalisée, la règle.

    `code` vaut `REGLE` quand `regle_id` est renseigné. `raison` sert aux
    messages (« la pause déjeuner », « la règle R007 (Le groupe A1 ne
    travaille pas le samedi) »).
    """

    code: CodeContrainte
    raison: str = ""
    regle_id: int | None = None

    @property
    def imputation(self) -> Imputation:
        """La clé d'imputation de l'entorse : la règle, sinon le code."""
        return self.regle_id if self.regle_id is not None else self.code


@dataclass(frozen=True)
class ExigencePause:
    """Une pause minimale exigée entre deux séances d'une paire, et à qui l'imputer."""

    duree: int
    violation: Violation
    dure: bool


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
    """Salles qui réunissent toutes les exigences dures d'un besoin, et le détail par exigence.

    `entorses` : par salle admissible, les exigences souples qu'elle ne
    satisfait pas (une salle sans équipement requis quand `EQUIPEMENT` est
    souple, par exemple).
    """

    salles: tuple[Salle, ...]
    exigences: tuple[Exigence, ...]
    entorses: dict[int, tuple[CodeContrainte, ...]] = field(default_factory=dict)


class Regles:
    """Contraintes d'un calcul, avec leur niveau, et recherches mises en cache sur l'instantané.

    `niveaux` donne le niveau de chaque contrainte présente ; à défaut, les
    contraintes prennent leur niveau par défaut. Dans les deux cas, les codes
    de `contraintes_desactivees` sont ignorés.
    """

    def __init__(
        self,
        instantane: Instantane,
        contraintes_desactivees: Iterable[str] = (),
        niveaux: Niveaux | None = None,
        niveaux_regles: dict[int, Niveau] | None = None,
    ) -> None:
        self.instantane = appliquer_niveaux(instantane, niveaux_regles)
        self.niveaux_regles: dict[int, Niveau] = {
            regle.id: regle.niveau for regle in self.instantane.regles
        }
        instantane = self.instantane
        retirees = normaliser_codes(contraintes_desactivees)
        if niveaux is None:
            niveaux = niveaux_depuis_desactivees(retirees)
        self.niveaux: Niveaux = {
            CodeContrainte(code): Niveau(n) for code, n in niveaux.items() if code not in retirees
        }
        self.actives = frozenset(self.niveaux)
        self.desactivees = frozenset(CATALOGUE) - self.actives
        self._partages: dict[tuple[int, int], tuple[bool, bool]] = {}
        self._recurrentes: dict[tuple[CibleIndisponibilite, int], tuple[Indisponibilite, ...]] = {}
        self._salles: dict[int, SallesCompatibles] = {}
        self._reels: dict[int, frozenset[int]] = {}
        self._lies: dict[int, frozenset[int]] = {}
        self._personnes: dict[int, tuple[Personne, ...]] = {}
        self._dejeuners: dict[int, tuple[ReglageDejeuner, ...]] = {}
        self._pauses: dict[tuple[int, int], tuple[ExigencePause, ...]] = {}
        self._regles_seance: dict[str, tuple[RegleResolue, ...]] | None = None
        self._regles_emploi: dict[tuple[int, str | None], tuple[RegleResolue, ...]] = {}
        self._limites = limites_demi_journee(instantane)

    def active(self, code: CodeContrainte) -> bool:
        """Vrai si la contrainte `code` s'applique à ce calcul (dure ou souple)."""
        return code in self.niveaux

    def dure(self, code: CodeContrainte) -> bool:
        """Vrai si la contrainte est présente et nécessaire : rien ne la viole."""
        return self.niveaux.get(code) is Niveau.NECESSAIRE

    def souple(self, code: CodeContrainte) -> bool:
        """Vrai si la contrainte est présente et peut être enfreinte contre un coût."""
        niveau = self.niveaux.get(code)
        return niveau is not None and niveau is not Niveau.NECESSAIRE

    def poids(self, code: CodeContrainte) -> int:
        """Coût d'une entorse à la contrainte (0 si elle est dure ou ignorée)."""
        niveau = self.niveaux.get(code)
        return POIDS_NIVEAU.get(niveau, 0) if niveau is not None else 0

    def souples(self, codes: Iterable[CodeContrainte]) -> list[CodeContrainte]:
        """Les codes souples parmi `codes`, sans doublon."""
        return list(dict.fromkeys(code for code in codes if self.souple(code)))

    # -- règles personnalisées : niveaux, sources, imputations ---------------------

    def regle(self, regle_id: int) -> RegleResolue:
        """La règle personnalisée retenue pour ce calcul, d'identifiant `regle_id`."""
        return self.instantane.regle(regle_id)

    def niveau_imputation(self, imputation: Imputation) -> Niveau | None:
        """Niveau d'une contrainte du catalogue ou d'une règle (`None` si elle est ignorée)."""
        if isinstance(imputation, CodeContrainte):
            return self.niveaux.get(imputation)
        return self.niveaux_regles.get(imputation)

    def dure_imputation(self, imputation: Imputation) -> bool:
        """Vrai si la contrainte ou la règle est présente et nécessaire."""
        return self.niveau_imputation(imputation) is Niveau.NECESSAIRE

    def souple_imputation(self, imputation: Imputation) -> bool:
        """Vrai si la contrainte ou la règle est présente et peut être enfreinte contre un coût."""
        niveau = self.niveau_imputation(imputation)
        return niveau is not None and niveau is not Niveau.NECESSAIRE

    def poids_imputation(self, imputation: Imputation) -> int:
        """Coût d'une entorse à la contrainte ou à la règle (0 si elle est dure ou ignorée)."""
        return POIDS_NIVEAU.get(self.niveau_imputation(imputation), 0)

    def dure_violation(self, violation: Violation) -> bool:
        """Vrai si la violation enfreint une contrainte ou une règle nécessaire."""
        return self.dure_imputation(violation.imputation)

    def souple_violation(self, violation: Violation) -> bool:
        """Vrai si la violation enfreint une contrainte ou une règle souple."""
        return self.souple_imputation(violation.imputation)

    def poids_violation(self, violation: Violation) -> int:
        """Coût de la violation dans l'objectif ou au replacement."""
        return self.poids_imputation(violation.imputation)

    def souples_violations(self, violations: Iterable[Violation]) -> tuple[Violation, ...]:
        """Les violations souples parmi `violations`, sans doublon d'imputation."""
        vues: dict[Imputation, Violation] = {}
        for violation in violations:
            if self.souple_violation(violation) and violation.imputation not in vues:
                vues[violation.imputation] = violation
        return tuple(vues.values())

    @staticmethod
    def violation_de_source(source: Source, code: CodeContrainte, raison: str) -> Violation:
        """La violation d'une valeur réglée : imputée à sa règle, sinon à la contrainte `code`."""
        if source.regle is None:
            return Violation(code, raison)
        return Violation(
            C.REGLE, f"la règle {source.regle.code} ({source.regle.phrase})", source.regle.id
        )

    def violation_de_regle(self, regle: RegleResolue) -> Violation:
        """La violation d'une règle personnalisée."""
        return Violation(C.REGLE, f"la règle {regle.code} ({regle.phrase})", regle.id)

    def texte_violation(self, violation: Violation) -> str:
        """« Pause entre deux séances » ou « règle R007 », pour un message."""
        if violation.regle_id is None:
            return f"« {CATALOGUE[violation.code].libelle} »"
        return f"la règle {self.regle(violation.regle_id).code}"

    def regles_de_seance(self, seance_id: str) -> tuple[RegleResolue, ...]:
        """Règles d'exigence de sorte « séance » qui concernent la séance (hors jours travaillés)."""
        if self._regles_seance is None:
            self._regles_seance = regles_de_seance(self.instantane)
        return self._regles_seance.get(seance_id, ())

    def regles_emploi_de(self, besoin: Besoin, seance_id: str | None) -> tuple[RegleResolue, ...]:
        """Règles d'emploi du temps (exigences) et de relation qui concernent la séance.

        Une règle d'emploi du temps concerne le besoin si l'une de ses
        personnes (`RegleResolue.personnes`) suit ses séances ; une relation,
        si la séance est parmi les siennes (`RegleResolue.seances`), ou l'une
        de ses personnes pour une occurrence sans séance hebdomadaire. Les
        réglages (déjeuner, pause) n'en font pas partie.
        """
        cle = (besoin.id, seance_id)
        regles = self._regles_emploi.get(cle)
        if regles is None:
            personnes = set(self.personnes(besoin))
            retenues = []
            for regle in self.instantane.regles:
                if regle.sorte is SorteRegle.RELATION:
                    concernee = (
                        seance_id in regle.seances
                        if seance_id is not None
                        else not personnes.isdisjoint(regle.personnes)
                    )
                elif regle.sorte is SorteRegle.EMPLOI_DU_TEMPS:
                    concernee = regle.famille is FamilleRegle.EXIGENCE and not personnes.isdisjoint(
                        regle.personnes
                    )
                else:
                    concernee = False
                if concernee:
                    retenues.append(regle)
            regles = tuple(retenues)
            self._regles_emploi[cle] = regles
        return regles

    def signature_regles(self, seance_id: str) -> tuple:
        """Ce qui, dans les règles, distingue les domaines de deux séances d'un même besoin."""
        jours = self.jours_de(seance_id)
        return (
            tuple(regle.id for regle in self.regles_de_seance(seance_id)),
            jours.standard,
            tuple(regle.id for regle in jours.retenues),
            tuple(sorted((campus, regle.id) for campus, regle in jours.par_campus.items())),
        )

    def violations_du_choix(
        self, seance_id: str, choix: Choix, date: dt.date | None = None
    ) -> list[Violation]:
        """Règles de sorte « séance » (hors jours) que ce choix enfreint.

        Avec `date` (couche datée), une règle à période datée ne compte que si
        elle couvre ce jour (`RegleResolue.couvre`).
        """
        return [
            self.violation_de_regle(regle)
            for regle in self.regles_de_seance(seance_id)
            if (date is None or regle.couvre(date)) and enfreint(regle, choix)
        ]

    def couvre(self, violation: Violation, date: dt.date | None) -> bool:
        """Vrai si la violation vaut ce jour-là : toujours pour le standard, selon sa période
        pour une règle (`None` : semaine type, toute règle retenue compte)."""
        if date is None or violation.regle_id is None:
            return True
        return self.regle(violation.regle_id).couvre(date)

    # -- jours et horaires --------------------------------------------------------

    def jours_de(self, seance_id: str) -> ReglesJours:
        """Jours permis d'une séance : standard et règles retenues (`Instantane.reglages`)."""
        return self.instantane.reglages.jours_de(seance_id)

    def violations_de_jour(
        self,
        seance_id: str | None,
        jour: int,
        campus_id: int | None = None,
        besoin: Besoin | None = None,
    ) -> list[Violation]:
        """Ce qu'enfreint la séance placée le `jour` (sur `campus_id`) : règles de jours, standard.

        Le standard est la contrainte `JOURS` (les jours travaillés de
        l'établissement, plus le samedi si le type de séance l'autorise) ; une
        règle `JOURS_TRAVAILLES` retenue l'emporte sur lui (précédence des
        axes, `edt.domaine.regles`). `JOURS` ignorée : le standard ne compte
        pas, les règles restent. Une séance inconnue de la semaine type
        (occurrence saisie à la main, `seance_id` absent) n'a que le standard
        de son `besoin`.
        """
        reglages = self.instantane.reglages.jours
        if seance_id is None or seance_id not in reglages:
            if not self.active(C.JOURS) or jour in self.instantane.jours_travailles:
                return []
            if jour == SAMEDI and besoin is not None and besoin.samedi_autorise:
                return []
            return [Violation(C.JOURS, f"le {JOURS[jour]} n'est pas un jour de cours permis")]
        violations = []
        for source in reglages[seance_id].entorses(jour, campus_id):
            if source.regle is None and not self.active(C.JOURS):
                continue
            violations.append(
                self.violation_de_source(
                    source, C.JOURS, f"le {JOURS[jour]} n'est pas un jour de cours permis"
                )
            )
        return violations

    def jour_permis(self, jour: int, seance_id: str, campus_id: int | None = None) -> bool:
        """Vrai si le jour est permis à la séance : un jour de la grille, qu'aucune règle de jours
        nécessaire ni le standard (`JOURS` nécessaire) n'interdit."""
        if not self.instantane.debuts_de_creneau(jour):
            return False
        return not any(
            self.dure_violation(v) for v in self.violations_de_jour(seance_id, jour, campus_id)
        )

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

    def violations_horaire(
        self,
        besoin: Besoin,
        duree: int,
        jour: int,
        minute: int,
        seance_id: str | None = None,
        date: dt.date | None = None,
    ) -> list[Violation]:
        """Règles d'horaire présentes que cette heure de début enfreint.

        - `HORAIRES` : ouverture, fermeture, demi-journée du créneau de début ;
        - `DEJEUNER`, pour chaque personne de la séance selon son réglage
          (`Regles.dejeuners`) : à horaires déterminés, la séance ne doit pas
          recouper la plage ; en pause libre bornée, elle ne doit pas, à elle
          seule, priver la personne de sa pause — seulement quand la source
          est dure : souple, l'entorse est comptée à l'échelle de la journée
          d'une personne par le modèle (`pause_dejeuner_tenue`), pas par
          séance. Une valeur réglée par une règle `PAUSE_DEJEUNER` est imputée
          à cette règle ;
        - avec `seance_id`, les règles `PLAGE_HORAIRE` et `CRENEAUX_INTERDITS`
          qui concernent la séance (`edt.solveur.personnalisees`).

        Une heure imposée par le besoin en dispense : la séance peut alors
        sortir de la grille.
        """
        parametres = self.instantane.parametres
        fin = minute + duree
        if besoin.debut_impose is not None:
            return []
        violations: list[Violation] = []
        if self.active(C.HORAIRES) and (
            minute < parametres.ouverture_min
            or fin > parametres.fermeture_min
            or fin > self._limites.get((jour, minute), 0)
        ):
            violations.append(
                Violation(
                    C.HORAIRES,
                    f"la séance de {duree_vers_texte(duree)} ne tient pas dans la demi-journée "
                    "ou dans les heures d'ouverture",
                )
            )
        for dejeuner in self.dejeuners(besoin):
            violation = self.violation_de_source(dejeuner.source, C.DEJEUNER, "la pause déjeuner")
            if dejeuner.plage is not None and se_chevauchent(minute, fin, *dejeuner.plage):
                violations.append(violation)
            elif (
                dejeuner.fenetre is not None
                and self.dure_violation(violation)
                and not pause_dejeuner_tenue([(minute, fin)], dejeuner.fenetre)
            ):
                violations.append(violation)
        if seance_id is not None:
            violations.extend(self.violations_du_choix(seance_id, Choix(jour, minute, fin), date))
        return violations

    def refus_horaire(
        self,
        besoin: Besoin,
        duree: int,
        jour: int,
        minute: int,
        seance_id: str | None = None,
        date: dt.date | None = None,
    ) -> Violation | None:
        """Raison **dure** pour laquelle la séance ne peut pas commencer à cette heure, sinon `None`.

        Aucune séance ne passe minuit ; ensuite, la première règle d'horaire
        nécessaire enfreinte (`violations_horaire`). Les règles souples
        enfreintes sont dans `entorses_horaire`.
        """
        if minute + duree > MINUTES_PAR_JOUR:
            return Violation(C.HORAIRES, "la séance finirait après minuit")
        return next(
            (
                violation
                for violation in self.violations_horaire(
                    besoin, duree, jour, minute, seance_id, date
                )
                if self.dure_violation(violation)
            ),
            None,
        )

    def entorses_horaire(
        self,
        besoin: Besoin,
        duree: int,
        jour: int,
        minute: int,
        seance_id: str | None = None,
        date: dt.date | None = None,
    ) -> tuple[Violation, ...]:
        """Règles d'horaire **souples** que cette heure de début enfreint."""
        return self.souples_violations(
            self.violations_horaire(besoin, duree, jour, minute, seance_id, date)
        )

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

    def personnes(self, besoin: Besoin) -> tuple[Personne, ...]:
        """Personnes dont l'emploi du temps contient les séances du besoin (`Instantane.emplois`).

        Les groupes feuilles dont la lignée croise les groupes du besoin, et
        ses enseignants non fictifs.
        """
        personnes = self._personnes.get(besoin.id)
        if personnes is None:
            personnes = tuple(
                emploi.personne
                for emploi in self.instantane.emplois
                if (
                    emploi.personne.nature is NaturePersonne.GROUPE
                    and not besoin.groupes.isdisjoint(emploi.lignee)
                )
                or (
                    emploi.personne.nature is NaturePersonne.ENSEIGNANT
                    and emploi.personne.id in besoin.enseignants
                )
            )
            self._personnes[besoin.id] = personnes
        return personnes

    def dejeuners(self, besoin: Besoin) -> tuple[ReglageDejeuner, ...]:
        """Réglages de pause déjeuner distincts des personnes du besoin (`Instantane.reglages`).

        Le standard n'est retenu que si `DEJEUNER` est active ; une règle,
        toujours (elle est retenue pour ce calcul). Un réglage sans pause
        (mode « aucun ») est omis.
        """
        dejeuners = self._dejeuners.get(besoin.id)
        if dejeuners is None:
            vus: dict[tuple, ReglageDejeuner] = {}
            for personne in self.personnes(besoin):
                reglage = self.instantane.reglages.dejeuner[personne]
                if not reglage.actif:
                    continue
                if reglage.source.regle is None and not self.active(C.DEJEUNER):
                    continue
                cle = (
                    reglage.mode,
                    reglage.debut,
                    reglage.fin,
                    reglage.duree,
                    reglage.source.regle.id if reglage.source.regle else None,
                )
                vus.setdefault(cle, reglage)
            dejeuners = tuple(vus.values())
            self._dejeuners[besoin.id] = dejeuners
        return dejeuners

    def exigences_de_pause(self, a: Besoin, b: Besoin) -> tuple[ExigencePause, ...]:
        """Pauses minimales exigées entre une séance de `a` et une de `b` qui se suivent.

        Une exigence par personne commune (`personnes`), selon son réglage
        (`Instantane.reglages`) ; deux personnes au même réglage et à la même
        source n'en font qu'une. Les personnes sont celles de
        `Instantane.emplois` (lignée) ; deux besoins en conflit de groupes
        sans groupe feuille commun (groupes seulement chevauchants) gardent
        le **standard** entre eux, comme chez le vérificateur : un réglage
        par personne ne remplace que la pause de cette personne. Le standard
        ne compte que si `PAUSE` est active ; une pause nulle n'exige rien.
        """
        cle = (a.id, b.id) if a.id <= b.id else (b.id, a.id)
        exigences = self._pauses.get(cle)
        if exigences is None:
            communes = set(self.personnes(a)) & set(self.personnes(b))
            standard = Violation(C.PAUSE, "la pause entre deux séances")
            vues: dict[tuple[int, Imputation], ExigencePause] = {}
            for personne in sorted(communes, key=lambda p: (p.nature, p.id)):
                reglage = self.instantane.reglages.pause[personne]
                if reglage.source.regle is None:
                    if not self.active(C.PAUSE) or reglage.duree <= 0:
                        continue
                    violation = standard
                else:
                    if reglage.duree <= 0:
                        continue
                    violation = self.violation_de_regle(reglage.source.regle)
                vues.setdefault(
                    (reglage.duree, violation.imputation),
                    ExigencePause(reglage.duree, violation, self.dure_violation(violation)),
                )
            _, groupes_en_conflit = self.partage(a, b)
            if (
                groupes_en_conflit
                and self.active(C.PAUSE)
                and not any(p.nature is NaturePersonne.GROUPE for p in communes)
            ):
                duree = self.instantane.parametres.pause_min
                if duree > 0:
                    vues.setdefault(
                        (duree, C.PAUSE), ExigencePause(duree, standard, self.dure(C.PAUSE))
                    )
            exigences = tuple(vues.values())
            self._pauses[cle] = exigences
        return exigences

    def pause_garantie(self) -> int:
        """Pause que les contraintes d'unicité peuvent garantir à tout le monde en allongeant les
        plages : la plus petite pause en vigueur, si toutes sont dures ; sinon 0.

        Une pause souple n'allonge rien (ses entorses sont comptées par
        paire) ; une personne au standard sans `PAUSE` active n'a pas de
        pause. Le reste des exigences passe par des couples de choix.
        """
        durees = []
        for reglage in self.instantane.reglages.pause.values():
            if reglage.source.regle is None:
                if not self.active(C.PAUSE):
                    return 0
                if not self.dure(C.PAUSE):
                    return 0
                durees.append(self.instantane.parametres.pause_min)
            else:
                if reglage.source.regle.niveau is not Niveau.NECESSAIRE:
                    return 0
                durees.append(reglage.duree)
        if not self.instantane.reglages.pause:
            return self.instantane.parametres.pause_min if self.dure(C.PAUSE) else 0
        return min(durees)

    def pause_la_plus_longue(self) -> int:
        """La plus longue pause minimale en vigueur (standard ou règle), pour le pas du codage."""
        durees = [self.instantane.parametres.pause_min]
        durees.extend(reglage.duree for reglage in self.instantane.reglages.pause.values())
        return max(durees)

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
        self,
        cible: CibleIndisponibilite,
        ressource_id: int,
        date: dt.date,
        debut: int,
        fin: int,
        datees_seulement: bool = False,
    ) -> Indisponibilite | None:
        """Première indisponibilité dure, récurrente ou datée, qui recoupe `[debut, fin[` ce jour-là.

        C'est la règle de la couche datée. Avec `datees_seulement`, les
        indisponibilités récurrentes sont passées (une règle `DISPO_*` souple
        les a déjà arbitrées dans la semaine type).
        """
        for indisponibilite in self.instantane.indisponibilites_de(cible, ressource_id):
            if (
                indisponibilite.dure
                and not (datees_seulement and indisponibilite.recurrente)
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
        """Salles qui satisfont les exigences dures du besoin, sans regarder les horaires.

        Une exigence souple n'élimine aucune salle : les salles qui ne la
        satisfont pas sont rendues avec leur entorse (`entorses`). Les salles
        sortent triées par capacité puis par identifiant, pour un modèle
        reproductible.
        """
        resultat = self._salles.get(besoin.id)
        if resultat is None:
            tests = self.exigences_de_salle(besoin)
            dures = [test for test in tests if self.dure(test[0])]
            souples = [test for test in tests if not self.dure(test[0])]
            salles = tuple(
                sorted(
                    (s for s in self.instantane.salles if all(test(s) for _, _, test in dures)),
                    key=lambda s: (s.capacite, s.id),
                )
            )
            exigences = tuple(
                Exigence(code, libelle, sum(1 for s in self.instantane.salles if test(s)))
                for code, libelle, test in dures
            )
            entorses: dict[int, tuple[CodeContrainte, ...]] = {}
            for salle in salles:
                codes = self.souples(code for code, _, test in souples if not test(salle))
                if codes:
                    entorses[salle.id] = tuple(codes)
            resultat = SallesCompatibles(salles, exigences, entorses)
            self._salles[besoin.id] = resultat
        return resultat

    # -- paires -----------------------------------------------------------------

    def violations_de_paire(
        self,
        besoin_a: Besoin,
        debut_a: int,
        fin_a: int,
        salle_a: int | None,
        besoin_b: Besoin,
        debut_b: int,
        fin_b: int,
        salle_b: int | None,
    ) -> list[Violation]:
        """Contraintes de paire violées par deux séances qui coexistent.

        Les appelants garantissent la coexistence (même jour et semaines
        communes, ou même date). Sans salle connue, les règles de salle et de
        trajet ne sont pas évaluées. Comme dans le vérificateur, la pause et
        le trajet ne concernent que deux séances qui ne se chevauchent pas :
        un chevauchement relève des contraintes d'unicité. La pause exigée est
        celle de chaque personne commune (`exigences_de_pause`), imputée à sa
        source.
        """
        violes: list[Violation] = []
        chevauchement = se_chevauchent(debut_a, fin_a, debut_b, fin_b)
        if (
            chevauchement
            and salle_a is not None
            and salle_a == salle_b
            and self.active(C.SALLE_UNIQUE)
        ):
            violes.append(Violation(C.SALLE_UNIQUE))
        enseignant, groupe = self.partage(besoin_a, besoin_b)
        if not (enseignant or groupe):
            return violes
        if chevauchement:
            if enseignant and self.active(C.ENSEIGNANT_UNIQUE):
                violes.append(Violation(C.ENSEIGNANT_UNIQUE))
            if groupe and self.active(C.GROUPE_UNIQUE):
                violes.append(Violation(C.GROUPE_UNIQUE))
            return violes
        libre = ecart(debut_a, fin_a, debut_b, fin_b)
        for exigence in self.exigences_de_pause(besoin_a, besoin_b):
            if libre < exigence.duree and exigence.violation not in violes:
                violes.append(exigence.violation)
        if self.active(C.CAMPUS_TRAJET) and salle_a is not None and salle_b is not None:
            campus_a = self.instantane.salle(salle_a).campus_id
            campus_b = self.instantane.salle(salle_b).campus_id
            if campus_a != campus_b and libre < self.instantane.trajet(campus_a, campus_b):
                violes.append(Violation(C.CAMPUS_TRAJET))
        return violes


# --------------------------------------------------------------------------- #
# Pause déjeuner libre bornée
# --------------------------------------------------------------------------- #


def pause_dejeuner_tenue(
    intervalles: Iterable[tuple[int, int]], fenetre: tuple[int, int, int]
) -> bool:
    """Règle `DEJEUNER` en pause libre bornée, pour la journée d'une personne.

    `intervalles` : les plages `[début, fin[` de ses séances ce jour-là ;
    `fenetre` : `(X, Y, N)` de `Parametres.fenetre_dejeuner`. Vrai si aucune
    séance ne recoupe `[X, Y[`, ou s'il reste dans `[X, Y[` un intervalle
    libre d'au moins N minutes d'un seul tenant.
    """
    debut_f, fin_f, duree = fenetre
    dans_la_fenetre = sorted(
        (max(debut, debut_f), min(fin, fin_f))
        for debut, fin in intervalles
        if se_chevauchent(debut, fin, debut_f, fin_f)
    )
    if not dans_la_fenetre:
        return True
    curseur = debut_f
    for debut, fin in dans_la_fenetre:
        if debut - curseur >= duree:
            return True
        curseur = max(curseur, fin)
    return fin_f - curseur >= duree


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
