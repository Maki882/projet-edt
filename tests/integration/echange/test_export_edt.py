"""Export d'un emploi du temps : CSV et iCal, par ressource, relu par la bibliothèque icalendar."""

from __future__ import annotations

import datetime as dt

import pytest
from icalendar import Calendar
from sqlalchemy import select
from tests.outils.echange.export_edt import exporter_edt as _exporter
from tests.outils.echange.export_edt import lignes_csv as _lignes

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable


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
