"""Pré-vérifications : ce qu'on peut dire d'un calcul avant de le lancer.

`pre_verifier` est une fonction pure et rapide (moins d'une seconde sur un
établissement complet). Elle est appelée d'office par le moteur avant toute
résolution et peut l'être seule par l'interface pour prévenir l'utilisateur
(`edt.services.generation.pre_verifier_semestre`).

Familles examinées
------------------
1. **Dépliage** : besoins qui ne peuvent pas être dépliés en séances.
2. **Domaines vides** : séance sans aucun horaire ou sans aucune salle
   possible, en disant ce qui élimine les possibilités (voir
   `edt.solveur.preparation`).
3. **Horaires fixes** : verrou intenable seul, verrous qui se contredisent,
   besoin qui impose le même créneau à plusieurs de ses séances.
4. **Charge des groupes** : heures à placer rapportées au temps ouvert par la
   grille, indisponibilités et séances à horaire fixe déduites.
5. **Charge des enseignants** : idem, avec leurs maxima horaires.
6. **Salles** : heures des séances qui ne peuvent aller que dans une famille
   de salles, rapportées aux heures d'ouverture de ces salles.

Gravité : `ERREUR` quand l'impossibilité est certaine (le moteur refuse alors
le calcul), `AVERTISSEMENT` quand la situation est tendue (au-delà de
`SEUIL_TENSION` du temps disponible).

Chaque famille n'est examinée que si la contrainte qui la rend impossible est
active. Les bornes sont des conditions nécessaires : une charge qui passe ici
ne garantit pas qu'une solution existe.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import JOURS, SAMEDI, CibleIndisponibilite, Gravite
from edt.domaine.heures import duree_vers_texte
from edt.domaine.instantane import Indisponibilite, Instantane
from edt.solveur.preparation import Preparation, SeancePreparee
from edt.solveur.regles import blocs_de_la_grille, enumerer
from edt.verification import Anomalie

journal = logging.getLogger(__name__)

C = CodeContrainte

SEUIL_TENSION = 0.9
"""Part du temps disponible au-delà de laquelle une charge est signalée comme tendue."""

Plage = tuple[int, int, int]
"""Plage horaire d'un jour de la semaine : `(jour, début, fin)`."""

Charge = tuple[int, int, int, bool]
"""Pire semaine d'une ressource : `(numéro, minutes demandées, minutes disponibles, constante)`."""


def pre_verifier(
    instantane: Instantane,
    contraintes_desactivees: Iterable[str] = (),
    *,
    tolerer_violations_heritees: bool = False,
) -> list[Anomalie]:
    """Liste les impossibilités certaines et les situations tendues d'un calcul.

    `contraintes_desactivees` : codes que l'utilisateur a décochés ; les
    vérifications qui en dépendent sont sautées. Avec
    `tolerer_violations_heritees`, les contradictions entre verrous sont
    rapportées comme avertissements et non comme erreurs.

    Une liste sans anomalie de gravité `ERREUR` signifie que le calcul peut
    être lancé, pas qu'il réussira.
    """
    debut = time.perf_counter()
    preparation = Preparation(instantane, contraintes_desactivees, tolerer_violations_heritees)
    anomalies = diagnostiquer(preparation)
    journal.info(
        "Pré-vérifications : %d erreur(s), %d avertissement(s) en %.3f s",
        sum(1 for anomalie in anomalies if anomalie.bloquante),
        sum(1 for anomalie in anomalies if not anomalie.bloquante),
        time.perf_counter() - debut,
    )
    return anomalies


