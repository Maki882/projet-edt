"""Opérations génériques sur les entités décrites : lister, lire, créer, modifier, supprimer.

Toutes les fonctions prennent la session de l'appelant et le **code** de
l'entité (« salle », « besoin »... voir `edt.services.referentiels.catalogue`)
ou sa description `Entite`. Elles ne valident jamais la transaction
(`session.flush()` seulement) et renvoient des valeurs sérialisables en JSON :
une interface web, une API ou un assistant en langage naturel les appellent
telles quelles.

    page = lister(session, "salle", recherche="N2", tri="capacite", page=1)
    salle = creer(session, "salle", {"code": "N210", "campus_id": 1, ...})
    modifier(session, "salle", salle["id"], {"capacite": 48}, partiel=True)
    supprimer(session, "salle", salle["id"])        # ErreurRegleMetier si utilisée

Erreurs levées :

- `ErreurSaisie` (sous-classe d'`ErreurDonneesInvalides`) : valeurs refusées,
  avec le message de chaque champ fautif dans `champs` ;
- `ErreurIntrouvable` : identifiant inconnu ;
- `ErreurRegleMetier` : suppression d'un élément encore utilisé (avant
  d'atteindre la base, avec le décompte précis).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import String, func, or_, select
from sqlalchemy.orm import Session

from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.referentiels.conversion import ENTIER_MAX, ErreurChamp, canonique, convertir
from edt.services.referentiels.description import (
    Champ,
    Entite,
    TypeChamp,
    options_reference,
)

journal = logging.getLogger(__name__)

TAILLE_PAGE = 50
"""Nombre de lignes par page par défaut."""
TAILLE_PAGE_MAX = 500


class ErreurSaisie(ErreurDonneesInvalides):
    """Valeurs refusées : `champs` associe à chaque champ fautif son message."""

    def __init__(self, champs: Mapping[str, str], libelles: Mapping[str, str] | None = None):
        self.champs = dict(champs)
        libelles = libelles or {}
        super().__init__(
            "Certaines valeurs sont à corriger.",
            [f"{libelles.get(nom, nom)} : {message}" for nom, message in self.champs.items()],
        )


@dataclass(frozen=True)
class PageListe:
    """Une page de liste, prête à afficher ou à sérialiser.

    `lignes` : un dictionnaire par élément, avec `id` et une clé par colonne
    de l'entité (une liste pour une colonne à plusieurs valeurs), plus
    `detail_<colonne>` pour une colonne abrégée ; `filtres` : valeur appliquée pour chaque filtre (vide = tous).
    """

    entite: str
    lignes: tuple[dict[str, Any], ...]
    total: int
    page: int
    taille: int
    nb_pages: int
    tri: str
    descendant: bool
    recherche: str
    filtres: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AnalyseSuppression:
    """Ce que donnerait la suppression d'un élément.

    `refus` : raisons qui l'empêchent (vide = possible) ; `consequences` : ce
    qu'elle emporterait ou modifierait ailleurs.
    """

    entite: str
    id: int
    libelle: str
    refus: tuple[str, ...]
    consequences: tuple[str, ...]

    @property
    def possible(self) -> bool:
        """Vrai si rien n'empêche la suppression."""
        return not self.refus


# --------------------------------------------------------------------------- #
# Résolution et description
# --------------------------------------------------------------------------- #


def entite(code: str | Entite) -> Entite:
    """Description de l'entité `code` ; lève `ErreurIntrouvable` si elle n'existe pas."""
    if isinstance(code, Entite):
        return code
    from edt.services.referentiels.catalogue import ENTITES

    try:
        return ENTITES[code]
    except KeyError:
        raise ErreurIntrouvable(
            f"Entité inconnue : « {code} » (connues : {', '.join(sorted(ENTITES))})."
        ) from None


def longueur_max(description: Entite, champ: Champ) -> int | None:
    """Longueur maximale d'un champ texte : celle déclarée, sinon celle de la colonne."""
    if champ.longueur_max is not None or champ.virtuel:
        return champ.longueur_max
    colonne = description.modele.__table__.columns.get(champ.nom)
    if colonne is not None and isinstance(colonne.type, String):
        return colonne.type.length
    return None


