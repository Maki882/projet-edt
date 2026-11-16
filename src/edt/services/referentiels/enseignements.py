"""Enseignements : matières, types de séance, besoins (la demande), indisponibilités."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased, object_session, selectinload

from edt.db.modeles import (
    Besoin,
    Enseignant,
    Groupe,
    Indisponibilite,
    Matiere,
    Occurrence,
    Salle,
    SeanceHebdo,
    Semestre,
    TypeSalle,
    TypeSeance,
    besoin_groupe,
)
from edt.domaine.calendrier import semaines_entre
from edt.domaine.enumerations import JOURS, CibleIndisponibilite, ModeVolume, NatureGroupe
from edt.domaine.heures import duree_vers_texte, minutes_vers_texte
from edt.services.calendrier import semestre_par_defaut
from edt.services.referentiels.description import (
    BESOINS,
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
    choix_jours,
    compter_ou,
    libelle_choix,
    libelles,
    options_reference,
    par_colonne,
)
from edt.services.referentiels.lieux import REF_CAMPUS, REF_EQUIPEMENT, REF_SALLE, REF_TYPE_SALLE
from edt.services.referentiels.operations import creer, lire
from edt.services.referentiels.personnes import REF_ENSEIGNANT, REF_GROUPE, sous_arbres

journal = logging.getLogger(__name__)

REF_PROMOTION = Reference(
    Groupe,
    lambda g: f"{g.code} - {g.libelle}",
    ordre=(Groupe.annee_scolaire_id.desc(), Groupe.code),
    condition=lambda: Groupe.nature == NatureGroupe.PROMOTION.value,
)
REF_MATIERE = Reference(
    Matiere,
    lambda m: f"{m.code} - {m.libelle}" + (f" ({m.promotion.code})" if m.promotion else ""),
    ordre=(Matiere.code,),
    chargements=(selectinload(Matiere.promotion),),
)
REF_TYPE_SEANCE = Reference(
    TypeSeance, lambda t: f"{t.code} - {t.libelle}", ordre=(TypeSeance.code,)
)
REF_SEMESTRE = Reference(Semestre, lambda s: s.libelle, ordre=(Semestre.date_debut.desc(),))


def _date(valeur: dt.date | None) -> str:
    return "" if valeur is None else valeur.strftime("%d/%m/%Y")


# --------------------------------------------------------------------------- #
# Matières
# --------------------------------------------------------------------------- #

MATIERE = Entite(
    code="matiere",
    modele=Matiere,
    nom="matière",
    pluriel="matières",
    feminin=True,
    demonstratif="Cette matière",
    nouveau="Nouvelle matière",
    libelle=lambda m: f"{m.code} - {m.libelle}",
    champs=(
        Champ("code", "Code module", obligatoire=True, aide="Par exemple « II.1102 »."),
        Champ("libelle", "Libellé", obligatoire=True),
        Champ(
            "promotion_id",
            "Promotion",
            TypeChamp.REFERENCE,
            reference=REF_PROMOTION,
            aide="Promotion de rattachement ; vide pour une matière commune à plusieurs promotions.",
        ),
    ),
    colonnes=(
        Colonne("code", "Code module", lambda m: m.code, tri=Matiere.code),
        Colonne("libelle", "Libellé", lambda m: m.libelle, tri=Matiere.libelle, texte=True),
        Colonne(
            "promotion",
            "Promotion",
            lambda m: m.promotion.code if m.promotion else None,
            tri=Groupe.code,
        ),
        Colonne(
            "besoins",
            "Besoins",
            lambda m: compter_ou(_session(m), Besoin.matiere_id, m.id),
            nombre=True,
        ),
    ),
    tri_defaut="code",
    recherche=(Matiere.code, Matiere.libelle, Groupe.code),
    jointures=((Groupe, Matiere.promotion_id == Groupe.id),),
    chargements=(selectinload(Matiere.promotion),),
    filtres=(
        Filtre(
            "promotion",
            "Promotion",
            lambda s: options_reference(s, REF_PROMOTION),
            lambda v: Matiere.promotion_id == int(v),
            champ="promotion_id",
        ),
    ),
    uniques=(
        Unicite(
            ("code", "promotion_id"),
            "Une matière porte déjà ce code pour cette promotion.",
            "code",
        ),
    ),
    usages=(
        Decompte(
            par_colonne(Besoin.matiere_id),
            "est enseignée par 1 besoin",
            "est enseignée par {n} besoins",
            lien=TOUS_SEMESTRES + "&matiere={id}",
            objets=BESOINS,
        ),
    ),
)


def _session(objet) -> Session:
    session = object_session(objet)
    assert session is not None, "objet détaché de sa session"
    return session


# --------------------------------------------------------------------------- #
# Types de séance
# --------------------------------------------------------------------------- #

TYPE_SEANCE = Entite(
    code="type_seance",
    modele=TypeSeance,
    nom="type de séance",
    pluriel="types de séance",
    feminin=False,
    demonstratif="Ce type de séance",
    nouveau="Nouveau type de séance",
    libelle=lambda t: f"{t.code} - {t.libelle}",
    champs=(
        Champ("code", "Code", obligatoire=True, aide="Par exemple « TD »."),
        Champ("libelle", "Libellé", obligatoire=True),
        Champ(
            "samedi_autorise",
            "Peut avoir lieu le samedi matin",
            TypeChamp.BOOLEEN,
            defaut=False,
        ),
        Champ(
            "type_salle_defaut_id",
            "Type de salle par défaut",
            TypeChamp.REFERENCE,
            reference=REF_TYPE_SALLE,
            aide="Retenu quand un besoin ne précise aucun type de salle.",
        ),
        Champ(
            "couleur",
            "Couleur d'affichage",
            TypeChamp.COULEUR,
            defaut="#6b7280",
            aide="Couleur des séances de ce type dans les emplois du temps.",
        ),
    ),
    colonnes=(
        Colonne("code", "Code", lambda t: t.code, tri=TypeSeance.code),
        Colonne("libelle", "Libellé", lambda t: t.libelle, tri=TypeSeance.libelle),
        Colonne(
            "samedi", "Samedi autorisé", lambda t: t.samedi_autorise, tri=TypeSeance.samedi_autorise
        ),
        Colonne(
            "type_salle",
            "Salle par défaut",
            lambda t: t.type_salle_defaut.libelle if t.type_salle_defaut else None,
            tri=TypeSalle.libelle,
        ),
        Colonne("couleur", "Couleur", lambda t: t.couleur, format="couleur"),
    ),
    tri_defaut="code",
    recherche=(TypeSeance.code, TypeSeance.libelle),
    jointures=((TypeSalle, TypeSeance.type_salle_defaut_id == TypeSalle.id),),
    chargements=(selectinload(TypeSeance.type_salle_defaut),),
    uniques=(Unicite(("code",), "Un type de séance porte déjà ce code.", "code"),),
    usages=(
        Decompte(
            par_colonne(Besoin.type_seance_id),
            "est le type d'un besoin",
            "est le type de {n} besoins",
            lien=TOUS_SEMESTRES + "&type={id}",
            objets=BESOINS,
        ),
    ),
)


# --------------------------------------------------------------------------- #
# Besoins
# --------------------------------------------------------------------------- #

CHOIX_MODE = (
    (ModeVolume.PAR_SEMAINE.value, "Chaque semaine"),
    (ModeVolume.TOTAL.value, "Volume total réparti entre deux dates"),
    (ModeVolume.PONCTUEL.value, "Séance ponctuelle datée"),
)
SEMAINE_OU_TOTAL = (ModeVolume.PAR_SEMAINE.value, ModeVolume.TOTAL.value)


def _heure_courte(minutes: int) -> str:
    """« 14 h » ou « 13 h 45 »."""
    heures, reste = divmod(minutes, 60)
    return f"{heures} h" if reste == 0 else f"{heures} h {reste:02d}"


def _jour_mois(valeur: dt.date) -> str:
    return valeur.strftime("%d/%m")


def texte_volume_court(besoin: Besoin) -> str:
    """Volume sur une ligne : « 2 / semaine », « 6 séances · 14/09 → 15/11 », « le 12/11 à 14 h »."""
    mode = ModeVolume(besoin.mode_volume)
    if mode is ModeVolume.PONCTUEL:
        texte = f"le {_jour_mois(besoin.date_ponctuelle)}" if besoin.date_ponctuelle else "daté"
        if besoin.debut_impose_min is not None:
            texte += f" à {_heure_courte(besoin.debut_impose_min)}"
        return texte
    if mode is ModeVolume.PAR_SEMAINE:
        texte = f"{besoin.nombre} / semaine"
    else:
        texte = f"{besoin.nombre} séance{'s' if besoin.nombre > 1 else ''}"
    if besoin.date_debut or besoin.date_fin:
        debut = _jour_mois(besoin.date_debut) if besoin.date_debut else "début"
        fin = _jour_mois(besoin.date_fin) if besoin.date_fin else "fin"
        texte += f" · {debut} → {fin}"
    return texte


def texte_volume(besoin: Besoin) -> str:
    """Volume complet : « 2 / semaine », « 10 au total, 2 max. / semaine, du ... au ... »."""
    mode = ModeVolume(besoin.mode_volume)
    if mode is ModeVolume.PONCTUEL:
        texte = f"Séance ponctuelle le {_date(besoin.date_ponctuelle)}"
        if besoin.debut_impose_min is not None:
            texte += f" à {minutes_vers_texte(besoin.debut_impose_min)}"
        return texte
    if mode is ModeVolume.PAR_SEMAINE:
        texte = f"{besoin.nombre} / semaine"
    else:
        texte = f"{besoin.nombre} au total, {besoin.max_par_semaine} max. / semaine"
    if besoin.date_debut or besoin.date_fin:
        debut = _date(besoin.date_debut) or "début du semestre"
        fin = _date(besoin.date_fin) or "fin du semestre"
        texte += f", du {debut} au {fin}"
    return texte


def exigences(besoin: Besoin) -> list[str]:
    """Exigences, une par élément : créneau, salle ou campus imposés, types de salle, équipements."""
    morceaux: list[str] = []
    if besoin.jour_impose is not None or besoin.debut_impose_min is not None:
        jour = JOURS[besoin.jour_impose] if besoin.jour_impose is not None else ""
        heure = (
            f"à {minutes_vers_texte(besoin.debut_impose_min)}"
            if besoin.debut_impose_min is not None
            else ""
        )
        morceaux.append(f"imposé : {jour} {heure}".replace("  ", " ").strip())
    if besoin.salle_imposee is not None:
        morceaux.append(f"salle {besoin.salle_imposee.code}")
    if besoin.campus_impose is not None:
        morceaux.append(f"campus {besoin.campus_impose.code}")
    morceaux += [type_salle.libelle for type_salle in besoin.types_salle]
    morceaux += [equipement.libelle for equipement in besoin.equipements]
    return morceaux


def libelle_besoin(besoin: Besoin) -> str:
    """Libellé court d'un besoin : « IM.1101 TD A1-G1 »."""
    return " ".join(
        part
        for part in (
            besoin.matiere.code,
            besoin.type_seance.code,
            libelles(besoin.groupes),
            f"({besoin.libelle})" if besoin.libelle else "",
        )
        if part
    )


