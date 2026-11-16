"""Adaptateur iCal : événements simples, récurrences, exceptions, remplacements, changement d'heure."""

from __future__ import annotations

import datetime as dt
import itertools

import pytest
from tests.outils.echange.fabrique import calendrier_ical, erreurs, seul

from edt.domaine.enumerations import Gravite
from edt.echange import ical
from edt.echange.format import StatutSeance
from edt.echange.ical import lire_ical

SEMESTRE = (dt.date(2026, 9, 7), dt.date(2027, 1, 29))


def _lire(*evenements: str, periode=SEMESTRE):
    return lire_ical("agenda.ics", calendrier_ical(*evenements), periode)


def test_evenement_simple_et_ses_champs():
    seances, signalements = _lire(
        """
        DTSTART;TZID=Europe/Paris:20261005T083000
        DTEND;TZID=Europe/Paris:20261005T103000
        SUMMARY:Mathématiques
        X-EDT-MATIERE:IM.1101
        X-EDT-TYPE:CM
        X-EDT-GROUPES:A1; A1-ANG-B2
        X-EDT-ENSEIGNANTS:ENS001
        X-EDT-SALLE:N016
        """
    )
    assert signalements == []
    (seance,) = seances
    assert (seance.date, seance.debut, seance.fin) == (dt.date(2026, 10, 5), 510, 630)
    assert (seance.matiere, seance.type, seance.salle) == ("IM.1101", "CM", "N016")
    assert seance.groupes == ("A1", "A1-ANG-B2") and seance.enseignants == ("ENS001",)
    assert seance.ligne == 4 and seance.libelle == "Mathématiques" and not seance.recurrente


def test_categories_et_lieu_a_defaut_des_proprietes_edt():
    (seance,), _ = _lire(
        """
        DTSTART;TZID=Europe/Paris:20261005T083000
        DURATION:PT1H30M
        CATEGORIES:TD
        LOCATION:N204
        """
    )
    assert (seance.type, seance.salle, seance.fin) == ("TD", "N204", 600)


def test_regle_de_recurrence_et_exception():
    seances, signalements = _lire(
        """
        DTSTART;TZID=Europe/Paris:20261005T083000
        DTEND;TZID=Europe/Paris:20261005T103000
        RRULE:FREQ=WEEKLY;COUNT=4
        EXDATE;TZID=Europe/Paris:20261012T083000
        """
    )
    assert [s.date for s in seances] == [
        dt.date(2026, 10, 5),
        dt.date(2026, 10, 19),
        dt.date(2026, 10, 26),
    ]
    assert all(s.recurrente for s in seances)
    assert seances[1].precision == "occurrence du 19/10/2026"
    assert [s.gravite for s in signalements] == [Gravite.INFORMATION]


def test_recurrence_garde_l_heure_locale_au_changement_d_heure():
    """Un semestre qui enjambe le passage à l'heure d'hiver (25/10/2026) : 8 h 30 avant et après."""
    seances, _ = _lire(
        """
        DTSTART;TZID=Europe/Paris:20261019T083000
        DTEND;TZID=Europe/Paris:20261019T103000
        RRULE:FREQ=WEEKLY;UNTIL=20261102T235959Z
        """
    )
    assert [(s.date, s.debut, s.fin) for s in seances] == [
        (dt.date(2026, 10, 19), 510, 630),
        (dt.date(2026, 10, 26), 510, 630),
        (dt.date(2026, 11, 2), 510, 630),
    ]


def test_heures_utc_converties_en_heure_de_paris_avant_et_apres_le_changement():
    seances, _ = _lire(
        "DTSTART:20261020T063000Z\nDTEND:20261020T083000Z",  # heure d'été : UTC+2
        "DTSTART:20261027T073000Z\nDTEND:20261027T093000Z",  # heure d'hiver : UTC+1
    )
    assert [(s.date, s.debut) for s in seances] == [
        (dt.date(2026, 10, 20), 510),
        (dt.date(2026, 10, 27), 510),
    ]


def test_heure_flottante_prise_comme_heure_locale():
    (seance,), _ = _lire("DTSTART:20261005T134500\nDTEND:20261005T154500")
    assert (seance.debut, seance.fin) == (825, 945)


