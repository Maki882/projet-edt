"""Lecture tolérante : encodages, séparateurs, dates, heures, oui/non, codes, classeurs."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest
from tests.outils.echange.fabrique import classeur, csv_envoye, messages, seul

from edt.domaine.enumerations import Gravite, NatureGroupe
from edt.echange import valeurs as v
from edt.echange.format import FEUILLES, feuille, identifier_feuille
from edt.echange.lecture import FichierEnvoye, detecter_separateur, lire_fichier, preparer_tableau
from edt.echange.rapport import Bilan
from edt.erreurs import ErreurDonneesInvalides

TEXTE = "code;nom\nNDC;Notre-Dame-des-Champs\nISSY;Issy-les-Moulineaux\n"


def _lire(nom: str, contenu: bytes):
    lu, signalements = lire_fichier(FichierEnvoye(nom, contenu))
    return lu.tableaux, signalements


# --------------------------------------------------------------------------- #
# Encodage
# --------------------------------------------------------------------------- #


def test_utf8_sans_bom():
    tableaux, signalements = _lire("campus.csv", TEXTE.replace("Notre", "Él").encode("utf-8"))
    assert tableaux[0].lignes[0].cellules == ("NDC", "Él-Dame-des-Champs")
    assert signalements == []


def test_utf8_avec_bom():
    tableaux, signalements = _lire("campus.csv", TEXTE.encode("utf-8-sig"))
    assert tableaux[0].entetes == ("code", "nom")  # le BOM ne colle pas au premier en-tête
    assert signalements == []


def test_repli_windows_1252_avec_avertissement():
    tableaux, signalements = _lire("campus.csv", "code;nom\nNDC;Électronique\n".encode("cp1252"))
    assert tableaux[0].lignes[0].cellules == ("NDC", "Électronique")
    assert [s.gravite for s in signalements] == [Gravite.AVERTISSEMENT]
    assert "Windows-1252" in signalements[0].message


def test_utf16_avec_bom():
    tableaux, _ = _lire("campus.csv", TEXTE.replace(";", "\t").encode("utf-16"))
    assert tableaux[0].lignes[1].cellules == ("ISSY", "Issy-les-Moulineaux")


def test_encodage_illisible_est_une_erreur():
    tableaux, signalements = _lire("campus.csv", b"code;nom\nNDC;\x81\x8d\x8f\n")
    assert not tableaux
    assert signalements[0].gravite is Gravite.ERREUR
    assert "Encodage illisible" in signalements[0].message


# --------------------------------------------------------------------------- #
# Séparateur
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("separateur", [";", ",", "\t"])
def test_separateur_detecte(separateur):
    texte = TEXTE.replace(";", separateur)
    assert detecter_separateur(texte) == separateur
    tableaux, _ = _lire("campus.csv", texte.encode())
    assert tableaux[0].lignes[0].cellules == ("NDC", "Notre-Dame-des-Champs")


def test_virgule_dans_une_cellule_entre_guillemets():
    tableaux, _ = _lire("salles.csv", b'code,equipements\nN204,"VIDEOPROJ, VISIO"\n')
    assert tableaux[0].lignes[0].cellules == ("N204", "VIDEOPROJ, VISIO")


def test_numeros_de_ligne_et_lignes_vides():
    tableaux, _ = _lire("campus.csv", b"\ncode;nom\nNDC;A\n;\nISSY;B\n")
    tableau = tableaux[0]
    assert tableau.ligne_entetes == 2
    assert [ligne.numero for ligne in tableau.lignes] == [3, 5]


def test_valeur_sans_en_tete_gardee_comme_colonne_n():
    tableaux, _ = _lire("campus.csv", b"code;nom\nNDC;A;surplus\n")
    assert tableaux[0].entetes == ("code", "nom", "colonne 3")


# --------------------------------------------------------------------------- #
# Valeurs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("05/10/2026", dt.date(2026, 10, 5)),
        ("5/10/2026", dt.date(2026, 10, 5)),
        ("05-10-2026", dt.date(2026, 10, 5)),
        ("2026-10-05", dt.date(2026, 10, 5)),
        ("2026-10-05 00:00:00", dt.date(2026, 10, 5)),
        (dt.datetime(2026, 10, 5), dt.date(2026, 10, 5)),
        ("", None),
    ],
)
def test_dates(texte, attendu):
    assert v.lire_date(texte) == attendu


@pytest.mark.parametrize("texte", ["31/02/2026", "10/05/26", "demain", "2026-10-05 08:30"])
def test_dates_refusees(texte):
    with pytest.raises(ErreurDonneesInvalides):
        v.lire_date(texte)


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("8h30", 510),
        ("8 h 30", 510),
        ("08:30", 510),
        ("8:30", 510),
        ("18 h", 1080),
        ("08:30:00", 510),
        (dt.time(13, 45), 825),
    ],
)
def test_heures(texte, attendu):
    assert v.lire_heure(texte) == attendu


@pytest.mark.parametrize("texte", ["25h00", "8h75", "huit heures", "08:30:15", 8.5])
def test_heures_refusees(texte):
    with pytest.raises(ErreurDonneesInvalides):
        v.lire_heure(texte)


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("oui", True),
        ("Non", False),
        ("vrai", True),
        ("FAUX", False),
        ("1", True),
        ("0", False),
        (True, True),
    ],
)
def test_oui_non(texte, attendu):
    assert v.lire_booleen(texte) is attendu


def test_oui_non_refuse_une_autre_valeur():
    with pytest.raises(ErreurDonneesInvalides, match="oui/non"):
        v.lire_booleen("peut-être")


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [("2 h", 120), ("1 h 30", 90), ("1h30", 90), ("90 min", 90), ("01:30", 90)],
)
def test_durees(texte, attendu):
    assert v.lire_duree(texte) == attendu


def test_duree_sans_unite_refusee():
    with pytest.raises(ErreurDonneesInvalides, match="unité"):
        v.lire_duree("90")


def test_codes_normalises_espaces_et_casse():
    assert v.lire_code("  A1 -  G1 ") == "A1 - G1"
    assert v.cle_code(" Ndc ") == v.cle_code("NDC")
    assert v.lire_liste("A1-G1; a1-g1 , A1-G2") == ("A1-G1", "A1-G2")


def test_listes_fermees_par_valeur_ou_libelle():
    assert v.lire_choix("Demi-groupe", NatureGroupe) is NatureGroupe.DEMI_GROUPE
    assert v.lire_choix("demi_groupe", NatureGroupe) is NatureGroupe.DEMI_GROUPE
    assert v.lire_jour("Mer") == 2
    with pytest.raises(ErreurDonneesInvalides, match="valeurs permises"):
        v.lire_choix("section", NatureGroupe)


def test_entiers():
    assert v.lire_entier("1 200") == 1200
    assert v.lire_entier(40.0) == 40
    with pytest.raises(ErreurDonneesInvalides):
        v.lire_entier("40,5")


# --------------------------------------------------------------------------- #
# Lignes typées
# --------------------------------------------------------------------------- #


def test_une_ligne_signale_toutes_ses_valeurs_illisibles():
    tableaux, _ = _lire(
        "salles.csv", b"code;campus;capacite;type;etage\nN1;NDC;beaucoup;COURS;haut\n"
    )
    bilan, signalements = Bilan("salles"), []
    lignes = preparer_tableau(tableaux[0], feuille("salles"), bilan, signalements)
    assert lignes == []
    assert bilan.nombre("rejetes") == 1
    assert sorted(s.colonne for s in signalements) == ["capacite", "etage"]
    assert {s.ligne for s in signalements} == {2}


def test_colonne_obligatoire_absente_rejette_le_tableau():
    tableaux, _ = _lire("salles.csv", b"code;campus\nN1;NDC\nN2;NDC\n")
    bilan, signalements = Bilan("salles"), []
    assert preparer_tableau(tableaux[0], feuille("salles"), bilan, signalements) == []
    assert bilan.nombre("rejetes") == 2
    assert "capacite" in signalements[0].message and "type" in signalements[0].message


def test_entetes_reconnus_par_libelle_sans_accents_ni_casse():
    tableaux, _ = _lire(
        "salles.csv", "Code;Campus;CAPACITÉ;Type de salle\nN1;NDC;10;COURS\n".encode()
    )
    lignes = preparer_tableau(tableaux[0], feuille("salles"), Bilan("salles"), [])
    assert lignes[0]["capacite"] == 10 and lignes[0]["type"] == "COURS"


def test_feuille_reconnue_par_nom_ou_par_entetes():
    assert identifier_feuille("Jours fermés (2).csv", []) is feuille("jours_fermes")
    assert identifier_feuille("salles_ndc.csv", []) is feuille("salles")
    assert identifier_feuille(
        "export.csv", ["identifiant", "nom", "statut", "civilite"]
    ) is feuille("enseignants")
    assert identifier_feuille("export.csv", ["n'importe", "quoi"]) is None


def test_classeur_valeurs_excel_typees():
    from openpyxl import Workbook

    classeur = Workbook()
    onglet = classeur.active
    onglet.title = "seances"
    onglet.append(["date", "debut", "fin", "capacite"])
    onglet.append([dt.datetime(2026, 10, 5), dt.time(8, 30), "10h30", 40])
    tampon = io.BytesIO()
    classeur.save(tampon)
    tableaux, _ = _lire("edt.xlsx", tampon.getvalue())
    cellules = tableaux[0].lignes[0].cellules
    assert v.lire_date(cellules[0]) == dt.date(2026, 10, 5)
    assert v.lire_heure(cellules[1]) == 510
    assert v.lire_entier(cellules[3]) == 40


@pytest.mark.parametrize(
    "lisez_moi", ["LISEZ-MOI.txt", "export/LISEZ-MOI.txt", r"export\LISEZ-MOI.txt"]
)
def test_archive_zip_de_csv(lisez_moi):
    tampon = io.BytesIO()
    membre = zipfile.ZipInfo("LISEZ-MOI.txt")
    membre.filename = lisez_moi  # écrit tel quel : ZipInfo changerait « \ » en « / » sous Windows
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("campus.csv", TEXTE)
        archive.writestr(membre, "explications")
    tableaux, signalements = _lire("donnees.zip", tampon.getvalue())
    assert [t.nom for t in tableaux] == ["campus.csv"]
    assert tableaux[0].fichier == "donnees.zip/campus.csv"
    assert signalements == []


@pytest.mark.cas_limite
def test_agenda_ical_dans_une_archive_ignore_avec_le_bon_conseil():
    """Cas : une archive ZIP contient des CSV et un agenda .ics.

    Risque : l'agenda lu comme un tableau (« Tableau non reconnu ») ou ignoré sans un mot ;
    l'utilisateur ne sait pas qu'il faut l'envoyer à part.
    """
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("campus.csv", TEXTE)
        archive.writestr("agenda.ics", "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")
    tableaux, signalements = _lire("donnees.zip", tampon.getvalue())
    assert [t.nom for t in tableaux] == ["campus.csv"]
    erreur = seul(signalements, Gravite.ERREUR)
    assert erreur.fichier == "donnees.zip/agenda.ics" and "s'envoie à part" in erreur.message


def test_ancien_format_excel_refuse_clairement():
    tableaux, signalements = _lire("vieux.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
    assert not tableaux and ".xlsx" in signalements[0].message


def test_toutes_les_feuilles_ont_un_identifiant():
    assert all(f.identifiant for f in FEUILLES)


# --------------------------------------------------------------------------- #
# Cas limites de la lecture (banque de tests)
# --------------------------------------------------------------------------- #

OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
"""Signature d'un conteneur Office binaire : ancien .xls, ou classeur chiffré par mot de passe."""


