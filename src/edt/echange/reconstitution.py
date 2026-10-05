"""Import d'un emploi du temps existant : des séances lues aux besoins, à la semaine type, à une version.

Entrée : les `SeanceLue` produites par un lecteur (tableau plat, iCal...).
Trois modes (`ModeImport`) :

- **référence** : crée les besoins et une version d'origine « référence »
  (inactive, pour comparaison) avec sa semaine type et ses séances datées ;
- **verrous** : crée les besoins et une version active dont les séances
  hebdomadaires sont **verrouillées** : le générateur les gardera à leur place ;
- **à replacer** : crée seulement les besoins, que le générateur placera.

Règles décidées par l'équipe
----------------------------
- Séance annulée : non comptée (tracée dans le rapport). Séance déplacée :
  comptée à sa position finale. Sans statut : réalisée.
- Enseignant absent, « à recruter » ou inconnu : l'enseignant fictif propre à
  la matière (créé au besoin, exclu des contraintes d'enseignant).
- Salle vide ou « à définir » : aucune salle, le générateur choisira.
- Groupe inconnu (ou matière, type, salle inconnus) : la séance est mise de
  côté et listée, l'import continue.
- Événement spécial (type AUTRE : conférence, forum) : un besoin à part,
  figé (date, heure et salle imposées), jamais regroupé avec les cours.
- Lignes strictement identiques : fusionnées.
- Une occurrence issue d'une règle de récurrence qui tombe un jour fermé est
  retirée. Une séance hors grille ou hors règle entre telle quelle : le
  vérificateur la signalera.

Des séances datées à la semaine type
------------------------------------
1. Regroupement par (matière, type, groupes, enseignants, durée) : un groupe
   devient un besoin.
2. Volume : une seule séance datée -> `ponctuel` à sa date ; même nombre de
   séances chaque semaine ouverte du semestre (une semaine sans la séance est
   tolérée si elle contient un jour fermé pour ses groupes) -> `par_semaine` ;
   sinon -> `total` (nombre observé, maximum hebdomadaire observé, période du
   premier au dernier jour).
3. Semaine type : par promotion (racine de l'arbre des groupes), la semaine
   dont la disposition (besoin, jour, heure, salle) revient le plus souvent ;
   à égalité, la plus ancienne. L'option `semaine_type` impose une autre
   semaine. Chaque besoin y prend ses créneaux ; il complète avec ses créneaux
   les plus fréquents s'il lui en manque.
4. Deux besoins de même identifiant métier (même matière, type, groupes,
   mais enseignants ou durée différents) reçoivent des libellés distincts.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.domaine.depliage import semaines_ouvertes
from edt.domaine.enumerations import (
    JOURS,
    Gravite,
    ModeVolume,
    OrigineOccurrence,
    OrigineVersion,
    StatutEnseignant,
    StatutOccurrence,
)
from edt.domaine.heures import duree_vers_texte, minutes_vers_hhmm
from edt.domaine.instantane import Besoin as BesoinDomaine
from edt.domaine.instantane import Instantane
from edt.echange.donnees import LONGUEUR_SOURCE, Action, Contexte, Rejet
from edt.echange.format import StatutSeance
from edt.echange.rapport import Bilan, Signalement
from edt.echange.seances import ModeImport, SeanceLue
from edt.echange.valeurs import cle_code, cle_mot
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services.instantane import charger_instantane

journal = logging.getLogger(__name__)

CODE_TYPE_SPECIAL = "AUTRE"
ENSEIGNANT_ABSENT = frozenset(
    {"a_recruter", "a_definir", "inconnu", "intervenant_exterieur", "aucun", "non_attribue"}
)
"""Mentions qui désignent un enseignant non nommé (comparées sans casse ni accents)."""
SALLE_A_DEFINIR = frozenset({"a_definir", "a_determiner", "aucune", "sans_salle"})
"""Mentions qui désignent une salle laissée au générateur."""


class _Ecart(Exception):  # noqa: N818 - mise de côté d'une séance, convertie en signalement
    def __init__(self, colonne: str, message: str) -> None:
        super().__init__(message)
        self.colonne = colonne
        self.message = message


@dataclass
class SeanceRetenue:
    """Séance résolue : ses références sont des objets de la base."""

    source: SeanceLue
    date: dt.date
    debut: int
    fin: int
    matiere: m.Matiere
    type_seance: m.TypeSeance
    groupes: tuple[m.Groupe, ...]
    enseignants: tuple[m.Enseignant, ...]
    salle: m.Salle | None
    deplacee: bool
    datee: bool
    semaine: int

    @property
    def duree(self) -> int:
        return self.fin - self.debut

    @property
    def jour(self) -> int:
        return self.date.weekday()

    @property
    def special(self) -> bool:
        return cle_code(self.type_seance.code) == cle_code(CODE_TYPE_SPECIAL)

    @property
    def creneau(self) -> tuple[int, int, int | None]:
        return (self.jour, self.debut, self.salle.id if self.salle else None)

    def cle(self) -> tuple:
        """Clé de regroupement : (matière, type, groupes, enseignants, durée)."""
        return (
            self.matiere.id,
            self.type_seance.id,
            frozenset(g.id for g in self.groupes),
            frozenset(e.id for e in self.enseignants),
            self.duree,
        )


@dataclass
class BesoinPrevu:
    """Besoin reconstitué, avant et après son écriture en base."""

    seances: list[SeanceRetenue]
    special: bool
    mode: ModeVolume
    nombre: int
    max_par_semaine: int
    date_debut: dt.date | None = None
    date_fin: dt.date | None = None
    date_ponctuelle: dt.date | None = None
    jour_impose: int | None = None
    debut_impose: int | None = None
    salle_imposee: m.Salle | None = None
    libelle_base: str | None = None
    complements: tuple[str, ...] = ()
    niveau: int = 0
    creneaux: list[tuple[int, int, int | None]] = field(default_factory=list)
    besoin: m.Besoin | None = None
    action: Action | None = None

    @property
    def premiere(self) -> SeanceRetenue:
        return self.seances[0]

    @property
    def libelle(self) -> str | None:
        morceaux = [x for x in (self.libelle_base, *self.complements[: self.niveau]) if x]
        return " - ".join(morceaux)[:128] if morceaux else None

    def cle_metier(self) -> tuple:
        s = self.premiere
        return (
            s.matiere.id,
            s.type_seance.id,
            frozenset(g.id for g in s.groupes),
            cle_code(self.libelle or ""),
            self.date_ponctuelle,
        )

    @property
    def rangs(self) -> int:
        """Nombre de séances hebdomadaires que le besoin peut demander au plus."""
        if self.mode is ModeVolume.PAR_SEMAINE:
            return self.nombre
        if self.mode is ModeVolume.TOTAL:
            return self.max_par_semaine
        return 1

    @property
    def sources(self) -> set[int]:
        return {id(s.source) for s in self.seances}


@dataclass(frozen=True)
class ResultatReconstitution:
    """Ce que la reconstitution a créé : bilans, rapport, version, semaines types, besoins."""

    bilans: tuple[Bilan, ...]
    version_id: int | None
    semaines_types: tuple[str, ...]
    besoins: tuple[dict[str, Any], ...]


# --------------------------------------------------------------------------- #
# Résolution des références d'une séance
# --------------------------------------------------------------------------- #


class Resolveur:
    """Résout les références d'une séance selon les règles de l'équipe."""

    def __init__(
        self,
        session: Session,
        semestre: m.Semestre,
        instantane: Instantane,
        signalements: list[Signalement],
        bilan_fictifs: Bilan,
    ) -> None:
        self.session = session
        self.semestre = semestre
        self.instantane = instantane
        self.contexte = Contexte(session)
        self.signalements = signalements
        self.bilan_fictifs = bilan_fictifs
        self._avertis: set[tuple] = set()

    def resoudre(self, seance: SeanceLue) -> list[SeanceRetenue]:
        """Séances retenues pour une séance lue (plusieurs pour une séance hebdomadaire).

        Lève `_Ecart` si la séance doit être mise de côté. Renvoie une liste
        vide pour une occurrence de récurrence qui tombe un jour fermé.
        """
        annee = self.semestre.annee
        if not seance.matiere:
            raise _Ecart("matiere", "Matière absente : séance mise de côté.")
        if not seance.type:
            raise _Ecart("type", "Type de séance absent : séance mise de côté.")
        if not seance.groupes:
            raise _Ecart("groupes", "Aucun groupe : séance mise de côté.")
        inconnus = [
            code
            for code in seance.groupes
            if (annee.id, cle_code(code)) not in self.contexte.groupes
        ]
        if inconnus:
            raise _Ecart(
                "groupes",
                f"Groupe(s) inconnu(s) pour l'année {annee.libelle} : {', '.join(inconnus)}. "
                "Séance mise de côté (importer d'abord les groupes).",
            )
        groupes = tuple(
            sorted(
                {self.contexte.groupes[(annee.id, cle_code(code))] for code in seance.groupes},
                key=lambda g: g.code,
            )
        )
        try:
            type_seance = self.contexte.type_seance(seance.type, "type")
            salle = None
            if seance.salle and cle_mot(seance.salle) not in SALLE_A_DEFINIR:
                salle = self.contexte.salle(seance.salle, "salle")
        except Rejet as rejet:
            raise _Ecart(rejet.colonne, rejet.message + " Séance mise de côté.") from None
        matiere = self._matiere(seance.matiere, groupes)
        enseignants = self._enseignants(seance, matiere)
        if seance.statut is StatutSeance.A_PROGRAMMER:
            raise _Ecart(
                "statut", "Séance « à programmer » : elle n'a pas de position, non importée."
            )
        deplacee = seance.statut is StatutSeance.DEPLACEE
        ids = frozenset(g.id for g in groupes)

        def retenue(jour: dt.date, datee: bool) -> SeanceRetenue:
            semaine = self.instantane.semaine_du(jour)
            return SeanceRetenue(
                seance,
                jour,
                seance.debut,
                seance.fin,
                matiere,
                type_seance,
                groupes,
                enseignants,
                salle,
                deplacee,
                datee,
                semaine.numero,
            )

        if seance.date is not None:
            if not self.semestre.date_debut <= seance.date <= self.semestre.date_fin:
                raise _Ecart(
                    "date",
                    f"Date hors du semestre ({self.semestre.libelle} : du "
                    f"{self.semestre.date_debut:%d/%m/%Y} au {self.semestre.date_fin:%d/%m/%Y}) : "
                    "séance mise de côté.",
                )
            if seance.recurrente and self._ferme(seance.date, ids):
                return []
            return [retenue(seance.date, datee=True)]

        ouvertes = semaines_ouvertes(
            _besoin_domaine(ids), self.instantane.semaines, self.instantane.jours_fermes
        )
        jours = [
            semaine.date_du(seance.jour)
            for semaine in ouvertes
            if self.semestre.date_debut <= semaine.date_du(seance.jour) <= self.semestre.date_fin
        ]
        if not jours:
            raise _Ecart("jour", "Aucune semaine de cours ouverte pour ces groupes ce jour-là.")
        return [retenue(jour, datee=False) for jour in jours]

    def _ferme(self, jour: dt.date, groupes: frozenset[int]) -> bool:
        return any(f.concerne(jour, groupes) for f in self.instantane.jours_fermes)

    def _matiere(self, code: str, groupes: tuple[m.Groupe, ...]) -> m.Matiere:
        candidates = [x for x in self.contexte.matieres if cle_code(x.code) == cle_code(code)]
        if not candidates:
            raise _Ecart(
                "matiere",
                f"Matière « {code} » inconnue : importer d'abord les matières (feuille matieres) ; "
                "séance mise de côté.",
            )
        if len(candidates) > 1:
            racines = {_racine(g).id for g in groupes}
            precises = [x for x in candidates if x.promotion_id in racines] or [
                x for x in candidates if x.promotion_id is None
            ]
            if len(precises) != 1:
                raise _Ecart(
                    "matiere",
                    f"Matière « {code} » ambiguë (plusieurs promotions) : séance mise de côté.",
                )
            candidates = precises
        return candidates[0]

    def _enseignants(self, seance: SeanceLue, matiere: m.Matiere) -> tuple[m.Enseignant, ...]:
        retenus: dict[int, m.Enseignant] = {}
        fictif = not seance.enseignants
        for identifiant in seance.enseignants:
            if cle_mot(identifiant) in ENSEIGNANT_ABSENT:
                fictif = True
                continue
            enseignant = self.contexte.enseignants.get(cle_code(identifiant))
            if enseignant is None:
                fictif = True
                cle = (seance.fichier, seance.ligne, cle_code(identifiant))
                if cle not in self._avertis:
                    self._avertis.add(cle)
                    self.signalements.append(
                        seance.signalement(
                            Gravite.AVERTISSEMENT,
                            f"Enseignant « {identifiant} » inconnu : remplacé par l'enseignant "
                            f"fictif de la matière {matiere.code}",
                            "enseignants",
                        )
                    )
                continue
            retenus[enseignant.id] = enseignant
        if fictif:
            enseignant = self._fictif(matiere, seance)
            retenus[enseignant.id] = enseignant
        return tuple(sorted(retenus.values(), key=lambda e: e.identifiant))

    def _fictif(self, matiere: m.Matiere, seance: SeanceLue) -> m.Enseignant:
        identifiant = f"FICTIF-{matiere.code}"
        if matiere.promotion is not None:
            identifiant += f"-{matiere.promotion.code}"
        identifiant = identifiant[:64]
        enseignant = self.contexte.enseignants.get(cle_code(identifiant))
        if enseignant is not None:
            return enseignant
        enseignant = m.Enseignant(
            identifiant=identifiant,
            nom=f"À recruter ({matiere.libelle})"[:128],
            statut=StatutEnseignant.VACATAIRE,
            fictif=True,
            source_fichier=seance.fichier[:LONGUEUR_SOURCE],
            source_ligne=seance.ligne,
        )
        self.session.add(enseignant)
        self.session.flush()
        self.contexte.enseignants[cle_code(identifiant)] = enseignant
        self.bilan_fictifs.ajouter("crees")
        self.signalements.append(
            seance.signalement(
                Gravite.INFORMATION,
                f"Enseignant fictif {identifiant} créé pour la matière {matiere.code} "
                "(enseignant absent ou inconnu)",
                "enseignants",
            )
        )
        return enseignant


def _racine(groupe: m.Groupe) -> m.Groupe:
    while groupe.parent is not None:
        groupe = groupe.parent
    return groupe


def _besoin_domaine(groupes: frozenset[int]) -> BesoinDomaine:
    """Besoin minimal du domaine, pour demander au dépliage les semaines ouvertes de ces groupes."""
    return BesoinDomaine(
        id=0,
        libelle="",
        matiere_id=0,
        matiere_code="",
        type_seance_id=0,
        type_seance_code="",
        samedi_autorise=False,
        duree=1,
        groupes=groupes,
        enseignants=frozenset(),
        effectif=0,
        accessibilite_requise=False,
    )


# --------------------------------------------------------------------------- #
# Reconstitution
# --------------------------------------------------------------------------- #


def reconstituer(
    session: Session,
    seances: Sequence[SeanceLue],
    mode: ModeImport,
    semestre_id: int,
    *,
    semaine_type: dt.date | None,
    fichiers: Sequence[str],
    bilan_seances: Bilan,
    signalements: list[Signalement],
) -> ResultatReconstitution:
    """Crée besoins, semaine type et version selon `mode` (une transaction ouverte est requise)."""
    if not mode.emploi_du_temps:
        raise ValueError(f"Mode {mode} : pas un import d'emploi du temps")
    semestre = session.get(m.Semestre, semestre_id)
    if semestre is None:
        raise ErreurIntrouvable(f"Le semestre {semestre_id} n'existe pas.")
    instantane = charger_instantane(session, semestre_id)
    semaine_imposee = None
    if semaine_type is not None:
        semaine_imposee = instantane.semaine_du(semaine_type)
        if semaine_imposee is None:
            raise ErreurDonneesInvalides(
                f"La semaine type demandée (date du {semaine_type:%d/%m/%Y}) est en dehors du "
                f"semestre {semestre.libelle}."
            )
    bilan_fictifs = Bilan("enseignants fictifs")
    resolveur = Resolveur(session, semestre, instantane, signalements, bilan_fictifs)

    bilan_seances.ajouter("lues", len(seances))
    retenues: list[SeanceRetenue] = []
    premieres: dict[tuple, SeanceLue] = {}
    for seance in seances:
        cle = seance.cle_doublon()
        if cle in premieres:
            bilan_seances.ajouter("fusionnes")
            premiere = premieres[cle]
            signalements.append(
                seance.signalement(
                    Gravite.INFORMATION,
                    f"Strictement identique à {premiere.fichier}, ligne {premiere.ligne} : fusionnée",
                )
            )
            continue
        premieres[cle] = seance
        if seance.statut is StatutSeance.ANNULEE:
            bilan_seances.ajouter("annulees")
            signalements.append(
                seance.signalement(Gravite.INFORMATION, "Séance annulée : non comptée", "statut")
            )
            continue
        try:
            resultat = resolveur.resoudre(seance)
        except _Ecart as ecart:
            bilan_seances.ajouter("ecartees")
            signalements.append(seance.signalement(Gravite.ERREUR, ecart.message, ecart.colonne))
            continue
        if not resultat:
            bilan_seances.ajouter("retirees")
            signalements.append(
                seance.signalement(
                    Gravite.INFORMATION,
                    "Occurrence d'une récurrence tombant un jour fermé du calendrier : retirée",
                    "date",
                )
            )
            continue
        retenues.extend(resultat)

    prevus = _prevoir(retenues, instantane)
    _libelles_distincts(prevus)
    semaines_types = _semaines_types(prevus, instantane, semaine_imposee)
    bilan_besoins = Bilan("besoins")
    ctx = resolveur.contexte
    for prevu in prevus:
        _ecrire_besoin(ctx, semestre, prevu, bilan_besoins, signalements)
    session.flush()
    ecrits = [p for p in prevus if p.besoin is not None]
    perdues = set().union(*(p.sources for p in prevus if p.besoin is None)) if prevus else set()
    bilan_seances.ajouter("ecartees", len(perdues))
    bilan_seances.ajouter(
        "retenues", len(set().union(*(p.sources for p in ecrits))) if ecrits else 0
    )

    bilans = [bilan_seances, bilan_fictifs, bilan_besoins]
    version_id = None
    if mode in (ModeImport.REFERENCE, ModeImport.VERROUS):
        if ecrits:
            version_id = _creer_version(
                session, semestre, mode, ecrits, fichiers, bilans, signalements
            )
        else:
            signalements.append(
                Signalement(Gravite.AVERTISSEMENT, "Aucune séance retenue : aucune version créée.")
            )
    journal.info(
        "Import d'emploi du temps (%s, semestre %s) : %s ; besoins : %s ; version %s",
        mode.value,
        semestre.libelle,
        bilan_seances.texte,
        bilan_besoins.texte,
        version_id,
    )
    return ResultatReconstitution(
        bilans=tuple(bilans),
        version_id=version_id,
        semaines_types=tuple(semaines_types if mode is not ModeImport.A_REPLACER else ()),
        besoins=tuple(_resume(p) for p in prevus),
    )


def _prevoir(retenues: list[SeanceRetenue], instantane: Instantane) -> list[BesoinPrevu]:
    """Regroupe les séances retenues en besoins (cours réguliers, puis événements spéciaux)."""
    groupes_cours: dict[tuple, list[SeanceRetenue]] = defaultdict(list)
    speciales: dict[tuple, list[SeanceRetenue]] = defaultdict(list)
    for seance in retenues:
        if seance.special:
            cle = (seance.date, seance.debut) if seance.datee else (id(seance.source),)
            speciales[(id(seance.source), *cle)].append(seance)
        else:
            groupes_cours[seance.cle()].append(seance)
    prevus = [_prevoir_cours(liste, instantane) for liste in groupes_cours.values()]
    prevus += [_prevoir_special(liste) for liste in speciales.values()]
    return prevus


def _prevoir_cours(seances: list[SeanceRetenue], instantane: Instantane) -> BesoinPrevu:
    par_semaine = Counter(s.semaine for s in seances)
    maximum = max(par_semaine.values())
    premiere = seances[0]
    complements = (
        f"{duree_vers_texte(premiere.duree)}, "
        + (", ".join(e.identifiant for e in premiere.enseignants) or "sans enseignant"),
        f"{JOURS[premiere.jour]} {minutes_vers_hhmm(premiere.debut)}",
    )
    if len(seances) == 1 and premiere.datee:
        return BesoinPrevu(
            seances,
            False,
            ModeVolume.PONCTUEL,
            1,
            1,
            date_ponctuelle=premiere.date,
            complements=complements,
        )
    ids = frozenset(g.id for g in premiere.groupes)
    ouvertes = {
        s.numero
        for s in semaines_ouvertes(
            _besoin_domaine(ids), instantane.semaines, instantane.jours_fermes
        )
    }
    manquantes = ouvertes - set(par_semaine)

    def excusee(numero: int) -> bool:
        semaine = instantane.semaine(numero)
        return any(
            f.concerne(semaine.date_du(jour), ids)
            for jour in range(5)
            for f in instantane.jours_fermes
        )

    reguliere = (
        set(par_semaine) <= ouvertes
        and len(set(par_semaine.values())) == 1
        and all(excusee(numero) for numero in manquantes)
    )
    if reguliere:
        return BesoinPrevu(
            seances, False, ModeVolume.PAR_SEMAINE, maximum, 1, complements=complements
        )
    dates = [s.date for s in seances]
    return BesoinPrevu(
        seances,
        False,
        ModeVolume.TOTAL,
        len(seances),
        maximum,
        date_debut=min(dates),
        date_fin=max(dates),
        complements=complements,
    )


def _prevoir_special(seances: list[SeanceRetenue]) -> BesoinPrevu:
    """Événement spécial : figé à sa date (ou chaque semaine à son jour), heure et salle imposées."""
    premiere = seances[0]
    commun = {
        "debut_impose": premiere.debut,
        "salle_imposee": premiere.salle,
        "libelle_base": premiere.source.libelle,
        "complements": (minutes_vers_hhmm(premiere.debut), f"ligne {premiere.source.ligne}"),
    }
    if premiere.datee:
        return BesoinPrevu(
            seances, True, ModeVolume.PONCTUEL, 1, 1, date_ponctuelle=premiere.date, **commun
        )
    return BesoinPrevu(
        seances, True, ModeVolume.PAR_SEMAINE, 1, 1, jour_impose=premiere.jour, **commun
    )


def _libelles_distincts(prevus: list[BesoinPrevu]) -> None:
    """Donne des libellés distincts aux besoins qui auraient le même identifiant métier."""
    for _ in range(3):
        par_cle: dict[tuple, list[BesoinPrevu]] = defaultdict(list)
        for prevu in prevus:
            par_cle[prevu.cle_metier()].append(prevu)
        collisions = [liste for liste in par_cle.values() if len(liste) > 1]
        if not collisions:
            return
        for liste in collisions:
            for prevu in liste:
                prevu.niveau += 1
    for numero, prevu in enumerate(prevus, start=1):  # dernier recours : un numéro
        if sum(1 for p in prevus if p.cle_metier() == prevu.cle_metier()) > 1:
            prevu.complements = (*prevu.complements, f"n° {numero}")
            prevu.niveau = len(prevu.complements)


def _semaines_types(prevus: list[BesoinPrevu], instantane: Instantane, imposee: Any) -> list[str]:
    """Choisit la semaine type de chaque promotion et en tire les créneaux de chaque besoin."""
    phrases = []
    par_promotion: dict[int, list[BesoinPrevu]] = defaultdict(list)
    racines: dict[int, m.Groupe] = {}
    for prevu in prevus:
        if prevu.special:
            s = prevu.premiere
            prevu.creneaux = [s.creneau]
            continue
        racine = _racine(min(prevu.premiere.groupes, key=lambda g: g.code))
        racines[racine.id] = racine
        par_promotion[racine.id].append(prevu)
    for racine_id, liste in sorted(par_promotion.items(), key=lambda x: racines[x[0]].code):
        dispositions: dict[int, set] = defaultdict(set)
        for indice, prevu in enumerate(liste):
            for s in prevu.seances:
                dispositions[s.semaine].add((indice, *s.creneau))
        if imposee is not None:
            numero = imposee.numero
            raison = "imposée"
        else:
            frequences = Counter(frozenset(d) for d in dispositions.values())
            numero = min(dispositions, key=lambda n: (-frequences[frozenset(dispositions[n])], n))
            raison = (
                f"disposition retrouvée {frequences[frozenset(dispositions[numero])]} fois sur "
                f"{len(dispositions)} semaine(s) de cours"
            )
        semaine = instantane.semaine(numero)
        phrases.append(
            f"Semaine type de {racines[racine_id].code} : semaine {numero} "
            f"(du lundi {semaine.lundi:%d/%m/%Y}), {raison}."
        )
        for prevu in liste:
            prevu.creneaux = _creneaux(prevu, numero)
    return phrases


def _creneaux(prevu: BesoinPrevu, semaine: int) -> list[tuple[int, int, int | None]]:
    """Créneaux du besoin : ceux de la semaine type, complétés par ses plus fréquents."""
    retenus = sorted({s.creneau for s in prevu.seances if s.semaine == semaine})[: prevu.rangs]
    frequences = Counter(s.creneau for s in prevu.seances)
    for creneau, _ in sorted(frequences.items(), key=lambda x: (-x[1], x[0][0], x[0][1])):
        if len(retenus) >= prevu.rangs:
            break
        if all((c[0], c[1]) != (creneau[0], creneau[1]) for c in retenus):
            retenus.append(creneau)
    return sorted(retenus, key=lambda c: (c[0], c[1]))


def _ecrire_besoin(
    ctx: Contexte,
    semestre: m.Semestre,
    prevu: BesoinPrevu,
    bilan: Bilan,
    signalements: list[Signalement],
) -> None:
    """Crée le besoin, ou met à jour celui de même identifiant métier (réimport sans doublon)."""
    s = prevu.premiere
    cle = (semestre.id, *prevu.cle_metier())
    index = ctx.besoins(semestre.id)
    existants = index.get(cle, [])
    if len(existants) > 1:
        bilan.ajouter("rejetes")
        signalements.append(
            s.source.signalement(
                Gravite.ERREUR,
                f"Plusieurs besoins existants (n° {', '.join(str(b.id) for b in existants)}) ont "
                f"le même identifiant que celui-ci ({len(prevu.sources)} séance(s)) : impossible "
                "de choisir, séances non importées",
            )
        )
        return
    champs = {
        "duree_min": s.duree,
        "mode_volume": prevu.mode,
        "nombre": prevu.nombre,
        "max_par_semaine": prevu.max_par_semaine,
        "date_debut": prevu.date_debut,
        "date_fin": prevu.date_fin,
    }
    if prevu.special:
        champs |= {
            "jour_impose": prevu.jour_impose,
            "debut_impose_min": prevu.debut_impose,
            "salle_imposee_id": prevu.salle_imposee.id if prevu.salle_imposee else None,
        }
    if not existants:
        besoin = m.Besoin(
            semestre_id=semestre.id,
            matiere_id=s.matiere.id,
            type_seance_id=s.type_seance.id,
            libelle=prevu.libelle,
            date_ponctuelle=prevu.date_ponctuelle,
            groupes=list(s.groupes),
            enseignants=list(s.enseignants),
            source_fichier=s.source.fichier[:LONGUEUR_SOURCE],
            source_ligne=s.source.ligne,
            **champs,
        )
        ctx.session.add(besoin)
        ctx.session.flush()
        index[cle] = [besoin]
        prevu.besoin, prevu.action = besoin, Action.CREE
    else:
        besoin = existants[0]
        change = False
        for attribut, valeur in champs.items():
            if getattr(besoin, attribut) != valeur:
                setattr(besoin, attribut, valeur)
                change = True
        if {e.id for e in besoin.enseignants} != {e.id for e in s.enseignants}:
            besoin.enseignants = list(s.enseignants)
            change = True
        if change:
            besoin.source_fichier = s.source.fichier[:LONGUEUR_SOURCE]
            besoin.source_ligne = s.source.ligne
        prevu.besoin, prevu.action = besoin, Action.MAJ if change else Action.INCHANGE
    bilan.ajouter(prevu.action.value)


def _creer_version(
    session: Session,
    semestre: m.Semestre,
    mode: ModeImport,
    prevus: list[BesoinPrevu],
    fichiers: Sequence[str],
    bilans: list[Bilan],
    signalements: list[Signalement],
) -> int:
    """Version « référence » (inactive) ou « verrous » (active, séances verrouillées)."""
    instantane = charger_instantane(session, semestre.id)
    ids = {p.besoin.id for p in prevus}
    for probleme in instantane.problemes_depliage:
        if probleme.besoin_id in ids:
            signalements.append(
                Signalement(
                    Gravite.AVERTISSEMENT, f"Besoin n° {probleme.besoin_id} : {probleme.message}"
                )
            )
    seances_a_placer = defaultdict(list)
    for seance in instantane.seances:
        if seance.besoin.id in ids:
            seances_a_placer[seance.besoin.id].append(seance)

    numero = 1 + (
        session.scalar(
            select(func.max(m.VersionEdt.numero)).where(m.VersionEdt.semestre_id == semestre.id)
        )
        or 0
    )
    verrous = mode is ModeImport.VERROUS
    if verrous:
        session.execute(
            update(m.VersionEdt)
            .where(m.VersionEdt.semestre_id == semestre.id, m.VersionEdt.active.is_(True))
            .values(active=False)
        )
    maintenant = dt.datetime.now()
    nature = "Verrous importés" if verrous else "Référence importée"
    version = m.VersionEdt(
        semestre_id=semestre.id,
        numero=numero,
        libelle=f"{nature} le {maintenant:%d/%m/%Y à %H h %M} (version {numero})",
        origine=OrigineVersion.REFERENCE,
        active=verrous,
        cree_le=maintenant,
        commentaire=f"Import ({mode.libelle}) des fichiers : {', '.join(fichiers)}",
    )
    session.add(version)
    session.flush()

    bilan_hebdo = Bilan("séances hebdomadaires")
    bilan_occurrences = Bilan("occurrences")
    for prevu in prevus:
        besoin = prevu.besoin
        for seance in sorted(seances_a_placer[besoin.id], key=lambda x: x.rang):
            if seance.rang > len(prevu.creneaux):
                signalements.append(
                    prevu.premiere.source.signalement(
                        Gravite.INFORMATION,
                        f"Besoin n° {besoin.id} : séance n° {seance.rang} sans créneau observé, "
                        "laissée au générateur",
                    )
                )
                continue
            jour, debut, salle_id = prevu.creneaux[seance.rang - 1]
            origine = next(s for s in prevu.seances if s.creneau == (jour, debut, salle_id)).source
            session.add(
                m.SeanceHebdo(
                    version_id=version.id,
                    besoin_id=besoin.id,
                    rang=seance.rang,
                    jour=jour,
                    debut_min=debut,
                    fin_min=debut + besoin.duree_min,
                    salle_id=salle_id,
                    verrou=verrous,
                    semaines=sorted(seance.semaines),
                    source_fichier=origine.fichier[:LONGUEUR_SOURCE],
                    source_ligne=origine.ligne,
                    extra=dict(origine.extra) or None,
                )
            )
            bilan_hebdo.ajouter("crees")
        if verrous:
            continue
        for s in prevu.seances:
            if not s.datee:
                continue
            session.add(
                m.Occurrence(
                    version_id=version.id,
                    besoin_id=besoin.id,
                    date=s.date,
                    debut_min=s.debut,
                    fin_min=s.fin,
                    salle_id=s.salle.id if s.salle else None,
                    statut=StatutOccurrence.DEPLACEE if s.deplacee else StatutOccurrence.PLANIFIEE,
                    origine=OrigineOccurrence.IMPORT,
                    source_fichier=s.source.fichier[:LONGUEUR_SOURCE],
                    source_ligne=s.source.ligne,
                    extra=dict(s.source.extra) or None,
                )
            )
            bilan_occurrences.ajouter("crees")
    session.flush()
    bilans += [bilan_hebdo, bilan_occurrences]
    journal.info("Version %d (n°%d, %s) créée par import", version.id, numero, nature.lower())
    return version.id


def _resume(prevu: BesoinPrevu) -> dict[str, Any]:
    """Une ligne lisible par besoin reconstitué (affichée avant d'appliquer)."""
    s = prevu.premiere
    if prevu.mode is ModeVolume.PONCTUEL:
        volume = f"ponctuel le {prevu.date_ponctuelle:%d/%m/%Y}"
    elif prevu.mode is ModeVolume.PAR_SEMAINE:
        volume = f"{prevu.nombre} par semaine"
    else:
        volume = (
            f"{prevu.nombre} au total, {prevu.max_par_semaine} par semaine au plus, du "
            f"{prevu.date_debut:%d/%m/%Y} au {prevu.date_fin:%d/%m/%Y}"
        )
    salles = {sa.id: sa.code for x in prevu.seances if (sa := x.salle) is not None}
    creneaux = "; ".join(
        f"{JOURS[jour]} {minutes_vers_hhmm(debut)}"
        + (f" {salles[salle]}" if salle in salles else "")
        for jour, debut, salle in prevu.creneaux
    )
    etats = {Action.CREE: "créé", Action.MAJ: "mis à jour", Action.INCHANGE: "inchangé"}
    return {
        "matiere": s.matiere.code,
        "type": s.type_seance.code,
        "libelle": prevu.libelle or "",
        "groupes": ", ".join(g.code for g in s.groupes),
        "enseignants": ", ".join(e.identifiant for e in s.enseignants),
        "duree": duree_vers_texte(s.duree),
        "volume": volume,
        "creneaux": creneaux,
        "seances": len(prevu.sources),
        "special": prevu.special,
        "etat": etats.get(prevu.action, "non importé") if prevu.action else "non importé",
    }
