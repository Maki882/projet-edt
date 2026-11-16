"""Adresses mal copiées, liens périmés, cookies forgés : jamais d'erreur 500.

Une adresse mal copiée, un lien périmé, un cookie forgé, un fragment ouvert
sans HTMX, une base sans grille ou une version vide ne doivent jamais produire
d'erreur 500 : la page dit ce qui ne va pas (introuvable, saisie invalide) ou
retombe sur un affichage par défaut, en le disant.

Chaque test porte le marqueur `cas_limite` et sa docstring dit le cas et le
risque (« Cas : … Risque : … »). Cette banque seule :
`python -m pytest -m cas_limite tests/unitaires/web_edt tests/integration/web_edt`
(les bornes des identifiants, sans passer par les écrans, sont dans
`tests/unitaires/web_edt/test_cas_limites.py`).

Aides communes, dans `tests/outils/web_edt/aides.py` : `ENORME`, `HX`,
`creer_version_vide`, `premiere_occurrence`, `calcul_qui_tourne` ; fixtures
`reperes` et `client_sans_trace` dans `tests/integration/web_edt/conftest.py`.
Trois cas sont testés près de leur écran, avec le même
marqueur : l'option « Tous les semestres » (`test_a_traiter.py`), le jour où
une séance ne tient nulle part (`test_emploi_du_temps.py`), l'arrêt d'un
calcul terminé (`test_calculs.py`). Comportements attendus :
`docs/interface.md`, section « Cas limites ».
"""

from __future__ import annotations

import logging
import sqlite3

import pytest
from sqlalchemy import delete, func, select
from tests.outils.web_edt.aides import (
    CINQ_MILLE_CHIFFRES,
    DELAI,
    ENORME,
    HX,
    autre_ecrivain,
    calcul_qui_tourne,
    creer_version_vide,
)

from edt.config import charger_configuration
from edt.db import modeles as m
from edt.db.moteur import chemin_base, session_base
from edt.domaine.enumerations import EtatCalcul, StatutOccurrence
from edt.services import generation
from edt.services.emploi_du_temps import modifications, vues

# --------------------------------------------------------------------------- #
# Identifiants énormes, négatifs ou inconnus
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("g", [ENORME, "999999"], ids=["enorme", "inconnu"])
def test_identifiants_enormes_ou_negatifs_donnent_introuvable(reperes, client, caplog, g):
    """Cas : lien mal copié ou périmé dont l'identifiant a vingt chiffres, est négatif ou
    n'existe pas, sur chaque adresse des onglets Générer, versions, Emploi du temps, À traiter.
    Risque : OverflowError de SQLite non rattrapée, donc erreur 500 et ligne ERROR au journal,
    au lieu d'un « introuvable » (404).
    """
    formulaire = {"jour": 0, "debut": 510, "date": "2026-10-06", "verrou": "true"}
    salle_introuvable = {**formulaire, "salle_id": g}  # la séance existe, pas la salle choisie
    lectures = [
        (f"/emploi-du-temps?version_id={g}", None),
        ("/emploi-du-temps?version_id=-1", None),
        (f"/calculs/{g}", None),
        (f"/calculs/{g}/etat", HX),
        (f"/emploi-du-temps/seances/{g}/panneau", HX),
        (f"/emploi-du-temps/seances/{g}/formulaire", HX),
        (f"/emploi-du-temps/occurrences/{g}/panneau", HX),
        (f"/emploi-du-temps/occurrences/{g}/formulaire", HX),
        (f"/a-traiter?semestre_id={g}", None),
        ("/a-traiter?semestre_id=-1", None),
        (f"/calculs?semestre_id={g}", None),
        (f"/versions?semestre_id={g}", None),
        (f"/calculs/verifications?semestre_id={g}", HX),
    ]
    envois = [
        (f"/calculs/{g}/arreter", None, formulaire),
        (f"/calculs/{g}/relancer", None, formulaire),
        (f"/versions/{g}/activer", None, formulaire),
        (f"/versions/{g}/supprimer", None, formulaire),
        (f"/emploi-du-temps/versions/{g}/seances-datees", None, formulaire),
        (f"/emploi-du-temps/seances/{g}/modifier", HX, formulaire),
        (f"/emploi-du-temps/seances/{g}/verrou", None, formulaire),
        (f"/emploi-du-temps/seances/{reperes.seance}/modifier", HX, salle_introuvable),
        (f"/emploi-du-temps/occurrences/{g}/deplacer", HX, formulaire),
        (f"/emploi-du-temps/occurrences/{reperes.occurrence}/deplacer", HX, salle_introuvable),
        (f"/emploi-du-temps/occurrences/{g}/annuler", None, formulaire),
    ]
    with caplog.at_level(logging.INFO):
        reponses = [
            (adresse, client.get(adresse, headers=entetes or {}, follow_redirects=False))
            for adresse, entetes in lectures
        ] + [
            (
                adresse,
                client.post(adresse, data=corps, headers=entetes or {}, follow_redirects=False),
            )
            for adresse, entetes, corps in envois
        ]
    for adresse, reponse in reponses:
        assert reponse.status_code == 404, (adresse, reponse.status_code)
        assert "n&#39;existe pas" in reponse.text or "introuvable" in reponse.text, adresse
        assert "Traceback" not in reponse.text and "Overflow" not in reponse.text, adresse
    erreurs = [e.getMessage() for e in caplog.records if e.levelno >= logging.ERROR]
    assert not erreurs, erreurs


