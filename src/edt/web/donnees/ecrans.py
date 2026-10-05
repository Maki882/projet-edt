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

Les routes appellent les services génériques ; une saisie refusée réaffiche
le formulaire avec les messages sous les champs (statut 422), sans rien
enregistrer.
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

from edt.erreurs import ErreurDonneesInvalides, ErreurRegleMetier
from edt.services.referentiels import operations
from edt.services.referentiels.description import Entite, TypeChamp
from edt.services.referentiels.operations import ErreurSaisie, PageListe
from edt.web.dependances import SessionRequete
from edt.web.donnees.formulaires import champs_affiches, saisies_formulaire
from edt.web.gabarits import est_htmx, rediriger, rendre

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
    - `parent` : (libellé, adresse) de la page parente, affichée en retour.
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

    @property
    def titre(self) -> str:
        """Titre de la page de liste."""
        return self.titre_page or self.entite.titre

    def url_element(self, ident: int) -> str:
        """Adresse du formulaire de modification d'un élément."""
        return f"{self.prefixe}/{ident}"

    def adresse_retour(self, brute: str | None) -> str:
        """Adresse de retour sûre : une page de cet écran, sinon la liste."""
        if brute and brute.startswith(self.prefixe) and "//" not in brute and "\\" not in brute:
            return brute
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

    def url(self, **changements: Any) -> str:
        """Adresse de la liste avec les changements demandés (page remise à 1 sauf mention)."""
        etat = replace(self, **({"page": 1} | changements))
        parametres: dict[str, Any] = {}
        if etat.recherche:
            parametres["q"] = etat.recherche
        parametres.update(etat.filtres)
        if etat.tri:
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
    courante = urlsplit(request.headers.get("HX-Current-URL", ""))
    if not request.query_params and courante.path == ecran.prefixe:
        return QueryParams(courante.query)
    return request.query_params


def _etat_demande(request: Request, ecran: Ecran) -> EtatListe:
    parametres = _parametres_de_liste(request, ecran)
    brute = parametres.get("page", "1").strip() or "1"
    if not brute.isdigit() or int(brute) < 1:
        raise ErreurDonneesInvalides(f"Numéro de page invalide : « {brute} ».")
    noms = {filtre.nom for filtre in ecran.entite.filtres}
    return EtatListe(
        prefixe=ecran.prefixe,
        recherche=parametres.get("q", "").strip(),
        tri=parametres.get("tri", "").strip(),
        descendant=parametres.get("sens") == "desc",
        page=int(brute),
        filtres={nom: valeur for nom, valeur in parametres.items() if nom in noms},
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
    etat = replace(demande, tri=page.tri, page=page.page, filtres=dict(page.filtres))
    prerempli = {
        filtre.champ: page.filtres[filtre.nom]
        for filtre in ecran.entite.filtres
        if filtre.champ and page.filtres.get(filtre.nom)
    }
    url_liste = etat.url(page=page.page)
    contexte: dict[str, Any] = {
        "ecran": ecran,
        "entite": ecran.entite,
        "page": page,
        "etat": etat,
        "colonnes": colonnes_affichees(ecran.entite, page.filtres),
        "filtres": [
            (filtre, filtre.options(session), page.filtres.get(filtre.nom, ""))
            for filtre in ecran.entite.filtres
        ],
        "url_nouveau": f"{ecran.prefixe}/nouveau"
        + (f"?{urlencode(prerempli)}" if prerempli else ""),
        "url_liste": url_liste,
        "suffixe_retour": "?" + urlencode({"retour": url_liste}),
        "filtre_actif": bool(page.recherche or any(page.filtres.values())),
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
            "suffixe_retour": "",
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
            "nb_colonnes": len(
                colonnes_affichees(ecran.entite, _filtres_appliques(request, session, ecran))
            )
            + 1,
            "erreur": erreur if erreur is not None and not erreurs else None,
        },
    )


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
        return rediriger(request, retour, _message(ecran, cree["nom_affiche"], "créé"))

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
            {
                "ecran": ecran,
                "entite": entite,
                "analyse": operations.analyser_suppression(session, entite, ident),
            },
        )

    @routeur.post("/{ident:int}/supprimer", name=f"{entite.code}_supprimer")
    def supprimer(request: Request, session: SessionRequete, ident: int) -> Response:
        try:
            nom = operations.supprimer(session, entite, ident)
        except ErreurRegleMetier as refus:
            return rediriger(
                request, f"{ecran.url_element(ident)}/supprimer", refus.message, "erreur"
            )
        return rediriger(request, ecran.prefixe, _message(ecran, nom, "supprimé"))

    @routeur.get("/{ident:int}/ligne", name=f"{entite.code}_ligne")
    def ligne(request: Request, session: SessionRequete, ident: int) -> Response:
        return _rendre_ligne(request, session, ecran, ident)

    if ecran.edition_en_ligne:

        @routeur.get("/{ident:int}/ligne/edition", name=f"{entite.code}_ligne_edition")
        def ligne_edition(request: Request, session: SessionRequete, ident: int) -> Response:
            valeurs = operations.lire(session, entite, ident)
            return _rendre_ligne_edition(request, session, ecran, ident, valeurs)

        @routeur.post("/{ident:int}/ligne", name=f"{entite.code}_ligne_enregistrer")
        def ligne_enregistrer(
            request: Request, session: SessionRequete, formulaire: Formulaire, ident: int
        ) -> Response:
            saisies = saisies_formulaire(entite, formulaire)
            try:
                operations.modifier(session, entite, ident, saisies)
            except ErreurDonneesInvalides as erreur:
                session.rollback()
                return _rendre_ligne_edition(request, session, ecran, ident, saisies, erreur)
            return _rendre_ligne(request, session, ecran, ident, modifiee=True)

    return routeur
