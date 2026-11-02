"""Dépliage des besoins en séances hebdomadaires (les trois modes de volume)."""

from __future__ import annotations

import datetime as dt
import random

import pytest

from edt.domaine.calendrier import Semaine, semaines_entre
from edt.domaine.contraintes import CodeContrainte
from edt.domaine.depliage import deplier, repartition_reguliere, semaines_ouvertes
from edt.domaine.enumerations import ModeVolume, NatureJourFerme
from edt.domaine.instantane import Besoin, JourFerme

# Quinze semaines, du lundi 7 septembre au vendredi 18 décembre 2026.
SEMAINES = semaines_entre(dt.date(2026, 9, 7), dt.date(2026, 12, 18))
TOUSSAINT = JourFerme(
    dt.date(2026, 10, 26), dt.date(2026, 11, 1), "Toussaint", NatureJourFerme.VACANCES
)  # semaine 8, entièrement fermée
ARMISTICE = JourFerme(
    dt.date(2026, 11, 11), dt.date(2026, 11, 11), "Armistice", NatureJourFerme.FERIE
)  # mercredi de la semaine 10


def besoin(identifiant: int = 1, **champs) -> Besoin:
    valeurs = dict(
        id=identifiant,
        libelle=f"Besoin {identifiant}",
        matiere_id=1,
        matiere_code="IM.1101",
        type_seance_id=1,
        type_seance_code="TD",
        samedi_autorise=False,
        duree=120,
        groupes=frozenset({10}),
        enseignants=frozenset({1}),
        effectif=30,
        accessibilite_requise=False,
    )
    valeurs.update(champs)
    return Besoin(**valeurs)


def test_par_semaine_une_seance_par_rang_sur_toutes_les_semaines_ouvertes():
    resultat = deplier([besoin(nombre=2)], SEMAINES, [TOUSSAINT, ARMISTICE])

    assert [s.id for s in resultat.seances] == ["1-1", "1-2"]
    attendues = frozenset(range(1, 16)) - {8}  # la semaine entièrement fermée disparaît
    assert all(s.semaines == attendues for s in resultat.seances)
    assert 10 in attendues  # un seul jour férié ne ferme pas la semaine
    assert resultat.problemes == ()


def test_par_semaine_limite_a_la_fenetre_du_besoin():
    fenetre = besoin(date_debut=dt.date(2026, 10, 5), date_fin=dt.date(2026, 10, 23))
    resultat = deplier([fenetre], SEMAINES, [])
    assert resultat.seances[0].semaines == frozenset({5, 6, 7})


def test_total_reparti_regulierement():
    assert repartition_reguliere(10, 15) == [1, 1, 0] * 5
    assert repartition_reguliere(20, 15) == [2, 1, 1] * 5

    total = besoin(mode_volume=ModeVolume.TOTAL, nombre=5, max_par_semaine=1)
    resultat = deplier([total], SEMAINES, [])

    (seance,) = resultat.seances
    assert seance.semaines == frozenset({1, 4, 7, 10, 13})  # une semaine sur trois, dès la première
    assert resultat.problemes == ()


def test_total_avec_plusieurs_seances_par_semaine():
    total = besoin(mode_volume=ModeVolume.TOTAL, nombre=20, max_par_semaine=2)
    resultat = deplier([total], SEMAINES, [])

    rang_1, rang_2 = resultat.seances
    assert rang_1.semaines == frozenset(range(1, 16))
    assert rang_2.semaines == frozenset({1, 4, 7, 10, 13})
    assert sum(len(s.semaines) for s in resultat.seances) == 20


def test_total_impossible_est_signale_sans_etre_corrige():
    trop = besoin(mode_volume=ModeVolume.TOTAL, nombre=20, max_par_semaine=1)
    resultat = deplier([trop], SEMAINES, [])

    (probleme,) = resultat.problemes
    assert probleme.besoin_id == 1
    assert probleme.code is CodeContrainte.VOLUME
    assert "20 séances demandées" in probleme.message
    # Ce qui tient est déplié : 15 semaines à une séance.
    assert sum(len(s.semaines) for s in resultat.seances) == 15


def test_ponctuel_actif_la_seule_semaine_de_sa_date():
    dst = besoin(
        mode_volume=ModeVolume.PONCTUEL,
        date_ponctuelle=dt.date(2026, 11, 19),
        debut_impose=825,
    )
    resultat = deplier([dst], SEMAINES, [TOUSSAINT])

    (seance,) = resultat.seances
    assert seance.semaines == frozenset({11})
    assert seance.jour_impose == 3  # un jeudi
    assert seance.debut_impose == 825
    assert resultat.problemes == ()


