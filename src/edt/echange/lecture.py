"""Lecture des fichiers envoyés : CSV, classeur XLSX, archive ZIP de CSV.

Un fichier devient un ou plusieurs **tableaux** : un en-tête et des lignes
numérotées comme dans le fichier d'origine (numéro de ligne du CSV ou de
l'onglet Excel), pour que le rapport désigne exactement la cellule fautive.

Lecture tolérante d'un CSV :

- encodage : BOM UTF-8 ou UTF-16 reconnu ; sinon UTF-8 ; sinon repli
  Windows-1252 **avec un avertissement** (les accents sont à vérifier) ;
- séparateur : point-virgule, virgule ou tabulation, détecté sur la ligne
  d'en-tête ;
- les lignes entièrement vides sont sautées (sans être gardées en mémoire) ;
  une valeur sans en-tête est gardée sous le nom « colonne N » (elle finira
  dans les colonnes non reconnues, jamais perdue en silence).

Bornes (le site est public : un fichier ne doit pas pouvoir épuiser la
mémoire ou le processeur du serveur). Au-delà, le tableau ou le fichier est
refusé avec un signalement d'erreur lisible, jamais tronqué en silence :

- archive ZIP : `MAX_MEMBRES_ARCHIVE` fichiers ; classeur XLSX (qui est
  aussi une archive) : `MAX_MEMBRES_CLASSEUR` parties ; dans les deux cas,
  `TAILLE_MAX_DECOMPRESSEE` octets une fois décompressé (tailles déclarées,
  qu'un membre ne peut pas dépasser à la lecture) ;
- tableau : `MAX_LIGNES_TABLEAU` lignes de données, `MAX_LIGNES_LUES`
  lignes lues (vides comprises), `MAX_COLONNES` colonnes ;
- envoi : `MAX_LIGNES_ENVOI` lignes de données pour tous les fichiers et
  onglets réunis (`Limites`).

Pièges signalés (avertissement, la lecture continue ; erreur pour le dernier) :

- cellules fusionnées d'un onglet : seule la première porte la valeur ;
- caractères de contrôle invisibles dans une cellule (gardés, à retirer s'ils
  ne sont pas voulus) ; dans un classeur, la notation `_xHHHH_` est d'abord
  restituée (`valeurs.restituer_controle`) ;
- colonne reconnue deux fois : seule la première est lue ;
- fichier sans aucune cellule remplie : erreur « rien à importer ».

Les noms des membres d'une archive sont assainis (`nom_sur` : ni « .. », ni
lecteur, ni racine) ; le contenu n'est jamais écrit sur le disque.

Un fichier `.ics` n'est pas un tableau : il est lu par `edt.echange.ical`.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
import zlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from edt.domaine.enumerations import Gravite
from edt.echange.format import Colonne, Feuille, TypeColonne, est_lisez_moi
from edt.echange.rapport import Apercu, Bilan, Signalement
from edt.echange.valeurs import (
    caracteres_de_controle,
    cle_code,
    restituer_controle,
    texte_cellule,
)
from edt.erreurs import ErreurDonneesInvalides

journal = logging.getLogger(__name__)

NB_LIGNES_APERCU = 20
SEPARATEURS = (";", "\t", ",")
"""Séparateurs reconnus, par ordre de préférence en cas d'égalité."""

MAX_LIGNES_TABLEAU = 50_000
"""Lignes de données au plus par tableau (un CSV ou un onglet)."""
MAX_LIGNES_LUES = 2 * MAX_LIGNES_TABLEAU
"""Lignes lues au plus par tableau, lignes vides comprises."""
MAX_LIGNES_ENVOI = 100_000
"""Lignes de données au plus pour tout un envoi (tous fichiers et onglets)."""
MAX_COLONNES = 500
"""Colonnes au plus par tableau."""
MAX_MEMBRES_ARCHIVE = 50
"""Fichiers au plus dans une archive ZIP."""
MAX_MEMBRES_CLASSEUR = 300
"""Parties au plus dans un classeur XLSX (le gabarit en compte une soixantaine)."""
TAILLE_MAX_DECOMPRESSEE = 20 * 1024 * 1024
"""Taille au plus d'une archive ou d'un classeur une fois décompressé (comme un envoi)."""
ERREURS_DECOMPRESSION = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    NotImplementedError,
    RuntimeError,
)
"""Erreurs d'un membre d'archive abîmé, chiffré ou compressé d'une façon inconnue."""


