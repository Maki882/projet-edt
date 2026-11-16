"""Propositions de l'assistant : ce que l'utilisateur confirme d'un clic, et leur écriture.

Une proposition est créée en mémoire par un outil (`edt.services.assistant.
outils`) après validation par le service concerné ; rien n'est écrit en base
avant le clic sur « Confirmer ». Elle garde le brouillon canonique validé,
la phrase à afficher et son état. À la confirmation, `enregistrer` écrit
dans une transaction courte prise d'emblée en écriture
(`transaction_immediate`), par les fonctions de service habituelles, qui
revalident tout : une donnée changée entre-temps (doublon, cible supprimée)
donne une `ErreurEDT` et rien n'est écrit.

Les services revalident la forme, pas l'identité : après un rechargement de
la base (établissement fictif chargé par un visiteur, remise à zéro), un
identifiant numérique ou un code de règle peut désigner un autre élément que
celui de la carte. Avant d'écrire, `_ecrire` vérifie donc
(`ChangementDepuisLaProposition` sinon) :

- que la base n'a pas été rechargée depuis la proposition : chaque
  proposition garde la génération de la base (`generation_base`, le numéro
  de schéma de SQLite, qui change quand les tables sont supprimées et
  recréées, jamais pour une simple écriture) ;
- que la cible est toujours celle que la carte a montrée :

- indisponibilité : l'élément de cet identifiant a toujours l'identité lue à
  la proposition (`identite` : identifiant et nom d'un enseignant, code et
  campus d'une salle, code, année et libellé d'un groupe) ;
- état d'une règle : même identifiant et même phrase qu'à la proposition ;
- modification d'une règle : la phrase obtenue est celle de la carte (sinon
  l'exception annule la transaction) ;
- nouvelle règle : la phrase de la règle revalidée est celle de la carte.

| Nature | Écriture |
|---|---|
| `regle` | `regles.creer(..., origine=assistant, etat=active)` |
| `modification` | `regles.modifier` |
| `etat` | `regles.changer_etat` (activer ou désactiver) |
| `indisponibilite` | `referentiels.operations.creer("indisponibilite", ...)` |
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from edt.db import modeles as m
from edt.db.moteur import session_base, transaction_immediate
from edt.domaine.regles import EtatRegle, OrigineRegle
from edt.erreurs import ErreurRegleMetier
from edt.services import regles
from edt.services.emploi_du_temps.presentation import adresse_regle
from edt.services.referentiels import operations

journal = logging.getLogger(__name__)


class NatureProposition(StrEnum):
    """Ce que la proposition ferait si elle était confirmée."""

    REGLE = "regle"
    MODIFICATION = "modification"
    ETAT = "etat"
    INDISPONIBILITE = "indisponibilite"


class EtatProposition(StrEnum):
    """Cycle de vie : en attente, puis confirmée, refusée ou échouée (en cours : écriture)."""

    EN_ATTENTE = "en_attente"
    EN_COURS = "en_cours"
    CONFIRMEE = "confirmee"
    REFUSEE = "refusee"
    ECHOUEE = "echouee"


@dataclass(frozen=True)
class Lien:
    """Lien affiché sous une réponse ou une proposition (adresse construite par le serveur)."""

    libelle: str
    adresse: str


LIBELLES_CONFIRMER = {
    NatureProposition.REGLE: "Confirmer la règle",
    NatureProposition.MODIFICATION: "Confirmer la modification",
    NatureProposition.ETAT: "Confirmer le changement d'état",
    NatureProposition.INDISPONIBILITE: "Confirmer l'indisponibilité",
}


@dataclass
class Proposition:
    """Une proposition présentée dans la conversation (`P1`, `P2`...).

    `donnees` : ce qu'il faut pour écrire (brouillon canonique, code et
    changements, valeurs d'indisponibilité, année) ; `jeton` : identifiant
    aléatoire utilisé dans les adresses des boutons.
    """

    numero: int
    nature: NatureProposition
    titre: str
    phrase: str
    details: tuple[str, ...]
    donnees: dict[str, Any]
    phrase_avant: str | None = None
    lien_ecran: Lien | None = None
    jeton: str = field(default_factory=lambda: secrets.token_urlsafe(9))
    etat: EtatProposition = EtatProposition.EN_ATTENTE
    compte_rendu: str | None = None
    lien: Lien | None = None

    @property
    def code(self) -> str:
        """« P1 »."""
        return f"P{self.numero}"

    @property
    def libelle_confirmer(self) -> str:
        """Libellé explicite du bouton Confirmer (lecteurs d'écran)."""
        return LIBELLES_CONFIRMER[self.nature]

    @property
    def en_attente(self) -> bool:
        """Vrai tant que l'utilisateur peut confirmer ou refuser."""
        return self.etat is EtatProposition.EN_ATTENTE

    def resume(self) -> str:
        """Une ligne pour le journal et les comptes rendus : « P1 (règle) : phrase »."""
        return f"{self.code} ({self.nature.value}) : {self.phrase}"


class ChangementDepuisLaProposition(ErreurRegleMetier):
    """La cible de la proposition n'est plus celle que la carte a montrée : rien n'est écrit."""

    def __init__(self, cible: str | None) -> None:
        cause = (
            "la base a été rechargée (établissement fictif chargé ou remise à zéro)"
            if cible is None
            else f"{cible} n'est plus l'élément visé par la carte"
        )
        super().__init__(
            f"les données ont changé depuis la proposition ({cause}) : redemandez la proposition"
        )


def generation_base(session: Session) -> int:
    """Génération de la base : change quand les tables sont recréées (voir l'en-tête)."""
    return int(session.execute(text("PRAGMA schema_version")).scalar_one())


def identite(objet: m.Enseignant | m.Salle | m.Groupe) -> dict[str, Any]:
    """Ce qui identifie une ressource pour l'utilisateur, au-delà de son identifiant numérique.

    `cle` : les valeurs comparées à la confirmation ; `texte` : la ressource
    telle que la carte la nomme.
    """
    if isinstance(objet, m.Enseignant):
        cle = [objet.identifiant, objet.civilite, objet.nom, objet.prenom]
        texte = f"{objet.nom_affiche} ({objet.identifiant})"
    elif isinstance(objet, m.Salle):
        cle = [objet.code, objet.campus.code]
        texte = f"la salle {objet.code} ({objet.campus.code})"
    else:
        cle = [objet.code, objet.annee_scolaire_id, objet.libelle]
        texte = f"le groupe {objet.code}"
    return {"modele": type(objet).__name__, "id": objet.id, "cle": cle, "texte": texte}


_MODELES_IDENTIFIES = {"Enseignant": m.Enseignant, "Salle": m.Salle, "Groupe": m.Groupe}


def _verifier_identite(session: Session, attendue: dict[str, Any]) -> None:
    objet = session.get(_MODELES_IDENTIFIES[attendue["modele"]], attendue["id"])
    if objet is None or identite(objet)["cle"] != attendue["cle"]:
        raise ChangementDepuisLaProposition(attendue["texte"])


def _verifier_regle(session: Session, proposition: Proposition, phrase: str) -> None:
    donnees = proposition.donnees
    detail = regles.lire(session, donnees["annee_id"], donnees["code"])
    if detail.id != donnees["regle_id"] or detail.phrase != phrase:
        raise ChangementDepuisLaProposition(f"la règle {donnees['code']} « {phrase} »")


def enregistrer(proposition: Proposition) -> tuple[str, Lien | None]:
    """Écrit la proposition dans sa propre transaction ; renvoie le compte rendu et un lien.

    Lève `ErreurEDT` (dont `ErreurSaisie`) si le service la refuse et
    `sqlalchemy.exc.DatabaseError` si la base est occupée : dans les deux cas
    rien n'est écrit.
    """
    with session_base() as session:
        transaction_immediate(session)
        return _ecrire(session, proposition)


def _ecrire(session: Session, proposition: Proposition) -> tuple[str, Lien | None]:
    donnees = proposition.donnees
    if donnees["generation"] != generation_base(session):
        raise ChangementDepuisLaProposition(None)
    annee_id = donnees.get("annee_id")
    nature = proposition.nature
    if nature is NatureProposition.REGLE:
        validation = regles.valider(session, donnees["brouillon"])
        if validation.valide and validation.phrase != proposition.phrase:
            raise ChangementDepuisLaProposition(f"« {proposition.phrase} »")
        detail = regles.creer(
            session, donnees["brouillon"], origine=OrigineRegle.ASSISTANT, etat=EtatRegle.ACTIVE
        )
        return (
            f"Enregistrée : règle {detail.code}, active. Elle comptera au prochain calcul.",
            Lien(f"Voir la règle {detail.code}", adresse_regle(detail.code, annee_id)),
        )
    if nature is NatureProposition.MODIFICATION:
        detail = regles.modifier(session, annee_id, donnees["code"], donnees["changements"])
        if detail.phrase != proposition.phrase:
            # L'exception annule la transaction : la modification n'est pas gardée.
            raise ChangementDepuisLaProposition(f"la règle {detail.code}")
        return (
            f"Enregistrée : règle {detail.code} modifiée. Elle comptera ainsi au prochain calcul.",
            Lien(f"Voir la règle {detail.code}", adresse_regle(detail.code, annee_id)),
        )
    if nature is NatureProposition.ETAT:
        etat = EtatRegle(donnees["etat"])
        _verifier_regle(session, proposition, proposition.phrase)
        detail = regles.changer_etat(session, annee_id, donnees["code"], etat)
        suite = (
            "Elle comptera au prochain calcul."
            if etat is EtatRegle.ACTIVE
            else "Elle n'aura plus d'effet au prochain calcul."
        )
        verbe = "activée" if etat is EtatRegle.ACTIVE else "désactivée"
        return (
            f"Enregistrée : règle {detail.code} {verbe}. {suite}",
            Lien(f"Voir la règle {detail.code}", adresse_regle(detail.code, annee_id)),
        )
    _verifier_identite(session, donnees["identite"])
    element = operations.creer(session, "indisponibilite", donnees["valeurs"])
    return (
        f"Enregistrée : indisponibilité n° {element['id']}. Elle comptera au prochain calcul ; "
        "les séances déjà placées sur ce créneau apparaîtront dans la page Points à traiter (onglet Générer).",
        Lien("Voir l'indisponibilité", f"/indisponibilites/{element['id']}"),
    )
