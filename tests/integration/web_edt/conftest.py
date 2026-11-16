"""Fixtures des tests d'intégration des écrans « Générer », « Emploi du temps », « À traiter »
et de l'accueil.

Fixtures partagées avec les tests unitaires (`sans_prechauffage`, appliquée
d'office, `faux_verificateur`, `sans_verificateur`, `aujourdhui_fige`) :
`tests/outils/web_edt/fixtures.py`. Propres à l'intégration :

- `version_generee` : jeu minimal + un calcul réussi du vrai moteur (ses
  séances datées sont produites par l'orchestration) ;
- `seances_datees` : la même, après contrôle que ses séances datées existent ;
- `reperes` : séance, occurrence, calcul, version et semestre réels de
  `version_generee`, lus dans la base (jamais écrits en dur) ;
- `delai_verrou_court` : l'attente sur verrou réduite, pour constater sans
  attendre dix secondes ce qui se passe au-delà du délai.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select
from tests.outils.web_edt.aides import (
    DELAI,
    DELAI_VERROU_COURT_MS,
    Reperes,
    VersionGeneree,
    premiere_occurrence,
    seance_du_besoin,
)
from tests.outils.web_edt.fixtures import (  # noqa: F401
    aujourdhui_fige,
    faux_verificateur,
    sans_prechauffage,
    sans_verificateur,
)

from edt.db import modeles as m
from edt.db import moteur as module_moteur
from edt.db.moteur import liberer_moteurs, session_base
from edt.services import generation


@pytest.fixture
def version_generee(jeu_minimal) -> VersionGeneree:
    """Jeu minimal et une version active produite par le moteur (séances datées comprises)."""
    calcul_id = generation.lancer_calcul(jeu_minimal.semestre_id)
    suivi = generation.attendre_calcul(calcul_id, DELAI)
    assert suivi.reussi, suivi.message
    return VersionGeneree(
        semestre_id=jeu_minimal.semestre_id,
        annee_scolaire_id=jeu_minimal.annee_scolaire_id,
        calcul_id=calcul_id,
        version_id=suivi.version_id,
    )


@pytest.fixture
def seances_datees(version_generee) -> VersionGeneree:
    """La version générée ; ses séances datées existent dès la fin du calcul."""
    with session_base() as session:
        nombre = session.scalar(
            select(func.count())
            .select_from(m.Occurrence)
            .where(m.Occurrence.version_id == version_generee.version_id)
        )
    assert nombre, "le calcul aurait dû produire les séances datées"
    return version_generee


@pytest.fixture
def reperes(version_generee) -> Reperes:
    """Séance (CM de mathématiques de A1), première occurrence, calcul, version et semestre."""
    with session_base() as session:
        seance = seance_du_besoin(session, version_generee.version_id, "Mathématiques", "CM", "A1")
        occurrence = premiere_occurrence(session, version_generee.version_id)
        resultat = Reperes(
            seance=seance.id,
            occurrence=occurrence.id,
            calcul=version_generee.calcul_id,
            version=version_generee.version_id,
            semestre=version_generee.semestre_id,
        )
    return resultat


@pytest.fixture
def delai_verrou_court(monkeypatch) -> Iterator[int]:
    """Attente sur verrou réduite à `DELAI_VERROU_COURT_MS` pour les connexions ouvertes ensuite.

    Le moteur relit `DELAI_VERROU_MS` à chaque nouvelle connexion : les moteurs existants
    sont libérés pour que la suivante l'applique (comme la fixture du socle, sans l'importer).
    """
    monkeypatch.setattr(module_moteur, "DELAI_VERROU_MS", DELAI_VERROU_COURT_MS)
    liberer_moteurs()
    yield DELAI_VERROU_COURT_MS
    liberer_moteurs()
