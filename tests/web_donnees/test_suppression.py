"""Suppression : retour conservé, confirmation sur place, refus avec liens, conséquences expliquées."""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from sqlalchemy import func, select

from edt.db.modeles import Besoin, Campus, Salle, SeanceHebdo
from edt.db.moteur import session_base
from edt.services.jeux import recharger_jeu_fictif
from edt.services.referentiels import operations

HTMX = {"HX-Request": "true"}


def _depuis(liste: str) -> dict[str, str]:
    """En-têtes d'une demande HTMX faite depuis la liste `liste`."""
    return {**HTMX, "HX-Current-URL": f"http://testserver{liste}"}


def _id_salle(code: str) -> int:
    with session_base() as session:
        return session.scalar(select(Salle.id).where(Salle.code == code))


def _nb_salles() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(Salle))


# --------------------------------------------------------------------------- #
# Retour conservé
# --------------------------------------------------------------------------- #


def test_lien_supprimer_de_la_ligne_porte_la_liste(site, ids):
    liste = f"/salles?campus={ids.issy}&tri=capacite"
    page = site.get(liste).text
    l012 = _id_salle("L012")
    retour = quote(liste, safe="")
    assert f'href="/salles/{l012}/supprimer?retour={retour}"' in page
    assert f'hx-get="/salles/{l012}/ligne/suppression"' in page


def test_confirmation_garde_la_liste_et_y_revient(site, ids):
    liste = f"/salles?campus={ids.issy}"
    l012 = _id_salle("L012")
    page = site.get(f"/salles/{l012}/supprimer", params={"retour": liste}).text
    assert f'<input type="hidden" name="_retour" value="{liste}">' in page.replace("&amp;", "&")
    assert f'<a class="bouton" href="{liste}">Annuler</a>' in page

    reponse = site.post(
        f"/salles/{l012}/supprimer", data={"_retour": liste}, follow_redirects=False
    )
    assert reponse.status_code == 303 and reponse.headers["location"] == liste
    arrivee = site.get(liste).text
    assert "Salle « L012 (ISSY) » supprimée." in arrivee
    assert "1 salle correspondant" in arrivee


def test_annuler_ramene_a_la_fiche_quand_on_en_vient(site, ids):
    fiche = site.get(f"/salles/{ids.salle_n016}", params={"retour": "/salles?q=N0"}).text
    lien = re.search(r'href="(/salles/\d+/supprimer\?[^"]+)">Supprimer</a>', fiche).group(1)
    assert "depuis=fiche" in lien and "retour=%2Fsalles%3Fq%3DN0" in lien

    confirmation = site.get(lien.replace("&amp;", "&")).text
    annuler = re.search(r'<a class="bouton" href="([^"]+)">Annuler</a>', confirmation).group(1)
    assert annuler.replace("&amp;", "&") == (
        f"/salles/{ids.salle_n016}?" + urlencode({"retour": "/salles?q=N0"})
    )
    assert '<input type="hidden" name="_depuis" value="fiche">' in confirmation


def test_retour_a_la_page_precedente_si_la_page_disparait(site, ids):
    with session_base() as session:
        for numero in range(46):  # 6 + 46 = 52 salles : la page 2 en a deux (Z44, Z45)
            operations.creer(
                session,
                "salle",
                {
                    "code": f"Z{numero:02d}",
                    "campus_id": ids.ndc,
                    "type_salle_id": ids.type_cours,
                    "capacite": 10,
                    "accessibilite": "oui",
                },
            )
    # La page 2 existe encore après la première suppression : on y revient.
    reponse = site.post(
        f"/salles/{_id_salle('Z45')}/supprimer",
        data={"_retour": "/salles?page=2"},
        follow_redirects=False,
    )
    assert reponse.headers["location"] == "/salles?page=2"

    # Depuis la ligne, la dernière salle de la page 2 : la zone montre la page 1.
    zone = site.post(
        f"/salles/{_id_salle('Z44')}/ligne/supprimer", headers=_depuis("/salles?page=2")
    )
    assert zone.headers["HX-Replace-Url"] == "/salles"
    assert "50 salles." in zone.text and "Salle « Z44 (NDC) » supprimée." in zone.text


# --------------------------------------------------------------------------- #
# Confirmation sur place (HTMX)
# --------------------------------------------------------------------------- #


def test_ligne_de_confirmation_quand_la_suppression_est_possible(site, ids):
    l115 = _id_salle("L115")
    reponse = site.get(f"/salles/{l115}/ligne/suppression", headers=_depuis("/salles"))
    assert reponse.status_code == 200 and "<html" not in reponse.text
    texte = reponse.text
    assert 'class="ligne-suppression"' in texte and 'colspan="9"' in texte
    assert "Supprimer définitivement cette salle « L115 (ISSY) » ?" in texte
    assert "Son indisponibilité sera supprimée avec elle." in texte
    assert f'hx-post="/salles/{l115}/ligne/supprimer" hx-target="#zone-liste"' in texte
    assert f'hx-get="/salles/{l115}/ligne"' in texte  # Annuler : la ligne revient


def test_ligne_de_confirmation_refusee_avec_lien(site, ids):
    reponse = site.get(
        f"/enseignants/{ids.enseignant_martin}/ligne/suppression", headers=_depuis("/enseignants")
    )
    texte = reponse.text
    assert "ligne-suppression-refusee" in texte and "Impossible de supprimer" in texte
    assert "Cet enseignant assure 3 besoins." in texte
    lien = f"/besoins?semestre=&amp;enseignant={ids.enseignant_martin}"
    assert f'<a href="{lien}">Voir ces 3 besoins</a>' in texte
    assert "Supprimer définitivement" not in texte

    besoins = site.get(lien.replace("&amp;", "&")).text
    assert "3 besoins correspondant" in besoins


