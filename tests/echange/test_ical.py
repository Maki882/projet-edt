"""Adaptateur iCal : événements simples, récurrences, exceptions, remplacements, changement d'heure."""

from __future__ import annotations

import datetime as dt

from edt.domaine.enumerations import Gravite
from edt.echange.format import StatutSeance
from edt.echange.ical import lire_ical

SEMESTRE = (dt.date(2026, 9, 7), dt.date(2027, 1, 29))


def _sans_retrait(texte: str) -> str:
    """Retire le retrait des lignes (en iCal, une ligne qui commence par une espace prolonge la précédente)."""
    return "\n".join(ligne.strip() for ligne in texte.strip().splitlines())


def _calendrier(*evenements: str) -> str:
    corps = "\n".join(
        f"BEGIN:VEVENT\nUID:e{i}@test\nDTSTAMP:20260901T000000Z\n{_sans_retrait(texte)}\nEND:VEVENT"
        for i, texte in enumerate(evenements)
    )
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//essai//FR\n{corps}\nEND:VCALENDAR\n"


def _lire(*evenements: str, periode=SEMESTRE):
    return lire_ical("agenda.ics", _calendrier(*evenements), periode)


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
    texte = _calendrier(
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
