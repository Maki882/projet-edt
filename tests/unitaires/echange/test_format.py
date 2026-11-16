"""Le format d'échange : gabarit XLSX et ZIP, documentation générée depuis la description."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.echange.format import (
    FEUILLE_SEANCES,
    FEUILLES,
    LONGUEURS,
    TypeColonne,
    documentation_markdown,
)
from edt.services import echange as service

DOCUMENTATION = Path(__file__).parents[3] / "docs" / "import-export.md"


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


# --------------------------------------------------------------------------- #
# Cas limites : longueurs des textes (banque de tests)
# --------------------------------------------------------------------------- #

COLONNES_DU_MODELE = {
    ("annees", "libelle"): m.AnneeScolaire.libelle,
    ("semestres", "annee"): m.AnneeScolaire.libelle,
    ("semestres", "code"): m.Semestre.code,
    ("semestres", "libelle"): m.Semestre.libelle,
    ("campus", "code"): m.Campus.code,
    ("campus", "nom"): m.Campus.nom,
    ("campus", "prefixe_salle"): m.Campus.prefixe_salle,
    ("trajets", "campus_a"): m.Campus.code,
    ("trajets", "campus_b"): m.Campus.code,
    ("salles", "code"): m.Salle.code,
    ("salles", "campus"): m.Campus.code,
    ("salles", "type"): m.TypeSalle.code,
    ("salles", "batiment"): m.Salle.batiment,
    ("salles", "equipements"): m.Equipement.code,
    ("enseignants", "identifiant"): m.Enseignant.identifiant,
    ("enseignants", "civilite"): m.Enseignant.civilite,
    ("enseignants", "nom"): m.Enseignant.nom,
    ("enseignants", "prenom"): m.Enseignant.prenom,
    ("groupes", "annee"): m.AnneeScolaire.libelle,
    ("groupes", "code"): m.Groupe.code,
    ("groupes", "libelle"): m.Groupe.libelle,
    ("groupes", "parent"): m.Groupe.code,
    ("groupes", "chevauche"): m.Groupe.code,
    ("jours_fermes", "annee"): m.AnneeScolaire.libelle,
    ("jours_fermes", "libelle"): m.JourFerme.libelle,
    ("jours_fermes", "groupe"): m.Groupe.code,
    ("etudiants", "identifiant"): m.Etudiant.identifiant,
    ("etudiants", "nom"): m.Etudiant.nom,
    ("etudiants", "prenom"): m.Etudiant.prenom,
    ("inscriptions", "annee"): m.AnneeScolaire.libelle,
    ("inscriptions", "etudiant"): m.Etudiant.identifiant,
    ("inscriptions", "groupe"): m.Groupe.code,
    ("matieres", "code"): m.Matiere.code,
    ("matieres", "libelle"): m.Matiere.libelle,
    ("matieres", "annee"): m.AnneeScolaire.libelle,
    ("matieres", "promotion"): m.Groupe.code,
    ("besoins", "annee"): m.AnneeScolaire.libelle,
    ("besoins", "semestre"): m.Semestre.code,
    ("besoins", "matiere"): m.Matiere.code,
    ("besoins", "promotion"): m.Groupe.code,
    ("besoins", "type"): m.TypeSeance.code,
    ("besoins", "libelle"): m.Besoin.libelle,
    ("besoins", "groupes"): m.Groupe.code,
    ("besoins", "enseignants"): m.Enseignant.identifiant,
    ("besoins", "campus_impose"): m.Campus.code,
    ("besoins", "types_salle"): m.TypeSalle.code,
    ("besoins", "equipements"): m.Equipement.code,
    ("indisponibilites", "ressource"): m.Enseignant.identifiant,  # la plus longue des références
    ("indisponibilites", "annee"): m.AnneeScolaire.libelle,
    ("indisponibilites", "motif"): m.Indisponibilite.motif,
    ("regles", "annee"): m.AnneeScolaire.libelle,
    ("regles", "code"): m.Regle.code,
    ("regles", "semestre"): m.Semestre.code,
    ("seances", "matiere"): m.Matiere.code,
    ("seances", "type"): m.TypeSeance.code,
    ("seances", "groupes"): m.Groupe.code,
    ("seances", "enseignants"): m.Enseignant.identifiant,
    ("seances", "libelle"): m.Besoin.libelle,
}
"""Colonne du modèle (ou du code référencé) dont chaque borne de `LONGUEURS` reprend la longueur."""

SALLE_AU_CAMPUS = m.Salle.code.type.length + 1 + m.Campus.code.type.length
"""Une salle désignée « code@campus »."""
COMPOSEES = {("besoins", "salle_imposee"): SALLE_AU_CAMPUS, ("seances", "salle"): SALLE_AU_CAMPUS}
LIBRES = {("regles", "cible"): 255}
"""Texte qui désigne la cible d'une règle (« matière / type / groupes ») : sans colonne propre."""
SANS_BORNE = {("besoins", "commentaire"), ("regles", "commentaire"), ("regles", "parametres")}
"""Textes longs par nature : colonnes Text du modèle, paramètres d'une règle (JSON)."""


@pytest.mark.cas_limite
def test_longueurs_alignees_sur_le_modele():
    """Cas : la longueur d'une colonne du modèle change (un code de salle passe à 64 caractères).

    Risque : la borne de l'import, recopiée dans LONGUEURS, refuserait des codes
    valides ou laisserait entrer des textes trop longs pour la base.
    """
    attendues = {cle: colonne.type.length for cle, colonne in COLONNES_DU_MODELE.items()}
    attendues |= COMPOSEES | LIBRES

    assert LONGUEURS == attendues


@pytest.mark.cas_limite
def test_toute_colonne_de_texte_a_une_longueur():
    """Cas : une nouvelle colonne de texte ou de code est ajoutée au format d'échange.

    Risque : sans entrée dans LONGUEURS, elle accepterait un texte de plusieurs
    milliers de caractères (SQLite ne l'arrête pas).
    """
    textes = {TypeColonne.TEXTE, TypeColonne.CODE, TypeColonne.LISTE}
    sans_longueur = {
        (f.nom, c.nom)
        for f in (*FEUILLES, FEUILLE_SEANCES)
        for c in f.colonnes
        if c.type in textes and c.longueur is None and not c.export_seulement
    }

    assert sans_longueur == SANS_BORNE
