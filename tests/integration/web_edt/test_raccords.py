"""Pré-vérifications et instanciation d'une version issue d'un calcul complet."""

from __future__ import annotations

from edt.services.emploi_du_temps import raccords
from edt.verification.contrat import Anomalie


def test_pre_verifications_et_instanciation_reelles(version_generee, session):
    anomalies = raccords.pre_verifier_semestre(session, version_generee.semestre_id)
    assert isinstance(anomalies, tuple)
    assert all(isinstance(anomalie, Anomalie) for anomalie in anomalies)

    rapport = raccords.instancier_version(session, version_generee.version_id)
    assert rapport.nb_occurrences > 0
    assert rapport.nb_occurrences == (
        rapport.nb_planifiees + rapport.nb_replacees + rapport.nb_a_programmer
    )
