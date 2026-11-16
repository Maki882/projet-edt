"""Écran « Données > Règles et calendrier > Règles personnalisées » (`/regles`).

Liste des règles d'une année scolaire (filtres, recherche, pagination,
règles proposées à confirmer), ajout guidé (type, puis portée, paramètres,
niveau, période, avec un aperçu de la phrase), modification, changement
d'état, suppression (confirmation dans la ligne, page de confirmation sans
JavaScript) et proposition des règles de base. Toute lecture et toute
écriture passent par `edt.services.regles` ; voir `docs/regles-personnalisees.md`.

Sans aucune année scolaire, l'écran l'explique (et mène au calendrier) au
lieu d'une erreur. Un identifiant impossible donne « introuvable » ; un
fragment (`/ligne...`, `/apercu`) demandé sans HTMX renvoie vers sa page.
"""

from __future__ import annotations

import logging
import math
import unicodedata
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import QueryParams

from edt.db import modeles as m
from edt.db.moteur import identifiant_valide
from edt.domaine.contraintes import Niveau
from edt.domaine.regles import (
    CATALOGUE_TYPES,
    DefinitionType,
    ErreurSaisie,
    EtatRegle,
    NaturePortee,
    OrigineRegle,
    TypeRegle,
    definition_type,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services import regles as service_regles
from edt.services.parametres import lire_parametres
from edt.services.referentiels.conversion import entier_naturel
from edt.services.referentiels.description import options_reference
from edt.services.referentiels.personnes import REF_ANNEE, annee_par_defaut
from edt.web.dependances import SessionRequete
from edt.web.donnees.ecrans import CIBLE_LISTE, Formulaire, numero_de_page
from edt.web.donnees.regles import (
    brouillon_depuis_formulaire,
    controles_formulaire,
    erreurs_par_champ,
    natures_admises,
    options_groupes,
    rubriques_regle,
    valeurs_de_formulaire,
    valeurs_de_regle,
    valeurs_nouvelle,
)
from edt.web.gabarits import (
    Message,
    adresse_courante,
    adresse_locale,
    est_htmx,
    rediriger,
    rendre,
)
from edt.web.menu import SECTION_CONFIGURATION, EntreeMenu

journal = logging.getLogger(__name__)

PREFIXE = "/regles"
TAILLE_PAGE = 50
FILTRES = ("etat", "type", "niveau", "origine", "portee", "groupe")

routeur = APIRouter(prefix=PREFIXE)
MENU = [
    EntreeMenu("Règles personnalisées", PREFIXE, section=SECTION_CONFIGURATION, ordre=505),
]


# --------------------------------------------------------------------------- #
# Aides
# --------------------------------------------------------------------------- #


def adresse_sure(brute: object, defaut: str) -> str:
    """Adresse de retour locale (« /salles?q=... », `adresse_locale`), sinon `defaut`."""
    texte = str(brute or "")
    if adresse_locale(texte):
        return texte
    if texte:
        journal.info("Adresse de retour écartée : %r", texte[:200])
    return defaut


def _sans_annee(request: Request, session: Session) -> Response | None:
    """Page qui explique qu'il faut d'abord une année scolaire, s'il n'y en a aucune."""
    if session.scalar(select(func.count()).select_from(m.AnneeScolaire)):
        return None
    journal.info("Règles personnalisées : aucune année scolaire, renvoi vers le calendrier")
    return rendre(request, "donnees/regles_sans_annee.html", {})


def _annee(session: Session, valeur: object) -> m.AnneeScolaire:
    """Année choisie (identifiant), ou celle proposée par défaut ; `ErreurIntrouvable` sinon."""
    texte = str(valeur or "").strip()
    if not texte:
        defaut = annee_par_defaut(session)
        if defaut is None:
            raise ErreurIntrouvable(
                "Aucune année scolaire n'existe : en créer une dans Calendrier."
            )
        texte = str(defaut)
    ident = entier_naturel(texte)
    annee = session.get(m.AnneeScolaire, ident) if identifiant_valide(ident) else None
    if annee is None:
        raise ErreurIntrouvable(f"Année scolaire introuvable : « {texte[:30]} ».")
    return annee


def _sans_accent(texte: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texte.lower()) if not unicodedata.combining(c)
    )