def _preparer(fichier: FichierEnvoye, nom_feuille: str):
    """Lit un fichier d'un seul tableau et prépare ses lignes : (lignes, bilan, signalements)."""
    lu, signalements = lire_fichier(fichier)
    (tableau,) = lu.tableaux
    bilan = Bilan(nom_feuille)
    lignes = preparer_tableau(tableau, feuille(nom_feuille), bilan, signalements)
    return lignes, bilan, signalements


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("fichier", "attendu"),
    [
        (FichierEnvoye("salles.csv", b"\xef\xbb\xbf"), "Aucune cellule remplie"),
        (FichierEnvoye("salles.csv", b"  ;;;\n;;;\n"), "Aucune cellule remplie"),
        (classeur({"Lisez-moi": [["Explications"], ["Rien d'autre."]]}), "Aucune cellule remplie"),
        (classeur({"salles": [], "groupes": []}), "Aucune cellule remplie"),
        (FichierEnvoye("salles.csv", b""), "Fichier vide"),
        (FichierEnvoye("salles.csv", b"  \n"), "Fichier vide"),
    ],
    ids=[
        "bom-seul",
        "separateurs-seuls",
        "lisez-moi-seul",
        "onglets-vides",
        "zero-octet",
        "blancs",
    ],
)
def test_fichier_sans_cellule_remplie_est_signale_comme_vide(fichier, attendu, base, importer):
    """Cas : un fichier sans aucune cellule de données (BOM seul, « ;;; », Lisez-moi seul...).

    Risque : rapport sans bilan ni signalement, affiché « Aucune ligne rejetée » :
    l'utilisateur croirait son fichier pris en compte.
    """
    lu, signalements = lire_fichier(fichier)

    assert lu.tableaux == ()
    message = seul(signalements, Gravite.ERREUR).message
    assert attendu in message and "rien à importer" in message

    rapport = importer(fichier, simuler=True)
    assert rapport.a_des_rejets
    assert messages(rapport.signalements, Gravite.ERREUR) == [message]