@pytest.mark.cas_limite
@pytest.mark.parametrize("parametre", ["enseignant_id", "salle_id", "groupe_id"])
def test_cible_enorme_dans_l_adresse_montre_la_premiere_classe(version_generee, client, parametre):
    """Cas : groupe, enseignant ou salle de vingt chiffres dans l'adresse de la grille.
    Risque : erreur 500 au lieu de la première classe, affichée en disant que la cible
    demandée n'existe pas.
    """
    page = client.get(
        f"/emploi-du-temps?version_id={version_generee.version_id}&{parametre}={ENORME}"
    )
    assert page.status_code == 200
    assert f"n° {ENORME} n&#39;existe pas" in page.text
    assert "Affichage de la première classe à la place" in page.text


@pytest.mark.cas_limite
def test_seance_enorme_dans_l_adresse_ouvre_la_page_sans_panneau(version_generee, client):
    """Cas : séance à ouvrir (`seance=h…` ou `o…`) de vingt ou 5 000 chiffres, nulle, en chiffres
    exotiques (« ² ») ou de préfixe inconnu dans l'adresse de la grille (lien abîmé, favori ancien).
    Risque : erreur 500 (`int()` refuse « ² » et plus de 4 300 chiffres) au lieu de la page sans
    panneau, avec un message qui ne recopie pas les 5 000 chiffres.
    """
    for seance in (
        f"h{ENORME}",
        f"o{ENORME}",
        "h0",
        "x12",
        "h²",
        f"h{CINQ_MILLE_CHIFFRES}",
        f"o{CINQ_MILLE_CHIFFRES}",
    ):
        page = client.get(
            f"/emploi-du-temps?version_id={version_generee.version_id}&seance={seance}"
        )
        assert page.status_code == 200, seance[:30]
        assert "La séance a pu être déplacée ou supprimée" in page.text, seance[:30]
        assert "1" * 100 not in page.text, seance[:30]  # ni dans le message, ni dans les liens


@pytest.mark.cas_limite
def test_cookie_de_cible_illisible_ou_enorme_est_ignore(version_generee, client, caplog):
    """Cas : cookie de dernière cible forgé ou laissé par une autre instance du même hôte
    (identifiant énorme ou négatif, valeur illisible, nature inconnue).
    Risque : l'onglet Emploi du temps répond 500 à chaque ouverture tant que le cookie
    n'est pas effacé.
    """
    with session_base() as session:
        defaut = vues.cible_par_defaut(session, version_generee.annee_scolaire_id).valeur
    for valeur in (
        f"enseignant:{ENORME}",
        "salle:-5",
        "groupe:abc",
        "enseignant:" + "9" * 5000,
        "inconnu:3",
    ):
        client.cookies.clear()  # sinon le cookie posé par la page précédente reste envoyé
        client.cookies.set("edt_derniere_cible", valeur)
        with caplog.at_level(logging.INFO, logger="edt.web.routes.emploi_du_temps"):
            caplog.clear()
            page = client.get("/emploi-du-temps")
        assert page.status_code == 200, valeur[:30]
        assert page.cookies.get("edt_derniere_cible") == defaut, valeur[:30]
        journal = caplog.text
        assert "Dernière cible consultée introuvable" in journal or "illisible" in journal


