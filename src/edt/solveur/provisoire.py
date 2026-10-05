"""Moteur PROVISOIRE, remplacé par le lot « solveur ».

Ce moteur existe uniquement pour que l'interface, la ligne de commande et
l'orchestration des calculs puissent être développées et testées avant le
vrai solveur CP-SAT. Il est volontairement naïf :

- il place les séances une par une sur le **premier créneau libre** de la
  grille (pas de retour en arrière, pas d'objectif) ;
- il ne garantit que l'**unicité des ressources** (salle, enseignants,
  groupes en conflit, en tenant compte des semaines actives) et la
  **capacité** de la salle ;
- il honore les verrous et les créneaux imposés, écarte le samedi pour les
  types de séance qui ne l'autorisent pas, et c'est tout : type de salle,
  équipements, accessibilité, déjeuner, pause, trajets, indisponibilités et
  maxima sont **ignorés** ;
- quand il échoue, il n'a rien prouvé : il renvoie `TEMPS_DEPASSE` (« pas
  trouvé »), jamais `INFAISABLE`.

Ne pas l'améliorer : écrire le vrai moteur à côté et changer l'import de
`edt/solveur/__init__.py`.
"""

from __future__ import annotations

import logging
import threading
import time

from edt.domaine.heures import se_chevauchent
from edt.domaine.instantane import Instantane, Placement, Salle, SeanceAPlacer
from edt.solveur.contrat import OptionsSolveur, ResultatSolveur, StatutSolveur

journal = logging.getLogger(__name__)

NOM_MOTEUR = "provisoire"


def resoudre(
    instantane: Instantane,
    options: OptionsSolveur,
    arret: threading.Event | None = None,
) -> ResultatSolveur:
    """Place les séances de l'instantané de façon gloutonne (moteur provisoire).

    Même signature que le futur moteur : voir `edt.solveur.contrat`.
    """
    debut = time.perf_counter()
    journal.warning(
        "Moteur PROVISOIRE : seules l'unicité des ressources et la capacité sont garanties."
    )
    seances = sorted(
        instantane.seances,
        key=lambda s: (
            s.verrou is None,
            s.jour_impose is None,
            -s.besoin.effectif,
            s.besoin.id,
            s.rang,
        ),
    )
    salles = sorted(instantane.salles, key=lambda salle: (salle.capacite, salle.code))
    poses: list[tuple[SeanceAPlacer, Placement]] = []

    def terminer(statut: StatutSolveur, message: str) -> ResultatSolveur:
        duree = time.perf_counter() - debut
        journal.info("Fin du moteur provisoire : %s (%s)", statut.value, message)
        return ResultatSolveur(
            statut=statut,
            placements=tuple(p for _, p in poses) if statut is StatutSolveur.SOLUTION else (),
            contraintes_desactivees=options.contraintes_desactivees,
            message=message,
            avertissements=(
                "Emploi du temps produit par le moteur provisoire : seules l'unicité des "
                "ressources et la capacité des salles sont garanties.",
            ),
            statistiques={
                "moteur": NOM_MOTEUR,
                "duree_s": round(duree, 3),
                "nb_seances": len(instantane.seances),
                "nb_placees": len(poses),
            },
        )

    for seance in seances:
        if arret is not None and arret.is_set():
            return terminer(StatutSolveur.ARRETE, "Calcul arrêté à la demande de l'utilisateur.")
        if time.perf_counter() - debut > options.temps_limite_s:
            return terminer(
                StatutSolveur.TEMPS_DEPASSE,
                "Aucun emploi du temps trouvé dans le temps imparti.",
            )
        placement = _placer(instantane, seance, salles, poses)
        if placement is None:
            return terminer(
                StatutSolveur.TEMPS_DEPASSE,
                f"Le moteur provisoire n'a pas trouvé de place pour « {seance.besoin.libelle} » "
                f"(séance {seance.id}). Ce n'est pas une preuve d'impossibilité.",
            )
        poses.append((seance, placement))
        journal.debug("Séance %s placée : %s", seance.id, placement)

    return terminer(StatutSolveur.SOLUTION, f"{len(poses)} séance(s) placée(s).")


def _placer(
    instantane: Instantane,
    seance: SeanceAPlacer,
    salles: list[Salle],
    poses: list[tuple[SeanceAPlacer, Placement]],
) -> Placement | None:
    """Premier placement libre pour `seance`, ou `None`."""
    if seance.verrou is not None and seance.verrou.salle_id is not None:
        return seance.verrou

    for jour, debut in _candidats(instantane, seance):
        fin = debut + seance.duree
        gene = [
            (autre, place)
            for autre, place in poses
            if place.jour == jour
            and se_chevauchent(debut, fin, place.debut, place.fin)
            and not seance.semaines.isdisjoint(autre.semaines)
        ]
        if any(_partagent_personnes(instantane, seance, autre) for autre, _ in gene):
            continue
        salles_prises = {place.salle_id for _, place in gene}
        for salle in salles:
            if seance.besoin.salle_imposee_id not in (None, salle.id):
                continue
            if salle.id in salles_prises or salle.capacite < seance.besoin.effectif:
                continue
            return Placement(seance.id, jour, debut, fin, salle.id)
    return None


def _candidats(instantane: Instantane, seance: SeanceAPlacer) -> list[tuple[int, int]]:
    """Couples (jour, début) à essayer, dans l'ordre."""
    if seance.verrou is not None:
        return [(seance.verrou.jour, seance.verrou.debut)]
    jours = (
        [seance.jour_impose]
        if seance.jour_impose is not None
        else sorted({creneau.jour for creneau in instantane.creneaux})
    )
    candidats = []
    for jour in jours:
        if jour == 5 and not seance.besoin.samedi_autorise and seance.jour_impose is None:
            continue
        if seance.debut_impose is not None:
            candidats.append((jour, seance.debut_impose))
            continue
        candidats.extend(
            (jour, debut)
            for debut in instantane.debuts_de_creneau(jour)
            if debut + seance.duree <= instantane.parametres.fermeture_min
        )
    return candidats


def _partagent_personnes(instantane: Instantane, a: SeanceAPlacer, b: SeanceAPlacer) -> bool:
    """Vrai si les deux séances ont un enseignant (non fictif) commun ou des groupes en conflit."""
    communs = a.besoin.enseignants & b.besoin.enseignants
    if any(not instantane.enseignant(enseignant_id).fictif for enseignant_id in communs):
        return True
    return instantane.besoins_en_conflit_de_groupes(a.besoin, b.besoin)
