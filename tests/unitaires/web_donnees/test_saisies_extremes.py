"""Banque des cas limites : saisie aberrante passée directement au service des référentiels.

Un entier Python démesuré, sans formulaire pour le borner, est refusé avec un
message sous le champ au lieu d'atteindre la base (revue n° 18).
"""

from __future__ import annotations

import pytest
from tests.outils.web_donnees.aides import saisie_salle

from edt.db.moteur import session_base
from edt.services.referentiels import operations
from edt.services.referentiels.operations import ErreurSaisie

pytestmark = pytest.mark.cas_limite


def test_service_borne_les_entiers(ids):
    """Cas : un appel programmatique (API, assistant) passe un entier Python de 2**70. Risque :
    le service, sans formulaire pour le borner, le transmet à la base."""
    with session_base() as session, pytest.raises(ErreurSaisie) as erreur:
        operations.creer(session, "salle", saisie_salle(ids, capacite=2**70))
    assert "capacite" in erreur.value.champs
