"""Règles personnalisées : liste et filtres, ajout guidé, modification, états, suppressions,
paramètre des jours travaillés, propositions à la création d'une année, parcours réel."""

from __future__ import annotations

import logging
import re

from sqlalchemy import func, select
from starlette.datastructures import FormData

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.parametres import JOURS_TRAVAILLES_PAR_DEFAUT
from edt.domaine.regles import CATALOGUE_TYPES, EtatRegle, TypeRegle
from edt.services import regles as service_regles
from edt.services.jeux import recharger_jeu_fictif
from edt.services.parametres import lire_parametres
from edt.services.referentiels import operations
from edt.web.donnees.regles import (
    CLE_BLOC_PARAMETRES,
    brouillon_depuis_formulaire,
    erreurs_par_champ,
    rubriques_regle,
)

HTMX = {"HX-Request": "true"}


def _id(modele, colonne, valeur) -> int:
    with session_base() as session:
        return session.scalar(select(modele.id).where(colonne == valeur))


def _regle(annee: int, code: str) -> service_regles.RegleDetail:
    with session_base() as session:
        return service_regles.lire(session, annee, code)


def _codes(page: str) -> list[str]:
    return re.findall(r"<strong>(R\d{3})</strong></a></td>", page)


def _nouvelle(annee: int, type_regle: str, **champs) -> dict:
    return {"type": type_regle, "annee": str(annee), "periode": "annee", **champs}


# --------------------------------------------------------------------------- #
# Liste et filtres
# --------------------------------------------------------------------------- #


def test_liste_de_l_annee_et_bandeau_des_regles_proposees(site, ids):
    page = site.get("/regles").text
    assert _codes(page) == ["R001", "R002", "R003", "R004"]
    assert "2 règles proposées à confirmer" in page
    assert 'class="ligne-proposee"' in page
    assert "La promotion A1 ne travaille que du lundi au vendredi." in page
    assert "Règles personnalisées" in page and '<a href="/regles" class="actif"' in page


def test_filtres_et_recherche(site, ids):
    base = f"/regles?annee={ids.annee}"
    assert _codes(site.get(f"{base}&etat=proposee").text) == ["R003", "R004"]
    assert _codes(site.get(f"{base}&type=PAUSE_DEJEUNER").text) == ["R002"]
    assert _codes(site.get(f"{base}&portee=etablissement").text) == ["R003", "R004"]
    assert _codes(site.get(f"{base}&origine=base&niveau=facultatif").text) == ["R003", "R004"]
    demi = _id(m.Groupe, m.Groupe.code, "A1-G1a")
    assert _codes(site.get(f"{base}&groupe={demi}").text) == ["R001", "R002"]  # héritées
    assert _codes(site.get(f"{base}&q=pause dejeuner").text) == ["R002"]  # sans accents
    assert _codes(site.get(f"{base}&q=R003").text) == ["R003"]
    zone = site.get(f"{base}&etat=active", headers={**HTMX, "HX-Target": "zone-liste"})
    assert "<html" not in zone.text and _codes(zone.text) == ["R001", "R002"]
    assert site.get(f"{base}&etat=inconnu").status_code == 422
    assert site.get("/regles?annee=999").status_code == 404


def test_bandeau_les_voir_ignore_les_filtres_en_cours(site, ids):
    # Un filtre qui exclut les proposées : le bandeau les compte quand même (toute l'année),
    # et « Les voir » mène à toutes, pas à une liste vide.
    page = site.get(f"/regles?annee={ids.annee}&type=PAUSE_DEJEUNER&q=midi").text
    assert "2 règles proposées à confirmer" in page
    lien = re.search(r'<a href="([^"]+)">Les voir</a>', page).group(1)
    assert lien == f"/regles?annee={ids.annee}&amp;etat=proposee"
    assert _codes(site.get(lien.replace("&amp;", "&")).text) == ["R003", "R004"]


# --------------------------------------------------------------------------- #
# Ajout guidé
# --------------------------------------------------------------------------- #


