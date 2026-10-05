"""Point d'entrée ASGI : `uvicorn edt.web.asgi:app`.

Ce module ne fait qu'instancier l'application ; toute la construction est
dans `edt.web.app.creer_app`.
"""

from edt.web.app import creer_app

app = creer_app()
