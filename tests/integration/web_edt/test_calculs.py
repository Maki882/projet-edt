"""Onglet « Générer » : lancement, suivi, messages de fin, vérifications préalables, versions."""

from __future__ import annotations

import html
import re
import threading

import pytest
from sqlalchemy import delete, func, select, update
from tests.outils.web_edt.aides import DELAI, HX

from edt import solveur
from edt.config import charger_configuration
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.contraintes import CATALOGUE, CodeContrainte
from edt.domaine.enumerations import EtatCalcul, Gravite
from edt.services import generation
from edt.services.emploi_du_temps import calculs, prechauffage, presentation
from edt.services.parametres import lire_parametres
from edt.solveur.contrat import ResultatSolveur, StatutSolveur
from edt.verification.contrat import Anomalie
from edt.web.routes import calculs as routes_calculs

MODIFIABLES = [code for code, entree in CATALOGUE.items() if entree.modifiable]


def _formulaire(semestre_id: int, niveaux: dict[str, str] | None = None, **options) -> dict:
    """Formulaire « Générer » : niveaux par défaut, sauf ceux de `niveaux` (`{code: niveau}`)."""
    choisis = {str(code): CATALOGUE[code].niveau_defaut.value for code in MODIFIABLES}
    choisis.update(niveaux or {})
    return {
        "semestre_id": semestre_id,
        **{f"niveau_{code}": valeur for code, valeur in choisis.items()},
        "duree": "court",
        **options,
    }


def _lancer(client, semestre_id: int, **kwargs) -> int:
    reponse = client.post(
        "/calculs", data=_formulaire(semestre_id, **kwargs), follow_redirects=False
    )
    assert reponse.status_code == 303, reponse.text
    return int(reponse.headers["location"].rsplit("/", 1)[1])


def _durees() -> tuple[int, int]:
    """Temps limites court et long réglés dans les paramètres (jamais en dur dans les tests)."""
    with session_base() as session:
        parametres = lire_parametres(session)
    return parametres.temps_limite_s, parametres.temps_limite_long_s


def _bouton_produire_au_bout_du_lien(client, page: str) -> bool:
    """Vrai si le lien « Voir l'emploi du temps » de `page` mène au bouton qui produit les
    séances datées (le message de la page renvoie à ce bouton)."""
    (adresse,) = re.findall(r'href="([^"]+)">Voir l.emploi du temps<', page)
    cible = client.get(html.unescape(adresse))
    assert cible.status_code == 200, adresse
    return ">Produire les séances datées</button>" in cible.text


def _nb_calculs() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(m.Calcul))


def test_page_generer_recapitule_et_propose_les_regles(jeu_minimal, client):
    page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text

    assert "16 besoins d&#39;enseignement, soit 17 séances par semaine type" in page
    # Quatre sections repliées derrière leur résumé, aucune ouverte par défaut.
    formulaire = page.split('id="formulaire-calcul"', 1)[1]
    assert formulaire.count('<details class="repliable"') == 4
    assert '<details class="repliable" open' not in formulaire
    assert "13 règles réglables, toutes à leur niveau par défaut ; 6 toujours nécessaires" in page
    catalogue = page.count('name="niveau_') - page.count('name="niveau_regle_')
    assert catalogue == 3 * len(MODIFIABLES)  # trois niveaux par règle du catalogue
    assert 'name="contraintes"' not in page  # pas de case pour ignorer une règle
    assert page.count(">Lancer le calcul</button>") == 2  # en tête de page et en bas du formulaire
    assert 'form="formulaire-calcul"' in page  # le bouton du haut soumet le même formulaire
    assert all(
        entree.libelle in page
        for code, entree in CATALOGUE.items()
        if code is not CodeContrainte.REGLE  # chaque règle personnalisée porte son niveau
    )
    court, long_ = _durees()
    assert f"Rechercher jusqu'à {presentation.duree_secondes(court)}" in page
    assert f"Rechercher jusqu'à {presentation.duree_secondes(long_)}" in page
    # Les vraies pré-vérifications ont tourné : pas d'encart « installation incomplète ».
    assert "installation incomplète" not in page
    assert "Vérifications avant le calcul" in page
    assert "Le calcul ne peut pas être lancé" not in page


