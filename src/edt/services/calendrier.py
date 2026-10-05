"""Calendrier en base : années scolaires, jours fériés, semestre proposé par défaut.

La création d'une année (avec ses semestres et ses jours fériés) vit dans
`edt.db.initialisation`, qui s'en sert aussi pour les données par défaut ;
elle est réexposée ici pour l'interface et la ligne de commande.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db.initialisation import assurer_annee_scolaire, preremplir_feries
from edt.db.modeles import Semestre
from edt.erreurs import ErreurIntrouvable

__all__ = ["assurer_annee_scolaire", "preremplir_feries", "semestre_par_defaut"]


def semestre_par_defaut(session: Session, aujourdhui: dt.date | None = None) -> Semestre:
    """Semestre à proposer par défaut dans l'interface.

    Le semestre qui contient la date du jour ; à défaut le prochain à venir ;
    à défaut le plus récent. Lève `ErreurIntrouvable` si aucun semestre n'existe.
    """
    aujourdhui = aujourdhui or dt.date.today()
    semestres = list(session.scalars(select(Semestre).order_by(Semestre.date_debut)))
    if not semestres:
        raise ErreurIntrouvable(
            "Aucun semestre n'existe encore : initialiser la base (« python -m edt init-db »)."
        )
    for semestre in semestres:
        if semestre.date_debut <= aujourdhui <= semestre.date_fin:
            return semestre
    a_venir = [semestre for semestre in semestres if semestre.date_debut > aujourdhui]
    return a_venir[0] if a_venir else semestres[-1]
