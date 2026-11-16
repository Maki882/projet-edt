"""Règles entre deux séances : SALLE_UNIQUE, ENSEIGNANT_UNIQUE, GROUPE_UNIQUE, PAUSE, CAMPUS_TRAJET.

La première partie couvre les scénarios classiques de chevauchement,
transposés à l'instantané : ressources distinctes, chaque ressource en conflit, jours
distincts, séances contiguës, inclusion et égalité des intervalles, entrées
invalides, identifiant dupliqué, emploi du temps vide.
"""

from __future__ import annotations

import pytest
from tests.outils.verification.fabrique import (
    ISSY,
    LUNDI,
    MARDI,
    NOTRE_DAME,
    Fabrique,
    codes,
    h,
    placer,
)

from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurDonneesInvalides
from edt.verification import verifier

CHEVAUCHEMENTS = ["SALLE_UNIQUE", "ENSEIGNANT_UNIQUE", "GROUPE_UNIQUE"]


def scenario_deux_seances(communes=(), jour_b=LUNDI, debut_b="9h30", fin_b="11h30"):
    """Deux séances : A le lundi de 8 h 30 à 10 h 30, B de 9 h 30 à 11 h 30 par défaut.

    `communes` liste les ressources que B partage avec A (« salle »,
    « enseignant », « groupe ») ; les autres sont distinctes.
    """
    f = Fabrique()
    salles = [f.salle("N101"), f.salle("N102")]
    profs = [f.enseignant("Mme Martin"), f.enseignant("M. Durand")]
    promo = f.groupe("A1", effectif=40)
    groupes = [f.groupe("A1-G1", parent=promo), f.groupe("A1-G2", parent=promo)]
    a = f.cours("Mathématiques TD", [groupes[0]], [profs[0]])
    b = f.cours(
        "Physique TD",
        [groupes[0] if "groupe" in communes else groupes[1]],
        [profs[0] if "enseignant" in communes else profs[1]],
    )
    placements = [
        placer(a, LUNDI, "8h30", salles[0]),
        placer(b, jour_b, debut_b, salles[0] if "salle" in communes else salles[1], fin=fin_b),
    ]
    return f.construire(), placements


TOUTES = ("salle", "enseignant", "groupe")


# --------------------------------------------------------------------------- #
# Scénarios de chevauchement de base
# --------------------------------------------------------------------------- #


def test_ressources_distinctes():
    instantane, placements = scenario_deux_seances()
    assert verifier(instantane, placements, CHEVAUCHEMENTS) == []


@pytest.mark.parametrize(
    ("ressource", "code"),
    [("salle", "SALLE_UNIQUE"), ("enseignant", "ENSEIGNANT_UNIQUE"), ("groupe", "GROUPE_UNIQUE")],
)
def test_chaque_ressource_en_conflit(ressource, code):
    instantane, placements = scenario_deux_seances(communes=(ressource,))
    anomalies = verifier(instantane, placements, CHEVAUCHEMENTS)
    assert codes(anomalies) == [code]
    assert anomalies[0].seances == ("1-1", "2-1")
    assert anomalies[0].gravite is Gravite.ERREUR
    assert anomalies[0].jour == LUNDI


def test_jours_distincts():
    instantane, placements = scenario_deux_seances(
        TOUTES, jour_b=MARDI, debut_b="8h30", fin_b="10h30"
    )
    assert verifier(instantane, placements, CHEVAUCHEMENTS) == []


def test_seances_contigues_sans_chevauchement():
    instantane, placements = scenario_deux_seances(TOUTES, debut_b="10h30", fin_b="12h30")
    assert verifier(instantane, placements, CHEVAUCHEMENTS) == []
    # Contiguës, elles enfreignent en revanche la pause (même enseignant, même groupe).
    assert codes(verifier(instantane, placements, ["PAUSE"])) == ["PAUSE"]


