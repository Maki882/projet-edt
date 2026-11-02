"""Niveaux des contraintes dans le vérificateur pur (`niveaux=` de `verifier` et `verifier_occurrences`)."""

from __future__ import annotations

import datetime as dt

import pytest

from edt.domaine.contraintes import CodeContrainte, Niveau
from edt.domaine.enumerations import Gravite
from edt.erreurs import ErreurDonneesInvalides
from edt.verification import PREFIXES_ENTORSE, calculer_indicateurs, verifier, verifier_occurrences
from edt.verification.niveaux import prefixer

from .fabrique import MARDI, Fabrique, h, occurrence, placer

PAUSE, SALLE = CodeContrainte.PAUSE, CodeContrainte.SALLE_UNIQUE


def pause_trop_courte():
    """Mme Martin enchaîne deux séances le mardi avec 10 min de pause seulement."""
    f = Fabrique()
    n204, n205 = f.salle("N204"), f.salle("N205")
    martin = f.enseignant("Mme Martin")
    g1, g2 = f.groupe("A1-G1"), f.groupe("A1-G2")
    a = f.cours("Mathématiques TD", [g1], [martin])
    b = f.cours("Physique TD", [g2], [martin])
    return f, [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "10h40", n205)], (a, b, n204, n205)


@pytest.mark.parametrize(
    ("niveau", "gravite"),
    [
        (Niveau.NECESSAIRE, Gravite.ERREUR),
        (Niveau.IMPORTANT, Gravite.AVERTISSEMENT),
        (Niveau.FACULTATIF, Gravite.INFORMATION),
    ],
)
def test_la_gravite_suit_le_niveau(niveau, gravite):
    f, placements, _ = pause_trop_courte()
    (anomalie,) = verifier(f.construire(), placements, niveaux={PAUSE: niveau})
    assert anomalie.code is PAUSE
    assert anomalie.gravite is gravite
    assert anomalie.niveau is niveau
    texte = "Mme Martin n'a que 10 min de pause le mardi"
    if niveau is Niveau.NECESSAIRE:
        assert anomalie.message.startswith(texte)
    else:
        assert anomalie.message == PREFIXES_ENTORSE[niveau] + anomalie.message.removeprefix(
            PREFIXES_ENTORSE[niveau]
        )
        assert anomalie.message.startswith(PREFIXES_ENTORSE[niveau] + texte)


def test_prefixes_stables():
    assert PREFIXES_ENTORSE == {
        Niveau.IMPORTANT: "Entorse à une règle importante : ",
        Niveau.FACULTATIF: "Entorse à une règle facultative : ",
    }
    assert prefixer("La salle N204 accueille…", Niveau.FACULTATIF) == (
        "Entorse à une règle facultative : la salle N204 accueille…"
    )
    assert prefixer("Mme Martin donne…", Niveau.IMPORTANT) == (
        "Entorse à une règle importante : Mme Martin donne…"
    )
    assert prefixer("La salle N204 accueille…", Niveau.NECESSAIRE) == "La salle N204 accueille…"


def test_cle_absente_contrainte_ignoree():
    f, placements, _ = pause_trop_courte()
    instantane = f.construire()
    assert verifier(instantane, placements, niveaux={SALLE: Niveau.NECESSAIRE}) == []
    assert verifier(instantane, placements, niveaux={}) == []


def test_contraintes_actives_ignorees_quand_les_niveaux_sont_donnes():
    f, placements, _ = pause_trop_courte()
    anomalies = verifier(f.construire(), placements, ["SALLE_UNIQUE"], niveaux={PAUSE: "important"})
    assert [(a.code, a.gravite) for a in anomalies] == [(PAUSE, Gravite.AVERTISSEMENT)]


def test_sans_niveaux_rien_ne_change():
    f, placements, _ = pause_trop_courte()
    (anomalie,) = verifier(f.construire(), placements, ["PAUSE"])
    assert anomalie.gravite is Gravite.ERREUR
    assert anomalie.niveau is None
    assert not anomalie.message.startswith("Entorse")


