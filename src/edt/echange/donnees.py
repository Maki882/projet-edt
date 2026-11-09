"""Import et export des données de l'établissement dans le format d'échange.

Import
------
1. Chaque tableau lu est rattaché à une feuille du format (par son nom, à
   défaut par ses en-têtes). Les feuilles sont traitées dans l'ordre de
   `FEUILLES` : une ligne peut donc référencer ce qu'une feuille précédente
   du même import vient de créer.
2. Chaque ligne est lue colonne par colonne (`Colonne.lire`) : toutes les
   valeurs illisibles d'une ligne sont signalées, et la ligne est rejetée.
3. Les lignes de même identifiant métier (plusieurs fichiers, ou deux fois
   la même ligne) sont dédoublonnées : identiques, elles sont fusionnées ;
   différentes, la première est retenue et les suivantes sont rejetées.
4. Chaque ligne est appliquée dans son propre point de sauvegarde : créée si
   son identifiant métier est inconnu, mise à jour s'il existe et qu'une
   valeur change, inchangée sinon. Une référence inconnue (salle d'un campus
   absent) rejette la ligne avec une explication.

Règles d'une mise à jour : une colonne **absente** du fichier ne modifie pas
la valeur existante ; une cellule **vide** d'une colonne présente remet la
valeur par défaut (ou vide). Une liste (équipements, enseignants...)
remplace la liste existante. Un réimport ne supprime jamais rien : seul
« tout remplacer » vide d'abord les données (`vider_donnees`).

Provenance : une ligne créée ou modifiée garde le nom du fichier et son
numéro de ligne ; les colonnes non reconnues sont conservées dans `extra`
(pour les tables qui ont ce champ ; sinon le rapport le dit).

Export
------
`exporter_donnees` relit la base dans le même format : exporter puis
réimporter dans une base vide redonne les mêmes données.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from edt.db import modeles as m
from edt.db.moteur import identifiant_valide
from edt.domaine.enumerations import CibleIndisponibilite, Gravite, ModeVolume
from edt.domaine.heures import MINUTES_PAR_JOUR, duree_vers_texte, minutes_vers_texte
from edt.domaine.regles import (
    CATALOGUE_TYPES,
    ErreurSaisie,
    EtatRegle,
    NaturePortee,
    OrigineRegle,
    Regle,
    SorteChamp,
    TypeRegle,
)
from edt.echange.format import FEUILLES, Feuille, identifier_feuille
from edt.echange.lecture import LigneTypee, Tableau, comparable, preparer_tableau
from edt.echange.rapport import Bilan, Signalement
from edt.echange.valeurs import cle_code, cle_mot
from edt.erreurs import ErreurDonneesInvalides
from edt.services import regles as service_regles

journal = logging.getLogger(__name__)

FEUILLES_AVEC_PROVENANCE = frozenset(
    {
        "salles",
        "enseignants",
        "groupes",
        "jours_fermes",
        "etudiants",
        "matieres",
        "besoins",
        "indisponibilites",
        "regles",
    }
)
"""Feuilles dont la table garde la provenance (fichier, ligne) et les colonnes inconnues."""

LONGUEUR_SOURCE = 255


def obtenir_par_id(session: Session, modele: type, identifiant: object) -> Any | None:
    """La ligne d'identifiant `identifiant`, ou `None` s'il ne peut désigner aucune ligne.

    La garde est celle du socle (`edt.db.moteur.identifiant_valide`) : un
    identifiant nul, négatif, booléen ou au-delà des entiers de SQLite répond
    « n'existe pas », là où `session.get` lèverait `OverflowError` pour un
    entier énorme venu d'une adresse ou de la ligne de commande.
    """
    if not identifiant_valide(identifiant):
        return None
    return session.get(modele, identifiant)


class Action(StrEnum):
    """Effet de l'application d'une ligne (nom du compteur du bilan)."""

    CREE = "crees"
    MAJ = "mis_a_jour"
    INCHANGE = "inchanges"


class Rejet(Exception):  # noqa: N818 - c'est un refus de ligne, pas une erreur de programme
    """Refus d'une ligne pendant l'import : interne au moteur, toujours converti en signalement."""

    def __init__(
        self, colonne: str, message: str, autres: tuple[tuple[str, str], ...] = ()
    ) -> None:
        super().__init__(message)
        self.colonne = colonne
        self.message = message
        self.details: tuple[tuple[str, str], ...] = ((colonne, message), *autres)
        """Toutes les erreurs de la ligne, (colonne, message) : un signalement chacune."""


# --------------------------------------------------------------------------- #
# Contexte : index des entités existantes, résolution des références
# --------------------------------------------------------------------------- #


class Contexte:
    """Session et index des entités, pour résoudre les références d'une ligne.

    Les index sont construits à la première utilisation et tenus à jour au
    fil des créations ; `oublier()` les fait reconstruire depuis la base.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self._index: dict[str, Any] = {}

    def oublier(self) -> None:
        """Oublie les index (après l'annulation d'une ligne par la base)."""
        self._index.clear()

    def _obtenir(self, nom: str, fabrique: Callable[[], Any]) -> Any:
        if nom not in self._index:
            self._index[nom] = fabrique()
        return self._index[nom]

    # -- index ------------------------------------------------------------------

    @property
    def annees(self) -> dict[str, m.AnneeScolaire]:
        return self._obtenir(
            "annees",
            lambda: {cle_code(a.libelle): a for a in self.session.scalars(select(m.AnneeScolaire))},
        )

    @property
    def semestres(self) -> dict[tuple[int, str], m.Semestre]:
        return self._obtenir(
            "semestres",
            lambda: {
                (s.annee_scolaire_id, cle_code(s.code)): s
                for s in self.session.scalars(select(m.Semestre))
            },
        )

    @property
    def campus_par_code(self) -> dict[str, m.Campus]:
        return self._obtenir(
            "campus", lambda: {cle_code(c.code): c for c in self.session.scalars(select(m.Campus))}
        )

    @property
    def trajets(self) -> dict[tuple[int, int], m.Trajet]:
        return self._obtenir(
            "trajets",
            lambda: {
                (t.campus_a_id, t.campus_b_id): t for t in self.session.scalars(select(m.Trajet))
            },
        )

    @property
    def salles(self) -> dict[str, list[m.Salle]]:
        """Salles par code (sans casse) : un même code peut exister sur plusieurs campus."""

        def fabrique() -> dict[str, list[m.Salle]]:
            index: dict[str, list[m.Salle]] = defaultdict(list)
            requete = select(m.Salle).options(
                selectinload(m.Salle.equipements), selectinload(m.Salle.campus)
            )
            for salle in self.session.scalars(requete):
                index[cle_code(salle.code)].append(salle)
            return index

        return self._obtenir("salles", fabrique)

    @property
    def enseignants(self) -> dict[str, m.Enseignant]:
        return self._obtenir(
            "enseignants",
            lambda: {
                cle_code(e.identifiant): e for e in self.session.scalars(select(m.Enseignant))
            },
        )

    @property
    def groupes(self) -> dict[tuple[int, str], m.Groupe]:
        return self._obtenir(
            "groupes",
            lambda: {
                (g.annee_scolaire_id, cle_code(g.code)): g
                for g in self.session.scalars(select(m.Groupe))
            },
        )

    @property
    def etudiants(self) -> dict[str, m.Etudiant]:
        return self._obtenir(
            "etudiants",
            lambda: {
                cle_code(e.identifiant): e
                for e in self.session.scalars(
                    select(m.Etudiant).options(selectinload(m.Etudiant.groupes))
                )
            },
        )

    @property
    def matieres(self) -> dict[str, list[m.Matiere]]:
        """Matières par code (sans casse) : un même code peut exister pour plusieurs promotions."""

        def fabrique() -> dict[str, list[m.Matiere]]:
            index: dict[str, list[m.Matiere]] = defaultdict(list)
            for matiere in self.session.scalars(select(m.Matiere)):
                index[cle_code(matiere.code)].append(matiere)
            return index

        return self._obtenir("matieres", fabrique)

    @property
    def regles(self) -> dict[tuple[int, str], m.Regle]:
        """Règles par (année, code sans casse)."""
        return self._obtenir(
            "regles",
            lambda: {
                (r.annee_scolaire_id, cle_code(r.code)): r
                for r in self.session.scalars(select(m.Regle))
            },
        )

    def _liste(self, modele: type) -> dict[str, Any]:
        return self._obtenir(
            modele.__tablename__,
            lambda: {cle_code(x.code): x for x in self.session.scalars(select(modele))},
        )

    # -- résolution (lève Rejet) ------------------------------------------------

    def annee(self, libelle: str | None, colonne: str = "annee") -> m.AnneeScolaire:
        """Année scolaire de libellé `libelle`."""
        if libelle is None:
            raise Rejet(colonne, "Année scolaire manquante.")
        annee = self.annees.get(cle_code(libelle))
        if annee is None:
            raise Rejet(
                colonne,
                f"Année scolaire « {libelle} » inconnue : la créer d'abord (feuille annees).",
            )
        return annee

    def semestre(self, annee: m.AnneeScolaire, code: str, colonne: str = "semestre") -> m.Semestre:
        """Semestre `code` de l'année."""
        semestre = self.semestres.get((annee.id, cle_code(code)))
        if semestre is None:
            raise Rejet(colonne, f"Semestre « {code} » inconnu pour l'année {annee.libelle}.")
        return semestre

    def campus(self, code: str, colonne: str = "campus") -> m.Campus:
        """Campus de code `code`."""
        campus = self.campus_par_code.get(cle_code(code))
        if campus is None:
            raise Rejet(colonne, f"Campus « {code} » inconnu (feuille campus).")
        return campus

    def type_salle(self, code: str, colonne: str) -> m.TypeSalle:
        """Type de salle de code `code` (liste configurable)."""
        return self._reference(m.TypeSalle, code, colonne, "Type de salle")

    def equipement(self, code: str, colonne: str) -> m.Equipement:
        """Équipement de code `code` (liste configurable)."""
        return self._reference(m.Equipement, code, colonne, "Équipement")

    def type_seance(self, code: str, colonne: str) -> m.TypeSeance:
        """Type de séance de code `code` (liste configurable)."""
        return self._reference(m.TypeSeance, code, colonne, "Type de séance")

    def _reference(self, modele: type, code: str, colonne: str, nom: str) -> Any:
        element = self._liste(modele).get(cle_code(code))
        if element is None:
            connus = ", ".join(sorted(x.code for x in self._liste(modele).values()))
            raise Rejet(colonne, f"{nom} « {code} » inconnu (codes connus : {connus}).")
        return element

    def salle(self, reference: str, colonne: str) -> m.Salle:
        """Salle désignée par son code, ou par « code@campus » si le code est ambigu.

        Le code exact est cherché d'abord : un code de salle peut lui-même
        contenir « @ ». Sinon, la référence se coupe au dernier « @ ».
        """
        candidates = list(self.salles.get(cle_code(reference), ()))
        if not candidates and "@" in reference:
            code, _, campus = reference.rpartition("@")
            candidates = [
                s
                for s in self.salles.get(cle_code(code), ())
                if cle_code(s.campus.code) == cle_code(campus)
            ]
        if not candidates:
            raise Rejet(colonne, f"Salle « {reference} » inconnue (feuille salles).")
        if len(candidates) > 1:
            exemples = ", ".join(f"{s.code}@{s.campus.code}" for s in candidates)
            raise Rejet(
                colonne,
                f"Salle « {reference} » ambiguë : ce code existe sur plusieurs campus ; "
                f"écrire par exemple {exemples}.",
            )
        return candidates[0]

    def enseignant(self, identifiant: str, colonne: str) -> m.Enseignant:
        """Enseignant d'identifiant `identifiant`."""
        enseignant = self.enseignants.get(cle_code(identifiant))
        if enseignant is None:
            raise Rejet(colonne, f"Enseignant « {identifiant} » inconnu (feuille enseignants).")
        return enseignant

    def groupe(self, annee: m.AnneeScolaire, code: str, colonne: str) -> m.Groupe:
        """Groupe `code` de l'année."""
        groupe = self.groupes.get((annee.id, cle_code(code)))
        if groupe is None:
            raise Rejet(
                colonne,
                f"Groupe « {code} » inconnu pour l'année {annee.libelle} (feuille groupes).",
            )
        return groupe

    def etudiant(self, identifiant: str, colonne: str) -> m.Etudiant:
        """Étudiant d'identifiant `identifiant`."""
        etudiant = self.etudiants.get(cle_code(identifiant))
        if etudiant is None:
            raise Rejet(colonne, f"Étudiant « {identifiant} » inconnu (feuille etudiants).")
        return etudiant

    def matiere(
        self, code: str, annee: m.AnneeScolaire | None, promotion: str | None, colonne: str
    ) -> m.Matiere:
        """Matière de code `code` ; la promotion la distingue d'une autre de même code.

        Sans promotion : la matière sans promotion si elle existe, sinon la
        seule matière qui porte ce code.
        """
        candidates = list(self.matieres.get(cle_code(code), ()))
        if promotion is not None:
            if annee is None:
                raise Rejet("annee", "L'année est obligatoire quand la promotion est renseignée.")
            groupe = self.groupe(annee, promotion, "promotion")
            candidates = [x for x in candidates if x.promotion_id == groupe.id]
        elif len(candidates) > 1:
            sans_promotion = [x for x in candidates if x.promotion_id is None]
            if not sans_promotion:
                raise Rejet(
                    colonne,
                    f"Matière « {code} » ambiguë : ce code existe pour plusieurs promotions ; "
                    "préciser la promotion.",
                )
            candidates = sans_promotion
        if not candidates:
            precision = f" pour la promotion {promotion}" if promotion else ""
            raise Rejet(colonne, f"Matière « {code} »{precision} inconnue (feuille matieres).")
        return candidates[0]

    def besoins(self, semestre_id: int) -> dict[tuple, list[m.Besoin]]:
        """Besoins existants du semestre, par identifiant métier."""
        return self._obtenir(
            f"besoins-{semestre_id}", lambda: _index_besoins(self.session, semestre_id)
        )

    @property
    def indisponibilites(self) -> dict[tuple, list[m.Indisponibilite]]:
        def fabrique() -> dict[tuple, list[m.Indisponibilite]]:
            index: dict[tuple, list[m.Indisponibilite]] = defaultdict(list)
            for i in self.session.scalars(select(m.Indisponibilite)):
                index[_cle_indisponibilite(i)].append(i)
            return index

        return self._obtenir("indisponibilites", fabrique)

    @property
    def jours_fermes(self) -> dict[tuple, list[m.JourFerme]]:
        def fabrique() -> dict[tuple, list[m.JourFerme]]:
            index: dict[tuple, list[m.JourFerme]] = defaultdict(list)
            for j in self.session.scalars(select(m.JourFerme)):
                index[(j.annee_scolaire_id, j.date_debut, j.date_fin, j.groupe_id)].append(j)
            return index

        return self._obtenir("jours_fermes", fabrique)


