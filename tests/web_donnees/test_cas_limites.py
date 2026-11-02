"""Banque des cas limites de l'espace « Données » : calcul en cours, dates aberrantes,
adresses et en-têtes fabriqués, fragments ouverts à la main, champs libres piégés.

Chaque test reproduit un scénario de l'inventaire des cas limites (son numéro
est rappelé dans les titres de section) et commence par « Cas : ... Risque :
... ». Un cas qui relève d'un autre lot reste marqué `xfail(strict=True)` avec
le fichier à corriger : le jour où ce lot l'aura corrigé, le test passera
(XPASS) et échouera pour qu'on retire la marque.

Les autres cas limites de l'espace sont rangés avec leur écran :
`test_parametres.py` (calcul en cours), `test_saisies_extremes.py` (nombres de
5 000 chiffres, textes libres), `test_regles.py` (identifiants énormes, aucune
année), `test_suppression.py` (rejeu), `test_performance.py` (jeu grand).
"""

from __future__ import annotations

import logging
import re

import pytest
from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.regles import EtatRegle
from edt.services import generation
from edt.services import regles as service_regles
from edt.services.referentiels import operations

from .conftest import (
    HX,
    LOT_WEB_EDT,
    compter,
    depuis,
    existe,
    formulaire_de,
    identifiant,
    occurrence_prevue,
    saisie_salle,
    seance_placee,
    texte_visible,
)

pytestmark = pytest.mark.cas_limite

ILLISIBLE = "http://[::1"
"""En-tête `HX-Current-URL` que `urlsplit` refuse (« Invalid IPv6 URL »)."""
INJECTE = "\r\nX-Injecte: 1"
"""Suite d'une adresse de retour fabriquée : un retour à la ligne suivi d'un faux en-tête."""
EN_COURS = "calcul d'emploi du temps est en cours"


# --------------------------------------------------------------------------- #
# Calcul en cours : aucune suppression sous ses pieds (inventaire n° 1 et 2)
# --------------------------------------------------------------------------- #


def test_suppression_d_un_besoin_ou_d_une_salle_pendant_le_calcul_est_refusee(
    site, ids, calcul_retenu, caplog
):
    """Cas : pendant un calcul, on supprime un besoin ou une salle du semestre calculé (page ou
    ligne). Risque : la suppression passe, le calcul enregistre des séances qui pointent vers
    elle, échoue sur une contrainte d'intégrité et ses minutes de travail sont perdues."""
    besoin = identifiant(m.Besoin, m.Besoin.semestre_id, calcul_retenu.semestre_id)
    salle = ids.salle_n016
    suivi = f'href="/calculs/{calcul_retenu.id}"'

    # Confirmation (page et ligne) : refus expliqué, lien vers le suivi, aucun bouton.
    confirmation = site.get(f"/besoins/{besoin}/supprimer")
    assert confirmation.status_code == 200 and EN_COURS in texte_visible(confirmation)
    assert suivi in confirmation.text and "Supprimer définitivement" not in confirmation.text
    ligne = site.get(f"/salles/{salle}/ligne/suppression", headers=depuis("/salles"))
    assert EN_COURS in texte_visible(ligne) and suivi in ligne.text
    assert "hx-post" not in ligne.text  # pas de bouton de suppression

    # Envois directs (formulaire resté ouvert, double clic) : refusés, rien n'est supprimé.
    page = site.post(
        f"/besoins/{besoin}/supprimer", data={"_retour": "/besoins"}, follow_redirects=False
    )
    assert page.status_code == 303
    assert page.headers["location"].startswith(f"/besoins/{besoin}/supprimer?")
    assert EN_COURS in texte_visible(site.get(page.headers["location"]))
    zone = site.post(f"/salles/{salle}/ligne/supprimer", headers=depuis("/salles"))
    assert zone.status_code == 200 and EN_COURS in texte_visible(zone)
    assert "message-erreur" in zone.text and "supprimée" not in zone.text
    assert existe(m.Besoin, besoin) and existe(m.Salle, salle)

    # Le calcul enregistre sa solution sans erreur...
    with caplog.at_level(logging.INFO):
        fin = calcul_retenu.liberer()
    assert fin.reussi and fin.version_id is not None, fin.message
    erreurs = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not erreurs, [r.getMessage() for r in erreurs]
    # ... et la même suppression passe une fois le calcul fini.
    apres = site.post(f"/besoins/{besoin}/supprimer", data={"_retour": "/besoins"})
    assert "supprimé" in apres.text and not existe(m.Besoin, besoin)


