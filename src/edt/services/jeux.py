"""Chargement d'un établissement fictif et remise à zéro de la base.

Fonctions appelées par l'écran de configuration et par la commande
`python -m edt jeu`. Le contenu des jeux est décrit dans
`docs/donnees-fictives.md` ; la génération elle-même est dans
`edt.synthese.generateur`.

- `tailles_disponibles()` : les tailles proposées, avec leur description ;
- `charger_jeu_fictif(session, taille, ...)` : charge un jeu dans la base de
  la session, qui doit être initialisée et vide de référentiels ; ne valide
  pas la transaction (convention des services) ;
- `remettre_a_zero(...)` : supprime toutes les données et recrée la base
  avec ses données par défaut ;
- `recharger_jeu_fictif(taille, ...)` : remise à zéro facultative puis
  chargement, dans ses propres sessions. C'est la fonction à appeler depuis
  une route web : une remise à zéro supprime les tables et ne doit pas se
  faire pendant qu'une session de requête est ouverte sur la base.

Toutes refusent d'agir pendant un calcul (`ErreurCalculEnCours`) : un calcul
lit la base pendant plusieurs secondes et ne doit pas la voir changer.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from edt.db.initialisation import RapportInitialisation, base_initialisee, initialiser_base
from edt.db.moteur import obtenir_moteur, session_base
from edt.erreurs import ErreurCalculEnCours, ErreurRegleMetier
from edt.services.generation import calcul_en_cours
from edt.synthese.configuration import CONFIGURATIONS, ConfigurationJeu, configuration_nommee
from edt.synthese.generateur import generer_jeu
from edt.synthese.rapport import RapportJeu

journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class TailleJeu:
    """Une taille de jeu proposée à l'utilisateur."""

    nom: str
    description: str
    nb_promotions: int
    nb_classes: int
    nb_salles: int
    semestres: tuple[str, ...]


_DESCRIPTIONS = {
    "petit": "Une promotion de 1re année, deux classes, le semestre 1 (essais rapides).",
    "moyen": "Une promotion complète de 3e année avec apprentis, langues, partiels et stage.",
    "grand": "L'établissement complet : cinq années, trois sites, les deux semestres.",
}


def tailles_disponibles() -> tuple[TailleJeu, ...]:
    """Les tailles de jeu fictif, de la plus petite à la plus grande."""
    return tuple(
        TailleJeu(
            nom=nom,
            description=_DESCRIPTIONS.get(nom, ""),
            nb_promotions=len(config.promotions),
            nb_classes=sum(p.nb_classes + p.nb_classes_apprentis for p in config.promotions),
            nb_salles=sum(salles.total for _, salles in config.salles),
            semestres=config.semestres,
        )
        for nom, config in CONFIGURATIONS.items()
    )


def charger_jeu_fictif(
    session: Session,
    taille: str | ConfigurationJeu = "moyen",
    graine: int = 0,
    *,
    annee_debut: int | None = None,
    enregistrer_reference: bool = False,
) -> RapportJeu:
    """Charge un établissement fictif dans la base de `session` et renvoie le rapport.

    La base doit être initialisée et vide de référentiels (`ErreurRegleMetier`
    sinon) ; une taille inconnue lève `ErreurDonneesInvalides`. Refusé
    pendant un calcul. Ne valide pas la transaction.
    """
    _exiger_aucun_calcul("charger un jeu fictif")
    configuration = taille if isinstance(taille, ConfigurationJeu) else configuration_nommee(taille)
    journal.info("Chargement du jeu fictif « %s » (graine %d) demandé", configuration.nom, graine)
    return generer_jeu(
        session,
        configuration,
        graine,
        annee_debut=annee_debut,
        enregistrer_reference=enregistrer_reference,
    )


def remettre_a_zero(
    *, aujourdhui: dt.date | None = None, url: str | None = None
) -> RapportInitialisation:
    """Supprime toutes les données puis recrée la base et ses données par défaut.

    `aujourdhui` choisit l'année scolaire recréée (défaut : la date du jour) ;
    `url` désigne la base (défaut : `EDT_DB_URL`). Refusé pendant un calcul.
    """
    _exiger_aucun_calcul("remettre la base à zéro")
    journal.warning("Remise à zéro de la base demandée : toutes les données seront supprimées")
    return initialiser_base(reset=True, aujourdhui=aujourdhui, url=url)


def recharger_jeu_fictif(
    taille: str | ConfigurationJeu = "moyen",
    graine: int = 0,
    *,
    reinitialiser: bool = False,
    aujourdhui: dt.date | None = None,
    annee_debut: int | None = None,
    enregistrer_reference: bool = False,
    url: str | None = None,
) -> RapportJeu:
    """Remet la base à zéro si demandé, puis y charge un jeu fictif (sessions propres).

    Sans `reinitialiser`, la base doit déjà être initialisée et vide de
    référentiels. Le jeu est validé en base si la génération réussit, rien
    n'est écrit sinon. Refusé pendant un calcul.
    """
    configuration = taille if isinstance(taille, ConfigurationJeu) else configuration_nommee(taille)
    if reinitialiser:
        remettre_a_zero(aujourdhui=aujourdhui, url=url)
    elif not base_initialisee(obtenir_moteur(url)):
        raise ErreurRegleMetier(
            "La base n'est pas initialisée : lancer d'abord « python -m edt init-db », "
            "ou demander la remise à zéro."
        )
    with session_base(url) as session:
        return charger_jeu_fictif(
            session,
            configuration,
            graine,
            annee_debut=annee_debut,
            enregistrer_reference=enregistrer_reference,
        )


def _exiger_aucun_calcul(action: str) -> None:
    suivi = calcul_en_cours()
    if suivi is not None:
        raise ErreurCalculEnCours(
            f"Impossible de {action} : un calcul d'emploi du temps est en cours "
            f"(calcul n°{suivi.id}). Attendre sa fin ou l'arrêter."
        )
