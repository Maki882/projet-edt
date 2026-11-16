"""Import des données de l'établissement : aller-retour, import partiel, simulation, réimport."""

from __future__ import annotations

import datetime as dt
import os
import threading
import time

import pytest
from sqlalchemy import func, select
from tests.outils.echange.fabrique import compter, csv_envoye, messages, seul

from edt.db import modeles as m
from edt.db.initialisation import initialiser_base
from edt.db.moteur import fabrique_sessions, session_base
from edt.domaine.enumerations import Accessibilite, EtatCalcul, Gravite
from edt.echange import donnees
from edt.echange.format import FEUILLES
from edt.echange.lecture import LigneTypee
from edt.echange.transaction import simulation_possible
from edt.echange.valeurs import cle_code
from edt.erreurs import (
    ErreurCalculEnCours,
    ErreurConfiguration,
    ErreurDonneesInvalides,
    ErreurRegleMetier,
)
from edt.services import echange as service
from edt.services.echange import FichierEnvoye, OptionsImport
from edt.services.generation import CLE_PROCESSUS


def _csv(nom: str, texte: str) -> FichierEnvoye:
    return FichierEnvoye(nom, texte.encode("utf-8"))


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
    apres, avant = exporter(vide), exporter()
    # Une règle créée par import a l'origine « import » : la colonne origine est informative.
    for regle in apres["regles"]:
        assert regle.pop("origine") == "import"
    for regle in avant["regles"]:
        regle.pop("origine")
    assert apres == avant
    assert rapport.bilan("besoins").nombre("crees") == jeu_minimal.nb_besoins
    assert rapport.bilan("groupes").nombre("crees") == jeu_minimal.nb_groupes


def test_reimporter_son_export_ne_change_rien(jeu_minimal, importer):
    with session_base() as session:
        fichier = service.exporter_donnees(session, "xlsx")

    rapport = importer(FichierEnvoye(fichier.nom, fichier.contenu), simuler=True)

    assert len(rapport.bilans) == len(FEUILLES)
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
    avant = compter(m.Salle)

    rapport = importer(fichier_exemple("salles_avec_erreurs.csv"), simuler=True)

    assert rapport.simulation
    assert rapport.bilan("salles").compteurs == {"crees": 2, "rejetes": 4}
    assert compter(m.Salle) == avant


def test_reimport_sans_doublon(jeu_minimal, importer, fichier_exemple):
    importer(fichier_exemple("salles_avec_erreurs.csv"))

    rapport = importer(fichier_exemple("salles_avec_erreurs.csv"))

    assert rapport.bilan("salles").compteurs == {"inchanges": 2, "rejetes": 4}
    assert compter(m.Salle) == jeu_minimal.nb_salles + 2


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
    assert compter(m.Salle) == jeu_minimal.nb_salles


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
    assert compter(m.Salle) == jeu_minimal.nb_salles


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
        with simulation_possible(session, simuler=False):
            session.add(m.Campus(code="SP", nom="Point de sauvegarde"))
        session.rollback()
    assert compter(m.Campus) == 0


# --------------------------------------------------------------------------- #
# Cas limites de l'import des données (banque de tests)
# --------------------------------------------------------------------------- #

ENTETE_BESOIN = "annee;semestre;matiere;type;groupes;duree;jour_impose;debut_impose;libelle"


@pytest.mark.cas_limite
@pytest.mark.parametrize("format_", ["xlsx", "zip"])
def test_export_xlsx_tolere_les_caracteres_de_controle(format_, jeu_minimal, importer):
    """Cas : un libellé de matière contient un caractère de contrôle invisible (U+0001 collé).

    Risque : openpyxl refuserait la cellule (IllegalCharacterError) et tout l'export
    XLSX de l'établissement échouerait en 500 ; un réimport, du classeur comme des CSV de
    l'archive, perdrait le caractère.
    """
    libelle = "Mathématiques\x01avancées"
    with session_base() as session:
        session.scalar(select(m.Matiere).where(m.Matiere.code == "IM.1101")).libelle = libelle
    with session_base() as session:
        fichier = service.exporter_donnees(session, format_)

    rapport = importer(FichierEnvoye(fichier.nom, fichier.contenu), simuler=True)

    assert rapport.bilan("matieres").compteurs == {"inchanges": 6}  # caractère restitué
    avertissement = seul(rapport.signalements, Gravite.AVERTISSEMENT)
    assert "U+0001" in avertissement.message and avertissement.colonne == "libelle"