def _valider_besoin(session: Session, v: Valeurs, actuel: Besoin | None) -> dict[str, str]:
    erreurs: dict[str, str] = {}
    mode = v.get("mode_volume")
    obligatoire = "Ce champ est obligatoire pour ce mode de volume."
    if mode == ModeVolume.PAR_SEMAINE.value and v.get("seances_par_semaine") is None:
        erreurs["seances_par_semaine"] = obligatoire
    if mode == ModeVolume.TOTAL.value:
        if v.get("nombre_total") is None:
            erreurs["nombre_total"] = obligatoire
        if v.get("max_par_semaine") is None:
            erreurs["max_par_semaine"] = obligatoire
    if mode == ModeVolume.PONCTUEL.value and v.get("date_ponctuelle") is None:
        erreurs["date_ponctuelle"] = obligatoire
    if mode in SEMAINE_OU_TOTAL:
        debut, fin = v.get("date_debut"), v.get("date_fin")
        if debut and fin and debut > fin:
            erreurs["date_fin"] = "La fin doit suivre le début."
    if v.get("debut_impose_min") is not None and v["debut_impose_min"] >= 24 * 60:
        erreurs["debut_impose_min"] = "Heure invalide."

    semestre = session.get(Semestre, v["semestre_id"]) if v.get("semestre_id") else None
    if semestre is not None:
        noms = (
            ("date_ponctuelle",)
            if mode == ModeVolume.PONCTUEL.value
            else ("date_debut", "date_fin")
        )
        for nom in noms:
            jour = v.get(nom)
            if jour and not semestre.date_debut <= jour <= semestre.date_fin:
                erreurs.setdefault(
                    nom,
                    f"La date doit être comprise dans le semestre (du {_date(semestre.date_debut)} "
                    f"au {_date(semestre.date_fin)}).",
                )
        groupes = v.get("groupes") or []
        if groupes:
            hors_annee = session.scalar(
                select(func.count()).where(
                    Groupe.id.in_(groupes), Groupe.annee_scolaire_id != semestre.annee_scolaire_id
                )
            )
            if hors_annee:
                erreurs["groupes"] = "Les groupes doivent appartenir à l'année du semestre."
    salle_id, campus_id = v.get("salle_imposee_id"), v.get("campus_impose_id")
    if salle_id and campus_id:
        salle = session.get(Salle, salle_id)
        if salle is not None and salle.campus_id != campus_id:
            erreurs["campus_impose_id"] = "La salle imposée n'est pas sur ce campus."
    return erreurs