def test_lancement_suivi_jusqu_a_la_fin_et_rappel_des_niveaux_choisis(jeu_minimal, client):
    calcul_id = _lancer(client, jeu_minimal.semestre_id, niveaux={"PAUSE": "facultatif"})
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi

    page = client.get(f"/calculs/{calcul_id}").text
    assert "Emploi du temps généré" in page
    assert f"/emploi-du-temps?version_id={suivi.version_id}" in page
    assert "<strong>Règles :</strong> Pause entre deux séances : facultatif</p>" in page
    assert "Traceback" not in page
    # Bilan du vrai moteur et de l'instanciation, lu dans les statistiques du calcul.
    bilan = suivi.statistiques["instanciation"]
    assert (
        f'<span class="compteur-nombre">{suivi.statistiques["nb_seances"]}</span>'
        '<span class="compteur-libelle">séances placées dans la semaine type' in page
    )
    assert (
        f'<span class="compteur-nombre">{bilan["nb_occurrences"]}</span>'
        '<span class="compteur-libelle">séances datées sur le semestre' in page
    )
    assert f'<span class="compteur-nombre">{bilan["nb_replacees"]}</span>' in page

    fragment = client.get(f"/calculs/{calcul_id}/etat", headers=HX).text
    assert "<html" not in fragment
    assert "hx-trigger" not in fragment  # calcul fini : l'interrogation s'arrête


@pytest.mark.cas_limite
def test_suivi_sans_rechargement_second_lancement_refuse_puis_arret(
    jeu_minimal, client, monkeypatch
):
    """Cas : double clic sur « Lancer le calcul » (second envoi pendant que le premier calcul
    tourne), suivi sans rechargement, puis arrêt depuis le suivi.
    Risque : deux calculs en parallèle sur le même semestre, ou un formulaire de lancement
    proposé pendant le calcul ; le second envoi doit mener au suivi du calcul en cours, et
    l'arrêt laisser « Calcul arrêté » sans version enregistrée.
    """
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(
            StatutSolveur.ARRETE if arret.is_set() else StatutSolveur.TEMPS_DEPASSE
        )

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    try:
        assert demarre.wait(DELAI)
        fragment = client.get(f"/calculs/{calcul_id}/etat", headers=HX).text
        assert 'hx-trigger="every 1s"' in fragment
        assert "Temps écoulé" in fragment and "Arrêter le calcul" in fragment

        page = client.get(f"/calculs?semestre_id={jeu_minimal.semestre_id}").text
        assert "Calcul en cours" in page
        assert 'id="formulaire-calcul"' not in page  # pas de second lancement proposé

        second = client.post(
            "/calculs", data=_formulaire(jeu_minimal.semestre_id), follow_redirects=False
        )
        assert second.status_code == 303
        assert second.headers["location"] == f"/calculs/{calcul_id}"
        assert _nb_calculs() == 1

        arret = client.post(f"/calculs/{calcul_id}/arreter", headers=HX)
        assert arret.status_code == 200
        assert "Arrêt demandé" in arret.text or "Calcul arrêté" in arret.text
    finally:
        generation.demander_arret(calcul_id)
    assert generation.attendre_calcul(calcul_id, DELAI).etat is EtatCalcul.ARRETE

    page = client.get(f"/calculs/{calcul_id}").text
    assert "Calcul arrêté" in page
    assert "Aucun emploi du temps n&#39;a été enregistré" in page


