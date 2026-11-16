"""Limites et coûts de l'assistant : messages par adresse, par jour, budget du jour.

Le site est public et sans connexion : la dépense doit rester bornée.

- **Par adresse** (`EDT_ASSISTANT_MESSAGES_PAR_ADRESSE`) : compteur en mémoire,
  par jour, sur une empreinte de l'adresse (aucune adresse en clair, ni en
  mémoire ni en base).
- **Pour le site** (`EDT_ASSISTANT_MESSAGES_PAR_JOUR`, `EDT_ASSISTANT_BUDGET_JOUR`) :
  la consommation du jour est dans un petit fichier,
  `<EDT_LOG_DIR>/assistant/consommation.json`, hors de la base : elle survit
  ainsi à la remise à zéro de la base (chargement d'un établissement fictif,
  ouvert à tous) comme à un redémarrage. Le fichier est réécrit d'un bloc
  (fichier temporaire puis remplacement) après chaque appel et à la fin de
  chaque échange qui a appelé le modèle. La consommation compte aussi en
  mémoire, et le plus grand des deux comptes fait foi ; une écriture
  impossible est journalisée et gardée en mémoire, ajoutée à l'écriture
  suivante.
- **Échange réservé** : un message accepté réserve son échange
  (`reserver_echange`), qui compte aussitôt dans la limite du jour avec les
  échanges en cours ; l'échange n'est écrit qu'à sa fin, s'il a tenté au
  moins un appel au modèle (`valider_echange`), et rendu sinon
  (`annuler_echange`). Un message refusé, ou arrêté avant tout appel, ne
  consomme ainsi ni échange du jour, ni message de l'adresse, ni budget.
- **Budget** : avant chaque appel au modèle, son coût maximal (les deux
  tentatives possibles, la demande et son repli, pour la taille réelle du
  contexte) est **réservé** ; l'appel n'a pas lieu si consommé + réservé +
  ce coût dépassent le budget. Après l'appel, la réservation est soldée par
  le coût réel, calculé d'après l'usage rendu (`cout_reel`).

Les montants sont des entiers de micro-dollars ; un nombre de jetons
multiplié par un prix en dollars par million de jetons en donne directement.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from edt.config import charger_configuration
from edt.erreurs import ErreurEDT
from edt.services.assistant import temps
from edt.services.assistant.configuration import (
    PRIX_PAR_MODELE,
    ConfigurationAssistant,
    Prix,
    prix_le_plus_cher,
)

journal = logging.getLogger(__name__)


class Limite(StrEnum):
    """Limite qui refuse un message ou arrête un échange."""

    VIDE = "vide"
    LONGUEUR = "longueur"
    EN_COURS = "en_cours"
    CONVERSATION = "conversation"
    CONTEXTE = "contexte"
    ADRESSE = "adresse"
    SITE = "site"
    SIMULTANES = "simultanes"
    PAUSE = "pause"
    MEMOIRE = "memoire"


class LimiteAtteinte(ErreurEDT):
    """Une limite de l'assistant est atteinte : `limite` dit laquelle, le message quoi faire."""

    def __init__(self, limite: Limite, message: str) -> None:
        super().__init__(message)
        self.limite = limite


MESSAGE_SITE = "L'assistant a atteint sa limite du jour ; il sera de nouveau disponible demain."


@dataclass
class Compteurs:
    """Consommation : échanges, appels, jetons par catégorie, coût en micro-dollars."""

    echanges: int = 0
    appels: int = 0
    entree: int = 0
    cache_lus: int = 0
    cache_ecrits: int = 0
    sortie: int = 0
    cout: int = 0

    def ajouter(self, autre: Compteurs) -> None:
        for champ in dataclasses.fields(self):
            setattr(self, champ.name, getattr(self, champ.name) + getattr(autre, champ.name))

    def maximum(self, autre: Compteurs) -> Compteurs:
        """Champ par champ, le plus grand des deux."""
        return Compteurs(
            **{
                champ.name: max(getattr(self, champ.name), getattr(autre, champ.name))
                for champ in dataclasses.fields(self)
            }
        )

    @property
    def jetons(self) -> int:
        """Total des jetons (entrée, cache lu et écrit, sortie)."""
        return self.entree + self.cache_lus + self.cache_ecrits + self.sortie


