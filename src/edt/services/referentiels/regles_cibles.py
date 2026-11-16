"""Règles personnalisées touchées par la suppression d'une cible.

Une règle **vise** une cible par sa portée (groupe, enseignant, campus, type
de séance, matière, besoin) : la base refuse de supprimer la cible tant que
la règle existe. Une règle **cite** une salle, un campus ou un type de séance
dans ses paramètres (`SALLES`, `CAMPUS`, `ORDRE_SEMAINE`) : la base ne dit
rien, mais la règle deviendrait fausse.

- `regles_touchees(session, code_entite, objet)` : ce que la suppression ferait
  aux règles (supprimées, ou la cible retirée de leurs paramètres) ;
- `appliquer(session, touchees)` : le fait, dans la transaction de l'appelant
  (règles d'abord, la cible ensuite), en journalisant chaque règle.

Une suppression d'un groupe touche aussi les règles de ses sous-groupes et
des matières rattachées à son sous-arbre (supprimés avec lui) ; celle d'un
semestre, les règles qui visent ses besoins.

`edt.services.regles` n'offre ni l'analyse ni le retrait d'une cible des
paramètres : ils sont faits ici, avec `regles_visant`, `lire_par_id`,
`modifier` et `supprimer` du service.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.regles import NaturePortee, TypeRegle
from edt.services import regles as service_regles
from edt.services.referentiels.personnes import sous_arbre

journal = logging.getLogger(__name__)

PARAMETRES_CITANT: dict[str, tuple[tuple[TypeRegle, str, bool], ...]] = {
    "salle": ((TypeRegle.SALLES, "salles", True),),
    "campus": ((TypeRegle.CAMPUS, "campus", True),),
    "type_seance": (
        (TypeRegle.ORDRE_SEMAINE, "avant", False),
        (TypeRegle.ORDRE_SEMAINE, "apres", False),
    ),
}
"""Entité → (type de règle, paramètre qui la cite par son code, paramètre en liste)."""


@dataclass(frozen=True)
class RegleTouchee:
    """Une règle que la suppression d'une cible supprime ou modifie.

    `retrait` : (paramètre, code) retiré des paramètres quand la règle est
    seulement modifiée ; vide quand elle est supprimée.
    """

    id: int
    code: str
    annee_scolaire_id: int
    annee: str
    phrase: str
    effet: str
    supprimee: bool
    retrait: tuple[str, str] | None = None

    def vers_dict(self) -> dict[str, Any]:
        """Représentation JSON."""
        return {
            "id": self.id,
            "code": self.code,
            "annee": self.annee,
            "phrase": self.phrase,
            "effet": self.effet,
            "supprimee": self.supprimee,
        }


def _visee(detail: service_regles.RegleDetail) -> RegleTouchee:
    return RegleTouchee(
        id=detail.id,
        code=detail.code,
        annee_scolaire_id=detail.regle.annee_scolaire_id or 0,
        annee=detail.annee_libelle,
        phrase=detail.phrase,
        effet="sera supprimée",
        supprimee=True,
    )


def _visant(session: Session, colonne: Any, identifiants: Iterable[int]) -> list[RegleTouchee]:
    ids = sorted(set(identifiants))
    if not ids:
        return []
    regles = session.scalars(select(m.Regle.id).where(colonne.in_(ids))).all()
    return [_visee(service_regles.lire_par_id(session, regle_id)) for regle_id in regles]


def _citant(session: Session, code_entite: str, objet: Any) -> list[RegleTouchee]:
    """Règles dont les paramètres citent l'objet par son code (salle, campus, type de séance)."""
    citations = PARAMETRES_CITANT.get(code_entite, ())
    if not citations:
        return []
    code = objet.code
    if code_entite == "salle":
        # Un code de salle peut exister sur deux campus : il reste valable si une autre salle le porte.
        homonymes = session.scalar(
            select(func.count()).where(m.Salle.code == code, m.Salle.id != objet.id)
        )
        if homonymes:
            return []
    touchees: dict[int, RegleTouchee] = {}
    for type_regle, nom, en_liste in citations:
        for ligne in session.scalars(select(m.Regle).where(m.Regle.type == type_regle)):
            valeur = (ligne.parametres or {}).get(nom)
            cite = code in valeur if en_liste and isinstance(valeur, list) else valeur == code
            if not cite or ligne.id in touchees:
                continue
            detail = service_regles.lire_par_id(session, ligne.id)
            reste = [v for v in valeur if v != code] if en_liste else []
            if en_liste and reste:
                effet = f"{code} sera retiré de ses {detail.regle.definition.champ(nom).libelle.lower()}"
                touchees[ligne.id] = RegleTouchee(
                    detail.id,
                    detail.code,
                    ligne.annee_scolaire_id,
                    detail.annee_libelle,
                    detail.phrase,
                    effet,
                    supprimee=False,
                    retrait=(nom, code),
                )
            else:
                touchees[ligne.id] = RegleTouchee(
                    detail.id,
                    detail.code,
                    ligne.annee_scolaire_id,
                    detail.annee_libelle,
                    detail.phrase,
                    f"sera supprimée (elle ne vaut plus sans {code})",
                    supprimee=True,
                )
    return list(touchees.values())


def regles_touchees(session: Session, code_entite: str, objet: Any) -> list[RegleTouchee]:
    """Règles que la suppression de `objet` (entité `code_entite`) supprime ou modifie."""
    touchees: list[RegleTouchee] = []
    if code_entite == "groupe":
        arbre = sous_arbre(session, objet.id)
        matieres = session.scalars(select(m.Matiere.id).where(m.Matiere.promotion_id.in_(arbre)))
        touchees += _visant(session, m.Regle.groupe_id, arbre)
        touchees += _visant(session, m.Regle.matiere_id, matieres)
    elif code_entite == "semestre":
        besoins = session.scalars(select(m.Besoin.id).where(m.Besoin.semestre_id == objet.id))
        touchees += _visant(session, m.Regle.besoin_id, besoins)
    else:
        colonne = {
            "enseignant": m.Regle.enseignant_id,
            "campus": m.Regle.campus_id,
            "type_seance": m.Regle.type_seance_id,
            "matiere": m.Regle.matiere_id,
            "besoin": m.Regle.besoin_id,
        }.get(code_entite)
        if colonne is not None:
            touchees += [
                _visee(detail)
                for detail in service_regles.regles_visant(
                    session, NaturePortee(code_entite), objet.id
                )
            ]
    deja = {t.id for t in touchees}
    touchees += [t for t in _citant(session, code_entite, objet) if t.id not in deja]
    return sorted(touchees, key=lambda t: (t.annee, t.code))


def appliquer(session: Session, touchees: Iterable[RegleTouchee], cible: str) -> None:
    """Supprime ou modifie les règles touchées (avant la suppression de la cible)."""
    for touchee in touchees:
        if touchee.supprimee:
            service_regles.supprimer(session, touchee.annee_scolaire_id, touchee.code)
            journal.info(
                "Règle %s (%s) supprimée avec %s : %s",
                touchee.code,
                touchee.annee,
                cible,
                touchee.phrase,
            )
            continue
        assert touchee.retrait is not None
        nom, code = touchee.retrait
        detail = service_regles.lire_par_id(session, touchee.id)
        reste = [v for v in detail.regle.parametres.get(nom, []) if v != code]
        service_regles.modifier(
            session, touchee.annee_scolaire_id, touchee.code, {"parametres": {nom: reste}}
        )
        journal.info(
            "Règle %s (%s) : %s retiré de « %s » avant la suppression de %s",
            touchee.code,
            touchee.annee,
            code,
            nom,
            cible,
        )