class TableauRefuse(Exception):  # noqa: N818 - refus d'un tableau, converti en signalement
    """Tableau ou fichier qui dépasse une borne : refusé en entier, avec ce message."""


@dataclass
class Limites:
    """Ce qu'il reste à lire pour un envoi : partagé par tous ses fichiers."""

    lignes_restantes: int = MAX_LIGNES_ENVOI


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


def lire_fichier(
    fichier: FichierEnvoye, limites: Limites | None = None
) -> tuple[FichierLu, list[Signalement]]:
    """Lit un fichier ; un fichier illisible ou trop grand donne un signalement d'erreur.

    `limites` est partagé par les fichiers d'un même envoi (défaut : un envoi
    réduit à ce fichier).
    """
    limites = limites if limites is not None else Limites()
    nature = nature_fichier(fichier)
    signalements: list[Signalement] = []
    if not fichier.contenu.strip():
        signalements.append(_erreur(fichier.nom, "Fichier vide : rien à importer."))
        return FichierLu(fichier.nom), signalements
    if nature == "xls":
        signalements.append(
            _erreur(
                fichier.nom,
                f"« {fichier.nom} » est un fichier Office binaire, illisible ici : un classeur "
                "protégé par un mot de passe (chiffré), ou l'ancien format Excel 97-2003 (.xls). "
                "Retirer le mot de passe, puis enregistrer le fichier au format .xlsx ou .csv.",
            )
        )
        return FichierLu(fichier.nom), signalements
    if nature == "ics":
        texte = decoder(fichier.contenu, fichier.nom, signalements)
        return FichierLu(fichier.nom, texte_ical=texte), signalements
    if nature == "xlsx":
        tableaux = tuple(lire_xlsx(fichier, signalements, limites))
    elif nature == "zip":
        tableaux = tuple(lire_zip(fichier, signalements, limites))
    else:
        texte = decoder(fichier.contenu, fichier.nom, signalements)
        if texte is None:
            return FichierLu(fichier.nom), signalements
        tableau = lire_csv_texte(fichier.nom, fichier.nom, texte, signalements, limites)
        tableaux = (tableau,) if tableau else ()
    if not tableaux and not any(s.gravite is Gravite.ERREUR for s in signalements):
        # Rien de lisible et aucune erreur pour l'expliquer : le dire, plutôt qu'un succès vide.
        signalements.append(
            _erreur(
                fichier.nom,
                "Aucune cellule remplie dans ce fichier (en dehors des explications) : rien "
                "à importer.",
            )
        )
    return FichierLu(fichier.nom, tableaux), signalements


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


def lire_csv_texte(
    fichier: str,
    nom: str,
    texte: str,
    signalements: list[Signalement],
    limites: Limites | None = None,
) -> Tableau | None:
    """Tableau d'un texte CSV (`None` s'il est vide, refusé ou illisible, avec un signalement)."""
    separateur = detecter_separateur(texte)
    lecteur = csv.reader(io.StringIO(texte, newline=""), delimiter=separateur)
    lignes = ((lecteur.line_num, cellules) for cellules in lecteur)
    try:
        return _tableau(fichier, nom, lignes, limites if limites is not None else Limites())
    except TableauRefuse as refus:
        signalements.append(Signalement(Gravite.ERREUR, str(refus), fichier, nom))
    except csv.Error as erreur:
        signalements.append(
            Signalement(
                Gravite.ERREUR,
                f"CSV illisible à la ligne {lecteur.line_num} ({erreur}) : fichier non importé.",
                fichier,
                nom,
                lecteur.line_num,
            )
        )
    return None


