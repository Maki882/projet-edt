"""De bout en bout : un petit emploi du temps valide, écrit à la main, ne donne aucune anomalie.

Il mélange ce qui se rencontre dans un vrai semestre : deux campus, une
promotion et ses demi-groupes, un CM, des TD, un TP le samedi, deux modules
courts qui se partagent le même créneau sur des semaines disjointes, un DST
imposé, des indisponibilités, des maxima horaires, un enseignant fictif et un
changement de campus séparé par le déjeuner. On vérifie la semaine type, puis
les occurrences obtenues en l'instanciant sur toutes les semaines.
"""

from __future__ import annotations

from edt.domaine.enumerations import Accessibilite, CibleIndisponibilite
from edt.verification import verifier, verifier_occurrences

from .fabrique import ISSY, JEUDI, LUNDI, MARDI, SAMEDI, VENDREDI, Fabrique, h, occurrence, placer


def semestre_valide():
    f = Fabrique()
    amphi = f.salle("N001", capacite=60, type_salle=2)
    n204 = f.salle("N204", capacite=30, accessibilite=Accessibilite.OUI)
    n205 = f.salle("N205", capacite=30)
    labo = f.salle("N310", capacite=24, type_salle=3, equipements=[1, 2])
    l101 = f.salle("L101", campus=ISSY, capacite=30)

    martin = f.enseignant("Mme Martin", max_jour=6 * 60, max_semaine=10 * 60)
    durand = f.enseignant("M. Durand")
    a_definir = f.enseignant("Enseignant à définir (physique)", fictif=True)

    a1 = f.groupe("A1", effectif=40)
    g1 = f.groupe("A1-G1", parent=a1, effectif=20, accessibilite_requise=True)
    g2 = f.groupe("A1-G2", parent=a1, effectif=20)

    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, martin, "8h30", "12h45", jour=JEUDI)
    f.indisponibilite(CibleIndisponibilite.SALLE, labo, "16h00", "18h00", jour=MARDI)
    f.indisponibilite(CibleIndisponibilite.GROUPE, a1, "16h00", "18h00", jour=VENDREDI)

    cm = f.cours("Mathématiques CM", [a1], [martin], type_seance="CM", types_salle_acceptes={2})
    td_g1 = f.cours("Mathématiques TD", [g1], [martin])
    td_g2 = f.cours("Mathématiques TD", [g2], [durand])
    tp = f.cours(
        "Physique TP",
        [g2],
        [a_definir],
        type_seance="TP",
        samedi_autorise=True,
        types_salle_acceptes={3},
        equipements_requis={1},
    )
    court_1 = f.cours("Python", [g1], [durand], semaines=range(1, 8))
    court_2 = f.cours("Réseaux", [g1], [durand], semaines=range(8, 16))
    dst = f.cours(
        "DST Mathématiques",
        [a1],
        [martin],
        duree=255,
        type_seance="DST",
        jour_impose=JEUDI,
        debut_impose=h("13h45"),
    )
    issy = f.cours("Anglais", [g2], [durand])

    placements = [
        placer(cm, LUNDI, "8h30", amphi),
        placer(td_g1, LUNDI, "10h45", n204),
        placer(td_g2, LUNDI, "10h45", n205),
        placer(issy, LUNDI, "13h45", l101),  # Issy : M. Durand et A1-G2 y vont pendant le déjeuner
        placer(tp, SAMEDI, "8h30", labo),
        placer(court_1, MARDI, "8h30", n204),
        placer(court_2, MARDI, "8h30", n204),
        placer(dst, JEUDI, "13h45", amphi, fin="18h00"),
    ]
    return f.construire(), placements


def test_semaine_type_valide():
    instantane, placements = semestre_valide()
    assert verifier(instantane, placements) == []


def test_occurrences_du_semestre_valides():
    instantane, placements = semestre_valide()
    toutes = []
    for placement in placements:
        seance = instantane.seance(placement.seance_id)
        for numero in sorted(seance.semaines):
            date = instantane.date_de(numero, placement.jour)
            toutes.append(
                occurrence(
                    seance,
                    date,
                    placement.debut,
                    placement.salle_id,
                    fin=placement.fin,
                    identifiant=len(toutes) + 1,
                )
            )
    # Comme l'instanciation, rien hors des dates du semestre : le samedi de la dernière
    # semaine (19 décembre) suit son dernier jour (vendredi 18 décembre).
    debut, fin = instantane.date_debut, instantane.date_fin
    occurrences = [o for o in toutes if debut <= o.date <= fin]
    assert len(occurrences) == 6 * 15 + 7 + 8 - 1
    assert verifier_occurrences(instantane, occurrences) == []
    # Instanciée naïvement, la semaine type place le TP du samedi 19 décembre hors du semestre.
    (hors,) = [o for o in toutes if o not in occurrences]
    anomalies = verifier_occurrences(instantane, toutes)
    assert [(str(a.code), a.occurrences) for a in anomalies] == [("JOUR_FERME", (hors.id,))]