def diagnostiquer(preparation: Preparation) -> list[Anomalie]:
    """Pré-vérifications d'un calcul déjà préparé (utilisée par le moteur)."""
    regles, instantane = preparation.regles, preparation.instantane
    anomalies = [
        Anomalie(
            probleme.code,
            Gravite.ERREUR,
            f"{probleme.message} Corriger le besoin avant de lancer le calcul.",
        )
        for probleme in instantane.problemes_depliage
        if regles.active(probleme.code)
    ]
    anomalies.extend(preparation.anomalies)
    for conflit in preparation.conflits_verrous:
        if preparation.tolerer:
            conflit = Anomalie(
                conflit.code,
                Gravite.AVERTISSEMENT,
                f"{conflit.message} Cette violation héritée est tolérée à votre demande.",
                seances=conflit.seances,
                jour=conflit.jour,
            )
        anomalies.append(conflit)
    # Les charges se mesurent au temps ouvert par la grille : sans `HORAIRES`, les séances
    # peuvent en sortir et seuls les maxima des enseignants restent certains.
    blocs = blocs_de_la_grille(instantane) if regles.active(C.HORAIRES) else []
    if blocs and regles.active(C.GROUPE_UNIQUE):
        anomalies.extend(_charges_des_groupes(preparation, blocs))
    anomalies.extend(_charges_des_enseignants(preparation, blocs))
    if blocs and regles.active(C.SALLE_UNIQUE):
        anomalies.extend(_charges_des_salles(preparation, blocs))
    return anomalies


# --------------------------------------------------------------------------- #
# Temps disponible
# --------------------------------------------------------------------------- #


def _minutes_libres(blocs: Sequence[Plage], obstacles: Sequence[Plage]) -> dict[int, int]:
    """Par jour, minutes des demi-journées `blocs` qui ne sont couvertes par aucun obstacle."""
    par_jour: dict[int, int] = defaultdict(int)
    for jour, debut, fin in blocs:
        coupes = sorted(
            (max(debut, o_debut), min(fin, o_fin))
            for o_jour, o_debut, o_fin in obstacles
            if o_jour == jour and o_debut < fin and debut < o_fin
        )
        occupe, curseur = 0, debut
        for coupe_debut, coupe_fin in coupes:
            if coupe_fin > curseur:
                occupe += coupe_fin - max(coupe_debut, curseur)
                curseur = coupe_fin
        par_jour[jour] += (fin - debut) - occupe
    return par_jour


def _dans_la_grille(seance: SeancePreparee) -> bool:
    """Vrai si la séance est à placer et tiendra dans une demi-journée de la grille.

    Une séance à horaire fixe est comptée à part (elle occupe sa plage) ; une
    séance à heure imposée peut sortir de la grille : pour que la borne reste
    certaine, elle n'entre pas dans la demande.
    """
    return not seance.horaire_fixe and seance.besoin.debut_impose is None


def _pire_semaine(
    preparation: Preparation,
    seances: Sequence[SeancePreparee],
    indisponibilites: Sequence[Indisponibilite],
    blocs: Sequence[Plage],
    plafond_jour: int | None = None,
) -> Charge | None:
    """Semaine la plus chargée d'une ressource.

    La demande est la somme des durées des séances à horaire libre actives
    cette semaine-là ; le disponible est le temps des `blocs` de la grille,
    moins les indisponibilités récurrentes et les séances à horaire fixe, le
    tout borné par le maximum journalier `plafond_jour`. Renvoie `None` si
    aucune séance à horaire libre n'est à placer.
    """
    interdits = [(i.jour, i.debut, i.fin) for i in indisponibilites]
    resultats: dict[tuple[int, ...], tuple[int, int, int]] = {}
    for semaine in preparation.instantane.semaines:
        bit = 1 << semaine.numero
        actives = [seance for seance in seances if seance.semaines & bit]
        signature = tuple(seance.index for seance in actives)
        if signature in resultats or not actives:
            continue  # mêmes séances actives qu'une semaine déjà examinée
        demande = sum(seance.duree for seance in actives if _dans_la_grille(seance))
        if demande == 0:
            continue
        fixes = [
            creneau[:3]
            for seance in actives
            if (creneau := preparation.creneau_fixe(seance)) is not None
        ]
        libres = _minutes_libres(blocs, interdits + fixes)
        if plafond_jour is not None:
            # Les séances à horaire fixe consomment elles aussi le maximum journalier.
            deja: dict[int, int] = defaultdict(int)
            for jour, debut, fin in fixes:
                deja[jour] += fin - debut
            libres = {
                jour: min(minutes, max(0, plafond_jour - deja[jour]))
                for jour, minutes in libres.items()
            }
        resultats[signature] = (semaine.numero, demande, sum(libres.values()))
    if not resultats:
        return None
    numero, demande, disponible = max(resultats.values(), key=lambda r: (r[1] - r[2], -r[0]))
    toutes = sum(1 << semaine.numero for semaine in preparation.instantane.semaines)
    constante = len(resultats) == 1 and all(seance.semaines == toutes for seance in seances)
    return numero, demande, disponible, constante


