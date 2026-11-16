"""Entrées refusées lisiblement et avant toute écriture.

Règles que ces tests figent :

- une entrée refusée l'est par une `ErreurDonneesInvalides` lisible (jamais une
  `ValueError`, une `IndexError` ou une `IntegrityError` brute) et **avant toute
  écriture** ;
- une entrée admise mais pauvre (peu de salles, semestre minuscule, grille réduite)
  donne un jeu cohérent : chaque besoin préparé est créé ou **écarté avec sa raison**
  (`total_prevu`), et le témoin reste valide (`problemes_du_temoin`) ;
- le jeu ne dépend que de la taille, de la graine et de la version du générateur
  (pas des noms de Faker, ni de la machine).

Chaque test commence par « Cas : ... Risque : ... ». Tous portent le marqueur
`cas_limite` ; les plus longs portent aussi `perf`. Aides : `tests/outils/synthese/aides.py`
et `tests/outils/synthese/controle_temoin.py`. Le cas limite de la commande
`python -m edt jeu` est dans `tests/integration/synthese/test_cas_limites.py`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools

import pytest
from faker import Faker
from sqlalchemy import delete, func, select, update
from tests.outils.synthese.aides import creer_base, generer_dans, total_prevu
from tests.outils.synthese.controle_temoin import problemes_du_temoin
from tests.outils.synthese.empreinte import empreinte_semantique

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import NatureGroupe, NatureJourFerme
from edt.erreurs import ErreurDonneesInvalides
from edt.services import jeux
from edt.services.parametres import ecrire_parametre
from edt.services.regles import lister
from edt.synthese import MOYEN, PETIT, ConfigPromotion, ConfigurationJeu, SallesSite
from edt.synthese.configuration import ANNEE_DEBUT_MAX, ANNEE_DEBUT_MIN, GRAINE_MAX
from edt.synthese.generateur import ESSAIS_NOM_UNIQUE, generer_jeu

pytestmark = pytest.mark.cas_limite

EMPREINTE_PETIT_GRAINE_0 = "212dffba6961c065ea2f16c2553470c9969d4d45ef0d7868fbc9968c74856539"
"""Empreinte sémantique du jeu « petit », graine 0 (sans les noms tirés par Faker).

Si le test qui la compare échoue après une modification VOULUE du générateur (catalogue,
témoin, règles, valeurs par défaut de la base), recalculer la valeur et la remplacer ici.
S'il échoue sans modification, le jeu dépend de la machine : c'est un défaut à corriger.
"""

ISSY_COMPLET = SallesSite(
    amphis=(60,), cours=4, informatique=2, electronique=1, physique=1, langues=1, projet=1
)


def _nombre(url: str, modele) -> int:
    with session_base(url) as session:
        return session.scalar(select(func.count()).select_from(modele))


# --------------------------------------------------------------------------- #
# Entrées hors bornes : refusées lisiblement, avant toute écriture
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("annee", [0, 1999, 2101, 9999])
def test_annee_de_debut_hors_bornes_refusee_lisiblement(base_vierge, annee):
    """Cas : année scolaire de début absurde (0, 9999) ou hors de 2000-2100.
    Risque : `ValueError` « year 10000 is out of range » au lieu d'un refus lisible."""
    with (
        session_base(base_vierge) as session,
        pytest.raises(ErreurDonneesInvalides, match=f"{ANNEE_DEBUT_MIN} à {ANNEE_DEBUT_MAX}"),
    ):
        generer_jeu(session, "petit", annee_debut=annee)
    assert _nombre(base_vierge, m.Groupe) == 0


@pytest.mark.parametrize("annee", [2019, 2035])
def test_annee_passee_ou_lointaine_donne_un_jeu_valide(base_vierge, annee):
    """Cas : jeu demandé pour une année passée ou lointaine (autres fériés, autres lundis).
    Risque : calendrier ou témoin faux hors de l'année courante."""
    rapport, _ = generer_dans(base_vierge, "petit", annee_debut=annee)
    assert rapport.annee == f"{annee}-{annee + 1}"
    assert rapport.besoins_ecartes == ()
    assert problemes_du_temoin(base_vierge, rapport) == []


@pytest.mark.parametrize("graine", [-1, -5, GRAINE_MAX + 1, 2**100, True, 1.5])
def test_graine_hors_bornes_refusee(base_vierge, graine):
    """Cas : graine négative, trop grande ou non entière.
    Risque : `random.Random(-5)` redonne silencieusement le jeu de la graine 5."""
    with (
        session_base(base_vierge) as session,
        pytest.raises(ErreurDonneesInvalides, match="Graine invalide"),
    ):
        generer_jeu(session, "petit", graine)


