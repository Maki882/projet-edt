"""Orchestration d'un calcul (génération d'un emploi du temps), indépendante du moteur.

Ce module est ce que l'interface web et la ligne de commande appellent :

    identifiant = lancer_calcul(semestre_id, options)   # rend la main tout de suite
    suivi = etat_calcul(identifiant)                    # à interroger chaque seconde
    demander_arret(identifiant)                         # bouton « Arrêter »
    suivi = attendre_calcul(identifiant)                # CLI et tests

Déroulement d'un calcul :

1. une ligne `calcul` est créée à l'état « en cours » ; **un seul calcul à la
   fois** (`ErreurCalculEnCours` sinon) ;
2. un fil d'exécution d'arrière-plan charge l'instantané du semestre et lance
   les **pré-vérifications** (`pre_verifier_semestre` les expose aussi seules
   à l'interface) : une impossibilité certaine (besoin impossible à déplier,
   séance sans salle, charge trop forte, verrous contradictoires...) **refuse**
   le calcul, qui passe à l'état « impossible » avec la liste des raisons ;
   une situation tendue devient un avertissement joint au suivi ;
3. le moteur (`edt.solveur.resoudre` par défaut) est appelé ; tout ce qui est
   journalisé pendant le calcul va aussi dans un fichier dédié de
   `<EDT_LOG_DIR>/generations/` ;
4. une solution est **contrôlée par le vérificateur** (`edt.verification`) :
   s'il relève une erreur, c'est un défaut du moteur, le calcul est en échec
   et rien n'est enregistré ;
5. la solution contrôlée devient une **nouvelle version** d'emploi du temps
   avec ses séances hebdomadaires (et activée), puis ses **séances datées**
   sont produites et replacées (`edt.services.instanciation`) ; dans tous les
   autres cas (impossible, temps dépassé, arrêt, erreur) **aucune version
   n'est créée** et le message explique pourquoi.

Les fonctions de ce module ouvrent leurs propres sessions : un calcul vit
plus longtemps que la requête qui l'a lancé. Le seul état gardé en mémoire
est le suivi des fils en cours ; tout le reste est en base.

Calcul abandonné
----------------
Un calcul appartient au processus qui l'a lancé (serveur web ou commande
`python -m edt generer`) : son numéro de processus est noté avec lui. Si ce
processus meurt (interruption, arrêt du serveur), le calcul resterait « en
cours » et bloquerait tout. Il est donc **clos en erreur** dès que quelqu'un
s'y intéresse (`lancer_calcul`, `calcul_en_cours`, `demander_arret`,
`cloturer_calculs_interrompus`), mais seulement si plus rien ne l'exécute :
le fil du calcul a disparu de ce processus, ou le processus noté n'existe
plus, ou le calcul a dépassé son temps limite de plus de `DELAI_DE_GRACE_S`.
Un calcul vivant d'un autre processus n'est jamais clos par celui-ci.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import logging
import os
import threading
import time
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from edt import solveur
from edt.config import Configuration, charger_configuration
from edt.db.modeles import Calcul, SeanceHebdo, Semestre, VersionEdt
from edt.db.moteur import session_base
from edt.domaine.contraintes import (
    CATALOGUE,
    Niveau,
    Niveaux,
    niveaux_depuis_desactivees,
    normaliser_codes,
)
from edt.domaine.enumerations import EtatCalcul, OrigineVersion
from edt.domaine.instantane import Instantane, Placement, SeanceAPlacer
from edt.erreurs import (
    ErreurCalculEnCours,
    ErreurEDT,
    ErreurInfaisable,
    ErreurIntrouvable,
    ErreurNonLivre,
    ErreurTempsDepasse,
)
from edt.journal import ouvrir_journal_calcul
from edt.services.instanciation import instancier_version
from edt.services.instantane import charger_instantane
from edt.services.parametres import lire_parametres
from edt.solveur.contrat import Moteur, OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.solveur.preverification import pre_verifier
from edt.verification import Anomalie, calculer_indicateurs, verifier

journal = logging.getLogger(__name__)

_ETAT_PAR_STATUT = {
    StatutSolveur.SOLUTION: EtatCalcul.REUSSI,
    StatutSolveur.INFAISABLE: EtatCalcul.INFAISABLE,
    StatutSolveur.TEMPS_DEPASSE: EtatCalcul.TEMPS_DEPASSE,
    StatutSolveur.ARRETE: EtatCalcul.ARRETE,
}

_MESSAGES_PAR_DEFAUT = {
    EtatCalcul.REUSSI: "L'emploi du temps a été généré.",
    EtatCalcul.INFAISABLE: (
        "Impossible : aucun emploi du temps ne respecte toutes les contraintes actives."
    ),
    EtatCalcul.TEMPS_DEPASSE: (
        "Aucun emploi du temps trouvé dans le temps imparti. Ce n'est pas une preuve "
        "d'impossibilité : le calcul peut être relancé plus longtemps."
    ),
    EtatCalcul.ARRETE: "Calcul arrêté à la demande de l'utilisateur.",
}

MESSAGE_INTERROMPU = "Calcul interrompu par l'arrêt de l'application."
CLE_PROCESSUS = "pid"
"""Clé des statistiques d'un calcul en cours : numéro du processus qui l'exécute."""
DELAI_DE_GRACE_S = 600.0
"""Dépassement du temps limite au-delà duquel un calcul « en cours » est tenu pour mort."""
PAS_ATTENTE_S = 0.5
"""Tranche d'attente de `attendre_calcul` (laisse passer une interruption au clavier)."""
NB_RAISONS_DANS_LE_MESSAGE = 3
"""Raisons citées dans le message d'un refus ; la liste complète est dans les statistiques."""

