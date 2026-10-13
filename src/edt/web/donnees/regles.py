"""Formulaire d'une règle personnalisée, engendré depuis le schéma de son type.

Le formulaire envoie des champs à plat ; ce module fait le passage dans les
deux sens avec le brouillon de `edt.services.regles` :

| Champ du formulaire          | Brouillon                         |
|------------------------------|-----------------------------------|
| `portee`                     | `portee.nature`                   |
| `cible_<nature>`             | `portee.cible` (identifiant)      |
| `p_<paramètre>`              | `parametres.<paramètre>`          |
| `niveau`, `commentaire`      | `niveau`, `commentaire`           |
| `periode` + `semestre`, `date_debut`, `date_fin` | `periode`     |

Les champs utiles à un seul choix (cible d'une nature, paramètre d'un mode,
semestre ou dates) portent `data-visible-si` : masqués, ils ne sont pas
envoyés. Un paramètre propre à la portée établissement (`CHAMPS_ETABLISSEMENT`)
n'est affiché que pour elle, et retiré du brouillon pour une autre portée
(envoi sans JavaScript compris). Les erreurs du service (clé = chemin JSON) reviennent sous le champ
correspondant (`erreurs_par_champ`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import FormData

from edt.db import modeles as m
from edt.domaine.contraintes import Niveau
from edt.domaine.enumerations import JOURS
from edt.domaine.heures import minutes_vers_hhmm
from edt.domaine.parametres import Parametres
from edt.domaine.regles import (
    ChampRegle,
    DefinitionType,
    NaturePortee,
    SorteChamp,
)
from edt.services.referentiels.enseignements import libelle_besoin
from edt.services.referentiels.personnes import NoeudGroupe, arbre_groupes
from edt.services.regles import RegleDetail
from edt.web.donnees.formulaires import ChampAffiche, RubriqueChamps

PREFIXE_PARAMETRE = "p_"
NATURES_AVEC_CIBLE: tuple[NaturePortee, ...] = tuple(
    nature for nature in NaturePortee if nature is not NaturePortee.ETABLISSEMENT
)
CHAMPS_ETABLISSEMENT: frozenset[str] = frozenset({"personnes"})
"""Paramètres qui n'ont de sens que pour la portée établissement.

« Personnes concernées » (groupes, enseignants ou les deux) choisit qui vise
une règle d'établissement ; pour un groupe ou un enseignant, la cible le dit
déjà et le domaine ignore ce paramètre. Le schéma ne le marque que dans sa
description : la liste est tenue ici.
"""
PERIODES = (("annee", "Toute l'année"), ("semestre", "Un semestre"), ("dates", "Entre deux dates"))
DEMI_JOURNEES = ("matin", "après-midi")


@dataclass(frozen=True)
class ErreursRegle:
    """Erreurs du service réparties : sous un champ du formulaire, ou en tête."""

    champs: dict[str, str]
    generales: list[str]


def natures_admises(definition: DefinitionType) -> list[NaturePortee]:
    """Portées admises par le type, dans l'ordre du catalogue (les refus expliqués exclus)."""
    refusees = {nature for nature, _ in definition.refus}
    return [n for n in NaturePortee if n in definition.portees and n not in refusees]


# --------------------------------------------------------------------------- #
# Choix proposés
# --------------------------------------------------------------------------- #


def _aplatir(noeuds: tuple[NoeudGroupe, ...], profondeur: int = 0) -> list[tuple[str, str]]:
    options: list[tuple[str, str]] = []
    for noeud in noeuds:
        retrait = " " * profondeur + ("└ " if profondeur else "")
        options.append((str(noeud.id), f"{retrait}{noeud.code} - {noeud.libelle}"))
        options += _aplatir(noeud.enfants, profondeur + 1)
    return options


def options_groupes(session: Session, annee_scolaire_id: int) -> list[tuple[str, str]]:
    """Groupes de l'année dans l'ordre de l'arbre, en retrait selon la profondeur."""
    return _aplatir(arbre_groupes(session, annee_scolaire_id))


