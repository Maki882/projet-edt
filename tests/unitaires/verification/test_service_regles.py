"""Règles personnalisées dans les services : niveaux d'une version et vérification d'une version.

Le jeu minimal du socle porte deux règles actives : R001 (« La promotion A1
ne travaille que du lundi au vendredi », nécessaire) et R002 (pause déjeuner
libre de A1-G1, importante).
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest
from sqlalchemy import select, update
from tests.outils.verification.versions import enregistrer_version

from edt.db import modeles as m
from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import EtatCalcul, Gravite, OrigineVersion
from edt.domaine.regles import EtatRegle
from edt.erreurs import ErreurDonneesInvalides
from edt.services.niveaux import (
    RegleActuelle,
    niveaux_regles_de_version,
    niveaux_regles_du_calcul,
)
from edt.services.verification import verifier_modification, verifier_version

DEBUT_CALCUL = dt.datetime(2026, 10, 6, 9, 0)
AVANT, APRES = dt.datetime(2026, 10, 1), dt.datetime(2026, 10, 7)


# --------------------------------------------------------------------------- #
# niveaux_regles_du_calcul (pur)
# --------------------------------------------------------------------------- #


def test_calcul_sans_copie_des_regles_chaque_regle_a_son_niveau():
    actuelles = [RegleActuelle(1, Niveau.NECESSAIRE, AVANT), RegleActuelle(2, Niveau.IMPORTANT)]
    attendus = {1: Niveau.NECESSAIRE, 2: Niveau.IMPORTANT}
    assert niveaux_regles_du_calcul({"niveaux": {}}, actuelles, DEBUT_CALCUL) == attendus
    assert niveaux_regles_du_calcul(None, actuelles) == attendus


def test_niveaux_choisis_au_calcul_et_regles_nouvelles():
    parametres = {
        "regles": [
            {"id": 1, "code": "R001", "niveau": "facultatif"},
            {"id": 2, "code": "R002", "niveau": None},  # explicitement non appliquée
        ]
    }
    actuelles = [
        RegleActuelle(1, Niveau.NECESSAIRE, AVANT),
        RegleActuelle(2, Niveau.IMPORTANT, AVANT),
        RegleActuelle(3, Niveau.IMPORTANT, AVANT),  # existait, non appliquée : ignorée
        RegleActuelle(4, Niveau.FACULTATIF, APRES),  # créée ou modifiée depuis : son niveau
    ]
    assert niveaux_regles_du_calcul(parametres, actuelles, DEBUT_CALCUL) == {
        1: Niveau.FACULTATIF,
        4: Niveau.FACULTATIF,
    }


@pytest.mark.parametrize(
    "regles", [{"id": 1}, [{"code": "R001"}], [{"id": True}], [{"id": 1, "niveau": "fort"}]]
)
def test_copie_des_regles_illisible(regles):
    with pytest.raises(ErreurDonneesInvalides):
        niveaux_regles_du_calcul({"regles": regles}, [RegleActuelle(1, Niveau.NECESSAIRE)])


# --------------------------------------------------------------------------- #
# Une version du jeu minimal
# --------------------------------------------------------------------------- #


def regle(session, code: str) -> m.Regle:
    return session.scalar(select(m.Regle).where(m.Regle.code == code))


@pytest.fixture
def version_tp_le_samedi(jeu_minimal, session) -> int:
    """La version saisie à la main, le TP d'algorithmique de A1-G2a déplacé au samedi 8 h 30."""
    version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    tp = session.scalar(
        select(m.SeanceHebdo)
        .join(m.Besoin, m.SeanceHebdo.besoin_id == m.Besoin.id)
        .where(m.SeanceHebdo.version_id == version_id, m.SeanceHebdo.jour == 1)
        .where(m.SeanceHebdo.debut_min == 13 * 60 + 45)
    )
    tp.jour = 5  # samedi : permis aux TP par le standard, interdit à A1 par R001
    tp.debut_min, tp.fin_min = 8 * 60 + 30, 10 * 60 + 30
    # Règles écrites avant le calcul attaché (`attacher_calcul`) : dates figées, pas l'horloge.
    session.execute(update(m.Regle).values(modifie_le=AVANT))
    session.commit()
    return version_id


def attacher_calcul(session, version_id: int, parametres: dict) -> None:
    version = session.get(m.VersionEdt, version_id)
    calcul = m.Calcul(
        semestre_id=version.semestre_id,
        etat=EtatCalcul.REUSSI,
        demarre_le=DEBUT_CALCUL,
        parametres=parametres,
        contraintes_desactivees=[],
    )
    session.add(calcul)
    session.flush()
    version.calcul_id = calcul.id
    version.origine = OrigineVersion.GENEREE
    session.commit()


def test_version_de_reference_regles_actuelles_a_leur_niveau(version_tp_le_samedi, session):
    r001, r002 = regle(session, "R001"), regle(session, "R002")
    assert niveaux_regles_de_version(session, version_tp_le_samedi) == {
        r001.id: Niveau.NECESSAIRE,
        r002.id: Niveau.IMPORTANT,
    }
    rapport = verifier_version(session, version_tp_le_samedi)
    regles = [a for a in rapport.anomalies if a.code is CodeContrainte.REGLE]
    assert [(a.regle_code, a.gravite) for a in regles] == [("R001", Gravite.ERREUR)]
    assert not rapport.valide
    assert rapport.niveaux_regles == {r001.id: Niveau.NECESSAIRE, r002.id: Niveau.IMPORTANT}
    donnees = rapport.vers_dict()
    (premiere,) = [a for a in donnees["anomalies"] if a["code"] == "REGLE"]
    assert premiere["regle_code"] == "R001" and premiere["regle_id"] == r001.id
    assert premiere["regle_phrase"].startswith("La promotion A1")
    assert donnees["niveaux_regles"][str(r001.id)] == "necessaire"


