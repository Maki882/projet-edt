"""Contrôle direct du témoin d'un jeu fictif, indépendant du générateur.

Lit l'instantané officiel du semestre (`charger_instantane`) et vérifie,
règle par règle et sans rien réutiliser du moteur du témoin, que les places
du témoin respectent les contraintes dures du projet. Renvoie la liste des
violations en clair (vide = témoin valide). Volontairement simple et
quadratique : quelques secondes sur le jeu grand, réservé aux tests marqués perf.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from itertools import combinations

from edt.db.moteur import session_base
from edt.domaine.enumerations import CibleIndisponibilite, NatureJourFerme
from edt.domaine.instantane import Instantane, SeanceAPlacer
from edt.services.instantane import charger_instantane
from edt.synthese.rapport import PlaceTemoin, RapportJeu
from edt.verification import verifier


def problemes_du_temoin(url: str, rapport: RapportJeu) -> list[str]:
    """Tout ce qui est reproché au témoin d'un jeu, semestre par semestre (vide = valide).

    Anomalies bloquantes du vérificateur officiel, écarts aux contraintes dures
    (`violations`) et aux règles personnalisées (`violations_regles`).
    """
    problemes: list[str] = []
    with session_base(url) as session:
        for code, semestre_id in rapport.semestres.items():
            instantane = charger_instantane(session, semestre_id)
            temoin = rapport.temoin_par_semestre[code]
            problemes += [
                f"[{code}] {a.code} : {a.message}"
                for a in verifier(instantane, rapport.placements(code))
                if a.bloquante
            ]
            problemes += [f"[{code}] {v}" for v in violations(instantane, temoin)]
            problemes += [f"[{code}] {v}" for v in violations_regles(instantane, temoin)]
    return problemes


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
    # Salle.
    salle = inst.salle(place.salle_id)
    # Jours permis (jours travaillés, case « samedi autorisé », règles) et déjeuner fixe.
    if place.jour not in inst.reglages.jours_de(seance.id).jours_permis(salle.campus_id):
        erreurs.append(f"{nom} : jour {place.jour} interdit (JOURS)")
    plage = p.plage_dejeuner
    if plage is not None and place.debut < plage[1] and plage[0] < place.fin:
        erreurs.append(f"{nom} : recoupe le déjeuner (DEJEUNER)")
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


# --------------------------------------------------------------------------- #
# Règles personnalisées
# --------------------------------------------------------------------------- #

TYPES_CONTROLES = frozenset(
    {
        "JOURS_TRAVAILLES",
        "PLAGE_HORAIRE",
        "CRENEAUX_INTERDITS",
        "CAMPUS",
        "SALLES",
        "PAUSE_DEJEUNER",
        "PAUSE_MINIMALE",
        "MAX_PAR_JOUR",
        "JOURNEES_LIBRES",
        "CAMPUS_PAR_JOUR",
    }
)
"""Types de règle que `violations_regles` sait contrôler."""


def _libre_dans(plages, debut_fenetre, fin_fenetre, duree) -> bool:
    """Au moins `duree` minutes libres d'un seul tenant dans la fenêtre (ou aucune séance dedans)."""
    dedans = sorted(
        (max(d, debut_fenetre), min(f, fin_fenetre))
        for d, f in plages
        if d < fin_fenetre and debut_fenetre < f
    )
    if not dedans:
        return True
    libres, curseur = [], debut_fenetre
    for d, f in dedans:
        libres.append(d - curseur)
        curseur = max(curseur, f)
    libres.append(fin_fenetre - curseur)
    return max(libres) >= duree