def _quand(semaine: int, constante: bool) -> str:
    return "par semaine" if constante else f"la semaine {semaine} (la plus chargée)"


def _jours_possibles(preparation: Preparation, seances: Iterable[SeancePreparee]) -> set[int]:
    return {preparation.decoder(code)[0] for seance in seances for code in seance.debuts}


def _decoupes_par_jours(
    preparation: Preparation, seances: Sequence[SeancePreparee], blocs: Sequence[Plage]
) -> list[tuple[list[SeancePreparee], list[Plage]]]:
    """Sous-ensembles de séances à comparer au temps des jours où elles peuvent aller.

    D'abord toutes les séances face aux jours que l'une d'elles peut occuper,
    puis celles qui ne peuvent pas aller le samedi face aux jours hors samedi.
    """
    libres = [seance for seance in seances if _dans_la_grille(seance)]
    jours = _jours_possibles(preparation, libres)
    decoupes = [(list(seances), [bloc for bloc in blocs if bloc[0] in jours])]
    if SAMEDI in jours:
        sans_samedi = [
            seance
            for seance in seances
            if seance.horaire_fixe or SAMEDI not in _jours_possibles(preparation, [seance])
        ]
        jours_sans_samedi = _jours_possibles(
            preparation, (seance for seance in sans_samedi if _dans_la_grille(seance))
        )
        decoupes.append((sans_samedi, [bloc for bloc in blocs if bloc[0] in jours_sans_samedi]))
    return decoupes


def _anomalie_de_charge(
    code: CodeContrainte,
    sujet: str,
    verbe: str,
    charge: Charge,
    complement: str = "",
    **cibles,
) -> Anomalie | None:
    """Anomalie si la demande dépasse (erreur) ou approche (avertissement) le temps disponible."""
    semaine, demande, disponible, constante = charge
    if demande <= SEUIL_TENSION * disponible:
        return None
    chiffres = (
        f"{duree_vers_texte(demande)} de cours {_quand(semaine, constante)} pour "
        f"{duree_vers_texte(disponible)} disponibles{complement}"
    )
    if demande > disponible:
        return Anomalie(
            code, Gravite.ERREUR, f"{sujet} {verbe} {chiffres} : c'est impossible.", **cibles
        )
    return Anomalie(
        code,
        Gravite.AVERTISSEMENT,
        f"{sujet} {verbe} {chiffres} : l'emploi du temps sera difficile à trouver.",
        **cibles,
    )


# --------------------------------------------------------------------------- #
# Familles
# --------------------------------------------------------------------------- #


def _charges_des_groupes(preparation: Preparation, blocs: Sequence[Plage]) -> list[Anomalie]:
    """Charge de chaque groupe : ses séances et celles des groupes qui le contiennent."""
    instantane, regles = preparation.instantane, preparation.regles
    par_groupe: dict[int, list[SeancePreparee]] = defaultdict(list)
    for seance in preparation.seances:
        for groupe_id in seance.besoin.groupes:
            par_groupe[groupe_id].append(seance)
    parents = {groupe.id: groupe.parent_id for groupe in instantane.groupes}

    def lignee(groupe_id: int) -> list[int]:
        """Le groupe et ses ancêtres, tant qu'ils sont bien en conflit avec lui."""
        chaine, courant = [groupe_id], parents.get(groupe_id)
        while courant is not None and courant not in chaine:
            if not instantane.groupes_en_conflit(groupe_id, courant):
                break
            chaine.append(courant)
            courant = parents.get(courant)
        return chaine

    anomalies: list[Anomalie] = []
    en_erreur: set[int] = set()
    for groupe_id in sorted(par_groupe, key=lambda g: (len(lignee(g)), g)):
        chaine = lignee(groupe_id)
        if any(ancetre in en_erreur for ancetre in chaine[1:]):
            en_erreur.add(groupe_id)  # déjà expliqué par le groupe qui le contient
            continue
        seances = list({s.index: s for g in chaine for s in par_groupe.get(g, ())}.values())
        indisponibilites = (
            [i for g in chaine for i in regles.recurrentes(CibleIndisponibilite.GROUPE, g)]
            if regles.active(C.DISPO_GROUPE)
            else []
        )
        for sous_ensemble, blocs_utiles in _decoupes_par_jours(preparation, seances, blocs):
            charge = _pire_semaine(preparation, sous_ensemble, indisponibilites, blocs_utiles)
            anomalie = charge and _anomalie_de_charge(
                C.GROUPE_UNIQUE,
                f"Le groupe {instantane.groupe(groupe_id).code}",
                "demande",
                charge,
                groupes=(groupe_id,),
            )
            if anomalie:
                anomalies.append(anomalie)
                if anomalie.bloquante:
                    en_erreur.add(groupe_id)
                break
    return anomalies


