"""Couche datée : instanciation de la semaine type, puis replacement des séances empêchées."""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from tests.solveur.fabrique import (
    C1,
    C2,
    C3,
    JEUDI,
    LUNDI,
    MARDI,
    RENTREE,
    Fabrique,
    H,
    grille,
    verifier_dates,
)
from tests.solveur.test_contraintes import UN_CRENEAU

from edt.domaine.contraintes import CodeContrainte as C
from edt.domaine.enumerations import CibleIndisponibilite, StatutOccurrence
from edt.domaine.instantane import Placement
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.solveur.instanciation import instancier
from edt.solveur.replacement import OptionsReplacement, replacer

SEMAINE = dt.timedelta(days=7)


def place(seance_id: str, jour: int, debut: int, salle: int, duree: int = 2 * H) -> Placement:
    return Placement(seance_id, jour, debut, debut + duree, salle)


def une_classe(nb_semaines: int = 3, creneaux=None, **besoin) -> Fabrique:
    """Une classe, un enseignant, deux salles, un besoin d'une séance par semaine."""
    f = Fabrique(nb_semaines=nb_semaines, creneaux=creneaux)
    f.salle("N101")
    f.salle("N102")
    f.besoin([f.groupe("A1")], [f.enseignant("Mme Martin")], **besoin)
    return f


# --------------------------------------------------------------------------- #
# Instanciation
# --------------------------------------------------------------------------- #


def test_une_occurrence_par_semaine_active():
    instantane = une_classe().instantane()
    resultat = instancier(instantane, [place("1-1", MARDI, C2, 1)])
    assert resultat.a_replacer == ()
    assert [o.date for o in resultat.occurrences] == [
        RENTREE + dt.timedelta(days=1) + n * SEMAINE for n in range(3)
    ]
    for occurrence in resultat.occurrences:
        assert (occurrence.debut, occurrence.fin, occurrence.salle_id) == (C2, C2 + 2 * H, 1)
        assert (occurrence.besoin_id, occurrence.seance_id) == (1, "1-1")
        assert occurrence.statut is StatutOccurrence.PLANIFIEE and occurrence.id is None
    verifier_dates(instantane, resultat.occurrences)


def test_placement_d_une_seance_inconnue():
    with pytest.raises(ErreurIntrouvable):
        instancier(une_classe().instantane(), [place("9-1", MARDI, C2, 1)])


def test_jour_ferme_signale_a_replacer():
    f = une_classe()
    f.jour_ferme(RENTREE + SEMAINE)  # le lundi de la deuxième semaine
    instantane = f.instantane()
    resultat = instancier(instantane, [place("1-1", LUNDI, C1, 1)])
    assert len(resultat.occurrences) == 2 and resultat.nb_occurrences == 3
    (revoir,) = resultat.a_replacer
    assert revoir.occurrence.date == RENTREE + SEMAINE
    assert revoir.code is C.JOUR_FERME
    assert "jour fermé" in revoir.raison and "14/09/2026" in revoir.raison
    assert instancier(instantane, [place("1-1", LUNDI, C1, 1)], [C.JOUR_FERME]).a_replacer == ()


def test_fermeture_propre_a_un_autre_groupe_n_empeche_rien():
    f = une_classe()
    autre = f.groupe("B1")
    f.jour_ferme(RENTREE, groupes=[autre])
    resultat = instancier(f.instantane(), [place("1-1", LUNDI, C1, 1)])
    assert resultat.a_replacer == ()


