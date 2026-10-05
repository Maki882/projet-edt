"""Onglet « Générer » : lancement, suivi, messages de fin, vérifications préalables, versions."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import func, select

from edt import solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CATALOGUE
from edt.domaine.enumerations import EtatCalcul, Gravite
from edt.services import generation
from edt.solveur.contrat import ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie

from .conftest import DELAI

HX = {"HX-Request": "true"}
TOUTES = [str(code) for code in CATALOGUE]


def _formulaire(semestre_id: int, sauf: tuple[str, ...] = (), **options) -> dict:
    return {
        "semestre_id": semestre_id,
        "contraintes": [code for code in TOUTES if code not in sauf],
        "contraintes_envoyees": "1",
        "duree": "court",
        **options,
    }


def _lancer(client, semestre_id: int, **kwargs) -> int:
    reponse = client.post(
        "/calculs", data=_formulaire(semestre_id, **kwargs), follow_redirects=False
    )
    assert reponse.status_code == 303, reponse.text
    return int(reponse.headers["location"].rsplit("/", 1)[1])


def _nb_calculs() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(m.Calcul))


def test_page_generer_recapitule_et_propose_les_contraintes(jeu_minimal, client):
    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text

    assert '<span class="compteur-nombre">16</span><span class="compteur-libelle">besoins' in page
    assert '<span class="compteur-nombre">17</span><span class="compteur-libelle">séances' in page
    assert page.count('name="contraintes"') == len(CATALOGUE)
    assert page.count(" checked>") >= len(CATALOGUE)  # toutes cochées par défaut
    assert all(entree.libelle in page for entree in CATALOGUE.values())
    assert "Rechercher jusqu'à 1 minute" in page and "5 minutes" in page
    # Pré-vérifications pas encore livrées : on le dit, sans prétendre que tout va bien.
    assert "pas encore disponibles" in page
    assert "Aucun problème relevé" not in page


def test_lancement_suivi_jusqu_a_la_fin_et_rappel_des_contraintes_decochees(jeu_minimal, client):
    calcul_id = _lancer(client, jeu_minimal.semestre_id, sauf=("DEJEUNER", "PAUSE"))
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi

    page = client.get(f"/calculs/{calcul_id}").text
    assert "Emploi du temps généré" in page
    assert f"/emploi-du-temps?version_id={suivi.version_id}" in page
    assert "Pause déjeuner" in page and "Pause entre deux séances" in page
    assert "Traceback" not in page

    fragment = client.get(f"/calculs/{calcul_id}/etat", headers=HX).text
    assert "<html" not in fragment
    assert "hx-trigger" not in fragment  # calcul fini : l'interrogation s'arrête


def test_suivi_sans_rechargement_second_lancement_refuse_puis_arret(
    jeu_minimal, client, monkeypatch
):
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(
            StatutSolveur.ARRETE if arret.is_set() else StatutSolveur.TEMPS_DEPASSE
        )

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    try:
        assert demarre.wait(DELAI)
        fragment = client.get(f"/calculs/{calcul_id}/etat", headers=HX).text
        assert 'hx-trigger="every 1s"' in fragment
        assert "Temps écoulé" in fragment and "Arrêter le calcul" in fragment

        page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
        assert "Calcul en cours" in page
        assert 'id="formulaire-calcul"' not in page  # pas de second lancement proposé

        second = client.post(
            "/calculs", data=_formulaire(jeu_minimal.semestre_id), follow_redirects=False
        )
        assert second.status_code == 303
        assert second.headers["location"] == f"/calculs/{calcul_id}"
        assert _nb_calculs() == 1

        arret = client.post(f"/calculs/{calcul_id}/arreter", headers=HX)
        assert arret.status_code == 200
        assert "Arrêt demandé" in arret.text or "Calcul arrêté" in arret.text
    finally:
        generation.demander_arret(calcul_id)
    assert generation.attendre_calcul(calcul_id, DELAI).etat is EtatCalcul.ARRETE

    page = client.get(f"/calculs/{calcul_id}").text
    assert "Calcul arrêté" in page
    assert "Aucun emploi du temps n&#39;a été enregistré" in page


@pytest.mark.parametrize(
    ("statut", "attendus", "absents"),
    [
        (
            StatutSolveur.INFAISABLE,
            ["Impossible avec ces contraintes", "Il est certain"],
            ["Relancer en cherchant", "Pas trouvé"],
        ),
        (
            StatutSolveur.TEMPS_DEPASSE,
            [
                "Pas trouvé dans le temps imparti",
                "pas une preuve",
                "Relancer en cherchant",
                "5 minutes",
            ],
            ["Impossible avec ces contraintes"],
        ),
    ],
)
def test_deux_messages_d_echec_distincts(
    jeu_minimal, client, monkeypatch, statut, attendus, absents
):
    monkeypatch.setattr(
        solveur, "resoudre", lambda instantane, options, arret=None: ResultatSolveur(statut)
    )
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    generation.attendre_calcul(calcul_id, DELAI)

    page = client.get(f"/calculs/{calcul_id}").text

    assert all(texte in page for texte in attendus)
    assert not any(texte in page for texte in absents)


def test_erreur_inattendue_donne_identifiant_et_journal_sans_detail_technique(
    jeu_minimal, client, monkeypatch
):
    def moteur_casse(instantane, options, arret=None):
        raise RuntimeError("panne interne du moteur")

    monkeypatch.setattr(solveur, "resoudre", moteur_casse)
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    suivi = generation.attendre_calcul(calcul_id, DELAI)

    page = client.get(f"/calculs/{calcul_id}").text

    assert "Le calcul n&#39;a pas pu aboutir" in page and "erreur inattendue" in page
    assert f"calcul n° {calcul_id}" in page
    assert f"<code>{suivi.fichier_journal.replace(chr(92), '/').rsplit('/', 1)[1]}</code>" in page
    for technique in ("panne interne", "RuntimeError", "Traceback"):
        assert technique not in page


def test_relance_longue_et_autre_solution(jeu_minimal, client, monkeypatch):
    monkeypatch.setattr(
        solveur,
        "resoudre",
        lambda instantane, options, arret=None: ResultatSolveur(StatutSolveur.TEMPS_DEPASSE),
    )
    premier = _lancer(client, jeu_minimal.semestre_id, sauf=("PAUSE",))
    generation.attendre_calcul(premier, DELAI)

    longue = client.post(
        f"/calculs/{premier}/relancer", data={"longue": "1"}, follow_redirects=False
    )
    second = int(longue.headers["location"].rsplit("/", 1)[1])
    generation.attendre_calcul(second, DELAI)
    autre = client.post(
        f"/calculs/{second}/relancer", data={"autre_solution": "1"}, follow_redirects=False
    )
    troisieme = int(autre.headers["location"].rsplit("/", 1)[1])
    generation.attendre_calcul(troisieme, DELAI)

    with session_base() as session:
        calculs = {c.id: c for c in session.scalars(select(m.Calcul))}
    assert calculs[premier].parametres["temps_limite_s"] == 60
    assert calculs[second].parametres["temps_limite_s"] == 300
    assert calculs[second].contraintes_desactivees == ["PAUSE"]  # mêmes règles qu'à l'origine
    assert calculs[troisieme].parametres["temps_limite_s"] == 300  # garde la durée longue
    assert calculs[troisieme].parametres["graine"] != calculs[second].parametres["graine"]


def test_une_erreur_prealable_empeche_de_lancer_un_avertissement_non(
    jeu_minimal, client, faux_verificateur
):
    faux_verificateur.pre_verifications = [
        Anomalie(
            "DISPO_ENSEIGNANT", Gravite.ERREUR, "M. Leroy a 10 h de cours pour 8 h disponibles."
        ),
        Anomalie(
            "CAPACITE", Gravite.AVERTISSEMENT, "Une seule salle peut accueillir la promotion."
        ),
    ]
    semestre_id = jeu_minimal.semestre_id

    page = client.get(f"/calculs?semestre_id={semestre_id}").text
    assert "M. Leroy a 10 h de cours" in page and "Une seule salle" in page
    assert "Le calcul ne peut pas être lancé" in page

    refus = client.post("/calculs", data=_formulaire(semestre_id), follow_redirects=False)
    assert refus.status_code == 409
    assert "n&#39;a pas été lancé" in refus.text
    assert _nb_calculs() == 0

    # Décocher la contrainte concernée : les vérifications se mettent à jour, le calcul part.
    fragment = client.get(
        "/calculs/verifications",
        params={
            "semestre_id": semestre_id,
            "contraintes": [c for c in TOUTES if c != "DISPO_ENSEIGNANT"],
            "contraintes_envoyees": "1",
        },
        headers=HX,
    ).text
    assert "M. Leroy" not in fragment and "Une seule salle" in fragment
    calcul_id = _lancer(client, semestre_id, sauf=("DISPO_ENSEIGNANT",))
    assert generation.attendre_calcul(calcul_id, DELAI).reussi
    assert "Disponibilité des enseignants" in client.get(f"/calculs/{calcul_id}").text


def test_historique_activation_et_suppression_des_versions(version_generee, client):
    second = generation.attendre_calcul(
        generation.lancer_calcul(version_generee.semestre_id), DELAI
    )
    premiere, deuxieme = version_generee.version_id, second.version_id

    page = client.get(f"/versions?semestre_id={version_generee.semestre_id}").text
    assert "Version 1" in page and "Version 2" in page and "Active" in page

    client.post(f"/versions/{premiere}/activer")
    supprimee = client.post(f"/versions/{deuxieme}/supprimer")
    assert supprimee.status_code == 200 and "supprimée" in supprimee.text
    refus = client.post(f"/versions/{premiere}/supprimer")
    assert refus.status_code == 409
    assert "ne peut pas être supprimée" in refus.text

    with session_base() as session:
        restantes = session.execute(select(m.VersionEdt.id, m.VersionEdt.active)).all()
    assert restantes == [(premiere, True)]
