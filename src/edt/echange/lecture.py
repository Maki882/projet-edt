"""Lecture des fichiers envoyés : CSV, classeur XLSX, archive ZIP de CSV.

Un fichier devient un ou plusieurs **tableaux** : un en-tête et des lignes
numérotées comme dans le fichier d'origine (numéro de ligne du CSV ou de
l'onglet Excel), pour que le rapport désigne exactement la cellule fautive.

Lecture tolérante d'un CSV :

- encodage : BOM UTF-8 ou UTF-16 reconnu ; sinon UTF-8 ; sinon repli
  Windows-1252 **avec un avertissement** (les accents sont à vérifier) ;
- séparateur : point-virgule, virgule ou tabulation, détecté sur la ligne
  d'en-tête ;
- les lignes entièrement vides sont sautées ; une valeur sans en-tête est
  gardée sous le nom « colonne N » (elle finira dans les colonnes non
  reconnues, jamais perdue en silence).

Un fichier `.ics` n'est pas un tableau : il est lu par `edt.echange.ical`.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from typing import Any

from edt.domaine.enumerations import Gravite
from edt.echange.format import est_lisez_moi
from edt.echange.rapport import Apercu, Signalement
from edt.echange.valeurs import texte_cellule

journal = logging.getLogger(__name__)

NB_LIGNES_APERCU = 20
SEPARATEURS = (";", "\t", ",")
"""Séparateurs reconnus, par ordre de préférence en cas d'égalité."""


@dataclass(frozen=True)
class FichierEnvoye:
    """Un fichier reçu (téléversé ou lu sur le disque) : son nom et son contenu brut."""

    nom: str
    contenu: bytes


@dataclass(frozen=True)
class LigneBrute:
    """Une ligne de données : son numéro dans le fichier et ses cellules (texte ou valeur Excel)."""

    numero: int
    cellules: tuple[Any, ...]


@dataclass(frozen=True)
class Tableau:
    """Un tableau lu : un CSV, ou un onglet d'un classeur.

    - `fichier` : nom du fichier envoyé (pour le rapport) ;
    - `nom` : nom qui sert à reconnaître la feuille (nom du CSV ou de l'onglet) ;
    - `entetes` : en-têtes, une valeur vide devenant « colonne N » ;
    - `lignes` : lignes de données non vides.
    """

    fichier: str
    nom: str
    entetes: tuple[str, ...]
    ligne_entetes: int
    lignes: tuple[LigneBrute, ...]

    def apercu(self, feuille: str | None = None, nombre: int = NB_LIGNES_APERCU) -> Apercu:
        """Les `nombre` premières lignes, en texte, pour l'aperçu avant import."""
        lignes = tuple(
            tuple(texte_cellule(c) or "" for c in ligne.cellules) for ligne in self.lignes[:nombre]
        )
        return Apercu(
            fichier=self.fichier,
            feuille=feuille or self.nom,
            entetes=self.entetes,
            lignes=lignes,
            nb_lignes=len(self.lignes),
        )


@dataclass(frozen=True)
class FichierLu:
    """Résultat de la lecture d'un fichier : ses tableaux, ou son texte iCal."""

    nom: str
    tableaux: tuple[Tableau, ...] = ()
    texte_ical: str | None = None


def nature_fichier(fichier: FichierEnvoye) -> str:
    """« xlsx », « zip », « ics », « xls » ou « csv », d'après le contenu puis l'extension."""
    nom = fichier.nom.lower()
    debut = fichier.contenu[:4096]
    if debut.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(fichier.contenu)) as archive:
                noms = set(archive.namelist())
        except zipfile.BadZipFile:
            return "zip"
        return "xlsx" if "xl/workbook.xml" in noms else "zip"
    if debut.startswith(b"\xd0\xcf\x11\xe0") or nom.endswith(".xls"):
        return "xls"
    texte_debut = debut.lstrip(b"\xef\xbb\xbf").lstrip()
    if nom.endswith(".ics") or texte_debut[:15].upper() == b"BEGIN:VCALENDAR":
        return "ics"
    return "csv"


