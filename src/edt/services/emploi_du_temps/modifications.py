"""Corrections manuelles d'un emploi du temps : placement, verrou, séances datées.

Règles :

- **toute modification de placement est vérifiée avant d'être enregistrée** :
  une anomalie de gravité « erreur » la refuse (rien n'est écrit) ; les
  avertissements et informations sont rendus avec le résultat. Si le
  vérificateur n'est pas encore disponible, la modification est refusée
  avec un message clair (une séance n'est jamais déplacée sans contrôle) ;
- la semaine type se vérifie par `raccords.verifier_modification` (rien
  n'est écrit pendant la vérification) ; une occurrence datée est déplacée
  dans la transaction, vérifiée par `raccords.verifier_occurrences_version`
  sur son jour, puis remise en l'état si le déplacement est refusé ;
- après un déplacement dans la semaine type, si la version a déjà des
  séances datées, elles sont recalculées (`recalculer_seances_datees`) ;
- poser ou lever un verrou, annuler une occurrence ne demandent pas de
  vérification (cela ne crée aucun conflit).

Retouches des séances datées
----------------------------
Déplacer ou annuler une séance datée en fait une **retouche** : son origine
devient « saisie manuelle ». Recalculer les séances datées
(`instancier_version`) remplace les séances issues de la semaine type et des
replacements, mais **conserve** les saisies manuelles et les évite. Comme
l'instanciation produit de nouveau la séance de la semaine type pour la
semaine retouchée, `recalculer_seances_datees` retire ensuite ce doublon :
une retouche reste la seule séance de sa semaine pour sa séance
hebdomadaire. Une annulation reste donc une annulation, un déplacement reste
à sa nouvelle place.

Une version dont les séances datées ont été **importées** (origine
« import », par exemple une version de référence) ne se recalcule pas : la
semaine type réappliquée s'ajouterait aux séances importées et doublerait
tout le semestre. Le recalcul est refusé avant l'instanciation
(`ErreurRegleMetier`), et un déplacement dans la semaine type d'une telle
version est enregistré en le disant.

Contraintes décochées
---------------------
Une retouche applique les mêmes règles que le calcul qui a produit la
version (`raccords.contraintes_actives_de`, passées au vérificateur), comme
l'instanciation des séances datées : une contrainte décochée lors de ce
calcul ne bloque pas la retouche. Le vérificateur ne renvoie que les
anomalies que le déplacement introduit ou aggrave : une violation déjà
présente ne fige pas la séance.

Les salles proposées sont les salles **compatibles** avec le besoin
(capacité, types acceptés, équipements, accessibilité, salle ou campus
imposés : mêmes règles que l'instantané), libres d'abord, les occupées
ensuite avec le nom de la séance qui les occupe.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from edt.db import modeles as m
from edt.domaine.calendrier import lundi_de
from edt.domaine.enumerations import (
    JOURS,
    SAMEDI,
    Accessibilite,
    Gravite,
    OrigineOccurrence,
    StatutOccurrence,
)
from edt.domaine.heures import minutes_vers_texte, se_chevauchent
from edt.domaine.instantane import id_seance
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.conflits_groupes import conflits_groupes
from edt.services.emploi_du_temps import raccords
from edt.services.emploi_du_temps.vues import Bloc, bloc_occurrence, bloc_seance
from edt.services.parametres import lire_parametres
from edt.verification.contrat import Anomalie

journal = logging.getLogger(__name__)

MESSAGE_SEANCES_IMPORTEES = (
    "Les séances datées de cette version ont été importées : les recalculer depuis la semaine "
    "type les doublerait. Elles restent telles quelles ; pour les changer, déplacer ou annuler "
    "les séances datées une par une."
)

REFUS_SANS_VERIFICATION = (
    "La modification n'a pas été enregistrée : une séance n'est déplacée qu'après vérification, "
    "et {raison}"
)


@dataclass(frozen=True)
class ChoixSalle:
    """Une salle proposée dans le formulaire de modification."""

    id: int
    code: str
    campus: str
    capacite: int
    libre: bool
    occupee_par: str | None
    actuelle: bool

    @property
    def libelle(self) -> str:
        """« N204 (NDC, 40 places) », suivi de l'occupation éventuelle."""
        texte = f"{self.code} ({self.campus}, {self.capacite} places)"
        if self.actuelle:
            texte += " - salle actuelle"
        if not self.libre:
            texte += f" - occupée : {self.occupee_par}"
        return texte


@dataclass(frozen=True)
class FormulaireSeance:
    """Valeurs et choix du formulaire de modification d'une séance hebdomadaire."""

    bloc: Bloc
    version_id: int
    jours: tuple[tuple[int, str], ...]
    debuts: tuple[tuple[int, str], ...]
    salles: tuple[ChoixSalle, ...]
    jour: int
    debut: int
    fin: int
    salle_id: int | None
    exigences: str


