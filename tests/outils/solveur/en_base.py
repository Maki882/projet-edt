"""Aides partagées par les tests du solveur qui travaillent sur la base temporaire du jeu minimal.

- `OPTIONS` : options de calcul des tests de service (30 s, graine 1, un fil, sans arrêt sur
  stagnation, critère qui lit l'horloge : résultat reproductible quelle que soit la charge) ;
- `rendre_impossible`, `calcul_fictif`, `calcul_abandonne`, `etat`, `regles_du_jeu` : préparer
  ou lire la base ;
- `journalise` : enregistrements du journal capturés par `caplog`.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import EtatCalcul, ModeVolume
from edt.services.generation import CLE_PROCESSUS
from edt.services.regles import charger_regles_actives
from edt.solveur import OptionsSolveur

OPTIONS = OptionsSolveur(temps_limite_s=30, graine=1, nb_workers=1, arret_sur_stagnation=False)


def rendre_impossible(semestre_id: int) -> None:
    """Demande 60 séances par semaine à un groupe : aucune semaine ne peut les contenir."""
    with session_base() as session:
        besoin = session.scalar(
            select(m.Besoin).where(m.Besoin.mode_volume == ModeVolume.PAR_SEMAINE)
        )
        besoin.nombre = 60


def calcul_fictif(semestre_id: int, **champs) -> int:
    """Inscrit en base une ligne de calcul telle quelle (suivi ancien, abandonné ou corrompu).

    Par défaut : « en cours », lancé à l'instant avec les options des tests,
    sans statistiques ; `champs` remplace n'importe quelle colonne.
    """
    valeurs = {
        "semestre_id": semestre_id,
        "etat": EtatCalcul.EN_COURS,
        "demarre_le": dt.datetime.now(),
        "parametres": OPTIONS.vers_dict(),
        "contraintes_desactivees": [],
        "statistiques": {},
        "message": "",
        **champs,
    }
    with session_base() as session:
        calcul = m.Calcul(**valeurs)
        session.add(calcul)
        session.flush()
        return calcul.id


def calcul_abandonne(semestre_id: int) -> int:
    """Calcul « en cours » d'un processus disparu (n° 999 999) : application arrêtée en route."""
    return calcul_fictif(
        semestre_id,
        demarre_le=dt.datetime(2026, 10, 5, 8, 0),
        statistiques={CLE_PROCESSUS: 999_999},
    )


def etat(calcul_id: int) -> EtatCalcul:
    with session_base() as session:
        return EtatCalcul(session.get(m.Calcul, calcul_id).etat)


def journalise(caplog, texte: str, niveau: int = logging.WARNING) -> list[logging.LogRecord]:
    """Enregistrements du journal de ce niveau dont le message contient `texte`."""
    return [r for r in caplog.records if r.levelno == niveau and texte in r.getMessage()]


def regles_du_jeu(jeu_minimal) -> dict[str, int]:
    """Identifiants des règles actives du jeu minimal, par code."""
    with session_base() as session:
        return {
            regle.code: regle.id
            for regle in charger_regles_actives(session, jeu_minimal.annee_scolaire_id)
        }