MESSAGE_ERREUR_INATTENDUE = (
    "Une erreur inattendue a interrompu le calcul. Le détail est dans le journal du calcul."
)


@dataclass(frozen=True)
class SuiviCalcul:
    """État d'un calcul, tel que l'interface l'affiche.

    - `etat` : en cours, réussi, impossible (prouvé), temps dépassé, arrêté, erreur ;
    - `temps_ecoule_s` : temps écoulé depuis le lancement (calcul en cours)
      ou durée totale (calcul terminé) ;
    - `message` : phrase française pour l'utilisateur ;
    - `version_id` : version créée, seulement si le calcul a réussi ;
    - `niveaux` : niveau retenu pour chaque contrainte (clé absente : contrainte
      ignorée), lu dans les options enregistrées ou, pour un calcul ancien,
      déduit des contraintes désactivées ;
    - `contraintes_desactivees` : codes ignorés au lancement (absents de `niveaux`) ;
    - `fichier_journal` : chemin du journal dédié du calcul ;
    - `avertissements` : situations tendues relevées par les pré-vérifications
      et remarques du moteur, à montrer à l'utilisateur ;
    - `statistiques` : mesures du moteur, plus `impossibilites` (raisons d'un
      refus), `indicateurs` (qualité de la solution) et `instanciation`
      (bilan des séances datées) quand ils existent.
    """

    id: int
    semestre_id: int
    etat: EtatCalcul
    demarre_le: dt.datetime
    termine_le: dt.datetime | None
    temps_ecoule_s: float
    message: str
    version_id: int | None
    contraintes_desactivees: tuple[str, ...]
    statistiques: dict
    fichier_journal: str | None
    arret_demande: bool
    avertissements: tuple[str, ...] = field(default=())
    niveaux: Niveaux = field(default_factory=dict)

    @property
    def en_cours(self) -> bool:
        """Vrai tant que le calcul n'est pas terminé."""
        return self.etat is EtatCalcul.EN_COURS

    @property
    def reussi(self) -> bool:
        """Vrai si le calcul a produit une version d'emploi du temps."""
        return self.etat is EtatCalcul.REUSSI


@dataclass
class _CalculActif:
    fil: threading.Thread
    arret: threading.Event


_verrou = threading.Lock()
_actifs: dict[int, _CalculActif] = {}


# --------------------------------------------------------------------------- #
# Fonctions publiques
# --------------------------------------------------------------------------- #


