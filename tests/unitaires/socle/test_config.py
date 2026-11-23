"""Configuration : variables EDT_* invalides refusées, valeurs vides au défaut, doctor."""

from __future__ import annotations

import pytest

from edt.config import DEFAUTS, charger_configuration
from edt.erreurs import ErreurConfiguration


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("variable", "valeur"),
    [
        ("EDT_PORT", "²"),  # isdigit() vrai, int() impossible
        ("EDT_PORT", "0"),
        ("EDT_PORT", "65536"),
        ("EDT_PORT", "abc"),
        ("EDT_PORT", "-1"),
        ("EDT_PORT", "80 80"),
        ("EDT_HOST", "adresse invalide"),
        ("EDT_HOST", "hote/chemin"),
        ("EDT_LOG_LEVEL", "verbose"),
        ("EDT_ENV", "production"),
    ],
)
def test_variables_d_environnement_invalides_levent_une_erreur_de_configuration(variable, valeur):
    """Cas : une variable EDT_* porte une valeur hors domaine (port « ² », hôte avec espace,
    niveau de journal ou environnement inconnus).

    Risque : `ValueError` brute au démarrage (code 2, trace) ou repli silencieux sur le
    défaut ; le message doit nommer la variable et la valeur lue.
    """
    with pytest.raises(ErreurConfiguration) as capture:
        charger_configuration({**DEFAUTS, variable: valeur})
    message = str(capture.value)
    assert variable in message and valeur.strip().lower() in message.lower()


@pytest.mark.cas_limite
def test_valeurs_vides_ou_entourees_d_espaces():
    """Cas : variables présentes mais vides, ou avec espaces et casse libres.

    Risque : une variable vide refusée au lieu de prendre son défaut, ou « PROD » non reconnu.
    """
    config = charger_configuration(
        {"EDT_PORT": " ", "EDT_HOST": "", "EDT_ENV": " PROD ", "EDT_LOG_LEVEL": " debug "}
    )
    assert (config.port, config.host, config.env, config.log_level) == (
        8000,
        "127.0.0.1",
        "prod",
        "DEBUG",
    )
    assert config.est_prod and config.db_url == DEFAUTS["EDT_DB_URL"]


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("gestion", "consultation", "variable"),
    [
        ("", "lecture-seule", "EDT_MDP_CONSULTATION"),
        ("court", "", "EDT_MDP_GESTION"),
        ("gestion-secrete", "court", "EDT_MDP_CONSULTATION"),
        (" gestion-secrete", "", "EDT_MDP_GESTION"),
        ("meme-mot-de-passe", "meme-mot-de-passe", "EDT_MDP_GESTION"),
    ],
)
def test_mots_de_passe_d_acces_incoherents_refuses_sans_afficher_leur_valeur(
    gestion, consultation, variable
):
    """Cas : mot de passe de consultation sans gestionnaire, trop court, entouré d'espaces, ou
    identique pour les deux rôles.
    Risque : site resté ouvert alors qu'on le croit protégé, consultation qui donne tous les
    droits, ou mot de passe recopié dans le message d'erreur et donc dans le journal.
    """
    with pytest.raises(ErreurConfiguration) as capture:
        charger_configuration(
            {**DEFAUTS, "EDT_MDP_GESTION": gestion, "EDT_MDP_CONSULTATION": consultation}
        )
    message = str(capture.value)
    assert variable in message
    for secret in (gestion.strip(), consultation.strip()):
        if secret:
            assert secret not in message


def test_sans_mot_de_passe_le_site_reste_ouvert_et_les_secrets_ne_s_affichent_pas():
    ouvert = charger_configuration(DEFAUTS)
    protege = charger_configuration(
        {**DEFAUTS, "EDT_MDP_GESTION": "gestion-secrete", "EDT_MDP_CONSULTATION": "lecture-seule"}
    )

    assert not ouvert.comptes_actifs
    assert protege.comptes_actifs
    assert "gestion-secrete" not in repr(protege) and "lecture-seule" not in repr(protege)