def test_choix_du_type_puis_formulaire_tire_du_schema(site, ids):
    types = site.get(f"/regles/nouveau?annee={ids.annee}").text
    assert types.count('class="carte carte-type"') == 14
    assert "« Le groupe A1 ne travaille pas le samedi. »" in types

    page = site.get(f"/regles/nouveau?annee={ids.annee}&type=PAUSE_DEJEUNER").text
    assert '<option value="groupe" selected>Groupe</option>' in page
    assert '<option value="campus">' not in page  # portée non admise par ce type
    assert 'name="p_mode"' in page and 'type="time" id="champ-p_debut"' in page
    assert 'data-visible-si="p_mode:libre"' in page  # durée : mode libre seulement
    assert 'data-visible-si="portee:enseignant"' in page
    assert re.search(r'<option value="\d+"> └ A1-G1 - ', page)  # arbre des groupes
    # L'aperçu part de sa section, pas du formulaire : HTMX valide un formulaire avant de
    # l'envoyer et n'enverrait rien tant qu'un champ requis est vide ou hors bornes.
    formulaire = re.search(r'<form class="formulaire formulaire-large"[^>]*>', page).group(0)
    assert "hx-post" not in formulaire and 'action="/regles/nouveau"' in formulaire
    section = re.search(r'<section class="apercu-regle"[^>]*>', page).group(0)
    assert 'hx-post="/regles/apercu"' in section and 'hx-include="closest form"' in section
    assert "from:closest form" in section and "load" not in section  # pas d'erreurs d'emblée


def test_personnes_concernees_seulement_pour_la_portee_etablissement(site, ids):
    page = site.get(f"/regles/nouveau?annee={ids.annee}&type=MAX_PAR_JOUR").text
    bloc = re.search(r'<div class="champ[^"]*"[^>]*>\s*<label for="champ-p_personnes"', page)
    assert 'data-visible-si="portee:etablissement"' in bloc.group(0)

    definition = CATALOGUE_TYPES[TypeRegle.MAX_PAR_JOUR]
    saisie = [("p_personnes", "enseignants"), ("p_unite", "minutes"), ("p_maximum", "300")]
    pour_un_groupe = brouillon_depuis_formulaire(
        definition, ids.annee, FormData([("portee", "groupe"), *saisie])
    )
    assert "personnes" not in pour_un_groupe["parametres"]  # même envoyé sans JavaScript
    pour_tous = brouillon_depuis_formulaire(
        definition, ids.annee, FormData([("portee", "etablissement"), *saisie])
    )
    assert pour_tous["parametres"]["personnes"] == "enseignants"

    classe = _id(m.Groupe, m.Groupe.code, "A1-G2")
    donnees = _nouvelle(
        ids.annee,
        "MAX_PAR_JOUR",
        portee="groupe",
        cible_groupe=str(classe),
        p_personnes="enseignants",
        p_unite="minutes",
        p_maximum="300",
        niveau="important",
    )
    cree = site.post("/regles/nouveau", data=donnees, follow_redirects=True)
    assert "créée" in cree.text
    assert "personnes" not in _regle(ids.annee, "R005").regle.parametres

    # « Personnes concernées » seul paramètre du type : tout le bloc suit la portée.
    page = site.get(f"/regles/nouveau?annee={ids.annee}&type=CAMPUS_PAR_JOUR").text
    assert '<fieldset data-visible-si="portee:etablissement">\n  <legend>Paramètres' in page


def test_type_non_datable_sans_mode_dates_et_dates_refusees(site, ids):
    datable = site.get(f"/regles/nouveau?annee={ids.annee}&type=JOURS_TRAVAILLES").text
    assert '<option value="dates">' in datable and 'name="date_debut"' in datable
    page = site.get(f"/regles/nouveau?annee={ids.annee}&type=PAUSE_DEJEUNER").text
    assert '<option value="dates"' not in page and 'name="date_debut"' not in page
    assert "sans dates" in page  # l'aide de la période le dit

    classe = _id(m.Groupe, m.Groupe.code, "A1-G2")
    donnees = _nouvelle(
        ids.annee,
        "PAUSE_DEJEUNER",
        portee="groupe",
        cible_groupe=str(classe),
        p_mode="fixe",
        p_debut="12:00",
        p_fin="13:00",
        niveau="important",
        periode="dates",
    )
    attendu = "Pause déjeuner vaut toute l&#39;année ou un semestre entier, sans dates"
    for dates in ({"date_debut": "2026-11-02", "date_fin": "2026-12-18"}, {}):
        refus = site.post("/regles/nouveau", data={**donnees, **dates})
        assert refus.status_code == 422
        erreur = re.search(r'id="erreur-periode">([^<]+)<', refus.text).group(1)
        assert erreur.startswith(attendu), dates  # erreur globale « periode » sous la période
        assert '<option value="dates" selected>' in refus.text  # la saisie reste visible
        apercu = site.post("/regles/apercu", data={**donnees, **dates}, headers=HTMX).text
        assert apercu.count("sans dates") == 1
    with session_base() as session:
        assert len(service_regles.lister(session, ids.annee)) == 4  # rien n'a été créé