def options_cibles(session: Session, annee: m.AnneeScolaire) -> dict[str, list[tuple[str, str]]]:
    """Cibles possibles de chaque nature de portée pour une année (valeur = identifiant)."""
    semestres = [s.id for s in annee.semestres]
    besoins = session.scalars(
        select(m.Besoin)
        .where(m.Besoin.semestre_id.in_(semestres))
        .options(
            selectinload(m.Besoin.matiere),
            selectinload(m.Besoin.type_seance),
            selectinload(m.Besoin.groupes),
            selectinload(m.Besoin.semestre),
        )
    ).all()
    matieres = session.scalars(
        select(m.Matiere)
        .outerjoin(m.Groupe, m.Matiere.promotion_id == m.Groupe.id)
        .where(or_(m.Matiere.promotion_id.is_(None), m.Groupe.annee_scolaire_id == annee.id))
        .order_by(m.Matiere.code)
    ).all()
    return {
        NaturePortee.GROUPE.value: options_groupes(session, annee.id),
        NaturePortee.ENSEIGNANT.value: [
            (str(e.id), f"{e.nom_affiche} ({e.identifiant})")
            for e in session.scalars(
                select(m.Enseignant).order_by(m.Enseignant.nom, m.Enseignant.prenom)
            )
        ],
        NaturePortee.CAMPUS.value: [
            (str(c.id), f"{c.code} - {c.nom}")
            for c in session.scalars(select(m.Campus).order_by(m.Campus.code))
        ],
        NaturePortee.TYPE_SEANCE.value: [
            (str(t.id), f"{t.code} - {t.libelle}")
            for t in session.scalars(select(m.TypeSeance).order_by(m.TypeSeance.code))
        ],
        NaturePortee.MATIERE.value: [(str(x.id), f"{x.code} - {x.libelle}") for x in matieres],
        NaturePortee.BESOIN.value: sorted(
            ((str(b.id), f"{b.semestre.code} · {libelle_besoin(b)}") for b in besoins),
            key=lambda option: option[1],
        ),
    }


def options_parametre(session: Session, champ: ChampRegle) -> list[tuple[str, str]]:
    """Choix d'un paramètre de règle (jours, choix fermé, codes de campus, salles, types)."""
    if champ.sorte is SorteChamp.JOURS:
        return [(nom, nom.capitalize()) for nom in JOURS]
    if champ.sorte is SorteChamp.CHOIX:
        return list(champ.choix)
    if champ.sorte is SorteChamp.MOMENTS:
        options = [(nom, nom.capitalize()) for nom in JOURS[:6]]
        options += [
            (f"{nom} {demi}", f"{nom.capitalize()} {demi}")
            for nom in JOURS[:6]
            for demi in DEMI_JOURNEES
        ]
        return options
    if champ.entite == "campus":
        return [
            (c.code, f"{c.code} - {c.nom}")
            for c in session.scalars(select(m.Campus).order_by(m.Campus.code))
        ]
    if champ.entite == "salle":
        options: dict[str, str] = {}
        for salle in session.scalars(
            select(m.Salle).options(selectinload(m.Salle.campus)).order_by(m.Salle.code)
        ):
            options.setdefault(
                salle.code, f"{salle.code} ({salle.campus.code}, {salle.capacite} places)"
            )
        return list(options.items())
    if champ.entite == "type_seance":
        return [
            (t.code, f"{t.code} - {t.libelle}")
            for t in session.scalars(select(m.TypeSeance).order_by(m.TypeSeance.code))
        ]
    return []


# --------------------------------------------------------------------------- #
# Valeurs du formulaire
# --------------------------------------------------------------------------- #

SORTES_LISTE = (SorteChamp.JOURS, SorteChamp.CODES, SorteChamp.MOMENTS)


def _defaut_parametre(champ: ChampRegle, parametres: Parametres) -> Any:
    if champ.defaut_global is not None:
        valeur = getattr(parametres, champ.defaut_global)
        return minutes_vers_hhmm(int(valeur)) if champ.sorte is SorteChamp.HEURE else valeur
    if isinstance(champ.defaut, list):
        return list(champ.defaut)
    return champ.defaut


