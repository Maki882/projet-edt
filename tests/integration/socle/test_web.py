"""Application web du socle : accueil, santé, erreurs lisibles, mécanisme d'extension."""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest
from fastapi import APIRouter, Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from starlette.exceptions import HTTPException
from tests.outils.socle.extension_web import NOM_MODULE_ESSAI

from edt.db.initialisation import supprimer_schema
from edt.db.modeles import Calcul, Campus
from edt.db.moteur import liberer_moteurs, obtenir_moteur, session_base
from edt.domaine.enumerations import EtatCalcul
from edt.erreurs import ErreurRegleMetier
from edt.services import generation
from edt.web import app as module_app
from edt.web.dependances import SessionRequete
from edt.web.gabarits import rediriger
from edt.web.menu import SECTION_REFERENTIELS, EntreeMenu


def _journal(dossier: Path) -> str:
    return (dossier / "logs" / "edt.log").read_text(encoding="utf-8")


def _app_etendue(monkeypatch):
    """Application à laquelle un module de routes d'essai est ajouté par la découverte.

    Les routes d'essai provoquent ce qu'une vraie route pourrait faire : une
    panne, un refus métier, une erreur du pilote de base (`/essai/panne-base?cause=…`),
    une erreur de programmation signalée par le pilote, un envoi trop volumineux,
    une écriture (`POST /essai/campus?code=…`).
    """
    routeur = APIRouter(prefix="/essai")

    @routeur.get("/panne")
    def panne():
        raise RuntimeError("détail technique à ne pas montrer")

    @routeur.get("/refus")
    def refus():
        raise ErreurRegleMetier("Cette salle est encore utilisée.", ["par le besoin n° 12"])

    @routeur.get("/panne-base")
    def panne_base(cause: str):
        raise OperationalError("UPDATE parametre ...", {}, sqlite3.OperationalError(cause))

    @routeur.get("/requete-fausse")
    def requete_fausse():
        raise ProgrammingError("SELECT ?", (), sqlite3.ProgrammingError("Incorrect bindings"))

    @routeur.get("/trop-gros")
    def trop_gros():
        raise HTTPException(status_code=413)

    @routeur.post("/campus")
    def creer_campus(request: Request, session: SessionRequete, code: str):
        session.add(Campus(code=code, nom=f"Campus {code}"))
        return rediriger(request, "/", f"Campus {code} créé.")

    module = types.ModuleType(NOM_MODULE_ESSAI)
    module.routeur = routeur
    module.MENU = [EntreeMenu("Écran d'essai", "/essai", section=SECTION_REFERENTIELS, ordre=150)]
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, module)
    reels = module_app.modules_de_routes()
    monkeypatch.setattr(module_app, "modules_de_routes", lambda: [*reels, NOM_MODULE_ESSAI])
    return module_app.creer_app()


@pytest.fixture
def client_etendu(base, monkeypatch):
    """Client de l'application étendue (`_app_etendue`)."""
    return TestClient(_app_etendue(monkeypatch))


def _calcul_reste_en_cours(semestre_id: int) -> int:
    """Une ligne « calcul en cours » laissée par un processus disparu."""
    with session_base() as session:
        calcul = Calcul(
            semestre_id=semestre_id,
            etat=EtatCalcul.EN_COURS,
            demarre_le=dt.datetime(2026, 10, 5, 8, 0),
            statistiques={"pid": 999_999},
        )
        session.add(calcul)
        session.flush()
        return calcul.id


def test_accueil_tait_une_base_en_bon_etat(jeu_minimal, client):
    reponse = client.get("/")

    assert reponse.status_code == 200
    assert reponse.headers["X-Request-ID"]
    page = reponse.text
    assert "Données fictives" not in page  # pas de bandeau : les données sont celles du projet
    # Tout va bien : ni bloc sur la base, ni emplacement, ni environnement.
    assert "État de la base de données" not in page
    assert "Emplacement" not in page and "Environnement" not in page
    assert "Contenu</h2>" not in page  # pas de compteurs d'entités sur l'accueil
    assert 'href="/" class="actif"' in page  # l'entrée de menu « Accueil » est active