def lancer_calcul(
    semestre_id: int,
    options: OptionsSolveur | None = None,
    *,
    moteur: Moteur | None = None,
    version_base_id: int | None = None,
    activer: bool = True,
) -> int:
    """Lance un calcul en arrière-plan et renvoie tout de suite son identifiant.

    - `options` : réglages du calcul (défaut : tirés des paramètres globaux) ;
    - `moteur` : moteur à utiliser (défaut : `edt.solveur.resoudre`) ;
    - `version_base_id` : version dont on reprend verrous et indices (défaut :
      la version active du semestre) ;
    - `activer` : rendre active la version produite en cas de succès.

    Lève `ErreurCalculEnCours` si un calcul tourne déjà, `ErreurIntrouvable`
    si le semestre n'existe pas.
    """
    config = charger_configuration()
    moteur = moteur or solveur.resoudre
    with _verrou:
        with session_base(config.db_url) as session:
            if session.get(Semestre, semestre_id) is None:
                raise ErreurIntrouvable(f"Le semestre {semestre_id} n'existe pas.")
            _cloturer_abandonnes(session)
            deja = session.scalar(
                select(Calcul.id).where(Calcul.etat == EtatCalcul.EN_COURS).limit(1)
            )
            if deja is not None or any(actif.fil.is_alive() for actif in _actifs.values()):
                raise ErreurCalculEnCours(
                    "Un calcul est déjà en cours : attendre sa fin ou l'arrêter avant d'en "
                    "lancer un autre."
                )
            if options is None:
                options = OptionsSolveur.depuis_parametres(lire_parametres(session))
            calcul = Calcul(
                semestre_id=semestre_id,
                etat=EtatCalcul.EN_COURS,
                demarre_le=dt.datetime.now(),
                parametres=options.vers_dict(),
                contraintes_desactivees=sorted(options.contraintes_desactivees),
                statistiques={CLE_PROCESSUS: os.getpid()},
            )
            session.add(calcul)
            session.flush()
            calcul_id = calcul.id

        arret = threading.Event()
        fil = threading.Thread(
            target=_executer,
            name=f"calcul-{calcul_id}",
            daemon=True,
            args=(calcul_id, semestre_id, options, moteur, arret, version_base_id, activer, config),
        )
        _actifs[calcul_id] = _CalculActif(fil=fil, arret=arret)
        fil.start()

    journal.info("Calcul %d lancé pour le semestre %d", calcul_id, semestre_id)
    return calcul_id


def pre_verifier_semestre(
    session: Session,
    semestre_id: int,
    contraintes_desactivees: Iterable[str] = (),
    *,
    niveaux: Niveaux | None = None,
    version_base_id: int | None = None,
    tolerer_violations_heritees: bool = False,
) -> list[Anomalie]:
    """Pré-vérifications d'un calcul sur le semestre, sans le lancer.

    Renvoie les impossibilités certaines (gravité « erreur » : un calcul
    serait refusé) et les situations tendues (« avertissement »), en phrases
    lisibles : charge des groupes et des enseignants, salles, séances sans
    salle ou sans horaire possible, verrous contradictoires, besoins non
    dépliables. `niveaux` : niveaux retenus ; seules les contraintes présentes
    sont examinées, quel que soit leur niveau. À défaut,
    `contraintes_desactivees` donne les codes ignorés. Les verrous examinés
    sont ceux de `version_base_id` (défaut : la version active du semestre).
    Voir `edt.solveur.preverification`.
    """
    if niveaux is not None:
        contraintes_desactivees = frozenset(CATALOGUE) - normaliser_codes(niveaux.keys())
    instantane = charger_instantane(session, semestre_id, version_base_id)
    return pre_verifier(
        instantane,
        contraintes_desactivees,
        tolerer_violations_heritees=tolerer_violations_heritees,
        niveaux=niveaux,
    )


def etat_calcul(calcul_id: int) -> SuiviCalcul:
    """État courant du calcul `calcul_id` ; lève `ErreurIntrouvable` s'il n'existe pas."""
    with session_base() as session:
        calcul = session.get(Calcul, calcul_id)
        if calcul is None:
            raise ErreurIntrouvable(f"Le calcul {calcul_id} n'existe pas.")
        return _suivi(session, calcul)


def calcul_en_cours() -> SuiviCalcul | None:
    """Le calcul en cours, s'il y en a un.

    Un calcul abandonné par son processus est d'abord clos : il ne compte pas.
    """
    with _verrou, session_base() as session:
        _cloturer_abandonnes(session)
        calcul = session.scalar(
            select(Calcul).where(Calcul.etat == EtatCalcul.EN_COURS).order_by(Calcul.id.desc())
        )
        return _suivi(session, calcul) if calcul is not None else None