def decrire(code: str | Entite) -> dict[str, Any]:
    """Description sérialisable de l'entité : champs, colonnes, tris, filtres."""
    description = entite(code)
    return {
        "code": description.code,
        "nom": description.nom,
        "pluriel": description.pluriel,
        "champs": [
            {
                "nom": champ.nom,
                "libelle": champ.libelle,
                "type": champ.type.value,
                "obligatoire": champ.obligatoire,
                "aide": champ.aide,
                "defaut": canonique(champ, champ.defaut),
                "minimum": champ.minimum,
                "maximum": champ.maximum,
                "longueur_max": longueur_max(description, champ),
                "choix": [[cle, libelle] for cle, libelle in champ.choix],
                "reference": champ.reference.modele.__tablename__ if champ.reference else None,
                "visible_si": list(champ.visible_si) if champ.visible_si else None,
            }
            for champ in description.champs
        ],
        "colonnes": [
            {"cle": colonne.cle, "libelle": colonne.libelle, "triable": colonne.tri is not None}
            for colonne in description.colonnes
        ],
        "tri_defaut": description.tri_defaut,
        "filtres": [{"nom": f.nom, "libelle": f.libelle} for f in description.filtres],
    }


def options(session: Session, code: str | Entite, nom_champ: str) -> list[tuple[str, str]]:
    """Choix possibles d'un champ (choix fixes ou éléments référencés) : (valeur, libellé)."""
    champ = entite(code).champ(nom_champ)
    if champ.reference is not None:
        return options_reference(session, champ.reference)
    return [(str(cle), libelle) for cle, libelle in champ.choix]


def options_filtre(session: Session, code: str | Entite, nom_filtre: str) -> list[tuple[str, str]]:
    """Choix d'un filtre de liste : (valeur, libellé)."""
    description = entite(code)
    filtre = description.filtre(nom_filtre)
    if filtre is None:
        raise ErreurIntrouvable(
            f"Filtre inconnu pour les {description.pluriel} : « {nom_filtre} »."
        )
    return filtre.options(session)


def valeurs_par_defaut(code: str | Entite) -> dict[str, Any]:
    """Valeurs d'un élément neuf (canoniques)."""
    description = entite(code)
    return {champ.nom: canonique(champ, champ.defaut) for champ in description.champs}


# --------------------------------------------------------------------------- #
# Liste
# --------------------------------------------------------------------------- #


