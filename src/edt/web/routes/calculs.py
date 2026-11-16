"""Onglet « Générer » : lancer et suivre un calcul, historique des calculs et des versions.

Adresses :

- `GET /calculs` : choix du semestre, récapitulatif, vérifications
  préalables, tableau des règles et de leur niveau (préremplis avec ceux du
  dernier calcul du semestre), pause déjeuner, options, lancement ; si un
  calcul tourne déjà, la page le montre au lieu de proposer d'en lancer un
  second ;
- `GET /calculs/verifications` : fragment des vérifications préalables,
  recalculé quand le niveau d'une règle change (champs `niveau_<CODE>`) ;
- `POST /calculs` : lit les niveaux (`niveau_<CODE>`) et les choix sur les
  règles personnalisées (`niveau_regle_<id>`, `ignorer_regle_<id>`), enregistre la pause
  déjeuner choisie (mode, heures, durée minimale) si elle diffère des paramètres, lance un calcul puis mène
  à son suivi ; un niveau illisible ou interdit, ou une pause déjeuner mal
  saisie, réaffiche la page (422) avec le message ;
- `GET /calculs/{id}` et `GET /calculs/{id}/etat` (fragment interrogé
  chaque seconde) : suivi et résultat ;
- `POST /calculs/{id}/arreter`, `POST /calculs/{id}/relancer` ;
- `GET /versions`, `POST /versions/{id}/activer`, `POST /versions/{id}/supprimer`.

Toute la logique est dans `edt.services.emploi_du_temps` ; ce module ne fait
que lire les formulaires et choisir la page.
"""

from __future__ import annotations

import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request

from edt.config import charger_configuration
from edt.domaine.contraintes import Niveaux
from edt.erreurs import ErreurCalculEnCours, ErreurIntrouvable
from edt.services import generation
from edt.services.emploi_du_temps import (
    calculs,
    prechauffage,
    presentation,
    regles_personnalisees,
    versions,
)
from edt.services.emploi_du_temps.identifiants import EntierFacultatif
from edt.web.dependances import SessionRequete
from edt.web.gabarits import Message, est_htmx, rediriger, rendre
from edt.web.menu import EntreeMenu

SECTION_GENERER = "Générer"

DELAI_DOUBLE_ENVOI_S = 5.0
"""Un second « Lancer le calcul » arrivé si tôt après le premier, avec le même semestre et les
mêmes réglages, est un double clic : la page dit « Calcul lancé. » comme pour le premier envoi,
au lieu d'avertir qu'un calcul est déjà en cours."""

journal = logging.getLogger(__name__)
routeur = APIRouter()
MENU = [
    EntreeMenu("Générer un emploi du temps", "/calculs", section=SECTION_GENERER, ordre=250),
    EntreeMenu("Historique et versions", "/versions", section=SECTION_GENERER, ordre=260),
]


async def _niveaux_saisis(request: Request) -> dict[str, str]:
    """Les choix `niveau_<CODE>` reçus : formulaire envoyé (POST) ou adresse (GET), non vérifiés."""
    champs = (await request.form()) if request.method == "POST" else request.query_params
    return calculs.niveaux_saisis(champs.multi_items())


NiveauxSaisis = Annotated[dict[str, str], Depends(_niveaux_saisis)]


async def _champs_regles(request: Request) -> dict[str, str]:
    """Les champs des règles personnalisées reçus (`niveau_regle_<id>`, `ignorer_regle_<id>`)."""
    champs = (await request.form()) if request.method == "POST" else request.query_params
    return regles_personnalisees.champs_regles(champs.multi_items())


ChampsRegles = Annotated[dict[str, str], Depends(_champs_regles)]