def dernier_calcul(semestre_id: int | None = None) -> SuiviCalcul | None:
    """Le calcul le plus récent (du semestre donné, ou tous semestres confondus)."""
    with session_base() as session:
        requete = select(Calcul).order_by(Calcul.id.desc()).limit(1)
        if semestre_id is not None:
            requete = requete.where(Calcul.semestre_id == semestre_id)
        calcul = session.scalar(requete)
        return _suivi(session, calcul) if calcul is not None else None


def demander_arret(calcul_id: int) -> SuiviCalcul:
    """Demande l'arrêt du calcul et renvoie son état.

    L'arrêt n'est pas instantané : le moteur rend la main dès qu'il voit la
    demande. Si un emploi du temps valide existe déjà, il est conservé et le
    calcul réussit ; sinon l'état passe à « arrêté ». Sans effet sur un calcul
    terminé. L'arrêt n'atteint que les calculs lancés par ce processus ; un
    calcul que plus rien n'exécute est clos en erreur.
    """
    with _verrou, session_base() as session:
        calcul = session.get(Calcul, calcul_id)
        if calcul is None:
            raise ErreurIntrouvable(f"Le calcul {calcul_id} n'existe pas.")
        if calcul.etat == EtatCalcul.EN_COURS:
            calcul.arret_demande = True
            actif = _actifs.get(calcul_id)
            if actif is not None:
                actif.arret.set()
                journal.info("Arrêt demandé pour le calcul %d", calcul_id)
            elif not _cloturer_abandonnes(session):
                journal.warning(
                    "Arrêt demandé pour le calcul %d, qu'un autre processus exécute encore : "
                    "il ira à son terme",
                    calcul_id,
                )
        session.flush()
        return _suivi(session, calcul)


def attendre_calcul(calcul_id: int, delai_s: float | None = None) -> SuiviCalcul:
    """Attend la fin du calcul (au plus `delai_s` secondes) et renvoie son état.

    Si le délai expire, l'état renvoyé est encore « en cours ». L'attente
    se fait par courtes tranches : une interruption au clavier la traverse.
    """
    with _verrou:
        actif = _actifs.get(calcul_id)
    if actif is not None:
        echeance = None if delai_s is None else time.monotonic() + delai_s
        while actif.fil.is_alive():
            reste = None if echeance is None else echeance - time.monotonic()
            if reste is not None and reste <= 0:
                break
            actif.fil.join(PAS_ATTENTE_S if reste is None else min(PAS_ATTENTE_S, reste))
    return etat_calcul(calcul_id)


def exiger_reussite(suivi: SuiviCalcul) -> SuiviCalcul:
    """Renvoie `suivi` s'il a réussi, sinon lève l'erreur métier correspondante.

    Pratique en ligne de commande : `ErreurInfaisable`, `ErreurTempsDepasse`
    ou `ErreurEDT` deviennent un code de sortie non nul.
    """
    if suivi.reussi:
        return suivi
    if suivi.etat is EtatCalcul.INFAISABLE:
        raise ErreurInfaisable(suivi.message)
    if suivi.etat is EtatCalcul.TEMPS_DEPASSE:
        raise ErreurTempsDepasse(suivi.message)
    raise ErreurEDT(suivi.message or f"Le calcul {suivi.id} n'a pas abouti ({suivi.etat.libelle}).")


def cloturer_calculs_interrompus() -> int:
    """Marque en erreur les calculs restés « en cours » que plus rien n'exécute.

    Appelée au démarrage du serveur, et d'office avant tout lancement : un
    calcul abandonné par son processus bloquerait sinon tous les suivants. Un
    calcul qu'un autre processus exécute encore est laissé tranquille (voir
    l'en-tête du module). Renvoie le nombre de calculs clôturés.
    """
    with _verrou, session_base() as session:
        return _cloturer_abandonnes(session)