def test_occurrence_remplacee_compte_a_sa_position_finale():
    texte = calendrier_ical(
        """
        DTSTART;TZID=Europe/Paris:20261005T083000
        DTEND;TZID=Europe/Paris:20261005T103000
        RRULE:FREQ=WEEKLY;COUNT=3
        X-EDT-MATIERE:IM.1101
        """
    ).replace("UID:e0@test", "UID:serie@test")
    remplacement = (
        "BEGIN:VEVENT\nUID:serie@test\nDTSTAMP:20260901T000000Z\n"
        "RECURRENCE-ID;TZID=Europe/Paris:20261012T083000\n"
        "DTSTART;TZID=Europe/Paris:20261013T160000\nDTEND;TZID=Europe/Paris:20261013T180000\n"
        "END:VEVENT\n"
    )
    texte = texte.replace("END:VCALENDAR", remplacement + "END:VCALENDAR")

    seances, _ = lire_ical("agenda.ics", texte, SEMESTRE)

    assert [(s.date, s.debut, s.statut) for s in seances] == [
        (dt.date(2026, 10, 5), 510, None),
        (dt.date(2026, 10, 13), 960, StatutSeance.DEPLACEE),
        (dt.date(2026, 10, 19), 510, None),
    ]
    assert seances[1].matiere == "IM.1101"  # champs repris de la série
    assert not seances[1].recurrente


def test_evenement_annule():
    (seance,), _ = _lire("DTSTART:20261005T083000\nDTEND:20261005T103000\nSTATUS:CANCELLED")
    assert seance.statut is StatutSeance.ANNULEE


def test_journee_entiere_et_evenement_sans_fin_non_importes():
    seances, signalements = _lire("DTSTART;VALUE=DATE:20261111", "DTSTART:20261005T083000")
    assert seances == []
    assert [s.gravite for s in signalements] == [Gravite.ERREUR, Gravite.ERREUR]
    assert "journée entière" in signalements[0].message
    assert "heure de fin" in signalements[1].message


def test_recurrence_sans_fin_bornee_par_le_semestre():
    seances, _ = _lire(
        "DTSTART;TZID=Europe/Paris:20261005T083000\nDTEND;TZID=Europe/Paris:20261005T103000\nRRULE:FREQ=WEEKLY"
    )
    assert seances[-1].date == dt.date(2027, 1, 25)
    assert all(SEMESTRE[0] <= s.date <= SEMESTRE[1] for s in seances)


def test_propriete_illisible_signalee_sans_arreter_la_lecture():
    seances, signalements = _lire(
        "DTSTART:demain\nDTEND:20261005T103000", "DTSTART:20261005T083000\nDTEND:20261005T103000"
    )
    assert len(seances) == 1
    assert signalements[0].gravite is Gravite.ERREUR and "illisible" in signalements[0].message


def test_fichier_illisible():
    seances, signalements = lire_ical(
        "cassé.ics", "BEGIN:VCALENDAR\nceci n'est pas de l'iCal", SEMESTRE
    )
    assert seances == [] and signalements[0].gravite is Gravite.ERREUR


# --------------------------------------------------------------------------- #
# Cas limites de la lecture iCal (banque de tests)
# --------------------------------------------------------------------------- #

SIMPLE = """
    DTSTART:20261006T083000
    DTEND:20261006T103000
    SUMMARY:Séance témoin, toujours lue
"""
"""Un événement ordinaire du mardi 6 octobre, placé à côté de l'événement à l'essai."""


def _recurrent(regle: str) -> str:
    """Un événement du lundi 5 octobre 2026, 8 h 30 - 10 h 30, répété selon `regle`."""
    return f"DTSTART:20261005T083000\nDTEND:20261005T103000\nRRULE:{regle}"


