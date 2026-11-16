"""Génération des jeux fictifs : configuration, déterminisme, unicités, effectifs, rapport."""

from __future__ import annotations

import dataclasses
import hashlib

import pytest
from sqlalchemy import func, select
from tests.outils.synthese.aides import generer

from edt.db import modeles as m
from edt.db.base import Base
from edt.db.moteur import fabrique_sessions, session_base
from edt.domaine.enumerations import NatureGroupe
from edt.erreurs import ErreurDonneesInvalides, ErreurRegleMetier
from edt.synthese import CONFIGURATIONS, PETIT, ConfigPromotion, configuration_nommee
from edt.synthese.generateur import generer_jeu

COLONNES_VOLATILES = {("parametre", "modifie_le")}
"""Colonnes remplies par l'horloge à l'initialisation de la base (hors générateur)."""


def empreinte(url: str) -> str:
    """Empreinte de toutes les lignes de toutes les tables (ordre de clé primaire)."""
    condense = hashlib.sha256()
    with fabrique_sessions(url)() as session:
        for table in Base.metadata.sorted_tables:
            colonnes = [c for c in table.columns if (table.name, c.name) not in COLONNES_VOLATILES]
            lignes = session.execute(select(*colonnes).order_by(*table.primary_key.columns)).all()
            condense.update(f"{table.name}:{[tuple(ligne) for ligne in lignes]!r}".encode())
    return condense.hexdigest()


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_trois_tailles_nommees():
    assert set(CONFIGURATIONS) == {"petit", "moyen", "grand"}
    assert configuration_nommee("moyen").nom == "moyen"
    with pytest.raises(ErreurDonneesInvalides, match="inconnue"):
        configuration_nommee("géant")


@pytest.mark.cas_limite
def test_configuration_invalide_refusee_avec_le_detail():
    """Cas : configuration sur mesure incohérente (classes d'apprentis en 1re année).
    Risque : jeu absurde ou erreur tardive sans la liste des problèmes."""
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        dataclasses.replace(PETIT, promotions=(ConfigPromotion(1, 2, nb_classes_apprentis=1),))
    assert "apprentis" in str(erreur.value)


# --------------------------------------------------------------------------- #
# Déterminisme
# --------------------------------------------------------------------------- #


def test_meme_graine_memes_donnees(tmp_path, jeu_petit):
    autre = generer(tmp_path / "bis", "petit", reference=True)
    assert empreinte(autre.url) == empreinte(jeu_petit.url)
    assert autre.rapport.temoin == jeu_petit.rapport.temoin


def test_graines_differentes_jeux_differents(tmp_path, jeu_petit):
    autre = generer(tmp_path / "graine7", "petit", graine=7, reference=True)
    assert empreinte(autre.url) != empreinte(jeu_petit.url)


@pytest.mark.cas_limite
def test_base_deja_garnie_refusee(jeu_petit):
    """Cas : génération directe dans une base qui contient déjà des référentiels.
    Risque : doublons ou mélange de deux établissements."""
    with session_base(jeu_petit.url) as session, pytest.raises(ErreurRegleMetier, match="déjà"):
        generer_jeu(session, "petit")


def test_jeu_sur_mesure(base_vierge):
    sur_mesure = dataclasses.replace(
        PETIT,
        nom="sur mesure",
        promotions=(ConfigPromotion(2, 1),),
        modules_max=3,
        etudiants=False,
        evenements=False,
    )
    with session_base(base_vierge) as session:
        rapport = generer_jeu(session, sur_mesure, graine=3)
        assert session.scalar(select(func.count()).select_from(m.Etudiant)) == 0
        # Sans étudiants, les groupes transverses sont déclarés chevauchants avec leur promotion.
        assert session.scalar(select(func.count()).select_from(m.groupe_chevauchement)) > 0
    assert rapport.configuration.nom == "sur mesure"
    assert rapport.nb_besoins > 0 and not rapport.besoins_ecartes


# --------------------------------------------------------------------------- #
# Unicités et effectifs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("modele", "colonnes"),
    [
        (m.Salle, ("code", "campus_id")),
        (m.Groupe, ("code", "annee_scolaire_id")),
        (m.Matiere, ("code", "promotion_id")),
        (m.Enseignant, ("identifiant",)),
        (m.Enseignant, ("civilite", "nom")),
        (m.Etudiant, ("identifiant",)),
    ],
)
def test_unicites(jeu_moyen, modele, colonnes):
    with jeu_moyen.session() as session:
        total = session.scalar(select(func.count()).select_from(modele))
        distincts = session.execute(
            select(*(getattr(modele, c) for c in colonnes)).distinct()
        ).all()
    assert total > 0
    assert len(distincts) == total


def test_effectifs_coherents(jeu_moyen):
    with jeu_moyen.session() as session:
        groupes = list(session.scalars(select(m.Groupe)))
        inscrits = dict(
            session.execute(
                select(m.inscription.c.groupe_id, func.count()).group_by(m.inscription.c.groupe_id)
            ).all()
        )
        nb_etudiants = session.scalar(select(func.count()).select_from(m.Etudiant))
    enfants: dict[int, list[m.Groupe]] = {}
    for g in groupes:
        if g.parent_id is not None:
            enfants.setdefault(g.parent_id, []).append(g)
    for g in groupes:
        if g.id in enfants:
            assert g.effectif == sum(e.effectif for e in enfants[g.id]), g.code
        if g.nature in (NatureGroupe.DEMI_GROUPE, NatureGroupe.TRANSVERSE) and g.id in inscrits:
            assert g.effectif == inscrits[g.id], g.code
    demi_groupes = [g for g in groupes if g.nature is NatureGroupe.DEMI_GROUPE]
    # Chaque étudiant est dans exactement un demi-groupe.
    assert sum(inscrits.get(g.id, 0) for g in demi_groupes) == nb_etudiants
    for g in groupes:
        if g.nature is NatureGroupe.CLASSE:
            assert 22 <= g.effectif <= 36, g.code


def test_groupes_transverses_relies_a_leurs_classes(jeu_moyen):
    """Sans inscrits ni chevauchement déclaré, un transverse ne serait en conflit avec aucune classe."""
    with jeu_moyen.session() as session:
        transverses = list(
            session.scalars(select(m.Groupe).where(m.Groupe.nature == NatureGroupe.TRANSVERSE))
        )
        inscrits = set(session.scalars(select(m.inscription.c.groupe_id)))
        declares = {
            g for paire in session.execute(select(m.groupe_chevauchement)).all() for g in paire
        }
    assert transverses
    assert all(g.id in inscrits or g.id in declares for g in transverses)


# --------------------------------------------------------------------------- #
# Rapport
# --------------------------------------------------------------------------- #


def test_rapport_coherent(jeu_moyen):
    rapport = jeu_moyen.rapport
    assert rapport.besoins_ecartes == ()
    assert rapport.compteurs["besoin"] == rapport.nb_besoins
    for code, nombre in rapport.seances_par_semestre.items():
        assert len(rapport.temoin_par_semestre[code]) == nombre
        assert len(rapport.placements(code)) == nombre
    assert 100 <= rapport.seances_par_semestre["S1"] <= 150
    for charge in rapport.charges:
        assert 0.4 <= charge.groupes.moyen <= charge.groupes.maximal <= 0.9
    resume = rapport.resume()
    assert "« moyen »" in resume and "Besoins écartés faute de place : 0" in resume