# --------------------------------------------------------------------------- #
# Fragments, panneaux, séances disparues
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_fragments_ouverts_sans_htmx_menent_a_la_page(reperes, client, faux_verificateur):
    """Cas : fragment HTMX (suivi d'un calcul, vérifications préalables, formulaire ou panneau
    d'une séance, compteur d'« À traiter ») ouvert sans en-tête HX-Request : adresse saisie,
    nouvel onglet, lien.
    Risque : un morceau de page nu, sans menu ni style, au lieu de la page complète.
    """
    attendus = {
        f"/calculs/{reperes.calcul}/etat": f"/calculs/{reperes.calcul}",
        f"/calculs/verifications?semestre_id={reperes.semestre}": (
            f"/calculs?semestre_id={reperes.semestre}"
        ),
        f"/emploi-du-temps/seances/{reperes.seance}/formulaire": f"seance=h{reperes.seance}",
        f"/emploi-du-temps/seances/{reperes.seance}/panneau": f"seance=h{reperes.seance}",
        f"/emploi-du-temps/occurrences/{reperes.occurrence}/formulaire": (
            f"seance=o{reperes.occurrence}"
        ),
        f"/emploi-du-temps/occurrences/{reperes.occurrence}/panneau": (
            f"seance=o{reperes.occurrence}"
        ),
        "/a-traiter/compteur": "/a-traiter",
    }
    for adresse, cible in attendus.items():
        sans = client.get(adresse, follow_redirects=False)
        assert sans.status_code == 303 and cible in sans.headers["location"], adresse
        avec = client.get(adresse, headers=HX)
        assert avec.status_code == 200 and "<html" not in avec.text, adresse


@pytest.mark.cas_limite
def test_panneau_d_une_seance_supprimee_entre_temps(reperes, client, faux_verificateur):
    """Cas : panneau, enregistrement ou lien d'une séance supprimée entre-temps (autre onglet,
    version régénérée).
    Risque : erreur 500, ou page entière dans le panneau, au lieu d'un « n'existe pas (ou plus) ».
    """
    with session_base() as session:
        session.execute(delete(m.SeanceHebdo).where(m.SeanceHebdo.id == reperes.seance))
    adresse = f"/emploi-du-temps/seances/{reperes.seance}"

    panneau = client.get(f"{adresse}/panneau", headers=HX)
    assert panneau.status_code == 404 and "<html" not in panneau.text
    assert "n&#39;existe pas (ou plus)" in panneau.text
    modification = client.post(f"{adresse}/modifier", data={"jour": 0, "debut": 510}, headers=HX)
    assert modification.status_code == 404
    page = client.get(f"/emploi-du-temps?version_id={reperes.version}&seance=h{reperes.seance}")
    assert page.status_code == 200
    assert "La séance a pu être déplacée ou supprimée" in page.text


@pytest.mark.cas_limite
def test_deplacement_vers_un_creneau_ou_un_jour_inexistant(reperes, client, faux_verificateur):
    """Cas : séance déplacée vers un début hors grille, un jour hors semaine, un jour ou une salle
    illisibles (« ² », 5 000 chiffres : formulaire forgé ou périmé).
    Risque : séance enregistrée à une place impossible, ou erreur 500, au lieu d'un refus
    lisible qui la laisse en place.
    """
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, reperes.seance)
        avant = (seance.jour, seance.debut_min)
    for donnees, attendu in (
        ({"jour": avant[0], "debut": 7}, "n&#39;est pas un début de créneau"),
        ({"jour": avant[0], "debut": 100000}, "n&#39;est pas un début de créneau"),
        ({"jour": 9, "debut": 510}, "Jour invalide"),
        ({"jour": "lundi", "debut": 510}, "Saisie invalide"),
        ({"jour": avant[0], "debut": avant[1], "salle_id": "²"}, "Salle illisible"),
        ({"jour": avant[0], "debut": avant[1], "salle_id": CINQ_MILLE_CHIFFRES}, "Salle illisible"),
    ):
        reponse = client.post(
            f"/emploi-du-temps/seances/{reperes.seance}/modifier", data=donnees, headers=HX
        )
        assert reponse.status_code == 422, donnees
        assert attendu in reponse.text and "Traceback" not in reponse.text, donnees
    assert faux_verificateur.appels == []  # refusé avant toute vérification
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, reperes.seance)
        assert (seance.jour, seance.debut_min) == avant


