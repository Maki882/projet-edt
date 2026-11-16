"""Fonctions du module de commandes appelées directement : extension refusée, point d'entrée,
sorties UTF-8."""

from __future__ import annotations

import sys
import types

import pytest
from tests.outils.socle.extension_cli import NOM_MODULE_ESSAI

from edt import cli
from edt.erreurs import ErreurConfiguration


def test_module_de_commandes_sans_objet_commandes_refuse(monkeypatch):
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, types.ModuleType(NOM_MODULE_ESSAI))
    monkeypatch.setattr(cli, "modules_de_commandes", lambda: [NOM_MODULE_ESSAI])
    with pytest.raises(ErreurConfiguration, match="commandes"):
        cli.construire_app()


def test_point_d_entree_renvoie_le_code_de_sortie(base):
    assert cli.main(["version"]) == 0
    assert cli.main(["commande-inconnue"]) == 2


def test_les_sorties_sont_forcees_en_utf8(monkeypatch):
    """Sous Windows, une sortie redirigée doit rester lisible : les accents sortent en UTF-8."""
    import io
    import sys

    from edt.cli import _forcer_sorties_utf8

    tampon = io.BytesIO()
    flux = io.TextIOWrapper(tampon, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", flux)

    _forcer_sorties_utf8()
    print("séances placées", file=flux, flush=True)

    assert flux.encoding.lower().replace("-", "") == "utf8"
    assert tampon.getvalue().decode("utf-8").strip() == "séances placées"


@pytest.mark.cas_limite
def test_options_de_niveau_insensibles_a_la_casse_et_aux_espaces():
    """Cas : `generer -d pause --niveau " Pause = Facultatif "` (casse et espaces libres).

    Risque : refus surprenant alors que `--regle r007=Facultatif` tolère casse et espaces ;
    fragile pour les scripts.
    """
    from edt.commandes.generation import niveaux_choisis
    from edt.domaine.contraintes import CodeContrainte, Niveau

    assert CodeContrainte.PAUSE not in niveaux_choisis(["pause"], None)
    assert (
        niveaux_choisis(None, [" Pause = Facultatif "])[CodeContrainte.PAUSE] is Niveau.FACULTATIF
    )
