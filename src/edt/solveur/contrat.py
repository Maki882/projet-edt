"""Contrat du solveur : options, résultat et signature du moteur.

Un moteur est une fonction pure du point de vue de l'application :

    resoudre(instantane, options, arret=None) -> ResultatSolveur

- il ne lit que l'instantané (jamais la base) et n'écrit rien ;
- il respecte toutes les contraintes présentes dans `options.niveaux` (une
  clé absente est une contrainte ignorée, ce que `options.contraintes_desactivees`
  répète), et n'en abandonne jamais une de lui-même ;
- il place **toutes** les séances ou n'en place aucune : jamais de solution
  partielle (`placements` est vide hors statut `SOLUTION`) ;
- il surveille `arret` (un `threading.Event`) et rend la main rapidement
  avec le statut `ARRETE` quand il est levé ;
- il ne dépasse pas `options.temps_limite_s` (à l'affectation finale des
  salles près, une seconde ou deux, quand il vient d'améliorer une solution) ;
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

from edt.domaine.contraintes import (
    CATALOGUE,
    CodeContrainte,
    Niveau,
    Niveaux,
    contrainte,
    niveau,
    niveaux_depuis_desactivees,
    niveaux_par_defaut,
    normaliser_codes,
)
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
    - `niveaux` : niveau retenu pour chaque contrainte (`edt.domaine.contraintes`) ;
      dictionnaire **complet** attendu, une clé absente étant une contrainte
      ignorée (`normaliser_niveaux` complète un choix partiel). C'est la seule
      vérité : `contraintes_desactivees` en est déduit ;
    - `contraintes_desactivees` : codes ignorés, c'est-à-dire absents de
      `niveaux`. Donné seul (sans `niveaux`), il sert à construire `niveaux`
      comme autrefois : niveaux par défaut sans ces codes ; donné avec
      `niveaux`, il en retire ces codes ;
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
      résultat strictement reproductible d'une exécution à l'autre ;
    - `niveaux_regles` : niveau retenu pour chaque règle personnalisée
      (identifiant de règle → niveau) ; une règle active absente du
      dictionnaire est ignorée pour ce calcul. `None` (défaut) : chaque règle
      active à son propre niveau.
    """

    temps_limite_s: float = 60.0
    contraintes_desactivees: frozenset[CodeContrainte] = frozenset()
    optimiser: bool = False
    graine: int = 0
    nb_workers: int = 0
    utiliser_indices: bool = True
    tolerer_violations_heritees: bool = False
    arret_sur_stagnation: bool = True
    niveaux: Niveaux = field(default_factory=niveaux_par_defaut)
    niveaux_regles: dict[int, Niveau] | None = None

    def __post_init__(self) -> None:
        desactivees = normaliser_codes(self.contraintes_desactivees)
        niveaux = _niveaux_types(self.niveaux)
        if self.niveaux_regles is not None:
            object.__setattr__(self, "niveaux_regles", _niveaux_regles_types(self.niveaux_regles))
        if desactivees and niveaux == niveaux_par_defaut():
            niveaux = niveaux_depuis_desactivees(desactivees)
        elif desactivees:
            # Les deux sont donnés : les codes ignorés le sont, quel que soit leur niveau.
            niveaux = {code: n for code, n in niveaux.items() if code not in desactivees}
        object.__setattr__(self, "niveaux", niveaux)
        object.__setattr__(self, "contraintes_desactivees", frozenset(CATALOGUE) - niveaux.keys())
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
        niveaux: Mapping[str, str | Niveau] | None = None,
        relance_longue: bool = False,
        graine: int = 0,
        tolerer_violations_heritees: bool = False,
        niveaux_regles: Mapping[int, str | Niveau] | None = None,
    ) -> OptionsSolveur:
        """Options par défaut d'un calcul, tirées des paramètres globaux.

        `niveaux`, s'il est donné, prime sur `contraintes_desactivees`.
        """
        return cls(
            temps_limite_s=float(
                parametres.temps_limite_long_s if relance_longue else parametres.temps_limite_s
            ),
            contraintes_desactivees=normaliser_codes(contraintes_desactivees),
            niveaux=dict(niveaux) if niveaux is not None else niveaux_par_defaut(),
            optimiser=parametres.objectif_actif,
            graine=graine,
            nb_workers=parametres.nb_workers,
            tolerer_violations_heritees=tolerer_violations_heritees,
            niveaux_regles=dict(niveaux_regles) if niveaux_regles is not None else None,
        )

    @staticmethod
    def lire_niveaux(
        parametres: Mapping[str, object], contraintes_desactivees: Iterable[str] = ()
    ) -> Niveaux:
        """Niveaux d'un calcul enregistré, inverse de `vers_dict` pour cette clé.

        `parametres["niveaux"]` fait foi, les codes qui en manquent prenant
        leur niveau par défaut ; les contraintes ignorées sont celles de
        `contraintes_desactivees` (la colonne du calcul), retirées. Un calcul
        enregistré avant les niveaux n'a que ses contraintes désactivées :
        niveaux par défaut sans ces codes. Même lecture que
        `edt.services.niveaux.niveaux_du_calcul`. Lève `ErreurDonneesInvalides`
        si les niveaux ne sont pas un dictionnaire, ou sur un code ou un
        niveau inconnu.
        """
        retirees = normaliser_codes(contraintes_desactivees)
        choix = parametres.get("niveaux")
        if choix is None:
            return niveaux_depuis_desactivees(retirees)
        if not isinstance(choix, Mapping):
            raise ErreurDonneesInvalides(
                "Les niveaux enregistrés avec le calcul sont illisibles : un dictionnaire "
                f"« code : niveau » est attendu (lu : {type(choix).__name__})."
            )
        niveaux = {**niveaux_par_defaut(), **_niveaux_types(choix)}
        return {code: n for code, n in niveaux.items() if code not in retirees}

    @staticmethod
    def lire_niveaux_regles(parametres: Mapping[str, object]) -> dict[int, Niveau] | None:
        """Niveaux des règles personnalisées d'un calcul enregistré, inverse de `vers_dict`.

        `parametres["niveaux_regles"]` : dictionnaire « identifiant : niveau »
        (clés enregistrées en texte par le JSON) ; absent ou `None`, chaque
        règle active vaut à son niveau. Lève `ErreurDonneesInvalides` si la
        valeur est illisible.
        """
        choix = parametres.get("niveaux_regles")
        if choix is None:
            return None
        if not isinstance(choix, Mapping):
            raise ErreurDonneesInvalides(
                "Les niveaux des règles enregistrés avec le calcul sont illisibles : un "
                f"dictionnaire « identifiant : niveau » est attendu (lu : {type(choix).__name__})."
            )
        return _niveaux_regles_types(choix)

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
            "niveaux": {code.value: n.value for code, n in sorted(self.niveaux.items())},
            "niveaux_regles": (
                None
                if self.niveaux_regles is None
                else {str(i): n.value for i, n in sorted(self.niveaux_regles.items())}
            ),
        }