@pytest.mark.cas_limite
def test_seance_annulee_ni_reannulee_ni_deplacee(reperes, client, faux_verificateur):
    """Cas : séance datée annulée deux fois (deux onglets ouverts), puis déplacée.
    Risque : motif répété dans le commentaire, ou séance annulée qui revient en étant déplacée.
    """
    with session_base() as session:
        occurrence = session.get(m.Occurrence, reperes.occurrence)
        date, debut = occurrence.date, occurrence.debut_min
    adresse = f"/emploi-du-temps/occurrences/{reperes.occurrence}"
    assert client.post(f"{adresse}/annuler", data={"motif": "Grève"}).status_code == 200

    deux_fois = client.post(f"{adresse}/annuler", data={"motif": "Grève"})
    assert deux_fois.status_code == 409 and "déjà annulée" in deux_fois.text
    deplacee = client.post(
        f"{adresse}/deplacer", data={"date": date.isoformat(), "debut": debut}, headers=HX
    )
    assert deplacee.status_code == 409 and "annulée ne peut pas être déplacée" in deplacee.text
    with session_base() as session:
        annulee = session.get(m.Occurrence, reperes.occurrence)
        assert annulee.statut is StatutOccurrence.ANNULEE
        assert annulee.commentaire.count("Annulée : Grève") == 1  # motif pas répété


@pytest.mark.cas_limite
def test_deplacer_une_seance_datee_hors_du_semestre(reperes, client, faux_verificateur):
    """Cas : séance datée déplacée au 4 janvier 1999 ou au 4 janvier 2100 (date saisie à la main).
    Risque : séance placée hors du semestre, donc absente de la grille et des vérifications.
    """
    with session_base() as session:
        avant = session.get(m.Occurrence, reperes.occurrence).date
    for date in ("1999-01-04", "2100-01-04"):
        reponse = client.post(
            f"/emploi-du-temps/occurrences/{reperes.occurrence}/deplacer",
            data={"date": date, "debut": 510},
            headers=HX,
        )
        assert reponse.status_code == 422 and "en dehors du semestre" in reponse.text, date
    with session_base() as session:
        assert session.get(m.Occurrence, reperes.occurrence).date == avant


@pytest.mark.cas_limite
def test_date_tres_ancienne_ou_lointaine_sans_erreur(version_generee, client):
    """Cas : grille datée demandée au 1er janvier de l'an 1 ou au 31 décembre 9999 (adresse
    modifiée à la main).
    Risque : la semaine autour sort du calendrier de Python (OverflowError, erreur 500) au
    lieu de la semaine en cours du semestre, affichée en le disant.
    """
    for date in ("0001-01-01", "9999-12-31"):
        page = client.get(
            f"/emploi-du-temps?version_id={version_generee.version_id}&couche=date&date={date}"
        )
        assert page.status_code == 200, date
        assert "trop loin" in page.text, date


# --------------------------------------------------------------------------- #
# Filtres et paramètres inconnus
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("parametres", "attendu"),
    [
        ("couche=xyz", "couche"),
        ("affichage=mois", "affichage"),
        ("etendu=peutetre", "etendu"),
        ("date=pas-une-date", "date"),
        ("affichage=jour&jour=-1", "Jour invalide"),
        ("affichage=jour&jour=99", "Jour invalide"),
        ("groupe_id=1&salle_id=2", "Choisir un seul groupe"),
        ("cible=bureau:3", "Choix d&#39;affichage invalide"),
        ("cible=groupe:²", "Choix d&#39;affichage invalide"),
        pytest.param(
            f"cible=groupe:{CINQ_MILLE_CHIFFRES}",
            "Choix d&#39;affichage invalide",
            id="cible-de-5000-chiffres",
        ),
    ],
)
def test_filtres_inconnus_refuses_lisiblement(version_generee, client, parametres, attendu):
    """Cas : paramètre d'affichage inconnu ou contradictoire dans l'adresse de la grille
    (couche, affichage, étendu, date, jour, deux cibles, nature de cible).
    Risque : erreur 500, ou affichage faux sans le dire, au lieu d'une page 422 qui nomme
    le paramètre.
    """
    reponse = client.get(f"/emploi-du-temps?version_id={version_generee.version_id}&{parametres}")
    assert reponse.status_code == 422
    assert attendu in reponse.text and "Traceback" not in reponse.text
    assert "1" * 100 not in reponse.text  # la saisie n'est pas recopiée en entier