# --------------------------------------------------------------------------- #
# Coûts
# --------------------------------------------------------------------------- #


def _prix(modele: str | None) -> Prix:
    if modele in PRIX_PAR_MODELE:
        return PRIX_PAR_MODELE[modele]
    journal.warning(
        "Modèle %r absent de la table des prix : coût compté au prix du plus cher", modele
    )
    return prix_le_plus_cher()


def _cout(prix: Prix, entree: int, cache_lus: int, cache_ecrits: int, sortie: int) -> int:
    return round(
        entree * prix.entree
        + cache_lus * prix.lecture_cache
        + cache_ecrits * prix.ecriture_cache
        + sortie * prix.sortie
    )


def cout_reel(usage: Any, modele: str | None) -> Compteurs:
    """Jetons et coût d'un appel d'après son usage (`usage.iterations` s'il est présent).

    Un repli facture chaque tentative à son propre modèle : chaque itération
    est comptée au prix de son modèle (celui de la réponse à défaut).
    """
    iterations = getattr(usage, "iterations", None) or [usage]
    total = Compteurs(appels=1)
    for iteration in iterations:
        entree = int(getattr(iteration, "input_tokens", 0) or 0)
        cache_lus = int(getattr(iteration, "cache_read_input_tokens", 0) or 0)
        cache_ecrits = int(getattr(iteration, "cache_creation_input_tokens", 0) or 0)
        sortie = int(getattr(iteration, "output_tokens", 0) or 0)
        modele_iteration = getattr(iteration, "model", None) or modele
        total.entree += entree
        total.cache_lus += cache_lus
        total.cache_ecrits += cache_ecrits
        total.sortie += sortie
        total.cout += _cout(_prix(modele_iteration), entree, cache_lus, cache_ecrits, sortie)
    return total


def cout_maximal_appel(jetons_entree: int, config: ConfigurationAssistant) -> int:
    """Coût maximal d'un appel : tout le contexte écrit en cache, toute la sortie, et le repli.

    Le modèle de repli n'est pas connu d'avance : il est compté au prix du
    plus cher de la table.
    """
    principal, secours = config.prix, prix_le_plus_cher()
    sortie = config.jetons_sortie_max
    return round(
        jetons_entree * max(principal.entree, principal.ecriture_cache)
        + sortie * principal.sortie
        + jetons_entree * max(secours.entree, secours.ecriture_cache)
        + sortie * secours.sortie
    )


# --------------------------------------------------------------------------- #
# Consommation du site
# --------------------------------------------------------------------------- #


FICHIER_CONSOMMATION = "consommation.json"
NB_ESSAIS_REMPLACEMENT = 5
"""Sous Windows, un lecteur qui tient le fichier ouvert empêche son remplacement : il est retenté."""


def dossier_consommation() -> Path:
    """Dossier du fichier de consommation : `<EDT_LOG_DIR>/assistant`."""
    return charger_configuration().log_dir / "assistant"


