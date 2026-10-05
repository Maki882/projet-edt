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

La configuration est relue à chaque appel de `charger_configuration()` : il
n'y a pas d'état global à invalider (utile pour les tests).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

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
}


@dataclass(frozen=True)
class Configuration:
    """Réglages de l'application pour le processus courant."""

    db_url: str
    log_dir: Path
    host: str
    port: int
    env: str
    log_level: str

    @property
    def est_prod(self) -> bool:
        """Vrai en production (`EDT_ENV=prod`)."""
        return self.env == "prod"

    @property
    def db_url_affichable(self) -> str:
        """URL de la base sans mot de passe éventuel, pour l'affichage et les journaux."""
        from sqlalchemy.engine import make_url

        return make_url(self.db_url).render_as_string(hide_password=True)


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
    if not port_brut.isdigit() or not 1 <= int(port_brut) <= 65535:
        raise ErreurConfiguration(
            f"EDT_PORT doit être un entier entre 1 et 65535 (valeur lue : {port_brut!r})."
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

    return Configuration(
        db_url=lire("EDT_DB_URL"),
        log_dir=Path(lire("EDT_LOG_DIR")),
        host=lire("EDT_HOST"),
        port=int(port_brut),
        env=env,
        log_level=niveau,
    )