def _processus_vivant(pid: int) -> bool:
    """Vrai si un processus de numéro `pid` existe sur cette machine."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # Sous Windows, `os.kill(pid, 0)` tuerait le processus : on l'interroge sans y toucher.
        noyau = ctypes.WinDLL("kernel32", use_last_error=True)
        noyau.OpenProcess.restype = ctypes.c_void_p
        noyau.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        noyau.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        noyau.CloseHandle.argtypes = (ctypes.c_void_p,)
        poignee = noyau.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not poignee:
            # Accès refusé (5) : il existe, mais n'est pas à nous.
            return ctypes.get_last_error() == 5
        try:
            code = ctypes.c_ulong()
            if not noyau.GetExitCodeProcess(poignee, ctypes.byref(code)):
                return True  # dans le doute, on ne clôt pas un calcul peut-être vivant
            return code.value == 259  # STILL_ACTIVE
        finally:
            noyau.CloseHandle(poignee)
    try:
        os.kill(pid, 0)  # signal 0 : ne fait que vérifier l'existence
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # il existe, mais n'est pas à nous
    return True


def _abandonne(calcul: Calcul, vivants: Collection[int]) -> bool:
    """Vrai si plus rien n'exécute ce calcul resté « en cours » (voir l'en-tête du module)."""
    if calcul.id in vivants:
        return False
    pid = (calcul.statistiques or {}).get(CLE_PROCESSUS)
    if not isinstance(pid, int) or pid == os.getpid() or not _processus_vivant(pid):
        return True
    # Le numéro de processus a pu être réattribué : passé le temps limite et le délai de
    # grâce, le calcul ne peut plus être vivant.
    limite = float((calcul.parametres or {}).get("temps_limite_s") or 0)
    echeance = calcul.demarre_le + dt.timedelta(seconds=limite + DELAI_DE_GRACE_S)
    return dt.datetime.now() > echeance


def _cloturer_abandonnes(session: Session) -> int:
    """Clôt en erreur les calculs « en cours » abandonnés ; à appeler en tenant `_verrou`.

    Le verrou garantit qu'un calcul de ce processus est soit enregistré avec
    son fil, soit pas encore créé : il ne peut pas être pris pour abandonné.
    """
    vivants = {calcul_id for calcul_id, actif in _actifs.items() if actif.fil.is_alive()}
    abandonnes = [
        calcul
        for calcul in session.scalars(select(Calcul).where(Calcul.etat == EtatCalcul.EN_COURS))
        if _abandonne(calcul, vivants)
    ]
    for calcul in abandonnes:
        calcul.etat = EtatCalcul.ERREUR
        calcul.message = MESSAGE_INTERROMPU
        calcul.termine_le = dt.datetime.now()
        journal.warning("Calcul %d clôturé : %s", calcul.id, MESSAGE_INTERROMPU)
    session.flush()
    return len(abandonnes)


# --------------------------------------------------------------------------- #
# Exécution dans le fil d'arrière-plan
# --------------------------------------------------------------------------- #


def _executer(
    calcul_id: int,
    semestre_id: int,
    options: OptionsSolveur,
    moteur: Moteur,
    arret: threading.Event,
    version_base_id: int | None,
    activer: bool,
    config: Configuration,
) -> None:
    """Corps du fil d'un calcul : ne laisse échapper aucune exception."""
    debut = time.perf_counter()
    etat = EtatCalcul.ERREUR
    message = MESSAGE_ERREUR_INATTENDUE
    statistiques: dict = {}
    chemin_journal: str | None = None
    try:
        with ouvrir_journal_calcul(calcul_id, config) as journal_calcul:
            chemin_journal = str(journal_calcul.chemin)
            try:
                journal.info(
                    "Début du calcul %d (semestre %d, options %s)",
                    calcul_id,
                    semestre_id,
                    options.vers_dict(),
                )
                with session_base(config.db_url) as session:
                    session.execute(
                        update(Calcul)
                        .where(Calcul.id == calcul_id)
                        .values(fichier_journal=chemin_journal)
                    )
                    instantane = charger_instantane(session, semestre_id, version_base_id)
                etat, message, statistiques = _calculer(
                    calcul_id, instantane, options, moteur, arret, activer, config
                )
            except ErreurEDT as erreur:
                journal.error("Calcul %d refusé ou en échec : %s", calcul_id, erreur)
                etat, message = EtatCalcul.ERREUR, str(erreur)
            except Exception:
                journal.exception("Erreur inattendue pendant le calcul %d", calcul_id)
                etat, message = EtatCalcul.ERREUR, MESSAGE_ERREUR_INATTENDUE
            journal.info("Fin du calcul %d : %s - %s", calcul_id, etat.value, message)
    except Exception:
        # Le journal dédié lui-même n'a pas pu être ouvert ou fermé : on le dit dans edt.log.
        journal.exception("Échec de la journalisation dédiée du calcul %d", calcul_id)
    finally:
        _clore(calcul_id, etat, message, statistiques, time.perf_counter() - debut, config)
        with _verrou:
            _actifs.pop(calcul_id, None)


