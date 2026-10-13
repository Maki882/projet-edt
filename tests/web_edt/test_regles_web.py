"""Règles personnalisées sur les écrans de l'emploi du temps : Générer, suivi, grille, anomalies.

Le jeu minimal a deux règles actives : R001 (la promotion A1 ne travaille pas
le samedi, nécessaire) et R002 (la classe A1-G1 a une pause déjeuner libre
d'au moins 1 h entre 12 h et 14 h au S1, importante), plus deux règles de base
proposées. Les raccords des lots solveur et vérificateur
(`OptionsSolveur.niveaux_regles`, `calcul.parametres["regles"]`,
`statistiques["entorses_regles"]`, `Anomalie.regle_id`) sont remplacés par des
doubles : ces tests ne dépendent pas de leur livraison.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from edt import solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import SAMEDI, Gravite
from edt.domaine.instantane import id_seance
from edt.services import generation
from edt.services import regles as service_regles
from edt.services.emploi_du_temps import modifications, presentation, regles_personnalisees, vues
from edt.services.parametres import ecrire_parametres
from edt.solveur.contrat import OptionsSolveur, ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie

from .conftest import DELAI, identifiant_groupe, seance_du_besoin

HX = {"HX-Request": "true"}


def _regles(semestre_id: int) -> dict[str, service_regles.RegleDetail]:
    """Les règles de l'année du semestre, par code."""
    with session_base() as session:
        annee = session.get(m.Semestre, semestre_id).annee_scolaire_id
        return {detail.code: detail for detail in service_regles.lister(session, annee)}


class _Capture:
    """Double de `avec_niveaux_regles` : retient ce que l'écran transmet au moteur."""

    def __init__(self) -> None:
        self.niveaux_regles: list[dict | None] = []

    def __call__(self, options, niveaux_regles):
        self.niveaux_regles.append(None if niveaux_regles is None else dict(niveaux_regles))
        return options


def _formulaire(semestre_id: int, **champs: str) -> dict:
    return {"semestre_id": semestre_id, "duree": "court", **champs}


def _calcul_temps_depasse(client, semestre_id: int, monkeypatch) -> int:
    monkeypatch.setattr(
        solveur,
        "resoudre",
        lambda instantane, options, arret=None: ResultatSolveur(StatutSolveur.TEMPS_DEPASSE),
    )
    reponse = client.post("/calculs", data=_formulaire(semestre_id), follow_redirects=False)
    assert reponse.status_code == 303, reponse.text
    calcul_id = int(reponse.headers["location"].rsplit("/", 1)[1])
    generation.attendre_calcul(calcul_id, DELAI)
    return calcul_id


def _enregistrer_regles(calcul_id: int, regles: list[dict], **statistiques) -> None:
    """Ce que le lot solveur enregistrera avec le calcul (double)."""
    with session_base() as session:
        calcul = session.get(m.Calcul, calcul_id)
        calcul.parametres = {**(calcul.parametres or {}), "regles": regles}
        calcul.statistiques = {**(calcul.statistiques or {}), **statistiques}