def test_erreurs_globales_du_service_sous_leur_bloc(site, ids):
    definition = CATALOGUE_TYPES[TypeRegle.MAX_PAR_JOUR]
    reparties = erreurs_par_champ(
        {
            "portee": "Portée mal formée.",
            "portee.cible": "Cible inconnue.",
            "periode": "Période mal formée.",
            "parametres": "Paramètres mal formés.",
            "parametres.inconnu": "Paramètre inconnu.",
            "parametres.maximum": "Trop grand.",
            "origine": "Origine inconnue.",
        },
        "etablissement",
        definition,
    )
    assert reparties.champs == {
        "portee": "Portée mal formée. Cible inconnue.",  # pas de liste de cibles ici
        "periode": "Période mal formée.",
        CLE_BLOC_PARAMETRES: "Paramètres mal formés. Paramètre inconnu.",
        "p_maximum": "Trop grand.",
    }
    assert reparties.generales == ["Origine inconnue."]

    with session_base() as session:
        annee = session.get(m.AnneeScolaire, ids.annee)
        rubriques = rubriques_regle(
            session,
            definition,
            annee,
            {"portee": "etablissement", "periode": "annee"},
            reparties.champs,
            lire_parametres(session),
        )
    bloc = next(r for r in rubriques if r.titre == "Paramètres")
    assert bloc.erreur == "Paramètres mal formés. Paramètre inconnu."
    assert bloc.conteneur == {}  # un bloc en erreur n'est jamais masqué
    periode = next(c for r in rubriques for c in r.champs if c.nom == "periode")
    assert periode.erreur == "Période mal formée."


def test_ajout_jours_travailles_avec_apercu_et_erreurs(site, ids):
    classe = _id(m.Groupe, m.Groupe.code, "A1-G2")
    donnees = _nouvelle(
        ids.annee,
        "JOURS_TRAVAILLES",
        portee="groupe",
        cible_groupe=str(classe),
        p_jours=["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"],
        niveau="important",
    )
    apercu = site.post("/regles/apercu", data=donnees, headers=HTMX).text
    assert "« La classe A1-G2 a aussi cours le samedi. »" in apercu

    vide = site.post("/regles/nouveau", data={**donnees, "p_jours": []})
    assert vide.status_code == 422
    assert re.search(r'id="erreur-p_jours">Au moins un jour', vide.text)
    sans_cible = site.post("/regles/nouveau", data={**donnees, "cible_groupe": ""})
    assert 'id="erreur-cible_groupe"' in sans_cible.text
    # Saisie refusée : l'aperçu est redemandé au chargement, il liste ce qui reste à corriger.
    assert re.search(r'<section class="apercu-regle"[^>]*hx-trigger="load, ', sans_cible.text)
    apercu_vide = site.post("/regles/apercu", data={**donnees, "p_jours": []}).text
    assert "À compléter ou corriger" in apercu_vide and "Jours travaillés" in apercu_vide

    cree = site.post("/regles/nouveau", data=donnees, follow_redirects=True)
    assert "Règle R005 créée : La classe A1-G2 a aussi cours le samedi." in cree.text
    regle = _regle(ids.annee, "R005")
    assert regle.etat is EtatRegle.ACTIVE and regle.niveau.value == "important"


