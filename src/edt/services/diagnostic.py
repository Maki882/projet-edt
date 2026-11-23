"""Diagnostic d'un calcul impossible : les règles et les données qui le rendent impossible.

    diagnostic = diagnostiquer_calcul(calcul_id, budget_s=30.0)   # synchrone, borné
    diagnostic = lire_diagnostic(calcul_id)                       # None s'il n'y en a pas

Le diagnostic reprend un calcul à l'état « impossible » (prouvé par le moteur,
ou refusé par les pré-vérifications) et cherche, par des calculs d'essai sur
des sous-ensembles, un ensemble irréductible de règles nécessaires et de
données (besoins, verrous, indisponibilités, jours fermés) qui le rend
impossible (`edt.solveur.diagnostic`). Il ne crée aucune version et ne modifie
ni règle, ni niveau, ni donnée : il n'écrit que `calcul.statistiques["diagnostic"]`,
qu'un nouveau diagnostic remplace.

Il porte sur les données **actuelles** du semestre (instantané rechargé, verrous
de la version active), avec les options du calcul : niveaux des contraintes et
des règles, tolérance des violations héritées, fils de calcul. Si les données
ont changé au point que le calcul est devenu possible, le statut le dit.

Budget : la durée totale, chargement et enregistrement compris, ne dépasse pas
`budget_s` (de `BUDGET_MIN_S` à `BUDGET_MAX_S`) ; le diagnostic s'arrête de
lui-même et rend un résultat partiel plutôt que d'être coupé.

Un seul diagnostic à la fois dans un processus, et jamais pendant un calcul :
`diagnostic_en_cours` le dit, `edt.services.generation.lancer_calcul` refuse un
calcul pendant un diagnostic. Limite : un diagnostic lancé par la ligne de
commande n'empêche pas un calcul lancé depuis le serveur web (autre processus) ;
le diagnostic, qui ne fait que lire, n'en est pas faussé, mais les deux se
partagent les processeurs.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import math
import threading
import time
from dataclasses import dataclass

from sqlalchemy.exc import SQLAlchemyError

from edt.config import Configuration, charger_configuration
from edt.db.modeles import Calcul
from edt.db.moteur import identifiant_valide, session_base, transaction_immediate
from edt.domaine.enumerations import EtatCalcul
from edt.erreurs import (
    ErreurCalculEnCours,
    ErreurDonneesInvalides,
    ErreurEDT,
    ErreurIntrouvable,
    ErreurRegleMetier,
)
from edt.services.generation import MESSAGE_SANS_SEANCE, calcul_en_cours
from edt.services.instantane import charger_instantane
from edt.services.niveaux import niveaux_du_calcul
from edt.solveur.contrat import OptionsSolveur
from edt.solveur.diagnostic import (
    NATURES_DONNEES,
    NATURES_REGLES,
    ElementEnCause,
    StatutDiagnostic,
    isoler_impossibilite,
)

__all__ = [
    "AVERTISSEMENT_DONNEES_ACTUELLES",
    "BUDGET_COMMANDE_S",
    "BUDGET_MAX_S",
    "BUDGET_MIN_S",
    "BUDGET_PAGE_S",
    "CLE_DIAGNOSTIC",
    "Diagnostic",
    "ElementEnCause",
    "StatutDiagnostic",
    "diagnostic_en_cours",
    "diagnostiquer_calcul",
    "lire_diagnostic",
    "message_diagnostic_en_cours",
]

journal = logging.getLogger(__name__)

BUDGET_MIN_S = 5.0
"""Temps accordé le plus court : en dessous, même un petit établissement ne conclut pas."""
BUDGET_MAX_S = 600.0
"""Temps accordé le plus long (dix minutes)."""
BUDGET_PAGE_S = 30.0
"""Temps accordé depuis la page d'un calcul : sous les délais habituels d'un serveur frontal."""
BUDGET_COMMANDE_S = 60.0
"""Temps accordé par défaut en ligne de commande."""
RESERVE_ENREGISTREMENT_S = 0.5
"""Part du budget gardée pour enregistrer le résultat, hors des calculs d'essai."""
CLE_DIAGNOSTIC = "diagnostic"
"""Clé de `calcul.statistiques` qui porte le dernier diagnostic."""

