"""Empreinte « sémantique » d'un jeu fictif : ce que le générateur a décidé, sans le hasard du reste.

Elle décrit chaque ligne par ses **codes** (jamais par les identifiants en
base, qui dépendent des lignes par défaut de l'initialisation) et **sans les
noms et prénoms** tirés par Faker (sa liste de noms change d'une version à
l'autre). Elle couvre les lieux, les groupes, les inscriptions, les
enseignants, les matières, les besoins, les indisponibilités, les
fermetures, les règles et le témoin : deux jeux de même empreinte sont le
même établissement, la même demande et la même preuve de faisabilité.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from edt.db import modeles as m
from edt.synthese.rapport import RapportJeu


def _trie(elements) -> list:
    """Liste triée par représentation textuelle (des `None` se mêlent aux valeurs)."""
    return sorted(elements, key=repr)


def _date(valeur) -> str | None:
    return valeur.isoformat() if valeur is not None else None


def contenu_semantique(session, rapport: RapportJeu) -> dict[str, list]:
    """Le contenu du jeu, table par table, en tuples de codes triés."""
    groupes = {g.id: g for g in session.scalars(select(m.Groupe))}
    salles = {
        s.id: s for s in session.scalars(select(m.Salle).options(selectinload(m.Salle.equipements)))
    }
    enseignants = {e.id: e for e in session.scalars(select(m.Enseignant))}
    semestres = {s.id: s.code for s in session.scalars(select(m.Semestre))}

    def code_groupe(identifiant):
        return groupes[identifiant].code if identifiant is not None else None

    contenu: dict[str, list] = {}
    contenu["salles"] = _trie(
        (
            s.code,
            s.campus.code,
            s.etage,
            s.capacite,
            s.type_salle.code,
            s.accessibilite.value,
            tuple(sorted(e.code for e in s.equipements)),
        )
        for s in salles.values()
    )
    campus = {c.id: c.code for c in session.scalars(select(m.Campus))}
    contenu["trajets"] = _trie(
        (tuple(sorted((campus[t.campus_a_id], campus[t.campus_b_id]))), t.duree_min)
        for t in session.scalars(select(m.Trajet))
    )
    contenu["groupes"] = _trie(
        (
            g.code,
            g.nature.value,
            code_groupe(g.parent_id),
            g.effectif,
            g.accessibilite_requise,
        )
        for g in groupes.values()
    )
    contenu["chevauchements"] = _trie(
        tuple(sorted((code_groupe(a), code_groupe(b))))
        for a, b in session.execute(select(m.groupe_chevauchement)).all()
    )
    contenu["inscriptions"] = _trie(
        (e.identifiant, tuple(sorted(g.code for g in e.groupes)))
        for e in session.scalars(select(m.Etudiant).options(selectinload(m.Etudiant.groupes)))
    )
    contenu["enseignants"] = _trie(
        (
            e.identifiant,
            e.civilite,
            e.statut.value,
            e.fictif,
            e.max_minutes_jour,
            e.max_minutes_semaine,
        )
        for e in enseignants.values()
    )
    contenu["matieres"] = _trie(
        (x.code, x.libelle, code_groupe(x.promotion_id)) for x in session.scalars(select(m.Matiere))
    )
    besoins = {}
    for b in session.scalars(
        select(m.Besoin).options(
            selectinload(m.Besoin.groupes),
            selectinload(m.Besoin.enseignants),
            selectinload(m.Besoin.types_salle),
            selectinload(m.Besoin.equipements),
        )
    ):
        besoins[b.id] = (
            semestres[b.semestre_id],
            b.matiere.code,
            b.type_seance.code,
            b.libelle,
            b.duree_min,
            b.mode_volume.value,
            b.nombre,
            b.max_par_semaine,
            _date(b.date_debut),
            _date(b.date_fin),
            _date(b.date_ponctuelle),
            b.jour_impose,
            b.debut_impose_min,
            salles[b.salle_imposee_id].code if b.salle_imposee_id else None,
            b.campus_impose.code if b.campus_impose_id else None,
            tuple(sorted(g.code for g in b.groupes)),
            tuple(sorted(e.identifiant for e in b.enseignants)),
            tuple(sorted(t.code for t in b.types_salle)),
            tuple(sorted(e.code for e in b.equipements)),
        )
    contenu["besoins"] = _trie(besoins.values())

    def cible_indispo(i):
        if i.enseignant_id is not None:
            return enseignants[i.enseignant_id].identifiant
        if i.salle_id is not None:
            return salles[i.salle_id].code
        return code_groupe(i.groupe_id)

    contenu["indisponibilites"] = _trie(
        (
            i.cible.value,
            cible_indispo(i),
            i.jour,
            _date(i.date_debut),
            _date(i.date_fin),
            i.debut_min,
            i.fin_min,
            i.motif,
        )
        for i in session.scalars(select(m.Indisponibilite))
    )
    contenu["fermetures"] = _trie(
        (
            j.libelle,
            j.nature.value,
            _date(j.date_debut),
            _date(j.date_fin),
            code_groupe(j.groupe_id),
        )
        for j in session.scalars(select(m.JourFerme))
    )

    def cible_regle(r):
        for objet, attribut in (
            (r.groupe, "code"),
            (r.enseignant, "identifiant"),
            (r.matiere, "code"),
            (r.type_seance, "code"),
            (r.campus, "code"),
        ):
            if objet is not None:
                return getattr(objet, attribut)
        return None

    contenu["regles"] = _trie(
        (
            r.code,
            r.type.value,
            r.portee.value,
            cible_regle(r),
            json.dumps(r.parametres, sort_keys=True, ensure_ascii=False),
            r.niveau.value,
            r.etat.value,
            r.origine.value,
            semestres.get(r.semestre_id),
        )
        for r in session.scalars(select(m.Regle))
    )
    temoin = []
    for code, places in rapport.temoin_par_semestre.items():
        for seance, place in places.items():
            besoin_id, rang = (int(x) for x in seance.split("-"))
            temoin.append(
                (
                    code,
                    besoins[besoin_id],
                    rang,
                    place.jour,
                    place.debut,
                    place.fin,
                    salles[place.salle_id].code,
                )
            )
    contenu["temoin"] = _trie(temoin)
    return contenu


def empreinte_semantique(session, rapport: RapportJeu) -> str:
    """Condensé SHA-256 du contenu sémantique du jeu (voir le module)."""
    contenu = contenu_semantique(session, rapport)
    return hashlib.sha256(repr(sorted(contenu.items())).encode("utf-8")).hexdigest()