def _page_calculs(
    request: Request,
    session,
    semestre_id: int | None,
    *,
    niveaux: Niveaux | None = None,
    choix_regles: dict | None = None,
    choix: dict | None = None,
    pre: calculs.PreVerification | None = None,
    dejeuner: calculs.ChoixDejeuner | None = None,
    erreurs_dejeuner: dict[str, str] | None = None,
    ouvert: frozenset[str] = frozenset(),
    statut: int = 200,
    messages=(),
):
    """La page « Générer ». `ouvert` : sections du formulaire à déplier (celle d'une saisie
    refusée) ; la pause déjeuner se déplie d'elle-même quand elle a des erreurs."""
    semestres = versions.lister_semestres(session)
    semestre = versions.choisir_semestre(session, semestre_id)
    contexte: dict = {"fmt": presentation, "semestres": semestres, "semestre": semestre}
    if semestre is not None:
        suivi = generation.calcul_en_cours()
        en_cours = calculs.resultat_calcul(session, suivi.id, indicateurs=False) if suivi else None
        tests = calculs.avertissement_tests_en_cours() if suivi is None else None
        if tests is not None:
            messages = [*messages, Message(tests, "attention")]
        dernier = generation.dernier_calcul(semestre.id)
        if niveaux is None:
            niveaux = calculs.niveaux_proposes(dernier)
        recap = calculs.recapitulatif(session, semestre.id)
        options = calculs.options_formulaire(session)
        bloc = calculs.bloc_regles_du_semestre(
            session, semestre.id, choix=choix_regles, dernier=dernier
        )
        niveaux_regles = regles_personnalisees.niveaux_pour_le_calcul(bloc.choix, bloc)
        regles = calculs.regles_proposees(niveaux)
        choix = choix or {"duree": "court", "optimiser": options.optimiser, "autre_solution": False}
        dejeuner = dejeuner or calculs.choix_dejeuner(session)
        erreurs_dejeuner = erreurs_dejeuner or {}
        annee_regles = versions.annee_du_semestre(session, semestre.id)
        # « Ajouter une règle » ramène à cette page une fois la règle créée.
        retour = {"annee": annee_regles, "retour": f"/calculs?semestre_id={semestre.id}"}
        contexte.update(
            recap=recap,
            pre=pre
            or calculs.pre_verifier(
                session, semestre.id, niveaux, recap, niveaux_regles=niveaux_regles
            ),
            regles=regles,
            niveaux_proposes=calculs.NIVEAUX_PROPOSES,
            bloc_regles=bloc,
            annee_regles=annee_regles,
            adresse_regles=regles_personnalisees.ADRESSE_REGLES,
            adresse_ajout_regle=f"{regles_personnalisees.ADRESSE_AJOUT_REGLE}?{urlencode(retour)}",
            options=options,
            choix=choix,
            resumes=calculs.resumer_reglages(regles, bloc, dejeuner, options, choix),
            ouvert=ouvert | ({"dejeuner"} if erreurs_dejeuner else set()),
            nb_retouches=calculs.nb_retouches_version_active(session, semestre.id),
            dejeuner=dejeuner,
            erreurs_dejeuner=erreurs_dejeuner,
            dejeuner_fixe=calculs.DEJEUNER_FIXE,
            dejeuner_libre=calculs.DEJEUNER_LIBRE,
            dejeuner_aucun=calculs.DEJEUNER_AUCUN,
            libelles_dejeuner=calculs.LIBELLES_DEJEUNER,
            bornes_duree_dejeuner=calculs.BORNES_DUREE_DEJEUNER,
            en_cours=en_cours,
            r=en_cours,
            dernier=(
                calculs.resultat_calcul(session, dernier.id, indicateurs=False)
                if dernier is not None and (suivi is None or dernier.id != suivi.id)
                else None
            ),
        )
    return rendre(request, "edt/calculs.html", contexte, statut=statut, messages=messages)


@routeur.get("/calculs", name="calculs")
def page_calculs(request: Request, session: SessionRequete, semestre_id: EntierFacultatif = None):
    """Écran de lancement d'un calcul (`semestre_id` vide : le semestre du jour)."""
    return _page_calculs(request, session, semestre_id)


