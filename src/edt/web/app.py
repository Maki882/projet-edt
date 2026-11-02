"""Construction de l'application web.

Mécanisme d'extension
---------------------
`creer_app()` importe tous les modules du paquet `edt.web.routes` et, pour
chacun, inclut son `routeur` (un `fastapi.APIRouter`, obligatoire) et ajoute
ses entrées de menu `MENU` (une liste d'`EntreeMenu`, facultative). Pour
ajouter un écran, on crée donc un module dans `edt/web/routes/` et ses
gabarits dans `edt/web/templates/`, sans toucher à ce fichier :

    # src/edt/web/routes/salles.py
    from fastapi import APIRouter, Request
    from edt.web.dependances import SessionRequete
    from edt.web.gabarits import rendre
    from edt.web.menu import SECTION_REFERENTIELS, EntreeMenu

    routeur = APIRouter(prefix="/salles")
    MENU = [EntreeMenu("Salles", "/salles", section=SECTION_REFERENTIELS, ordre=120)]

    @routeur.get("")
    def liste(request: Request, session: SessionRequete):
        return rendre(request, "salles/liste.html", {"salles": lister_salles(session)})

Les modules dont le nom commence par `_` sont ignorés (code partagé).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

from edt import __version__
from edt.config import charger_configuration
from edt.db.initialisation import base_initialisee
from edt.db.moteur import obtenir_moteur
from edt.erreurs import ErreurConfiguration
from edt.journal import configurer_journalisation
from edt.services.generation import cloturer_calculs_interrompus
from edt.web import routes
from edt.web.erreurs import installer_gestionnaires
from edt.web.gabarits import DOSSIER_STATIQUE
from edt.web.intergiciels import IntergicielJournal
from edt.web.menu import EntreeMenu, construire_menu

journal = logging.getLogger(__name__)

ATTRIBUT_ROUTEUR = "routeur"
ATTRIBUT_MENU = "MENU"


def modules_de_routes() -> list[str]:
    """Noms complets des modules de `edt.web.routes` à charger, par ordre alphabétique."""
    noms = sorted(
        info.name for info in pkgutil.iter_modules(routes.__path__) if not info.name.startswith("_")
    )
    return [f"{routes.__name__}.{nom}" for nom in noms]


@asynccontextmanager
async def _cycle_de_vie(app: FastAPI) -> AsyncIterator[None]:
    """Au démarrage du serveur : clôt les calculs qu'un arrêt précédent a laissés « en cours ».

    Une base corrompue, verrouillée par un autre processus ou inaccessible ne
    fait pas échouer le démarrage : le serveur doit pouvoir répondre « base
    indisponible » (et `/sante` dire « dégradé ») plutôt que de laisser le
    gestionnaire de services boucler sur des redémarrages. La panne est
    journalisée ; la clôture des calculs est simplement reportée au prochain
    lancement de calcul.
    """
    config = charger_configuration()
    journal.info(
        "Application EDT %s démarrée (environnement %s, base %s)",
        __version__,
        config.env,
        config.db_url_affichable,
    )
    try:
        prete = base_initialisee(obtenir_moteur())
    except Exception:
        journal.exception(
            "Base de données indisponible au démarrage (%s) : le serveur démarre quand même "
            "et répondra « base indisponible » jusqu'à sa réparation",
            config.db_url_affichable,
        )
        prete = False
    if prete:
        try:
            cloturer_calculs_interrompus()
        except Exception:
            journal.warning(
                "Clôture des calculs interrompus reportée : la base est occupée ou indisponible "
                "au démarrage ; elle se fera au prochain lancement de calcul",
                exc_info=True,
            )
    else:
        journal.warning(
            "La base n'est pas prête (non initialisée, schéma périmé ou inaccessible) : "
            "lancer « python -m edt verifier-schema » puis « python -m edt init-db »."
        )
    yield
    journal.info("Application EDT arrêtée")


def creer_app() -> FastAPI:
    """Crée l'application FastAPI : journalisation, routes découvertes, menu, erreurs."""
    config = charger_configuration()
    configurer_journalisation(config)

    app = FastAPI(
        title="EDT",
        description="Générateur automatique d'emplois du temps",
        version=__version__,
        docs_url=None if config.est_prod else "/docs",
        redoc_url=None,
        openapi_url=None if config.est_prod else "/openapi.json",
        lifespan=_cycle_de_vie,
    )
    app.mount("/static", StaticFiles(directory=DOSSIER_STATIQUE), name="static")

    entrees: list[EntreeMenu] = []
    for nom_module in modules_de_routes():
        module = importlib.import_module(nom_module)
        routeur = getattr(module, ATTRIBUT_ROUTEUR, None)
        if not isinstance(routeur, APIRouter):
            raise ErreurConfiguration(
                f"Le module {nom_module} doit exposer un objet « {ATTRIBUT_ROUTEUR} » de type "
                "fastapi.APIRouter (voir docs/architecture.md, « Ajouter un écran »)."
            )
        menu = getattr(module, ATTRIBUT_MENU, ())
        if not all(isinstance(entree, EntreeMenu) for entree in menu):
            raise ErreurConfiguration(
                f"Le module {nom_module} : « {ATTRIBUT_MENU} » doit être une liste d'EntreeMenu."
            )
        app.include_router(routeur)
        entrees.extend(menu)
        journal.debug("Routes chargées : %s (%d entrée(s) de menu)", nom_module, len(menu))

    app.state.menu = construire_menu(entrees)
    installer_gestionnaires(app)
    app.add_middleware(IntergicielJournal)
    return app
