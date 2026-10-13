"""Onglet « Générer » : lancer et suivre un calcul, historique des calculs et des versions.

Adresses :

- `GET /calculs` : choix du semestre, récapitulatif, vérifications
  préalables, tableau des règles et de leur niveau (préremplis avec ceux du
  dernier calcul du semestre), pause déjeuner, options, lancement ; si un
  calcul tourne déjà, la page le montre au lieu de proposer d'en lancer un
  second ;
- `GET /calculs/verifications` : fragment des vérifications préalables,
  recalculé quand on change le niveau d'une règle (champs `niveau_<CODE>`) ;
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

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request

from edt.config import charger_configuration
from edt.domaine.contraintes import Niveaux
from edt.erreurs import ErreurCalculEnCours
from edt.services import generation
from edt.services.emploi_du_temps import (
    calculs,
    prechauffage,
    presentation,
    regles_personnalisees,
    versions,
)
from edt.web.dependances import SessionRequete
from edt.web.gabarits import Message, est_htmx, rediriger, rendre
from edt.web.menu import EntreeMenu

SECTION_GENERER = "Générer"

routeur = APIRouter()
MENU = [
    EntreeMenu("Lancer un calcul", "/calculs", section=SECTION_GENERER, ordre=250),
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
    statut: int = 200,
    messages=(),
):
    semestres = versions.lister_semestres(session)
    semestre = versions.choisir_semestre(session, semestre_id)
    contexte: dict = {"fmt": presentation, "semestres": semestres, "semestre": semestre}
    if semestre is not None:
        suivi = generation.calcul_en_cours()
        en_cours = calculs.resultat_calcul(session, suivi.id, indicateurs=False) if suivi else None
        dernier = generation.dernier_calcul(semestre.id)
        if niveaux is None:
            niveaux = calculs.niveaux_proposes(dernier)
        recap = calculs.recapitulatif(session, semestre.id)
        options = calculs.options_formulaire(session)
        bloc = calculs.bloc_regles_du_semestre(
            session, semestre.id, choix=choix_regles, dernier=dernier
        )
        niveaux_regles = regles_personnalisees.niveaux_pour_le_calcul(bloc.choix, bloc)
        contexte.update(
            recap=recap,
            pre=pre
            or calculs.pre_verifier(
                session, semestre.id, niveaux, recap, niveaux_regles=niveaux_regles
            ),
            regles=calculs.regles_proposees(niveaux),
            niveaux_proposes=calculs.NIVEAUX_PROPOSES,
            bloc_regles=bloc,
            adresse_regles=regles_personnalisees.ADRESSE_REGLES,
            adresse_ajout_regle=regles_personnalisees.ADRESSE_AJOUT_REGLE,
            options=options,
            choix=choix
            or {"duree": "court", "optimiser": options.optimiser, "autre_solution": False},
            dejeuner=dejeuner or calculs.choix_dejeuner(session),
            erreurs_dejeuner=erreurs_dejeuner or {},
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
def page_calculs(request: Request, session: SessionRequete, semestre_id: int | None = None):
    """Écran de lancement d'un calcul."""
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
    """
    versions.exiger_semestre(session, semestre_id)
    choix_regles, bloc = calculs.lire_choix_regles(session, semestre_id, champs_regles)
    pre = calculs.pre_verifier(
        session,
        semestre_id,
        calculs.lire_niveaux(saisis),
        niveaux_regles=regles_personnalisees.niveaux_pour_le_calcul(choix_regles, bloc),
    )
    return rendre(request, "edt/_pre_verifications.html", {"pre": pre, "fmt": presentation})


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
    except ErreurCalculEnCours:
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
    """État du calcul (fragment interrogé chaque seconde tant que le calcul tourne)."""
    resultat = calculs.resultat_calcul(session, calcul_id)
    _prechauffer_si_fini(resultat)
    return rendre(request, "edt/_suivi_calcul.html", {"r": resultat, "fmt": presentation})


@routeur.post("/calculs/{calcul_id:int}/arreter", name="arreter_calcul")
def arreter(request: Request, session: SessionRequete, calcul_id: int):
    """Demande l'arrêt du calcul (le moteur rend la main dès qu'il voit la demande)."""
    generation.demander_arret(calcul_id)
    if est_htmx(request):
        resultat = calculs.resultat_calcul(session, calcul_id, indicateurs=False)
        return rendre(request, "edt/_suivi_calcul.html", {"r": resultat, "fmt": presentation})
    return rediriger(request, f"/calculs/{calcul_id}", "Arrêt demandé.", "info")


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
        nouveau = calculs.relancer_calcul(
            session, calcul_id, longue=longue, autre_solution=autre_solution
        )
    except ErreurCalculEnCours:
        suivi = generation.calcul_en_cours()
        cible = f"/calculs/{suivi.id}" if suivi else "/calculs"
        return rediriger(
            request, cible, "Un calcul est déjà en cours : voici son avancement.", "attention"
        )
    except calculs.ErreurPreVerification as erreur:
        semestre_id = calculs.resultat_calcul(session, calcul_id, indicateurs=False).semestre.id
        return rediriger(request, f"/calculs?semestre_id={semestre_id}", erreur.message, "erreur")
    return rediriger(request, f"/calculs/{nouveau}", "Calcul relancé.")


@routeur.get("/versions", name="versions")
def page_versions(request: Request, session: SessionRequete, semestre_id: int | None = None):
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
def supprimer(request: Request, session: SessionRequete, version_id: int):
    """Supprime une version qui n'est pas active (après confirmation dans la page)."""
    semestre_id = versions.lire_version(session, version_id).semestre_id
    libelle = versions.supprimer_version(session, version_id)
    return rediriger(request, f"/versions?semestre_id={semestre_id}", f"« {libelle} » supprimée.")