def test_ponctuel_hors_semestre_ou_un_jour_ferme_est_signale():
    hors_semestre = besoin(1, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=dt.date(2027, 3, 1))
    jour_ferie = besoin(2, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=dt.date(2026, 11, 11))
    resultat = deplier([hors_semestre, jour_ferie], SEMAINES, [ARMISTICE])

    codes = {p.besoin_id: p.code for p in resultat.problemes}
    assert codes == {1: CodeContrainte.VOLUME, 2: CodeContrainte.JOUR_FERME}
    assert [s.id for s in resultat.seances] == ["2-1"]  # la séance hors semestre n'existe pas


def test_semaine_d_examens_ferme_les_cours_mais_pas_les_seances_ponctuelles():
    partiels = JourFerme(
        dt.date(2026, 12, 7),
        dt.date(2026, 12, 11),
        "Partiels",
        NatureJourFerme.EXAMENS,
        groupes=frozenset({10, 11}),
    )
    cours = besoin(1)
    autre_groupe = besoin(2, groupes=frozenset({20}))
    examen = besoin(3, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=dt.date(2026, 12, 8))
    resultat = deplier([cours, autre_groupe, examen], SEMAINES, [partiels])

    par_id = {s.id: s for s in resultat.seances}
    assert 14 not in par_id["1-1"].semaines  # le groupe en examens n'a pas cours
    assert 14 in par_id["2-1"].semaines  # la fermeture ne vise pas l'autre groupe
    assert par_id["3-1"].semaines == frozenset({14})  # l'examen, lui, a bien lieu
    assert resultat.problemes == ()


# --------------------------------------------------------------------------- #
# Non-régression : le calcul rapide des semaines ouvertes suit la règle à la lettre
# --------------------------------------------------------------------------- #


def semaines_ouvertes_de_reference(un_besoin, semaines, jours_fermes) -> tuple[Semaine, ...]:
    """La règle écrite au plus simple, jour par jour, avec `JourFerme.concerne`.

    C'est la définition (lente : une question par fermeture, par jour et par
    semaine). `semaines_ouvertes` doit toujours donner exactement ce résultat.
    """
    ouvertes = []
    for semaine in semaines:
        if un_besoin.date_debut is not None and semaine.dimanche < un_besoin.date_debut:
            continue
        if un_besoin.date_fin is not None and semaine.lundi > un_besoin.date_fin:
            continue
        entierement_fermee = all(
            any(
                ferme.concerne(
                    semaine.date_du(jour), un_besoin.groupes, ponctuelle=un_besoin.ponctuel
                )
                for ferme in jours_fermes
            )
            for jour in range(5)
        )
        if not entierement_fermee:
            ouvertes.append(semaine)
    return tuple(ouvertes)


def _fermeture_au_hasard(tirage: random.Random) -> JourFerme:
    lundi = SEMAINES[0].lundi + dt.timedelta(weeks=tirage.randint(-2, len(SEMAINES) + 1))
    debut = lundi + dt.timedelta(days=tirage.choice((0, 0, 0, 1, 2, 3, 4, 5, 6)))
    duree = tirage.choice((1, 1, 2, 3, 5, 5, 7, 12, 14, 40))
    return JourFerme(
        debut,
        debut + dt.timedelta(days=duree - 1),
        "Fermeture",
        tirage.choice(list(NatureJourFerme)),
        groupes=tirage.choice(
            (None, None, frozenset(tirage.sample(range(1, 7), tirage.randint(1, 3))))
        ),
    )


def _besoin_au_hasard(tirage: random.Random, identifiant: int) -> Besoin:
    champs: dict = {"groupes": frozenset(tirage.sample(range(1, 7), tirage.randint(1, 3)))}
    jour = SEMAINES[0].lundi + dt.timedelta(days=tirage.randint(-10, 7 * len(SEMAINES) + 10))
    if tirage.random() < 0.4:
        champs["date_debut"] = jour
    if tirage.random() < 0.4:
        champs["date_fin"] = jour + dt.timedelta(days=tirage.randint(0, 60))
    if tirage.random() < 0.3:
        champs.update(mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=jour)
    return besoin(identifiant, **champs)


