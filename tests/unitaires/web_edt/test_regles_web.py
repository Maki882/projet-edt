"""Règles personnalisées : présentation d'une anomalie qui ne cite pas de règle."""

from __future__ import annotations

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.services.emploi_du_temps import presentation
from edt.verification.contrat import Anomalie


def test_anomalie_sans_regle_citee_reste_telle_quelle():
    simple = Anomalie(CodeContrainte.HORAIRES, Gravite.ERREUR, "Hors grille.")
    assert presentation.regle_citee(simple) is None
    sans_code = Anomalie(CodeContrainte.REGLE, Gravite.ERREUR, "Écart.", regle_id=4)
    citee = presentation.regle_citee(sans_code)
    assert citee.code == "règle n° 4" and citee.lien is None
