"""Personnes et groupes : enseignants, groupes (arbre, chevauchements), étudiants."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from edt.db.modeles import (
    AnneeScolaire,
    Besoin,
    Enseignant,
    Etudiant,
    Groupe,
    Indisponibilite,
    JourFerme,
    Matiere,
    Semestre,
    besoin_enseignant,
    besoin_groupe,
    groupe_chevauchement,
    inscription,
)
from edt.domaine.enumerations import NatureGroupe, StatutEnseignant
from edt.domaine.heures import duree_vers_texte
from edt.services.referentiels.description import (
    BESOINS,
    EFFECTIF_MAX,
    TOUS_SEMESTRES,
    Champ,
    Colonne,
    Decompte,
    Entite,
    Filtre,
    Reference,
    TypeChamp,
    Unicite,
    Valeurs,
    choix_enum,
    libelle_choix,
    options_reference,
    par_colonne,
)
from edt.services.referentiels.operations import Raison

journal = logging.getLogger(__name__)

REF_ANNEE = Reference(AnneeScolaire, lambda a: a.libelle, ordre=(AnneeScolaire.date_debut.desc(),))
REF_GROUPE = Reference(
    Groupe,
    lambda g: f"{g.code} - {g.libelle}",
    ordre=(Groupe.annee_scolaire_id.desc(), Groupe.code),
)
REF_ENSEIGNANT = Reference(
    Enseignant,
    lambda e: f"{e.nom_affiche} ({e.identifiant})",
    ordre=(Enseignant.nom, Enseignant.prenom, Enseignant.identifiant),
)

CHOIX_NATURE = choix_enum(NatureGroupe)
CHOIX_STATUT = choix_enum(StatutEnseignant)


def annee_par_defaut(session: Session) -> int | None:
    """Année scolaire proposée par défaut : celle du semestre en cours (ou à venir).

    `None` si aucune année n'existe encore.
    """
    from edt.services.calendrier import semestre_par_defaut

    if session.scalar(select(Semestre.id).limit(1)) is None:
        return None
    return semestre_par_defaut(session).annee_scolaire_id


def _annee_par_defaut_texte(session: Session) -> str | None:
    annee = annee_par_defaut(session)
    return None if annee is None else str(annee)


def _pluriel(nombre: int, singulier: str, pluriel: str) -> str:
    return f"{nombre} {singulier if nombre == 1 else pluriel}"


# --------------------------------------------------------------------------- #
# Enseignants
# --------------------------------------------------------------------------- #

ENSEIGNANT = Entite(
    code="enseignant",
    modele=Enseignant,
    nom="enseignant",
    pluriel="enseignants",
    feminin=False,
    demonstratif="Cet enseignant",
    nouveau="Nouvel enseignant",
    libelle=lambda e: e.nom_affiche,
    champs=(
        Champ(
            "identifiant",
            "Identifiant",
            obligatoire=True,
            aide="Matricule stable et unique ; il sert à retrouver l'enseignant lors d'un import.",
        ),
        Champ("civilite", "Civilité", aide="« Mme » ou « M. »."),
        Champ("nom", "Nom", obligatoire=True),
        Champ("prenom", "Prénom"),
        Champ(
            "statut",
            "Statut",
            TypeChamp.CHOIX,
            obligatoire=True,
            defaut=StatutEnseignant.PERMANENT.value,
            choix=CHOIX_STATUT,
        ),
        Champ(
            "max_minutes_jour",
            "Maximum d'enseignement par jour",
            TypeChamp.DUREE,
            minimum=0,
            maximum=24 * 60,
            aide="Facultatif. Par exemple « 6 h ».",
        ),
        Champ(
            "max_minutes_semaine",
            "Maximum d'enseignement par semaine",
            TypeChamp.DUREE,
            minimum=0,
            maximum=7 * 24 * 60,
            aide="Facultatif. Par exemple « 18 h ».",
        ),
        Champ(
            "fictif",
            "Enseignant de remplacement (fictif)",
            TypeChamp.BOOLEEN,
            defaut=False,
            aide="Remplace un enseignant inconnu : il n'entre dans aucune contrainte d'enseignant.",
        ),
    ),
    colonnes=(
        Colonne("identifiant", "Identifiant", lambda e: e.identifiant, tri=Enseignant.identifiant),
        Colonne("nom", "Nom", lambda e: e.nom, tri=(Enseignant.nom, Enseignant.prenom)),
        Colonne("prenom", "Prénom", lambda e: e.prenom, tri=Enseignant.prenom),
        Colonne(
            "statut",
            "Statut",
            lambda e: libelle_choix(CHOIX_STATUT, e.statut),
            tri=Enseignant.statut,
        ),
        Colonne(
            "max_minutes_jour",
            "Max. par jour",
            lambda e: _duree(e.max_minutes_jour),
            tri=Enseignant.max_minutes_jour,
        ),
        Colonne(
            "max_minutes_semaine",
            "Max. par semaine",
            lambda e: _duree(e.max_minutes_semaine),
            tri=Enseignant.max_minutes_semaine,
        ),
        Colonne("fictif", "Fictif", lambda e: e.fictif, tri=Enseignant.fictif),
    ),
    tri_defaut="nom",
    recherche=(Enseignant.identifiant, Enseignant.nom, Enseignant.prenom),
    filtres=(
        Filtre(
            "statut",
            "Statut",
            lambda s: [(str(v), libelle) for v, libelle in CHOIX_STATUT],
            lambda v: Enseignant.statut == v,
        ),
    ),
    uniques=(
        Unicite(("identifiant",), "Un enseignant porte déjà cet identifiant.", "identifiant"),
    ),
    usages=(
        Decompte(
            par_colonne(besoin_enseignant.c.enseignant_id),
            "assure 1 besoin",
            "assure {n} besoins",
            lien=TOUS_SEMESTRES + "&enseignant={id}",
            objets=BESOINS,
        ),
    ),
    consequences=(
        Decompte(
            par_colonne(Indisponibilite.enseignant_id),
            "Son indisponibilité sera supprimée avec lui",
            "Ses {n} indisponibilités seront supprimées avec lui",
        ),
    ),
)


def _duree(minutes: int | None) -> str | None:
    return None if minutes is None else duree_vers_texte(minutes)


# --------------------------------------------------------------------------- #
# Groupes
# --------------------------------------------------------------------------- #


def sous_arbre(session: Session, groupe_id: int) -> set[int]:
    """Le groupe et tous ses descendants (identifiants)."""
    return sous_arbres(session, (groupe_id,))


def sous_arbres(session: Session, groupe_ids: Iterable[int]) -> set[int]:
    """Les groupes donnés et tous leurs descendants (identifiants)."""
    enfants: dict[int, list[int]] = {}
    for ident, parent in session.execute(select(Groupe.id, Groupe.parent_id)):
        if parent is not None:
            enfants.setdefault(parent, []).append(ident)
    resultat = set(groupe_ids)
    a_visiter = list(resultat)
    while a_visiter:
        for enfant in enfants.get(a_visiter.pop(), []):
            if enfant not in resultat:
                resultat.add(enfant)
                a_visiter.append(enfant)
    return resultat


def chevauchements_de(session: Session, groupe_id: int) -> list[int]:
    """Identifiants des groupes déclarés chevauchants avec `groupe_id`."""
    lignes = session.execute(
        select(groupe_chevauchement.c.groupe_a_id, groupe_chevauchement.c.groupe_b_id).where(
            or_(
                groupe_chevauchement.c.groupe_a_id == groupe_id,
                groupe_chevauchement.c.groupe_b_id == groupe_id,
            )
        )
    )
    return sorted(b if a == groupe_id else a for a, b in lignes)


def _valider_groupe(session: Session, valeurs: Valeurs, actuel: Groupe | None) -> dict[str, str]:
    erreurs: dict[str, str] = {}
    annee = valeurs.get("annee_scolaire_id")
    parent_id = valeurs.get("parent_id")
    if parent_id is not None:
        parent = session.get(Groupe, parent_id)
        if valeurs.get("nature") == NatureGroupe.PROMOTION.value:
            erreurs["parent_id"] = "Une promotion n'est contenue dans aucun groupe."
        elif annee is not None and parent is not None and parent.annee_scolaire_id != annee:
            erreurs["parent_id"] = "Le groupe contenant doit être de la même année scolaire."
        elif actuel is not None and parent_id in sous_arbre(session, actuel.id):
            erreurs["parent_id"] = (
                "Un groupe ne peut pas être contenu dans lui-même ni dans l'un de ses sous-groupes."
            )
    autres = valeurs.get("chevauchements") or []
    if actuel is not None and actuel.id in autres:
        erreurs["chevauchements"] = "Un groupe ne peut pas chevaucher lui-même."
    elif autres and annee is not None:
        hors_annee = session.scalar(
            select(func.count()).where(Groupe.id.in_(autres), Groupe.annee_scolaire_id != annee)
        )
        if hors_annee:
            erreurs["chevauchements"] = "Les groupes chevauchants doivent être de la même année."
    return erreurs


def _preparer_groupe(session: Session, valeurs: Valeurs) -> Valeurs:
    valeurs.pop("chevauchements", None)
    return valeurs


def _ecrire_chevauchements(session: Session, groupe: Groupe, valeurs: Valeurs) -> None:
    session.execute(
        delete(groupe_chevauchement).where(
            or_(
                groupe_chevauchement.c.groupe_a_id == groupe.id,
                groupe_chevauchement.c.groupe_b_id == groupe.id,
            )
        )
    )
    paires = [sorted((groupe.id, autre)) for autre in valeurs.get("chevauchements") or []]
    if paires:
        session.execute(
            insert(groupe_chevauchement),
            [{"groupe_a_id": a, "groupe_b_id": b} for a, b in paires],
        )


def _analyser_groupe(session: Session, groupe: Groupe) -> tuple[list[Raison], list[str]]:
    arbre = sous_arbre(session, groupe.id)
    sous_groupes = len(arbre) - 1
    # Le filtre « Groupe » des besoins montre exactement les besoins qui bloquent.
    lien = f"{TOUS_SEMESTRES}&groupe={groupe.id}"
    refus: list[Raison] = []
    nb_besoins = session.scalar(
        select(func.count(func.distinct(besoin_groupe.c.besoin_id))).where(
            besoin_groupe.c.groupe_id.in_(arbre)
        )
    )
    if nb_besoins:
        sujet = "Ce groupe et ses sous-groupes sont suivis" if sous_groupes else "Ce groupe suit"
        verbe = "par " if sous_groupes else ""
        refus.append(
            Raison(
                f"{sujet} {verbe}{_pluriel(nb_besoins, 'besoin', 'besoins')}.",
                lien,
                "Voir les besoins concernés",
            )
        )
    matieres = select(Matiere.id).where(Matiere.promotion_id.in_(arbre))
    nb_besoins_matieres = session.scalar(
        select(func.count()).where(Besoin.matiere_id.in_(matieres))
    )
    if nb_besoins_matieres:
        refus.append(
            Raison(
                "Les matières rattachées à ce groupe sont enseignées par "
                f"{_pluriel(nb_besoins_matieres, 'besoin', 'besoins')}.",
                lien,
                "Voir les besoins concernés",
            )
        )
    consequences: list[str] = []
    decomptes = (
        (sous_groupes, "sous-groupe", "sous-groupes"),
        (
            session.scalar(select(func.count()).where(Matiere.promotion_id.in_(arbre))),
            "matière rattachée",
            "matières rattachées",
        ),
        (
            session.scalar(select(func.count()).where(Indisponibilite.groupe_id.in_(arbre))),
            "indisponibilité",
            "indisponibilités",
        ),
        (
            session.scalar(select(func.count()).where(JourFerme.groupe_id.in_(arbre))),
            "jour fermé propre au groupe",
            "jours fermés propres au groupe",
        ),
        (
            session.scalar(select(func.count()).where(inscription.c.groupe_id.in_(arbre))),
            "inscription d'étudiant",
            "inscriptions d'étudiants",
        ),
    )
    for nombre, singulier, pluriel in decomptes:
        if nombre:
            accord = "sera supprimée" if nombre == 1 else "seront supprimées"
            if singulier in ("sous-groupe", "jour fermé propre au groupe"):
                accord = "sera supprimé" if nombre == 1 else "seront supprimés"
            consequences.append(f"{_pluriel(nombre, singulier, pluriel)} {accord}.")
    return refus, consequences


Parent = aliased(Groupe)

GROUPE = Entite(
    code="groupe",
    modele=Groupe,
    nom="groupe",
    pluriel="groupes",
    feminin=False,
    demonstratif="Ce groupe",
    nouveau="Nouveau groupe",
    libelle=lambda g: g.code,
    champs=(
        Champ(
            "annee_scolaire_id",
            "Année scolaire",
            TypeChamp.REFERENCE,
            obligatoire=True,
            reference=REF_ANNEE,
        ),
        Champ("code", "Code", obligatoire=True, aide="Unique dans l'année, par exemple « A1-G2 »."),
        Champ("libelle", "Libellé", obligatoire=True),
        Champ(
            "nature",
            "Nature",
            TypeChamp.CHOIX,
            obligatoire=True,
            defaut=NatureGroupe.CLASSE.value,
            choix=CHOIX_NATURE,
            aide="Promotion > classe > demi-groupe ; transverse (langue, option) ; tiers-temps.",
        ),
        Champ(
            "parent_id",
            "Contenu dans",
            TypeChamp.REFERENCE,
            reference=REF_GROUPE,
            aide="Groupe qui le contient (vide pour une promotion ou un groupe à part).",
        ),
        Champ(
            "effectif",
            "Effectif",
            TypeChamp.ENTIER,
            obligatoire=True,
            defaut=0,
            minimum=0,
            maximum=EFFECTIF_MAX,
            unite="étudiants",
        ),
        Champ(
            "accessibilite_requise",
            "Salle accessible requise",
            TypeChamp.BOOLEEN,
            defaut=False,
            aide="Toutes les séances du groupe auront lieu en salle accessible.",
        ),
        Champ(
            "chevauchements",
            "Groupes chevauchants",
            TypeChamp.REFERENCES,
            reference=REF_GROUPE,
            virtuel=True,
            lire=lambda session, g: chevauchements_de(session, g.id),
            large=True,
            aide="Groupes qui partagent des étudiants avec celui-ci : jamais de cours en même temps.",
        ),
    ),
    colonnes=(
        Colonne("code", "Code", lambda g: g.code, tri=Groupe.code),
        Colonne("libelle", "Libellé", lambda g: g.libelle, tri=Groupe.libelle, texte=True),
        Colonne(
            "nature", "Nature", lambda g: libelle_choix(CHOIX_NATURE, g.nature), tri=Groupe.nature
        ),
        Colonne(
            "parent", "Contenu dans", lambda g: g.parent.code if g.parent else None, tri=Parent.code
        ),
        Colonne("effectif", "Effectif", lambda g: g.effectif, tri=Groupe.effectif, nombre=True),
        Colonne(
            "accessibilite_requise",
            "Salle accessible",
            lambda g: g.accessibilite_requise,
            tri=Groupe.accessibilite_requise,
        ),
        Colonne(
            "annee",
            "Année",
            lambda g: g.annee.libelle,
            tri=(AnneeScolaire.date_debut, Groupe.code),
        ),
    ),
    tri_defaut="code",
    recherche=(Groupe.code, Groupe.libelle, Parent.code),
    jointures=(
        (Parent, Groupe.parent_id == Parent.id),
        (AnneeScolaire, Groupe.annee_scolaire_id == AnneeScolaire.id),
    ),
    chargements=(selectinload(Groupe.parent), selectinload(Groupe.annee)),
    filtres=(
        Filtre(
            "annee",
            "Année",
            lambda s: options_reference(s, REF_ANNEE),
            lambda v: Groupe.annee_scolaire_id == int(v),
            defaut=_annee_par_defaut_texte,
            champ="annee_scolaire_id",
        ),
        Filtre(
            "nature",
            "Nature",
            lambda s: [(str(v), libelle) for v, libelle in CHOIX_NATURE],
            lambda v: Groupe.nature == v,
            champ="nature",
        ),
        Filtre(
            "parent",
            "Contenu dans",
            lambda s: options_reference(s, REF_GROUPE),
            lambda v: Groupe.parent_id == int(v),
            champ="parent_id",
        ),
    ),
    uniques=(
        Unicite(("code", "annee_scolaire_id"), "Un groupe porte déjà ce code cette année.", "code"),
    ),
    valider=_valider_groupe,
    preparer=_preparer_groupe,
    apres_ecriture=_ecrire_chevauchements,
    analyser=_analyser_groupe,
)


@dataclass(frozen=True)
class NoeudGroupe:
    """Un groupe dans l'arbre d'une année, avec ses sous-groupes."""

    id: int
    code: str
    libelle: str
    nature: str
    nature_libelle: str
    effectif: int
    accessibilite_requise: bool
    chevauchements: tuple[str, ...]
    enfants: tuple[NoeudGroupe, ...]

    @property
    def effectif_enfants(self) -> int | None:
        """Somme des effectifs des sous-groupes (`None` sans sous-groupe)."""
        return sum(enfant.effectif for enfant in self.enfants) if self.enfants else None


_ORDRE_NATURES = {membre.value: rang for rang, membre in enumerate(NatureGroupe)}


def arbre_groupes(session: Session, annee_scolaire_id: int) -> tuple[NoeudGroupe, ...]:
    """Les groupes d'une année en arbre : promotions, puis groupes à part, avec leurs sous-groupes."""
    groupes = list(
        session.scalars(select(Groupe).where(Groupe.annee_scolaire_id == annee_scolaire_id))
    )
    codes = {g.id: g.code for g in groupes}
    paires = session.execute(
        select(groupe_chevauchement.c.groupe_a_id, groupe_chevauchement.c.groupe_b_id).where(
            groupe_chevauchement.c.groupe_a_id.in_(codes)
        )
    ).all()
    voisins: dict[int, list[str]] = {}
    for a, b in paires:
        voisins.setdefault(a, []).append(codes.get(b, str(b)))
        voisins.setdefault(b, []).append(codes.get(a, str(a)))
    enfants: dict[int | None, list[Groupe]] = {}
    for groupe in groupes:
        parent = groupe.parent_id if groupe.parent_id in codes else None
        enfants.setdefault(parent, []).append(groupe)

    def cle(groupe: Groupe) -> tuple[int, str]:
        return (_ORDRE_NATURES.get(str(groupe.nature), 99), groupe.code)

    def noeud(groupe: Groupe, chemin: frozenset[int]) -> NoeudGroupe:
        sous = [g for g in enfants.get(groupe.id, []) if g.id not in chemin]
        return NoeudGroupe(
            id=groupe.id,
            code=groupe.code,
            libelle=groupe.libelle,
            nature=str(groupe.nature),
            nature_libelle=libelle_choix(CHOIX_NATURE, groupe.nature),
            effectif=groupe.effectif,
            accessibilite_requise=groupe.accessibilite_requise,
            chevauchements=tuple(sorted(voisins.get(groupe.id, []))),
            enfants=tuple(noeud(g, chemin | {g.id}) for g in sorted(sous, key=cle)),
        )

    return tuple(noeud(g, frozenset({g.id})) for g in sorted(enfants.get(None, []), key=cle))