def _preparer_besoin(session: Session, v: Valeurs) -> Valeurs:
    mode = v["mode_volume"]
    par_semaine = v.pop("seances_par_semaine", None)
    total = v.pop("nombre_total", None)
    if mode == ModeVolume.PAR_SEMAINE.value:
        v.update(nombre=par_semaine, max_par_semaine=1, date_ponctuelle=None)
    elif mode == ModeVolume.TOTAL.value:
        v.update(nombre=total, date_ponctuelle=None)
    else:
        v.update(nombre=1, max_par_semaine=1, date_debut=None, date_fin=None, jour_impose=None)
    return v


def _semestre_par_defaut_texte(session: Session) -> str | None:
    if session.scalar(select(Semestre.id).limit(1)) is None:
        return None
    return str(semestre_par_defaut(session).id)


_G2 = aliased(Groupe)
_G3 = aliased(Groupe)


def _condition_groupe(valeur: str):
    """Besoins d'un groupe : suivis par lui ou ses sous-groupes, ou de ses matières rattachées.

    Sert aux filtres « Promotion » et « Groupe » (l'arbre a trois niveaux :
    promotion > classe > demi-groupe).
    """
    groupe = int(valeur)
    descendants = select(_G2.id).where(
        or_(
            _G2.id == groupe,
            _G2.parent_id == groupe,
            _G2.parent_id.in_(select(_G3.id).where(_G3.parent_id == groupe)),
        )
    )
    return or_(
        Besoin.groupes.any(Groupe.id.in_(descendants)),
        Besoin.matiere.has(Matiere.promotion_id.in_(descendants)),
    )


