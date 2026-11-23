"""Moteur de base : URL acceptées, transactions SQLite, identifiants, verrous d'écriture."""

from __future__ import annotations

import sqlite3
import threading
import time
import warnings

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DatabaseError, OperationalError, SAWarning

from edt.db import moteur as module_moteur
from edt.db.modeles import Campus
from edt.db.moteur import (
    creer_moteur,
    expliquer_erreur_base,
    identifiant_valide,
    session_base,
    transaction_immediate,
)
from edt.erreurs import ErreurConfiguration


def _codes_campus() -> set[str]:
    with session_base() as session:
        return set(session.scalars(select(Campus.code)))


def test_une_url_sqlite_sur_fichier_est_acceptee(environnement):
    moteur = creer_moteur(f"sqlite:///{(environnement / 'sous' / 'dossier' / 'edt.db').as_posix()}")
    with moteur.connect() as connexion:
        assert connexion.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
    moteur.dispose()
    assert (environnement / "sous" / "dossier").is_dir()  # créé au besoin


@pytest.mark.cas_limite
def test_un_point_de_sauvegarde_libere_reste_annule_par_la_transaction_englobante(base):
    """Cas : un service ouvre un point de sauvegarde avant toute écriture, écrit, le libère,
    puis la route lève (rollback de la transaction).

    Risque : le pilote sqlite3 n'ouvre une transaction qu'avant une écriture ; sans le
    contournement du moteur, le RELEASE du SAVEPOINT validerait l'insertion malgré le rollback.
    """
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


@pytest.mark.cas_limite
def test_un_identifiant_hors_des_entiers_sqlite_est_invalide():
    """Cas : un identifiant venu d'une adresse ou d'une option vaut 0, -1, 2**63, 10**30…

    Risque : `session.get` ferait lever `OverflowError` au pilote (500 / code 2) au lieu
    de répondre « n'existe pas » ; la garde `identifiant_valide` doit tout refuser.
    """
    assert identifiant_valide(1) and identifiant_valide(2**63 - 1)
    for valeur in (0, -1, 2**63, 10**30, True, False, "3", None, 2.0):
        assert not identifiant_valide(valeur), valeur


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("cause", "mot"),
    [
        ("database is locked", "verrouillée"),
        ("attempt to write a readonly database", "lecture seule"),
        ("file is not a database", "n'est pas une base SQLite"),
        ("database or disk is full", "disque"),
        ("no such table: semestre", "init-db"),
        ("une autre cause", "inaccessible : une autre cause"),
        (
            "RIGHT and FULL OUTER JOINs are not currently supported",
            "inaccessible : RIGHT and FULL",
        ),
    ],
)
def test_les_erreurs_du_pilote_sont_expliquees_avec_leur_remede(cause, mot):
    """Cas : le pilote SQLite refuse une opération (verrou, lecture seule, fichier corrompu,
    disque plein).

    Risque : un message technique sans remède ; l'opérateur pourrait effacer la base pour un
    simple verrou, ou libérer du disque pour une requête que la version de SQLite refuse
    (« FULL OUTER JOIN » ne veut pas dire « disque plein »). Chaque cause est enveloppée
    dans la classe que le pilote lève vraiment : `DatabaseError` pour un fichier qui n'est
    pas une base, `OperationalError` sinon.
    """
    if "not a database" in cause:
        erreur = DatabaseError("SELECT ...", {}, sqlite3.DatabaseError(cause))
    else:
        erreur = OperationalError("INSERT ...", {}, sqlite3.OperationalError(cause))
    assert mot in expliquer_erreur_base(erreur)


@pytest.mark.cas_limite
def test_une_table_absente_est_expliquee_comme_une_base_non_initialisee():
    """Cas : une commande lit une base jamais initialisée ; le pilote répond
    « no such table: semestre ».

    Risque : le message « base inaccessible » oriente vers une panne de droits ou un verrou,
    sans remède ; il faut nommer la table absente et indiquer « python -m edt init-db ».
    """
    erreur = OperationalError("SELECT ...", {}, sqlite3.OperationalError("no such table: semestre"))
    message = expliquer_erreur_base(erreur)
    assert "inaccessible" not in message
    assert "« semestre »" in message and "python -m edt init-db" in message


@pytest.mark.cas_limite
def test_une_ecriture_attend_la_fin_d_une_transaction_courte(base):
    """Cas : deux fils écrivent presque en même temps (fil de calcul et requête web).

    Risque : sans `busy_timeout`, la seconde écriture échouerait aussitôt « database is
    locked » et la saisie ou la version serait perdue ; elle doit attendre puis réussir.
    """
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
    with session_base() as session:  # attend le verrou au lieu d'échouer
        session.add(Campus(code="RAPIDE", nom="Fil principal"))
    duree = time.perf_counter() - debut
    fil.join(5)

    assert _codes_campus() >= {"LENT", "RAPIDE"}
    assert 0.3 <= duree < module_moteur.DELAI_VERROU_MS / 1000


