"""Règles personnalisées sur les écrans de l'emploi du temps : Générer, suivi, grille, anomalies.

Le jeu minimal a deux règles actives : R001 (la promotion A1 ne travaille pas
le samedi, nécessaire) et R002 (la classe A1-G1 a une pause déjeuner libre
d'au moins 1 h entre 12 h et 14 h au S1, importante), plus deux règles de base
proposées (R003, R004). Les calculs passent par le vrai moteur ; seuls les
décomptes d'entorses sont injectés quand le test a besoin d'un nombre précis.
"""

from __future__ import annotations

from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import SAMEDI, Gravite
from edt.domaine.instantane import id_seance
from edt.services import generation
from edt.services import regles as service_regles
from edt.services.emploi_du_temps import modifications, presentation, vues
from edt.services.parametres import ecrire_parametres
from edt.verification.contrat import Anomalie

from .conftest import DELAI, identifiant_groupe, seance_du_besoin

HX = {"HX-Request": "true"}


def _regles(semestre_id: int) -> dict[str, service_regles.RegleDetail]:
    """Les règles de l'année du semestre, par code."""
    with session_base() as session:
        annee = session.get(m.Semestre, semestre_id).annee_scolaire_id
        return {detail.code: detail for detail in service_regles.lister(session, annee)}


def _annee(semestre_id: int) -> int:
    with session_base() as session:
        return session.get(m.Semestre, semestre_id).annee_scolaire_id


def _lien(code: str, annee_id: int) -> str:
    """Le lien d'une règle tel que la page l'écrit (année du semestre, `&` échappé)."""
    return f'<a href="/regles?annee={annee_id}&amp;q={code}">{code}</a>'


def _formulaire(semestre_id: int, **champs: str) -> dict:
    return {"semestre_id": semestre_id, "duree": "court", **champs}


def _lancer(client, semestre_id: int, **champs: str) -> generation.SuiviCalcul:
    """Lance un calcul par la page Générer (vrai moteur) et attend sa fin."""
    reponse = client.post(
        "/calculs", data=_formulaire(semestre_id, **champs), follow_redirects=False
    )
    assert reponse.status_code == 303, reponse.text
    calcul_id = int(reponse.headers["location"].rsplit("/", 1)[1])
    return generation.attendre_calcul(calcul_id, DELAI)


def _r001_facultative_r002_ignoree(regles) -> dict[str, str]:
    r001, r002 = regles["R001"], regles["R002"]
    return {
        f"niveau_regle_{r001.id}": "facultatif",
        f"niveau_regle_{r002.id}": "important",
        f"ignorer_regle_{r002.id}": "1",
    }


# --------------------------------------------------------------------------- #
# Page Générer
# --------------------------------------------------------------------------- #


def test_tableau_des_regles_personnalisees_sur_generer(jeu_minimal, client):
    regles = _regles(jeu_minimal.semestre_id)
    r001, r002 = regles["R001"], regles["R002"]

    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text

    assert "<legend>Règles personnalisées</legend>" in page
    bloc = page.split("<legend>Règles personnalisées</legend>", 1)[1].split("</fieldset>", 1)[0]
    for detail in (r001, r002):
        assert _lien(detail.code, _annee(jeu_minimal.semestre_id)) in bloc
        assert detail.phrase.replace("'", "&#39;") in bloc
        for niveau in Niveau:
            assert f'name="niveau_regle_{detail.id}" value="{niveau.value}"' in bloc
        assert f'name="ignorer_regle_{detail.id}" value="1">' in bloc  # non cochée
    assert f'name="niveau_regle_{r001.id}" value="necessaire" checked' in bloc
    assert f'name="niveau_regle_{r002.id}" value="important" checked' in bloc
    assert "Jours travaillés" in bloc and "Pause déjeuner" in bloc  # types
    assert "S1" in bloc and "toute l&#39;année" in bloc  # périodes
    # Les règles de base proposées sont rappelées, sans effet ; liens vers l'écran des règles.
    assert (
        "2 règles proposées ne sont pas encore activées : elles n&#39;ont aucun effet sur le "
        "calcul tant qu&#39;elles ne sont pas confirmées." in bloc
    )
    assert 'href="/regles"' in bloc and 'href="/regles/nouveau"' in bloc
    assert "pas encore transmis" not in page and "ne lit pas encore" not in page
    # REGLE n'est pas dans le tableau des niveaux du catalogue.
    assert 'name="niveau_REGLE"' not in page