@pytest.mark.parametrize(("debut", "fin"), [("9h00", "10h00"), ("8h30", "10h30")])
def test_inclusion_et_egalite_des_intervalles(debut, fin):
    instantane, placements = scenario_deux_seances(TOUTES, debut_b=debut, fin_b=fin)
    anomalies = verifier(instantane, list(reversed(placements)), CHEVAUCHEMENTS)
    assert codes(anomalies) == CHEVAUCHEMENTS


@pytest.mark.parametrize(
    "changements",
    [
        {"jour": 7},
        {"jour": None},
        {"debut": 25 * 60},
        {"fin": -5},
        {"fin": h("8h30")},
        {"fin": h("7h00")},
        {"debut": "8h30"},
        {"salle_id": "N204"},
    ],
)
def test_entrees_invalides(changements):
    from dataclasses import replace

    instantane, placements = scenario_deux_seances()
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        verifier(instantane, [replace(placements[0], **changements), placements[1]])
    assert erreur.value.details  # chaque défaut est listé


def test_toutes_les_entrees_invalides_sont_listees():
    from dataclasses import replace

    instantane, placements = scenario_deux_seances()
    with pytest.raises(ErreurDonneesInvalides) as erreur:
        verifier(instantane, [replace(p, jour=9) for p in placements])
    assert len(erreur.value.details) == 2


@pytest.mark.cas_limite
def test_identifiant_duplique():
    """Cas : la même séance placée deux fois au même créneau de la semaine type.

    Risque : les deux copies en conflit « avec elles-mêmes » (salle, enseignant, groupe) :
    trois erreurs de plus pour un seul défaut, le doublon.
    """
    instantane, placements = scenario_deux_seances()
    anomalies = verifier(instantane, [placements[0], placements[0], placements[1]])
    volume = [a for a in anomalies if str(a.code) == "VOLUME"]
    assert len(volume) == 1
    assert "placée 2 fois" in volume[0].message
    # Le doublon est nommé une fois, par VOLUME : pas de conflit entre les deux copies...
    assert not set(codes(anomalies)) & set(CHEVAUCHEMENTS)
    # ... mais chaque copie reste vérifiée face aux autres séances.
    instantane, placements = scenario_deux_seances(TOUTES)
    doublees = codes(verifier(instantane, [placements[0], placements[0], placements[1]]))
    assert [doublees.count(code) for code in CHEVAUCHEMENTS] == [2, 2, 2]
    # Sans VOLUME, le conflit des deux copies reste signalé : le doublon ne passe pas inaperçu.
    sans_volume = verifier(instantane, [placements[0], placements[0]], CHEVAUCHEMENTS)
    assert codes(sans_volume) == CHEVAUCHEMENTS


def test_emploi_du_temps_vide():
    assert verifier(Fabrique().construire(), []) == []


# --------------------------------------------------------------------------- #
# Chevauchements
# --------------------------------------------------------------------------- #


def deux_groupes_freres():
    f = Fabrique()
    n204 = f.salle("N204")
    martin, durand = f.enseignant("Mme Martin"), f.enseignant("M. Durand")
    a1 = f.groupe("A1", effectif=40)
    g1, g2 = f.groupe("A1-G1", parent=a1), f.groupe("A1-G2", parent=a1)
    return f, n204, martin, durand, a1, g1, g2


def test_message_salle_unique():
    f, n204, martin, durand, _, g1, g2 = deux_groupes_freres()
    maths = f.cours("Mathématiques TD", [g1], [martin])
    physique = f.cours("Physique TD", [g2], [durand])
    anomalies = verifier(
        f.construire(),
        [placer(maths, MARDI, "10h45", n204), placer(physique, MARDI, "10h45", n204)],
    )
    assert len(anomalies) == 1  # une seule anomalie pour la paire, pas deux symétriques
    assert anomalies[0].message == (
        "La salle N204 accueille deux séances en même temps le mardi de 10 h 45 à 12 h 45 : "
        "Mathématiques TD (A1-G1) et Physique TD (A1-G2)."
    )
    assert anomalies[0].salles == (n204,)
    assert anomalies[0].groupes == ()