def test_accueil_reste_lisible_sans_base_initialisee(environnement):
    client = TestClient(module_app.creer_app())

    reponse = client.get("/")
    assert reponse.status_code == 200
    assert "pas encore initialisée" in reponse.text
    # Le chargement d'un établissement fictif initialise la base : proposé à côté d'init-db.
    assert '<a href="/jeux">charger un établissement fictif</a> (la base est préparée' in reponse.text

    sante = client.get("/sante")
    assert sante.status_code == 503
    assert sante.json()["statut"] == "degrade"


def test_sante(client):
    reponse = client.get("/sante")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["statut"] == "ok"
    assert corps["base"] == {
        "accessible": True,
        "initialisee": True,
        "inscriptible": True,
        "mode_journal": "wal",
        "ecarts_schema": [],
    }


def test_fichiers_statiques_embarques(client):
    assert client.get("/static/css/app.css").status_code == 200
    htmx = client.get("/static/js/htmx.min.js")
    assert htmx.status_code == 200
    assert "htmx" in htmx.text and len(htmx.text) > 10_000


def test_erreur_inattendue_page_lisible_et_trace_journalisee(client_etendu, environnement):
    reponse = client_etendu.get("/essai/panne")

    assert reponse.status_code == 500
    id_requete = reponse.headers["X-Request-ID"]
    assert "Une erreur inattendue" in reponse.text
    assert id_requete in reponse.text  # la référence est affichée à l'utilisateur
    assert "Traceback" not in reponse.text
    assert "détail technique" not in reponse.text

    journal = _journal(environnement)
    assert f"req={id_requete}" in journal
    assert "Traceback" in journal
    assert "RuntimeError: détail technique à ne pas montrer" in journal


def test_erreur_metier_traduite_en_message(client_etendu):
    reponse = client_etendu.get("/essai/refus")

    assert reponse.status_code == 409
    assert "Cette salle est encore utilisée." in reponse.text
    assert "par le besoin n° 12" in reponse.text


def test_erreur_rendue_en_fragment_pour_htmx_et_en_json(client_etendu):
    fragment = client_etendu.get("/essai/refus", headers={"HX-Request": "true"})
    assert fragment.status_code == 409
    assert "<html" not in fragment.text and "message-erreur" in fragment.text

    donnees = client_etendu.get("/essai/refus", headers={"Accept": "application/json"})
    assert donnees.json()["message"] == "Cette salle est encore utilisée."


def test_page_inconnue(client):
    reponse = client.get("/n-existe-pas")
    assert reponse.status_code == 404
    assert "Page introuvable" in reponse.text and "Vérifier l&#39;adresse" in reponse.text
    # La référence reste, pour le diagnostic, sans parler d'incident pour une simple erreur.
    assert f"Référence : <code>{reponse.headers['X-Request-ID']}</code>" in reponse.text
    assert "Référence de l&#39;incident" not in reponse.text


def test_pied_de_page_sans_reference_de_requete(jeu_minimal, client):
    reponse = client.get("/")
    pied = reponse.text.split('<footer class="pied">', 1)[1].split("</footer>", 1)[0]
    assert "requête" not in pied and reponse.headers["X-Request-ID"] not in reponse.text


def test_saisie_invalide_nomme_le_champ_sans_nom_technique(jeu_minimal, client):
    reponse = client.get("/emploi-du-temps", params={"version_id": "abc"})
    assert reponse.status_code == 422
    assert "Version : valeur manquante ou invalide" in reponse.text
    assert "version_id" not in reponse.text.split("<main", 1)[1]
    assert "Revenir à la page précédente et recommencer." in reponse.text


def test_module_de_routes_decouvert_avec_son_menu(client_etendu):
    page = client_etendu.get("/").text
    assert "Référentiels" in page
    assert '<a href="/essai">Écran d&#39;essai</a>' in page


