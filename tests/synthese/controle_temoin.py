"""Contrôle direct du témoin d'un jeu fictif, indépendant du générateur.

Lit l'instantané officiel du semestre (`charger_instantane`) et vérifie,
règle par règle et sans rien réutiliser du moteur du témoin, que les places
du témoin respectent les contraintes dures du projet. Renvoie la liste des
violations en clair (vide = témoin valide). Volontairement simple et
quadratique : à réserver aux jeux petit et moyen.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from itertools import combinations

from edt.domaine.enumerations import SAMEDI, CibleIndisponibilite, NatureJourFerme
from edt.domaine.instantane import Instantane, SeanceAPlacer
from edt.synthese.rapport import PlaceTemoin


def violations(instantane: Instantane, temoin: Mapping[str, PlaceTemoin]) -> list[str]:
    """Liste des règles que le témoin enfreint sur ce semestre."""
    inst = instantane
    erreurs: list[str] = []
    seances = {s.id: s for s in inst.seances}
    if set(seances) != set(temoin):
        erreurs.append(f"séances différentes : {sorted(set(seances) ^ set(temoin))[:10]} (VOLUME)")
    places = [(seances[i], temoin[i]) for i in sorted(temoin) if i in seances]
    for seance, place in places:
        erreurs += _unitaires(inst, seance, place)
    erreurs += _paires(inst, places)
    erreurs += _maxima(inst, places)
    erreurs += _couche_datee(inst, places)
    return erreurs


def _fin_demi_journee(inst: Instantane, jour: int, debut: int) -> int | None:
    creneau = next((c for c in inst.creneaux if c.jour == jour and c.debut == debut), None)
    if creneau is None:
        return None
    return max(
        c.fin for c in inst.creneaux if c.jour == jour and c.demi_journee == creneau.demi_journee
    )


def _unitaires(inst: Instantane, seance: SeanceAPlacer, place: PlaceTemoin) -> list[str]:
    b, p = seance.besoin, inst.parametres
    nom = f"{seance.id} ({b.libelle})"
    erreurs = []
    if place.fin - place.debut != b.duree:
        erreurs.append(f"{nom} : durée {place.fin - place.debut} au lieu de {b.duree}")
    # Jours, horaires, déjeuner.
    if seance.jour_impose is not None and place.jour != seance.jour_impose:
        erreurs.append(f"{nom} : jour imposé non respecté (HORAIRES)")
    if seance.debut_impose is not None:
        if place.debut != seance.debut_impose:
            erreurs.append(f"{nom} : début imposé non respecté (HORAIRES)")
    else:
        if place.debut not in inst.debuts_de_creneau(place.jour):
            erreurs.append(f"{nom} : ne commence pas sur un créneau (HORAIRES)")
        if place.debut < p.ouverture_min or place.fin > p.fermeture_min:
            erreurs.append(f"{nom} : hors de la plage d'ouverture (HORAIRES)")
        fin_demi = _fin_demi_journee(inst, place.jour, place.debut)
        if fin_demi is not None and place.fin > fin_demi:
            erreurs.append(f"{nom} : déborde de sa demi-journée (HORAIRES)")
    if place.jour > SAMEDI or (place.jour == SAMEDI and not b.samedi_autorise):
        erreurs.append(f"{nom} : jour {place.jour} interdit (JOURS)")
    if place.debut < p.dejeuner_fin_min and p.dejeuner_debut_min < place.fin:
        erreurs.append(f"{nom} : recoupe le déjeuner (DEJEUNER)")
    # Salle.
    salle = inst.salle(place.salle_id)
    if salle.capacite < b.effectif:
        erreurs.append(f"{nom} : {salle.code} trop petite (CAPACITE)")
    if b.types_salle_acceptes and salle.type_salle_id not in b.types_salle_acceptes:
        erreurs.append(f"{nom} : {salle.code} d'un type refusé (TYPE_SALLE)")
    if b.salle_imposee_id is not None and salle.id != b.salle_imposee_id:
        erreurs.append(f"{nom} : salle imposée non respectée (TYPE_SALLE)")
    if b.campus_impose_id is not None and salle.campus_id != b.campus_impose_id:
        erreurs.append(f"{nom} : campus imposé non respecté (TYPE_SALLE)")
    if not b.equipements_requis <= salle.equipements:
        erreurs.append(f"{nom} : {salle.code} sans les équipements requis (EQUIPEMENT)")
    if b.accessibilite_requise and not inst.salle_accessible(salle):
        erreurs.append(f"{nom} : {salle.code} non accessible (ACCESSIBILITE)")
    # Indisponibilités récurrentes.
    ressources = [(CibleIndisponibilite.SALLE, salle.id)]
    ressources += [
        (CibleIndisponibilite.ENSEIGNANT, e) for e in b.enseignants if not inst.enseignant(e).fictif
    ]
    ressources += [
        (CibleIndisponibilite.GROUPE, h) for g in b.groupes for h in inst.groupes_lies(g)
    ]
    for cible, ressource in ressources:
        for indispo in inst.indisponibilites_recurrentes(cible, ressource):
            if (
                indispo.jour == place.jour
                and place.debut < indispo.fin
                and indispo.debut < place.fin
            ):
                erreurs.append(f"{nom} : pendant une indisponibilité de {cible} {ressource}")
    return erreurs


def _paires(inst: Instantane, places) -> list[str]:
    pause = inst.parametres.pause_min
    erreurs = []
    par_jour: dict[int, list] = {}
    for seance, place in places:
        par_jour.setdefault(place.jour, []).append((seance, place))
    for liste in par_jour.values():
        for (sa, pa), (sb, pb) in combinations(liste, 2):
            if sa.semaines.isdisjoint(sb.semaines):
                continue
            chevauche = pa.debut < pb.fin and pb.debut < pa.fin
            if chevauche and pa.salle_id == pb.salle_id:
                erreurs.append(f"{sa.id} et {sb.id} : même salle en même temps (SALLE_UNIQUE)")
            reels_a = {e for e in sa.besoin.enseignants if not inst.enseignant(e).fictif}
            enseignant = not reels_a.isdisjoint(sb.besoin.enseignants)
            groupes = inst.besoins_en_conflit_de_groupes(sa.besoin, sb.besoin)
            if not (enseignant or groupes):
                continue
            if chevauche:
                erreurs.append(f"{sa.id} et {sb.id} : personne partagée en même temps")
                continue
            campus_a = inst.salle(pa.salle_id).campus_id
            campus_b = inst.salle(pb.salle_id).campus_id
            requis = max(pause, inst.trajet(campus_a, campus_b))
            ecart = max(pa.debut, pb.debut) - min(pa.fin, pb.fin)
            if ecart < requis:
                erreurs.append(
                    f"{sa.id} et {sb.id} : {ecart} min entre elles, {requis} requises "
                    "(PAUSE / CAMPUS_TRAJET)"
                )
    return erreurs


def _maxima(inst: Instantane, places) -> list[str]:
    erreurs = []
    par_semaine: Counter[tuple[int, int]] = Counter()
    par_jour: Counter[tuple[int, int, int]] = Counter()
    for seance, place in places:
        for e in seance.besoin.enseignants:
            for semaine in seance.semaines:
                par_semaine[(e, semaine)] += place.fin - place.debut
                par_jour[(e, semaine, place.jour)] += place.fin - place.debut
    for (e, semaine), minutes in par_semaine.items():
        maximum = inst.enseignant(e).max_minutes_semaine
        if maximum is not None and minutes > maximum:
            erreurs.append(f"enseignant {e}, semaine {semaine} : {minutes} min (MAX_ENSEIGNANT)")
    for (e, semaine, jour), minutes in par_jour.items():
        maximum = inst.enseignant(e).max_minutes_jour
        if maximum is not None and minutes > maximum:
            erreurs.append(
                f"enseignant {e}, semaine {semaine}, jour {jour} : {minutes} min (MAX_ENSEIGNANT)"
            )
    return erreurs


def _couche_datee(inst: Instantane, places) -> list[str]:
    """Le générateur promet que ses fermetures et indisponibilités datées ne touchent pas le témoin.

    Seuls les jours fériés (posés par l'initialisation, pas par le générateur)
    peuvent tomber sur une séance : la couche datée la replacera.
    """
    erreurs = []
    for seance, place in places:
        b = seance.besoin
        cibles = [(CibleIndisponibilite.SALLE, place.salle_id)]
        cibles += [(CibleIndisponibilite.ENSEIGNANT, e) for e in b.enseignants]
        cibles += [
            (CibleIndisponibilite.GROUPE, h) for g in b.groupes for h in inst.groupes_lies(g)
        ]
        for numero in seance.semaines:
            date = inst.date_de(numero, place.jour)
            fermeture = inst.fermeture(date, b)
            if fermeture is not None and fermeture.nature is not NatureJourFerme.FERIE:
                erreurs.append(f"{seance.id} : le {date} est fermé ({fermeture.libelle})")
            for cible, ressource in cibles:
                for indispo in inst.indisponibilites_de(cible, ressource):
                    if (
                        not indispo.recurrente
                        and indispo.concerne(date)
                        and place.debut < indispo.fin
                        and indispo.debut < place.fin
                    ):
                        erreurs.append(
                            f"{seance.id} : le {date} pendant « {indispo.motif} » ({cible})"
                        )
    return erreurs
