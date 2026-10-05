"""Séances d'une vue d'emploi du temps : version, cible (groupe, enseignant, salle), couche, période.

Trois affichages :

- **semaine type** (`couche=hebdo`, `affichage=semaine`) : les séances
  hebdomadaires de la version ;
- **semaine datée** (`couche=date`, `affichage=semaine`) : les occurrences
  datées d'une semaine du calendrier, avec jours fermés et vacances ;
- **jour** (`affichage=jour`) : un seul jour, de la semaine type ou daté.

Seules les séances affichées sont lues en base (filtre SQL sur la version,
la cible et la période) : un établissement complet compte environ
800 séances hebdomadaires et 25 000 occurrences par semestre.

L'emploi du temps d'un **groupe** montre aussi les séances des groupes qui
partagent ses étudiants (relation de `edt.services.conflits_groupes`) :
celles de ses ascendants le concernent en entier, celles de ses
descendants ou des groupes chevauchants n'en concernent qu'une partie
(`Bloc.partielle`).

Le résultat (`VueEmploiDuTemps`) est fait de dataclasses figées ; la
position des blocs est calculée par `edt.services.emploi_du_temps.disposition`.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from edt.db import modeles as m
from edt.domaine.calendrier import Semaine, lundi_de, semaine_de, semaines_entre
from edt.domaine.enumerations import (
    DIMANCHE,
    JOURS,
    SAMEDI,
    Gravite,
    ModeVolume,
    NatureGroupe,
    StatutOccurrence,
)
from edt.domaine.instantane import id_seance
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable
from edt.services.conflits_groupes import conflits_groupes
from edt.services.emploi_du_temps import presentation, raccords
from edt.services.emploi_du_temps.disposition import Disposition, Plage, disposer
from edt.services.emploi_du_temps.versions import (
    ResumeSemestre,
    ResumeVersion,
    exiger_semestre,
    lire_version,
)
from edt.services.parametres import lire_parametres
from edt.verification.contrat import Anomalie

journal = logging.getLogger(__name__)

JOURS_OUVRES = (0, 1, 2, 3, 4)
"""Colonnes toujours affichées en vue semaine ; le samedi s'ajoute s'il a des séances."""

ORDRE_GRAVITE = {Gravite.ERREUR: 0, Gravite.AVERTISSEMENT: 1, Gravite.INFORMATION: 2}


class NatureCible(StrEnum):
    """Pour qui l'emploi du temps est affiché."""

    GROUPE = "groupe"
    ENSEIGNANT = "enseignant"
    SALLE = "salle"


class Couche(StrEnum):
    """Semaine type (séances hebdomadaires) ou séances datées (occurrences)."""

    HEBDO = "hebdo"
    DATE = "date"


class Affichage(StrEnum):
    """Une semaine entière ou un seul jour."""

    SEMAINE = "semaine"
    JOUR = "jour"


@dataclass(frozen=True)
class Cible:
    """Un groupe, un enseignant ou une salle."""

    nature: NatureCible
    id: int

    @property
    def valeur(self) -> str:
        """Forme texte, pour les formulaires : « groupe:12 »."""
        return f"{self.nature.value}:{self.id}"

    @classmethod
    def lire(cls, texte: str) -> Cible:
        """Lit « groupe:12 », « enseignant:3 » ou « salle:7 »."""
        nature, _, identifiant = texte.partition(":")
        try:
            return cls(NatureCible(nature), int(identifiant))
        except ValueError as erreur:
            raise ErreurDonneesInvalides(
                f"Choix d'affichage invalide : {texte!r} (attendu : un groupe, un enseignant ou "
                "une salle de la liste)."
            ) from erreur

    @classmethod
    def depuis(
        cls,
        *,
        groupe_id: int | None = None,
        enseignant_id: int | None = None,
        salle_id: int | None = None,
        texte: str | None = None,
    ) -> Cible | None:
        """Cible désignée par au plus un identifiant (ou par sa forme texte)."""
        donnes = [
            (nature, identifiant)
            for nature, identifiant in (
                (NatureCible.GROUPE, groupe_id),
                (NatureCible.ENSEIGNANT, enseignant_id),
                (NatureCible.SALLE, salle_id),
            )
            if identifiant is not None
        ]
        if len(donnes) > 1:
            raise ErreurDonneesInvalides(
                "Choisir un seul groupe, un seul enseignant ou une seule salle à la fois."
            )
        if donnes:
            return cls(*donnes[0])
        if texte:
            return cls.lire(texte)
        return None