def cle_besoin(besoin: m.Besoin) -> tuple:
    """Identifiant métier d'un besoin : semestre, matière, type, groupes, libellé, date."""
    return (
        besoin.semestre_id,
        besoin.matiere_id,
        besoin.type_seance_id,
        frozenset(g.id for g in besoin.groupes),
        cle_code(besoin.libelle or ""),
        besoin.date_ponctuelle,
    )


def _index_besoins(session: Session, semestre_id: int) -> dict[tuple, list[m.Besoin]]:
    index: dict[tuple, list[m.Besoin]] = defaultdict(list)
    for besoin in session.scalars(
        select(m.Besoin)
        .where(m.Besoin.semestre_id == semestre_id)
        .options(
            selectinload(m.Besoin.groupes),
            selectinload(m.Besoin.enseignants),
            selectinload(m.Besoin.types_salle),
            selectinload(m.Besoin.equipements),
        )
    ):
        index[cle_besoin(besoin)].append(besoin)
    return index


def _cle_indisponibilite(i: m.Indisponibilite) -> tuple:
    return (
        CibleIndisponibilite(i.cible),
        i.enseignant_id or i.salle_id or i.groupe_id,
        i.jour,
        i.date_debut,
        i.date_fin,
        i.debut_min,
        i.fin_min,
    )


# --------------------------------------------------------------------------- #
# Écriture d'un objet : champs, relations, provenance
# --------------------------------------------------------------------------- #


def _ecrire(objet: Any, ligne: LigneTypee, champs: Mapping[str, tuple[str, Any]]) -> bool:
    """Affecte les champs dont la colonne est présente dans le fichier ; vrai si l'un change."""
    change = False
    for attribut, (colonne, valeur) in champs.items():
        if colonne not in ligne.presentes:
            continue
        if getattr(objet, attribut) != valeur:
            setattr(objet, attribut, valeur)
            change = True
    return change


def _relation(objet: Any, attribut: str, colonne: str, ligne: LigneTypee, nouveaux: list) -> bool:
    """Remplace une relation multiple si sa colonne est présente et que l'ensemble change."""
    if colonne not in ligne.presentes:
        return False
    actuels = getattr(objet, attribut)
    if {x.id for x in actuels} == {x.id for x in nouveaux}:
        return False
    setattr(objet, attribut, list(nouveaux))
    return True


def _provenance(objet: Any, ligne: LigneTypee, creation: bool, change: bool) -> bool:
    """Garde le fichier, la ligne et les colonnes inconnues ; vrai si `extra` change.

    Une ligne sans colonne inconnue laisse `extra` tel quel (rien à conserver).
    """
    extra_change = bool(ligne.extra) and objet.extra != ligne.extra
    if extra_change:
        objet.extra = dict(ligne.extra)
    if creation or change or extra_change:
        objet.source_fichier = ligne.fichier[:LONGUEUR_SOURCE]
        objet.source_ligne = ligne.numero
    return extra_change


def _creer(session: Session, objet: Any, ligne: LigneTypee) -> Action:
    if hasattr(objet, "source_fichier"):
        _provenance(objet, ligne, creation=True, change=True)
    session.add(objet)
    session.flush()
    return Action.CREE


def _resultat(objet: Any, ligne: LigneTypee, change: bool) -> Action:
    if hasattr(objet, "source_fichier"):
        change = _provenance(objet, ligne, creation=False, change=change) or change
    return Action.MAJ if change else Action.INCHANGE


# --------------------------------------------------------------------------- #
# Une fonction d'import par feuille (lève Rejet avant toute modification)
# --------------------------------------------------------------------------- #


