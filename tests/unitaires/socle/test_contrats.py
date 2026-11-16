"""Contrats partagés : catalogue des contraintes, vérificateur, solveur, paramètres."""

from __future__ import annotations

from itertools import combinations

import pytest

from edt.db.modeles import Parametre
from edt.domaine.contraintes import (
    CATALOGUE,
    PREFERENCES,
    CodeContrainte,
    codes_actifs,
    codes_par_defaut,
    contrainte,
)
from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import decomposer_id_seance, id_seance
from edt.domaine.parametres import Parametres, convertir_texte, definition, definitions
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services.instantane import charger_instantane
from edt.services.parametres import ecrire_parametre, ecrire_parametres, lire_parametres
from edt.solveur import OptionsSolveur, StatutSolveur, resoudre

CODES_DES_DECISIONS = {
    "SALLE_UNIQUE",
    "ENSEIGNANT_UNIQUE",
    "GROUPE_UNIQUE",
    "CAMPUS_TRAJET",
    "ACCESSIBILITE",
    "HORAIRES",
    "JOURS",
    "PAUSE",
    "DEJEUNER",
    "CAPACITE",
    "TYPE_SALLE",
    "EQUIPEMENT",
    "DISPO_ENSEIGNANT",
    "DISPO_SALLE",
    "DISPO_GROUPE",
    "MAX_ENSEIGNANT",
    "VOLUME",
    "VERROU",
    "JOUR_FERME",
    "REGLE",
}
"""Codes du catalogue des contraintes : ils sont stables."""


def test_le_catalogue_reprend_exactement_les_codes_des_decisions():
    assert {code.value for code in CodeContrainte} == CODES_DES_DECISIONS
    assert set(CATALOGUE) == set(CodeContrainte)
    for code, entree in CATALOGUE.items():
        assert entree.code is code
        assert entree.libelle and entree.description
        assert entree.active_par_defaut


def test_activation_des_contraintes():
    assert codes_par_defaut() == frozenset(CodeContrainte)
    assert CodeContrainte.CAPACITE not in codes_actifs(["CAPACITE"])
    assert contrainte("PAUSE").libelle
    with pytest.raises(ErreurDonneesInvalides):
        codes_actifs(["INCONNUE"])
    with pytest.raises(ErreurDonneesInvalides):
        OptionsSolveur(contraintes_desactivees=frozenset({"INCONNUE"}))


def test_les_poids_des_preferences_sont_des_parametres():
    parametres = Parametres()
    poids = {p.code: getattr(parametres, p.cle_poids) for p in PREFERENCES}
    assert poids == {"TROUS": 10, "CHANGEMENT_CAMPUS": 5, "SAMEDI": 2, "CRENEAUX_EXTREMES": 1}


def test_identifiant_de_seance():
    assert id_seance(12, 3) == "12-3"
    assert decomposer_id_seance("12-3") == (12, 3)
    with pytest.raises(ErreurDonneesInvalides):
        decomposer_id_seance("douze")


def test_le_solveur_place_toutes_les_seances_sans_conflit_ni_salle_trop_petite(
    jeu_minimal, session
):
    instantane = charger_instantane(session, jeu_minimal.semestre_id)
    resultat = resoudre(instantane, OptionsSolveur())

    assert resultat.statut is StatutSolveur.SOLUTION
    assert {p.seance_id for p in resultat.placements} == {s.id for s in instantane.seances}
    for placement in resultat.placements:
        seance = instantane.seance(placement.seance_id)
        assert instantane.salle(placement.salle_id).capacite >= seance.besoin.effectif
        assert placement.fin - placement.debut == seance.duree

    for a, b in combinations(resultat.placements, 2):
        seance_a, seance_b = instantane.seance(a.seance_id), instantane.seance(b.seance_id)
        simultanees = (
            a.jour == b.jour
            and se_chevauchent(a.debut, a.fin, b.debut, b.fin)
            and not seance_a.semaines.isdisjoint(seance_b.semaines)
        )
        if not simultanees:
            continue
        assert a.salle_id != b.salle_id
        assert seance_a.besoin.enseignants.isdisjoint(seance_b.besoin.enseignants)
        assert not instantane.besoins_en_conflit_de_groupes(seance_a.besoin, seance_b.besoin)