@pytest.mark.parametrize(
    ("statut", "attendus", "absents"),
    [
        (
            StatutSolveur.INFAISABLE,
            ["Impossible avec ces contraintes", "aucun emploi du temps ne respecte"],
            ["Relancer en cherchant", "Pas trouvé"],
        ),
        (
            StatutSolveur.TEMPS_DEPASSE,
            [
                "Pas trouvé dans le temps imparti",
                "pas une preuve",
                "Relancer en cherchant",
            ],
            ["Impossible avec ces contraintes"],
        ),
    ],
)
def test_deux_messages_d_echec_distincts(
    jeu_minimal, client, monkeypatch, statut, attendus, absents
):
    monkeypatch.setattr(
        solveur, "resoudre", lambda instantane, options, arret=None: ResultatSolveur(statut)
    )
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    generation.attendre_calcul(calcul_id, DELAI)

    page = client.get(f"/calculs/{calcul_id}").text

    assert all(texte in page for texte in attendus)
    assert not any(texte in page for texte in absents)
    if statut is StatutSolveur.TEMPS_DEPASSE:  # la relance propose la durée longue réglée
        assert f"jusqu'à {presentation.duree_secondes(_durees()[1])}" in page


def test_erreur_inattendue_donne_identifiant_et_journal_sans_detail_technique(
    jeu_minimal, client, monkeypatch
):
    def moteur_casse(instantane, options, arret=None):
        raise RuntimeError("panne interne du moteur")

    monkeypatch.setattr(solveur, "resoudre", moteur_casse)
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    suivi = generation.attendre_calcul(calcul_id, DELAI)

    page = client.get(f"/calculs/{calcul_id}").text

    assert "Le calcul n&#39;a pas pu aboutir" in page and "erreur inattendue" in page
    assert f"calcul n° {calcul_id}" in page
    assert f"<code>{suivi.fichier_journal.replace(chr(92), '/').rsplit('/', 1)[1]}</code>" in page
    for technique in ("panne interne", "RuntimeError", "Traceback"):
        assert technique not in page


def test_refus_par_les_verifications_prealables_distinct_d_une_recherche_vaine(
    jeu_minimal, client, monkeypatch
):
    # Le calcul est lancé directement (sans l'écran, qui refuserait avant) : c'est
    # l'orchestration qui refuse, avec la liste de ses raisons.
    raison = Anomalie(
        CodeContrainte.DISPO_GROUPE, Gravite.ERREUR, "La classe A1-G1 n'a aucun créneau libre."
    )
    with pytest.MonkeyPatch.context() as temporaire:
        temporaire.setattr(generation, "pre_verifier", lambda *a, **k: [raison])
        refuse = generation.attendre_calcul(
            generation.lancer_calcul(jeu_minimal.semestre_id), DELAI
        )
    assert refuse.etat is EtatCalcul.INFAISABLE and refuse.statistiques["refuse"]

    page = client.get(f"/calculs/{refuse.id}").text
    assert "Calcul non lancé : points bloquants relevés avant la recherche" in page
    assert "La classe A1-G1 n&#39;a aucun créneau libre." in page
    assert "Impossible avec ces contraintes" not in page

    monkeypatch.setattr(
        solveur,
        "resoudre",
        lambda instantane, options, arret=None: ResultatSolveur(
            StatutSolveur.INFAISABLE, message="Aucun emploi du temps ne peut tout respecter."
        ),
    )
    cherche = generation.attendre_calcul(generation.lancer_calcul(jeu_minimal.semestre_id), DELAI)
    page = client.get(f"/calculs/{cherche.id}").text
    assert "Impossible avec ces contraintes" in page
    assert "Aucun emploi du temps ne peut tout respecter." in page
    assert "Calcul non lancé" not in page