@dataclass(frozen=True)
class MarqueAnomalie:
    """Anomalie rattachée à un bloc, pour la marque et l'infobulle."""

    gravite: Gravite
    code: str
    message: str


@dataclass(frozen=True)
class Bloc:
    """Une séance affichée (hebdomadaire ou datée).

    `cle` vaut `h<id>` pour une séance hebdomadaire et `o<id>` pour une
    occurrence ; `seance_id` est l'identifiant `"<besoin>-<rang>"` du
    vérificateur. `partielle` : séance d'un groupe lié qui ne concerne
    qu'une partie du groupe affiché. `semaines_texte` n'est renseigné que
    pour une séance qui n'a lieu que certaines semaines.
    """

    cle: str
    seance_hebdo_id: int | None
    occurrence_id: int | None
    seance_id: str | None
    besoin_id: int
    jour: int
    date: dt.date | None
    debut: int
    fin: int
    matiere_code: str
    matiere: str
    type_code: str
    type_libelle: str
    precision: str | None
    groupes: tuple[str, ...]
    groupe_ids: tuple[int, ...]
    enseignants: tuple[str, ...]
    enseignant_ids: tuple[int, ...]
    salle: str | None
    salle_id: int | None
    campus: str | None
    verrou: bool
    statut: StatutOccurrence | None
    date_initiale: dt.date | None
    commentaire: str | None
    semaines: tuple[int, ...]
    semaines_texte: str | None
    partielle: bool
    couleur: int
    couleur_type: str | None
    anomalies: tuple[MarqueAnomalie, ...] = ()

    @property
    def gravite(self) -> Gravite | None:
        """Gravité la plus forte des anomalies du bloc, ou `None`."""
        if not self.anomalies:
            return None
        return min((a.gravite for a in self.anomalies), key=ORDRE_GRAVITE.__getitem__)

    @property
    def annulee(self) -> bool:
        """Vrai pour une occurrence annulée."""
        return self.statut is StatutOccurrence.ANNULEE

    @property
    def deplacee(self) -> bool:
        """Vrai pour une occurrence déplacée."""
        return self.statut is StatutOccurrence.DEPLACEE

    @property
    def titre(self) -> str:
        """Matière et précision du besoin : « Mathématiques (Devoir surveillé) »."""
        if self.precision:
            return f"{self.matiere} ({self.precision})"
        return self.matiere


@dataclass(frozen=True)
class JourAffiche:
    """Une colonne de la grille."""

    jour: int
    date: dt.date | None
    fermeture: str | None

    @property
    def libelle(self) -> str:
        """« lundi » ou « lundi 05/10 »."""
        nom = JOURS[self.jour]
        return f"{nom} {self.date:%d/%m}" if self.date else nom


@dataclass(frozen=True)
class Navigation:
    """Période affichée et périodes voisines (pour les flèches)."""

    titre: str
    detail: str
    precedente: dict[str, str] | None
    suivante: dict[str, str] | None


@dataclass(frozen=True)
class OptionCible:
    """Une entrée du sélecteur « pour qui » (valeur « groupe:12 », libellé, rubrique)."""

    valeur: str
    libelle: str
    rubrique: str


@dataclass(frozen=True)
class VueEmploiDuTemps:
    """Tout ce qu'il faut pour afficher une grille."""

    semestre: ResumeSemestre
    version: ResumeVersion
    cible: Cible | None
    titre_cible: str
    couche: Couche
    affichage: Affichage
    date: dt.date | None
    jour: int | None
    jours: tuple[JourAffiche, ...]
    blocs: tuple[Bloc, ...]
    a_programmer: tuple[Bloc, ...]
    disposition: Disposition
    dejeuner: tuple[float, float] | None
    navigation: Navigation | None
    controle: raccords.Controle | None
    anomalies: tuple[Anomalie, ...]
    groupes_lies: tuple[str, ...]
    occurrences_disponibles: bool
    nb_semaines: int


# --------------------------------------------------------------------------- #
# Sélecteurs
# --------------------------------------------------------------------------- #

RUBRIQUES_GROUPES = {
    NatureGroupe.PROMOTION: "Groupes : promotions",
    NatureGroupe.CLASSE: "Groupes : classes",
    NatureGroupe.DEMI_GROUPE: "Groupes : demi-groupes",
    NatureGroupe.TRANSVERSE: "Groupes : transverses",
    NatureGroupe.TIERS_TEMPS: "Groupes : tiers-temps",
}