@dataclass(frozen=True)
class FormulaireOccurrence:
    """Valeurs et choix du formulaire de déplacement d'une séance datée."""

    bloc: Bloc
    version_id: int
    date: dt.date
    date_min: dt.date
    date_max: dt.date
    debuts: tuple[tuple[int, str], ...]
    salles: tuple[ChoixSalle, ...]
    debut: int
    fin: int
    salle_id: int | None
    exigences: str
    fermeture: str | None
    modifiable: bool


@dataclass(frozen=True)
class ResultatModification:
    """Issue d'une modification : enregistrée ou refusée, avec ses raisons.

    `anomalies` : les erreurs qui ont refusé la modification, ou les
    avertissements et informations d'une modification enregistrée.
    `remarques` : phrases complémentaires (séances datées recalculées ou non).
    """

    enregistree: bool
    message: str
    niveau: str
    anomalies: tuple[Anomalie, ...] = ()
    remarques: tuple[str, ...] = ()


@dataclass(frozen=True)
class BilanRecalcul:
    """Séances datées d'une version après un recalcul."""

    nb_occurrences: int
    nb_replacees: int
    nb_a_programmer: int
    nb_retouches: int
    nb_doublons_retires: int

    @property
    def message(self) -> str:
        """Phrase pour l'utilisateur."""
        texte = (
            f"Séances datées recalculées : {self.nb_occurrences} au total, dont "
            f"{self.nb_replacees} replacée(s) après un jour fermé ou une indisponibilité et "
            f"{self.nb_a_programmer} à programmer."
        )
        if self.nb_retouches:
            texte += (
                f" Vos {self.nb_retouches} retouche(s) à la main (déplacements, annulations) "
                "sont conservées."
            )
        if self.nb_a_programmer:
            texte += " Les séances à programmer sont listées dans l'onglet « À traiter »."
        return texte


@dataclass(frozen=True)
class AnomaliesSeance:
    """Anomalies actuelles d'une séance, ou la raison pour laquelle on ne les connaît pas."""

    controle: raccords.Controle
    anomalies: tuple[Anomalie, ...]


def anomalies_seance(session: Session, seance_hebdo_id: int) -> AnomaliesSeance:
    """Anomalies de la semaine type qui concernent cette séance (pour le panneau de détail)."""
    seance = _seance(session, seance_hebdo_id)
    controle = raccords.controler_version(session, seance.version_id)
    if controle.verification is None:
        return AnomaliesSeance(controle, ())
    identifiant = id_seance(seance.besoin_id, seance.rang)
    return AnomaliesSeance(
        controle,
        _triees(a for a in controle.verification.anomalies if identifiant in a.seances),
    )


def anomalies_occurrence(session: Session, occurrence_id: int) -> AnomaliesSeance:
    """Anomalies datées qui concernent cette occurrence, vérifiées sur son jour."""
    occurrence = _occurrence(session, occurrence_id)
    controle = raccords.controler_occurrences(
        session, occurrence.version_id, date_debut=occurrence.date, date_fin=occurrence.date
    )
    if controle.verification is None:
        return AnomaliesSeance(controle, ())
    return AnomaliesSeance(
        controle,
        _triees(a for a in controle.verification.anomalies if occurrence.id in a.occurrences),
    )


# --------------------------------------------------------------------------- #
# Séances hebdomadaires
# --------------------------------------------------------------------------- #


def formulaire_seance(
    session: Session,
    seance_hebdo_id: int,
    *,
    jour: int | None = None,
    debut: int | None = None,
    salle_id: int | None = None,
) -> FormulaireSeance:
    """Choix proposés pour déplacer une séance (jour, début, salle), valeurs actuelles par défaut."""
    seance = _seance(session, seance_hebdo_id)
    besoin = seance.besoin
    duree = seance.fin_min - seance.debut_min
    jours = _jours_possibles(session, besoin, seance.jour)
    jour = seance.jour if jour is None else jour
    if jour not in dict(jours):
        jour = seance.jour
    debuts = _debuts_possibles(
        session, besoin, jour, duree, seance.debut_min if jour == seance.jour else None
    )
    if debut is None or debut not in dict(debuts):
        debut = seance.debut_min if jour == seance.jour else debuts[0][0]
    fin = debut + duree
    exigences = _Exigences.du_besoin(session, besoin)
    salles = _salles_hebdo(session, seance, exigences, jour, debut, fin)
    if salle_id is None or salle_id not in {s.id for s in salles}:
        salle_id = _salle_par_defaut(salles, seance.salle_id)
    return FormulaireSeance(
        bloc=bloc_seance(session, seance_hebdo_id),
        version_id=seance.version_id,
        jours=jours,
        debuts=debuts,
        salles=salles,
        jour=jour,
        debut=debut,
        fin=fin,
        salle_id=salle_id,
        exigences=exigences.texte(session),
    )


