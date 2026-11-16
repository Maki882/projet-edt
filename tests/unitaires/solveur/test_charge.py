"""Essais de charge (marqués `perf`) : toujours avec deux fils de calcul, comme le serveur.

- instantanés générés (`tests/solveur/generateur.py`) : 200 séances en moins
  de 30 s, 800 séances en moins de 120 s ;
- jeux fictifs de `edt.synthese` : `moyen` en moins de 30 s, `grand` en moins
  de 120 s, pour les deux semestres ; le mode optimisé fait mieux que
  l'emploi du temps témoin du jeu.

Chaque solution passe par l'oracle. Lancer avec `pytest tests/solveur -m perf`.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterator

import pytest
from tests.outils.solveur.fabrique import (
    niveaux_regles_posables,
    options,
    verifier_dates,
    verifier_solution,
)
from tests.outils.solveur.generateur import generer

from edt.db.initialisation import initialiser_base
from edt.db.moteur import liberer_moteurs, session_base
from edt.domaine.instantane import Instantane, Placement
from edt.services.instantane import charger_instantane
from edt.solveur import resoudre
from edt.solveur.instanciation import instancier
from edt.solveur.replacement import OptionsReplacement, replacer
from edt.synthese.generateur import generer_jeu
from edt.verification import calculer_indicateurs

pytestmark = pytest.mark.perf


def deux_fils(instantane: Instantane, **changements):
    """Deux fils de calcul, 120 s, et les règles personnalisées que le moteur sait poser."""
    reglages = {"nb_workers": 2, "temps_limite_s": 120}
    reglages.update(changements)
    return options(niveaux_regles=niveaux_regles_posables(instantane), **reglages)


Jeu = dict[str, tuple[Instantane, tuple[Placement, ...]]]
"""Par code de semestre : l'instantané et l'emploi du temps témoin du jeu."""


@pytest.mark.parametrize(("nb_seances", "limite_s"), [(200, 30), (800, 120)])
def test_instantane_genere(nb_seances: int, limite_s: int):
    instantane, _ = generer(nb_seances, graine=1)
    assert len(instantane.seances) >= 0.95 * nb_seances
    debut = time.perf_counter()
    resultat = resoudre(instantane, deux_fils(instantane))
    duree = time.perf_counter() - debut
    verifier_solution(instantane, resultat)
    assert duree < limite_s, f"{nb_seances} séances en {duree:.1f} s"


def _charger(dossier, taille: str) -> Jeu:
    """Génère le jeu dans une base temporaire et charge l'instantané de chaque semestre."""
    url = f"sqlite:///{(dossier / 'jeu.db').as_posix()}"
    initialiser_base(url=url, aujourdhui=dt.date(2026, 10, 5))
    with session_base(url) as session:
        rapport = generer_jeu(session, taille, 0)
        return {
            code: (charger_instantane(session, semestre_id), rapport.placements(code))
            for code, semestre_id in rapport.semestres.items()
        }


@pytest.fixture(scope="module")
def jeu_moyen(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Jeu]:
    yield _charger(tmp_path_factory.mktemp("moyen"), "moyen")
    liberer_moteurs()


@pytest.fixture(scope="module")
def jeu_grand(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Jeu]:
    yield _charger(tmp_path_factory.mktemp("grand"), "grand")
    liberer_moteurs()


def _resoudre_en_moins_de(instantane: Instantane, limite_s: int):
    debut = time.perf_counter()
    resultat = resoudre(instantane, deux_fils(instantane))
    duree = time.perf_counter() - debut
    verifier_solution(instantane, resultat)
    assert duree < limite_s, f"{len(instantane.seances)} séances en {duree:.1f} s"
    return resultat


@pytest.mark.parametrize("semestre", ["S1", "S2"])
def test_jeu_moyen(jeu_moyen: Jeu, semestre: str):
    _resoudre_en_moins_de(jeu_moyen[semestre][0], 30)


@pytest.mark.parametrize("semestre", ["S1", "S2"])
def test_jeu_grand(jeu_grand: Jeu, semestre: str):
    _resoudre_en_moins_de(jeu_grand[semestre][0], 120)


def test_jeu_moyen_optimise_fait_mieux_que_le_temoin(jeu_moyen: Jeu):
    instantane, temoin = jeu_moyen["S1"]
    resultat = resoudre(instantane, deux_fils(instantane, temps_limite_s=30, optimiser=True))
    verifier_solution(instantane, resultat)
    obtenu = calculer_indicateurs(instantane, resultat.placements)
    reference = calculer_indicateurs(instantane, temoin)
    assert obtenu.valeur_objectif < reference.valeur_objectif
    assert obtenu.trous_creneaux <= reference.trous_creneaux


@pytest.mark.parametrize("semestre", ["S1", "S2"])
def test_jeu_moyen_ameliore_puis_s_arrete_de_lui_meme(jeu_moyen: Jeu, semestre: str):
    """Réglages par défaut (300 s, arrêt sur stagnation) : un petit établissement rend vite."""
    instantane, temoin = jeu_moyen[semestre]
    debut = time.perf_counter()
    resultat = resoudre(
        instantane,
        deux_fils(instantane, temps_limite_s=300, optimiser=True, arret_sur_stagnation=True),
    )
    duree = time.perf_counter() - debut
    verifier_solution(instantane, resultat)
    assert resultat.statistiques["raison_arret"] in ("stagnation", "optimum_prouve")
    assert duree < 60, f"jeu moyen {semestre} en {duree:.1f} s"
    obtenu = calculer_indicateurs(instantane, resultat.placements)
    reference = calculer_indicateurs(instantane, temoin)
    assert obtenu.valeur_objectif < reference.valeur_objectif
    assert obtenu.trous_creneaux <= reference.trous_creneaux


def test_jeu_moyen_instanciation_et_replacement(jeu_moyen: Jeu):
    """Les jours fériés tombent sur des séances : elles sont replacées, rien n'est perdu."""
    instantane, _ = jeu_moyen["S1"]
    resultat = _resoudre_en_moins_de(instantane, 30)
    posables = niveaux_regles_posables(instantane)
    instancie = instancier(instantane, resultat.placements, niveaux_regles=posables)
    assert instancie.a_replacer, "le calendrier du jeu contient des jours fériés"
    debut = time.perf_counter()
    replace = replacer(
        instantane,
        instancie.occurrences,
        [revoir.occurrence for revoir in instancie.a_replacer],
        OptionsReplacement(niveaux_regles=posables),
    )
    assert time.perf_counter() - debut < 30
    assert len(replace.replacements) == len(instancie.a_replacer)
    assert len(replace.replacees) >= 0.9 * len(instancie.a_replacer)
    verifier_dates(
        instantane,
        [*instancie.occurrences, *(r.occurrence for r in replace.replacees)],
        niveaux_regles=posables,
    )


def test_jeu_moyen_graines_avec_un_seul_fil(jeu_moyen: Jeu):
    """« Proposer une autre solution » : une autre graine, une autre solution, même à un fil."""
    instantane, _ = jeu_moyen["S1"]
    solutions = []
    for graine in (1, 2, 3, 1):
        resultat = resoudre(
            instantane, deux_fils(instantane, graine=graine, nb_workers=1, temps_limite_s=60)
        )
        verifier_solution(instantane, resultat)
        solutions.append(resultat.placements)
    assert len(set(solutions[:3])) == 3
    assert solutions[0] == solutions[3]  # même graine, même solution
