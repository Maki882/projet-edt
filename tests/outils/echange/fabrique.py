"""Fabrique des fichiers et des contrôles communs aux tests du lot « import et export ».

Les tests de cas limites forment une banque durable : chacun construit ses
fichiers ici, en mémoire et avec des données fictives (codes du jeu minimal
ou noms inventés), plutôt que de recopier ses propres aides.

Fichiers
--------
- `csv_envoye(nom, *lignes)` : un CSV à point-virgule, en UTF-8 ;
- `classeur(onglets, fusions=...)` : un classeur XLSX, un onglet par clé
  (première ligne : les en-têtes), avec des cellules fusionnées au besoin ;
- `archive_zip(membres)` : le contenu d'une archive ZIP ;
- `calendrier_ical(*evenements)` : un fichier iCal ; chaque événement est
  donné par ses propriétés, une par ligne (le retrait est ignoré).

Chemins
-------
- `DOSSIER_FICHIERS` : le dossier des fichiers d'exemple fictifs (`fichiers/`).

Contrôles
---------
- `compter(modele)` : nombre de lignes d'une table de la base du test ;
- `messages(signalements, gravite)`, `erreurs(signalements)` : les messages
  d'une gravité, des erreurs ;
- `seul(signalements, gravite)` : le seul signalement de cette gravité.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import func, select

from edt.db.moteur import session_base
from edt.domaine.enumerations import Gravite
from edt.echange.lecture import FichierEnvoye
from edt.echange.rapport import Signalement

DOSSIER_FICHIERS = Path(__file__).parent / "fichiers"

# --------------------------------------------------------------------------- #
# Fichiers
# --------------------------------------------------------------------------- #


def csv_envoye(nom: str, *lignes: str) -> FichierEnvoye:
    """Un CSV en mémoire : une chaîne par ligne, cellules séparées par « ; »."""
    return FichierEnvoye(nom, ("\n".join(lignes) + "\n").encode("utf-8"))


def classeur(
    onglets: Mapping[str, Sequence[Sequence[Any]]],
    *,
    nom: str = "donnees.xlsx",
    fusions: Mapping[str, Sequence[str]] | None = None,
) -> FichierEnvoye:
    """Un classeur XLSX en mémoire.

    `onglets` : nom de l'onglet -> lignes (la première porte les en-têtes ;
    une cellule garde son type Python : date, heure, nombre ou texte).
    `fusions` : nom de l'onglet -> plages fusionnées (« A2:A3 »).
    """
    document = Workbook()
    document.remove(document.active)
    for titre, lignes in onglets.items():
        onglet = document.create_sheet(titre)
        for ligne in lignes:
            onglet.append(list(ligne))
        for plage in (fusions or {}).get(titre, ()):
            onglet.merge_cells(plage)
    tampon = io.BytesIO()
    document.save(tampon)
    return FichierEnvoye(nom, tampon.getvalue())


def archive_zip(membres: Mapping[str, bytes | str]) -> bytes:
    """Contenu d'une archive ZIP ; le nom d'un membre est gardé tel quel (« ../x.csv » compris)."""
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for nom, contenu in membres.items():
            archive.writestr(nom, contenu)
    return tampon.getvalue()


def calendrier_ical(*evenements: str) -> str:
    """Un fichier iCal : chaque événement est donné par ses propriétés, une par ligne.

    UID et DTSTAMP sont ajoutés ; le retrait des lignes est retiré (en iCal,
    une ligne qui commence par une espace prolonge la précédente).
    """
    corps = "\n".join(
        f"BEGIN:VEVENT\nUID:e{i}@test\nDTSTAMP:20260901T000000Z\n{_sans_retrait(texte)}\nEND:VEVENT"
        for i, texte in enumerate(evenements)
    )
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//essai//FR\n{corps}\nEND:VCALENDAR\n"


def _sans_retrait(texte: str) -> str:
    return "\n".join(ligne.strip() for ligne in texte.strip().splitlines())


# --------------------------------------------------------------------------- #
# Contrôles
# --------------------------------------------------------------------------- #


def compter(modele: type, url: str | None = None) -> int:
    """Nombre de lignes de la table de `modele` dans la base du test (ou celle de `url`)."""
    with session_base(url) as session:
        return session.scalar(select(func.count()).select_from(modele))


def messages(signalements: Iterable[Signalement], gravite: Gravite) -> list[str]:
    """Messages des signalements d'une gravité, dans l'ordre du rapport."""
    return [s.message for s in signalements if s.gravite is gravite]


def erreurs(signalements: Iterable[Signalement]) -> list[str]:
    """Messages des signalements d'erreur, dans l'ordre du rapport."""
    return messages(signalements, Gravite.ERREUR)


def seul(signalements: Iterable[Signalement], gravite: Gravite) -> Signalement:
    """Le seul signalement de cette gravité (échec lisible s'il y en a zéro ou plusieurs)."""
    trouves = [s for s in signalements if s.gravite is gravite]
    assert len(trouves) == 1, [(s.gravite.value, s.position, s.message) for s in signalements]
    return trouves[0]