def options_cibles(session: Session, annee_scolaire_id: int) -> tuple[OptionCible, ...]:
    """Groupes de l'année (par nature), enseignants, salles (par campus), pour le sélecteur."""
    options: list[OptionCible] = []
    groupes = session.scalars(
        select(m.Groupe).where(m.Groupe.annee_scolaire_id == annee_scolaire_id)
    ).all()
    ordre_natures = {nature: rang for rang, nature in enumerate(RUBRIQUES_GROUPES)}
    for groupe in sorted(groupes, key=lambda g: (ordre_natures[NatureGroupe(g.nature)], g.code)):
        options.append(
            OptionCible(
                Cible(NatureCible.GROUPE, groupe.id).valeur,
                f"{groupe.code} - {groupe.libelle}",
                RUBRIQUES_GROUPES[NatureGroupe(groupe.nature)],
            )
        )
    for enseignant in session.scalars(
        select(m.Enseignant).order_by(m.Enseignant.nom, m.Enseignant.prenom, m.Enseignant.id)
    ):
        libelle = f"{enseignant.nom} {enseignant.prenom or ''}".strip()
        if enseignant.fictif:
            libelle += " (enseignant à préciser)"
        options.append(
            OptionCible(Cible(NatureCible.ENSEIGNANT, enseignant.id).valeur, libelle, "Enseignants")
        )
    for salle in session.scalars(
        select(m.Salle).options(joinedload(m.Salle.campus)).order_by(m.Salle.code, m.Salle.id)
    ):
        options.append(
            OptionCible(
                Cible(NatureCible.SALLE, salle.id).valeur,
                f"{salle.code} ({salle.capacite} places)",
                f"Salles : {salle.campus.nom}",
            )
        )
    return tuple(options)


def cible_par_defaut(session: Session, annee_scolaire_id: int) -> Cible | None:
    """Première promotion de l'année (sinon premier groupe), pour ouvrir la page sur quelque chose."""
    groupes = session.execute(
        select(m.Groupe.id, m.Groupe.nature, m.Groupe.code).where(
            m.Groupe.annee_scolaire_id == annee_scolaire_id
        )
    ).all()
    if not groupes:
        return None
    promotions = sorted(
        (code, i) for i, nature, code in groupes if nature == NatureGroupe.PROMOTION
    )
    choix = promotions[0][1] if promotions else sorted((code, i) for i, _, code in groupes)[0][1]
    return Cible(NatureCible.GROUPE, choix)


# --------------------------------------------------------------------------- #
# Construction d'une vue
# --------------------------------------------------------------------------- #