def valeurs_nouvelle(definition: DefinitionType, parametres: Parametres) -> dict[str, Any]:
    """Valeurs initiales du formulaire d'une nouvelle règle du type (défauts compris)."""
    valeurs: dict[str, Any] = {
        "portee": natures_admises(definition)[0].value,
        "niveau": definition.niveau_defaut.value,
        "periode": "annee",
    }
    for champ in definition.champs:
        defaut = _defaut_parametre(champ, parametres)
        if defaut is not None:
            valeurs[PREFIXE_PARAMETRE + champ.nom] = defaut
    return valeurs


def valeurs_de_regle(detail: RegleDetail) -> dict[str, Any]:
    """Valeurs du formulaire pour modifier une règle enregistrée."""
    regle = detail.regle
    valeurs: dict[str, Any] = {
        "portee": regle.portee.nature.value,
        "niveau": regle.niveau.value,
        "commentaire": regle.commentaire,
        "periode": "semestre"
        if regle.semestre_code
        else ("dates" if regle.date_debut or regle.date_fin else "annee"),
        "semestre": regle.semestre_code or "",
        "date_debut": regle.date_debut.isoformat() if regle.date_debut else "",
        "date_fin": regle.date_fin.isoformat() if regle.date_fin else "",
    }
    if regle.portee.cible_id is not None:
        valeurs[f"cible_{regle.portee.nature.value}"] = str(regle.portee.cible_id)
    for nom, valeur in regle.parametres.items():
        valeurs[PREFIXE_PARAMETRE + nom] = valeur
    return valeurs


def valeurs_de_formulaire(definition: DefinitionType, formulaire: FormData) -> dict[str, Any]:
    """Saisie brute, pour réafficher le formulaire tel qu'il a été envoyé."""
    valeurs: dict[str, Any] = {cle: str(valeur) for cle, valeur in formulaire.items()}
    for champ in definition.champs:
        if champ.sorte in SORTES_LISTE:
            valeurs[PREFIXE_PARAMETRE + champ.nom] = formulaire.getlist(
                PREFIXE_PARAMETRE + champ.nom
            )
    return valeurs


def brouillon_depuis_formulaire(
    definition: DefinitionType, annee_scolaire_id: int, formulaire: FormData
) -> dict[str, Any]:
    """Brouillon de `edt.services.regles` depuis le formulaire envoyé.

    Un paramètre masqué (son mode n'est pas choisi) n'est pas envoyé : il est
    absent du brouillon. Une liste sans case cochée vaut la liste vide.
    """
    nature = str(formulaire.get("portee", "")).strip()
    cible = str(formulaire.get(f"cible_{nature}", "")).strip()
    parametres: dict[str, Any] = {}
    for champ in definition.champs:
        nom = PREFIXE_PARAMETRE + champ.nom
        if champ.nom in CHAMPS_ETABLISSEMENT and nature != NaturePortee.ETABLISSEMENT.value:
            continue
        if champ.sorte in SORTES_LISTE:
            parametres[champ.nom] = formulaire.getlist(nom)
        elif nom in formulaire:
            parametres[champ.nom] = str(formulaire[nom]).strip()
    mode = str(formulaire.get("periode", "annee"))
    periode: dict[str, Any] = {}
    if mode == "semestre":
        periode["semestre"] = str(formulaire.get("semestre", "")).strip()
    elif mode == "dates":
        periode["date_debut"] = str(formulaire.get("date_debut", "")).strip() or None
        periode["date_fin"] = str(formulaire.get("date_fin", "")).strip() or None
    return {
        "type": definition.code.value,
        "annee": annee_scolaire_id,
        "portee": {
            "nature": nature,
            "cible": int(cible) if cible.isascii() and cible.isdigit() else (cible or None),
        },
        "parametres": parametres,
        "niveau": str(formulaire.get("niveau", "")).strip() or None,
        "periode": periode,
        "commentaire": str(formulaire.get("commentaire", "")).strip(),
    }


def controles_formulaire(formulaire: FormData) -> dict[str, str]:
    """Erreurs propres au formulaire, que le service ne peut pas voir (champ laissé vide)."""
    if formulaire.get("periode") == "semestre" and not str(formulaire.get("semestre", "")).strip():
        return {"semestre": "Choisir le semestre."}
    return {}