@pytest.mark.cas_limite
def test_sous_un_verrou_tenu_l_ecriture_echoue_apres_le_delai_et_les_lecteurs_sont_servis(
    base, compter, delai_verrou_court, verrou_ecriture
):
    """Cas : un autre processus (sqlite3, commande) tient le verrou d'écriture longtemps.

    Risque : sans mode WAL les lectures seraient bloquées aussi (/sante rouge à chaque
    import) ; sans délai borné l'écriture attendrait sans fin. Elle doit échouer après le
    délai, « locked », sans rien écrire, pendant que les lecteurs sont servis.
    """
    with verrou_ecriture():
        debut = time.perf_counter()
        with pytest.raises(OperationalError, match="locked"), session_base() as session:
            session.add(Campus(code="BLOQUE", nom="Jamais"))
            session.flush()
        assert time.perf_counter() - debut >= delai_verrou_court / 1000 - 0.05
        assert compter(Campus) == 0  # les lecteurs passent pendant le verrou
    assert "BLOQUE" not in _codes_campus()


@pytest.mark.cas_limite
def test_transaction_immediate_prend_le_verrou_d_ecriture_des_l_ouverture(base, environnement):
    """Cas : un écrivain long (l'import) demande le verrou d'écriture dès l'ouverture de sa
    transaction, avant toute lecture.

    Risque : avec le BEGIN différé par défaut, une transaction qui a déjà lu et veut écrire
    échoue aussitôt « database is locked » face à un autre écrivain (SQLite n'attend pas), et
    deux imports pourraient commencer en même temps ; les lecteurs doivent rester servis.
    """
    chemin = environnement / "edt.db"
    with session_base() as session:
        transaction_immediate(session)
        autre_ecrivain = sqlite3.connect(chemin, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                autre_ecrivain.execute("BEGIN IMMEDIATE")
        finally:
            autre_ecrivain.close()
        lecteur = sqlite3.connect(chemin, timeout=0)  # mode WAL : lire reste possible
        try:
            assert lecteur.execute("SELECT count(*) FROM campus").fetchone()[0] == 0
        finally:
            lecteur.close()
        session.add(Campus(code="IMM", nom="Immédiat"))
    # Verrou rendu à la validation ; l'écriture a bien eu lieu.
    autre_ecrivain = sqlite3.connect(chemin, timeout=0)
    try:
        autre_ecrivain.execute("BEGIN IMMEDIATE")
        autre_ecrivain.rollback()
    finally:
        autre_ecrivain.close()
    assert "IMM" in _codes_campus()


@pytest.mark.cas_limite
def test_transaction_immediate_apres_une_requete_est_une_erreur_de_configuration(base):
    """Cas : le verrou immédiat est demandé après une première requête de la transaction.

    Risque : SQLAlchemy ignore alors l'option avec un simple avertissement : la transaction
    resterait différée en silence et l'import croirait tenir le verrou.
    """
    with session_base() as session:
        session.scalar(select(func.count()).select_from(Campus))
        with pytest.raises(ErreurConfiguration, match="avant la première requête"):
            transaction_immediate(session)
    # Demandée en tête puis répétée dans la même transaction : sans effet, pas une erreur.
    with session_base() as session:
        transaction_immediate(session)
        transaction_immediate(session)
        session.add(Campus(code="DEUX", nom="Deux fois"))
    assert "DEUX" in _codes_campus()


@pytest.mark.cas_limite
def test_transaction_immediate_sans_avertissement_de_sqlalchemy(base, environnement):
    """Cas : verrou immédiat demandé deux fois dans la même transaction (route web d'écriture,
    puis service d'import), y compris dans un point de sauvegarde, ou après une première requête.

    Risque : avertissement « execution_options ignored » à chaque import par l'écran, qui
    encombre la sortie et fait douter que le verrou soit pris ; le verrou doit rester tenu.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        with session_base() as session:
            transaction_immediate(session)
            point = session.begin_nested()
            transaction_immediate(session)
            autre_ecrivain = sqlite3.connect(environnement / "edt.db", timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    autre_ecrivain.execute("BEGIN IMMEDIATE")
            finally:
                autre_ecrivain.close()
            point.rollback()
        with session_base() as session:
            session.scalar(select(func.count()).select_from(Campus))
            with pytest.raises(ErreurConfiguration, match="avant la première requête"):
                transaction_immediate(session)


@pytest.mark.cas_limite
def test_une_transaction_qui_lit_ne_bloque_pas_les_autres_ecrivains(base, environnement):
    """Cas : une transaction ordinaire a lu (BEGIN différé) pendant qu'un autre processus veut
    écrire.

    Risque : si le défaut devenait IMMEDIATE, chaque page lue tiendrait le verrou d'écriture et
    bloquerait les écrivains ; la politique par défaut doit rester le BEGIN différé.
    """
    with session_base() as session:
        session.scalar(select(func.count()).select_from(Campus))  # lecture : aucun verrou pris
        autre_ecrivain = sqlite3.connect(environnement / "edt.db", timeout=0)
        try:
            autre_ecrivain.execute("BEGIN IMMEDIATE")
            autre_ecrivain.rollback()
        finally:
            autre_ecrivain.close()