def modifier_placement(
    session: Session,
    seance_hebdo_id: int,
    *,
    jour: int,
    debut: int,
    salle_id: int | None,
    verrouiller: bool | None = None,
) -> ResultatModification:
    """Déplace une séance de la semaine type après vérification (voir l'en-tête du module).

    `verrouiller` : poser (`True`) ou lever (`False`) le verrou en même
    temps ; `None` le laisse tel quel.
    """
    seance = _seance(session, seance_hebdo_id)
    besoin = seance.besoin
    duree = seance.fin_min - seance.debut_min
    if not 0 <= jour <= 6:
        raise ErreurDonneesInvalides(f"Jour invalide : {jour} (attendu : 0 pour lundi à 6).")
    if jour not in dict(_jours_possibles(session, besoin, seance.jour)):
        raise ErreurDonneesInvalides(f"Cette séance ne peut pas être proposée le {JOURS[jour]}.")
    debuts = dict(
        _debuts_possibles(
            session, besoin, jour, duree, seance.debut_min if jour == seance.jour else None
        )
    )
    if debut not in debuts:
        raise ErreurDonneesInvalides(
            f"L'heure de début {minutes_vers_texte(debut) if 0 <= debut <= 1440 else debut} "
            f"n'est pas un début de créneau de la grille le {JOURS[jour]}."
        )
    if salle_id is not None and session.get(m.Salle, salle_id) is None:
        raise ErreurIntrouvable(f"La salle n° {salle_id} n'existe pas.")
    fin = debut + duree

    deplacement = (jour, debut, salle_id) != (seance.jour, seance.debut_min, seance.salle_id)
    changement_verrou = verrouiller is not None and verrouiller != seance.verrou
    if not deplacement and not changement_verrou:
        return ResultatModification(
            enregistree=False,
            message="La séance est déjà à cette place : rien n'a été modifié.",
            niveau="info",
        )
    if not deplacement:
        return ResultatModification(
            enregistree=True,
            message=definir_verrou(session, seance_hebdo_id, bool(verrouiller)),
            niveau="succes",
        )

    try:
        verification = raccords.verifier_modification(
            session,
            seance.version_id,
            id_seance(seance.besoin_id, seance.rang),
            jour,
            debut,
            fin,
            salle_id,
            contraintes_actives=raccords.contraintes_actives_de(session, seance.version_id),
        )
    except raccords.ErreurRaccordIndisponible as erreur:
        return ResultatModification(
            enregistree=False,
            message=REFUS_SANS_VERIFICATION.format(raison=_minuscule(erreur.message)),
            niveau="attention",
        )
    retenues = tuple(verification.anomalies)
    erreurs = [a for a in retenues if a.gravite is Gravite.ERREUR]
    if erreurs:
        journal.info(
            "Déplacement de la séance %d refusé par le vérificateur : %d erreur(s)",
            seance.id,
            len(erreurs),
        )
        return ResultatModification(
            enregistree=False,
            message="La modification n'a pas été enregistrée : elle ne respecte pas les règles suivantes.",
            niveau="erreur",
            anomalies=_triees(retenues),
        )

    ancien = (seance.jour, seance.debut_min, seance.salle_id)
    seance.jour, seance.debut_min, seance.fin_min, seance.salle_id = jour, debut, fin, salle_id
    if verrouiller is not None:
        seance.verrou = verrouiller
    session.flush()
    journal.info(
        "Séance %d déplacée de %s à %s (version %d)",
        seance.id,
        ancien,
        (jour, debut, salle_id),
        seance.version_id,
    )
    remarques = _recalculer_seances_datees(session, seance.version_id)
    salle = session.get(m.Salle, salle_id) if salle_id is not None else None
    message = f"Séance déplacée au {JOURS[jour]} à {minutes_vers_texte(debut)}" + (
        f", salle {salle.code}." if salle else ", sans salle."
    )
    if verrouiller:
        message += " Elle est verrouillée à cette place."
    return ResultatModification(
        enregistree=True,
        message=message,
        niveau="attention" if remarques or retenues else "succes",
        anomalies=_triees(retenues),
        remarques=tuple(remarques),
    )


