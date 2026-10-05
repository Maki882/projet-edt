"""Un test rapide par entité : la liste répond, le formulaire s'ouvre, une création valide passe."""

from __future__ import annotations

import pytest


def _donnees(ids) -> dict[str, tuple[str, dict]]:
    """Adresse et saisies valides d'une création, par entité."""
    return {
        "campus": ("/campus", {"code": "STAN", "nom": "Site voisin", "prefixe_salle": "Stan"}),
        "salle": (
            "/salles",
            {
                "code": "Stan301",
                "campus_id": ids.ndc,
                "type_salle_id": ids.type_cours,
                "capacite": 30,
            },
        ),
        "type_salle": ("/types-salle", {"code": "LANGUES", "libelle": "Salle de langues"}),
        "equipement": ("/equipements", {"code": "TBI", "libelle": "Tableau interactif"}),
        "enseignant": (
            "/enseignants",
            {
                "identifiant": "ENS099",
                "nom": "Durand",
                "statut": "vacataire",
                "max_minutes_jour": "6 h",
            },
        ),
        "groupe": (
            "/groupes",
            {
                "annee_scolaire_id": ids.annee,
                "code": "A1-G1c",
                "libelle": "Demi-groupe c",
                "nature": "demi_groupe",
                "parent_id": ids.classe_g1,
                "effectif": 10,
                "chevauchements": [ids.promo],
            },
        ),
        "etudiant": (
            "/etudiants",
            {"identifiant": "ETU099", "nom": "Lune", "groupes": [ids.classe_g1]},
        ),
        "matiere": (
            "/matieres",
            {"code": "IM.1102", "libelle": "Analyse", "promotion_id": ids.promo},
        ),
        "type_seance": (
            "/types-seance",
            {"code": "SOUT", "libelle": "Soutien", "couleur": "#123456"},
        ),
        "indisponibilite": (
            "/indisponibilites",
            {
                "cible": "salle",
                "salle_id": ids.salle_n016,
                "forme": "datee",
                "date_debut": "2026-12-01",
            },
        ),
        "annee_scolaire": (
            "/calendrier/annees",
            {"libelle": "2030-2031", "date_debut": "2030-09-01", "date_fin": "2031-08-31"},
        ),
        "semestre": (
            "/calendrier/semestres",
            {
                "annee_scolaire_id": ids.annee,
                "code": "S3",
                "libelle": "Session d'été",
                "date_debut": "2027-06-01",
                "date_fin": "2027-06-30",
            },
        ),
        "jour_ferme": (
            "/calendrier/jours-fermes",
            {
                "annee_scolaire_id": ids.annee,
                "libelle": "Pont",
                "nature": "autre",
                "date_debut": "10/11/2026",
                "groupe_id": ids.promo,
            },
        ),
        "creneau": ("/grille", {"jour": 5, "debut_min": "13:45", "fin_min": "15:45"}),
    }


ENTITES = (
    "campus",
    "salle",
    "type_salle",
    "equipement",
    "enseignant",
    "groupe",
    "etudiant",
    "matiere",
    "type_seance",
    "indisponibilite",
    "annee_scolaire",
    "semestre",
    "jour_ferme",
    "creneau",
)


@pytest.mark.parametrize("code", ENTITES)
def test_liste_formulaire_et_creation(site, ids, code):
    adresse, saisies = _donnees(ids)[code]

    assert site.get(adresse).status_code == 200
    assert site.get(f"{adresse}/nouveau").status_code == 200

    reponse = site.post(adresse, data=saisies, follow_redirects=False)
    assert reponse.status_code == 303, reponse.text
    assert "créé" in site.get(reponse.headers["location"]).text


def test_arbre_des_groupes(site, ids):
    page = site.get(f"/groupes/arbre?annee={ids.annee}").text
    assert page.index("A1-G1") < page.index("A1-G1a")  # la classe avant son demi-groupe
    assert "chevauche" in page
    assert site.get("/groupes/arbre?annee=999").status_code == 404


def test_matrice_des_trajets(site, ids):
    a, b = sorted((ids.ndc, ids.issy))
    page = site.get("/campus").text
    assert f'name="trajet_{a}_{b}" value="60"' in page

    refus = site.post("/campus/trajets", data={f"trajet_{a}_{b}": "beaucoup"})
    assert refus.status_code == 422 and "Nombre de minutes attendu." in refus.text

    site.post("/campus/trajets", data={f"trajet_{a}_{b}": "45"})
    assert f'name="trajet_{a}_{b}" value="45"' in site.get("/campus").text


def test_calendrier_et_jours_feries(site, ids):
    page = site.get("/calendrier").text
    assert "Année 2026-2027" in page and "Semestre 1 (2026-2027)" in page

    reponse = site.post(f"/calendrier/annees/{ids.annee}/feries", follow_redirects=True)
    assert "déjà présents" in reponse.text  # préremplis à l'initialisation

    creee = site.post(
        "/calendrier/annees/par-defaut", data={"annee_debut": "2028"}, follow_redirects=True
    )
    assert "Année scolaire 2028-2029 créée" in creee.text
    deja = site.post(
        "/calendrier/annees/par-defaut", data={"annee_debut": "2028"}, follow_redirects=True
    )
    assert "existe déjà" in deja.text


def test_creneau_refuse_une_fin_avant_le_debut(site):
    reponse = site.post("/grille", data={"jour": 5, "debut_min": "15:00", "fin_min": "14:00"})
    assert reponse.status_code == 422 and "La fin doit suivre le début." in reponse.text
