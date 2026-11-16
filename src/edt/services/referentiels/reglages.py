"""Paramètres globaux saisis dans un formulaire : description affichable et enregistrement.

La lecture et l'écriture typées sont dans `edt.services.parametres` ; ce
module ajoute la conversion des saisies (heures « 08:30 » comprises) avec
une erreur par champ, pour l'écran de configuration ou une API.

Trois types de paramètre : entier, oui / non, et **à choix** (une valeur
parmi une liste fermée, `DefinitionParametre.type == "choix"`). Certains
paramètres ne servent que dans un mode : `ParametreAffiche.visible_si` le dit
(les bornes du déjeuner ne servent pas sans pause, la durée minimale
seulement en pause libre).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from edt.domaine.enumerations import JOURS
from edt.domaine.heures import minutes_vers_hhmm, minutes_vers_texte, texte_vers_minutes
from edt.domaine.parametres import (
    DEJEUNER_FIXE,
    DEJEUNER_LIBRE,
    TYPE_CHOIX,
    TYPE_JOURS,
    Choix,
    Parametres,
    ValeurParametre,
    convertir_texte,
    defauts_dejeuner,
    definitions,
)
from edt.erreurs import ErreurCalculEnCours, ErreurDonneesInvalides
from edt.services import generation
from edt.services.parametres import (
    anomalies_parametres,
    ecrire_parametres,
    lire_parametres,
    reinitialiser_parametres,
)
from edt.services.referentiels.operations import ErreurSaisie

journal = logging.getLogger(__name__)

UNITE_HEURE = "heure"

VISIBLE_SI: dict[str, tuple[str, tuple[str, ...]]] = {
    "dejeuner_debut_min": ("dejeuner_mode", (DEJEUNER_FIXE, DEJEUNER_LIBRE)),
    "dejeuner_fin_min": ("dejeuner_mode", (DEJEUNER_FIXE, DEJEUNER_LIBRE)),
    "dejeuner_duree_min": ("dejeuner_mode", (DEJEUNER_LIBRE,)),
}
"""Paramètres utiles dans certains modes seulement : (paramètre du mode, valeurs)."""


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
    choix: Choix = ()
    visible_si: tuple[str, tuple[str, ...]] | None = None

    @property
    def est_choix(self) -> bool:
        """Vrai pour un paramètre à choix (liste déroulante)."""
        return self.type == TYPE_CHOIX

    @property
    def est_jours(self) -> bool:
        """Vrai pour un ensemble de jours (sept cases à cocher)."""
        return self.type == TYPE_JOURS

    @property
    def options_jours(self) -> tuple[tuple[str, str], ...]:
        """Cases d'un ensemble de jours : (numéro, nom du jour)."""
        return tuple((str(numero), nom.capitalize()) for numero, nom in enumerate(JOURS))

    def jours_coches(self, saisie: object) -> list[str]:
        """Numéros cochés, depuis la valeur enregistrée ou la saisie réaffichée (« 0,1,4 »)."""
        if isinstance(saisie, str):
            return [morceau for morceau in saisie.split(",") if morceau.strip()]
        return [str(jour) for jour in (saisie or ())]

    @property
    def est_heure(self) -> bool:
        """Vrai pour une heure du jour (saisie « 08:30 »)."""
        return self.unite == UNITE_HEURE

    def en_texte(self, valeur: ValeurParametre) -> str:
        """Valeur telle qu'elle se saisit (« 08:30 » pour une heure)."""
        if self.est_heure and isinstance(valeur, int) and not isinstance(valeur, bool):
            return minutes_vers_hhmm(valeur)
        if self.est_jours and isinstance(valeur, tuple | list):
            return ",".join(str(jour) for jour in valeur)
        return str(valeur)

    def texte_lisible(self, valeur: ValeurParametre) -> str:
        """Valeur telle qu'elle se lit : « oui », le libellé d'un choix, « 8 h 30 »."""
        if self.type == "bool":
            return "oui" if valeur else "non"
        if self.est_choix:
            return dict(self.choix).get(str(valeur), str(valeur))
        if self.est_jours and isinstance(valeur, tuple | list):
            return ", ".join(JOURS[jour] for jour in valeur)
        if self.est_heure and isinstance(valeur, int) and not isinstance(valeur, bool):
            return minutes_vers_texte(valeur)
        return self.en_texte(valeur)


def parametres_affichables(session: Session) -> tuple[ParametreAffiche, ...]:
    """Tous les paramètres, dans l'ordre de déclaration, avec leur valeur courante.

    Lecture tolérante : une valeur enregistrée invalide (base abîmée) est
    remplacée par la valeur par défaut, pour que l'écran s'ouvre et serve à la
    réparer (voir `parametres_invalides`).
    """
    courants = lire_parametres(session, tolerant=True)
    return tuple(
        ParametreAffiche(
            cle=d.cle,
            libelle=d.libelle,
            description=d.description,
            type=d.type if isinstance(d.type, str) else d.type.__name__,
            unite=d.unite,
            minimum=d.minimum,
            maximum=d.maximum,
            valeur=getattr(courants, d.cle),
            defaut=d.defaut,
            choix=d.choix,
            visible_si=VISIBLE_SI.get(d.cle),
        )
        for d in definitions()
    )


