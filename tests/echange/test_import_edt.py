"""Import d'un emploi du temps existant : trois modes et règles décidées par l'équipe."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import (
    Gravite,
    ModeVolume,
    OrigineOccurrence,
    OrigineVersion,
    StatutOccurrence,
)
from edt.erreurs import ErreurDonneesInvalides
from edt.services.echange import FichierEnvoye, ModeImport

FICHIER = "seances_existantes.csv"


@pytest.fixture
def importer_edt(jeu_minimal, importer, fichier_exemple):
    """Importe un fichier d'emploi du temps dans le semestre du jeu minimal."""

    def importer_edt_(mode: ModeImport, nom: str = FICHIER, **options):
        return importer(
            fichier_exemple(nom), mode=mode, semestre_id=jeu_minimal.semestre_id, **options
        )

    return importer_edt_


def _besoin(session, matiere: str, type_: str) -> m.Besoin:
    return session.scalar(
        select(m.Besoin)
        .join(m.Matiere)
        .join(m.TypeSeance)
        .where(m.Matiere.code == matiere, m.TypeSeance.code == type_)
    )


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def test_mode_reference(importer_edt, jeu_minimal):
    rapport = importer_edt(ModeImport.REFERENCE)

    assert rapport.bilan("séances").compteurs == {
        "lues": 11,
        "fusionnes": 1,
        "annulees": 1,
        "ecartees": 1,
        "retenues": 8,
    }
    assert rapport.bilan("enseignants fictifs").compteurs == {"crees": 2}
    assert rapport.bilan("besoins").compteurs == {"crees": 2, "mis_a_jour": 1, "inchanges": 1}
    assert rapport.bilan("séances hebdomadaires").compteurs == {"crees": 4}
    assert rapport.bilan("occurrences").compteurs == {"crees": 7}
    assert rapport.semaines_types[0].startswith(
        "Semaine type de A1 : semaine 1 (du lundi 07/09/2026)"
    )

    with session_base() as session:
        version = session.get(m.VersionEdt, rapport.version_id)
        assert version.origine == OrigineVersion.REFERENCE and not version.active
        occurrences = session.scalars(
            select(m.Occurrence).where(m.Occurrence.version_id == version.id)
        ).all()
        assert all(o.origine == OrigineOccurrence.IMPORT for o in occurrences)
        deplacee = next(o for o in occurrences if o.date == dt.date(2026, 10, 5))
        assert deplacee.statut == StatutOccurrence.DEPLACEE and deplacee.debut_min == 645
        assert {o.source_ligne for o in occurrences} == {2, 3, 4, 6, 7, 8, 10}

        # Annulée non comptée : 4 cours de mathématiques sur 5 lignes, regroupés en volume total.
        cm = _besoin(session, "IM.1101", "CM")
        assert (cm.mode_volume, cm.nombre, cm.max_par_semaine) == (ModeVolume.TOTAL, 4, 1)
        assert (cm.date_debut, cm.date_fin) == (dt.date(2026, 9, 7), dt.date(2026, 10, 5))
        hebdo = session.scalar(
            select(m.SeanceHebdo).where(
                m.SeanceHebdo.version_id == version.id, m.SeanceHebdo.besoin_id == cm.id
            )
        )
        assert (hebdo.jour, hebdo.debut_min, hebdo.salle.code, hebdo.verrou) == (
            0,
            510,
            "N016",
            False,
        )

        # Séance hebdomadaire (colonne jour) : une par semaine ouverte.
        langue = _besoin(session, "IL.1101", "LANGUE")
        assert (langue.mode_volume, langue.nombre) == (ModeVolume.PAR_SEMAINE, 1)


def test_regles_enseignant_salle_groupe_doublon(importer_edt):
    rapport = importer_edt(ModeImport.REFERENCE)
    par_ligne = {}
    for s in rapport.signalements:
        par_ligne.setdefault(s.ligne, []).append(s)

    with session_base() as session:
        td = _besoin(session, "II.1101", "TD")
        # « à recruter » et l'identifiant inconnu ENS999 : l'enseignant fictif de la matière.
        assert [e.identifiant for e in td.enseignants] == ["FICTIF-II.1101-A1"]
        assert td.enseignants[0].fictif
        # « à définir » et cellule vide : aucune salle, le générateur choisira.
        hebdo = session.scalar(select(m.SeanceHebdo).where(m.SeanceHebdo.besoin_id == td.id))
        assert hebdo.salle_id is None

    inconnu = next(s for s in par_ligne[8] if s.colonne == "enseignants")
    assert inconnu.gravite is Gravite.AVERTISSEMENT and "ENS999" in inconnu.message
    (groupe,) = par_ligne[9]
    assert (groupe.gravite, groupe.colonne) == (Gravite.ERREUR, "groupes")
    assert "A1-G9" in groupe.message and "mise de côté" in groupe.message
    (annulee,) = par_ligne[5]
    assert annulee.gravite is Gravite.INFORMATION and "annulée" in annulee.message
    (doublon,) = par_ligne[11]
    assert doublon.gravite is Gravite.INFORMATION and "ligne 10" in doublon.message