# --------------------------------------------------------------------------- #
# Étudiants
# --------------------------------------------------------------------------- #

ETUDIANT = Entite(
    code="etudiant",
    modele=Etudiant,
    nom="étudiant",
    pluriel="étudiants",
    feminin=False,
    demonstratif="Cet étudiant",
    nouveau="Nouvel étudiant",
    libelle=lambda e: f"{e.prenom + ' ' if e.prenom else ''}{e.nom} ({e.identifiant})",
    champs=(
        Champ("identifiant", "Identifiant", obligatoire=True, aide="Identifiant stable et unique."),
        Champ("nom", "Nom", obligatoire=True),
        Champ("prenom", "Prénom"),
        Champ(
            "groupes",
            "Inscrit dans les groupes",
            TypeChamp.REFERENCES,
            reference=REF_GROUPE,
            large=True,
            aide="Un étudiant inscrit dans un groupe appartient aussi aux groupes qui le contiennent.",
        ),
    ),
    colonnes=(
        Colonne("identifiant", "Identifiant", lambda e: e.identifiant, tri=Etudiant.identifiant),
        Colonne("nom", "Nom", lambda e: e.nom, tri=(Etudiant.nom, Etudiant.prenom)),
        Colonne("prenom", "Prénom", lambda e: e.prenom, tri=Etudiant.prenom),
        Colonne("groupes", "Groupes", lambda e: sorted(g.code for g in e.groupes), apercu=3),
    ),
    tri_defaut="nom",
    recherche=(Etudiant.identifiant, Etudiant.nom, Etudiant.prenom),
    chargements=(selectinload(Etudiant.groupes),),
    filtres=(
        Filtre(
            "groupe",
            "Groupe",
            lambda s: options_reference(s, REF_GROUPE),
            lambda v: Etudiant.groupes.any(Groupe.id == int(v)),
            champ="groupes",
        ),
    ),
    uniques=(Unicite(("identifiant",), "Un étudiant porte déjà cet identifiant.", "identifiant"),),
)
