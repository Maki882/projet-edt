"""Assistant conversationnel : répond sur les données et propose des règles à confirmer.

Point d'entrée des interfaces (panneau web, ligne de commande). Conception
complète : `docs/assistant.md`.

- `etat()` : disponible, ou pourquoi pas (ne lit que l'environnement).
"""

from __future__ import annotations

from dataclasses import dataclass

from edt.services.assistant.configuration import (
    Raison,
    annoncer,
    charger_configuration_assistant,
)


@dataclass(frozen=True)
class EtatAssistant:
    """État affiché par le panneau : disponible, raison et phrase d'explication."""

    disponible: bool
    raison: Raison
    raison_texte: str


def etat() -> EtatAssistant:
    """État de l'assistant d'après l'environnement ; journalisé à chaque changement."""
    config = charger_configuration_assistant()
    annoncer(config)
    return EtatAssistant(config.disponible, config.raison, config.raison_texte)