def construire_vue(
    session: Session,
    *,
    version_id: int,
    cible: Cible | None,
    couche: Couche = Couche.HEBDO,
    affichage: Affichage = Affichage.SEMAINE,
    date: dt.date | None = None,
    jour: int | None = None,
    aujourdhui: dt.date | None = None,
    verifier: bool = True,
) -> VueEmploiDuTemps:
    """Séances, colonnes, disposition et anomalies d'une vue (voir l'en-tête du module).

    - `date` : en couche datée, un jour de la semaine voulue (ou le jour, en
      affichage jour) ; par défaut aujourd'hui s'il tombe dans le semestre,
      sinon le premier jour du semestre ;
    - `jour` : en semaine type et affichage jour, le jour voulu (0 = lundi) ;
    - `verifier` : demander les anomalies au vérificateur (marques sur les blocs).
    """
    version = lire_version(session, version_id)
    semestre = exiger_semestre(session, version.semestre_id)
    semaines = semaines_entre(semestre.date_debut, semestre.date_fin)
    parametres = lire_parametres(session)
    annee_id = session.scalar(
        select(m.Semestre.annee_scolaire_id).where(m.Semestre.id == semestre.id)
    )
    contexte_groupes = _ContexteGroupes.charger(session, annee_id, cible)
    titre = _titre_cible(session, cible, annee_id)

    occurrences_disponibles = _a_des_occurrences(session, version.id)
    a_programmer: tuple[Bloc, ...] = ()
    periode: tuple[dt.date, dt.date] | None = None
    if couche is Couche.HEBDO:
        if affichage is Affichage.JOUR:
            jour = 0 if jour is None else jour
            if not 0 <= jour <= DIMANCHE:
                raise ErreurDonneesInvalides(f"Jour invalide : {jour} (attendu : 0 à 6).")
        blocs = _blocs_hebdo(session, version.id, cible, contexte_groupes, len(semaines))
        if affichage is Affichage.JOUR:
            blocs = [bloc for bloc in blocs if bloc.jour == jour]
            jours = (JourAffiche(jour, None, None),)
        else:
            jours = tuple(JourAffiche(j, None, None) for j in _jours_de_semaine(blocs))
        date = None
        navigation = _navigation_hebdo(affichage, jour)
    else:
        date = _date_par_defaut(date, semestre, aujourdhui)
        if affichage is Affichage.JOUR:
            debut = fin = date
        else:
            debut = lundi_de(date)
            fin = debut + dt.timedelta(days=6)
        periode = (debut, fin)
        tous = _blocs_dates(session, version.id, cible, contexte_groupes, debut, fin, len(semaines))
        blocs = [b for b in tous if b.statut is not StatutOccurrence.A_PROGRAMMER]
        a_programmer = tuple(b for b in tous if b.statut is StatutOccurrence.A_PROGRAMMER)
        fermetures = _fermetures(session, annee_id, debut, fin, contexte_groupes)
        if affichage is Affichage.JOUR:
            jours_affiches = (date.weekday(),)
        else:
            jours_affiches = _jours_de_semaine(blocs)
        jours = tuple(
            JourAffiche(j, debut + dt.timedelta(days=j - debut.weekday()), None)
            for j in jours_affiches
        )
        jours = tuple(
            dataclasses.replace(j, fermeture=fermetures.get(j.date)) if j.date else j for j in jours
        )
        jour = date.weekday() if affichage is Affichage.JOUR else None
        navigation = _navigation_datee(affichage, date, semaines)

    controle = None
    anomalies: tuple[Anomalie, ...] = ()
    if verifier and cible is not None:
        if couche is Couche.HEBDO:
            controle = raccords.controler(raccords.verifier_version, session, version.id)
        elif occurrences_disponibles and periode is not None:
            controle = raccords.controler(
                raccords.verifier_occurrences_version,
                session,
                version.id,
                date_debut=periode[0],
                date_fin=periode[1],
            )
        if controle is not None and controle.verification is not None:
            blocs, anomalies = _attacher_anomalies(
                blocs, controle.verification.anomalies, cible, couche
            )

    disposition = disposer(
        (Plage(b.cle, b.jour, b.debut, b.fin) for b in blocs),
        parametres.ouverture_min,
        parametres.fermeture_min,
    )
    dejeuner = None
    if parametres.dejeuner_debut_min < parametres.dejeuner_fin_min:
        axe = disposition.axe
        haut = axe.position(max(parametres.dejeuner_debut_min, axe.debut))
        bas = axe.position(min(parametres.dejeuner_fin_min, axe.fin))
        if bas > haut:
            dejeuner = (haut, round(bas - haut, 4))

    return VueEmploiDuTemps(
        semestre=semestre,
        version=version,
        cible=cible,
        titre_cible=titre,
        couche=couche,
        affichage=affichage,
        date=date,
        jour=jour,
        jours=jours,
        blocs=tuple(blocs),
        a_programmer=a_programmer,
        disposition=disposition,
        dejeuner=dejeuner,
        navigation=navigation,
        controle=controle,
        anomalies=anomalies,
        groupes_lies=contexte_groupes.codes_lies if contexte_groupes else (),
        occurrences_disponibles=occurrences_disponibles,
        nb_semaines=len(semaines),
    )


def bloc_seance(session: Session, seance_hebdo_id: int) -> Bloc:
    """Le bloc d'une séance hebdomadaire (pour le panneau de détail)."""
    seance = session.scalar(
        _avec_details_hebdo(select(m.SeanceHebdo).where(m.SeanceHebdo.id == seance_hebdo_id))
    )
    if seance is None:
        raise ErreurIntrouvable(f"La séance n° {seance_hebdo_id} n'existe pas (ou plus).")
    semestre = session.get(m.Semestre, seance.besoin.semestre_id)
    nb_semaines = len(semaines_entre(semestre.date_debut, semestre.date_fin))
    return _bloc_hebdo(seance, partielle=False, nb_semaines=nb_semaines)