def test_suppression_du_semestre_refusee_pendant_son_calcul(site, ids, calcul_retenu):
    """Cas : on supprime le semestre (ou l'année) pendant son calcul. Risque : le calcul est
    supprimé en cascade sous son fil : le suivi répond 404 chaque seconde, le verrou « un seul
    calcul à la fois » tombe et un second calcul peut démarrer en parallèle."""
    semestre = calcul_retenu.semestre_id
    page = site.post(
        f"/calendrier/semestres/{semestre}/supprimer",
        data={"_retour": "/calendrier/semestres"},
        follow_redirects=False,
    )
    assert page.status_code == 303
    assert page.headers["location"].startswith(f"/calendrier/semestres/{semestre}/supprimer?")
    assert EN_COURS in texte_visible(site.get(page.headers["location"]))
    zone = site.post(
        f"/calendrier/semestres/{semestre}/ligne/supprimer", headers=depuis("/calendrier/semestres")
    )
    assert EN_COURS in texte_visible(zone)
    annee = site.post(
        f"/calendrier/annees/{ids.annee}/supprimer",
        data={"_retour": "/calendrier/annees"},
        follow_redirects=False,
    )
    assert annee.headers["location"].startswith(f"/calendrier/annees/{ids.annee}/supprimer?")

    assert existe(m.Semestre, semestre) and existe(m.AnneeScolaire, ids.annee)
    en_cours = generation.calcul_en_cours()
    assert en_cours is not None and en_cours.id == calcul_retenu.id  # verrou toujours tenu
    assert site.get(f"/calculs/{calcul_retenu.id}/etat", headers=HX).status_code == 200
    assert calcul_retenu.liberer().reussi


def test_regles_personnalisees_supprimables_pendant_un_calcul(site, calcul_retenu):
    """Cas : pendant un calcul, on supprime les règles personnalisées actives. Risque : si le
    calcul relisait ses règles à la fin, il échouerait ; il les copie à son lancement, et ce test
    garde la décision de ne pas bloquer leur suppression (contrairement aux autres données)."""
    with session_base() as session:
        ids_regles = session.scalars(
            select(m.Regle.id).where(m.Regle.etat == EtatRegle.ACTIVE)
        ).all()
    assert ids_regles  # le jeu minimal en a (R001, R002)
    for regle in ids_regles:
        reponse = site.post(f"/regles/{regle}/supprimer", data={"_retour": "/regles"})
        assert reponse.status_code == 200 and "supprimée" in reponse.text
    fin = calcul_retenu.liberer()
    assert fin.reussi, fin.message


# --------------------------------------------------------------------------- #
# Dates aberrantes d'une année ou d'un semestre (inventaire n° 3)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("debut", "fin", "champ", "message"),
    [
        ("2026-09-01", "9999-12-31", "date_fin", "Une année scolaire dure deux ans au plus"),
        ("2026-09-01", "2227-08-31", "date_fin", "Une année scolaire dure deux ans au plus"),
        ("9998-09-01", "9999-08-31", "date_debut", "commencer entre 2000 et 2100"),
    ],
    ids=["fin-9999", "faute-de-frappe-2227", "aux-confins-du-calendrier"],
)
def test_duree_d_une_annee_bornee(site, debut, fin, champ, message):
    """Cas : une année scolaire saisie jusqu'au 31/12/9999, ou « 2227 » pour « 2027 ». Risque :
    acceptée, elle fait déborder le calcul des semaines (erreur 500 sur Générer) ou produit des
    centaines de milliers de séances datées."""
    reponse = site.post(
        "/calendrier/annees", data={"libelle": "Aberrante", "date_debut": debut, "date_fin": fin}
    )
    assert reponse.status_code == 422
    erreur = re.search(rf'id="erreur-{champ}">([^<]+)<', reponse.text)
    assert erreur is not None and message in erreur.group(1)
    assert compter(m.AnneeScolaire, m.AnneeScolaire.libelle == "Aberrante") == 0


def test_duree_d_un_semestre_bornee(site):
    """Cas : un semestre de plus d'un an dans une année longue (deux ans, permise). Risque : un
    calcul sur ce semestre instancie des milliers de semaines et gèle le serveur."""
    site.post(
        "/calendrier/annees",
        data={"libelle": "2027-2029", "date_debut": "2027-09-01", "date_fin": "2029-08-31"},
    )
    annee = identifiant(m.AnneeScolaire, m.AnneeScolaire.libelle, "2027-2029")
    assert annee is not None  # deux ans : permis
    reponse = site.post(
        "/calendrier/semestres",
        data={
            "annee_scolaire_id": str(annee),
            "code": "SL",
            "libelle": "Semestre trop long",
            "date_debut": "2027-09-06",
            "date_fin": "2029-06-29",
        },
    )
    assert reponse.status_code == 422
    erreur = re.search(r'id="erreur-date_fin">([^<]+)<', reponse.text).group(1)
    assert "Un semestre dure un an au plus" in erreur
    assert identifiant(m.Semestre, m.Semestre.code, "SL") is None


