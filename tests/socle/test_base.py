"""Initialisation de la base, unicités et règles de suppression."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from edt.db import modeles as m
from edt.db.base import Base
from edt.db.initialisation import initialiser_base, tables_manquantes
from edt.db.moteur import obtenir_moteur, session_base
from edt.domaine.enumerations import NatureGroupe, NatureJourFerme, OrigineVersion
from edt.domaine.parametres import definitions

RACINE = Path(__file__).resolve().parents[2]


def _compter(session, modele) -> int:
    return session.scalar(select(func.count()).select_from(modele))


def test_initialisation_cree_le_schema_et_les_donnees_par_defaut(base, session):
    assert tables_manquantes(obtenir_moteur()) == []
    assert base.nb_tables == len(Base.metadata.tables)

    # Grille : 4 créneaux du lundi au vendredi, 2 le samedi matin.
    assert _compter(session, m.Creneau) == 5 * 4 + 2
    assert session.scalars(
        select(m.Creneau.debut_min).where(m.Creneau.jour == 0).order_by(m.Creneau.debut_min)
    ).all() == [510, 645, 825, 960]

    codes = set(session.scalars(select(m.TypeSeance.code)))
    assert codes == {"CM", "TD", "TP", "PROJET", "LANGUE", "DST", "EXAMEN", "AUTRE"}
    tp = session.scalar(select(m.TypeSeance).where(m.TypeSeance.code == "TP"))
    assert tp.samedi_autorise is True

    assert _compter(session, m.TypeSalle) >= 4
    assert _compter(session, m.Equipement) >= 4
    assert _compter(session, m.Parametre) == len(definitions())

    annee = session.scalar(select(m.AnneeScolaire))
    assert annee.libelle == "2026-2027"
    assert [s.code for s in annee.semestres] == ["S1", "S2"]


def test_le_calendrier_par_defaut_contient_les_feries_mobiles(session):
    feries = dict(
        session.execute(
            select(m.JourFerme.date_debut, m.JourFerme.libelle).where(
                m.JourFerme.nature == NatureJourFerme.FERIE
            )
        ).all()
    )
    assert len(feries) == 11
    assert feries[dt.date(2027, 3, 29)] == "Lundi de Pâques"
    assert feries[dt.date(2027, 5, 6)] == "Ascension"
    assert feries[dt.date(2027, 5, 17)] == "Lundi de Pentecôte"
    assert feries[dt.date(2026, 11, 11)] == "Armistice de 1918"


def test_initialisation_idempotente(base, aujourdhui):
    seconde = initialiser_base(aujourdhui=aujourdhui)
    assert seconde.nb_ajouts == 0


def test_reset_supprime_toutes_les_donnees(jeu_minimal, aujourdhui):
    initialiser_base(reset=True, aujourdhui=aujourdhui)
    with session_base() as session:
        assert _compter(session, m.Salle) == 0
        assert _compter(session, m.Besoin) == 0
        assert _compter(session, m.Creneau) == 22  # les données par défaut sont reposées


def _doublon_salle(session):
    salle = session.scalar(select(m.Salle))
    session.add(
        m.Salle(
            code=salle.code,
            campus_id=salle.campus_id,
            capacite=10,
            type_salle_id=salle.type_salle_id,
        )
    )


def _doublon_groupe(session):
    groupe = session.scalar(select(m.Groupe))
    session.add(
        m.Groupe(
            annee_scolaire_id=groupe.annee_scolaire_id,
            code=groupe.code,
            libelle="Doublon",
            nature=NatureGroupe.CLASSE,
        )
    )


def _doublon_enseignant(session):
    enseignant = session.scalar(select(m.Enseignant))
    session.add(m.Enseignant(identifiant=enseignant.identifiant, nom="Homonyme"))


def _doublon_matiere(session):
    matiere = session.scalar(select(m.Matiere))
    session.add(m.Matiere(code=matiere.code, libelle="Doublon", promotion_id=matiere.promotion_id))


def _doublon_matiere_sans_promotion(session):
    session.add_all(
        [m.Matiere(code="XX.0001", libelle="Un"), m.Matiere(code="XX.0001", libelle="Deux")]
    )


def _doublon_campus(session):
    session.add(m.Campus(code="NDC", nom="Autre"))


def _doublon_semestre(session):
    semestre = session.scalar(select(m.Semestre))
    session.add(
        m.Semestre(
            annee_scolaire_id=semestre.annee_scolaire_id,
            code=semestre.code,
            libelle="Doublon",
            date_debut=semestre.date_debut,
            date_fin=semestre.date_fin,
        )
    )


def _deux_versions_actives(session):
    semestre = session.scalar(select(m.Semestre))
    for numero in (1, 2):
        session.add(
            m.VersionEdt(
                semestre_id=semestre.id,
                numero=numero,
                libelle=f"Version {numero}",
                origine=OrigineVersion.REFERENCE,
                active=True,
            )
        )


def _doublon_seance(session):
    semestre = session.scalar(select(m.Semestre))
    besoin = session.scalar(select(m.Besoin))
    version = m.VersionEdt(
        semestre_id=semestre.id, numero=1, libelle="V1", origine=OrigineVersion.REFERENCE
    )
    for _ in range(2):
        version.seances.append(
            m.SeanceHebdo(besoin_id=besoin.id, rang=1, jour=0, debut_min=510, fin_min=630)
        )
    session.add(version)


@pytest.mark.parametrize(
    "creer_doublon",
    [
        _doublon_salle,
        _doublon_groupe,
        _doublon_enseignant,
        _doublon_matiere,
        _doublon_matiere_sans_promotion,
        _doublon_campus,
        _doublon_semestre,
        _deux_versions_actives,
        _doublon_seance,
    ],
    ids=lambda f: f.__name__.lstrip("_"),
)
def test_unicites(jeu_minimal, session, creer_doublon):
    creer_doublon(session)
    with pytest.raises(IntegrityError):
        session.flush()


def test_supprimer_un_groupe_utilise_par_un_besoin_est_refuse(jeu_minimal, session):
    classe = session.scalar(select(m.Groupe).where(m.Groupe.code == "A1-G1"))
    session.delete(classe)
    with pytest.raises(IntegrityError):
        session.flush()


def test_supprimer_une_annee_supprime_tout_ce_qui_en_depend(jeu_minimal, session):
    session.delete(session.get(m.AnneeScolaire, jeu_minimal.annee_scolaire_id))
    session.commit()
    for modele in (m.Semestre, m.Groupe, m.Matiere, m.Besoin, m.JourFerme):
        assert _compter(session, modele) == 0, modele.__tablename__
    # Les référentiels indépendants de l'année restent.
    assert _compter(session, m.Salle) == jeu_minimal.nb_salles
    assert _compter(session, m.Enseignant) == jeu_minimal.nb_enseignants


def test_supprimer_une_salle_laisse_la_seance_placee_sans_salle(jeu_minimal, session):
    besoin = session.scalar(select(m.Besoin))
    salle = session.scalar(select(m.Salle).where(m.Salle.code == "N204"))
    version = m.VersionEdt(
        semestre_id=jeu_minimal.semestre_id,
        numero=1,
        libelle="V1",
        origine=OrigineVersion.REFERENCE,
    )
    version.seances.append(
        m.SeanceHebdo(
            besoin_id=besoin.id, rang=1, jour=0, debut_min=510, fin_min=630, salle_id=salle.id
        )
    )
    session.add(version)
    session.commit()

    session.delete(salle)
    session.commit()
    session.expire_all()
    assert session.scalar(select(m.SeanceHebdo.salle_id)) is None


def test_chaque_table_est_documentee():
    """`docs/modele-donnees.md` doit décrire toutes les tables du modèle."""
    documentation = (RACINE / "docs" / "modele-donnees.md").read_text(encoding="utf-8")
    absentes = [nom for nom in Base.metadata.tables if f"### `{nom}`" not in documentation]
    assert absentes == []