BESOIN = Entite(
    code="besoin",
    modele=Besoin,
    nom="besoin",
    pluriel="besoins",
    feminin=False,
    demonstratif="Ce besoin",
    nouveau="Nouveau besoin",
    libelle=libelle_besoin,
    champs=(
        Champ(
            "semestre_id",
            "Semestre",
            TypeChamp.REFERENCE,
            obligatoire=True,
            reference=REF_SEMESTRE,
            groupe="Enseignement",
        ),
        Champ(
            "matiere_id",
            "Matière",
            TypeChamp.REFERENCE,
            obligatoire=True,
            reference=REF_MATIERE,
            groupe="Enseignement",
        ),
        Champ(
            "type_seance_id",
            "Type de séance",
            TypeChamp.REFERENCE,
            obligatoire=True,
            reference=REF_TYPE_SEANCE,
            groupe="Enseignement",
        ),
        Champ(
            "duree_min",
            "Durée d'une séance",
            TypeChamp.DUREE,
            obligatoire=True,
            defaut=120,
            minimum=5,
            maximum=24 * 60,
            aide="Par exemple « 2 h » ou « 1 h 30 ».",
            groupe="Enseignement",
        ),
        Champ(
            "libelle",
            "Précision",
            aide="Facultatif, par exemple « Partiel ».",
            groupe="Enseignement",
        ),
        Champ(
            "groupes",
            "Groupes",
            TypeChamp.REFERENCES,
            obligatoire=True,
            reference=REF_GROUPE,
            large=True,
            aide="Les groupes choisis suivent ensemble chaque séance.",
            groupe="Pour qui, par qui",
        ),
        Champ(
            "enseignants",
            "Enseignants",
            TypeChamp.REFERENCES,
            reference=REF_ENSEIGNANT,
            large=True,
            aide="Aucun : enseignant à désigner plus tard.",
            groupe="Pour qui, par qui",
        ),
        Champ(
            "mode_volume",
            "Volume",
            TypeChamp.CHOIX,
            obligatoire=True,
            defaut=ModeVolume.PAR_SEMAINE.value,
            choix=CHOIX_MODE,
            groupe="Combien",
        ),
        Champ(
            "seances_par_semaine",
            "Séances par semaine",
            TypeChamp.ENTIER,
            defaut=1,
            minimum=1,
            maximum=30,
            virtuel=True,
            lire=lambda s, b: b.nombre if b.mode_volume == ModeVolume.PAR_SEMAINE.value else None,
            visible_si=("mode_volume", (ModeVolume.PAR_SEMAINE.value,)),
            groupe="Combien",
        ),
        Champ(
            "nombre_total",
            "Nombre total de séances",
            TypeChamp.ENTIER,
            minimum=1,
            maximum=1000,
            virtuel=True,
            lire=lambda s, b: b.nombre if b.mode_volume == ModeVolume.TOTAL.value else None,
            visible_si=("mode_volume", (ModeVolume.TOTAL.value,)),
            aide="Réparties régulièrement sur les semaines ouvertes.",
            groupe="Combien",
        ),
        Champ(
            "max_par_semaine",
            "Au plus par semaine",
            TypeChamp.ENTIER,
            defaut=1,
            minimum=1,
            maximum=30,
            visible_si=("mode_volume", (ModeVolume.TOTAL.value,)),
            groupe="Combien",
        ),
        Champ(
            "date_debut",
            "À partir du",
            TypeChamp.DATE,
            visible_si=("mode_volume", SEMAINE_OU_TOTAL),
            aide="Vide : dès le début du semestre.",
            groupe="Combien",
        ),
        Champ(
            "date_fin",
            "Jusqu'au",
            TypeChamp.DATE,
            visible_si=("mode_volume", SEMAINE_OU_TOTAL),
            aide="Vide : jusqu'à la fin du semestre.",
            groupe="Combien",
        ),
        Champ(
            "date_ponctuelle",
            "Date de la séance",
            TypeChamp.DATE,
            visible_si=("mode_volume", (ModeVolume.PONCTUEL.value,)),
            groupe="Combien",
        ),
        Champ(
            "jour_impose",
            "Jour imposé",
            TypeChamp.CHOIX,
            choix=choix_jours(5),
            visible_si=("mode_volume", SEMAINE_OU_TOTAL),
            aide="Facultatif.",
            groupe="Créneau et lieu imposés",
        ),
        Champ(
            "debut_impose_min",
            "Heure de début imposée",
            TypeChamp.HEURE,
            aide="Facultatif. Peut sortir de la grille horaire.",
            groupe="Créneau et lieu imposés",
        ),
        Champ(
            "salle_imposee_id",
            "Salle imposée",
            TypeChamp.REFERENCE,
            reference=REF_SALLE,
            groupe="Créneau et lieu imposés",
        ),
        Champ(
            "campus_impose_id",
            "Campus imposé",
            TypeChamp.REFERENCE,
            reference=REF_CAMPUS,
            groupe="Créneau et lieu imposés",
        ),
        Champ(
            "types_salle",
            "Types de salle acceptés",
            TypeChamp.REFERENCES,
            reference=REF_TYPE_SALLE,
            aide="Aucun : le type de salle par défaut du type de séance.",
            groupe="Exigences de salle",
        ),
        Champ(
            "equipements",
            "Équipements requis",
            TypeChamp.REFERENCES,
            reference=REF_EQUIPEMENT,
            groupe="Exigences de salle",
        ),
        Champ(
            "commentaire",
            "Commentaire",
            TypeChamp.TEXTE_LONG,
            large=True,
            groupe="Exigences de salle",
        ),
    ),
    colonnes=(
        Colonne(
            "matiere",
            "Matière",
            lambda b: (
                f"{b.matiere.code} {b.matiere.libelle}" + (f" - {b.libelle}" if b.libelle else "")
            ),
            tri=(Matiere.code, Besoin.id),
            texte=True,
        ),
        Colonne("type", "Type", lambda b: b.type_seance.code, tri=TypeSeance.code),
        Colonne("groupes", "Groupes", lambda b: [g.code for g in b.groupes]),
        Colonne(
            "enseignants",
            "Enseignants",
            lambda b: [e.nom_affiche for e in b.enseignants],
            apercu=1,
        ),
        Colonne(
            "duree",
            "Durée",
            lambda b: duree_vers_texte(b.duree_min),
            tri=Besoin.duree_min,
            nombre=True,
        ),
        Colonne(
            "volume",
            "Volume",
            texte_volume_court,
            tri=(Besoin.mode_volume, Besoin.nombre),
            detail=texte_volume,
            texte=True,
        ),
        Colonne("exigences", "Exigences", exigences, apercu=1),
        Colonne(
            "semestre",
            "Semestre",
            lambda b: b.semestre.code,
            tri=Semestre.date_debut,
            filtre="semestre",
        ),
    ),
    tri_defaut="matiere",
    recherche=(Matiere.code, Matiere.libelle, Besoin.libelle, TypeSeance.code),
    recherche_avancee=(
        lambda motif: Besoin.groupes.any(Groupe.code.ilike(motif, escape="\\")),
        lambda motif: Besoin.enseignants.any(Enseignant.nom.ilike(motif, escape="\\")),
    ),
    jointures=(
        (Matiere, Besoin.matiere_id == Matiere.id),
        (TypeSeance, Besoin.type_seance_id == TypeSeance.id),
        (Semestre, Besoin.semestre_id == Semestre.id),
    ),
    chargements=(
        selectinload(Besoin.matiere),
        selectinload(Besoin.type_seance),
        selectinload(Besoin.semestre),
        selectinload(Besoin.groupes),
        selectinload(Besoin.enseignants),
        selectinload(Besoin.types_salle),
        selectinload(Besoin.equipements),
        selectinload(Besoin.salle_imposee),
        selectinload(Besoin.campus_impose),
    ),
    filtres=(
        Filtre(
            "semestre",
            "Semestre",
            lambda s: options_reference(s, REF_SEMESTRE),
            lambda v: Besoin.semestre_id == int(v),
            defaut=_semestre_par_defaut_texte,
            champ="semestre_id",
        ),
        Filtre(
            "promotion",
            "Promotion",
            lambda s: options_reference(s, REF_PROMOTION),
            _condition_groupe,
        ),
        Filtre(
            "groupe",
            "Groupe",
            lambda s: options_reference(s, REF_GROUPE),
            _condition_groupe,
            champ="groupes",
        ),
        Filtre(
            "matiere",
            "Matière",
            lambda s: options_reference(s, REF_MATIERE),
            lambda v: Besoin.matiere_id == int(v),
            champ="matiere_id",
        ),
        Filtre(
            "type",
            "Type",
            lambda s: options_reference(s, REF_TYPE_SEANCE),
            lambda v: Besoin.type_seance_id == int(v),
            champ="type_seance_id",
        ),
        Filtre(
            "enseignant",
            "Enseignant",
            lambda s: options_reference(s, REF_ENSEIGNANT),
            lambda v: Besoin.enseignants.any(Enseignant.id == int(v)),
            champ="enseignants",
        ),
        Filtre(
            "salle_imposee",
            "Salle imposée",
            lambda s: options_reference(s, REF_SALLE),
            lambda v: Besoin.salle_imposee_id == int(v),
            champ="salle_imposee_id",
        ),
        Filtre(
            "mode",
            "Volume",
            lambda s: [(str(v), libelle) for v, libelle in CHOIX_MODE],
            lambda v: Besoin.mode_volume == v,
            champ="mode_volume",
        ),
    ),
    consequences=(
        Decompte(
            lambda s, i: (
                compter_ou(s, SeanceHebdo.besoin_id, i) + compter_ou(s, Occurrence.besoin_id, i)
            ),
            "Sa séance déjà placée dans un emploi du temps disparaîtra avec lui ; le reste de "
            "l'emploi du temps ne change pas",
            "Ses {n} séances déjà placées dans des emplois du temps disparaîtront avec lui ; le "
            "reste des emplois du temps ne change pas",
        ),
    ),
    valider=_valider_besoin,
    preparer=_preparer_besoin,
)