def test_semaines_ouvertes_identiques_a_la_regle_de_reference():
    tirage = random.Random(20261005)
    avec_semaine_fermee = 0
    for numero in range(400):
        fermetures = [_fermeture_au_hasard(tirage) for _ in range(tirage.randint(0, 12))]
        un_besoin = _besoin_au_hasard(tirage, numero)
        # Le plus souvent le semestre entier ; parfois des semaines éparses, dans le désordre.
        semaines = list(SEMAINES)
        if numero % 5 == 0:
            semaines = tirage.sample(semaines, tirage.randint(0, len(semaines)))

        attendues = semaines_ouvertes_de_reference(un_besoin, semaines, fermetures)

        assert semaines_ouvertes(un_besoin, semaines, fermetures) == attendues, (
            un_besoin,
            fermetures,
        )
        dans_la_fenetre = semaines_ouvertes_de_reference(un_besoin, semaines, [])
        avec_semaine_fermee += len(attendues) < len(dans_la_fenetre)
    # Le tirage exerce bien la règle : des semaines entièrement fermées dans de nombreux cas.
    assert avec_semaine_fermee > 100


def test_le_depliage_complet_suit_la_regle_de_reference():
    tirage = random.Random(5102026)
    for _ in range(40):
        fermetures = [_fermeture_au_hasard(tirage) for _ in range(tirage.randint(0, 12))]
        besoins = [_besoin_au_hasard(tirage, numero) for numero in range(1, 13)]
        resultat = deplier(besoins, SEMAINES, fermetures)

        par_besoin = {s.besoin.id: s for s in resultat.seances}
        sans_semaine = {p.besoin_id for p in resultat.problemes}
        for un_besoin in besoins:
            if un_besoin.ponctuel:
                continue
            attendues = semaines_ouvertes_de_reference(un_besoin, SEMAINES, fermetures)
            if not attendues:
                assert un_besoin.id in sans_semaine and un_besoin.id not in par_besoin
            else:
                assert par_besoin[un_besoin.id].semaines == {s.numero for s in attendues}


# --------------------------------------------------------------------------- #
# Cas limites : ponctuel hors des dates du semestre, besoin sans groupe
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_ponctuel_hors_des_dates_du_semestre_est_un_probleme_de_depliage():
    """Cas : le semestre commence un mercredi ; une séance ponctuelle est datée du lundi de la
    même semaine (saisie avant un raccourcissement du semestre).

    Risque : le dépliage jugeait à la semaine et acceptait, le solveur plaçait, puis
    l'instanciation rejetait la date avec une raison vague : la cause réelle (hors semestre)
    doit être dite avant le calcul.
    """
    debut, fin = dt.date(2026, 9, 9), dt.date(2026, 12, 18)  # un mercredi
    semaines = semaines_entre(debut, fin)
    lundi_avant = dt.date(2026, 9, 7)
    ponctuel = besoin(1, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=lundi_avant)

    resultat = deplier([ponctuel], semaines, [], periode=(debut, fin))
    assert resultat.seances == ()
    (probleme,) = resultat.problemes
    assert probleme.code is CodeContrainte.VOLUME
    assert "en dehors du semestre" in probleme.message and "09/09/2026" in probleme.message
    # Sans les dates exactes, la semaine suffit (compatibilité des appelants) ; dans les dates,
    # la séance est dépliée.
    assert len(deplier([ponctuel], semaines, []).seances) == 1
    dans = besoin(2, mode_volume=ModeVolume.PONCTUEL, date_ponctuelle=dt.date(2026, 9, 10))
    assert len(deplier([dans], semaines, [], periode=(debut, fin)).seances) == 1


@pytest.mark.cas_limite
def test_un_besoin_sans_groupe_est_signale_et_non_deplie():
    """Cas : un besoin dont la liste des groupes est vide (import partiel, groupe retiré par un
    script) ; la base l'accepte, la conception non.

    Risque : déplié avec un effectif nul (toute salle convient), planifié comme si de rien
    n'était ; la demande incomplète doit être signalée, pas placée.
    """
    resultat = deplier([besoin(1, groupes=frozenset()), besoin(2)], SEMAINES, [])
    assert [s.id for s in resultat.seances] == ["2-1"]
    (probleme,) = resultat.problemes
    assert probleme.besoin_id == 1 and probleme.code is CodeContrainte.VOLUME
    assert "aucun groupe" in probleme.message
