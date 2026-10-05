"""Import des données de l'établissement : aller-retour, import partiel, simulation, réimport."""

from __future__ import annotations

import datetime as dt
import os

import pytest
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.initialisation import initialiser_base
from edt.db.moteur import fabrique_sessions, session_base
from edt.domaine.enumerations import Accessibilite, EtatCalcul, Gravite
from edt.echange.transaction import simulation_possible
from edt.erreurs import ErreurCalculEnCours, ErreurRegleMetier
from edt.services import echange as service
from edt.services.echange import FichierEnvoye
from edt.services.generation import CLE_PROCESSUS


def _csv(nom: str, texte: str) -> FichierEnvoye:
    return FichierEnvoye(nom, texte.encode("utf-8"))


def _compter(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


def _salle(code: str) -> m.Salle:
    with session_base() as session:
        salle = session.scalar(select(m.Salle).where(m.Salle.code == code))
        assert salle is not None, code
        _ = (salle.campus.code, salle.type_salle.code, [e.code for e in salle.equipements])
        return salle


# --------------------------------------------------------------------------- #
# Aller-retour
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("format_", ["xlsx", "zip"])
def test_aller_retour_dans_une_base_vide(
    format_, jeu_minimal, environnement, aujourdhui, importer, exporter
):
    with session_base() as session:
        fichier = service.exporter_donnees(session, format_)
    vide = f"sqlite:///{(environnement / 'vide.db').as_posix()}"
    initialiser_base(aujourdhui=aujourdhui, url=vide)

    rapport = importer(FichierEnvoye(fichier.nom, fichier.contenu), url=vide)

    assert not rapport.a_des_rejets, rapport.signalements
    assert exporter(vide) == exporter()
    assert rapport.bilan("besoins").nombre("crees") == jeu_minimal.nb_besoins
    assert rapport.bilan("groupes").nombre("crees") == jeu_minimal.nb_groupes


def test_reimporter_son_export_ne_change_rien(jeu_minimal, importer):
    with session_base() as session:
        fichier = service.exporter_donnees(session, "xlsx")

    rapport = importer(FichierEnvoye(fichier.nom, fichier.contenu), simuler=True)

    assert len(rapport.bilans) == 13
    assert all(set(b.compteurs) == {"inchanges"} for b in rapport.bilans), [
        b.texte for b in rapport.bilans
    ]
    assert rapport.signalements == ()


# --------------------------------------------------------------------------- #
# Import partiel et rapport
# --------------------------------------------------------------------------- #


def test_import_partiel_et_rapport_exact(jeu_minimal, importer, fichier_exemple):
    rapport = importer(fichier_exemple("salles_avec_erreurs.csv"))

    bilan = rapport.bilan("salles")
    assert bilan.compteurs == {"crees": 2, "rejetes": 4}
    assert bilan.texte == "2 créé(s), 4 rejeté(s)"
    positions = [(s.gravite, s.fichier, s.ligne, s.colonne) for s in rapport.signalements]
    fichier = "salles_avec_erreurs.csv"
    assert positions == [
        (Gravite.AVERTISSEMENT, fichier, 1, ""),
        (Gravite.ERREUR, fichier, 3, "capacite"),
        (Gravite.ERREUR, fichier, 5, "accessible"),
        (Gravite.ERREUR, fichier, 6, "code"),
        (Gravite.ERREUR, fichier, 4, "campus"),
    ]
    messages = [s.message for s in rapport.signalements]
    assert "remarque" in messages[0] and "conservées" in messages[0]
    assert messages[1] == "Nombre entier attendu, pas 'quarante'."
    assert messages[3] == "Valeur obligatoire manquante."
    assert messages[4] == "Campus « LYON » inconnu (feuille campus)."
    assert rapport.a_des_rejets and rapport.nb_rejets == 4

    nouvelle = _salle("N401")
    assert nouvelle.extra == {"remarque": "rénovée en 2025"}
    assert (nouvelle.source_fichier, nouvelle.source_ligne) == (fichier, 2)
    normalisee = _salle("N404")  # « ndc  », « cours », « videoproj, visio », « OUI »
    assert normalisee.campus.code == "NDC" and normalisee.type_salle.code == "COURS"
    assert [e.code for e in normalisee.equipements] == ["VIDEOPROJ", "VISIO"]
    assert normalisee.accessibilite == Accessibilite.OUI


def test_simulation_n_ecrit_rien(jeu_minimal, importer, fichier_exemple):
    avant = _compter(m.Salle)

    rapport = importer(fichier_exemple("salles_avec_erreurs.csv"), simuler=True)

    assert rapport.simulation
    assert rapport.bilan("salles").compteurs == {"crees": 2, "rejetes": 4}
    assert _compter(m.Salle) == avant


def test_reimport_sans_doublon(jeu_minimal, importer, fichier_exemple):
    importer(fichier_exemple("salles_avec_erreurs.csv"))

    rapport = importer(fichier_exemple("salles_avec_erreurs.csv"))

    assert rapport.bilan("salles").compteurs == {"inchanges": 2, "rejetes": 4}
    assert _compter(m.Salle) == jeu_minimal.nb_salles + 2


def test_mise_a_jour_par_identifiant_metier(jeu_minimal, importer):
    rapport = importer(_csv("salles.csv", "code;campus;capacite;type\nn204;NDC;45;COURS\n"))

    assert rapport.bilan("salles").compteurs == {"mis_a_jour": 1}
    salle = _salle("N204")
    assert salle.capacite == 45
    assert [e.code for e in salle.equipements] == ["VIDEOPROJ"]  # colonne absente : inchangée
    assert (salle.source_fichier, salle.source_ligne) == ("salles.csv", 2)


def test_cellule_vide_d_une_colonne_presente_remet_a_vide(jeu_minimal, importer):
    rapport = importer(
        _csv("salles.csv", "code;campus;capacite;type;equipements\nN204;NDC;40;COURS;\n")
    )

    assert rapport.bilan("salles").compteurs == {"mis_a_jour": 1}
    assert _salle("N204").equipements == []


def test_plusieurs_fichiers_dedoublonnes(jeu_minimal, importer):
    entete = "code;campus;capacite;type\n"
    premier = _csv("salles_ndc.csv", entete + "N501;NDC;20;COURS\nN502;NDC;20;COURS\n")
    second = _csv("salles_bis.csv", entete + "N501;NDC;20;COURS\nN502;NDC;99;COURS\n")

    rapport = importer([premier, second])

    assert rapport.bilan("salles").compteurs == {"crees": 2, "fusionnes": 1, "rejetes": 1}
    fusion, rejet = rapport.signalements
    assert fusion.gravite is Gravite.INFORMATION and "salles_ndc.csv, ligne 2" in fusion.message
    assert rejet.gravite is Gravite.ERREUR and rejet.colonne == "capacite"
    assert (rejet.fichier, rejet.ligne) == ("salles_bis.csv", 3)
    assert _salle("N502").capacite == 20


def test_ordre_des_feuilles_selon_les_dependances(base, importer):
    salles = _csv("salles.csv", "code;campus;capacite;type\nB01;BORD;30;COURS\n")
    campus = _csv("campus.csv", "code;nom\nBORD;Campus de Bordeaux (fictif)\n")

    rapport = importer([salles, campus])  # salles avant campus dans l'envoi

    assert rapport.bilan("campus").compteurs == {"crees": 1}
    assert rapport.bilan("salles").compteurs == {"crees": 1}
    assert [b.objet for b in rapport.bilans] == ["campus", "salles"]


def test_groupes_enfant_avant_parent_et_chevauchements(base, importer):
    texte = (
        "annee;code;libelle;nature;parent;effectif;chevauche\n"
        "2026-2027;B1-G1;Classe 1;classe;B1;20;B1-ESP\n"
        "2026-2027;B1;Promotion B1;promotion;;40;\n"
        "2026-2027;B1-ESP;Espagnol;transverse;;12;\n"
        "2026-2027;B1-X;Boucle;classe;B1-Y;1;\n"
        "2026-2027;B1-Y;Boucle;classe;B1-X;1;\n"
    )

    rapport = importer(_csv("groupes.csv", texte))

    assert rapport.bilan("groupes").compteurs == {"crees": 3, "rejetes": 2}
    assert {s.colonne for s in rapport.signalements} == {"parent"}
    with session_base() as session:
        classe = session.scalar(select(m.Groupe).where(m.Groupe.code == "B1-G1"))
        assert classe.parent.code == "B1"
        paires = session.execute(select(m.groupe_chevauchement)).all()
        codes = {g.id: g.code for g in session.scalars(select(m.Groupe))}
        assert {frozenset((codes[a], codes[b])) for a, b in paires} == {
            frozenset(("B1-G1", "B1-ESP"))
        }

    # Réimport identique : rien ne change, chevauchements compris.
    assert importer(_csv("groupes.csv", texte)).bilan("groupes").compteurs == {
        "inchanges": 3,
        "rejetes": 2,
    }


def test_regles_des_besoins_verifiees_avec_un_message_clair(jeu_minimal, importer):
    entete = "annee;semestre;matiere;type;groupes;duree;mode;date;nombre\n"
    lignes = (
        "2026-2027;S1;IM.1101;DST;A1;3 h;ponctuel;;\n"
        "2026-2027;S1;IM.1101;TD;A1-G1;2 h;par_semaine;19/11/2026;\n"
        "2026-2027;S1;IM.1101;TD;A1-G9;2 h;;;\n"
        "2026-2027;S1;IM.1101;TP;A1-G1a;2 h;total;;6\n"
    )

    rapport = importer(_csv("besoins.csv", entete + lignes))

    assert rapport.bilan("besoins").compteurs == {"crees": 1, "rejetes": 3}
    erreurs = {(s.ligne, s.colonne): s.message for s in rapport.signalements}
    assert erreurs[(2, "date")] == "Un besoin ponctuel demande sa date (colonne date)."
    assert erreurs[(3, "date")] == "La date ponctuelle ne vaut que pour le mode « ponctuel »."
    assert "A1-G9" in erreurs[(4, "groupes")]


def test_indisponibilite_recurrente_ou_datee(jeu_minimal, importer):
    texte = (
        "cible;ressource;jour;date_debut;debut;fin;motif\n"
        "enseignant;ENS001;mardi;;08:30;12:45;Réunion\n"
        "enseignant;ENS001;mardi;03/11/2026;;;\n"
        "salle;N105;;09/11/2026;;;Peinture\n"
    )

    rapport = importer(_csv("indisponibilites.csv", texte))

    assert rapport.bilan("indisponibilites").compteurs == {"crees": 2, "rejetes": 1}
    with session_base() as session:
        datee = session.scalar(
            select(m.Indisponibilite).where(m.Indisponibilite.motif == "Peinture")
        )
        assert (datee.date_debut, datee.date_fin) == (dt.date(2026, 11, 9), dt.date(2026, 11, 9))
        assert (datee.debut_min, datee.fin_min) == (0, 24 * 60)


def test_tableau_non_reconnu_signale(base, importer):
    rapport = importer(_csv("divers.csv", "alpha;beta\n1;2\n"))
    assert rapport.signalements[0].gravite is Gravite.ERREUR
    assert "non reconnu" in rapport.signalements[0].message


# --------------------------------------------------------------------------- #
# Tout remplacer
# --------------------------------------------------------------------------- #


def test_tout_remplacer_exige_une_confirmation(jeu_minimal, importer):
    campus = _csv("campus.csv", "code;nom\nNDC;Notre-Dame-des-Champs\n")
    with pytest.raises(ErreurRegleMetier, match="confirmer"):
        importer(campus, tout_remplacer=True)
    assert _compter(m.Salle) == jeu_minimal.nb_salles


def test_tout_remplacer_vide_puis_importe(jeu_minimal, importer, exporter):
    with session_base() as session:
        fichier = service.exporter_donnees(session, "zip")
    importer(_csv("campus.csv", "code;nom\nTMP;Campus temporaire\n"))
    avant = exporter()

    rapport = importer(
        FichierEnvoye(fichier.nom, fichier.contenu),
        tout_remplacer=True,
        confirmer_remplacement=True,
    )

    assert rapport.bilan("tout remplacer").nombre("supprimes") > 0
    apres = exporter()
    assert [c["code"] for c in apres["campus"]] == ["ISSY", "NDC"]  # le campus TMP a disparu
    assert apres["besoins"] == avant["besoins"]
    assert not rapport.a_des_rejets


def test_tout_remplacer_en_simulation_ne_supprime_rien(jeu_minimal, importer):
    rapport = importer(
        _csv("campus.csv", "code;nom\nNDC;Notre-Dame\n"), simuler=True, tout_remplacer=True
    )
    assert rapport.bilan("tout remplacer").nombre("supprimes") > 0
    assert _compter(m.Salle) == jeu_minimal.nb_salles


# --------------------------------------------------------------------------- #
# Garde-fous
# --------------------------------------------------------------------------- #


def test_import_refuse_pendant_un_calcul(jeu_minimal, importer):
    with session_base() as session:
        session.add(
            m.Calcul(
                semestre_id=jeu_minimal.semestre_id,
                etat=EtatCalcul.EN_COURS,
                demarre_le=dt.datetime.now(),
                # Calcul d'un autre processus encore vivant (ici le parent des tests) :
                # sans cela il serait pris pour abandonné et clos.
                parametres={"temps_limite_s": 300},
                statistiques={CLE_PROCESSUS: os.getppid()},
            )
        )
    with pytest.raises(ErreurCalculEnCours):
        importer(_csv("campus.csv", "code;nom\nX;Y\n"), simuler=True)


def test_point_de_sauvegarde_ne_valide_pas_la_transaction(base):
    """Le RELEASE d'un point de sauvegarde ne doit pas valider l'import avant l'appelant."""
    with fabrique_sessions()() as session:
        session.scalar(select(func.count()).select_from(m.Campus))  # lecture seule d'abord
        with simulation_possible(session, simuler=False):
            session.add(m.Campus(code="SP", nom="Point de sauvegarde"))
        session.rollback()
    assert _compter(m.Campus) == 0
