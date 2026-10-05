"""Rapport d'une génération de jeu fictif : compteurs, charges, besoins écartés, témoin.

`RapportJeu` est figé et sérialisable (types simples). Il porte l'emploi du
temps témoin sous la forme `identifiant de séance -> (jour, début, fin,
identifiant de salle)`, l'identifiant de séance étant celui du dépliage
(`edt.domaine.instantane.id_seance`) : un test d'intégration peut le
soumettre tel quel au vérificateur (`RapportJeu.placements`).

Taux de charge (mesurés sur le témoin, semaine par semaine) :

- d'un **demi-groupe** (le niveau d'un étudiant) : minutes de la semaine
  occupées par une séance qui le concerne (la sienne, celle d'un groupe en
  conflit avec lui), rapportées aux minutes de créneaux du lundi au
  vendredi ; moyenne sur les semaines où il a cours, maximum sur toutes ;
- d'un **enseignant** : même mesure sur ses propres séances ;
- d'une **salle** : même mesure sur ses séances, moyenne sur les semaines de
  cours du semestre ; regroupées par type de salle.

Le témoin est aussi soumis au vérificateur officiel (`edt.verification`) :
le rapport compte ses anomalies par gravité (aucune erreur attendue ; des
informations pour les créneaux imposés hors grille) et garde les
indicateurs de qualité du vérificateur, point de comparaison avec la
solution du solveur.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import NamedTuple

from edt.domaine.instantane import Placement
from edt.erreurs import ErreurIntrouvable
from edt.synthese.configuration import ConfigurationJeu
from edt.verification import Indicateurs


class PlaceTemoin(NamedTuple):
    """Place d'une séance dans le témoin : (jour, début, fin, identifiant de salle)."""

    jour: int
    debut: int
    fin: int
    salle_id: int


@dataclass(frozen=True)
class TauxCharge:
    """Taux de charge d'une famille de ressources (valeurs entre 0 et 1)."""

    moyen: float
    maximal: float
    element_max: str

    def __str__(self) -> str:
        if not self.element_max:
            return "aucune séance"
        return f"moyenne {self.moyen:.0%}, maximum {self.maximal:.0%} ({self.element_max})"


@dataclass(frozen=True)
class ChargeSemestre:
    """Charges mesurées sur le témoin d'un semestre."""

    semestre: str
    nb_seances: int
    nb_semaines: int
    groupes: TauxCharge
    enseignants: TauxCharge
    types_salle: dict[str, TauxCharge]


@dataclass(frozen=True)
class BesoinEcarte:
    """Besoin que le générateur a renoncé à créer, avec la raison."""

    semestre: str
    libelle: str
    raison: str


@dataclass(frozen=True)
class SeancePlacee:
    """Séance du témoin avec ce qu'il faut pour mesurer les charges."""

    seance_id: str
    jour: int
    debut: int
    fin: int
    masque: int
    salle_id: int
    groupes: tuple[int, ...]
    enseignants: tuple[str, ...]


