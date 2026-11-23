"""Diagnostic d'un calcul impossible face aux calculs, et commande « diagnostiquer ».

Base SQLite temporaire du jeu minimal rendu impossible (`semestre_bloque`). Les refus croisés
(un calcul pendant un diagnostic, un diagnostic pendant un calcul ou un autre diagnostic) passent
par `lancer_calcul` ; la commande passe par `CliRunner`. Les fonctions du service seules sont
dans `tests/unitaires/solveur/test_diagnostic_service.py`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select
from tests.outils.solveur.en_base import (
    OPTIONS,
    bloquer_un_enseignant,
    calcul_fictif,
    calcul_impossible,
)

from edt import cli, solveur
from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul
from edt.erreurs import ErreurCalculEnCours
from edt.services import diagnostic as service
from edt.services import generation
from edt.services.diagnostic import (
    AVERTISSEMENT_DONNEES_ACTUELLES,
    BUDGET_MIN_S,
    StatutDiagnostic,
    diagnostiquer_calcul,
    lire_diagnostic,
)
from edt.solveur import ResultatSolveur, StatutSolveur

DELAI = 20
"""Garde-fou (secondes) : un test bloqué échoue au lieu d'attendre sans fin."""

LIBELLES_STATUT = {
    StatutDiagnostic.MINIMAL: "Ensemble minimal trouvé",
    StatutDiagnostic.PARTIEL: "Ensemble trouvé, peut-être pas le plus petit",
    StatutDiagnostic.POSSIBLE: "Le calcul n'est plus impossible",
    StatutDiagnostic.INDETERMINE: "Diagnostic sans conclusion",
}


@pytest.fixture
def semestre_bloque(jeu_minimal) -> int:
    """Semestre du jeu minimal rendu impossible : l'enseignante d'anglais absente tous les jours."""
    bloquer_un_enseignant(jeu_minimal.semestre_id)
    return jeu_minimal.semestre_id


def _nb_calculs() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(m.Calcul))


@contextmanager
def _diagnostic_bloque(calcul_id: int, monkeypatch) -> Iterator[threading.Thread]:
    """Un diagnostic en cours sur `calcul_id`, retenu dans sa recherche jusqu'à la sortie.

    La fonction du solveur appelée par le service attend un événement : le diagnostic reste en
    cours sans dépendre d'une durée. À la sortie, il est libéré et attendu.
    """
    commence, libere = threading.Event(), threading.Event()
    vraie = service.isoler_impossibilite
    erreurs: list[BaseException] = []

    def isoler_retenue(instantane, options, budget_s, **kwargs):
        commence.set()
        libere.wait(DELAI)
        return vraie(instantane, options, budget_s, **kwargs)

    def executer():
        try:
            diagnostiquer_calcul(calcul_id, BUDGET_MIN_S)
        except BaseException as erreur:  # rapportée au test, jamais avalée
            erreurs.append(erreur)

    monkeypatch.setattr(service, "isoler_impossibilite", isoler_retenue)
    fil = threading.Thread(target=executer, name=f"diagnostic-{calcul_id}", daemon=True)
    fil.start()
    try:
        assert commence.wait(DELAI), "le diagnostic n'a pas commencé"
        yield fil
    finally:
        libere.set()
        fil.join(DELAI)
    assert not fil.is_alive(), "le diagnostic ne s'est pas terminé"
    assert not erreurs, erreurs


