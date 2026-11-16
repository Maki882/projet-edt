"""Initialisation de la base, unicités et règles de suppression."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from edt.db import modeles as m
from edt.db.base import Base
from edt.db.initialisation import initialiser_base, tables_manquantes
from edt.db.jeux import creer_jeu_minimal
from edt.db.moteur import obtenir_moteur, session_base
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import (
    CibleIndisponibilite,
    DemiJournee,
    ModeVolume,
    NatureGroupe,
    NatureJourFerme,
    OrigineVersion,
)
from edt.domaine.normalisation import normaliser_code
from edt.domaine.parametres import definitions
from edt.domaine.regles import NaturePortee, TypeRegle
from edt.erreurs import ErreurRegleMetier

RACINE = Path(__file__).resolve().parents[3]


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


# --------------------------------------------------------------------------- #
# Cas limites : contrôles du modèle, codes normalisés, init-db relancé, jeu minimal
# --------------------------------------------------------------------------- #


def _creneau_a_l_envers(session):
    session.add(m.Creneau(jour=0, debut_min=600, fin_min=500, demi_journee=DemiJournee.MATIN))


def _besoin_de_duree_nulle(session):
    besoin = session.scalar(select(m.Besoin))
    session.add(
        m.Besoin(
            semestre_id=besoin.semestre_id,
            matiere_id=besoin.matiere_id,
            type_seance_id=besoin.type_seance_id,
            duree_min=0,
        )
    )


def _ponctuel_sans_date(session):
    besoin = session.scalar(select(m.Besoin))
    session.add(
        m.Besoin(
            semestre_id=besoin.semestre_id,
            matiere_id=besoin.matiere_id,
            type_seance_id=besoin.type_seance_id,
            mode_volume=ModeVolume.PONCTUEL,
        )
    )


def _trajet_a_l_envers(session):
    campus = sorted(session.scalars(select(m.Campus.id)))
    session.add(m.Trajet(campus_a_id=campus[1], campus_b_id=campus[0], duree_min=10))


def _indisponibilite_ni_recurrente_ni_datee(session):
    enseignant = session.scalar(select(m.Enseignant))
    session.add(
        m.Indisponibilite(
            cible=CibleIndisponibilite.ENSEIGNANT,
            enseignant_id=enseignant.id,
            debut_min=0,
            fin_min=60,
        )
    )


def _maximum_journalier_negatif(session):
    session.add(m.Enseignant(identifiant="NEG-J", nom="Négatif", max_minutes_jour=-60))


def _maximum_hebdomadaire_nul(session):
    session.add(m.Enseignant(identifiant="NEG-S", nom="Nul", max_minutes_semaine=0))


def _groupe_son_propre_parent(session):
    groupe = session.scalar(select(m.Groupe))
    groupe.parent_id = groupe.id


@pytest.mark.parametrize(
    ("fabrique", "contrainte"),
    [
        (_creneau_a_l_envers, "ck_creneau_plage"),
        (_besoin_de_duree_nulle, "ck_besoin_duree_valide"),
        (_ponctuel_sans_date, "ck_besoin_date_ponctuelle_si_ponctuel"),
        (_trajet_a_l_envers, "ck_trajet_paire_ordonnee"),
        (_indisponibilite_ni_recurrente_ni_datee, "ck_indisponibilite_recurrente_ou_datee"),
        (_maximum_journalier_negatif, "ck_enseignant_max_jour_positif"),
        (_maximum_hebdomadaire_nul, "ck_enseignant_max_semaine_positif"),
        (_groupe_son_propre_parent, "ck_groupe_pas_son_propre_parent"),
    ],
    ids=lambda x: x.__name__.lstrip("_") if callable(x) else None,
)
@pytest.mark.cas_limite
def test_contraintes_de_controle_du_modele_et_maxima_positifs(
    jeu_minimal, session, fabrique, contrainte
):
    """Cas : une écriture qui contourne les services viole une règle de cohérence du modèle
    (créneau à l'envers, besoin de durée nulle, ponctuel sans date, trajet inversé,
    indisponibilité ni récurrente ni datée, maxima horaires négatifs ou nuls, groupe parent de
    lui-même).

    Risque : dernière ligne de défense avant un instantané incohérent ; un remaniement de
    `modeles.py` perdrait ces CHECK sans signal, et les maxima négatifs rendaient
    `MAX_ENSEIGNANT` infaisable sans dire que la donnée est absurde.
    """
    fabrique(session)
    with pytest.raises(IntegrityError) as capture:
        session.flush()
    assert contrainte in str(capture.value.orig)  # la base nomme la règle violée


@pytest.mark.parametrize("variante", ["N105\u00a0", "\u200bN105", "  N105 ", "N105\t"])
@pytest.mark.cas_limite
def test_un_code_aux_espaces_invisibles_est_le_meme_code(jeu_minimal, session, variante):
    """Cas : un code saisi ou importé porte une espace insécable, une espace de largeur nulle,
    des espaces aux bords ou une tabulation (collé depuis un traitement de texte).

    Risque : « N105 » et « N105\u00a0 » passaient l'unicité et créaient un doublon invisible,
    introuvable à la recherche ; le code doit être normalisé dès l'affectation.
    """
    salle = session.scalar(select(m.Salle).where(m.Salle.code == "N105"))
    doublon = m.Salle(
        code=variante, campus_id=salle.campus_id, capacite=10, type_salle_id=salle.type_salle_id
    )
    assert doublon.code == "N105"  # normalisé dès l'affectation
    session.add(doublon)
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.cas_limite
def test_les_codes_et_identifiants_sont_normalises_en_nfc(jeu_minimal, session):
    """Cas : le même code en forme Unicode décomposée (« e » + accent) et précomposée ;
    identifiant d'enseignant avec une espace insécable.

    Risque : doublons invisibles de campus ou d'enseignants ; la forme enregistrée doit être NFC
    et l'unicité doit jouer.
    """
    session.add(m.Campus(code="e\u0301cole", nom="Forme décomposée"))  # « e » + accent
    session.flush()
    assert session.scalar(select(m.Campus.code).where(m.Campus.nom == "Forme décomposée")) == (
        "\u00e9cole"
    )
    session.add(m.Campus(code="\u00e9cole", nom="Forme composée"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.add(m.Enseignant(identifiant="ENS004\u00a0", nom="Homonyme invisible"))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    assert normaliser_code("  A1\u00a0\u00a0G2 ") == "A1 G2"
    assert normaliser_code("ENS\u200b001\ufeff") == "ENS001"
    assert normaliser_code("IM.1101") == "IM.1101"


@pytest.mark.cas_limite
def test_init_db_relance_ne_recree_pas_un_referentiel_supprime_volontairement(base, aujourdhui):
    """Cas : l'utilisateur a supprimé un type de séance, un équipement et toute la grille pour
    la ressaisir ; le déploiement suivant relance `init-db`.

    Risque : lignes ressuscitées sans un mot (choix de l'utilisateur annulé à chaque mise à
    jour) ; seul un paramètre absent doit être reposé.
    """
    with session_base() as session:
        session.delete(session.scalar(select(m.TypeSeance).where(m.TypeSeance.code == "PROJET")))
        session.delete(session.scalar(select(m.Equipement).where(m.Equipement.code == "VISIO")))
        session.execute(delete(m.Creneau))  # grille effacée pour être ressaisie

    rapport = initialiser_base(aujourdhui=aujourdhui)

    assert rapport.ajouts["type_seance"] == 0
    assert rapport.ajouts["equipement"] == 0
    assert rapport.ajouts["creneau"] == 0
    with session_base() as session:
        assert session.scalar(select(m.TypeSeance).where(m.TypeSeance.code == "PROJET")) is None
        assert _compter(session, m.Creneau) == 0
        # Un paramètre absent, lui, est toujours reposé (paramètre nouveau d'une version).
        session.delete(session.get(m.Parametre, "pause_min"))
    assert initialiser_base(aujourdhui=aujourdhui).ajouts["parametre"] == 1


@pytest.mark.cas_limite
def test_un_type_de_seance_par_defaut_sans_son_type_de_salle_est_signale(base, aujourdhui, caplog):
    """Cas : base sans aucun paramètre ni type de séance, mais dont les types de salle sont
    déjà saisis sans « AMPHI » (base remplie à la main, puis `init-db`).

    Risque : le type de séance « CM » créé sans type de salle par défaut, sans un mot ; la
    création continue, mais l'écart est journalisé avec les deux codes.
    """
    with session_base() as session:
        session.execute(delete(m.Parametre))
        session.execute(delete(m.TypeSeance))
        session.delete(session.scalar(select(m.TypeSalle).where(m.TypeSalle.code == "AMPHI")))

    with caplog.at_level(logging.WARNING, logger="edt.db.initialisation"):
        rapport = initialiser_base(aujourdhui=aujourdhui)

    assert rapport.ajouts["type_seance"] > 0
    with session_base() as session:
        cm = session.scalar(select(m.TypeSeance).where(m.TypeSeance.code == "CM"))
        td = session.scalar(select(m.TypeSeance).where(m.TypeSeance.code == "TD"))
        assert cm.type_salle_defaut is None and td.type_salle_defaut.code == "COURS"
    assert "CM" in caplog.text and "AMPHI" in caplog.text


@pytest.mark.cas_limite
def test_le_jeu_minimal_refuse_proprement_une_base_avec_regles_ou_sans_semestre_s1(base):
    """Cas : `jeu-minimal` sur une base sans référentiel mais avec une règle déjà créée, puis sur
    une base dont les semestres ont été renommés.

    Risque : `IntegrityError` brute (unicité (année, code) de la règle) puis `StopIteration`
    (semestre S1 introuvable) : code 2 / 500 au lieu d'un refus qui dit quoi faire.
    """
    with session_base() as session:
        annee = session.scalar(select(m.AnneeScolaire))
        session.add(
            m.Regle(
                annee_scolaire_id=annee.id,
                code="R001",
                type=TypeRegle.ORDRE_SEMAINE,
                portee=NaturePortee.ETABLISSEMENT,
                parametres={"avant": "CM", "apres": "TD"},
                niveau=Niveau.FACULTATIF,
            )
        )
    with session_base() as session, pytest.raises(ErreurRegleMetier) as capture:
        creer_jeu_minimal(session)
    assert "règles" in capture.value.message and "init-db --reset" in capture.value.message

    with session_base() as session:
        session.execute(delete(m.Regle))
        for semestre in session.scalars(select(m.Semestre)):
            semestre.code = semestre.code.replace("S", "SEM")
    with session_base() as session, pytest.raises(ErreurRegleMetier) as capture:
        creer_jeu_minimal(session)
    assert "S1" in capture.value.message and "SEM1, SEM2" in capture.value.message
    with session_base() as session:
        assert _compter(session, m.Salle) == 0  # rien n'a été écrit