def test_parametres_par_defaut_puis_modification(session):
    parametres = lire_parametres(session)
    assert parametres == Parametres()
    assert (parametres.temps_limite_s, parametres.pause_min) == (300, 15)
    assert (parametres.temps_limite_long_s, parametres.objectif_actif) == (900, True)
    assert "Relancer en cherchant jusqu'à" in definition("temps_limite_long_s").description
    assert (parametres.dejeuner_debut_min, parametres.dejeuner_fin_min) == (765, 825)
    assert parametres.accessibilite_inconnue_acceptee is False

    # Par défaut : un calcul de 5 minutes qui améliore, une relance longue de 15 minutes.
    par_defaut = OptionsSolveur.depuis_parametres(parametres)
    assert (par_defaut.temps_limite_s, par_defaut.optimiser) == (300.0, True)
    assert OptionsSolveur.depuis_parametres(parametres, relance_longue=True).temps_limite_s == 900.0

    ecrire_parametre(session, "temps_limite_s", 120)
    ecrire_parametre(session, "objectif_actif", False)
    session.commit()
    relu = lire_parametres(session)
    assert relu.temps_limite_s == 120 and relu.objectif_actif is False
    options = OptionsSolveur.depuis_parametres(relu)
    assert (options.temps_limite_s, options.optimiser) == (120.0, False)
    assert OptionsSolveur.depuis_parametres(relu, relance_longue=True).temps_limite_s == 900.0


def test_parametre_invalide_refuse_sans_rien_modifier(session):
    with pytest.raises(ErreurDonneesInvalides):
        ecrire_parametre(session, "temps_limite_s", 0)
    with pytest.raises(ErreurDonneesInvalides):
        ecrire_parametre(session, "pause_min", "quinze")
    with pytest.raises(ErreurIntrouvable):
        ecrire_parametre(session, "inconnu", 1)
    with pytest.raises(ErreurDonneesInvalides):  # incohérence d'ensemble
        ecrire_parametres(session, {"ouverture_min": 1100, "pause_min": 20})
    assert lire_parametres(session) == Parametres()


def test_reglages_du_calcul_bornes_pour_un_site_public(session):
    """Fils et temps limites : personne ne peut demander plus que la machine ne supporte."""
    maxima = {d.cle: d.maximum for d in definitions()}
    assert (maxima["nb_workers"], maxima["temps_limite_s"], maxima["temps_limite_long_s"]) == (
        8,
        900,
        1800,
    )
    for cle, trop in (("nb_workers", 9), ("temps_limite_s", 901), ("temps_limite_long_s", 1801)):
        with pytest.raises(ErreurDonneesInvalides):
            ecrire_parametre(session, cle, trop)
    assert lire_parametres(session) == Parametres()


def test_valeur_enregistree_au_dela_des_bornes_ramenee_a_la_borne(session, caplog):
    """Une valeur enregistrée hors des bornes actuelles reste lisible, sans plantage."""
    anciennes = {"nb_workers": 64, "temps_limite_s": 3600, "temps_limite_long_s": 7200}
    for cle, valeur in anciennes.items():
        session.get(Parametre, cle).valeur = valeur  # écrit sans validation (script, import)
    session.commit()

    with caplog.at_level("WARNING", logger="edt.services.parametres"):
        relu = lire_parametres(session)
    assert (relu.nb_workers, relu.temps_limite_s, relu.temps_limite_long_s) == (8, 900, 1800)
    assert OptionsSolveur.depuis_parametres(relu).nb_workers == 8
    for cle in anciennes:
        assert any(cle in message and "bornes" in message for message in caplog.messages)
    assert session.get(Parametre, "nb_workers").valeur == 64  # la lecture ne modifie rien

    # Enregistrer de nouveau l'écran des paramètres remet la base d'aplomb.
    ecrire_parametres(session, {cle: getattr(relu, cle) for cle in anciennes})
    session.commit()
    caplog.clear()
    with caplog.at_level("WARNING", logger="edt.services.parametres"):
        assert lire_parametres(session) == relu
    assert not caplog.messages

    # Une valeur d'un autre type reste une erreur dite clairement.
    session.get(Parametre, "pause_min").valeur = "quinze"
    session.commit()
    with pytest.raises(ErreurDonneesInvalides, match="pause_min"):
        lire_parametres(session)


def test_saisie_de_formulaire_convertie_selon_le_type():
    assert convertir_texte("pause_min", " 20 ") == 20
    assert convertir_texte("objectif_actif", "oui") is True
    assert convertir_texte("objectif_actif", "") is False
    with pytest.raises(ErreurDonneesInvalides):
        convertir_texte("pause_min", "vingt")
    assert all(d.libelle and d.description for d in definitions())