def test_plages_differentes_citees_une_par_une():
    f, n204, martin, durand, _, g1, g2 = deux_groupes_freres()
    maths = f.cours("Mathématiques TD", [g1], [martin])
    physique = f.cours("Physique TD", [g2], [durand])
    anomalies = verifier(
        f.construire(),
        [placer(physique, MARDI, "11h00", n204), placer(maths, MARDI, "10h45", n204)],
        ["SALLE_UNIQUE"],
    )
    assert anomalies[0].message == (
        "La salle N204 accueille deux séances en même temps le mardi : Mathématiques TD (A1-G1) "
        "de 10 h 45 à 12 h 45 et Physique TD (A1-G2) de 11 h 00 à 13 h 00."
    )


def test_semaines_disjointes_pas_de_conflit():
    f, n204, martin, _, _, g1, _ = deux_groupes_freres()
    court_1 = f.cours("Module court 1", [g1], [martin], semaines=range(1, 8))
    court_2 = f.cours("Module court 2", [g1], [martin], semaines=range(8, 16))
    placements = [placer(court_1, MARDI, "8h30", n204), placer(court_2, MARDI, "8h30", n204)]
    assert verifier(f.construire(), placements) == []


def test_semaines_qui_se_croisent_en_partie():
    f, n204, martin, durand, _, g1, g2 = deux_groupes_freres()
    court_1 = f.cours("Module court 1", [g1], [martin], semaines=range(1, 9))
    court_2 = f.cours("Module court 2", [g2], [durand], semaines=range(8, 16))
    anomalies = verifier(
        f.construire(),
        [placer(court_1, MARDI, "8h30", n204), placer(court_2, MARDI, "8h30", n204)],
    )
    assert codes(anomalies) == ["SALLE_UNIQUE"]
    assert "(semaine 8)" in anomalies[0].message


def test_enseignant_fictif_ignore():
    f, n204, _, _, _, g1, g2 = deux_groupes_freres()
    inconnu = f.enseignant("Enseignant à définir (maths)", fictif=True)
    n205 = f.salle("N205")
    a = f.cours("Mathématiques TD", [g1], [inconnu])
    b = f.cours("Mathématiques TD bis", [g2], [inconnu])
    placements = [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "8h30", n205)]
    assert verifier(f.construire(), placements) == []


def test_enseignant_unique_message():
    f, n204, martin, _, _, g1, g2 = deux_groupes_freres()
    n205 = f.salle("N205")
    a = f.cours("Mathématiques TD", [g1], [martin])
    b = f.cours("Physique TD", [g2], [martin])
    anomalies = verifier(
        f.construire(), [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "8h30", n205)]
    )
    assert codes(anomalies) == ["ENSEIGNANT_UNIQUE"]
    assert anomalies[0].message.startswith("Mme Martin donne deux séances en même temps le mardi")
    assert anomalies[0].enseignants == (martin,)


def test_groupes_freres_sans_conflit_et_groupe_parent_en_conflit():
    f, n204, martin, durand, a1, g1, g2 = deux_groupes_freres()
    n205, amphi = f.salle("N205"), f.salle("Amphi", capacite=100)
    durant = f.enseignant("M. Petit")
    a = f.cours("Mathématiques TD", [g1], [martin])
    b = f.cours("Physique TD", [g2], [durand])
    cm = f.cours("Chimie CM", [a1], [durant], type_seance="CM")
    instantane = f.construire()
    freres = [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "8h30", n205)]
    assert verifier(instantane, freres, CHEVAUCHEMENTS) == []

    anomalies = verifier(instantane, [*freres, placer(cm, MARDI, "8h30", amphi)])
    assert codes(anomalies) == ["GROUPE_UNIQUE", "GROUPE_UNIQUE"]
    assert anomalies[0].message.startswith(
        "Les groupes A1 et A1-G1, qui ont des étudiants en commun, ont deux séances"
    )
    assert anomalies[0].groupes == (a1, g1)


