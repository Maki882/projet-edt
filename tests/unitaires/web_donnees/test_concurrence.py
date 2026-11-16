"""Un seul chargement de jeu ou une seule remise à zéro à la fois, sur le service.

Le premier chargement est ralenti (génération remplacée par une attente) ;
pendant qu'il tient le verrou, toute autre demande au service est refusée.
"""

from __future__ import annotations

import pytest
from tests.outils.web_donnees.concurrence import DELAI_S, _en_arriere_plan

from edt.services import jeux


def test_service_refuse_un_second_chargement_ou_une_remise_a_zero(base, generation_lente):
    entre, liberer = generation_lente
    fil, premier = _en_arriere_plan(
        lambda: jeux.recharger_jeu_fictif("petit", 3, reinitialiser=True)
    )
    assert entre.wait(DELAI_S)
    assert jeux.operation_en_cours()

    with pytest.raises(jeux.ErreurChargementEnCours, match="déjà en cours"):
        jeux.recharger_jeu_fictif("petit", 4, reinitialiser=True)
    with pytest.raises(jeux.ErreurChargementEnCours, match="remettre la base à zéro"):
        jeux.remettre_a_zero()

    liberer.set()
    fil.join(DELAI_S)
    assert "erreur" not in premier, premier.get("erreur")
    assert premier["valeur"].graine == 3
    assert not jeux.operation_en_cours()
    jeux.remettre_a_zero()  # le verrou est rendu : la demande suivante passe
