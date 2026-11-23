"""Service du diagnostic d'un calcul impossible : garde-fous, enregistrement, relecture, message.

Base SQLite temporaire du jeu minimal rendu impossible (`bloquer_un_enseignant` : l'enseignante
d'anglais absente tous les jours), avec un calcul « impossible » inscrit tel quel, sans lancer le
moteur (`calcul_impossible`). Le diagnostic tourne pour de vrai sur cette base. Un cas lourd
(`rendre_impossible`, 60 séances par semaine) vérifie l'arrêt au budget. Les refus pendant un calcul ou un autre diagnostic
passent par `lancer_calcul` : ils sont dans `tests/integration/solveur/test_diagnostic.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import threading
import time

import pytest
from sqlalchemy import delete, func, select
from tests.outils.solveur.en_base import (
    MOTIF_BLOCAGE,
    bloquer_un_enseignant,
    calcul_fictif,
    calcul_impossible,
    rendre_impossible,
)

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services import diagnostic as service
from edt.services.diagnostic import (
    AVERTISSEMENT_DONNEES_ACTUELLES,
    BUDGET_MAX_S,
    BUDGET_MIN_S,
    Diagnostic,
    ElementEnCause,
    StatutDiagnostic,
    diagnostiquer_calcul,
    lire_diagnostic,
)

BUDGET_S = BUDGET_MIN_S
"""Le plus petit budget permis : le jeu minimal se diagnostique bien en deçà."""


def _calcul(calcul_id: int) -> dict:
    """Colonnes du calcul, lues dans une session neuve."""
    with session_base() as session:
        calcul = session.get(m.Calcul, calcul_id)
        return {
            "etat": calcul.etat,
            "message": calcul.message,
            "parametres": dict(calcul.parametres),
            "statistiques": dict(calcul.statistiques),
            "termine_le": calcul.termine_le,
            "duree_s": calcul.duree_s,
        }


def _compte(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def _blocages() -> set[int]:
    """Identifiants des indisponibilités posées par `bloquer_un_enseignant`."""
    with session_base() as session:
        return set(
            session.scalars(
                select(m.Indisponibilite.id).where(m.Indisponibilite.motif == MOTIF_BLOCAGE)
            )
        )


def _calcul_bloque(jeu_minimal) -> tuple[int, int]:
    """Semestre rendu impossible et calcul « impossible » inscrit : (calcul, besoin en cause)."""
    besoin_id = bloquer_un_enseignant(jeu_minimal.semestre_id)
    return calcul_impossible(jeu_minimal.semestre_id), besoin_id


# --------------------------------------------------------------------------- #
# Diagnostic réel : résultat, enregistrement, rien d'autre ne change
# --------------------------------------------------------------------------- #


def test_diagnostic_designe_le_besoin_en_cause_et_n_ecrit_que_sa_cle(jeu_minimal):
    calcul_id, besoin_id = _calcul_bloque(jeu_minimal)
    blocages = _blocages()
    avant = _calcul(calcul_id)
    nb_versions, nb_calculs, nb_regles = _compte(m.VersionEdt), _compte(m.Calcul), _compte(m.Regle)

    diagnostic = diagnostiquer_calcul(calcul_id, BUDGET_S)

    assert isinstance(diagnostic, Diagnostic)
    assert diagnostic.calcul_id == calcul_id
    assert diagnostic.statut is StatutDiagnostic.MINIMAL, diagnostic.message
    assert ("besoin", str(besoin_id)) in {(e.nature, e.identifiant) for e in diagnostic.donnees}
    # Ensemble irréductible : seules les absences posées pour le test, jamais une indisponibilité
    # sans rapport (celles des autres enseignants, ni celle du vendredi après-midi, redondante).
    indisponibilites = {
        int(e.identifiant) for e in diagnostic.donnees if e.nature == "indisponibilite"
    }
    assert indisponibilites and indisponibilites <= blocages
    assert all(e.nature in ("contrainte", "regle") for e in diagnostic.regles)
    assert all(e.nature not in ("contrainte", "regle") for e in diagnostic.donnees)
    assert all(e.libelle for e in diagnostic.regles + diagnostic.donnees)
    assert diagnostic.budget_s == BUDGET_S
    assert 0 < diagnostic.duree_s <= BUDGET_S
    assert diagnostic.nb_essais >= 2
    dt.datetime.fromisoformat(diagnostic.lance_le)

    apres = _calcul(calcul_id)
    # Seule la clé « diagnostic » s'ajoute aux statistiques ; le reste du calcul est intact.
    assert apres["statistiques"].pop("diagnostic") == diagnostic.vers_dict()
    assert apres == avant
    # Aucune version, aucun calcul, aucune règle créés ; la donnée en cause n'est pas corrigée.
    assert (_compte(m.VersionEdt), _compte(m.Calcul), _compte(m.Regle)) == (
        nb_versions,
        nb_calculs,
        nb_regles,
    )
    assert _blocages() == blocages
    assert lire_diagnostic(calcul_id) == diagnostic


def test_message_rappelle_que_les_donnees_sont_les_donnees_actuelles(jeu_minimal):
    calcul_id, _ = _calcul_bloque(jeu_minimal)

    diagnostic = diagnostiquer_calcul(calcul_id, BUDGET_S)

    assert AVERTISSEMENT_DONNEES_ACTUELLES
    # Décision D5 : le résumé, puis l'avertissement, toujours en dernier ; la phrase des règles
    # qui définissent le problème est écrite par la page et la commande, pas répétée ici.
    assert diagnostic.message.endswith(AVERTISSEMENT_DONNEES_ACTUELLES)
    assert diagnostic.message != AVERTISSEMENT_DONNEES_ACTUELLES
    assert "définissent le problème" not in diagnostic.message


def test_second_diagnostic_remplace_le_premier(jeu_minimal):
    calcul_id, _ = _calcul_bloque(jeu_minimal)
    premier = diagnostiquer_calcul(calcul_id, BUDGET_S)

    second = diagnostiquer_calcul(calcul_id, BUDGET_S + 1)

    assert second.budget_s == BUDGET_S + 1 != premier.budget_s
    stockes = _calcul(calcul_id)["statistiques"]
    assert stockes["diagnostic"] == second.vers_dict()
    assert stockes["temoin"] == "garde"
    assert lire_diagnostic(calcul_id) == second


@pytest.mark.cas_limite
def test_donnees_corrigees_depuis_le_calcul_donnent_possible(jeu_minimal):
    """Cas : la donnée fautive est corrigée après le calcul impossible, puis le diagnostic est lancé.

    Risque : un diagnostic qui relirait les données du moment du calcul, ou qui inventerait un
    ensemble, désignerait des causes qui n'existent plus ; il doit dire que le calcul n'est plus
    impossible, avec les données actuelles.
    """
    calcul_id, _ = _calcul_bloque(jeu_minimal)
    with session_base() as session:
        session.execute(delete(m.Indisponibilite).where(m.Indisponibilite.motif == MOTIF_BLOCAGE))

    diagnostic = diagnostiquer_calcul(calcul_id, BUDGET_S)

    assert diagnostic.statut is StatutDiagnostic.POSSIBLE
    assert diagnostic.regles == () and diagnostic.donnees == ()
    assert AVERTISSEMENT_DONNEES_ACTUELLES in diagnostic.message
    assert lire_diagnostic(calcul_id) == diagnostic


# --------------------------------------------------------------------------- #
# Garde-fous
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("calcul_id", [999_999, 0, -1, 2**63])
def test_calcul_introuvable(jeu_minimal, calcul_id):
    """Cas : identifiant de calcul absent, nul, négatif ou hors des entiers de la base.

    Risque : une erreur technique (dépassement d'entier SQLite) ou une page blanche au lieu d'un
    message qui dit que le calcul n'existe pas.
    """
    with pytest.raises(ErreurIntrouvable) as erreur:
        diagnostiquer_calcul(calcul_id, BUDGET_S)
    assert erreur.value.message


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "etat",
    [EtatCalcul.REUSSI, EtatCalcul.TEMPS_DEPASSE, EtatCalcul.ARRETE, EtatCalcul.ERREUR],
)
def test_calcul_qui_n_est_pas_impossible_refuse(jeu_minimal, etat):
    """Cas : diagnostic demandé sur un calcul réussi, en temps dépassé, arrêté ou en erreur.

    Risque : un « temps dépassé » n'a pas de preuve d'impossibilité ; le diagnostiquer
    présenterait comme causes des éléments qui n'en sont pas.
    """
    calcul_id = calcul_fictif(
        jeu_minimal.semestre_id, etat=etat, termine_le=dt.datetime.now(), statistiques={}
    )

    with pytest.raises(ErreurRegleMetier) as erreur:
        diagnostiquer_calcul(calcul_id, BUDGET_S)

    assert erreur.value.message
    assert "diagnostic" not in _calcul(calcul_id)["statistiques"]


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "budget", [BUDGET_MIN_S - 0.01, BUDGET_MAX_S + 0.01, 0, -30, math.nan, math.inf, "trente"]
)
def test_budget_hors_bornes_refuse_sans_rien_ecrire(jeu_minimal, budget):
    """Cas : budget sous 5 s, au-dessus de 600 s, nul, négatif, NaN, infini ou non numérique.

    Risque : un budget infini ou énorme bloquerait la page ou le serveur ; un budget trop court
    donnerait toujours « sans conclusion » sans le dire.
    """
    calcul_id = calcul_impossible(jeu_minimal.semestre_id)

    with pytest.raises(ErreurDonneesInvalides) as erreur:
        diagnostiquer_calcul(calcul_id, budget)

    assert erreur.value.message
    assert "diagnostic" not in _calcul(calcul_id)["statistiques"]
    assert service.diagnostic_en_cours() is None


@pytest.mark.cas_limite
def test_budget_aux_bornes_accepte(jeu_minimal):
    """Cas : budget exactement à 5 s puis à 600 s.

    Risque : des bornes exclues par erreur refuseraient le réglage le plus court ou le plus long
    que la commande propose.
    """
    calcul_id, _ = _calcul_bloque(jeu_minimal)

    assert diagnostiquer_calcul(calcul_id, BUDGET_MIN_S).budget_s == BUDGET_MIN_S
    assert diagnostiquer_calcul(calcul_id, BUDGET_MAX_S).budget_s == BUDGET_MAX_S


def test_aucun_diagnostic_en_cours_hors_diagnostic(jeu_minimal):
    assert service.diagnostic_en_cours() is None
    diagnostiquer_calcul(_calcul_bloque(jeu_minimal)[0], BUDGET_S)
    assert service.diagnostic_en_cours() is None


@pytest.mark.cas_limite
@pytest.mark.perf  # deux diagnostics menés jusqu'au budget minimal de 5 s
def test_diagnostic_arrete_au_budget_ne_laisse_aucun_fil(jeu_minimal):
    """Cas : un essai (sans la grille horaire, 60 séances d'un groupe) construit un modèle énorme ;
    le budget de 5 s est atteint en pleine construction.

    Risque : un diagnostic qui rend la main au budget en laissant tourner son dernier essai en
    arrière-plan occupe encore les processeurs pendant le calcul ou le diagnostic suivant, qui
    dépasse alors son propre budget (décision D4).
    """
    rendre_impossible(jeu_minimal.semestre_id)
    calcul_id = calcul_impossible(jeu_minimal.semestre_id)
    fils_avant = set(threading.enumerate())
    debut = time.perf_counter()

    diagnostic = diagnostiquer_calcul(calcul_id, BUDGET_MIN_S)

    ecoule = time.perf_counter() - debut
    assert service.diagnostic_en_cours() is None
    assert diagnostic.statut in (StatutDiagnostic.PARTIEL, StatutDiagnostic.MINIMAL)
    assert diagnostic.duree_s <= BUDGET_MIN_S
    assert ecoule <= BUDGET_MIN_S + 1.0, f"rendu en {ecoule:.1f} s pour un budget de 5 s"
    restes = [fil.name for fil in set(threading.enumerate()) - fils_avant if fil.is_alive()]
    assert not restes, f"fils encore en route après le diagnostic : {restes}"
    # Rien ne reste en route : un second diagnostic est accepté aussitôt.
    assert diagnostiquer_calcul(calcul_id, BUDGET_MIN_S).calcul_id == calcul_id
    assert service.diagnostic_en_cours() is None


# --------------------------------------------------------------------------- #
# Relecture
# --------------------------------------------------------------------------- #


def test_lire_diagnostic_sans_diagnostic_rend_none(jeu_minimal):
    assert lire_diagnostic(calcul_impossible(jeu_minimal.semestre_id)) is None


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "entree",
    [
        "texte libre",
        42,
        [1, 2],
        {},
        {"statut": "inconnu"},
        {"calcul_id": "x", "statut": "minimal", "regles": "pas une liste"},
    ],
)
def test_lire_diagnostic_illisible_rend_none_et_journalise(jeu_minimal, caplog, entree):
    """Cas : entrée « diagnostic » des statistiques abîmée (texte, nombre, liste, clés manquantes).

    Risque : une exception à l'affichage de la page du calcul, qui deviendrait inaccessible ; ou
    une entrée abîmée ignorée sans trace dans le journal.
    """
    calcul_id = calcul_impossible(
        jeu_minimal.semestre_id, statistiques={"temoin": "garde", "diagnostic": entree}
    )

    with caplog.at_level(logging.WARNING):
        assert lire_diagnostic(calcul_id) is None

    assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_vers_dict_et_depuis_dict_font_l_aller_retour():
    diagnostic = Diagnostic(
        calcul_id=7,
        statut=StatutDiagnostic.PARTIEL,
        regles=(
            ElementEnCause("contrainte", "DISPO_ENSEIGNANT", "Disponibilité des enseignants"),
            ElementEnCause("regle", "3", "Règle R003 : la classe 1 ne travaille que le mardi"),
        ),
        donnees=(
            ElementEnCause("besoin", "12", "Besoin « TD Analyse - G1 » (2 séances par semaine)"),
            ElementEnCause("verrou", "12-1", "Séance verrouillée le lundi à 8 h 30"),
        ),
        message="Ensemble trouvé ; « guillemets » et accents conservés.",
        nb_essais=14,
        duree_s=8.25,
        budget_s=30.0,
        lance_le="2026-11-23T16:19:05",
    )

    texte = json.dumps(diagnostic.vers_dict())  # la colonne des statistiques est en JSON

    relu = Diagnostic.depuis_dict(json.loads(texte))
    assert relu == diagnostic
    assert isinstance(relu.statut, StatutDiagnostic)
    assert isinstance(relu.regles, tuple) and isinstance(relu.donnees, tuple)


@pytest.mark.cas_limite
def test_lire_diagnostic_d_un_calcul_introuvable_ne_leve_pas_d_erreur_technique(jeu_minimal):
    """Cas : relecture du diagnostic d'un calcul supprimé ou d'un identifiant inventé.

    Risque : une exception SQL brute au lieu de `None` ou d'une erreur métier lisible.
    """
    try:
        assert lire_diagnostic(999_999) is None
    except ErreurIntrouvable as erreur:
        assert erreur.message


def test_journal_du_diagnostic(jeu_minimal, caplog):
    calcul_id, _ = _calcul_bloque(jeu_minimal)

    with caplog.at_level(logging.INFO):
        diagnostiquer_calcul(calcul_id, BUDGET_S)

    # Le service laisse une trace qui nomme son calcul (niveau INFO ou au-dessus) ; le journal du
    # solveur, qui ne connaît que le semestre, ne compte pas.
    assert [
        r
        for r in caplog.records
        if r.name == "edt.services.diagnostic"
        and r.levelno >= logging.INFO
        and f"calcul {calcul_id}" in r.getMessage()
    ]


@pytest.mark.cas_limite
def test_journal_du_diagnostic_sans_alerte_ni_retrait_attribue_a_l_utilisateur(jeu_minimal, caplog):
    """Cas : diagnostic d'un calcul impossible ; ses essais retirent des règles et des données, et
    plusieurs sont refusés par les vérifications préalables.

    Risque : la commande et le journal affichent des dizaines d'alertes du moteur pour des essais
    voulus, dont « Contraintes désactivées à la demande de l'utilisateur », ce qui est faux : c'est
    le diagnostic qui retire les règles (tâche T8). Ces lignes peuvent rester au niveau DEBUG.
    """
    calcul_id, _ = _calcul_bloque(jeu_minimal)
    caplog.set_level(logging.DEBUG)

    diagnostic = diagnostiquer_calcul(calcul_id, BUDGET_S)

    assert diagnostic.nb_essais > 1  # des essais ont bien retiré des éléments
    alertes = [
        f"{r.name} {r.levelname} : {r.getMessage()}"
        for r in caplog.records
        if r.levelno >= logging.WARNING
    ]
    assert not alertes, "\n".join(alertes)
    # Le niveau DEBUG reste permis : il ne s'affiche qu'en mode de débogage voulu.
    attribues = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.INFO and "à la demande de l'utilisateur" in r.getMessage()
    ]
    assert not attribues, "\n".join(attribues)
