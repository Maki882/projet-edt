"""Chargement de l'instantané d'un semestre depuis la base."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from edt import cli
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import CibleIndisponibilite, ModeVolume, OrigineVersion
from edt.erreurs import ErreurIntrouvable
from edt.services.instantane import charger_instantane, charger_occurrences, charger_placements
from edt.services.niveaux import niveaux_de_version, niveaux_regles_de_version
from edt.synthese.generateur import generer_jeu

from .test_depliage import semaines_ouvertes_de_reference


@pytest.fixture
def instantane(jeu_minimal, session):
    return charger_instantane(session, jeu_minimal.semestre_id)


def _besoin(instantane, debut_libelle: str):
    (trouve,) = [b for b in instantane.besoins if b.libelle.startswith(debut_libelle)]
    return trouve


def test_contenu_de_l_instantane_du_jeu_minimal(instantane, jeu_minimal):
    assert instantane.semestre_id == jeu_minimal.semestre_id
    assert len(instantane.semaines) == 21
    assert len(instantane.creneaux) == 22
    assert len(instantane.salles) == jeu_minimal.nb_salles
    assert len(instantane.enseignants) == jeu_minimal.nb_enseignants
    assert len(instantane.groupes) == jeu_minimal.nb_groupes
    assert len(instantane.besoins) == jeu_minimal.nb_besoins
    assert instantane.problemes_depliage == ()
    assert instantane.version_base_id is None

    # 10 besoins par semaine, 4 en volume total (dont un à deux rangs), 2 ponctuels.
    assert len(instantane.seances) == 17
    modes = {mode: 0 for mode in ModeVolume}
    for besoin in instantane.besoins:
        modes[besoin.mode_volume] += 1
    assert modes == {ModeVolume.PAR_SEMAINE: 10, ModeVolume.TOTAL: 4, ModeVolume.PONCTUEL: 2}


def test_exigences_resolues_des_besoins(instantane):
    codes_salles = {s.code: s for s in instantane.salles}

    projet = _besoin(instantane, "PROJET")
    assert projet.effectif == 56  # somme des effectifs des deux classes
    assert projet.enseignants == frozenset()
    assert len(projet.groupes) == 2

    # Sans type de salle demandé, c'est le type par défaut du type de séance (CM : amphi).
    cm = _besoin(instantane, "CM Mathématiques")
    assert cm.types_salle_acceptes == frozenset({codes_salles["N016"].type_salle_id})
    (type_accepte,) = cm.types_salle_acceptes
    assert instantane.libelle_type_salle(type_accepte) == "Amphithéâtre"

    tp = _besoin(instantane, "TP Algorithmique - A1-G1a")
    assert [instantane.libelle_equipement(e) for e in tp.equipements_requis] == [
        "Postes informatiques"
    ]

    # Le groupe PMR partage un étudiant avec A1-G1a : toute sa lignée requiert l'accessibilité.
    assert _besoin(instantane, "TD Mathématiques - A1-G1").accessibilite_requise
    assert _besoin(instantane, "TP Algorithmique - A1-G1a").accessibilite_requise
    assert cm.accessibilite_requise
    assert not _besoin(instantane, "TD Mathématiques - A1-G2").accessibilite_requise
    assert not _besoin(instantane, "TP Algorithmique - A1-G1b").accessibilite_requise


def test_semaines_actives_des_seances(instantane):
    cm = instantane.seance(f"{_besoin(instantane, 'CM Mathématiques').id}-1")
    # 21 semaines moins Toussaint (8), Noël (16, 17) et les partiels de la promotion (19).
    assert cm.semaines == frozenset(range(1, 22)) - {8, 16, 17, 19}

    examen = instantane.seance(f"{_besoin(instantane, 'Partiel').id}-1")
    assert examen.semaines == frozenset({19})  # l'examen ponctuel a lieu pendant les partiels
    assert examen.jour_impose == 1 and examen.debut_impose == 510


def test_ressources_et_reglages_de_l_instantane(instantane):
    par_code = {s.code: s for s in instantane.salles}
    assert instantane.trajet(par_code["N016"].campus_id, par_code["L012"].campus_id) == 60
    assert instantane.trajet(par_code["N016"].campus_id, par_code["N204"].campus_id) == 0
    assert instantane.salle_accessible(par_code["N016"])
    assert not instantane.salle_accessible(par_code["N310"])  # accessibilité inconnue = non

    leroy = next(e for e in instantane.enseignants if e.identifiant == "ENS004")
    (lundi,) = instantane.indisponibilites_recurrentes(CibleIndisponibilite.ENSEIGNANT, leroy.id)
    assert (lundi.jour, lundi.debut, lundi.fin) == (0, 0, 1440)

    # L'indisponibilité datée de la salle de TP ne concerne que la couche datée.
    salle_tp = par_code["L115"]
    assert instantane.indisponibilites_recurrentes(CibleIndisponibilite.SALLE, salle_tp.id) == ()
    (travaux,) = instantane.indisponibilites_de(CibleIndisponibilite.SALLE, salle_tp.id)
    assert travaux.concerne(dt.date(2026, 11, 4))
    assert not travaux.concerne(dt.date(2026, 11, 9))

    # Fermeture propre à la promotion : étendue à ses descendants, pas aux autres groupes.
    promo = next(g for g in instantane.groupes if g.code == "A1")
    partiels = next(
        j
        for j in instantane.jours_fermes
        if j.libelle.startswith("Partiels") and promo.id in j.groupes
    )
    codes = {g.code for g in instantane.groupes if g.id in partiels.groupes}
    assert codes == {"A1", "A1-G1", "A1-G2", "A1-G1a", "A1-G1b", "A1-G2a", "A1-G2b"}
    assert instantane.parametres.pause_min == 15


def test_verrous_et_indices_viennent_de_la_version_active(jeu_minimal, instantane):
    premiere, seconde = instantane.seances[0], instantane.seances[1]
    salle = instantane.salles[0]
    with session_base() as session:
        version = m.VersionEdt(
            semestre_id=jeu_minimal.semestre_id,
            numero=1,
            libelle="Référence",
            origine=OrigineVersion.REFERENCE,
            active=True,
        )
        for seance, verrou in ((premiere, True), (seconde, False)):
            version.seances.append(
                m.SeanceHebdo(
                    besoin_id=seance.besoin.id,
                    rang=seance.rang,
                    jour=2,
                    debut_min=510,
                    fin_min=630,
                    salle_id=salle.id,
                    verrou=verrou,
                )
            )
        session.add(version)

    with session_base() as session:
        recharge = charger_instantane(session, jeu_minimal.semestre_id)
        version_id = session.scalar(select(m.VersionEdt.id))
        placements = charger_placements(session, version_id)

    assert recharge.version_base_id == version_id
    verrouillee = recharge.seance(premiere.id)
    assert verrouillee.verrou is not None and verrouillee.verrou == verrouillee.indice
    assert (verrouillee.verrou.jour, verrouillee.verrou.debut, verrouillee.verrou.salle_id) == (
        2,
        510,
        salle.id,
    )
    libre = recharge.seance(seconde.id)
    assert libre.verrou is None and libre.indice is not None
    assert recharge.seance(instantane.seances[2].id).indice is None
    assert {p.seance_id for p in placements} == {premiere.id, seconde.id}


def test_semestre_inconnu(base, session):
    with pytest.raises(ErreurIntrouvable):
        charger_instantane(session, 9999)


def test_semaines_actives_d_un_jeu_realiste_conformes_a_la_regle_de_reference(base):
    """Non-régression du dépliage rapide sur un établissement fictif complet (jeu « moyen »)."""
    with session_base() as session:
        generer_jeu(session, "moyen", 0)
    with session_base() as session:
        instantanes = [
            charger_instantane(session, semestre_id)
            for semestre_id in session.scalars(select(m.Semestre.id).order_by(m.Semestre.id))
        ]

    comparees, jeux_de_semaines = 0, set()
    for instantane in instantanes:
        assert instantane.problemes_depliage == ()
        assert len(instantane.seances) > 100
        for seance in instantane.seances:
            if seance.besoin.ponctuel:
                continue
            ouvertes = {
                semaine.numero
                for semaine in semaines_ouvertes_de_reference(
                    seance.besoin, instantane.semaines, instantane.jours_fermes
                )
            }
            if seance.besoin.mode_volume is ModeVolume.PAR_SEMAINE:
                assert seance.semaines == ouvertes, seance.besoin.libelle
            else:
                assert seance.semaines <= ouvertes, seance.besoin.libelle
            comparees += 1
            jeux_de_semaines.add(frozenset(ouvertes))
    assert comparees > 100
    # Le jeu exerce la règle : selon les groupes et les fenêtres, les semaines ouvertes diffèrent.
    assert len(jeux_de_semaines) > 3


# --------------------------------------------------------------------------- #
# Cas limites : identifiants hors bornes, salles homonymes, versions actives, besoin sans groupe
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("identifiant", [2**63, 10**30, -1, 0])
def test_un_identifiant_hors_des_entiers_sqlite_est_introuvable(
    base, session, identifiant, lanceur
):
    """Cas : un identifiant de semestre ou de version au-delà de 2**63, négatif ou nul arrive
    par une adresse web ou une option de commande.

    Risque : `OverflowError` brute du pilote (500 côté web, code 2 en ligne de commande) alors
    que -1 donnait proprement « n'existe pas » ; tous doivent être introuvables.
    """
    for chargeur in (charger_instantane, charger_placements, charger_occurrences):
        with pytest.raises(ErreurIntrouvable, match="n'existe pas"):
            chargeur(session, identifiant)
    # Même garde pour les niveaux d'une version.
    for lecteur in (niveaux_de_version, niveaux_regles_de_version):
        with pytest.raises(ErreurIntrouvable, match="n'existe pas"):
            lecteur(session, identifiant)


@pytest.mark.cas_limite
@pytest.mark.xfail(
    strict=True,
    reason="à corriger par les lots solveur et vérification : src/edt/commandes/generation.py "
    "(_semestre, ligne 196) et src/edt/services/verification.py (version_a_verifier) font "
    "session.get sans garder l'identifiant par identifiant_valide",
)
@pytest.mark.parametrize(
    ("commande", "identifiant"),
    [
        (["pre-verifier", "--semestre"], 2**63),
        (["pre-verifier", "--semestre"], 10**30),
        (["verifier", "--version"], 2**63),
    ],
    ids=["pre-verifier-2^63", "pre-verifier-10^30", "verifier-2^63"],
)
def test_une_commande_avec_un_identifiant_hors_bornes_repond_n_existe_pas(
    base, commande, identifiant, lanceur
):
    """Cas : `pre-verifier --semestre 99999999999999999999` ou `verifier --version 2**63`
    (identifiant au-delà des entiers de SQLite).

    Risque : `OverflowError` brute du pilote, code 2 « Erreur inattendue » avec trace, au lieu
    du code 1 « n'existe pas » rendu pour -1.
    """
    resultat = lanceur.invoke(cli.app, [*commande, str(identifiant)])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "n'existe pas" in resultat.output and "Traceback" not in resultat.output
