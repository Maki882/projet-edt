"""Niveaux des contraintes d'un calcul ou d'une version (nécessaire, important, facultatif).

Le vérificateur reçoit des niveaux (`edt.domaine.contraintes.Niveaux` : une
clé absente est une contrainte ignorée) ; ce module retrouve ceux qui
s'appliquent à une version enregistrée :

- `niveaux_du_calcul` (pur) : à partir des paramètres et des contraintes
  désactivées enregistrés avec un calcul ;
- `niveaux_de_version` : ceux du calcul qui a produit la version ; une
  version de référence (importée) ou dont le calcul a disparu prend les
  niveaux par défaut du catalogue ;
- `niveaux_regles_du_calcul` (pur) et `niveaux_regles_de_version` : niveaux
  des **règles personnalisées** (identifiant de règle → niveau). Une version
  se vérifie avec les règles **actuelles** (actives de son année) et, pour
  celles qui existaient lors du calcul, le niveau choisi alors.

Chaque résolution est journalisée en DEBUG avec son origine.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db.modeles import Calcul, Regle, Semestre, VersionEdt
from edt.db.moteur import identifiant_valide
from edt.domaine.contraintes import (
    Niveau,
    Niveaux,
    niveau,
    niveaux_depuis_desactivees,
    niveaux_par_defaut,
    normaliser_codes,
    normaliser_niveaux,
)
from edt.domaine.regles import EtatRegle
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable

journal = logging.getLogger(__name__)

CLE_NIVEAUX = "niveaux"
"""Clé des paramètres d'un calcul (`Calcul.parametres`) qui porte les niveaux choisis."""

CLE_REGLES = "regles"
"""Clé des paramètres d'un calcul qui copie chaque règle personnalisée appliquée.

Écrite par le lot solveur : une liste de dictionnaires `{id, code, type,
portee, parametres, niveau, phrase}` ; `niveau` vide (`None`) marque une
règle explicitement non appliquée.
"""


@dataclass(frozen=True)
class RegleActuelle:
    """Ce qu'il faut savoir d'une règle active pour retrouver son niveau de vérification."""

    id: int
    niveau: Niveau
    modifiee_le: dt.datetime | None = None


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
    version = session.get(VersionEdt, version_id) if identifiant_valide(version_id) else None
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


def niveaux_regles_du_calcul(
    parametres: Mapping[str, Any] | None,
    regles: Iterable[RegleActuelle],
    demarre_le: dt.datetime | None = None,
) -> dict[int, Niveau]:
    """Niveau de vérification de chaque règle active actuelle, d'après un calcul (pur).

    - calcul sans `parametres["regles"]` (antérieur aux règles, ou pas de
      calcul) : chaque règle à son propre niveau ;
    - règle copiée par le calcul : le niveau choisi alors (`None` : règle
      explicitement non appliquée, donc ignorée) ;
    - règle absente de la copie : à son propre niveau si elle a été créée ou
      modifiée (activation comprise) après le début du calcul ; sinon ignorée,
      car elle existait et n'a pas été appliquée (« ne pas appliquer » coché).

    Lève `ErreurDonneesInvalides` si la copie est illisible (pas une liste,
    entrée sans identifiant entier, niveau inconnu) : jamais de repli silencieux.
    """
    actuelles = list(regles)
    brut = (parametres or {}).get(CLE_REGLES)
    if brut is None:
        journal.debug(
            "Calcul sans copie des règles : %d règle(s) à leur propre niveau", len(actuelles)
        )
        return {regle.id: regle.niveau for regle in actuelles}
    if not isinstance(brut, list | tuple):
        raise ErreurDonneesInvalides(
            "Les règles enregistrées avec le calcul sont illisibles : une liste est attendue "
            f"(lu : {type(brut).__name__})."
        )
    choisis: dict[int, Niveau | None] = {}
    for entree in brut:
        identifiant = entree.get("id") if isinstance(entree, Mapping) else None
        if isinstance(identifiant, bool) or not isinstance(identifiant, int):
            raise ErreurDonneesInvalides(
                "Une règle enregistrée avec le calcul n'a pas d'identifiant entier "
                f"(lu : {entree!r})."
            )
        valeur = entree.get("niveau")
        choisis[identifiant] = None if valeur is None else niveau(str(valeur))
    resultat: dict[int, Niveau] = {}
    nouvelles = ignorees = 0
    for regle in actuelles:
        if regle.id in choisis:
            choisi = choisis[regle.id]
            if choisi is None:
                ignorees += 1
            else:
                resultat[regle.id] = choisi
        elif demarre_le is None or (
            regle.modifiee_le is not None and regle.modifiee_le > demarre_le
        ):
            resultat[regle.id] = regle.niveau
            nouvelles += 1
        else:
            ignorees += 1
    journal.debug(
        "Niveaux des règles : %d du calcul, %d créée(s) ou modifiée(s) depuis (niveau propre), "
        "%d ignorée(s)",
        len(resultat) - nouvelles,
        nouvelles,
        ignorees,
    )
    return resultat


def niveaux_regles_de_version(session: Session, version_id: int) -> dict[int, Niveau]:
    """Niveaux des règles personnalisées pour vérifier la version `version_id`.

    Règles **actuelles** : les règles actives de l'année du semestre de la
    version ; niveaux : `niveaux_regles_du_calcul` avec le calcul qui a produit
    la version (aucun pour une version de référence : chaque règle à son
    niveau). Lève `ErreurIntrouvable` si la version n'existe pas.
    """
    version = session.get(VersionEdt, version_id) if identifiant_valide(version_id) else None
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps {version_id} n'existe pas.")
    semestre = session.get(Semestre, version.semestre_id)
    if semestre is None:
        raise ErreurIntrouvable(f"Le semestre {version.semestre_id} n'existe pas.")
    actuelles = [
        RegleActuelle(identifiant, niveau_regle, modifiee_le)
        for identifiant, niveau_regle, modifiee_le in session.execute(
            select(Regle.id, Regle.niveau, Regle.modifie_le).where(
                Regle.annee_scolaire_id == semestre.annee_scolaire_id,
                Regle.etat == EtatRegle.ACTIVE,
            )
        )
    ]
    calcul = None if version.calcul_id is None else session.get(Calcul, version.calcul_id)
    if version.calcul_id is not None and calcul is None:
        journal.warning(
            "Version %d : le calcul %d est introuvable ; règles à leur propre niveau",
            version.id,
            version.calcul_id,
        )
    journal.debug(
        "Version %d : niveaux des règles %s",
        version.id,
        "sans calcul" if calcul is None else f"du calcul {calcul.id}",
    )
    return niveaux_regles_du_calcul(
        None if calcul is None else calcul.parametres,
        actuelles,
        None if calcul is None else calcul.demarre_le,
    )


def _resume(niveaux: Niveaux) -> str:
    """« 15 nécessaire(s), 3 important(s), 1 facultatif(s) » pour le journal."""
    return ", ".join(
        f"{sum(1 for n in niveaux.values() if n is membre)} {membre.libelle.lower()}(s)"
        for membre in Niveau
    )


__all__ = [
    "CLE_NIVEAUX",
    "CLE_REGLES",
    "RegleActuelle",
    "niveaux_de_version",
    "niveaux_du_calcul",
    "niveaux_regles_de_version",
    "niveaux_regles_du_calcul",
]