def definir_verrou(session: Session, seance_hebdo_id: int, verrou: bool) -> str:
    """Pose ou lève le verrou d'une séance ; renvoie la phrase à afficher."""
    seance = session.get(m.SeanceHebdo, seance_hebdo_id)
    if seance is None:
        raise ErreurIntrouvable(f"La séance n° {seance_hebdo_id} n'existe pas (ou plus).")
    if seance.verrou == verrou:
        return (
            "La séance était déjà verrouillée." if verrou else "La séance n'était pas verrouillée."
        )
    seance.verrou = verrou
    session.flush()
    journal.info("Séance %d %s", seance.id, "verrouillée" if verrou else "déverrouillée")
    if verrou:
        return "Séance verrouillée : les prochains calculs la laisseront à sa place."
    return "Séance déverrouillée : les prochains calculs pourront la déplacer."


# --------------------------------------------------------------------------- #
# Séances datées
# --------------------------------------------------------------------------- #


def formulaire_occurrence(
    session: Session,
    occurrence_id: int,
    *,
    date: dt.date | None = None,
    debut: int | None = None,
    salle_id: int | None = None,
) -> FormulaireOccurrence:
    """Choix proposés pour déplacer une séance datée (date, début, salle)."""
    occurrence = _occurrence(session, occurrence_id)
    besoin = occurrence.besoin
    semestre = besoin.semestre
    duree = occurrence.fin_min - occurrence.debut_min
    date = occurrence.date if date is None else date
    if not semestre.date_debut <= date <= semestre.date_fin:
        date = occurrence.date
    garde = occurrence.debut_min if date.weekday() == occurrence.date.weekday() else None
    debuts = _debuts_possibles(session, besoin, date.weekday(), duree, garde)
    if not debuts:
        debuts = ((occurrence.debut_min, minutes_vers_texte(occurrence.debut_min)),)
    if debut is None or debut not in dict(debuts):
        debut = garde if garde is not None else debuts[0][0]
    fin = debut + duree
    exigences = _Exigences.du_besoin(session, besoin)
    salles = _salles_datees(session, occurrence, exigences, date, debut, fin)
    if salle_id is None or salle_id not in {s.id for s in salles}:
        salle_id = _salle_par_defaut(salles, occurrence.salle_id)
    return FormulaireOccurrence(
        bloc=bloc_occurrence(session, occurrence_id),
        version_id=occurrence.version_id,
        date=date,
        date_min=semestre.date_debut,
        date_max=semestre.date_fin,
        debuts=debuts,
        salles=salles,
        debut=debut,
        fin=fin,
        salle_id=salle_id,
        exigences=exigences.texte(session),
        fermeture=_fermeture_globale(session, semestre.annee_scolaire_id, date),
        modifiable=occurrence.statut != StatutOccurrence.ANNULEE,
    )


def deplacer_occurrence(
    session: Session,
    occurrence_id: int,
    *,
    date: dt.date,
    debut: int,
    salle_id: int | None,
) -> ResultatModification:
    """Déplace une séance datée (ou programme une séance « à programmer ») après vérification."""
    occurrence = _occurrence(session, occurrence_id)
    if occurrence.statut == StatutOccurrence.ANNULEE:
        raise ErreurRegleMetier("Une séance annulée ne peut pas être déplacée.")
    semestre = occurrence.besoin.semestre
    if not semestre.date_debut <= date <= semestre.date_fin:
        raise ErreurDonneesInvalides(
            f"La date du {date:%d/%m/%Y} est en dehors du semestre "
            f"({semestre.date_debut:%d/%m/%Y} au {semestre.date_fin:%d/%m/%Y})."
        )
    duree = occurrence.fin_min - occurrence.debut_min
    garde = occurrence.debut_min if date.weekday() == occurrence.date.weekday() else None
    if debut not in dict(
        _debuts_possibles(session, occurrence.besoin, date.weekday(), duree, garde)
    ):
        raise ErreurDonneesInvalides(
            f"L'heure de début choisie n'est pas un début de créneau de la grille le "
            f"{JOURS[date.weekday()]}."
        )
    if salle_id is not None and session.get(m.Salle, salle_id) is None:
        raise ErreurIntrouvable(f"La salle n° {salle_id} n'existe pas.")
    if (date, debut, salle_id) == (occurrence.date, occurrence.debut_min, occurrence.salle_id) and (
        occurrence.statut != StatutOccurrence.A_PROGRAMMER
    ):
        return ResultatModification(
            enregistree=False,
            message="La séance est déjà à cette place : rien n'a été modifié.",
            niveau="info",
        )

    ancien = (
        occurrence.date,
        occurrence.debut_min,
        occurrence.fin_min,
        occurrence.salle_id,
        occurrence.statut,
        occurrence.date_initiale,
        occurrence.origine,
    )
    occurrence.date_initiale = occurrence.date_initiale or occurrence.date
    occurrence.date, occurrence.debut_min, occurrence.fin_min = date, debut, debut + duree
    occurrence.salle_id = salle_id
    occurrence.statut = StatutOccurrence.DEPLACEE
    occurrence.origine = OrigineOccurrence.MANUELLE  # retouche : conservée au recalcul
    session.flush()

    def remettre() -> None:
        (
            occurrence.date,
            occurrence.debut_min,
            occurrence.fin_min,
            occurrence.salle_id,
            occurrence.statut,
            occurrence.date_initiale,
            occurrence.origine,
        ) = ancien
        session.flush()

    try:
        verification = raccords.verifier_occurrences_version(
            session,
            occurrence.version_id,
            date_debut=date,
            date_fin=date,
            contraintes_actives=raccords.contraintes_actives_de(session, occurrence.version_id),
        )
    except raccords.ErreurRaccordIndisponible as erreur:
        remettre()
        return ResultatModification(
            enregistree=False,
            message=REFUS_SANS_VERIFICATION.format(raison=_minuscule(erreur.message)),
            niveau="attention",
        )
    except Exception:
        remettre()
        raise
    seance_id = (
        id_seance(occurrence.besoin_id, occurrence.seance_hebdo.rang)
        if occurrence.seance_hebdo is not None
        else None
    )
    concernees = tuple(
        a
        for a in verification.anomalies
        if occurrence.id in a.occurrences
        or (not a.occurrences and seance_id in a.seances and a.date in (None, date))
    )
    if any(a.gravite is Gravite.ERREUR for a in concernees):
        remettre()
        journal.info("Déplacement de l'occurrence %d refusé par le vérificateur", occurrence.id)
        return ResultatModification(
            enregistree=False,
            message="Le déplacement n'a pas été enregistré : il ne respecte pas les règles suivantes.",
            niveau="erreur",
            anomalies=_triees(concernees),
        )
    journal.info("Occurrence %d déplacée de %s au %s %s", occurrence.id, ancien[0], date, debut)
    salle = session.get(m.Salle, salle_id) if salle_id is not None else None
    return ResultatModification(
        enregistree=True,
        message=(
            f"Séance déplacée au {JOURS[date.weekday()]} {date:%d/%m/%Y} à "
            f"{minutes_vers_texte(debut)}"
            + (f", salle {salle.code}." if salle else ", sans salle.")
        ),
        niveau="attention" if concernees else "succes",
        anomalies=_triees(concernees),
    )