def test_autre_solution_donne_un_autre_emploi_du_temps(jeu_minimal, monkeypatch):
    """Nouvelle graine et pas d'indices : le moteur (2 fils par défaut) change de solution."""
    graines = iter([11, 12])
    monkeypatch.setattr(calculs, "_nouvelle_graine", lambda: next(graines))

    def lancer_autre() -> tuple[dict, list[tuple]]:
        with session_base() as session:
            calcul_id = calculs.demarrer_calcul(
                session, jeu_minimal.semestre_id, autre_solution=True
            )
        suivi = generation.attendre_calcul(calcul_id, DELAI)
        assert suivi.reussi, suivi.message
        with session_base() as session:
            parametres = session.get(m.Calcul, calcul_id).parametres
            placements = sorted(
                (s.besoin_id, s.rang, s.jour, s.debut_min, s.salle_id)
                for s in session.scalars(
                    select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == suivi.version_id)
                )
            )
        return parametres, placements

    premiers_parametres, premiers = lancer_autre()
    seconds_parametres, seconds = lancer_autre()

    assert (premiers_parametres["graine"], seconds_parametres["graine"]) == (11, 12)
    assert (
        not premiers_parametres["utiliser_indices"] and not seconds_parametres["utiliser_indices"]
    )
    assert premiers != seconds


def test_relance_longue_et_autre_solution(jeu_minimal, client, monkeypatch):
    monkeypatch.setattr(
        solveur,
        "resoudre",
        lambda instantane, options, arret=None: ResultatSolveur(StatutSolveur.TEMPS_DEPASSE),
    )
    premier = _lancer(client, jeu_minimal.semestre_id, niveaux={"PAUSE": "facultatif"})
    generation.attendre_calcul(premier, DELAI)

    longue = client.post(
        f"/calculs/{premier}/relancer", data={"longue": "1"}, follow_redirects=False
    )
    second = int(longue.headers["location"].rsplit("/", 1)[1])
    generation.attendre_calcul(second, DELAI)
    autre = client.post(
        f"/calculs/{second}/relancer", data={"autre_solution": "1"}, follow_redirects=False
    )
    troisieme = int(autre.headers["location"].rsplit("/", 1)[1])
    generation.attendre_calcul(troisieme, DELAI)

    with session_base() as session:
        par_id = {c.id: c for c in session.scalars(select(m.Calcul))}
    court, long_ = _durees()
    assert par_id[premier].parametres["temps_limite_s"] == court
    assert par_id[second].parametres["temps_limite_s"] == long_
    for relance in (second, troisieme):  # mêmes règles qu'à l'origine
        assert par_id[relance].parametres["niveaux"]["PAUSE"] == "facultatif"
        assert par_id[relance].contraintes_desactivees == []
    assert par_id[troisieme].parametres["temps_limite_s"] == long_  # garde la durée longue
    assert par_id[troisieme].parametres["graine"] != par_id[second].parametres["graine"]
    assert par_id[second].parametres["utiliser_indices"]  # relance longue : mêmes indices
    assert not par_id[troisieme].parametres["utiliser_indices"]  # autre solution : sans indices


def test_une_erreur_prealable_empeche_de_lancer_un_avertissement_non(
    jeu_minimal, client, faux_verificateur
):
    faux_verificateur.pre_verifications = [
        Anomalie(
            "DISPO_ENSEIGNANT", Gravite.ERREUR, "M. Leroy a 10 h de cours pour 8 h disponibles."
        ),
        Anomalie(
            "CAPACITE", Gravite.AVERTISSEMENT, "Une seule salle peut accueillir la promotion."
        ),
    ]
    semestre_id = jeu_minimal.semestre_id

    page = client.get(f"/calculs?semestre_id={semestre_id}").text
    assert "M. Leroy a 10 h de cours" in page and "Une seule salle" in page
    assert "Le calcul ne peut pas être lancé" in page

    refus = client.post("/calculs", data=_formulaire(semestre_id), follow_redirects=False)
    assert refus.status_code == 409
    assert "n&#39;a pas été lancé" in refus.text
    assert _nb_calculs() == 0

    # Changer un niveau : les vérifications sont refaites avec les niveaux choisis.
    fragment = client.get(
        "/calculs/verifications",
        params={"semestre_id": semestre_id, "niveau_DISPO_ENSEIGNANT": "important"},
        headers=HX,
    ).text
    assert "<html" not in fragment and "Une seule salle" in fragment
    assert faux_verificateur.niveaux_pre_verifies[CodeContrainte.DISPO_ENSEIGNANT] == "important"


