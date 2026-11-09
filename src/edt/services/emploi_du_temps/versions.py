"""Semestres et versions d'emploi du temps : choix, historique, activation, suppression.

Une seule version est active par semestre (index unique en base) ; c'est
elle que l'on consulte par défaut et elle que le calcul suivant prend comme
base (verrous et indices). La version active ne se supprime pas : il faut
d'abord en activer une autre.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.enumerations import EtatCalcul, OrigineVersion, StatutOccurrence
from edt.erreurs import ErreurIntrouvable, ErreurRegleMetier
from edt.services.calendrier import semestre_par_defaut
from edt.services.emploi_du_temps import presentation, regles_personnalisees
from edt.services.emploi_du_temps.identifiants import exiger_identifiant, obtenir
from edt.services.parametres import lire_parametres

journal = logging.getLogger(__name__)

LIMITE_HISTORIQUE = 100
"""Nombre maximal de calculs listés dans l'historique d'un semestre (les plus récents)."""


@dataclass(frozen=True)
class ResumeSemestre:
    """Un semestre, tel qu'il apparaît dans les sélecteurs."""

    id: int
    code: str
    libelle: str
    annee: str
    date_debut: dt.date
    date_fin: dt.date
    version_active_id: int | None


@dataclass(frozen=True)
class ResumeVersion:
    """Une version d'emploi du temps."""

    id: int
    semestre_id: int
    numero: int
    libelle: str
    origine: OrigineVersion
    active: bool
    cree_le: dt.datetime
    calcul_id: int | None
    nb_seances: int
    nb_occurrences: int
    nb_a_programmer: int


@dataclass(frozen=True)
class LigneCalcul:
    """Un calcul de l'historique.

    `niveaux_modifies` : règles dont le niveau s'écartait du défaut, en
    phrases ; `contraintes_desactivees` : règles ignorées (calculs d'avant
    les niveaux de règles) ; `regles_modifiees` : règles personnalisées prises
    à un autre niveau que le leur (« R002 : facultatif »).
    """

    id: int
    etat: EtatCalcul
    demarre_le: dt.datetime
    duree_s: float | None
    message: str
    contraintes_desactivees: tuple[str, ...]
    version_id: int | None
    version_numero: int | None
    niveaux_modifies: tuple[str, ...] = ()
    regles_modifiees: tuple[str, ...] = ()


@dataclass(frozen=True)
class Historique:
    """Calculs (les plus récents d'abord) et versions d'un semestre."""

    semestre: ResumeSemestre
    calculs: tuple[LigneCalcul, ...]
    versions: tuple[ResumeVersion, ...]
    calculs_tronques: bool


def lister_semestres(session: Session) -> tuple[ResumeSemestre, ...]:
    """Tous les semestres, du plus ancien au plus récent, avec leur version active."""
    actives = dict(
        session.execute(
            select(m.VersionEdt.semestre_id, m.VersionEdt.id).where(m.VersionEdt.active.is_(True))
        ).all()
    )
    lignes = session.execute(
        select(m.Semestre, m.AnneeScolaire.libelle)
        .join(m.AnneeScolaire, m.Semestre.annee_scolaire_id == m.AnneeScolaire.id)
        .order_by(m.Semestre.date_debut, m.Semestre.id)
    ).all()
    return tuple(
        _resume_semestre(semestre, annee, actives.get(semestre.id)) for semestre, annee in lignes
    )


def choisir_semestre(
    session: Session, semestre_id: int | None, aujourdhui: dt.date | None = None
) -> ResumeSemestre | None:
    """Le semestre demandé, sinon le semestre par défaut (celui du jour), sinon `None`.

    Lève `ErreurIntrouvable` si `semestre_id` est donné mais n'existe pas.
    """
    semestres = lister_semestres(session)
    if semestre_id is not None:
        for semestre in semestres:
            if semestre.id == semestre_id:
                return semestre
        raise ErreurIntrouvable(f"Le semestre n° {semestre_id} n'existe pas.")
    if not semestres:
        return None
    defaut = semestre_par_defaut(session, aujourdhui)
    return next(semestre for semestre in semestres if semestre.id == defaut.id)


