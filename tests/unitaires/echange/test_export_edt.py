"""Export d'un emploi du temps importé en mode référence : iCal et CSV datés, relus et réimportés.

Partie unitaire de `tests/integration/echange/test_export_edt.py` : l'export est
appelé par le service sur une version importée, sans calcul ni interface.
"""

from __future__ import annotations

import datetime as dt

import pytest
from icalendar import Calendar
from tests.outils.echange.export_edt import exporter_edt as _exporter
from tests.outils.echange.export_edt import lignes_csv as _lignes

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.services.echange import FichierEnvoye, ModeImport


def test_ical_garde_l_heure_locale_apres_le_changement_d_heure(version_reference):
    calendrier = Calendar.from_ical(_exporter(version_reference, "ics", "date").contenu)

    debuts = {e["DTSTART"].dt.date(): e["DTSTART"].dt for e in calendrier.walk("VEVENT")}
    avant, apres = debuts[dt.date(2026, 9, 7)], debuts[dt.date(2026, 10, 5)]
    assert (avant.hour, avant.minute) == (8, 30) and avant.utcoffset() == dt.timedelta(hours=2)
    deplacee = next(
        e for e in calendrier.walk("VEVENT") if e["DTSTART"].dt.date() == dt.date(2026, 10, 5)
    )
    assert str(deplacee["X-EDT-STATUT"]) == "deplacee" and str(deplacee["STATUS"]) == "CONFIRMED"
    assert apres.astimezone(dt.UTC).hour == 8  # 10 h 45 à Paris (UTC+2) = 8 h 45 UTC


def test_csv_des_seances_datees_se_reimporte_sans_doublon(version_reference, importer, jeu_minimal):
    fichier = _exporter(version_reference, "csv", "date")
    lignes = _lignes(fichier)
    assert len(lignes) == 7
    assert {ligne["statut"] for ligne in lignes} == {"planifiee", "deplacee"}

    rapport = importer(
        FichierEnvoye(fichier.nom, fichier.contenu),
        mode=ModeImport.A_REPLACER,
        semestre_id=jeu_minimal.semestre_id,
        simuler=True,
    )

    assert rapport.bilan("séances").nombre("retenues") == 7
    assert rapport.bilan("besoins").compteurs == {"inchanges": 3}


def test_ical_exporte_se_reimporte(version_reference, importer, jeu_minimal):
    fichier = _exporter(version_reference, "ics", "date")

    rapport = importer(
        FichierEnvoye(fichier.nom, fichier.contenu),
        mode=ModeImport.A_REPLACER,
        semestre_id=jeu_minimal.semestre_id,
        simuler=True,
    )

    assert not rapport.a_des_rejets, rapport.signalements
    assert rapport.bilan("besoins").compteurs == {"inchanges": 3}


# --------------------------------------------------------------------------- #
# Cas limites de l'export (banque de tests)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_ical_hebdo_tolere_un_semestre_raccourci_apres_la_version(version_reference, caplog):
    """Cas : la fin du semestre est avancée au 20/09/2026 après l'enregistrement d'une version.

    Risque : « Semaine 3 absent(e) » : page 404 « Élément introuvable » qui ne parle
    ni de la version ni des dates, alors que l'export CSV de la même version passe ; ou
    un avertissement par séance, des centaines de lignes de journal pour un export.
    """
    fin = dt.date(2026, 9, 20)
    with session_base() as session:
        version = session.get(m.VersionEdt, version_reference)
        session.get(m.Semestre, version.semestre_id).date_fin = fin

    with caplog.at_level("WARNING", logger="edt.echange.export_edt"):
        fichier = _exporter(version_reference, "ics", "hebdo")

    evenements = Calendar.from_ical(fichier.contenu).walk("VEVENT")
    assert evenements  # les séances des semaines encore dans le semestre restent
    for evenement in evenements:
        assert evenement.decoded("dtstart").date() <= fin
        limite = evenement.get("rrule", {}).get("UNTIL", [None])[0]
        assert limite is None or limite.date() <= fin
    avertissements = [message for message in caplog.messages if "hors du semestre" in message]
    assert len(avertissements) == 1  # une ligne par export, pas une par séance
    assert f"version {version_reference}" in avertissements[0]
    assert _lignes(_exporter(version_reference, "csv", "hebdo"))  # le CSV passe toujours
