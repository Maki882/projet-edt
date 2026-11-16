"""Écran des besoins : trois modes de volume, filtres, duplication, charge hebdomadaire."""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import func, select

from edt.db.modeles import Besoin
from edt.db.moteur import session_base
from edt.domaine.enumerations import ModeVolume
from edt.services.referentiels import operations


def _besoin(ids, **champs):
    return {
        "semestre_id": ids.semestre,
        "matiere_id": ids.matiere_maths,
        "type_seance_id": ids.type_td,
        "groupes": [ids.classe_g1],
        "enseignants": [ids.enseignant_martin],
        "duree_min": "1 h 30",
        **champs,
    }


def _dernier_besoin() -> Besoin:
    with session_base() as session:
        return session.scalar(select(Besoin).order_by(Besoin.id.desc()).limit(1))


def test_mode_par_semaine(site, ids):
    reponse = site.post(
        "/besoins",
        data=_besoin(ids, mode_volume="par_semaine", seances_par_semaine="2", max_par_semaine="5"),
        follow_redirects=False,
    )
    assert reponse.status_code == 303
    besoin = _dernier_besoin()
    assert (besoin.mode_volume, besoin.nombre, besoin.duree_min) == (ModeVolume.PAR_SEMAINE, 2, 90)
    assert besoin.max_par_semaine == 1  # sans objet dans ce mode : valeur neutre


def test_mode_total_entre_deux_dates(site, ids):
    reponse = site.post(
        "/besoins",
        data=_besoin(
            ids,
            mode_volume="total",
            nombre_total="10",
            max_par_semaine="2",
            date_debut="2026-09-07",
            date_fin="2026-10-23",
        ),
        follow_redirects=False,
    )
    assert reponse.status_code == 303
    besoin = _dernier_besoin()
    assert (besoin.mode_volume, besoin.nombre, besoin.max_par_semaine) == (ModeVolume.TOTAL, 10, 2)
    assert besoin.date_fin == dt.date(2026, 10, 23)

    sans_total = site.post("/besoins", data=_besoin(ids, mode_volume="total", nombre_total=""))
    assert sans_total.status_code == 422
    assert "Ce champ est obligatoire pour ce mode de volume." in sans_total.text


def test_mode_ponctuel(site, ids):
    reponse = site.post(
        "/besoins",
        data=_besoin(
            ids,
            mode_volume="ponctuel",
            date_ponctuelle="19/11/2026",
            debut_impose_min="13:45",
            jour_impose="0",
            date_debut="2026-09-07",
        ),
        follow_redirects=False,
    )
    assert reponse.status_code == 303
    besoin = _dernier_besoin()
    assert besoin.mode_volume == ModeVolume.PONCTUEL and besoin.nombre == 1
    assert besoin.date_ponctuelle == dt.date(2026, 11, 19) and besoin.debut_impose_min == 825
    assert besoin.jour_impose is None and besoin.date_debut is None  # champs hors mode écartés

    hors_semestre = site.post(
        "/besoins", data=_besoin(ids, mode_volume="ponctuel", date_ponctuelle="2027-05-01")
    )
    assert hors_semestre.status_code == 422
    assert "La date doit être comprise dans le semestre" in hors_semestre.text


def test_formulaire_marque_les_champs_propres_a_chaque_mode(site):
    page = site.get("/besoins/nouveau").text
    assert 'data-visible-si="mode_volume:total"' in page
    assert 'data-visible-si="mode_volume:ponctuel"' in page
    assert 'data-visible-si="mode_volume:par_semaine,total"' in page
    assert page.count('class="choix-multiple"') == 4  # groupes, enseignants, types, équipements


def test_liste_filtrable_et_ajout_rapide(site, ids):
    tout = site.get(f"/besoins?semestre={ids.semestre}").text
    assert "16 besoins" in tout and "Ajout rapide" in tout
    assert "Charge hebdomadaire par groupe" in tout

    par_enseignant = site.get(
        f"/besoins?semestre={ids.semestre}&enseignant={ids.enseignant_martin}"
    )
    assert "3 besoins correspondant" in par_enseignant.text
    par_promotion = site.get(f"/besoins?semestre={ids.semestre}&promotion={ids.promo}").text
    assert "16 besoins correspondant" in par_promotion
    ponctuels = site.get(f"/besoins?semestre={ids.semestre}&mode=ponctuel").text
    assert "2 besoins correspondant" in ponctuels and "le 19/11/2026" in ponctuels


def test_duplication(site):
    reponse = site.post("/besoins/1/dupliquer", follow_redirects=False)
    assert reponse.status_code == 303
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(Besoin)) == 17
    assert "Copie créée" in site.get(reponse.headers["location"]).text


def test_liste_compacte(site, ids):
    page = site.get(f"/besoins?semestre={ids.semestre}&mode=total").text
    assert "8 séances" in page  # volume court, sur une ligne
    assert "6 séances · 05/10 → 18/12" in page
    assert 'title="6 au total, 1 max. / semaine, du 05/10/2026 au 18/12/2026"' in page  # détail
    assert 'class="tableau tableau-compact"' in page
    assert 'title="Trier par semestre"' not in page  # colonne fixée par le filtre : omise

    tous = site.get("/besoins?semestre=&mode=ponctuel").text
    assert 'title="Trier par semestre"' in tous and "le 19/11 à 13 h 45" in tous

    projet = site.get(f"/besoins?semestre={ids.semestre}&q=Projet").text
    assert 'title="A1-G1, A1-G2"' in projet  # liste complète au survol


def test_liste_abregee_au_dela_de_l_apercu(site, ids):
    with session_base() as session:
        operations.modifier(
            session,
            "besoin",
            1,
            {"groupes": [ids.promo, ids.classe_g1, *range(5, 9)]},
            partiel=True,
        )
    page = site.get(f"/besoins?semestre={ids.semestre}&q=IM.1101").text
    assert '<span class="plus">+4</span>' in page


def test_charges_repliees_et_limitees_aux_groupes_concernes(site, ids):
    page = site.get(f"/besoins?semestre={ids.semestre}").text
    bloc = re.search(r'<details class="carte repliable" id="charges-groupes">', page)
    assert bloc is not None  # replié par défaut (pas d'attribut open)
    assert "(8 groupes)" in page  # le groupe PMR, sans séance, n'y figure pas

    langue = site.get(f"/besoins?semestre={ids.semestre}&type=", params={"q": "Anglais"}).text
    assert "(1 groupe concernés par la recherche et les filtres)" in langue

    fragment = site.get(
        f"/besoins?semestre={ids.semestre}&enseignant={ids.enseignant_martin}",
        headers={"HX-Request": "true", "HX-Target": "zone-liste"},
    ).text
    assert 'id="charges-groupes"' in fragment  # rafraîchi avec la zone de liste
    assert "concernés par la recherche et les filtres" in fragment
