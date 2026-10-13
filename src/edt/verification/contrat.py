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
- Niveaux (`niveaux=`, facultatif) : seules les contraintes présentes sont
  vérifiées, et la gravité suit le niveau de la contrainte (nécessaire :
  erreur ; important : avertissement ; facultatif : information), sans
  jamais dépasser la gravité que la règle donne déjà. Le message d'une
  entorse à une règle importante ou facultative commence par
  `PREFIXES_ENTORSE[niveau]`. Sans niveaux, tout est vérifié en dur
  (comportement d'origine) et `Anomalie.niveau` vaut `None`.
- Règles personnalisées (`Instantane.regles`, code `REGLE`) : vérifiées si
  `REGLE` est vérifiée ; `niveaux_regles=` (facultatif, identifiant de règle
  → niveau) dit lesquelles et à quel niveau (défaut : toutes, au niveau de la
  règle ; une règle absente est ignorée, comme si elle n'existait pas). La
  gravité d'une entorse suit toujours le niveau de la règle.
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

from edt.domaine.contraintes import CodeContrainte, Niveau
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
      vérificateur : seul repère d'un problème de dépliage, qui n'a pas de séance) ;
    - `niveau` : niveau appliqué à la contrainte quand la vérification a reçu
      des niveaux, `None` sinon. Pour un niveau important ou facultatif, le
      message commence par `PREFIXES_ENTORSE[niveau]`. Pour une anomalie de
      règle personnalisée (code `REGLE`), toujours le niveau de **la règle**
      retenu pour la vérification ;
    - `regle_id`, `regle_code`, `regle_phrase` : la règle personnalisée
      enfreinte (code `REGLE` seulement), pour la nommer et la relier dans
      l'interface sans relire la base.

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
    niveau: Niveau | None = None
    regle_id: int | None = None
    regle_code: str | None = None
    regle_phrase: str | None = None

    @property
    def bloquante(self) -> bool:
        """Vrai si l'anomalie invalide l'emploi du temps (gravité « erreur »)."""
        return self.gravite is Gravite.ERREUR


PREFIXES_ENTORSE: dict[Niveau, str] = {
    Niveau.IMPORTANT: "Entorse à une règle importante : ",
    Niveau.FACULTATIF: "Entorse à une règle facultative : ",
}
"""Début stable du message d'une anomalie selon le niveau de sa contrainte (rien si nécessaire)."""


FonctionVerification = Callable[
    [Instantane, Sequence[Placement], Iterable[str] | None], list[Anomalie]
]
"""Signature de `verifier` (semaine type)."""

FonctionVerificationDatee = Callable[
    [Instantane, Sequence[Occurrence], Iterable[str] | None], list[Anomalie]
]
"""Signature de `verifier_occurrences` (séances datées)."""