def test_session_validee_avant_la_redirection_et_message_affiche(client_etendu):
    reponse = client_etendu.post("/essai/campus", params={"code": "ZZ"}, follow_redirects=False)
    assert reponse.status_code == 303
    with session_base() as session:
        assert session.scalar(select(Campus.code)) == "ZZ"  # déjà validé à ce stade

    assert "Campus ZZ créé." in client_etendu.get("/").text
    assert "Campus ZZ créé." not in client_etendu.get("/").text  # affiché une seule fois

    # Un doublon viole l'unicité : refus expliqué, rien d'enregistré.
    doublon = client_etendu.post("/essai/campus", params={"code": "ZZ"}, follow_redirects=False)
    assert doublon.status_code == 409
    assert "Opération refusée" in doublon.text


# --------------------------------------------------------------------------- #
# Cas limites : cycle de vie du serveur, base corrompue ou verrouillée, pannes du pilote
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_cycle_de_vie_du_serveur_clot_les_calculs_interrompus_et_journalise(
    jeu_minimal, environnement
):
    """Cas : démarrage puis arrêt du serveur sur une base prête, avec un calcul resté « en
    cours » depuis un processus disparu.

    Risque : journalisation du démarrage, clôture des calculs abandonnés et message d'arrêt
    cassés sans signal.
    """
    abandonne = _calcul_reste_en_cours(jeu_minimal.semestre_id)

    with TestClient(module_app.creer_app()) as client:
        assert client.get("/sante").status_code == 200

    journal = _journal(environnement)
    assert "Application EDT" in journal and "démarrée" in journal and "arrêtée" in journal
    assert f"Calcul {abandonne} clôturé" in journal
    assert generation.etat_calcul(abandonne).etat is EtatCalcul.ERREUR


def _attendre_preparation(dossier: Path) -> str:
    """Journal, une fois la préparation des premières pages terminée (`edt.web.app._preparer`)."""
    limite = time.monotonic() + 20
    while "Premières pages préparées" not in (journal := _journal(dossier)):
        assert time.monotonic() < limite, "préparation du démarrage non terminée"
        time.sleep(0.05)
    return journal


@pytest.mark.cas_limite
def test_une_panne_de_preparation_n_empeche_pas_de_servir(jeu_minimal, environnement, monkeypatch):
    """Cas : la préparation des premières pages au démarrage échoue (gabarit illisible, page
    qui lève une exception).

    Risque : démarrage en échec, ou tâche de fond morte en silence ; la panne doit être
    journalisée et le serveur servir normalement.
    """

    def panne(*args, **kwargs):
        raise RuntimeError("préparation impossible")

    monkeypatch.setattr(module_app, "precompiler_gabarits", panne)
    monkeypatch.setattr(module_app, "_servir_en_interne", panne)
    with TestClient(module_app.creer_app()) as client:
        journal = _attendre_preparation(environnement)
        assert client.get("/sante").status_code == 200
        assert client.get("/").status_code == 200
    assert "compilation des gabarits impossible" in journal
    assert "Préparation de la page / impossible" in journal
    assert "RuntimeError: préparation impossible" in journal


@pytest.mark.cas_limite
def test_cycle_de_vie_sans_base_initialisee_avertit_sans_echouer(environnement):
    """Cas : le serveur démarre avant `init-db` (base vide).

    Risque : démarrage en échec ou silence ; il doit démarrer et dire quoi lancer.
    """
    with TestClient(module_app.creer_app()) as client:
        assert client.get("/sante").status_code == 503
    assert "n'est pas prête" in _journal(environnement)


