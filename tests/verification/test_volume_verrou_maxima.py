"""VOLUME, VERROU, MAX_ENSEIGNANT et choix des contraintes vérifiées (semaine type)."""

from __future__ import annotations

import pytest

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Placement
from edt.erreurs import ErreurDonneesInvalides
from edt.verification import verifier

from .fabrique import JEUDI, LUNDI, MARDI, MERCREDI, Fabrique, codes, h, placer

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


def test_placement_d_une_seance_inconnue():
    f, n204, s1, s2 = deux_seances()
    placements = [
        placer(s1, LUNDI, "8h30", n204),
        placer(s2, MARDI, "8h30", n204),
        Placement("99-1", JEUDI, h("8h30"), h("10h30"), n204),
    ]
    anomalies = verifier(f.construire(), placements, ["VOLUME"])
    assert codes(anomalies) == ["VOLUME"]
    assert "désigne une séance inconnue dans ce semestre (« 99-1 »)" in anomalies[0].message


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
    # Une séance placée deux fois, à midi : volume, déjeuner, grille, maxima, chevauchements.
    melange = [
        *placements,
        placer(instantane.seances[0], LUNDI, "8h30"),
        placer(instantane.seances[1], MERCREDI, "12h00"),
    ]
    anomalies = verifier(instantane, melange)
    assert anomalies == verifier(instantane, list(reversed(melange)))
    rangs = [Gravite.ERREUR, Gravite.AVERTISSEMENT, Gravite.INFORMATION]
    assert [rangs.index(a.gravite) for a in anomalies] == sorted(
        rangs.index(a.gravite) for a in anomalies
    )
    assert len(set(codes(anomalies))) >= 5
