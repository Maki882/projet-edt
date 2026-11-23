"""Diagnostic d'un calcul impossible : un ensemble irréductible de règles et de données en cause.

    isoler_impossibilite(instantane, options, budget_s) -> ResultatDiagnostic

Fonction pure : elle ne lit que l'instantané, n'écrit rien, et ne fait que des
**essais** : un appel au moteur (`edt.solveur.resoudre`) sur un instantané dont
on a retiré certains éléments. Elle ne retire jamais une règle pour réussir un
calcul : elle cherche ce qui le rend impossible.

Éléments candidats
------------------
- règles : les contraintes du catalogue au niveau **nécessaire** et modifiables
  (`Contrainte.modifiable`), les règles personnalisées au niveau nécessaire ;
- données : les besoins (avec toutes leurs séances), les verrous (un par séance
  verrouillée), les indisponibilités récurrentes et dures (les seules que lit la
  semaine type), les jours fermés.

Les contraintes qui définissent le problème (une séance par salle, par
enseignant, par groupe, toutes les séances placées, verrous et jours fermés en
tant que règles) ne sont jamais candidates : elles restent dans chaque essai et
le message les rappelle. Les contraintes et règles **souples** restent aussi
dans chaque essai, à leur niveau : les retirer n'est pas neutre (une contrainte
`HORAIRES` ignorée ouvre des débuts hors de la grille, une règle de réglage
ignorée rend la place au standard, qui peut être plus strict).

Un essai : INFAISABLE dit que les éléments retirés ne sont pas nécessaires ;
SOLUTION qu'il en faut au moins un ; TEMPS_DEPASSE ne dit rien, l'élément est
gardé et le résultat devient partiel. Un essai s'arrête à la première solution
valide (l'amélioration ne sert à rien ici).

Déroulement
-----------
1. Essai complet : SOLUTION donne `POSSIBLE` (les données ont changé depuis le
   calcul), sans conclusion `INDETERMINE`.
2. Réduction, famille par famille (besoins, verrous, indisponibilités, jours
   fermés, contraintes, règles) : la famille entière, puis des blocs de plus en
   plus petits, jusqu'à un élément à la fois.
3. Confirmation : chaque élément gardé est retiré seul de l'ensemble final ; le
   statut `MINIMAL` n'est rendu que si chacun de ces essais donne SOLUTION. Un
   élément dont le retrait laisse le calcul impossible (effet non monotone,
   rare) est retiré et la passe reprend.

Budget : la durée totale ne dépasse pas `budget_s`. Le premier essai reçoit
tout le temps qui reste moins une marge ; les suivants au plus un tiers
(`PART_MAX_ESSAI`), pour qu'un essai sans conclusion ne prenne pas tout. La
marge grandit du plus grand dépassement observé d'un essai sur son temps
limite, et aucun essai n'est lancé avec moins que la durée de préparation et de
construction déjà observée plus `ESSAI_MIN_S`. Le moteur ne s'interrompt
qu'entre deux étapes de la construction de son modèle, et une étape peut durer
bien plus que le temps limite : chaque essai tourne donc dans son fil, attendu
jusqu'à la fin du budget au plus. Celui qui n'a pas rendu la main reçoit
l'ordre d'arrêt et finit en arrière-plan (`ResultatDiagnostic.essai_abandonne`)
pendant que le diagnostic rend `PARTIEL` (ou `INDETERMINE` au premier essai).

Identifiants des éléments (`ElementEnCause.identifiant`, toujours un texte) :
code du catalogue, identifiant de règle, de besoin, d'indisponibilité ou de
jour fermé, identifiant de séance pour un verrou (« 12-1 »). Un jour fermé
sans identifiant (instantané construit en mémoire) est désigné par
`jour_ferme:<rang>`, son rang dans `Instantane.jours_fermes` ; son libellé
suffit à le reconnaître.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from edt.domaine.contraintes import CATALOGUE, CodeContrainte, Niveau
from edt.domaine.depliage import deplier
from edt.domaine.enumerations import JOURS, CibleIndisponibilite
from edt.domaine.heures import plage_vers_texte
from edt.domaine.instantane import Instantane, SeanceAPlacer
from edt.erreurs import ErreurDonneesInvalides
from edt.solveur.contrat import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.moteur import resoudre

journal = logging.getLogger(__name__)

ESSAI_MIN_S = 0.2
"""Temps limite le plus court donné à un essai : en dessous, la fonction s'arrête."""
MARGE_MIN_S = 0.3
"""Marge gardée sur le budget à chaque essai, avant tout dépassement observé."""
ATTENTE_RESERVEE_S = 0.1
"""Part du budget gardée, après l'attente d'un essai, pour conclure."""
PART_MAX_ESSAI = 1 / 3
"""Part du temps restant qu'un essai de réduction ou de confirmation reçoit au plus (le premier
essai, lui, peut prendre tout le budget)."""