@pytest.mark.cas_limite
def test_le_serveur_demarre_et_repond_base_indisponible_malgre_une_base_corrompue(
    jeu_minimal, environnement, corrompre_base
):
    """Cas : `edt.db` a été tronqué ou remplacé (« file is not a database ») avant le
    démarrage du serveur.

    Risque : « Application startup failed », cinq redémarrages puis 502 pour tout le monde et
    `/sante` jamais servi ; une fois servi, 500 « erreur inattendue » car
    `sqlite3.DatabaseError` n'est pas une `OperationalError`.
    """
    corrompre_base()

    with TestClient(module_app.creer_app()) as client:  # le cycle de vie ne lève pas
        sante = client.get("/sante")
        assert sante.status_code == 503 and sante.json()["statut"] == "degrade"
        page = client.get("/")  # l'accueil reste lisible et montre l'état de la base
        assert page.status_code == 200 and "not a database" in page.text
    journal = _journal(environnement)
    assert "indisponible au démarrage" in journal and "startup failed" not in journal
    assert "n'est pas prête" not in journal  # « lancer init-db » n'est pas le remède ici


@pytest.mark.cas_limite
def test_une_route_sur_une_base_corrompue_repond_503_avec_le_remede(
    base, corrompre_base, client_etendu
):
    """Cas : une page qui écrit dans la base alors que le fichier n'est plus une base SQLite.

    Risque : 500 « erreur inattendue » au lieu du 503 « base indisponible » qui nomme le remède.
    """
    corrompre_base()
    reponse = client_etendu.post("/essai/campus", params={"code": "KO"}, follow_redirects=False)
    assert reponse.status_code == 503
    assert "Base de données indisponible" in reponse.text and "pas une base SQLite" in reponse.text
    assert "init-db --reset" in reponse.text and "inattendue" not in reponse.text


@pytest.mark.cas_limite
def test_le_serveur_demarre_malgre_une_base_verrouillee_et_reporte_la_cloture_des_calculs(
    jeu_minimal, environnement, monkeypatch, delai_verrou_court, verrou_ecriture
):
    """Cas : un autre processus tient le verrou d'écriture au démarrage, et un calcul abandonné
    attend d'être clos.

    Risque : la clôture (une écriture) fait échouer le démarrage ; elle doit être reportée
    (avertissement), les lectures servies (mode WAL) et une écriture répondre « occupée ».
    """
    abandonne = _calcul_reste_en_cours(jeu_minimal.semestre_id)
    liberer_moteurs()

    with verrou_ecriture(), TestClient(_app_etendue(monkeypatch)) as client:
        assert client.get("/sante").status_code == 200  # lecture servie malgré le verrou (WAL)
        assert client.get("/static/css/app.css").status_code == 200
        ecriture = client.post("/essai/campus", params={"code": "VERROU"}, follow_redirects=False)
        assert ecriture.status_code == 503 and "occupée" in ecriture.text

    journal = _journal(environnement)
    assert "Clôture des calculs interrompus reportée" in journal
    assert generation.etat_calcul(abandonne).etat is EtatCalcul.EN_COURS  # pas clos sous verrou
    assert generation.cloturer_calculs_interrompus() == 1  # rattrapé une fois le verrou rendu


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("cause", "mot"),
    [
        ("database is locked", "occupée"),
        ("database is busy", "occupée"),
        ("no such table: parametre", "init-db"),
        ("file is not a database", "pas une base SQLite"),
    ],
)
def test_une_panne_du_pilote_de_base_est_un_503_qui_dit_quoi_faire(client_etendu, cause, mot):
    """Cas : une route reçoit du pilote SQLite une erreur de verrou, de table absente ou de
    fichier illisible.

    Risque : page 500 sans remède ; chaque cause doit donner un 503 avec sa marche à suivre.
    """
    reponse = client_etendu.get("/essai/panne-base", params={"cause": cause})
    assert reponse.status_code == 503, reponse.text
    assert mot in reponse.text and "inattendue" not in reponse.text


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("cause", "mot"),
    [
        ("attempt to write a readonly database", "lecture seule"),
        ("database or disk is full", "espace disque"),
    ],
)
def test_une_base_en_lecture_seule_ou_un_disque_plein_sont_nommes(client_etendu, cause, mot):
    """Cas : la base est en lecture seule (fichier copié en root) ou le disque est plein.

    Risque : 503 générique « réessayer » : le travail saisi est perdu et rien ne nomme les
    droits ni l'espace disque.
    """
    reponse = client_etendu.get("/essai/panne-base", params={"cause": cause})
    assert reponse.status_code == 503 and mot in reponse.text