def annuler_occurrence(session: Session, occurrence_id: int, motif: str | None = None) -> str:
    """Annule une séance datée (elle n'a pas lieu et ne compte plus dans le volume)."""
    occurrence = session.get(m.Occurrence, occurrence_id)
    if occurrence is None:
        raise ErreurIntrouvable(f"La séance datée n° {occurrence_id} n'existe pas (ou plus).")
    if occurrence.statut == StatutOccurrence.ANNULEE:
        raise ErreurRegleMetier("Cette séance est déjà annulée.")
    occurrence.statut = StatutOccurrence.ANNULEE
    occurrence.origine = OrigineOccurrence.MANUELLE  # retouche : conservée au recalcul
    motif = (motif or "").strip()
    if motif:
        note = f"Annulée : {motif}"
        occurrence.commentaire = (
            f"{occurrence.commentaire}\n{note}" if occurrence.commentaire else note
        )
    session.flush()
    journal.info("Occurrence %d annulée (%s)", occurrence.id, motif or "sans motif")
    return f"Séance du {occurrence.date:%d/%m/%Y} annulée."


def recalculer_seances_datees(session: Session, version_id: int) -> BilanRecalcul:
    """Réapplique la semaine type de la version au calendrier, retouches conservées.

    Les séances datées issues de la semaine type ou d'un replacement sont
    recalculées (jours fermés et indisponibilités replacés, sinon « à
    programmer ») ; les retouches à la main restent telles quelles (voir
    l'en-tête du module). Lève `ErreurRegleMetier` pour une version dont les
    séances datées ont été importées (avant toute écriture), et
    `raccords.ErreurRaccordIndisponible` si l'instanciation n'est pas livrée.
    """
    if session.get(m.VersionEdt, version_id) is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps n° {version_id} n'existe pas.")
    if seances_datees_importees(session, version_id):
        raise ErreurRegleMetier(MESSAGE_SEANCES_IMPORTEES)
    raccords.instancier_version(session, version_id)
    session.flush()
    retouches, doublons = _retirer_doublons_des_retouches(session, version_id)
    comptes = dict(
        session.execute(
            select(m.Occurrence.statut, func.count())
            .where(m.Occurrence.version_id == version_id)
            .group_by(m.Occurrence.statut)
        ).all()
    )
    nb_replacees = (
        session.scalar(
            select(func.count()).where(
                m.Occurrence.version_id == version_id,
                m.Occurrence.origine == OrigineOccurrence.REPLACEMENT,
                m.Occurrence.statut == StatutOccurrence.DEPLACEE,
            )
        )
        or 0
    )
    bilan = BilanRecalcul(
        nb_occurrences=sum(comptes.values()),
        nb_replacees=nb_replacees,
        nb_a_programmer=comptes.get(StatutOccurrence.A_PROGRAMMER, 0),
        nb_retouches=retouches,
        nb_doublons_retires=doublons,
    )
    journal.info(
        "Séances datées de la version %d recalculées : %d séance(s), %d retouche(s) "
        "conservée(s), %d doublon(s) retiré(s)",
        version_id,
        bilan.nb_occurrences,
        retouches,
        doublons,
    )
    return bilan


