"""Construction de l'application web.

Mécanisme d'extension
---------------------
`creer_app()` importe tous les modules du paquet `edt.web.routes` et, pour
chacun, inclut son `routeur` (un `fastapi.APIRouter`, obligatoire) et ajoute
ses entrées de menu `MENU` (une liste d'`EntreeMenu`, facultative). Pour
ajouter un écran, il suffit de créer un module dans `edt/web/routes/` et ses
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

import asyncio
import contextlib
import importlib
import logging
import pkgutil
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message

from edt import __version__
from edt.config import charger_configuration
from edt.db.initialisation import base_initialisee
from edt.db.moteur import obtenir_moteur
from edt.erreurs import ErreurConfiguration
from edt.journal import configurer_journalisation
from edt.services.generation import cloturer_calculs_interrompus
from edt.web import routes
from edt.web.dependances import ROUTES_ECRITURE_DELEGUEE
from edt.web.erreurs import installer_gestionnaires
from edt.web.gabarits import DOSSIER_STATIQUE, precompiler_gabarits
from edt.web.intergiciels import IntergicielHead, IntergicielJournal
from edt.web.menu import EntreeMenu, construire_menu

journal = logging.getLogger(__name__)

ATTRIBUT_ROUTEUR = "routeur"
ATTRIBUT_MENU = "MENU"

PAGES_A_PREPARER: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("/", ()),
    ("/a-traiter/compteur", (("HX-Request", "true"),)),
    ("/calculs", ()),
    ("/emploi-du-temps", ()),
    ("/a-traiter", ()),
)
"""Pages servies une fois au démarrage, sans visiteur, avec leurs en-têtes (voir `_preparer`)."""


def modules_de_routes() -> list[str]:
    """Noms complets des modules de `edt.web.routes` à charger, par ordre alphabétique."""
    noms = sorted(
        info.name for info in pkgutil.iter_modules(routes.__path__) if not info.name.startswith("_")
    )
    return [f"{routes.__name__}.{nom}" for nom in noms]


@asynccontextmanager
async def _cycle_de_vie(app: FastAPI) -> AsyncIterator[None]:
    """Au démarrage du serveur : clôt les calculs qu'un arrêt précédent a laissés « en cours »,
    puis prépare les premières pages en tâche de fond (`_preparer`).

    Une base corrompue, verrouillée par un autre processus ou inaccessible ne
    fait pas échouer le démarrage : le serveur doit pouvoir répondre « base
    indisponible » (et `/sante` dire « dégradé ») plutôt que de laisser le
    gestionnaire de services boucler sur des redémarrages. La panne est
    journalisée ; la clôture des calculs est reportée au prochain lancement
    de calcul.
    """
    config = charger_configuration()
    journal.info(
        "Application EDT %s démarrée (environnement %s, base %s)",
        __version__,
        config.env,
        config.db_url_affichable,
    )
    try:
        prete: bool | None = base_initialisee(obtenir_moteur())
    except Exception:
        journal.exception(
            "Base de données indisponible au démarrage (%s) : le serveur démarre quand même "
            "et répondra « base indisponible » jusqu'à sa réparation",
            config.db_url_affichable,
        )
        prete = None
    if prete:
        try:
            cloturer_calculs_interrompus()
        except Exception:
            journal.warning(
                "Clôture des calculs interrompus reportée : la base est occupée ou indisponible "
                "au démarrage ; elle se fera au prochain lancement de calcul",
                exc_info=True,
            )
    elif prete is False:
        journal.warning(
            "La base n'est pas prête (non initialisée ou schéma périmé) : "
            "lancer « python -m edt verifier-schema » puis « python -m edt init-db »."
        )
    preparation = asyncio.create_task(_preparer(app, avec_pages=bool(prete)))
    yield
    # Arrêt pendant la préparation : la page en cours est abandonnée (son calcul, mené dans un
    # fil, va à son terme sans être servi) et les suivantes ne sont pas préparées.
    preparation.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await preparation
    journal.info("Application EDT arrêtée")


async def _preparer(app: ASGIApp, *, avec_pages: bool) -> None:
    """Paie au démarrage ce que paierait sinon le premier visiteur de chaque page.

    La première visite d'une page compile ses gabarits et calcule des
    résultats coûteux gardés ensuite en mémoire tant que la base ne change
    pas (`edt.services.emploi_du_temps.memoire` : vérification de la version
    active, pré-vérifications, compteur « À traiter ») : plusieurs secondes
    sur un serveur modeste. Tous les gabarits sont donc compilés, puis les
    pages de `PAGES_A_PREPARER` servies une fois, en interne, par
    l'application elle-même : exactement les calculs d'une vraie visite, sans
    les recopier ici.

    Tâche de fond : le serveur répond dès son démarrage (un visiteur arrivé
    pendant la préparation est servi normalement). Une panne est journalisée
    et n'empêche jamais de servir. Sans base prête (`avec_pages` faux), seuls
    les gabarits sont compilés.
    """
    debut = time.perf_counter()
    try:
        nb_gabarits = await asyncio.to_thread(precompiler_gabarits)
    except Exception:
        journal.warning("Préparation : compilation des gabarits impossible", exc_info=True)
        nb_gabarits = 0
    bilan = []
    for chemin, en_tetes in PAGES_A_PREPARER if avec_pages else ():
        debut_page = time.perf_counter()
        try:
            statut = await _servir_en_interne(app, chemin, en_tetes)
        except Exception:
            journal.warning("Préparation de la page %s impossible", chemin, exc_info=True)
            continue
        duree_ms = (time.perf_counter() - debut_page) * 1000
        if statut >= 500:
            journal.warning("Préparation : la page %s a répondu %d", chemin, statut)
        bilan.append(f"{chemin} {statut} en {duree_ms:.0f} ms")
    journal.info(
        "Premières pages préparées en %.1f s : %d gabarits compilés%s",
        time.perf_counter() - debut,
        nb_gabarits,
        f" ; {', '.join(bilan)}" if bilan else " (base non prête : aucune page servie)",
    )


async def _servir_en_interne(
    app: ASGIApp, chemin: str, en_tetes: tuple[tuple[str, str], ...]
) -> int:
    """Sert `GET chemin` par l'application, sans passer par le réseau ; renvoie le statut HTTP."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": chemin,
        "raw_path": chemin.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"localhost"),
            *((nom.lower().encode(), valeur.encode()) for nom, valeur in en_tetes),
        ],
        "client": ("127.0.0.1", 0),
        "server": ("localhost", 80),
        "state": {},
    }
    corps_lu = False
    terminee = asyncio.Event()
    statut = 0

    async def recevoir() -> Message:
        nonlocal corps_lu
        if not corps_lu:
            corps_lu = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await terminee.wait()
        return {"type": "http.disconnect"}

    async def envoyer(message: Message) -> None:
        nonlocal statut
        if message["type"] == "http.response.start":
            statut = message["status"]
        elif message["type"] == "http.response.body" and not message.get("more_body"):
            terminee.set()

    try:
        await app(scope, recevoir, envoyer)
    finally:
        terminee.set()
    return statut


