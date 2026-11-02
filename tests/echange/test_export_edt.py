"""Export d'un emploi du temps : CSV et iCal, par ressource, relu par la bibliothèque icalendar."""

from __future__ import annotations

import csv
import datetime as dt
import io
from zoneinfo import ZoneInfo

import pytest
from icalendar import Calendar
from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services import echange as service
from edt.services.echange import FichierEnvoye, ModeImport

PARIS = ZoneInfo("Europe/Paris")


def _exporter(version_id: int, format_: str = "csv", couche: str = "hebdo", **filtre):
    with session_base() as session:
        return service.exporter_emploi_du_temps(session, version_id, format_, couche, **filtre)


def _lignes(fichier) -> list[dict[str, str]]:
    texte = fichier.contenu.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(texte), delimiter=";"))


def _identifiant(modele, **critere) -> int:
    with session_base() as session:
        return session.scalar(select(modele.id).filter_by(**critere))


def test_csv_de_la_semaine_type(version_generee):
    fichier = _exporter(version_generee)

    assert fichier.nom == "emploi-du-temps-v1-hebdo.csv"
    assert fichier.contenu.startswith(b"\xef\xbb\xbf")  # UTF-8 avec BOM : Excel l'ouvre bien
    lignes = _lignes(fichier)
    assert len(lignes) == 17
    premiere = lignes[0]
    assert list(premiere) == [
        "jour", "debut", "fin", "matiere", "matiere_libelle", "type", "libelle", "groupes",
        "enseignants", "enseignants_noms", "salle", "campus", "semaines", "verrou", "besoin",
    ]  # fmt: skip
    assert premiere["jour"] in ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi")
    assert premiere["salle"] and premiere["semaines"]


def test_filtres_par_groupe_enseignant_salle(version_generee):
    tout = _lignes(_exporter(version_generee))

    demi_groupe = _lignes(
        _exporter(version_generee, groupe_id=_identifiant(m.Groupe, code="A1-G1a"))
    )
    groupes = {ligne["groupes"] for ligne in demi_groupe}
    assert "A1" in groupes and "A1-G1a" in groupes  # sa promotion et lui-même
    assert groupes.isdisjoint({"A1-G2", "A1-G2a", "A1-G2b"})  # pas la classe voisine
    assert len(demi_groupe) < len(tout)

    martin = _lignes(
        _exporter(version_generee, enseignant_id=_identifiant(m.Enseignant, identifiant="ENS001"))
    )
    assert martin and all("ENS001" in ligne["enseignants"] for ligne in martin)

    salle = _identifiant(m.Salle, code="N016")
    assert all(
        ligne["salle"] == "N016" for ligne in _lignes(_exporter(version_generee, salle_id=salle))
    )
    assert (
        _exporter(version_generee, salle_id=salle).nom == "emploi-du-temps-v1-hebdo-salle-N016.csv"
    )


def test_au_plus_un_filtre(version_generee):
    with pytest.raises(ErreurDonneesInvalides, match="au plus un filtre"):
        _exporter(version_generee, groupe_id=1, salle_id=1)


@pytest.mark.parametrize(
    ("version", "format_", "couche", "erreur"),
    [
        (999, "csv", "hebdo", ErreurIntrouvable),
        (None, "pdf", "hebdo", ErreurDonneesInvalides),
        (None, "csv", "mois", ErreurDonneesInvalides),
    ],
)
def test_parametres_invalides(version_generee, version, format_, couche, erreur):
    with pytest.raises(erreur):
        _exporter(version or version_generee, format_, couche)


def test_ical_de_la_semaine_type_relu_par_icalendar(version_generee):
    fichier = _exporter(version_generee, "ics")

    calendrier = Calendar.from_ical(fichier.contenu)
    fuseaux = calendrier.walk("VTIMEZONE")
    assert [str(f["TZID"]) for f in fuseaux] == ["Europe/Paris"]
    evenements = calendrier.walk("VEVENT")
    assert len(evenements) == 17
    for evenement in evenements:
        debut = evenement["DTSTART"].dt
        assert debut.tzinfo is not None and str(debut.tzinfo) == "Europe/Paris"
        assert str(evenement["UID"]).startswith("seance-") and evenement.get("RRULE")
        assert evenement.get("LOCATION") and "Matière :" in str(evenement["DESCRIPTION"])
        assert evenement.get("X-EDT-MATIERE")
    # Les vacances de la Toussaint (semaine du 26/10) sont exclues de chaque série hebdomadaire.
    exclues = {d.date() for e in evenements for d in e.exdates}
    assert any(dt.date(2026, 10, 26) <= jour <= dt.date(2026, 10, 30) for jour in exclues)
    # Identifiants stables : deux exports successifs donnent les mêmes événements.
    second = Calendar.from_ical(_exporter(version_generee, "ics").contenu)
    assert [str(e["UID"]) for e in second.walk("VEVENT")] == [str(e["UID"]) for e in evenements]


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
    ni de la version ni des dates, alors que l'export CSV de la même version passe.
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
    assert any(
        f"version {version_reference}" in message and "hors du semestre" in message
        for message in caplog.messages
    )
    assert _lignes(_exporter(version_reference, "csv", "hebdo"))  # le CSV passe toujours