@pytest.mark.cas_limite
def test_notation_de_caractere_ecrite_telle_quelle_survit_a_l_aller_retour_xlsx(
    jeu_minimal, importer, exporter
):
    """Cas : un libellé contient, écrites telles quelles, les suites « _x000B_ » et « _x005F_ »
    (la notation des caractères spéciaux du format XLSX).

    Risque : le réimport du classeur les prendrait pour des notations : le libellé
    changerait (caractère de contrôle invisible, « _ » isolé) et serait signalé à tort.
    """
    libelle = "Code _x000B_ et _x005F_ littéraux"
    with session_base() as session:
        session.scalar(select(m.Matiere).where(m.Matiere.code == "IM.1101")).libelle = libelle
    with session_base() as session:
        fichier = service.exporter_donnees(session, "xlsx")

    rapport = importer(FichierEnvoye(fichier.nom, fichier.contenu), simuler=True)

    assert rapport.bilan("matieres").compteurs == {"inchanges": 6}
    assert not [s for s in rapport.signalements if s.gravite is Gravite.AVERTISSEMENT]
    assert any(x["libelle"] == libelle for x in exporter()["matieres"])


@pytest.mark.cas_limite
def test_caractere_de_controle_importe_est_garde_et_signale(base, importer):
    """Cas : un CSV dont un libellé contient U+0001 (copier-coller depuis un autre logiciel).

    Risque : valeur corrigée en silence (perte), ou caractère invisible gardé sans
    que personne ne le sache.
    """
    rapport = importer(csv_envoye("matieres.csv", "code;libelle", "IF.9001;Matière\x01fictive"))

    assert rapport.bilan("matieres").compteurs == {"crees": 1}
    avertissement = seul(rapport.signalements, Gravite.AVERTISSEMENT)
    assert (avertissement.ligne, avertissement.colonne) == (2, "libelle")
    assert "U+0001" in avertissement.message
    with session_base() as session:
        gardé = session.scalar(select(m.Matiere.libelle).where(m.Matiere.code == "IF.9001"))
    assert gardé == "Matière\x01fictive"


@pytest.mark.cas_limite
def test_besoin_dont_le_debut_impose_et_la_duree_depassent_minuit_est_rejete(jeu_minimal, importer):
    """Cas : un besoin de 24 h imposé à 23:00, ou de 3 h imposé à 22:30.

    Risque : durée et heure contrôlées séparément, le besoin entrerait ; toute séance
    placée à ce début violerait la contrainte de plage horaire à l'enregistrement de
    la version : plantage différé, au calcul, sans lien visible avec l'import.
    """
    fichier = csv_envoye(
        "besoins.csv",
        ENTETE_BESOIN,
        "2026-2027;S1;IM.1101;TD;A1-G1;24 h;lundi;23:00;Veille fictive",
        "2026-2027;S1;IM.1101;TD;A1-G1;3 h;mardi;22:30;Nocturne fictive",
        "2026-2027;S1;IM.1101;TD;A1-G1;2 h;mercredi;22:00;Fin à minuit pile",  # accepté
    )

    rapport = importer(fichier, simuler=True)

    assert rapport.bilan("besoins").compteurs == {"crees": 1, "rejetes": 2}
    erreurs = [s for s in rapport.signalements if s.gravite is Gravite.ERREUR]
    assert [(s.ligne, s.colonne) for s in erreurs] == [(2, "debut_impose"), (3, "debut_impose")]
    assert erreurs[1].message == (
        "Une séance de 3 h commençant à 22 h 30 dépasserait minuit : avancer l'heure de début "
        "ou raccourcir la durée."
    )


