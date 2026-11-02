"""VOLUME, VERROU, MAX_ENSEIGNANT et choix des contraintes vérifiées (semaine type)."""

from __future__ import annotations

import pytest

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Occurrence, Placement, decomposer_id_seance
from edt.erreurs import ErreurDonneesInvalides
from edt.verification import verifier, verifier_occurrences

from .fabrique import (
    JEUDI,
    LUNDI,
    MARDI,
    MARDI_6_OCT,
    MERCREDI,
    SAMEDI,
    Fabrique,
    codes,
    h,
    placer,
)

# --------------------------------------------------------------------------- #
# Volume
# --------------------------------------------------------------------------- #


def deux_seances():
    f = Fabrique()
    n204 = f.salle("N204")
    g1 = f.groupe("A1-G1")
    besoin = f.besoin("Mathématiques TD", [g1], [f.enseignant("Mme Martin")], nombre=2)
    return f, n204, f.seance(besoin, 1), f.seance(besoin, 2, semaines=range(1, 8))


def test_seance_non_placee():
    f, n204, s1, _ = deux_seances()
    anomalies = verifier(f.construire(), [placer(s1, LUNDI, "8h30", n204)], ["VOLUME"])
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].message == (
        "La séance Mathématiques TD n° 2 (A1-G1) (semaines 1 à 7) n'est pas placée."
    )
    assert anomalies[0].seances == ("1-2",)
    assert anomalies[0].besoins == (1,)


def test_placement_d_une_seance_qui_n_est_plus_demandee_nomme_le_besoin():
    # Constat n° 17 de la revue : après le raccourcissement d'un semestre, la version garde
    # la séance « 1-3 » que le besoin ne produit plus. Le message nomme le besoin et dit quoi
    # faire, sans exposer l'identifiant technique (qui reste dans le champ `seances`).
    f, n204, s1, s2 = deux_seances()
    placements = [
        placer(s1, LUNDI, "8h30", n204),
        placer(s2, MARDI, "8h30", n204),
        Placement("1-3", JEUDI, h("8h30"), h("10h30"), n204),
    ]
    anomalies = verifier(f.construire(), placements, ["VOLUME"])
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].message == (
        "La séance n° 3 de « Mathématiques TD (A1-G1) », placée le jeudi de 8 h 30 à 10 h 30 "
        "en salle N204, n'est plus demandée : la période du besoin ou celle du semestre a été "
        "raccourcie, ou le nombre de séances demandées a diminué. Relancer le calcul pour "
        "remettre l'emploi du temps en accord avec les besoins."
    )
    assert "1-3" not in anomalies[0].message
    assert anomalies[0].seances == ("1-3",)
    assert anomalies[0].besoins == (1,)
    assert anomalies[0].groupes == (1,)


def test_placement_d_un_besoin_absent_du_semestre():
    f, n204, s1, s2 = deux_seances()
    placements = [
        placer(s1, LUNDI, "8h30", n204),
        placer(s2, MARDI, "8h30", n204),
        Placement("99-1", JEUDI, h("8h30"), h("10h30"), None),
    ]
    anomalies = verifier(f.construire(), placements, ["VOLUME"])
    assert codes(anomalies) == ["VOLUME"]
    assert anomalies[0].message.startswith(
        "La séance placée le jeudi de 8 h 30 à 10 h 30 se rattache à un besoin qui n'existe pas "
        "dans ce semestre (n° 99) : elle ne peut pas être vérifiée."
    )


def test_placement_inconnu_hors_volume_journalise(caplog):
    f, n204, s1, s2 = deux_seances()
    placements = [
        placer(s1, LUNDI, "8h30", n204),
        placer(s2, MARDI, "8h30", n204),
        Placement("99-1", JEUDI, h("8h30"), h("10h30"), n204),
    ]
    with caplog.at_level("WARNING", logger="edt.verification"):
        assert verifier(f.construire(), placements, ["SALLE_UNIQUE"]) == []
    assert "99-1" in caplog.text