def _calculer(
    calcul_id: int,
    instantane: Instantane,
    options: OptionsSolveur,
    moteur: Moteur,
    arret: threading.Event,
    activer: bool,
    config: Configuration,
) -> tuple[EtatCalcul, str, dict]:
    """Pré-vérifications, moteur, contrôle, enregistrement, séances datées.

    Renvoie l'état final, le message et les statistiques du calcul. Un calcul
    refusé par les pré-vérifications a toujours le même visage : état
    « impossible », `statistiques["refuse"]` vrai, message qui cite les
    premières raisons et liste complète dans `statistiques["impossibilites"]`.
    """
    # Pré-vérifications : une impossibilité certaine arrête tout de suite.
    anomalies = pre_verifier(
        instantane,
        options.contraintes_desactivees,
        tolerer_violations_heritees=options.tolerer_violations_heritees,
        niveaux=options.niveaux,
    )
    impossibilites = [anomalie.message for anomalie in anomalies if anomalie.bloquante]
    alertes = [anomalie.message for anomalie in anomalies if not anomalie.bloquante]
    for anomalie in anomalies:
        niveau = logging.ERROR if anomalie.bloquante else logging.WARNING
        journal.log(niveau, "Pré-vérification [%s] : %s", anomalie.code.value, anomalie.message)
    if impossibilites:
        reste = len(impossibilites) - NB_RAISONS_DANS_LE_MESSAGE
        suite = f" (et {reste} autre(s) raison(s))" if reste > 0 else ""
        message = (
            f"Le calcul n'a pas été lancé : {len(impossibilites)} impossibilité(s) certaine(s) "
            "relevée(s) par les pré-vérifications. "
            + " ".join(impossibilites[:NB_RAISONS_DANS_LE_MESSAGE])
            + suite
        )
        statistiques = {
            "refuse": True,
            "nb_seances": len(instantane.seances),
            "impossibilites": impossibilites,
            "avertissements": alertes,
        }
        return EtatCalcul.INFAISABLE, message, statistiques

    resultat = moteur(instantane, options, arret)
    statistiques = dict(resultat.statistiques)
    etat = _ETAT_PAR_STATUT[resultat.statut]
    message = resultat.message or _MESSAGES_PAR_DEFAUT[etat]
    for avertissement in resultat.avertissements:
        journal.warning("Avertissement du moteur : %s", avertissement)
    statistiques["avertissements"] = list(dict.fromkeys([*alertes, *resultat.avertissements]))
    if not resultat.reussi:
        return etat, message, statistiques

    _exiger_solution_complete(instantane, resultat)
    indicateurs = _controler(instantane, resultat, options)
    if indicateurs is not None:
        statistiques["indicateurs"] = indicateurs
    with session_base(config.db_url) as session:
        version = _enregistrer_version(session, calcul_id, instantane, resultat, activer)
        version_id = version.id
        journal.info(
            "Version %d (n°%d) enregistrée : %d séances",
            version.id,
            version.numero,
            len(resultat.placements),
        )
    message += " " + _instancier(version_id, statistiques, config)
    return etat, message, statistiques


