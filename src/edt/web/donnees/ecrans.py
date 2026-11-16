"""Écrans génériques d'une entité : liste, formulaire, suppression, édition dans la liste.

Un écran se déclare en une ligne à partir de la description de l'entité :

    ECRAN_SALLES = Ecran(SALLE, "/salles", introduction="Les salles de cours...")
    routeur.include_router(routeur_ecran(ECRAN_SALLES))

et reçoit les adresses suivantes (`<p>` = préfixe, `<n>` = identifiant) :

| Méthode | Adresse                | Rôle                                              |
|---------|------------------------|---------------------------------------------------|
| GET     | `<p>`                  | liste : recherche `q`, tri `tri`/`sens`, `page`, filtres |
| GET     | `<p>/nouveau`          | formulaire d'ajout (prérempli par les paramètres d'adresse) |
| POST    | `<p>`                  | création                                          |
| GET     | `<p>/<n>`              | formulaire de modification                        |
| POST    | `<p>/<n>`              | modification                                      |
| GET     | `<p>/<n>/supprimer`    | confirmation (ce qui sera emporté, ou pourquoi c'est refusé) |
| POST    | `<p>/<n>/supprimer`    | suppression                                       |
| GET     | `<p>/<n>/ligne`        | ligne du tableau (fragment HTMX)                  |
| GET     | `<p>/<n>/ligne/edition`| ligne en édition (si `edition_en_ligne`)          |
| POST    | `<p>/<n>/ligne`        | enregistrement depuis la ligne                    |
| GET     | `<p>/<n>/ligne/suppression` | ligne de confirmation de suppression (HTMX)  |
| POST    | `<p>/<n>/ligne/supprimer`   | suppression depuis la ligne : zone de liste rafraîchie (HTMX) |

Un fragment (`/ligne...`) demandé sans HTMX (lien copié, historique, sans
JavaScript) renvoie vers la page entière qui lui correspond : la fiche, ou la
confirmation de suppression ; un enregistrement depuis la ligne revient à la
liste. Jamais un morceau de tableau nu, sans menu ni mise en page.

Les routes appellent les services génériques ; une saisie refusée réaffiche
le formulaire avec les messages sous les champs (statut 422), sans rien
enregistrer.

Retour : les liens « Modifier » et « Supprimer » d'une liste portent son
adresse (`retour`, avec recherche, filtres, tri et page) ; après un
enregistrement ou une suppression, on revient sur cette liste (à la page
précédente si la page a disparu). « Annuler » ramène là d'où l'on venait :
la liste, ou la fiche si la suppression a été demandée depuis la fiche
(`depuis=fiche`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, QueryParams

from edt.db.moteur import identifiant_valide
from edt.erreurs import ErreurDonneesInvalides, ErreurRegleMetier
from edt.services.referentiels import operations
from edt.services.referentiels.conversion import ENTIER_MAX, entier_naturel
from edt.services.referentiels.description import Entite, TypeChamp
from edt.services.referentiels.operations import ErreurSaisie, PageListe
from edt.web.dependances import SessionRequete
from edt.web.donnees.formulaires import champs_affiches, saisies_formulaire
from edt.web.gabarits import (
    Message,
    adresse_courante,
    adresse_locale,
    est_htmx,
    rediriger,
    rendre,
)

journal = logging.getLogger(__name__)

CIBLE_LISTE = "zone-liste"
"""Identifiant HTML de la zone remplacée par HTMX (tableau et pagination)."""


async def lire_formulaire(request: Request) -> FormData:
    """Formulaire envoyé (dépendance : la route elle-même reste synchrone)."""
    return await request.form()


Formulaire = Annotated[FormData, Depends(lire_formulaire)]


@dataclass(frozen=True)
class ActionLigne:
    """Bouton supplémentaire d'une ligne : POST vers `<p>/<n><suffixe>`."""

    libelle: str
    suffixe: str