def _importer_annee(ctx: Contexte, ligne: LigneTypee) -> Action:
    if ligne["date_debut"] > ligne["date_fin"]:
        raise Rejet("date_fin", "La fin de l'année précède son début.")
    annee = ctx.annees.get(cle_code(ligne["libelle"]))
    champs = {
        "date_debut": ("date_debut", ligne["date_debut"]),
        "date_fin": ("date_fin", ligne["date_fin"]),
    }
    if annee is None:
        annee = m.AnneeScolaire(libelle=ligne["libelle"], **{a: x for a, (_, x) in champs.items()})
        action = _creer(ctx.session, annee, ligne)
        ctx.annees[cle_code(annee.libelle)] = annee
        return action
    return _resultat(annee, ligne, _ecrire(annee, ligne, champs))


def _importer_semestre(ctx: Contexte, ligne: LigneTypee) -> Action:
    annee = ctx.annee(ligne["annee"])
    if ligne["date_debut"] > ligne["date_fin"]:
        raise Rejet("date_fin", "Le dernier jour de cours précède le premier.")
    champs = {
        "libelle": ("libelle", ligne["libelle"]),
        "date_debut": ("date_debut", ligne["date_debut"]),
        "date_fin": ("date_fin", ligne["date_fin"]),
    }
    semestre = ctx.semestres.get((annee.id, cle_code(ligne["code"])))
    if semestre is None:
        semestre = m.Semestre(
            annee_scolaire_id=annee.id, code=ligne["code"], **{a: x for a, (_, x) in champs.items()}
        )
        action = _creer(ctx.session, semestre, ligne)
        ctx.semestres[(annee.id, cle_code(semestre.code))] = semestre
        return action
    return _resultat(semestre, ligne, _ecrire(semestre, ligne, champs))


def _importer_campus(ctx: Contexte, ligne: LigneTypee) -> Action:
    champs = {
        "nom": ("nom", ligne["nom"]),
        "prefixe_salle": ("prefixe_salle", ligne["prefixe_salle"]),
    }
    campus = ctx.campus_par_code.get(cle_code(ligne["code"]))
    if campus is None:
        campus = m.Campus(code=ligne["code"], **{a: x for a, (_, x) in champs.items()})
        action = _creer(ctx.session, campus, ligne)
        ctx.campus_par_code[cle_code(campus.code)] = campus
        return action
    return _resultat(campus, ligne, _ecrire(campus, ligne, champs))


def _importer_trajet(ctx: Contexte, ligne: LigneTypee) -> Action:
    a = ctx.campus(ligne["campus_a"], "campus_a")
    b = ctx.campus(ligne["campus_b"], "campus_b")
    if a.id == b.id:
        raise Rejet("campus_b", "Un trajet relie deux campus différents.")
    paire = (min(a.id, b.id), max(a.id, b.id))
    trajet = ctx.trajets.get(paire)
    champs = {"duree_min": ("duree", ligne["duree"])}
    if trajet is None:
        trajet = m.Trajet(campus_a_id=paire[0], campus_b_id=paire[1], duree_min=ligne["duree"])
        action = _creer(ctx.session, trajet, ligne)
        ctx.trajets[paire] = trajet
        return action
    return _resultat(trajet, ligne, _ecrire(trajet, ligne, champs))


def _importer_salle(ctx: Contexte, ligne: LigneTypee) -> Action:
    campus = ctx.campus(ligne["campus"])
    type_salle = ctx.type_salle(ligne["type"], "type")
    equipements = [ctx.equipement(code, "equipements") for code in ligne["equipements"] or ()]
    champs = {
        "capacite": ("capacite", ligne["capacite"]),
        "type_salle_id": ("type", type_salle.id),
        "batiment": ("batiment", ligne["batiment"]),
        "etage": ("etage", ligne["etage"]),
        "accessibilite": ("accessible", ligne["accessible"]),
    }
    salle = next(
        (s for s in ctx.salles.get(cle_code(ligne["code"]), ()) if s.campus_id == campus.id), None
    )
    if salle is None:
        salle = m.Salle(
            code=ligne["code"],
            campus=campus,
            equipements=equipements,
            **{a: x for a, (_, x) in champs.items()},
        )
        action = _creer(ctx.session, salle, ligne)
        ctx.salles[cle_code(salle.code)].append(salle)
        return action
    change = _ecrire(salle, ligne, champs)
    change = _relation(salle, "equipements", "equipements", ligne, equipements) or change
    return _resultat(salle, ligne, change)


def _importer_enseignant(ctx: Contexte, ligne: LigneTypee) -> Action:
    champs = {
        "civilite": ("civilite", ligne["civilite"]),
        "nom": ("nom", ligne["nom"]),
        "prenom": ("prenom", ligne["prenom"]),
        "statut": ("statut", ligne["statut"]),
        "max_minutes_jour": ("max_par_jour", ligne["max_par_jour"]),
        "max_minutes_semaine": ("max_par_semaine", ligne["max_par_semaine"]),
        "fictif": ("fictif", ligne["fictif"]),
    }
    enseignant = ctx.enseignants.get(cle_code(ligne["identifiant"]))
    if enseignant is None:
        enseignant = m.Enseignant(
            identifiant=ligne["identifiant"], **{a: x for a, (_, x) in champs.items()}
        )
        action = _creer(ctx.session, enseignant, ligne)
        ctx.enseignants[cle_code(enseignant.identifiant)] = enseignant
        return action
    return _resultat(enseignant, ligne, _ecrire(enseignant, ligne, champs))


def _importer_jour_ferme(ctx: Contexte, ligne: LigneTypee) -> Action:
    annee = ctx.annee(ligne["annee"])
    if ligne["date_fin"] < ligne["date_debut"]:
        raise Rejet("date_fin", "Le dernier jour fermé précède le premier.")
    groupe = ctx.groupe(annee, ligne["groupe"], "groupe") if ligne["groupe"] else None
    cle = (annee.id, ligne["date_debut"], ligne["date_fin"], groupe.id if groupe else None)
    champs = {"libelle": ("libelle", ligne["libelle"]), "nature": ("nature", ligne["nature"])}
    existants = ctx.jours_fermes.get(cle, [])
    if not existants:
        jour = m.JourFerme(
            annee_scolaire_id=annee.id,
            date_debut=ligne["date_debut"],
            date_fin=ligne["date_fin"],
            groupe_id=cle[3],
            **{a: x for a, (_, x) in champs.items()},
        )
        action = _creer(ctx.session, jour, ligne)
        ctx.jours_fermes.setdefault(cle, []).append(jour)
        return action
    if len(existants) > 1:
        raise Rejet(
            "date_debut",
            f"{len(existants)} fermetures existent déjà avec ces dates et ce groupe : "
            "impossible de savoir laquelle mettre à jour.",
        )
    return _resultat(existants[0], ligne, _ecrire(existants[0], ligne, champs))


def _importer_etudiant(ctx: Contexte, ligne: LigneTypee) -> Action:
    champs = {"nom": ("nom", ligne["nom"]), "prenom": ("prenom", ligne["prenom"])}
    etudiant = ctx.etudiants.get(cle_code(ligne["identifiant"]))
    if etudiant is None:
        etudiant = m.Etudiant(
            identifiant=ligne["identifiant"], **{a: x for a, (_, x) in champs.items()}
        )
        action = _creer(ctx.session, etudiant, ligne)
        ctx.etudiants[cle_code(etudiant.identifiant)] = etudiant
        return action
    return _resultat(etudiant, ligne, _ecrire(etudiant, ligne, champs))


def _importer_inscription(ctx: Contexte, ligne: LigneTypee) -> Action:
    annee = ctx.annee(ligne["annee"])
    etudiant = ctx.etudiant(ligne["etudiant"], "etudiant")
    groupe = ctx.groupe(annee, ligne["groupe"], "groupe")
    if any(g.id == groupe.id for g in etudiant.groupes):
        return Action.INCHANGE
    etudiant.groupes.append(groupe)
    ctx.session.flush()
    return Action.CREE


def _importer_matiere(ctx: Contexte, ligne: LigneTypee) -> Action:
    promotion = None
    if ligne["promotion"] is not None:
        if ligne["annee"] is None:
            raise Rejet("annee", "L'année est obligatoire quand la promotion est renseignée.")
        promotion = ctx.groupe(ctx.annee(ligne["annee"]), ligne["promotion"], "promotion")
    matiere = next(
        (
            x
            for x in ctx.matieres.get(cle_code(ligne["code"]), ())
            if x.promotion_id == (promotion.id if promotion else None)
        ),
        None,
    )
    champs = {"libelle": ("libelle", ligne["libelle"])}
    if matiere is None:
        matiere = m.Matiere(code=ligne["code"], libelle=ligne["libelle"], promotion=promotion)
        action = _creer(ctx.session, matiere, ligne)
        ctx.matieres[cle_code(matiere.code)].append(matiere)
        return action
    return _resultat(matiere, ligne, _ecrire(matiere, ligne, champs))


