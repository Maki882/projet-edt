"""Aides de génération des tests du générateur de données fictives.

- `creer_base(dossier)` : base initialisée et vide de référentiels ;
- `generer(dossier, taille, graine)` : base neuve + jeu, renvoie un `Jeu` ;
- `generer_dans(url, taille, graine, **options)` : jeu dans une base existante
  (éventuellement dégradée avant), renvoie le rapport et l'empreinte sémantique ;
- `total_prevu(rapport)` : besoins créés + besoins écartés (tout besoin préparé
  est l'un ou l'autre, jamais perdu).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session
from tests.outils.synthese.empreinte import empreinte_semantique

from edt.db.initialisation import initialiser_base
from edt.db.moteur import fabrique_sessions, session_base
from edt.synthese.generateur import generer_jeu
from edt.synthese.rapport import RapportJeu

AUJOURDHUI = dt.date(2026, 10, 5)
"""Date de référence : l'année scolaire initialisée est 2026-2027."""


@dataclass(frozen=True)
class Jeu:
    """Un jeu généré : l'URL de sa base et le rapport de génération."""

    url: str
    rapport: RapportJeu

    def session(self) -> Session:
        """Nouvelle session sur la base du jeu (à fermer par l'appelant)."""
        return fabrique_sessions(self.url)()


def creer_base(dossier: Path) -> str:
    """Initialise une base vierge dans `dossier` et renvoie son URL."""
    url = f"sqlite:///{(dossier / 'jeu.db').as_posix()}"
    initialiser_base(url=url, aujourdhui=AUJOURDHUI)
    return url


def generer(dossier: Path, taille, graine: int = 0, reference: bool = False) -> Jeu:
    """Crée une base vierge, y génère le jeu et renvoie l'URL et le rapport."""
    url = creer_base(dossier)
    with session_base(url) as session:
        rapport = generer_jeu(session, taille, graine, enregistrer_reference=reference)
    return Jeu(url, rapport)


def generer_dans(url: str, taille, graine: int = 0, **options) -> tuple[RapportJeu, str]:
    """Génère le jeu dans la base `url` ; renvoie le rapport et son empreinte sémantique."""
    with session_base(url) as session:
        rapport = generer_jeu(session, taille, graine, **options)
        return rapport, empreinte_semantique(session, rapport)


def total_prevu(rapport: RapportJeu) -> int:
    """Besoins préparés : créés ou écartés (aucun n'est perdu en silence)."""
    return rapport.nb_besoins + len(rapport.besoins_ecartes)
