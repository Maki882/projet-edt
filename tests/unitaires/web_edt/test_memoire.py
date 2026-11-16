"""Mémoire des écrans : un même calcul demandé par deux fils n'est fait qu'une fois."""

from __future__ import annotations

import threading

import pytest
from tests.outils.web_edt.aides import DELAI

from edt.db.moteur import session_base
from edt.erreurs import ErreurEDT
from edt.services.emploi_du_temps import memoire


class _DeuxDemandeurs:
    """Deux fils qui demandent la même clé ; le premier calcule, le second arrive pendant."""

    def __init__(self, monkeypatch) -> None:
        memoire.oublier()
        self.commence = threading.Event()
        self.libere = threading.Event()
        self.second_attend = threading.Event()
        self.resultats: list[object] = []
        self.erreurs: list[BaseException] = []
        original = memoire._attendre

        def attendre(evenement):
            self.second_attend.set()
            return original(evenement)

        monkeypatch.setattr(memoire, "_attendre", attendre)

    def demander(self, calcul) -> threading.Thread:
        def corps():
            try:
                with session_base() as session:
                    self.resultats.append(memoire.memoriser(session, "essai", (1,), calcul))
            except ErreurEDT as erreur:
                self.erreurs.append(erreur)

        fil = threading.Thread(target=corps)
        fil.start()
        return fil

    def bloquant(self, appels: list[str], issue):
        """Calcul qui se signale, attend `libere`, puis rend ou lève `issue`."""

        def calcul():
            appels.append("premier")
            self.commence.set()
            assert self.libere.wait(DELAI)
            if isinstance(issue, BaseException):
                raise issue
            return issue

        return calcul


def test_meme_calcul_demande_par_deux_fils_n_est_fait_qu_une_fois(base, monkeypatch):
    essai = _DeuxDemandeurs(monkeypatch)
    appels: list[str] = []
    premier = essai.demander(essai.bloquant(appels, "résultat"))
    assert essai.commence.wait(DELAI)
    second = essai.demander(lambda: appels.append("second") or "autre résultat")
    assert essai.second_attend.wait(DELAI)  # il attend au lieu de recalculer en même temps
    essai.libere.set()
    premier.join(DELAI)
    second.join(DELAI)

    assert appels == ["premier"]
    assert essai.resultats == ["résultat", "résultat"]


@pytest.mark.cas_limite
def test_echec_du_premier_calcul_ne_bloque_pas_le_second(base, monkeypatch):
    """Cas : le premier fil échoue pendant que le second attend son résultat.

    Risque : le second attend pour toujours (page qui ne répond plus), ou reçoit l'erreur
    d'un autre au lieu de calculer lui-même.
    """
    essai = _DeuxDemandeurs(monkeypatch)
    appels: list[str] = []
    premier = essai.demander(essai.bloquant(appels, ErreurEDT("Vérification impossible.")))
    assert essai.commence.wait(DELAI)
    second = essai.demander(lambda: appels.append("second") or "résultat du second")
    assert essai.second_attend.wait(DELAI)
    essai.libere.set()
    premier.join(DELAI)
    second.join(DELAI)

    assert not premier.is_alive() and not second.is_alive()
    assert appels == ["premier", "second"]
    assert [e.message for e in essai.erreurs] == ["Vérification impossible."]
    assert essai.resultats == ["résultat du second"]
    with session_base() as session:  # mémorisé : plus rien n'est en cours
        assert memoire.memoriser(session, "essai", (1,), lambda: "jamais") == "résultat du second"
    assert memoire._en_cours == {}