@pytest.mark.cas_limite
def test_fichier_d_en_tetes_seuls_dit_rien_a_traiter(base, importer):
    """Cas : un gabarit rempli de ses seuls en-têtes (aucune ligne de données).

    Risque : confondu avec un fichier vide, il serait signalé en erreur ; il doit
    au contraire passer et dire « rien à traiter » pour sa feuille.
    """
    rapport = importer(csv_envoye("salles.csv", "code;campus;capacite;type"), simuler=True)

    assert rapport.signalements == () and not rapport.a_des_rejets
    assert rapport.bilan("salles").texte == "rien à traiter"


@pytest.mark.cas_limite
def test_classeur_protege_par_mot_de_passe_signale_clairement():
    """Cas : un classeur .xlsx enregistré avec un mot de passe (conteneur OLE2 chiffré).

    Risque : annoncé « format Excel 97-2003 (.xls) », il laisserait l'utilisateur,
    qui envoie déjà un .xlsx, sans comprendre qu'il faut retirer le mot de passe.
    """
    lu, signalements = lire_fichier(FichierEnvoye("donnees.xlsx", OLE2 + b"\x00" * 600))

    assert lu.tableaux == ()
    message = seul(signalements, Gravite.ERREUR).message
    assert "« donnees.xlsx »" in message
    assert "mot de passe" in message and "enregistrer le fichier au format .xlsx" in message


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("nom_feuille", "entetes", "cellules", "colonne", "montre"),
    [
        (
            "groupes",
            ["annee", "code", "libelle", "nature", "effectif"],
            ["2026-2027", dt.datetime(2026, 1, 2), "Groupe fictif", "classe", 20],
            "code",
            "une date (02/01/2026)",
        ),
        (
            "salles",
            ["code", "campus", "capacite", "type"],
            [dt.time(8, 30), "NDC", 30, "COURS"],
            "code",
            "une heure (08:30)",
        ),
        (
            "besoins",
            ["annee", "semestre", "matiere", "type", "groupes", "duree"],
            ["2026-2027", "S1", "IM.1101", "CM", dt.datetime(2026, 11, 2), "2 h"],
            "groupes",
            "une date (02/11/2026)",
        ),
    ],
    ids=["code-devenu-date", "code-devenu-heure", "liste-devenue-date"],
)
def test_cellule_date_dans_une_colonne_code_est_refusee_avec_explication(
    nom_feuille, entetes, cellules, colonne, montre
):
    """Cas : un code « 1-2 » (ou « 8:30 ») que le tableur a converti en date ou en heure.

    Risque : « 2026-01-02 00:00:00 » enregistré comme code, sans avertissement ;
    les références à ce groupe échoueraient ensuite sans explication.
    """
    fichier = classeur({nom_feuille: [entetes, cellules]})

    lignes, bilan, signalements = _preparer(fichier, nom_feuille)

    assert lignes == [] and bilan.compteurs == {"rejetes": 1}
    erreur = seul(signalements, Gravite.ERREUR)
    assert (erreur.ligne, erreur.colonne) == (2, colonne)
    assert montre in erreur.message and "format Texte" in erreur.message


