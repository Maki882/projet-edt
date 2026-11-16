"""Propositions de l'assistant : ce que l'utilisateur confirme d'un clic, et leur écriture.

Une proposition est créée en mémoire par un outil (`edt.services.assistant.
outils`) après validation par le service concerné ; rien n'est écrit en base
avant le clic sur « Confirmer ». Elle garde le brouillon canonique validé,
la phrase à afficher et son état. À la confirmation, `enregistrer` écrit
dans une transaction courte prise d'emblée en écriture
(`transaction_immediate`), par les fonctions de service habituelles, qui
revalident tout : une donnée changée entre-temps (doublon, cible supprimée)
donne une `ErreurEDT` et rien n'est écrit.

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

from sqlalchemy.orm import Session

from edt.db.moteur import session_base, transaction_immediate
from edt.domaine.regles import EtatRegle, OrigineRegle
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
    annee_id = donnees.get("annee_id")
    nature = proposition.nature
    if nature is NatureProposition.REGLE:
        detail = regles.creer(
            session, donnees["brouillon"], origine=OrigineRegle.ASSISTANT, etat=EtatRegle.ACTIVE
        )
        return (
            f"Enregistrée : règle {detail.code}, active. Elle comptera au prochain calcul.",
            Lien(f"Voir la règle {detail.code}", adresse_regle(detail.code, annee_id)),
        )
    if nature is NatureProposition.MODIFICATION:
        detail = regles.modifier(session, annee_id, donnees["code"], donnees["changements"])
        return (
            f"Enregistrée : règle {detail.code} modifiée. Elle comptera ainsi au prochain calcul.",
            Lien(f"Voir la règle {detail.code}", adresse_regle(detail.code, annee_id)),
        )
    if nature is NatureProposition.ETAT:
        etat = EtatRegle(donnees["etat"])
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
    element = operations.creer(session, "indisponibilite", donnees["valeurs"])
    return (
        f"Enregistrée : indisponibilité n° {element['id']}. Elle comptera au prochain calcul ; "
        "les séances déjà placées sur ce créneau apparaîtront dans « À traiter ».",
        Lien("Voir l'indisponibilité", f"/indisponibilites/{element['id']}"),
    )
