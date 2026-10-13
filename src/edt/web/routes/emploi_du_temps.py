"""Onglet « Emploi du temps » : voir et corriger l'emploi du temps d'un groupe, d'un enseignant ou d'une salle.

Adresses (paramètres de la page : `semestre_id`, `version_id`, au plus un de
`groupe_id` / `enseignant_id` / `salle_id` (ou `cible=groupe:12`), `couche`
`hebdo` ou `date`, `affichage` `semaine` ou `jour`, `date`, `jour`, `seance`
pour ouvrir le panneau d'une séance `h<id>` ou d'une occurrence `o<id>`) :

- `GET /emploi-du-temps` : la page (sélecteurs, grille, panneau) ;
- `GET /emploi-du-temps/seances/{id}/panneau`, `.../formulaire` : panneau
  latéral et formulaire de modification d'une séance (fragments HTMX) ;
- `POST /emploi-du-temps/seances/{id}/modifier`, `.../verrou` ;
- `GET /emploi-du-temps/occurrences/{id}/panneau`, `.../formulaire` ;
- `POST /emploi-du-temps/occurrences/{id}/deplacer`, `.../annuler` ;
- `POST /emploi-du-temps/versions/{id}/seances-datees` : (re)calculer les
  séances datées d'une version (les retouches à la main sont conservées).

Après une action, on revient sur la vue d'où l'on vient (`retour`, limité aux
adresses de cet onglet). Les exports (CSV, iCal) sont servis par un autre
lot, aux adresses `/export/emploi-du-temps.csv` et `.ics`.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from edt.domaine.enumerations import JOURS
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.emploi_du_temps import modifications, presentation, raccords, versions, vues
from edt.services.emploi_du_temps.vues import Affichage, Cible, Couche
from edt.web.dependances import SessionRequete
from edt.web.gabarits import Message, est_htmx, rediriger, rendre
from edt.web.menu import SECTION_EMPLOI_DU_TEMPS, EntreeMenu

journal = logging.getLogger(__name__)

routeur = APIRouter(prefix="/emploi-du-temps")
MENU = [
    EntreeMenu("Emploi du temps", "/emploi-du-temps", section=SECTION_EMPLOI_DU_TEMPS, ordre=300)
]

ADRESSE = "/emploi-du-temps"
COOKIE_CIBLE = "edt_derniere_cible"
"""Cookie du navigateur qui retient le dernier groupe, enseignant ou salle consulté."""
DUREE_COOKIE_S = 365 * 24 * 3600
NB_ANOMALIES_DANS_MESSAGE = 4


class UrlVue:
    """Construit les adresses de la page en gardant les paramètres de la vue courante."""

    def __init__(self, parametres: dict[str, str]) -> None:
        self.parametres = {cle: valeur for cle, valeur in parametres.items() if valeur is not None}

    def __call__(self, **changements) -> str:
        parametres = dict(self.parametres)
        for cle, valeur in changements.items():
            if valeur is None:
                parametres.pop(cle, None)
            else:
                parametres[cle] = str(valeur)
        return f"{ADRESSE}?{urlencode(parametres)}" if parametres else ADRESSE

    def sans(self, *cles: str) -> str:
        """L'adresse sans certains paramètres."""
        return self(**dict.fromkeys(cles))


def _parametres(vue: vues.VueEmploiDuTemps) -> dict[str, str]:
    parametres = {"version_id": str(vue.version.id)}
    if vue.cible is not None:
        parametres[f"{vue.cible.nature.value}_id"] = str(vue.cible.id)
    parametres["couche"] = vue.couche.value
    parametres["affichage"] = vue.affichage.value
    if vue.couche is Couche.DATE and vue.date is not None:
        parametres["date"] = vue.date.isoformat()
    if vue.couche is Couche.HEBDO and vue.affichage is Affichage.JOUR and vue.jour is not None:
        parametres["jour"] = str(vue.jour)
    perimetre = vue.perimetre
    if perimetre is not None and perimetre.etendu != perimetre.etendu_par_defaut:
        parametres["etendu"] = "1" if perimetre.etendu else "0"
    return parametres


