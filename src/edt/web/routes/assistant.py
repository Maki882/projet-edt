"""Panneau « Assistant » : conversation, réponses et propositions à confirmer.

La fonction globale Jinja `etat_assistant()` (posée à l'import de ce module,
que `creer_app` charge avec les autres routes) donne au gabarit du panneau
(`templates/assistant/_panneau.html`, inclus par `base.html`) l'état de
l'assistant. Conception : `docs/assistant.md`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from edt.services import assistant
from edt.web.gabarits import gabarits

journal = logging.getLogger(__name__)

routeur = APIRouter(prefix="/assistant")

gabarits.env.globals["etat_assistant"] = assistant.etat