AVERTISSEMENT_DONNEES_ACTUELLES = (
    "Ce diagnostic porte sur les données et les règles actuelles du semestre, pas sur celles du "
    "moment du calcul."
)
"""Phrase fixe, dans chaque message de diagnostic, à l'écran et en ligne de commande."""


@dataclass(frozen=True)
class Diagnostic:
    """Résultat d'un diagnostic, tel que l'interface l'affiche et que la base le garde.

    - `regles` : contraintes du catalogue (nature `contrainte`, ordre du
      catalogue) puis règles personnalisées (`regle`, par identifiant) ;
    - `donnees` : besoins, verrous, indisponibilités, jours fermés, dans cet
      ordre, chacun par identifiant ;
    - `message` : résumé prêt à afficher, qui finit par
      `AVERTISSEMENT_DONNEES_ACTUELLES` ;
    - `lance_le` : date-heure locale du lancement, au format ISO.
    """

    calcul_id: int
    statut: StatutDiagnostic
    regles: tuple[ElementEnCause, ...]
    donnees: tuple[ElementEnCause, ...]
    message: str
    nb_essais: int
    duree_s: float
    budget_s: float
    lance_le: str

    def vers_dict(self) -> dict[str, object]:
        """Représentation JSON (`calcul.statistiques["diagnostic"]`)."""
        return {
            "calcul_id": self.calcul_id,
            "statut": self.statut.value,
            "regles": [element.vers_dict() for element in self.regles],
            "donnees": [element.vers_dict() for element in self.donnees],
            "message": self.message,
            "nb_essais": self.nb_essais,
            "duree_s": self.duree_s,
            "budget_s": self.budget_s,
            "lance_le": self.lance_le,
        }

    @classmethod
    def depuis_dict(cls, donnees: object) -> Diagnostic:
        """Relit `vers_dict` ; lève `ErreurDonneesInvalides` sur une entrée mal formée."""
        if not isinstance(donnees, dict):
            raise ErreurDonneesInvalides(f"Diagnostic illisible : {donnees!r}.")
        try:
            statut = StatutDiagnostic(donnees.get("statut"))
        except ValueError as erreur:
            raise ErreurDonneesInvalides(
                f"Statut de diagnostic inconnu : {donnees.get('statut')!r}."
            ) from erreur
        listes = {cle: donnees.get(cle) for cle in ("regles", "donnees")}
        if not all(isinstance(liste, list) for liste in listes.values()):
            raise ErreurDonneesInvalides("Diagnostic illisible : listes d'éléments absentes.")
        regles = tuple(ElementEnCause.depuis_dict(e) for e in listes["regles"])
        elements = tuple(ElementEnCause.depuis_dict(e) for e in listes["donnees"])
        if any(e.nature not in NATURES_REGLES for e in regles) or any(
            e.nature not in NATURES_DONNEES for e in elements
        ):
            raise ErreurDonneesInvalides(
                "Diagnostic illisible : élément rangé dans la mauvaise liste."
            )
        entiers = [donnees.get(cle) for cle in ("calcul_id", "nb_essais")]
        nombres = [donnees.get(cle) for cle in ("duree_s", "budget_s")]
        textes = [donnees.get(cle) for cle in ("message", "lance_le")]
        if (
            not all(isinstance(v, int) and not isinstance(v, bool) for v in entiers)
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nombres)
            or not all(isinstance(v, str) for v in textes)
        ):
            raise ErreurDonneesInvalides("Diagnostic illisible : champ manquant ou mal typé.")
        return cls(
            calcul_id=entiers[0],
            statut=statut,
            regles=regles,
            donnees=elements,
            message=textes[0],
            nb_essais=entiers[1],
            duree_s=float(nombres[0]),
            budget_s=float(nombres[1]),
            lance_le=textes[1],
        )