def test_calculs_d_un_semestre_sain_sans_erreur(site, ids):
    """Cas : la page Générer d'un semestre ordinaire, après les bornes posées sur les dates.
    Risque : une borne trop stricte ferait refuser ou planter un semestre normal."""
    reponse = site.get(f"/calculs?semestre_id={ids.semestre}")
    assert reponse.status_code == 200 and "Une erreur inattendue" not in reponse.text


# --------------------------------------------------------------------------- #
# Adresse de retour fabriquée : jamais dans un en-tête (inventaire n° 4)
# --------------------------------------------------------------------------- #


def _sans_injection(reponse) -> None:
    assert "x-injecte" not in {cle.lower() for cle in reponse.headers}
    for cle, valeur in reponse.headers.items():
        assert not re.search(r"[\x00-\x1f\x7f]", valeur), (cle, valeur)


@pytest.mark.parametrize("htmx", [True, False], ids=["htmx", "sans-javascript"])
def test_adresse_de_retour_avec_saut_de_ligne_est_ecartee(site, ids, htmx):
    """Cas : le champ caché `_retour` d'un formulaire est modifié en « /salles », un retour à la
    ligne et « X-Injecte: 1 ». Risque : la valeur part telle quelle dans l'en-tête HX-Redirect
    (en-tête injecté, ou réponse perdue), ou dans une redirection vers une adresse mutilée."""
    entetes = HX if htmx else {}
    regle = identifiant(m.Regle, m.Regle.code, "R001")
    envois = (
        (
            f"/salles/{ids.salle_n016}",
            {**formulaire_de("salle", ids.salle_n016), "_retour": "/salles" + INJECTE},
            "/salles",
        ),
        (
            f"/regles/{regle}/etat",
            {"etat": "inactive", "_retour": "/regles" + INJECTE},
            f"/regles?annee={ids.annee}",
        ),
    )
    for adresse, donnees, defaut in envois:
        reponse = site.post(adresse, data=donnees, headers=entetes, follow_redirects=False)
        _sans_injection(reponse)
        if htmx:
            assert reponse.status_code == 204 and reponse.headers["hx-redirect"] == defaut
        else:
            assert reponse.status_code == 303 and reponse.headers["location"] == defaut


# --------------------------------------------------------------------------- #
# En-tête HX-Current-URL illisible (inventaire n° 6)
# --------------------------------------------------------------------------- #


def test_hx_current_url_illisible_est_ignore(site, ids, caplog):
    """Cas : une requête HTMX arrive avec l'en-tête HX-Current-URL « http://[::1 » (navigateur
    ou extension fautifs). Risque : urlsplit lève ValueError, erreur 500 et trace dans le
    journal pour une simple ligne de tableau."""
    regle = identifiant(m.Regle, m.Regle.code, "R001")
    adresses = (
        (f"/salles/{ids.salle_n016}/ligne", {}),
        (f"/salles/{ids.salle_n016}/ligne/suppression", {}),
        ("/salles", {"HX-Target": "zone-liste"}),
        (f"/campus/{ids.ndc}/ligne/edition", {}),
        (f"/regles/{regle}/ligne", {}),
        (f"/regles/{regle}/ligne/suppression", {}),
    )
    for adresse, autres in adresses:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="edt.web.gabarits"):
            reponse = site.get(adresse, headers={**HX, "HX-Current-URL": ILLISIBLE, **autres})
        assert reponse.status_code == 200, adresse
        assert "Une erreur inattendue" not in reponse.text, adresse
        assert any("HX-Current-URL" in r.getMessage() for r in caplog.records), adresse


@pytest.mark.xfail(
    strict=True,
    reason=LOT_WEB_EDT + "src/edt/web/routes/emploi_du_temps.py, _adresse_de_la_page "
    "(urlsplit de HX-Current-URL sans garde ; edt.web.gabarits.adresse_courante le fait)",
)
def test_hx_current_url_illisible_dans_le_panneau_d_une_seance(site, version_generee):
    """Cas : le panneau d'une séance (grille de l'emploi du temps) demandé avec un
    HX-Current-URL illisible. Risque : erreur 500 au clic sur une séance."""
    seance = seance_placee(version_generee)
    reponse = site.get(
        f"/emploi-du-temps/seances/{seance.id}/panneau",
        headers={**HX, "HX-Current-URL": ILLISIBLE},
    )
    assert reponse.status_code == 200