def test_la_gravite_de_la_regle_est_un_plafond():
    # Séance imposée à midi : DEJEUNER donne une information, même nécessaire.
    f = Fabrique()
    n204 = f.salle("N204")
    dst = f.cours("DST", [f.groupe("A1")], debut_impose=h("12h00"))
    sans_salle = f.cours("TD", [f.groupe("B1")])
    placements = [placer(dst, MARDI, "12h00", n204), placer(sans_salle, MARDI, "8h30")]
    niveaux = {CodeContrainte.DEJEUNER: Niveau.NECESSAIRE, CodeContrainte.VOLUME: Niveau.NECESSAIRE}
    anomalies = verifier(f.construire(), placements, niveaux=niveaux)
    assert [(a.code, a.gravite, a.niveau) for a in anomalies] == [
        # « Salle à définir » reste un avertissement, VOLUME a beau être nécessaire.
        (CodeContrainte.VOLUME, Gravite.AVERTISSEMENT, Niveau.NECESSAIRE),
        (CodeContrainte.DEJEUNER, Gravite.INFORMATION, Niveau.NECESSAIRE),
    ]
    anomalies = verifier(
        f.construire(), placements, niveaux={CodeContrainte.DEJEUNER: Niveau.IMPORTANT}
    )
    assert [(a.gravite, a.niveau) for a in anomalies] == [(Gravite.INFORMATION, Niveau.IMPORTANT)]


def test_niveaux_invalides_refuses():
    f, placements, _ = pause_trop_courte()
    instantane = f.construire()
    with pytest.raises(ErreurDonneesInvalides, match="définit le problème"):
        verifier(instantane, placements, niveaux={CodeContrainte.VOLUME: Niveau.FACULTATIF})
    with pytest.raises(ErreurDonneesInvalides):
        verifier(instantane, placements, niveaux={"PAS_UN_CODE": "important"})
    with pytest.raises(ErreurDonneesInvalides):
        verifier(instantane, placements, niveaux={"PAUSE": "indispensable"})


def test_couche_datee():
    f, _, (a, b, n204, n205) = pause_trop_courte()
    mardi = dt.date(2026, 10, 6)
    occurrences = [occurrence(a, mardi, "8h30", n204), occurrence(b, mardi, "10h40", n205)]
    (anomalie,) = verifier_occurrences(
        f.construire(), occurrences, niveaux={PAUSE: Niveau.FACULTATIF}
    )
    assert (anomalie.gravite, anomalie.niveau) == (Gravite.INFORMATION, Niveau.FACULTATIF)
    assert anomalie.message.startswith("Entorse à une règle facultative : Mme Martin")


def test_indicateurs_ne_comptent_que_les_erreurs():
    f, placements, _ = pause_trop_courte()
    instantane = f.construire()
    dure = calculer_indicateurs(instantane, placements, niveaux={PAUSE: Niveau.NECESSAIRE})
    assert dure.anomalies_bloquantes_par_code == ((PAUSE, 1),)
    souple = calculer_indicateurs(instantane, placements, niveaux={PAUSE: Niveau.IMPORTANT})
    assert souple.nb_anomalies_bloquantes == 0


# --------------------------------------------------------------------------- #
# Cas limites (banque de tests : `pytest -m cas_limite`)
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
def test_prefixe_garde_la_majuscule_d_un_nom_propre():
    """Cas : l'enseignant « Le Gall » dépasse un maximum à un niveau important, ou enfreint une règle personnalisée.

    Risque : « Entorse à une règle importante : le Gall dépasse… » : nom propre déformé dans
    l'interface, la commande et le JSON.
    """
    f = Fabrique()
    n204 = f.salle("N204")
    le_gall = f.enseignant("Le Gall", max_jour=60)
    a = f.cours("TD 1", [f.groupe("A1-G1")], [le_gall])
    b = f.cours("TD 2", [f.groupe("A1-G2")], [le_gall])
    placements = [placer(a, MARDI, "8h30", n204), placer(b, MARDI, "10h45", n204)]
    (anomalie,) = verifier(
        f.construire(), placements, niveaux={CodeContrainte.MAX_ENSEIGNANT: Niveau.IMPORTANT}
    )
    assert anomalie.message.startswith("Entorse à une règle importante : Le Gall dépasse")
    # Dans le message d'une règle personnalisée aussi, après « Règle R001 (...) : ».
    f.regle("MAX_PAR_JOUR", "enseignant", le_gall, unite="seances", maximum=1)
    (regle,) = verifier(f.construire(), placements, ["REGLE"])
    assert regle.regle_code == "R001"
    assert ") : Le Gall a plus de 1 séance le mardi" in regle.message
    # Les débuts de phrase du vérificateur (noms communs) passent bien en minuscule.
    assert prefixer("La salle N204 accueille…", Niveau.IMPORTANT).startswith(
        "Entorse à une règle importante : la salle N204"
    )
    assert prefixer("Les groupes A1 et B1…", Niveau.FACULTATIF).startswith(
        "Entorse à une règle facultative : les groupes"
    )
    assert prefixer("La Rochelle…", Niveau.IMPORTANT).endswith(": La Rochelle…")