# --------------------------------------------------------------------------- #
# Un seul calcul ou diagnostic à la fois
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_calcul_et_second_diagnostic_refuses_pendant_un_diagnostic(semestre_bloque, monkeypatch):
    """Cas : un calcul, puis un second diagnostic, demandés pendant qu'un diagnostic tourne.

    Risque : le calcul ou le second diagnostic se partageraient les processeurs avec le premier,
    qui dépasserait son budget ; le message doit dire quel calcul est diagnostiqué et quoi faire.
    """
    calcul_id = calcul_impossible(semestre_bloque)
    autre_id = calcul_impossible(semestre_bloque)
    nb_calculs = _nb_calculs()

    with _diagnostic_bloque(calcul_id, monkeypatch):
        assert service.diagnostic_en_cours() == calcul_id

        with pytest.raises(ErreurCalculEnCours) as refus_calcul:
            generation.lancer_calcul(semestre_bloque, OPTIONS)
        with pytest.raises(ErreurCalculEnCours) as refus_diagnostic:
            diagnostiquer_calcul(autre_id, BUDGET_MIN_S)

        assert _nb_calculs() == nb_calculs  # aucun calcul inscrit
    for refus in (refus_calcul, refus_diagnostic):
        assert f"n° {calcul_id}" in refus.value.message
        assert "relancer" in refus.value.message.lower()

    assert service.diagnostic_en_cours() is None
    assert lire_diagnostic(calcul_id) is not None
    assert lire_diagnostic(autre_id) is None
    # Le diagnostic fini, un calcul se lance de nouveau.
    calcul = generation.lancer_calcul(semestre_bloque, OPTIONS)
    assert generation.attendre_calcul(calcul, DELAI).etat.est_termine


@pytest.mark.cas_limite
def test_diagnostic_refuse_pendant_un_calcul(semestre_bloque, monkeypatch):
    """Cas : diagnostic demandé pendant qu'un calcul tourne.

    Risque : le diagnostic lirait des données en cours de remplacement et ralentirait le calcul.
    """
    calcul_id = calcul_impossible(semestre_bloque)
    demarre = threading.Event()

    def moteur_lent(instantane, options, arret=None):
        demarre.set()
        arret.wait(DELAI)
        return ResultatSolveur(StatutSolveur.ARRETE)

    monkeypatch.setattr(solveur, "resoudre", moteur_lent)
    en_cours = generation.lancer_calcul(semestre_bloque, OPTIONS)
    try:
        assert demarre.wait(DELAI), "le calcul n'a pas démarré"
        with pytest.raises(ErreurCalculEnCours) as refus:
            diagnostiquer_calcul(calcul_id, BUDGET_MIN_S)
        assert refus.value.message
        assert service.diagnostic_en_cours() is None
    finally:
        generation.demander_arret(en_cours)
        assert generation.attendre_calcul(en_cours, DELAI).etat.est_termine
    assert lire_diagnostic(calcul_id) is None


def test_diagnostic_d_un_calcul_refuse_par_les_verifications_prealables(semestre_bloque):
    """Le vrai calcul du semestre rendu impossible se termine « impossible » ; il se diagnostique."""
    refuse = generation.attendre_calcul(generation.lancer_calcul(semestre_bloque, OPTIONS), DELAI)
    assert refuse.etat is EtatCalcul.INFAISABLE

    diagnostic = diagnostiquer_calcul(refuse.id, BUDGET_MIN_S)

    assert diagnostic.statut in (StatutDiagnostic.MINIMAL, StatutDiagnostic.PARTIEL)
    assert diagnostic.donnees
    assert lire_diagnostic(refuse.id) == diagnostic


# --------------------------------------------------------------------------- #
# Commande « python -m edt diagnostiquer »
# --------------------------------------------------------------------------- #


def test_commande_affiche_le_diagnostic(lanceur, semestre_bloque):
    calcul_id = calcul_impossible(semestre_bloque)

    resultat = lanceur.invoke(cli.app, ["diagnostiquer", str(calcul_id), "--temps", "5"])

    assert resultat.exit_code == 0, resultat.output
    diagnostic = lire_diagnostic(calcul_id)
    assert diagnostic is not None and diagnostic.budget_s == 5
    sortie = resultat.stdout
    assert f"Diagnostic du calcul n° {calcul_id} : {LIBELLES_STATUT[diagnostic.statut]}" in sortie
    assert "Données en cause :" in sortie and "Règles en cause :" in sortie
    assert all(f"- {e.libelle}" in sortie for e in diagnostic.regles + diagnostic.donnees)
    assert AVERTISSEMENT_DONNEES_ACTUELLES in sortie
    assert "Les règles qui définissent le problème sont toujours gardées" in sortie
    assert "essai" in sortie.splitlines()[-1]