def bloc_occurrence(session: Session, occurrence_id: int) -> Bloc:
    """Le bloc d'une occurrence datée (pour le panneau de détail)."""
    occurrence = session.scalar(
        _avec_details_dates(select(m.Occurrence).where(m.Occurrence.id == occurrence_id))
    )
    if occurrence is None:
        raise ErreurIntrouvable(f"La séance datée n° {occurrence_id} n'existe pas (ou plus).")
    semestre = session.get(m.Semestre, occurrence.besoin.semestre_id)
    nb_semaines = len(semaines_entre(semestre.date_debut, semestre.date_fin))
    return _bloc_date(occurrence, partielle=False, nb_semaines=nb_semaines)


# --------------------------------------------------------------------------- #
# Groupes liés
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ContexteGroupes:
    """Pour une cible « groupe » : groupes affichés et groupes qui concernent tout le groupe."""

    groupe_id: int
    vus: frozenset[int]
    entiers: frozenset[int]
    codes_lies: tuple[str, ...]

    @classmethod
    def charger(
        cls, session: Session, annee_id: int, cible: Cible | None
    ) -> _ContexteGroupes | None:
        if cible is None or cible.nature is not NatureCible.GROUPE:
            return None
        lignes = session.execute(
            select(m.Groupe.id, m.Groupe.parent_id, m.Groupe.code).where(
                m.Groupe.annee_scolaire_id == annee_id
            )
        ).all()
        parents = {identifiant: parent for identifiant, parent, _ in lignes}
        codes = {identifiant: code for identifiant, _, code in lignes}
        if cible.id not in parents:
            raise ErreurIntrouvable(
                f"Le groupe n° {cible.id} n'existe pas pour l'année de ce semestre."
            )
        ascendants: set[int] = set()
        courant = parents.get(cible.id)
        while courant is not None and courant not in ascendants:
            ascendants.add(courant)
            courant = parents.get(courant)
        lies = {cible.id}
        for a, b in conflits_groupes(session, annee_id):
            if a == cible.id:
                lies.add(b)
            elif b == cible.id:
                lies.add(a)
        return cls(
            groupe_id=cible.id,
            vus=frozenset(lies),
            entiers=frozenset({cible.id} | ascendants),
            codes_lies=tuple(sorted(codes[g] for g in lies if g != cible.id)),
        )

    def partielle(self, groupe_ids: Iterable[int]) -> bool:
        """Vrai si la séance ne concerne qu'une partie du groupe affiché."""
        return self.entiers.isdisjoint(groupe_ids)


# --------------------------------------------------------------------------- #
# Lecture des séances
# --------------------------------------------------------------------------- #


def _options_besoin():
    return selectinload(m.SeanceHebdo.besoin).options(
        selectinload(m.Besoin.groupes),
        selectinload(m.Besoin.enseignants),
        joinedload(m.Besoin.matiere),
        joinedload(m.Besoin.type_seance),
    )


def _avec_details_hebdo(requete):
    return requete.options(
        _options_besoin(),
        selectinload(m.SeanceHebdo.salle).joinedload(m.Salle.campus),
    )


def _avec_details_dates(requete):
    return requete.options(
        selectinload(m.Occurrence.besoin).options(
            selectinload(m.Besoin.groupes),
            selectinload(m.Besoin.enseignants),
            joinedload(m.Besoin.matiere),
            joinedload(m.Besoin.type_seance),
        ),
        selectinload(m.Occurrence.salle).joinedload(m.Salle.campus),
        selectinload(m.Occurrence.seance_hebdo),
    )


def _filtre_cible(colonne_besoin, colonne_salle, cible: Cible, groupes: _ContexteGroupes | None):
    """Clause SQL qui restreint les séances à celles de la cible."""
    if cible.nature is NatureCible.SALLE:
        return colonne_salle == cible.id
    if cible.nature is NatureCible.ENSEIGNANT:
        return colonne_besoin.in_(
            select(m.besoin_enseignant.c.besoin_id).where(
                m.besoin_enseignant.c.enseignant_id == cible.id
            )
        )
    assert groupes is not None  # construit par _ContexteGroupes.charger pour une cible groupe
    return colonne_besoin.in_(
        select(m.besoin_groupe.c.besoin_id).where(m.besoin_groupe.c.groupe_id.in_(groupes.vus))
    )