def test_niveau_choisi_au_calcul(version_tp_le_samedi, session):
    r001 = regle(session, "R001")
    attacher_calcul(
        session, version_tp_le_samedi, {"regles": [{"id": r001.id, "niveau": "facultatif"}]}
    )
    rapport = verifier_version(session, version_tp_le_samedi)
    regles = [a for a in rapport.anomalies if a.code is CodeContrainte.REGLE]
    assert [(a.regle_code, a.gravite, a.niveau) for a in regles] == [
        ("R001", Gravite.INFORMATION, Niveau.FACULTATIF)
    ]
    assert rapport.valide


def test_regle_non_appliquee_au_calcul_ignoree(version_tp_le_samedi, session):
    attacher_calcul(session, version_tp_le_samedi, {"regles": []})
    rapport = verifier_version(session, version_tp_le_samedi)
    assert not [a for a in rapport.anomalies if a.code is CodeContrainte.REGLE]
    assert rapport.niveaux_regles == {}
    # Le standard reprend sa place : le samedi est permis aux TP, rien à signaler non plus.
    assert "JOURS" not in rapport.par_code


def test_contraintes_choisies_et_regles(version_tp_le_samedi, session):
    sans = verifier_version(session, version_tp_le_samedi, ["JOURS"])
    assert sans.anomalies == ()
    avec = verifier_version(session, version_tp_le_samedi, ["JOURS", "REGLE"])
    assert [a.regle_code for a in avec.anomalies] == ["R001"]


def test_modification_qui_enfreint_une_regle(jeu_minimal, session):
    version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    tp = session.scalar(
        select(m.SeanceHebdo).where(
            m.SeanceHebdo.version_id == version_id,
            m.SeanceHebdo.jour == 1,
            m.SeanceHebdo.debut_min == 13 * 60 + 45,
        )
    )
    seance = f"{tp.besoin_id}-{tp.rang}"
    rapport = verifier_modification(
        session, version_id, seance, 5, 8 * 60 + 30, 10 * 60 + 30, tp.salle_id
    )
    assert [(str(a.code), a.regle_code) for a in rapport.anomalies] == [("REGLE", "R001")]
    assert not rapport.valide
    ignoree = verifier_modification(
        session, version_id, seance, 5, 8 * 60 + 30, 10 * 60 + 30, tp.salle_id, niveaux_regles={}
    )
    assert ignoree.valide


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


def calcul_citant_r001_et_une_regle_disparue(session, version_id: int) -> m.Regle:
    """Attache à la version un calcul dont la copie des règles cite R001 et la règle n° 999."""
    r001 = regle(session, "R001")
    attacher_calcul(
        session,
        version_id,
        {"regles": [{"id": r001.id, "niveau": "necessaire"}, {"id": 999, "niveau": "necessaire"}]},
    )
    return r001


@pytest.mark.cas_limite
@pytest.mark.parametrize("disparition", ["desactivee", "supprimee"])
def test_regle_supprimee_ou_desactivee_apres_le_calcul_ignoree_sans_erreur(
    version_tp_le_samedi, session, disparition
):
    """Cas : une règle copiée par le calcul de la version est désactivée ou supprimée ensuite.

    Risque : `KeyError` ou règle fantôme passée au vérificateur : la vérification de toutes
    les anciennes versions casserait.
    """
    r001 = calcul_citant_r001_et_une_regle_disparue(session, version_tp_le_samedi)
    if disparition == "desactivee":
        r001.etat = EtatRegle.INACTIVE
    else:
        session.delete(r001)
    session.commit()

    # R001 et la règle n° 999 ne sont plus actives ; R002 existait avant le calcul sans y
    # être appliquée : aucune règle à vérifier, et aucune erreur.
    assert niveaux_regles_de_version(session, version_tp_le_samedi) == {}
    rapport = verifier_version(session, version_tp_le_samedi)
    assert rapport.niveaux_regles == {}
    assert not [a for a in rapport.anomalies if a.code is CodeContrainte.REGLE]
    assert rapport.valide  # le TP du samedi reste permis par le standard


@pytest.mark.cas_limite
def test_regles_du_calcul_introuvables_nommees_dans_le_journal(
    version_tp_le_samedi, session, caplog
):
    """Cas : la copie des règles d'un calcul cite une règle qui n'est plus active (n° 999).

    Risque : la règle disparaît de la vérification sans laisser de trace : impossible de
    comprendre pourquoi une entorse d'hier n'est plus signalée.
    """
    calcul_citant_r001_et_une_regle_disparue(session, version_tp_le_samedi)
    with caplog.at_level(logging.INFO, logger="edt.services"):
        niveaux_regles_de_version(session, version_tp_le_samedi)
    signalees = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.INFO and "introuvable" in r.getMessage()
    ]
    # Seule la règle n° 999 a disparu (R001 est toujours active).
    assert len(signalees) == 1 and signalees[0].endswith(" : 999")