def test_commande_sans_identifiant_diagnostique_le_dernier_calcul(lanceur, semestre_bloque):
    ancien = calcul_impossible(semestre_bloque)
    dernier = calcul_impossible(semestre_bloque)

    resultat = lanceur.invoke(cli.app, ["diagnostiquer", "--temps", "5"])

    assert resultat.exit_code == 0, resultat.output
    assert f"Diagnostic du calcul n° {dernier} :" in resultat.stdout
    assert lire_diagnostic(dernier) is not None
    assert lire_diagnostic(ancien) is None


@pytest.mark.cas_limite
def test_commande_sans_aucun_calcul(lanceur, jeu_minimal):
    """Cas : `diagnostiquer` sans identifiant alors qu'aucun calcul n'a été lancé.

    Risque : trace Python ou code 0 ; il faut le code des erreurs métier et dire quoi faire.
    """
    resultat = lanceur.invoke(cli.app, ["diagnostiquer"])

    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "Aucun calcul n'a encore été lancé" in resultat.stderr
    assert "Traceback" not in resultat.output


@pytest.mark.cas_limite
def test_commande_sur_un_dernier_calcul_reussi(lanceur, jeu_minimal):
    """Cas : le dernier calcul a réussi ; `diagnostiquer` sans identifiant.

    Risque : un diagnostic lancé sur un calcul qui n'est pas impossible, ou un code de sortie 0.
    """
    reussi = calcul_fictif(jeu_minimal.semestre_id, etat=EtatCalcul.REUSSI, statistiques={})

    resultat = lanceur.invoke(cli.app, ["diagnostiquer", "--temps", "5"])

    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "Erreur :" in resultat.stderr
    assert lire_diagnostic(reussi) is None


@pytest.mark.cas_limite
@pytest.mark.parametrize("temps", ["4.9", "600.5", "0", "-5", "nan", "inf"])
def test_commande_temps_hors_bornes(lanceur, semestre_bloque, temps):
    """Cas : `--temps` sous 5 s, au-dessus de 600 s, nul, négatif, NaN ou infini.

    Risque : un diagnostic sans borne de temps qui bloque le terminal, ou une trace Python.
    """
    calcul_id = calcul_impossible(semestre_bloque)

    resultat = lanceur.invoke(cli.app, ["diagnostiquer", str(calcul_id), "--temps", temps])

    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "Erreur :" in resultat.stderr and "Traceback" not in resultat.output
    assert lire_diagnostic(calcul_id) is None


@pytest.mark.cas_limite
def test_commande_temps_non_numerique(lanceur, semestre_bloque):
    """Cas : `--temps trente`.

    Risque : une trace Python au lieu de l'erreur d'usage de la ligne de commande.
    """
    calcul_id = calcul_impossible(semestre_bloque)

    resultat = lanceur.invoke(cli.app, ["diagnostiquer", str(calcul_id), "--temps", "trente"])

    assert resultat.exit_code == 2, resultat.output
    assert "Traceback" not in resultat.output
    assert lire_diagnostic(calcul_id) is None


@pytest.mark.cas_limite
@pytest.mark.parametrize("calcul_id", ["999999", "0"])
def test_commande_calcul_introuvable(lanceur, jeu_minimal, calcul_id):
    """Cas : identifiant de calcul inexistant ou nul.

    Risque : trace Python ou code 0 au lieu du code des erreurs métier.
    """
    resultat = lanceur.invoke(cli.app, ["diagnostiquer", calcul_id, "--temps", "5"])

    assert resultat.exit_code == cli.CODE_ERREUR_METIER, resultat.output
    assert "Erreur :" in resultat.stderr and "Traceback" not in resultat.output