def lire_fichier(fichier: FichierEnvoye) -> tuple[FichierLu, list[Signalement]]:
    """Lit un fichier ; un fichier illisible donne un signalement d'erreur et aucun tableau."""
    nature = nature_fichier(fichier)
    signalements: list[Signalement] = []
    if not fichier.contenu.strip():
        signalements.append(_erreur(fichier.nom, "Fichier vide : rien à importer."))
        return FichierLu(fichier.nom), signalements
    if nature == "xls":
        signalements.append(
            _erreur(
                fichier.nom,
                "Format Excel 97-2003 (.xls) non pris en charge : enregistrer le fichier au "
                "format .xlsx ou .csv.",
            )
        )
        return FichierLu(fichier.nom), signalements
    if nature == "ics":
        texte = decoder(fichier.contenu, fichier.nom, signalements)
        return FichierLu(fichier.nom, texte_ical=texte), signalements
    if nature == "xlsx":
        return FichierLu(fichier.nom, tuple(lire_xlsx(fichier, signalements))), signalements
    if nature == "zip":
        return FichierLu(fichier.nom, tuple(lire_zip(fichier, signalements))), signalements
    texte = decoder(fichier.contenu, fichier.nom, signalements)
    if texte is None:
        return FichierLu(fichier.nom), signalements
    tableau = lire_csv_texte(fichier.nom, fichier.nom, texte)
    return FichierLu(fichier.nom, (tableau,) if tableau else ()), signalements


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #


def decoder(contenu: bytes, nom: str, signalements: list[Signalement]) -> str | None:
    """Texte d'un fichier : BOM, puis UTF-8, puis repli Windows-1252 avec avertissement.

    Renvoie `None` (avec un signalement d'erreur) si aucun encodage ne convient.
    """
    if contenu.startswith(b"\xef\xbb\xbf"):
        return contenu[3:].decode("utf-8", errors="strict")
    if contenu.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return contenu.decode("utf-16")
        except UnicodeDecodeError as erreur:
            signalements.append(_erreur(nom, f"Fichier UTF-16 illisible ({erreur.reason})."))
            return None
    try:
        return contenu.decode("utf-8")
    except UnicodeDecodeError:
        journal.info("Fichier %s : pas en UTF-8, lecture en Windows-1252", nom)
    try:
        texte = contenu.decode("cp1252")
    except UnicodeDecodeError as erreur:
        signalements.append(
            _erreur(
                nom,
                "Encodage illisible : le fichier n'est ni en UTF-8 ni en Windows-1252 "
                f"(octet invalide en position {erreur.start}). L'enregistrer en « CSV UTF-8 ».",
            )
        )
        return None
    signalements.append(
        Signalement(
            Gravite.AVERTISSEMENT,
            "Fichier lu en Windows-1252 (il n'est pas en UTF-8) : vérifier les accents dans "
            "l'aperçu ; à défaut, l'enregistrer en « CSV UTF-8 ».",
            fichier=nom,
        )
    )
    return texte


def detecter_separateur(texte: str) -> str:
    """Séparateur le plus fréquent sur la première ligne non vide (« ; » à égalité)."""
    premiere = next((ligne for ligne in texte.splitlines() if ligne.strip()), "")
    comptes = {separateur: premiere.count(separateur) for separateur in SEPARATEURS}
    meilleur = max(comptes.values())
    if meilleur == 0:
        return ";"
    return next(separateur for separateur in SEPARATEURS if comptes[separateur] == meilleur)


def lire_csv_texte(fichier: str, nom: str, texte: str) -> Tableau | None:
    """Tableau d'un texte CSV (`None` s'il ne contient aucune ligne non vide)."""
    separateur = detecter_separateur(texte)
    lecteur = csv.reader(io.StringIO(texte, newline=""), delimiter=separateur)
    lignes = []
    for cellules in lecteur:
        lignes.append((lecteur.line_num, tuple(cellules)))
    return _tableau(fichier, nom, lignes)


# --------------------------------------------------------------------------- #
# Classeur XLSX et archive ZIP
# --------------------------------------------------------------------------- #