def exiger_semestre(session: Session, semestre_id: int) -> ResumeSemestre:
    """Le semestre `semestre_id` ; lève `ErreurIntrouvable` s'il n'existe pas."""
    semestre = choisir_semestre(session, semestre_id)
    if semestre is None:  # impossible : choisir_semestre lève déjà pour un identifiant inconnu
        raise ErreurIntrouvable(f"Le semestre n° {semestre_id} n'existe pas.")
    return semestre


def annee_du_semestre(session: Session, semestre_id: int) -> int:
    """Identifiant de l'année scolaire du semestre (les groupes sont rattachés à l'année)."""
    exiger_identifiant(semestre_id, f"Le semestre n° {semestre_id} n'existe pas.")
    annee_id = session.scalar(
        select(m.Semestre.annee_scolaire_id).where(m.Semestre.id == semestre_id)
    )
    if annee_id is None:
        raise ErreurIntrouvable(f"Le semestre n° {semestre_id} n'existe pas.")
    return annee_id


def lire_version(session: Session, version_id: int) -> ResumeVersion:
    """Résumé d'une version ; lève `ErreurIntrouvable` si elle n'existe pas."""
    version = obtenir(session, m.VersionEdt, version_id)
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps n° {version_id} n'existe pas.")
    return _resumes_versions(session, [version])[0]


def lister_versions(session: Session, semestre_id: int) -> tuple[ResumeVersion, ...]:
    """Versions d'un semestre, la plus récente d'abord."""
    versions = list(
        session.scalars(
            select(m.VersionEdt)
            .where(m.VersionEdt.semestre_id == semestre_id)
            .order_by(m.VersionEdt.numero.desc())
        )
    )
    return _resumes_versions(session, versions)


def version_active(session: Session, semestre_id: int) -> ResumeVersion | None:
    """La version active du semestre, s'il y en a une."""
    version = session.scalar(
        select(m.VersionEdt).where(
            m.VersionEdt.semestre_id == semestre_id, m.VersionEdt.active.is_(True)
        )
    )
    return _resumes_versions(session, [version])[0] if version is not None else None


def historique(session: Session, semestre_id: int) -> Historique:
    """Calculs et versions d'un semestre, pour l'écran d'historique."""
    semestre = exiger_semestre(session, semestre_id)
    calculs = list(
        session.scalars(
            select(m.Calcul)
            .where(m.Calcul.semestre_id == semestre_id)
            .order_by(m.Calcul.id.desc())
            .limit(LIMITE_HISTORIQUE + 1)
        )
    )
    tronques = len(calculs) > LIMITE_HISTORIQUE
    calculs = calculs[:LIMITE_HISTORIQUE]
    produites = dict(
        session.execute(
            select(m.VersionEdt.calcul_id, m.VersionEdt.id).where(
                m.VersionEdt.calcul_id.in_([c.id for c in calculs])
            )
        ).all()
    )
    numeros = dict(
        session.execute(
            select(m.VersionEdt.id, m.VersionEdt.numero).where(
                m.VersionEdt.id.in_(list(produites.values()))
            )
        ).all()
    )
    # La pause déjeuner n'est pas enregistrée avec le calcul : celle d'aujourd'hui fait foi.
    dejeuner_actif = lire_parametres(session).dejeuner_actif
    lignes = tuple(
        LigneCalcul(
            id=c.id,
            etat=EtatCalcul(c.etat),
            demarre_le=c.demarre_le,
            duree_s=c.duree_s,
            message=c.message,
            contraintes_desactivees=tuple(c.contraintes_desactivees or ()),
            version_id=produites.get(c.id),
            version_numero=numeros.get(produites.get(c.id)),
            niveaux_modifies=_niveaux_modifies(c, dejeuner_actif),
            regles_modifiees=regles_personnalisees.resume_regles_du_calcul(c),
        )
        for c in calculs
    )
    return Historique(
        semestre=semestre,
        calculs=lignes,
        versions=lister_versions(session, semestre_id),
        calculs_tronques=tronques,
    )