def groupes_des_besoins(session: Session, besoin_ids: Collection[int]) -> set[int]:
    """Groupes suivant ces besoins, avec leurs sous-groupes (qui suivent aussi ces séances)."""
    if not besoin_ids:
        return set()
    directs = session.scalars(
        select(besoin_groupe.c.groupe_id).where(besoin_groupe.c.besoin_id.in_(besoin_ids))
    )
    return sous_arbres(session, set(directs))


def dupliquer_besoin(session: Session, ident: int) -> dict:
    """Crée une copie du besoin `ident` (mêmes valeurs) et renvoie ses valeurs (comme `lire`)."""
    valeurs = lire(session, BESOIN, ident)
    for cle in ("id", "nom_affiche"):
        valeurs.pop(cle)
    copie = creer(session, BESOIN, valeurs)
    journal.info("Besoin n° %d dupliqué : copie n° %d", ident, copie["id"])
    return copie


@dataclass(frozen=True)
class ChargeGroupe:
    """Charge hebdomadaire d'un groupe sur un semestre.

    - `propres` : séances par semaine de ses propres besoins ;
    - `heritees` : séances par semaine des groupes qui le contiennent ;
    - `minutes` : durée hebdomadaire totale (propres et héritées) ;
    - `ponctuelles` : séances ponctuelles (propres et héritées).

    Un besoin au volume total compte pour sa moyenne hebdomadaire sur sa fenêtre.
    """

    groupe_id: int
    code: str
    libelle: str
    propres: float
    heritees: float
    minutes: float
    ponctuelles: int

    @property
    def total(self) -> float:
        """Séances par semaine, propres et héritées."""
        return round(self.propres + self.heritees, 1)