# --------------------------------------------------------------------------- #
# Classeur XLSX et archive ZIP
# --------------------------------------------------------------------------- #


def lire_xlsx(
    fichier: FichierEnvoye, signalements: list[Signalement], limites: Limites | None = None
) -> list[Tableau]:
    """Un tableau par onglet (l'onglet d'explications est sauté)."""
    from openpyxl import load_workbook

    limites = limites if limites is not None else Limites()
    if not _archive_acceptable(fichier, MAX_MEMBRES_CLASSEUR, "Classeur Excel", signalements):
        return []
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
            # Les dimensions déclarées par le fichier ne servent pas : chaque ligne garde sa
            # longueur réelle (un onglet qui se dit large de 16 384 colonnes reste borné).
            onglet.reset_dimensions()
            lignes = (
                (
                    numero,
                    tuple(restituer_controle(c) if isinstance(c, str) else c for c in cellules),
                )
                for numero, cellules in enumerate(onglet.iter_rows(values_only=True), start=1)
            )
            try:
                tableau = _tableau(fichier.nom, onglet.title, lignes, limites)
            except TableauRefuse as refus:
                signalements.append(
                    Signalement(Gravite.ERREUR, str(refus), fichier.nom, onglet.title)
                )
                continue
            if tableau is not None:
                tableaux.append(tableau)
                _signaler_fusions(fichier, onglet, signalements)
    except ERREURS_DECOMPRESSION as erreur:
        journal.warning("Classeur %s : partie illisible (%r)", fichier.nom, erreur)
        signalements.append(
            _erreur(fichier.nom, f"Classeur Excel abîmé ({erreur}) : lecture interrompue.")
        )
    finally:
        classeur.close()
    return tableaux


_FUSION = re.compile(rb'<(?:\w+:)?mergeCell\b[^>]*\bref="([^"]+)"')


def _signaler_fusions(fichier: FichierEnvoye, onglet: Any, signalements: list[Signalement]) -> None:
    """Avertit si l'onglet a des cellules fusionnées : seule la première porte la valeur.

    La lecture en continu d'openpyxl ne voit pas les fusions : elles se lisent
    dans le XML de l'onglet (taille déjà bornée par `_archive_acceptable`).
    """
    chemin = getattr(onglet, "_worksheet_path", None)
    if not chemin:
        journal.debug("Onglet %s : chemin interne inconnu, fusions non examinées", onglet.title)
        return
    with zipfile.ZipFile(io.BytesIO(fichier.contenu)) as archive:
        fusions = [ref.decode("ascii", "replace") for ref in _FUSION.findall(archive.read(chemin))]
    if fusions:
        apercu = ", ".join(fusions[:5]) + (" ..." if len(fusions) > 5 else "")
        signalements.append(
            Signalement(
                Gravite.AVERTISSEMENT,
                f"L'onglet contient des cellules fusionnées ({apercu}) : seule la première "
                "cellule d'une fusion porte la valeur, les autres sont lues vides. Défusionner "
                "et recopier la valeur dans chaque ligne.",
                fichier.nom,
                onglet.title,
            )
        )


