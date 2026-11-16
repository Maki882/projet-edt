"""Routes web, découvertes automatiquement par `edt.web.app.creer_app`.

Chaque module de ce paquet expose :

- `routeur` : un `fastapi.APIRouter` (obligatoire) ;
- `MENU` : une liste d'`edt.web.menu.EntreeMenu` (facultatif).

Un nouvel écran s'ajoute dans son propre module (ou sous-paquet), sans
modifier `edt/web/app.py` ni les autres modules. Les modules dont le nom
commence par `_` ne sont pas chargés.
"""