def charges_hebdomadaires(
    session: Session, semestre_id: int, groupes_retenus: Collection[int] | None = None
) -> tuple[ChargeGroupe, ...]:
    """Séances hebdomadaires par groupe sur un semestre, par code de groupe.

    Seuls les groupes qui ont au moins une séance figurent ; `groupes_retenus`
    restreint encore la liste (la charge de chacun compte toujours tous les
    besoins du semestre).
    """
    semestre = session.get(Semestre, semestre_id)
    if semestre is None:
        return ()
    besoins = session.scalars(
        select(Besoin)
        .where(Besoin.semestre_id == semestre_id)
        .options(selectinload(Besoin.groupes))
    ).all()
    seances: dict[int, float] = {}
    minutes: dict[int, float] = {}
    ponctuelles: dict[int, int] = {}
    for besoin in besoins:
        mode = ModeVolume(besoin.mode_volume)
        if mode is ModeVolume.PONCTUEL:
            for groupe in besoin.groupes:
                ponctuelles[groupe.id] = ponctuelles.get(groupe.id, 0) + 1
            continue
        if mode is ModeVolume.PAR_SEMAINE:
            par_semaine = float(besoin.nombre)
        else:
            debut = max(besoin.date_debut or semestre.date_debut, semestre.date_debut)
            fin = min(besoin.date_fin or semestre.date_fin, semestre.date_fin)
            nb_semaines = len(semaines_entre(debut, fin)) if debut <= fin else 0
            par_semaine = besoin.nombre / nb_semaines if nb_semaines else 0.0
        for groupe in besoin.groupes:
            seances[groupe.id] = seances.get(groupe.id, 0.0) + par_semaine
            minutes[groupe.id] = minutes.get(groupe.id, 0.0) + par_semaine * besoin.duree_min

    groupes = session.scalars(
        select(Groupe).where(Groupe.annee_scolaire_id == semestre.annee_scolaire_id)
    ).all()
    parents = {g.id: g.parent_id for g in groupes}

    def ascendants(groupe_id: int) -> list[int]:
        chaine, courant, vus = [], parents.get(groupe_id), {groupe_id}
        while courant is not None and courant not in vus:
            chaine.append(courant)
            vus.add(courant)
            courant = parents.get(courant)
        return chaine

    resultat = []
    for groupe in sorted(groupes, key=lambda g: g.code):
        if groupes_retenus is not None and groupe.id not in groupes_retenus:
            continue
        lignee = ascendants(groupe.id)
        heritees = sum(seances.get(a, 0.0) for a in lignee)
        propres = seances.get(groupe.id, 0.0)
        nb_ponctuelles = ponctuelles.get(groupe.id, 0) + sum(ponctuelles.get(a, 0) for a in lignee)
        if not (propres or heritees or nb_ponctuelles):
            continue
        resultat.append(
            ChargeGroupe(
                groupe_id=groupe.id,
                code=groupe.code,
                libelle=groupe.libelle,
                propres=round(propres, 1),
                heritees=round(heritees, 1),
                minutes=round(
                    minutes.get(groupe.id, 0.0) + sum(minutes.get(a, 0.0) for a in lignee)
                ),
                ponctuelles=nb_ponctuelles,
            )
        )
    return tuple(resultat)