def test_evenement_special_fige_hors_regroupement(importer_edt):
    importer_edt(ModeImport.A_REPLACER)

    with session_base() as session:
        forum = _besoin(session, "IG.1101", "AUTRE")
        assert forum.mode_volume == ModeVolume.PONCTUEL
        assert forum.date_ponctuelle == dt.date(2026, 9, 17)
        assert forum.debut_impose_min == 825 and forum.duree_min == 240
        assert forum.salle_imposee.code == "N016"
        assert forum.libelle == "Forum entreprises"


def test_mode_verrous(importer_edt):
    rapport = importer_edt(ModeImport.VERROUS)

    assert rapport.bilan("séances hebdomadaires").compteurs == {"crees": 4}
    assert rapport.bilan("occurrences").compteurs == {}
    with session_base() as session:
        version = session.get(m.VersionEdt, rapport.version_id)
        assert version.active and version.origine == OrigineVersion.REFERENCE
        assert all(s.verrou for s in version.seances)


def test_mode_a_replacer_cree_seulement_les_besoins(importer_edt, jeu_minimal):
    rapport = importer_edt(ModeImport.A_REPLACER)

    assert rapport.version_id is None
    assert rapport.bilan("besoins").compteurs == {"crees": 2, "mis_a_jour": 1, "inchanges": 1}
    assert _compter(m.VersionEdt) == 0
    assert _compter(m.Besoin) == jeu_minimal.nb_besoins + 2


def test_reimport_dans_un_autre_mode_sans_doublon(importer_edt, jeu_minimal):
    importer_edt(ModeImport.REFERENCE)

    rapport = importer_edt(ModeImport.VERROUS)

    assert rapport.bilan("besoins").compteurs == {"inchanges": 4}
    assert rapport.bilan("enseignants fictifs").compteurs == {}
    assert _compter(m.Besoin) == jeu_minimal.nb_besoins + 2
    assert _compter(m.VersionEdt) == 2


def test_simulation_d_un_import_d_emploi_du_temps(importer_edt, jeu_minimal):
    rapport = importer_edt(ModeImport.REFERENCE, simuler=True)

    assert rapport.simulation and rapport.version_id is None
    assert rapport.bilan("besoins").nombre("crees") == 2
    assert [b["etat"] for b in rapport.besoins] == ["mis à jour", "créé", "inchangé", "créé"]
    assert _compter(m.VersionEdt) == 0
    assert _compter(m.Besoin) == jeu_minimal.nb_besoins


def test_semaine_type_imposee(importer_edt):
    rapport = importer_edt(ModeImport.REFERENCE, semaine_type=dt.date(2026, 10, 7))

    assert rapport.semaines_types[0].startswith("Semaine type de A1 : semaine 5")
    assert "imposée" in rapport.semaines_types[0]
    cours = next(b for b in rapport.besoins if b["type"] == "CM")
    assert cours["creneaux"] == "lundi 10:45 N016"


def test_semaine_type_hors_semestre_refusee(importer_edt):
    with pytest.raises(ErreurDonneesInvalides, match="en dehors du semestre"):
        importer_edt(ModeImport.REFERENCE, semaine_type=dt.date(2027, 6, 1))


def test_import_d_un_fichier_ical(importer_edt):
    rapport = importer_edt(ModeImport.REFERENCE, nom="agenda.ics")

    assert rapport.bilan("séances").compteurs == {
        "rejetes": 1,
        "lues": 5,
        "retirees": 1,
        "retenues": 4,
    }
    journee = next(s for s in rapport.signalements if s.ligne == 37 and s.gravite is Gravite.ERREUR)
    assert "journée entière" in journee.message
    with session_base() as session:
        td = _besoin(session, "IM.1101", "TD")
        assert td.groupes[0].code == "A1-G1"
        dates = sorted(
            (o.date, o.debut_min, o.statut)
            for o in session.scalars(select(m.Occurrence).where(m.Occurrence.besoin_id == td.id))
        )
        assert dates == [
            (dt.date(2026, 10, 13), 510, StatutOccurrence.PLANIFIEE),
            (dt.date(2026, 11, 4), 825, StatutOccurrence.DEPLACEE),
            (dt.date(2026, 11, 10), 510, StatutOccurrence.PLANIFIEE),
        ]
        conference = _besoin(session, "IG.1101", "AUTRE")
        assert conference.debut_impose_min == 14 * 60  # 12:00 UTC le 21/10 = 14:00 à Paris
        assert conference.salle_imposee.code == "N016"


def test_tableau_plat_illisible_rejete_sans_arreter(importer_edt, importer, jeu_minimal):
    texte = (
        "date;jour;debut;fin;matiere;type;groupes\n"
        "05/10/2026;;25h00;10:30;IM.1101;CM;A1\n"
        ";;08:30;10:30;IM.1101;CM;A1\n"
        "05/10/2026;mardi;08:30;10:30;IM.1101;CM;A1\n"
        "06/10/2026;;08:30;10:30;IM.1101;CM;A1\n"
    )
    rapport = importer(
        FichierEnvoye("edt.csv", texte.encode()),
        mode=ModeImport.A_REPLACER,
        semestre_id=jeu_minimal.semestre_id,
    )
    assert rapport.bilan("séances").compteurs == {"rejetes": 3, "lues": 1, "retenues": 1}
    assert [(s.ligne, s.colonne) for s in rapport.signalements if s.gravite is Gravite.ERREUR] == [
        (2, "debut"),
        (3, "date"),
        (4, "jour"),
    ]
