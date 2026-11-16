"""Relation de conflit entre groupes : qui ne peut pas avoir cours en même temps que qui.

C'est **la seule** implémentation de cette relation : l'instantané l'embarque
(`Instantane.conflits_groupes`), le solveur et le vérificateur la consomment.

Deux groupes distincts sont en conflit dans trois cas :

1. **Inclusion hiérarchique** : l'un est un ascendant de l'autre dans l'arbre
   (promotion > classe > demi-groupe). Deux frères ne sont pas en conflit.
2. **Chevauchement déclaré** : la paire est déclarée chevauchante. Faute de
   liste d'étudiants, rien ne dit lesquels sont communs : la déclaration
   vaut donc pour toute la **lignée** des deux groupes (chacun, ses
   ascendants et ses descendants).
3. **Étudiant commun** : un étudiant est inscrit dans les deux. Un étudiant
   inscrit dans un groupe appartient aussi à tous les ascendants de ce
   groupe (pas à ses descendants).

Conséquence à connaître : un groupe transverse sans étudiants inscrits et
sans chevauchement déclaré n'est en conflit qu'avec ses propres ascendants.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db.modeles import Groupe, groupe_chevauchement, inscription
from edt.erreurs import ErreurDonneesInvalides

Paire = tuple[int, int]


def calculer_conflits_groupes(
    parents: Mapping[int, int | None],
    chevauchements: Iterable[Paire] = (),
    inscriptions: Iterable[Paire] = (),
) -> frozenset[Paire]:
    """Calcule les paires de groupes en conflit (fonction pure).

    - `parents` : pour chaque identifiant de groupe, celui de son parent ou `None` ;
    - `chevauchements` : paires de groupes déclarés chevauchants ;
    - `inscriptions` : couples `(etudiant_id, groupe_id)`.

    Renvoie des paires `(a, b)` avec `a < b`. Les identifiants inconnus dans
    `chevauchements` ou `inscriptions` lèvent `ErreurDonneesInvalides`.
    """
    ascendants = {groupe: _ascendants(groupe, parents) for groupe in parents}
    descendants: dict[int, set[int]] = {groupe: set() for groupe in parents}
    for groupe, lignee in ascendants.items():
        for ascendant in lignee:
            descendants[ascendant].add(groupe)

    conflits: set[Paire] = set()

    # 1. Inclusion hiérarchique.
    for groupe, lignee in ascendants.items():
        conflits.update(_paire(groupe, ascendant) for ascendant in lignee)

    # 2. Chevauchements déclarés, étendus aux lignées.
    for a, b in chevauchements:
        _verifier_connu(a, parents)
        _verifier_connu(b, parents)
        if a == b:
            continue
        lignee_a = {a} | ascendants[a] | descendants[a]
        lignee_b = {b} | ascendants[b] | descendants[b]
        conflits.update(_paire(x, y) for x in lignee_a for y in lignee_b if x != y)

    # 3. Étudiants communs (un inscrit appartient aussi aux ascendants de son groupe).
    groupes_par_etudiant: dict[int, set[int]] = {}
    for etudiant, groupe in inscriptions:
        _verifier_connu(groupe, parents)
        groupes_par_etudiant.setdefault(etudiant, set()).update({groupe} | ascendants[groupe])
    for groupes in groupes_par_etudiant.values():
        conflits.update(_paire(x, y) for x, y in combinations(sorted(groupes), 2))

    return frozenset(conflits)


def conflits_groupes(session: Session, annee_scolaire_id: int) -> frozenset[Paire]:
    """Paires de groupes en conflit pour une année scolaire, lues dans la base."""
    parents = dict(
        session.execute(
            select(Groupe.id, Groupe.parent_id).where(Groupe.annee_scolaire_id == annee_scolaire_id)
        ).all()
    )
    if not parents:
        return frozenset()
    identifiants = list(parents)
    chevauchements = session.execute(
        select(groupe_chevauchement.c.groupe_a_id, groupe_chevauchement.c.groupe_b_id).where(
            groupe_chevauchement.c.groupe_a_id.in_(identifiants),
            groupe_chevauchement.c.groupe_b_id.in_(identifiants),
        )
    ).all()
    inscriptions = session.execute(
        select(inscription.c.etudiant_id, inscription.c.groupe_id).where(
            inscription.c.groupe_id.in_(identifiants)
        )
    ).all()
    return calculer_conflits_groupes(
        parents,
        [(a, b) for a, b in chevauchements],
        [(etudiant, groupe) for etudiant, groupe in inscriptions],
    )


def _ascendants(groupe: int, parents: Mapping[int, int | None]) -> set[int]:
    lignee: set[int] = set()
    courant = parents.get(groupe)
    while courant is not None:
        if courant == groupe or courant in lignee:
            raise ErreurDonneesInvalides(
                f"L'arbre des groupes contient une boucle autour du groupe n° {groupe} : "
                "corriger son groupe parent (Données › Groupes)."
            )
        if courant not in parents:
            raise ErreurDonneesInvalides(
                f"Le groupe {groupe} a pour ascendant le groupe {courant}, qui est inconnu "
                "(autre année scolaire ?)."
            )
        lignee.add(courant)
        courant = parents[courant]
    return lignee


def _paire(a: int, b: int) -> Paire:
    return (a, b) if a < b else (b, a)


def _verifier_connu(groupe: int, parents: Mapping[int, int | None]) -> None:
    if groupe not in parents:
        raise ErreurDonneesInvalides(f"Groupe inconnu dans la relation de conflit : {groupe}.")