def test_ajout_pause_dejeuner_d_un_enseignant(site, ids):
    donnees = _nouvelle(
        ids.annee,
        "PAUSE_DEJEUNER",
        portee="enseignant",
        cible_enseignant=str(ids.enseignant_martin),
        p_mode="libre",
        p_debut="11:45",
        p_fin="14:15",
        p_duree="200",
        niveau="important",
        periode="semestre",
        semestre="S1",
    )
    trop = site.post("/regles/nouveau", data=donnees)
    assert trop.status_code == 422 and 'id="erreur-p_duree"' in trop.text
    assert 'value="11:45"' in trop.text  # la saisie est conservée
    apercu = site.post("/regles/apercu", data=donnees, headers=HTMX).text
    assert apercu.count("Durée minimale") == 1  # libellé non répété devant le message
    sans_semestre = site.post("/regles/nouveau", data={**donnees, "p_duree": "75", "semestre": ""})
    assert re.search(r'id="erreur-semestre">Choisir le semestre', sans_semestre.text)

    cree = site.post("/regles/nouveau", data={**donnees, "p_duree": "75"}, follow_redirects=True)
    assert "pause déjeuner libre d&#39;au moins 1 h 15 entre 11 h 45 et 14 h 15" in cree.text
    regle = _regle(ids.annee, "R005")
    assert regle.regle.parametres["duree"] == 75 and regle.regle.semestre_code == "S1"


def test_ajout_salles_imposees_et_portee_refusee(site, ids):
    tp = _id(m.TypeSeance, m.TypeSeance.code, "TP")
    donnees = _nouvelle(
        ids.annee,
        "SALLES",
        portee="type_seance",
        cible_type_seance=str(tp),
        p_mode="imposer",
        p_salles=["L115", "N310"],
        niveau="necessaire",
    )
    sans_salle = site.post("/regles/nouveau", data={**donnees, "p_salles": []})
    assert 'id="erreur-p_salles"' in sans_salle.text
    besoin = site.post("/regles/nouveau", data={**donnees, "portee": "besoin", "cible_besoin": "1"})
    assert re.search(r'id="erreur-portee">Pour un seul besoin', besoin.text)

    cree = site.post("/regles/nouveau", data=donnees, follow_redirects=True)
    assert "Règle R005 créée" in cree.text and "L115" in cree.text
    assert _regle(ids.annee, "R005").regle.parametres["salles"] == ["L115", "N310"]


# --------------------------------------------------------------------------- #
# Modification et états
# --------------------------------------------------------------------------- #


def test_modification_preremplie_et_doublon_refuse(site, ids):
    r002 = _regle(ids.annee, "R002")
    page = site.get(f"/regles/{r002.id}/modifier").text
    assert 'value="12:00"' in page and 'name="p_duree" value="60"' in page
    assert '<option value="libre" selected>' in page and 'name="_id"' in page

    donnees = {
        "portee": "groupe",
        "cible_groupe": str(ids.classe_g1),
        "p_mode": "libre",
        "p_debut": "12:00",
        "p_fin": "14:00",
        "p_duree": "90",
        "niveau": "facultatif",
        "periode": "semestre",
        "semestre": "S1",
        "_retour": "/regles?etat=active",
    }
    apercu = site.post(
        "/regles/apercu",
        data={**donnees, "type": "PAUSE_DEJEUNER", "annee": str(ids.annee), "_id": r002.id},
    )
    assert "1 h 30" in apercu.text  # pas de doublon avec elle-même
    reponse = site.post(f"/regles/{r002.id}/modifier", data=donnees, follow_redirects=False)
    assert reponse.headers["location"] == "/regles?etat=active"
    regle = _regle(ids.annee, "R002")
    assert regle.regle.parametres["duree"] == 90 and regle.niveau.value == "facultatif"

    doublon = _nouvelle(ids.annee, "PAUSE_DEJEUNER", **{k: v for k, v in donnees.items()})
    refus = site.post("/regles/nouveau", data=doublon)
    assert refus.status_code == 422
    assert re.search(r'id="erreur-portee">La règle R002 règle déjà', refus.text)


def test_etats_activer_ecarter_desactiver_tout_confirmer(site, ids):
    r003, r004 = _regle(ids.annee, "R003"), _regle(ids.annee, "R004")
    site.post(f"/regles/{r003.id}/etat", data={"etat": "active"})
    site.post(f"/regles/{r004.id}/etat", data={"etat": "inactive"})
    assert _regle(ids.annee, "R003").etat is EtatRegle.ACTIVE
    assert _regle(ids.annee, "R004").etat is EtatRegle.INACTIVE
    page = site.get("/regles").text
    assert "proposées à confirmer" not in page and "Désactiver" in page

    site.post(f"/regles/{r004.id}/etat", data={"etat": "proposee"})
    reponse = site.post("/regles/confirmer", data={"annee": ids.annee}, follow_redirects=True)
    assert "1 règle confirmée en 2026-2027." in reponse.text
    assert site.post(f"/regles/{r003.id}/etat", data={"etat": "autre"}).status_code == 422


