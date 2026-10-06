"""Niveaux des contraintes d'un calcul ou d'une version (nécessaire, important, facultatif).

Le vérificateur reçoit des niveaux (`edt.domaine.contraintes.Niveaux` : une
clé absente est une contrainte ignorée) ; ce module retrouve ceux qui
s'appliquent à une version enregistrée :

- `niveaux_du_calcul` (pur) : à partir des paramètres et des contraintes
  désactivées enregistrés avec un calcul ;
- `niveaux_de_version` : ceux du calcul qui a produit la version ; une
  version de référence (importée) ou dont le calcul a disparu prend les
  niveaux par défaut du catalogue.

Chaque résolution est journalisée en DEBUG avec son origine.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from edt.db.modeles import Calcul, VersionEdt
from edt.domaine.contraintes import (
    Niveau,
    Niveaux,
    niveaux_depuis_desactivees,
    niveaux_par_defaut,
    normaliser_codes,
    normaliser_niveaux,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable

journal = logging.getLogger(__name__)

CLE_NIVEAUX = "niveaux"
"""Clé des paramètres d'un calcul (`Calcul.parametres`) qui porte les niveaux choisis."""


def niveaux_du_calcul(
    parametres: Mapping[str, Any] | None, contraintes_desactivees: Iterable[str] | None
) -> Niveaux:
    """Niveaux appliqués par un calcul, d'après ce qui a été enregistré avec lui.

    - `parametres["niveaux"]` (dictionnaire code → « necessaire »,
      « important » ou « facultatif ») : validé par `normaliser_niveaux`, les
      codes absents prenant leur niveau par défaut ; les codes de
      `contraintes_desactivees` éventuellement enregistrés en plus sont
      retirés (contraintes ignorées) ;
    - sinon, calcul antérieur aux niveaux : `niveaux_depuis_desactivees`
      (niveaux par défaut sans les contraintes désactivées) ;
    - sinon, les niveaux par défaut.

    Lève `ErreurDonneesInvalides` si les niveaux enregistrés sont illisibles
    (pas un dictionnaire, code ou niveau inconnu) : jamais de repli silencieux.
    """
    desactivees = tuple(contraintes_desactivees or ())
    brut = (parametres or {}).get(CLE_NIVEAUX)
    if brut is not None:
        if not isinstance(brut, Mapping):
            raise ErreurDonneesInvalides(
                "Les niveaux enregistrés avec le calcul sont illisibles : un dictionnaire "
                f"« code : niveau » est attendu (lu : {type(brut).__name__})."
            )
        niveaux = normaliser_niveaux({str(code): str(valeur) for code, valeur in brut.items()})
        retirees = normaliser_codes(desactivees)
        niveaux = {code: n for code, n in niveaux.items() if code not in retirees}
        journal.debug(
            "Niveaux lus dans les paramètres du calcul : %s ; contraintes ignorées : %s",
            _resume(niveaux),
            ", ".join(sorted(retirees)) or "aucune",
        )
        return niveaux
    if desactivees:
        niveaux = niveaux_depuis_desactivees(desactivees)
        journal.debug(
            "Calcul sans niveaux enregistrés : niveaux par défaut sans les contraintes "
            "désactivées (%s)",
            ", ".join(sorted(desactivees)),
        )
        return niveaux
    journal.debug("Calcul sans niveaux ni contrainte désactivée : niveaux par défaut")
    return niveaux_par_defaut()


def niveaux_de_version(session: Session, version_id: int) -> Niveaux:
    """Niveaux à appliquer pour vérifier la version `version_id`.

    Ceux du calcul qui l'a produite (`niveaux_du_calcul`). Une version de
    référence ou importée n'a pas de calcul : niveaux par défaut. Une version
    générée dont le calcul a été supprimé prend aussi les niveaux par défaut,
    avec un avertissement dans le journal. Lève `ErreurIntrouvable` si la
    version n'existe pas.
    """
    version = session.get(VersionEdt, version_id)
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps {version_id} n'existe pas.")
    if version.calcul_id is None:
        journal.debug(
            "Version %d (%s) sans calcul : niveaux par défaut", version.id, version.origine
        )
        return niveaux_par_defaut()
    calcul = session.get(Calcul, version.calcul_id)
    if calcul is None:
        journal.warning(
            "Version %d : le calcul %d qui l'a produite est introuvable ; niveaux par défaut",
            version.id,
            version.calcul_id,
        )
        return niveaux_par_defaut()
    journal.debug("Version %d : niveaux du calcul %d", version.id, calcul.id)
    return niveaux_du_calcul(calcul.parametres, calcul.contraintes_desactivees)


def _resume(niveaux: Niveaux) -> str:
    """« 15 nécessaire(s), 3 important(s), 1 facultatif(s) » pour le journal."""
    return ", ".join(
        f"{sum(1 for n in niveaux.values() if n is membre)} {membre.libelle.lower()}(s)"
        for membre in Niveau
    )


__all__ = ["CLE_NIVEAUX", "niveaux_de_version", "niveaux_du_calcul"]