@pytest.mark.parametrize("graine", [0, GRAINE_MAX])
def test_graines_extremes_admises_et_deterministes(tmp_path, graine):
    """Cas : plus petite et plus grande graine admises.
    Risque : jeu différent d'une génération à l'autre pour une même graine extrême."""
    premier, empreinte = generer_dans(creer_base(tmp_path / "a"), "petit", graine)
    _, empreinte_bis = generer_dans(creer_base(tmp_path / "b"), "petit", graine)
    assert empreinte == empreinte_bis
    assert premier.nb_besoins > 0 and premier.besoins_ecartes == ()


def test_borne_de_graine_identique_au_formulaire_web():
    """Cas : même graine saisie à l'écran et en ligne de commande.
    Risque : une graine acceptée par la commande refusée par l'interface, ou l'inverse."""
    from edt.web.routes.donnees_jeux import GRAINE_MAX as GRAINE_MAX_WEB

    assert GRAINE_MAX == GRAINE_MAX_WEB


def test_service_refuse_une_graine_invalide_avant_la_remise_a_zero(base):
    """Cas : le service de rechargement (écran, futur assistant) reçoit une graine refusée
    avec la remise à zéro. Risque : toutes les données perdues pour une faute de saisie."""
    jeux.recharger_jeu_fictif("petit")
    with pytest.raises(ErreurDonneesInvalides):
        jeux.recharger_jeu_fictif("petit", graine=-1, reinitialiser=True)
    with session_base() as session:
        assert session.scalar(select(func.count()).select_from(m.Besoin)) > 0


# --------------------------------------------------------------------------- #
# Configurations sur mesure incohérentes ou pauvres
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("salles", [(), (("NDC", SallesSite()),)])
def test_configuration_sans_salle_refusee_a_la_construction(salles):
    """Cas : configuration sur mesure sans site, ou avec un site sans aucune salle.
    Risque : `IndexError` en pleine génération au lieu d'un refus qui liste le problème."""
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        dataclasses.replace(PETIT, salles=salles)
    assert "au moins un site avec au moins une salle" in str(erreur.value)


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({"amphis": (-5,)}, "-5"),
        ({"amphis": (0,)}, "capacité d'amphithéâtre invalide : 0"),
        ({"cours": -3}, "« cours » invalide : -3"),
    ],
)
def test_salles_site_negatives_refusees(champs, attendu):
    """Cas : capacité d'amphithéâtre nulle ou négative, nombre de salles négatif.
    Risque : `IntegrityError` SQLite brute, ou salles ignorées sans explication."""
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        SallesSite(**champs)
    assert attendu in str(erreur.value)


def test_configuration_qui_ne_tient_pas_dans_les_salles(base_vierge, jeu_petit):
    """Cas : une seule salle de cours pour toute une promotion (pas d'amphithéâtre).
    Risque : plantage, besoins perdus en silence ou témoin faux au lieu d'écarts expliqués."""
    etroite = dataclasses.replace(PETIT, nom="étroit", salles=(("NDC", SallesSite(cours=1)),))
    rapport, _ = generer_dans(base_vierge, etroite)
    assert rapport.besoins_ecartes and rapport.nb_besoins > 0
    assert total_prevu(rapport) == total_prevu(jeu_petit.rapport)
    assert all(e.raison and e.libelle for e in rapport.besoins_ecartes)
    assert any("aucune salle" in e.raison for e in rapport.besoins_ecartes)
    assert problemes_du_temoin(base_vierge, rapport) == []
    assert "Besoins écartés faute de place" in rapport.resume()


def test_configuration_minimale_un_etudiant_par_classe(base_vierge):
    """Cas : une classe et une classe d'apprentis d'un seul étudiant (demi-groupe vide,
    parcours minuscules). Risque : division par zéro, groupe vide mal compté, témoin faux."""
    minimale = ConfigurationJeu(
        nom="minimale",
        promotions=(ConfigPromotion(annee=5, nb_classes=1, nb_classes_apprentis=1),),
        salles=(("ISSY", ISSY_COMPLET),),
        effectif_classe=(1, 1),
        effectif_classe_apprentis=(1, 1),
    )
    rapport, _ = generer_dans(base_vierge, minimale)
    assert rapport.besoins_ecartes == ()
    assert problemes_du_temoin(base_vierge, rapport) == []
    with session_base(base_vierge) as session:
        promotion = session.scalar(select(m.Groupe).where(m.Groupe.code == "A5"))
        classes = session.scalars(
            select(m.Groupe).where(m.Groupe.nature == NatureGroupe.CLASSE)
        ).all()
        assert promotion.effectif == sum(c.effectif for c in classes) == 2


# --------------------------------------------------------------------------- #
# Calendrier, grille et paramètres dégénérés
# --------------------------------------------------------------------------- #


