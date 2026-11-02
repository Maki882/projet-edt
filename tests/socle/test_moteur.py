"""Moteur de base : URL acceptées, transactions SQLite, identifiants, verrous d'écriture."""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from typer.testing import CliRunner

from edt import cli
from edt.db import moteur as module_moteur
from edt.db.modeles import Campus
from edt.db.moteur import (
    creer_moteur,
    expliquer_erreur_base,
    identifiant_valide,
    liberer_moteurs,
    obtenir_moteur,
    session_base,
)
from edt.erreurs import ErreurConfiguration


def _codes_campus() -> set[str]:
    with session_base() as session:
        return set(session.scalars(select(Campus.code)))


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_une_url_sqlite_sans_fichier_est_refusee(url, monkeypatch):
    with pytest.raises(ErreurConfiguration, match="EDT_DB_URL") as capture:
        creer_moteur(url)
    assert "fichier est requis" in str(capture.value)

    monkeypatch.setenv("EDT_DB_URL", url)
    liberer_moteurs()
    with pytest.raises(ErreurConfiguration, match="fichier est requis"):
        obtenir_moteur()
    resultat = CliRunner().invoke(cli.app, ["init-db"])
    assert resultat.exit_code == cli.CODE_ERREUR_METIER
    assert "EDT_DB_URL" in resultat.output and "Traceback" not in resultat.output


def test_une_url_sqlite_sur_fichier_est_acceptee(environnement):
    moteur = creer_moteur(f"sqlite:///{(environnement / 'sous' / 'dossier' / 'edt.db').as_posix()}")
    with moteur.connect() as connexion:
        assert connexion.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    moteur.dispose()
    assert (environnement / "sous" / "dossier").is_dir()  # créé au besoin


@pytest.mark.parametrize(
    "url", ["postgresql://utilisateur:secret@localhost/edt", "nimporte://quoi", "sqlit:///x.db"]
)
def test_une_url_sans_pilote_ou_de_schema_inconnu_est_une_erreur_de_configuration(url, monkeypatch):
    with pytest.raises(ErreurConfiguration, match="seul SQLite") as capture:
        creer_moteur(url)
    message = str(capture.value)
    assert "EDT_DB_URL" in message and "secret" not in message  # le mot de passe reste caché

    monkeypatch.setenv("EDT_DB_URL", "postgresql://x/y")
    liberer_moteurs()
    resultat = CliRunner().invoke(cli.app, ["doctor"])
    assert resultat.exit_code == 1
    assert "ECHEC" in resultat.output and "seul SQLite" in resultat.output
    assert "Traceback" not in resultat.output


def test_un_point_de_sauvegarde_libere_reste_annule_par_la_transaction_englobante(base):
    # Le pilote sqlite3 n'ouvre une transaction qu'avant une écriture : sans le contournement
    # du moteur, le RELEASE du SAVEPOINT validerait l'insertion malgré le rollback qui suit.
    with session_base() as session:
        point = session.begin_nested()
        session.add(Campus(code="SP2", nom="Point de sauvegarde"))
        session.flush()
        point.commit()
        session.rollback()
    assert "SP2" not in _codes_campus()

    # Jumeau : un point annulé disparaît, ce qui suit reste validé par la transaction.
    with session_base() as session:
        point = session.begin_nested()
        session.add(Campus(code="SP3", nom="Annulé"))
        session.flush()
        point.rollback()
        session.add(Campus(code="SP4", nom="Validé"))
    codes = _codes_campus()
    assert "SP3" not in codes and "SP4" in codes


def test_un_identifiant_hors_des_entiers_sqlite_est_invalide():
    assert identifiant_valide(1) and identifiant_valide(2**63 - 1)
    for valeur in (0, -1, 2**63, 10**30, True, False, "3", None, 2.0):
        assert not identifiant_valide(valeur), valeur


@pytest.mark.parametrize(
    ("cause", "mot"),
    [
        ("database is locked", "verrouillée"),
        ("attempt to write a readonly database", "lecture seule"),
        ("file is not a database", "n'est pas une base SQLite"),
        ("database or disk is full", "disque"),
        ("une autre cause", "inaccessible : une autre cause"),
    ],
)
def test_les_erreurs_du_pilote_sont_expliquees_avec_leur_remede(cause, mot):
    erreur = OperationalError("INSERT ...", {}, sqlite3.OperationalError(cause))
    assert mot in expliquer_erreur_base(erreur)


def test_une_ecriture_attend_la_fin_d_une_transaction_courte(base):
    pret, lacher = threading.Event(), threading.Event()

    def ecrivain_lent() -> None:
        with session_base() as session:
            session.add(Campus(code="LENT", nom="Fil A"))
            session.flush()  # le verrou d'écriture est pris jusqu'à la validation
            pret.set()
            lacher.wait(5)

    fil = threading.Thread(target=ecrivain_lent, name="ecrivain-lent")
    fil.start()
    assert pret.wait(5)
    threading.Timer(0.5, lacher.set).start()
    debut = time.perf_counter()
    with session_base() as session:  # attend le verrou (busy_timeout) au lieu d'échouer
        session.add(Campus(code="RAPIDE", nom="Fil principal"))
    duree = time.perf_counter() - debut
    fil.join(5)

    assert _codes_campus() >= {"LENT", "RAPIDE"}
    assert 0.3 <= duree < module_moteur.DELAI_VERROU_MS / 1000


def test_sous_un_verrou_tenu_l_ecriture_echoue_apres_le_delai_et_les_lecteurs_sont_servis(
    base, environnement, monkeypatch
):
    monkeypatch.setattr(module_moteur, "DELAI_VERROU_MS", 400)
    liberer_moteurs()  # les nouvelles connexions prennent le délai réduit
    brute = sqlite3.connect(environnement / "edt.db")
    brute.execute("BEGIN IMMEDIATE")  # un autre processus tient le verrou d'écriture
    try:
        debut = time.perf_counter()
        with pytest.raises(OperationalError, match="locked"), session_base() as session:
            session.add(Campus(code="BLOQUE", nom="Jamais"))
            session.flush()
        assert time.perf_counter() - debut >= 0.35
        with session_base() as session:  # mode WAL : lire reste possible pendant le verrou
            assert session.scalar(select(func.count()).select_from(Campus)) == 0
    finally:
        brute.rollback()
        brute.close()
    assert "BLOQUE" not in _codes_campus()