@pytest.mark.cas_limite
def test_salle_au_code_contenant_arobase_reste_referencable(
    jeu_minimal, environnement, aujourdhui, importer, exporter
):
    """Cas : une salle de code « A@B » imposée à un besoin, puis export et réimport.

    Risque : la référence serait coupée au premier « @ » (salle « A » du campus
    « B ») : « Salle inconnue », ligne rejetée, salle imposée perdue à l'aller-retour.
    """
    salles = csv_envoye("salles.csv", "code;campus;capacite;type", "A@B;NDC;30;COURS")
    besoins = csv_envoye(
        "besoins.csv",
        "annee;semestre;matiere;type;groupes;duree;salle_imposee;libelle",
        "2026-2027;S1;IM.1101;TD;A1-G1;2 h;A@B;Séance en salle A@B",
    )

    rapport = importer([salles, besoins])

    assert not rapport.a_des_rejets, rapport.signalements
    assert rapport.bilan("besoins").compteurs == {"crees": 1}
    with session_base() as session:
        export = service.exporter_donnees(session, "zip")
    vide = f"sqlite:///{(environnement / 'vide.db').as_posix()}"
    initialiser_base(aujourdhui=aujourdhui, url=vide)
    retour = importer(FichierEnvoye(export.nom, export.contenu), url=vide)
    assert not retour.a_des_rejets, retour.signalements
    apres, avant = exporter(vide), exporter()
    assert apres["salles"] == avant["salles"]
    assert apres["besoins"] == avant["besoins"]
    assert [b["salle_imposee"] for b in avant["besoins"] if b["salle_imposee"]] == ["A@B"]


@pytest.mark.cas_limite
def test_reference_de_salle_a_double_lecture_refusee_comme_ambigue(jeu_minimal, importer):
    """Cas : une salle de code « A@NDC » (campus ISSY) et deux salles « A » (NDC et ISSY) ;
    un besoin impose la salle « A@NDC », référence que l'export écrit pour la salle A de NDC.

    Risque : la salle de code « A@NDC » serait choisie en silence au lieu de la salle A du
    campus NDC : salle imposée changée au réimport d'un export, sans un mot.
    """
    salles = csv_envoye(
        "salles.csv",
        "code;campus;capacite;type",
        "A@NDC;ISSY;30;COURS",
        "A;NDC;30;COURS",
        "A;ISSY;30;COURS",
    )
    besoins = csv_envoye(
        "besoins.csv",
        "annee;semestre;matiere;type;groupes;duree;salle_imposee;libelle",
        "2026-2027;S1;IM.1101;TD;A1-G1;2 h;A@NDC;Séance en salle A@NDC",
    )

    rapport = importer([salles, besoins], simuler=True)

    erreurs = [s for s in rapport.signalements if s.gravite is Gravite.ERREUR]
    assert [(s.feuille, s.colonne) for s in erreurs] == [("besoins.csv", "salle_imposee")]
    assert "ambiguë" in erreurs[0].message and "campus NDC" in erreurs[0].message


@pytest.mark.cas_limite
def test_durees_absurdes_signalees(jeu_minimal, importer):
    """Cas : un trajet de 100 h entre deux campus, un maximum de 90 h par jour.

    Risque : accepté sans borne, il rendrait le calcul infaisable (trajet impossible)
    ou n'aurait aucun sens, sans lien visible avec l'import.
    """
    trajets = csv_envoye("trajets.csv", "campus_a;campus_b;duree", "NDC;ISSY;100 h")
    enseignants = csv_envoye(
        "enseignants.csv",
        "identifiant;nom;max_par_jour;max_par_semaine",
        "ENS001;Martin;90 h;",
        "ENS002;Bernard;;200 h",
        "ENS003;Dubois;24 h;168 h",  # juste aux bornes : accepté
    )

    rapport = importer([trajets, enseignants], simuler=True)

    assert rapport.bilan("trajets").compteurs == {"rejetes": 1}
    assert rapport.bilan("enseignants").nombre("rejetes") == 2
    erreurs = {
        (s.fichier, s.colonne): s.message
        for s in rapport.signalements
        if s.gravite is Gravite.ERREUR
    }
    assert erreurs == {
        ("trajets.csv", "duree"): "Valeur trop grande : 100 h (au plus 12 h).",
        ("enseignants.csv", "max_par_jour"): "Valeur trop grande : 90 h (au plus 24 h).",
        ("enseignants.csv", "max_par_semaine"): "Valeur trop grande : 200 h (au plus 168 h).",
    }


