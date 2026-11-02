"""Moteur SQLAlchemy et sessions.

Un moteur est créé par URL de base et réutilisé (les fils d'exécution du
serveur et du calcul partagent le même moteur, chacun avec sa session).
Seul SQLite **sur fichier** est pris en charge : une base en mémoire serait
propre à chaque fil d'exécution. Chaque connexion active les clés étrangères,
le mode WAL et un délai d'attente sur verrou.

Transactions : le pilote `sqlite3` de Python n'ouvre une transaction qu'avant
une écriture, si bien qu'un SAVEPOINT émis avant toute écriture ouvrirait
lui-même la transaction et que son RELEASE la validerait, malgré un rollback
englobant. On lui retire donc la main (`isolation_level=None`) et SQLAlchemy
émet le `BEGIN` au début de chaque transaction (recommandation de sa
documentation) : un point de sauvegarde libéré reste annulable par la
transaction qui le contient. Deux modes :

- **`BEGIN` différé**, le défaut : une transaction qui ne fait que lire ne
  prend aucun verrou, et les lecteurs sont servis pendant qu'un verrou
  d'écriture long est tenu (mode WAL) ;
- **`BEGIN IMMEDIATE`** à la demande, pour un écrivain long et connu
  (l'import de données) : `transaction_immediate(session)` en tête de la
  transaction prend le verrou d'écriture d'emblée. Les autres écrivains
  attendent alors le délai de verrou au lieu d'échouer « database is
  locked » (SQLite n'attend pas quand une transaction qui a déjà lu veut
  écrire), et deux imports sont sérialisés.

Usage dans un service appelé hors requête web (CLI, fil de calcul) :

    with session_base() as session:
        ...            # validé à la sortie du bloc, annulé en cas d'erreur

Dans une route web, utiliser la dépendance `edt.web.dependances.session_requete`.
"""

from __future__ import annotations

import logging
import threading
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError, SAWarning
from sqlalchemy.orm import Session, sessionmaker

from edt.config import charger_configuration, url_affichable
from edt.erreurs import ErreurConfiguration

journal = logging.getLogger(__name__)

DELAI_VERROU_MS = 10_000

IDENTIFIANT_MAX = 2**63 - 1
"""Plus grand entier qu'une colonne SQLite accepte ; au-delà, le pilote lève `OverflowError`."""

OPTION_ECRITURE_IMMEDIATE = "edt_ecriture_immediate"
"""Option d'exécution SQLAlchemy : la transaction s'ouvre par `BEGIN IMMEDIATE` (voir l'en-tête)."""

_verrou = threading.Lock()
_moteurs: dict[str, Engine] = {}


def identifiant_valide(valeur: object) -> bool:
    """Vrai si `valeur` peut désigner une ligne : entier strictement positif dans les bornes de SQLite.

    À appeler avant `session.get` sur un identifiant venu de l'extérieur
    (adresse web, option de commande) : un entier hors bornes ferait lever le
    pilote au lieu de répondre « n'existe pas ».
    """
    return (
        isinstance(valeur, int) and not isinstance(valeur, bool) and 0 < valeur <= IDENTIFIANT_MAX
    )