@pytest.mark.cas_limite
def test_hx_current_url_illisible_dans_les_panneaux(reperes, client, faux_verificateur, caplog):
    """Cas : panneau d'une séance ou d'une séance datée demandé avec un en-tête HX-Current-URL
    que `urlsplit` refuse (« http://[::1 » : navigateur ou extension qui l'abîme).
    Risque : erreur 500 au clic sur chaque séance de la grille ; le panneau doit s'ouvrir avec
    son adresse de retour par défaut, l'en-tête ignoré et journalisé.
    """
    entetes = {**HX, "HX-Current-URL": "http://[::1"}
    for adresse, retour in (
        (f"/emploi-du-temps/seances/{reperes.seance}/panneau", f"seance=h{reperes.seance}"),
        (
            f"/emploi-du-temps/occurrences/{reperes.occurrence}/panneau",
            f"seance=o{reperes.occurrence}",
        ),
    ):
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="edt.web.gabarits"):
            panneau = client.get(adresse, headers=entetes)
        assert panneau.status_code == 200 and "<html" not in panneau.text, adresse
        assert retour in panneau.text, adresse  # retour par défaut : la séance dans sa version
        assert any("HX-Current-URL" in r.getMessage() for r in caplog.records), adresse


# --------------------------------------------------------------------------- #
# Calculs : relance pendant un calcul, saisies extrêmes au lancement
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_relance_pendant_un_calcul_mene_au_calcul_en_cours(version_generee, client, monkeypatch):
    """Cas : « Relancer » (plus long, autre solution, simple) depuis le suivi d'un ancien calcul
    alors qu'un calcul du semestre tourne déjà.
    Risque : deux calculs en parallèle sur le même semestre, ou une erreur, au lieu d'être mené
    au calcul en cours.
    """
    with calcul_qui_tourne(version_generee.semestre_id, monkeypatch) as en_cours:
        for donnees in ({"longue": "1"}, {"autre_solution": "1"}, {}):
            reponse = client.post(
                f"/calculs/{version_generee.calcul_id}/relancer",
                data=donnees,
                follow_redirects=False,
            )
            assert reponse.status_code == 303, donnees
            assert reponse.headers["location"] == f"/calculs/{en_cours}", donnees
        assert "Un calcul est déjà en cours" in client.get(f"/calculs/{en_cours}").text
        with session_base() as session:  # le calcul de la version générée et celui en cours
            assert session.scalar(select(func.count()).select_from(m.Calcul)) == 2


class _ArretSuiviDUneEcriture:
    """Signal d'arrêt d'un calcul : à sa levée, une autre connexion écrit dans la base et valide
    aussitôt, sans attendre le verrou (`timeout=0`).

    Rejoue de façon déterministe la course vue en CI sous Linux : le fil du calcul, réveillé par
    l'arrêt, enregistre son état final avant que `demander_arret` ait écrit. Note dans `refusee`
    si l'écriture a trouvé le verrou pris (elle n'attend jamais : pas de blocage du test).
    """

    def __init__(self, arret, calcul_id: int) -> None:
        self._arret = arret
        self._calcul_id = calcul_id
        self.refusee: bool | None = None

    def set(self) -> None:
        brute = sqlite3.connect(
            chemin_base(charger_configuration().db_url), timeout=0, isolation_level=None
        )
        try:
            brute.execute("BEGIN IMMEDIATE")
            brute.execute(
                "UPDATE calcul SET message = 'écriture d''un autre écrivain' WHERE id = ?",
                (self._calcul_id,),
            )
            brute.execute("COMMIT")
            self.refusee = False
        except sqlite3.OperationalError as erreur:
            assert "locked" in str(erreur)
            self.refusee = True
        finally:
            brute.close()
        self._arret.set()


@pytest.mark.cas_limite
def test_arret_quand_un_autre_ecrivain_valide_entre_lecture_et_ecriture(jeu_minimal, monkeypatch):
    """Cas : « Arrêter le calcul » pendant qu'un autre écrivain (le fil du calcul qui enregistre
    son état final, réveillé par l'arrêt) valide entre la lecture du calcul et l'écriture de la
    demande d'arrêt.
    Risque : `demander_arret` lit dans une transaction différée puis écrit sur un instantané
    périmé : SQLite répond aussitôt « database is locked » sans attendre, la page renvoie 503 et
    la demande d'arrêt est perdue (vu en CI sous Linux, où le fil réveillé valide plus vite).
    """
    with calcul_qui_tourne(jeu_minimal.semestre_id, monkeypatch) as calcul_id:
        actif = generation._actifs[calcul_id]
        espion = _ArretSuiviDUneEcriture(actif.arret, calcul_id)
        monkeypatch.setitem(
            generation._actifs, calcul_id, generation._CalculActif(fil=actif.fil, arret=espion)
        )
        suivi = generation.demander_arret(calcul_id)
        assert suivi.arret_demande
        assert espion.refusee is not None  # le signal a bien été levé
        fin = generation.attendre_calcul(calcul_id, DELAI)
        assert fin.etat is EtatCalcul.ARRETE and fin.arret_demande


