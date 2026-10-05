"""Routes web, découvertes automatiquement par `edt.web.app.creer_app`.

Chaque module de ce paquet expose :

- `routeur` : un `fastapi.APIRouter` (obligatoire) ;
- `MENU` : une liste d'`edt.web.menu.EntreeMenu` (facultatif).

Un lot ajoute ses écrans dans **son** module (ou son sous-paquet), sans
modifier `edt/web/app.py` ni les modules des autres lots. Les modules dont
le nom commence par `_` ne sont pas chargés.
"""