def _texte_regles(nombre: int, adjectif: str) -> str:
    return f"{nombre} règle {adjectif}" if nombre <= 1 else f"{nombre} règles {adjectif}s"


def _parametres_liste(request: Request) -> QueryParams:
    """Paramètres de la liste : ceux de la requête, ou ceux de la page qui demande un fragment."""
    courante = adresse_courante(request)
    if not request.query_params and courante is not None and courante.path == PREFIXE:
        return QueryParams(courante.query)
    return request.query_params


def _choix_filtres(session: Session, annee: m.AnneeScolaire) -> dict[str, list[tuple[str, str]]]:
    return {
        "etat": [(e.value, e.libelle) for e in EtatRegle],
        "type": [(t.value, t.libelle) for t in TypeRegle],
        "niveau": [(n.value, n.libelle) for n in Niveau],
        "origine": [(o.value, o.libelle) for o in OrigineRegle],
        "portee": [(p.value, p.libelle) for p in NaturePortee],
        "groupe": options_groupes(session, annee.id),
    }


LIBELLES_FILTRES = {
    "etat": "État",
    "type": "Type",
    "niveau": "Niveau",
    "origine": "Origine",
    "portee": "Portée",
    "groupe": "Groupe (règles héritées comprises)",
}


def contexte_liste(request: Request, session: Session) -> dict[str, Any]:
    """Variables de la liste des règles : année, filtres, recherche, page, règles."""
    parametres = _parametres_liste(request)
    annee = _annee(session, parametres.get("annee"))
    choix = _choix_filtres(session, annee)
    filtres = {nom: parametres.get(nom, "").strip() for nom in FILTRES}
    for nom, valeur in filtres.items():
        if valeur and valeur not in {cle for cle, _ in choix[nom]}:
            raise ErreurDonneesInvalides(
                f"Filtre « {LIBELLES_FILTRES[nom]} » : valeur inconnue « {valeur} »."
            )
    recherche = parametres.get("q", "").strip()
    numero = numero_de_page(parametres)

    details = service_regles.lister(
        session,
        annee.id,
        etat=filtres["etat"] or None,
        type_regle=filtres["type"] or None,
        niveau=filtres["niveau"] or None,
        origine=filtres["origine"] or None,
        groupe_id=int(filtres["groupe"]) if filtres["groupe"] else None,
    )
    if filtres["portee"]:
        details = [d for d in details if d.portee.nature.value == filtres["portee"]]
    if recherche:
        motif = _sans_accent(recherche)
        details = [
            d
            for d in details
            if motif
            in _sans_accent(
                " ".join(
                    (
                        d.code,
                        d.phrase,
                        d.portee.cible_code or "",
                        d.portee.cible_libelle or "",
                        d.regle.commentaire,
                    )
                )
            )
        ]
    total = len(details)
    nb_pages = max(1, math.ceil(total / TAILLE_PAGE))
    page = min(numero, nb_pages)

    def url(**changements: Any) -> str:
        valeurs = {"annee": annee.id, "q": recherche, **filtres, "page": page, **changements}
        propres = {
            cle: v for cle, v in valeurs.items() if v not in ("", None) and (cle != "page" or v > 1)
        }
        return f"{PREFIXE}?{urlencode(propres)}"

    proposees = service_regles.lister(session, annee.id, etat=EtatRegle.PROPOSEE)
    return {
        "annee": annee,
        "annees": options_reference(session, REF_ANNEE),
        "filtres": [(nom, LIBELLES_FILTRES[nom], choix[nom], filtres[nom]) for nom in FILTRES],
        "recherche": recherche,
        "filtre_actif": bool(recherche or any(filtres.values())),
        "regles": details[(page - 1) * TAILLE_PAGE : page * TAILLE_PAGE],
        "total": total,
        "page": page,
        "nb_pages": nb_pages,
        "url_page": lambda numero: url(page=numero),
        "url_liste": url(),
        "nb_proposees": len(proposees),
        # Toutes les proposées de l'année, comme le compte du bandeau : sans les autres filtres.
        "url_proposees": f"{PREFIXE}?{urlencode({'annee': annee.id, 'etat': EtatRegle.PROPOSEE.value})}",
    }


