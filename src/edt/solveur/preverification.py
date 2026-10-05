"""Pré-vérifications : ce qu'on peut dire d'un calcul avant de le lancer.

`pre_verifier` est une fonction pure et rapide (moins d'une seconde sur un
établissement complet). Elle est appelée d'office par le moteur avant toute
résolution et peut l'être seule par l'interface pour prévenir l'utilisateur.

Familles examinées
------------------
1. **Dépliage** : besoins qui ne peuvent pas être dépliés en séances.
2. **Domaines vides** : séance sans aucun horaire ou sans aucune salle
   possible, en disant ce qui élimine les possibilités (voir
   `edt.solveur.preparation`).
3. **Verrous** : verrou intenable seul, paires de verrous qui se contredisent.
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
from edt.domaine.enumerations import SAMEDI, CibleIndisponibilite, Gravite
from edt.domaine.heures import duree_vers_texte
from edt.domaine.instantane import Instantane
from edt.solveur.preparation import Preparation, SeancePreparee
from edt.solveur.regles import PlageInterdite, blocs_de_la_grille, enumerer
from edt.verification import Anomalie

journal = logging.getLogger(__name__)

C = CodeContrainte

SEUIL_TENSION = 0.9
"""Part du temps disponible au-delà de laquelle une charge est signalée comme tendue."""

Obstacle = tuple[int, int, int]
"""Plage occupée d'avance : `(jour, début, fin)`."""


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
    blocs = blocs_de_la_grille(instantane)
    if regles.active(C.GROUPE_UNIQUE):
        anomalies.extend(_charges_des_groupes(preparation, blocs))
    anomalies.extend(_charges_des_enseignants(preparation, blocs))
    if regles.active(C.SALLE_UNIQUE):
        anomalies.extend(_charges_des_salles(preparation, blocs))
    return anomalies


# --------------------------------------------------------------------------- #
# Temps disponible
# --------------------------------------------------------------------------- #


def _minutes_libres(blocs: Sequence[Obstacle], obstacles: Sequence[Obstacle]) -> dict[int, int]:
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


def _fixe(preparation: Preparation, seance: SeancePreparee) -> Obstacle | None:
    """Plage d'une séance dont l'horaire est déjà certain (un seul début possible)."""
    if len(seance.debuts) != 1:
        return None
    jour, minute = preparation.decoder(seance.debuts[0])
    return jour, minute, minute + seance.duree


def _pire_semaine(
    preparation: Preparation,
    seances: Sequence[SeancePreparee],
    plages: Sequence[PlageInterdite],
    blocs: Sequence[Obstacle],
    plafond_jour: int | None = None,
) -> tuple[int, int, int, bool] | None:
    """Semaine la plus chargée d'une ressource : `(semaine, demande, disponible, constante)`.

    La demande est la somme des durées des séances à horaire libre actives
    cette semaine-là ; le disponible est le temps des `blocs` de la grille,
    moins les plages interdites et les séances à horaire fixe. `constante`
    est vraie si toutes les semaines donnent les mêmes chiffres. Renvoie
    `None` si aucune séance libre n'est à placer.
    """
    resultats: list[tuple[int, int, int]] = []
    for semaine in preparation.instantane.semaines:
        bit = 1 << semaine.numero
        actives = [seance for seance in seances if seance.semaines & bit]
        fixes = [fixe for seance in actives if (fixe := _fixe(preparation, seance)) is not None]
        demande = sum(seance.duree for seance in actives if len(seance.debuts) != 1)
        if demande == 0:
            continue
        obstacles = fixes + [
            (plage.jour, plage.debut, plage.fin)
            for plage in plages
            if semaine.numero in plage.semaines
        ]
        libres = _minutes_libres(blocs, obstacles)
        if plafond_jour is not None:
            # Les séances à horaire fixe consomment elles aussi le maximum journalier.
            deja: dict[int, int] = defaultdict(int)
            for jour, debut, fin in fixes:
                deja[jour] += fin - debut
            libres = {
                jour: min(minutes, max(0, plafond_jour - deja[jour]))
                for jour, minutes in libres.items()
            }
        resultats.append((semaine.numero, demande, sum(libres.values())))
    if not resultats:
        return None
    semaine, demande, disponible = max(resultats, key=lambda r: (r[1] - r[2], -r[0]))
    constante = len({(d, c) for _, d, c in resultats}) == 1 and len(resultats) == len(
        preparation.instantane.semaines
    )
    return semaine, demande, disponible, constante


def _quand(semaine: int, constante: bool) -> str:
    return "par semaine" if constante else f"la semaine {semaine} (la plus chargée)"


def _jours_possibles(preparation: Preparation, seances: Sequence[SeancePreparee]) -> set[int]:
    return {preparation.decoder(code)[0] for seance in seances for code in seance.debuts}