def lire_xlsx(fichier: FichierEnvoye, signalements: list[Signalement]) -> list[Tableau]:
    """Un tableau par onglet (l'onglet d'explications est sauté)."""
    from openpyxl import load_workbook

    try:
        classeur = load_workbook(io.BytesIO(fichier.contenu), read_only=True, data_only=True)
    except Exception as erreur:  # openpyxl lève des erreurs variées sur un fichier abîmé
        journal.exception("Classeur %s illisible", fichier.nom)
        signalements.append(
            _erreur(fichier.nom, f"Classeur Excel illisible ({type(erreur).__name__} : {erreur}).")
        )
        return []
    tableaux = []
    try:
        for onglet in classeur.worksheets:
            if est_lisez_moi(onglet.title):
                continue
            lignes = [
                (numero, tuple(cellules))
                for numero, cellules in enumerate(onglet.iter_rows(values_only=True), start=1)
            ]
            tableau = _tableau(fichier.nom, onglet.title, lignes)
            if tableau is not None:
                tableaux.append(tableau)
    finally:
        classeur.close()
    return tableaux


def lire_zip(fichier: FichierEnvoye, signalements: list[Signalement]) -> list[Tableau]:
    """Les CSV et classeurs d'une archive (le fichier d'explications est sauté)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(fichier.contenu))
    except zipfile.BadZipFile as erreur:
        signalements.append(_erreur(fichier.nom, f"Archive ZIP illisible ({erreur})."))
        return []
    tableaux: list[Tableau] = []
    with archive:
        for membre in archive.infolist():
            if membre.is_dir() or est_lisez_moi(membre.filename.rsplit("/", 1)[-1]):
                continue
            nom_complet = f"{fichier.nom}/{membre.filename}"
            interne = FichierEnvoye(nom_complet, archive.read(membre))
            nature = nature_fichier(interne)
            if nature == "xlsx":
                tableaux.extend(lire_xlsx(interne, signalements))
            elif nature == "csv":
                texte = decoder(interne.contenu, nom_complet, signalements)
                if texte is not None:
                    tableau = lire_csv_texte(nom_complet, membre.filename, texte)
                    if tableau is not None:
                        tableaux.append(tableau)
            else:
                signalements.append(
                    _erreur(
                        nom_complet,
                        f"Fichier de type {nature} non pris en charge dans une archive.",
                    )
                )
    return tableaux


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #


def _tableau(fichier: str, nom: str, lignes: list[tuple[int, tuple[Any, ...]]]) -> Tableau | None:
    """Construit un tableau : la première ligne non vide est l'en-tête."""
    non_vides = [(numero, cellules) for numero, cellules in lignes if _non_vide(cellules)]
    if not non_vides:
        return None
    ligne_entetes, entetes_bruts = non_vides[0]
    donnees = non_vides[1:]
    largeur = (
        max(len(entetes_bruts), *(len(cellules) for _, cellules in donnees))
        if donnees
        else len(entetes_bruts)
    )
    entetes = tuple(
        (texte_cellule(entetes_bruts[i]) if i < len(entetes_bruts) else None) or f"colonne {i + 1}"
        for i in range(largeur)
    )
    # Une colonne sans en-tête et sans aucune valeur (cellules vides de fin de ligne) disparaît.
    utiles = [
        i
        for i in range(largeur)
        if i < len(entetes_bruts)
        and texte_cellule(entetes_bruts[i])
        or any(i < len(c) and texte_cellule(c[i]) for _, c in donnees)
    ]
    return Tableau(
        fichier=fichier,
        nom=nom,
        entetes=tuple(entetes[i] for i in utiles),
        ligne_entetes=ligne_entetes,
        lignes=tuple(
            LigneBrute(numero, tuple(cellules[i] if i < len(cellules) else None for i in utiles))
            for numero, cellules in donnees
        ),
    )


def _non_vide(cellules: tuple[Any, ...]) -> bool:
    return any(texte_cellule(cellule) for cellule in cellules)


def _erreur(fichier: str, message: str) -> Signalement:
    return Signalement(Gravite.ERREUR, message, fichier=fichier)