PHRASE_REGLES_DU_PROBLEME = (
    "Les règles qui définissent le problème restent toujours en vigueur : une séance à la fois "
    "par salle, par enseignant et par groupe, toutes les séances placées, verrous et jours fermés "
    "respectés."
)
"""Rappel joint au message : ces règles ne sont jamais candidates, elles s'ajoutent à l'ensemble."""

CONTRAINTES_ELARGISSANTES = frozenset({CodeContrainte.HORAIRES, CodeContrainte.JOURS})
"""Contraintes dont le retrait ouvre des débuts hors de la grille : essayées en dernier."""

NATURES_REGLES = ("contrainte", "regle")
NATURES_DONNEES = ("besoin", "verrou", "indisponibilite", "jour_ferme")


class StatutDiagnostic(StrEnum):
    """Issue d'un diagnostic."""

    MINIMAL = "minimal"
    """Ensemble irréductible, prouvé par la passe de confirmation."""
    PARTIEL = "partiel"
    """Ensemble qui rend toujours le calcul impossible, peut-être pas irréductible."""
    POSSIBLE = "possible"
    """Avec ces données, le calcul n'est plus impossible."""
    INDETERMINE = "indetermine"
    """Le premier essai n'a pas conclu dans le budget."""


@dataclass(frozen=True)
class ElementEnCause:
    """Une règle ou une donnée de l'ensemble en cause.

    `nature` : `contrainte`, `regle`, `besoin`, `verrou`, `indisponibilite` ou
    `jour_ferme` ; `identifiant` : voir l'en-tête du module ; `libelle` : phrase
    prête à afficher.
    """

    nature: str
    identifiant: str
    libelle: str

    def vers_dict(self) -> dict[str, str]:
        """Représentation JSON."""
        return {"nature": self.nature, "identifiant": self.identifiant, "libelle": self.libelle}

    @classmethod
    def depuis_dict(cls, donnees: object) -> ElementEnCause:
        """Relit `vers_dict` ; lève `ErreurDonneesInvalides` sur une entrée mal formée."""
        if not isinstance(donnees, dict):
            raise ErreurDonneesInvalides(f"Élément de diagnostic illisible : {donnees!r}.")
        valeurs = [donnees.get(cle) for cle in ("nature", "identifiant", "libelle")]
        if not all(isinstance(valeur, str) for valeur in valeurs):
            raise ErreurDonneesInvalides(f"Élément de diagnostic illisible : {donnees!r}.")
        nature, identifiant, libelle = valeurs
        if nature not in (*NATURES_REGLES, *NATURES_DONNEES):
            raise ErreurDonneesInvalides(f"Nature d'élément de diagnostic inconnue : {nature!r}.")
        return cls(nature, identifiant, libelle)


@dataclass(frozen=True)
class ResultatDiagnostic:
    """Résultat de `isoler_impossibilite`.

    `regles` : contraintes (ordre du catalogue) puis règles (par identifiant) ;
    `donnees` : besoins, verrous, indisponibilités, jours fermés, chacun par
    identifiant. Vides pour `POSSIBLE` et `INDETERMINE`.
    """

    statut: StatutDiagnostic
    regles: tuple[ElementEnCause, ...]
    donnees: tuple[ElementEnCause, ...]
    message: str
    nb_essais: int
    duree_s: float
    essai_abandonne: threading.Thread | None = field(default=None, compare=False)
    """Fil d'un essai qui n'a pas rendu la main à temps : il a reçu l'ordre d'arrêt et finit en
    arrière-plan (voir l'en-tête du module) ; `None` sinon."""


