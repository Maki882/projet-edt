"""Écriture des fichiers d'échange : CSV, classeur XLSX à onglets, archive ZIP de CSV.

Deux présentations équivalentes du même format (`edt.echange.format`) :

- un classeur XLSX : un onglet « Lisez-moi » en tête (explications et
  description de chaque colonne), puis un onglet par feuille ; les en-têtes
  des colonnes obligatoires sont en gras et chaque en-tête porte son aide en
  commentaire ;
- une archive ZIP : un CSV par feuille (UTF-8 avec BOM, séparateur
  point-virgule, ce qu'Excel ouvre directement) et un fichier LISEZ-MOI.txt.

Les deux se relisent par `edt.echange.lecture` : exporter puis réimporter
redonne les mêmes données.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from edt.echange.format import (
    ENTETES_DESCRIPTION,
    Feuille,
    TypeColonne,
    lignes_description,
)

TITRE_LISEZ_MOI = "Lisez-moi"
NOM_LISEZ_MOI_TXT = "LISEZ-MOI.txt"
_DEBUTS_FORMULE = ("=", "+", "-", "@")

Contenu = Sequence[tuple[Feuille, Sequence[Mapping[str, Any]]]]
"""Feuilles à écrire, chacune avec ses enregistrements (valeurs typées par nom de colonne)."""


def csv_octets(entetes: Sequence[str], lignes: Iterable[Sequence[Any]]) -> bytes:
    """CSV en UTF-8 avec BOM, séparateur point-virgule, fins de ligne Windows."""
    tampon = io.StringIO()
    ecrivain = csv.writer(tampon, delimiter=";", lineterminator="\r\n")
    ecrivain.writerow(entetes)
    for ligne in lignes:
        ecrivain.writerow(["" if valeur is None else valeur for valeur in ligne])
    return tampon.getvalue().encode("utf-8-sig")


def lignes_texte(feuille: Feuille, enregistrements: Sequence[Mapping[str, Any]]) -> list[list[str]]:
    """Enregistrements typés -> lignes de texte, dans l'ordre des colonnes de la feuille."""
    return [
        [colonne.ecrire(enregistrement.get(colonne.nom)) for colonne in feuille.colonnes]
        for enregistrement in enregistrements
    ]


def archive_zip(contenu: Contenu, lisez_moi: Sequence[str]) -> bytes:
    """Archive ZIP : un CSV par feuille, plus LISEZ-MOI.txt."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        texte = "\r\n\r\n".join(lisez_moi) + "\r\n"
        archive.writestr(NOM_LISEZ_MOI_TXT, texte.encode("utf-8-sig"))
        for feuille, enregistrements in contenu:
            archive.writestr(
                f"{feuille.nom}.csv",
                csv_octets(feuille.entetes, lignes_texte(feuille, enregistrements)),
            )
    return tampon.getvalue()


def classeur_xlsx(contenu: Contenu, lisez_moi: Sequence[str]) -> bytes:
    """Classeur XLSX : onglet « Lisez-moi », puis un onglet par feuille."""
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill

    gras = Font(bold=True)
    fond_obligatoire = PatternFill("solid", fgColor="DDE7F3")
    classeur = Workbook()
    explications = classeur.active
    explications.title = TITRE_LISEZ_MOI
    explications.column_dimensions["A"].width = 24
    for colonne in "BCDEFG":
        explications.column_dimensions[colonne].width = 22
    explications.column_dimensions["F"].width = 70
    rangee = 1
    for paragraphe in lisez_moi:
        cellule = explications.cell(rangee, 1, _texte_sur(paragraphe))
        cellule.alignment = Alignment(wrap_text=False)
        rangee += 1
    for feuille, _ in contenu:
        rangee += 1
        explications.cell(rangee, 1, f"Feuille {feuille.nom}").font = gras
        rangee += 1
        for numero, entete in enumerate(ENTETES_DESCRIPTION, start=1):
            explications.cell(rangee, numero, entete).font = gras
        for ligne in lignes_description(feuille):
            rangee += 1
            for numero, valeur in enumerate(ligne, start=1):
                explications.cell(rangee, numero, _texte_sur(valeur))

    for feuille, enregistrements in contenu:
        onglet = classeur.create_sheet(feuille.nom)
        onglet.freeze_panes = "A2"
        for numero, colonne in enumerate(feuille.colonnes, start=1):
            entete = onglet.cell(1, numero, colonne.nom)
            if colonne.obligatoire:
                entete.font = gras
                entete.fill = fond_obligatoire
            aide = f"{colonne.libelle}. {colonne.aide_complete}".strip()
            if colonne.exemple:
                aide += f" Exemple : {colonne.exemple}"
            entete.comment = Comment(aide, "EDT")
            largeur = max(len(colonne.nom), len(colonne.exemple or "")) + 4
            onglet.column_dimensions[entete.column_letter].width = min(max(largeur, 12), 40)
        for rangee, enregistrement in enumerate(enregistrements, start=2):
            for numero, colonne in enumerate(feuille.colonnes, start=1):
                valeur = enregistrement.get(colonne.nom)
                if valeur is None:
                    continue
                if colonne.type is TypeColonne.DATE and isinstance(valeur, dt.date):
                    cellule = onglet.cell(rangee, numero, valeur)
                    cellule.number_format = "DD/MM/YYYY"
                elif colonne.type is TypeColonne.ENTIER and isinstance(valeur, int):
                    onglet.cell(rangee, numero, valeur)
                else:
                    onglet.cell(rangee, numero, _texte_sur(colonne.ecrire(valeur)))
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


def _texte_sur(texte: str) -> str:
    """Un texte qui commence comme une formule (« = », « + »...) n'en devient pas une.

    openpyxl écrit en formule toute chaîne qui commence par « = », et un
    tableur interprète de même « + », « - » et « @ ». On ajoute alors une
    espace insécable en tête, que la lecture retire (`valeurs.texte_cellule`).
    """
    if texte and texte.startswith(_DEBUTS_FORMULE):
        return " " + texte
    return texte