def _rendre_liste(request: Request, session: Session, messages=()) -> Response:
    contexte = contexte_liste(request, session)
    if est_htmx(request) and request.headers.get("HX-Target") == CIBLE_LISTE:
        return rendre(request, "donnees/_regles_zone.html", contexte, messages=messages)
    return rendre(request, "donnees/regles.html", contexte, messages=messages)


# --------------------------------------------------------------------------- #
# Liste, états, propositions
# --------------------------------------------------------------------------- #


@routeur.get("", name="regles")
def liste(request: Request, session: SessionRequete) -> Response:
    """Règles d'une année scolaire."""
    return _sans_annee(request, session) or _rendre_liste(request, session)


@routeur.post("/{ident:int}/etat", name="regle_etat")
def changer_etat(
    request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
) -> Response:
    """Active, désactive ou écarte une règle (une activation est revalidée : doublons)."""
    detail = service_regles.lire_par_id(session, ident)
    retour = adresse_sure(
        formulaire.get("_retour"), f"{PREFIXE}?annee={detail.regle.annee_scolaire_id}"
    )
    etat = str(formulaire.get("etat", ""))
    if etat not in {e.value for e in EtatRegle}:
        raise ErreurDonneesInvalides(f"État inconnu : « {etat} ».")
    try:
        nouveau = service_regles.changer_etat(
            session, detail.regle.annee_scolaire_id or 0, detail.code, etat
        )
    except ErreurSaisie as refus:
        session.rollback()
        journal.info("Changement d'état refusé (%s) : %s", detail.code, refus.erreurs)
        return rediriger(request, retour, " ".join(refus.erreurs.values()), "erreur")
    verbe = {
        EtatRegle.ACTIVE: "activée",
        EtatRegle.INACTIVE: "désactivée",
        EtatRegle.PROPOSEE: "remise en proposition",
    }[nouveau.etat]
    return rediriger(request, retour, f"Règle {nouveau.code} {verbe} : {nouveau.phrase}")


@routeur.post("/confirmer", name="regles_confirmer")
def confirmer_tout(request: Request, session: SessionRequete, formulaire: Formulaire) -> Response:
    """Active toutes les règles proposées de l'année (celles qui feraient doublon restent)."""
    annee = _annee(session, formulaire.get("annee"))
    retour = adresse_sure(formulaire.get("_retour"), f"{PREFIXE}?annee={annee.id}")
    activees, refusees = 0, []
    for detail in service_regles.lister(session, annee.id, etat=EtatRegle.PROPOSEE):
        try:
            service_regles.activer(session, annee.id, detail.code)
            activees += 1
        except ErreurSaisie as refus:
            journal.info("Règle %s non confirmée : %s", detail.code, refus.erreurs)
            refusees.append(f"{detail.code} : {' '.join(refus.erreurs.values())}")
    message = f"{_texte_regles(activees, 'confirmée')} en {annee.libelle}."
    if refusees:
        message += " Restent proposées : " + " ; ".join(refusees)
    return rediriger(request, retour, message, "attention" if refusees else "succes")


@routeur.post("/proposer", name="regles_proposer")
def proposer(request: Request, session: SessionRequete, formulaire: Formulaire) -> Response:
    """Propose à l'année les règles de base et la reprise de l'année précédente."""
    annee = _annee(session, formulaire.get("annee"))
    proposition = service_regles.proposer_pour_annee(session, annee.id)
    morceaux = [f"{_texte_regles(len(proposition.creees), 'proposée')} pour {annee.libelle}"]
    if proposition.deja_presentes:
        morceaux.append(f"{proposition.deja_presentes} déjà présente(s)")
    if proposition.a_revoir:
        morceaux.append(
            f"{len(proposition.a_revoir)} à revoir (cible absente cette année) : "
            + " ; ".join(proposition.a_revoir[:3])
            + (" ..." if len(proposition.a_revoir) > 3 else "")
        )
    message = ", ".join(morceaux) + "."
    if proposition.creees:
        message += " Elles sont sans effet tant qu'elles ne sont pas confirmées."
    return rediriger(
        request,
        f"{PREFIXE}?annee={annee.id}&etat={EtatRegle.PROPOSEE.value}",
        message,
        "succes" if proposition.creees else "info",
    )


