"""Feuille « regles » du format d'échange : gabarit, import, export, réimport, tout remplacer.

Le jeu minimal pose quatre règles : R001 (jours de la promotion A1), R002
(pause déjeuner de la classe A1-G1), R003 et R004 (règles de base, proposées).
"""

from __future__ import annotations

import io
import zipfile

from openpyxl import load_workbook
from sqlalchemy import func, select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite
from edt.domaine.regles import EtatRegle, OrigineRegle, TypeRegle
from edt.echange.donnees import ecrire_parametres_regle, lire_parametres_regle
from edt.echange.format import feuille
from edt.services import echange as service
from edt.services import regles as service_regles
from edt.services.echange import FichierEnvoye, ModeImport
from edt.services.parametres import ecrire_parametre

ENTETE = (
    "annee;code;type;portee;cible;parametres;niveau;etat;semestre;date_debut;date_fin;commentaire\n"
)

TROIS_REGLES = ENTETE + (
    "2026-2027;R010;JOURS_TRAVAILLES;groupe;a1-g2;jours = lundi, mardi, mercredi;;;S1;;;Essai\n"
    '2026-2027;;PAUSE_DEJEUNER;enseignant;ENS002;"mode = libre ; debut = 11h45 ; fin = 14h15 ; '
    'duree = 1 h 15";important;active;;;;\n'
    '2026-2027;R012;SALLES;matiere;IE.1101;"mode = imposer ; salles = L115, N016";necessaire;;;'
    "01/10/2026;30/11/2026;\n"
)


def _csv(texte: str, nom: str = "regles.csv") -> FichierEnvoye:
    """Un CSV en mémoire. Une cellule de paramètres qui contient des « ; » est entre guillemets,
    comme l'écrivent un tableur et notre export."""
    return FichierEnvoye(nom, texte.encode("utf-8"))


def _regles(annee: str = "2026-2027") -> dict[str, service_regles.RegleDetail]:
    with session_base() as session:
        annee_id = session.scalar(
            select(m.AnneeScolaire.id).where(m.AnneeScolaire.libelle == annee)
        )
        return {d.code: d for d in service_regles.lister(session, annee_id)}


def _nombre_de_regles() -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(m.Regle))


# --------------------------------------------------------------------------- #
# Gabarit et cellule des paramètres
# --------------------------------------------------------------------------- #


def test_gabarit_avec_la_feuille_regles(base):
    with session_base() as session:
        classeur = service.produire_gabarit(session, "xlsx")
        archive = service.produire_gabarit(session, "zip")

    lu = load_workbook(io.BytesIO(classeur.contenu))
    assert lu.sheetnames[-1] == "regles"
    assert [c.value for c in lu["regles"][1]] == list(feuille("regles").entetes)
    explications = " ".join(
        str(c.value) for ligne in lu["Lisez-moi"].iter_rows() for c in ligne if c.value
    )
    assert (
        "PAUSE_DEJEUNER (Pause déjeuner)" in explications
        and "duree (durée ; 15 à 180 min" in explications
    )
    with zipfile.ZipFile(io.BytesIO(archive.contenu)) as contenu:
        assert (
            contenu.read("regles.csv")
            .decode("utf-8-sig")
            .startswith("annee;code;type;portee;cible")
        )


def test_cellule_des_parametres_aller_retour():
    texte = "Mode = libre ; début = 11h45 ; fin = 14:15 ; durée = 1 h 15"
    brouillon = lire_parametres_regle(TypeRegle.PAUSE_DEJEUNER, texte)
    assert brouillon == {"mode": "libre", "debut": "11h45", "fin": "14:15", "duree": "1 h 15"}
    canonique = {"mode": "libre", "debut": "11:45", "fin": "14:15", "duree": 75}
    assert ecrire_parametres_regle(TypeRegle.PAUSE_DEJEUNER, canonique) == (
        "mode = libre ; debut = 11:45 ; fin = 14:15 ; duree = 1 h 15"
    )
    assert lire_parametres_regle(TypeRegle.JOURS_TRAVAILLES, "jours = lundi, mardi") == {
        "jours": ["lundi", "mardi"]
    }
    assert lire_parametres_regle(TypeRegle.JOURNEES_LIBRES, "parmi =") == {"parmi": []}


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #


def test_import_de_trois_regles_valides_puis_relecture(jeu_minimal, importer, exporter):
    rapport = importer(_csv(TROIS_REGLES))

    assert rapport.bilan("regles").compteurs == {"crees": 3}, rapport.signalements
    assert not rapport.a_des_rejets
    regles = _regles()
    jours, pause, salles = regles["R010"], regles["R011"], regles["R012"]  # R011 : code attribué
    # Phrase relative au standard (lundi à vendredi).
    assert jours.phrase == "La classe A1-G2 ne travaille pas le jeudi et le vendredi."
    assert jours.portee.cible_code == "A1-G2"  # « a1-g2 » retrouvé sans tenir compte de la casse
    assert jours.regle.semestre_code == "S1" and jours.regle.commentaire == "Essai"
    assert "1 h 15 entre 11 h 45 et 14 h 15" in pause.phrase
    assert pause.regle.parametres == {
        "mode": "libre",
        "debut": "11:45",
        "fin": "14:15",
        "duree": 75,
    }
    assert salles.regle.parametres == {"mode": "imposer", "salles": ["L115", "N016"]}
    assert salles.regle.date_debut.isoformat() == "2026-10-01"
    assert {d.regle.origine for d in (jours, pause, salles)} == {OrigineRegle.IMPORT}
    assert {d.etat for d in (jours, pause, salles)} == {EtatRegle.ACTIVE}
    with session_base() as session:
        lignes = session.scalars(select(m.Regle).where(m.Regle.code.in_(["R010", "R011", "R012"])))
        assert sorted((r.source_fichier, r.source_ligne) for r in lignes) == [
            ("regles.csv", 2),
            ("regles.csv", 3),
            ("regles.csv", 4),
        ]

    exportees = {r["code"]: r for r in exporter()["regles"]}
    assert (
        exportees["R011"]["parametres"]
        == "mode = libre ; debut = 11:45 ; fin = 14:15 ; duree = 1 h 15"
    )
    assert exportees["R011"]["origine"] == "import" and exportees["R011"]["phrase"] == pause.phrase


def test_regles_invalides_rejetees_ligne_par_ligne_sans_rien_ecrire(jeu_minimal, importer):
    avant = _nombre_de_regles()
    texte = ENTETE + (
        '2026-2027;R020;PAUSE_DEJEUNER;groupe;A1-G2;"mode = libre ; duree = 200";;;;;;\n'
        "2026-2027;R021;JOURS_TRAVAILLES;groupe;A9-ZZ;jours = lundi;;;;;;\n"
        "2026-2027;R022;PAUSE_GEANTE;groupe;A1;;;;;;;\n"
        "2026-2027;R023;JOURS_TRAVAILLES;etablissement;;jours = lundi;;;;;;\n"
        "2026-2027;R024;SANS_TROU;groupe;A9-ZZ;maximum = 50;;;;;;\n"
        '2026-2027;R025;CAMPUS;groupe;A1;"mode = imposer ; campus = LYON";;;;;;\n'
    )

    rapport = importer(_csv(texte))

    assert rapport.bilan("regles").compteurs == {"rejetes": 6}
    erreurs = [
        (s.ligne, s.colonne, s.message) for s in rapport.signalements if s.gravite is Gravite.ERREUR
    ]
    par_ligne: dict[int, list[tuple[str, str]]] = {}
    for ligne, colonne, message in erreurs:
        par_ligne.setdefault(ligne, []).append((colonne, message))
    ((colonne, message),) = par_ligne[2]  # paramètre hors bornes
    assert colonne == "parametres" and message.startswith("duree : ") and "180" in message
    assert [c for c, _ in par_ligne[3]] == ["cible"] and "A9-ZZ" in par_ligne[3][0][1]
    assert [c for c, _ in par_ligne[4]] == ["type"]  # type inconnu : refusé à la lecture
    assert [c for c, _ in par_ligne[5]] == ["portee"]  # réglage refusé pour l'établissement
    assert sorted(c for c, _ in par_ligne[6]) == ["cible", "parametres"]  # deux erreurs, une ligne
    assert [c for c, _ in par_ligne[7]] == ["parametres"] and "LYON" in par_ligne[7][0][1]
    assert _nombre_de_regles() == avant


def test_simulation_n_ecrit_aucune_regle(jeu_minimal, importer):
    avant = _nombre_de_regles()
    rapport = importer(_csv(TROIS_REGLES), simuler=True)
    assert rapport.bilan("regles").compteurs == {"crees": 3}
    assert _nombre_de_regles() == avant