def erreurs_par_champ(erreurs: Mapping[str, str], nature: str) -> ErreursRegle:
    """Erreurs du service (chemin JSON) rangées sous les champs du formulaire."""
    champs: dict[str, str] = {}
    generales: list[str] = []
    correspondances = {
        "portee.nature": "portee",
        "portee": "portee",
        "portee.cible": f"cible_{nature}",
        "niveau": "niveau",
        "periode": "periode",
        "periode.semestre": "semestre",
        "periode.date_debut": "date_debut",
        "periode.date_fin": "date_fin",
    }
    for cle, message in erreurs.items():
        if cle in correspondances:
            champs.setdefault(correspondances[cle], message)
        elif cle.startswith("parametres."):
            champs.setdefault(PREFIXE_PARAMETRE + cle.removeprefix("parametres."), message)
        else:
            generales.append(message)
    return ErreursRegle(champs, generales)


# --------------------------------------------------------------------------- #
# Champs affichés
# --------------------------------------------------------------------------- #


def _champ_parametre(
    session: Session,
    champ: ChampRegle,
    valeur: Any,
    erreur: str | None,
    parametres: Parametres,
) -> ChampAffiche:
    nom = PREFIXE_PARAMETRE + champ.nom
    aide = champ.description
    if champ.defaut_global is not None:
        defaut = _defaut_parametre(champ, parametres)
        aide = (aide + " " if aide else "") + f"Défaut de l'établissement : {defaut}."
    conteneur: dict[str, Any] = {}
    if champ.condition is not None:
        autre, permises = champ.condition
        conteneur["data-visible-si"] = f"{PREFIXE_PARAMETRE}{autre}:{','.join(permises)}"
    if champ.nom in CHAMPS_ETABLISSEMENT:
        # Une seule condition par bloc : celle de la portée prime (aucun de ces
        # paramètres n'a de condition de mode aujourd'hui) ; le brouillon le filtre aussi.
        conteneur["data-visible-si"] = f"portee:{NaturePortee.ETABLISSEMENT.value}"
    attributs: dict[str, Any] = {}
    unite = champ.unite or None
    if champ.sorte is SorteChamp.JOURS or (
        champ.sorte is SorteChamp.CODES and champ.entite == "campus"
    ):
        type_html = "cases"
    elif champ.sorte in (SorteChamp.CODES, SorteChamp.MOMENTS):
        type_html = "multiple"
    elif champ.sorte in (SorteChamp.CHOIX, SorteChamp.CODE):
        type_html = "select"
    elif champ.sorte is SorteChamp.HEURE:
        type_html = "time"
    else:
        type_html = "number"
        attributs = {"min": champ.minimum, "max": champ.maximum, "step": 1}
        attributs = {cle: v for cle, v in attributs.items() if v is not None}
        if champ.sorte is SorteChamp.DUREE:
            unite = "min"
    options = (
        options_parametre(session, champ) if type_html in ("cases", "multiple", "select") else []
    )
    if type_html == "select" and champ.defaut is None and champ.defaut_global is None:
        options = [("", "Choisir..."), *options]
    if type_html in ("cases", "multiple"):
        valeur = [str(v) for v in (valeur or [])]
    else:
        valeur = "" if valeur is None else str(valeur)
    return ChampAffiche(
        nom=nom,
        libelle=champ.libelle,
        type=type_html,
        valeur=valeur,
        requis=champ.obligatoire and type_html not in ("cases", "multiple"),
        aide=aide,
        erreur=erreur,
        options=tuple(options),
        attributs=attributs,
        unite=unite,
        conteneur=conteneur,
        large=type_html in ("cases", "multiple"),
    )