def test_seance_placee_deux_fois():
    f, n204, s1, s2 = deux_seances()
    placements = [
        placer(s1, LUNDI, "8h30", n204),
        placer(s1, JEUDI, "10h45", n204),
        placer(s2, MARDI, "8h30", n204),
    ]
    anomalies = verifier(f.construire(), placements, ["VOLUME"])
    assert anomalies[0].message == (
        "La séance Mathématiques TD n° 1 (A1-G1) est placée 2 fois au lieu d'une : le lundi de "
        "8 h 30 à 10 h 30 et le jeudi de 10 h 45 à 12 h 45."
    )


def test_problemes_de_depliage_rapportes_tels_quels():
    f, n204, s1, s2 = deux_seances()
    besoin = f.besoin("DST Mathématiques", [1])
    f.probleme(besoin, CodeContrainte.VOLUME, "DST Mathématiques : séance ponctuelle sans date.")
    f.probleme(besoin, CodeContrainte.JOUR_FERME, "DST Mathématiques : la date est fermée.")
    instantane = f.construire()
    placements = [placer(s1, LUNDI, "8h30", n204), placer(s2, MARDI, "8h30", n204)]
    anomalies = verifier(instantane, placements, ["VOLUME", "JOUR_FERME"])
    assert [(str(a.code), a.message) for a in anomalies] == [
        ("VOLUME", "DST Mathématiques : séance ponctuelle sans date."),
        ("JOUR_FERME", "DST Mathématiques : la date est fermée."),
    ]
    assert all(a.gravite is Gravite.ERREUR and a.besoins == (besoin.id,) for a in anomalies)
    assert codes(verifier(instantane, placements, ["VOLUME"])) == ["VOLUME"]


# --------------------------------------------------------------------------- #
# Verrou
# --------------------------------------------------------------------------- #


def seance_verrouillee(avec_salle=True):
    f = Fabrique()
    n204, l101 = f.salle("N204"), f.salle("L101")
    besoin = f.besoin("Anglais TD", [f.groupe("A1-G1")])
    verrou = Placement("1-1", MARDI, h("8h30"), h("10h30"), n204 if avec_salle else None)
    return f, n204, l101, f.seance(besoin, 1, verrou=verrou)


def test_verrou_respecte():
    f, n204, _, seance = seance_verrouillee()
    assert verifier(f.construire(), [placer(seance, MARDI, "8h30", n204)], ["VERROU"]) == []


def test_verrou_deplace():
    f, n204, l101, seance = seance_verrouillee()
    anomalies = verifier(f.construire(), [placer(seance, JEUDI, "10h45", l101)], ["VERROU"])
    assert codes(anomalies) == ["VERROU"]
    assert anomalies[0].message == (
        "La séance verrouillée Anglais TD (A1-G1) doit rester le mardi de 8 h 30 à 10 h 30 en "
        "salle N204, mais elle est placée le jeudi de 10 h 45 à 12 h 45 en salle L101."
    )
    anomalies = verifier(f.construire(), [placer(seance, MARDI, "8h30", l101)], ["VERROU"])
    assert codes(anomalies) == ["VERROU"]


def test_verrou_sans_salle():
    f, _, l101, seance = seance_verrouillee(avec_salle=False)
    assert verifier(f.construire(), [placer(seance, MARDI, "8h30", l101)], ["VERROU"]) == []


def test_seance_verrouillee_hors_grille_information():
    f = Fabrique()
    n204 = f.salle("N204")
    besoin = f.besoin("Conférence", [f.groupe("A1")])
    verrou = Placement("1-1", MARDI, h("12h00"), h("14h00"), n204)
    seance = f.seance(besoin, 1, verrou=verrou)
    anomalies = verifier(f.construire(), [verrou], ["VERROU", "HORAIRES", "DEJEUNER"])
    assert {a.gravite for a in anomalies} == {Gravite.INFORMATION}
    assert codes(anomalies) == ["HORAIRES", "DEJEUNER"]
    assert seance.verrou == verrou