def violations_regles(instantane: Instantane, temoin: Mapping[str, PlaceTemoin]) -> list[str]:
    """Écarts du témoin aux règles personnalisées actives et aux réglages par personne.

    Contrôle direct, type par type, avec `Instantane.regles`, `Instantane.reglages`
    et `Instantane.emplois` ; un type actif non contrôlé est lui-même signalé.
    """
    inst = instantane
    erreurs: list[str] = []
    seances = {s.id: s for s in inst.seances}
    place = {i: temoin[i] for i in seances if i in temoin}
    campus_de = {i: inst.salle(p.salle_id).campus_id for i, p in place.items()}

    # Jours : précédence résolue par l'instantané (standard ou règles).
    for i, p in place.items():
        permis = inst.reglages.jours_de(i).jours_permis(campus_de[i])
        if p.jour not in permis:
            erreurs.append(f"{i} : jour {p.jour} hors des jours permis {sorted(permis)}")

    # Emplois du temps : par personne, semaine et jour.
    def journees(emploi_seances):
        par = {}
        for i in emploi_seances:
            if i not in place:
                continue
            for semaine in seances[i].semaines:
                par.setdefault((semaine, place[i].jour), []).append(i)
        return par

    for emploi in inst.emplois:
        personne = emploi.personne
        dejeuner = inst.reglages.dejeuner[personne]
        pause = inst.reglages.pause[personne].duree
        for (semaine, jour), ids in journees(emploi.seances).items():
            plages = sorted((place[i].debut, place[i].fin) for i in ids)
            if dejeuner.plage is not None and any(
                d < dejeuner.plage[1] and dejeuner.plage[0] < f for d, f in plages
            ):
                erreurs.append(f"{personne} semaine {semaine} jour {jour} : déjeuner fixe occupé")
            if dejeuner.fenetre is not None and not _libre_dans(plages, *dejeuner.fenetre):
                erreurs.append(f"{personne} semaine {semaine} jour {jour} : pas de pause déjeuner")
            for (_, fin), (debut, _) in zip(plages, plages[1:], strict=False):
                if debut - fin < pause:
                    erreurs.append(
                        f"{personne} semaine {semaine} jour {jour} : {debut - fin} min de pause "
                        f"au lieu de {pause}"
                    )

    emplois = {e.personne: e for e in inst.emplois}
    for regle in inst.regles:
        nom, v = f"{regle.code} ({regle.phrase})", regle.valeurs
        if regle.type.value not in TYPES_CONTROLES:
            erreurs.append(f"{nom} : type actif non contrôlé")
            continue
        for i in sorted(regle.seances):
            if i not in place or seances[i].semaines.isdisjoint(regle.semaines):
                continue
            p = place[i]
            if regle.type.value == "PLAGE_HORAIRE" and (not v["jours"] or p.jour in v["jours"]):
                if p.debut < v["debut"] or p.fin > v["fin"]:
                    erreurs.append(f"{nom} : {i} hors de la plage")
            elif regle.type.value == "CRENEAUX_INTERDITS" and p.jour in v["jours"]:
                if p.debut < v["fin"] and v["debut"] < p.fin:
                    erreurs.append(f"{nom} : {i} sur un créneau interdit")
            elif regle.type.value == "CAMPUS":
                dedans = campus_de[i] in v["campus"]
                if dedans != (v["mode"] == "imposer"):
                    erreurs.append(f"{nom} : {i} sur un campus refusé")
            elif regle.type.value == "SALLES":
                dedans = p.salle_id in v["salles"]
                if dedans != (v["mode"] == "imposer"):
                    erreurs.append(f"{nom} : {i} dans une salle refusée")
        for personne in sorted(regle.personnes, key=lambda x: (x.nature, x.id)):
            par = journees(emplois[personne].seances)
            if regle.type.value == "MAX_PAR_JOUR":
                for (semaine, jour), ids in par.items():
                    if semaine not in regle.semaines:
                        continue
                    total = (
                        len(ids)
                        if v["unite"] == "seances"
                        else sum(place[i].fin - place[i].debut for i in ids)
                    )
                    if total > v["maximum"]:
                        erreurs.append(f"{nom} : {personne} semaine {semaine} jour {jour}")
            elif regle.type.value == "CAMPUS_PAR_JOUR":
                for (semaine, jour), ids in par.items():
                    if len({campus_de[i] for i in ids}) > 1:
                        erreurs.append(f"{nom} : {personne} semaine {semaine} jour {jour}")
            elif regle.type.value == "JOURNEES_LIBRES":
                if v["unite"] != "journee":
                    erreurs.append(f"{nom} : unité « demi-journée » non contrôlée")
                    break
                parmi = {j for j, _ in v["parmi"]} or set(inst.jours_travailles)
                for semaine in regle.semaines:
                    presents = {jour for (s, jour) in par if s == semaine}
                    if len(parmi - presents) < v["nombre"]:
                        erreurs.append(f"{nom} : {personne} semaine {semaine}")
    return erreurs
