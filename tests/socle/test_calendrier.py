"""Calendrier (Pâques, fériés, semaines) et conversions d'heures."""

from __future__ import annotations

import datetime as dt

import pytest

from edt.domaine.calendrier import (
    decoupage_par_defaut,
    jours_feries,
    jours_feries_entre,
    paques,
    semaine_de,
    semaines_entre,
)
from edt.domaine.heures import (
    duree_vers_texte,
    minutes_vers_hhmm,
    minutes_vers_texte,
    se_chevauchent,
    texte_vers_minutes,
)
from edt.erreurs import ErreurDonneesInvalides


@pytest.mark.parametrize(
    ("annee", "attendu"),
    [
        (2024, dt.date(2024, 3, 31)),
        (2025, dt.date(2025, 4, 20)),
        (2026, dt.date(2026, 4, 5)),
        (2027, dt.date(2027, 3, 28)),
        (2038, dt.date(2038, 4, 25)),  # date la plus tardive possible
    ],
)
def test_paques(annee, attendu):
    assert paques(annee) == attendu


def test_feries_mobiles_et_fixes_d_une_annee():
    feries = jours_feries(2027)
    assert len(feries) == 11
    assert feries[dt.date(2027, 3, 29)] == "Lundi de Pâques"
    assert feries[dt.date(2027, 5, 6)] == "Ascension"
    assert feries[dt.date(2027, 5, 17)] == "Lundi de Pentecôte"
    assert feries[dt.date(2027, 7, 14)] == "Fête nationale"


def test_feries_d_une_periode_a_cheval_sur_deux_annees():
    feries = jours_feries_entre(dt.date(2026, 12, 1), dt.date(2027, 1, 31))
    assert [jour for jour, _ in feries] == [dt.date(2026, 12, 25), dt.date(2027, 1, 1)]


def test_semaines_d_un_semestre():
    decoupage = decoupage_par_defaut(2026)
    assert decoupage.semestre_1 == (dt.date(2026, 9, 7), dt.date(2027, 1, 29))

    semaines = semaines_entre(*decoupage.semestre_1)
    assert len(semaines) == 21
    assert [s.numero for s in semaines] == list(range(1, 22))
    assert semaines[0].lundi == dt.date(2026, 9, 7)
    assert semaines[0].date_du(3) == dt.date(2026, 9, 10)  # jeudi de la première semaine
    assert semaine_de(dt.date(2026, 11, 19), semaines).numero == 11
    assert semaine_de(dt.date(2027, 3, 1), semaines) is None


def test_semaines_d_une_periode_qui_commence_en_milieu_de_semaine():
    semaines = semaines_entre(dt.date(2026, 9, 9), dt.date(2026, 9, 15))
    assert [s.lundi for s in semaines] == [dt.date(2026, 9, 7), dt.date(2026, 9, 14)]


def test_conversions_d_heures():
    assert minutes_vers_texte(510) == "8 h 30"
    assert minutes_vers_texte(1080) == "18 h 00"
    assert minutes_vers_hhmm(510) == "08:30"
    assert duree_vers_texte(120) == "2 h"
    assert duree_vers_texte(90) == "1 h 30"
    for texte in ("8 h 30", "8h30", "08:30", "8H30"):
        assert texte_vers_minutes(texte) == 510
    assert texte_vers_minutes("18 h") == 1080


@pytest.mark.parametrize("texte", ["", "25:00", "8h75", "midi", "830"])
def test_heure_invalide_refusee(texte):
    with pytest.raises(ErreurDonneesInvalides):
        texte_vers_minutes(texte)


def test_deux_plages_contigues_ne_se_chevauchent_pas():
    assert not se_chevauchent(510, 630, 630, 750)
    assert se_chevauchent(510, 630, 600, 720)