@pytest.mark.cas_limite
def test_maximum_d_enseignement_nul_signale(jeu_minimal, importer):
    """Cas : un maximum d'enseignement de « 0 h » par jour ou par semaine, au lieu d'une
    cellule vide.

    Risque : lu sans erreur puis refusé par la base (maximum strictement positif) : échec
    de l'import entier au lieu d'une ligne rejetée qui nomme la colonne.
    """
    enseignants = csv_envoye(
        "enseignants.csv",
        "identifiant;nom;max_par_jour;max_par_semaine",
        "ENS001;Martin;0 h;",
        "ENS002;Bernard;;0 h",
    )

    rapport = importer([enseignants], simuler=True)

    assert rapport.bilan("enseignants").nombre("rejetes") == 2
    erreurs = {
        (s.fichier, s.colonne): s.message
        for s in rapport.signalements
        if s.gravite is Gravite.ERREUR
    }
    assert erreurs == {
        ("enseignants.csv", "max_par_jour"): "Valeur trop petite : 0 min (au moins 1 min).",
        ("enseignants.csv", "max_par_semaine"): "Valeur trop petite : 0 min (au moins 1 min).",
    }


ENTETE_GROUPE = "annee;code;libelle;nature;effectif;parent"


def _chaine_de_groupes(nombre: int) -> list[str]:
    """Lignes de `nombre` groupes en chaîne (CH0000 <- CH0001 <- ...), enfants d'abord."""
    lignes = [
        f"2026-2027;CH{i:04d};Chaîne {i};classe;10;{f'CH{i - 1:04d}' if i else ''}"
        for i in range(nombre)
    ]
    return list(reversed(lignes))


@pytest.mark.cas_limite
def test_groupe_parent_de_lui_meme_rejete(jeu_minimal, importer):
    """Cas : un groupe parent de lui-même, nouveau (BOUCLE) ou déjà en base (A1-G1).

    Risque : l'auto-parenté finirait en IntegrityError générique, ou en boucle sans
    fin dans le parcours des parents.
    """
    boucles = csv_envoye(
        "groupes.csv",
        ENTETE_GROUPE,
        "2026-2027;BOUCLE;Boucle fictive;classe;10;BOUCLE",
        "2026-2027;A1-G1;1re année, classe 1;classe;28;A1-G1",
    )

    rapport = importer(boucles)

    assert rapport.bilan("groupes").compteurs == {"rejetes": 2}
    erreurs = [s for s in rapport.signalements if s.gravite is Gravite.ERREUR]
    assert [(s.ligne, s.colonne) for s in erreurs] == [(2, "parent"), (3, "parent")]
    assert all(s.message.startswith("Parenté circulaire") for s in erreurs)
    assert compter(m.Groupe) == jeu_minimal.nb_groupes


@pytest.mark.cas_limite
def test_ordre_des_parents_lineaire_sur_une_chaine_profonde():
    """Cas : 20 000 groupes en chaîne, chaque enfant écrit avant son parent.

    Risque : un ordonnancement récursif lèverait RecursionError au-delà de
    1 000 niveaux, un algorithme quadratique prendrait des minutes.
    """
    lignes = [
        LigneTypee(
            "groupes.csv",
            "groupes",
            numero,
            {"annee": "2026-2027", "code": f"CH{i:05d}", "parent": f"CH{i - 1:05d}" if i else None},
            frozenset(),
        )
        for numero, i in enumerate(reversed(range(20_000)), start=2)
    ]
    cles = {id(ligne): (cle_code(ligne["annee"]), cle_code(ligne["code"])) for ligne in lignes}

    ordre, circulaires = donnees._parents_d_abord(lignes, cles)

    assert [ligne["code"] for ligne in ordre] == [f"CH{i:05d}" for i in range(20_000)]
    assert circulaires == set()