# --------------------------------------------------------------------------- #
# Maxima des enseignants
# --------------------------------------------------------------------------- #


def charge(max_jour=None, max_semaine=None, fictif=False):
    """Mme Martin : trois séances de 2 h, lundi, lundi et mardi ; la 3e les semaines 1 à 5."""
    f = Fabrique()
    n204 = f.salle("N204")
    martin = f.enseignant("Mme Martin", fictif=fictif, max_jour=max_jour, max_semaine=max_semaine)
    g1, g2 = f.groupe("A1-G1"), f.groupe("A1-G2")
    a = f.cours("Mathématiques TD", [g1], [martin])
    b = f.cours("Mathématiques TD", [g2], [martin])
    c = f.cours("Soutien", [g1], [martin], semaines=range(1, 6))
    placements = [
        placer(a, LUNDI, "8h30", n204),
        placer(b, LUNDI, "10h45", n204),
        placer(c, MARDI, "8h30", n204),
    ]
    return f.construire(), placements


def test_maximum_hebdomadaire():
    instantane, placements = charge(max_semaine=5 * 60)
    anomalies = verifier(instantane, placements, ["MAX_ENSEIGNANT"])
    assert codes(anomalies) == ["MAX_ENSEIGNANT"]  # une seule anomalie, pas une par semaine
    assert anomalies[0].message == (
        "Mme Martin dépasse son maximum de 5 h de cours par semaine : 6 h de cours les "
        "semaines 1 à 5."
    )
    assert anomalies[0].seances == ("1-1", "2-1", "3-1")
    assert anomalies[0].jour is None


def test_maximum_atteint_exactement():
    instantane, placements = charge(max_semaine=6 * 60, max_jour=4 * 60)
    assert verifier(instantane, placements, ["MAX_ENSEIGNANT"]) == []


def test_maximum_journalier():
    instantane, placements = charge(max_jour=3 * 60)
    anomalies = verifier(instantane, placements, ["MAX_ENSEIGNANT"])
    assert codes(anomalies) == ["MAX_ENSEIGNANT"]
    assert anomalies[0].jour == LUNDI
    assert anomalies[0].message == (
        "Mme Martin dépasse son maximum de 3 h de cours par jour le lundi : 4 h de cours les "
        "semaines 1 à 15."
    )


def test_maximum_enseignant_fictif_ou_sans_maximum():
    instantane, placements = charge(max_semaine=60, max_jour=60, fictif=True)
    assert verifier(instantane, placements, ["MAX_ENSEIGNANT"]) == []
    instantane, placements = charge()
    assert verifier(instantane, placements, ["MAX_ENSEIGNANT"]) == []


# --------------------------------------------------------------------------- #
# Choix des contraintes
# --------------------------------------------------------------------------- #


def test_code_inconnu_refuse():
    instantane, placements = charge()
    with pytest.raises(ErreurDonneesInvalides):
        verifier(instantane, placements, ["SALLE_UNIQUE", "PAS_UN_CODE"])
    with pytest.raises(ErreurDonneesInvalides):
        verifier(instantane, placements, "SALLE_UNIQUE")


def test_codes_acceptes_en_texte_ou_type():
    instantane, placements = charge(max_semaine=60)
    assert codes(verifier(instantane, placements, [CodeContrainte.MAX_ENSEIGNANT])) == codes(
        verifier(instantane, placements, ["MAX_ENSEIGNANT"])
    )


def test_anomalies_triees_et_independantes_de_l_ordre_recu():
    instantane, placements = charge(max_semaine=60, max_jour=60)
    # Des séances placées deux fois, dont une à midi et une sur le créneau d'une autre séance
    # de Mme Martin : volume, déjeuner, grille, maxima, chevauchement.
    melange = [
        *placements,
        placer(instantane.seances[0], LUNDI, "8h30"),
        placer(instantane.seances[1], MERCREDI, "12h00"),
        placer(instantane.seances[2], LUNDI, "10h45"),
    ]
    anomalies = verifier(instantane, melange)
    assert anomalies == verifier(instantane, list(reversed(melange)))
    rangs = [Gravite.ERREUR, Gravite.AVERTISSEMENT, Gravite.INFORMATION]
    assert [rangs.index(a.gravite) for a in anomalies] == sorted(
        rangs.index(a.gravite) for a in anomalies
    )
    assert len(set(codes(anomalies))) >= 5