def _cible_retenue(request: Request) -> Cible | None:
    """Dernière cible consultée, lue dans le cookie (ignorée, et journalisée, si illisible)."""
    texte = request.cookies.get(COOKIE_CIBLE)
    if not texte:
        return None
    try:
        return Cible.lire(texte)
    except ErreurDonneesInvalides:
        journal.info("Cookie de dernière cible illisible, ignoré : %r", texte[:50])
        return None


def _retour(retour: str | None, defaut: str = ADRESSE) -> str:
    """L'adresse de retour si elle reste dans cet onglet, sinon `defaut` (pas de redirection ouverte)."""
    if (
        retour
        and retour.startswith(ADRESSE)
        and "//" not in retour
        and "\\" not in retour
        and not any(c in retour for c in "\r\n")
    ):
        return retour
    return defaut


def _adresse_de_la_page(request: Request, retour: str | None, cle: str) -> str | None:
    """Adresse de retour d'un panneau : `retour` s'il est donné, sinon la page d'où vient HTMX.

    HTMX envoie l'adresse de la page dans l'en-tête `HX-Current-URL` : inutile
    de la répéter dans chaque bloc de la grille. On y met la séance `cle` en
    évidence, pour revenir sur elle après une action.
    """
    if retour:
        return retour
    courante = request.headers.get("HX-Current-URL")
    if not courante:
        return None
    morceaux = urlsplit(courante)
    if morceaux.path != ADRESSE:
        return None
    parametres = dict(parse_qsl(morceaux.query))
    parametres["seance"] = cle
    return f"{ADRESSE}?{urlencode(parametres)}"


def _entier_ou_rien(texte: str | None, nom: str) -> int | None:
    if texte is None or texte.strip() == "":
        return None
    try:
        return int(texte)
    except ValueError as erreur:
        raise ErreurDonneesInvalides(f"{nom} : un nombre entier est attendu.") from erreur


def _resolution(session, semestre_id: int | None, version_id: int | None):
    """(semestre, version) à afficher ; la version demandée l'emporte si elle est du semestre."""
    version = None
    if version_id is not None:
        version = versions.lire_version(session, version_id)
        if semestre_id is not None and version.semestre_id != semestre_id:
            version = None
        else:
            semestre_id = version.semestre_id
    semestre = versions.choisir_semestre(session, semestre_id)
    if semestre is not None and version is None:
        version = versions.version_active(session, semestre.id)
        if version is None:
            existantes = versions.lister_versions(session, semestre.id)
            version = existantes[0] if existantes else None
    return semestre, version


def _message_resultat(resultat: modifications.ResultatModification) -> str:
    morceaux = [resultat.message, *resultat.remarques]
    anomalies = [a.message for a in resultat.anomalies[:NB_ANOMALIES_DANS_MESSAGE]]
    if anomalies:
        morceaux.append(
            ("Raisons : " if not resultat.enregistree else "À noter : ") + " ".join(anomalies)
        )
    reste = len(resultat.anomalies) - NB_ANOMALIES_DANS_MESSAGE
    if reste > 0:
        morceaux.append(f"(et {reste} autre(s) remarque(s))")
    return " ".join(morceaux)


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #


@routeur.get("", name="emploi_du_temps")
def page(
    request: Request,
    session: SessionRequete,
    semestre_id: int | None = None,
    version_id: int | None = None,
    groupe_id: int | None = None,
    enseignant_id: int | None = None,
    salle_id: int | None = None,
    cible: str | None = None,
    couche: Couche = Couche.HEBDO,
    affichage: Affichage = Affichage.SEMAINE,
    date: dt.date | None = None,
    jour: int | None = None,
    seance: str | None = None,
    etendu: bool | None = None,
):
    """L'emploi du temps choisi, et le panneau d'une séance si `seance` est donné.

    Sans cible dans l'adresse, la page s'ouvre sur la dernière cible consultée
    (cookie du navigateur), sinon sur la première classe de l'année.
    """
    semestre, version = _resolution(session, semestre_id, version_id)
    contexte: dict = {
        "fmt": presentation,
        "semestres": versions.lister_semestres(session),
        "semestre": semestre,
        "version": version,
        "vue": None,
    }
    messages: list[Message] = []
    if semestre is None or version is None:
        return rendre(request, "edt/emploi_du_temps.html", contexte)

    annee_id = versions.annee_du_semestre(session, semestre.id)
    if date is not None and not vues.date_admissible(date, semestre):
        messages.append(
            Message(
                f"La date du {date:%d/%m/%Y} est trop loin du {semestre.libelle.lower()} : "
                "affichage de la semaine en cours du semestre à la place.",
                "attention",
            )
        )
        date = None
    choisie = Cible.depuis(
        groupe_id=groupe_id, enseignant_id=enseignant_id, salle_id=salle_id, texte=cible
    )
    origine = "demandee"
    if choisie is None:
        choisie = _cible_retenue(request)
        origine = "retenue" if choisie is not None else "defaut"
    if choisie is None:
        choisie = vues.cible_par_defaut(session, annee_id)

    def construire(cible_vue, etendu_vue):
        return vues.construire_vue(
            session,
            version_id=version.id,
            cible=cible_vue,
            couche=couche,
            affichage=affichage,
            date=date,
            jour=jour,
            etendu=etendu_vue,
        )

    try:
        vue = construire(choisie, etendu)
    except ErreurIntrouvable as erreur:
        # Lien ancien, cible retenue d'une autre année, ou changement de semestre sans
        # JavaScript : on montre la cible par défaut.
        defaut = vues.cible_par_defaut(session, annee_id)
        if choisie == defaut:
            raise
        if origine == "demandee":
            messages.append(
                Message(
                    f"{erreur.message} Affichage de la première classe à la place.", "attention"
                )
            )
        else:
            journal.info("Dernière cible consultée introuvable ici (%s) : cible par défaut", erreur)
        origine = "defaut"
        vue = construire(defaut, None)
        seance = None
    url_vue = UrlVue(_parametres(vue))
    panneau = None
    if seance:
        try:
            panneau = _panneau(session, seance, vue)
        except (ErreurIntrouvable, ErreurDonneesInvalides) as erreur:
            messages.append(
                Message(f"{erreur.message} La séance a pu être déplacée ou supprimée.", "attention")
            )
    export = {"version_id": vue.version.id, "couche": vue.couche.value}
    if vue.cible is not None:
        export[f"{vue.cible.nature.value}_id"] = vue.cible.id
    contexte.update(
        vue=vue,
        url_vue=url_vue,
        retour=url_vue(seance=seance) if seance else url_vue(),
        selection=seance,
        panneau=panneau,
        options_cibles=vues.options_cibles(session, annee_id),
        versions_semestre=versions.lister_versions(session, semestre.id),
        export=urlencode(export),
        JOURS=JOURS,
        origine_cible=origine,
        annee_regles=annee_id,
    )
    reponse = rendre(request, "edt/emploi_du_temps.html", contexte, messages=messages)
    if vue.cible is not None:
        reponse.set_cookie(
            COOKIE_CIBLE, vue.cible.valeur, max_age=DUREE_COOKIE_S, httponly=True, samesite="lax"
        )
    return reponse


def _panneau(session, seance: str, vue: vues.VueEmploiDuTemps) -> dict:
    """Données du panneau latéral pour `seance` (`h<id>` ou `o<id>`)."""
    nature, identifiant = seance[:1], seance[1:]
    if nature not in ("h", "o") or not identifiant.isdigit():
        raise ErreurDonneesInvalides(f"Séance demandée invalide : {seance!r}.")
    identifiant_entier = int(identifiant)
    marques = next((b.anomalies for b in vue.blocs if b.cle == seance), None)
    if nature == "h":
        formulaire = modifications.formulaire_seance(session, identifiant_entier)
        if formulaire.version_id != vue.version.id:
            raise ErreurIntrouvable("Cette séance n'appartient pas à la version affichée.")
        return {"type": "seance", "f": formulaire, "marques": marques, "controle": vue.controle}
    formulaire = modifications.formulaire_occurrence(session, identifiant_entier)
    if formulaire.version_id != vue.version.id:
        raise ErreurIntrouvable("Cette séance n'appartient pas à la version affichée.")
    return {"type": "occurrence", "f": formulaire, "marques": marques, "controle": vue.controle}


# --------------------------------------------------------------------------- #
# Séances hebdomadaires
# --------------------------------------------------------------------------- #


