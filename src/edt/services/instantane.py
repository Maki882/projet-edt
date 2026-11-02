"""Chargement de l'instantané d'un semestre depuis la base.

`charger_instantane` est la seule passerelle entre l'ORM et la
représentation figée (`edt.domaine.instantane`) lue par le vérificateur et
le solveur. Elle résout au passage tout ce qui se calcule une fois pour
toutes : relation de conflit entre groupes, effectif et besoin
d'accessibilité de chaque besoin, types de salle acceptés, dépliage en
séances hebdomadaires, verrous et indices tirés de la version de base,
règles personnalisées actives résolues (`Instantane.regles`).

`charger_placements` et `charger_occurrences` donnent, dans le même
vocabulaire, le contenu d'une version enregistrée (pour la vérifier ou
l'afficher).
"""

from __future__ import annotations

import dataclasses
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from edt.db import modeles as m
from edt.db.moteur import identifiant_valide
from edt.domaine.calendrier import semaines_entre
from edt.domaine.depliage import deplier
from edt.domaine.enumerations import CibleIndisponibilite
from edt.domaine.instantane import (
    Besoin,
    Campus,
    Creneau,
    Enseignant,
    Groupe,
    Indisponibilite,
    Instantane,
    JourFerme,
    Occurrence,
    Placement,
    Reference,
    Salle,
    SeanceAPlacer,
    Trajet,
    id_seance,
)
from edt.domaine.regles import (
    ErreurSaisie,
    NaturePortee,
    RegleResolue,
    resoudre_regle,
    valider_regle,
)
from edt.erreurs import ErreurDonneesInvalides, ErreurIntrouvable, ErreurRegleMetier
from edt.services.conflits_groupes import conflits_groupes
from edt.services.parametres import lire_parametres
from edt.services.regles import charger_regles_actives

journal = logging.getLogger(__name__)


