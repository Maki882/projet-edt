"""Contrat du vérificateur : type `Anomalie` et signatures des fonctions de vérification.

Le vérificateur est **indépendant du solveur** : il ne lit que l'instantané
(`edt.domaine.instantane.Instantane`) et les placements ou occurrences qu'on
lui donne. Une liste vide signifie « emploi du temps valide ». Il sert
d'oracle aux tests du solveur, valide les modifications manuelles, audite un
emploi du temps importé et alimente le panneau des anomalies.

Règles communes aux deux fonctions
----------------------------------
- `contraintes_actives=None` vérifie toutes les contraintes actives par
  défaut du catalogue (`edt.domaine.contraintes.codes_par_defaut()`) ; sinon
  seules les contraintes dont le code est fourni sont vérifiées.
- Chaque anomalie porte le code de la contrainte violée, pris dans le
  catalogue : jamais de code hors catalogue.
- Gravité : `ERREUR` pour une contrainte violée ; `AVERTISSEMENT` pour une
  situation douteuse qui n'invalide pas l'emploi du temps ; `INFORMATION`
  pour un écart admis (par exemple un créneau imposé hors de la grille,
  code `HORAIRES`). Un emploi du temps est valide s'il n'a aucune `ERREUR`.
- Le message est une phrase française lisible par un non-informaticien :
  il nomme les séances, les salles, les personnes et l'heure en clair.
- Les fonctions sont pures : elles ne modifient rien et ne lèvent pas
  d'erreur pour signaler une violation (elles la renvoient).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Instantane, Occurrence, Placement
from edt.erreurs import ErreurNonLivre


@dataclass(frozen=True)
class Anomalie:
    """Une violation (ou un écart signalé) relevée par le vérificateur.

    - `code` : contrainte du catalogue concernée ;
    - `gravite` : erreur, avertissement ou information ;
    - `message` : phrase française complète, affichable telle quelle ;
    - `seances` : identifiants des séances à placer concernées (`"<besoin>-<rang>"`) ;
    - `occurrences` : identifiants des occurrences datées concernées ;
    - `salles`, `enseignants`, `groupes` : identifiants des ressources concernées ;
    - `jour` : jour de la semaine type (0 = lundi), pour une anomalie de semaine type ;
    - `date` : date, pour une anomalie de la couche datée.
    """

    code: CodeContrainte
    gravite: Gravite
    message: str
    seances: tuple[str, ...] = ()
    occurrences: tuple[int, ...] = ()
    salles: tuple[int, ...] = ()
    enseignants: tuple[int, ...] = ()
    groupes: tuple[int, ...] = ()
    jour: int | None = None
    date: dt.date | None = None

    @property
    def bloquante(self) -> bool:
        """Vrai si l'anomalie invalide l'emploi du temps (gravité « erreur »)."""
        return self.gravite is Gravite.ERREUR


FonctionVerification = Callable[
    [Instantane, Sequence[Placement], Iterable[str] | None], list[Anomalie]
]
"""Signature de `verifier`."""

FonctionVerificationDatee = Callable[
    [Instantane, Sequence[Occurrence], Iterable[str] | None], list[Anomalie]
]
"""Signature de `verifier_occurrences`."""


def verifier(
    instantane: Instantane,
    placements: Sequence[Placement],
    contraintes_actives: Iterable[str] | None = None,
) -> list[Anomalie]:
    """Liste les anomalies d'une semaine type.

    `placements` associe des séances de `instantane.seances` à un jour, une
    plage horaire et une salle. Une séance de l'instantané sans placement est
    une anomalie `VOLUME`. Deux séances ne peuvent se gêner que si leurs
    semaines actives se croisent. Les jours fermés ne sont pas examinés ici
    (couche datée).

    PROVISOIRE : implémentation à livrer par le lot « vérificateur ».
    """
    raise ErreurNonLivre(
        "Vérificateur non livré : la vérification de la semaine type appartient au lot "
        "« vérificateur » (edt.verification.verifier)."
    )


def verifier_occurrences(
    instantane: Instantane,
    occurrences: Sequence[Occurrence],
    contraintes_actives: Iterable[str] | None = None,
) -> list[Anomalie]:
    """Liste les anomalies d'un ensemble de séances datées.

    Seules les occurrences qui ont lieu (`statut.occupe_les_ressources`)
    occupent salle, enseignants et groupes. En plus des contraintes de la
    semaine type appliquées date par date, vérifie `JOUR_FERME`
    (`instantane.fermeture`) et les indisponibilités datées.

    PROVISOIRE : implémentation à livrer par le lot « vérificateur ».
    """
    raise ErreurNonLivre(
        "Vérificateur non livré : la vérification des séances datées appartient au lot "
        "« vérificateur » (edt.verification.verifier_occurrences)."
    )
