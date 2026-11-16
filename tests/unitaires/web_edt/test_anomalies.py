"""Liste « Points à traiter » : séances datées qui ne font que répéter la semaine type."""

from __future__ import annotations

from edt.domaine.enumerations import Gravite
from edt.services.emploi_du_temps.anomalies import ATraiter, GroupeContrainte, GroupeGravite


def gravites(*couples: tuple[Gravite, str]) -> tuple[GroupeGravite, ...]:
    return tuple(
        GroupeGravite(gravite, gravite.value, (GroupeContrainte(code, code, "", (), 1),), 1)
        for gravite, code in couples
    )


def a_traiter(hebdo, datees) -> ATraiter:
    return ATraiter(
        semestre=None,
        version=None,
        hebdo=None,
        datees=None,
        gravites_hebdo=gravites(*hebdo),
        gravites_datees=gravites(*datees),
        a_programmer=(),
    )


def test_seances_datees_qui_repetent_la_semaine_type():
    erreur_salle = (Gravite.ERREUR, "SALLE_UNIQUE")
    pause = (Gravite.AVERTISSEMENT, "PAUSE")
    assert a_traiter([erreur_salle, pause], [erreur_salle]).datees_comme_hebdo
    # Une règle propre aux séances datées (jour fermé), ou une autre gravité : non.
    assert not a_traiter(
        [erreur_salle], [erreur_salle, (Gravite.ERREUR, "JOUR_FERME")]
    ).datees_comme_hebdo
    assert not a_traiter(
        [erreur_salle], [(Gravite.AVERTISSEMENT, "SALLE_UNIQUE")]
    ).datees_comme_hebdo
    assert not a_traiter([erreur_salle], []).datees_comme_hebdo  # rien à expliquer
