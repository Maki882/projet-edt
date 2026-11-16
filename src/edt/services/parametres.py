"""Lecture et écriture des paramètres globaux (table `parametre`).

Les paramètres, leurs types, leurs bornes et leurs valeurs par défaut sont
définis une seule fois dans `edt.domaine.parametres.Parametres`. La table
stocke chaque valeur en JSON : entier, booléen ou chaîne (paramètres à choix).

Base qui porte encore le paramètre `dejeuner_horaires_determines` (remplacé
par `dejeuner_mode`) ou des réglages de déjeuner incohérents : la lecture
reste possible sans rien modifier en base (voir `_mode_dejeuner_herite` et
`_dejeuner_lisible`) ; enregistrer les paramètres dans leur écran met la base
d'aplomb.

Valeur corrompue (type impossible, « quinze » pour une durée) : la lecture
stricte lève en nommant le paramètre ; l'écriture et la réinitialisation
lisent en mode **tolérant** (défaut à la place, avertissement) pour que
l'application reste réparable depuis son écran ; `anomalies_parametres`
liste les paramètres fautifs à afficher.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from edt.db.modeles import Parametre
from edt.domaine.parametres import (
    DEJEUNER_AUCUN,
    DEJEUNER_FIXE,
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

ANCIENNE_CLE_DEJEUNER = "dejeuner_horaires_determines"
"""Paramètre booléen remplacé par `dejeuner_mode`, lu s'il figure encore dans une base."""


def lire_parametres(session: Session, *, tolerant: bool = False) -> Parametres:
    """Tous les paramètres, typés.

    Un paramètre absent de la table (base créée avant son ajout) prend sa
    valeur par défaut documentée, et le fait est journalisé. Un nombre
    enregistré hors des bornes actuelles (bornes resserrées depuis son
    enregistrement) est ramené à la borne la plus proche, avec un
    avertissement au journal : la valeur en base n'est pas modifiée. Le mode
    de déjeuner d'une ancienne base se déduit de `dejeuner_horaires_determines`
    (voir `_mode_dejeuner_herite`), et des réglages de déjeuner incohérents
    sont lus comme « pas de pause » (voir `_dejeuner_lisible`). Toute autre
    valeur enregistrée invalide lève `ErreurDonneesInvalides` ; en mode
    `tolerant`, elle est remplacée par la valeur par défaut avec un
    avertissement, pour que l'écran des paramètres puisse la réenregistrer.
    """
    valeurs, anomalies = _lire(session)
    if anomalies:
        cle, message = next(iter(anomalies.items()))
        if not tolerant:
            raise ErreurDonneesInvalides(
                f"Le paramètre « {cle} » enregistré en base est invalide : {message}"
            )
        for cle, message in anomalies.items():
            journal.warning(
                "Paramètre « %s » enregistré invalide (%s) : valeur par défaut %r utilisée à sa "
                "place ; l'enregistrer de nouveau dans l'écran des paramètres.",
                cle,
                message,
                definition(cle).defaut,
            )
    valeurs.update(_dejeuner_lisible(valeurs))
    return Parametres(**valeurs)


def anomalies_parametres(session: Session) -> dict[str, str]:
    """Paramètres dont la valeur enregistrée est invalide : clé → message (vide si tout va bien)."""
    return _lire(session)[1]


def _lire(session: Session) -> tuple[dict[str, ValeurParametre], dict[str, str]]:
    """Valeurs valides (défaut pour une valeur absente ou invalide) et anomalies par clé."""
    enregistres = dict(session.execute(select(Parametre.cle, Parametre.valeur)).all())
    valeurs: dict[str, ValeurParametre] = {}
    anomalies: dict[str, str] = {}
    try:
        enregistres.update(_mode_dejeuner_herite(enregistres))
    except ErreurDonneesInvalides as erreur:
        # Ancien paramètre illisible : le mode par défaut est proposé, et l'enregistrer
        # inscrit `dejeuner_mode`, qui rend l'ancien paramètre sans effet.
        anomalies["dejeuner_mode"] = erreur.message
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
            anomalies[entree.cle] = erreur.message
    return valeurs, anomalies


def _mode_dejeuner_herite(enregistres: dict[str, object]) -> dict[str, str]:
    """Mode de déjeuner déduit de l'ancien paramètre `dejeuner_horaires_determines`, ou `{}`.

    Si la base porte l'ancien booléen `dejeuner_horaires_determines` et pas
    encore `dejeuner_mode`, vrai devient « horaires déterminés » et faux « pas
    de pause ». Le fait est journalisé ; la base n'est pas modifiée.
    """
    if "dejeuner_mode" in enregistres or ANCIENNE_CLE_DEJEUNER not in enregistres:
        return {}
    ancien = enregistres[ANCIENNE_CLE_DEJEUNER]
    if not isinstance(ancien, bool):
        raise ErreurDonneesInvalides(
            f"l'ancien paramètre « {ANCIENNE_CLE_DEJEUNER} » vaut {ancien!r} au lieu de oui ou non."
        )
    mode = DEJEUNER_FIXE if ancien else DEJEUNER_AUCUN
    journal.info(
        "Ancien paramètre « %s » = %r lu comme mode de déjeuner « %s » ; la base n'est pas "
        "modifiée (enregistrer les paramètres dans leur écran pour l'y inscrire).",
        ANCIENNE_CLE_DEJEUNER,
        ancien,
        mode,
    )
    return {"dejeuner_mode": mode}


def _dejeuner_lisible(valeurs: dict[str, ValeurParametre]) -> dict[str, ValeurParametre]:
    """Correction de lecture de réglages de déjeuner enregistrés incohérents, ou `{}`.

    La validation exige une plage de déjeuner non vide dans les heures
    d'ouverture (ou, en pause libre, une fenêtre assez longue). Une base qui
    tient une plage vide (début = fin) ou hors des heures d'ouverture reste
    lisible : le déjeuner est lu comme « pas de pause » et un avertissement dit
    quoi régler. La base n'est pas modifiée.
    """
    lues = {entree.cle: entree.defaut for entree in definitions()} | valeurs
    if lues["dejeuner_mode"] == DEJEUNER_AUCUN:
        return {}
    defauts = defauts_dejeuner(
        lues["dejeuner_debut_min"],
        lues["dejeuner_fin_min"],
        lues["ouverture_min"],
        lues["fermeture_min"],
        mode=str(lues["dejeuner_mode"]),
        duree=int(lues["dejeuner_duree_min"]),
    )
    if not defauts:
        return {}
    journal.warning(
        "Pause déjeuner enregistrée incohérente (%s) : elle est lue comme « pas de pause ». "
        "L'enregistrer de nouveau dans l'écran des paramètres fait disparaître cet avertissement.",
        " ".join(defauts.values()),
    )
    return {"dejeuner_mode": DEJEUNER_AUCUN}


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
    # Lecture tolérante : une valeur corrompue ailleurs n'empêche pas de réparer la base.
    resultat = lire_parametres(session, tolerant=True).avec(**nouvelles)
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