@pytest.mark.cas_limite
def test_saisies_de_cinq_mille_chiffres_au_lancement(jeu_minimal, client):
    """Cas : formulaire « Lancer le calcul » fabriqué : règle personnalisée `niveau_regle_<5 000
    chiffres>` ou `niveau_regle_²`, durée de pause de 5 000 chiffres ; même règle dans
    l'adresse des vérifications préalables.
    Risque : `int()` lève ValueError, erreur 500 au lancement au lieu d'un refus clair ; ou un
    calcul lancé sur une saisie illisible.
    """
    semestre = jeu_minimal.semestre_id
    base = {"semestre_id": semestre}
    for champ in (f"niveau_regle_{CINQ_MILLE_CHIFFRES}", "niveau_regle_²"):
        reponse = client.post("/calculs", data={**base, champ: "important"})
        assert reponse.status_code == 422, champ[:20]
        assert "n&#39;est pas (ou plus) active" in reponse.text, champ[:20]
        assert "1" * 100 not in reponse.text, champ[:20]  # le message ne recopie pas le champ
    pause = client.post(
        "/calculs",
        data={
            **base,
            "dejeuner": "libre",
            "dejeuner_debut": "12:00",
            "dejeuner_fin": "14:00",
            "dejeuner_duree": CINQ_MILLE_CHIFFRES,
        },
    )
    assert pause.status_code == 422 and "Durée minimale hors bornes" in pause.text
    verifications = client.get(
        "/calculs/verifications",
        params={"semestre_id": semestre, f"niveau_regle_{CINQ_MILLE_CHIFFRES}": "important"},
        headers=HX,
    )
    assert verifications.status_code == 422 and "n&#39;est pas (ou plus)" in verifications.text
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Calcul)) == 0  # rien lancé


# --------------------------------------------------------------------------- #
# Séance datée : motif d'annulation borné
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_motif_d_annulation_borne(reperes, client, faux_verificateur):
    """Cas : motif d'annulation d'une séance datée envoyé avec un caractère de plus que la limite
    (le navigateur borne le champ, pas un envoi fabriqué), puis exactement à la limite.
    Risque : 200 Ko enregistrés dans la remarque de la séance et réaffichés dans son panneau ;
    ou une séance annulée malgré le refus.
    """
    limite = modifications.MOTIF_MAX
    adresse = f"/emploi-du-temps/occurrences/{reperes.occurrence}"
    panneau = client.get(f"{adresse}/panneau", headers=HX).text
    assert f'maxlength="{limite}"' in panneau  # le champ du navigateur borne comme le serveur

    trop = client.post(f"{adresse}/annuler", data={"motif": "x" * (limite + 1)})
    assert trop.status_code == 422
    assert f"Motif trop long : {limite} caractères au plus (lu : {limite + 1})" in trop.text
    with session_base() as session:
        occurrence = session.get(m.Occurrence, reperes.occurrence)
        assert occurrence.statut is not StatutOccurrence.ANNULEE
        assert "xxx" not in (occurrence.commentaire or "")

    assert client.post(f"{adresse}/annuler", data={"motif": "x" * limite}).status_code == 200
    with session_base() as session:
        occurrence = session.get(m.Occurrence, reperes.occurrence)
        assert occurrence.statut is StatutOccurrence.ANNULEE
        assert occurrence.commentaire.endswith("x" * limite)


# --------------------------------------------------------------------------- #
# HEAD : supervision et vérificateurs de liens
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_head_sur_les_ecrans_de_l_emploi_du_temps(version_generee, client):
    """Cas : HEAD (sonde de supervision, `curl -I`, vérificateur de liens) sur les pages
    Générer, versions, grille, suivi d'un calcul et « À traiter », et sur une action POST.
    Risque : 405 « Action non permise » sur toute page (FastAPI ne déclare que GET) ; ou une
    réponse HEAD qui porte la page entière.
    """
    for adresse in (
        f"/calculs?semestre_id={version_generee.semestre_id}",
        f"/versions?semestre_id={version_generee.semestre_id}",
        f"/emploi-du-temps?version_id={version_generee.version_id}",
        f"/calculs/{version_generee.calcul_id}",
        "/a-traiter",
    ):
        reponse = client.head(adresse)
        assert reponse.status_code == 200 and reponse.content == b"", adresse
        assert int(reponse.headers["content-length"]) > 0, adresse  # celle de la page en GET
    action = client.head(f"/calculs/{version_generee.calcul_id}/arreter")
    assert action.status_code == 405 and action.content == b""