def charger_instantane(
    session: Session, semestre_id: int, version_base_id: int | None = None
) -> Instantane:
    """Construit l'instantané du semestre `semestre_id`.

    `version_base_id` désigne la version dont on reprend les verrous
    (`SeanceAPlacer.verrou`) et les placements comme indices
    (`SeanceAPlacer.indice`) ; par défaut, la version active du semestre s'il
    y en a une.

    Lève `ErreurIntrouvable` si le semestre ou la version n'existe pas,
    `ErreurRegleMetier` si la version appartient à un autre semestre,
    `ErreurDonneesInvalides` si un besoin vise un groupe d'une autre année ou
    si une règle personnalisée active est devenue invalide (elle est nommée).
    Les besoins impossibles à déplier ne lèvent rien : ils sont listés dans
    `Instantane.problemes_depliage`.
    """
    semestre = session.get(m.Semestre, semestre_id) if identifiant_valide(semestre_id) else None
    if semestre is None:
        raise ErreurIntrouvable(f"Le semestre {semestre_id} n'existe pas.")
    annee_id = semestre.annee_scolaire_id

    semaines = semaines_entre(semestre.date_debut, semestre.date_fin)
    groupes_orm = list(
        session.scalars(
            select(m.Groupe).where(m.Groupe.annee_scolaire_id == annee_id).order_by(m.Groupe.code)
        )
    )
    groupes = tuple(_groupe(g) for g in groupes_orm)
    conflits = conflits_groupes(session, annee_id)
    lies = _groupes_lies(groupes, conflits)
    flagges = frozenset(g.id for g in groupes if g.accessibilite_requise)

    salles_orm = list(
        session.scalars(
            select(m.Salle).options(selectinload(m.Salle.equipements)).order_by(m.Salle.code)
        )
    )
    enseignants_orm = list(session.scalars(select(m.Enseignant).order_by(m.Enseignant.nom)))

    groupes_par_id = {g.id: g for g in groupes}
    besoins = tuple(
        _besoin(b, lies, flagges, groupes_par_id)
        for b in session.scalars(
            select(m.Besoin)
            .where(m.Besoin.semestre_id == semestre_id)
            .options(
                selectinload(m.Besoin.groupes),
                selectinload(m.Besoin.enseignants),
                selectinload(m.Besoin.types_salle),
                selectinload(m.Besoin.equipements),
                selectinload(m.Besoin.matiere),
                selectinload(m.Besoin.type_seance),
            )
            .order_by(m.Besoin.id)
        )
    )
    jours_fermes = _jours_fermes(session, semestre, groupes)
    parametres = lire_parametres(session)
    depliage = deplier(
        besoins,
        semaines,
        jours_fermes,
        parametres.jours_travailles,
        periode=(semestre.date_debut, semestre.date_fin),
    )

    version_base = _version_base(session, semestre, version_base_id)
    seances = _attacher_version(session, depliage.seances, version_base)

    instantane = Instantane(
        semestre_id=semestre.id,
        libelle=semestre.libelle,
        date_debut=semestre.date_debut,
        date_fin=semestre.date_fin,
        semaines=semaines,
        creneaux=tuple(
            Creneau(c.jour, c.debut_min, c.fin_min, c.demi_journee)
            for c in session.scalars(
                select(m.Creneau).order_by(m.Creneau.jour, m.Creneau.debut_min)
            )
        ),
        campus=tuple(
            Campus(c.id, c.code, c.nom)
            for c in session.scalars(select(m.Campus).order_by(m.Campus.code))
        ),
        trajets=tuple(
            Trajet(t.campus_a_id, t.campus_b_id, t.duree_min)
            for t in session.scalars(select(m.Trajet))
        ),
        salles=tuple(_salle(s) for s in salles_orm),
        enseignants=tuple(_enseignant(e) for e in enseignants_orm),
        groupes=groupes,
        conflits_groupes=conflits,
        indisponibilites=_indisponibilites(session, {g.id for g in groupes}),
        jours_fermes=jours_fermes,
        parametres=parametres,
        besoins=besoins,
        seances=seances,
        problemes_depliage=depliage.problemes,
        version_base_id=version_base.id if version_base is not None else None,
        types_salle=tuple(
            Reference(t.id, t.code, t.libelle)
            for t in session.scalars(select(m.TypeSalle).order_by(m.TypeSalle.code))
        ),
        equipements=tuple(
            Reference(e.id, e.code, e.libelle)
            for e in session.scalars(select(m.Equipement).order_by(m.Equipement.code))
        ),
    )
    instantane = dataclasses.replace(
        instantane, regles=_regles_resolues(session, semestre, instantane)
    )
    journal.info(
        "Instantané du semestre %s chargé : %d besoins, %d séances à placer, %d salles, "
        "%d règle(s) personnalisée(s), %d problème(s) de dépliage, version de base %s",
        semestre.libelle,
        len(besoins),
        len(seances),
        len(instantane.salles),
        len(instantane.regles),
        len(depliage.problemes),
        instantane.version_base_id,
    )
    return instantane


