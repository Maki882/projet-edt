"""Contrat du solveur : options, résultat et signature du moteur.

Un moteur est une fonction pure du point de vue de l'application :

    resoudre(instantane, options, arret=None) -> ResultatSolveur

- il ne lit que l'instantané (jamais la base) et n'écrit rien ;
- il respecte toutes les contraintes du catalogue sauf celles de
  `options.contraintes_desactivees`, et n'en abandonne jamais une de lui-même ;
- il place **toutes** les séances ou n'en place aucune : jamais de solution
  partielle (`placements` est vide hors statut `SOLUTION`) ;
- il surveille `arret` (un `threading.Event`) et rend la main rapidement
  avec le statut `ARRETE` quand il est levé ;
- il ne dépasse pas `options.temps_limite_s` ;
- il journalise avec `logging` : tout ce qui est émis dans le fil du calcul
  arrive dans le journal dédié (voir `edt.journal.journal_calcul_courant`
  pour les rappels exécutés dans un autre fil).

L'orchestration (fil d'arrière-plan, suivi, enregistrement de la version)
est dans `edt.services.generation` et ne dépend pas du moteur.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from edt.domaine.contraintes import CodeContrainte, normaliser_codes
from edt.domaine.instantane import Instantane, Placement
from edt.domaine.parametres import Parametres
from edt.erreurs import ErreurDonneesInvalides


class StatutSolveur(StrEnum):
    """Issue d'une résolution."""

    SOLUTION = "solution"
    """Toutes les séances sont placées et toutes les contraintes actives respectées."""
    INFAISABLE = "infaisable"
    """Il est prouvé qu'aucune solution n'existe avec les contraintes actives."""
    TEMPS_DEPASSE = "temps_depasse"
    """Aucune solution trouvée dans le temps imparti, sans preuve d'impossibilité."""
    ARRETE = "arrete"
    """La recherche a été interrompue à la demande de l'utilisateur."""


@dataclass(frozen=True)
class OptionsSolveur:
    """Réglages d'un calcul.

    - `temps_limite_s` : durée maximale de la recherche ;
    - `contraintes_desactivees` : codes du catalogue que l'utilisateur a
      décochés (tracés dans le résultat) ;
    - `optimiser` : minimiser l'objectif de qualité au lieu de s'arrêter à la
      première solution valide ;
    - `graine` : graine aléatoire (une autre graine donne une autre solution) ;
    - `nb_workers` : fils de calcul ; 0 laisse le moteur choisir, 1 avec une
      graine fixe donne un résultat reproductible (mode déterministe des tests) ;
    - `utiliser_indices` : partir des placements de la version précédente
      (`SeanceAPlacer.indice`) ;
    - `tolerer_violations_heritees` : accepter que des séances verrouillées
      violent déjà entre elles une contrainte, au lieu de refuser le calcul ;
    - `arret_sur_stagnation` : avec `optimiser`, laisser l'amélioration
      s'arrêter d'elle-même quand elle ne progresse plus, sans attendre le
      temps limite. Ce critère lit l'horloge : le désactiver quand on veut un
      résultat strictement reproductible d'une exécution à l'autre.
    """

    temps_limite_s: float = 60.0
    contraintes_desactivees: frozenset[CodeContrainte] = frozenset()
    optimiser: bool = False
    graine: int = 0
    nb_workers: int = 0
    utiliser_indices: bool = True
    tolerer_violations_heritees: bool = False
    arret_sur_stagnation: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "contraintes_desactivees", normaliser_codes(self.contraintes_desactivees)
        )
        if self.temps_limite_s <= 0:
            raise ErreurDonneesInvalides("Le temps limite doit être strictement positif.")
        if self.nb_workers < 0:
            raise ErreurDonneesInvalides("Le nombre de fils de calcul ne peut pas être négatif.")

    @classmethod
    def depuis_parametres(
        cls,
        parametres: Parametres,
        *,
        contraintes_desactivees: Iterable[str] = (),
        relance_longue: bool = False,
        graine: int = 0,
        tolerer_violations_heritees: bool = False,
    ) -> OptionsSolveur:
        """Options par défaut d'un calcul, tirées des paramètres globaux."""
        return cls(
            temps_limite_s=float(
                parametres.temps_limite_long_s if relance_longue else parametres.temps_limite_s
            ),
            contraintes_desactivees=normaliser_codes(contraintes_desactivees),
            optimiser=parametres.objectif_actif,
            graine=graine,
            nb_workers=parametres.nb_workers,
            tolerer_violations_heritees=tolerer_violations_heritees,
        )

    def vers_dict(self) -> dict[str, object]:
        """Représentation JSON des options (enregistrée avec le calcul)."""
        return {
            "temps_limite_s": self.temps_limite_s,
            "contraintes_desactivees": sorted(self.contraintes_desactivees),
            "optimiser": self.optimiser,
            "graine": self.graine,
            "nb_workers": self.nb_workers,
            "utiliser_indices": self.utiliser_indices,
            "tolerer_violations_heritees": self.tolerer_violations_heritees,
            "arret_sur_stagnation": self.arret_sur_stagnation,
        }


@dataclass(frozen=True)
class ResultatSolveur:
    """Résultat d'une résolution.

    - `statut` : voir `StatutSolveur` ;
    - `placements` : un placement par séance de l'instantané si `SOLUTION`,
      vide sinon ;
    - `contraintes_desactivees` : rappel des contraintes ignorées à la
      demande de l'utilisateur ;
    - `message` : phrase française pour l'utilisateur (raison de l'échec,
      résumé du succès) ;
    - `avertissements` : pré-vérifications et remarques à afficher ;
    - `statistiques` : mesures sérialisables en JSON (durée, nombre de
      séances, valeur de l'objectif...).
    """

    statut: StatutSolveur
    placements: tuple[Placement, ...] = ()
    contraintes_desactivees: frozenset[CodeContrainte] = frozenset()
    message: str = ""
    avertissements: tuple[str, ...] = ()
    statistiques: Mapping[str, int | float | str | bool] = field(default_factory=dict)

    @property
    def reussi(self) -> bool:
        """Vrai si une solution complète a été trouvée."""
        return self.statut is StatutSolveur.SOLUTION


Moteur = Callable[[Instantane, OptionsSolveur, threading.Event | None], ResultatSolveur]
"""Signature d'un moteur de résolution (celle de `edt.solveur.resoudre`)."""
