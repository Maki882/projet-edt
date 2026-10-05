"""Interface web : FastAPI, gabarits Jinja2, HTMX embarqué (aucun outil Node).

- `app.creer_app()` construit l'application ; `asgi.app` est le point
  d'entrée d'uvicorn (`edt.web.asgi:app`) ;
- `routes/` : un module par écran ou famille d'écrans, découvert
  automatiquement (voir `edt.web.app`) ;
- `gabarits` : rendu des pages, filtres, messages ;
- `dependances` : session de base par requête ;
- `intergiciels` : identifiant de requête et journal des requêtes ;
- `erreurs` : traduction des erreurs en pages lisibles ;
- `templates/` et `static/` : gabarits, feuille de style, HTMX.

Une route appelle un service (`edt.services`) et rend un gabarit ; elle ne
contient pas de logique métier.
"""