@dataclass(frozen=True)
class SuiteCreation:
    """Ce qu'une création a entraîné : phrase ajoutée au message, page où aller ensuite."""

    texte: str
    adresse: str | None = None


@dataclass(frozen=True)
class Ecran:
    """Écran d'une entité.

    - `introduction` : phrase d'explication en tête de liste ;
    - `gabarit_liste` : gabarit de la page de liste (il peut étendre
      `donnees/liste.html` et remplir ses blocs `avant_liste`, `apres_liste`) ;
    - `contexte_liste` : variables supplémentaires de ce gabarit ;
    - `complement_zone` : gabarit ajouté au bas de la zone de liste, donc
      rafraîchi avec elle quand la recherche ou les filtres changent ;
    - `edition_en_ligne` : le bouton « Modifier » d'une ligne l'édite sur place ;
    - `actions_ligne` : boutons supplémentaires de chaque ligne ;
    - `liens` : liens en tête de page (libellé, adresse) ;
    - `parent` : (libellé, adresse) de la page parente, affichée en retour ;
    - `suite_creation(session, cree)` : ce qu'une création a entraîné (une
      année reçoit ses règles proposées), dit dans le message ; son adresse,
      s'il y en a une, remplace le retour à la liste.
    """

    entite: Entite
    prefixe: str
    introduction: str = ""
    gabarit_liste: str = "donnees/liste.html"
    contexte_liste: Callable[[Session, PageListe], Mapping[str, Any]] | None = None
    complement_zone: str = ""
    edition_en_ligne: bool = False
    actions_ligne: tuple[ActionLigne, ...] = ()
    liens: tuple[tuple[str, str], ...] = ()
    parent: tuple[str, str] | None = None
    titre_page: str = ""
    suite_creation: Callable[[Session, Mapping[str, Any]], SuiteCreation | None] | None = None

    @property
    def titre(self) -> str:
        """Titre de la page de liste."""
        return self.titre_page or self.entite.titre

    def url_element(self, ident: int) -> str:
        """Adresse du formulaire de modification d'un élément."""
        return f"{self.prefixe}/{ident}"

    def adresse_retour(self, brute: str | None) -> str:
        """Adresse de retour sûre : une page de cet écran (`adresse_locale`), sinon la liste."""
        if adresse_locale(brute, self.prefixe):
            return str(brute)
        if brute:
            journal.info("Adresse de retour écartée (%s) : %r", self.prefixe, str(brute)[:200])
        return self.prefixe


@dataclass(frozen=True)
class EtatListe:
    """Paramètres d'une liste (recherche, filtres, tri, page) et construction de ses adresses."""

    prefixe: str
    recherche: str = ""
    tri: str = ""
    descendant: bool = False
    page: int = 1
    filtres: dict[str, str] = field(default_factory=dict)
    tri_defaut: str = ""

    def url(self, **changements: Any) -> str:
        """Adresse de la liste avec les changements demandés (page remise à 1 sauf mention)."""
        etat = replace(self, **({"page": 1} | changements))
        parametres: dict[str, Any] = {}
        if etat.recherche:
            parametres["q"] = etat.recherche
        parametres.update(etat.filtres)
        if etat.tri and (etat.tri != etat.tri_defaut or etat.descendant):
            parametres["tri"] = etat.tri
        if etat.descendant:
            parametres["sens"] = "desc"
        if etat.page > 1:
            parametres["page"] = etat.page
        return f"{etat.prefixe}?{urlencode(parametres)}" if parametres else etat.prefixe

    def url_page(self, numero: int) -> str:
        """Adresse d'une page de la liste."""
        return self.url(page=numero)

    def url_tri(self, cle: str) -> str:
        """Adresse triée par la colonne `cle` (un second clic inverse le sens)."""
        return self.url(tri=cle, descendant=(self.tri == cle and not self.descendant))


