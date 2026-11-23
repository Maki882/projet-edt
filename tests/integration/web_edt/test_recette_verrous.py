"""Verrous posés et levés depuis le panneau d'une séance, puis tenus par le calcul suivant.

La version de départ est la version saisie à la main du jeu minimal (`EMPLOI_DU_TEMPS`) : la
même sur toutes les machines. Le calcul suivant est lancé depuis l'écran « Générer » ; les
tests ne vérifient de son résultat que des propriétés vraies pour toute solution du moteur.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.outils.verification.versions import enregistrer_version
from tests.outils.web_edt.aides import DELAI, seance_du_besoin

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CATALOGUE
from edt.services import generation

MESSAGE_VERROU = "Séance verrouillée : les prochains calculs la laisseront à sa place."
MESSAGE_LIBRE = "Séance déverrouillée : les prochains calculs pourront la déplacer."
PLACE_VERROUILLEE = "<dt>Place</dt><dd>Verrouillée : les calculs la laissent à sa place</dd>"
PLACE_LIBRE = "<dt>Place</dt><dd>Libre : un nouveau calcul peut la déplacer</dd>"


@pytest.fixture
def version_saisie(jeu_minimal) -> int:
    """Version active du jeu minimal saisie à la main : déterministe."""
    with session_base() as session:
        return enregistrer_version(session, jeu_minimal.semestre_id)


def _cm_de_mathematiques(version_id: int) -> m.SeanceHebdo:
    with session_base() as session:
        seance = seance_du_besoin(session, version_id, "Mathématiques", "CM", "A1")
        session.expunge(seance)
    return seance


def _panneau(client, version_id: int, seance_id: int) -> str:
    """La page de la grille, panneau de la séance ouvert (après une action, comme le site)."""
    page = client.get(f"/emploi-du-temps?version_id={version_id}&seance=h{seance_id}")
    assert page.status_code == 200
    return page.text


def _verrou(client, version_id: int, seance_id: int, verrou: bool) -> str:
    """Le bouton « Verrouiller à cette place » ou « Déverrouiller » du panneau, page d'arrivée."""
    reponse = client.post(
        f"/emploi-du-temps/seances/{seance_id}/verrou",
        data={
            "verrou": "true" if verrou else "false",
            "retour": f"/emploi-du-temps?version_id={version_id}&seance=h{seance_id}",
        },
    )
    assert reponse.status_code == 200
    return reponse.text


def test_verrouiller_puis_deverrouiller_une_seance_depuis_son_panneau(version_saisie, client):
    """L'utilisateur verrouille une séance depuis son panneau, lit le message et la ligne
    « Place », puis la déverrouille ; un second clic sur le même bouton ne change rien."""
    seance = _cm_de_mathematiques(version_saisie)
    avant = _panneau(client, version_saisie, seance.id)
    assert PLACE_LIBRE in avant and ">Verrouiller à cette place</button>" in avant

    verrouillee = _verrou(client, version_saisie, seance.id, True)
    assert MESSAGE_VERROU in verrouillee
    assert PLACE_VERROUILLEE in verrouillee and ">Déverrouiller</button>" in verrouillee
    with session_base() as session:
        assert session.get(m.SeanceHebdo, seance.id).verrou is True
    assert "La séance était déjà verrouillée." in _verrou(client, version_saisie, seance.id, True)

    libre = _verrou(client, version_saisie, seance.id, False)
    assert MESSAGE_LIBRE in libre
    assert PLACE_LIBRE in libre and ">Verrouiller à cette place</button>" in libre
    with session_base() as session:
        assert session.get(m.SeanceHebdo, seance.id).verrou is False
    assert "La séance n&#39;était pas verrouillée." in _verrou(
        client, version_saisie, seance.id, False
    )


def _formulaire_generer(semestre_id: int) -> dict:
    """Le formulaire « Générer » tel que la page le propose : niveaux par défaut, durée courte."""
    niveaux = {
        f"niveau_{code}": entree.niveau_defaut.value
        for code, entree in CATALOGUE.items()
        if entree.modifiable
    }
    return {"semestre_id": semestre_id, **niveaux, "duree": "court"}


def test_verrou_pose_par_le_site_tenu_par_le_calcul_suivant(version_saisie, jeu_minimal, client):
    """L'utilisateur verrouille une séance depuis son panneau puis lance un nouveau calcul
    depuis « Générer » : dans la nouvelle version, la séance garde son jour, son heure et sa
    salle, et reste verrouillée ; les autres séances n'ont pas de verrou."""
    seance = _cm_de_mathematiques(version_saisie)
    place = (seance.jour, seance.debut_min, seance.fin_min, seance.salle_id)
    assert MESSAGE_VERROU in _verrou(client, version_saisie, seance.id, True)

    lancement = client.post(
        "/calculs", data=_formulaire_generer(jeu_minimal.semestre_id), follow_redirects=False
    )
    assert lancement.status_code == 303, lancement.text
    calcul_id = int(lancement.headers["location"].rsplit("/", 1)[1])
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi, suivi.message
    assert suivi.version_id != version_saisie

    with session_base() as session:
        nouvelles = session.scalars(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == suivi.version_id)
        ).all()
        par_seance = {(s.besoin_id, s.rang): s for s in nouvelles}
        tenue = par_seance[(seance.besoin_id, seance.rang)]
        assert (tenue.jour, tenue.debut_min, tenue.fin_min, tenue.salle_id) == place
        assert tenue.verrou is True
        # Les séances non verrouillées ne sont pas tenues : le moteur les place librement.
        assert [s.id for s in nouvelles if s.verrou] == [tenue.id]
    page = client.get(f"/emploi-du-temps?version_id={suivi.version_id}&seance=h{tenue.id}").text
    assert PLACE_VERROUILLEE in page