def _retirer_doublons_des_retouches(session: Session, version_id: int) -> tuple[int, int]:
    """Retire la séance recalculée d'une semaine déjà couverte par une retouche à la main.

    Une séance hebdomadaire a au plus une séance datée par semaine : la
    semaine d'une séance datée est celle de sa date d'origine
    (`date_initiale`, sinon sa date). Renvoie (retouches, doublons retirés).
    """
    lignes = session.execute(
        select(
            m.Occurrence.id,
            m.Occurrence.seance_hebdo_id,
            m.Occurrence.date,
            m.Occurrence.date_initiale,
            m.Occurrence.origine,
        ).where(m.Occurrence.version_id == version_id, m.Occurrence.seance_hebdo_id.is_not(None))
    ).all()
    couvertes = {
        (seance, lundi_de(initiale or date))
        for _, seance, date, initiale, origine in lignes
        if origine == OrigineOccurrence.MANUELLE
    }
    if not couvertes:
        return 0, 0
    doublons = [
        identifiant
        for identifiant, seance, date, initiale, origine in lignes
        if origine != OrigineOccurrence.MANUELLE
        and origine != OrigineOccurrence.IMPORT
        and (seance, lundi_de(initiale or date)) in couvertes
    ]
    for debut in range(0, len(doublons), 500):
        session.execute(
            delete(m.Occurrence).where(m.Occurrence.id.in_(doublons[debut : debut + 500]))
        )
    session.flush()
    return len(couvertes), len(doublons)


# --------------------------------------------------------------------------- #
# Exigences de salle (mêmes règles que edt.services.instantane)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Exigences:
    effectif: int
    types: frozenset[int]
    equipements: frozenset[int]
    accessibilite: bool
    inconnue_acceptee: bool
    salle_imposee_id: int | None
    campus_impose_id: int | None

    @classmethod
    def du_besoin(cls, session: Session, besoin: m.Besoin) -> _Exigences:
        """Exigences résolues : effectif total, types (sinon type par défaut), accessibilité."""
        types = frozenset(t.id for t in besoin.types_salle)
        if not types and besoin.type_seance.type_salle_defaut_id is not None:
            types = frozenset({besoin.type_seance.type_salle_defaut_id})
        groupes = {g.id for g in besoin.groupes}
        accessibilite = any(g.accessibilite_requise for g in besoin.groupes)
        if not accessibilite and groupes:
            annee_id = besoin.groupes[0].annee_scolaire_id
            signales = set(
                session.scalars(
                    select(m.Groupe.id).where(
                        m.Groupe.annee_scolaire_id == annee_id,
                        m.Groupe.accessibilite_requise.is_(True),
                    )
                )
            )
            if signales:
                accessibilite = any(
                    (a in groupes and b in signales) or (b in groupes and a in signales)
                    for a, b in conflits_groupes(session, annee_id)
                )
        return cls(
            effectif=sum(g.effectif for g in besoin.groupes),
            types=types,
            equipements=frozenset(e.id for e in besoin.equipements),
            accessibilite=accessibilite,
            inconnue_acceptee=lire_parametres(session).accessibilite_inconnue_acceptee,
            salle_imposee_id=besoin.salle_imposee_id,
            campus_impose_id=besoin.campus_impose_id,
        )

    def accepte(self, salle: m.Salle) -> bool:
        """Vrai si la salle satisfait toutes les exigences du besoin."""
        if self.salle_imposee_id is not None and salle.id != self.salle_imposee_id:
            return False
        if self.campus_impose_id is not None and salle.campus_id != self.campus_impose_id:
            return False
        if salle.capacite < self.effectif:
            return False
        if self.types and salle.type_salle_id not in self.types:
            return False
        if not self.equipements <= {e.id for e in salle.equipements}:
            return False
        if self.accessibilite:
            accessible = salle.accessibilite == Accessibilite.OUI or (
                self.inconnue_acceptee and salle.accessibilite == Accessibilite.INCONNU
            )
            if not accessible:
                return False
        return True

    def texte(self, session: Session) -> str:
        """Résumé lisible : « 28 places ; salle informatique ; postes informatiques ; accessible »."""
        morceaux = [f"{self.effectif} places au moins"]
        if self.types:
            libelles = session.scalars(
                select(m.TypeSalle.libelle).where(m.TypeSalle.id.in_(self.types))
            ).all()
            morceaux.append("type : " + ", ".join(sorted(libelles)).lower())
        if self.equipements:
            libelles = session.scalars(
                select(m.Equipement.libelle).where(m.Equipement.id.in_(self.equipements))
            ).all()
            morceaux.append("équipements : " + ", ".join(sorted(libelles)).lower())
        if self.accessibilite:
            morceaux.append("salle accessible")
        if self.salle_imposee_id is not None:
            morceaux.append("salle imposée")
        if self.campus_impose_id is not None:
            morceaux.append("campus imposé")
        return " ; ".join(morceaux)