def _charges_des_enseignants(preparation: Preparation, blocs: Sequence[Plage]) -> list[Anomalie]:
    """Charge de chaque enseignant non fictif : temps disponible et maxima horaires."""
    instantane, regles = preparation.instantane, preparation.regles
    par_enseignant: dict[int, list[SeancePreparee]] = defaultdict(list)
    for seance in preparation.seances:
        for enseignant_id in regles.enseignants_reels(seance.besoin):
            par_enseignant[enseignant_id].append(seance)

    anomalies: list[Anomalie] = []
    maxima = regles.active(C.MAX_ENSEIGNANT)
    for enseignant_id in sorted(par_enseignant):
        enseignant = instantane.enseignant(enseignant_id)
        seances = par_enseignant[enseignant_id]
        if maxima and enseignant.max_minutes_semaine is not None:
            # Le total d'une semaine ne dépend pas du placement : dépassement certain.
            totaux = {
                semaine.numero: sum(s.duree for s in seances if s.semaines & (1 << semaine.numero))
                for semaine in instantane.semaines
            }
            numero, total = max(totaux.items(), key=lambda item: (item[1], -item[0]))
            if total > enseignant.max_minutes_semaine:
                anomalies.append(
                    Anomalie(
                        C.MAX_ENSEIGNANT,
                        Gravite.ERREUR,
                        f"{enseignant.nom} doit assurer {duree_vers_texte(total)} de cours "
                        f"{_quand(numero, len(set(totaux.values())) == 1)} pour un maximum de "
                        f"{duree_vers_texte(enseignant.max_minutes_semaine)} par semaine : "
                        "c'est impossible.",
                        enseignants=(enseignant_id,),
                    )
                )
                continue
        if maxima and enseignant.max_minutes_jour is not None:
            depassement = _fixes_au_dela_du_maximum(
                preparation, seances, enseignant.max_minutes_jour
            )
            if depassement is not None:
                jour, total = depassement
                anomalies.append(
                    Anomalie(
                        C.MAX_ENSEIGNANT,
                        Gravite.ERREUR,
                        f"{enseignant.nom} a déjà {duree_vers_texte(total)} de séances à horaire "
                        f"fixe (verrous, créneaux imposés) le {JOURS[jour]}, pour un maximum de "
                        f"{duree_vers_texte(enseignant.max_minutes_jour)} par jour : c'est "
                        "impossible.",
                        enseignants=(enseignant_id,),
                        jour=jour,
                    )
                )
                continue
        if not blocs or not regles.active(C.ENSEIGNANT_UNIQUE):
            continue
        indisponibilites = (
            regles.recurrentes(CibleIndisponibilite.ENSEIGNANT, enseignant_id)
            if regles.active(C.DISPO_ENSEIGNANT)
            else ()
        )
        plafond = enseignant.max_minutes_jour if maxima else None
        deductions = [
            texte
            for texte, present in (
                ("ses indisponibilités", indisponibilites),
                ("son maximum journalier", plafond is not None),
            )
            if present
        ]
        complement = f" (compte tenu de {' et de '.join(deductions)})" if deductions else ""
        for sous_ensemble, blocs_utiles in _decoupes_par_jours(preparation, seances, blocs):
            charge = _pire_semaine(
                preparation, sous_ensemble, indisponibilites, blocs_utiles, plafond
            )
            anomalie = charge and _anomalie_de_charge(
                C.ENSEIGNANT_UNIQUE,
                enseignant.nom,
                "doit assurer",
                charge,
                complement,
                enseignants=(enseignant_id,),
            )
            if anomalie:
                anomalies.append(anomalie)
                break
    return anomalies


