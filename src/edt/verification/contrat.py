"""Contrat du vérificateur : type `Anomalie` et signatures des fonctions de vérification.

Le vérificateur est **indépendant du solveur** : il ne lit que l'instantané
(`edt.domaine.instantane.Instantane`) et les placements ou occurrences qu'on
lui donne. Une liste vide signifie « emploi du temps valide ». Il sert
d'oracle aux tests du solveur, valide les modifications manuelles, audite un
emploi du temps importé et alimente le panneau des anomalies.

Les implémentations sont `edt.verification.semaine_type.verifier` et
`edt.verification.datee.verifier_occurrences`, exportées par
`edt.verification` ; leurs signatures sont décrites ici par
`FonctionVerification` et `FonctionVerificationDatee`.

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
  d'erreur pour signaler une violation (elles la renvoient). Elles lèvent
  une erreur métier (`edt.erreurs`) seulement quand l'entrée est trop
  incohérente pour être vérifiée (heure hors de la journée, salle inconnue...).
- La liste renvoyée est triée de façon déterministe : gravité, ordre du
  catalogue, jour ou date, séances concernées, puis message.

Le détail des règles est dans `docs/verification.md`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.domaine.instantane import Instantane, Occurrence, Placement


@dataclass(frozen=True)
class Anomalie:
    """Une violation (ou un écart signalé) relevée par le vérificateur.

    - `code` : contrainte du catalogue concernée ;
    - `gravite` : erreur, avertissement ou information ;
    - `message` : phrase française complète, affichable telle quelle ;
    - `seances` : identifiants des séances à placer concernées (`"<besoin>-<rang>"`) ;
    - `occurrences` : identifiants des occurrences datées concernées (celles
      qui en ont un : une occurrence candidate, pas encore enregistrée, n'en a pas) ;
    - `salles`, `enseignants`, `groupes` : identifiants des ressources en cause ;
    - `jour` : jour de la semaine type (0 = lundi), pour une anomalie de semaine type ;
    - `date` : date, pour une anomalie de la couche datée ;
    - `besoins` : identifiants des besoins concernés (ajouté par le lot
      vérificateur : seul repère d'un problème de dépliage, qui n'a pas de séance).

    Les tuples d'identifiants sont triés et sans doublon.
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
    besoins: tuple[int, ...] = ()

    @property
    def bloquante(self) -> bool:
        """Vrai si l'anomalie invalide l'emploi du temps (gravité « erreur »)."""
        return self.gravite is Gravite.ERREUR


FonctionVerification = Callable[
    [Instantane, Sequence[Placement], Iterable[str] | None], list[Anomalie]
]
"""Signature de `verifier` (semaine type)."""

FonctionVerificationDatee = Callable[
    [Instantane, Sequence[Occurrence], Iterable[str] | None], list[Anomalie]
]
"""Signature de `verifier_occurrences` (séances datées)."""