def _parametres_de_liste(request: Request, ecran: Ecran) -> QueryParams:
    """Paramètres de la liste affichée : ceux de la requête, ou pour un fragment de
    ligne (HTMX), ceux de la liste d'où il est demandé (en-tête `HX-Current-URL`)."""
    courante = adresse_courante(request)
    if not request.query_params and courante is not None and courante.path == ecran.prefixe:
        return QueryParams(courante.query)
    return request.query_params


def _etat_demande(request: Request, ecran: Ecran) -> EtatListe:
    return _etat_depuis(_parametres_de_liste(request, ecran), ecran)


def numero_de_page(parametres: QueryParams) -> int:
    """Numéro de page lu dans une adresse (1 par défaut) ; `ErreurDonneesInvalides` s'il est illisible."""
    brute = parametres.get("page", "1").strip() or "1"
    numero = entier_naturel(brute)
    if numero is None or not 1 <= numero <= ENTIER_MAX:
        raise ErreurDonneesInvalides(f"Numéro de page invalide : « {brute[:30]} ».")
    return numero


def _etat_depuis(parametres: QueryParams, ecran: Ecran) -> EtatListe:
    """État d'une liste lu dans les paramètres d'une adresse ; page invalide refusée."""
    numero = numero_de_page(parametres)
    noms = {filtre.nom for filtre in ecran.entite.filtres}
    return EtatListe(
        prefixe=ecran.prefixe,
        recherche=parametres.get("q", "").strip(),
        tri=parametres.get("tri", "").strip(),
        descendant=parametres.get("sens") == "desc",
        page=numero,
        filtres={nom: valeur for nom, valeur in parametres.items() if nom in noms},
        tri_defaut=ecran.entite.tri_defaut,
    )