def _niveaux_modifies(calcul: m.Calcul, dejeuner_actif: bool) -> tuple[str, ...]:
    """Règles dont le niveau s'écartait du défaut pour ce calcul (rien pour un calcul ancien).

    `DEJEUNER` n'est pas citée sans pause déjeuner (`presentation.libelles_niveaux`).
    """
    niveaux = (calcul.parametres or {}).get("niveaux")
    if niveaux is None:
        return ()
    if not isinstance(niveaux, dict):
        journal.warning("Calcul %d : niveaux enregistrés illisibles (%r)", calcul.id, niveaux)
        return ("niveaux illisibles",)
    return presentation.libelles_niveaux(niveaux, dejeuner_actif=dejeuner_actif)


def activer_version(session: Session, version_id: int) -> ResumeVersion:
    """Rend la version active pour son semestre (l'ancienne version active est désactivée)."""
    version = obtenir(session, m.VersionEdt, version_id)
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps n° {version_id} n'existe pas.")
    if version.active:
        return lire_version(session, version_id)
    # Désactiver d'abord l'ancienne : l'index unique n'admet qu'une version active à la fois.
    session.execute(
        update(m.VersionEdt)
        .where(m.VersionEdt.semestre_id == version.semestre_id, m.VersionEdt.active.is_(True))
        .values(active=False)
        .execution_options(synchronize_session="fetch")
    )
    session.flush()
    version.active = True
    session.flush()
    journal.info(
        "Version %d (n°%d) activée pour le semestre %d",
        version.id,
        version.numero,
        version.semestre_id,
    )
    return lire_version(session, version_id)


def supprimer_version(session: Session, version_id: int) -> str:
    """Supprime une version non active (ses séances partent avec elle) ; renvoie son libellé."""
    version = obtenir(session, m.VersionEdt, version_id)
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps n° {version_id} n'existe pas.")
    if version.active:
        raise ErreurRegleMetier(
            "La version active ne peut pas être supprimée : rendre d'abord une autre version "
            "active, puis supprimer celle-ci."
        )
    libelle = version.libelle
    session.delete(version)
    session.flush()
    journal.info("Version %d (%s) supprimée", version_id, libelle)
    return libelle


# --------------------------------------------------------------------------- #
# Interne
# --------------------------------------------------------------------------- #


def _resume_semestre(
    semestre: m.Semestre, annee: str, version_active_id: int | None
) -> ResumeSemestre:
    return ResumeSemestre(
        id=semestre.id,
        code=semestre.code,
        libelle=semestre.libelle,
        annee=annee,
        date_debut=semestre.date_debut,
        date_fin=semestre.date_fin,
        version_active_id=version_active_id,
    )


def _resumes_versions(session: Session, versions: list[m.VersionEdt]) -> tuple[ResumeVersion, ...]:
    """Résumés avec les comptes de séances, en trois requêtes quel que soit le nombre de versions."""
    if not versions:
        return ()
    ids = [v.id for v in versions]
    seances = dict(
        session.execute(
            select(m.SeanceHebdo.version_id, func.count())
            .where(m.SeanceHebdo.version_id.in_(ids))
            .group_by(m.SeanceHebdo.version_id)
        ).all()
    )
    occurrences = dict(
        session.execute(
            select(m.Occurrence.version_id, func.count())
            .where(m.Occurrence.version_id.in_(ids))
            .group_by(m.Occurrence.version_id)
        ).all()
    )
    a_programmer = dict(
        session.execute(
            select(m.Occurrence.version_id, func.count())
            .where(
                m.Occurrence.version_id.in_(ids),
                m.Occurrence.statut == StatutOccurrence.A_PROGRAMMER,
            )
            .group_by(m.Occurrence.version_id)
        ).all()
    )
    return tuple(
        ResumeVersion(
            id=v.id,
            semestre_id=v.semestre_id,
            numero=v.numero,
            libelle=v.libelle,
            origine=OrigineVersion(v.origine),
            active=bool(v.active),
            cree_le=v.cree_le,
            calcul_id=v.calcul_id,
            nb_seances=seances.get(v.id, 0),
            nb_occurrences=occurrences.get(v.id, 0),
            nb_a_programmer=a_programmer.get(v.id, 0),
        )
        for v in versions
    )