def _fixes_au_dela_du_maximum(
    preparation: Preparation, seances: Sequence[SeancePreparee], maximum: int
) -> tuple[int, int] | None:
    """`(jour, minutes)` si les séances à horaire fixe d'un jour dépassent seules le maximum."""
    for semaine in preparation.instantane.semaines:
        par_jour: dict[int, int] = defaultdict(int)
        for seance in seances:
            creneau = preparation.creneau_fixe(seance)
            if creneau is not None and seance.semaines >> semaine.numero & 1:
                par_jour[creneau[0]] += creneau[2] - creneau[1]
        for jour, total in sorted(par_jour.items()):
            if total > maximum:
                return jour, total
    return None


def _charges_des_salles(preparation: Preparation, blocs: Sequence[Plage]) -> list[Anomalie]:
    """Demande rapportée à l'offre, par famille de salles.

    Une famille est l'ensemble des salles qui conviennent à un besoin (type,
    capacité, équipements, accessibilité). Toutes les séances qui ne peuvent
    aller que dans des salles de cette famille doivent tenir dans les heures
    d'ouverture de ces salles, indisponibilités récurrentes déduites.
    """
    instantane, regles = preparation.instantane, preparation.regles
    par_famille: dict[frozenset[int], list[SeancePreparee]] = defaultdict(list)
    for seance in preparation.seances:
        if seance.verrouillee or seance.besoin.debut_impose is not None:
            continue  # créneau fixé ou imposé, peut-être hors grille : hors de cette borne
        salles = frozenset(s.id for s in regles.salles_compatibles(seance.besoin).salles)
        if salles:
            par_famille[salles].append(seance)

    ouverture = sum(fin - debut for _, debut, fin in blocs)
    offre_par_salle: dict[int, int] = {}
    for salle in instantane.salles:
        indisponibilites = (
            regles.recurrentes(CibleIndisponibilite.SALLE, salle.id)
            if regles.active(C.DISPO_SALLE)
            else ()
        )
        offre_par_salle[salle.id] = (
            sum(
                _minutes_libres(
                    blocs, [(i.jour, i.debut, i.fin) for i in indisponibilites]
                ).values()
            )
            if indisponibilites
            else ouverture
        )

    toutes = sum(1 << semaine.numero for semaine in instantane.semaines)
    anomalies: list[Anomalie] = []
    for famille in sorted(par_famille, key=lambda f: (len(f), sorted(f))):
        seances = [
            seance for autre, liste in par_famille.items() if autre <= famille for seance in liste
        ]
        par_masque: dict[int, int] = defaultdict(int)
        for seance in seances:
            par_masque[seance.semaines] += seance.duree
        demandes = {
            semaine.numero: sum(
                minutes for masque, minutes in par_masque.items() if masque >> semaine.numero & 1
            )
            for semaine in instantane.semaines
        }
        numero, demande = max(demandes.items(), key=lambda item: (item[1], -item[0]))
        offre = sum(offre_par_salle[salle_id] for salle_id in famille)
        codes = sorted(instantane.salle(salle_id).code for salle_id in famille)
        if len(codes) == 1:
            lieu = f"dans la salle {codes[0]}"
        else:
            lieu = f"dans l'une des {len(codes)} salles {enumerer(codes, maximum=4)}"
        anomalie = _anomalie_de_charge(
            C.SALLE_UNIQUE,
            f"Les {len(seances)} séance(s) qui ne peuvent avoir lieu que {lieu}",
            "demandent",
            (numero, demande, offre, set(par_masque) == {toutes}),
            " dans ces salles",
            salles=tuple(sorted(famille)),
        )
        if anomalie:
            anomalies.append(anomalie)
    return anomalies
