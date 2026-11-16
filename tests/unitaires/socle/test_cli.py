"""Ligne de commande : fonctions du module de commandes appelées directement (extension refusée, point d'entrée, sorties UTF-8, niveaux)."""

from __future__ import annotations

import sys
import types

import pytest
from tests.outils.socle.extension_cli import NOM_MODULE_ESSAI

from edt import cli
from edt.commandes import base as module_base
from edt.erreurs import ErreurConfiguration


def test_module_de_commandes_sans_objet_commandes_refuse(monkeypatch):
    monkeypatch.setitem(sys.modules, NOM_MODULE_ESSAI, types.ModuleType(NOM_MODULE_ESSAI))
    monkeypatch.setattr(cli, "modules_de_commandes", lambda: [NOM_MODULE_ESSAI])
    with pytest.raises(ErreurConfiguration, match="commandes"):
        cli.construire_app()


@pytest.mark.cas_limite
def test_une_ancienne_sauvegarde_indelebile_n_empeche_pas_la_rotation(
    tmp_path, monkeypatch, caplog
):
    """Cas : la rotation des sauvegardes rencontre un fichier ancien tenu par un autre
    programme (antivirus, explorateur de fichiers sous Windows).

    Risque : `sauvegarder` sortirait en « Erreur inattendue » (code 2) alors que la
    sauvegarde du jour est faite et vérifiée ; le fichier tenu est signalé, les autres
    anciens sont supprimés.
    """
    noms = [f"quotidienne-2026110{jour}T000000Z.db" for jour in range(1, 5)]
    for nom in noms:
        (tmp_path / nom).write_text("sauvegarde", encoding="utf-8")
    tenu = tmp_path / noms[0]
    suppression = type(tenu).unlink

    def unlink_refuse(chemin, *arguments, **options):
        if chemin == tenu:
            raise PermissionError("fichier tenu par un autre programme")
        return suppression(chemin, *arguments, **options)

    monkeypatch.setattr(type(tenu), "unlink", unlink_refuse)
    supprimees = module_base._faire_tourner(tmp_path, "quotidienne", garder=2)

    assert supprimees == [noms[1]]
    assert sorted(f.name for f in tmp_path.glob("*.db")) == [noms[0], *noms[2:]]
    assert "non supprimée" in caplog.text and noms[0] in caplog.text


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
    """Cas : `generer -d pause` et `generer --niveau " Pause = Facultatif "` (casse et espaces
    libres), puis les deux options sur la même contrainte.

    Risque : refus surprenant alors que `--regle r007=Facultatif` tolère casse et espaces ;
    fragile pour les scripts. Une contrainte à la fois ignorée et dotée d'un niveau est
    contradictoire : refus qui le dit.
    """
    from edt.commandes.generation import niveaux_choisis
    from edt.domaine.contraintes import CodeContrainte, Niveau
    from edt.erreurs import ErreurDonneesInvalides

    assert CodeContrainte.PAUSE not in niveaux_choisis(["pause"], None)
    assert (
        niveaux_choisis(None, [" Pause = Facultatif "])[CodeContrainte.PAUSE] is Niveau.FACULTATIF
    )
    with pytest.raises(ErreurDonneesInvalides, match="à la fois"):
        niveaux_choisis(["pause"], ["PAUSE=facultatif"])