def test_indisponibilites_datees_signalees_a_replacer():
    f = une_classe()
    f.indisponibilite(CibleIndisponibilite.ENSEIGNANT, 1, date_debut=RENTREE)
    f.indisponibilite(CibleIndisponibilite.SALLE, 1, date_debut=RENTREE + SEMAINE, fin=9 * H)
    f.indisponibilite(CibleIndisponibilite.GROUPE, 1, date_debut=RENTREE + 2 * SEMAINE, dure=False)
    instantane = f.instantane()
    resultat = instancier(instantane, [place("1-1", LUNDI, C1, 1)])
    assert [(r.occurrence.date, r.code) for r in resultat.a_replacer] == [
        (RENTREE, C.DISPO_ENSEIGNANT),
        (RENTREE + SEMAINE, C.DISPO_SALLE),
    ]
    assert "Mme Martin est indisponible le 07/09/2026" in resultat.a_replacer[0].raison
    assert "la salle N101 est indisponible" in resultat.a_replacer[1].raison
    # L'indisponibilité souple du groupe (préférence) n'empêche rien.
    assert [o.date for o in resultat.occurrences] == [RENTREE + 2 * SEMAINE]


# --------------------------------------------------------------------------- #
# Replacement
# --------------------------------------------------------------------------- #


def instancier_et_replacer(f: Fabrique, placements, options=None, arret=None):
    instantane = f.instantane()
    instancie = instancier(instantane, placements)
    resultat = replacer(
        instantane,
        instancie.occurrences,
        [revoir.occurrence for revoir in instancie.a_replacer],
        options,
        arret,
    )
    assert len(resultat.replacements) == len(instancie.a_replacer)
    placees = [r.occurrence for r in resultat.replacees]
    verifier_dates(instantane, [*instancie.occurrences, *placees])
    return instantane, instancie, resultat


def test_jour_ferie_l_occurrence_est_replacee_dans_la_meme_semaine():
    f = une_classe()
    ferie = RENTREE + SEMAINE
    f.jour_ferme(ferie)
    _, _, resultat = instancier_et_replacer(f, [place("1-1", LUNDI, C1, 1)])
    (replacement,) = resultat.replacements
    nouvelle = replacement.occurrence
    assert replacement.replacee and replacement.raison == ""
    assert nouvelle.statut is StatutOccurrence.DEPLACEE
    assert nouvelle.date == ferie + dt.timedelta(days=1)  # le mardi : au plus près
    assert (nouvelle.debut, nouvelle.salle_id) == (C1, 1)  # même heure, même salle
    assert replacement.origine.date == ferie
    assert replacement.cout == 10
    assert resultat.a_programmer == () and resultat.statistiques["nb_replacees"] == 1


def test_replacement_evite_les_occurrences_en_place():
    """Le mardi est pris par le même groupe à 8 h 30 : la séance replacée prend un autre créneau."""
    f = une_classe(nombre=2)
    ferie = RENTREE + SEMAINE
    f.jour_ferme(ferie)
    _, instancie, resultat = instancier_et_replacer(
        f, [place("1-1", LUNDI, C1, 1), place("1-2", MARDI, C1, 1)]
    )
    (replacement,) = resultat.replacements
    nouvelle = replacement.occurrence
    assert nouvelle.date == ferie + dt.timedelta(days=1)
    assert nouvelle.debut == C2  # 8 h 30 est pris ; 10 h 45 respecte la pause


def test_deux_occurrences_replacees_ne_se_marchent_pas_dessus():
    f = Fabrique(nb_semaines=2, creneaux=grille(jours=[LUNDI, MARDI], samedi=False))
    f.salle("N101")
    f.besoin([f.groupe("A1")], [f.enseignant()], nombre=4)
    f.jour_ferme(RENTREE)
    _, _, resultat = instancier_et_replacer(
        f, [place(f"1-{rang}", LUNDI, debut, 1) for rang, debut in enumerate((C1, C2, C3), 1)]
    )
    nouvelles = [r.occurrence for r in resultat.replacees]
    assert len(nouvelles) == 3
    assert len({(o.date, o.debut) for o in nouvelles}) == 3
    assert {o.date for o in nouvelles} == {RENTREE + dt.timedelta(days=1)}


