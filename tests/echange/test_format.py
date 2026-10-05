"""Le format d'échange : gabarit XLSX et ZIP, documentation générée depuis la description."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from edt.db.moteur import session_base
from edt.echange.format import FEUILLES, documentation_markdown
from edt.services import echange as service

DOCUMENTATION = Path(__file__).parents[2] / "docs" / "import-export.md"


def test_gabarit_xlsx(base):
    with session_base() as session:
        fichier = service.produire_gabarit(session, "xlsx")

    assert fichier.nom == "gabarit-edt.xlsx"
    classeur = load_workbook(io.BytesIO(fichier.contenu))
    assert classeur.sheetnames == ["Lisez-moi", *(f.nom for f in FEUILLES)]
    salles = classeur["salles"]
    entetes = [cellule.value for cellule in salles[1]]
    assert entetes == [
        "code",
        "campus",
        "capacite",
        "type",
        "batiment",
        "etage",
        "equipements",
        "accessible",
    ]
    assert salles["A1"].font.bold and not salles["E1"].font.bold  # obligatoire / facultative
    assert "Exemple : N204" in salles["A1"].comment.text
    assert salles.max_row == 1  # gabarit vide
    explications = "\n".join(
        str(c.value) for ligne in classeur["Lisez-moi"].iter_rows() for c in ligne if c.value
    )
    assert "AMPHI (Amphithéâtre)" in explications  # codes des listes configurables
    assert "Ordre de traitement" in explications


def test_gabarit_zip(base):
    with session_base() as session:
        fichier = service.produire_gabarit(session, "zip")

    with zipfile.ZipFile(io.BytesIO(fichier.contenu)) as archive:
        noms = archive.namelist()
        salles = archive.read("salles.csv").decode("utf-8-sig")
    assert noms == ["LISEZ-MOI.txt", *(f"{f.nom}.csv" for f in FEUILLES)]
    assert salles == "code;campus;capacite;type;batiment;etage;equipements;accessible\r\n"


def test_documentation_a_jour():
    """docs/import-export.md reprend exactement les tableaux générés depuis la description."""
    assert documentation_markdown() in DOCUMENTATION.read_text(encoding="utf-8")