def test_mise_a_jour_par_annee_et_code(jeu_minimal, importer):
    # R002 : pause libre de la classe A1-G1 ; on passe en pause fixe, niveau facultatif.
    texte = ENTETE + "2026-2027;r002;PAUSE_DEJEUNER;groupe;A1-G1;mode = fixe;facultatif;;;;;\n"

    rapport = importer(_csv(texte))

    assert rapport.bilan("regles").compteurs == {"mis_a_jour": 1}
    pause = _regles()["R002"]
    assert pause.regle.parametres["mode"] == "fixe" and "duree" not in pause.regle.parametres
    assert pause.niveau.value == "facultatif"
    assert importer(_csv(texte)).bilan("regles").compteurs == {"inchanges": 1}

    autre_type = ENTETE + "2026-2027;R002;SANS_TROU;groupe;A1-G1;maximum = 1;;;;;;\n"
    refus = importer(_csv(autre_type))
    assert refus.bilan("regles").compteurs == {"rejetes": 1}
    assert (
        refus.signalements[0].colonne == "type"
        and "garde son type" in refus.signalements[0].message
    )


# --------------------------------------------------------------------------- #
# Export, réimport, tout remplacer
# --------------------------------------------------------------------------- #


def test_export_puis_reimport_identique(jeu_minimal, importer):
    importer(_csv(TROIS_REGLES))
    for format_ in ("xlsx", "zip"):
        with session_base() as session:
            fichier = service.exporter_donnees(session, format_)
        rapport = importer(FichierEnvoye(fichier.nom, fichier.contenu), simuler=True)
        assert rapport.bilan("regles").compteurs == {"inchanges": 7}, (
            format_,
            rapport.signalements,
        )


def test_aller_retour_des_regles_dans_une_base_vide_par_l_archive_csv(
    jeu_minimal, importer, environnement, aujourdhui
):
    from edt.db.initialisation import initialiser_base

    importer(_csv(TROIS_REGLES))
    with session_base() as session:
        archive = service.exporter_donnees(session, "zip")
    vide = f"sqlite:///{(environnement / 'vide.db').as_posix()}"
    initialiser_base(aujourdhui=aujourdhui, url=vide)

    rapport = importer(FichierEnvoye(archive.nom, archive.contenu), url=vide)

    assert rapport.bilan("regles").compteurs == {"crees": 7}, rapport.signalements
    with session_base(vide) as session:
        annee_id = session.scalar(
            select(m.AnneeScolaire.id).where(m.AnneeScolaire.libelle == "2026-2027")
        )
        copie = {d.code: d for d in service_regles.lister(session, annee_id)}
    origine = _regles()
    assert set(copie) == set(origine)
    for code, detail in origine.items():
        assert copie[code].phrase == detail.phrase
        assert copie[code].regle.parametres == detail.regle.parametres
        assert (copie[code].etat, copie[code].niveau) == (detail.etat, detail.niveau)
        assert copie[code].regle.origine is OrigineRegle.IMPORT


def test_tout_remplacer_vide_les_regles(jeu_minimal, importer):
    assert _nombre_de_regles() == 4
    campus = _csv("code;nom\nNDC;Notre-Dame-des-Champs\n", "campus.csv")

    rapport = importer(campus, tout_remplacer=True, confirmer_remplacement=True)

    assert _nombre_de_regles() == 0
    assert "4 règles personnalisées" in rapport.signalements[0].message


# --------------------------------------------------------------------------- #
# Reconstitution d'un emploi du temps : jours travaillés de l'établissement
# --------------------------------------------------------------------------- #


def test_reconstitution_lit_les_jours_travailles(jeu_minimal, importer):
    """Une ligne hebdomadaire « lundi » pour A1 : la semaine des partiels de A1 (du lundi au
    vendredi) n'est ouverte que si le samedi est un jour travaillé de l'établissement."""
    seances = _csv(
        "jour;debut;fin;matiere;type;groupes;enseignants;salle\n"
        "lundi;08:30;10:30;IM.1101;CM;A1;ENS001;N016\n",
        "seances.csv",
    )
    options = {
        "mode": ModeImport.REFERENCE,
        "semestre_id": jeu_minimal.semestre_id,
        "simuler": True,
    }

    standard = importer(seances, **options)
    with session_base() as session:
        ecrire_parametre(session, "jours_travailles", (0, 1, 2, 3, 4, 5))
    avec_samedi = importer(seances, **options)

    assert "sur 17 semaine(s)" in standard.semaines_types[0]
    assert "sur 18 semaine(s)" in avec_samedi.semaines_types[0]
