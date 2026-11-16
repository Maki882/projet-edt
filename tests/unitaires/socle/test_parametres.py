"""Paramètres globaux : une valeur corrompue en base ne bloque pas la réparation."""

from __future__ import annotations

import pytest

from edt.db.modeles import Parametre
from edt.domaine.parametres import definition
from edt.erreurs import ErreurDonneesInvalides
from edt.services.parametres import (
    anomalies_parametres,
    ecrire_parametre,
    lire_parametres,
    reinitialiser_parametres,
)


@pytest.mark.cas_limite
def test_un_parametre_corrompu_en_base_reste_reparable_par_l_ecriture_et_la_reinitialisation(
    base, session
):
    """Cas : `parametre.valeur` mal typée après un import, un script ou une requête SQL
    (« quinze » pour une durée, « vrai » pour un booléen).

    Risque : la lecture lèverait partout (422 sur /parametres et /calculs) et l'écriture comme
    la réinitialisation commencent par lire : l'application ne pourrait plus se réparer, seul
    `init-db --reset` resterait (perte des données).
    """
    session.get(Parametre, "pause_min").valeur = "quinze"
    session.get(Parametre, "objectif_actif").valeur = "vrai"
    session.flush()

    with pytest.raises(ErreurDonneesInvalides, match="pause_min"):
        lire_parametres(session)  # la lecture stricte nomme le paramètre fautif
    assert set(anomalies_parametres(session)) == {"pause_min", "objectif_actif"}
    tolerants = lire_parametres(session, tolerant=True)
    assert tolerants.pause_min == definition("pause_min").defaut
    assert tolerants.objectif_actif == definition("objectif_actif").defaut

    ecrire_parametre(session, "pause_min", 20)  # réparable malgré l'autre valeur corrompue
    assert session.get(Parametre, "pause_min").valeur == 20
    assert set(anomalies_parametres(session)) == {"objectif_actif"}
    reinitialiser_parametres(session)
    assert anomalies_parametres(session) == {}
    assert lire_parametres(session).pause_min == definition("pause_min").defaut