@dataclass(frozen=True)
class RapportJeu:
    """Ce que `generer_jeu` a produit.

    - `compteurs` : nombre de lignes par table après génération ;
    - `besoins_par_mode`, `besoins_par_type` : répartition des besoins créés ;
    - `seances_par_semestre` : séances hebdomadaires à placer par semestre ;
    - `charges` : taux de charge par semestre ;
    - `besoins_ecartes` : besoins non créés faute de place dans le témoin ;
    - `temoin_par_semestre` : code du semestre -> identifiant de séance -> place ;
    - `semestres` : code -> identifiant en base ;
    - `versions_reference` : code -> identifiant de la version « référence »
      créée à partir du témoin (vide sans l'option) ;
    - `nb_recrutements` : enseignants ajoutés au vivier pendant le témoin ;
    - `anomalies_temoin` : code -> gravité -> nombre d'anomalies relevées par
      le vérificateur sur le témoin ;
    - `indicateurs` : code -> indicateurs du vérificateur sur le témoin ;
    - `duree_s` : durée de la génération (seule valeur non déterministe).
    """

    configuration: ConfigurationJeu
    graine: int
    annee: str
    compteurs: dict[str, int]
    besoins_par_mode: dict[str, int]
    besoins_par_type: dict[str, int]
    seances_par_semestre: dict[str, int]
    charges: tuple[ChargeSemestre, ...]
    besoins_ecartes: tuple[BesoinEcarte, ...]
    temoin_par_semestre: dict[str, dict[str, PlaceTemoin]]
    semestres: dict[str, int]
    versions_reference: dict[str, int]
    nb_recrutements: int
    anomalies_temoin: dict[str, dict[str, int]]
    indicateurs: dict[str, Indicateurs]
    duree_s: float

    @property
    def temoin(self) -> dict[str, PlaceTemoin]:
        """Témoin de tous les semestres : identifiant de séance -> place."""
        return {
            seance: place
            for code in sorted(self.temoin_par_semestre)
            for seance, place in self.temoin_par_semestre[code].items()
        }

    @property
    def nb_besoins(self) -> int:
        """Nombre de besoins créés."""
        return sum(self.besoins_par_mode.values())

    def placements(self, semestre: str) -> tuple[Placement, ...]:
        """Le témoin du semestre `semestre` (code) au format du vérificateur."""
        if semestre not in self.temoin_par_semestre:
            raise ErreurIntrouvable(
                f"Le jeu ne contient pas de semestre {semestre!r} "
                f"(semestres générés : {', '.join(sorted(self.temoin_par_semestre))})."
            )
        return tuple(
            Placement(seance, place.jour, place.debut, place.fin, place.salle_id)
            for seance, place in self.temoin_par_semestre[semestre].items()
        )

    def resume(self) -> str:
        """Résumé lisible pour la ligne de commande."""
        c = self.compteurs
        lignes = [
            f"Jeu fictif « {self.configuration.nom} » (graine {self.graine}), "
            f"année {self.annee}, généré en {self.duree_s:.1f} s",
            f"  Lieux : {c.get('campus', 0)} sites, {c.get('salle', 0)} salles, "
            f"{c.get('trajet', 0)} trajets",
            f"  Personnes : {c.get('groupe', 0)} groupes, {c.get('etudiant', 0)} étudiants "
            f"({c.get('inscription', 0)} inscriptions), {c.get('enseignant', 0)} enseignants "
            f"(dont {self.nb_recrutements} recrutés pendant le témoin)",
            f"  Enseignement : {c.get('matiere', 0)} matières, {self.nb_besoins} besoins ("
            + ", ".join(f"{mode} {n}" for mode, n in sorted(self.besoins_par_mode.items()))
            + ")",
            "  Types de séance : "
            + ", ".join(f"{code} {n}" for code, n in sorted(self.besoins_par_type.items())),
            f"  Calendrier : {c.get('jour_ferme', 0)} fermetures, "
            f"{c.get('indisponibilite', 0)} indisponibilités",
        ]
        for charge in self.charges:
            lignes.append(
                f"  Semestre {charge.semestre} : {charge.nb_seances} séances hebdomadaires "
                f"sur {charge.nb_semaines} semaines"
            )
            lignes.append(f"    charge des demi-groupes : {charge.groupes}")
            lignes.append(f"    charge des enseignants : {charge.enseignants}")
            for type_salle, taux in sorted(charge.types_salle.items()):
                lignes.append(f"    salles {type_salle} : {taux}")
            anomalies = self.anomalies_temoin.get(charge.semestre, {})
            lignes.append(
                "    vérificateur sur le témoin : "
                + ", ".join(
                    f"{anomalies.get(g, 0)} {g}(s)"
                    for g in ("erreur", "avertissement", "information")
                )
            )
            indicateurs = self.indicateurs.get(charge.semestre)
            if indicateurs is not None:
                lignes.append(f"    indicateurs du témoin : {_texte_indicateurs(indicateurs)}")
        if self.versions_reference:
            lignes.append(
                "  Versions de référence (témoin) : "
                + ", ".join(
                    f"{code} n°{vid}" for code, vid in sorted(self.versions_reference.items())
                )
            )
        lignes.append(f"  Besoins écartés faute de place : {len(self.besoins_ecartes)}")
        for ecarte in self.besoins_ecartes[:10]:
            lignes.append(f"    - [{ecarte.semestre}] {ecarte.libelle} : {ecarte.raison}")
        if len(self.besoins_ecartes) > 10:
            lignes.append(f"    ... et {len(self.besoins_ecartes) - 10} autre(s)")
        return "\n".join(lignes)


