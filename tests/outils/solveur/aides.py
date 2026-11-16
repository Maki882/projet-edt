"""Constantes et aides partagées par plusieurs modules de tests du solveur (instantanés en mémoire).

- `UN_CRENEAU`, `MATINEE` : petites grilles du lundi matin ;
- `SEMAINE`, `place`, `instancier_et_replacer` : couche datée (instanciation puis replacement,
  vérifiés par l'oracle des séances datées) ;
- `niveaux_avec` : toutes les contraintes nécessaires sauf celles qui sont assouplies.
"""

from __future__ import annotations

import datetime as dt

from tests.outils.solveur.fabrique import (
    C1,
    LUNDI,
    NIVEAUX_DURS,
    Fabrique,
    H,
    grille,
    verifier_dates,
)

from edt.domaine.contraintes import CodeContrainte as C
from edt.domaine.contraintes import Niveau, niveaux_par_defaut
from edt.domaine.enumerations import DemiJournee
from edt.domaine.instantane import Creneau, Placement
from edt.solveur.instanciation import instancier
from edt.solveur.replacement import replacer

UN_CRENEAU = (Creneau(LUNDI, C1, C1 + 2 * H, DemiJournee.MATIN),)


MATINEE = grille(jours=[LUNDI], samedi=False)[:2]
"""Lundi matin : 8 h 30 - 10 h 30 puis 10 h 45 - 12 h 45."""


SEMAINE = dt.timedelta(days=7)


def place(seance_id: str, jour: int, debut: int, salle: int, duree: int = 2 * H) -> Placement:
    return Placement(seance_id, jour, debut, debut + duree, salle)


def instancier_et_replacer(f: Fabrique, placements, options=None, arret=None):
    instantane = f.instantane()
    instancie = instancier(instantane, placements)
    resultat = replacer(
        instantane,
        instancie.occurrences,
        [revoir.occurrence for revoir in instancie.a_replacer],
        options,
        arret,
    )
    assert len(resultat.replacements) == len(instancie.a_replacer)
    placees = [r.occurrence for r in resultat.replacees]
    niveaux = options.niveaux if options is not None else niveaux_par_defaut()
    verifier_dates(instantane, [*instancie.occurrences, *placees], niveaux=niveaux)
    return instantane, instancie, resultat


def niveaux_avec(**choix: str) -> dict[C, Niveau]:
    """Toutes les contraintes nécessaires, sauf celles de `choix` (code=niveau)."""
    return {**NIVEAUX_DURS, **{C(code): Niveau(valeur) for code, valeur in choix.items()}}