@pytest.mark.cas_limite
def test_valeur_trop_longue_pour_sa_colonne_rejetee():
    """Cas : une feuille salles avec un code de 5 000 caractères, un bâtiment de 10 000.

    Risque : SQLite n'impose pas la longueur VARCHAR : la ligne entrerait et déformerait
    listes, menus et noms de fichiers d'export.
    """
    fichier = csv_envoye(
        "salles.csv",
        "code;campus;capacite;type;batiment",
        f"{'S' * 5000};NDC;30;COURS;Principal",
        f"N900;NDC;30;COURS;{'B' * 10000}",
        f"{'N' * 32};NDC;30;COURS;{'B' * 64}",  # juste à la borne : accepté
    )

    lignes, bilan, signalements = _preparer(fichier, "salles")

    assert [ligne["code"] for ligne in lignes] == ["N" * 32]
    assert bilan.compteurs == {"rejetes": 2}
    assert [(s.ligne, s.colonne) for s in signalements] == [(2, "code"), (3, "batiment")]
    assert "5000 caractères, 32 au plus" in signalements[0].message
    assert "10000 caractères, 64 au plus" in signalements[1].message


@pytest.mark.cas_limite
def test_colonne_en_double_lue_une_seule_fois_et_signalee_une_fois():
    """Cas : en-têtes « code;campus;capacite;type;Capacité », capacité 30 puis 99.

    Risque : deux avertissements contradictoires, et la seconde valeur rangée parmi
    les colonnes libres (extra) de la salle : trace trompeuse.
    """
    fichier = csv_envoye("salles.csv", "code;campus;capacite;type;Capacité", "N900;NDC;30;COURS;99")

    (ligne,), bilan, signalements = _preparer(fichier, "salles")

    assert ligne["capacite"] == 30 and ligne.extra == {}
    avertissement = seul(signalements, Gravite.AVERTISSEMENT)
    assert avertissement.colonne == "Capacité" and "en double" in avertissement.message
    assert bilan.compteurs == {}