def _s1_d_un_jour(session):
    session.execute(
        update(m.Semestre)
        .where(m.Semestre.code == "S1")
        .values(date_debut=dt.date(2026, 9, 7), date_fin=dt.date(2026, 9, 7))
    )


def _un_seul_creneau(session):
    premier = session.scalar(
        select(m.Creneau.id).order_by(m.Creneau.jour, m.Creneau.debut_min).limit(1)
    )
    session.execute(delete(m.Creneau).where(m.Creneau.id != premier))


def _samedi_seul(session):
    ecrire_parametre(session, "jours_travailles", [5])


def _dimanche_seul_sans_creneau(session):
    ecrire_parametre(session, "jours_travailles", [6])


def _pause_de_deux_heures(session):
    ecrire_parametre(session, "pause_min", 120)


@pytest.mark.parametrize(
    "degrader",
    [
        _s1_d_un_jour,
        _un_seul_creneau,
        _samedi_seul,
        _dimanche_seul_sans_creneau,
        _pause_de_deux_heures,
    ],
    ids=["semestre d'un jour", "un seul créneau", "samedi seul", "dimanche seul", "pause de 2 h"],
)
def test_calendrier_et_grille_degeneres_sans_plantage(base_vierge, jeu_petit, degrader):
    """Cas : semestre d'un jour, grille d'un créneau, seul jour travaillé le samedi ou un
    dimanche sans créneau, pause de deux heures. Risque : division par zéro dans les taux,
    semaine d'intégration mal fermée, besoins perdus en silence, témoin faux."""
    with session_base(base_vierge) as session:
        degrader(session)
    rapport, _ = generer_dans(base_vierge, "petit")
    assert total_prevu(rapport) == total_prevu(jeu_petit.rapport)
    assert all(e.raison for e in rapport.besoins_ecartes)
    assert problemes_du_temoin(base_vierge, rapport) == []
    assert rapport.resume()


def test_besoin_impossible_d_avance_ecarte_et_explique(base_vierge):
    """Cas : la conférence métiers n'a aucune date ouverte (semestre d'un jour).
    Risque : besoin omis sans laisser de trace dans le rapport."""
    with session_base(base_vierge) as session:
        _s1_d_un_jour(session)
    rapport, _ = generer_dans(base_vierge, "petit")
    conferences = [e for e in rapport.besoins_ecartes if "Conférence métiers" in e.libelle]
    assert len(conferences) == 1
    assert "aucune date ouverte" in conferences[0].raison


def test_semestre_tres_court_avec_stage_ne_plante_pas(base_vierge, jeu_moyen):
    """Cas : semestre 2 de deux semaines pour une promotion qui finit l'année en stage.
    Risque : `IndexError` (le stage prend les quatre dernières semaines) au lieu d'un jeu."""
    with session_base(base_vierge) as session:
        session.execute(
            update(m.Semestre)
            .where(m.Semestre.code == "S2")
            .values(date_debut=dt.date(2027, 2, 1), date_fin=dt.date(2027, 2, 12))
        )
    rapport, _ = generer_dans(base_vierge, "moyen")
    assert total_prevu(rapport) == total_prevu(jeu_moyen.rapport)
    assert problemes_du_temoin(base_vierge, rapport) == []
    with session_base(base_vierge) as session:
        libelles = set(
            session.scalars(
                select(m.JourFerme.libelle).where(m.JourFerme.nature == NatureJourFerme.AUTRE)
            )
        )
    assert "Stage en entreprise" not in libelles


def test_grille_vide_refusee_lisiblement(base_vierge):
    """Cas : tous les créneaux de la grille supprimés avant de charger un jeu.
    Risque : jeu vide incompréhensible ou erreur technique."""
    with session_base(base_vierge) as session:
        session.execute(delete(m.Creneau))
    with (
        session_base(base_vierge) as session,
        pytest.raises(ErreurDonneesInvalides, match="ajouter des créneaux"),
    ):
        generer_jeu(session, "petit")


def test_semestre_manquant_refuse_lisiblement(base_vierge):
    """Cas : le semestre 2 a été supprimé et la taille demandée en a besoin.
    Risque : `KeyError` au lieu d'un refus qui nomme le semestre manquant."""
    with session_base(base_vierge) as session:
        session.execute(delete(m.Semestre).where(m.Semestre.code == "S2"))
    with (
        session_base(base_vierge) as session,
        pytest.raises(ErreurDonneesInvalides, match="pas de semestre S2"),
    ):
        generer_jeu(session, MOYEN)


# --------------------------------------------------------------------------- #
# Déterminisme : indépendant de Faker et de la machine
# --------------------------------------------------------------------------- #