@pytest.mark.cas_limite
def test_fuseau_inconnu_signale_en_avertissement():
    """Cas : DTSTART et DTEND portent TZID=Mars/Phobos, fuseau inconnu et sans VTIMEZONE.

    Risque : heure prise pour une heure de Paris sans un mot : séances décalées
    de plusieurs heures si l'agenda venait d'un autre fuseau.
    """
    seances, signalements = _lire(
        "DTSTART;TZID=Mars/Phobos:20261005T083000\nDTEND;TZID=Mars/Phobos:20261005T103000",
        "DTSTART;TZID=Mars/Phobos:20261006T083000\nDTEND;TZID=Mars/Phobos:20261006T103000",
    )

    assert [(s.date, s.debut) for s in seances] == [
        (dt.date(2026, 10, 5), 8 * 60 + 30),
        (dt.date(2026, 10, 6), 8 * 60 + 30),
    ]
    avertissement = seul(signalements, Gravite.AVERTISSEMENT)
    assert "« Mars/Phobos » inconnu (2 événement(s))" in avertissement.message
    assert "heures de Paris" in avertissement.message
    _, connu = _lire(
        "DTSTART;TZID=Europe/Paris:20261005T083000\nDTEND;TZID=Europe/Paris:20261005T103000"
    )
    assert connu == []  # un fuseau connu ne donne aucun avertissement


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("debut", "regle", "attendues"),
    [("19980907", "FREQ=DAILY", 145), ("18000908", "FREQ=WEEKLY", 21)],
    ids=["quotidienne-depuis-1998", "hebdomadaire-depuis-1800"],
)
def test_recurrence_ancienne_bornee_par_la_periode_sans_refus(debut, regle, attendues):
    """Cas : une série quotidienne depuis 1998 ou hebdomadaire depuis 1800, lue sur 2026-2027.

    Risque : les occurrences antérieures à la période compteraient dans la borne de
    10 000 pas : « trop d'occurrences », et les séances du semestre seraient perdues.
    """
    seances, signalements = _lire(f"DTSTART:{debut}T083000\nDTEND:{debut}T103000\nRRULE:{regle}")

    assert len(seances) == attendues
    assert all(SEMESTRE[0] <= s.date <= SEMESTRE[1] for s in seances)
    assert [s.gravite for s in signalements] == [Gravite.INFORMATION]


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("regle", "attendu"),
    [
        ("FREQ=DAILY;INTERVAL=0;BYMONTH=2", "intervalle 0"),  # sinon : boucle sans fin
        ("FREQ=DAILY;INTERVAL=-1", "intervalle -1"),  # sinon : ValueError hors du filet
        ("FREQ=SECONDLY;BYMONTH=2;BYMONTHDAY=31", "répétition « SECONDLY »"),
        ("FREQ=HOURLY", "répétition « HOURLY »"),
        ("FREQ=DAILY;BYMONTH=13", "BYMONTH=13 n'existe pas"),
        ("FREQ=DAILY;BYMONTHDAY=40", "BYMONTHDAY=40 n'existe pas"),
        ("FREQ=MONTHLY;BYMONTH=2;BYMONTHDAY=30,31", "comme un 31 février"),
        ("FREQ=DAILY;BYSETPOS=2", "BYSETPOS dans une répétition quotidienne"),
        ("FREQ=MONTHLY;BYDAY=9MO", "BYDAY=9MO n'existe pas"),  # sinon : IndexError
        ("FREQ=DAILY;INTERVAL=7;BYDAY=TU", "aucune date possible"),  # début un lundi
    ],
    ids=[
        "intervalle-nul",
        "intervalle-negatif",
        "a-la-seconde-impossible",
        "a-l-heure",
        "mois-13",
        "jour-40",
        "31-fevrier",
        "position-quotidienne",
        "9e-lundi",
        "tous-les-7-jours-autre-jour",
    ],
)
def test_regle_de_recurrence_hostile_refusee_avant_tout_calcul(regle, attendu, monkeypatch):
    """Cas : une règle à intervalle nul, trop fine ou sans occurrence possible (fichier abîmé ou hostile).

    Risque : dateutil chercherait sans fin (INTERVAL=0) ou des secondes durant une
    occurrence inexistante, base réservée ; d'autres formes feraient planter tout
    l'import (ValueError, IndexError).
    """

    def interdit(*_arguments, **_options):
        raise AssertionError("la règle devait être refusée avant le calcul par dateutil")

    monkeypatch.setattr(ical, "rrulestr", interdit)

    seances, signalements = _lire(_recurrent(regle), SIMPLE)

    assert [s.date for s in seances] == [dt.date(2026, 10, 6)]  # le reste du fichier est lu
    assert attendu in seul(signalements, Gravite.ERREUR).message


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("regle", "attendues"),
    [
        ("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE", 18),
        ("FREQ=MONTHLY;BYDAY=1MO", 4),
        ("FREQ=DAILY;INTERVAL=7;BYDAY=MO", 17),
        ("FREQ=MONTHLY;BYMONTHDAY=31", 3),  # le 5 octobre, puis les 31 octobre et décembre
        ("FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=29", 1),  # rare mais possible : admise
        ("FREQ=WEEKLY;BYMONTH=10,11", 9),
    ],
)
def test_regle_de_cours_ordinaire_toujours_admise(regle, attendues):
    """Cas : des règles de cours ordinaires ou rares (tous les quinze jours, le 1er lundi, le 29 février).

    Risque : la vérification des règles hostiles, trop large, refuserait des
    séries légitimes et des séances seraient perdues.
    """
    seances, signalements = _lire(_recurrent(regle))

    assert len(seances) == attendues
    assert erreurs(signalements) == []


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    "proprietes",
    [
        "DTSTART:20261005T083000\nDURATION:P99999999W",
        "DTSTART:99991231T230000Z\nDTEND:99991231T233000Z",
        "DTSTART:20261005T083000\nDTEND:20261005T103000\nRECURRENCE-ID:illisible",
        "DTSTART:20261005T083000\nDTEND:20261005T103000\nRECURRENCE-ID:99991231T230000Z",
    ],
    ids=["duree-immense", "an-10000-a-paris", "recurrence-id-illisible", "recurrence-id-an-10000"],
)
def test_evenement_aux_dates_incalculables_ecarte_sans_arreter_la_lecture(proprietes):
    """Cas : un événement à la durée immense, au-delà de l'an 9999 à Paris, ou au RECURRENCE-ID illisible.

    Risque : OverflowError ou erreur d'icalendar non capturée : tout l'import
    planté (« erreur inattendue ») à cause d'un seul événement.
    """
    seances, signalements = _lire(proprietes, SIMPLE)

    assert [s.date for s in seances] == [dt.date(2026, 10, 6)]
    assert len(erreurs(signalements)) == 1