def lire_zip(
    fichier: FichierEnvoye, signalements: list[Signalement], limites: Limites | None = None
) -> list[Tableau]:
    """Les CSV et classeurs d'une archive (le fichier d'explications est sauté)."""
    limites = limites if limites is not None else Limites()
    if not _archive_acceptable(fichier, MAX_MEMBRES_ARCHIVE, "Archive ZIP", signalements):
        return []
    tableaux: list[Tableau] = []
    with zipfile.ZipFile(io.BytesIO(fichier.contenu)) as archive:  # lisible : vérifiée avant
        for membre in archive.infolist():
            if membre.is_dir() or est_lisez_moi(nom_sur(membre.filename).rsplit("/", 1)[-1]):
                continue
            nom_complet = f"{fichier.nom}/{nom_sur(membre.filename)}"
            try:
                contenu = archive.read(membre)
            except ERREURS_DECOMPRESSION as erreur:
                journal.warning("Archive %s : membre illisible (%r)", nom_complet, erreur)
                signalements.append(
                    _erreur(nom_complet, f"Fichier de l'archive illisible ({erreur}) : ignoré.")
                )
                continue
            interne = FichierEnvoye(nom_complet, contenu)
            nature = nature_fichier(interne)
            if nature == "xlsx":
                tableaux.extend(lire_xlsx(interne, signalements, limites))
            elif nature == "csv":
                texte = decoder(interne.contenu, nom_complet, signalements)
                if texte is not None:
                    tableau = lire_csv_texte(
                        nom_complet, nom_sur(membre.filename), texte, signalements, limites
                    )
                    if tableau is not None:
                        tableaux.append(tableau)
            else:
                conseil = (
                    "un agenda iCal s'envoie à part, pour importer un emploi du temps existant"
                    if nature == "ics"
                    else "une archive ne contient que des fichiers CSV ou XLSX"
                )
                signalements.append(
                    _erreur(
                        nom_complet,
                        f"Fichier de type {nature} non pris en charge dans une archive : "
                        f"{conseil}. Fichier ignoré.",
                    )
                )
    return tableaux


# --------------------------------------------------------------------------- #
# Lignes typées selon une feuille du format
# --------------------------------------------------------------------------- #


@dataclass
class LigneTypee:
    """Une ligne lue et convertie : valeurs typées par colonne, colonnes non reconnues."""

    fichier: str
    feuille: str
    numero: int
    valeurs: dict[str, Any]
    presentes: frozenset[str]
    extra: dict[str, str] = field(default_factory=dict)

    def __getitem__(self, nom: str) -> Any:
        return self.valeurs.get(nom)

    @property
    def position(self) -> str:
        """« salles.csv, ligne 4 »."""
        return f"{self.fichier}, ligne {self.numero}"