def test_salle_indisponible_a_une_date_meme_creneau_autre_salle():
    f = une_classe()
    f.indisponibilite(CibleIndisponibilite.SALLE, 1, date_debut=RENTREE)
    _, _, resultat = instancier_et_replacer(f, [place("1-1", LUNDI, C1, 1)])
    (replacement,) = resultat.replacements
    nouvelle = replacement.occurrence
    assert (nouvelle.date, nouvelle.debut, nouvelle.salle_id) == (RENTREE, C1, 2)
    assert replacement.cout == 0 and nouvelle.statut is StatutOccurrence.DEPLACEE


def test_impossible_a_replacer_passe_a_programmer():
    """Jour imposé, un seul créneau par semaine : l'autre lundi est déjà pris par la même classe."""
    f = une_classe(nb_semaines=2, creneaux=UN_CRENEAU, jour_impose=LUNDI)
    f.jour_ferme(RENTREE)
    instantane, instancie, resultat = instancier_et_replacer(f, [place("1-1", LUNDI, C1, 1)])
    (replacement,) = resultat.replacements
    assert not replacement.replacee and replacement.cout is None
    assert replacement.occurrence.statut is StatutOccurrence.A_PROGRAMMER
    assert replacement.occurrence.date == RENTREE  # la date d'origine reste la référence
    assert "aucune place libre" in replacement.raison
    assert "l'emploi du temps de ses groupes ou de ses enseignants" in replacement.raison
    assert resultat.a_programmer == (replacement,) and resultat.replacees == ()
    assert resultat.statistiques["nb_a_programmer"] == 1


def test_semaine_voisine_quand_la_semaine_est_pleine():
    """Semaine fermée du mardi au vendredi : la séance du lundi férié part la semaine suivante."""
    f = une_classe(nb_semaines=3, creneaux=grille(jours=[LUNDI, MARDI], samedi=False))
    f.jour_ferme(RENTREE + SEMAINE, RENTREE + SEMAINE + dt.timedelta(days=1))
    _, _, resultat = instancier_et_replacer(f, [place("1-1", LUNDI, C1, 1)])
    (replacement,) = resultat.replacements
    assert replacement.replacee
    ecart = abs((replacement.occurrence.date - replacement.origine.date).days)
    assert ecart >= 6 and replacement.cout >= 100


def test_fenetre_de_dates():
    f = une_classe()
    ferie = RENTREE + SEMAINE
    f.jour_ferme(ferie)
    jeudi = ferie + dt.timedelta(days=JEUDI)
    _, _, resultat = instancier_et_replacer(
        f, [place("1-1", LUNDI, C1, 1)], OptionsReplacement(date_debut=jeudi, date_fin=jeudi)
    )
    assert resultat.replacements[0].occurrence.date == jeudi
    with pytest.raises(ErreurDonneesInvalides):
        OptionsReplacement(date_debut=jeudi, date_fin=ferie)


def test_arret_rien_n_est_perdu():
    f = une_classe()
    f.jour_ferme(RENTREE)
    arret = threading.Event()
    arret.set()
    _, instancie, resultat = instancier_et_replacer(f, [place("1-1", LUNDI, C1, 1)], arret=arret)
    assert resultat.arrete
    (replacement,) = resultat.replacements
    assert replacement.occurrence.statut is StatutOccurrence.A_PROGRAMMER
    assert "interrompu" in replacement.raison


def test_replacement_respecte_le_maximum_journalier():
    f = Fabrique(nb_semaines=1, creneaux=grille(jours=[LUNDI, MARDI, JEUDI], samedi=False))
    f.salle("N101")
    f.salle("N102")
    martin = f.enseignant("Mme Martin", max_jour=2 * H)
    f.besoin([f.groupe("A1")], [martin])
    f.besoin([f.groupe("A2")], [martin])
    f.jour_ferme(RENTREE)
    _, _, resultat = instancier_et_replacer(
        f, [place("1-1", LUNDI, C1, 1), place("2-1", MARDI, C1, 1)]
    )
    (replacement,) = resultat.replacements
    # Le mardi, Mme Martin a déjà ses deux heures : la séance part au jeudi.
    assert replacement.occurrence.date == RENTREE + dt.timedelta(days=JEUDI)
