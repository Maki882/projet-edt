"""Registre des entités éditables : code -> description.

from edt.services.referentiels.catalogue import ENTITES
ENTITES["salle"].champs
"""

from __future__ import annotations

from edt.services.referentiels.calendrier import ANNEE_SCOLAIRE, CRENEAU, JOUR_FERME, SEMESTRE
from edt.services.referentiels.description import Entite
from edt.services.referentiels.enseignements import BESOIN, INDISPONIBILITE, MATIERE, TYPE_SEANCE
from edt.services.referentiels.lieux import CAMPUS, EQUIPEMENT, SALLE, TYPE_SALLE
from edt.services.referentiels.personnes import ENSEIGNANT, ETUDIANT, GROUPE

ENTITES: dict[str, Entite] = {
    entite.code: entite
    for entite in (
        CAMPUS,
        TYPE_SALLE,
        EQUIPEMENT,
        SALLE,
        ENSEIGNANT,
        GROUPE,
        ETUDIANT,
        MATIERE,
        TYPE_SEANCE,
        BESOIN,
        INDISPONIBILITE,
        ANNEE_SCOLAIRE,
        SEMESTRE,
        JOUR_FERME,
        CRENEAU,
    )
}
"""Toutes les entités éditables, par code."""