def _importer_besoin(ctx: Contexte, ligne: LigneTypee) -> Action:
    annee = ctx.annee(ligne["annee"])
    semestre = ctx.semestre(annee, ligne["semestre"])
    matiere = ctx.matiere(ligne["matiere"], annee, ligne["promotion"], "matiere")
    type_seance = ctx.type_seance(ligne["type"], "type")
    groupes = [ctx.groupe(annee, code, "groupes") for code in ligne["groupes"]]
    enseignants = [ctx.enseignant(code, "enseignants") for code in ligne["enseignants"] or ()]
    types_salle = [ctx.type_salle(code, "types_salle") for code in ligne["types_salle"] or ()]
    equipements = [ctx.equipement(code, "equipements") for code in ligne["equipements"] or ()]
    salle = ctx.salle(ligne["salle_imposee"], "salle_imposee") if ligne["salle_imposee"] else None
    campus = ctx.campus(ligne["campus_impose"], "campus_impose") if ligne["campus_impose"] else None
    _verifier_besoin(ligne)

    champs = {
        "duree_min": ("duree", ligne["duree"]),
        "mode_volume": ("mode", ligne["mode"]),
        "nombre": ("nombre", ligne["nombre"]),
        "max_par_semaine": ("max_par_semaine", ligne["max_par_semaine"]),
        "date_debut": ("date_debut", ligne["date_debut"]),
        "date_fin": ("date_fin", ligne["date_fin"]),
        "jour_impose": ("jour_impose", ligne["jour_impose"]),
        "debut_impose_min": ("debut_impose", ligne["debut_impose"]),
        "salle_imposee_id": ("salle_imposee", salle.id if salle else None),
        "campus_impose_id": ("campus_impose", campus.id if campus else None),
        "commentaire": ("commentaire", ligne["commentaire"]),
    }
    cle = (
        semestre.id,
        matiere.id,
        type_seance.id,
        frozenset(g.id for g in groupes),
        cle_code(ligne["libelle"] or ""),
        ligne["date"],
    )
    index = ctx.besoins(semestre.id)
    existants = index.get(cle, [])
    if len(existants) > 1:
        numeros = ", ".join(str(b.id) for b in existants)
        raise Rejet(
            "libelle",
            f"Plusieurs besoins existants (n° {numeros}) ont cette matière, ce type, ces groupes "
            "et ce libellé : leur donner des libellés distincts avant de réimporter.",
        )
    if not existants:
        besoin = m.Besoin(
            semestre_id=semestre.id,
            matiere_id=matiere.id,
            type_seance_id=type_seance.id,
            libelle=ligne["libelle"],
            date_ponctuelle=ligne["date"],
            groupes=groupes,
            enseignants=enseignants,
            types_salle=types_salle,
            equipements=equipements,
            **{a: x for a, (_, x) in champs.items()},
        )
        action = _creer(ctx.session, besoin, ligne)
        index[cle] = [besoin]
        return action
    besoin = existants[0]
    change = _ecrire(besoin, ligne, champs)
    for attribut, colonne, nouveaux in (
        ("enseignants", "enseignants", enseignants),
        ("types_salle", "types_salle", types_salle),
        ("equipements", "equipements", equipements),
    ):
        change = _relation(besoin, attribut, colonne, ligne, nouveaux) or change
    return _resultat(besoin, ligne, change)


def _verifier_besoin(ligne: LigneTypee) -> None:
    """Les règles du modèle, vérifiées avant l'écriture pour donner un message clair."""
    mode, date, nombre = ligne["mode"], ligne["date"], ligne["nombre"]
    if not 0 < ligne["duree"] <= MINUTES_PAR_JOUR:
        raise Rejet("duree", "La durée d'une séance va de 1 minute à 24 heures.")
    if mode is ModeVolume.PONCTUEL and date is None:
        raise Rejet("date", "Un besoin ponctuel demande sa date (colonne date).")
    if mode is not ModeVolume.PONCTUEL and date is not None:
        raise Rejet("date", "La date ponctuelle ne vaut que pour le mode « ponctuel ».")
    if mode is ModeVolume.PONCTUEL and nombre != 1:
        raise Rejet("nombre", "Un besoin ponctuel compte une seule séance (nombre = 1).")
    if ligne["date_debut"] and ligne["date_fin"] and ligne["date_debut"] > ligne["date_fin"]:
        raise Rejet("date_fin", "La fin de la période précède son début.")
    if ligne["debut_impose"] is not None and ligne["debut_impose"] >= MINUTES_PAR_JOUR:
        raise Rejet("debut_impose", "L'heure de début imposée doit être avant minuit.")
    if (
        ligne["debut_impose"] is not None
        and ligne["debut_impose"] + ligne["duree"] > MINUTES_PAR_JOUR
    ):
        raise Rejet(
            "debut_impose",
            f"Une séance de {duree_vers_texte(ligne['duree'])} commençant à "
            f"{minutes_vers_texte(ligne['debut_impose'])} dépasserait minuit : avancer l'heure "
            "de début ou raccourcir la durée.",
        )


def _importer_indisponibilite(ctx: Contexte, ligne: LigneTypee) -> Action:
    cible = ligne["cible"]
    ressource = ligne["ressource"]
    cles = {"enseignant_id": None, "salle_id": None, "groupe_id": None}
    if cible is CibleIndisponibilite.ENSEIGNANT:
        cles["enseignant_id"] = ctx.enseignant(ressource, "ressource").id
    elif cible is CibleIndisponibilite.SALLE:
        cles["salle_id"] = ctx.salle(ressource, "ressource").id
    else:
        if ligne["annee"] is None:
            raise Rejet("annee", "L'année est obligatoire pour l'indisponibilité d'un groupe.")
        cles["groupe_id"] = ctx.groupe(ctx.annee(ligne["annee"]), ressource, "ressource").id
    jour, date_debut, date_fin = ligne["jour"], ligne["date_debut"], ligne["date_fin"]
    if jour is not None and date_debut is not None:
        raise Rejet(
            "jour", "Une indisponibilité est récurrente (jour) ou datée (dates), pas les deux."
        )
    if jour is None and date_debut is None:
        raise Rejet("jour", "Renseigner le jour (récurrente) ou la date de début (datée).")
    if date_debut is not None and date_fin < date_debut:
        raise Rejet("date_fin", "Le dernier jour précède le premier.")
    debut, fin = ligne["debut"], ligne["fin"]
    if not 0 <= debut < fin <= MINUTES_PAR_JOUR:
        raise Rejet(
            "fin",
            "La plage horaire doit aller d'une heure de début à une heure de fin plus tardive.",
        )
    cle = (
        cible,
        next(x for x in cles.values() if x is not None),
        jour,
        date_debut,
        date_fin,
        debut,
        fin,
    )
    champs = {
        "motif": ("motif", ligne["motif"]),
        "dure": ("dure", ligne["dure"]),
        "poids": ("poids", ligne["poids"]),
    }
    existants = ctx.indisponibilites.get(cle, [])
    if not existants:
        indisponibilite = m.Indisponibilite(
            cible=cible,
            jour=jour,
            date_debut=date_debut,
            date_fin=date_fin,
            debut_min=debut,
            fin_min=fin,
            **cles,
            **{a: x for a, (_, x) in champs.items()},
        )
        action = _creer(ctx.session, indisponibilite, ligne)
        ctx.indisponibilites.setdefault(cle, []).append(indisponibilite)
        return action
    if len(existants) > 1:
        raise Rejet(
            "ressource",
            f"{len(existants)} indisponibilités identiques existent déjà : impossible de savoir "
            "laquelle mettre à jour.",
        )
    return _resultat(existants[0], ligne, _ecrire(existants[0], ligne, champs))


# --------------------------------------------------------------------------- #
# Règles personnalisées
# --------------------------------------------------------------------------- #

_SORTES_LISTE = (SorteChamp.JOURS, SorteChamp.CODES, SorteChamp.MOMENTS)


def lire_parametres_regle(type_regle: TypeRegle, texte: str | None) -> dict[str, Any]:
    """Cellule « clé = valeur ; clé = valeur » -> brouillon de paramètres (non validé).

    Les listes s'écrivent séparées par des virgules ; une liste vide (« jours = »)
    reste une liste vide. Une clé inconnue est transmise telle quelle : la
    validation du service la refuse avec la liste des clés attendues.
    """
    if not texte:
        return {}
    sortes = {champ.nom: champ.sorte for champ in CATALOGUE_TYPES[type_regle].champs}
    valeurs: dict[str, Any] = {}
    for morceau in texte.split(";"):
        if not morceau.strip():
            continue
        cle, egal, valeur = morceau.partition("=")
        nom = cle_mot(cle)
        if not egal or not nom:
            attendues = ", ".join(sortes) or "aucune"
            raise ErreurDonneesInvalides(
                f"« {morceau.strip()} » : écrire « clé = valeur » (clés de ce type : {attendues})."
            )
        if nom in valeurs:
            raise ErreurDonneesInvalides(f"La clé « {nom} » est donnée deux fois.")
        valeur = valeur.strip()
        if sortes.get(nom) in _SORTES_LISTE:
            valeurs[nom] = [v.strip() for v in valeur.split(",") if v.strip()]
        else:
            valeurs[nom] = valeur
    return valeurs