def expliquer_erreur_base(erreur: DatabaseError | Exception) -> str:
    """Phrase française pour une erreur du pilote de base, avec le remède.

    Couvre les pannes d'exploitation : base verrouillée par un autre
    processus, fichier en lecture seule, fichier qui n'est pas une base
    SQLite, disque plein. Sinon, le message du pilote est repris. Accepte
    l'erreur enveloppée par SQLAlchemy comme celle du pilote `sqlite3` brut.
    """
    cause = str(getattr(erreur, "orig", None) or erreur)
    minuscule = cause.lower()
    if "locked" in minuscule or "busy" in minuscule:
        return (
            "La base de données est verrouillée par une autre commande ou un autre processus "
            "(service web, commande « edt » en cours, session sqlite3) : attendre sa fin ou "
            "l'arrêter, puis réessayer (sous Linux, « fuser <fichier de la base> » le nomme)."
        )
    if "readonly" in minuscule or "read-only" in minuscule:
        return (
            "La base de données est en lecture seule : vérifier les droits du fichier de la base "
            "et de son dossier (le fichier -wal et -shm doivent pouvoir y être créés), ou l'espace "
            "disque."
        )
    if "not a database" in minuscule or "malformed" in minuscule:
        return (
            "Le fichier de la base de données n'est pas une base SQLite lisible (fichier "
            "corrompu, tronqué ou remplacé) : restaurer une sauvegarde, ou recréer la base avec "
            "« python -m edt init-db --reset » (les données sont perdues)."
        )
    if "full" in minuscule:
        return "Le disque de la base de données est plein : libérer de l'espace, puis réessayer."
    return f"La base de données est inaccessible : {cause}"


def _preparer_sqlite(connexion_dbapi, _enregistrement) -> None:
    """Réglages appliqués à chaque nouvelle connexion SQLite."""
    # Le pilote ne doit plus ouvrir de transaction lui-même (voir l'en-tête du module).
    connexion_dbapi.isolation_level = None
    curseur = connexion_dbapi.cursor()
    try:
        curseur.execute("PRAGMA foreign_keys=ON")
        curseur.execute("PRAGMA journal_mode=WAL")
        curseur.execute("PRAGMA synchronous=NORMAL")
        curseur.execute(f"PRAGMA busy_timeout={DELAI_VERROU_MS}")
    finally:
        curseur.close()


def _commencer_transaction(connexion) -> None:
    """Ouvre explicitement la transaction SQLite (le pilote ne le fait plus, voir l'en-tête).

    `BEGIN IMMEDIATE` si la connexion porte l'option `OPTION_ECRITURE_IMMEDIATE`
    (`transaction_immediate`), `BEGIN` différé sinon.
    """
    immediate = bool(connexion.get_execution_options().get(OPTION_ECRITURE_IMMEDIATE))
    instruction = "BEGIN IMMEDIATE" if immediate else "BEGIN"
    connexion.exec_driver_sql(instruction)
    journal.debug("Transaction SQLite ouverte : %s", instruction)


def transaction_immediate(session: Session) -> None:
    """Ouvre la transaction de `session` en prenant le verrou d'écriture d'emblée (`BEGIN IMMEDIATE`).

    À appeler **avant la première requête** de la transaction, pour un
    écrivain long et connu (l'import de données) : les autres écrivains
    attendent le délai de verrou au lieu d'échouer aussitôt « database is
    locked » quand une transaction qui a déjà lu veut écrire, et deux
    écrivains longs sont sérialisés. Ce n'est pas le défaut : un `BEGIN`
    différé laisse les lecteurs servis pendant qu'un verrou d'écriture long
    est tenu, et une transaction qui ne fait que lire ne prend aucun verrou.
    La demande vaut pour la transaction en cours ; après validation, la
    connexion revient au pool sans l'option.

    Lève `ErreurConfiguration` si la connexion de la session est déjà
    engagée (une requête a déjà été exécutée) : SQLAlchemy ignore alors
    l'option, le `BEGIN` différé est déjà émis et la transaction resterait
    différée en silence.
    """
    with warnings.catch_warnings():
        # SQLAlchemy avertit (et ignore l'option) si la connexion existe déjà : le cas est
        # contrôlé juste après et devient une erreur, pas un avertissement perdu.
        warnings.simplefilter("ignore", SAWarning)
        connexion = session.connection(execution_options={OPTION_ECRITURE_IMMEDIATE: True})
    if not connexion.get_execution_options().get(OPTION_ECRITURE_IMMEDIATE):
        raise ErreurConfiguration(
            "Le verrou d'écriture immédiat se demande avant la première requête de la "
            "transaction : la connexion de la session est déjà engagée (BEGIN différé déjà "
            "émis). Appeler transaction_immediate(session) en tête du bloc, avant toute lecture."
        )