def test_niveau_modifie_et_regle_ignoree_transmis_au_moteur(jeu_minimal, client):
    regles = _regles(jeu_minimal.semestre_id)
    r001 = regles["R001"]

    suivi = _lancer(client, jeu_minimal.semestre_id, **_r001_facultative_r002_ignoree(regles))

    assert suivi.etat.est_termine, suivi.message
    # Transmis au moteur : R001 facultative, R002 absente (ignorée).
    assert suivi.niveaux_regles == {r001.id: Niveau.FACULTATIF}
    copie = {entree["code"]: entree for entree in suivi.regles}
    assert copie["R001"]["niveau"] == "facultatif" and copie["R002"]["niveau"] is None
    assert copie["R001"]["phrase"] == r001.phrase
    with session_base() as session:
        parametres = session.get(m.Calcul, suivi.id).parametres
    assert parametres["niveaux_regles"] == {str(r001.id): "facultatif"}
    assert {entree["code"] for entree in parametres["regles"]} == {"R001", "R002"}


def test_sans_choix_chaque_regle_garde_son_niveau(jeu_minimal, client):
    suivi = _lancer(client, jeu_minimal.semestre_id)
    assert suivi.niveaux_regles is None  # rien à transmettre
    assert {e["code"]: e["niveau"] for e in suivi.regles} == {
        "R001": "necessaire",
        "R002": "important",
    }


def test_pre_verifications_recoivent_les_niveaux_des_regles(jeu_minimal, client, faux_verificateur):
    regles = _regles(jeu_minimal.semestre_id)
    fragment = client.get(
        "/calculs/verifications",
        params={"semestre_id": jeu_minimal.semestre_id, **_r001_facultative_r002_ignoree(regles)},
        headers=HX,
    )
    assert fragment.status_code == 200 and "<html" not in fragment.text
    assert faux_verificateur.niveaux_regles_pre_verifies == {regles["R001"].id: Niveau.FACULTATIF}


def test_choix_illisible_sur_une_regle_reaffiche_la_page(jeu_minimal, client):
    r002 = _regles(jeu_minimal.semestre_id)["R002"]
    for champs, attendu in (
        ({f"niveau_regle_{r002.id}": "tres_important"}, "niveau inconnu pour la règle R002"),
        ({"niveau_regle_99999": "important"}, "n&#39;est pas (ou plus) active"),
    ):
        reponse = client.post(
            "/calculs",
            data=_formulaire(jeu_minimal.semestre_id, niveau_PAUSE="facultatif", **champs),
            follow_redirects=False,
        )
        assert reponse.status_code == 422, champs
        assert attendu in reponse.text and "Traceback" not in reponse.text
        # Les autres choix reviennent tels quels.
        assert 'name="niveau_PAUSE" value="facultatif" checked' in reponse.text
    with session_base() as session:
        assert not session.scalars(select(m.Calcul)).all()


def test_relance_et_preremplissage_reprennent_les_niveaux_du_calcul(jeu_minimal, client):
    regles = _regles(jeu_minimal.semestre_id)
    r001, r002 = regles["R001"], regles["R002"]
    origine = _lancer(client, jeu_minimal.semestre_id, **_r001_facultative_r002_ignoree(regles))

    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert f'name="niveau_regle_{r001.id}" value="facultatif" checked' in page
    # « Ignorer » ne vaut que pour un calcul : R002 revient à son niveau, non ignorée.
    assert f'name="niveau_regle_{r002.id}" value="important" checked' in page
    assert f'name="ignorer_regle_{r002.id}" value="1">' in page

    for donnees in ({"longue": "1"}, {"autre_solution": "1"}):
        relance = client.post(
            f"/calculs/{origine.id}/relancer", data=donnees, follow_redirects=False
        )
        assert relance.status_code == 303, relance.text
        suivi = generation.attendre_calcul(
            int(relance.headers["location"].rsplit("/", 1)[1]), DELAI
        )
        assert suivi.niveaux_regles == origine.niveaux_regles == {r001.id: Niveau.FACULTATIF}
        assert {e["code"]: e["niveau"] for e in suivi.regles} == {
            "R001": "facultatif",
            "R002": None,
        }


# --------------------------------------------------------------------------- #
# Suivi et historique
# --------------------------------------------------------------------------- #