def test_conflit_declare_entre_groupes_freres():
    f, n204, martin, durand, _, g1, g2 = deux_groupes_freres()
    f.conflit(g1, g2)
    n205 = f.salle("N205")
    a = f.cours("Mathématiques TD", [g1], [martin])
    b = f.cours("Physique TD", [g2], [durand])
    anomalies = verifier(
        f.construire(), [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "8h30", n205)]
    )
    assert codes(anomalies) == ["GROUPE_UNIQUE"]


def test_contrainte_desactivee():
    instantane, placements = scenario_deux_seances(TOUTES)
    assert codes(verifier(instantane, placements, ["SALLE_UNIQUE"])) == ["SALLE_UNIQUE"]
    assert verifier(instantane, placements, []) == []


# --------------------------------------------------------------------------- #
# Pause
# --------------------------------------------------------------------------- #


def enchainement(
    debut_b,
    *,
    meme_enseignant=True,
    campus_b=None,
    salle_b=True,
    semaines_a=None,
    semaines_b=None,
    fictif=False,
):
    """A (N204) finit le mardi à 10 h 30 ; B commence à `debut_b` ; même enseignant par défaut."""
    f, n204, martin, durand, _, g1, g2 = deux_groupes_freres()
    if fictif:
        martin = durand = f.enseignant("Enseignant à définir", fictif=True)
    salle = None
    if salle_b:
        salle = f.salle("L101", campus=campus_b) if campus_b else f.salle("N205")
    a = f.cours("Mathématiques TD", [g1], [martin], semaines=semaines_a)
    b = f.cours("Physique TD", [g2], [martin if meme_enseignant else durand], semaines=semaines_b)
    return f, [placer(a, MARDI, "8h30", n204), placer(b, MARDI, debut_b, salle)]


def test_pause_insuffisante():
    f, placements = enchainement("10h40")
    anomalies = verifier(f.construire(), placements, ["PAUSE"])
    assert codes(anomalies) == ["PAUSE"]
    assert anomalies[0].message == (
        "Mme Martin n'a que 10 min de pause le mardi entre Mathématiques TD (A1-G1), qui finit "
        "à 10 h 30, et Physique TD (A1-G2), qui commence à 10 h 40 ; il faut au moins 15 min."
    )
    assert anomalies[0].enseignants == (1,)
    assert anomalies[0].salles == ()


def test_pause_exactement_egale_valide():
    f, placements = enchainement("10h45")
    assert verifier(f.construire(), placements, ["PAUSE"]) == []


def test_pause_seances_contigues():
    f, placements = enchainement("10h30")
    anomalies = verifier(f.construire(), placements, ["PAUSE"])
    assert "Mme Martin n'a aucune pause le mardi" in anomalies[0].message


def test_pause_sans_personne_commune():
    f, placements = enchainement("10h30", meme_enseignant=False)
    assert verifier(f.construire(), placements, ["PAUSE"]) == []


def test_pause_enseignant_fictif():
    f, placements = enchainement("10h30", fictif=True)
    assert verifier(f.construire(), placements, ["PAUSE"]) == []


def test_pause_semaines_disjointes():
    f, placements = enchainement("10h30", semaines_a=range(1, 8), semaines_b=range(8, 16))
    assert verifier(f.construire(), placements, ["PAUSE"]) == []


def test_pause_desactivee():
    f, placements = enchainement("10h30")
    assert verifier(f.construire(), placements, ["SALLE_UNIQUE", "ENSEIGNANT_UNIQUE"]) == []