# --------------------------------------------------------------------------- #
# Suppressions
# --------------------------------------------------------------------------- #


def test_suppression_d_une_regle_sur_place_et_sans_javascript(site, ids):
    r003 = _regle(ids.annee, "R003")
    liste = f"/regles?annee={ids.annee}&etat=proposee"
    entetes = {**HTMX, "HX-Current-URL": f"http://testserver{liste}"}
    ligne = site.get(f"/regles/{r003.id}/ligne/suppression", headers=entetes).text
    assert "Supprimer définitivement la règle R003 ?" in ligne and 'colspan="7"' in ligne
    zone = site.post(f"/regles/{r003.id}/ligne/supprimer", headers=entetes).text
    assert _codes(zone) == ["R004"] and "Règle R003 supprimée" in zone

    r004 = _regle(ids.annee, "R004")
    page = site.get(f"/regles/{r004.id}/supprimer", params={"retour": liste}).text
    assert "Supprimer définitivement" in page
    reponse = site.post(
        f"/regles/{r004.id}/supprimer", data={"_retour": liste}, follow_redirects=False
    )
    assert reponse.headers["location"] == liste
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Regle)) == 2


def test_suppression_d_une_cible_et_de_ses_regles(site, ids, caplog):
    with session_base() as session:
        enseignant = operations.creer(
            session, "enseignant", {"identifiant": "ENS099", "nom": "Roux", "civilite": "Mme"}
        )
        service_regles.creer(
            session,
            {
                "type": "CRENEAUX_INTERDITS",
                "annee": ids.annee,
                "portee": {"nature": "enseignant", "cible": enseignant["id"]},
                "parametres": {"jours": ["lundi"], "debut": "08:30", "fin": "10:30"},
            },
        )
    ident = enseignant["id"]
    page = site.get(f"/enseignants/{ident}/supprimer").text
    assert "Règles personnalisées concernées" in page
    assert "Mme Roux n&#39;a pas cours le lundi de 8 h 30 à 10 h 30." in page
    regle = _regle(ids.annee, "R005")
    assert f'href="/regles/{regle.id}/modifier?retour=' in page
    assert f'href="/regles/{regle.id}/supprimer?retour=' in page
    assert "Supprimer cet enseignant et sa règle" in page
    assert f'name="_regles" value="{regle.id}"' in page

    sans_accord = site.post(f"/enseignants/{ident}/supprimer", data={}, follow_redirects=True)
    assert "concerné par 1 règle(s) personnalisée(s) (R005 (2026-2027))" in sans_accord.text
    with caplog.at_level(logging.INFO):
        reponse = site.post(
            f"/enseignants/{ident}/supprimer",
            data={"_regles": str(regle.id)},
            follow_redirects=True,
        )
    assert "Enseignant « Mme Roux » supprimé. 1 règle personnalisée supprimée" in reponse.text
    assert "Règle R005 (2026-2027) supprimée avec enseignant « Mme Roux »" in caplog.text
    with session_base() as session:
        assert session.get(m.Enseignant, ident) is None
        assert session.scalar(select(func.count()).where(m.Regle.code == "R005")) == 0