def _blocs_hebdo(
    session: Session,
    version_id: int,
    cible: Cible | None,
    groupes: _ContexteGroupes | None,
    nb_semaines: int,
) -> list[Bloc]:
    if cible is None:
        return []
    requete = _avec_details_hebdo(
        select(m.SeanceHebdo)
        .where(
            m.SeanceHebdo.version_id == version_id,
            _filtre_cible(m.SeanceHebdo.besoin_id, m.SeanceHebdo.salle_id, cible, groupes),
        )
        .order_by(m.SeanceHebdo.jour, m.SeanceHebdo.debut_min, m.SeanceHebdo.id)
    )
    return [
        _bloc_hebdo(
            seance,
            partielle=groupes.partielle(g.id for g in seance.besoin.groupes) if groupes else False,
            nb_semaines=nb_semaines,
        )
        for seance in session.scalars(requete)
    ]


def _blocs_dates(
    session: Session,
    version_id: int,
    cible: Cible | None,
    groupes: _ContexteGroupes | None,
    debut: dt.date,
    fin: dt.date,
    nb_semaines: int,
) -> list[Bloc]:
    if cible is None:
        return []
    requete = _avec_details_dates(
        select(m.Occurrence)
        .where(
            m.Occurrence.version_id == version_id,
            m.Occurrence.date >= debut,
            m.Occurrence.date <= fin,
            _filtre_cible(m.Occurrence.besoin_id, m.Occurrence.salle_id, cible, groupes),
        )
        .order_by(m.Occurrence.date, m.Occurrence.debut_min, m.Occurrence.id)
    )
    return [
        _bloc_date(
            occurrence,
            partielle=groupes.partielle(g.id for g in occurrence.besoin.groupes)
            if groupes
            else False,
            nb_semaines=nb_semaines,
        )
        for occurrence in session.scalars(requete)
    ]


def _champs_besoin(besoin: m.Besoin) -> dict:
    groupes = sorted(besoin.groupes, key=lambda g: g.code)
    enseignants = sorted(besoin.enseignants, key=lambda e: (e.nom, e.id))
    return {
        "besoin_id": besoin.id,
        "matiere_code": besoin.matiere.code,
        "matiere": besoin.matiere.libelle,
        "type_code": besoin.type_seance.code,
        "type_libelle": besoin.type_seance.libelle,
        "precision": besoin.libelle,
        "groupes": tuple(g.code for g in groupes),
        "groupe_ids": tuple(g.id for g in groupes),
        "enseignants": tuple(e.nom_affiche for e in enseignants),
        "enseignant_ids": tuple(e.id for e in enseignants),
        "couleur": presentation.indice_couleur(besoin.matiere.code),
        "couleur_type": presentation.couleur_valide(besoin.type_seance.couleur),
    }


def _texte_semaines(besoin: m.Besoin, semaines: Sequence[int], nb_semaines: int) -> str | None:
    """Rappel des semaines actives, seulement pour une séance qui n'a pas lieu chaque semaine."""
    if besoin.mode_volume == ModeVolume.PONCTUEL and besoin.date_ponctuelle is not None:
        return f"Le {besoin.date_ponctuelle:%d/%m/%Y} seulement"
    if (
        besoin.mode_volume == ModeVolume.PAR_SEMAINE
        and besoin.date_debut is None
        and besoin.date_fin is None
    ):
        return None
    return presentation.texte_semaines(semaines, nb_semaines)


def _bloc_hebdo(seance: m.SeanceHebdo, *, partielle: bool, nb_semaines: int) -> Bloc:
    semaines = tuple(sorted(int(s) for s in seance.semaines or ()))
    salle = seance.salle
    return Bloc(
        cle=f"h{seance.id}",
        seance_hebdo_id=seance.id,
        occurrence_id=None,
        seance_id=id_seance(seance.besoin_id, seance.rang),
        jour=seance.jour,
        date=None,
        debut=seance.debut_min,
        fin=seance.fin_min,
        salle=salle.code if salle else None,
        salle_id=seance.salle_id,
        campus=salle.campus.code if salle else None,
        verrou=bool(seance.verrou),
        statut=None,
        date_initiale=None,
        commentaire=None,
        semaines=semaines,
        semaines_texte=_texte_semaines(seance.besoin, semaines, nb_semaines),
        partielle=partielle,
        **_champs_besoin(seance.besoin),
    )