@routeur.get("/calculs/verifications", name="calculs_verifications")
def fragment_verifications(
    request: Request,
    session: SessionRequete,
    semestre_id: int,
    saisis: NiveauxSaisis,
    champs_regles: ChampsRegles,
):
    """Vérifications préalables avec les niveaux actuellement choisis (fragment HTMX).

    Niveaux des contraintes et des règles personnalisées (règles ignorées
    comprises). Un choix illisible lève `ErreurSaisieNiveaux` ou
    `ErreurSaisieRegles`, rendues comme toute erreur métier (message, pas de trace).
    Ouverte hors HTMX (adresse saisie, lien), elle mène à la page « Générer ».
    """
    versions.exiger_semestre(session, semestre_id)
    if not est_htmx(request):
        return rediriger(request, f"/calculs?semestre_id={semestre_id}")
    choix_regles, bloc = calculs.lire_choix_regles(session, semestre_id, champs_regles)
    pre = calculs.pre_verifier(
        session,
        semestre_id,
        calculs.lire_niveaux(saisis),
        niveaux_regles=regles_personnalisees.niveaux_pour_le_calcul(choix_regles, bloc),
    )
    contexte = {
        "pre": pre,
        "fmt": presentation,
        "annee_regles": versions.annee_du_semestre(session, semestre_id),
    }
    return rendre(request, "edt/_pre_verifications.html", contexte)


@routeur.post("/calculs", name="lancer_calcul")
def lancer(
    request: Request,
    session: SessionRequete,
    semestre_id: Annotated[int, Form()],
    saisis: NiveauxSaisis,
    champs_regles: ChampsRegles,
    duree: Annotated[str, Form()] = "court",
    optimiser: Annotated[bool, Form()] = False,
    autre_solution: Annotated[bool, Form()] = False,
    dejeuner: Annotated[str | None, Form()] = None,
    dejeuner_debut: Annotated[str | None, Form()] = None,
    dejeuner_fin: Annotated[str | None, Form()] = None,
    dejeuner_duree: Annotated[str | None, Form()] = None,
):
    """Lit les niveaux des règles, règle la pause déjeuner puis lance un calcul ; refus expliqué sinon.

    Une règle sans champ `niveau_<CODE>` garde son niveau par défaut, une
    règle personnalisée sans champ le sien. Sans le bloc « Pause déjeuner »
    (`dejeuner` absent), la pause déjeuner enregistrée reste telle quelle.
    """
    choix = {"duree": duree, "optimiser": optimiser, "autre_solution": autre_solution}
    try:
        choix_regles, bloc = calculs.lire_choix_regles(session, semestre_id, champs_regles)
    except regles_personnalisees.ErreurSaisieRegles as erreur:
        saisie, erreurs_dejeuner = calculs.relire_saisie_dejeuner(
            session, dejeuner, dejeuner_debut, dejeuner_fin, dejeuner_duree
        )
        return _page_calculs(
            request,
            session,
            semestre_id,
            niveaux=calculs.lire_niveaux_lisibles(saisis),
            choix_regles=erreur.choix,
            choix=choix,
            dejeuner=saisie,
            erreurs_dejeuner=erreurs_dejeuner,
            ouvert=frozenset({"regles_personnalisees"}),
            statut=422,
            messages=[Message(erreur.message, "erreur")],
        )
    try:
        niveaux = calculs.lire_niveaux(saisis)
    except calculs.ErreurSaisieNiveaux as erreur:
        # La pause déjeuner saisie revient telle quelle, avec ses propres fautes s'il y en a.
        saisie, erreurs_dejeuner = calculs.relire_saisie_dejeuner(
            session, dejeuner, dejeuner_debut, dejeuner_fin, dejeuner_duree
        )
        return _page_calculs(
            request,
            session,
            semestre_id,
            niveaux=erreur.niveaux,
            choix_regles=choix_regles,
            choix=choix,
            dejeuner=saisie,
            erreurs_dejeuner=erreurs_dejeuner,
            ouvert=frozenset({"regles"}),
            statut=422,
            messages=[Message(erreur.message, "erreur")],
        )
    # Règles illisibles (déjà dit sur la page) : rien à transmettre, le moteur lit les mêmes.
    niveaux_regles = regles_personnalisees.niveaux_pour_le_calcul(choix_regles, bloc)
    try:
        calcul_id = calculs.demarrer_calcul(
            session,
            semestre_id,
            niveaux=niveaux,
            longue=duree == "long",
            optimiser=optimiser,
            autre_solution=autre_solution,
            dejeuner=calculs.lire_saisie_dejeuner(
                dejeuner, dejeuner_debut, dejeuner_fin, dejeuner_duree
            ),
            niveaux_regles=niveaux_regles,
        )
    except calculs.ErreurMemeCalculEnCours as erreur:
        suivi = erreur.suivi
        if suivi.temps_ecoule_s < DELAI_DOUBLE_ENVOI_S:
            return rediriger(request, f"/calculs/{suivi.id}", "Calcul lancé.")
        return rediriger(
            request,
            f"/calculs/{suivi.id}",
            "Un calcul est déjà en cours : voici son avancement.",
            "attention",
        )
    except generation.ErreurLancementRefuse as erreur:
        # Tests, import ou chargement en cours : retour sur Générer, avec la cause et le remède.
        return rediriger(
            request, f"/calculs?semestre_id={semestre_id}", erreur.message, "attention"
        )
    except ErreurCalculEnCours:
        # Autre semestre ou autres réglages : les réglages envoyés n'ont pas été appliqués.
        suivi = generation.calcul_en_cours()
        cible = f"/calculs/{suivi.id}" if suivi else "/calculs"
        return rediriger(
            request, cible, "Un calcul est déjà en cours : voici son avancement.", "attention"
        )
    except calculs.ErreurSaisieDejeuner as erreur:
        return _page_calculs(
            request,
            session,
            semestre_id,
            niveaux=niveaux,
            choix_regles=choix_regles,
            choix=choix,
            dejeuner=erreur.saisie,
            erreurs_dejeuner=erreur.champs,
            statut=422,
            messages=[Message(erreur.message, "erreur")],
        )
    except calculs.ErreurPreVerification as erreur:
        return _page_calculs(
            request,
            session,
            semestre_id,
            niveaux=niveaux,
            choix_regles=choix_regles,
            choix=choix,
            pre=erreur.pre_verification,
            statut=409,
            messages=[Message(erreur.message, "erreur")],
        )
    return rediriger(request, f"/calculs/{calcul_id}", "Calcul lancé.")