def test_salle_citee_retiree_des_regles_et_cible_bloquee(site, ids):
    tp = _id(m.TypeSeance, m.TypeSeance.code, "TP")
    with session_base() as session:
        for salles in (["L115", "N310"], ["L115"]):
            service_regles.creer(
                session,
                {
                    "type": "SALLES",
                    "annee": ids.annee,
                    "portee": {"nature": "type_seance", "cible": tp},
                    "parametres": {"mode": "imposer", "salles": salles},
                },
            )
    l115 = _id(m.Salle, m.Salle.code, "L115")
    ligne = site.get(
        f"/salles/{l115}/ligne/suppression",
        headers={**HTMX, "HX-Current-URL": "http://testserver/salles"},
    ).text
    assert "L115 sera retiré de ses salles" in ligne
    assert "Sera supprimée (elle ne vaut plus sans L115)" in ligne
    assert "et ses 2 règles" in ligne and "hx-vals=" in ligne
    ids_regles = re.search(r'"_regles": "([\d,]+)"', ligne)
    zone = site.post(
        f"/salles/{l115}/ligne/supprimer",
        data={"_regles": ids_regles.group(1)},
        headers={**HTMX, "HX-Current-URL": "http://testserver/salles"},
    ).text
    assert "Salle « L115 (ISSY) » supprimée. 2 règles personnalisées" in zone
    assert _regle(ids.annee, "R005").regle.parametres["salles"] == ["N310"]
    with session_base() as session:
        assert session.scalar(select(func.count()).where(m.Regle.code == "R006")) == 0

    # Une cible encore utilisée par des besoins : règles listées, pas de bouton.
    groupe = site.get(f"/groupes/{ids.classe_g1}/supprimer").text
    assert "Suppression impossible" in groupe and "R002" in groupe
    assert "et sa règle" not in groupe


def test_type_de_seance_cite_par_une_regle_de_base(site, ids):
    cm = _id(m.TypeSeance, m.TypeSeance.code, "CM")
    page = site.get(f"/types-seance/{cm}/supprimer").text
    assert "R003" in page and "Sera supprimée (elle ne vaut plus sans CM)" in page


# --------------------------------------------------------------------------- #
# Paramètre des jours travaillés
# --------------------------------------------------------------------------- #


def test_parametre_jours_travailles_en_sept_cases(client):
    page = client.get("/parametres").text
    assert page.count('name="jours_travailles"') == 7
    assert 'name="jours_travailles" value="4" checked' in page
    assert 'name="jours_travailles" value="5">' in page  # samedi non coché
    assert "Par défaut : lundi, mardi, mercredi, jeudi, vendredi." in page

    def saisies(jours):
        page = client.get("/parametres").text
        valeurs = dict(
            re.findall(
                r'<input type="(?:number|time)" id="champ-\w+" name="(\w+)" value="([^"]*)"', page
            )
        )
        valeurs["dejeuner_mode"] = "fixe"
        return {**valeurs, "jours_travailles": jours}

    reponse = client.post(
        "/parametres", data=saisies(["0", "1", "2", "3", "4", "5"]), follow_redirects=True
    )
    assert "Paramètres enregistrés." in reponse.text
    with session_base() as session:
        assert lire_parametres(session).jours_travailles == (0, 1, 2, 3, 4, 5)

    vide = client.post("/parametres", data=saisies([]))
    assert vide.status_code == 422
    assert re.search(r'id="erreur-jours_travailles">Au moins un jour', vide.text)
    with session_base() as session:
        assert lire_parametres(session).jours_travailles != JOURS_TRAVAILLES_PAR_DEFAUT


# --------------------------------------------------------------------------- #
# Propositions à la création d'une année
# --------------------------------------------------------------------------- #


def test_creation_d_une_annee_propose_les_regles(site, ids):
    reponse = site.post(
        "/calendrier/annees/par-defaut", data={"annee_debut": "2027"}, follow_redirects=True
    )
    assert "Année scolaire 2027-2028 créée" in reponse.text
    assert "2 règles proposées, à revoir dans Règles personnalisées." in reponse.text
    nouvelle = _id(m.AnneeScolaire, m.AnneeScolaire.libelle, "2027-2028")
    page = site.get(f"/regles?annee={nouvelle}").text
    assert _codes(page) == ["R001", "R002"] and "2 règles proposées à confirmer" in page

    encore = site.post("/regles/proposer", data={"annee": nouvelle}, follow_redirects=True)
    assert "0 règle proposée pour 2027-2028, 2 déjà présente(s)" in encore.text


def test_creation_d_une_annee_par_le_formulaire_annonce_les_regles_proposees(site, ids):
    reponse = site.post(
        "/calendrier/annees",
        data={"libelle": "2028-2029", "date_debut": "2028-09-01", "date_fin": "2029-08-31"},
        follow_redirects=True,
    )
    nouvelle = _id(m.AnneeScolaire, m.AnneeScolaire.libelle, "2028-2029")
    assert str(reponse.url).endswith(f"/regles?annee={nouvelle}&etat=proposee")
    assert (
        "Année scolaire « 2028-2029 » créée. 2 règles personnalisées proposées pour cette "
        "année, ci-dessous : sans effet tant qu&#39;elles ne sont pas confirmées."
    ) in reponse.text
    assert _codes(reponse.text) == ["R001", "R002"]