def _annee_de_la_version(session, version_id: int) -> int:
    """Année scolaire d'une version : les liens des règles citées ouvrent la liste de cette année."""
    return versions.annee_du_semestre(
        session, versions.lire_version(session, version_id).semestre_id
    )


def _rendre_panneau_seance(request, session, formulaire, retour, *, resultat=None, statut=200):
    detail = modifications.anomalies_seance(session, formulaire.bloc.seance_hebdo_id)
    return rendre(
        request,
        "edt/_panneau_seance.html",
        {
            "fmt": presentation,
            "annee_regles": _annee_de_la_version(session, formulaire.version_id),
            "f": formulaire,
            "retour": retour,
            "resultat": resultat,
            "marques": detail.anomalies,
            "controle": detail.controle,
        },
        statut=statut,
    )


@routeur.get("/seances/{seance_id:int}/panneau", name="panneau_seance")
def panneau_seance(
    request: Request, session: SessionRequete, seance_id: int, retour: str | None = None
):
    """Détail et formulaire d'une séance hebdomadaire (fragment)."""
    formulaire = modifications.formulaire_seance(session, seance_id)
    retour = _retour(
        _adresse_de_la_page(request, retour, f"h{seance_id}"),
        f"{ADRESSE}?version_id={formulaire.version_id}&seance=h{seance_id}",
    )
    if not est_htmx(request):
        return RedirectResponse(retour, status_code=303)
    return _rendre_panneau_seance(request, session, formulaire, retour)


@routeur.get("/seances/{seance_id:int}/formulaire", name="formulaire_seance")
def formulaire_seance(
    request: Request,
    session: SessionRequete,
    seance_id: int,
    jour: int | None = None,
    debut: int | None = None,
    salle_id: str | None = None,
    verrouiller: bool = False,
    retour: str | None = None,
):
    """Formulaire recalculé quand on change le jour ou l'heure (débuts et salles libres)."""
    formulaire = modifications.formulaire_seance(
        session, seance_id, jour=jour, debut=debut, salle_id=_entier_ou_rien(salle_id, "Salle")
    )
    return rendre(
        request,
        "edt/_formulaire_seance.html",
        {
            "fmt": presentation,
            "f": formulaire,
            "retour": _retour(retour),
            "resultat": None,
            "verrouiller": verrouiller,
        },
    )


@routeur.post("/seances/{seance_id:int}/modifier", name="modifier_seance")
def modifier_seance(
    request: Request,
    session: SessionRequete,
    seance_id: int,
    jour: Annotated[int, Form()],
    debut: Annotated[int, Form()],
    salle_id: Annotated[str, Form()] = "",
    verrouiller: Annotated[bool, Form()] = False,
    retour: Annotated[str, Form()] = "",
):
    """Vérifie puis enregistre le nouveau placement ; un refus est expliqué dans le panneau."""
    resultat = modifications.modifier_placement(
        session,
        seance_id,
        jour=jour,
        debut=debut,
        salle_id=_entier_ou_rien(salle_id, "Salle"),
        verrouiller=True if verrouiller else None,
    )
    retour = _retour(retour)
    if resultat.enregistree:
        return rediriger(request, retour, _message_resultat(resultat), resultat.niveau)
    if not est_htmx(request):
        return rediriger(request, retour, _message_resultat(resultat), resultat.niveau)
    formulaire = modifications.formulaire_seance(
        session, seance_id, jour=jour, debut=debut, salle_id=_entier_ou_rien(salle_id, "Salle")
    )
    return _rendre_panneau_seance(request, session, formulaire, retour, resultat=resultat)


@routeur.post("/seances/{seance_id:int}/verrou", name="verrou_seance")
def verrou_seance(
    request: Request,
    session: SessionRequete,
    seance_id: int,
    verrou: Annotated[bool, Form()],
    retour: Annotated[str, Form()] = "",
):
    """Pose ou lève le verrou d'une séance."""
    message = modifications.definir_verrou(session, seance_id, verrou)
    return rediriger(request, _retour(retour), message)


# --------------------------------------------------------------------------- #
# Séances datées
# --------------------------------------------------------------------------- #