def _niveaux_regles_types(choix: Mapping[object, object]) -> dict[int, Niveau]:
    """Niveaux de règles validés et typés : `{identifiant: Niveau}`.

    Lève `ErreurDonneesInvalides` pour un identifiant qui n'est pas un entier
    ou un niveau inconnu.
    """
    niveaux: dict[int, Niveau] = {}
    for cle, valeur in choix.items():
        try:
            identifiant = int(cle)
        except (TypeError, ValueError) as erreur:
            raise ErreurDonneesInvalides(
                f"Identifiant de règle invalide dans les niveaux des règles : {cle!r}."
            ) from erreur
        niveaux[identifiant] = valeur if isinstance(valeur, Niveau) else niveau(valeur)
    return niveaux


def _niveaux_types(choix: Mapping[str, str | Niveau]) -> Niveaux:
    """Niveaux validés et typés ; une clé absente reste absente (contrainte ignorée).

    Lève `ErreurDonneesInvalides` pour un code ou un niveau inconnu, et pour un
    niveau autre que « nécessaire » sur une contrainte qui définit le problème.
    """
    niveaux: Niveaux = {}
    for code, valeur in choix.items():
        entree = contrainte(code)
        retenu = valeur if isinstance(valeur, Niveau) else niveau(valeur)
        if not entree.modifiable and retenu is not Niveau.NECESSAIRE:
            raise ErreurDonneesInvalides(
                f"La contrainte « {entree.libelle} » ({entree.code}) définit le problème : elle "
                f"reste nécessaire, son niveau ne peut pas être « {retenu.libelle} »."
            )
        niveaux[entree.code] = retenu
    return niveaux


@dataclass(frozen=True)
class ResultatSolveur:
    """Résultat d'une résolution.

    - `statut` : voir `StatutSolveur` ;
    - `placements` : un placement par séance de l'instantané si `SOLUTION`,
      vide sinon ;
    - `contraintes_desactivees` : rappel des contraintes ignorées à la
      demande de l'utilisateur ;
    - `niveaux` : rappel du niveau retenu pour chaque contrainte présente ;
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
    niveaux: Niveaux = field(default_factory=dict)
    niveaux_regles: dict[int, Niveau] = field(default_factory=dict)
    avertissements: tuple[str, ...] = ()
    statistiques: Mapping[str, int | float | str | bool] = field(default_factory=dict)

    @property
    def reussi(self) -> bool:
        """Vrai si une solution complète a été trouvée."""
        return self.statut is StatutSolveur.SOLUTION


Moteur = Callable[[Instantane, OptionsSolveur, threading.Event | None], ResultatSolveur]
"""Signature d'un moteur de résolution (celle de `edt.solveur.resoudre`)."""