@routeur.get("/calculs/{calcul_id:int}", name="calcul")
def page_calcul(request: Request, session: SessionRequete, calcul_id: int):
    """Suivi puis résultat d'un calcul."""
    resultat = calculs.resultat_calcul(session, calcul_id)
    _prechauffer_si_fini(resultat)
    return rendre(request, "edt/calcul.html", {"r": resultat, "fmt": presentation})


def _prechauffer_si_fini(resultat: calculs.ResultatCalcul) -> None:
    """Calcul réussi : prépare en arrière-plan les écrans que l'utilisateur va ouvrir."""
    if resultat.issue == calculs.Issue.REUSSI:
        prechauffage.lancer(charger_configuration().db_url)


@routeur.get("/calculs/{calcul_id:int}/etat", name="calcul_etat")
def fragment_etat(request: Request, session: SessionRequete, calcul_id: int):
    """État du calcul (fragment interrogé chaque seconde tant que le calcul tourne).

    Ouvert hors HTMX (adresse saisie, lien), il mène à la page du calcul.
    """
    if not est_htmx(request):
        return rediriger(request, f"/calculs/{calcul_id}")
    resultat = calculs.resultat_calcul(session, calcul_id)
    _prechauffer_si_fini(resultat)
    # Calcul fini : le bandeau de la page (« Calcul lancé. ») est périmé, il est vidé.
    return rendre(
        request,
        "edt/_suivi_calcul.html",
        {"r": resultat, "fmt": presentation, "vider_messages": not resultat.en_cours},
    )


@routeur.post("/calculs/{calcul_id:int}/arreter", name="arreter_calcul")
def arreter(request: Request, session: SessionRequete, calcul_id: int):
    """Demande l'arrêt du calcul (le moteur rend la main dès qu'il voit la demande).

    Un calcul déjà terminé (deux onglets ouverts, par exemple) n'est pas
    « arrêté » : la page le dit.
    """
    _, tournait = calculs.arreter_calcul(calcul_id)
    remarque = None if tournait else calculs.CALCUL_DEJA_TERMINE
    if est_htmx(request):
        resultat = calculs.resultat_calcul(session, calcul_id, indicateurs=False)
        return rendre(
            request,
            "edt/_suivi_calcul.html",
            {"r": resultat, "fmt": presentation, "remarque": remarque},
        )
    return rediriger(request, f"/calculs/{calcul_id}", remarque or "Arrêt demandé.", "info")


