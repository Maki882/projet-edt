"""Détecte les chevauchements du planning existant, sans modifier la base."""

import argparse
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
import re
import sqlite3


@dataclass(frozen=True)
class Cours:
    id: int
    professeur_id: int
    classe_id: int
    salle_id: int
    jour: str
    heure_debut: str
    heure_fin: str


@dataclass(frozen=True)
class Conflit:
    cours_a: int
    cours_b: int
    ressource: str
    ressource_id: int

    def __str__(self):
        return (
            f"Cours {self.cours_a} et {self.cours_b} : "
            f"chevauchement pour {self.ressource} {self.ressource_id}."
        )


def minutes(horaire):
    """Accepte uniquement une heure HH:MM valide."""
    if not isinstance(horaire, str) or not re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", horaire
    ):
        raise ValueError(f"Horaire invalide : {horaire!r} (format attendu HH:MM).")
    heures, minutes_restantes = map(int, horaire.split(":"))
    return heures * 60 + minutes_restantes


def detecter_conflits(cours):
    """Compare des intervalles [début, fin[ sur un même jour.

    Le jour est une clé fournie par l'appelant (nom de jour ou date ISO).
    Cette vérification ne contrôle ni les pauses ni les autres contraintes.
    Une entrée invalide est signalée plutôt qu'ignorée.
    """
    intervalles = []
    identifiants = set()
    for seance in cours:
        if seance.id in identifiants:
            raise ValueError(f"Identifiant de cours répété : {seance.id}.")
        identifiants.add(seance.id)
        if not isinstance(seance.jour, str) or not seance.jour.strip():
            raise ValueError(f"Cours {seance.id} : jour manquant.")
        debut, fin = minutes(seance.heure_debut), minutes(seance.heure_fin)
        if debut >= fin:
            raise ValueError(f"Cours {seance.id} : la fin doit suivre le début.")
        for champ in ("professeur_id", "classe_id", "salle_id"):
            if getattr(seance, champ) is None:
                raise ValueError(f"Cours {seance.id} : {champ} manquant.")
        intervalles.append((seance, debut, fin))

    conflits = []
    for (a, debut_a, fin_a), (b, debut_b, fin_b) in combinations(intervalles, 2):
        if a.jour.strip().casefold() != b.jour.strip().casefold():
            continue
        if debut_a >= fin_b or debut_b >= fin_a:
            continue
        for ressource in ("professeur", "classe", "salle"):
            identifiant = getattr(a, f"{ressource}_id")
            if identifiant == getattr(b, f"{ressource}_id"):
                conflits.append(Conflit(a.id, b.id, ressource, identifiant))
    return conflits


def verifier_base(chemin):
    """Ouvre une base existante en lecture seule, sans créer de fichier."""
    uri = Path(chemin).resolve().as_uri() + "?mode=ro"
    connexion = sqlite3.connect(uri, uri=True)
    try:
        lignes = connexion.execute(
            "SELECT id, professeur_id, classe_id, salle_id, jour, "
            "heure_debut, heure_fin FROM cours ORDER BY id"
        ).fetchall()
    finally:
        connexion.close()
    return detecter_conflits(Cours(*ligne) for ligne in lignes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?", default="emploi_du_temps.db")
    arguments = parser.parse_args()
    try:
        conflits = verifier_base(arguments.base)
    except (ValueError, sqlite3.Error) as erreur:
        print(f"Vérification impossible : {erreur}")
        return 2
    for conflit in conflits:
        print(conflit)
    if not conflits:
        print("Aucun chevauchement de salle, de professeur ou de classe détecté.")
    return 1 if conflits else 0


if __name__ == "__main__":
    raise SystemExit(main())
