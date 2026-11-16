"""Construction du client du SDK `anthropic` : le seul endroit qui le construit.

L'identifiant n'est ni lu ni passé par l'application : le SDK le prend dans
l'environnement (`ANTHROPIC_API_KEY` ou `ANTHROPIC_AUTH_TOKEN`). Les journaux
du SDK et de sa couche HTTP (`anthropic`, `httpx2`) sont limités aux
avertissements : ils ne recopient jamais d'en-tête dans `edt.log`.

Les tests remplacent `creer_client` par un faux client (aucun appel réseau).
"""

from __future__ import annotations

import logging
from typing import Any

from edt.services.assistant.configuration import ConfigurationAssistant

journal = logging.getLogger(__name__)

DELAI_CONNEXION_S = 5.0


def creer_client(reglages: ConfigurationAssistant) -> Any:
    """Client du SDK, sans reprise automatique (la boucle les décide), délai d'un appel.

    Lève `anthropic.CredentialsError` si le SDK ne trouve aucun identifiant.
    """
    import anthropic

    for nom in ("anthropic", "httpx2"):
        logging.getLogger(nom).setLevel(logging.WARNING)
    return anthropic.Anthropic(
        max_retries=0,
        timeout=anthropic.Timeout(float(reglages.delai_appel_s), connect=DELAI_CONNEXION_S),
    )