# --------------------------------------------------------------------------- #
# Fragments HTMX ouverts sans HTMX (inventaire n° 9)
# --------------------------------------------------------------------------- #


def test_fragments_sans_htmx_redirigent_vers_la_page(site, ids):
    """Cas : l'adresse d'un fragment (ligne, confirmation dans la ligne, aperçu de règle) est
    ouverte directement : lien copié, historique, navigateur sans JavaScript. Risque : un morceau
    de tableau nu, sans menu ni styles, dont les boutons perdent le contexte de la liste."""
    regle = identifiant(m.Regle, m.Regle.code, "R001")
    salle, campus = ids.salle_n016, ids.ndc
    attendus = (
        (f"/salles/{salle}/ligne", f"/salles/{salle}"),
        (f"/salles/{salle}/ligne/suppression", f"/salles/{salle}/supprimer"),
        (f"/campus/{campus}/ligne/edition", f"/campus/{campus}"),
        (f"/regles/{regle}/ligne", f"/regles/{regle}/modifier"),
        (f"/regles/{regle}/ligne/suppression", f"/regles/{regle}/supprimer"),
    )
    for adresse, page in attendus:
        reponse = site.get(adresse, follow_redirects=False)
        assert reponse.status_code == 303, adresse
        assert reponse.headers["location"] == page, adresse
        assert "<html" in site.get(adresse).text, adresse  # la page entière, avec son menu

    apercu = site.post(
        "/regles/apercu",
        data={"type": "JOURS_TRAVAILLES", "annee": str(ids.annee)},
        follow_redirects=False,
    )
    assert apercu.status_code == 303
    assert apercu.headers["location"] == f"/regles/nouveau?annee={ids.annee}&type=JOURS_TRAVAILLES"

    # Enregistrement depuis la ligne sans HTMX : retour à la liste, pas un morceau de tableau.
    modifie = site.post(
        f"/campus/{campus}/ligne", data=formulaire_de("campus", campus), follow_redirects=False
    )
    assert modifie.status_code == 303 and modifie.headers["location"] == "/campus"


@pytest.mark.parametrize(
    "adresse",
    [
        pytest.param(
            "/a-traiter/compteur",
            marks=pytest.mark.xfail(
                strict=True, reason=LOT_WEB_EDT + "src/edt/web/routes/a_traiter.py (compteur)"
            ),
        ),
        "/calculs/verifications",  # déjà correct : la page entière
        pytest.param(
            "calcul_etat",
            marks=pytest.mark.xfail(
                strict=True,
                reason=LOT_WEB_EDT + "src/edt/web/routes/calculs.py (/calculs/<n>/etat)",
            ),
        ),
        pytest.param(
            "formulaire_seance",
            marks=pytest.mark.xfail(
                strict=True,
                reason=LOT_WEB_EDT + "src/edt/web/routes/emploi_du_temps.py (formulaire de séance)",
            ),
        ),
    ],
)
def test_fragments_des_autres_ecrans_sans_htmx(site, version_generee, adresse):
    """Cas : un fragment des écrans Générer, Emploi du temps ou À traiter ouvert directement.
    Risque : HTML nu sans mise en page (le compteur d'onglet, le suivi d'un calcul...)."""
    with session_base() as session:
        calcul = session.get(m.VersionEdt, version_generee).calcul_id
    seance = seance_placee(version_generee).id
    adresse = {
        "calcul_etat": f"/calculs/{calcul}/etat",
        "formulaire_seance": f"/emploi-du-temps/seances/{seance}/formulaire",
    }.get(adresse, adresse)
    reponse = site.get(adresse, follow_redirects=False)
    assert reponse.status_code == 303 or "<html" in reponse.text, adresse


# --------------------------------------------------------------------------- #
# Champs libres piégés : échappés sur toutes les pages (inventaire n° 13)
# --------------------------------------------------------------------------- #

CODE_PIEGE = "<b>x</b>"
BATIMENT_PIEGE = '"><img src=x onerror=alert(1)>'
LIBELLE_PIEGE = "<script>alert('groupe')</script>"
COMMENTAIRE_PIEGE = "<script>alert('regle')</script>"
MOTIF_PIEGE = "<script>alert('motif')</script>"
BRUTS = (CODE_PIEGE, "<img src=x", "<script>alert(")
"""Ce qui ne doit jamais apparaître tel quel dans une page."""