def test_suppression_depuis_la_ligne_rafraichit_la_zone(site, ids):
    l012 = _id_salle("L012")
    reponse = site.post(
        f"/salles/{l012}/ligne/supprimer", headers=_depuis(f"/salles?campus={ids.issy}")
    )
    assert reponse.status_code == 200
    texte = reponse.text
    assert "1 salle correspondant" in texte  # compte à jour, filtre conservé
    assert f"ligne-salle-{l012}" not in texte
    assert re.search(r'<div id="messages"[^>]*hx-swap-oob="true"', texte)
    assert "Salle « L012 (ISSY) » supprimée." in texte
    assert _id_salle("L012") is None


def test_suppression_refusee_depuis_la_ligne_ne_touche_a_rien(site, ids):
    reponse = site.post(
        f"/enseignants/{ids.enseignant_martin}/ligne/supprimer", headers=_depuis("/enseignants")
    )
    assert "message-erreur" in reponse.text and "Cet enseignant assure 3 besoins." in reponse.text
    assert "5 enseignants." in reponse.text


def test_sans_javascript_la_suppression_depuis_la_ligne_redirige(site, ids):
    l012 = _id_salle("L012")
    reponse = site.post(f"/salles/{l012}/ligne/supprimer", follow_redirects=False)
    assert reponse.status_code == 303 and reponse.headers["location"] == "/salles"


# --------------------------------------------------------------------------- #
# Refus utiles et conséquences
# --------------------------------------------------------------------------- #


def test_refus_d_une_salle_imposee_mene_aux_besoins(site, ids):
    with session_base() as session:
        for besoin_id in (1, 2):
            operations.modifier(
                session, "besoin", besoin_id, {"salle_imposee_id": ids.salle_n016}, partiel=True
            )
    page = site.get(f"/salles/{ids.salle_n016}/supprimer").text
    assert "Cette salle est imposée à 2 besoins." in page
    lien = f"/besoins?semestre=&amp;salle_imposee={ids.salle_n016}"
    assert f'<a href="{lien}">Voir ces 2 besoins</a>' in page
    assert "2 besoins correspondant" in site.get(lien.replace("&amp;", "&")).text


def test_refus_par_campus_et_par_groupe(site, ids):
    campus = site.get(f"/campus/{ids.issy}/supprimer").text
    assert f'<a href="/salles?campus={ids.issy}">Voir ces 2 salles</a>' in campus

    groupe = site.get(f"/groupes/{ids.promo}/supprimer").text
    lien = f"/besoins?semestre=&amp;groupe={ids.promo}"
    assert f'<a href="{lien}">Voir les besoins concernés</a>' in groupe
    assert "16 besoins correspondant" in site.get(lien.replace("&amp;", "&")).text


def test_consequences_expliquent_la_suite(ids):
    with session_base() as session:
        analyse = operations.analyser_suppression(session, "type_salle", ids.type_cours)
    assert any("accepteront alors toutes les salles" in texte for texte in analyse.consequences)
    assert all(raison.lien == "" for raison in analyse.effets)


# --------------------------------------------------------------------------- #
# Parcours réel sur le jeu moyen
# --------------------------------------------------------------------------- #


def test_parcours_reel_sur_le_jeu_moyen(client):
    recharger_jeu_fictif("moyen", 0, enregistrer_reference=True)
    salles_au_depart = _nb_salles()  # le jeu évolue : on compte ce qui est supprimé, pas un total
    with session_base() as session:
        stan = session.scalar(select(Campus.id).where(Campus.code == "STAN"))
        placee = session.scalar(
            select(SeanceHebdo.salle_id).where(SeanceHebdo.salle_id.is_not(None)).limit(1)
        )
        besoins = session.scalars(select(Besoin.id).order_by(Besoin.id).limit(2)).all()
    liste = f"/salles?campus={stan}"
    assert "2 salles correspondant" in client.get(liste).text

    # Avec JavaScript : confirmation dans la ligne, puis zone rafraîchie avec le message.
    stan301 = _id_salle("Stan301")
    confirmation = client.get(f"/salles/{stan301}/ligne/suppression", headers=_depuis(liste))
    assert "Supprimer définitivement" in confirmation.text
    zone = client.post(f"/salles/{stan301}/ligne/supprimer", headers=_depuis(liste)).text
    assert "1 salle correspondant" in zone and "Salle « Stan301 (STAN) » supprimée." in zone

    # Sans JavaScript : page de confirmation, puis retour sur la même liste filtrée.
    stan201 = _id_salle("Stan201")
    reponse = client.post(
        f"/salles/{stan201}/supprimer", data={"_retour": liste}, follow_redirects=False
    )
    assert reponse.headers["location"] == liste
    arrivee = client.get(liste).text
    assert "Salle « Stan201 (STAN) » supprimée." in arrivee and "Aucun élément" in arrivee

    # Une salle où des séances sont placées : la suite est expliquée.
    page = client.get(f"/salles/{placee}/supprimer").text
    assert "resteront sans salle" in page and "« À traiter »" in page
    assert "prochain calcul" in page

    # Imposée à deux besoins : refus, et le lien mène à ces deux besoins.
    with session_base() as session:
        for besoin_id in besoins:
            operations.modifier(
                session, "besoin", besoin_id, {"salle_imposee_id": placee}, partiel=True
            )
    refus = client.get(f"/salles/{placee}/ligne/suppression", headers=_depuis("/salles")).text
    lien = re.search(r'<a href="([^"]+)">Voir ces 2 besoins</a>', refus).group(1)
    assert "2 besoins correspondant" in client.get(lien.replace("&amp;", "&")).text
    assert _nb_salles() == salles_au_depart - 2