# --------------------------------------------------------------------------- #
# Interne
# --------------------------------------------------------------------------- #


def _options_besoin():
    return (
        selectinload(m.Besoin.groupes),
        selectinload(m.Besoin.types_salle),
        selectinload(m.Besoin.equipements),
        joinedload(m.Besoin.type_seance),
        joinedload(m.Besoin.matiere),
        joinedload(m.Besoin.semestre),
    )


def _seance(session: Session, seance_hebdo_id: int) -> m.SeanceHebdo:
    seance = session.scalar(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.id == seance_hebdo_id)
        .options(selectinload(m.SeanceHebdo.besoin).options(*_options_besoin()))
    )
    if seance is None:
        raise ErreurIntrouvable(f"La séance n° {seance_hebdo_id} n'existe pas (ou plus).")
    return seance


def _occurrence(session: Session, occurrence_id: int) -> m.Occurrence:
    occurrence = session.scalar(
        select(m.Occurrence)
        .where(m.Occurrence.id == occurrence_id)
        .options(
            selectinload(m.Occurrence.besoin).options(*_options_besoin()),
            selectinload(m.Occurrence.seance_hebdo),
        )
    )
    if occurrence is None:
        raise ErreurIntrouvable(f"La séance datée n° {occurrence_id} n'existe pas (ou plus).")
    return occurrence


def _jours_possibles(
    session: Session, besoin: m.Besoin, actuel: int
) -> tuple[tuple[int, str], ...]:
    """Jours de la grille ; le samedi seulement si le type de séance l'autorise (ou s'il est actuel)."""
    jours = set(session.scalars(select(m.Creneau.jour).distinct()))
    if not besoin.type_seance.samedi_autorise and actuel != SAMEDI:
        jours.discard(SAMEDI)
    jours.add(actuel)
    return tuple((jour, JOURS[jour].capitalize()) for jour in sorted(jours))


def _debuts_possibles(
    session: Session, besoin: m.Besoin, jour: int, duree: int, actuel: int | None
) -> tuple[tuple[int, str], ...]:
    """Débuts de créneau de la grille ce jour-là où la séance tient avant la fermeture.

    S'y ajoutent l'heure actuelle (`actuel`, quand on garde le même jour) et
    le début imposé du besoin : une séance imposée hors grille (un DST à 8 h)
    reste modifiable sans perdre son heure.
    """
    fermeture = lire_parametres(session).fermeture_min
    debuts = {
        debut
        for debut in session.scalars(select(m.Creneau.debut_min).where(m.Creneau.jour == jour))
        if debut + duree <= fermeture
    }
    if actuel is not None:
        debuts.add(actuel)
    if besoin.debut_impose_min is not None and besoin.jour_impose in (None, jour):
        debuts.add(besoin.debut_impose_min)
    return tuple(
        (debut, minutes_vers_texte(debut)) for debut in sorted(debuts) if debut + duree <= 1440
    )


def _toutes_les_salles(session: Session) -> list[m.Salle]:
    return list(
        session.scalars(
            select(m.Salle)
            .options(selectinload(m.Salle.equipements), joinedload(m.Salle.campus))
            .order_by(m.Salle.code, m.Salle.id)
        )
    )


def _libelle_occupant(besoin: m.Besoin) -> str:
    groupes = ", ".join(sorted(g.code for g in besoin.groupes))
    return f"{besoin.matiere.libelle} ({groupes})" if groupes else besoin.matiere.libelle


def _salles_hebdo(
    session: Session,
    seance: m.SeanceHebdo,
    exigences: _Exigences,
    jour: int,
    debut: int,
    fin: int,
) -> tuple[ChoixSalle, ...]:
    semaines = set(seance.semaines or ())
    occupees: dict[int, str] = {}
    autres = session.scalars(
        select(m.SeanceHebdo)
        .where(
            m.SeanceHebdo.version_id == seance.version_id,
            m.SeanceHebdo.jour == jour,
            m.SeanceHebdo.salle_id.is_not(None),
            m.SeanceHebdo.id != seance.id,
            m.SeanceHebdo.debut_min < fin,
            m.SeanceHebdo.fin_min > debut,
        )
        .options(
            selectinload(m.SeanceHebdo.besoin).options(
                selectinload(m.Besoin.groupes), joinedload(m.Besoin.matiere)
            )
        )
    )
    for autre in autres:
        if semaines.isdisjoint(autre.semaines or ()):
            continue
        occupees.setdefault(autre.salle_id, _libelle_occupant(autre.besoin))
    return _choix_salles(session, exigences, occupees, seance.salle_id)


