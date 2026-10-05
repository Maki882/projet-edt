"""Page des jeux de données : établissement fictif, refus pendant un calcul, remise à zéro."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import func, select

from edt.db.modeles import Besoin, Campus
from edt.db.moteur import session_base


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def test_page_decrit_les_tailles(client):
    page = client.get("/jeux").text
    for taille in ("Petit", "Moyen", "Grand"):
        assert f"<strong>{taille}</strong>" in page
    assert 'name="graine" value="0"' in page
    assert "data-attente=" in page  # bouton désactivé pendant l'envoi


def test_chargement_du_jeu_petit(client):
    reponse = client.post("/jeux/charger", data={"taille": "petit", "graine": "0"})

    assert reponse.status_code == 200
    assert "Établissement fictif « petit » chargé" in reponse.text
    assert "Jeu fictif « petit » (graine 0)" in reponse.text  # résumé du rapport
    assert 'href="/besoins"' in reponse.text
    assert _compter(Campus) == 2 and _compter(Besoin) > 0


def test_rechargement_exige_une_confirmation_d_effacement(jeu_minimal, client):
    page = client.get("/jeux").text
    assert "Seront effacés" in page and "Salles : 6" in page

    refus = client.post("/jeux/charger", data={"taille": "petit", "graine": "0"})
    assert refus.status_code == 422 and "Cochez la case" in refus.text
    assert _compter(Besoin) == 16  # rien n'a changé

    ok = client.post("/jeux/charger", data={"taille": "petit", "graine": "1", "confirmation": "1"})
    assert ok.status_code == 200 and "(graine 1)" in ok.text


def test_refus_clair_pendant_un_calcul(jeu_minimal, client, monkeypatch):
    monkeypatch.setattr(
        "edt.services.jeux.calcul_en_cours", lambda: SimpleNamespace(id=42, semestre_id=1)
    )
    charge = client.post(
        "/jeux/charger", data={"taille": "petit", "graine": "0", "confirmation": "1"}
    )
    assert charge.status_code == 409
    assert "un calcul d&#39;emploi du temps est en cours" in charge.text
    assert (
        "Une erreur inattendue" not in charge.text
        and 'class="message message-attention"' in charge.text
    )

    zero = client.post("/jeux/remise-a-zero", data={"confirmation_zero": "1"})
    assert zero.status_code == 409 and "calcul n°42" in zero.text
    assert _compter(Besoin) == 16


def test_remise_a_zero_confirmee(jeu_minimal, client):
    sans_confirmation = client.post("/jeux/remise-a-zero", data={})
    assert sans_confirmation.status_code == 422
    assert _compter(Campus) == 2

    reponse = client.post(
        "/jeux/remise-a-zero", data={"confirmation_zero": "1"}, follow_redirects=True
    )
    assert "La base a été remise à zéro" in reponse.text
    assert _compter(Campus) == 0 and _compter(Besoin) == 0
    assert client.get("/salles").status_code == 200
