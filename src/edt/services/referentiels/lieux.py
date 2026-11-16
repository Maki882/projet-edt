"""Lieux : campus, matrice des trajets, types de salle, équipements, salles."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, object_session, selectinload

from edt.db.modeles import (
    Besoin,
    Campus,
    Equipement,
    Indisponibilite,
    Occurrence,
    Salle,
    SeanceHebdo,
    Trajet,
    TypeSalle,
    TypeSeance,
    besoin_equipement,
    besoin_type_salle,
    salle_equipement,
)
from edt.domaine.enumerations import Accessibilite
from edt.services.parametres import lire_parametres
from edt.services.referentiels.conversion import ErreurChamp, est_vide
from edt.services.referentiels.description import (
    BESOINS,
    EFFECTIF_MAX,
    SALLES,
    TOUS_SEMESTRES,
    Champ,
    Colonne,
    Decompte,
    Entite,
    Filtre,
    Reference,
    TypeChamp,
    Unicite,
    choix_enum,
    compter_ou,
    libelle_choix,
    options_reference,
    par_colonne,
)
from edt.services.referentiels.operations import ErreurSaisie

journal = logging.getLogger(__name__)

TRAJET_MAX_MIN = 24 * 60

REF_CAMPUS = Reference(Campus, lambda c: f"{c.code} - {c.nom}", ordre=(Campus.code,))
REF_TYPE_SALLE = Reference(TypeSalle, lambda t: t.libelle, ordre=(TypeSalle.libelle,))
REF_EQUIPEMENT = Reference(Equipement, lambda e: e.libelle, ordre=(Equipement.libelle,))
REF_SALLE = Reference(
    Salle,
    lambda s: f"{s.code} ({s.campus.code}, {s.capacite} places)",
    ordre=(Salle.code,),
    chargements=(selectinload(Salle.campus),),
)


def _nombre_de_salles(colonne):
    return lambda objet: compter_ou(object_session(objet), colonne, objet.id)


CAMPUS = Entite(
    code="campus",
    modele=Campus,
    nom="campus",
    pluriel="campus",
    feminin=False,
    demonstratif="Ce campus",
    nouveau="Nouveau campus",
    libelle=lambda c: c.code,
    champs=(
        Champ("code", "Code", obligatoire=True, aide="Code court et unique, par exemple « NDC »."),
        Champ("nom", "Nom", obligatoire=True, aide="Nom complet du site."),
        Champ(
            "prefixe_salle",
            "Préfixe des salles",
            aide="Début des codes de salle de ce site, par exemple « N » pour N204.",
        ),
    ),
    colonnes=(
        Colonne("code", "Code", lambda c: c.code, tri=Campus.code),
        Colonne("nom", "Nom", lambda c: c.nom, tri=Campus.nom),
        Colonne("prefixe_salle", "Préfixe des salles", lambda c: c.prefixe_salle),
        Colonne("salles", "Salles", _nombre_de_salles(Salle.campus_id), nombre=True),
    ),
    tri_defaut="code",
    recherche=(Campus.code, Campus.nom),
    uniques=(Unicite(("code",), "Un campus porte déjà ce code.", "code"),),
    usages=(
        Decompte(
            par_colonne(Salle.campus_id),
            "est le campus d'une salle",
            "est le campus de {n} salles",
            lien="/salles?campus={id}",
            objets=SALLES,
        ),
        Decompte(
            par_colonne(Besoin.campus_impose_id),
            "est imposé à 1 besoin",
            "est imposé à {n} besoins",
        ),
    ),
    consequences=(
        Decompte(
            lambda s, i: (
                compter_ou(s, Trajet.campus_a_id, i) + compter_ou(s, Trajet.campus_b_id, i)
            ),
            "Son temps de trajet vers l'autre campus sera supprimé avec lui",
            "Ses {n} temps de trajet vers les autres campus seront supprimés avec lui",
        ),
    ),
)


TYPE_SALLE = Entite(
    code="type_salle",
    modele=TypeSalle,
    nom="type de salle",
    pluriel="types de salle",
    feminin=False,
    demonstratif="Ce type de salle",
    nouveau="Nouveau type de salle",
    libelle=lambda t: t.libelle,
    champs=(
        Champ(
            "code", "Code", obligatoire=True, aide="Code court et unique, par exemple « AMPHI »."
        ),
        Champ("libelle", "Libellé", obligatoire=True),
    ),
    colonnes=(
        Colonne("code", "Code", lambda t: t.code, tri=TypeSalle.code),
        Colonne("libelle", "Libellé", lambda t: t.libelle, tri=TypeSalle.libelle),
        Colonne("salles", "Salles", _nombre_de_salles(Salle.type_salle_id), nombre=True),
    ),
    tri_defaut="code",
    recherche=(TypeSalle.code, TypeSalle.libelle),
    uniques=(Unicite(("code",), "Un type de salle porte déjà ce code.", "code"),),
    usages=(
        Decompte(
            par_colonne(Salle.type_salle_id),
            "est le type d'une salle",
            "est le type de {n} salles",
            lien="/salles?type={id}",
            objets=SALLES,
        ),
        Decompte(
            par_colonne(besoin_type_salle.c.type_salle_id),
            "est demandé par 1 besoin",
            "est demandé par {n} besoins",
        ),
    ),
    consequences=(
        Decompte(
            par_colonne(TypeSeance.type_salle_defaut_id),
            "1 type de séance perdra son type de salle par défaut : ses besoins sans type "
            "de salle précisé accepteront alors toutes les salles",
            "{n} types de séance perdront leur type de salle par défaut : leurs besoins sans "
            "type de salle précisé accepteront alors toutes les salles",
        ),
    ),
)


EQUIPEMENT = Entite(
    code="equipement",
    modele=Equipement,
    nom="équipement",
    pluriel="équipements",
    feminin=False,
    demonstratif="Cet équipement",
    nouveau="Nouvel équipement",
    libelle=lambda e: e.libelle,
    champs=(
        Champ(
            "code",
            "Code",
            obligatoire=True,
            aide="Code court et unique, par exemple « VIDEOPROJ ».",
        ),
        Champ("libelle", "Libellé", obligatoire=True),
    ),
    colonnes=(
        Colonne("code", "Code", lambda e: e.code, tri=Equipement.code),
        Colonne("libelle", "Libellé", lambda e: e.libelle, tri=Equipement.libelle),
        Colonne(
            "salles",
            "Salles équipées",
            _nombre_de_salles(salle_equipement.c.equipement_id),
            nombre=True,
        ),
    ),
    tri_defaut="code",
    recherche=(Equipement.code, Equipement.libelle),
    uniques=(Unicite(("code",), "Un équipement porte déjà ce code.", "code"),),
    usages=(
        Decompte(
            par_colonne(besoin_equipement.c.equipement_id),
            "est requis par 1 besoin",
            "est requis par {n} besoins",
        ),
    ),
    consequences=(
        Decompte(
            par_colonne(salle_equipement.c.equipement_id),
            "Il sera retiré des équipements d'une salle",
            "Il sera retiré des équipements de {n} salles",
        ),
    ),
)


def _options_campus(session: Session) -> list[tuple[str, str]]:
    return options_reference(session, REF_CAMPUS)


def _options_types_salle(session: Session) -> list[tuple[str, str]]:
    return options_reference(session, REF_TYPE_SALLE)


SALLE = Entite(
    code="salle",
    modele=Salle,
    nom="salle",
    pluriel="salles",
    feminin=True,
    demonstratif="Cette salle",
    nouveau="Nouvelle salle",
    libelle=lambda s: f"{s.code} ({s.campus.code})",
    champs=(
        Champ("code", "Code", obligatoire=True, aide="Par exemple « N204 »."),
        Champ("campus_id", "Campus", TypeChamp.REFERENCE, obligatoire=True, reference=REF_CAMPUS),
        Champ(
            "type_salle_id",
            "Type de salle",
            TypeChamp.REFERENCE,
            obligatoire=True,
            reference=REF_TYPE_SALLE,
        ),
        Champ(
            "capacite",
            "Capacité",
            TypeChamp.ENTIER,
            obligatoire=True,
            minimum=0,
            maximum=EFFECTIF_MAX,
            unite="places",
        ),
        Champ("batiment", "Bâtiment"),
        Champ(
            "etage",
            "Étage",
            TypeChamp.ENTIER,
            minimum=-10,
            maximum=100,
            aide="0 pour le rez-de-chaussée, négatif pour un sous-sol.",
        ),
        Champ(
            "accessibilite",
            "Accessible aux personnes à mobilité réduite",
            TypeChamp.CHOIX,
            obligatoire=True,
            defaut=Accessibilite.INCONNU.value,
            choix=choix_enum(Accessibilite),
            aide="« Inconnu » compte comme « non », sauf réglage contraire dans les paramètres.",
        ),
        Champ(
            "equipements",
            "Équipements",
            TypeChamp.REFERENCES,
            reference=REF_EQUIPEMENT,
            large=True,
        ),
    ),
    colonnes=(
        Colonne("code", "Code", lambda s: s.code, tri=Salle.code),
        Colonne("campus", "Campus", lambda s: s.campus.code, tri=Campus.code),
        Colonne("type", "Type", lambda s: s.type_salle.libelle, tri=TypeSalle.libelle, texte=True),
        Colonne("capacite", "Places", lambda s: s.capacite, tri=Salle.capacite, nombre=True),
        Colonne("batiment", "Bâtiment", lambda s: s.batiment, tri=Salle.batiment),
        Colonne("etage", "Étage", lambda s: s.etage, tri=Salle.etage, nombre=True),
        Colonne(
            "accessibilite",
            "Accessible",
            lambda s: libelle_choix(choix_enum(Accessibilite), s.accessibilite),
            tri=Salle.accessibilite,
        ),
        Colonne(
            "equipements", "Équipements", lambda s: [e.libelle for e in s.equipements], apercu=1
        ),
    ),
    tri_defaut="code",
    recherche=(Salle.code, Salle.batiment, Campus.code, Campus.nom, TypeSalle.libelle),
    jointures=(
        (Campus, Salle.campus_id == Campus.id),
        (TypeSalle, Salle.type_salle_id == TypeSalle.id),
    ),
    chargements=(
        selectinload(Salle.campus),
        selectinload(Salle.type_salle),
        selectinload(Salle.equipements),
    ),
    filtres=(
        Filtre(
            "campus",
            "Campus",
            _options_campus,
            lambda v: Salle.campus_id == int(v),
            champ="campus_id",
        ),
        Filtre(
            "type",
            "Type",
            _options_types_salle,
            lambda v: Salle.type_salle_id == int(v),
            champ="type_salle_id",
        ),
        Filtre(
            "accessibilite",
            "Accessibilité",
            lambda s: [(str(v), libelle) for v, libelle in choix_enum(Accessibilite)],
            lambda v: Salle.accessibilite == v,
        ),
    ),
    uniques=(
        Unicite(("code", "campus_id"), "Une salle porte déjà ce code sur ce campus.", "code"),
    ),
    usages=(
        Decompte(
            par_colonne(Besoin.salle_imposee_id),
            "est imposée à 1 besoin",
            "est imposée à {n} besoins",
            lien=TOUS_SEMESTRES + "&salle_imposee={id}",
            objets=BESOINS,
        ),
    ),
    consequences=(
        Decompte(
            par_colonne(Indisponibilite.salle_id),
            "Son indisponibilité sera supprimée avec elle",
            "Ses {n} indisponibilités seront supprimées avec elle",
        ),
        Decompte(
            lambda s, i: (
                compter_ou(s, SeanceHebdo.salle_id, i) + compter_ou(s, Occurrence.salle_id, i)
            ),
            "1 séance déjà placée restera sans salle : elle apparaîtra dans « Points à traiter » "
            "(onglet Générer) et retrouvera une salle au prochain calcul ou par un replacement à la main",
            "{n} séances déjà placées resteront sans salle : elles apparaîtront dans « Points à "
            "traiter » (onglet Générer) et retrouveront une salle au prochain calcul ou par un replacement à la main",
        ),
    ),
)


# --------------------------------------------------------------------------- #
# Matrice des trajets
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MatriceTrajets:
    """Temps de trajet entre campus.

    - `campus` : (identifiant, code, nom) de chaque campus, par code ;
    - `durees` : minutes par paire, clé « a-b » avec a < b (paire absente =
      trajet par défaut) ;
    - `defaut_min` : trajet par défaut (paramètre `trajet_defaut_min`).
    """

    campus: tuple[tuple[int, str, str], ...]
    durees: dict[str, int]
    defaut_min: int

    def duree(self, campus_a: int, campus_b: int) -> int | None:
        """Durée saisie entre deux campus, ou `None` (trajet par défaut)."""
        a, b = sorted((campus_a, campus_b))
        return self.durees.get(f"{a}-{b}")


def matrice_trajets(session: Session) -> MatriceTrajets:
    """La matrice des trajets entre tous les campus."""
    campus = tuple(
        (c.id, c.code, c.nom) for c in session.scalars(select(Campus).order_by(Campus.code))
    )
    durees = {
        f"{t.campus_a_id}-{t.campus_b_id}": t.duree_min for t in session.scalars(select(Trajet))
    }
    return MatriceTrajets(campus, durees, lire_parametres(session).trajet_defaut_min)


def cle_trajet(campus_a: int, campus_b: int) -> str:
    """Nom du champ de saisie d'une paire de campus (a < b) : « trajet_3_5 »."""
    a, b = sorted((campus_a, campus_b))
    return f"trajet_{a}_{b}"