def _regles_resolues(
    session: Session, semestre: m.Semestre, instantane: Instantane
) -> tuple[RegleResolue, ...]:
    """Règles actives de l'année dont la période recoupe le semestre, résolues.

    Une règle active devenue invalide (paramètre hors bornes, cible hors de
    l'année, code de salle ou de campus disparu) fait échouer le chargement
    en la nommant : rien ne s'applique en silence. Une règle sur un besoin
    d'un autre semestre est simplement hors périmètre.
    """
    codes_connus = {
        "campus": {c.code for c in instantane.campus},
        "salle": {s.code for s in instantane.salles},
        "type_seance": set(session.scalars(select(m.TypeSeance.code))),
    }
    # Deux salles peuvent porter le même code sur deux campus : un code donne tous ses ids.
    salles_par_code: dict[str, list[int]] = {}
    for salle in instantane.salles:
        salles_par_code.setdefault(salle.code, []).append(salle.id)
    identifiants: dict[str, dict] = {
        "campus": {c.code: c.id for c in instantane.campus},
        "salle": salles_par_code,
        "type_seance": dict(session.execute(select(m.TypeSeance.code, m.TypeSeance.id)).all()),
    }
    connues = {
        NaturePortee.GROUPE: {g.id for g in instantane.groupes},
        NaturePortee.ENSEIGNANT: {e.id for e in instantane.enseignants},
        NaturePortee.CAMPUS: {c.id for c in instantane.campus},
        NaturePortee.TYPE_SEANCE: set(identifiants["type_seance"].values()),
        NaturePortee.MATIERE: {b.matiere_id for b in instantane.besoins},
        NaturePortee.BESOIN: set(
            session.scalars(
                select(m.Besoin.id)
                .join(m.Semestre, m.Besoin.semestre_id == m.Semestre.id)
                .where(m.Semestre.annee_scolaire_id == semestre.annee_scolaire_id)
            )
        ),
    }
    besoins_du_semestre = {b.id for b in instantane.besoins}
    resolues = []
    for regle in charger_regles_actives(session, semestre.annee_scolaire_id):
        if regle.semestre_id is not None and regle.semestre_id != semestre.id:
            continue
        nom = f"La règle {regle.code} (« {regle.type.libelle} », {regle.portee.designation})"
        try:
            regle = valider_regle(regle, instantane.parametres, codes_connus)
        except ErreurSaisie as erreur:
            raise ErreurDonneesInvalides(
                f"{nom} est devenue invalide : la corriger ou la désactiver.", erreur.details
            ) from erreur
        nature = regle.portee.nature
        if nature is not NaturePortee.ETABLISSEMENT and (
            regle.portee.cible_id not in connues[nature] and nature is not NaturePortee.MATIERE
        ):
            raise ErreurDonneesInvalides(
                f"{nom} vise une cible qui n'existe plus dans l'année du semestre : la corriger "
                "ou la désactiver."
            )
        if nature is NaturePortee.BESOIN and regle.portee.cible_id not in besoins_du_semestre:
            continue  # le besoin est dans l'autre semestre : hors périmètre, sans effet ici
        try:
            resolue = resoudre_regle(regle, instantane, identifiants)
        except ErreurDonneesInvalides as erreur:
            raise ErreurDonneesInvalides(
                f"{nom} est devenue invalide : la corriger ou la désactiver.", [erreur.message]
            ) from erreur
        if resolue.semaines:
            resolues.append(resolue)
    return tuple(resolues)


def charger_placements(session: Session, version_id: int) -> tuple[Placement, ...]:
    """Placements de la semaine type d'une version enregistrée, triés par jour et heure."""
    _exiger_version(session, version_id)
    lignes = session.scalars(
        select(m.SeanceHebdo)
        .where(m.SeanceHebdo.version_id == version_id)
        .order_by(m.SeanceHebdo.jour, m.SeanceHebdo.debut_min, m.SeanceHebdo.id)
    )
    return tuple(_placement(ligne) for ligne in lignes)


def charger_occurrences(session: Session, version_id: int) -> tuple[Occurrence, ...]:
    """Séances datées d'une version enregistrée, triées par date et heure."""
    _exiger_version(session, version_id)
    lignes = session.execute(
        select(m.Occurrence, m.SeanceHebdo.rang)
        .outerjoin(m.SeanceHebdo, m.Occurrence.seance_hebdo_id == m.SeanceHebdo.id)
        .where(m.Occurrence.version_id == version_id)
        .order_by(m.Occurrence.date, m.Occurrence.debut_min, m.Occurrence.id)
    )
    return tuple(
        Occurrence(
            id=o.id,
            besoin_id=o.besoin_id,
            seance_id=id_seance(o.besoin_id, rang) if rang is not None else None,
            date=o.date,
            debut=o.debut_min,
            fin=o.fin_min,
            salle_id=o.salle_id,
            statut=o.statut,
        )
        for o, rang in lignes
    )


# --------------------------------------------------------------------------- #
# Conversions ORM -> domaine
# --------------------------------------------------------------------------- #


def _groupe(g: m.Groupe) -> Groupe:
    return Groupe(
        id=g.id,
        code=g.code,
        libelle=g.libelle,
        nature=g.nature,
        parent_id=g.parent_id,
        effectif=g.effectif,
        accessibilite_requise=g.accessibilite_requise,
    )


