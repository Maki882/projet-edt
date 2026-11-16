"""Accueil : prochaine étape, version active, points à traiter, calcul sans solution, base.

Seulement ce dont l'équipe pédagogique a besoin : aucune statistique
(indicateurs, nombres de séances, emplacement de la base).
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest
from tests.outils.web_edt.aides import DELAI

from edt import solveur
from edt.config import charger_configuration
from edt.db import modeles as m
from edt.db.moteur import chemin_base, expliquer_erreur_base, session_base
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.services import etat, generation
from edt.services.emploi_du_temps.presentation import pluriel
from edt.services.emploi_du_temps.tableau_de_bord import tableau_de_bord
from edt.solveur.contrat import ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie

DELAI_SANS_ATTENTE_S = 5.0
"""Bien moins que l'attente sur verrou de la base (10 s) : `/sante` n'a pas attendu."""


def test_base_vide_propose_un_etablissement_fictif(client):
    page = client.get("/").text

    assert "Prochaine étape" in page
    assert (
        '<a class="bouton bouton-principal" href="/jeux">Charger un établissement fictif</a>'
        in page
    )
    assert 'href="/import"' in page and 'href="/salles"' in page
    # Base vide : le bloc de la base apparaît, avec l'invitation à charger un jeu.
    assert "État de la base de données" in page
    assert "La base ne contient encore aucune donnée" in page


def test_donnees_sans_emploi_du_temps_conseille_de_generer(jeu_minimal, client, aujourdhui_fige):
    page = client.get("/").text

    assert "Vérifier les données et lancer le calcul" in page
    assert f'href="/calculs?semestre_id={jeu_minimal.semestre_id}"' in page
    bloc = page.split("<h2>Emploi du temps du semestre", 1)[1].split("</section>", 1)[0]
    assert "Aucun emploi du temps n'a encore été généré pour ce semestre." in bloc
    assert "besoin" not in bloc  # plus le nombre de besoins saisis


def test_version_active_sans_verificateur(
    version_generee, client, aujourdhui_fige, sans_verificateur
):
    page = client.get("/").text

    assert "Consulter l&#39;emploi du temps" in page or "Consulter l'emploi du temps" in page
    assert f"/emploi-du-temps?version_id={version_generee.version_id}" in page
    assert "les vérifications automatiques ne sont pas encore disponibles" in page
    assert "anomalies non comptées : vérifications pas encore disponibles" in page
    assert "conflit" not in page  # pas de « 0 conflit » inventé


def _sans_statistique(page: str) -> None:
    """Ni indicateurs, ni nombres de séances, ni détails de la base."""
    for statistique in (
        "compteur-nombre",
        "des séances placées",
        "utilisation des salles",
        "changements de campus",
        "séances par semaine type",
        "séances datées",
        "Emplacement",
        "Environnement",
        "La base est initialisée",
    ):
        assert statistique not in page, statistique


def test_points_a_traiter_sans_indicateurs(
    version_generee, client, aujourdhui_fige, faux_verificateur
):
    faux_verificateur.hebdo = [
        Anomalie(CodeContrainte.SALLE_UNIQUE, Gravite.ERREUR, "La salle N016 est prise deux fois.")
    ]

    page = client.get("/").text

    assert "1 point à traiter" in page
    assert f'href="/a-traiter?semestre_id={version_generee.semestre_id}"' in page
    _sans_statistique(page)


def test_calcul_en_cours_conseille_de_le_suivre(jeu_minimal, client, aujourdhui_fige, monkeypatch):
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    try:
        assert demarre.wait(DELAI)
        page = client.get("/").text
        assert "Suivre le calcul" in page and f'href="/calculs/{calcul_id}"' in page
    finally:
        generation.demander_arret(calcul_id)
        generation.attendre_calcul(calcul_id, DELAI)


def test_tout_va_bien_version_a_consulter_et_rien_d_autre(version_generee, client, aujourdhui_fige):
    page = client.get("/").text

    with session_base() as session:
        libelle = session.get(m.VersionEdt, version_generee.version_id).libelle
        reste = tableau_de_bord(session, aujourdhui_fige).nb_a_traiter
    assert "Version active" in page and libelle in page
    adresse = f"/emploi-du-temps?version_id={version_generee.version_id}"
    assert f'<a href="{adresse}">Consulter</a>' in page
    assert reste is not None  # le vrai vérificateur a compté
    attendu = "Aucun point à traiter" if not reste else f"{pluriel(reste, 'point')} à traiter"
    assert attendu in page
    # Le calcul réussi ne s'affiche pas : la version active le dit déjà.
    assert "Dernier calcul" not in page
    assert f'href="/calculs/{version_generee.calcul_id}"' not in page
    # La base va bien : son bloc disparaît.
    assert "État de la base de données" not in page
    _sans_statistique(page)


def _refuser(*args, **kwargs):
    return [
        Anomalie(
            CodeContrainte.DISPO_GROUPE, Gravite.ERREUR, "La classe A1-G1 n'a aucun créneau libre."
        )
    ]


@pytest.mark.parametrize(
    ("issue", "etiquette", "extrait"),
    [
        ("temps_depasse", "Pas trouvé dans le temps imparti", None),
        ("refuse", "Calcul refusé", "aucun créneau libre"),
    ],
)
def test_dernier_calcul_sans_solution_visible(
    version_generee, client, aujourdhui_fige, issue, etiquette, extrait
):
    with pytest.MonkeyPatch.context() as temporaire:
        if issue == "refuse":
            temporaire.setattr(generation, "pre_verifier", _refuser)
        else:
            temporaire.setattr(
                solveur,
                "resoudre",
                lambda instantane, options, arret=None: ResultatSolveur(
                    StatutSolveur.TEMPS_DEPASSE
                ),
            )
        echoue = generation.attendre_calcul(
            generation.lancer_calcul(version_generee.semestre_id), DELAI
        )
    assert not echoue.reussi

    page = client.get("/").text

    bloc = page.split("<h2>Dernier calcul</h2>", 1)[1].split("</section>", 1)[0]
    assert etiquette in bloc and "Lancé le" in bloc
    assert f'<a href="/calculs/{echoue.id}">Voir le détail</a>' in bloc
    if extrait:
        assert extrait in bloc  # le message du calcul
    # La version active d'avant reste affichée à côté.
    assert f"/emploi-du-temps?version_id={version_generee.version_id}" in page
    assert f'href="/calculs/{version_generee.calcul_id}"' not in page
    _sans_statistique(page)


def test_base_au_schema_perime(client):
    from edt.db.moteur import obtenir_moteur

    with obtenir_moteur().begin() as connexion:
        connexion.exec_driver_sql("ALTER TABLE besoin ADD COLUMN ancien_champ VARCHAR(10)")

    page = client.get("/").text
    assert "Le schéma de la base ne correspond plus à l&#39;application" in page
    assert "colonne en trop" in page and "init-db --reset" in page
    assert "pas encore initialisée" not in page

    sante = client.get("/sante")
    assert sante.status_code == 503
    assert any("colonne en trop" in ecart for ecart in sante.json()["base"]["ecarts_schema"])


@pytest.mark.cas_limite
def test_base_qui_refuse_l_ecriture_signalee_sur_l_accueil_et_sante(
    jeu_minimal, client, monkeypatch
):
    """Cas : la base refuse l'écriture (fichier copié en root, disque plein) alors qu'elle se lit.
    Risque : accueil muet et `/sante` vert : la supervision est rassurée et chaque saisie de la
    journée échoue ensuite.
    """
    cause = expliquer_erreur_base(sqlite3.OperationalError("attempt to write a readonly database"))
    monkeypatch.setattr(etat, "verifier_ecriture", lambda moteur: (False, cause))

    page = client.get("/").text
    assert "La base de données refuse les écritures" in page
    assert "lecture seule" in page and "python -m edt doctor" in page
    sante = client.get("/sante")
    assert sante.status_code == 503 and sante.json()["base"]["inscriptible"] is False


@pytest.mark.cas_limite
def test_base_occupee_par_une_ecriture_n_est_pas_une_panne(jeu_minimal, client):
    """Cas : un autre processus tient le verrou d'écriture (calcul, import, commande) pendant
    que la supervision interroge `/sante` et qu'on ouvre l'accueil.
    Risque : `/sante` rouge à chaque écriture longue, ou une sonde bloquée le temps du délai
    d'attente sur verrou (dix secondes) ; l'écriture d'essai doit être sautée, sans attente.
    """
    detenteur = sqlite3.connect(
        chemin_base(charger_configuration().db_url), isolation_level=None, timeout=0
    )
    try:
        detenteur.execute("BEGIN IMMEDIATE")
        debut = time.perf_counter()
        sante = client.get("/sante")
        assert time.perf_counter() - debut < DELAI_SANS_ATTENTE_S
        assert sante.status_code == 200 and sante.json()["base"]["inscriptible"] is None
        assert "refuse les écritures" not in client.get("/").text
    finally:
        detenteur.rollback()
        detenteur.close()
    assert client.get("/sante").json()["base"]["inscriptible"] is True  # verrou rendu
