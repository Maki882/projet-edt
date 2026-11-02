"""Charge : un établissement complet d'environ 1 000 séances hebdomadaires (test marqué `perf`).

Cinq promotions de six classes de deux demi-groupes, sur deux campus. Chaque
semaine type compte 1 000 séances : 8 CM par promotion (moitiés de semestre
qui se partagent quatre créneaux), 8 séances par classe (même principe) et
12 TD par demi-groupe. L'emploi du temps est valide par construction : le
vérificateur doit le dire (liste vide) en moins de 2 secondes.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from edt.domaine.enumerations import CibleIndisponibilite
from edt.verification import calculer_indicateurs, verifier, verifier_occurrences

from .fabrique import ISSY, NOTRE_DAME, SAMEDI, Fabrique, codes, h, occurrence, placer

DEBUTS = (h("8h30"), h("10h45"), h("13h45"), h("16h00"))
CRENEAUX_CM = (0, 5, 10, 15)
CRENEAUX_CLASSE = (1, 6, 11, 16)
CRENEAUX_TD = tuple(c for c in range(20) if c not in CRENEAUX_CM + CRENEAUX_CLASSE)
MOITIES = (range(1, 8), range(8, 16))
JOURS_OUVRES = ["lundi", "mardi", "mercredi", "jeudi", "vendredi"]

pytestmark = pytest.mark.perf


def fabrique_etablissement():
    """La fabrique de l'établissement et ses placements (pour y ajouter des règles)."""
    f = Fabrique()
    placements = []

    def placer_au_creneau(seance, creneau, salle):
        placements.append(placer(seance, creneau // 4, DEBUTS[creneau % 4], salle))

    for p in range(5):
        campus, prefixe = (NOTRE_DAME, "N") if p < 3 else (ISSY, "L")
        promo = f.groupe(f"P{p}", effectif=240)
        amphi = f.salle(f"{prefixe}A{p}", campus, capacite=250)
        salles = [f.salle(f"{prefixe}{p}{k:02d}", campus, capacite=40) for k in range(12)]
        prof_cm = f.enseignant(f"Prof CM {p}")
        f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, prof_cm, "8h00", "13h00", jour=SAMEDI)
        for creneau in CRENEAUX_CM:
            for moitie in MOITIES:
                cm = f.cours(
                    f"CM {p}-{creneau}", [promo], [prof_cm], type_seance="CM", semaines=moitie
                )
                placer_au_creneau(cm, creneau, amphi)
        for c in range(6):
            classe = f.groupe(f"P{p}-C{c}", parent=promo, effectif=40)
            prof_classe = f.enseignant(f"Prof classe {p}-{c}")
            for creneau in CRENEAUX_CLASSE:
                for moitie in MOITIES:
                    seance = f.cours(
                        f"Classe {p}-{c}-{creneau}", [classe], [prof_classe], semaines=moitie
                    )
                    placer_au_creneau(seance, creneau, salles[c])
            for d in range(2):
                demi = f.groupe(f"P{p}-C{c}-G{d}", parent=classe, effectif=20)
                prof_td = f.enseignant(f"Prof TD {p}-{c}-{d}", max_jour=8 * 60)
                for creneau in CRENEAUX_TD:
                    td = f.cours(f"TD {p}-{c}-{d}-{creneau}", [demi], [prof_td])
                    placer_au_creneau(td, creneau, salles[2 * c + d])
    return f, placements


def etablissement():
    f, placements = fabrique_etablissement()
    return f.construire(), placements


def test_semaine_type_de_1000_seances_en_moins_de_2_secondes():
    instantane, placements = etablissement()
    assert len(instantane.seances) == len(placements) == 1000

    chrono = time.perf_counter()
    anomalies = verifier(instantane, placements)
    duree = time.perf_counter() - chrono
    print(f"\nVérification de 1 000 séances : {duree * 1000:.0f} ms")
    assert anomalies == []
    assert duree < 2.0

    chrono = time.perf_counter()
    indicateurs = calculer_indicateurs(instantane, placements, anomalies)
    print(f"Indicateurs : {(time.perf_counter() - chrono) * 1000:.0f} ms")
    assert indicateurs.pourcentage_seances_placees == 100.0


def test_une_semaine_datee_de_1000_occurrences():
    instantane, placements = etablissement()
    occurrences = [
        occurrence(
            instantane.seance(p.seance_id), instantane.date_de(1, p.jour), p.debut, p.salle_id
        )
        for p in placements
        if 1 in instantane.seance(p.seance_id).semaines
    ]
    chrono = time.perf_counter()
    anomalies = verifier_occurrences(instantane, occurrences)
    duree = time.perf_counter() - chrono
    print(f"\nVérification de {len(occurrences)} occurrences : {duree * 1000:.0f} ms")
    assert anomalies == []
    assert duree < 2.0


def test_conflits_detectes_a_grande_echelle():
    instantane, placements = etablissement()
    # Les dix CM du lundi 8 h 30 (cinq promotions, deux moitiés de semestre) passent dans
    # l'amphi de la promotion 0, à Notre-Dame : 2 x 10 paires de CM de la même moitié se
    # chevauchent, et les promotions 3 et 4 n'ont plus le temps de rejoindre Issy à 10 h 45
    # (2 promotions x 6 classes x 2 moitiés).
    salle = placements[0].salle_id
    modifies = [
        replace(p, salle_id=salle) if (p.jour, p.debut) == (0, DEBUTS[0]) else p for p in placements
    ]
    chrono = time.perf_counter()
    anomalies = verifier(instantane, modifies)
    duree = time.perf_counter() - chrono
    print(f"\nVérification avec conflits ({len(anomalies)} anomalies) : {duree * 1000:.0f} ms")
    assert codes(anomalies).count("SALLE_UNIQUE") == 20
    assert codes(anomalies).count("CAMPUS_TRAJET") == 24
    assert len(anomalies) == 44
    assert duree < 2.0


@pytest.mark.cas_limite
def test_un_semestre_entier_de_13000_occurrences_en_moins_de_10_secondes():
    """Cas : tout un semestre de séances datées (12 900) avec dix règles personnalisées, comme l'écran À traiter le vérifie.

    Risque : une régression de coût (règles reconstruites par séance et par date, emplois
    par personne) rendrait À traiter lent de plusieurs secondes sans qu'aucun test ne le voie.
    """
    f, placements = fabrique_etablissement()
    for promo in (g.id for g in f.groupes if g.parent_id is None):
        f.regle("JOURS_TRAVAILLES", "groupe", promo, jours=JOURS_OUVRES)
        f.regle("MAX_PAR_JOUR", "groupe", promo, unite="seances", maximum=4)
    instantane = f.construire()
    assert len(instantane.regles) == 10
    occurrences = [
        occurrence(
            instantane.seance(p.seance_id),
            instantane.date_de(numero, p.jour),
            p.debut,
            p.salle_id,
            identifiant=rang,
        )
        for rang, (p, numero) in enumerate(
            (p, numero) for p in placements for numero in instantane.seance(p.seance_id).semaines
        )
    ]
    assert len(occurrences) == 12_900

    # Temps processeur, insensible aux autres programmes. Mesuré : 1,1 s seul, jusqu'à 5,2 s en
    # fin de suite complète (tas fragmenté par les tests précédents : même coût par séance,
    # mémoire moins favorable). Le seuil vise une régression d'un ordre de grandeur.
    chrono = time.process_time()
    anomalies = verifier_occurrences(instantane, occurrences)
    duree = time.process_time() - chrono
    print(f"\nVérification de {len(occurrences)} séances datées : {duree * 1000:.0f} ms")
    assert anomalies == []
    assert duree < 10.0