def _salle(s: m.Salle) -> Salle:
    return Salle(
        id=s.id,
        code=s.code,
        campus_id=s.campus_id,
        capacite=s.capacite,
        type_salle_id=s.type_salle_id,
        equipements=frozenset(e.id for e in s.equipements),
        accessibilite=s.accessibilite,
    )


def _enseignant(e: m.Enseignant) -> Enseignant:
    return Enseignant(
        id=e.id,
        identifiant=e.identifiant,
        nom=e.nom_affiche,
        statut=e.statut,
        max_minutes_jour=e.max_minutes_jour,
        max_minutes_semaine=e.max_minutes_semaine,
        fictif=e.fictif,
    )


def _placement(ligne: m.SeanceHebdo) -> Placement:
    return Placement(
        seance_id=id_seance(ligne.besoin_id, ligne.rang),
        jour=ligne.jour,
        debut=ligne.debut_min,
        fin=ligne.fin_min,
        salle_id=ligne.salle_id,
    )


def _groupes_lies(
    groupes: tuple[Groupe, ...], conflits: frozenset[tuple[int, int]]
) -> dict[int, set[int]]:
    lies = {g.id: {g.id} for g in groupes}
    for a, b in conflits:
        lies[a].add(b)
        lies[b].add(a)
    return lies


def _besoin(
    b: m.Besoin,
    lies: dict[int, set[int]],
    flagges: frozenset[int],
    groupes: dict[int, Groupe],
) -> Besoin:
    etrangers = [g.code for g in b.groupes if g.id not in groupes]
    if etrangers:
        raise ErreurDonneesInvalides(
            f"Le besoin {b.id} ({b.matiere.libelle}) vise des groupes d'une autre année "
            f"scolaire que son semestre : {', '.join(etrangers)}."
        )
    ids_groupes = frozenset(g.id for g in b.groupes)
    types_acceptes = frozenset(t.id for t in b.types_salle)
    if not types_acceptes and b.type_seance.type_salle_defaut_id is not None:
        types_acceptes = frozenset({b.type_seance.type_salle_defaut_id})

    base = b.libelle or f"{b.type_seance.code} {b.matiere.libelle}"
    codes = ", ".join(sorted(g.code for g in b.groupes))
    return Besoin(
        id=b.id,
        libelle=f"{base} - {codes}" if codes else base,
        matiere_id=b.matiere_id,
        matiere_code=b.matiere.code,
        type_seance_id=b.type_seance_id,
        type_seance_code=b.type_seance.code,
        samedi_autorise=b.type_seance.samedi_autorise,
        duree=b.duree_min,
        groupes=ids_groupes,
        enseignants=frozenset(e.id for e in b.enseignants),
        effectif=sum(g.effectif for g in b.groupes),
        accessibilite_requise=any(not lies[g].isdisjoint(flagges) for g in ids_groupes),
        types_salle_acceptes=types_acceptes,
        equipements_requis=frozenset(e.id for e in b.equipements),
        salle_imposee_id=b.salle_imposee_id,
        campus_impose_id=b.campus_impose_id,
        jour_impose=b.jour_impose,
        debut_impose=b.debut_impose_min,
        mode_volume=b.mode_volume,
        nombre=b.nombre,
        max_par_semaine=b.max_par_semaine,
        date_debut=b.date_debut,
        date_fin=b.date_fin,
        date_ponctuelle=b.date_ponctuelle,
    )


def _jours_fermes(
    session: Session, semestre: m.Semestre, groupes: tuple[Groupe, ...]
) -> tuple[JourFerme, ...]:
    """Fermetures qui recoupent le semestre ; la portée « groupe » est étendue aux descendants."""
    enfants: dict[int, list[int]] = {}
    for g in groupes:
        if g.parent_id is not None:
            enfants.setdefault(g.parent_id, []).append(g.id)

    def lignee_descendante(racine: int) -> frozenset[int]:
        vus, pile = {racine}, [racine]
        while pile:
            for enfant in enfants.get(pile.pop(), ()):
                if enfant not in vus:
                    vus.add(enfant)
                    pile.append(enfant)
        return frozenset(vus)

    lignes = session.scalars(
        select(m.JourFerme)
        .where(
            m.JourFerme.annee_scolaire_id == semestre.annee_scolaire_id,
            m.JourFerme.date_fin >= semestre.date_debut,
            m.JourFerme.date_debut <= semestre.date_fin,
        )
        .order_by(m.JourFerme.date_debut, m.JourFerme.id)
    )
    return tuple(
        JourFerme(
            date_debut=j.date_debut,
            date_fin=j.date_fin,
            libelle=j.libelle,
            nature=j.nature,
            groupes=lignee_descendante(j.groupe_id) if j.groupe_id is not None else None,
        )
        for j in lignes
    )