@contextmanager
def connexion_immediate(moteur: Engine) -> Iterator[Connection]:
    """Connexion dont la transaction s'ouvre par `BEGIN IMMEDIATE`, validée à la sortie du bloc.

    Pour les écrivains longs qui travaillent hors session (création et
    suppression du schéma) : même politique que `transaction_immediate`.
    """
    with moteur.connect() as connexion:
        connexion.execution_options(**{OPTION_ECRITURE_IMMEDIATE: True})
        with connexion.begin():
            yield connexion


def chemin_base(url: str) -> Path | None:
    """Chemin du fichier SQLite désigné par `url`, ou `None` si l'URL n'est pas lisible."""
    try:
        url_analysee = make_url(url)
    except Exception:  # URL illisible : `creer_moteur` le dira
        return None
    if url_analysee.get_backend_name() != "sqlite" or not url_analysee.database:
        return None
    return Path(url_analysee.database)


def creer_moteur(url: str) -> Engine:
    """Crée un moteur pour `url` (sans le mettre en cache).

    Seul SQLite sur fichier est accepté ; le dossier parent est créé s'il
    manque. Toute autre URL lève `ErreurConfiguration` avec le remède.
    """
    try:
        url_analysee = make_url(url)
    except Exception as erreur:
        raise ErreurConfiguration(f"EDT_DB_URL invalide ({url!r}) : {erreur}") from erreur

    if url_analysee.get_backend_name() != "sqlite":
        raise ErreurConfiguration(
            "EDT_DB_URL : seul SQLite est pris en charge, avec une URL de la forme "
            f"« sqlite:///chemin/edt.db » (lu : {url_affichable(url_analysee)})."
        )
    chemin = url_analysee.database
    if not chemin or chemin == ":memory:":
        raise ErreurConfiguration(
            "EDT_DB_URL : un fichier est requis (« sqlite:///chemin/edt.db »). Une base en "
            "mémoire (« sqlite:// » ou « :memory: ») serait propre à chaque fil d'exécution : le "
            "serveur, le calcul et les commandes ne verraient pas les mêmes données "
            f"(lu : {url!r})."
        )
    try:
        Path(chemin).parent.mkdir(parents=True, exist_ok=True)
    except OSError as erreur:
        raise ErreurConfiguration(
            f"Impossible de créer le dossier de la base « {Path(chemin).parent} » : {erreur}"
        ) from erreur

    moteur = create_engine(url_analysee, connect_args={"check_same_thread": False})
    event.listen(moteur, "connect", _preparer_sqlite)
    event.listen(moteur, "begin", _commencer_transaction)
    return moteur


def obtenir_moteur(url: str | None = None) -> Engine:
    """Moteur partagé pour `url` (par défaut : `EDT_DB_URL`)."""
    url = url or charger_configuration().db_url
    with _verrou:
        moteur = _moteurs.get(url)
        if moteur is None:
            moteur = creer_moteur(url)
            _moteurs[url] = moteur
            journal.debug("Moteur de base créé pour %s", url_affichable(url))
        return moteur


def fabrique_sessions(url: str | None = None) -> sessionmaker[Session]:
    """Fabrique de sessions liée au moteur partagé.

    `expire_on_commit=False` : les objets restent lisibles après validation,
    ce qui évite des requêtes surprises dans les gabarits.
    """
    return sessionmaker(bind=obtenir_moteur(url), expire_on_commit=False)


@contextmanager
def session_base(url: str | None = None) -> Iterator[Session]:
    """Ouvre une session : validée à la sortie du bloc, annulée si une erreur survient."""
    session = fabrique_sessions(url)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def liberer_moteurs() -> None:
    """Ferme toutes les connexions et oublie les moteurs (tests, arrêt propre)."""
    with _verrou:
        for moteur in _moteurs.values():
            moteur.dispose()
        _moteurs.clear()