class Budget:
    """Consommation du jour du site : fichier, mémoire, réservations (voir l'en-tête)."""

    def __init__(self, dossier: Path | None = None) -> None:
        self._dossier = dossier
        self._verrou = threading.RLock()
        self._jour: dt.date | None = None
        self._memoire = Compteurs()
        self._a_ecrire = Compteurs()
        self._reserve = 0
        self._echanges_reserves = 0
        self._averti = False

    @property
    def chemin(self) -> Path:
        """Le fichier de la consommation du jour."""
        return (self._dossier or dossier_consommation()) / FICHIER_CONSOMMATION

    def _du_jour(self) -> dt.date:
        jour = temps.aujourdhui()
        if self._jour != jour:
            if self._jour is not None and self._a_ecrire.appels + self._a_ecrire.echanges:
                self._persister(self._jour, Compteurs())
            self._jour, self._memoire, self._a_ecrire = jour, Compteurs(), Compteurs()
            self._reserve, self._echanges_reserves, self._averti = 0, 0, False
        return jour

    def consommation(self) -> Compteurs:
        """Consommation du jour qui fait foi : le plus grand du fichier et de la mémoire."""
        with self._verrou:
            jour = self._du_jour()
            enregistree = self._lire(jour)
            enregistree.ajouter(self._a_ecrire)
            return enregistree.maximum(self._memoire)

    def reserver_echange(self, config: ConfigurationAssistant) -> None:
        """Réserve un échange ; `LimiteAtteinte` si le nombre du jour ou le budget est atteint.

        Les échanges réservés (en cours) comptent dans la limite comme les
        échanges écrits : deux envois simultanés ne la dépassent pas. Rien n'est
        écrit avant `valider_echange`.
        """
        with self._verrou:
            self._du_jour()
            actuelle = self.consommation()
            if (
                actuelle.echanges + self._echanges_reserves >= config.messages_par_jour
                or actuelle.cout >= config.budget_jour_micro
            ):
                self._refuser(actuelle, config)
            self._echanges_reserves += 1

    def valider_echange(self) -> None:
        """Compte l'échange réservé (au moins un appel au modèle tenté) : mémoire et fichier."""
        with self._verrou:
            jour = self._du_jour()
            self._echanges_reserves = max(0, self._echanges_reserves - 1)
            delta = Compteurs(echanges=1)
            self._memoire.ajouter(delta)
            self._persister(jour, delta)

    def annuler_echange(self) -> None:
        """Rend l'échange réservé : message refusé ensuite, ou échange arrêté avant tout appel."""
        with self._verrou:
            self._echanges_reserves = max(0, self._echanges_reserves - 1)

    def reserver(self, cout_max: int, config: ConfigurationAssistant) -> int:
        """Réserve `cout_max` micro-dollars ; `LimiteAtteinte` si le budget serait dépassé."""
        with self._verrou:
            actuelle = self.consommation()
            if actuelle.cout + self._reserve + cout_max > config.budget_jour_micro:
                self._refuser(actuelle, config)
            self._reserve += cout_max
            return cout_max

    def solder(self, reservation: int, consommation: Compteurs) -> None:
        """Remplace la réservation par la consommation réelle d'un appel (fichier et mémoire)."""
        with self._verrou:
            jour = self._du_jour()
            self._reserve = max(0, self._reserve - reservation)
            self._memoire.ajouter(consommation)
            self._persister(jour, consommation)

    def annuler(self, reservation: int) -> None:
        """Libère une réservation dont l'appel n'a rien coûté (erreur avant réponse)."""
        with self._verrou:
            self._reserve = max(0, self._reserve - reservation)

    def _refuser(self, actuelle: Compteurs, config: ConfigurationAssistant) -> None:
        if not self._averti:
            self._averti = True
            journal.warning(
                "Assistant : limite du jour atteinte (%d échanges sur %d, coût estimé %d µ$ sur "
                "%d µ$) : plus aucun appel aujourd'hui",
                actuelle.echanges,
                config.messages_par_jour,
                actuelle.cout,
                config.budget_jour_micro,
            )
        raise LimiteAtteinte(Limite.SITE, MESSAGE_SITE)

    def _lire(self, jour: dt.date) -> Compteurs:
        """Consommation enregistrée pour `jour` (celle d'un autre jour ne compte pas)."""
        try:
            donnees = json.loads(self.chemin.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Compteurs()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as erreur:
            journal.warning(
                "Consommation de l'assistant illisible (%s : %s) : le compte en mémoire fait foi",
                self.chemin,
                erreur,
            )
            return Compteurs()
        if not isinstance(donnees, dict) or donnees.get("jour") != jour.isoformat():
            return Compteurs()
        try:
            return Compteurs(
                **{champ.name: int(donnees[champ.name]) for champ in dataclasses.fields(Compteurs)}
            )
        except (KeyError, TypeError, ValueError) as erreur:
            journal.warning(
                "Consommation de l'assistant mal formée (%s : %s) : le compte en mémoire fait foi",
                self.chemin,
                erreur,
            )
            return Compteurs()

    def _persister(self, jour: dt.date, delta: Compteurs) -> None:
        """Ajoute `delta` (et ce qui n'a pas pu être écrit avant) au fichier du jour."""
        a_ecrire = Compteurs()
        a_ecrire.ajouter(self._a_ecrire)
        a_ecrire.ajouter(delta)
        total = self._lire(jour)
        total.ajouter(a_ecrire)
        try:
            self.chemin.parent.mkdir(parents=True, exist_ok=True)
            _ecrire_atomique(
                self.chemin,
                json.dumps({"jour": jour.isoformat(), **dataclasses.asdict(total)}, indent=1),
            )
        except OSError as erreur:
            journal.error(
                "Consommation de l'assistant non enregistrée (%s : %s) : gardée en mémoire, "
                "ajoutée à la prochaine écriture",
                self.chemin,
                erreur,
            )
            self._a_ecrire = a_ecrire
            return
        self._a_ecrire = Compteurs()


def _ecrire_atomique(chemin: Path, texte: str) -> None:
    """Écrit `texte` dans un fichier temporaire du même dossier, puis remplace `chemin`."""
    descripteur, temporaire = tempfile.mkstemp(
        dir=chemin.parent, prefix=".consommation-", suffix=".tmp"
    )
    try:
        with os.fdopen(descripteur, "w", encoding="utf-8") as flux:
            flux.write(texte)
            flux.flush()
            os.fsync(flux.fileno())
        for essai in range(NB_ESSAIS_REMPLACEMENT):
            try:
                os.replace(temporaire, chemin)
                return
            except PermissionError:
                if essai == NB_ESSAIS_REMPLACEMENT - 1:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(temporaire):
            os.remove(temporaire)


# --------------------------------------------------------------------------- #
# Messages par adresse
# --------------------------------------------------------------------------- #


class CompteurAdresses:
    """Messages du jour par empreinte d'adresse (en mémoire seulement)."""

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._jour: dt.date | None = None
        self._comptes: dict[str, int] = {}

    def compter(self, empreinte: str, config: ConfigurationAssistant) -> dt.date:
        """Compte un message ; `LimiteAtteinte` si l'adresse a atteint sa limite du jour.

        Renvoie le jour compté, à repasser à `rendre`.
        """
        with self._verrou:
            jour = temps.aujourdhui()
            if jour != self._jour:
                self._jour, self._comptes = jour, {}
            if self._comptes.get(empreinte, 0) >= config.messages_par_adresse:
                journal.info("Assistant : limite par adresse atteinte (%s)", empreinte)
                raise LimiteAtteinte(
                    Limite.ADRESSE,
                    f"Vous avez atteint la limite de {config.messages_par_adresse} messages pour "
                    "aujourd'hui ; l'assistant sera de nouveau disponible demain.",
                )
            self._comptes[empreinte] = self._comptes.get(empreinte, 0) + 1
            return jour

    def rendre(self, empreinte: str, jour: dt.date) -> None:
        """Annule un compte de `jour` (message refusé ensuite, ou arrêté avant tout appel).

        Après minuit, le compte du jour précédent n'existe plus : rien à rendre.
        """
        with self._verrou:
            if jour == self._jour and self._comptes.get(empreinte, 0) > 0:
                self._comptes[empreinte] -= 1