# --------------------------------------------------------------------------- #
# Indisponibilités
# --------------------------------------------------------------------------- #

CHOIX_CIBLE = choix_enum(CibleIndisponibilite)
CHOIX_FORME = (("recurrente", "Chaque semaine"), ("datee", "Pendant une période datée"))
TOUTE_LA_JOURNEE = (0, 24 * 60)


def texte_quand(indispo: Indisponibilite) -> str:
    """« Chaque lundi », « Le 02/11/2026 » ou « Du 02/11/2026 au 06/11/2026 »."""
    if indispo.jour is not None:
        return f"Chaque {JOURS[indispo.jour]}"
    if indispo.date_debut == indispo.date_fin:
        return f"Le {_date(indispo.date_debut)}"
    return f"Du {_date(indispo.date_debut)} au {_date(indispo.date_fin)}"


def texte_plage(indispo: Indisponibilite) -> str:
    """« Toute la journée » ou « 13 h 45 - 18 h 00 »."""
    if (indispo.debut_min, indispo.fin_min) == TOUTE_LA_JOURNEE:
        return "Toute la journée"
    return f"{minutes_vers_texte(indispo.debut_min)} - {minutes_vers_texte(indispo.fin_min)}"


def texte_ressource(indispo: Indisponibilite) -> str:
    """Nom de la ressource visée."""
    if indispo.enseignant is not None:
        return indispo.enseignant.nom_affiche
    if indispo.salle is not None:
        return indispo.salle.code
    if indispo.groupe is not None:
        return indispo.groupe.code
    return ""


def _valider_indisponibilite(
    session: Session, v: Valeurs, actuel: Indisponibilite | None
) -> dict[str, str]:
    erreurs: dict[str, str] = {}
    cible = v.get("cible")
    champ_cible = {
        CibleIndisponibilite.ENSEIGNANT.value: "enseignant_id",
        CibleIndisponibilite.SALLE.value: "salle_id",
        CibleIndisponibilite.GROUPE.value: "groupe_id",
    }.get(cible)
    if champ_cible and v.get(champ_cible) is None:
        erreurs[champ_cible] = "Choisir la ressource indisponible."
    forme = v.get("forme")
    if forme == "recurrente" and v.get("jour") is None:
        erreurs["jour"] = "Choisir le jour."
    if forme == "datee":
        debut, fin = v.get("date_debut"), v.get("date_fin")
        if debut is None:
            erreurs["date_debut"] = "Choisir la date."
        elif fin is not None and fin < debut:
            erreurs["date_fin"] = "La fin doit suivre le début."
    debut_min = v.get("debut_min")
    fin_min = v.get("fin_min")
    debut_min = TOUTE_LA_JOURNEE[0] if debut_min is None else debut_min
    fin_min = TOUTE_LA_JOURNEE[1] if fin_min is None else fin_min
    if debut_min >= fin_min:
        erreurs["fin_min"] = "La fin doit suivre le début."
    return erreurs


def _preparer_indisponibilite(session: Session, v: Valeurs) -> Valeurs:
    cible = v["cible"]
    for valeur, champ in (
        (CibleIndisponibilite.ENSEIGNANT.value, "enseignant_id"),
        (CibleIndisponibilite.SALLE.value, "salle_id"),
        (CibleIndisponibilite.GROUPE.value, "groupe_id"),
    ):
        if cible != valeur:
            v[champ] = None
    if v.pop("forme") == "recurrente":
        v.update(date_debut=None, date_fin=None)
    else:
        v.update(jour=None, date_fin=v.get("date_fin") or v["date_debut"])
    if v.get("debut_min") is None:
        v["debut_min"] = TOUTE_LA_JOURNEE[0]
    if v.get("fin_min") is None:
        v["fin_min"] = TOUTE_LA_JOURNEE[1]
    v["dure"] = True
    return v


def _visible_cible(cible: CibleIndisponibilite) -> tuple[str, tuple[str, ...]]:
    return ("cible", (cible.value,))