def preparer_tableau(
    tableau: Tableau,
    feuille: Feuille,
    bilan: Bilan,
    signalements: list[Signalement],
    extra_conserve: bool = True,
) -> list[LigneTypee]:
    """Lit les lignes d'un tableau selon la feuille ; les lignes illisibles sont rejetées.

    Toutes les valeurs illisibles d'une ligne sont signalées (pas seulement la
    première). Une colonne obligatoire absente rejette tout le tableau.
    `extra_conserve` dit si les colonnes non reconnues seront gardées (pour
    le message du rapport).
    """
    correspondance: list[Colonne | None] = []
    vues: set[str] = set()
    inconnues: list[str] = []
    doublons: set[int] = set()
    for indice, entete in enumerate(tableau.entetes):
        colonne = feuille.reconnaitre_entete(entete)
        if colonne is not None and colonne.nom in vues:
            # Une seule trace, et la valeur n'est pas rangée parmi les colonnes inconnues.
            signalements.append(
                Signalement(
                    Gravite.AVERTISSEMENT,
                    f"Colonne « {entete} » en double (déjà lue sous « {colonne.nom} ») : seule "
                    "la première est lue, celle-ci est ignorée.",
                    tableau.fichier,
                    tableau.nom,
                    tableau.ligne_entetes,
                    entete,
                )
            )
            doublons.add(indice)
            colonne = None
        elif colonne is None:
            inconnues.append(entete)
        else:
            vues.add(colonne.nom)
        correspondance.append(colonne)

    manquantes = [nom for nom in feuille.obligatoires if nom not in vues]
    if manquantes:
        signalements.append(
            Signalement(
                Gravite.ERREUR,
                f"Colonne(s) obligatoire(s) absente(s) : {', '.join(manquantes)}. Aucune des "
                f"{len(tableau.lignes)} ligne(s) de ce tableau n'est importée.",
                tableau.fichier,
                tableau.nom,
                tableau.ligne_entetes,
                ", ".join(manquantes),
            )
        )
        bilan.ajouter("rejetes", len(tableau.lignes))
        return []
    if inconnues:
        suite = (
            "leurs valeurs sont conservées avec chaque ligne (champ « extra »)."
            if extra_conserve
            else "leurs valeurs ne sont pas conservées : la table ne prévoit pas de champ pour "
            "des colonnes supplémentaires."
        )
        signalements.append(
            Signalement(
                Gravite.AVERTISSEMENT,
                f"Colonne(s) non reconnue(s) : {', '.join(inconnues)} ; {suite}",
                tableau.fichier,
                tableau.nom,
                tableau.ligne_entetes,
            )
        )

    presentes = frozenset(vues)
    lignes = []
    for brute in tableau.lignes:
        valeurs: dict[str, Any] = {}
        extra: dict[str, str] = {}
        erreurs: list[tuple[str, str]] = []
        for indice, (colonne, entete, cellule) in enumerate(
            zip(correspondance, tableau.entetes, brute.cellules, strict=True)
        ):
            if indice in doublons:
                continue
            if colonne is None:
                texte = texte_cellule(cellule)
                if texte:
                    extra[entete] = texte
                continue
            if colonne.export_seulement:
                continue  # information écrite par l'export (phrase d'une règle) : ignorée
            if isinstance(cellule, str) and (codes := caracteres_de_controle(cellule)):
                signalements.append(
                    Signalement(
                        Gravite.AVERTISSEMENT,
                        f"Caractère(s) de contrôle invisible(s) {', '.join(codes)} dans la "
                        "cellule (souvent un copier-coller) : à retirer s'il n'est pas voulu.",
                        tableau.fichier,
                        tableau.nom,
                        brute.numero,
                        colonne.nom,
                    )
                )
            try:
                valeur = colonne.lire(cellule)
            except ErreurDonneesInvalides as erreur:
                erreurs.append((colonne.nom, erreur.message))
                continue
            if valeur is None and colonne.obligatoire:
                erreurs.append((colonne.nom, "Valeur obligatoire manquante."))
                continue
            valeurs[colonne.nom] = colonne.defaut if valeur is None else valeur
        for colonne in feuille.colonnes:
            valeurs.setdefault(colonne.nom, colonne.defaut)
        for colonne in feuille.colonnes:
            if colonne.defaut_de is not None and valeurs[colonne.nom] is None:
                valeurs[colonne.nom] = valeurs.get(colonne.defaut_de)
        if erreurs:
            bilan.ajouter("rejetes")
            for nom, message in erreurs:
                signalements.append(
                    Signalement(
                        Gravite.ERREUR, message, tableau.fichier, tableau.nom, brute.numero, nom
                    )
                )
            continue
        lignes.append(
            LigneTypee(tableau.fichier, tableau.nom, brute.numero, valeurs, presentes, extra)
        )
    return lignes


def comparable(colonne: Colonne, valeur: Any) -> Any:
    """Forme d'une valeur pour comparer deux lignes (codes sans casse, listes sans ordre)."""
    if valeur is None:
        return None
    if colonne.type is TypeColonne.CODE:
        return cle_code(valeur)
    if colonne.type is TypeColonne.LISTE:
        return frozenset(cle_code(x) for x in valeur)
    return valeur


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #


def nom_sur(chemin: str) -> str:
    """Nom d'un membre d'archive sans « .. », lecteur ni racine : « ../../etc/x.csv » -> « etc/x.csv ».

    Le contenu d'une archive n'est jamais écrit sur le disque ; le nom ne sert
    qu'à reconnaître la feuille et à l'afficher dans le rapport.
    """
    morceaux = [
        m for m in re.split(r"[/\\]", chemin) if m not in ("", ".", "..") and not m.endswith(":")
    ]
    return "/".join(morceaux) or "sans_nom"