# --------------------------------------------------------------------------- #
# Ajout et modification
# --------------------------------------------------------------------------- #


def _definition(valeur: object) -> DefinitionType:
    try:
        return definition_type(str(valeur or ""))
    except ErreurDonneesInvalides as erreur:
        raise ErreurIntrouvable(erreur.message) from erreur


def _rendre_formulaire(
    request: Request,
    session: Session,
    definition: DefinitionType,
    annee: m.AnneeScolaire,
    valeurs: dict[str, Any],
    *,
    detail: service_regles.RegleDetail | None = None,
    erreurs: dict[str, str] | None = None,
    generales: list[str] | None = None,
    retour: str,
) -> Response:
    statut = 422 if erreurs or generales else 200
    messages = []
    if erreurs or generales:
        messages.append(
            Message(
                "La règle n'a pas été enregistrée : corriger les champs signalés.",
                "erreur",
                tuple(generales or ()),
            )
        )
    return rendre(
        request,
        "donnees/regle_formulaire.html",
        {
            "definition": definition,
            "annee": annee,
            "detail": detail,
            "rubriques": rubriques_regle(
                session, definition, annee, valeurs, erreurs or {}, lire_parametres(session)
            ),
            "action": f"{PREFIXE}/{detail.id}/modifier" if detail else f"{PREFIXE}/nouveau",
            "retour": retour,
            "a_corriger": bool(erreurs or generales),
            "portees": [n.libelle for n in natures_admises(definition)],
        },
        statut=statut,
        messages=messages,
    )


@routeur.get("/nouveau", name="regle_nouvelle")
def nouvelle(request: Request, session: SessionRequete) -> Response:
    """Étape 1 : choix du type ; étape 2 (avec `type`) : le formulaire de la règle."""
    sans_annee = _sans_annee(request, session)
    if sans_annee is not None:
        return sans_annee
    annee = _annee(session, request.query_params.get("annee"))
    retour = adresse_sure(request.query_params.get("retour"), f"{PREFIXE}?annee={annee.id}")
    if not request.query_params.get("type"):
        return rendre(
            request,
            "donnees/regle_types.html",
            {
                "annee": annee,
                "annees": options_reference(session, REF_ANNEE),
                "types": list(CATALOGUE_TYPES.values()),
                "natures_admises": natures_admises,
                "retour": retour,
            },
        )
    definition = _definition(request.query_params.get("type"))
    valeurs = valeurs_nouvelle(definition, lire_parametres(session))
    return _rendre_formulaire(request, session, definition, annee, valeurs, retour=retour)


@routeur.post("/nouveau", name="regle_creer")
def creer(request: Request, session: SessionRequete, formulaire: Formulaire) -> Response:
    """Crée la règle (active) après validation ; erreurs sous les champs sinon."""
    definition = _definition(formulaire.get("type"))
    annee = _annee(session, formulaire.get("annee"))
    retour = adresse_sure(formulaire.get("_retour"), f"{PREFIXE}?annee={annee.id}")
    locales = controles_formulaire(definition, formulaire)
    brouillon = brouillon_depuis_formulaire(definition, annee.id, formulaire)
    try:
        if locales:
            raise ErreurSaisie(service_regles.valider(session, brouillon).erreurs)
        detail = service_regles.creer(session, brouillon, origine=OrigineRegle.SAISIE)
    except ErreurSaisie as refus:
        session.rollback()
        reparties = erreurs_par_champ(refus.erreurs, str(formulaire.get("portee", "")), definition)
        journal.info("Création de règle refusée : %s", {**refus.erreurs, **locales})
        return _rendre_formulaire(
            request,
            session,
            definition,
            annee,
            valeurs_de_formulaire(definition, formulaire),
            erreurs={**reparties.champs, **locales},
            generales=reparties.generales,
            retour=retour,
        )
    return rediriger(request, retour, f"Règle {detail.code} créée : {detail.phrase}")