def ecrire_parametres_regle(type_regle: TypeRegle, parametres: Mapping[str, Any]) -> str:
    """Paramètres canoniques -> « mode = libre ; debut = 11:45 ; duree = 1 h 15 » (relisible)."""
    champs = {champ.nom: champ for champ in CATALOGUE_TYPES[type_regle].champs}
    ordre = [nom for nom in champs if nom in parametres]
    ordre += [nom for nom in parametres if nom not in champs]  # jamais perdu, même inattendu
    morceaux = []
    for nom in ordre:
        valeur = parametres[nom]
        champ = champs.get(nom)
        if isinstance(valeur, (list, tuple)):
            texte = ", ".join(str(x) for x in valeur)
        elif champ is not None and champ.sorte is SorteChamp.DUREE and isinstance(valeur, int):
            texte = duree_vers_texte(valeur)
        else:
            texte = "" if valeur is None else str(valeur)
        morceaux.append(f"{nom} = {texte}".rstrip())
    return " ; ".join(morceaux)


def _cible_regle(
    ctx: Contexte, annee: m.AnneeScolaire, nature: NaturePortee, texte: str | None
) -> str | None:
    """Code de la cible tel que la base l'écrit (casse et espaces normalisés), sinon tel quel.

    Un besoin (« matière / type / groupes ») passe tel quel : le service le
    cherche sans tenir compte de la casse, dans le semestre de la colonne
    `semestre` (`periode.semestre` du brouillon) quand elle est remplie, ce qui
    départage un enseignement présent dans les deux semestres.
    """
    if texte is None or nature is NaturePortee.BESOIN:
        return texte
    cle = cle_code(texte)
    if nature is NaturePortee.GROUPE:
        groupe = ctx.groupes.get((annee.id, cle))
        return groupe.code if groupe else texte
    if nature is NaturePortee.ENSEIGNANT:
        enseignant = ctx.enseignants.get(cle)
        return enseignant.identifiant if enseignant else texte
    if nature is NaturePortee.CAMPUS:
        campus = ctx.campus_par_code.get(cle)
        return campus.code if campus else texte
    if nature is NaturePortee.TYPE_SEANCE:
        type_seance = ctx._liste(m.TypeSeance).get(cle)
        return type_seance.code if type_seance else texte
    if nature is NaturePortee.MATIERE:
        candidates = ctx.matieres.get(cle, [])
        return candidates[0].code if candidates else texte
    return texte


def _colonne_regle(cle: str, message: str) -> tuple[str, str]:
    """Erreur du service (clé = chemin JSON) -> (colonne de la feuille, message)."""
    if cle.startswith("parametres."):
        return "parametres", f"{cle.split('.', 1)[1]} : {message}"
    if cle == "portee.cible":
        return "cible", message
    if cle.startswith("portee"):
        return "portee", message
    if cle.startswith("periode."):
        return cle.split(".", 1)[1], message
    if cle == "periode":
        # « Semestre ou dates, pas les deux », ou un réglage qui refuse des dates : la
        # colonne des dates porte l'erreur.
        return "date_debut", message
    return (cle if cle in ("annee", "code", "type", "niveau", "etat") else ""), message


def _meme_regle(avant: Regle, apres: Regle) -> bool:
    """Vrai si deux règles ont le même contenu (hors état, origine et provenance)."""
    return (
        avant.type is apres.type
        and avant.portee.nature is apres.portee.nature
        and avant.portee.cible_id == apres.portee.cible_id
        and dict(avant.parametres) == dict(apres.parametres)
        and avant.niveau == apres.niveau
        and avant.semestre_id == apres.semestre_id
        and avant.date_debut == apres.date_debut
        and avant.date_fin == apres.date_fin
        and (avant.commentaire or "") == (apres.commentaire or "")
    )


def _importer_regle(ctx: Contexte, ligne: LigneTypee) -> Action:
    """Crée la règle (origine « import ») ou met à jour celle de même année et code.

    La validation est celle du service des règles (`edt.services.regles`) :
    chacune de ses erreurs devient un signalement de la ligne, sur la colonne
    concernée. Un code existant garde son type.
    """
    annee = ctx.annee(ligne["annee"])
    type_regle: TypeRegle = ligne["type"]
    nature: NaturePortee = ligne["portee"]
    erreurs: list[tuple[str, str]] = []
    parametres: dict[str, Any] = {}
    try:
        parametres = lire_parametres_regle(type_regle, ligne["parametres"])
    except ErreurDonneesInvalides as erreur:
        erreurs.append(("parametres", erreur.message))
    if nature is NaturePortee.ETABLISSEMENT and ligne["cible"]:
        erreurs.append(
            ("cible", "Pas de cible pour la portée établissement : laisser la colonne vide.")
        )
    existante = ctx.regles.get((annee.id, cle_code(ligne["code"]))) if ligne["code"] else None
    if existante is not None and TypeRegle(existante.type) is not type_regle:
        erreurs.append(
            (
                "type",
                f"La règle {existante.code} existe déjà en {annee.libelle} avec le type "
                f"{TypeRegle(existante.type).value} : un code garde son type ; choisir un autre "
                "code ou laisser la colonne code vide.",
            )
        )
    if erreurs:
        raise Rejet(*erreurs[0], tuple(erreurs[1:]))

    semestre = ligne["semestre"]
    if semestre:
        trouve = ctx.semestres.get((annee.id, cle_code(semestre)))
        semestre = trouve.code if trouve else semestre
    brouillon: dict[str, Any] = {
        "type": type_regle.value,
        "annee": annee.libelle,
        "portee": {
            "nature": nature.value,
            "cible": _cible_regle(ctx, annee, nature, ligne["cible"]),
        },
        "parametres": parametres,
        "niveau": ligne["niveau"].value if ligne["niveau"] else None,
        "etat": ligne["etat"].value,
        "periode": {
            "semestre": semestre,
            "date_debut": ligne["date_debut"],
            "date_fin": ligne["date_fin"],
        },
        "commentaire": ligne["commentaire"] or "",
    }
    try:
        if existante is None:
            if ligne["code"]:
                brouillon["code"] = ligne["code"]
            detail = service_regles.creer(
                ctx.session, brouillon, OrigineRegle.IMPORT, ligne["etat"]
            )
            regle = ctx.session.get(m.Regle, detail.id)
            _provenance(regle, ligne, creation=True, change=True)
            ctx.regles[(annee.id, cle_code(regle.code))] = regle
            return Action.CREE
        avant = service_regles.lire_par_id(ctx.session, existante.id).regle
        # Validation pour comparer seulement : à l'état inactif, le service ne cherche pas
        # de doublon de réglage (la règle se trouverait elle-même) ; `modifier` le fait.
        verification = service_regles.valider(
            ctx.session, {**brouillon, "code": existante.code, "etat": EtatRegle.INACTIVE.value}
        )
        if not verification.valide:
            raise ErreurSaisie(verification.erreurs)
        change = not _meme_regle(avant, verification.regle) or avant.etat is not ligne["etat"]
        if change:
            # Une cellule décrit tous les paramètres : un paramètre absent reprend son défaut.
            complets = {champ.nom: None for champ in CATALOGUE_TYPES[type_regle].champs}
            brouillon["parametres"] = {**complets, **parametres}
            changements = {cle: valeur for cle, valeur in brouillon.items() if cle != "annee"}
            service_regles.modifier(ctx.session, annee.id, existante.code, changements)
        return _resultat(existante, ligne, change)
    except ErreurSaisie as erreur:
        details = [_colonne_regle(cle, message) for cle, message in erreur.erreurs.items()]
        details = details or [("", erreur.message)]
        raise Rejet(*details[0], tuple(details[1:])) from None


IMPORTEURS: dict[str, Callable[[Contexte, LigneTypee], Action]] = {
    "annees": _importer_annee,
    "semestres": _importer_semestre,
    "campus": _importer_campus,
    "trajets": _importer_trajet,
    "salles": _importer_salle,
    "enseignants": _importer_enseignant,
    "jours_fermes": _importer_jour_ferme,
    "etudiants": _importer_etudiant,
    "inscriptions": _importer_inscription,
    "matieres": _importer_matiere,
    "besoins": _importer_besoin,
    "indisponibilites": _importer_indisponibilite,
    "regles": _importer_regle,
}
"""Fonction d'import d'une ligne, par feuille (les groupes ont leur propre traitement)."""


# --------------------------------------------------------------------------- #
# Groupes : ordre des parents et chevauchements
# --------------------------------------------------------------------------- #