def test_suivi_et_historique_disent_les_regles_appliquees_et_leurs_entorses(jeu_minimal, client):
    regles = _regles(jeu_minimal.semestre_id)
    r001, r002 = regles["R001"], regles["R002"]
    suivi = _lancer(client, jeu_minimal.semestre_id, **_r001_facultative_r002_ignoree(regles))
    # Décompte du moteur remplacé par un nombre connu (le vrai dépend de la solution).
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        calcul.statistiques = {
            **(calcul.statistiques or {}),
            "entorses_regles": {"R001": 2, "R002": 1, "R009": 0},
        }

    page = client.get(f"/calculs/{suivi.id}").text.replace("&#39;", "'")

    phrase1, phrase2 = r001.phrase.rstrip("."), r002.phrase.rstrip(".")
    assert (
        f"<strong>Règles personnalisées :</strong> R001 ({phrase1}) : facultatif ; "
        f"R002 ({phrase2}) : ignorée pour ce calcul</p>"
    ) in page
    assert (
        "<strong>Entorses aux règles personnalisées :</strong> "
        f"2 entorses à R001 ({phrase1}) ; 1 entorse à R002 ({phrase2})</p>"
    ) in page
    assert "R009" not in page  # aucune entorse : non citée
    assert "Comptées par le calcul" in page

    historique = client.get(f"/versions?semestre_id={jeu_minimal.semestre_id}").text
    assert "R001 : facultatif ; R002 : ignorée" in historique

    # Un calcul où chaque règle garde son niveau n'en dit rien.
    defaut = _lancer(client, jeu_minimal.semestre_id)
    assert "Règles personnalisées :" not in client.get(f"/calculs/{defaut.id}").text


def test_les_ecarts_se_mesurent_au_niveau_que_la_regle_avait_au_calcul(jeu_minimal, client):
    regles = _regles(jeu_minimal.semestre_id)
    r001 = regles["R001"]  # nécessaire
    suivi = _lancer(client, jeu_minimal.semestre_id, **_r001_facultative_r002_ignoree(regles))

    # La copie ne porte pas le niveau propre de la règle au calcul : comparé au niveau
    # actuel, et la page le dit.
    page = client.get(f"/calculs/{suivi.id}").text
    assert "R001 (" in page and ") : facultatif" in page
    assert "Comparés au niveau actuel de chaque règle" in page

    # Avec le niveau d'alors dans la copie (`niveau_regle`), la comparaison ne bouge plus
    # quand la règle change ensuite.
    with session_base() as session:
        calcul = session.get(m.Calcul, suivi.id)
        calcul.parametres = {
            **calcul.parametres,
            "regles": [
                {**entree, "niveau_regle": regles[entree["code"]].niveau.value}
                for entree in calcul.parametres["regles"]
            ],
        }
        session.get(m.Regle, r001.id).niveau = Niveau.FACULTATIF  # modifiée après le calcul
    page = client.get(f"/calculs/{suivi.id}").text.replace("&#39;", "'")
    assert f"R001 ({r001.phrase.rstrip('.')}) : facultatif" in page
    assert "Comparés au niveau actuel" not in page
    historique = client.get(f"/versions?semestre_id={jeu_minimal.semestre_id}").text
    assert "R001 : facultatif ; R002 : ignorée" in historique


def test_une_relance_applique_les_regles_creees_depuis_et_le_dit(jeu_minimal, client):
    regles = _regles(jeu_minimal.semestre_id)
    origine = _lancer(client, jeu_minimal.semestre_id, **_r001_facultative_r002_ignoree(regles))
    with session_base() as session:
        annee = session.get(m.AnneeScolaire, _annee(jeu_minimal.semestre_id)).libelle
        nouvelle = service_regles.creer(
            session,
            {
                "type": "CRENEAUX_INTERDITS",
                "portee": {"nature": "etablissement"},
                "parametres": {"jours": ["vendredi"], "debut": "16:00", "fin": "18:00"},
                "niveau": "facultatif",
                "annee": annee,
            },
        )

    relance = client.post(f"/calculs/{origine.id}/relancer", data={"longue": "1"})
    assert relance.status_code == 200
    assert f"appliquées à leur propre niveau : {nouvelle.code}" in relance.text
    with session_base() as session:
        dernier = session.scalar(select(m.Calcul).order_by(m.Calcul.id.desc()))
        calcul_id = dernier.id
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    # Jamais ignorée en silence : la règle nouvelle part à son niveau ; R002 reste ignorée.
    assert suivi.niveaux_regles[nouvelle.id] is Niveau.FACULTATIF
    assert suivi.niveaux_regles[regles["R001"].id] is Niveau.FACULTATIF
    assert regles["R002"].id not in suivi.niveaux_regles


# --------------------------------------------------------------------------- #
# Anomalies d'une règle : À traiter et panneau d'une séance
# --------------------------------------------------------------------------- #


