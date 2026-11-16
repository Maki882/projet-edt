"""Le témoin de chaque jeu passe le vérificateur officiel sans erreur ni avertissement.

Seules sont admises des informations `HORAIRES` sur des séances à créneau
imposé qui sortent de la grille (DST de 2e année à 8 h, amphi de rentrée,
partiels à 14 h).
"""

from __future__ import annotations

import pytest
from tests.outils.synthese.aides import Jeu

from edt.domaine.contraintes import CodeContrainte
from edt.domaine.enumerations import Gravite
from edt.services.instantane import charger_instantane
from edt.verification import verifier


def _anomalies_inattendues(jeu: Jeu) -> list[str]:
    inattendues = []
    with jeu.session() as session:
        for code, semestre_id in jeu.rapport.semestres.items():
            instantane = charger_instantane(session, semestre_id)
            for anomalie in verifier(instantane, jeu.rapport.placements(code)):
                admise = (
                    anomalie.gravite is Gravite.INFORMATION
                    and anomalie.code is CodeContrainte.HORAIRES
                    and all(
                        instantane.seance(s).debut_impose is not None
                        and instantane.seance(s).debut_impose
                        not in instantane.debuts_de_creneau(instantane.seance(s).jour_impose)
                        for s in anomalie.seances
                    )
                )
                if not admise:
                    inattendues.append(
                        f"[{code}] {anomalie.gravite} {anomalie.code} : {anomalie.message}"
                    )
    return inattendues


@pytest.mark.parametrize("nom_jeu", ["jeu_petit", "jeu_moyen"])
def test_temoin_accepte_par_le_verificateur(request, nom_jeu):
    jeu = request.getfixturevalue(nom_jeu)
    assert _anomalies_inattendues(jeu) == []
    for code, compte in jeu.rapport.anomalies_temoin.items():
        assert set(compte) <= {"information"}, code
        assert jeu.rapport.indicateurs[code].nb_anomalies_bloquantes == 0
        assert jeu.rapport.indicateurs[code].pourcentage_seances_placees == 100.0


@pytest.mark.perf
def test_temoin_du_grand_jeu_accepte_par_le_verificateur(jeu_grand):
    assert _anomalies_inattendues(jeu_grand) == []
    # Le DST de 2e année (8 h, avant l'ouverture) est bien signalé pour information.
    assert all(
        compte.get("information", 0) > 0 for compte in jeu_grand.rapport.anomalies_temoin.values()
    )


@pytest.mark.parametrize("nom_jeu", ["jeu_petit", "jeu_moyen", "jeu_grand"])
def test_temoin_sans_anomalie_de_regle(request, nom_jeu):
    """Le vérificateur réel ne relève aucune anomalie `REGLE` sur le témoin, à aucun niveau."""
    jeu = request.getfixturevalue(nom_jeu)
    with jeu.session() as session:
        for code, semestre_id in jeu.rapport.semestres.items():
            instantane = charger_instantane(session, semestre_id)
            assert instantane.regles, code
            anomalies = [
                f"[{code}] {a.gravite} : {a.message}"
                for a in verifier(instantane, jeu.rapport.placements(code))
                if a.code is CodeContrainte.REGLE
            ]
            assert anomalies == []