def _importer_groupes(
    ctx: Contexte, lignes: list[LigneTypee], bilan: Bilan, signalements: list[Signalement]
) -> None:
    """Applique les groupes en traitant chaque parent avant ses enfants, puis les chevauchements.

    Un chevauchement est symétrique : déclaré sur l'une des deux lignes, il
    vaut pour les deux groupes. Les chevauchements d'un groupe présent dans
    la feuille (si la colonne `chevauche` est présente) sont remplacés par
    ceux que la feuille déclare.
    """
    cle_ligne = {id(ligne): (cle_code(ligne["annee"]), cle_code(ligne["code"])) for ligne in lignes}
    connus = set(cle_ligne.values()) | {
        (cle_code(groupe.annee.libelle), code) for (_, code), groupe in ctx.groupes.items()
    }
    voulus: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    a_traiter = []
    for ligne in lignes:
        cle = cle_ligne[id(ligne)]
        inconnus = [c for c in ligne["chevauche"] or () if (cle[0], cle_code(c)) not in connus]
        if inconnus:
            _rejeter(
                ligne,
                "chevauche",
                f"Groupe(s) inconnu(s) dans les chevauchements : {', '.join(inconnus)}.",
                bilan,
                signalements,
            )
            continue
        for code in ligne["chevauche"] or ():
            autre = (cle[0], cle_code(code))
            if autre == cle:
                _rejeter(
                    ligne, "chevauche", "Un groupe ne chevauche pas lui-même.", bilan, signalements
                )
                break
            voulus[cle].add(autre)
            voulus[autre].add(cle)
        else:
            a_traiter.append(ligne)
    avec_colonne = {cle_ligne[id(ligne)] for ligne in a_traiter if "chevauche" in ligne.presentes}
    paires = _paires_existantes(ctx.session)
    cle_existante = {
        groupe.id: (cle_code(groupe.annee.libelle), code)
        for (_, code), groupe in ctx.groupes.items()
    }
    partenaires: dict[int, set[tuple[str, str]]] = defaultdict(set)
    for a, b in paires:
        partenaires[a].add(cle_existante[b])
        partenaires[b].add(cle_existante[a])

    def importer(ctx_: Contexte, ligne: LigneTypee) -> Action:
        cle = cle_ligne[id(ligne)]
        action = _importer_groupe(ctx_, ligne)
        if action is Action.INCHANGE and cle in avec_colonne:
            groupe = ctx_.groupe(ctx_.annee(ligne["annee"]), ligne["code"], "code")
            if partenaires.get(groupe.id, set()) != voulus.get(cle, set()):
                action = Action.MAJ
        return action

    ordre, circulaires = _parents_d_abord(a_traiter, cle_ligne)
    traites: list[LigneTypee] = []
    for ligne in ordre:
        if id(ligne) in circulaires:
            _rejeter(
                ligne,
                "parent",
                "Parenté circulaire : ce groupe est, par ses parents, son propre ancêtre.",
                bilan,
                signalements,
            )
        elif _appliquer_ligne(ctx, ligne, importer, bilan, signalements):
            traites.append(ligne)
    _ecrire_chevauchements(ctx, traites, cle_ligne, voulus, avec_colonne, paires, signalements)


def _parents_d_abord(
    lignes: list[LigneTypee], cle_ligne: dict[int, tuple[str, str]]
) -> tuple[list[LigneTypee], set[int]]:
    """Ordonne les lignes pour qu'un parent de la feuille passe avant ses enfants (en temps linéaire).

    Renvoie l'ordre et les lignes prises dans une parenté circulaire. Un
    parent absent de la feuille est cherché en base au moment de la ligne.
    """
    par_cle = {cle_ligne[id(ligne)]: ligne for ligne in lignes}
    ordre: list[LigneTypee] = []
    vues: set[int] = set()
    circulaires: set[int] = set()
    for ligne in lignes:
        chaine: list[LigneTypee] = []
        dans_chaine: set[int] = set()
        courante: LigneTypee | None = ligne
        while courante is not None and id(courante) not in vues:
            vues.add(id(courante))
            dans_chaine.add(id(courante))
            chaine.append(courante)
            parent = courante["parent"]
            courante = (
                par_cle.get((cle_ligne[id(courante)][0], cle_code(parent))) if parent else None
            )
        if courante is not None and id(courante) in dans_chaine:
            debut = next(i for i, x in enumerate(chaine) if x is courante)
            circulaires.update(id(x) for x in chaine[debut:])
        ordre.extend(reversed(chaine))
    return ordre, circulaires


def _importer_groupe(ctx: Contexte, ligne: LigneTypee) -> Action:
    annee = ctx.annee(ligne["annee"])
    parent = ctx.groupe(annee, ligne["parent"], "parent") if ligne["parent"] else None
    groupe = ctx.groupes.get((annee.id, cle_code(ligne["code"])))
    if parent is not None and groupe is not None:
        ancetre: m.Groupe | None = parent
        while ancetre is not None:
            if ancetre.id == groupe.id:
                raise Rejet(
                    "parent", "Parenté circulaire : un groupe ne peut pas contenir son parent."
                )
            ancetre = ancetre.parent
    champs = {
        "libelle": ("libelle", ligne["libelle"]),
        "nature": ("nature", ligne["nature"]),
        "parent_id": ("parent", parent.id if parent else None),
        "effectif": ("effectif", ligne["effectif"]),
        "accessibilite_requise": ("accessibilite_requise", ligne["accessibilite_requise"]),
    }
    if groupe is None:
        groupe = m.Groupe(
            annee_scolaire_id=annee.id, code=ligne["code"], **{a: x for a, (_, x) in champs.items()}
        )
        action = _creer(ctx.session, groupe, ligne)
        ctx.groupes[(annee.id, cle_code(groupe.code))] = groupe
        return action
    return _resultat(groupe, ligne, _ecrire(groupe, ligne, champs))


def _paires_existantes(session: Session) -> set[tuple[int, int]]:
    return {
        (a, b)
        for a, b in session.execute(
            select(m.groupe_chevauchement.c.groupe_a_id, m.groupe_chevauchement.c.groupe_b_id)
        )
    }


def _ecrire_chevauchements(
    ctx: Contexte,
    traites: list[LigneTypee],
    cle_ligne: dict[int, tuple[str, str]],
    voulus: dict[tuple[str, str], set[tuple[str, str]]],
    avec_colonne: set[tuple[str, str]],
    paires: set[tuple[int, int]],
    signalements: list[Signalement],
) -> None:
    par_cle = {
        (cle_code(groupe.annee.libelle), code): groupe for (_, code), groupe in ctx.groupes.items()
    }
    concernes = {cle_ligne[id(ligne)] for ligne in traites} & avec_colonne
    souhaitees: set[tuple[int, int]] = set()
    for ligne in traites:
        cle = cle_ligne[id(ligne)]
        if cle not in concernes:
            continue
        groupe = par_cle[cle]
        for autre in sorted(voulus.get(cle, ())):
            partenaire = par_cle.get(autre)
            if partenaire is None:
                signalements.append(
                    _signalement(
                        Gravite.AVERTISSEMENT,
                        ligne,
                        "chevauche",
                        f"Chevauchement avec « {autre[1]} » non enregistré : ce groupe n'existe "
                        "pas (sa ligne a été rejetée).",
                    )
                )
                continue
            souhaitees.add((min(groupe.id, partenaire.id), max(groupe.id, partenaire.id)))
    ids_concernes = {par_cle[cle].id for cle in concernes}
    a_retirer = {p for p in paires if (p[0] in ids_concernes or p[1] in ids_concernes)} - souhaitees
    a_ajouter = souhaitees - paires
    table = m.groupe_chevauchement
    for a, b in a_retirer:
        ctx.session.execute(delete(table).where(table.c.groupe_a_id == a, table.c.groupe_b_id == b))
    if a_ajouter:
        ctx.session.execute(
            insert(table), [{"groupe_a_id": a, "groupe_b_id": b} for a, b in sorted(a_ajouter)]
        )
    if a_retirer or a_ajouter:
        journal.info(
            "Chevauchements de groupes : %d ajouté(s), %d retiré(s)", len(a_ajouter), len(a_retirer)
        )


# --------------------------------------------------------------------------- #
# Moteur d'import
# --------------------------------------------------------------------------- #


def cle_ligne(feuille: Feuille, ligne: LigneTypee) -> tuple:
    """Identifiant métier d'une ligne, pour repérer les doublons d'un import."""
    if feuille.nom == "trajets":
        return (frozenset({cle_code(ligne["campus_a"]), cle_code(ligne["campus_b"])}),)
    if feuille.nom == "matieres" and ligne["promotion"] is None:
        return (cle_code(ligne["code"]), None, None)
    if feuille.nom == "regles" and ligne["code"] is None:
        return ("sans code", id(ligne))  # code attribué à l'import : jamais un doublon
    return tuple(comparable(feuille.colonne(nom), ligne[nom]) for nom in feuille.identifiant)


def dedoublonner(
    feuille: Feuille, lignes: list[LigneTypee], bilan: Bilan, signalements: list[Signalement]
) -> list[LigneTypee]:
    """Fusionne les lignes identiques de même identifiant ; rejette les suivantes si elles diffèrent."""
    premieres: dict[tuple, LigneTypee] = {}
    retenues = []
    for ligne in lignes:
        cle = cle_ligne(feuille, ligne)
        premiere = premieres.get(cle)
        if premiere is None:
            premieres[cle] = ligne
            retenues.append(ligne)
            continue
        differences = [
            colonne.nom
            for colonne in feuille.colonnes
            if colonne.nom in ligne.presentes & premiere.presentes
            and comparable(colonne, ligne[colonne.nom])
            != comparable(colonne, premiere[colonne.nom])
        ]
        if not differences:
            bilan.ajouter("fusionnes")
            signalements.append(
                _signalement(
                    Gravite.INFORMATION,
                    ligne,
                    "",
                    f"Identique à {premiere.position} (même identifiant, mêmes valeurs) : "
                    "fusionnée avec elle.",
                )
            )
            continue
        _rejeter(
            ligne,
            differences[0],
            f"Même identifiant que {premiere.position}, mais valeurs différentes (colonnes "
            f"{', '.join(differences)}) : ligne rejetée, la première est retenue.",
            bilan,
            signalements,
        )
    return retenues


