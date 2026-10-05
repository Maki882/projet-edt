"""Écran des besoins : trois modes de volume, filtres, duplication, charge hebdomadaire."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from edt.db.modeles import Besoin
from edt.db.moteur import session_base
from edt.domaine.enumerations import ModeVolume
from edt.services.referentiels.enseignements import charges_hebdomadaires


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


def test_charge_hebdomadaire_par_groupe(ids):
    with session_base() as session:
        charges = {c.code: c for c in charges_hebdomadaires(session, ids.semestre)}
    classe = charges["A1-G1"]
    assert classe.heritees == charges["A1"].propres  # elle suit les cours de sa promotion
    assert classe.ponctuelles == 2  # DST et partiel de la promotion
    assert classe.total == round(classe.propres + classe.heritees, 1)
