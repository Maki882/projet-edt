"""Formulaire de déplacement d'une séance : heures de début annoncées « possible » ou « refusé »."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.outils.verification.versions import enregistrer_version

from edt.db import modeles as m
from edt.domaine.enumerations import Gravite
from edt.services.emploi_du_temps import modifications
from edt.verification.contrat import Anomalie


@pytest.fixture
def seance(jeu_minimal, session) -> m.SeanceHebdo:
    """Le CM de mathématiques de la version saisie à la main (lundi 8 h 30)."""
    version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    return session.scalar(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.version_id == version_id)
        .order_by(m.SeanceHebdo.jour, m.SeanceHebdo.debut_min)
        .limit(1)
    )


def mentions(formulaire) -> list[str]:
    return [texte.rpartition(" (")[2].rstrip(")") for _, texte in formulaire.debuts]


def test_chaque_heure_annoncee_sans_changer_les_valeurs(seance, session, faux_verificateur):
    formulaire = modifications.formulaire_seance(session, seance.id)
    valeurs = [debut for debut, _ in formulaire.debuts]
    assert len(valeurs) > 1
    attendu = ["actuelle" if v == seance.debut_min else "possible" for v in valeurs]
    assert mentions(formulaire) == attendu
    verifiees = [a for a in faux_verificateur.appels if a[0] == "modification"]
    assert len(verifiees) == len(valeurs) - 1  # une vérification par heure, sauf l'actuelle
    for appel in verifiees:  # avec la salle que le formulaire propose à cette heure
        a_cette_heure = modifications.formulaire_seance(session, seance.id, debut=appel[3])
        assert appel[-1] == a_cette_heure.salle_id is not None

    faux_verificateur.modification = [Anomalie("GROUPE_UNIQUE", Gravite.ERREUR, "Conflit.")]
    refusees = modifications.formulaire_seance(session, seance.id)
    assert [d for d, _ in refusees.debuts] == valeurs
    assert mentions(refusees) == ["actuelle" if m_ == "actuelle" else "refusé" for m_ in attendu]


@pytest.mark.cas_limite
def test_budget_depasse_ou_verificateur_absent(seance, session, faux_verificateur, monkeypatch):
    """Cas : la vérification des heures dépasse son budget, ou le vérificateur est absent.

    Risque : un formulaire qui met des secondes à se recharger à chaque changement de jour,
    ou une erreur qui empêche d'ouvrir le panneau de la séance.
    """
    monkeypatch.setattr(modifications, "BUDGET_ANNOTATION_S", -1.0)
    formulaire = modifications.formulaire_seance(session, seance.id)
    assert not [a for a in faux_verificateur.appels if a[0] == "modification"]
    assert "possible" not in mentions(formulaire) and formulaire.remarque_debuts is None

    monkeypatch.setattr(modifications, "BUDGET_ANNOTATION_S", 1.5)
    monkeypatch.setitem(__import__("sys").modules, "edt.services.verification", None)
    sans = modifications.formulaire_seance(session, seance.id)
    assert "possible" not in mentions(sans)
    assert sans.remarque_debuts.startswith("Les heures possibles n'ont pas pu être vérifiées")


@pytest.mark.cas_limite
def test_heure_annoncee_possible_est_acceptee_avec_la_salle_proposee(jeu_minimal, session):
    """Cas : le TD de mathématiques du groupe A1-G1 (lundi 13 h 45 à Notre-Dame) est déplacé au
    vendredi, où le groupe sort à 10 h 30 d'un TP à Issy ; à 10 h 45, le groupe et l'enseignant
    sont libres, mais toutes les salles proposées sont à Notre-Dame, à une heure de trajet.

    Risque : l'heure est annoncée « possible » (vérifiée sans salle), puis l'enregistrement est
    refusé avec chacune des salles proposées : l'annonce trompe l'utilisateur.
    """
    from edt.domaine.instantane import id_seance
    from edt.services.emploi_du_temps import raccords

    version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    seance = session.scalar(  # TD de mathématiques de A1-G1, lundi 13 h 45 (salle N105)
        select(m.SeanceHebdo).where(
            m.SeanceHebdo.version_id == version_id,
            m.SeanceHebdo.jour == 0,
            m.SeanceHebdo.debut_min == 13 * 60 + 45,
        )
    )
    vendredi = 4
    formulaire = modifications.formulaire_seance(session, seance.id, jour=vendredi)
    annonces = dict(zip((d for d, _ in formulaire.debuts), mentions(formulaire), strict=True))
    assert annonces[10 * 60 + 45] == "refusé"
    duree = seance.fin_min - seance.debut_min
    for debut, mention in annonces.items():
        salle_id = modifications.formulaire_seance(
            session, seance.id, jour=vendredi, debut=debut
        ).salle_id
        verification = raccords.verifier_modification(
            session,
            version_id,
            id_seance(seance.besoin_id, seance.rang),
            vendredi,
            debut,
            debut + duree,
            salle_id,
        )
        assert mention == ("possible" if verification.valide else "refusé"), debut


def test_heure_acceptee_avec_un_avertissement_le_dit(seance, session, faux_verificateur):
    faux_verificateur.modification = [
        Anomalie("PAUSE", Gravite.AVERTISSEMENT, "Entorse à une règle importante.")
    ]
    formulaire = modifications.formulaire_seance(session, seance.id)
    assert set(mentions(formulaire)) == {"actuelle", "possible, avec avertissement"}
    assert formulaire.conseil_debuts is None

    faux_verificateur.modification = [Anomalie("HORAIRES", Gravite.INFORMATION, "Écart admis.")]
    assert set(mentions(modifications.formulaire_seance(session, seance.id))) == {
        "actuelle",
        "possible",
    }


@pytest.mark.cas_limite
def test_jour_sans_aucune_heure_possible_dit_quoi_faire(
    seance, session, faux_verificateur, monkeypatch
):
    """Cas : toutes les heures du jour choisi sont refusées (CM d'une promotion entière).

    Risque : une liste de « (refusé) » sans explication : l'utilisateur ne sait ni pourquoi ni
    quoi essayer.
    """
    faux_verificateur.modification = [
        Anomalie("GROUPE_UNIQUE", Gravite.ERREUR, "Le groupe a deux séances."),
        Anomalie("SALLE_UNIQUE", Gravite.AVERTISSEMENT, "Remarque sur la salle."),
    ]
    autre_jour = modifications.formulaire_seance(session, seance.id, jour=seance.jour + 1)
    assert set(mentions(autre_jour)) == {"refusé"}
    assert autre_jour.conseil_debuts == (
        "Aucune heure possible le mardi avec les salles proposées : essayer un autre jour "
        "(règle la plus souvent en cause : Une séance par groupe)."
    )
    meme_jour = modifications.formulaire_seance(session, seance.id)
    assert meme_jour.conseil_debuts.startswith("Aucune autre heure possible le lundi")

    faux_verificateur.modification = []  # une heure possible : pas de conseil
    assert modifications.formulaire_seance(session, seance.id).conseil_debuts is None
    # Budget dépassé : des heures restent sans mention, rien n'est affirmé.
    monkeypatch.setattr(modifications, "BUDGET_ANNOTATION_S", -1.0)
    faux_verificateur.modification = [Anomalie("GROUPE_UNIQUE", Gravite.ERREUR, "Conflit.")]
    assert modifications.formulaire_seance(session, seance.id).conseil_debuts is None
