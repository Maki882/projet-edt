"""Niveaux de règles sur l'écran « Générer », le suivi, l'historique et « À traiter ».

Chaque règle modifiable se règle à trois niveaux (nécessaire, important,
facultatif) ; les six règles qui définissent le problème restent toujours
nécessaires ; l'écran ne propose pas d'ignorer une règle.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from tests.outils.web_edt.aides import DELAI

from edt import solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CATALOGUE, CodeContrainte, Niveau, niveaux_par_defaut
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import id_seance
from edt.domaine.parametres import DEJEUNER_AUCUN, DEJEUNER_FIXE
from edt.services import generation
from edt.services.emploi_du_temps import modifications, raccords
from edt.services.parametres import ecrire_parametres, lire_parametres
from edt.solveur.contrat import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie

MODIFIABLES = [code for code, entree in CATALOGUE.items() if entree.modifiable]
FIXES = [
    code
    for code, entree in CATALOGUE.items()
    if not entree.modifiable and code is not CodeContrainte.REGLE  # REGLE absente du tableau
]


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
    # La pause déjeuner a son niveau, avec sa note ; pas de case pour ignorer une règle.
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
    # Le nombre d'entorses et celui des anomalies peuvent différer : la page le dit.
    assert "Comptées par le calcul (une par séance, par paire ou par heure selon la règle)" in page

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
        calcul.parametres = anciens  # enregistré sans niveaux de règles
        calcul.contraintes_desactivees = ["PAUSE"]

    page = client.get(f"/calculs/{suivi.id}").text
    assert "Règles ignorées pour ce calcul :" in page and "Pause entre deux séances" in page
    assert "lancé avant les niveaux" not in page  # vrai aussi d'une relance ou de `generer -d`
    historique = client.get(f"/versions?semestre_id={jeu_minimal.semestre_id}").text
    assert "ignorées : Pause entre deux séances" in historique
    # L'écran ne propose pas d'ignorer : la règle revient à son niveau par défaut.
    formulaire = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert _coche(formulaire, "PAUSE", "important")


@dataclass(frozen=True)
class AnomalieAvecNiveau(Anomalie):
    """Anomalie telle que le vérificateur la rend : avec le niveau de sa règle."""

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


def test_niveau_refuse_garde_la_pause_dejeuner_saisie(jeu_minimal, client):
    """Un niveau refusé ne fait pas perdre la pause déjeuner saisie, ni ses propres fautes."""
    dejeuner = {"dejeuner": "libre", "dejeuner_debut": "12:00", "dejeuner_fin": "14:00"}
    reponse = client.post(
        "/calculs",
        data={
            "semestre_id": jeu_minimal.semestre_id,
            "niveau_PAUSE": "tres_important",
            "dejeuner_duree": "75",
            **dejeuner,
        },
        follow_redirects=False,
    )
    assert reponse.status_code == 422
    assert 'name="dejeuner" value="libre" checked' in reponse.text
    assert 'name="dejeuner_debut" value="12:00"' in reponse.text
    assert 'name="dejeuner_duree" value="75"' in reponse.text
    assert 'id="erreur-dejeuner_duree"' not in reponse.text  # saisie correcte

    fautive = client.post(
        "/calculs",
        data={
            "semestre_id": jeu_minimal.semestre_id,
            "niveau_PAUSE": "tres_important",
            "dejeuner_duree": "une heure",
            **dejeuner,
        },
        follow_redirects=False,
    )
    assert fautive.status_code == 422
    assert '<p class="erreur-champ" id="erreur-dejeuner_duree">' in fautive.text
    with session_base() as session:
        assert lire_parametres(session).dejeuner_mode == DEJEUNER_FIXE  # rien d'enregistré
    assert _nb_calculs() == 0


def test_niveau_du_dejeuner_tu_sans_pause_dejeuner(jeu_minimal, client):
    """Sans pause déjeuner, la règle DEJEUNER ne s'applique pas : son niveau n'est pas cité.

    Le calcul n'enregistre pas la pause déjeuner : ce sont les paramètres
    courants qui décident (`calculs.dejeuner_actif_du_calcul`).
    """
    with session_base() as session:
        ecrire_parametres(session, {"dejeuner_mode": DEJEUNER_AUCUN})
    suivi = _calcul_lance(
        client, jeu_minimal.semestre_id, DEJEUNER="facultatif", PAUSE="facultatif"
    )
    assert suivi.reussi and suivi.niveaux[CodeContrainte.DEJEUNER] is Niveau.FACULTATIF

    page = client.get(f"/calculs/{suivi.id}").text
    assert "<strong>Règles :</strong> Pause entre deux séances : facultatif</p>" in page
    historique = client.get(f"/versions?semestre_id={jeu_minimal.semestre_id}").text
    assert "Pause déjeuner : facultatif" not in historique

    with session_base() as session:
        ecrire_parametres(session, {"dejeuner_mode": DEJEUNER_FIXE})
    assert "Pause déjeuner : facultatif" in client.get(f"/calculs/{suivi.id}").text


def _deplacement_avec_entorse_pause(session, version_id: int) -> tuple[int, int, int, int] | None:
    """Un déplacement que le vérificateur accepte avec une seule sorte d'écart : PAUSE.

    Cherché parmi les débuts de la grille du jeu minimal, salle inchangée,
    avec les niveaux du calcul de la version (sans liste de contraintes).
    """
    seances = session.scalars(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.version_id == version_id)
        .order_by(m.SeanceHebdo.id)
    ).all()
    debuts = sorted({c.debut_min for c in session.scalars(select(m.Creneau))})
    fermeture = lire_parametres(session).fermeture_min
    for seance in seances:
        duree = seance.fin_min - seance.debut_min
        for jour in range(5):
            for debut in debuts:
                if (jour, debut) == (seance.jour, seance.debut_min) or debut + duree > fermeture:
                    continue
                anomalies = raccords.verifier_modification(
                    session,
                    version_id,
                    id_seance(seance.besoin_id, seance.rang),
                    jour,
                    debut,
                    debut + duree,
                    seance.salle_id,
                ).anomalies
                if anomalies and all(
                    a.code is CodeContrainte.PAUSE and a.gravite is Gravite.AVERTISSEMENT
                    for a in anomalies
                ):
                    return seance.id, jour, debut, seance.salle_id
    return None


def test_retouche_d_une_version_a_regle_ignoree_garde_les_niveaux(jeu_minimal):
    """Une règle ignorée au calcul ne fait pas vérifier la retouche en dur.

    Version calculée avec PAUSE importante et CAPACITE ignorée : une retouche
    qui crée une entorse à PAUSE passe avec un avertissement, comme « À
    traiter » la montre ; une salle déjà occupée reste refusée.
    """
    niveaux = niveaux_par_defaut()
    del niveaux[CodeContrainte.CAPACITE]  # règle ignorée lors du calcul
    assert niveaux[CodeContrainte.PAUSE] is Niveau.IMPORTANT
    suivi = generation.attendre_calcul(
        generation.lancer_calcul(jeu_minimal.semestre_id, OptionsSolveur(niveaux=niveaux)), DELAI
    )
    assert suivi.reussi and suivi.contraintes_desactivees == ("CAPACITE",)
    # Une pause réglementaire plus longue que l'écart entre deux créneaux (15 min) : une séance
    # accolée à une autre de la même ressource fait une entorse à PAUSE.
    with session_base() as session:
        ecrire_parametres(session, {"pause_min": 20})

    with session_base() as session:
        candidat = _deplacement_avec_entorse_pause(session, suivi.version_id)
    assert candidat is not None, "aucun déplacement du jeu minimal ne crée une entorse PAUSE seule"
    seance_id, jour, debut, salle = candidat
    with session_base() as session:
        accepte = modifications.modifier_placement(
            session, seance_id, jour=jour, debut=debut, salle_id=salle
        )
    assert accepte.enregistree, accepte.message
    assert accepte.niveau == "attention"
    assert {(a.code, a.gravite) for a in accepte.anomalies} == {
        (CodeContrainte.PAUSE, Gravite.AVERTISSEMENT)
    }

    # Une erreur réelle reste bloquante : la salle d'une autre séance, au même moment.
    with session_base() as session:
        deplacee = session.get(m.SeanceHebdo, seance_id)
        duree = deplacee.fin_min - deplacee.debut_min
        autre = next(
            s
            for s in session.scalars(
                select(m.SeanceHebdo)
                .where(m.SeanceHebdo.version_id == suivi.version_id)
                .order_by(m.SeanceHebdo.id)
            )
            if s.id != seance_id
            and s.salle_id is not None
            and s.jour < 5
            and s.debut_min + duree <= lire_parametres(session).fermeture_min
        )
        refus = modifications.modifier_placement(
            session, seance_id, jour=autre.jour, debut=autre.debut_min, salle_id=autre.salle_id
        )
    assert not refus.enregistree
    assert (CodeContrainte.SALLE_UNIQUE, Gravite.ERREUR) in {
        (a.code, a.gravite) for a in refus.anomalies
    }
