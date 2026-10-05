"""Lecture tolérante : encodages, séparateurs, dates, heures, oui/non, codes, classeurs."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest

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


def test_archive_zip_de_csv():
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("campus.csv", TEXTE)
        archive.writestr("LISEZ-MOI.txt", "explications")
    tableaux, signalements = _lire("donnees.zip", tampon.getvalue())
    assert [t.nom for t in tableaux] == ["campus.csv"]
    assert tableaux[0].fichier == "donnees.zip/campus.csv"
    assert signalements == []


def test_ancien_format_excel_refuse_clairement():
    tableaux, signalements = _lire("vieux.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
    assert not tableaux and ".xlsx" in signalements[0].message


def test_toutes_les_feuilles_ont_un_identifiant():
    assert all(f.identifiant for f in FEUILLES)