def test_libelle_du_socle_qui_cite_deja_ses_groupes():
    # Convention de edt.services.instantane : « <type> <matière> - <codes des groupes> ».
    f = Fabrique()
    a1 = f.groupe("A1")
    besoin = f.besoin("CM Physique - A1", [a1], nombre=2)
    f.seance(besoin, 1)
    f.seance(besoin, 2)
    anomalies = verifier(f.construire(), [], ["VOLUME"])
    assert [a.message for a in anomalies] == [
        "La séance CM Physique - A1 (n° 1) n'est pas placée.",
        "La séance CM Physique - A1 (n° 2) n'est pas placée.",
    ]


def test_seance_verrouillee_deplacee_n_est_plus_imposee():
    # Même règle que la couche datée (constat n° 15) : hors de la place de son verrou, une
    # séance verrouillée n'a plus droit aux écarts admis.
    f, n204, _, seance = seance_verrouillee()
    anomalies = verifier(f.construire(), [placer(seance, SAMEDI, "8h30", n204)], ["JOURS"])
    assert [(str(a.code), a.gravite) for a in anomalies] == [("JOURS", Gravite.ERREUR)]


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize("identifiant", ["²-1", "٣-1", "1-²"])
def test_identifiant_de_seance_illisible_refuse_proprement(identifiant):
    """Cas : un placement ou une séance datée porte un identifiant de séance aux chiffres non ASCII (« ²-1 », « ٣-1 »).

    Risque : `str.isdigit` les accepte mais `int` lève `ValueError` : erreur 500 ou trace
    au lieu d'une anomalie ou d'une erreur métier.
    """
    f, n204, s1, s2 = deux_seances()
    instantane = f.construire()
    placements = [
        placer(s1, LUNDI, "8h30", n204),
        placer(s2, MARDI, "8h30", n204),
        Placement(identifiant, JEUDI, h("8h30"), h("10h30"), n204),
    ]
    # Semaine type : un placement de séance inconnue, signalé (VOLUME).
    (anomalie,) = verifier(instantane, placements, ["VOLUME"])
    assert anomalie.seances == (identifiant,)
    if identifiant.startswith("1-"):  # besoin lisible, rang illisible : le besoin est nommé
        assert anomalie.message.startswith("La séance de « Mathématiques TD (A1-G1) »")
    else:
        assert f"(référence « {identifiant} »)" in anomalie.message
    # Couche datée : une séance datée qui n'appartient pas à son besoin rend la vérification
    # impossible, avec une erreur métier.
    illisible = Occurrence(1, 1, identifiant, MARDI_6_OCT, h("8h30"), h("10h30"), n204)
    with pytest.raises(ErreurDonneesInvalides, match="séances datées") as erreur:
        verifier_occurrences(instantane, [illisible])
    assert f"« {identifiant} »" in erreur.value.details[0]


@pytest.mark.cas_limite
@pytest.mark.xfail(
    strict=True,
    raises=ValueError,
    reason="à corriger par le lot socle : src/edt/domaine/instantane.py, decomposer_id_seance "
    "(isdigit() laisse passer « ² » ; isascii() et isdecimal() attendus)",
)
def test_decomposer_un_identifiant_aux_chiffres_non_decimaux_leve_une_erreur_metier():
    """Cas : `decomposer_id_seance` reçoit « ²-1 » (identifiant cité par une anomalie, une saisie, un import).

    Risque : `ValueError` au lieu d'`ErreurDonneesInvalides` dans les services qui
    décomposent les identifiants (anomalies de l'emploi du temps, instanciation).
    """
    with pytest.raises(ErreurDonneesInvalides):
        decomposer_id_seance("²-1")