@pytest.mark.cas_limite
def test_une_erreur_de_programmation_du_pilote_est_une_page_500(client_etendu, environnement):
    """Cas : le pilote de base signale une erreur de programmation (paramètres faux, curseur
    fermé) : un défaut du code, pas une panne de la base.

    Risque : page 503 « réessayer dans quelques instants », conseil inopérant, au lieu de la
    page 500 qui donne la référence à communiquer.
    """
    reponse = client_etendu.get("/essai/requete-fausse")
    assert reponse.status_code == 500
    assert "erreur inattendue" in reponse.text and reponse.headers["X-Request-ID"] in reponse.text
    assert "Incorrect bindings" in _journal(environnement)


@pytest.mark.cas_limite
def test_un_envoi_trop_volumineux_donne_une_page_qui_dit_quoi_faire(client_etendu):
    """Cas : l'application refuse un envoi trop volumineux (413).

    Risque : page générique « Requête refusée » qui ne dit ni la cause ni le remède.
    """
    reponse = client_etendu.get("/essai/trop-gros")
    assert reponse.status_code == 413
    assert "Envoi trop volumineux" in reponse.text and "découper" in reponse.text


@pytest.mark.cas_limite
def test_sante_signale_une_base_en_lecture_seule(base, environnement, lecture_seule):
    """Cas : `edt.db` est en lecture seule (droits), le déploiement a réussi.

    Risque : `/sante` vert et supervision rassurée alors qu'aucune écriture n'est possible.
    """
    liberer_moteurs()
    with lecture_seule(environnement / "edt.db"):
        sante = TestClient(module_app.creer_app()).get("/sante")
        assert sante.status_code == 503
        assert sante.json()["base"].get("inscriptible") is False


@pytest.mark.cas_limite
def test_head_repond_comme_get_sans_corps(jeu_minimal, client):
    """Cas : la supervision (sonde, `curl -I`) envoie HEAD / et HEAD /sante.

    Risque : 405 « Action non permise » partout : la supervision conclut à une panne permanente.
    """
    for chemin in ("/", "/sante"):
        reponse = client.head(chemin)
        assert reponse.status_code == 200, (chemin, reponse.status_code)
        assert reponse.content == b""


@pytest.mark.cas_limite
def test_une_methode_non_permise_donne_une_page_405(client):
    """Cas : un client envoie PUT ou DELETE sur une page.

    Risque : trace ou page blanche ; la page « Action non permise » doit répondre 405.
    """
    for methode in ("put", "delete"):
        reponse = getattr(client, methode)("/sante")
        assert reponse.status_code == 405 and "Action non permise" in reponse.text


@pytest.mark.cas_limite
def test_remise_a_zero_par_un_autre_processus_donne_base_occupee(
    base, environnement, client_etendu
):
    """Cas : un autre processus (`edt jeu --reset`) a supprimé les tables et les recrée pendant
    que le serveur sert une requête.

    Risque : « base non initialisée : lancer init-db » (faux et dangereux) ; le marqueur posé
    par l'autre processus doit faire répondre « occupée, réessayer ».
    """
    supprimer_schema(obtenir_moteur())
    marqueur = environnement / "edt.db.operation"
    marqueur.write_text("4242 remise à zéro", encoding="utf-8")

    pendant = client_etendu.post("/essai/campus", params={"code": "X"}, follow_redirects=False)
    assert pendant.status_code == 503
    assert "occupée" in pendant.text and "init-db" not in pendant.text

    marqueur.unlink()
    apres = client_etendu.post("/essai/campus", params={"code": "X"}, follow_redirects=False)
    assert apres.status_code == 503 and "init-db" in apres.text
