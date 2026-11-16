"""Versions enregistrées du jeu minimal (`edt.db.jeux`), pour les tests des services.

`EMPLOI_DU_TEMPS` est construit à la main pour le jeu minimal et enregistré
par `enregistrer_version` comme version de référence. Il respecte toutes les
contraintes de la semaine type, y compris des cas délicats : deux séances au
même créneau sur des semaines disjointes (CM de physique et partiel), un
changement de campus séparé par le déjeuner, un DST imposé de trois heures,
des salles accessibles pour les groupes liés au groupe PMR.

Instancié naïvement sur le calendrier (chaque séance, chaque semaine active,
sans replacement), il donne en revanche des anomalies datées attendues : le
mercredi 11 novembre est férié et la salle L115 est en travaux du 2 au
6 novembre.

Les fixtures qui s'en servent sont dans `tests/unitaires/verification/conftest.py`,
`tests/outils/verification/fixtures.py` et `tests/integration/verification/test_commande.py`.
"""

from __future__ import annotations

from sqlalchemy import select

from edt.db import modeles as m
from edt.domaine.enumerations import OrigineVersion
from edt.domaine.heures import texte_vers_minutes
from edt.services.instantane import charger_instantane

LUNDI, MARDI, MERCREDI, JEUDI, VENDREDI = range(5)

EMPLOI_DU_TEMPS = {
    # (libellé du besoin, rang) : (jour, début, salle)
    ("CM Mathématiques - A1", 1): (LUNDI, "8h30", "N016"),
    ("CM Algorithmique - A1", 1): (LUNDI, "10h45", "N016"),
    ("TD Mathématiques - A1-G1", 1): (LUNDI, "13h45", "N105"),
    ("TD Mathématiques - A1-G2", 1): (LUNDI, "16h00", "N204"),
    # Semaines 1 à 7 et semaine 19 : même créneau, même salle, sans conflit.
    ("CM Physique - A1", 1): (MARDI, "8h30", "N016"),
    ("Partiel d'algorithmique - A1", 1): (MARDI, "8h30", "N016"),
    ("CM Physique - A1", 2): (MARDI, "10h45", "N016"),
    ("TP Algorithmique - A1-G2a", 1): (MARDI, "13h45", "N310"),
    ("TP Algorithmique - A1-G2b", 1): (MARDI, "16h00", "N310"),
    # A1-G1a requiert une salle accessible : L012, à Issy.
    ("TP Algorithmique - A1-G1a", 1): (MERCREDI, "8h30", "L012"),
    ("TP Algorithmique - A1-G1b", 1): (MERCREDI, "10h45", "L012"),
    # A1-G1b quitte Issy à 12 h 45 et a anglais à 13 h 45 à Notre-Dame : 1 h de trajet.
    ("LANGUE Anglais - A1-ANG-B2", 1): (MERCREDI, "13h45", "N105"),
    ("PROJET Projet d'intégration - A1-G1, A1-G2", 1): (MERCREDI, "16h00", "N016"),
    ("CM Électronique - A1", 1): (JEUDI, "8h30", "N016"),
    ("Devoir surveillé de mathématiques - A1", 1): (JEUDI, "13h45", "N016"),
    ("TP Électronique - A1-G1", 1): (VENDREDI, "8h30", "L115"),
    ("TP Électronique - A1-G2", 1): (VENDREDI, "10h45", "L115"),
}


def enregistrer_version(session, semestre_id: int, *, occurrences: bool = False) -> int:
    """Enregistre EMPLOI_DU_TEMPS comme version active ; renvoie son identifiant.

    Avec `occurrences`, instancie aussi chaque séance chaque semaine active
    (sans replacement des jours fermés).
    """
    instantane = charger_instantane(session, semestre_id)
    besoins = {besoin.libelle: besoin for besoin in instantane.besoins}
    salles = {salle.code: salle.id for salle in instantane.salles}
    version = m.VersionEdt(
        semestre_id=semestre_id,
        numero=1,
        libelle="Version saisie à la main",
        origine=OrigineVersion.REFERENCE,
        active=True,
    )
    lignes = []
    for (libelle, rang), (jour, debut, code_salle) in EMPLOI_DU_TEMPS.items():
        besoin = besoins[libelle]
        seance = instantane.seance(f"{besoin.id}-{rang}")
        debut_min = texte_vers_minutes(debut)
        ligne = m.SeanceHebdo(
            besoin_id=besoin.id,
            rang=rang,
            jour=jour,
            debut_min=debut_min,
            fin_min=debut_min + besoin.duree,
            salle_id=salles[code_salle],
            semaines=sorted(seance.semaines),
        )
        version.seances.append(ligne)
        lignes.append((ligne, seance))
    session.add(version)
    session.flush()
    if occurrences:
        for ligne, seance in lignes:
            for numero in sorted(seance.semaines):
                session.add(
                    m.Occurrence(
                        version_id=version.id,
                        seance_hebdo_id=ligne.id,
                        besoin_id=ligne.besoin_id,
                        date=instantane.date_de(numero, ligne.jour),
                        debut_min=ligne.debut_min,
                        fin_min=ligne.fin_min,
                        salle_id=ligne.salle_id,
                    )
                )
    session.commit()
    return version.id


def salle_par_code(session, code: str) -> m.Salle:
    """La salle `code` du jeu minimal (« N204 », « L115 »...)."""
    salle = session.scalar(select(m.Salle).where(m.Salle.code == code))
    assert salle is not None, f"salle {code} absente du jeu minimal"
    return salle