def _controler(
    instantane: Instantane, resultat: ResultatSolveur, options: OptionsSolveur
) -> dict | None:
    """Contrôle la solution du moteur avec le vérificateur officiel et renvoie ses indicateurs.

    Une erreur relevée sur une solution du moteur est un défaut du moteur :
    elle est journalisée en détail et le calcul échoue (`ErreurEDT`), sans
    rien enregistrer. Avec `tolerer_violations_heritees`, les violations
    entre séances toutes verrouillées sont attendues et ne comptent pas.
    """
    # Seules les contraintes nécessaires sont dures : une entorse à l'une d'elles est un
    # défaut du moteur. Les contraintes souples relèvent du bilan, pas du contrôle.
    necessaires = frozenset(c for c, n in options.niveaux.items() if n is Niveau.NECESSAIRE)
    try:
        anomalies = verifier(instantane, resultat.placements, necessaires)
    except ErreurNonLivre:
        journal.warning(
            "Résultat non contrôlé : le vérificateur n'est pas livré (edt.verification)."
        )
        return None
    verrouillees = {seance.id for seance in instantane.seances if seance.verrou is not None}
    erreurs = [
        anomalie
        for anomalie in anomalies
        if anomalie.bloquante
        and not (
            options.tolerer_violations_heritees
            and len(anomalie.seances) >= 2
            and verrouillees.issuperset(anomalie.seances)
        )
    ]
    if erreurs:
        for anomalie in erreurs:
            journal.error(
                "Défaut du moteur, solution refusée [%s] : %s (séances %s)",
                anomalie.code.value,
                anomalie.message,
                ", ".join(anomalie.seances),
            )
        raise ErreurEDT(
            f"Le moteur a produit un emploi du temps qui viole {len(erreurs)} contrainte(s) "
            "active(s) : c'est un défaut du moteur, à signaler avec le journal du calcul. "
            "Aucun emploi du temps n'a été enregistré.",
            [anomalie.message for anomalie in erreurs[:10]],
        )
    indicateurs = calculer_indicateurs(instantane, resultat.placements, anomalies)
    journal.info(
        "Solution contrôlée par le vérificateur : aucune erreur ; objectif %d, %d créneau(x) "
        "de trou",
        indicateurs.valeur_objectif,
        indicateurs.trous_creneaux,
    )
    return indicateurs.vers_dict()


def _instancier(version_id: int, statistiques: dict, config: Configuration) -> str:
    """Produit les séances datées de la version ; renvoie la phrase à ajouter au message.

    La semaine type est déjà enregistrée : un échec ici ne l'annule pas, il
    est journalisé et dit à l'utilisateur, qui peut relancer l'instanciation.
    """
    try:
        with session_base(config.db_url) as session:
            rapport = instancier_version(session, version_id)
    except Exception:
        journal.exception("Échec de l'instanciation de la version %d", version_id)
        statistiques["instanciation"] = {"erreur": True}
        return (
            "Ses séances datées n'ont pas pu être produites (détail dans le journal du "
            "calcul) : relancer l'instanciation."
        )
    statistiques["instanciation"] = rapport.vers_dict()
    return rapport.resume()


def _exiger_solution_complete(instantane: Instantane, resultat: ResultatSolveur) -> None:
    """Refuse toute solution incomplète ou incohérente (un placement par séance, avec salle)."""
    places = [placement.seance_id for placement in resultat.placements]
    attendues = {seance.id for seance in instantane.seances}
    if len(places) != len(set(places)) or set(places) != attendues:
        manquantes = sorted(attendues - set(places))
        inconnues = sorted(set(places) - attendues)
        doublons = len(places) - len(set(places))
        details = []
        if manquantes:
            details.append(
                f"{len(manquantes)} séance(s) sans placement : {', '.join(manquantes[:10])}"
            )
        if inconnues:
            details.append(
                f"{len(inconnues)} placement(s) de séance inconnue : {', '.join(inconnues[:10])}"
            )
        if doublons:
            details.append(f"{doublons} placement(s) en double")
        raise ErreurEDT(
            "Le moteur a renvoyé une solution incomplète ou incohérente : aucun emploi du "
            "temps n'a été enregistré.",
            details,
        )
    sans_salle = sorted(p.seance_id for p in resultat.placements if p.salle_id is None)
    if sans_salle:
        raise ErreurEDT(
            "Le moteur a renvoyé des séances sans salle : aucun emploi du temps n'a été "
            "enregistré.",
            [", ".join(sans_salle[:10])],
        )
    for placement in resultat.placements:
        instantane.salle(placement.salle_id)  # lève ErreurIntrouvable si la salle est inconnue