def test_empreinte_figee_du_jeu_petit_graine_0(jeu_petit):
    """Cas : même taille et même graine sur une autre machine (CI en 3.11 et 3.13, serveur).
    Risque : les jeux divergent sans que personne ne le voie."""
    with jeu_petit.session() as session:
        empreinte = empreinte_semantique(session, jeu_petit.rapport)
    assert empreinte == EMPREINTE_PETIT_GRAINE_0, (
        "Le jeu « petit » graine 0 a changé : si c'est voulu, mettre à jour "
        "EMPREINTE_PETIT_GRAINE_0 ; sinon le jeu dépend de la machine (voir sa docstring)."
    )


def test_jeu_independant_des_noms_proposes_par_faker(base_vierge, jeu_petit, monkeypatch):
    """Cas : autre version de Faker (autre liste de noms), donc autres collisions de noms.
    Risque : les tirages du générateur suivent les noms et tout le jeu change avec Faker."""
    noms = itertools.cycle(["Durand", "Petit"])
    monkeypatch.setattr(Faker, "last_name", lambda self: next(noms), raising=False)
    monkeypatch.setattr(Faker, "first_name", lambda self: "Camille", raising=False)
    _, empreinte = generer_dans(base_vierge, "petit")
    with jeu_petit.session() as session:
        assert empreinte == empreinte_semantique(session, jeu_petit.rapport)


def test_noms_epuises_homonymes_admis_sans_boucle_infinie(base_vierge, monkeypatch):
    """Cas : plus d'enseignants que de noms distincts chez Faker (800 couples civilité-nom).
    Risque : boucle infinie, la génération ne se termine jamais."""
    appels = itertools.count()

    def un_seul_nom(self):
        if next(appels) > 100 * ESSAIS_NOM_UNIQUE:
            raise AssertionError("tirages de noms sans fin : la boucle d'unicité ne s'arrête pas")
        return "Martin"

    monkeypatch.setattr(Faker, "last_name", un_seul_nom, raising=False)
    rapport, _ = generer_dans(base_vierge, "petit")
    with session_base(base_vierge) as session:
        enseignants = session.scalars(select(m.Enseignant)).all()
    assert len({e.identifiant for e in enseignants}) == len(enseignants) > 2
    assert {e.nom for e in enseignants if not e.fictif} == {"Martin"}
    assert rapport.besoins_ecartes == ()


# --------------------------------------------------------------------------- #
# Robustesse sur plusieurs graines et durée
# --------------------------------------------------------------------------- #


def test_graine_difficile_du_moyen_sans_besoin_ecarte(tmp_path):
    """Cas : graine 13 du jeu moyen, où un premier placement glouton laisse deux TP sans place.
    Risque : jeu incomplet selon la graine, ou relance du témoin non déterministe."""
    rapport, empreinte = generer_dans(creer_base(tmp_path / "a"), "moyen", 13)
    _, empreinte_bis = generer_dans(creer_base(tmp_path / "b"), "moyen", 13)
    assert rapport.besoins_ecartes == ()
    assert empreinte == empreinte_bis


def _controler_plusieurs_graines(tmp_path, taille, graines):
    for graine in graines:
        url = creer_base(tmp_path / f"{taille}-{graine}")
        rapport, _ = generer_dans(url, taille, graine)
        assert rapport.besoins_ecartes == (), (graine, rapport.besoins_ecartes[:3])
        assert problemes_du_temoin(url, rapport) == [], graine
        with session_base(url) as session:
            annee_id = session.scalar(select(m.AnneeScolaire.id))
            for detail in lister(session, annee_id):
                assert detail.phrase[0].isupper() and detail.phrase.endswith("."), detail.phrase


def test_temoin_et_regles_valides_sur_trois_graines_du_petit(tmp_path):
    """Cas : autres graines que 0 (enseignants, vacataires et règles d'exemple tirés autrement).
    Risque : règle d'exemple invalide ou témoin faux pour une graine que personne n'essaie."""
    _controler_plusieurs_graines(tmp_path, "petit", (1, 2, 3))


@pytest.mark.perf
@pytest.mark.parametrize("taille", ["petit", "moyen", "grand"])
def test_temoin_et_regles_valides_sur_dix_graines(tmp_path, taille):
    """Cas : dix graines par taille (10 à 19). Risque : besoin écarté, témoin faux ou règle
    d'exemple invalide pour une graine donnée (le jeu de démonstration se recharge souvent)."""
    _controler_plusieurs_graines(tmp_path, taille, range(10, 20))


def test_generation_bornee_en_temps(jeu_petit, jeu_moyen):
    """Cas : chargement d'un jeu depuis l'écran (la requête attend la fin de la génération).
    Risque : génération qui s'emballe et bloque l'interface (moins d'une seconde d'habitude)."""
    assert jeu_petit.rapport.duree_s < 10
    assert jeu_moyen.rapport.duree_s < 20
