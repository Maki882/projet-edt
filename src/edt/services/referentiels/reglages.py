"""Paramètres globaux saisis dans un formulaire : description affichable et enregistrement.

La lecture et l'écriture typées sont dans `edt.services.parametres` ; ce
module ajoute la conversion des saisies (heures « 08:30 » comprises) avec
une erreur par champ, pour l'écran de configuration ou une API.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from edt.domaine.heures import minutes_vers_hhmm, texte_vers_minutes
from edt.domaine.parametres import Parametres, ValeurParametre, convertir_texte, definitions
from edt.erreurs import ErreurDonneesInvalides
from edt.services.parametres import ecrire_parametres, lire_parametres
from edt.services.referentiels.operations import ErreurSaisie

journal = logging.getLogger(__name__)

UNITE_HEURE = "heure"


@dataclass(frozen=True)
class ParametreAffiche:
    """Un paramètre prêt à afficher : définition, valeur courante et valeur par défaut."""

    cle: str
    libelle: str
    description: str
    type: str
    unite: str
    minimum: int | None
    maximum: int | None
    valeur: ValeurParametre
    defaut: ValeurParametre

    @property
    def est_heure(self) -> bool:
        """Vrai pour une heure du jour (saisie « 08:30 »)."""
        return self.unite == UNITE_HEURE

    def en_texte(self, valeur: ValeurParametre) -> str:
        """Valeur telle qu'on la saisit (« 08:30 » pour une heure)."""
        if self.est_heure and isinstance(valeur, int) and not isinstance(valeur, bool):
            return minutes_vers_hhmm(valeur)
        return str(valeur)


def parametres_affichables(session: Session) -> tuple[ParametreAffiche, ...]:
    """Tous les paramètres, dans l'ordre de déclaration, avec leur valeur courante."""
    courants = lire_parametres(session)
    return tuple(
        ParametreAffiche(
            cle=d.cle,
            libelle=d.libelle,
            description=d.description,
            type=d.type.__name__,
            unite=d.unite,
            minimum=d.minimum,
            maximum=d.maximum,
            valeur=getattr(courants, d.cle),
            defaut=d.defaut,
        )
        for d in definitions()
    )


def convertir_saisie(cle: str, texte: str, unite: str) -> ValeurParametre:
    """Convertit la saisie d'un paramètre (une heure « 08:30 » devient 510 minutes)."""
    if unite == UNITE_HEURE and texte.strip():
        texte = str(texte_vers_minutes(texte))
    return convertir_texte(cle, texte)


def enregistrer_parametres_saisis(session: Session, saisies: Mapping[str, str]) -> Parametres:
    """Enregistre les paramètres saisis (textes) ; les absents gardent leur valeur.

    Lève `ErreurSaisie` (une erreur par paramètre fautif : type, bornes) avant
    toute écriture, puis `ErreurDonneesInvalides` si l'ensemble est incohérent
    (ouverture après la fermeture...).
    """
    connues = {d.cle: d for d in definitions()}
    inconnues = sorted(set(saisies) - set(connues))
    if inconnues:
        raise ErreurDonneesInvalides(f"Paramètre(s) inconnu(s) : {', '.join(inconnues)}.")
    valeurs: dict[str, ValeurParametre] = {}
    erreurs: dict[str, str] = {}
    for cle, texte in saisies.items():
        definition = connues[cle]
        try:
            valeurs[cle] = convertir_saisie(cle, texte, definition.unite)
        except ErreurDonneesInvalides as erreur:
            message = erreur.message.removeprefix(f"{definition.libelle} : ")
            erreurs[cle] = message[:1].upper() + message[1:]
    if erreurs:
        raise ErreurSaisie(erreurs, {cle: connues[cle].libelle for cle in erreurs})
    resultat = ecrire_parametres(session, valeurs)
    journal.info("Paramètres enregistrés depuis l'écran de configuration (%d)", len(valeurs))
    return resultat