@pytest.mark.cas_limite
def test_cellule_fusionnee_signalee_comme_telle():
    """Cas : un onglet salles où le code N904 est fusionné sur deux lignes (A2:A3).

    Risque : openpyxl rend vide la cellule absorbée : ligne 3 rejetée « Valeur
    obligatoire manquante » sans que la fusion, vraie cause, soit nommée.
    """
    lignes_ = [["code", "campus", "capacite", "type"], ["N904", "NDC", 30, "COURS"]]
    lignes_.append([None, "NDC", 40, "COURS"])

    lignes, _, signalements = _preparer(
        classeur({"salles": lignes_}, fusions={"salles": ["A2:A3"]}), "salles"
    )

    avertissement = seul(signalements, Gravite.AVERTISSEMENT)
    assert "fusionnées (A2:A3)" in avertissement.message and avertissement.feuille == "salles"
    assert [ligne["code"] for ligne in lignes] == ["N904"]
    assert seul(signalements, Gravite.ERREUR).ligne == 3
    # Le même onglet sans fusion ne donne aucun avertissement.
    _, _, sans_fusion = _preparer(classeur({"salles": lignes_}), "salles")
    assert messages(sans_fusion, Gravite.AVERTISSEMENT) == []


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("lire", "valeur", "conseil"),
    [
        (v.lire_date, 46300, "format Date"),
        (v.lire_date, 46300.0, "format Date"),
        (v.lire_date, "46300", "format Date"),
        (v.lire_heure, 0.354166, "format Heure"),
        (v.lire_heure, "0,354166", "format Heure"),
    ],
)
def test_nombre_de_serie_excel_refuse_avec_un_indice(lire, valeur, conseil):
    """Cas : une date (46300) ou une heure (0,354166 pour 8 h 30) gardée en nombre par le tableur.

    Risque : refus sans explication pour un non-informaticien, ou conversion
    silencieuse en une date ou une heure fausse.
    """
    with pytest.raises(ErreurDonneesInvalides, match="pas le nombre") as erreur:
        lire(valeur)
    assert conseil in erreur.value.message


@pytest.mark.cas_limite
def test_heure_entiere_ecrite_en_nombre_reste_une_heure():
    """Cas : une heure saisie « 8 », ou 8.0 dans une cellule de format Nombre.

    Risque : l'indice « nombre de série » refuserait aussi une heure entière
    légitime ; un nombre qui n'est pas une heure garde le message ordinaire.
    """
    assert v.lire_heure(8) == v.lire_heure(8.0) == v.lire_heure("8") == 8 * 60
    with pytest.raises(ErreurDonneesInvalides, match="formats acceptés"):
        v.lire_heure("25")