def colonnes_affichees(
    entite: Entite, filtres: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """Description des colonnes pour les gabarits de liste.

    Une colonne liée à un filtre (`Colonne.filtre`) est omise quand ce filtre a
    une valeur : elle serait la même sur toutes les lignes.
    """
    filtres = filtres or {}
    return [
        {
            "cle": c.cle,
            "libelle": c.libelle,
            "triable": c.tri is not None,
            "nombre": c.nombre,
            "format": c.format,
            "texte": c.texte,
            "apercu": c.apercu,
        }
        for c in entite.colonnes
        if not (c.filtre and filtres.get(c.filtre))
    ]


def contexte_liste(request: Request, session: Session, ecran: Ecran) -> dict[str, Any]:
    """Variables de la page de liste (aussi utilisées par les écrans sur mesure)."""
    demande = _etat_demande(request, ecran)
    page = operations.lister(
        session,
        ecran.entite,
        recherche=demande.recherche,
        tri=demande.tri or None,
        descendant=demande.descendant,
        page=demande.page,
        filtres=demande.filtres,
    )
    # Adresse sans filtre vide inutile : un filtre vide n'est gardé que s'il a une valeur
    # par défaut (« Semestre : Tous » diffère alors de l'absence du paramètre).
    etat = replace(
        demande,
        tri=page.tri,
        page=page.page,
        filtres={
            nom: valeur
            for nom, valeur in page.filtres.items()
            if valeur or ecran.entite.filtre(nom).defaut is not None
        },
    )
    prerempli = {
        filtre.champ: page.filtres[filtre.nom]
        for filtre in ecran.entite.filtres
        if filtre.champ and page.filtres.get(filtre.nom)
    }
    url_liste = etat.url(page=page.page)
    filtres = [
        (filtre, filtre.options(session), page.filtres.get(filtre.nom, ""))
        for filtre in ecran.entite.filtres
    ]
    # Un filtre « posé » est un filtre que l'utilisateur a changé : sa valeur par
    # défaut (l'année ou le semestre en cours) ne compte pas, elle est rappelée à part.
    defauts = operations.valeurs_filtres(session, ecran.entite, {})
    filtre_actif = bool(
        page.recherche
        or any(valeur and valeur != defauts[nom] for nom, valeur in page.filtres.items())
    )
    filtres_par_defaut = [
        f"{filtre.libelle} : {dict(choix).get(valeur, valeur)}"
        for filtre, choix, valeur in filtres
        if valeur and valeur == defauts[filtre.nom]
    ]
    contexte: dict[str, Any] = {
        "ecran": ecran,
        "entite": ecran.entite,
        "page": page,
        "etat": etat,
        "colonnes": colonnes_affichees(ecran.entite, page.filtres),
        "filtres": filtres,
        "url_nouveau": f"{ecran.prefixe}/nouveau"
        + (f"?{urlencode(prerempli)}" if prerempli else ""),
        "url_liste": url_liste,
        "suffixe_retour": "?" + urlencode({"retour": url_liste}),
        "filtre_actif": filtre_actif,
        "filtres_par_defaut": filtres_par_defaut,
    }
    if ecran.contexte_liste is not None:
        contexte.update(ecran.contexte_liste(session, page))
    return contexte


def _message(ecran: Ecran, nom_affiche: str, participe: str) -> str:
    entite = ecran.entite
    return f"{entite.nom_majuscule} « {nom_affiche} » {entite.accorde(participe)}."


def rendre_formulaire(
    request: Request,
    session: Session,
    ecran: Ecran,
    valeurs: Mapping[str, Any],
    *,
    ident: int | None = None,
    nom_affiche: str = "",
    erreur: ErreurDonneesInvalides | None = None,
    retour: str | None = None,
) -> Response:
    """Page du formulaire d'ajout ou de modification ; statut 422 si `erreur`."""
    entite = ecran.entite
    erreurs = erreur.champs if isinstance(erreur, ErreurSaisie) else {}
    titre = entite.nouveau if ident is None else f"{entite.nom_majuscule} « {nom_affiche} »"
    return rendre(
        request,
        "donnees/formulaire.html",
        {
            "ecran": ecran,
            "entite": entite,
            "titre": titre,
            "ident": ident,
            "rubriques": champs_affiches(session, entite, valeurs, erreurs),
            "action": ecran.prefixe if ident is None else ecran.url_element(ident),
            "libelle_bouton": "Ajouter" if ident is None else "Enregistrer",
            "retour": ecran.adresse_retour(retour),
            "erreur": erreur,
        },
        statut=422 if erreur is not None else 200,
    )


def _prerempli(request: Request, ecran: Ecran) -> dict[str, Any]:
    valeurs = operations.valeurs_par_defaut(ecran.entite)
    for champ in ecran.entite.champs:
        if champ.nom in request.query_params:
            if champ.type is TypeChamp.REFERENCES:
                valeurs[champ.nom] = request.query_params.getlist(champ.nom)
            else:
                valeurs[champ.nom] = request.query_params[champ.nom]
    return valeurs


def regles_confirmees(formulaire: FormData) -> list[int] | None:
    """Identifiants des règles personnalisées dont l'utilisateur a confirmé la suppression.

    Champ `_regles` : identifiants séparés par des virgules ; absent = aucune
    confirmation (une suppression qui toucherait des règles est alors refusée).
    Une valeur qui n'est pas un identifiant (champ fabriqué) est refusée.
    """
    brut = formulaire.get("_regles")
    if brut is None:
        return None
    identifiants = []
    for morceau in (m.strip() for m in str(brut).split(",")):
        if not morceau:
            continue
        ident = entier_naturel(morceau)
        if not identifiant_valide(ident):
            raise ErreurDonneesInvalides(
                f"Règles confirmées illisibles : « {morceau[:30]} » n'est pas un identifiant. "
                "Rien n'a été supprimé."
            )
        identifiants.append(ident)
    return identifiants


def _texte_regles(nombre: int) -> str:
    if not nombre:
        return ""
    if nombre == 1:
        return " 1 règle personnalisée supprimée ou modifiée avec."
    return f" {nombre} règles personnalisées supprimées ou modifiées avec."


def _suffixe_retour(adresse: str) -> str:
    return "?" + urlencode({"retour": adresse})


def _liste_courante(request: Request, ecran: Ecran) -> str:
    """Adresse de la liste d'où vient la demande (pour un fragment : `HX-Current-URL`)."""
    etat = _etat_demande(request, ecran)
    return etat.url(page=etat.page)


def _retour_apres_suppression(session: Session, ecran: Ecran, retour: str) -> str:
    """Adresse de retour après une suppression : la même liste, à une page qui existe encore."""
    morceaux = urlsplit(retour)
    if morceaux.path != ecran.prefixe:
        return retour
    try:
        demande = _etat_depuis(QueryParams(morceaux.query), ecran)
        if demande.page <= 1:
            return retour
        page = operations.lister(
            session,
            ecran.entite,
            recherche=demande.recherche,
            tri=demande.tri or None,
            descendant=demande.descendant,
            page=demande.page,
            filtres=demande.filtres,
        )
    except ErreurDonneesInvalides as erreur:
        # Adresse de retour fabriquée ou périmée (filtre disparu...) : retour à la liste.
        journal.info("Adresse de retour écartée (%s) : %s", retour, erreur.message)
        return ecran.prefixe
    return demande.url(page=page.page)


def _filtres_appliques(request: Request, session: Session, ecran: Ecran) -> dict[str, str]:
    return operations.valeurs_filtres(session, ecran.entite, _etat_demande(request, ecran).filtres)


def _rendre_ligne(
    request: Request, session: Session, ecran: Ecran, ident: int, *, modifiee: bool = False
) -> Response:
    return rendre(
        request,
        "donnees/_ligne.html",
        {
            "ecran": ecran,
            "ligne": operations.ligne(session, ecran.entite, ident),
            "colonnes": colonnes_affichees(
                ecran.entite, _filtres_appliques(request, session, ecran)
            ),
            "suffixe_retour": _suffixe_retour(_liste_courante(request, ecran)),
            "modifiee": modifiee,
        },
    )


def _rendre_ligne_edition(
    request: Request,
    session: Session,
    ecran: Ecran,
    ident: int,
    valeurs: Mapping[str, Any],
    erreur: ErreurDonneesInvalides | None = None,
) -> Response:
    erreurs = erreur.champs if isinstance(erreur, ErreurSaisie) else {}
    return rendre(
        request,
        "donnees/_ligne_edition.html",
        {
            "ecran": ecran,
            "ident": ident,
            "rubriques": champs_affiches(session, ecran.entite, valeurs, erreurs),
            "nb_colonnes": _nb_colonnes(request, session, ecran),
            "erreur": erreur if erreur is not None and not erreurs else None,
        },
    )


def _nb_colonnes(request: Request, session: Session, ecran: Ecran) -> int:
    """Colonnes affichées par la liste courante, colonne d'actions comprise."""
    filtres = _filtres_appliques(request, session, ecran)
    return len(colonnes_affichees(ecran.entite, filtres)) + 1


def _rendre_ligne_suppression(
    request: Request, session: Session, ecran: Ecran, ident: int
) -> Response:
    retour = _liste_courante(request, ecran)
    return rendre(
        request,
        "donnees/_ligne_suppression.html",
        {
            "ecran": ecran,
            "entite": ecran.entite,
            "analyse": operations.analyser_suppression(session, ecran.entite, ident),
            "url_element": ecran.url_element(ident),
            "url_page": f"{ecran.url_element(ident)}/supprimer{_suffixe_retour(retour)}",
            "retour_regles": f"{ecran.url_element(ident)}/supprimer{_suffixe_retour(retour)}",
            "nb_colonnes": _nb_colonnes(request, session, ecran),
        },
    )


def _contexte_confirmation(
    request: Request, session: Session, ecran: Ecran, ident: int
) -> dict[str, Any]:
    retour = ecran.adresse_retour(request.query_params.get("retour"))
    depuis_fiche = request.query_params.get("depuis") == "fiche"
    url_fiche = ecran.url_element(ident) + _suffixe_retour(retour)
    page = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return {
        "ecran": ecran,
        "entite": ecran.entite,
        "analyse": operations.analyser_suppression(session, ecran.entite, ident),
        "retour_regles": page,
        "retour": retour,
        "depuis_fiche": depuis_fiche,
        "url_fiche": url_fiche,
        "annuler": url_fiche if depuis_fiche else retour,
    }


def routeur_ecran(ecran: Ecran) -> APIRouter:
    """Routes génériques de l'écran (voir le tableau en tête du module)."""
    routeur = APIRouter(prefix=ecran.prefixe)
    entite = ecran.entite

    @routeur.get("", name=f"{entite.code}_liste")
    def liste(request: Request, session: SessionRequete) -> Response:
        contexte = contexte_liste(request, session, ecran)
        if est_htmx(request) and request.headers.get("HX-Target") == CIBLE_LISTE:
            return rendre(request, "donnees/_zone_liste.html", contexte)
        return rendre(request, ecran.gabarit_liste, contexte)

    @routeur.get("/nouveau", name=f"{entite.code}_nouveau")
    def nouveau(request: Request, session: SessionRequete) -> Response:
        return rendre_formulaire(
            request,
            session,
            ecran,
            _prerempli(request, ecran),
            retour=request.query_params.get("retour"),
        )

    @routeur.post("", name=f"{entite.code}_creer")
    def creer(request: Request, session: SessionRequete, formulaire: Formulaire) -> Response:
        saisies = saisies_formulaire(entite, formulaire)
        retour = ecran.adresse_retour(formulaire.get("_retour"))
        try:
            cree = operations.creer(session, entite, saisies)
        except ErreurDonneesInvalides as erreur:
            session.rollback()
            return rendre_formulaire(request, session, ecran, saisies, erreur=erreur, retour=retour)
        message = _message(ecran, cree["nom_affiche"], "créé")
        suite = ecran.suite_creation(session, cree) if ecran.suite_creation else None
        if suite is not None:
            message = f"{message} {suite.texte}"
            retour = suite.adresse or retour
        return rediriger(request, retour, message)

    @routeur.get("/{ident:int}", name=f"{entite.code}_modifier")
    def element(request: Request, session: SessionRequete, ident: int) -> Response:
        valeurs = operations.lire(session, entite, ident)
        return rendre_formulaire(
            request,
            session,
            ecran,
            valeurs,
            ident=ident,
            nom_affiche=valeurs["nom_affiche"],
            retour=request.query_params.get("retour"),
        )

    @routeur.post("/{ident:int}", name=f"{entite.code}_enregistrer")
    def enregistrer(
        request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
    ) -> Response:
        saisies = saisies_formulaire(entite, formulaire)
        retour = ecran.adresse_retour(formulaire.get("_retour"))
        try:
            modifie = operations.modifier(session, entite, ident, saisies)
        except ErreurDonneesInvalides as erreur:
            session.rollback()
            nom = operations.lire(session, entite, ident)["nom_affiche"]
            return rendre_formulaire(
                request,
                session,
                ecran,
                saisies,
                ident=ident,
                nom_affiche=nom,
                erreur=erreur,
                retour=retour,
            )
        return rediriger(request, retour, _message(ecran, modifie["nom_affiche"], "modifié"))

    @routeur.get("/{ident:int}/supprimer", name=f"{entite.code}_confirmer_suppression")
    def confirmer_suppression(request: Request, session: SessionRequete, ident: int) -> Response:
        return rendre(
            request,
            "donnees/suppression.html",
            _contexte_confirmation(request, session, ecran, ident),
        )

    @routeur.post("/{ident:int}/supprimer", name=f"{entite.code}_supprimer")
    def supprimer(
        request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
    ) -> Response:
        retour = ecran.adresse_retour(formulaire.get("_retour"))
        confirmees = regles_confirmees(formulaire)
        try:
            nom = operations.supprimer(session, entite, ident, regles_confirmees=confirmees)
        except ErreurRegleMetier as refus:
            parametres = {"retour": retour}
            if formulaire.get("_depuis") == "fiche":
                parametres["depuis"] = "fiche"
            return rediriger(
                request,
                f"{ecran.url_element(ident)}/supprimer?{urlencode(parametres)}",
                refus.message,
                "erreur",
            )
        return rediriger(
            request,
            _retour_apres_suppression(session, ecran, retour),
            _message(ecran, nom, "supprimé") + _texte_regles(len(confirmees or ())),
        )

    @routeur.get("/{ident:int}/ligne/suppression", name=f"{entite.code}_ligne_suppression")
    def ligne_suppression(request: Request, session: SessionRequete, ident: int) -> Response:
        if not est_htmx(request):
            return rediriger(request, f"{ecran.url_element(ident)}/supprimer")
        return _rendre_ligne_suppression(request, session, ecran, ident)

    @routeur.post("/{ident:int}/ligne/supprimer", name=f"{entite.code}_ligne_supprimer")
    def ligne_supprimer(
        request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
    ) -> Response:
        """Supprime depuis la ligne et renvoie la zone de liste à jour, avec le message."""
        confirmees = regles_confirmees(formulaire)
        try:
            nom = operations.supprimer(session, entite, ident, regles_confirmees=confirmees)
            texte = _message(ecran, nom, "supprimé") + _texte_regles(len(confirmees or ()))
            message = Message(texte, "succes")
        except ErreurRegleMetier as refus:
            message = Message(refus.message, "erreur")
        if not est_htmx(request):
            retour = _retour_apres_suppression(session, ecran, _liste_courante(request, ecran))
            return rediriger(request, retour, message.texte, message.niveau)
        demande = _etat_demande(request, ecran)
        contexte = contexte_liste(request, session, ecran)
        reponse = rendre(
            request, "donnees/_zone_apres_suppression.html", contexte, messages=[message]
        )
        if contexte["etat"].page != demande.page:
            # La page affichée a disparu : l'adresse suit la page montrée.
            reponse.headers["HX-Replace-Url"] = contexte["url_liste"]
        return reponse

    @routeur.get("/{ident:int}/ligne", name=f"{entite.code}_ligne")
    def ligne(request: Request, session: SessionRequete, ident: int) -> Response:
        if not est_htmx(request):
            return rediriger(request, ecran.url_element(ident))
        return _rendre_ligne(request, session, ecran, ident)

    if ecran.edition_en_ligne:

        @routeur.get("/{ident:int}/ligne/edition", name=f"{entite.code}_ligne_edition")
        def ligne_edition(request: Request, session: SessionRequete, ident: int) -> Response:
            if not est_htmx(request):
                return rediriger(request, ecran.url_element(ident))
            valeurs = operations.lire(session, entite, ident)
            return _rendre_ligne_edition(request, session, ecran, ident, valeurs)

        @routeur.post("/{ident:int}/ligne", name=f"{entite.code}_ligne_enregistrer")
        def ligne_enregistrer(
            request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
        ) -> Response:
            saisies = saisies_formulaire(entite, formulaire)
            try:
                modifie = operations.modifier(session, entite, ident, saisies)
            except ErreurDonneesInvalides as erreur:
                session.rollback()
                if not est_htmx(request):  # sans JavaScript : le formulaire complet
                    nom = operations.lire(session, entite, ident)["nom_affiche"]
                    return rendre_formulaire(
                        request,
                        session,
                        ecran,
                        saisies,
                        ident=ident,
                        nom_affiche=nom,
                        erreur=erreur,
                    )
                return _rendre_ligne_edition(request, session, ecran, ident, saisies, erreur)
            if not est_htmx(request):
                return rediriger(
                    request, ecran.prefixe, _message(ecran, modifie["nom_affiche"], "modifié")
                )
            return _rendre_ligne(request, session, ecran, ident, modifiee=True)

    return routeur
