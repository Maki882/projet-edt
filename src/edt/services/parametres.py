"""Lecture et écriture des paramètres globaux (table `parametre`).

Les paramètres, leurs types, leurs bornes et leurs valeurs par défaut sont
définis une seule fois dans `edt.domaine.parametres.Parametres`.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db.modeles import Parametre
from edt.domaine.parametres import (
    Parametres,
    ValeurParametre,
    borner,
    defauts_dejeuner,
    definition,
    definitions,
    valider,
)
from edt.erreurs import ErreurDonneesInvalides

journal = logging.getLogger(__name__)


def lire_parametres(session: Session) -> Parametres:
    """Tous les paramètres, typés.

    Un paramètre absent de la table (base créée avant son ajout) prend sa
    valeur par défaut documentée, et le fait est journalisé. Un nombre
    enregistré hors des bornes actuelles (bornes resserrées depuis son
    enregistrement) est ramené à la borne la plus proche, avec un
    avertissement au journal : la valeur en base n'est pas modifiée. Une
    pause déjeuner « à horaires déterminés » dont la plage enregistrée est
    incohérente (voir `_dejeuner_lisible`) est lue comme « pas d'horaires
    déterminés », avec un avertissement. Toute autre valeur enregistrée
    invalide lève `ErreurDonneesInvalides`.
    """
    enregistres = dict(session.execute(select(Parametre.cle, Parametre.valeur)).all())
    valeurs: dict[str, ValeurParametre] = {}
    for entree in definitions():
        if entree.cle not in enregistres:
            journal.info(
                "Paramètre « %s » absent de la base : valeur par défaut %r utilisée "
                "(relancer « init-db » pour l'enregistrer).",
                entree.cle,
                entree.defaut,
            )
            continue
        enregistree = enregistres[entree.cle]
        retenue = borner(entree.cle, enregistree)
        if retenue != enregistree:
            journal.warning(
                "Paramètre « %s » : la valeur enregistrée %r sort des bornes actuelles "
                "(%s à %s) ; %r est utilisée à sa place. L'enregistrer de nouveau dans "
                "l'écran des paramètres fait disparaître cet avertissement.",
                entree.cle,
                enregistree,
                entree.minimum,
                entree.maximum,
                retenue,
            )
        try:
            valeurs[entree.cle] = valider(entree.cle, retenue)
        except ErreurDonneesInvalides as erreur:
            raise ErreurDonneesInvalides(
                f"Le paramètre « {entree.cle} » enregistré en base est invalide : {erreur.message}"
            ) from erreur
    valeurs.update(_dejeuner_lisible(valeurs))
    return Parametres(**valeurs)


def _dejeuner_lisible(valeurs: dict[str, ValeurParametre]) -> dict[str, ValeurParametre]:
    """Correction de lecture d'une pause déjeuner enregistrée incohérente, ou `{}`.

    Avant le paramètre `dejeuner_horaires_determines`, une plage vide
    (début = fin) voulait dire « aucune plage réservée » ; la validation
    exige désormais une plage non vide et dans les heures d'ouverture. Une
    telle base reste lisible : la pause est lue comme « pas d'horaires
    déterminés » (même effet qu'avant pour une plage vide) et un
    avertissement dit quoi régler. La valeur en base n'est pas modifiée.
    """
    lues = {entree.cle: entree.defaut for entree in definitions()} | valeurs
    if not lues["dejeuner_horaires_determines"]:
        return {}
    defauts = defauts_dejeuner(
        lues["dejeuner_debut_min"],
        lues["dejeuner_fin_min"],
        lues["ouverture_min"],
        lues["fermeture_min"],
    )
    if not defauts:
        return {}
    journal.warning(
        "Pause déjeuner enregistrée incohérente (%s) : elle est lue comme « pas d'horaires "
        "déterminés ». L'enregistrer de nouveau dans l'écran des paramètres fait disparaître "
        "cet avertissement.",
        " ".join(defauts.values()),
    )
    return {"dejeuner_horaires_determines": False}


def lire_parametre(session: Session, cle: str) -> ValeurParametre:
    """Valeur d'un paramètre ; lève `ErreurIntrouvable` si la clé n'existe pas."""
    definition(cle)
    return getattr(lire_parametres(session), cle)


def ecrire_parametre(session: Session, cle: str, valeur: ValeurParametre) -> None:
    """Enregistre un paramètre après validation (type, bornes, cohérence d'ensemble).

    Ne valide pas la transaction : l'appelant appelle `session.commit()`.
    """
    ecrire_parametres(session, {cle: valeur})


def ecrire_parametres(session: Session, valeurs: dict[str, ValeurParametre]) -> Parametres:
    """Enregistre plusieurs paramètres d'un coup et renvoie l'ensemble résultant.

    Tout est validé avant la première écriture : en cas d'erreur, rien n'est modifié.
    """
    nouvelles = {cle: valider(cle, valeur) for cle, valeur in valeurs.items()}
    resultat = lire_parametres(session).avec(**nouvelles)
    for cle, valeur in nouvelles.items():
        ligne = session.get(Parametre, cle)
        if ligne is None:
            session.add(Parametre(cle=cle, valeur=valeur))
        else:
            ligne.valeur = valeur
        journal.info("Paramètre « %s » réglé sur %r", cle, valeur)
    session.flush()
    return resultat


def reinitialiser_parametres(session: Session) -> Parametres:
    """Remet tous les paramètres à leur valeur par défaut."""
    return ecrire_parametres(session, {entree.cle: entree.defaut for entree in definitions()})
