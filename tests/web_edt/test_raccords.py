"""Raccords vers les lots en cours : absence explicite, lecture du rapport, panne journalisée."""

from __future__ import annotations

import logging
import sys
import types

import pytest

from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurNonLivre
from edt.services.emploi_du_temps import raccords
from edt.verification.contrat import Anomalie

ERREUR = Anomalie(
    "SALLE_UNIQUE", Gravite.ERREUR, "La salle N204 est prise deux fois.", seances=("1-1",)
)
AVERTISSEMENT = Anomalie("PAUSE", Gravite.AVERTISSEMENT, "Pause courte.", seances=("2-1",))


def test_module_absent_donne_une_erreur_explicite_journalisee(session, caplog):
    caplog.set_level(logging.WARNING, logger="edt.services.emploi_du_temps.raccords")

    with pytest.raises(raccords.ErreurRaccordIndisponible) as erreur:
        raccords.verifier_version(session, 1)

    assert isinstance(erreur.value, ErreurNonLivre)
    assert erreur.value.message == raccords.MESSAGE_VERIFICATION
    assert erreur.value.fonction == "edt.services.verification.verifier_version"
    assert "edt.services.verification" in caplog.text


def test_fonction_absente_ou_non_livree(session, monkeypatch):
    # pre_verifier_semestre n'existe pas encore dans edt.services.generation.
    with pytest.raises(raccords.ErreurRaccordIndisponible, match="préalables"):
        raccords.pre_verifier_semestre(session, 1)

    module = types.ModuleType("edt.services.instanciation")

    def instancier_version(session, version_id):
        raise ErreurNonLivre("Instanciation non livrée.")

    module.instancier_version = instancier_version
    monkeypatch.setitem(sys.modules, "edt.services.instanciation", module)
    with pytest.raises(raccords.ErreurRaccordIndisponible) as erreur:
        raccords.instancier_version(session, 1)
    assert erreur.value.message == raccords.MESSAGE_INSTANCIATION


def test_rapport_lu_et_indicateurs_normalises(session, faux_verificateur):
    faux_verificateur.hebdo = [ERREUR, AVERTISSEMENT]

    verification = raccords.verifier_version(session, 7)

    assert faux_verificateur.appels == [("version", 7)]
    assert verification.anomalies == (ERREUR, AVERTISSEMENT)
    assert (verification.nb_erreurs, verification.nb_avertissements) == (1, 1)
    assert not verification.valide
    assert verification.par_code == {"SALLE_UNIQUE": 1, "PAUSE": 1}
    indicateurs = verification.indicateurs
    assert indicateurs.pourcentage_seances_placees == 100.0
    assert indicateurs.pourcentage_utilisation_salles == 37.5
    assert indicateurs.changements_campus == 8  # groupes + enseignants
    assert indicateurs.changements_campus_par_semaine == pytest.approx(0.4)


def test_indicateurs_incomplets_signales_sans_valeur_inventee(caplog):
    caplog.set_level(logging.WARNING, logger="edt.services.emploi_du_temps.raccords")

    indicateurs = raccords.lire_indicateurs({"pourcentage_seances_placees": 90})

    assert indicateurs.pourcentage_seances_placees == 90.0
    assert indicateurs.pourcentage_utilisation_salles is None
    assert indicateurs.changements_campus is None
    assert "incomplets" in caplog.text


def test_controler_ne_plante_jamais_mais_le_dit(session, caplog):
    indisponible = raccords.controler(raccords.verifier_version, session, 1)
    assert not indisponible.disponible
    assert indisponible.motif == raccords.MESSAGE_VERIFICATION
    assert indisponible.niveau == "attention"

    def en_panne(session, version_id):
        raise RuntimeError("détail technique")

    caplog.set_level(logging.ERROR)
    panne = raccords.controler(en_panne, session, 1)
    assert not panne.disponible and panne.niveau == "erreur"
    assert "détail technique" not in panne.motif
    assert "RuntimeError: détail technique" in caplog.text  # la trace est dans le journal


def test_un_module_livre_mais_casse_n_est_pas_maquille(session, monkeypatch):
    def importer(nom):
        raise ModuleNotFoundError(
            "No module named 'bibliotheque_absente'", name="bibliotheque_absente"
        )

    monkeypatch.setattr(raccords.importlib, "import_module", importer)
    with pytest.raises(ModuleNotFoundError):
        raccords.verifier_version(session, 1)