@pytest.mark.perf
@pytest.mark.cas_limite
def test_hierarchie_profonde_et_auto_parente(jeu_minimal, importer):
    """Cas : l'import réel de 1 500 groupes en chaîne, enfants avant parents, plus une boucle.

    Risque : un import de hiérarchie profonde qui s'effondre (pile, temps) ou qui
    laisse entrer la boucle ; mesuré de bout en bout, base comprise.
    """
    lignes = [*_chaine_de_groupes(1500), "2026-2027;BOUCLE;Boucle fictive;classe;10;BOUCLE"]

    debut = time.perf_counter()
    rapport = importer(csv_envoye("groupes.csv", ENTETE_GROUPE, *lignes), simuler=True)
    duree = time.perf_counter() - debut

    assert rapport.bilan("groupes").compteurs == {"crees": 1500, "rejetes": 1}
    assert seul(rapport.signalements, Gravite.ERREUR).message.startswith("Parenté circulaire")
    assert duree < 15, f"{duree:.1f} s"


@pytest.mark.cas_limite
def test_ical_en_mode_donnees_signale_le_mauvais_mode(jeu_minimal, importer, fichier_exemple):
    """Cas : un agenda .ics envoyé avec le mode « données de l'établissement ».

    Risque : une régression ignorerait
    l'agenda en silence, l'utilisateur croyant son emploi du temps importé.
    """
    campus = csv_envoye("campus.csv", "code;nom", "ZZ;Campus fictif")

    rapport = importer([fichier_exemple("agenda.ics"), campus], simuler=True)

    erreur = seul(rapport.signalements, Gravite.ERREUR)
    assert erreur.fichier == "agenda.ics"
    assert "choisir un mode d'import d'emploi du temps" in erreur.message
    assert "référence, verrous ou à replacer" in erreur.message
    assert rapport.a_des_rejets and rapport.bilan("séances").compteurs == {}
    assert rapport.bilan("campus").compteurs == {"crees": 1}  # le reste de l'envoi est lu
    assert messages(rapport.signalements, Gravite.AVERTISSEMENT) == []


@pytest.mark.cas_limite
def test_import_en_cours_signale_pendant_tout_l_import(base, monkeypatch):
    """Cas : un calcul est demandé depuis un autre onglet pendant qu'un import lit ses fichiers.

    Risque : sans marqueur consultable, le lancement d'un calcul ne pourrait pas
    savoir qu'un import tourne (calcul sur des données en train de changer),
    alors que l'import, lui, refuse un calcul en cours.
    """
    commence, liberer = threading.Event(), threading.Event()
    fins = []

    def lecture_bloquee(fichier, limites):
        commence.set()
        assert liberer.wait(10)
        raise ErreurDonneesInvalides("fin de la lecture simulée")

    def importer_():
        try:
            with session_base() as session:
                service.appliquer_import(
                    session, [FichierEnvoye("donnees.xlsx", b"x" * 100)], OptionsImport()
                )
        except ErreurDonneesInvalides as erreur:  # vérifiée ci-dessous
            fins.append(erreur.message)

    monkeypatch.setattr(service, "lire_fichier", lecture_bloquee)
    fil = threading.Thread(target=importer_)
    assert not service.import_en_cours()
    fil.start()
    try:
        assert commence.wait(10), "l'import simulé n'a pas commencé"
        assert service.import_en_cours()
    finally:
        liberer.set()
        fil.join(10)
        assert not fil.is_alive(), "l'import ne s'est pas terminé"

    assert fins == ["fin de la lecture simulée"]
    assert not service.import_en_cours()  # remis à zéro, même après une erreur


@pytest.mark.cas_limite
def test_import_sur_une_session_deja_engagee_refuse_clairement(base):
    """Cas : un appelant lit avec la session, puis lui confie un import (simulation_possible).

    Risque : la transaction, déjà ouverte en mode différé, ne réserverait pas la
    base : deux imports simultanés échoueraient au hasard « database is locked »,
    sans que rien ne dise pourquoi.
    """
    with fabrique_sessions()() as session:
        session.scalar(select(func.count()).select_from(m.Campus))  # la session a servi

        with pytest.raises(ErreurConfiguration), simulation_possible(session, simuler=True):
            session.add(m.Campus(code="SE", nom="Session engagée"))  # jamais atteint

    assert compter(m.Campus) == 0