def _texte_indicateurs(ind: Indicateurs) -> str:
    """Les indicateurs principaux, en une ligne (totaux sur le semestre)."""
    if ind.pourcentage_utilisation_salles is None:
        salles = "-"
    else:
        salles = f"{ind.pourcentage_utilisation_salles:.0f} %"
    return (
        f"salles utilisées {salles}, trous {ind.trous_creneaux} créneaux, "
        f"changements de campus {ind.changements_campus_groupes} (groupes) / "
        f"{ind.changements_campus_enseignants} (enseignants), samedi {ind.seances_samedi}, "
        f"créneaux extrêmes {ind.seances_creneaux_extremes}, objectif {ind.valeur_objectif} "
        f"(sur {ind.nb_semaines} semaines)"
    )


# --------------------------------------------------------------------------- #
# Calcul des charges
# --------------------------------------------------------------------------- #


def calculer_charge(
    semestre: str,
    nb_semaines: int,
    capacite: int,
    seances: Iterable[SeancePlacee],
    groupes_suivis: Mapping[int, str],
    lies: Mapping[int, frozenset[int]],
    salles: Mapping[int, tuple[str, str]],
) -> ChargeSemestre:
    """Mesure les charges d'un semestre sur son témoin.

    - `capacite` : minutes de créneaux du lundi au vendredi ;
    - `groupes_suivis` : identifiant -> code des groupes mesurés (les demi-groupes) ;
    - `lies` : groupe -> lui-même et les groupes en conflit avec lui ;
    - `salles` : identifiant -> (code, type de salle) de toutes les salles.
    """
    seances = list(seances)
    semaines = range(1, nb_semaines + 1)
    semaines_de_cours = [w for w in semaines if any(s.masque >> w & 1 for s in seances)]

    par_groupe: dict[int, list[SeancePlacee]] = {g: [] for g in groupes_suivis}
    par_enseignant: dict[str, list[SeancePlacee]] = {}
    par_salle: dict[int, list[SeancePlacee]] = {salle: [] for salle in salles}
    for seance in seances:
        concernes = {g for h in seance.groupes for g in lies.get(h, (h,)) if g in groupes_suivis}
        for groupe in concernes:
            par_groupe[groupe].append(seance)
        for enseignant in seance.enseignants:
            par_enseignant.setdefault(enseignant, []).append(seance)
        par_salle.setdefault(seance.salle_id, []).append(seance)

    def taux(liste: list[SeancePlacee], toutes_semaines: bool) -> tuple[float, float]:
        valeurs = [_minutes_semaine(liste, w) / capacite for w in semaines_de_cours]
        actives = valeurs if toutes_semaines else [v for v in valeurs if v > 0]
        if not actives:
            return 0.0, 0.0
        return sum(actives) / len(actives), max(actives)

    groupes = _synthese(
        [(*taux(liste, False), groupes_suivis[g]) for g, liste in sorted(par_groupe.items())]
    )
    enseignants = _synthese(
        [(*taux(liste, False), nom) for nom, liste in sorted(par_enseignant.items())]
    )
    par_type: dict[str, list[tuple[float, float, str]]] = {}
    for salle_id, liste in sorted(par_salle.items()):
        code, type_salle = salles[salle_id]
        par_type.setdefault(type_salle, []).append((*taux(liste, True), code))
    return ChargeSemestre(
        semestre=semestre,
        nb_seances=len(seances),
        nb_semaines=len(semaines_de_cours),
        groupes=groupes,
        enseignants=enseignants,
        types_salle={type_salle: _synthese(m) for type_salle, m in sorted(par_type.items())},
    )


def _minutes_semaine(seances: list[SeancePlacee], semaine: int) -> int:
    """Minutes occupées la semaine `semaine` (union des plages, jour par jour)."""
    par_jour: dict[int, list[tuple[int, int]]] = {}
    for seance in seances:
        if seance.masque >> semaine & 1:
            par_jour.setdefault(seance.jour, []).append((seance.debut, seance.fin))
    total = 0
    for plages in par_jour.values():
        plages.sort()
        debut_courant, fin_courante = plages[0]
        for debut, fin in plages[1:]:
            if debut > fin_courante:
                total += fin_courante - debut_courant
                debut_courant, fin_courante = debut, fin
            else:
                fin_courante = max(fin_courante, fin)
        total += fin_courante - debut_courant
    return total


def _synthese(mesures: list[tuple[float, float, str]]) -> TauxCharge:
    if not mesures:
        return TauxCharge(0.0, 0.0, "")
    moyen = sum(m[0] for m in mesures) / len(mesures)
    _, maximal, nom = max(mesures, key=lambda m: (m[1], m[2]))
    return TauxCharge(round(moyen, 4), round(maximal, 4), nom)