# --------------------------------------------------------------------------- #
# Verrou d'écriture tenu par un autre écrivain (import, chargement de jeu)
# --------------------------------------------------------------------------- #

ECRITURE_D_UN_IMPORT = (
    "UPDATE occurrence SET commentaire = 'Import en cours' "
    "WHERE id = (SELECT max(id) FROM occurrence)"
)
"""Ce que l'autre écrivain change et valide : la note de la dernière séance datée."""


@pytest.mark.cas_limite
def test_une_ecriture_attend_son_tour_pendant_un_autre_ecrivain(reperes, client, faux_verificateur):
    """Cas : une séance datée est annulée pendant qu'un import, dans un autre onglet, tient le
    verrou d'écriture puis valide ses changements environ 0,3 s plus tard.
    Risque : la requête lit d'abord puis écrit ; SQLite la refuse aussitôt (« database is
    locked », page « base occupée ») au lieu de la faire attendre la fin de l'import.
    """
    adresse = f"/emploi-du-temps/occurrences/{reperes.occurrence}/annuler"
    with autre_ecrivain(ecriture=ECRITURE_D_UN_IMPORT, rendre_apres_s=0.3) as rendu:
        reponse = client.post(adresse, data={"motif": "Grève"})
        assert rendu.is_set()  # la requête n'a abouti qu'après la fin de l'autre écrivain
    assert reponse.status_code == 200 and "occupée" not in reponse.text
    with session_base() as session:
        assert session.get(m.Occurrence, reperes.occurrence).statut is StatutOccurrence.ANNULEE
        derniere = session.get(m.Occurrence, session.scalar(select(func.max(m.Occurrence.id))))
        assert derniere.commentaire == "Import en cours"  # les deux écritures, l'une après l'autre


@pytest.mark.cas_limite
def test_une_lecture_est_servie_pendant_un_autre_ecrivain(version_generee, client):
    """Cas : la grille, la page Générer et « À traiter » sont ouvertes (GET, HEAD) pendant
    qu'un import tient le verrou d'écriture.
    Risque : une lecture qui prendrait le verrou d'emblée attendrait la fin de l'import (dix
    secondes au plus) puis répondrait « base occupée » : chaque onglet gelé pendant l'import.
    """
    with autre_ecrivain() as rendu:
        for adresse in (
            f"/emploi-du-temps?version_id={version_generee.version_id}",
            f"/calculs?semestre_id={version_generee.semestre_id}",
            "/a-traiter",
        ):
            assert client.get(adresse).status_code == 200, adresse
        assert client.head("/a-traiter").status_code == 200
        assert not rendu.is_set()  # servies sous le verrou, sans l'attendre


@pytest.mark.cas_limite
def test_verrou_tenu_au_dela_du_delai_donne_la_page_base_occupee(
    reperes, client, faux_verificateur, delai_verrou_court
):
    """Cas : l'autre écrivain garde le verrou au-delà du délai d'attente de la base (import
    très long, commande bloquée sur le serveur).
    Risque : trace ou erreur 500 « inattendue » au lieu de la page « base occupée » (503) qui
    dit de réessayer ; ou une séance annulée à moitié.
    """
    adresse = f"/emploi-du-temps/occurrences/{reperes.occurrence}/annuler"
    with autre_ecrivain() as rendu:
        reponse = client.post(adresse, data={"motif": "Grève"})
        assert not rendu.is_set()  # le délai (réduit) est écoulé, l'autre écrivain tient toujours
    assert reponse.status_code == 503
    assert "Base de données occupée" in reponse.text and "réessayer" in reponse.text
    assert "inattendue" not in reponse.text and "Traceback" not in reponse.text
    with session_base() as session:
        assert session.get(m.Occurrence, reperes.occurrence).statut is not StatutOccurrence.ANNULEE