def _archive_acceptable(
    fichier: FichierEnvoye, max_membres: int, nature: str, signalements: list[Signalement]
) -> bool:
    """Vérifie, avant toute décompression, le nombre de membres et la taille décompressée.

    Les tailles déclarées suffisent : à la lecture, un membre qui dépasse sa
    taille déclarée lève une erreur au lieu de déborder.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(fichier.contenu)) as archive:
            membres = archive.infolist()
    except (zipfile.BadZipFile, EOFError, ValueError) as erreur:
        signalements.append(_erreur(fichier.nom, f"{nature} illisible ({erreur})."))
        return False
    if len(membres) > max_membres:
        signalements.append(
            _erreur(
                fichier.nom,
                f"{nature} refusé : {len(membres)} fichiers à l'intérieur, {max_membres} au "
                "plus. Envoyer les fichiers séparément.",
            )
        )
        return False
    taille = sum(membre.file_size for membre in membres)
    if taille > TAILLE_MAX_DECOMPRESSEE:
        journal.warning("%s %s refusé : %d octets décompressés", nature, fichier.nom, taille)
        signalements.append(
            _erreur(
                fichier.nom,
                f"{nature} refusé : {taille / 1024 / 1024:.0f} Mo une fois décompressé, "
                f"{TAILLE_MAX_DECOMPRESSEE // (1024 * 1024)} Mo au plus. Découper l'envoi.",
            )
        )
        return False
    return True


def _tableau(
    fichier: str,
    nom: str,
    lignes: Iterable[tuple[int, Sequence[Any]]],
    limites: Limites,
) -> Tableau | None:
    """Construit un tableau au fil de la lecture : la première ligne non vide est l'en-tête.

    Les lignes vides ne sont pas gardées. Lève `TableauRefuse` dès qu'une
    borne est dépassée (le reste du fichier n'est pas lu).
    """
    entetes_bruts: Sequence[Any] | None = None
    ligne_entetes = 0
    donnees: list[tuple[int, tuple[Any, ...]]] = []
    utiles: set[int] = set()
    for lues, (numero, cellules) in enumerate(lignes, start=1):
        if lues > MAX_LIGNES_LUES:
            raise TableauRefuse(
                f"Tableau « {nom} » refusé : plus de {MAX_LIGNES_LUES} lignes (lignes vides "
                "comprises). Découper le fichier."
            )
        cellules = _borner_colonnes(nom, cellules)
        remplies = [i for i, cellule in enumerate(cellules) if texte_cellule(cellule)]
        if not remplies:
            continue
        utiles.update(remplies)
        if entetes_bruts is None:
            entetes_bruts, ligne_entetes = cellules, numero
            continue
        if len(donnees) >= MAX_LIGNES_TABLEAU:
            raise TableauRefuse(
                f"Tableau « {nom} » refusé : plus de {MAX_LIGNES_TABLEAU} lignes de données. "
                "Découper le fichier."
            )
        if len(donnees) >= limites.lignes_restantes:
            raise TableauRefuse(
                f"Tableau « {nom} » refusé : l'envoi dépasse {MAX_LIGNES_ENVOI} lignes de "
                "données au total. Importer en plusieurs fois."
            )
        donnees.append((numero, tuple(cellules[: remplies[-1] + 1])))
    if entetes_bruts is None:
        return None
    limites.lignes_restantes -= len(donnees)
    colonnes = sorted(utiles)
    return Tableau(
        fichier=fichier,
        nom=nom,
        entetes=tuple(
            (texte_cellule(entetes_bruts[i]) if i < len(entetes_bruts) else None)
            or f"colonne {i + 1}"
            for i in colonnes
        ),
        ligne_entetes=ligne_entetes,
        lignes=tuple(
            LigneBrute(numero, tuple(cellules[i] if i < len(cellules) else None for i in colonnes))
            for numero, cellules in donnees
        ),
    )


def _borner_colonnes(nom: str, cellules: Sequence[Any]) -> Sequence[Any]:
    """Les `MAX_COLONNES` premières cellules ; refuse le tableau si une valeur est au-delà."""
    if len(cellules) <= MAX_COLONNES:
        return cellules
    if any(texte_cellule(cellule) for cellule in cellules[MAX_COLONNES:]):
        raise TableauRefuse(f"Tableau « {nom} » refusé : plus de {MAX_COLONNES} colonnes remplies.")
    return cellules[:MAX_COLONNES]


def _erreur(fichier: str, message: str) -> Signalement:
    return Signalement(Gravite.ERREUR, message, fichier=fichier)
