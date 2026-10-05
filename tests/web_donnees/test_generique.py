"""Mécanisme générique : liste (tri, recherche, filtres, pagination), formulaire, suppression."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from edt.db.modeles import Besoin, Salle
from edt.db.moteur import session_base
from edt.erreurs import ErreurRegleMetier
from edt.services.referentiels import operations
from edt.services.referentiels.operations import ErreurSaisie


def _salle(ids, code="N999", **autres):
    return {
        "code": code,
        "campus_id": str(ids.ndc),
        "type_salle_id": str(ids.type_cours),
        "capacite": "30",
        "accessibilite": "oui",
        **autres,
    }


def test_liste_triee_recherchee_et_filtree(site, ids):
    page = site.get("/salles").text
    assert "6 salles." in page
    assert page.index(">L012<") < page.index(">N016<")  # tri par code par défaut

    par_capacite = site.get("/salles?tri=capacite&sens=desc").text
    assert par_capacite.index(">N016<") < par_capacite.index(">N310<")  # 120 places avant 24
    assert 'aria-sort="descending"' in par_capacite

    recherche = site.get("/salles?q=n2").text  # sans casse
    assert "1 salle correspondant" in recherche and ">N204<" in recherche

    filtree = site.get(f"/salles?campus={ids.issy}").text
    assert "2 salles correspondant" in filtree and ">N016<" not in filtree


def test_pagination(site, ids):
    with session_base() as session:
        for numero in range(60):
            operations.creer(session, "salle", _salle(ids, code=f"P{numero:03d}"))

    premiere = site.get("/salles").text
    assert "66 salles." in premiere and "Page 1 sur 2" in premiere
    seconde = site.get("/salles?page=2").text
    assert "Page 2 sur 2" in seconde and ">P059<" in seconde and ">P000<" not in seconde
    assert site.get("/salles?page=99").status_code == 200  # ramenée à la dernière page
    assert site.get("/salles?page=abc").status_code == 422


def test_fragment_htmx_de_la_liste(site):
    fragment = site.get("/salles?q=N1", headers={"HX-Request": "true", "HX-Target": "zone-liste"})
    assert fragment.status_code == 200
    assert "<html" not in fragment.text and "<table" in fragment.text


def test_creation_puis_message(site, ids):
    reponse = site.post("/salles", data=_salle(ids, equipements=["1", "2"]), follow_redirects=False)
    assert reponse.status_code == 303
    page = site.get(reponse.headers["location"]).text
    assert "Salle « N999 (NDC) » créée." in page
    with session_base() as session:
        salle = session.scalar(select(Salle).where(Salle.code == "N999"))
        assert salle.capacite == 30 and len(salle.equipements) == 2


def test_erreur_de_saisie_reaffichee_avec_les_valeurs(site, ids):
    reponse = site.post(
        "/salles", data=_salle(ids, code="N016", capacite="trente", batiment="Ouest")
    )
    assert reponse.status_code == 422
    assert "Nombre entier attendu." in reponse.text
    assert 'value="Ouest"' in reponse.text and 'value="trente"' in reponse.text  # rien n'est perdu

    doublon = site.post("/salles", data=_salle(ids, code="N016"))
    assert doublon.status_code == 422
    assert "Une salle porte déjà ce code sur ce campus." in doublon.text


def test_modification(site, ids):
    reponse = site.post(
        f"/salles/{ids.salle_n016}",
        data={**_salle(ids, code="N016", capacite="150"), "_retour": "/salles?tri=capacite"},
        follow_redirects=False,
    )
    assert reponse.status_code == 303
    assert reponse.headers["location"] == "/salles?tri=capacite"  # retour à la liste d'origine
    with session_base() as session:
        assert session.get(Salle, ids.salle_n016).capacite == 150
        assert session.get(Salle, ids.salle_n016).equipements == []  # case décochée = retirée


def test_modification_partielle_par_le_service(ids):
    with session_base() as session:
        valeurs = operations.modifier(
            session, "salle", ids.salle_n016, {"capacite": 99}, partiel=True
        )
    assert valeurs["capacite"] == 99 and len(valeurs["equipements"]) == 2


def test_suppression_confirmee(site, ids):
    confirmation = site.get(f"/salles/{ids.salle_n016}/supprimer").text
    assert "Supprimer définitivement" in confirmation
    reponse = site.post(f"/salles/{ids.salle_n016}/supprimer", follow_redirects=True)
    assert "Salle « N016 (NDC) » supprimée." in reponse.text
    assert site.get(f"/salles/{ids.salle_n016}").status_code == 404


def test_suppression_refusee_avant_la_base_avec_message(site, ids):
    confirmation = site.get(f"/enseignants/{ids.enseignant_martin}/supprimer").text
    assert "Suppression impossible" in confirmation
    assert "Cet enseignant assure 3 besoins." in confirmation

    reponse = site.post(f"/enseignants/{ids.enseignant_martin}/supprimer", follow_redirects=True)
    assert "Cet enseignant assure 3 besoins." in reponse.text  # message affiché après refus
    with session_base() as session:
        with pytest.raises(ErreurRegleMetier, match="sous-groupes sont suivis par 5 besoins"):
            operations.supprimer(session, "groupe", ids.classe_g1)
        assert session.scalar(select(func.count()).select_from(Besoin)) == 16  # rien n'a bougé


def test_service_refuse_une_saisie_avec_le_detail_par_champ(ids):
    with session_base() as session, pytest.raises(ErreurSaisie) as erreur:
        operations.creer(session, "salle", {"code": "", "campus_id": 999, "capacite": -1})
    assert set(erreur.value.champs) == {"code", "campus_id", "type_salle_id", "capacite"}


def test_edition_dans_la_ligne(site):
    edition = site.get("/types-salle/1/ligne/edition", headers={"HX-Request": "true"})
    assert (
        'class="ligne-edition"' in edition.text and 'hx-post="/types-salle/1/ligne"' in edition.text
    )

    refus = site.post("/types-salle/1/ligne", data={"code": "AMPHI", "libelle": ""})
    assert refus.status_code == 200 and "Ce champ est obligatoire." in refus.text

    ok = site.post("/types-salle/1/ligne", data={"code": "AMPHI", "libelle": "Grand amphi"})
    assert "ligne-modifiee" in ok.text and "Grand amphi" in ok.text