def test_historique_activation_et_suppression_des_versions(version_generee, client):
    second = generation.attendre_calcul(
        generation.lancer_calcul(version_generee.semestre_id), DELAI
    )
    premiere, deuxieme = version_generee.version_id, second.version_id

    page = client.get(f"/versions?semestre_id={version_generee.semestre_id}").text
    assert "Version 1" in page and "Version 2" in page and "Active" in page

    client.post(f"/versions/{premiere}/activer")
    supprimee = client.post(f"/versions/{deuxieme}/supprimer")
    assert supprimee.status_code == 200 and "supprimée" in supprimee.text
    refus = client.post(f"/versions/{premiere}/supprimer")
    assert refus.status_code == 409
    assert "ne peut pas être supprimée" in refus.text

    with session_base() as session:
        restantes = session.execute(select(m.VersionEdt.id, m.VersionEdt.active)).all()
    assert restantes == [(premiere, True)]


@pytest.mark.cas_limite
def test_double_clic_sur_supprimer_une_version(version_generee, client):
    """Cas : double clic sur « Supprimer » d'une version : deux envois identiques, le navigateur
    affiche la réponse du second.
    Risque : page « Élément introuvable » (404) alors que la suppression demandée a bien eu lieu.
    """
    second = generation.attendre_calcul(
        generation.lancer_calcul(version_generee.semestre_id), DELAI
    )
    client.post(f"/versions/{version_generee.version_id}/activer")
    historique = f"/versions?semestre_id={version_generee.semestre_id}"
    # L'adresse du formulaire, telle que la page de l'historique la donne.
    (action,) = re.findall(
        rf'action="(/versions/{second.version_id}/supprimer[^"]*)"', client.get(historique).text
    )
    reponses = [client.post(html.unescape(action), follow_redirects=False) for _ in range(2)]
    assert [r.status_code for r in reponses] == [303, 303]
    # Le second envoi revient à l'historique du même semestre, pas au semestre par défaut.
    assert reponses[1].headers["location"] == historique
    page = client.get(historique)
    assert page.status_code == 200 and "déjà été supprimée" in page.text


@pytest.mark.cas_limite
def test_double_clic_sur_lancer_le_calcul(jeu_minimal, client, monkeypatch):
    """Cas : double clic sur « Lancer le calcul » : le second envoi arrive pendant que le calcul
    lancé par le premier démarre ; puis un autre onglet envoie d'autres réglages.
    Risque : bandeau « Un calcul est déjà en cours » qui fait croire à un problème alors que le
    calcul demandé tourne ; deux calculs lancés ; ou « Calcul lancé. » pour des réglages qui
    n'ont pas été appliqués.
    """
    # Délai du double envoi très large : le test ne dépend pas de la charge de la machine.
    monkeypatch.setattr(routes_calculs, "DELAI_DOUBLE_ENVOI_S", 3600.0)
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    premier = _lancer(client, jeu_minimal.semestre_id)
    try:
        second = client.post(
            "/calculs", data=_formulaire(jeu_minimal.semestre_id), follow_redirects=False
        )
        assert second.status_code == 303
        assert second.headers["location"] == f"/calculs/{premier}"
        page = client.get(second.headers["location"]).text
        assert "Calcul lancé." in page and "déjà en cours" not in page
        autres_reglages = _formulaire(jeu_minimal.semestre_id, duree="long", optimiser="1")
        autre = client.post("/calculs", data=autres_reglages, follow_redirects=False)
        assert autre.headers["location"] == f"/calculs/{premier}"
        page = client.get(autre.headers["location"]).text
        assert "Calcul lancé." not in page and "déjà en cours" in page
        assert _nb_calculs() == 1
    finally:
        generation.demander_arret(premier)
    assert demarre.wait(DELAI)
    assert generation.attendre_calcul(premier, DELAI).etat is EtatCalcul.ARRETE