def parametres_invalides(session: Session) -> dict[str, str]:
    """Paramètres dont la valeur enregistrée est invalide : clé → message pour l'écran.

    L'écran propose à leur place la valeur par défaut ; l'enregistrer corrige
    la base (voir `edt.services.parametres.anomalies_parametres`).
    """
    libelles = {d.cle: d.libelle for d in definitions()}
    messages = {}
    for cle, message in anomalies_parametres(session).items():
        detail = message.removeprefix(f"{libelles.get(cle, cle)} : ").rstrip(".")
        messages[cle] = (
            f"Valeur enregistrée invalide ({detail}) : la valeur par défaut est proposée, "
            "enregistrer pour corriger la base."
        )
    return messages


def convertir_saisie(cle: str, texte: str, unite: str) -> ValeurParametre:
    """Convertit la saisie d'un paramètre (une heure « 08:30 » devient 510 minutes)."""
    if unite == UNITE_HEURE and texte.strip():
        texte = str(texte_vers_minutes(texte))
    return convertir_texte(cle, texte)


def _refuser_pendant_un_calcul() -> None:
    """Lève `ErreurCalculEnCours` si un calcul tourne : les paramètres ne changent pas alors.

    Un calcul lit les paramètres à son lancement et les relit pour contrôler
    puis dater sa solution : les changer entre-temps donnerait une version
    vérifiée avec d'autres réglages que ceux du calcul (la pause déjeuner
    réglée depuis « Générer » est refusée de même).
    """
    suivi = generation.calcul_en_cours()
    if suivi is None:
        return
    journal.info("Paramètres non modifiés : calcul n° %d en cours", suivi.id)
    raise ErreurCalculEnCours(
        f"Un calcul d'emploi du temps est en cours (calcul n° {suivi.id}) : les paramètres ne "
        "changent pas pendant un calcul. Attendre qu'il se termine, ou l'arrêter, puis "
        "enregistrer de nouveau. Rien n'a été enregistré."
    )


def enregistrer_parametres_saisis(session: Session, saisies: Mapping[str, str]) -> Parametres:
    """Enregistre les paramètres saisis (textes) ; les absents gardent leur valeur.

    Lève `ErreurCalculEnCours` pendant un calcul, `ErreurSaisie` (une erreur
    par paramètre fautif : type, bornes, choix inconnu, réglages du déjeuner
    incohérents) avant toute écriture, puis `ErreurDonneesInvalides` si
    l'ensemble reste incohérent (ouverture après la fermeture...).
    """
    _refuser_pendant_un_calcul()
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
    if not erreurs:
        # Lecture tolérante : une valeur corrompue ailleurs n'empêche pas de réparer la base.
        erreurs = _defauts_du_dejeuner(lire_parametres(session, tolerant=True), valeurs)
    if erreurs:
        raise ErreurSaisie(erreurs, {cle: connues[cle].libelle for cle in erreurs})
    resultat = ecrire_parametres(session, valeurs)
    journal.info("Paramètres enregistrés depuis l'écran de configuration (%d)", len(valeurs))
    return resultat


def remettre_parametres_par_defaut(session: Session) -> Parametres:
    """Remet tous les paramètres à leur valeur par défaut ; `ErreurCalculEnCours` pendant un calcul."""
    _refuser_pendant_un_calcul()
    resultat = reinitialiser_parametres(session)
    journal.info("Paramètres remis à leur valeur par défaut depuis l'écran de configuration")
    return resultat


def _defauts_du_dejeuner(
    courants: Parametres, valeurs: Mapping[str, ValeurParametre]
) -> dict[str, str]:
    """Réglages du déjeuner incohérents une fois les saisies appliquées, par champ fautif.

    Les messages viennent de `edt.domaine.parametres.defauts_dejeuner`, la règle
    qui valide aussi `Parametres` : ils s'affichent sous le champ concerné.
    """

    def valeur(cle: str):
        return valeurs.get(cle, getattr(courants, cle))

    if valeur("ouverture_min") >= valeur("fermeture_min"):
        return {}  # incohérence d'ensemble, signalée par `Parametres` en tête du formulaire
    return defauts_dejeuner(
        valeur("dejeuner_debut_min"),
        valeur("dejeuner_fin_min"),
        valeur("ouverture_min"),
        valeur("fermeture_min"),
        mode=valeur("dejeuner_mode"),
        duree=valeur("dejeuner_duree_min"),
    )