Essai = Callable[[Instantane, OptionsSolveur], ResultatSolveur]
"""Un essai : le moteur sur un instantané filtré (`resoudre` par défaut, remplaçable en test)."""


@dataclass(frozen=True)
class _Element:
    """Un candidat : sa nature, sa clé dans l'instantané et son ordre d'affichage."""

    nature: str
    cle: object
    ordre: tuple


def isoler_impossibilite(
    instantane: Instantane,
    options: OptionsSolveur,
    budget_s: float,
    *,
    essai: Essai | None = None,
) -> ResultatDiagnostic:
    """Cherche, dans `budget_s` secondes au plus, un ensemble irréductible qui rend le calcul impossible.

    `options` : celles du calcul diagnostiqué (niveaux des contraintes et des
    règles, tolérance des violations héritées, fils de calcul) ; le temps
    limite, la graine et l'objectif sont fixés par le diagnostic. `essai`
    remplace le moteur (tests). Lève `ErreurDonneesInvalides` si le budget
    n'est pas un nombre fini strictement positif.
    """
    if (
        isinstance(budget_s, bool)
        or not isinstance(budget_s, (int, float))
        or not math.isfinite(budget_s)
        or budget_s <= 0
    ):
        raise ErreurDonneesInvalides(
            f"Le temps accordé au diagnostic doit être un nombre de secondes positif ; lu : "
            f"{budget_s!r}."
        )
    if essai is None:
        moteur = _premiere_solution
    else:

        def moteur(inst: Instantane, opts: OptionsSolveur, _arret: threading.Event):
            return essai(inst, opts)

    return _Recherche(instantane, options, float(budget_s), moteur).lancer()


def _premiere_solution(
    instantane: Instantane, options: OptionsSolveur, arret: threading.Event
) -> ResultatSolveur:
    """Le moteur, arrêté dès le premier emploi du temps valide : sans amélioration.

    `arret` sert aussi au diagnostic pour interrompre un essai qui dépasse son temps.
    """
    return resoudre(instantane, options, arret, sur_premiere_solution=lambda _duree: arret.set())


class _Budget(Exception):
    """Plus assez de temps pour un essai utile."""