def _rendre_panneau_occurrence(request, session, formulaire, retour, *, resultat=None):
    detail = modifications.anomalies_occurrence(session, formulaire.bloc.occurrence_id)
    return rendre(
        request,
        "edt/_panneau_occurrence.html",
        {
            "fmt": presentation,
            "annee_regles": _annee_de_la_version(session, formulaire.version_id),
            "f": formulaire,
            "retour": retour,
            "resultat": resultat,
            "marques": detail.anomalies,
            "controle": detail.controle,
        },
    )


@routeur.get("/occurrences/{occurrence_id:int}/panneau", name="panneau_occurrence")
def panneau_occurrence(
    request: Request, session: SessionRequete, occurrence_id: int, retour: str | None = None
):
    """Détail et formulaire d'une séance datée (fragment)."""
    formulaire = modifications.formulaire_occurrence(session, occurrence_id)
    retour = _retour(
        _adresse_de_la_page(request, retour, f"o{occurrence_id}"),
        f"{ADRESSE}?version_id={formulaire.version_id}&couche=date"
        f"&date={formulaire.bloc.date.isoformat()}&seance=o{occurrence_id}",
    )
    if not est_htmx(request):
        return RedirectResponse(retour, status_code=303)
    return _rendre_panneau_occurrence(request, session, formulaire, retour)


@routeur.get("/occurrences/{occurrence_id:int}/formulaire", name="formulaire_occurrence")
def formulaire_occurrence(
    request: Request,
    session: SessionRequete,
    occurrence_id: int,
    date: dt.date | None = None,
    debut: int | None = None,
    salle_id: str | None = None,
    retour: str | None = None,
):
    """Formulaire recalculé quand on change la date ou l'heure."""
    formulaire = modifications.formulaire_occurrence(
        session, occurrence_id, date=date, debut=debut, salle_id=_entier_ou_rien(salle_id, "Salle")
    )
    return rendre(
        request,
        "edt/_formulaire_occurrence.html",
        {"fmt": presentation, "f": formulaire, "retour": _retour(retour), "resultat": None},
    )


@routeur.post("/occurrences/{occurrence_id:int}/deplacer", name="deplacer_occurrence")
def deplacer_occurrence(
    request: Request,
    session: SessionRequete,
    occurrence_id: int,
    date: Annotated[dt.date, Form()],
    debut: Annotated[int, Form()],
    salle_id: Annotated[str, Form()] = "",
    retour: Annotated[str, Form()] = "",
):
    """Vérifie puis enregistre le déplacement d'une séance datée."""
    resultat = modifications.deplacer_occurrence(
        session, occurrence_id, date=date, debut=debut, salle_id=_entier_ou_rien(salle_id, "Salle")
    )
    retour = _retour(retour)
    if resultat.enregistree or not est_htmx(request):
        return rediriger(request, retour, _message_resultat(resultat), resultat.niveau)
    formulaire = modifications.formulaire_occurrence(
        session, occurrence_id, date=date, debut=debut, salle_id=_entier_ou_rien(salle_id, "Salle")
    )
    return _rendre_panneau_occurrence(request, session, formulaire, retour, resultat=resultat)


@routeur.post("/occurrences/{occurrence_id:int}/annuler", name="annuler_occurrence")
def annuler_occurrence(
    request: Request,
    session: SessionRequete,
    occurrence_id: int,
    motif: Annotated[str, Form()] = "",
    retour: Annotated[str, Form()] = "",
):
    """Annule une séance datée (après confirmation dans la page)."""
    message = modifications.annuler_occurrence(session, occurrence_id, motif)
    return rediriger(request, _retour(retour), message)


@routeur.post("/versions/{version_id:int}/seances-datees", name="recalculer_seances_datees")
def recalculer_seances_datees(
    request: Request,
    session: SessionRequete,
    version_id: int,
    retour: Annotated[str, Form()] = "",
):
    """Réapplique la semaine type au calendrier ; les retouches à la main sont conservées."""
    retour = _retour(retour, f"{ADRESSE}?version_id={version_id}&couche=date")
    try:
        bilan = modifications.recalculer_seances_datees(session, version_id)
    except (raccords.ErreurRaccordIndisponible, ErreurRegleMetier) as erreur:
        # Séances datées importées ou instanciation qui refuse : rien n'a été écrit.
        return rediriger(request, retour, erreur.message, "attention")
    return rediriger(request, retour, bilan.message)