@pytest.fixture
def donnees_piegees(site, ids, version_generee):
    """Une salle, un groupe, une règle et une annulation dont les textes contiennent du HTML.

    La salle et le groupe sont ceux d'une séance placée de la version : ils
    s'affichent aussi dans la grille et le panneau de l'emploi du temps.
    """
    seance = seance_placee(version_generee)
    occurrence = occurrence_prevue(version_generee)
    with session_base() as session:
        operations.modifier(
            session,
            "salle",
            seance.salle_id,
            {"code": CODE_PIEGE, "batiment": BATIMENT_PIEGE},
            partiel=True,
        )
        operations.modifier(
            session, "groupe", seance.groupe_id, {"libelle": LIBELLE_PIEGE}, partiel=True
        )
        service_regles.modifier(session, ids.annee, "R001", {"commentaire": COMMENTAIRE_PIEGE})
    annulee = site.post(
        f"/emploi-du-temps/occurrences/{occurrence}/annuler",
        data={"motif": MOTIF_PIEGE, "retour": "/emploi-du-temps"},
    )
    assert annulee.status_code == 200
    return {"seance": seance, "occurrence": occurrence, "version": version_generee}


def _controler_echappement(site, pages: dict[str, str | None], entetes: dict) -> None:
    for adresse, echappe in pages.items():
        reponse = site.get(adresse, headers=entetes)
        assert reponse.status_code == 200, adresse
        for brut in BRUTS:
            assert brut not in reponse.text, (adresse, brut)
        if echappe is not None:  # le texte est bien affiché, sous sa forme échappée
            assert echappe in reponse.text, (adresse, echappe)


def test_champs_libres_echappes_sur_les_pages_de_donnees(site, ids, donnees_piegees):
    """Cas : un code de salle « <b>x</b> », un bâtiment « "><img onerror=...> », un libellé de
    groupe et un commentaire de règle « <script> », affichés dans les listes, fiches,
    confirmations, lignes HTMX et messages. Risque : un seul |safe oublié et du script
    s'exécute chez le responsable de scolarité."""
    salle = donnees_piegees["seance"].salle_id
    message = site.post("/salles", data=saisie_salle(ids, code="<i>y</i>"))  # message suivant
    assert "Salle « &lt;i&gt;y&lt;/i&gt;" in message.text and "<i>y</i>" not in message.text
    _controler_echappement(
        site,
        {
            "/salles": "&lt;b&gt;x&lt;/b&gt;",
            "/salles?q=<b>": "&lt;b&gt;",
            f"/salles/{salle}": 'value="&lt;b&gt;x&lt;/b&gt;"',
            f"/salles/{salle}/supprimer": "&lt;b&gt;x&lt;/b&gt;",
            "/groupes": "&lt;script&gt;alert(&#39;groupe&#39;)&lt;/script&gt;",
            "/groupes/arbre": "&lt;script&gt;",
            "/regles": "&lt;script&gt;alert(&#39;regle&#39;)&lt;/script&gt;",
            f"/regles/nouveau?annee={ids.annee}&type=SALLES": "&lt;b&gt;x&lt;/b&gt;",
        },
        {},
    )
    _controler_echappement(
        site, {f"/salles/{salle}/ligne": "&lt;b&gt;x&lt;/b&gt;"}, depuis("/salles")
    )


def test_champs_libres_echappes_sur_les_pages_de_l_emploi_du_temps(site, donnees_piegees):
    """Cas : les mêmes textes piégés, plus un motif d'annulation « <script> », dans la grille,
    les panneaux de séance, l'export et À traiter. Risque : script exécuté au simple affichage
    de l'emploi du temps."""
    seance, occurrence = donnees_piegees["seance"], donnees_piegees["occurrence"]
    _controler_echappement(
        site,
        {
            f"/emploi-du-temps?version_id={donnees_piegees['version']}": "&lt;b&gt;x&lt;/b&gt;",
            "/export": None,
            "/a-traiter": None,
        },
        {},
    )
    _controler_echappement(
        site,
        {
            f"/emploi-du-temps/seances/{seance.id}/panneau": "&lt;b&gt;x&lt;/b&gt;",
            f"/emploi-du-temps/occurrences/{occurrence}/panneau": "&lt;script&gt;alert(",
        },
        depuis("/emploi-du-temps"),
    )