_verrou = threading.Lock()
_en_cours: tuple[int, float] | None = None
"""Diagnostic en cours dans ce processus : `(calcul_id, échéance en time.monotonic())`."""


def diagnostic_en_cours() -> int | None:
    """Identifiant du calcul en cours de diagnostic dans ce processus, sinon `None`."""
    with _verrou:
        return _en_cours[0] if _en_cours is not None else None


def message_diagnostic_en_cours() -> str | None:
    """Phrase de refus pendant un diagnostic (« ... relancer ensuite. »), `None` sans diagnostic."""
    with _verrou:
        en_cours = _en_cours
    return _message_refus(*en_cours) if en_cours is not None else None


def _message_refus(calcul_id: int, echeance: float) -> str:
    reste = math.ceil(echeance - time.monotonic())
    if reste <= 0:
        # Budget écoulé : un calcul d'essai abandonné finit de s'interrompre (`_liberer_apres`).
        return (
            f"Un diagnostic sur le calcul n° {calcul_id} finit d'interrompre son dernier calcul "
            "d'essai : relancer dans quelques instants."
        )
    return (
        f"Un diagnostic est en cours sur le calcul n° {calcul_id} ; il se termine en moins de "
        f"{reste} s : relancer ensuite."
    )


def diagnostiquer_calcul(
    calcul_id: int, budget_s: float = BUDGET_PAGE_S, *, config: Configuration | None = None
) -> Diagnostic:
    """Diagnostique le calcul impossible `calcul_id` en `budget_s` secondes au plus, et l'enregistre.

    Lève `ErreurIntrouvable` (calcul inexistant ou identifiant invalide),
    `ErreurDonneesInvalides` (budget non numérique ou hors de
    [`BUDGET_MIN_S`, `BUDGET_MAX_S`]), `ErreurCalculEnCours` (un calcul, ou un
    autre diagnostic, est en cours), `ErreurRegleMetier` (le calcul n'est pas
    à l'état « impossible »). Chaque message dit quoi faire.
    """
    global _en_cours
    debut = time.monotonic()
    lance_le = dt.datetime.now().isoformat(timespec="seconds")
    if not identifiant_valide(calcul_id):
        raise ErreurIntrouvable(f"Le calcul n° {calcul_id} n'existe pas.")
    if (
        isinstance(budget_s, bool)
        or not isinstance(budget_s, (int, float))
        or not math.isfinite(budget_s)
        or not BUDGET_MIN_S <= budget_s <= BUDGET_MAX_S
    ):
        raise ErreurDonneesInvalides(
            f"Le temps accordé au diagnostic doit être un nombre de secondes entre "
            f"{BUDGET_MIN_S:g} et {BUDGET_MAX_S:g} ; lu : {budget_s!r}."
        )
    budget_s = float(budget_s)
    config = config or charger_configuration()
    with _verrou:
        deja = _en_cours
        if deja is None:
            _en_cours = (calcul_id, debut + budget_s)
    if deja is not None:
        refus = _message_refus(*deja)
        journal.info("Diagnostic du calcul %d refusé : %s", calcul_id, refus)
        raise ErreurCalculEnCours(refus)
    abandonne: threading.Thread | None = None
    try:
        # Inscrit avant de regarder les calculs : un lancement qui passe après ce contrôle
        # voit le diagnostic (`lancer_calcul`) ; un lancement passé avant est vu ici.
        suivi = calcul_en_cours()
        if suivi is not None:
            raise ErreurCalculEnCours(
                f"Le calcul n° {suivi.id} est en cours : attendre sa fin ou l'arrêter, puis "
                "relancer le diagnostic."
            )
        diagnostic, abandonne = _diagnostiquer(calcul_id, budget_s, debut, lance_le, config)
        return diagnostic
    finally:
        if abandonne is not None and abandonne.is_alive():
            # L'essai abandonné occupe encore les processeurs : le diagnostic reste « en
            # cours » (calculs et diagnostics refusés) jusqu'à ce qu'il ait rendu la main.
            threading.Thread(
                target=_liberer_apres,
                args=(abandonne, calcul_id),
                name="edt-diagnostic-liberation",
                daemon=True,
            ).start()
        else:
            with _verrou:
                _en_cours = None