def _appliquer_ligne(
    ctx: Contexte,
    ligne: LigneTypee,
    importer: Callable[[Contexte, LigneTypee], Action],
    bilan: Bilan,
    signalements: list[Signalement],
) -> bool:
    """Applique une ligne dans son point de sauvegarde ; vrai si elle est entrée."""
    try:
        with ctx.session.begin_nested():
            action = importer(ctx, ligne)
            ctx.session.flush()
    except Rejet as rejet:
        _rejeter(ligne, rejet.colonne, rejet.message, bilan, signalements)
        for colonne, message in rejet.details[1:]:
            signalements.append(_signalement(Gravite.ERREUR, ligne, colonne, message))
        return False
    except IntegrityError as erreur:
        journal.exception(
            "Ligne refusée par la base : %s (feuille %s)", ligne.position, ligne.feuille
        )
        ctx.oublier()
        _rejeter(
            ligne,
            "",
            "La base de données a refusé cette ligne (règle d'intégrité non respectée : "
            f"{erreur.orig}). Les autres lignes ne sont pas affectées.",
            bilan,
            signalements,
        )
        return False
    except OverflowError:
        # Les lectures bornent les nombres ; ce filet garde l'import debout si une valeur
        # hors des limites de SQLite passait quand même.
        journal.exception("Valeur hors limites : %s (feuille %s)", ligne.position, ligne.feuille)
        ctx.oublier()
        _rejeter(
            ligne,
            "",
            "Un nombre de cette ligne dépasse ce que la base peut enregistrer : ligne rejetée, "
            "les autres lignes ne sont pas affectées.",
            bilan,
            signalements,
        )
        return False
    bilan.ajouter(action.value)
    return True


def importer_tableaux(
    session: Session,
    tableaux: Sequence[Tableau],
    signalements: list[Signalement] | None = None,
) -> tuple[list[Bilan], list[Signalement]]:
    """Importe des tableaux de données de l'établissement (une transaction ouverte est requise).

    Ne valide rien : l'appelant (le service) choisit d'annuler (simulation)
    ou de garder les écritures. Les signalements s'ajoutent à `signalements`
    (une liste bornée, de préférence), qui est renvoyée.
    """
    signalements = signalements if signalements is not None else []
    par_feuille: dict[str, list[Tableau]] = defaultdict(list)
    for tableau in tableaux:
        feuille = identifier_feuille(tableau.nom, tableau.entetes)
        if feuille is None:
            signalements.append(
                Signalement(
                    Gravite.ERREUR,
                    f"Tableau « {tableau.nom} » non reconnu : nommer l'onglet ou le fichier comme "
                    f"une feuille du format ({', '.join(f.nom for f in FEUILLES)}). "
                    f"{len(tableau.lignes)} ligne(s) non importée(s).",
                    tableau.fichier,
                    tableau.nom,
                )
            )
            continue
        par_feuille[feuille.nom].append(tableau)

    ctx = Contexte(session)
    bilans = []
    for feuille in FEUILLES:
        if feuille.nom not in par_feuille:
            continue
        bilan = Bilan(feuille.nom)
        lignes = [
            ligne
            for tableau in par_feuille[feuille.nom]
            for ligne in preparer_tableau(
                tableau,
                feuille,
                bilan,
                signalements,
                extra_conserve=feuille.nom in FEUILLES_AVEC_PROVENANCE,
            )
        ]
        lignes = dedoublonner(feuille, lignes, bilan, signalements)
        if feuille.nom == "groupes":
            _importer_groupes(ctx, lignes, bilan, signalements)
        else:
            for ligne in lignes:
                _appliquer_ligne(ctx, ligne, IMPORTEURS[feuille.nom], bilan, signalements)
        journal.info("Feuille %s : %s", feuille.nom, bilan.texte)
        bilans.append(bilan)
    return bilans, signalements


def _signalement(gravite: Gravite, ligne: LigneTypee, colonne: str, message: str) -> Signalement:
    return Signalement(gravite, message, ligne.fichier, ligne.feuille, ligne.numero, colonne)


def _rejeter(
    ligne: LigneTypee, colonne: str, message: str, bilan: Bilan, signalements: list[Signalement]
) -> None:
    bilan.ajouter("rejetes")
    signalements.append(_signalement(Gravite.ERREUR, ligne, colonne, message))
    journal.debug("Ligne rejetée (%s, colonne %s) : %s", ligne.position, colonne, message)


# --------------------------------------------------------------------------- #
# Tout remplacer
# --------------------------------------------------------------------------- #

TABLES_REMPLACEES: tuple[tuple[str, type], ...] = (
    ("règles personnalisées", m.Regle),
    ("versions d'emploi du temps", m.VersionEdt),
    ("calculs", m.Calcul),
    ("besoins", m.Besoin),
    ("indisponibilités", m.Indisponibilite),
    ("étudiants", m.Etudiant),
    ("matières", m.Matiere),
    ("jours fermés", m.JourFerme),
    ("groupes", m.Groupe),
    ("enseignants", m.Enseignant),
    ("salles", m.Salle),
    ("trajets", m.Trajet),
    ("campus", m.Campus),
)
"""Ce que « tout remplacer » supprime, dans l'ordre imposé par les clés étrangères.

Sont conservés : années, semestres, grille horaire, listes configurables
(types de salle, équipements, types de séance) et paramètres.
"""


def compter_donnees(session: Session) -> dict[str, int]:
    """Nombre de lignes que « tout remplacer » supprimerait, par libellé."""
    return {
        libelle: session.scalar(select(func.count()).select_from(modele)) or 0
        for libelle, modele in TABLES_REMPLACEES
    }


def vider_donnees(session: Session) -> dict[str, int]:
    """Supprime les données de l'établissement (voir `TABLES_REMPLACEES`) ; renvoie les comptes."""
    comptes = compter_donnees(session)
    for _, modele in TABLES_REMPLACEES:
        session.execute(delete(modele))
    session.flush()
    session.expire_all()
    journal.warning(
        "Tout remplacer : données supprimées avant import (%s)",
        ", ".join(f"{n} {libelle}" for libelle, n in comptes.items() if n),
    )
    return comptes


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #


def exporter_donnees(session: Session) -> list[tuple[Feuille, list[dict[str, Any]]]]:
    """Toutes les données de l'établissement, feuille par feuille, en valeurs typées."""
    salles = list(
        session.scalars(
            select(m.Salle).options(selectinload(m.Salle.equipements), selectinload(m.Salle.campus))
        )
    )
    reference_salle = references_salles(salles)
    groupes = list(session.scalars(select(m.Groupe).options(selectinload(m.Groupe.annee))))
    groupes_par_id = {g.id: g for g in groupes}
    exportateurs: dict[str, Callable[[], Iterable[dict[str, Any]]]] = {
        "annees": lambda: _exporter_annees(session),
        "semestres": lambda: _exporter_semestres(session),
        "campus": lambda: _exporter_campus(session),
        "trajets": lambda: _exporter_trajets(session),
        "salles": lambda: _exporter_salles(salles, reference_salle),
        "enseignants": lambda: _exporter_enseignants(session),
        "groupes": lambda: _exporter_groupes(session, groupes, groupes_par_id),
        "jours_fermes": lambda: _exporter_jours_fermes(session, groupes_par_id),
        "etudiants": lambda: _exporter_etudiants(session),
        "inscriptions": lambda: _exporter_inscriptions(session),
        "matieres": lambda: _exporter_matieres(session),
        "besoins": lambda: _exporter_besoins(session, reference_salle),
        "indisponibilites": lambda: _exporter_indisponibilites(
            session, reference_salle, groupes_par_id
        ),
        "regles": lambda: _exporter_regles(session),
    }
    return [(feuille, list(exportateurs[feuille.nom]())) for feuille in FEUILLES]


def references_salles(salles: Sequence[m.Salle]) -> dict[int, str]:
    """Référence de chaque salle : son code, ou « code@campus » si le code est ambigu."""
    nombre = defaultdict(int)
    for salle in salles:
        nombre[cle_code(salle.code)] += 1
    return {
        salle.id: salle.code
        if nombre[cle_code(salle.code)] == 1
        else f"{salle.code}@{salle.campus.code}"
        for salle in salles
    }


def _exporter_annees(session: Session) -> Iterable[dict[str, Any]]:
    for annee in session.scalars(select(m.AnneeScolaire).order_by(m.AnneeScolaire.date_debut)):
        yield {"libelle": annee.libelle, "date_debut": annee.date_debut, "date_fin": annee.date_fin}


def _exporter_semestres(session: Session) -> Iterable[dict[str, Any]]:
    requete = (
        select(m.Semestre)
        .join(m.AnneeScolaire)
        .order_by(m.AnneeScolaire.date_debut, m.Semestre.date_debut, m.Semestre.code)
    )
    for semestre in session.scalars(requete):
        yield {
            "annee": semestre.annee.libelle,
            "code": semestre.code,
            "libelle": semestre.libelle,
            "date_debut": semestre.date_debut,
            "date_fin": semestre.date_fin,
        }


def _exporter_campus(session: Session) -> Iterable[dict[str, Any]]:
    for campus in session.scalars(select(m.Campus).order_by(m.Campus.code)):
        yield {"code": campus.code, "nom": campus.nom, "prefixe_salle": campus.prefixe_salle}


