"""Orchestration d'un calcul (génération d'un emploi du temps), indépendante du moteur.

Ce module est ce que l'interface web et la ligne de commande appellent :

    identifiant = lancer_calcul(semestre_id, options)   # rend la main tout de suite
    suivi = etat_calcul(identifiant)                    # à interroger chaque seconde
    demander_arret(identifiant)                         # bouton « Arrêter »
    suivi = attendre_calcul(identifiant)                # CLI et tests

Déroulement d'un calcul :

1. une ligne `calcul` est créée à l'état « en cours » ; **un seul calcul à la
   fois** (`ErreurCalculEnCours` sinon) ;
2. un fil d'exécution d'arrière-plan charge l'instantané du semestre, refuse
   de continuer si des besoins ne peuvent pas être dépliés, puis appelle le
   moteur (`edt.solveur.resoudre` par défaut) ;
3. tout ce qui est journalisé pendant le calcul va aussi dans un fichier
   dédié de `<EDT_LOG_DIR>/generations/` ;
4. en cas de solution, une **nouvelle version** d'emploi du temps est
   enregistrée avec ses séances hebdomadaires (et activée) ; dans tous les
   autres cas (impossible, temps dépassé, arrêt, erreur) **aucune version
   n'est créée** et le message explique pourquoi.

Les fonctions de ce module ouvrent leurs propres sessions : un calcul vit
plus longtemps que la requête qui l'a lancé. Le seul état gardé en mémoire
est le suivi des fils en cours ; tout le reste est en base.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from edt import solveur
from edt.config import Configuration, charger_configuration
from edt.db.modeles import Calcul, SeanceHebdo, Semestre, VersionEdt
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul, OrigineVersion
from edt.domaine.instantane import Instantane
from edt.erreurs import (
    ErreurCalculEnCours,
    ErreurDonneesInvalides,
    ErreurEDT,
    ErreurInfaisable,
    ErreurIntrouvable,
    ErreurTempsDepasse,
)
from edt.journal import ouvrir_journal_calcul
from edt.services.instantane import charger_instantane
from edt.services.parametres import lire_parametres
from edt.solveur.contrat import Moteur, OptionsSolveur, ResultatSolveur, StatutSolveur

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
    - `contraintes_desactivees` : codes décochés par l'utilisateur au lancement ;
    - `fichier_journal` : chemin du journal dédié du calcul.
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


def etat_calcul(calcul_id: int) -> SuiviCalcul:
    """État courant du calcul `calcul_id` ; lève `ErreurIntrouvable` s'il n'existe pas."""
    with session_base() as session:
        calcul = session.get(Calcul, calcul_id)
        if calcul is None:
            raise ErreurIntrouvable(f"Le calcul {calcul_id} n'existe pas.")
        return _suivi(session, calcul)


def calcul_en_cours() -> SuiviCalcul | None:
    """Le calcul en cours, s'il y en a un."""
    with session_base() as session:
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
    demande, puis l'état passe à « arrêté ». Sans effet sur un calcul terminé.
    L'arrêt n'atteint que les calculs lancés par ce processus.
    """
    with session_base() as session:
        calcul = session.get(Calcul, calcul_id)
        if calcul is None:
            raise ErreurIntrouvable(f"Le calcul {calcul_id} n'existe pas.")
        if calcul.etat == EtatCalcul.EN_COURS:
            calcul.arret_demande = True
            with _verrou:
                actif = _actifs.get(calcul_id)
            if actif is not None:
                actif.arret.set()
                journal.info("Arrêt demandé pour le calcul %d", calcul_id)
            else:
                journal.warning(
                    "Arrêt demandé pour le calcul %d, qui ne tourne pas dans ce processus",
                    calcul_id,
                )
        session.flush()
        return _suivi(session, calcul)


def attendre_calcul(calcul_id: int, delai_s: float | None = None) -> SuiviCalcul:
    """Attend la fin du calcul (au plus `delai_s` secondes) et renvoie son état.

    Si le délai expire, l'état renvoyé est encore « en cours ».
    """
    with _verrou:
        actif = _actifs.get(calcul_id)
    if actif is not None:
        actif.fil.join(delai_s)
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
    """Marque en erreur les calculs restés « en cours » alors qu'aucun fil ne les exécute.

    À appeler au démarrage du serveur : un calcul interrompu par un arrêt du
    processus bloquerait sinon tous les suivants. Renvoie le nombre de
    calculs clôturés.
    """
    with _verrou:
        vivants = [calcul_id for calcul_id, actif in _actifs.items() if actif.fil.is_alive()]
    with session_base() as session:
        orphelins = [
            calcul
            for calcul in session.scalars(select(Calcul).where(Calcul.etat == EtatCalcul.EN_COURS))
            if calcul.id not in vivants
        ]
        for calcul in orphelins:
            calcul.etat = EtatCalcul.ERREUR
            calcul.message = MESSAGE_INTERROMPU
            calcul.termine_le = dt.datetime.now()
            journal.warning("Calcul %d clôturé : %s", calcul.id, MESSAGE_INTERROMPU)
        return len(orphelins)


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
                _refuser_si_non_depliable(instantane, options)

                resultat = moteur(instantane, options, arret)
                statistiques = dict(resultat.statistiques)
                etat = _ETAT_PAR_STATUT[resultat.statut]
                message = resultat.message or _MESSAGES_PAR_DEFAUT[etat]
                for avertissement in resultat.avertissements:
                    journal.warning("Avertissement du moteur : %s", avertissement)
                if resultat.reussi:
                    with session_base(config.db_url) as session:
                        version = _enregistrer_version(
                            session, calcul_id, instantane, resultat, activer
                        )
                        journal.info(
                            "Version %d (n°%d) enregistrée : %d séances",
                            version.id,
                            version.numero,
                            len(resultat.placements),
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


def _refuser_si_non_depliable(instantane: Instantane, options: OptionsSolveur) -> None:
    bloquants = [
        probleme
        for probleme in instantane.problemes_depliage
        if probleme.code not in options.contraintes_desactivees
    ]
    if bloquants:
        raise ErreurDonneesInvalides(
            "Le calcul n'a pas été lancé : certains besoins ne peuvent pas être planifiés "
            "tels qu'ils sont saisis.",
            [probleme.message for probleme in bloquants],
        )


def _enregistrer_version(
    session: Session,
    calcul_id: int,
    instantane: Instantane,
    resultat: ResultatSolveur,
    activer: bool,
) -> VersionEdt:
    """Crée la version et ses séances hebdomadaires ; refuse toute solution incomplète."""
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
                verrou=seance.verrou is not None,
                semaines=sorted(seance.semaines),
            )
        )
    session.add(version)
    session.flush()
    return version


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
    return SuiviCalcul(
        id=calcul.id,
        semestre_id=calcul.semestre_id,
        etat=etat,
        demarre_le=calcul.demarre_le,
        termine_le=calcul.termine_le,
        temps_ecoule_s=round(max(ecoule, 0.0), 1),
        message=calcul.message,
        version_id=version_id,
        contraintes_desactivees=tuple(calcul.contraintes_desactivees or ()),
        statistiques=dict(calcul.statistiques or {}),
        fichier_journal=calcul.fichier_journal,
        arret_demande=calcul.arret_demande,
    )