def test_arret_avec_solution_conservee(jeu_minimal, client, monkeypatch):
    """Arrêt demandé alors qu'une solution existe : réussite, et l'écran dit que c'est un arrêt."""
    import dataclasses

    from edt.solveur.moteur import MESSAGE_SOLUTION_CONSERVEE

    vrai = solveur.resoudre
    demarre = threading.Event()

    def moteur_arrete_avec_solution(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        resultat = vrai(instantane, options, None)
        return dataclasses.replace(
            resultat,
            message=f"{resultat.message} {MESSAGE_SOLUTION_CONSERVEE}",
            statistiques={**resultat.statistiques, "arrete_par_utilisateur": True},
        )

    monkeypatch.setattr(solveur, "resoudre", moteur_arrete_avec_solution)
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    assert demarre.wait(DELAI)
    en_cours = client.get(f"/calculs/{calcul_id}/etat", headers=HX).text
    assert "le meilleur est conservé" in en_cours  # le bouton d'arrêt l'annonce
    generation.demander_arret(calcul_id)
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi and suivi.arret_demande and suivi.version_id is not None

    page = client.get(f"/calculs/{calcul_id}").text
    assert "Calcul arrêté : meilleure solution conservée" in page
    assert "la meilleure solution trouvée a été conservée" in page
    assert "Voir l&#39;emploi du temps" in page or "Voir l'emploi du temps" in page


def test_bilan_de_qualite_du_moteur_sans_doublon(jeu_minimal, client):
    calcul_id = _lancer(client, jeu_minimal.semestre_id, optimiser="1")
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi and "Bilan sur l'ensemble du semestre" in suivi.message

    page = client.get(f"/calculs/{calcul_id}").text
    assert "<p>Bilan sur l&#39;ensemble du semestre" in page
    assert "Emploi du temps trouvé :" not in page  # déjà dit par les compteurs
    assert calculs.qualite_du_moteur(
        "Emploi du temps trouvé : 3 séance(s) placée(s). Bilan sur l'ensemble du semestre : "
        "2 créneau(x) libre(s) entre deux cours et 0 séance(s) le samedi. 12 séance(s) "
        "datée(s) : 12 à la date prévue, 0 replacée(s), 0 à programmer."
    ) == (
        "Bilan sur l'ensemble du semestre : 2 créneau(x) libre(s) entre deux cours et "
        "0 séance(s) le samedi."
    )


@pytest.mark.cas_limite
def test_arret_d_un_calcul_termine_dit_qu_il_est_fini(version_generee, client):
    """Cas : « Arrêter » cliqué dans un second onglet resté sur le suivi d'un calcul déjà terminé.
    Risque : « Arrêt demandé » affiché : l'utilisateur croit avoir interrompu un calcul fini dont
    la version est déjà active.
    """
    calcul_id = version_generee.calcul_id

    sans_htmx = client.post(f"/calculs/{calcul_id}/arreter")  # suit la redirection
    assert sans_htmx.status_code == 200
    assert "Ce calcul est déjà terminé : rien à arrêter." in sans_htmx.text
    assert "Arrêt demandé" not in sans_htmx.text

    fragment = client.post(f"/calculs/{calcul_id}/arreter", headers=HX)
    assert fragment.status_code == 200 and "<html" not in fragment.text
    assert "Ce calcul est déjà terminé : rien à arrêter." in fragment.text
    assert "Arrêter le calcul" not in fragment.text

    suivi = generation.etat_calcul(calcul_id)
    assert suivi.reussi and suivi.version_id == version_generee.version_id
    assert not suivi.arret_demande


@pytest.mark.cas_limite
def test_calcul_reussi_sans_seances_datees_mene_au_bouton_qui_les_produit(
    jeu_minimal, client, monkeypatch
):
    """Cas : le calcul enregistre sa semaine type, puis la production des séances datées échoue ;
    la page du calcul dit de les produire avec le bouton « Produire les séances datées ».

    Risque : le lien « Voir l'emploi du temps » ouvre la semaine type, où ce bouton n'existe pas
    (il n'apparaît que dans la vue datée) : l'utilisateur ne trouve pas ce qu'on lui indique.
    """

    def instanciation_en_panne(session, version_id, *args, **kwargs):
        raise RuntimeError("panne simulée")

    monkeypatch.setattr(generation, "instancier_version", instanciation_en_panne)
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    assert generation.attendre_calcul(calcul_id, DELAI).reussi
    page = client.get(f"/calculs/{calcul_id}").text
    assert "Produire les séances datées" in page
    assert _bouton_produire_au_bout_du_lien(client, page)


@pytest.mark.cas_limite
@pytest.mark.parametrize("version_enregistree", [False, True], ids=["sans version", "avec version"])
def test_calcul_interrompu_par_un_redemarrage(version_generee, client, version_enregistree):
    """Cas : le serveur redémarre pendant un calcul : au démarrage, le calcul resté « en cours »
    est clos ; il avait pu, ou non, enregistrer sa version avant ses séances datées.
    Risque : page du calcul qui parle d'un « problème dans les données » ou affirme qu'aucun
    emploi du temps n'a été enregistré alors qu'une version active sans séances datées existe ;
    aucun moyen de relancer depuis la page.
    """
    calcul_id = version_generee.calcul_id
    with session_base() as session:
        # Tel que le laisse un processus tué : « en cours », sans numéro de processus vivant.
        session.execute(
            update(m.Calcul)
            .where(m.Calcul.id == calcul_id)
            .values(etat=EtatCalcul.EN_COURS, statistiques={}, message="", termine_le=None)
        )
        session.execute(
            delete(m.Occurrence).where(m.Occurrence.version_id == version_generee.version_id)
        )
        if not version_enregistree:
            session.execute(delete(m.VersionEdt).where(m.VersionEdt.calcul_id == calcul_id))
    assert generation.cloturer_calculs_interrompus() == 1  # ce que fait le démarrage du serveur

    page = client.get(f"/calculs/{calcul_id}").text
    assert "Le calcul n&#39;a pas pu aboutir" in page
    assert "interrompu par un arrêt de l&#39;application" in page and "problème" not in page
    assert "Relancer le même calcul" in page
    # Le suivi interrogé chaque seconde reçoit la même fin : formulaire de relance, références
    # techniques repliées.
    fragment = client.get(f"/calculs/{calcul_id}/etat", headers=HX).text
    assert f'action="/calculs/{calcul_id}/relancer"' in fragment
    assert "<summary>Références pour obtenir de l'aide</summary>" in fragment
    if version_enregistree:
        assert "Aucun emploi du temps n&#39;a été enregistré" not in page
        assert "Produire les séances datées" in page
        assert _bouton_produire_au_bout_du_lien(client, page)
    else:
        assert "Aucun emploi du temps n&#39;a été enregistré" in page
        assert "Voir l'emploi du temps" not in page
    relance = client.post(f"/calculs/{calcul_id}/relancer", follow_redirects=False)
    assert relance.status_code == 303, relance.text
    nouveau = int(relance.headers["location"].rsplit("/", 1)[1])
    assert generation.attendre_calcul(nouveau, DELAI).reussi


@pytest.mark.cas_limite
def test_prechauffage_qui_ne_peut_pas_demarrer_ne_casse_pas_la_page(
    version_generee, client, monkeypatch, caplog
):
    """Cas : le fil de préchauffage des écrans ne peut pas démarrer (plus de fil disponible)
    quand la page d'un calcul réussi s'affiche.
    Risque : RuntimeError remontée dans la requête (page du calcul en erreur 500), et
    préchauffage tenu pour « en cours » à jamais : plus aucun préchauffage jusqu'au redémarrage.
    """

    class FilImpossible:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(prechauffage, "ACTIF", True)
    monkeypatch.setattr(prechauffage.threading, "Thread", FilImpossible)
    page = client.get(f"/calculs/{version_generee.calcul_id}")
    assert page.status_code == 200 and "Emploi du temps généré" in page.text
    assert any("Préchauffage des écrans non lancé" in r.getMessage() for r in caplog.records)
    assert not prechauffage._en_cours  # la demande suivante pourra relancer le préchauffage


def test_prechauffage_apres_un_calcul_sur_une_version_active(version_generee, monkeypatch):
    """Deux demandes rapprochées de préchauffage après un calcul réussi : sans erreur ni trace."""
    monkeypatch.setattr(prechauffage, "ACTIF", True)
    assert prechauffage.lancer(charger_configuration().db_url)
    prechauffage.lancer(charger_configuration().db_url)  # ignorée si le premier fil tourne
    for fil in threading.enumerate():
        if fil.name == "prechauffage-ecrans":
            fil.join(DELAI)
    assert not prechauffage._en_cours


@pytest.mark.cas_limite
def test_retouches_de_la_version_active_annoncees_avant_le_calcul(reperes, client):
    """Cas : une séance datée de la version active est annulée à la main, puis l'utilisateur
    revient sur Générer.
    Risque : le nouveau calcul crée une version sans cette retouche et la séance annulée
    réapparaît, sans que personne ait été prévenu.
    """
    page = client.get(f"/calculs?semestre_id={reperes.semestre}").text
    assert "retouche à la main" not in page
    annulation = client.post(
        f"/emploi-du-temps/occurrences/{reperes.occurrence}/annuler", data={"motif": "Grève"}
    )
    assert annulation.status_code in (200, 303)
    page = client.get(f"/calculs?semestre_id={reperes.semestre}").text
    assert "La version active compte 1 retouche à la main" in page
    assert "seules les séances verrouillées gardent leur place" in page


@pytest.mark.cas_limite
def test_generer_sans_aucun_besoin_dit_quoi_faire(base, client):
    """Cas : page Générer d'un semestre qui n'a encore aucun besoin (base neuve, remise à zéro).
    Risque : bandeau d'erreur rouge sans issue, ou calcul lancé pour enregistrer une version
    vide.
    """
    with session_base() as session:
        semestre = session.scalar(select(m.Semestre.id).order_by(m.Semestre.id))
    page = client.get(f"/calculs?semestre_id={semestre}").text
    assert "Aucun besoin d'enseignement à placer pour ce semestre." in page
    for lien in ('href="/besoins"', 'href="/import"', 'href="/jeux"'):
        assert lien in page, lien
    assert page.count("""disabled title="Aucun besoin d'enseignement à placer\"""") == 2
    assert "Rien à calculer pour l&#39;instant" in page
    assert "Le calcul ne peut pas être lancé" not in page  # pas de bandeau d'erreur


@pytest.mark.cas_limite
def test_suivi_dit_qu_un_emploi_du_temps_est_deja_trouve(jeu_minimal, client, monkeypatch):
    """Cas : calcul long (grand établissement) qui a trouvé un emploi du temps complet et cherche
    à l'améliorer pendant des minutes.
    Risque : l'utilisateur ne voit qu'un temps qui passe, ignore qu'arrêter garderait déjà un
    emploi du temps, et attend la fin pendant la démonstration.
    """
    signale, liberer = threading.Event(), threading.Event()

    def moteur_qui_trouve_puis_attend(instantane, options, arret=None, sur_premiere_solution=None):
        sur_premiere_solution(48.0)
        signale.set()
        liberer.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(solveur, "resoudre", moteur_qui_trouve_puis_attend)
    calcul_id = _lancer(client, jeu_minimal.semestre_id)
    try:
        assert signale.wait(DELAI)
        fragment = client.get(f"/calculs/{calcul_id}/etat", headers=HX).text
        assert "Un emploi du temps est déjà trouvé (après 48 s)" in fragment
        assert "le meilleur sera gardé" in fragment
    finally:
        liberer.set()
    assert generation.attendre_calcul(calcul_id, DELAI).etat is EtatCalcul.ARRETE