@routeur.get("/{ident:int}/modifier", name="regle_modifier")
def modifier_formulaire(request: Request, session: SessionRequete, ident: int) -> Response:
    """Formulaire prérempli d'une règle enregistrée (son type ne change pas)."""
    detail = service_regles.lire_par_id(session, ident)
    annee = _annee(session, detail.regle.annee_scolaire_id)
    retour = adresse_sure(request.query_params.get("retour"), f"{PREFIXE}?annee={annee.id}")
    return _rendre_formulaire(
        request,
        session,
        detail.regle.definition,
        annee,
        valeurs_de_regle(detail),
        detail=detail,
        retour=retour,
    )


@routeur.post("/{ident:int}/modifier", name="regle_enregistrer")
def modifier(
    request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
) -> Response:
    """Enregistre la règle modifiée (revalidée en entier)."""
    detail = service_regles.lire_par_id(session, ident)
    definition = detail.regle.definition
    annee = _annee(session, detail.regle.annee_scolaire_id)
    retour = adresse_sure(formulaire.get("_retour"), f"{PREFIXE}?annee={annee.id}")
    locales = controles_formulaire(definition, formulaire)
    changements = brouillon_depuis_formulaire(definition, annee.id, formulaire)
    try:
        if locales:
            essai = {**changements, "etat": EtatRegle.INACTIVE.value}
            raise ErreurSaisie(service_regles.valider(session, essai).erreurs)
        changements.pop("annee")
        nouveau = service_regles.modifier(session, annee.id, detail.code, changements)
    except ErreurSaisie as refus:
        session.rollback()
        reparties = erreurs_par_champ(refus.erreurs, str(formulaire.get("portee", "")), definition)
        journal.info("Modification de la règle %s refusée : %s", detail.code, refus.erreurs)
        return _rendre_formulaire(
            request,
            session,
            definition,
            annee,
            valeurs_de_formulaire(definition, formulaire),
            detail=detail,
            erreurs={**reparties.champs, **locales},
            generales=reparties.generales,
            retour=retour,
        )
    return rediriger(request, retour, f"Règle {nouveau.code} modifiée : {nouveau.phrase}")


def _avec_libelle(libelle: str, texte: str) -> str:
    """« Libellé : message », sans répéter le libellé quand le message le donne déjà."""
    return texte if texte.startswith(f"{libelle} :") else f"{libelle} : {texte}"


@routeur.post("/apercu", name="regle_apercu")
def apercu(request: Request, session: SessionRequete, formulaire: Formulaire) -> Response:
    """Aperçu (HTMX) : la phrase de la règle telle qu'elle serait enregistrée, ou ce qui manque.

    Demandé sans HTMX (adresse ouverte à la main) : renvoi vers le formulaire.
    """
    if not est_htmx(request):
        ident = entier_naturel(formulaire.get("_id"))
        if ident is not None:
            return rediriger(request, f"{PREFIXE}/{ident}/modifier")
        parametres = {cle: str(formulaire[cle]) for cle in ("annee", "type") if formulaire.get(cle)}
        return rediriger(
            request, f"{PREFIXE}/nouveau" + (f"?{urlencode(parametres)}" if parametres else "")
        )
    definition = _definition(formulaire.get("type"))
    annee = _annee(session, formulaire.get("annee"))
    brouillon = brouillon_depuis_formulaire(definition, annee.id, formulaire)
    if formulaire.get("_id"):
        # Une règle modifiée ne fait pas doublon avec elle-même : contrôle fait à l'enregistrement.
        brouillon["etat"] = EtatRegle.INACTIVE.value
    validation = service_regles.valider(session, brouillon)
    locales = controles_formulaire(definition, formulaire)
    libelles = {
        **{f"parametres.{c.nom}": c.libelle for c in definition.champs},
        "portee": "Portée",
        "portee.nature": "Portée",
        "portee.cible": "Cible",
        "niveau": "Niveau",
        "periode": "Période",
        "periode.semestre": "Semestre",
        "periode.date_debut": "Du",
        "periode.date_fin": "Au",
        "parametres": "Paramètres",
        "origine": "Origine",
        "commentaire": "Commentaire",
    }
    erreurs = [
        _avec_libelle(libelles.get(cle, cle), texte) for cle, texte in validation.erreurs.items()
    ]
    libelles_locaux = {"semestre": "Semestre", "periode": "Période"}
    erreurs += [
        _avec_libelle(libelles_locaux.get(cle, cle), texte)
        for cle, texte in locales.items()
        if texte not in validation.erreurs.values()
    ]
    return rendre(
        request,
        "donnees/_regle_apercu.html",
        {"validation": validation, "erreurs": erreurs, "definition": definition},
    )


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #


def _contexte_ligne(request: Request, session: Session, ident: int) -> dict[str, Any]:
    detail = service_regles.lire_par_id(session, ident)
    courante = adresse_courante(request)
    if courante is not None and courante.path == PREFIXE:
        url_liste = PREFIXE + (f"?{courante.query}" if courante.query else "")
    else:
        url_liste = f"{PREFIXE}?annee={detail.regle.annee_scolaire_id}"
    return {"regle": detail, "url_liste": url_liste}


@routeur.get("/{ident:int}/ligne", name="regle_ligne")
def ligne(request: Request, session: SessionRequete, ident: int) -> Response:
    """Ligne de la liste (fragment HTMX, après « Annuler ») ; sans HTMX, la fiche de la règle."""
    if not est_htmx(request):
        return rediriger(request, f"{PREFIXE}/{ident}/modifier")
    return rendre(request, "donnees/_regle_ligne.html", _contexte_ligne(request, session, ident))


@routeur.get("/{ident:int}/ligne/suppression", name="regle_ligne_suppression")
def ligne_suppression(request: Request, session: SessionRequete, ident: int) -> Response:
    """Ligne de confirmation de suppression (fragment HTMX) ; sans HTMX, la page de confirmation."""
    if not est_htmx(request):
        return rediriger(request, f"{PREFIXE}/{ident}/supprimer")
    return rendre(
        request, "donnees/_regle_ligne_suppression.html", _contexte_ligne(request, session, ident)
    )


@routeur.post("/{ident:int}/ligne/supprimer", name="regle_ligne_supprimer")
def ligne_supprimer(request: Request, session: SessionRequete, ident: int) -> Response:
    """Supprime depuis la ligne et renvoie la zone de liste à jour (HTMX)."""
    detail = service_regles.lire_par_id(session, ident)
    service_regles.supprimer(session, detail.regle.annee_scolaire_id or 0, detail.code)
    message = Message(f"Règle {detail.code} supprimée : {detail.phrase}", "succes")
    if not est_htmx(request):
        return rediriger(
            request, f"{PREFIXE}?annee={detail.regle.annee_scolaire_id}", message.texte
        )
    contexte = contexte_liste(request, session)
    return rendre(
        request, "donnees/_regles_zone_apres_suppression.html", contexte, messages=[message]
    )


@routeur.get("/{ident:int}/supprimer", name="regle_confirmer_suppression")
def confirmer_suppression(request: Request, session: SessionRequete, ident: int) -> Response:
    """Page de confirmation (sans JavaScript, ou depuis la suppression d'une cible)."""
    detail = service_regles.lire_par_id(session, ident)
    retour = adresse_sure(
        request.query_params.get("retour"), f"{PREFIXE}?annee={detail.regle.annee_scolaire_id}"
    )
    return rendre(request, "donnees/regle_suppression.html", {"regle": detail, "retour": retour})


@routeur.post("/{ident:int}/supprimer", name="regle_supprimer")
def supprimer(
    request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
) -> Response:
    """Supprime la règle et revient là d'où l'on venait."""
    detail = service_regles.lire_par_id(session, ident)
    retour = adresse_sure(
        formulaire.get("_retour"), f"{PREFIXE}?annee={detail.regle.annee_scolaire_id}"
    )
    service_regles.supprimer(session, detail.regle.annee_scolaire_id or 0, detail.code)
    return rediriger(request, retour, f"Règle {detail.code} supprimée : {detail.phrase}")