def _bloc_date(occurrence: m.Occurrence, *, partielle: bool, nb_semaines: int) -> Bloc:
    hebdo = occurrence.seance_hebdo
    salle = occurrence.salle
    return Bloc(
        cle=f"o{occurrence.id}",
        seance_hebdo_id=occurrence.seance_hebdo_id,
        occurrence_id=occurrence.id,
        seance_id=id_seance(occurrence.besoin_id, hebdo.rang) if hebdo else None,
        jour=occurrence.date.weekday(),
        date=occurrence.date,
        debut=occurrence.debut_min,
        fin=occurrence.fin_min,
        salle=salle.code if salle else None,
        salle_id=occurrence.salle_id,
        campus=salle.campus.code if salle else None,
        verrou=bool(hebdo.verrou) if hebdo else False,
        statut=StatutOccurrence(occurrence.statut),
        date_initiale=occurrence.date_initiale,
        commentaire=occurrence.commentaire,
        semaines=(),
        semaines_texte=None,
        partielle=partielle,
        **_champs_besoin(occurrence.besoin),
    )


def _a_des_occurrences(session: Session, version_id: int) -> bool:
    return (
        session.scalar(
            select(m.Occurrence.id).where(m.Occurrence.version_id == version_id).limit(1)
        )
        is not None
    )


# --------------------------------------------------------------------------- #
# Calendrier de la vue
# --------------------------------------------------------------------------- #


def _jours_de_semaine(blocs: Iterable[Bloc]) -> tuple[int, ...]:
    """Lundi à vendredi, plus le samedi (et le dimanche) s'ils ont des séances."""
    presents = {bloc.jour for bloc in blocs}
    jours = list(JOURS_OUVRES)
    jours.extend(j for j in (SAMEDI, DIMANCHE) if j in presents)
    return tuple(jours)


def _date_par_defaut(
    date: dt.date | None, semestre: ResumeSemestre, aujourdhui: dt.date | None
) -> dt.date:
    if date is not None:
        return date
    aujourdhui = aujourdhui or dt.date.today()
    if semestre.date_debut <= aujourdhui <= semestre.date_fin:
        return aujourdhui
    return semestre.date_debut


def _navigation_hebdo(affichage: Affichage, jour: int | None) -> Navigation | None:
    if affichage is not Affichage.JOUR or jour is None:
        return None
    return Navigation(
        titre=f"Semaine type : {JOURS[jour]}",
        detail="Chaque séance est rappelée telle qu'elle revient de semaine en semaine.",
        precedente={"jour": str(jour - 1)} if jour > 0 else None,
        suivante={"jour": str(jour + 1)} if jour < SAMEDI else None,
    )


def _navigation_datee(
    affichage: Affichage, date: dt.date, semaines: tuple[Semaine, ...]
) -> Navigation:
    premier = semaines[0].lundi
    dernier = semaines[-1].dimanche
    semaine = semaine_de(date, semaines)
    iso = date.isocalendar().week
    if semaine is not None:
        titre = f"Semaine {semaine.numero} du semestre (semaine {iso} de l'année)"
    else:
        titre = f"Semaine {iso} de l'année, hors du semestre"
    if affichage is Affichage.JOUR:
        precedente = date - dt.timedelta(days=2 if date.weekday() == 0 else 1)
        suivante = date + dt.timedelta(days=2 if date.weekday() == SAMEDI else 1)
        return Navigation(
            titre=titre,
            detail=presentation.date_longue(date).capitalize(),
            precedente={"date": precedente.isoformat()} if precedente >= premier else None,
            suivante={"date": suivante.isoformat()} if suivante <= dernier else None,
        )
    lundi = lundi_de(date)
    dimanche = lundi + dt.timedelta(days=6)
    precedente = lundi - dt.timedelta(days=7)
    suivante = lundi + dt.timedelta(days=7)
    return Navigation(
        titre=titre,
        detail=f"Du {presentation.date_longue(lundi)} au {presentation.date_longue(dimanche)}",
        precedente={"date": precedente.isoformat()} if precedente >= premier else None,
        suivante={"date": suivante.isoformat()} if suivante <= dernier else None,
    )


