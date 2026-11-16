"""Onglets « Tests unitaires » et « Tests d'intégration » : lancer les tests du projet, voir leurs résultats.

Adresses :

- `GET /tests` : mène à la page des tests unitaires ;
- `GET /tests/{famille}` (`unitaires` ou `integration`) : schéma de
  l'organisation des tests, résumé de la famille, puis chaque composant,
  fichier et test avec son dernier résultat et sa date ;
- `POST /tests/{famille}/lancer` (champ `cible` : `famille`, `composant:<nom>`,
  `fichier:<chemin>`, `test:<clé>`) : lance l'exécution puis mène à son suivi ;
  la cible est cherchée dans le catalogue, une cible inconnue est refusée avec
  un message ;
- `GET /tests/{famille}/suivi?execution=…&depuis=N` : fragment interrogé
  chaque seconde pendant l'exécution ; il remplace aussi (HTMX, `hx-swap-oob`)
  les lignes des tests terminés depuis le N-ième et les résumés de leur
  fichier, de leur composant et de la famille ;
- `POST /tests/arreter` (champ `famille`) : demande l'arrêt de l'exécution ;
- `GET /tests/journal` : sortie complète de pytest pour la dernière exécution.

Toute la logique est dans `edt.services.banc_tests` ; ces routes n'ouvrent
aucune session de base.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from edt.erreurs import ErreurIntrouvable
from edt.services.banc_tests import execution as ex
from edt.services.banc_tests import resultats as res
from edt.services.banc_tests.catalogue import FAMILLES, lire_catalogue, resoudre_cible
from edt.services.banc_tests.tableau import tableau_famille, texte_duree
from edt.web.gabarits import est_htmx, rediriger, rendre
from edt.web.menu import SECTION_TESTS_INTEGRATION, SECTION_TESTS_UNITAIRES, EntreeMenu

journal = logging.getLogger(__name__)

routeur = APIRouter(prefix="/tests")
MENU = [
    EntreeMenu("Tests unitaires", "/tests/unitaires", section=SECTION_TESTS_UNITAIRES, ordre=800),
    EntreeMenu(
        "Tests d'intégration", "/tests/integration", section=SECTION_TESTS_INTEGRATION, ordre=850
    ),
]

TAILLE_JOURNAL_MAX = 2_000_000
"""Au-delà, seule la fin du journal est montrée (octets)."""

ANCRE_SUIVI = "#suivi-tests"


def _exiger_famille(famille: str) -> str:
    if famille not in FAMILLES:
        raise ErreurIntrouvable(
            f"Famille de tests inconnue : « {famille[:50]} ». Choisir « Tests unitaires » ou "
            "« Tests d'intégration » dans la barre d'onglets."
        )
    return famille


def _contexte(famille: str, execution_suivie: str | None = None) -> dict:
    return {
        "t": tableau_famille(famille, execution_suivie=execution_suivie),
        "duree": texte_duree,
        "etats": res,
    }


@routeur.get("", name="tests")
def accueil_tests() -> RedirectResponse:
    """Mène à la page des tests unitaires."""
    return RedirectResponse("/tests/unitaires", status_code=303)


@routeur.get("/journal", name="journal_tests")
def journal_execution() -> PlainTextResponse:
    """Sortie complète de pytest pour la dernière exécution (fin seulement si elle est très longue)."""
    chemin = res.dossier_resultats() / res.FICHIER_JOURNAL
    try:
        taille = chemin.stat().st_size
        with chemin.open("rb") as flux:
            flux.seek(max(0, taille - TAILLE_JOURNAL_MAX))
            texte = flux.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return PlainTextResponse("Aucune exécution des tests n'a encore été journalisée.")
    if taille > TAILLE_JOURNAL_MAX:
        texte = "[… début du journal omis …]\n" + texte
    return PlainTextResponse(texte)


@routeur.post("/arreter", name="arreter_tests")
def arreter(request: Request, famille: Annotated[str, Form()] = "unitaires") -> Response:
    """Demande l'arrêt de l'exécution en cours, puis revient à son suivi."""
    _exiger_famille(famille)
    courante = ex.demander_arret()
    if courante is not None and courante.en_cours:
        message, niveau = "Arrêt demandé : les tests s'arrêtent dans un instant.", "info"
    else:
        message, niveau = "Aucune exécution des tests en cours : rien à arrêter.", "info"
    return rediriger(request, f"/tests/{famille}{ANCRE_SUIVI}", message, niveau)


@routeur.get("/{famille}", name="page_tests")
def page(request: Request, famille: str) -> Response:
    """Page d'une famille de tests."""
    _exiger_famille(famille)
    return rendre(request, "tests/page.html", _contexte(famille))


@routeur.post("/{famille}/lancer", name="lancer_tests")
def lancer(request: Request, famille: str, cible: Annotated[str, Form()] = "") -> Response:
    """Lance la cible choisie ; refus expliqué si elle est inconnue ou si le lancement est impossible."""
    _exiger_famille(famille)
    adresse = f"/tests/{famille}"
    catalogue = lire_catalogue()
    try:
        choisie = resoudre_cible(catalogue, cible, famille)
        ex.lancer(choisie, catalogue.racine, depuis_le_site=True)
    except ErreurIntrouvable as erreur:
        journal.info("Lancement de tests refusé : %s", erreur)
        return rediriger(request, adresse, str(erreur), "erreur")
    except ex.ErreurTestsEnCours as erreur:
        courante = ex.execution_courante()
        if courante is not None and courante.en_cours and courante.cible.famille in FAMILLES:
            adresse = f"/tests/{courante.cible.famille}"
        texte = (
            "Cette exécution est déjà en cours : voici son avancement."
            if erreur.meme_cible
            else str(erreur)
        )
        return rediriger(request, adresse + ANCRE_SUIVI, texte, "info")
    except ex.ErreurLancementImpossible as erreur:
        return rediriger(request, adresse, str(erreur), "attention")
    return rediriger(request, adresse + ANCRE_SUIVI, f"Exécution lancée : {choisie.libelle}.")


@routeur.get("/{famille}/suivi", name="suivi_tests")
def suivi(request: Request, famille: str, execution: str = "", depuis: int = 0) -> Response:
    """Avancement de l'exécution (fragment HTMX), avec les lignes des tests terminés depuis `depuis`.

    Une exécution que ce processus ne connaît plus (serveur redémarré) fait
    recharger la page entière (`HX-Refresh`). Ouvert hors HTMX, mène à la page.
    """
    _exiger_famille(famille)
    if not est_htmx(request):
        return RedirectResponse(f"/tests/{famille}", status_code=303)
    contexte = _contexte(famille, execution_suivie=execution)
    tableau = contexte["t"]
    if ex.execution(execution) is None or tableau.suivi is None or tableau.suivi.id != execution:
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    s = tableau.suivi
    cles = list(s.termines[max(0, depuis) :])
    if s.test_en_cours:
        cles.append(s.test_en_cours)
    if not s.en_cours:  # fin : les tests restés « en attente » (arrêt) reprennent leur état
        cles += sorted(s.tests_cible)
    lignes = {cle: tableau.ligne(cle) for cle in cles if tableau.ligne(cle) is not None}
    fichiers = {id(f): f for f in (tableau.fichier_de(c) for c in lignes) if f is not None}
    composants = {id(c): c for c in (tableau.composant_de(c) for c in lignes) if c is not None}
    contexte.update(
        oob=True,
        lignes_oob=list(lignes.values()),
        fichiers_oob=list(fichiers.values()),
        composants_oob=list(composants.values()),
    )
    return rendre(request, "tests/_suivi.html", contexte)
