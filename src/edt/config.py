"""Configuration de l'application, lue dans les variables d'environnement.

Toutes les variables ont une valeur par défaut : l'application démarre sans
rien régler. Aucun chemin absolu n'est écrit en dur ; les chemins relatifs
sont résolus par rapport au répertoire de lancement.

| Variable        | Défaut                 | Rôle                                    |
|-----------------|------------------------|-----------------------------------------|
| `EDT_DB_URL`    | `sqlite:///data/edt.db`| URL SQLAlchemy de la base               |
| `EDT_LOG_DIR`   | `logs`                 | dossier des journaux                    |
| `EDT_HOST`      | `127.0.0.1`            | adresse d'écoute du serveur web         |
| `EDT_PORT`      | `8000`                 | port d'écoute du serveur web            |
| `EDT_ENV`       | `dev`                  | `dev` ou `prod`                         |
| `EDT_LOG_LEVEL` | `INFO`                 | niveau de journalisation                |
| `EDT_MDP_GESTION` | (vide)               | mot de passe du gestionnaire ; vide = site ouvert, sans connexion |
| `EDT_MDP_CONSULTATION` | (vide)          | mot de passe de l'accès en consultation (facultatif) |

Les deux mots de passe sont des secrets : sur le serveur, ils vont dans
`/etc/edt/comptes.env` (voir docs/deploiement.md), jamais dans `edt.env` ni
dans le dépôt. Ils n'apparaissent ni dans `repr()` ni dans les journaux.

La configuration est relue à chaque appel de `charger_configuration()` : il
n'y a pas d'état global à invalider (utile pour les tests).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from edt.erreurs import ErreurConfiguration

ENVIRONNEMENTS = ("dev", "prod")
NIVEAUX_JOURNAL = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAUTS: dict[str, str] = {
    "EDT_DB_URL": "sqlite:///data/edt.db",
    "EDT_LOG_DIR": "logs",
    "EDT_HOST": "127.0.0.1",
    "EDT_PORT": "8000",
    "EDT_ENV": "dev",
    "EDT_LOG_LEVEL": "INFO",
    "EDT_MDP_GESTION": "",
    "EDT_MDP_CONSULTATION": "",
}

LONGUEUR_MIN_MOT_DE_PASSE = 8
"""Longueur minimale d'un mot de passe d'accès (`EDT_MDP_GESTION`, `EDT_MDP_CONSULTATION`)."""


@dataclass(frozen=True)
class Configuration:
    """Réglages de l'application pour le processus courant."""

    db_url: str
    log_dir: Path
    host: str
    port: int
    env: str
    log_level: str
    mdp_gestion: str = field(default="", repr=False)
    mdp_consultation: str = field(default="", repr=False)

    @property
    def comptes_actifs(self) -> bool:
        """Vrai si le site demande une connexion (`EDT_MDP_GESTION` renseigné)."""
        return bool(self.mdp_gestion)

    @property
    def est_prod(self) -> bool:
        """Vrai en production (`EDT_ENV=prod`)."""
        return self.env == "prod"

    @property
    def db_url_affichable(self) -> str:
        """URL de la base sans mot de passe éventuel, pour l'affichage et les journaux."""
        return url_affichable(self.db_url)


def url_affichable(url: object) -> str:
    """Rend une URL de base lisible et sans mot de passe (texte ou URL SQLAlchemy).

    Les caractères encodés sont rétablis : `sqlite:///C:/donnees/edt.db` et non
    `sqlite:///C%3A/donnees/edt.db`.
    """
    from sqlalchemy.engine import make_url

    return unquote(make_url(url).render_as_string(hide_password=True))


def charger_configuration(environ: Mapping[str, str] | None = None) -> Configuration:
    """Construit la configuration depuis l'environnement (ou le dictionnaire fourni).

    Une variable absente ou vide prend sa valeur par défaut documentée ; une
    valeur présente mais invalide lève `ErreurConfiguration` (jamais de repli
    silencieux sur le défaut).
    """
    source = os.environ if environ is None else environ

    def lire(nom: str) -> str:
        valeur = source.get(nom, "").strip()
        return valeur or DEFAUTS[nom]

    port_brut = lire("EDT_PORT")
    # `isdigit` accepte des chiffres exotiques (« ² ») que `int` refuse : chiffres ASCII seuls.
    if not (port_brut.isascii() and port_brut.isdigit()) or not 1 <= int(port_brut) <= 65535:
        raise ErreurConfiguration(
            f"EDT_PORT doit être un entier entre 1 et 65535 (valeur lue : {port_brut!r})."
        )

    hote = lire("EDT_HOST")
    if any(caractere.isspace() for caractere in hote) or "/" in hote:
        raise ErreurConfiguration(
            "EDT_HOST doit être une adresse IP ou un nom d'hôte, sans espace ni « / » "
            f"(valeur lue : {hote!r})."
        )

    env = lire("EDT_ENV").lower()
    if env not in ENVIRONNEMENTS:
        raise ErreurConfiguration(
            f"EDT_ENV doit valoir « dev » ou « prod » (valeur lue : {env!r})."
        )

    niveau = lire("EDT_LOG_LEVEL").upper()
    if niveau not in NIVEAUX_JOURNAL:
        raise ErreurConfiguration(
            "EDT_LOG_LEVEL doit valoir "
            + ", ".join(NIVEAUX_JOURNAL)
            + f" (valeur lue : {niveau!r})."
        )

    mdp_gestion = source.get("EDT_MDP_GESTION", "")
    mdp_consultation = source.get("EDT_MDP_CONSULTATION", "")
    _verifier_mots_de_passe(mdp_gestion, mdp_consultation)

    return Configuration(
        db_url=lire("EDT_DB_URL"),
        log_dir=Path(lire("EDT_LOG_DIR")),
        host=hote,
        port=int(port_brut),
        env=env,
        log_level=niveau,
        mdp_gestion=mdp_gestion,
        mdp_consultation=mdp_consultation,
    )


def _verifier_mots_de_passe(gestion: str, consultation: str) -> None:
    """Refuse une combinaison de mots de passe d'accès incohérente.

    Les messages nomment la variable, jamais sa valeur (c'est un secret).
    Un mot de passe n'est pas « nettoyé » : des espaces autour seraient
    retirés en silence, et le mot de passe tapé ne correspondrait plus.
    """
    for nom, valeur in (("EDT_MDP_GESTION", gestion), ("EDT_MDP_CONSULTATION", consultation)):
        if valeur and valeur != valeur.strip():
            raise ErreurConfiguration(
                f"{nom} commence ou finit par un espace : le retirer (la valeur n'est pas affichée)."
            )
        if valeur and len(valeur) < LONGUEUR_MIN_MOT_DE_PASSE:
            raise ErreurConfiguration(
                f"{nom} doit compter au moins {LONGUEUR_MIN_MOT_DE_PASSE} caractères "
                "(la valeur n'est pas affichée)."
            )
    if consultation and not gestion:
        raise ErreurConfiguration(
            "EDT_MDP_CONSULTATION est renseigné sans EDT_MDP_GESTION : sans mot de passe du "
            "gestionnaire, le site reste ouvert à tous. Renseigner les deux, ou aucun."
        )
    if gestion and gestion == consultation:
        raise ErreurConfiguration(
            "EDT_MDP_GESTION et EDT_MDP_CONSULTATION sont identiques : la consultation donnerait "
            "tous les droits. Choisir deux mots de passe différents."
        )
