"""Niveaux d'un calcul et d'une version (`edt.services.niveaux`) et leur usage par le service."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from edt.db import modeles as m
from edt.domaine.contraintes import CodeContrainte, Niveau, niveaux_par_defaut
from edt.domaine.enumerations import EtatCalcul, Gravite, OrigineVersion
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services.niveaux import niveaux_de_version, niveaux_du_calcul
from edt.services.verification import verifier_modification, verifier_version

from .test_service import enregistrer_version

MAX, PAUSE = CodeContrainte.MAX_ENSEIGNANT, CodeContrainte.PAUSE


# --------------------------------------------------------------------------- #
# niveaux_du_calcul (pur)
# --------------------------------------------------------------------------- #


def test_niveaux_du_calcul_lus_dans_les_parametres():
    niveaux = niveaux_du_calcul({"niveaux": {"PAUSE": "facultatif", "JOURS": "important"}}, [])
    attendus = niveaux_par_defaut() | {
        PAUSE: Niveau.FACULTATIF,
        CodeContrainte.JOURS: Niveau.IMPORTANT,
    }
    assert niveaux == attendus


def test_niveaux_du_calcul_ancien_avec_contraintes_desactivees():
    niveaux = niveaux_du_calcul({"temps_limite_s": 60}, ["PAUSE", "DEJEUNER"])
    assert PAUSE not in niveaux and CodeContrainte.DEJEUNER not in niveaux
    assert niveaux[MAX] is Niveau.IMPORTANT  # niveau par défaut


def test_niveaux_du_calcul_par_defaut():
    assert niveaux_du_calcul(None, None) == niveaux_par_defaut()
    assert niveaux_du_calcul({}, []) == niveaux_par_defaut()


def test_niveaux_et_contraintes_desactivees_ensemble():
    niveaux = niveaux_du_calcul({"niveaux": {"PAUSE": "facultatif"}}, ["DEJEUNER"])
    assert niveaux[PAUSE] is Niveau.FACULTATIF
    assert CodeContrainte.DEJEUNER not in niveaux


@pytest.mark.parametrize(
    "niveaux", [["PAUSE"], {"PAS_UN_CODE": "important"}, {"PAUSE": "indispensable"}]
)
def test_niveaux_du_calcul_illisibles(niveaux):
    with pytest.raises(ErreurDonneesInvalides):
        niveaux_du_calcul({"niveaux": niveaux}, [])


# --------------------------------------------------------------------------- #
# niveaux_de_version (base)
# --------------------------------------------------------------------------- #


def creer_calcul(session, semestre_id: int, parametres: dict, desactivees: list[str]) -> int:
    calcul = m.Calcul(
        semestre_id=semestre_id,
        etat=EtatCalcul.REUSSI,
        demarre_le=dt.datetime(2026, 10, 6, 9, 0),
        parametres=parametres,
        contraintes_desactivees=desactivees,
    )
    session.add(calcul)
    session.flush()
    return calcul.id


def creer_version(session, semestre_id: int, numero: int, calcul_id: int | None) -> int:
    version = m.VersionEdt(
        semestre_id=semestre_id,
        numero=numero,
        libelle=f"Version {numero}",
        origine=OrigineVersion.REFERENCE if calcul_id is None else OrigineVersion.GENEREE,
        active=False,
        calcul_id=calcul_id,
    )
    session.add(version)
    session.flush()
    return version.id


def test_niveaux_de_version(jeu_minimal, session):
    semestre = jeu_minimal.semestre_id
    avec_niveaux = creer_version(
        session,
        semestre,
        1,
        creer_calcul(session, semestre, {"niveaux": {"PAUSE": "facultatif"}}, []),
    )
    ancienne = creer_version(
        session, semestre, 2, creer_calcul(session, semestre, {"graine": 0}, ["MAX_ENSEIGNANT"])
    )
    reference = creer_version(session, semestre, 3, None)

    assert niveaux_de_version(session, avec_niveaux)[PAUSE] is Niveau.FACULTATIF
    assert MAX not in niveaux_de_version(session, ancienne)
    assert niveaux_de_version(session, reference) == niveaux_par_defaut()
    with pytest.raises(ErreurIntrouvable):
        niveaux_de_version(session, 999)


# --------------------------------------------------------------------------- #
# verifier_version sans argument : niveaux de la version
# --------------------------------------------------------------------------- #


@pytest.fixture
def version_maximum_depasse(jeu_minimal, session) -> int:
    """Version saisie à la main où M. Bernard dépasse un maximum hebdomadaire de 4 h."""
    version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    bernard = session.scalar(select(m.Enseignant).where(m.Enseignant.identifiant == "ENS002"))
    bernard.max_minutes_semaine = 4 * 60
    session.commit()
    return version_id


def attacher_calcul(session, version_id: int, parametres: dict, desactivees: list[str]) -> None:
    version = session.get(m.VersionEdt, version_id)
    version.calcul_id = creer_calcul(session, version.semestre_id, parametres, desactivees)
    version.origine = OrigineVersion.GENEREE
    session.commit()


def test_verifier_version_lit_les_niveaux_du_calcul(version_maximum_depasse, session):
    attacher_calcul(
        session, version_maximum_depasse, {"niveaux": {"MAX_ENSEIGNANT": "facultatif"}}, []
    )
    rapport = verifier_version(session, version_maximum_depasse)
    (anomalie,) = rapport.anomalies
    assert (anomalie.code, anomalie.gravite, anomalie.niveau) == (
        MAX,
        Gravite.INFORMATION,
        Niveau.FACULTATIF,
    )
    assert anomalie.message.startswith("Entorse à une règle facultative : M. Bernard dépasse")
    assert rapport.valide and rapport.nb_informations == 1
    assert rapport.niveaux[MAX] is Niveau.FACULTATIF
    assert rapport.vers_dict()["niveaux"]["MAX_ENSEIGNANT"] == "facultatif"
    assert rapport.vers_dict()["anomalies"][0]["niveau"] == "facultatif"


def test_verifier_version_de_reference_niveaux_par_defaut(version_maximum_depasse, session):
    rapport = verifier_version(session, version_maximum_depasse)
    (anomalie,) = rapport.anomalies
    assert (anomalie.gravite, anomalie.niveau) == (Gravite.AVERTISSEMENT, Niveau.IMPORTANT)
    assert rapport.valide and rapport.nb_avertissements == 1


def test_verifier_version_calcul_ancien_contrainte_desactivee(version_maximum_depasse, session):
    attacher_calcul(session, version_maximum_depasse, {}, ["MAX_ENSEIGNANT"])
    rapport = verifier_version(session, version_maximum_depasse)
    assert rapport.anomalies == ()
    assert "MAX_ENSEIGNANT" not in rapport.contraintes


def test_verifier_version_niveaux_ou_contraintes_explicites(version_maximum_depasse, session):
    attacher_calcul(
        session, version_maximum_depasse, {"niveaux": {"MAX_ENSEIGNANT": "facultatif"}}, []
    )
    explicites = verifier_version(
        session, version_maximum_depasse, niveaux={MAX: Niveau.NECESSAIRE}
    )
    assert [a.gravite for a in explicites.anomalies] == [Gravite.ERREUR]
    assert explicites.niveaux == {MAX: Niveau.NECESSAIRE}
    en_dur = verifier_version(session, version_maximum_depasse, ["MAX_ENSEIGNANT"])
    assert [(a.gravite, a.niveau) for a in en_dur.anomalies] == [(Gravite.ERREUR, None)]
    assert en_dur.niveaux is None


def test_modification_entorse_nouvelle_a_une_regle_importante(jeu_minimal, session):
    # Mme Martin, 2 h par jour au plus. Déplacer son TD du lundi au jeudi 10 h 45 la fait
    # dépasser son maximum la semaine du DST (2 h + 3 h) : entorse nouvelle à MAX_ENSEIGNANT,
    # importante par défaut, donc un avertissement qui ne bloque pas la modification.
    version_id = enregistrer_version(session, jeu_minimal.semestre_id)
    martin = session.scalar(select(m.Enseignant).where(m.Enseignant.identifiant == "ENS001"))
    martin.max_minutes_jour = 2 * 60
    session.commit()
    td = session.scalar(
        select(m.SeanceHebdo).where(
            m.SeanceHebdo.jour == 0, m.SeanceHebdo.debut_min == 13 * 60 + 45
        )
    )
    seance, jeudi, debut = f"{td.besoin_id}-{td.rang}", 3, 10 * 60 + 45

    rapport = verifier_modification(
        session, version_id, seance, jeudi, debut, debut + 120, td.salle_id
    )
    (anomalie,) = rapport.anomalies
    assert (anomalie.code, anomalie.gravite, anomalie.niveau) == (
        MAX,
        Gravite.AVERTISSEMENT,
        Niveau.IMPORTANT,
    )
    assert anomalie.message.startswith("Entorse à une règle importante : Mme Martin dépasse")
    assert rapport.valide

    en_dur = verifier_modification(
        session,
        version_id,
        seance,
        jeudi,
        debut,
        debut + 120,
        td.salle_id,
        contraintes_actives=["MAX_ENSEIGNANT"],
    )
    assert not en_dur.valide