def enregistrer_trajets(
    session: Session, durees: Mapping[tuple[int, int], int | str | None]
) -> int:
    """Enregistre des temps de trajet (minutes) ; une valeur vide rétablit le trajet par défaut.

    Toutes les valeurs sont contrôlées avant la première écriture ; une valeur
    refusée lève `ErreurSaisie` (clé de champ : `cle_trajet(a, b)`). Renvoie le
    nombre de paires modifiées.
    """
    identifiants = set(session.scalars(select(Campus.id)))
    erreurs: dict[str, str] = {}
    propres: dict[tuple[int, int], int | None] = {}
    for (campus_a, campus_b), valeur in durees.items():
        cle = cle_trajet(campus_a, campus_b)
        if campus_a == campus_b or not {campus_a, campus_b} <= identifiants:
            erreurs[cle] = "Paire de campus invalide."
            continue
        try:
            propres[tuple(sorted((campus_a, campus_b)))] = _duree_trajet(valeur)
        except ErreurChamp as erreur:
            erreurs[cle] = erreur.message
    if erreurs:
        raise ErreurSaisie(erreurs)

    existants = {(t.campus_a_id, t.campus_b_id): t for t in session.scalars(select(Trajet))}
    modifies = 0
    for (a, b), minutes in propres.items():
        trajet = existants.get((a, b))
        if minutes is None:
            if trajet is not None:
                session.execute(delete(Trajet).where(Trajet.id == trajet.id))
                modifies += 1
        elif trajet is None:
            session.add(Trajet(campus_a_id=a, campus_b_id=b, duree_min=minutes))
            modifies += 1
        elif trajet.duree_min != minutes:
            trajet.duree_min = minutes
            modifies += 1
    session.flush()
    journal.info("Matrice des trajets enregistrée : %d paire(s) modifiée(s)", modifies)
    return modifies


def _duree_trajet(valeur: int | str | None) -> int | None:
    if est_vide(valeur):
        return None
    try:
        minutes = int(str(valeur).strip())
    except ValueError:
        raise ErreurChamp("Nombre de minutes attendu.") from None
    if not 0 <= minutes <= TRAJET_MAX_MIN:
        raise ErreurChamp(f"Entre 0 et {TRAJET_MAX_MIN} minutes.")
    return minutes