def test_proposer_pour_une_annee_existante_liste_les_regles_a_revoir(site, ids):
    with session_base() as session:
        nouvelle = operations.creer(
            session,
            "annee_scolaire",
            {"libelle": "2027-2028", "date_debut": "2027-09-01", "date_fin": "2028-08-31"},
        )
        # La création générique propose aussi les règles de base.
        assert len(service_regles.lister(session, nouvelle["id"])) == 2
    reponse = site.post("/regles/proposer", data={"annee": nouvelle["id"]}, follow_redirects=True)
    assert "2 à revoir (cible absente cette année)" in reponse.text


# --------------------------------------------------------------------------- #
# Parcours réel sur le jeu moyen
# --------------------------------------------------------------------------- #


def test_parcours_reel_sur_le_jeu_moyen(client):
    recharger_jeu_fictif("moyen", 0)
    with session_base() as session:
        annee = session.scalar(select(m.AnneeScolaire.id))
        groupe = session.scalar(select(m.Groupe).where(m.Groupe.nature == "classe").limit(1))
        enseignant = session.scalar(select(m.Enseignant).limit(1))
    liste = client.get(f"/regles?annee={annee}").text
    avant = len(_codes(liste))

    formulaire = client.get(f"/regles/nouveau?annee={annee}&type=MAX_PAR_JOUR").text
    assert "Maximum par jour" in formulaire
    donnees = _nouvelle(
        annee,
        "MAX_PAR_JOUR",
        portee="groupe",
        cible_groupe=str(groupe.id),
        p_unite="seances",
        p_maximum="3",
        niveau="important",
    )
    apercu = client.post("/regles/apercu", data=donnees).text
    assert "apercu-phrase" in apercu and groupe.code in apercu
    cree = client.post("/regles/nouveau", data=donnees, follow_redirects=True).text
    assert "créée" in cree and len(_codes(cree)) == avant + 1
    code = re.search(r"Règle (R\d{3}) créée", cree).group(1)

    detail = _regle(annee, code)
    client.post(
        f"/regles/{detail.id}/modifier", data={**donnees, "p_maximum": "4"}, follow_redirects=True
    )
    assert _regle(annee, code).regle.parametres["maximum"] == 4
    client.post(f"/regles/{detail.id}/etat", data={"etat": "inactive"})
    assert _regle(annee, code).etat is EtatRegle.INACTIVE

    with session_base() as session:
        service_regles.creer(
            session,
            {
                "type": "JOURS_TRAVAILLES",
                "annee": annee,
                "portee": {"nature": "enseignant", "cible": enseignant.id},
                "parametres": {"jours": ["lundi", "mardi"]},
            },
        )
    page = client.get(f"/enseignants/{enseignant.id}/supprimer").text
    assert "Règles personnalisées concernées" in page
    assert enseignant.nom in page and "ne travaille que le lundi et le mardi" in page


def test_aucune_page_des_regles_ne_renvoie_de_trace(site, ids):
    adresses = ["/regles", f"/regles?annee={ids.annee}&q=x&page=1", "/regles/nouveau"]
    adresses += [f"/regles/nouveau?annee={ids.annee}&type={t}" for t in CATALOGUE_TYPES]
    adresses += [f"/regles/{n}/modifier" for n in (1, 2, 3, 4)]
    adresses += ["/regles/1/supprimer", "/regles/1/ligne", "/regles/1/ligne/suppression"]
    for adresse in adresses:
        reponse = site.get(adresse)
        assert reponse.status_code == 200, adresse
        assert "Traceback" not in reponse.text and "Une erreur inattendue" not in reponse.text
    for type_regle in CATALOGUE_TYPES:  # aperçu d'un formulaire vide : message, jamais de 500
        apercu = site.post("/regles/apercu", data={"type": type_regle, "annee": ids.annee})
        assert apercu.status_code == 200, type_regle
    assert site.get("/regles/999/modifier").status_code == 404
    assert site.get("/regles/nouveau?type=INCONNU").status_code == 404