def _indisponibilites(session: Session, ids_groupes: set[int]) -> tuple[Indisponibilite, ...]:
    """Toutes les indisponibilités des enseignants, des salles et des groupes de l'année."""
    resultat = []
    for i in session.scalars(select(m.Indisponibilite).order_by(m.Indisponibilite.id)):
        if i.cible == CibleIndisponibilite.GROUPE and i.groupe_id not in ids_groupes:
            continue
        resultat.append(
            Indisponibilite(
                id=i.id,
                cible=i.cible,
                ressource_id=i.ressource_id,
                jour=i.jour,
                date_debut=i.date_debut,
                date_fin=i.date_fin,
                debut=i.debut_min,
                fin=i.fin_min,
                motif=i.motif or "",
                dure=i.dure,
                poids=i.poids,
            )
        )
    return tuple(resultat)


def _exiger_version(session: Session, version_id: int) -> m.VersionEdt:
    version = session.get(m.VersionEdt, version_id) if identifiant_valide(version_id) else None
    if version is None:
        raise ErreurIntrouvable(f"La version d'emploi du temps {version_id} n'existe pas.")
    return version


def _version_base(
    session: Session, semestre: m.Semestre, version_base_id: int | None
) -> m.VersionEdt | None:
    if version_base_id is None:
        actives = session.scalars(
            select(m.VersionEdt)
            .where(m.VersionEdt.semestre_id == semestre.id, m.VersionEdt.active.is_(True))
            .order_by(m.VersionEdt.id)
        ).all()
        if len(actives) > 1:
            # L'index unique partiel l'interdit ; une base créée sans lui peut en avoir deux.
            raise ErreurRegleMetier(
                f"Plusieurs versions d'emploi du temps sont actives pour « {semestre.libelle} » "
                f"(versions {', '.join(str(v.id) for v in actives)}) : en garder une seule, puis "
                "lancer « python -m edt verifier-schema » (l'index qui l'interdit manque)."
            )
        return actives[0] if actives else None
    version = _exiger_version(session, version_base_id)
    if version.semestre_id != semestre.id:
        raise ErreurRegleMetier(
            f"La version {version_base_id} appartient à un autre semestre que « {semestre.libelle} »."
        )
    return version


def _attacher_version(
    session: Session, seances: tuple[SeanceAPlacer, ...], version: m.VersionEdt | None
) -> tuple[SeanceAPlacer, ...]:
    """Complète les séances avec le verrou et l'indice issus de la version de base."""
    if version is None:
        return seances
    lignes = {
        id_seance(ligne.besoin_id, ligne.rang): ligne
        for ligne in session.scalars(
            select(m.SeanceHebdo).where(m.SeanceHebdo.version_id == version.id)
        )
    }
    connues = {seance.id for seance in seances}
    orphelines = sorted(set(lignes) - connues)
    if orphelines:
        journal.info(
            "Version %d : %d séance(s) enregistrée(s) ne correspondent plus à aucun besoin "
            "déplié et sont ignorées (%s)",
            version.id,
            len(orphelines),
            ", ".join(orphelines[:10]),
        )
    completees = []
    for seance in seances:
        ligne = lignes.get(seance.id)
        if ligne is None:
            completees.append(seance)
            continue
        placement = _placement(ligne)
        completees.append(
            SeanceAPlacer(
                id=seance.id,
                besoin=seance.besoin,
                rang=seance.rang,
                semaines=seance.semaines,
                verrou=placement if ligne.verrou else None,
                indice=placement,
            )
        )
    return tuple(completees)