def _copie(detail: service_regles.RegleDetail, niveau: Niveau) -> dict:
    return {
        "id": detail.id,
        "code": detail.code,
        "type": detail.type.value,
        "portee": detail.portee.nature.value,
        "parametres": dict(detail.regle.parametres),
        "niveau": niveau.value,
        "phrase": detail.phrase,
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
        assert f'<a href="/regles?code={detail.code}">{detail.code}</a>' in bloc
        assert detail.phrase.replace("'", "&#39;") in bloc
        for niveau in Niveau:
            assert f'name="niveau_regle_{detail.id}" value="{niveau.value}"' in bloc
        assert f'name="ignorer_regle_{detail.id}" value="1">' in bloc  # non cochée
    assert f'name="niveau_regle_{r001.id}" value="necessaire" checked' in bloc
    assert f'name="niveau_regle_{r002.id}" value="important" checked' in bloc
    assert "Jours travaillés" in bloc and "Pause déjeuner" in bloc  # types
    assert "S1" in bloc and "toute l&#39;année" in bloc  # périodes
    # Les règles de base proposées sont rappelées, sans effet ; liens vers l'écran des règles.
    assert "2 règles proposées ne sont pas encore activées" in bloc
    assert 'href="/regles"' in bloc and 'href="/regles/ajouter"' in bloc
    # REGLE n'est pas dans le tableau des niveaux du catalogue.
    assert 'name="niveau_REGLE"' not in page


def test_niveau_modifie_et_regle_ignoree_transmis_au_moteur(jeu_minimal, client, monkeypatch):
    regles = _regles(jeu_minimal.semestre_id)
    r001, r002 = regles["R001"], regles["R002"]
    capture = _Capture()
    monkeypatch.setattr(regles_personnalisees, "avec_niveaux_regles", capture)
    lancees = []
    monkeypatch.setattr(
        generation, "lancer_calcul", lambda semestre_id, options: lancees.append(options) or 999
    )

    reponse = client.post(
        "/calculs",
        data=_formulaire(
            jeu_minimal.semestre_id,
            **{
                f"niveau_regle_{r001.id}": "necessaire",
                f"ignorer_regle_{r001.id}": "1",
                f"niveau_regle_{r002.id}": "facultatif",
            },
        ),
        follow_redirects=False,
    )

    assert reponse.status_code == 303 and reponse.headers["location"] == "/calculs/999"
    assert capture.niveaux_regles == [{r002.id: Niveau.FACULTATIF}]  # R001 ignorée : absente
    assert len(lancees) == 1
    # Tant que le moteur ne lit pas les niveaux des règles, l'écran le dit.
    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert "ne sont pas encore transmis" in page


def test_options_portent_les_niveaux_quand_le_moteur_les_lit(monkeypatch):
    @dataclass(frozen=True)
    class OptionsAvecRegles(OptionsSolveur):
        niveaux_regles: dict = field(default_factory=dict)

    monkeypatch.setattr(regles_personnalisees, "OptionsSolveur", OptionsAvecRegles)
    assert regles_personnalisees.moteur_lit_les_niveaux_regles()
    options = regles_personnalisees.avec_niveaux_regles(OptionsAvecRegles(), {7: Niveau.FACULTATIF})
    assert options.niveaux_regles == {7: Niveau.FACULTATIF}
    assert regles_personnalisees.avec_niveaux_regles(options, None) is options


def test_choix_illisible_sur_une_regle_reaffiche_la_page(jeu_minimal, client, monkeypatch):
    r002 = _regles(jeu_minimal.semestre_id)["R002"]
    monkeypatch.setattr(generation, "lancer_calcul", lambda *a, **k: 999)
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


def test_relance_et_preremplissage_reprennent_les_niveaux_du_calcul(
    jeu_minimal, client, monkeypatch
):
    regles = _regles(jeu_minimal.semestre_id)
    r002 = regles["R002"]
    calcul_id = _calcul_temps_depasse(client, jeu_minimal.semestre_id, monkeypatch)
    # Le calcul a appliqué R002 en facultatif et ignoré R001 (absente de la copie).
    _enregistrer_regles(calcul_id, [_copie(r002, Niveau.FACULTATIF)])

    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
    assert f'name="niveau_regle_{r002.id}" value="facultatif" checked' in page
    # « Ignorer » ne vaut que pour un calcul : R001 n'est pas préremplie ignorée.
    assert f'name="ignorer_regle_{regles["R001"].id}" value="1">' in page

    capture = _Capture()
    monkeypatch.setattr(regles_personnalisees, "avec_niveaux_regles", capture)
    for donnees in ({"longue": "1"}, {"autre_solution": "1"}):
        relance = client.post(
            f"/calculs/{calcul_id}/relancer", data=donnees, follow_redirects=False
        )
        assert relance.status_code == 303, relance.text
        generation.attendre_calcul(int(relance.headers["location"].rsplit("/", 1)[1]), DELAI)
    assert capture.niveaux_regles == [{r002.id: Niveau.FACULTATIF}] * 2


# --------------------------------------------------------------------------- #
# Suivi et historique
# --------------------------------------------------------------------------- #


def test_suivi_et_historique_disent_les_regles_appliquees_et_leurs_entorses(
    jeu_minimal, client, monkeypatch
):
    regles = _regles(jeu_minimal.semestre_id)
    r001, r002 = regles["R001"], regles["R002"]
    calcul_id = _calcul_temps_depasse(client, jeu_minimal.semestre_id, monkeypatch)
    page = client.get(f"/calculs/{calcul_id}").text
    assert "Règles personnalisées :" not in page  # rien d'enregistré : rien d'inventé

    _enregistrer_regles(
        calcul_id,
        [_copie(r002, Niveau.FACULTATIF)],
        entorses_regles={"R002": 2, "R001": 1, "R009": 0},
    )
    page = client.get(f"/calculs/{calcul_id}").text.replace("&#39;", "'")

    phrase1, phrase2 = r001.phrase.rstrip("."), r002.phrase.rstrip(".")
    assert (
        f"<strong>Règles personnalisées :</strong> R002 ({phrase2}) : facultatif ; "
        f"R001 ({phrase1}) : ignorée pour ce calcul</p>"
    ) in page
    assert (
        "<strong>Entorses aux règles personnalisées :</strong> "
        f"1 entorse à R001 ({phrase1}) ; 2 entorses à R002 ({phrase2})</p>"
    ) in page
    assert "R009" not in page  # aucune entorse : non citée
    assert "Comptées par le calcul" in page

    historique = client.get(f"/versions?semestre_id={jeu_minimal.semestre_id}").text
    assert "R002 : facultatif" in historique


# --------------------------------------------------------------------------- #
# Anomalies d'une règle : À traiter et panneau d'une séance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AnomalieDeRegle(Anomalie):
    """Anomalie telle que le lot vérificateur la livrera : avec la règle citée."""

    regle_id: int | None = None
    regle_code: str | None = None
    regle_phrase: str | None = None


def test_anomalie_regle_affiche_la_phrase_et_le_lien(version_generee, client, faux_verificateur):
    r002 = _regles(version_generee.semestre_id)["R002"]
    with session_base() as session:
        seance = seance_du_besoin(
            session, version_generee.version_id, "Mathématiques", "TD", "A1-G1"
        )
        identifiant = seance.id
        cle = id_seance(seance.besoin_id, seance.rang)
    faux_verificateur.hebdo = [
        AnomalieDeRegle(
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
    assert '<a href="/regles?code=R002">R002</a>' in page
    assert r002.phrase.replace("'", "&#39;") in page

    panneau = client.get(f"/emploi-du-temps/seances/{identifiant}/panneau", headers=HX).text
    assert '<a href="/regles?code=R002">R002</a>' in panneau
    assert "n&#39;a que 30 min de libre" in panneau


def test_anomalie_sans_regle_citee_reste_telle_quelle():
    simple = Anomalie(CodeContrainte.HORAIRES, Gravite.ERREUR, "Hors grille.")
    assert presentation.regle_citee(simple) is None
    sans_code = AnomalieDeRegle(CodeContrainte.REGLE, Gravite.ERREUR, "Écart.", regle_id=4)
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