def _anomalie_de_charge(
    code: CodeContrainte,
    sujet: str,
    verbe: str,
    resultat: tuple[int, int, int, bool],
    complement: str = "",
    **cibles,
) -> Anomalie | None:
    """Anomalie de charge si la demande dépasse (erreur) ou approche (avertissement) le disponible."""
    semaine, demande, libres, constante = resultat
    if demande <= SEUIL_TENSION * libres:
        return None
    chiffres = (
        f"{duree_vers_texte(demande)} de cours {_quand(semaine, constante)} pour "
        f"{duree_vers_texte(libres)} disponibles{complement}"
    )
    if demande > libres:
        return Anomalie(
            code,
            Gravite.ERREUR,
            f"{sujet} {verbe} {chiffres} : c'est impossible.",
            **cibles,
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


def _charges_des_groupes(preparation: Preparation, blocs: Sequence[Obstacle]) -> list[Anomalie]:
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
    groupes = sorted(par_groupe, key=lambda g: (len(lignee(g)), g))
    for groupe_id in groupes:
        chaine = lignee(groupe_id)
        if any(ancetre in en_erreur for ancetre in chaine[1:]):
            en_erreur.add(groupe_id)  # déjà expliqué par le groupe qui le contient
            continue
        seances = list({s.index: s for g in chaine for s in par_groupe.get(g, ())}.values())
        plages = (
            [
                plage
                for g in chaine
                for plage in regles.plages_interdites(CibleIndisponibilite.GROUPE, g)
            ]
            if regles.active(C.DISPO_GROUPE)
            else []
        )
        code_groupe = instantane.groupe(groupe_id).code
        for sous_ensemble, blocs_utiles in _decoupes_par_jours(preparation, seances, blocs):
            resultat = _pire_semaine(preparation, sous_ensemble, plages, blocs_utiles)
            if resultat is None:
                continue
            anomalie = _anomalie_de_charge(
                C.GROUPE_UNIQUE,
                f"Le groupe {code_groupe}",
                "demande",
                resultat,
                groupes=(groupe_id,),
            )
            if anomalie is not None:
                anomalies.append(anomalie)
                if anomalie.bloquante:
                    en_erreur.add(groupe_id)
                break
    return anomalies


def _decoupes_par_jours(
    preparation: Preparation, seances: Sequence[SeancePreparee], blocs: Sequence[Obstacle]
) -> list[tuple[list[SeancePreparee], list[Obstacle]]]:
    """Sous-ensembles de séances à comparer au temps des jours où elles peuvent aller.

    D'abord toutes les séances face aux jours que l'une d'elles peut occuper,
    puis celles qui ne peuvent pas aller le samedi face aux jours hors samedi.
    """
    libres = [seance for seance in seances if len(seance.debuts) != 1]
    jours = _jours_possibles(preparation, libres)
    decoupes = [(list(seances), [bloc for bloc in blocs if bloc[0] in jours])]
    if SAMEDI in jours:
        sans_samedi = [
            seance
            for seance in seances
            if len(seance.debuts) == 1 or SAMEDI not in _jours_possibles(preparation, [seance])
        ]
        jours_sans_samedi = _jours_possibles(
            preparation, [seance for seance in sans_samedi if len(seance.debuts) != 1]
        )
        decoupes.append((sans_samedi, [bloc for bloc in blocs if bloc[0] in jours_sans_samedi]))
    return decoupes


def _charges_des_enseignants(preparation: Preparation, blocs: Sequence[Obstacle]) -> list[Anomalie]:
    """Charge de chaque enseignant non fictif : temps disponible et maxima horaires."""
    instantane, regles = preparation.instantane, preparation.regles
    par_enseignant: dict[int, list[SeancePreparee]] = defaultdict(list)
    for seance in preparation.seances:
        for enseignant_id in regles.enseignants_reels(seance.besoin):
            par_enseignant[enseignant_id].append(seance)

    anomalies: list[Anomalie] = []
    for enseignant_id in sorted(par_enseignant):
        enseignant = instantane.enseignant(enseignant_id)
        seances = par_enseignant[enseignant_id]
        maxima = regles.active(C.MAX_ENSEIGNANT)
        if maxima and enseignant.max_minutes_semaine is not None:
            totaux = {
                semaine.numero: sum(s.duree for s in seances if s.semaines & (1 << semaine.numero))
                for semaine in instantane.semaines
            }
            numero, total = max(
                totaux.items(), key=lambda item: (item[1], -item[0]), default=(0, 0)
            )
            if total > enseignant.max_minutes_semaine:
                constante = len(set(totaux.values())) == 1
                anomalies.append(
                    Anomalie(
                        C.MAX_ENSEIGNANT,
                        Gravite.ERREUR,
                        f"{enseignant.nom} doit assurer {duree_vers_texte(total)} de cours "
                        f"{_quand(numero, constante)} pour un maximum de "
                        f"{duree_vers_texte(enseignant.max_minutes_semaine)} par semaine : "
                        "c'est impossible.",
                        enseignants=(enseignant_id,),
                    )
                )
                continue
        if not regles.active(C.ENSEIGNANT_UNIQUE):
            continue
        plages = (
            regles.plages_interdites(CibleIndisponibilite.ENSEIGNANT, enseignant_id)
            if regles.active(C.DISPO_ENSEIGNANT)
            else ()
        )
        plafond = enseignant.max_minutes_jour if maxima else None
        for sous_ensemble, blocs_utiles in _decoupes_par_jours(preparation, seances, blocs):
            resultat = _pire_semaine(preparation, sous_ensemble, plages, blocs_utiles, plafond)
            if resultat is None:
                continue
            anomalie = _anomalie_de_charge(
                C.ENSEIGNANT_UNIQUE,
                enseignant.nom,
                "doit assurer",
                resultat,
                " (indisponibilités et maximum journalier déduits)" if plages or plafond else "",
                enseignants=(enseignant_id,),
            )
            if anomalie is not None:
                anomalies.append(anomalie)
                break
    return anomalies


def _charges_des_salles(preparation: Preparation, blocs: Sequence[Obstacle]) -> list[Anomalie]:
    """Demande rapportée à l'offre, par famille de salles.

    Une famille est l'ensemble des salles qui conviennent à un besoin (type,
    capacité, équipements, accessibilité). Toutes les séances qui ne peuvent
    aller que dans des salles de cette famille doivent tenir dans les heures
    d'ouverture de ces salles.
    """
    instantane, regles = preparation.instantane, preparation.regles
    par_famille: dict[frozenset[int], list[SeancePreparee]] = defaultdict(list)
    for seance in preparation.seances:
        if seance.verrouillee and seance.seance.verrou.salle_id is not None:
            continue  # sa salle est déjà fixée : elle ne concourt pas pour une famille
        salles = frozenset(s.id for s in regles.salles_compatibles(seance.besoin).salles)
        if salles:
            par_famille[salles].append(seance)

    ouverture = sum(fin - debut for _, debut, fin in blocs)
    # Heures perdues par salle et par semaine à cause de ses indisponibilités.
    pertes: dict[int, dict[int, int]] = {}
    if regles.active(C.DISPO_SALLE):
        for salle in instantane.salles:
            plages = regles.plages_interdites(CibleIndisponibilite.SALLE, salle.id)
            if plages:
                pertes[salle.id] = {
                    semaine.numero: ouverture
                    - sum(
                        _minutes_libres(
                            blocs,
                            [
                                (plage.jour, plage.debut, plage.fin)
                                for plage in plages
                                if semaine.numero in plage.semaines
                            ],
                        ).values()
                    )
                    for semaine in instantane.semaines
                }

    anomalies: list[Anomalie] = []
    for famille in sorted(par_famille, key=lambda f: (len(f), sorted(f))):
        seances = [
            seance for autre, liste in par_famille.items() if autre <= famille for seance in liste
        ]
        par_masque: dict[int, int] = defaultdict(int)
        for seance in seances:
            par_masque[seance.semaines] += seance.duree
        resultats = []
        for semaine in instantane.semaines:
            bit = 1 << semaine.numero
            demande = sum(minutes for masque, minutes in par_masque.items() if masque & bit)
            if demande == 0:
                continue
            offre = ouverture * len(famille) - sum(
                pertes[salle_id][semaine.numero] for salle_id in famille if salle_id in pertes
            )
            resultats.append((semaine.numero, demande, offre))
        if not resultats:
            continue
        numero, demande, offre = max(resultats, key=lambda r: (r[1] - r[2], -r[0]))
        constante = len({(d, o) for _, d, o in resultats}) == 1 and len(resultats) == len(
            instantane.semaines
        )
        codes = sorted(instantane.salle(salle_id).code for salle_id in famille)
        if len(codes) == 1:
            lieu = f"dans la salle {codes[0]}"
        else:
            lieu = f"dans l'une des {len(codes)} salles {enumerer(codes, maximum=4)}"
        anomalie = _anomalie_de_charge(
            C.SALLE_UNIQUE,
            f"Les {len(seances)} séance(s) qui ne peuvent avoir lieu que {lieu}",
            "demandent",
            (numero, demande, offre, constante),
            " dans ces salles",
            salles=tuple(sorted(famille)),
        )
        if anomalie is not None:
            anomalies.append(anomalie)
    return anomalies