@pytest.mark.cas_limite
def test_evenements_aux_dates_incalculables_comptes_en_une_ligne_de_journal(caplog):
    """Cas : un fichier hostile de nombreux événements aux dates incalculables.

    Risque : une ligne d'avertissement par événement, jusqu'à 50 000 lignes de journal pour
    un seul envoi ; le rapport, lui, garde une erreur par événement.
    """
    incalculables = ["DTSTART:20261005T083000\nDURATION:P99999999W"] * 3
    with caplog.at_level("WARNING", logger="edt.echange.ical"):
        _, signalements = _lire(*incalculables, SIMPLE)
    assert len(erreurs(signalements)) == 3
    assert len(caplog.records) == 1 and "3 événement(s)" in caplog.records[0].getMessage()


@pytest.mark.cas_limite
def test_fichier_aux_recurrences_trop_couteuses_refuse_en_entier(monkeypatch):
    """Cas : des règles qui, ensemble, épuisent le temps de calcul d'un fichier (DELAI_RECURRENCES).

    Risque : un fichier de quelques kilo-octets aux règles impossibles (près d'une
    seconde chacune) occuperait le serveur et réserverait la base des minutes durant.
    """
    secondes = itertools.count()  # horloge factice : une seconde de plus à chaque lecture
    monkeypatch.setattr(ical, "_horloge", lambda: next(secondes))
    regle = _recurrent("FREQ=WEEKLY")

    seances, signalements = _lire(regle, regle, regle, regle)

    assert seances == []
    message = seul(signalements, Gravite.ERREUR).message
    assert "Fichier iCal refusé" in message and "plus de 3 s de calcul" in message