def creer_app() -> FastAPI:
    """Crée l'application FastAPI : journalisation, routes découvertes, menu, erreurs.

    Toute route GET répond aussi à HEAD, sans corps (`IntergicielHead`) : la
    supervision et `curl -I` ne voient pas un 405 « Action non permise ».
    """
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
    noms_des_routes: set[str] = set()
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
        noms_des_routes.update(route.name for route in routeur.routes if hasattr(route, "name"))
        entrees.extend(menu)
        journal.debug("Routes chargées : %s (%d entrée(s) de menu)", nom_module, len(menu))

    _verifier_routes_a_ecriture_deleguee(noms_des_routes)
    app.state.menu = construire_menu(entrees)
    installer_gestionnaires(app)
    # Le dernier ajouté enveloppe les autres : le journal voit la méthode reçue (HEAD).
    app.add_middleware(IntergicielHead)
    app.add_middleware(IntergicielJournal)
    return app


def _verifier_routes_a_ecriture_deleguee(noms_des_routes: set[str]) -> None:
    """Chaque nom de `ROUTES_ECRITURE_DELEGUEE` doit désigner une route des modules chargés.

    Une route renommée sans mettre la liste à jour prendrait le verrou d'écriture
    d'emblée et bloquerait jusqu'au délai le service de génération qu'elle appelle.
    """
    inconnues = sorted(ROUTES_ECRITURE_DELEGUEE - noms_des_routes)
    if inconnues:
        raise ErreurConfiguration(
            "edt.web.dependances.ROUTES_ECRITURE_DELEGUEE cite des routes inexistantes "
            f"({', '.join(inconnues)}) : mettre la liste à jour avec les noms des routes "
            "dont la session ne fait que lire."
        )