def _liberer_apres(fil: threading.Thread, calcul_id: int) -> None:
    """Attend la fin d'un calcul d'essai abandonné, puis libère la place du diagnostic."""
    global _en_cours
    debut = time.monotonic()
    fil.join()
    with _verrou:
        _en_cours = None
    journal.info(
        "Diagnostic du calcul %d : le calcul d'essai abandonné s'est arrêté %.1f s après la fin "
        "du diagnostic",
        calcul_id,
        time.monotonic() - debut,
    )


def _diagnostiquer(
    calcul_id: int, budget_s: float, debut: float, lance_le: str, config: Configuration
) -> tuple[Diagnostic, threading.Thread | None]:
    """Corps de `diagnostiquer_calcul`, garde-fous de concurrence passés.

    Rend aussi le fil d'un calcul d'essai abandonné encore en marche, s'il y en a un.
    """
    with session_base(config.db_url) as session:
        calcul = session.get(Calcul, calcul_id)
        if calcul is None:
            raise ErreurIntrouvable(f"Le calcul n° {calcul_id} n'existe pas.")
        etat = EtatCalcul(calcul.etat)
        if etat is not EtatCalcul.INFAISABLE:
            raise ErreurRegleMetier(
                f"Le calcul n° {calcul_id} n'est pas impossible (résultat : {etat.libelle}) : le "
                "diagnostic ne porte que sur un calcul impossible."
            )
        options = _options_du_calcul(calcul)
        instantane = charger_instantane(session, calcul.semestre_id)
    journal.info(
        "Diagnostic du calcul %d (semestre %d) : budget %.1f s, instantané chargé en %.2f s",
        calcul_id,
        instantane.semestre_id,
        budget_s,
        time.monotonic() - debut,
    )
    if not instantane.seances and not instantane.problemes_depliage:
        # Le calcul a été refusé faute de séance : il n'y a rien à réduire.
        statut, regles, donnees, nb_essais = StatutDiagnostic.MINIMAL, (), (), 0
        message, abandonne = MESSAGE_SANS_SEANCE, None
    else:
        restant = budget_s - (time.monotonic() - debut) - RESERVE_ENREGISTREMENT_S
        resultat = isoler_impossibilite(instantane, options, max(restant, 0.001))
        statut, regles, donnees = resultat.statut, resultat.regles, resultat.donnees
        message, nb_essais = resultat.message, resultat.nb_essais
        abandonne = resultat.essai_abandonne
    diagnostic = Diagnostic(
        calcul_id=calcul_id,
        statut=statut,
        regles=regles,
        donnees=donnees,
        message=f"{message} {AVERTISSEMENT_DONNEES_ACTUELLES}",
        nb_essais=nb_essais,
        duree_s=round(time.monotonic() - debut, 3),
        budget_s=budget_s,
        lance_le=lance_le,
    )
    probleme = _enregistrer(diagnostic, config)
    if probleme is not None:
        diagnostic = dataclasses.replace(diagnostic, message=f"{diagnostic.message} {probleme}")
    journal.info(
        "Diagnostic du calcul %d : %s, %d règle(s) et %d donnée(s), %d essai(s), %.1f s",
        calcul_id,
        statut.value,
        len(regles),
        len(donnees),
        nb_essais,
        time.monotonic() - debut,
    )
    return diagnostic, abandonne