@routeur.post("/calculs/{calcul_id:int}/relancer", name="relancer_calcul")
def relancer(
    request: Request,
    session: SessionRequete,
    calcul_id: int,
    longue: Annotated[bool | None, Form()] = None,
    autre_solution: Annotated[bool, Form()] = False,
):
    """Relance un calcul avec les mêmes règles (plus longtemps, ou pour une autre solution)."""
    try:
        relance = calculs.relancer_calcul(
            session, calcul_id, longue=longue, autre_solution=autre_solution
        )
    except ErreurCalculEnCours:
        suivi = generation.calcul_en_cours()
        cible = f"/calculs/{suivi.id}" if suivi else "/calculs"
        return rediriger(
            request, cible, "Un calcul est déjà en cours : voici son avancement.", "attention"
        )
    except generation.ErreurLancementRefuse as erreur:
        semestre_id = calculs.resultat_calcul(session, calcul_id, indicateurs=False).semestre.id
        return rediriger(
            request, f"/calculs?semestre_id={semestre_id}", erreur.message, "attention"
        )
    except calculs.ErreurPreVerification as erreur:
        semestre_id = calculs.resultat_calcul(session, calcul_id, indicateurs=False).semestre.id
        return rediriger(request, f"/calculs?semestre_id={semestre_id}", erreur.message, "erreur")
    if relance.regles_nouvelles:
        return rediriger(
            request,
            f"/calculs/{relance.calcul_id}",
            "Calcul relancé. Règles créées ou activées depuis le calcul d'origine, appliquées à "
            f"leur propre niveau : {', '.join(relance.regles_nouvelles)}.",
            "info",
        )
    return rediriger(request, f"/calculs/{relance.calcul_id}", "Calcul relancé.")


@routeur.get("/versions", name="versions")
def page_versions(request: Request, session: SessionRequete, semestre_id: EntierFacultatif = None):
    """Historique des calculs et des versions d'un semestre."""
    semestre = versions.choisir_semestre(session, semestre_id)
    historique = versions.historique(session, semestre.id) if semestre else None
    return rendre(
        request,
        "edt/versions.html",
        {
            "fmt": presentation,
            "semestres": versions.lister_semestres(session),
            "historique": historique,
        },
    )


@routeur.post("/versions/{version_id:int}/activer", name="activer_version")
def activer(request: Request, session: SessionRequete, version_id: int, taches: BackgroundTasks):
    """Rend une version active pour son semestre (écrans préparés après l'enregistrement)."""
    version = versions.activer_version(session, version_id)
    taches.add_task(prechauffage.lancer, charger_configuration().db_url)
    return rediriger(
        request,
        f"/versions?semestre_id={version.semestre_id}",
        f"« {version.libelle} » est maintenant la version active.",
    )


@routeur.post("/versions/{version_id:int}/supprimer", name="supprimer_version")
def supprimer(
    request: Request,
    session: SessionRequete,
    version_id: int,
    semestre_id: EntierFacultatif = None,
):
    """Supprime une version qui n'est pas active (après confirmation dans la page).

    Une version qui n'existe pas ou plus (double clic, second onglet) mène à l'historique avec un
    message d'information, jamais à une page d'erreur : le résultat voulu est atteint. Le
    semestre de la page d'origine (`semestre_id`, dans l'adresse du formulaire) y est gardé.
    """
    try:
        semestre_id = versions.lire_version(session, version_id).semestre_id
    except ErreurIntrouvable:
        journal.info("Suppression de la version %s : elle n'existe pas ou plus", version_id)
        historique = "/versions" if semestre_id is None else f"/versions?semestre_id={semestre_id}"
        return rediriger(
            request, historique, "Cette version n'existe plus : elle a déjà été supprimée.", "info"
        )
    libelle = versions.supprimer_version(session, version_id)
    return rediriger(request, f"/versions?semestre_id={semestre_id}", f"« {libelle} » supprimée.")