# --------------------------------------------------------------------------- #
# Données vides : version sans séance, semestre sans grille
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_version_sans_seance_s_affiche_et_s_exporte(version_generee, client):
    """Cas : version sans aucune séance (import resté vide, référence non remplie) affichée en
    semaine type, datée et par jour, listée dans l'historique, exportée en CSV et en iCal.
    Risque : erreur 500 (minimum ou maximum d'une liste vide), blocs inventés, ou fichier
    d'export illisible par le tableur ou l'agenda.
    """
    vide = creer_version_vide(version_generee.semestre_id)
    for adresse in (
        f"/emploi-du-temps?version_id={vide}",
        f"/emploi-du-temps?version_id={vide}&couche=date",
        f"/emploi-du-temps?version_id={vide}&affichage=jour&jour=0",
        f"/versions?semestre_id={version_generee.semestre_id}",
    ):
        page = client.get(adresse)
        assert page.status_code == 200, adresse
        assert "Traceback" not in page.text, adresse
    grille = client.get(f"/emploi-du-temps?version_id={vide}").text
    assert 'class="edt-bloc ' not in grille  # aucun bloc inventé

    csv = client.get(f"/export/emploi-du-temps.csv?version_id={vide}")
    assert csv.status_code == 200 and csv.text.lstrip("﻿").startswith("jour;debut;fin")
    assert len(csv.text.strip().splitlines()) == 1  # l'en-tête seul
    ics = client.get(f"/export/emploi-du-temps.ics?version_id={vide}")
    assert ics.status_code == 200
    assert "BEGIN:VCALENDAR" in ics.text and "BEGIN:VEVENT" not in ics.text


@pytest.mark.cas_limite
def test_version_active_vide_sur_a_traiter_et_l_accueil(version_generee, client, aujourdhui_fige):
    """Cas : la version active du semestre n'a aucune séance (import resté vide).
    Risque : erreur 500 sur « À traiter » ou l'accueil, ou version vide présentée comme un
    emploi du temps sans défaut.
    """
    creer_version_vide(version_generee.semestre_id, active=True)
    for adresse in ("/a-traiter", f"/a-traiter?semestre_id={version_generee.semestre_id}"):
        page = client.get(adresse)
        assert page.status_code == 200, adresse
        assert "n&#39;est pas placée" in page.text, adresse  # chaque séance demandée manque
        assert "Aucune anomalie" not in page.text, adresse
    accueil = client.get("/")
    assert accueil.status_code == 200
    assert "Version vide" in accueil.text and "Aucun point à traiter" not in accueil.text


@pytest.mark.cas_limite
def test_semestre_sans_grille_reste_utilisable(reperes, client, faux_verificateur):
    """Cas : la grille de créneaux est vidée après la génération (grille en cours de refonte).
    Risque : erreur 500 sur Générer ou sur la grille, ou séance proposée à une place qui
    n'existe plus.
    """
    with session_base() as session:
        seance = session.get(m.SeanceHebdo, reperes.seance)
        jour, debut = seance.jour, seance.debut_min
        session.execute(delete(m.Creneau))
    for adresse in (
        f"/calculs?semestre_id={reperes.semestre}",
        f"/emploi-du-temps?version_id={reperes.version}",
    ):
        assert client.get(adresse).status_code == 200, adresse
    # Sans grille, la séance ne peut que rester où elle est : rien d'autre n'est proposé.
    with session_base() as session:
        formulaire = modifications.formulaire_seance(session, reperes.seance)
    assert [j for j, _ in formulaire.jours] == [jour]
    assert [d for d, _ in formulaire.debuts] == [debut]
    reponse = client.post(
        f"/emploi-du-temps/seances/{reperes.seance}/modifier",
        data={"jour": (jour + 1) % 5, "debut": debut},
        headers=HX,
    )
    assert reponse.status_code == 422 and "ne peut pas être proposée" in reponse.text


# --------------------------------------------------------------------------- #
# Exports (lot échange) : identifiant énorme
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("extension", ["csv", "ics"])
def test_export_d_une_version_enorme_dit_introuvable(jeu_minimal, client_sans_trace, extension):
    """Cas : export CSV ou iCal d'une version de vingt chiffres (lien mal copié).
    Risque : OverflowError de SQLite non rattrapée : erreur 500 au lieu d'un « introuvable » (404).
    Corrigé par le lot échange ; `client_sans_trace` montre un retour du 500 comme un statut.
    """
    reponse = client_sans_trace.get(f"/export/emploi-du-temps.{extension}?version_id={ENORME}")
    assert reponse.status_code == 404
