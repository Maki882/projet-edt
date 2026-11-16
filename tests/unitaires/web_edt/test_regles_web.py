"""Page Générer : anomalie sans règle citée, résumés des sections, préchauffage des écrans."""

from __future__ import annotations

import logging
import threading

import pytest
from tests.outils.web_edt.aides import DELAI

from edt.config import charger_configuration
from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import Gravite
from edt.domaine.parametres import DEJEUNER_AUCUN, DEJEUNER_FIXE, DEJEUNER_LIBRE
from edt.services.emploi_du_temps import calculs, prechauffage, presentation
from edt.services.emploi_du_temps.regles_personnalisees import BlocRegles, RegleDuCalcul
from edt.verification.contrat import Anomalie

OPTIONS = calculs.OptionsFormulaire(optimiser=True, temps_court_s=300, temps_long_s=900)
FIXE = calculs.ChoixDejeuner(DEJEUNER_FIXE, "12:45", "13:45", "60")


def _regle(identifiant: int, niveau: Niveau, ignoree: bool = False) -> RegleDuCalcul:
    return RegleDuCalcul(
        id=identifiant,
        code=f"R00{identifiant}",
        phrase="Phrase.",
        type_libelle="Type",
        portee="Classe A1",
        periode="S1",
        niveau=niveau,
        niveau_regle=Niveau.IMPORTANT,
        ignoree=ignoree,
    )


def _resumes(niveaux=None, bloc=BlocRegles(), dejeuner=FIXE, **choix):
    choix = {"duree": "court", "optimiser": True, "autre_solution": False, **choix}
    return calculs.resumer_reglages(
        calculs.regles_proposees(niveaux), bloc, dejeuner, OPTIONS, choix
    )


def test_anomalie_sans_regle_citee_reste_telle_quelle():
    simple = Anomalie(CodeContrainte.HORAIRES, Gravite.ERREUR, "Hors grille.")
    assert presentation.regle_citee(simple) is None
    sans_code = Anomalie(CodeContrainte.REGLE, Gravite.ERREUR, "Écart.", regle_id=4)
    citee = presentation.regle_citee(sans_code)
    assert citee.code == "règle n° 4" and citee.lien is None


def test_resumes_par_defaut():
    resumes = _resumes()
    assert resumes.regles == (
        "13 règles réglables, toutes à leur niveau par défaut ; 6 toujours nécessaires"
    )
    assert resumes.regles_personnalisees == "Aucune règle personnalisée active"
    assert resumes.dejeuner == "Horaires déterminés, de 12 h 45 à 13 h 45"
    assert resumes.duree_options == (
        "Recherche jusqu'à 5 minutes ; emploi du temps plus confortable"
    )
    assert all("(par défaut)" not in texte for texte in vars(resumes).values())


def test_resumes_niveaux_changes_et_regles_ignorees():
    niveaux = {CodeContrainte.PAUSE: Niveau.FACULTATIF}
    bloc = BlocRegles(
        regles=(
            _regle(1, Niveau.IMPORTANT),
            _regle(2, Niveau.IMPORTANT, ignoree=True),
            _regle(3, Niveau.FACULTATIF),
        )
    )
    resumes = _resumes(niveaux, bloc, duree="long", optimiser=False, autre_solution=True)
    assert resumes.regles == (
        "13 règles réglables, dont 1 à un autre niveau que par défaut "
        "(Pause entre deux séances : facultatif) ; 6 toujours nécessaires"
    )
    assert resumes.regles_personnalisees == (
        "3 règles actives : 1 ignorée pour ce calcul, 1 à un autre niveau que le sien"
    )
    assert resumes.duree_options == (
        "Recherche jusqu'à 15 minutes ; sans recherche de confort ; "
        "autre solution que la précédente"
    )
    assert _resumes(bloc=BlocRegles(erreur="illisible")).regles_personnalisees == (
        "Règles personnalisées illisibles (voir ci-dessous)"
    )


def test_resumes_de_la_pause_dejeuner():
    libre = calculs.ChoixDejeuner(DEJEUNER_LIBRE, "12:00", "14:00", "60")
    assert _resumes(dejeuner=libre).dejeuner == (
        "Pause libre bornée : au moins 60 min entre 12 h 00 et 14 h 00"
    )
    aucun = calculs.ChoixDejeuner(DEJEUNER_AUCUN, "", "", "")
    assert _resumes(dejeuner=aucun).dejeuner == "Pas de pause"
    # Une heure illisible, réaffichée après un refus, est recopiée telle quelle.
    illisible = calculs.ChoixDejeuner(DEJEUNER_FIXE, "25:00", "13:45", "60")
    assert _resumes(dejeuner=illisible).dejeuner == "Horaires déterminés, de 25:00 à 13 h 45"


def _prechauffer_jusqu_au_bout(monkeypatch, caplog) -> list[bool]:
    """Deux demandes rapprochées de préchauffage, puis attente de la fin du fil."""
    monkeypatch.setattr(prechauffage, "ACTIF", True)
    lances = [prechauffage.lancer(charger_configuration().db_url) for _ in range(2)]
    for fil in threading.enumerate():
        if fil.name == "prechauffage-ecrans":
            fil.join(DELAI)
    assert not prechauffage._en_cours
    erreurs = [r for r in caplog.records if r.levelno >= logging.WARNING or r.exc_info]
    assert not erreurs, [r.getMessage() for r in erreurs]
    return lances


@pytest.mark.cas_limite
@pytest.mark.parametrize("etat_de_la_base", ["non initialisée", "vide"])
def test_prechauffage_sur_toute_base(request, environnement, monkeypatch, caplog, etat_de_la_base):
    """Cas : préchauffage demandé deux fois de suite au démarrage du serveur ou après un
    changement de données, sur une base non initialisée ou initialisée sans données (le cas
    d'une version active est couvert après un calcul, côté intégration).
    Risque : trace « no such table » au journal à chaque démarrage sur une base neuve, erreur
    sur une base sans version active, ou deux fils qui font le même travail en même temps.
    """
    if etat_de_la_base == "vide":
        request.getfixturevalue("base")
    caplog.clear()
    with caplog.at_level(logging.INFO):
        lances = _prechauffer_jusqu_au_bout(monkeypatch, caplog)
    assert lances[0]  # la seconde, si le premier fil tourne encore, est ignorée sans erreur
    attendu = "sans objet" if etat_de_la_base == "non initialisée" else "Écrans préchauffés"
    assert any(attendu in r.getMessage() for r in caplog.records)