def _exporter_trajets(session: Session) -> Iterable[dict[str, Any]]:
    campus = {c.id: c.code for c in session.scalars(select(m.Campus))}
    lignes = []
    for trajet in session.scalars(select(m.Trajet)):
        a, b = sorted((campus[trajet.campus_a_id], campus[trajet.campus_b_id]))
        lignes.append({"campus_a": a, "campus_b": b, "duree": trajet.duree_min})
    return sorted(lignes, key=lambda x: (x["campus_a"], x["campus_b"]))


def _exporter_salles(
    salles: Sequence[m.Salle], reference: dict[int, str]
) -> Iterable[dict[str, Any]]:
    for salle in sorted(salles, key=lambda s: (s.campus.code, s.code)):
        yield {
            "code": salle.code,
            "campus": salle.campus.code,
            "capacite": salle.capacite,
            "type": salle.type_salle.code,
            "batiment": salle.batiment,
            "etage": salle.etage,
            "equipements": tuple(e.code for e in salle.equipements) or None,
            "accessible": salle.accessibilite,
        }


def _exporter_enseignants(session: Session) -> Iterable[dict[str, Any]]:
    for e in session.scalars(select(m.Enseignant).order_by(m.Enseignant.identifiant)):
        yield {
            "identifiant": e.identifiant,
            "civilite": e.civilite,
            "nom": e.nom,
            "prenom": e.prenom,
            "statut": e.statut,
            "max_par_jour": e.max_minutes_jour,
            "max_par_semaine": e.max_minutes_semaine,
            "fictif": e.fictif,
        }


def _exporter_groupes(
    session: Session, groupes: Sequence[m.Groupe], par_id: dict[int, m.Groupe]
) -> Iterable[dict[str, Any]]:
    chevauche: dict[int, list[str]] = defaultdict(list)
    for a, b in _paires_existantes(session):
        premier, second = sorted((par_id[a], par_id[b]), key=lambda g: cle_code(g.code))
        chevauche[premier.id].append(second.code)
    for groupe in sorted(groupes, key=lambda g: (g.annee.date_debut, g.code)):
        yield {
            "annee": groupe.annee.libelle,
            "code": groupe.code,
            "libelle": groupe.libelle,
            "nature": groupe.nature,
            "parent": par_id[groupe.parent_id].code if groupe.parent_id else None,
            "effectif": groupe.effectif,
            "accessibilite_requise": groupe.accessibilite_requise,
            "chevauche": tuple(sorted(chevauche[groupe.id])) or None,
        }


def _exporter_jours_fermes(
    session: Session, groupes: dict[int, m.Groupe]
) -> Iterable[dict[str, Any]]:
    requete = select(m.JourFerme).options(selectinload(m.JourFerme.annee))
    lignes = [
        {
            "annee": j.annee.libelle,
            "date_debut": j.date_debut,
            "date_fin": j.date_fin,
            "libelle": j.libelle,
            "nature": j.nature,
            "groupe": groupes[j.groupe_id].code if j.groupe_id else None,
        }
        for j in session.scalars(requete)
    ]
    return sorted(lignes, key=lambda x: (x["date_debut"], x["date_fin"], x["groupe"] or ""))


def _exporter_etudiants(session: Session) -> Iterable[dict[str, Any]]:
    for e in session.scalars(select(m.Etudiant).order_by(m.Etudiant.identifiant)):
        yield {"identifiant": e.identifiant, "nom": e.nom, "prenom": e.prenom}


def _exporter_inscriptions(session: Session) -> Iterable[dict[str, Any]]:
    requete = select(m.Etudiant).options(
        selectinload(m.Etudiant.groupes).selectinload(m.Groupe.annee)
    )
    lignes = [
        {"annee": g.annee.libelle, "etudiant": e.identifiant, "groupe": g.code}
        for e in session.scalars(requete)
        for g in e.groupes
    ]
    return sorted(lignes, key=lambda x: (x["annee"], x["etudiant"], x["groupe"]))


def _exporter_matieres(session: Session) -> Iterable[dict[str, Any]]:
    requete = select(m.Matiere).options(
        selectinload(m.Matiere.promotion).selectinload(m.Groupe.annee)
    )
    lignes = [
        {
            "code": x.code,
            "libelle": x.libelle,
            "annee": x.promotion.annee.libelle if x.promotion else None,
            "promotion": x.promotion.code if x.promotion else None,
        }
        for x in session.scalars(requete)
    ]
    return sorted(lignes, key=lambda x: (x["code"], x["annee"] or "", x["promotion"] or ""))


def _exporter_besoins(
    session: Session, reference_salle: dict[int, str]
) -> Iterable[dict[str, Any]]:
    requete = select(m.Besoin).options(
        selectinload(m.Besoin.semestre).selectinload(m.Semestre.annee),
        selectinload(m.Besoin.matiere).selectinload(m.Matiere.promotion),
        selectinload(m.Besoin.type_seance),
        selectinload(m.Besoin.groupes),
        selectinload(m.Besoin.enseignants),
        selectinload(m.Besoin.types_salle),
        selectinload(m.Besoin.equipements),
        selectinload(m.Besoin.campus_impose),
    )
    lignes = []
    for b in session.scalars(requete):
        lignes.append(
            {
                "annee": b.semestre.annee.libelle,
                "semestre": b.semestre.code,
                "matiere": b.matiere.code,
                "promotion": b.matiere.promotion.code if b.matiere.promotion else None,
                "type": b.type_seance.code,
                "libelle": b.libelle,
                "groupes": tuple(sorted(g.code for g in b.groupes)),
                "enseignants": tuple(sorted(e.identifiant for e in b.enseignants)) or None,
                "duree": b.duree_min,
                "mode": b.mode_volume,
                "nombre": b.nombre,
                "max_par_semaine": b.max_par_semaine,
                "date_debut": b.date_debut,
                "date_fin": b.date_fin,
                "date": b.date_ponctuelle,
                "jour_impose": b.jour_impose,
                "debut_impose": b.debut_impose_min,
                "salle_imposee": reference_salle[b.salle_imposee_id]
                if b.salle_imposee_id
                else None,
                "campus_impose": b.campus_impose.code if b.campus_impose else None,
                "types_salle": tuple(t.code for t in b.types_salle) or None,
                "equipements": tuple(e.code for e in b.equipements) or None,
                "commentaire": b.commentaire,
                "_ordre": b.id,
            }
        )
    lignes.sort(
        key=lambda x: (
            x["annee"],
            x["semestre"],
            x["matiere"],
            x["type"],
            x["groupes"],
            x["_ordre"],
        )
    )
    for ligne in lignes:
        del ligne["_ordre"]
    return lignes


def _exporter_indisponibilites(
    session: Session, reference_salle: dict[int, str], groupes: dict[int, m.Groupe]
) -> Iterable[dict[str, Any]]:
    enseignants = {e.id: e.identifiant for e in session.scalars(select(m.Enseignant))}
    lignes = []
    for i in session.scalars(select(m.Indisponibilite)):
        cible = CibleIndisponibilite(i.cible)
        if cible is CibleIndisponibilite.ENSEIGNANT:
            ressource, annee = enseignants[i.enseignant_id], None
        elif cible is CibleIndisponibilite.SALLE:
            ressource, annee = reference_salle[i.salle_id], None
        else:
            groupe = groupes[i.groupe_id]
            ressource, annee = groupe.code, groupe.annee.libelle
        lignes.append(
            {
                "cible": cible,
                "ressource": ressource,
                "annee": annee,
                "jour": i.jour,
                "date_debut": i.date_debut,
                "date_fin": i.date_fin,
                "debut": i.debut_min,
                "fin": i.fin_min,
                "motif": i.motif,
                "dure": i.dure,
                "poids": i.poids,
            }
        )
    return sorted(
        lignes,
        key=lambda x: (
            x["cible"].value,
            x["ressource"],
            -1 if x["jour"] is None else x["jour"],
            x["date_debut"].isoformat() if x["date_debut"] else "",
            x["debut"],
        ),
    )


def description_besoin(besoin: m.Besoin) -> str:
    """Un besoin désigné par « matière / type / groupes » (cible d'une règle)."""
    groupes = ", ".join(sorted(g.code for g in besoin.groupes))
    return f"{besoin.matiere.code} / {besoin.type_seance.code} / {groupes}"


def _exporter_regles(session: Session) -> Iterable[dict[str, Any]]:
    """Règles de toutes les années, tous états confondus, avec leur phrase."""
    for annee in session.scalars(select(m.AnneeScolaire).order_by(m.AnneeScolaire.date_debut)):
        for detail in service_regles.lister(session, annee.id):
            regle = detail.regle
            cible = regle.portee.cible_code
            if regle.portee.nature is NaturePortee.BESOIN and regle.portee.cible_id is not None:
                cible = description_besoin(session.get(m.Besoin, regle.portee.cible_id))
            yield {
                "annee": detail.annee_libelle,
                "code": regle.code,
                "type": regle.type,
                "portee": regle.portee.nature,
                "cible": cible,
                "parametres": ecrire_parametres_regle(regle.type, regle.parametres) or None,
                "niveau": regle.niveau,
                "etat": regle.etat,
                "semestre": regle.semestre_code,
                "date_debut": regle.date_debut,
                "date_fin": regle.date_fin,
                "commentaire": regle.commentaire or None,
                "origine": regle.origine.value,
                "phrase": detail.phrase,
            }