def _enregistrer_version(
    session: Session,
    calcul_id: int,
    instantane: Instantane,
    resultat: ResultatSolveur,
    activer: bool,
) -> VersionEdt:
    """Crée la version et ses séances hebdomadaires à partir d'une solution complète."""
    _exiger_solution_complete(instantane, resultat)
    numero = 1 + (
        session.scalar(
            select(func.max(VersionEdt.numero)).where(
                VersionEdt.semestre_id == instantane.semestre_id
            )
        )
        or 0
    )
    if activer:
        session.execute(
            update(VersionEdt)
            .where(VersionEdt.semestre_id == instantane.semestre_id, VersionEdt.active.is_(True))
            .values(active=False)
        )
    maintenant = dt.datetime.now()
    version = VersionEdt(
        semestre_id=instantane.semestre_id,
        numero=numero,
        libelle=f"Version {numero} (générée le {maintenant:%d/%m/%Y à %H h %M})",
        origine=OrigineVersion.GENEREE,
        active=activer,
        cree_le=maintenant,
        calcul_id=calcul_id,
    )
    for placement in resultat.placements:
        seance = instantane.seance(placement.seance_id)
        version.seances.append(
            SeanceHebdo(
                besoin_id=seance.besoin.id,
                rang=seance.rang,
                jour=placement.jour,
                debut_min=placement.debut,
                fin_min=placement.fin,
                salle_id=placement.salle_id,
                verrou=_verrou_tenu(seance, placement),
                semaines=sorted(seance.semaines),
            )
        )
    session.add(version)
    session.flush()
    return version


def _verrou_tenu(seance: SeanceAPlacer, placement: Placement) -> bool:
    """Vrai si la séance est verrouillée et que le placement retenu est bien celui du verrou.

    Avec la contrainte « Séances verrouillées » décochée, le moteur déplace
    librement ces séances : le verrou ne les suit pas à leur nouvelle place,
    sinon les calculs suivants figeraient une place que personne n'a choisie.
    """
    verrou = seance.verrou
    return (
        verrou is not None
        and (placement.jour, placement.debut, placement.fin)
        == (verrou.jour, verrou.debut, verrou.fin)
        and (verrou.salle_id is None or placement.salle_id == verrou.salle_id)
    )


def _clore(
    calcul_id: int,
    etat: EtatCalcul,
    message: str,
    statistiques: dict,
    duree_s: float,
    config: Configuration,
) -> None:
    """Enregistre l'état final du calcul (journalise si même cela échoue)."""
    try:
        with session_base(config.db_url) as session:
            session.execute(
                update(Calcul)
                .where(Calcul.id == calcul_id)
                .values(
                    etat=etat,
                    message=message,
                    statistiques=statistiques,
                    termine_le=dt.datetime.now(),
                    duree_s=round(duree_s, 3),
                )
            )
    except Exception:
        journal.exception(
            "Impossible d'enregistrer la fin du calcul %d (état %s)", calcul_id, etat.value
        )


def _suivi(session: Session, calcul: Calcul) -> SuiviCalcul:
    etat = EtatCalcul(calcul.etat)
    if etat is EtatCalcul.EN_COURS:
        ecoule = (dt.datetime.now() - calcul.demarre_le).total_seconds()
    else:
        ecoule = calcul.duree_s or 0.0
    version_id = session.scalar(select(VersionEdt.id).where(VersionEdt.calcul_id == calcul.id))
    desactivees = tuple(calcul.contraintes_desactivees or ())
    return SuiviCalcul(
        id=calcul.id,
        semestre_id=calcul.semestre_id,
        etat=etat,
        demarre_le=calcul.demarre_le,
        termine_le=calcul.termine_le,
        temps_ecoule_s=round(max(ecoule, 0.0), 1),
        message=calcul.message,
        version_id=version_id,
        contraintes_desactivees=desactivees,
        statistiques=dict(calcul.statistiques or {}),
        fichier_journal=calcul.fichier_journal,
        arret_demande=calcul.arret_demande,
        avertissements=tuple((calcul.statistiques or {}).get("avertissements", ())),
        niveaux=_niveaux_du_calcul(calcul.id, calcul.parametres or {}, desactivees),
    )


def _niveaux_du_calcul(calcul_id: int, parametres: dict, desactivees: Iterable[str]) -> Niveaux:
    """Niveaux retenus pour un calcul enregistré.

    `parametres["niveaux"]` fait foi ; les contraintes ignorées en sont
    absentes et sont aussi listées dans `contraintes_desactivees`. Un calcul
    enregistré avant les niveaux n'a que ses contraintes désactivées : les
    niveaux par défaut sans ces codes.
    """
    try:
        return OptionsSolveur.lire_niveaux(parametres, desactivees)
    except ErreurEDT as erreur:
        journal.warning(
            "Calcul %d : niveaux enregistrés illisibles (%s), niveaux par défaut utilisés",
            calcul_id,
            erreur,
        )
        return niveaux_depuis_desactivees(normaliser_codes(desactivees))