def rubriques_regle(
    session: Session,
    definition: DefinitionType,
    annee: m.AnneeScolaire,
    valeurs: Mapping[str, Any],
    erreurs: Mapping[str, str],
    parametres: Parametres,
) -> tuple[RubriqueChamps, ...]:
    """Les rubriques du formulaire : portée, paramètres, niveau et période."""
    natures = natures_admises(definition)
    portee = [
        ChampAffiche(
            nom="portee",
            libelle="À qui s'applique la règle",
            type="select",
            valeur=str(valeurs.get("portee", "")),
            requis=True,
            aide="Portées admises par ce type. Un groupe vaut aussi pour ses sous-groupes.",
            erreur=erreurs.get("portee"),
            options=tuple((n.value, n.libelle) for n in natures),
        )
    ]
    cibles = options_cibles(session, annee)
    for nature in natures:
        if nature is NaturePortee.ETABLISSEMENT:
            continue
        nom = f"cible_{nature.value}"
        portee.append(
            ChampAffiche(
                nom=nom,
                libelle=nature.libelle,
                type="select",
                valeur=str(valeurs.get(nom, "")),
                requis=True,
                aide="Groupes dans l'ordre de l'arbre : promotion, puis ses classes et demi-groupes."
                if nature is NaturePortee.GROUPE
                else "",
                erreur=erreurs.get(nom),
                options=(("", "Choisir..."), *cibles[nature.value]),
                conteneur={"data-visible-si": f"portee:{nature.value}"},
                large=True,
            )
        )
    champs_parametres = [
        _champ_parametre(
            session,
            champ,
            valeurs.get(PREFIXE_PARAMETRE + champ.nom),
            erreurs.get(PREFIXE_PARAMETRE + champ.nom),
            parametres,
        )
        for champ in definition.champs
    ]
    semestres = [(s.code, f"{s.code} - {s.libelle}") for s in annee.semestres]
    niveau_defaut = definition.niveau_defaut.libelle.lower()
    reglages = [
        ChampAffiche(
            nom="niveau",
            libelle="Niveau",
            type="select",
            valeur=str(valeurs.get("niveau", "")),
            requis=True,
            aide=f"Nécessaire : jamais enfreinte. Important ou facultatif : respectée au mieux. "
            f"Défaut de ce type : {niveau_defaut}.",
            erreur=erreurs.get("niveau"),
            options=tuple((n.value, n.libelle) for n in Niveau),
        ),
        ChampAffiche(
            nom="periode",
            libelle="Période",
            type="select",
            valeur=str(valeurs.get("periode", "annee")),
            requis=True,
            aide=f"Dans l'année {annee.libelle}.",
            erreur=erreurs.get("periode"),
            options=PERIODES,
        ),
        ChampAffiche(
            nom="semestre",
            libelle="Semestre",
            type="select",
            valeur=str(valeurs.get("semestre", "")),
            requis=True,
            aide="",
            erreur=erreurs.get("semestre"),
            options=(("", "Choisir..."), *semestres),
            conteneur={"data-visible-si": "periode:semestre"},
        ),
        ChampAffiche(
            nom="date_debut",
            libelle="Du",
            type="date",
            valeur=str(valeurs.get("date_debut", "")),
            requis=False,
            aide="Vide : dès le début de l'année.",
            erreur=erreurs.get("date_debut"),
            conteneur={"data-visible-si": "periode:dates"},
        ),
        ChampAffiche(
            nom="date_fin",
            libelle="Au",
            type="date",
            valeur=str(valeurs.get("date_fin", "")),
            requis=False,
            aide="Vide : jusqu'à la fin de l'année.",
            erreur=erreurs.get("date_fin"),
            conteneur={"data-visible-si": "periode:dates"},
        ),
        ChampAffiche(
            nom="commentaire",
            libelle="Commentaire",
            type="textarea",
            valeur=str(valeurs.get("commentaire", "") or ""),
            requis=False,
            aide="Facultatif : qui l'a demandée, pourquoi.",
            erreur=None,
            attributs={"maxlength": 2000},
            large=True,
        ),
    ]
    rubriques = [RubriqueChamps("Portée", tuple(portee))]
    if champs_parametres:
        rubriques.append(RubriqueChamps("Paramètres", tuple(champs_parametres)))
    rubriques.append(RubriqueChamps("Niveau, période et commentaire", tuple(reglages)))
    return tuple(rubriques)