def _options_du_calcul(calcul: Calcul) -> OptionsSolveur:
    """Options du calcul enregistré : niveaux, niveaux des règles, tolérance, fils de calcul.

    Le temps limite, la graine et l'objectif sont ceux du diagnostic. Des
    options illisibles lèvent `ErreurDonneesInvalides` en le disant.
    """
    parametres = calcul.parametres if isinstance(calcul.parametres, dict) else None
    desactivees = calcul.contraintes_desactivees
    if parametres is None or not isinstance(desactivees, list):
        raise ErreurDonneesInvalides(
            f"Les options enregistrées du calcul n° {calcul.id} sont illisibles : relancer le "
            "calcul, puis le diagnostiquer."
        )
    try:
        niveaux = niveaux_du_calcul(parametres, desactivees)
        niveaux_regles = OptionsSolveur.lire_niveaux_regles(parametres)
    except ErreurEDT as erreur:
        raise ErreurDonneesInvalides(
            f"Les niveaux enregistrés du calcul n° {calcul.id} sont illisibles ({erreur}) : "
            "relancer le calcul, puis le diagnostiquer."
        ) from erreur
    nb_workers = parametres.get("nb_workers")
    return OptionsSolveur(
        niveaux=niveaux,
        niveaux_regles=niveaux_regles,
        tolerer_violations_heritees=parametres.get("tolerer_violations_heritees") is True,
        nb_workers=nb_workers if isinstance(nb_workers, int) and nb_workers >= 0 else 0,
    )


def _enregistrer(diagnostic: Diagnostic, config: Configuration) -> str | None:
    """Écrit le diagnostic dans `statistiques["diagnostic"]`, sans toucher aux autres clés.

    Rend `None` si c'est fait, sinon la phrase qui dit pourquoi il ne l'est pas
    (calcul supprimé entre-temps, statistiques illisibles, base indisponible).
    """
    try:
        with session_base(config.db_url) as session:
            transaction_immediate(session)
            calcul = session.get(Calcul, diagnostic.calcul_id)
            if calcul is None:
                journal.warning(
                    "Calcul %d supprimé pendant son diagnostic : résultat non enregistré",
                    diagnostic.calcul_id,
                )
                return "Le calcul a été supprimé entre-temps : ce résultat n'est pas enregistré."
            if not isinstance(calcul.statistiques, dict):
                journal.error(
                    "Calcul %d : statistiques illisibles (%s), diagnostic non enregistré",
                    diagnostic.calcul_id,
                    type(calcul.statistiques).__name__,
                )
                return (
                    "Les mesures enregistrées du calcul sont illisibles : ce résultat n'est pas "
                    "enregistré."
                )
            # Nouveau dictionnaire : la colonne JSON n'enregistre pas une modification en place.
            calcul.statistiques = {
                **calcul.statistiques,
                CLE_DIAGNOSTIC: diagnostic.vers_dict(),
            }
    except SQLAlchemyError:
        journal.exception(
            "Calcul %d : enregistrement du diagnostic impossible", diagnostic.calcul_id
        )
        return (
            "La base de données n'a pas pu enregistrer ce résultat (voir le journal) : il ne sera "
            "pas réaffiché."
        )
    return None


def lire_diagnostic(calcul_id: int, *, config: Configuration | None = None) -> Diagnostic | None:
    """Le dernier diagnostic du calcul, ou `None` (calcul inconnu, pas de diagnostic, entrée illisible).

    Ne lève pas d'erreur du projet : une entrée illisible est signalée au
    journal (avertissement) et vaut `None`, pour que la page du calcul
    s'affiche toujours. Une base inaccessible lève l'erreur de la base, comme
    toute lecture.
    """
    if not identifiant_valide(calcul_id):
        return None
    config = config or charger_configuration()
    with session_base(config.db_url) as session:
        calcul = session.get(Calcul, calcul_id)
        statistiques = calcul.statistiques if calcul is not None else None
    if not isinstance(statistiques, dict) or CLE_DIAGNOSTIC not in statistiques:
        return None
    try:
        return Diagnostic.depuis_dict(statistiques[CLE_DIAGNOSTIC])
    except ErreurDonneesInvalides as erreur:
        journal.warning(
            "Calcul %d : diagnostic enregistré illisible, ignoré (%s)", calcul_id, erreur
        )
        return None