def test_pause_pour_un_groupe_et_la_salle_ne_compte_pas():
    f, n204, martin, durand, a1, g1, _ = deux_groupes_freres()
    cm = f.cours("Chimie CM", [a1], [durand], type_seance="CM")
    td = f.cours("Mathématiques TD", [g1], [martin])
    anomalies = verifier(
        f.construire(),
        [placer(cm, MARDI, "8h30", n204), placer(td, MARDI, "10h30", n204)],
        ["PAUSE", "SALLE_UNIQUE"],
    )
    assert codes(anomalies) == ["PAUSE"]
    assert anomalies[0].message.startswith("Les groupes A1 et A1-G1 n'ont aucune pause le mardi")
    assert anomalies[0].groupes == (a1, g1)


def test_salle_seule_partagee_sans_pause():
    f, n204, martin, durand, _, g1, g2 = deux_groupes_freres()
    a = f.cours("Mathématiques TD", [g1], [martin])
    b = f.cours("Physique TD", [g2], [durand])
    placements = [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "10h30", n204)]
    assert verifier(f.construire(), placements, ["PAUSE", "SALLE_UNIQUE"]) == []


# --------------------------------------------------------------------------- #
# Trajet entre campus
# --------------------------------------------------------------------------- #


def test_trajet_insuffisant():
    f, placements = enchainement("10h45", campus_b=ISSY)
    anomalies = verifier(f.construire(), placements, ["CAMPUS_TRAJET"])
    assert codes(anomalies) == ["CAMPUS_TRAJET"]
    assert anomalies[0].message == (
        "Mme Martin doit changer de campus le mardi en 15 min seulement : Mathématiques TD "
        "(A1-G1) finit à 10 h 30 en salle N204 (Notre-Dame-des-Champs) et Physique TD (A1-G2) "
        "commence à 10 h 45 en salle L101 (Issy) ; le trajet demande 1 h."
    )
    assert len(anomalies[0].salles) == 2


def test_trajet_exactement_egal_valide():
    # Une heure d'écart (comme le déjeuner) suffit pour le trajet par défaut de 60 min.
    f, placements = enchainement("11h30", campus_b=ISSY)
    assert verifier(f.construire(), placements, ["CAMPUS_TRAJET"]) == []


@pytest.mark.parametrize(("debut_b", "attendu"), [("10h45", ["CAMPUS_TRAJET"]), ("10h50", [])])
def test_trajet_selon_la_matrice(debut_b, attendu):
    f, placements = enchainement(debut_b, campus_b=ISSY)
    f.trajet(ISSY, NOTRE_DAME, 20)
    assert codes(verifier(f.construire(), placements, ["CAMPUS_TRAJET"])) == attendu


def test_trajet_meme_campus_ou_salle_a_definir():
    f, placements = enchainement("10h45")
    assert verifier(f.construire(), placements, ["CAMPUS_TRAJET"]) == []
    f, placements = enchainement("10h45", salle_b=False)
    assert verifier(f.construire(), placements, ["CAMPUS_TRAJET"]) == []


def test_trajet_sans_personne_commune():
    f, placements = enchainement("10h45", campus_b=ISSY, meme_enseignant=False)
    assert verifier(f.construire(), placements, ["CAMPUS_TRAJET"]) == []


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_aucune_contrainte_a_verifier_journalisee(caplog):
    """Cas : le vérificateur pur reçoit une liste de contraintes vide, ou des niveaux vides.

    Risque : une liste vide renvoyée sans un mot, prise pour « emploi du temps valide » (les
    services, eux, la refusent : voir test_service).
    """
    instantane, placements = scenario_deux_seances(TOUTES)
    for verification in (
        lambda: verifier(instantane, placements, []),
        lambda: verifier(instantane, placements, niveaux={}),
    ):
        caplog.clear()
        with caplog.at_level("WARNING", logger="edt.verification"):
            assert verification() == []
        assert "Aucune contrainte à vérifier" in caplog.text
