"""Niveaux de règles sur l'écran « Générer », le suivi, l'historique et « À traiter ».

Chaque règle modifiable se règle à trois niveaux (nécessaire, important,
facultatif) ; les six règles qui définissent le problème restent toujours
nécessaires ; l'écran ne permet plus d'ignorer une règle.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select

from edt import solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CATALOGUE, CodeContrainte, Niveau, niveaux_par_defaut
from edt.domaine.enumerations import Gravite
from edt.services import generation
from edt.solveur.contrat import ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie

from .conftest import DELAI

MODIFIABLES = [code for code, entree in CATALOGUE.items() if entree.modifiable]
FIXES = [code for code, entree in CATALOGUE.items() if not entree.modifiable]


def _lancer(client, semestre_id: int, **niveaux: str):
    """Envoie le formulaire avec seulement les niveaux donnés (les autres : défaut)."""
    donnees = {"semestre_id": semestre_id, "duree": "court"}
    donnees.update({f"niveau_{code}": valeur for code, valeur in niveaux.items()})
    return client.post("/calculs", data=donnees, follow_redirects=False)


def _calcul_lance(client, semestre_id: int, **niveaux: str) -> generation.SuiviCalcul:
    reponse = _lancer(client, semestre_id, **niveaux)
    assert reponse.status_code == 303, reponse.text
    calcul_id = int(reponse.headers["location"].rsplit("/", 1)[1])
    return generation.attendre_calcul(calcul_id, DELAI)


def _nb_calculs() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(m.Calcul))


def _coche(page: str, code: str, niveau: str) -> bool:
    return f'name="niveau_{code}" value="{niveau}" checked' in page


def test_trois_niveaux_par_regle_modifiable_et_six_regles_toujours_necessaires(jeu_minimal, client):
    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text

    assert len(FIXES) == 6
    for code in MODIFIABLES:
        for niveau in Niveau:
            assert f'name="niveau_{code}" value="{niveau.value}"' in page
        assert _coche(page, code, CATALOGUE[code].niveau_defaut.value)  # défaut proposé
    for code in FIXES:
        assert f'name="niveau_{code}"' not in page
    assert page.count("Toujours nécessaire</span>") == 6
    assert page.count("(par défaut)") == len(MODIFIABLES)  # défaut signalé discrètement
    assert f'title="{Niveau.FACULTATIF.description}"' in page.replace("&#39;", "'")
    # La pause déjeuner a son niveau, avec sa note ; plus de case pour ignorer une règle.
    assert 'name="niveau_DEJEUNER"' in page
    assert "Ne s&#39;applique pas sans pause déjeuner" in page
    assert 'type="checkbox" name="contraintes"' not in page


def test_niveau_choisi_transmis_et_le_reste_par_defaut(jeu_minimal, client):
    suivi = _calcul_lance(client, jeu_minimal.semestre_id, PAUSE="facultatif")

    assert suivi.reussi, suivi.message
    assert suivi.niveaux[CodeContrainte.PAUSE] is Niveau.FACULTATIF
    assert suivi.niveaux == {**niveaux_par_defaut(), CodeContrainte.PAUSE: Niveau.FACULTATIF}
    assert suivi.contraintes_desactivees == ()


def test_preremplissage_avec_les_niveaux_du_dernier_calcul(jeu_minimal, client):
    _calcul_lance(client, jeu_minimal.semestre_id, PAUSE="facultatif", EQUIPEMENT="necessaire")

    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text

    assert _coche(page, "PAUSE", "facultatif")
    assert _coche(page, "EQUIPEMENT", "necessaire")
    assert _coche(page, "MAX_ENSEIGNANT", "important")  # non choisi : son défaut


def test_niveau_illisible_ou_interdit_reaffiche_la_page_sans_rien_lancer(jeu_minimal, client):
    semestre_id = jeu_minimal.semestre_id
    cas = (
        ({"PAUSE": "tres_important"}, "niveau inconnu"),
        ({"SALLE_UNIQUE": "facultatif"}, "définit le problème"),
        ({"REGLE_INVENTEE": "important"}, "contrainte inconnue"),
    )
    for niveaux, attendu in cas:
        reponse = _lancer(client, semestre_id, CAMPUS_TRAJET="facultatif", **niveaux)
        assert reponse.status_code == 422, niveaux
        assert "Le calcul n&#39;a pas été lancé" in reponse.text and attendu in reponse.text
        assert "Traceback" not in reponse.text
        # Le formulaire revient, avec les choix valides gardés.
        assert _coche(reponse.text, "CAMPUS_TRAJET", "facultatif")
    assert _nb_calculs() == 0


def test_relances_reprennent_les_niveaux_du_calcul_d_origine(jeu_minimal, client, monkeypatch):
    monkeypatch.setattr(
        solveur,
        "resoudre",
        lambda instantane, options, arret=None: ResultatSolveur(StatutSolveur.TEMPS_DEPASSE),
    )
    origine = _calcul_lance(
        client, jeu_minimal.semestre_id, PAUSE="facultatif", CAMPUS_TRAJET="important"
    )

    relances = []
    for donnees in ({"longue": "1"}, {"autre_solution": "1"}):
        reponse = client.post(
            f"/calculs/{origine.id}/relancer", data=donnees, follow_redirects=False
        )
        assert reponse.status_code == 303, reponse.text
        relance_id = int(reponse.headers["location"].rsplit("/", 1)[1])
        relances.append(generation.attendre_calcul(relance_id, DELAI))

    assert origine.niveaux[CodeContrainte.CAMPUS_TRAJET] is Niveau.IMPORTANT
    assert all(relance.niveaux == origine.niveaux for relance in relances)


def test_suivi_et_historique_disent_les_niveaux_modifies_et_les_entorses(jeu_minimal, client):
    defaut = _calcul_lance(client, jeu_minimal.semestre_id)
    choisi = _calcul_lance(
        client, jeu_minimal.semestre_id, PAUSE="facultatif", CAMPUS_TRAJET="important"
    )
    assert defaut.reussi and choisi.reussi
    with session_base() as session:
        calcul = session.get(m.Calcul, choisi.id)
        calcul.statistiques = {
            **calcul.statistiques,
            "entorses": {"PAUSE": 2, "CAMPUS_TRAJET": 1, "EQUIPEMENT": 0},
        }

    page = client.get(f"/calculs/{choisi.id}").text
    assert (
        "<strong>Règles :</strong> Trajet entre campus : important ; "
        "Pause entre deux séances : facultatif</p>"
    ) in page
    assert (
        "<strong>Entorses :</strong> 1 entorse à une règle importante : Trajet entre campus (1) ; "
        "2 entorses à des règles facultatives : Pause entre deux séances (2)</p>"
    ) in page
    assert "Équipements de la salle (0)" not in page

    tout_par_defaut = client.get(f"/calculs/{defaut.id}").text
    assert "<strong>Règles :</strong>" not in tout_par_defaut
    assert "<strong>Entorses :</strong>" not in tout_par_defaut

    historique = client.get(f"/versions?semestre_id={jeu_minimal.semestre_id}").text
    assert "Trajet entre campus : important ; Pause entre deux séances : facultatif" in historique
    assert '<span class="discret">par défaut</span>' in historique

    # Un décompte illisible est dit, pas tu.
    with session_base() as session:
        calcul = session.get(m.Calcul, choisi.id)
        calcul.statistiques = {**calcul.statistiques, "entorses": {"PAUSE": "beaucoup"}}
    assert (
        "décompte des entorses de ce calcul est illisible"
        in client.get(f"/calculs/{choisi.id}").text
    )


def test_ancien_calcul_a_regle_ignoree_reste_lisible(jeu_minimal, client):
    suivi = _calcul_lance(client, jeu_minimal.semestre_id)
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        anciens = {cle: v for cle, v in calcul.parametres.items() if cle != "niveaux"}
        calcul.parametres = anciens  # enregistré avant les niveaux de règles
        calcul.contraintes_desactivees = ["PAUSE"]

    page = client.get(f"/calculs/{suivi.id}").text
    assert "Règles ignorées pour ce calcul" in page and "Pause entre deux séances" in page
    historique = client.get(f"/versions?semestre_id={jeu_minimal.semestre_id}").text
    assert "ignorées : Pause entre deux séances" in historique
    # L'écran ne propose plus d'ignorer : la règle revient à son niveau par défaut.
    formulaire = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert _coche(formulaire, "PAUSE", "important")


@dataclass(frozen=True)
class AnomalieAvecNiveau(Anomalie):
    """Anomalie telle que le lot vérificateur la livrera : avec le niveau de sa règle."""

    niveau: Niveau | None = None


def test_a_traiter_etiquette_le_niveau_des_anomalies(version_generee, client, faux_verificateur):
    faux_verificateur.hebdo = [
        AnomalieAvecNiveau(
            CodeContrainte.PAUSE,
            Gravite.AVERTISSEMENT,
            "Pas de pause entre deux séances du groupe A1.",
            niveau=Niveau.IMPORTANT,
        ),
        AnomalieAvecNiveau(
            CodeContrainte.CAMPUS_TRAJET,
            Gravite.INFORMATION,
            "Trajet court entre deux campus.",
            niveau=Niveau.FACULTATIF,
        ),
        AnomalieAvecNiveau(
            CodeContrainte.CAPACITE,
            Gravite.ERREUR,
            "La salle N016 est trop petite.",
            niveau=Niveau.NECESSAIRE,
        ),
        Anomalie(CodeContrainte.HORAIRES, Gravite.ERREUR, "Séance hors de la grille."),
    ]

    page = client.get(f"/a-traiter?semestre_id={version_generee.semestre_id}").text

    assert page.count(">règle importante<") == 1 and page.count(">règle facultative<") == 1
    ligne_capacite = page.split("La salle N016 est trop petite.", 1)[1].split("</li>", 1)[0]
    assert "règle" not in ligne_capacite  # nécessaire : pas d'étiquette
    ligne_horaires = page.split("Séance hors de la grille.", 1)[1].split("</li>", 1)[0]
    assert "règle" not in ligne_horaires  # anomalie sans niveau : rien