INDISPONIBILITE = Entite(
    code="indisponibilite",
    modele=Indisponibilite,
    nom="indisponibilité",
    pluriel="indisponibilités",
    feminin=True,
    demonstratif="Cette indisponibilité",
    nouveau="Nouvelle indisponibilité",
    libelle=lambda i: f"{texte_ressource(i)}, {texte_quand(i).lower()}",
    champs=(
        Champ(
            "cible",
            "Qui est indisponible",
            TypeChamp.CHOIX,
            obligatoire=True,
            defaut=CibleIndisponibilite.ENSEIGNANT.value,
            choix=CHOIX_CIBLE,
            groupe="Qui",
        ),
        Champ(
            "enseignant_id",
            "Enseignant",
            TypeChamp.REFERENCE,
            reference=REF_ENSEIGNANT,
            visible_si=_visible_cible(CibleIndisponibilite.ENSEIGNANT),
            groupe="Qui",
        ),
        Champ(
            "salle_id",
            "Salle",
            TypeChamp.REFERENCE,
            reference=REF_SALLE,
            visible_si=_visible_cible(CibleIndisponibilite.SALLE),
            groupe="Qui",
        ),
        Champ(
            "groupe_id",
            "Groupe",
            TypeChamp.REFERENCE,
            reference=REF_GROUPE,
            visible_si=_visible_cible(CibleIndisponibilite.GROUPE),
            aide="Vaut aussi pour les groupes qui le contiennent ou qu'il contient.",
            groupe="Qui",
        ),
        Champ(
            "forme",
            "Quand",
            TypeChamp.CHOIX,
            obligatoire=True,
            defaut="recurrente",
            choix=CHOIX_FORME,
            virtuel=True,
            lire=lambda s, i: "datee" if i.date_debut is not None else "recurrente",
            groupe="Quand",
        ),
        Champ(
            "jour",
            "Jour",
            TypeChamp.CHOIX,
            choix=choix_jours(6),
            visible_si=("forme", ("recurrente",)),
            groupe="Quand",
        ),
        Champ(
            "date_debut",
            "Du",
            TypeChamp.DATE,
            visible_si=("forme", ("datee",)),
            groupe="Quand",
        ),
        Champ(
            "date_fin",
            "Au",
            TypeChamp.DATE,
            visible_si=("forme", ("datee",)),
            aide="Vide : un seul jour.",
            groupe="Quand",
        ),
        Champ(
            "debut_min",
            "De",
            TypeChamp.HEURE,
            lire=lambda s, i: None if i.debut_min == TOUTE_LA_JOURNEE[0] else i.debut_min,
            aide="Vide : dès le matin.",
            groupe="Quand",
        ),
        Champ(
            "fin_min",
            "À",
            TypeChamp.HEURE,
            lire=lambda s, i: None if i.fin_min == TOUTE_LA_JOURNEE[1] else i.fin_min,
            aide="Vide : jusqu'au soir.",
            groupe="Quand",
        ),
        Champ("motif", "Motif", aide="Facultatif, affiché dans les listes.", large=True),
    ),
    colonnes=(
        Colonne(
            "ressource",
            "Ressource",
            texte_ressource,
            tri=func.coalesce(Enseignant.nom, Salle.code, Groupe.code),
        ),
        Colonne(
            "cible",
            "Type",
            lambda i: libelle_choix(CHOIX_CIBLE, i.cible),
            tri=Indisponibilite.cible,
        ),
        Colonne(
            "quand",
            "Quand",
            texte_quand,
            tri=(Indisponibilite.jour, Indisponibilite.date_debut, Indisponibilite.debut_min),
        ),
        Colonne("plage", "Horaire", texte_plage, tri=Indisponibilite.debut_min),
        Colonne("motif", "Motif", lambda i: i.motif, tri=Indisponibilite.motif, texte=True),
    ),
    tri_defaut="ressource",
    recherche=(Indisponibilite.motif, Enseignant.nom, Enseignant.prenom, Salle.code, Groupe.code),
    jointures=(
        (Enseignant, Indisponibilite.enseignant_id == Enseignant.id),
        (Salle, Indisponibilite.salle_id == Salle.id),
        (Groupe, Indisponibilite.groupe_id == Groupe.id),
    ),
    chargements=(
        selectinload(Indisponibilite.enseignant),
        selectinload(Indisponibilite.salle),
        selectinload(Indisponibilite.groupe),
    ),
    filtres=(
        Filtre(
            "cible",
            "Type",
            lambda s: [(str(v), libelle) for v, libelle in CHOIX_CIBLE],
            lambda v: Indisponibilite.cible == v,
            champ="cible",
        ),
        Filtre(
            "forme",
            "Quand",
            lambda s: [(str(v), libelle) for v, libelle in CHOIX_FORME],
            lambda v: (
                Indisponibilite.jour.is_not(None)
                if v == "recurrente"
                else Indisponibilite.date_debut.is_not(None)
            ),
            champ="forme",
        ),
    ),
    valider=_valider_indisponibilite,
    preparer=_preparer_indisponibilite,
)