class _Recherche:
    """Un diagnostic : candidats, essais et budget."""

    def __init__(
        self,
        instantane: Instantane,
        options: OptionsSolveur,
        budget_s: float,
        moteur: Callable[[Instantane, OptionsSolveur, threading.Event], ResultatSolveur],
    ) -> None:
        self.instantane = instantane
        self.options = options
        self.budget_s = budget_s
        self.moteur = moteur
        self.debut = time.perf_counter()
        self.nb_essais = 0
        self.depassement_s = 0.0
        self.cout_fixe_s = 0.0
        self.sans_conclusion = False
        self.abandonne: threading.Thread | None = None
        self.niveaux_regles = (
            dict(options.niveaux_regles)
            if options.niveaux_regles is not None
            else {regle.id: regle.niveau for regle in instantane.regles}
        )
        self.familles = self._candidats()
        self.gardes: set[_Element] = {e for famille in self.familles for e in famille}

    # -- candidats ---------------------------------------------------------------

    def _candidats(self) -> list[list[_Element]]:
        """Les candidats, par famille, dans l'ordre de la réduction (données d'abord)."""
        inst = self.instantane
        besoins = [_Element("besoin", b.id, (b.id,)) for b in sorted(inst.besoins, key=_id)]
        verrous = [
            _Element("verrou", s.id, (s.besoin.id, s.rang))
            for s in inst.seances
            if s.verrou is not None
        ]
        indisponibilites = [
            _Element("indisponibilite", i.id, (i.id,))
            for i in sorted(inst.indisponibilites, key=_id)
            if i.recurrente and i.dure
        ]
        jours_fermes = [
            _Element("jour_ferme", rang, (j.id if j.id is not None else rang,))
            for rang, j in enumerate(inst.jours_fermes)
        ]
        ordre_catalogue = {code: rang for rang, code in enumerate(CATALOGUE)}
        contraintes = [
            _Element("contrainte", code, (ordre_catalogue[code],))
            for code in CATALOGUE
            if self.options.niveaux.get(code) is Niveau.NECESSAIRE and CATALOGUE[code].modifiable
        ]
        regles = [
            _Element("regle", regle.id, (regle.id,))
            for regle in sorted(inst.regles, key=_id)
            if self.niveaux_regles.get(regle.id) is Niveau.NECESSAIRE
        ]
        # Sans `HORAIRES` ou `JOURS`, chaque séance reçoit des débuts toutes les 30 minutes : le
        # modèle de l'essai grossit beaucoup. Ces deux codes sont essayés à part, après les
        # autres, pour que les blocs des autres codes restent des essais rapides.
        elargissantes = [e for e in contraintes if e.cle in CONTRAINTES_ELARGISSANTES]
        contraintes = [e for e in contraintes if e.cle not in CONTRAINTES_ELARGISSANTES]
        return [
            besoins,
            verrous,
            indisponibilites,
            jours_fermes,
            contraintes,
            regles,
            elargissantes,
        ]

    # -- déroulement -------------------------------------------------------------

    def lancer(self) -> ResultatDiagnostic:
        journal.info(
            "Diagnostic du semestre %d : %d candidat(s) (%s), budget %.1f s",
            self.instantane.semestre_id,
            len(self.gardes),
            ", ".join(f"{len(f)} {f[0].nature}" for f in self.familles if f) or "aucun",
            self.budget_s,
        )
        try:
            premier = self._essayer(frozenset())
        except _Budget:
            return self._conclure(StatutDiagnostic.INDETERMINE)
        if premier is StatutSolveur.SOLUTION:
            return self._conclure(StatutDiagnostic.POSSIBLE)
        if premier is not StatutSolveur.INFAISABLE:
            return self._conclure(StatutDiagnostic.INDETERMINE)
        try:
            for famille in self.familles:
                self._reduire(famille)
            minimal = self._confirmer()
        except _Budget:
            return self._conclure(StatutDiagnostic.PARTIEL, budget_epuise=True)
        if minimal and not self.sans_conclusion:
            return self._conclure(StatutDiagnostic.MINIMAL)
        return self._conclure(StatutDiagnostic.PARTIEL)

    def _reduire(self, famille: Sequence[_Element]) -> None:
        """Retire de `gardes` les éléments de la famille dont l'ensemble se passe."""
        restants = [e for e in famille if e in self.gardes]
        taille = len(restants)
        while taille >= 1 and restants:
            rang = 0
            while rang < len(restants):
                bloc = restants[rang : rang + taille]
                statut = self._essayer(frozenset(bloc))
                if statut is StatutSolveur.INFAISABLE:
                    self.gardes.difference_update(bloc)
                    del restants[rang : rang + taille]
                    continue
                if statut is not StatutSolveur.SOLUTION and taille == 1:
                    self.sans_conclusion = True
                rang += taille
            if taille == 1:
                break
            taille = max(1, taille // 2)

    def _confirmer(self) -> bool:
        """Retire chaque élément gardé, seul : vrai si chaque retrait rend le calcul possible."""
        while True:
            confirme = True
            for element in sorted(self.gardes, key=_tri):
                statut = self._essayer(frozenset({element}))
                if statut is StatutSolveur.INFAISABLE:
                    journal.info(
                        "Confirmation : l'ensemble se passe aussi de %s %s, retiré",
                        element.nature,
                        element.cle,
                    )
                    self.gardes.discard(element)
                    break
                if statut is not StatutSolveur.SOLUTION:
                    self.sans_conclusion = True
                    confirme = False
            else:
                return confirme

    def _essayer(self, retires: frozenset[_Element]) -> StatutSolveur:
        """Un essai sur les éléments gardés moins `retires` ; journalisé."""
        reste = self.budget_s - (time.perf_counter() - self.debut)
        temps = reste - MARGE_MIN_S - self.depassement_s
        if self.nb_essais:
            # Un essai sans conclusion ne doit pas prendre tout le budget : ses blocs se divisent.
            temps = min(temps, max(PART_MAX_ESSAI * reste, self.cout_fixe_s + ESSAI_MIN_S))
        # La préparation et la construction d'un essai ne s'interrompent pas : en dessous de
        # leur durée observée, l'essai dépasserait son temps limite.
        if temps < self.cout_fixe_s + ESSAI_MIN_S:
            journal.info(
                "Diagnostic : budget épuisé après %d essai(s) (%.1f s restantes)",
                self.nb_essais,
                max(reste, 0.0),
            )
            raise _Budget
        gardes = self.gardes - retires
        instantane, options = self._filtrer(gardes, temps)
        numero = self.nb_essais + 1
        # Le moteur journalise l'essai comme un calcul : les contraintes « désactivées à la
        # demande de l'utilisateur » sont celles que l'essai retire, et l'« arrêt à votre
        # demande » est l'arrêt voulu à la première solution. Cette ligne le dit d'avance.
        journal.info(
            "Diagnostic, essai %d (%.1f s au plus) : %s retiré(s), %d élément(s) gardé(s) ; "
            "retraits et arrêt à la première solution voulus par le diagnostic",
            numero,
            temps,
            ", ".join(f"{e.nature} {e.cle}" for e in sorted(retires, key=_tri)) or "rien",
            len(gardes),
        )
        debut = time.perf_counter()
        resultat = self._executer(instantane, options, numero, reste)
        duree = time.perf_counter() - debut
        self.nb_essais = numero
        self.depassement_s = max(self.depassement_s, duree - temps)
        stats = resultat.statistiques
        fixe = sum(
            valeur
            for valeur in (stats.get("duree_preparation_s"), stats.get("duree_construction_s"))
            if isinstance(valeur, (int, float))
        )
        self.cout_fixe_s = max(self.cout_fixe_s, fixe)
        journal.info(
            "Diagnostic, essai %d : %s en %.2f s", numero, _verdict(resultat.statut), duree
        )
        return resultat.statut

    def _executer(
        self, instantane: Instantane, options: OptionsSolveur, numero: int, reste: float
    ) -> ResultatSolveur:
        """L'essai dans un fil à part, attendu jusqu'à la fin du budget au plus.

        Le moteur ne s'interrompt qu'entre deux étapes de la construction de son
        modèle : une étape peut durer bien plus que le temps limite (plusieurs
        dizaines de secondes quand l'essai retire `HORAIRES` d'un grand besoin).
        L'essai qui n'a pas rendu la main à l'échéance reçoit l'ordre d'arrêt et
        est abandonné à son fil ; le diagnostic s'arrête là (`_Budget`).
        """
        arret = threading.Event()
        boite: list[ResultatSolveur | BaseException] = []

        def executer() -> None:
            try:
                boite.append(self.moteur(instantane, options, arret))
            except BaseException as erreur:  # rendue au fil du diagnostic, qui la relève
                boite.append(erreur)

        fil = threading.Thread(target=executer, name=f"edt-diagnostic-essai-{numero}", daemon=True)
        fil.start()
        fil.join(max(0.0, reste - ATTENTE_RESERVEE_S))
        if fil.is_alive():
            arret.set()
            self.abandonne = fil
            self.nb_essais = numero
            journal.warning(
                "Diagnostic, essai %d : pas de réponse à la fin du temps accordé ; l'essai est "
                "arrêté et finit en arrière-plan, le diagnostic s'arrête là",
                numero,
            )
            raise _Budget
        if isinstance(boite[0], BaseException):
            raise boite[0]
        return boite[0]

    def _filtrer(
        self, gardes: set[_Element], temps_limite_s: float
    ) -> tuple[Instantane, OptionsSolveur]:
        """L'instantané et les options d'un essai qui ne garde que `gardes` parmi les candidats."""
        inst = self.instantane
        retires = {(e.nature, e.cle) for f in self.familles for e in f if e not in gardes}
        besoins = tuple(b for b in inst.besoins if ("besoin", b.id) not in retires)
        jours_fermes = tuple(
            j for rang, j in enumerate(inst.jours_fermes) if ("jour_ferme", rang) not in retires
        )
        ids_besoins = {b.id for b in besoins}
        if len(jours_fermes) == len(inst.jours_fermes):
            seances = tuple(s for s in inst.seances if s.besoin.id in ids_besoins)
            problemes = tuple(p for p in inst.problemes_depliage if p.besoin_id in ids_besoins)
        else:
            # Une fermeture retirée peut rouvrir une semaine : le dépliage est refait.
            depliage = deplier(
                besoins,
                inst.semaines,
                jours_fermes,
                inst.parametres.jours_travailles,
                periode=(inst.date_debut, inst.date_fin),
            )
            origines = {s.id: s for s in inst.seances}
            seances = tuple(_reprendre(s, origines) for s in depliage.seances)
            problemes = depliage.problemes
        seances = tuple(
            dataclasses.replace(s, verrou=None) if ("verrou", s.id) in retires else s
            for s in seances
        )
        instantane = dataclasses.replace(
            inst,
            besoins=besoins,
            seances=seances,
            problemes_depliage=problemes,
            jours_fermes=jours_fermes,
            indisponibilites=tuple(
                i for i in inst.indisponibilites if ("indisponibilite", i.id) not in retires
            ),
        )
        niveaux = {
            code: n
            for code, n in self.options.niveaux.items()
            if ("contrainte", code) not in retires
        }
        niveaux_regles = {
            regle_id: n
            for regle_id, n in self.niveaux_regles.items()
            if ("regle", regle_id) not in retires
        }
        options = OptionsSolveur(
            temps_limite_s=temps_limite_s,
            niveaux=niveaux,
            niveaux_regles=niveaux_regles,
            optimiser=False,
            graine=0,
            nb_workers=self.options.nb_workers,
            utiliser_indices=False,
            tolerer_violations_heritees=self.options.tolerer_violations_heritees,
            arret_sur_stagnation=True,
        )
        return instantane, options

    # -- résultat ----------------------------------------------------------------

    def _conclure(
        self, statut: StatutDiagnostic, budget_epuise: bool = False
    ) -> ResultatDiagnostic:
        duree = round(time.perf_counter() - self.debut, 3)
        if statut in (StatutDiagnostic.MINIMAL, StatutDiagnostic.PARTIEL):
            elements = sorted(self.gardes, key=_tri)
            regles = tuple(self._element(e) for e in elements if e.nature in NATURES_REGLES)
            donnees = tuple(self._element(e) for e in elements if e.nature in NATURES_DONNEES)
        else:
            regles = donnees = ()
        message = self._message(statut, len(regles), len(donnees), budget_epuise)
        journal.info(
            "Diagnostic du semestre %d : %s, %d règle(s) et %d donnée(s) en cause, %d essai(s) "
            "en %.1f s",
            self.instantane.semestre_id,
            statut.value,
            len(regles),
            len(donnees),
            self.nb_essais,
            duree,
        )
        return ResultatDiagnostic(
            statut, regles, donnees, message, self.nb_essais, duree, self.abandonne
        )

    def _message(
        self, statut: StatutDiagnostic, nb_regles: int, nb_donnees: int, budget_epuise: bool
    ) -> str:
        if statut is StatutDiagnostic.POSSIBLE:
            return (
                "Avec les données et les règles actuelles, un emploi du temps existe : le calcul "
                "n'est plus impossible. Relancer le calcul."
            )
        if statut is StatutDiagnostic.INDETERMINE:
            return (
                "Le premier essai, avec toutes les règles et toutes les données, n'a pas abouti "
                "dans le temps accordé : relancer le diagnostic avec plus de temps."
            )
        ensemble = f"{nb_regles} règle(s) et {nb_donnees} donnée(s)"
        if statut is StatutDiagnostic.MINIMAL:
            return (
                f"Ces {ensemble} rendent ensemble le calcul impossible, et aucune ne peut être "
                f"retirée sans qu'il devienne possible. {PHRASE_REGLES_DU_PROBLEME}"
            )
        cause = (
            "le temps accordé est épuisé"
            if budget_epuise
            else "un essai n'a pas abouti dans le temps qui lui restait"
        )
        return (
            f"Ces {ensemble} rendent ensemble le calcul impossible, mais l'ensemble n'est peut-être "
            f"pas le plus réduit : {cause}. Relancer le diagnostic avec plus de temps pour "
            f"l'affiner. {PHRASE_REGLES_DU_PROBLEME}"
        )

    def _element(self, element: _Element) -> ElementEnCause:
        """L'élément tel qu'il s'affiche : nature, identifiant texte, libellé."""
        inst = self.instantane
        cle = element.cle
        if element.nature == "contrainte":
            code = CodeContrainte(cle)
            return ElementEnCause("contrainte", code.value, f"Règle « {CATALOGUE[code].libelle} »")
        if element.nature == "regle":
            regle = next(r for r in inst.regles if r.id == cle)
            return ElementEnCause("regle", str(cle), f"Règle {regle.code} : {regle.phrase}")
        if element.nature == "besoin":
            besoin = inst.besoin(cle)
            nombre = sum(1 for s in inst.seances if s.besoin.id == cle)
            return ElementEnCause(
                "besoin", str(cle), f"Besoin « {besoin.libelle} » ({nombre} séance(s) à placer)"
            )
        if element.nature == "verrou":
            seance = inst.seance(cle)
            verrou = seance.verrou
            # Un verrou sur une salle inconnue peut être justement ce qui est en cause.
            codes = {s.id: s.code for s in inst.salles}
            salle = (
                f", salle {codes.get(verrou.salle_id, f'n° {verrou.salle_id} (inconnue)')}"
                if verrou.salle_id is not None
                else ""
            )
            return ElementEnCause(
                "verrou",
                cle,
                f"Verrou de « {seance.besoin.libelle} » (séance n° {seance.rang} de la semaine) : "
                f"{JOURS[verrou.jour]} {plage_vers_texte(verrou.debut, verrou.fin)}{salle}",
            )
        if element.nature == "indisponibilite":
            indispo = next(i for i in inst.indisponibilites if i.id == cle)
            motif = f" ({indispo.motif})" if indispo.motif else ""
            return ElementEnCause(
                "indisponibilite",
                str(cle),
                f"Indisponibilité de {_ressource(inst, indispo.cible, indispo.ressource_id)} le "
                f"{JOURS[indispo.jour]} {plage_vers_texte(indispo.debut, indispo.fin)}{motif}",
            )
        ferme = inst.jours_fermes[cle]
        identifiant = str(ferme.id) if ferme.id is not None else f"jour_ferme:{cle}"
        periode = (
            f"le {ferme.date_debut:%d/%m/%Y}"
            if ferme.date_debut == ferme.date_fin
            else f"du {ferme.date_debut:%d/%m/%Y} au {ferme.date_fin:%d/%m/%Y}"
        )
        portee = "" if ferme.groupes is None else " (certains groupes)"
        return ElementEnCause(
            "jour_ferme", identifiant, f"Jour fermé « {ferme.libelle} » {periode}{portee}"
        )


def _verdict(statut: StatutSolveur) -> str:
    """Le statut d'un essai, dit pour le diagnostic."""
    return {
        StatutSolveur.SOLUTION: "possible (un élément retiré est nécessaire)",
        StatutSolveur.INFAISABLE: "toujours impossible (les éléments retirés ne sont pas nécessaires)",
        StatutSolveur.TEMPS_DEPASSE: "sans conclusion dans le temps de l'essai",
        StatutSolveur.ARRETE: "interrompu",
    }[statut]


def _id(objet: object) -> int:
    return objet.id


def _tri(element: _Element) -> tuple:
    """Ordre stable : nature (règles puis données), puis ordre propre à la nature."""
    natures = (*NATURES_REGLES, *NATURES_DONNEES)
    return (natures.index(element.nature), element.ordre)


def _reprendre(seance: SeanceAPlacer, origines: dict[str, SeanceAPlacer]) -> SeanceAPlacer:
    """Séance d'un nouveau dépliage, avec le verrou et l'indice de la séance de même identifiant.

    Une séance que la fermeture retirée fait apparaître n'a ni verrou ni indice.
    """
    origine = origines.get(seance.id)
    if origine is None:
        return seance
    return dataclasses.replace(seance, verrou=origine.verrou, indice=origine.indice)


def _ressource(instantane: Instantane, cible: CibleIndisponibilite, ressource_id: int) -> str:
    """Nom lisible de la ressource d'une indisponibilité."""
    if cible is CibleIndisponibilite.ENSEIGNANT:
        return instantane.enseignant(ressource_id).nom
    if cible is CibleIndisponibilite.SALLE:
        return f"la salle {instantane.salle(ressource_id).code}"
    return f"le groupe {instantane.groupe(ressource_id).code}"