def test_anomalie_regle_affiche_la_phrase_et_le_lien(version_generee, client, faux_verificateur):
    r002 = _regles(version_generee.semestre_id)["R002"]
    with session_base() as session:
        seance = seance_du_besoin(
            session, version_generee.version_id, "Mathématiques", "TD", "A1-G1"
        )
        identifiant = seance.id
        cle = id_seance(seance.besoin_id, seance.rang)
    faux_verificateur.hebdo = [
        Anomalie(
            CodeContrainte.REGLE,
            Gravite.AVERTISSEMENT,
            "Le mardi, A1-G1a n'a que 30 min de libre dans la fenêtre du déjeuner.",
            seances=(cle,),
            regle_id=r002.id,
            regle_code="R002",
            regle_phrase=r002.phrase,
        )
    ]

    page = client.get(f"/a-traiter?semestre_id={version_generee.semestre_id}").text
    assert "Règle personnalisée (1)" in page
    assert _lien("R002", _annee(version_generee.semestre_id)) in page
    assert r002.phrase.replace("'", "&#39;") in page

    panneau = client.get(f"/emploi-du-temps/seances/{identifiant}/panneau", headers=HX).text
    assert _lien("R002", _annee(version_generee.semestre_id)) in panneau
    assert "n&#39;a que 30 min de libre" in panneau


def test_anomalie_sans_regle_citee_reste_telle_quelle():
    simple = Anomalie(CodeContrainte.HORAIRES, Gravite.ERREUR, "Hors grille.")
    assert presentation.regle_citee(simple) is None
    sans_code = Anomalie(CodeContrainte.REGLE, Gravite.ERREUR, "Écart.", regle_id=4)
    citee = presentation.regle_citee(sans_code)
    assert citee.code == "règle n° 4" and citee.lien is None


# --------------------------------------------------------------------------- #
# Grille : jours travaillés, déjeuner de la personne affichée ; retouche : jours permis
# --------------------------------------------------------------------------- #


def test_la_grille_montre_les_jours_travailles_samedi_compris(version_generee):
    version = version_generee.version_id
    with session_base() as session:
        occupees_le_samedi = set(
            session.scalars(
                select(m.SeanceHebdo.salle_id).where(
                    m.SeanceHebdo.version_id == version, m.SeanceHebdo.jour == SAMEDI
                )
            )
        )
        salle = session.scalar(
            select(m.SeanceHebdo.salle_id).where(
                m.SeanceHebdo.version_id == version,
                m.SeanceHebdo.salle_id.is_not(None),
                m.SeanceHebdo.salle_id.not_in(occupees_le_samedi or {0}),
            )
        )
    assert salle is not None, "aucune salle sans séance le samedi dans la version générée"
    cible = vues.Cible(vues.NatureCible.SALLE, salle)

    def jours() -> list[int]:
        with session_base() as session:
            vue = vues.construire_vue(session, version_id=version, cible=cible, verifier=False)
        return [j.jour for j in vue.jours]

    assert jours() == [0, 1, 2, 3, 4]  # standard : du lundi au vendredi
    with session_base() as session:
        ecrire_parametres(session, {"jours_travailles": [0, 1, 2, 3, 4, 5]})
    assert jours() == [0, 1, 2, 3, 4, 5]


def test_la_bande_dejeuner_suit_le_reglage_du_groupe_affiche(version_generee, client):
    version = version_generee.version_id
    with session_base() as session:
        classe = identifiant_groupe(session, "A1-G1")  # R002 : pause libre de 12 h à 14 h
        promotion = identifiant_groupe(session, "A1")  # établissement : 12 h 45 - 13 h 45

    page = client.get(f"/emploi-du-temps?version_id={version}&groupe_id={classe}").text
    assert '<div class="edt-dejeuner edt-dejeuner-libre" style=' in page
    assert 'title="Pause libre : au moins 60 min dans cette fenêtre (règle R002)"' in page

    page = client.get(f"/emploi-du-temps?version_id={version}&groupe_id={promotion}").text
    assert 'title="Déjeuner : 12 h 45 - 13 h 45, libre pour tous"><span>Déjeuner</span>' in page
    assert "edt-dejeuner-libre" not in page


def test_les_jours_proposes_pour_une_retouche_suivent_les_regles(version_generee):
    version = version_generee.version_id
    semestre = version_generee.semestre_id
    with session_base() as session:
        tp = seance_du_besoin(session, version, "Algorithmique", "TP", "A1-G1a")
        assert tp.besoin.type_seance.samedi_autorise  # le type ouvre le samedi
        tp.jour, tp.debut_min, tp.fin_min = 0, 510, 510 + (tp.fin_min - tp.debut_min)
        identifiant = tp.id
    with session_base() as session:
        jours = dict(modifications.formulaire_seance(session, identifiant).jours)
    assert SAMEDI not in jours  # R001 (nécessaire) : la promotion A1 ne travaille pas le samedi

    with session_base() as session:
        annee = session.get(m.Semestre, semestre).annee_scolaire_id
        service_regles.desactiver(session, annee, "R001")
    with session_base() as session:
        jours = dict(modifications.formulaire_seance(session, identifiant).jours)
    assert SAMEDI in jours  # sans la règle, le type de séance l'ouvre