def _salles_datees(
    session: Session,
    occurrence: m.Occurrence,
    exigences: _Exigences,
    date: dt.date,
    debut: int,
    fin: int,
) -> tuple[ChoixSalle, ...]:
    occupees: dict[int, str] = {}
    autres = session.scalars(
        select(m.Occurrence)
        .where(
            m.Occurrence.version_id == occurrence.version_id,
            m.Occurrence.date == date,
            m.Occurrence.salle_id.is_not(None),
            m.Occurrence.id != occurrence.id,
            m.Occurrence.statut.in_([StatutOccurrence.PLANIFIEE, StatutOccurrence.DEPLACEE]),
        )
        .options(
            selectinload(m.Occurrence.besoin).options(
                selectinload(m.Besoin.groupes), joinedload(m.Besoin.matiere)
            )
        )
    )
    for autre in autres:
        if se_chevauchent(debut, fin, autre.debut_min, autre.fin_min):
            occupees.setdefault(autre.salle_id, _libelle_occupant(autre.besoin))
    return _choix_salles(session, exigences, occupees, occurrence.salle_id)


def _choix_salles(
    session: Session, exigences: _Exigences, occupees: dict[int, str], actuelle: int | None
) -> tuple[ChoixSalle, ...]:
    """Salles compatibles (plus la salle actuelle), libres d'abord puis par code."""
    choix = [
        ChoixSalle(
            id=salle.id,
            code=salle.code,
            campus=salle.campus.code,
            capacite=salle.capacite,
            libre=salle.id not in occupees,
            occupee_par=occupees.get(salle.id),
            actuelle=salle.id == actuelle,
        )
        for salle in _toutes_les_salles(session)
        if exigences.accepte(salle) or salle.id == actuelle
    ]
    choix.sort(key=lambda c: (not c.libre, c.code, c.id))
    return tuple(choix)


def _salle_par_defaut(salles: tuple[ChoixSalle, ...], actuelle: int | None) -> int | None:
    for salle in salles:
        if salle.id == actuelle and salle.libre:
            return salle.id
    libres = [salle for salle in salles if salle.libre]
    if libres:
        return libres[0].id
    return actuelle


def _fermeture_globale(session: Session, annee_id: int, date: dt.date) -> str | None:
    libelles = session.scalars(
        select(m.JourFerme.libelle).where(
            m.JourFerme.annee_scolaire_id == annee_id,
            m.JourFerme.groupe_id.is_(None),
            m.JourFerme.date_debut <= date,
            m.JourFerme.date_fin >= date,
        )
    ).all()
    return ", ".join(libelles) or None


def _triees(anomalies) -> tuple[Anomalie, ...]:
    ordre = {Gravite.ERREUR: 0, Gravite.AVERTISSEMENT: 1, Gravite.INFORMATION: 2}
    return tuple(sorted(anomalies, key=lambda a: (ordre[a.gravite], str(a.code), a.message)))


def _minuscule(phrase: str) -> str:
    return phrase[:1].lower() + phrase[1:] if phrase else phrase


def _recalculer_seances_datees(session: Session, version_id: int) -> list[str]:
    """Recalcule les séances datées d'une version qui en a déjà ; renvoie les remarques."""
    if (
        session.scalar(
            select(m.Occurrence.id).where(m.Occurrence.version_id == version_id).limit(1)
        )
        is None
    ):
        return []
    try:
        bilan = recalculer_seances_datees(session, version_id)
    except (raccords.ErreurRaccordIndisponible, ErreurRegleMetier) as erreur:
        # Une version importée, ou l'instanciation qui refuse : la semaine type est
        # enregistrée, ses séances datées restent telles quelles, et on le dit.
        return [
            "Les séances datées de cette version n'ont pas été recalculées : "
            + _minuscule(erreur.message)
        ]
    return [bilan.message]


def seances_datees_importees(session: Session, version_id: int) -> bool:
    """Vrai si la version a des séances datées importées (elles ne se recalculent pas)."""
    return (
        session.scalar(
            select(m.Occurrence.id)
            .where(
                m.Occurrence.version_id == version_id,
                m.Occurrence.origine == OrigineOccurrence.IMPORT,
            )
            .limit(1)
        )
        is not None
    )