def _echapper_motif(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def valeurs_filtres(
    session: Session, code: str | Entite, filtres: Mapping[str, str] | None
) -> dict[str, str]:
    """Valeur de chaque filtre : celle demandée, sinon la valeur par défaut du filtre."""
    description = entite(code)
    demandes = dict(filtres or {})
    inconnus = sorted(set(demandes) - {f.nom for f in description.filtres})
    if inconnus:
        raise ErreurDonneesInvalides(
            f"Filtre inconnu pour les {description.pluriel} : {', '.join(inconnus)}."
        )
    resultat: dict[str, str] = {}
    for filtre in description.filtres:
        if filtre.nom in demandes:
            resultat[filtre.nom] = (demandes[filtre.nom] or "").strip()
        elif filtre.defaut is not None:
            resultat[filtre.nom] = filtre.defaut(session) or ""
        else:
            resultat[filtre.nom] = ""
    return resultat


def lister(
    session: Session,
    code: str | Entite,
    *,
    recherche: str = "",
    tri: str | None = None,
    descendant: bool = False,
    page: int = 1,
    taille: int = TAILLE_PAGE,
    filtres: Mapping[str, str] | None = None,
) -> PageListe:
    """Une page de la liste : recherche plein texte, filtres, tri, pagination.

    - `recherche` : texte cherché (sans casse) dans les colonnes de recherche ;
    - `tri` : clé d'une colonne triable (défaut : `tri_defaut` de l'entité) ;
    - `filtres` : valeur par nom de filtre ; un filtre absent prend sa valeur
      par défaut, une valeur vide le désactive ;
    - `page` : numéro de page dès 1 (ramené à la dernière page s'il la dépasse).
    """
    description = entite(code)
    if not 1 <= taille <= TAILLE_PAGE_MAX:
        raise ErreurDonneesInvalides(f"Taille de page invalide : {taille} (1 à {TAILLE_PAGE_MAX}).")
    if page < 1:
        raise ErreurDonneesInvalides(f"Numéro de page invalide : {page}.")
    cle_tri = tri or description.tri_defaut
    colonne = description.colonne(cle_tri)
    if colonne is None or colonne.tri is None:
        triables = [c.cle for c in description.colonnes if c.tri is not None]
        raise ErreurDonneesInvalides(
            f"Tri impossible sur « {cle_tri} » (colonnes triables : {', '.join(triables)})."
        )

    requete, texte, appliques = _requete_filtree(session, description, recherche, filtres)
    modele = description.modele

    total = session.scalar(select(func.count()).select_from(requete.order_by(None).subquery()))
    total = total or 0
    nb_pages = max(1, math.ceil(total / taille))
    page = min(page, nb_pages)

    expressions = colonne.tri if isinstance(colonne.tri, tuple) else (colonne.tri,)
    ordre = [e.desc() if descendant else e.asc() for e in expressions] + [modele.id.asc()]
    objets = session.scalars(
        requete.options(*description.chargements)
        .order_by(*ordre)
        .limit(taille)
        .offset((page - 1) * taille)
    ).all()
    return PageListe(
        entite=description.code,
        lignes=tuple(_ligne(description, objet) for objet in objets),
        total=total,
        page=page,
        taille=taille,
        nb_pages=nb_pages,
        tri=cle_tri,
        descendant=descendant,
        recherche=texte,
        filtres=appliques,
    )


def identifiants(
    session: Session,
    code: str | Entite,
    *,
    recherche: str = "",
    filtres: Mapping[str, str] | None = None,
) -> list[int]:
    """Identifiants de tous les éléments retenus par la recherche et les filtres (toutes pages)."""
    description = entite(code)
    requete, _, _ = _requete_filtree(session, description, recherche, filtres)
    return list(session.scalars(requete.with_only_columns(description.modele.id).order_by(None)))


def _requete_filtree(
    session: Session,
    description: Entite,
    recherche: str,
    filtres: Mapping[str, str] | None,
) -> tuple[Any, str, dict[str, str]]:
    """Requête des éléments retenus, texte cherché nettoyé, valeur appliquée de chaque filtre."""
    requete = select(description.modele)
    for cible, condition in description.jointures:
        requete = requete.outerjoin(cible, condition)

    conditions: list[Any] = []
    texte = recherche.strip()
    if texte and not description.cherchable:
        raise ErreurDonneesInvalides(
            f"La recherche n'est pas proposée pour les {description.pluriel}."
        )
    if texte:
        motif = f"%{_echapper_motif(texte)}%"
        alternatives = [col.ilike(motif, escape="\\") for col in description.recherche]
        alternatives += [fonction(motif) for fonction in description.recherche_avancee]
        conditions.append(or_(*alternatives))
    appliques = valeurs_filtres(session, description, filtres)
    for filtre in description.filtres:
        valeur = appliques[filtre.nom]
        if valeur:
            # Valeur venue d'une adresse : elle doit être l'un des choix du filtre.
            if valeur not in {cle for cle, _ in filtre.options(session)}:
                raise ErreurDonneesInvalides(
                    f"Filtre « {filtre.libelle} » : valeur inconnue « {valeur} »."
                )
            conditions.append(filtre.condition(valeur))
    return requete.where(*conditions), texte, appliques


def _ligne(description: Entite, trouve: Any) -> dict[str, Any]:
    """Ligne de liste : `id`, une clé par colonne et, pour une colonne qui a un
    `detail`, la clé `detail_<colonne>` (texte complet affiché au survol)."""
    ligne: dict[str, Any] = {"id": trouve.id}
    for colonne in description.colonnes:
        ligne[colonne.cle] = colonne.valeur(trouve)
        if colonne.detail is not None:
            ligne[f"detail_{colonne.cle}"] = colonne.detail(trouve)
    return ligne


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #


def identifiant_possible(ident: object) -> bool:
    """Vrai si `ident` peut être un identifiant en base (entier positif représentable).

    Une adresse comme « /salles/99999999999999999999 » donne alors « introuvable »
    au lieu d'une erreur de la base.
    """
    return isinstance(ident, int) and not isinstance(ident, bool) and 0 < ident <= ENTIER_MAX


def objet(session: Session, code: str | Entite, ident: int) -> Any:
    """L'objet ORM `ident` ; lève `ErreurIntrouvable` s'il n'existe pas."""
    description = entite(code)
    trouve = session.get(description.modele, ident) if identifiant_possible(ident) else None
    if trouve is None:
        raise ErreurIntrouvable(
            f"{description.nom_majuscule} n° {ident} introuvable : "
            f"{'elle' if description.feminin else 'il'} a peut-être été "
            f"{description.accorde('supprimé')}."
        )
    return trouve


def _valeurs_objet(session: Session, description: Entite, trouve: Any) -> dict[str, Any]:
    valeurs: dict[str, Any] = {}
    for champ in description.champs:
        brute = champ.lire(session, trouve) if champ.lire else getattr(trouve, champ.nom)
        valeurs[champ.nom] = canonique(champ, brute)
    return valeurs


def lire(session: Session, code: str | Entite, ident: int) -> dict[str, Any]:
    """Valeurs canoniques d'un élément, avec `id` et `nom_affiche` (son libellé lisible)."""
    description = entite(code)
    trouve = objet(session, description, ident)
    return {
        "id": trouve.id,
        "nom_affiche": description.libelle(trouve),
        **_valeurs_objet(session, description, trouve),
    }


def ligne(session: Session, code: str | Entite, ident: int) -> dict[str, Any]:
    """Une ligne de liste (même format que `PageListe.lignes`) pour un seul élément."""
    description = entite(code)
    return _ligne(description, objet(session, description, ident))


# --------------------------------------------------------------------------- #
# Écriture
# --------------------------------------------------------------------------- #


def _valider(
    session: Session, description: Entite, saisies: Mapping[str, Any], actuel: Any | None
) -> dict[str, Any]:
    """Convertit et contrôle toutes les valeurs ; lève `ErreurSaisie` au moindre refus."""
    erreurs: dict[str, str] = {}
    convertis: dict[str, Any] = {}
    for champ in description.champs:
        try:
            convertis[champ.nom] = convertir(
                session, champ, saisies.get(champ.nom), longueur_max(description, champ)
            )
        except ErreurChamp as erreur:
            erreurs[champ.nom] = erreur.message
    if description.valider is not None:
        for nom, message in description.valider(session, convertis, actuel).items():
            erreurs.setdefault(nom, message)
    if not erreurs:
        for unicite in description.uniques:
            erreur = _controler_unicite(session, description, unicite.champs, convertis, actuel)
            if erreur:
                erreurs.setdefault(unicite.champ_erreur, unicite.message)
    if erreurs:
        libelles = {champ.nom: champ.libelle for champ in description.champs}
        journal.info(
            "Saisie refusée (%s) : %s",
            description.nom,
            "; ".join(f"{nom} : {message}" for nom, message in erreurs.items()),
        )
        raise ErreurSaisie(erreurs, libelles)
    return convertis


def _controler_unicite(
    session: Session,
    description: Entite,
    champs: tuple[str, ...],
    valeurs: Mapping[str, Any],
    actuel: Any | None,
) -> bool:
    """Vrai si un autre élément porte déjà la même combinaison de valeurs."""
    modele = description.modele
    conditions = []
    for nom in champs:
        colonne = getattr(modele, nom)
        valeur = valeurs.get(nom)
        conditions.append(colonne.is_(None) if valeur is None else colonne == valeur)
    if actuel is not None:
        conditions.append(modele.id != actuel.id)
    with session.no_autoflush:
        return session.scalar(select(modele.id).where(*conditions).limit(1)) is not None


def _ecrire(session: Session, description: Entite, cible: Any, donnees: Mapping[str, Any]) -> None:
    for nom, valeur in donnees.items():
        champ = description._index.get(nom)
        if champ is not None and champ.virtuel:
            continue
        if champ is not None and champ.type is TypeChamp.REFERENCES:
            assert champ.reference is not None
            modele = champ.reference.modele
            objets = (
                {o.id: o for o in session.scalars(select(modele).where(modele.id.in_(valeur)))}
                if valeur
                else {}
            )
            setattr(cible, nom, [objets[i] for i in valeur])
        else:
            setattr(cible, nom, valeur)


def _verifier_cles(description: Entite, valeurs: Mapping[str, Any]) -> None:
    inconnus = sorted(nom for nom in valeurs if not description.a_champ(nom))
    if inconnus:
        raise ErreurDonneesInvalides(
            f"Champ(s) inconnu(s) pour {description.nom} : {', '.join(inconnus)}."
        )


def _enregistrer(
    session: Session, description: Entite, cible: Any, saisies: Mapping[str, Any], actuel: Any
) -> None:
    with session.no_autoflush:
        convertis = _valider(session, description, saisies, actuel)
        donnees = (
            description.preparer(session, dict(convertis))
            if description.preparer
            else dict(convertis)
        )
        _ecrire(session, description, cible, donnees)
    if actuel is None:
        session.add(cible)
    session.flush()
    if description.apres_ecriture is not None:
        description.apres_ecriture(session, cible, convertis)
        session.flush()
    session.expire(cible)


def creer(session: Session, code: str | Entite, valeurs: Mapping[str, Any]) -> dict[str, Any]:
    """Crée un élément ; les champs absents prennent leur valeur par défaut.

    Renvoie les valeurs de l'élément créé (comme `lire`). Lève `ErreurSaisie`
    si une valeur est refusée : rien n'est alors écrit.
    """
    description = entite(code)
    _verifier_cles(description, valeurs)
    saisies = {**valeurs_par_defaut(description), **valeurs}
    nouveau = description.modele()
    _enregistrer(session, description, nouveau, saisies, None)
    resultat = lire(session, description, nouveau.id)
    journal.info(
        "%s %s : %s (n° %d)",
        description.nom_majuscule,
        description.accorde("créé"),
        resultat["nom_affiche"],
        nouveau.id,
    )
    return resultat


def modifier(
    session: Session,
    code: str | Entite,
    ident: int,
    valeurs: Mapping[str, Any],
    *,
    partiel: bool = False,
) -> dict[str, Any]:
    """Modifie un élément et renvoie ses nouvelles valeurs (comme `lire`).

    `partiel=True` : seuls les champs fournis changent (usage API) ; sinon les
    champs absents reprennent leur valeur par défaut (usage formulaire, où une
    case décochée est absente).
    """
    description = entite(code)
    _verifier_cles(description, valeurs)
    actuel = objet(session, description, ident)
    base = (
        _valeurs_objet(session, description, actuel) if partiel else valeurs_par_defaut(description)
    )
    _enregistrer(session, description, actuel, {**base, **valeurs}, actuel)
    resultat = lire(session, description, ident)
    journal.info(
        "%s %s : %s (n° %d)",
        description.nom_majuscule,
        description.accorde("modifié"),
        resultat["nom_affiche"],
        ident,
    )
    return resultat


# --------------------------------------------------------------------------- #
# Suppression
# --------------------------------------------------------------------------- #


def analyser_suppression(session: Session, code: str | Entite, ident: int) -> AnalyseSuppression:
    """Dit si l'élément peut être supprimé, et ce que la suppression emporterait."""
    description = entite(code)
    trouve = objet(session, description, ident)
    refus = [
        f"{description.demonstratif} {usage.texte(nombre)}."
        for usage in description.usages
        if (nombre := usage.compter(session, ident))
    ]
    consequences = [
        f"{effet.texte(nombre)}."
        for effet in description.consequences
        if (nombre := effet.compter(session, ident))
    ]
    if description.analyser is not None:
        refus_calcules, consequences_calculees = description.analyser(session, trouve)
        refus += refus_calcules
        consequences += consequences_calculees
    return AnalyseSuppression(
        entite=description.code,
        id=ident,
        libelle=description.libelle(trouve),
        refus=tuple(refus),
        consequences=tuple(consequences),
    )


def supprimer(session: Session, code: str | Entite, ident: int) -> str:
    """Supprime un élément et renvoie son libellé.

    Lève `ErreurRegleMetier` **avant toute écriture** si l'élément est encore
    utilisé (« Cette salle est imposée à 12 besoins. »).
    """
    description = entite(code)
    analyse = analyser_suppression(session, description, ident)
    if not analyse.possible:
        journal.warning(
            "Suppression refusée (%s « %s ») : %s",
            description.nom,
            analyse.libelle,
            " ".join(analyse.refus),
        )
        raise ErreurRegleMetier(
            " ".join(analyse.refus)
            + " Modifiez ou supprimez d'abord ces éléments : rien n'a été supprimé.",
            analyse.refus,
        )
    session.delete(objet(session, description, ident))
    session.flush()
    journal.info(
        "%s %s : %s (n° %d)%s",
        description.nom_majuscule,
        description.accorde("supprimé"),
        analyse.libelle,
        ident,
        f" ; {' '.join(analyse.consequences)}" if analyse.consequences else "",
    )
    return analyse.libelle