def _fermetures(
    session: Session,
    annee_id: int,
    debut: dt.date,
    fin: dt.date,
    groupes: _ContexteGroupes | None,
) -> dict[dt.date, str]:
    """Libellés des fermetures de chaque jour de la période, pour la cible.

    Fermetures globales toujours ; pour un groupe, aussi celles de ce groupe
    et de ses ascendants (une fermeture vaut pour un groupe et ses descendants).
    """
    lignes = session.scalars(
        select(m.JourFerme).where(
            m.JourFerme.annee_scolaire_id == annee_id,
            m.JourFerme.date_fin >= debut,
            m.JourFerme.date_debut <= fin,
        )
    ).all()
    portee = groupes.entiers if groupes else frozenset()
    libelles: dict[dt.date, list[str]] = defaultdict(list)
    for ferme in sorted(lignes, key=lambda f: (f.date_debut, f.id)):
        if ferme.groupe_id is not None and ferme.groupe_id not in portee:
            continue
        jour = max(ferme.date_debut, debut)
        while jour <= min(ferme.date_fin, fin):
            if ferme.libelle not in libelles[jour]:
                libelles[jour].append(ferme.libelle)
            jour += dt.timedelta(days=1)
    return {jour: ", ".join(textes) for jour, textes in libelles.items()}


def _titre_cible(session: Session, cible: Cible | None, annee_id: int) -> str:
    if cible is None:
        return ""
    if cible.nature is NatureCible.GROUPE:
        groupe = session.get(m.Groupe, cible.id)
        if groupe is None or groupe.annee_scolaire_id != annee_id:
            raise ErreurIntrouvable(f"Le groupe n° {cible.id} n'existe pas pour ce semestre.")
        return f"Groupe {groupe.code} - {groupe.libelle}"
    if cible.nature is NatureCible.ENSEIGNANT:
        enseignant = session.get(m.Enseignant, cible.id)
        if enseignant is None:
            raise ErreurIntrouvable(f"L'enseignant n° {cible.id} n'existe pas.")
        nom = f"{enseignant.prenom or ''} {enseignant.nom}".strip()
        return f"{enseignant.civilite + ' ' if enseignant.civilite else ''}{nom}"
    salle = session.scalar(
        select(m.Salle).options(joinedload(m.Salle.campus)).where(m.Salle.id == cible.id)
    )
    if salle is None:
        raise ErreurIntrouvable(f"La salle n° {cible.id} n'existe pas.")
    return f"Salle {salle.code} - {salle.campus.nom}, {salle.capacite} places"


# --------------------------------------------------------------------------- #
# Anomalies
# --------------------------------------------------------------------------- #


def _attacher_anomalies(
    blocs: list[Bloc], anomalies: Sequence[Anomalie], cible: Cible, couche: Couche
) -> tuple[list[Bloc], tuple[Anomalie, ...]]:
    """Rattache les anomalies aux blocs affichés ; renvoie aussi celles qui concernent la vue."""
    par_cle: dict[str, list[MarqueAnomalie]] = defaultdict(list)
    retenues: list[Anomalie] = []
    par_seance = defaultdict(list)
    par_occurrence = {}
    for bloc in blocs:
        if bloc.occurrence_id is not None:
            par_occurrence[bloc.occurrence_id] = bloc
        if bloc.seance_id is not None:
            par_seance[bloc.seance_id].append(bloc)
    for anomalie in anomalies:
        touches: list[Bloc] = []
        if couche is Couche.DATE:
            touches.extend(par_occurrence[o] for o in anomalie.occurrences if o in par_occurrence)
            if not anomalie.occurrences:
                touches.extend(
                    bloc
                    for seance in anomalie.seances
                    for bloc in par_seance.get(seance, ())
                    if anomalie.date is None or bloc.date == anomalie.date
                )
        else:
            touches.extend(
                bloc for seance in anomalie.seances for bloc in par_seance.get(seance, ())
            )
        concerne_cible = (
            cible.id
            in {
                NatureCible.GROUPE: anomalie.groupes,
                NatureCible.ENSEIGNANT: anomalie.enseignants,
                NatureCible.SALLE: anomalie.salles,
            }[cible.nature]
        )
        if not touches and not concerne_cible:
            continue
        retenues.append(anomalie)
        marque = MarqueAnomalie(anomalie.gravite, str(anomalie.code), anomalie.message)
        for bloc in {b.cle: b for b in touches}.values():
            par_cle[bloc.cle].append(marque)
    blocs = [
        dataclasses.replace(bloc, anomalies=tuple(par_cle[bloc.cle]))
        if bloc.cle in par_cle
        else bloc
        for bloc in blocs
    ]
    retenues.sort(key=lambda a: (ORDRE_GRAVITE[a.gravite], str(a.code), a.message))
    return blocs, tuple(retenues)
