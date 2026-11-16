"""Conversations de l'assistant : en mémoire du processus, bornées et expirantes.

Rien du contenu d'une conversation n'est écrit en base. Le registre garde au
plus `EDT_ASSISTANT_CONVERSATIONS_MAX` conversations, dont au plus
`EDT_ASSISTANT_CONVERSATIONS_PAR_ADRESSE` par adresse ; une conversation sans
activité depuis `EDT_ASSISTANT_EXPIRATION_MIN` minutes est oubliée. Une
conversation n'est créée que pour un message accepté (limites passées).
Quand la place manque, la moins récemment active est retirée (d'abord parmi
celles de la même adresse), mais jamais une conversation qui a un échange en
cours ou une proposition en attente : faute de place, le nouveau message est
refusé. Un redémarrage du serveur efface tout : le panneau le dit.

Une conversation garde :

- `messages` : l'historique envoyé au service, en **ajout seul** (les blocs
  du modèle tels que reçus) ; un échange n'y ajoute ses messages que s'il
  aboutit ;
- `tours` : ce que le panneau affiche (bulles, propositions, erreurs) ;
- `suites` : confirmations et refus depuis le dernier échange abouti,
  repris par le message système du tour suivant ;
- l'échange en cours (un seul à la fois) et les propositions par jeton.

Chaque conversation a son verrou ; le registre a le sien.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from edt.services.assistant import temps
from edt.services.assistant.limites import Limite, LimiteAtteinte
from edt.services.assistant.propositions import Lien, Proposition

journal = logging.getLogger(__name__)

ETAPE_INITIALE = "Je lis votre demande…"


class RoleTour(StrEnum):
    """Qui parle dans une bulle : l'utilisateur, l'assistant, ou l'application (erreur, limite)."""

    UTILISATEUR = "utilisateur"
    ASSISTANT = "assistant"
    ERREUR = "erreur"
    ATTENTION = "attention"


@dataclass
class Tour:
    """Une bulle du fil : texte, liens et propositions ; `renvoyer` : demande à renvoyer."""

    role: RoleTour
    texte: str
    liens: tuple[Lien, ...] = ()
    propositions: list[Proposition] = field(default_factory=list)
    reference: str | None = None
    renvoyer: str | None = None


@dataclass
class Echange:
    """Un échange en cours ou terminé : étape affichée, puis la bulle de réponse.

    `version` augmente à chaque changement d'étape : la page qui interroge
    l'échange ne reçoit rien de neuf tant qu'elle a déjà la version courante.
    """

    jeton: str
    conversation_id: str
    texte: str
    page: str | None
    debut: float
    etape: str = ETAPE_INITIALE
    version: int = 1
    tour: Tour | None = None
    _verrou: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def reference(self) -> str:
        """Référence citée dans les journaux et les messages d'erreur (« as-<jeton> »)."""
        return f"as-{self.jeton}"

    @property
    def termine(self) -> bool:
        """Vrai quand la bulle de réponse (ou d'erreur) est prête."""
        return self.tour is not None

    def signaler(self, etape: str) -> None:
        """Nouvelle étape affichée pendant l'attente."""
        with self._verrou:
            if etape != self.etape and self.tour is None:
                self.etape = etape
                self.version += 1

    def terminer(self, tour: Tour) -> None:
        """Pose la bulle finale."""
        with self._verrou:
            self.tour = tour
            self.version += 1

    def instantane(self) -> tuple[int, str, Tour | None]:
        """(version, étape, bulle finale) lus ensemble."""
        with self._verrou:
            return self.version, self.etape, self.tour


@dataclass
class Conversation:
    """Une conversation (voir l'en-tête du module)."""

    identifiant: str
    cle_adresse: str
    derniere_activite: float
    messages: list[Any] = field(default_factory=list)
    tours: list[Tour] = field(default_factory=list)
    suites: list[str] = field(default_factory=list)
    nb_envois: int = 0
    taille_contexte: int = 0
    echange: Echange | None = None
    close: str | None = None
    propositions: dict[str, Proposition] = field(default_factory=dict)
    _numero: int = 0
    verrou: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def prochain_numero(self) -> int:
        """Numéro de la proposition suivante (P1, P2... sur toute la conversation)."""
        with self.verrou:
            self._numero += 1
            return self._numero

    @property
    def a_proposition_en_attente(self) -> bool:
        """Vrai si une proposition attend encore Confirmer ou Refuser."""
        return any(p.en_attente for p in self.propositions.values())

    @property
    def retirable(self) -> bool:
        """Vrai si la conversation peut être retirée pour faire de la place."""
        return self.echange is None and not self.a_proposition_en_attente

    @property
    def court(self) -> str:
        """Début de l'identifiant, pour les journaux."""
        return self.identifiant[:8]


class RegistreConversations:
    """Conversations du processus, par identifiant ; échanges par jeton."""

    def __init__(self) -> None:
        self._verrou = threading.RLock()
        self._conversations: dict[str, Conversation] = {}
        self._echanges: dict[str, Echange] = {}

    def trouver(self, identifiant: str | None, expiration_s: float) -> Conversation | None:
        """La conversation `identifiant` si elle existe encore (les expirées sont oubliées)."""
        with self._verrou:
            self._purger(expiration_s)
            if not identifiant:
                return None
            return self._conversations.get(identifiant)

    def creer(
        self, cle_adresse: str, maximum: int, par_adresse: int, expiration_s: float
    ) -> Conversation:
        """Nouvelle conversation (voir l'en-tête) ; `LimiteAtteinte` si aucune place ne se libère."""
        with self._verrou:
            self._purger(expiration_s)
            miennes = [c for c in self._conversations.values() if c.cle_adresse == cle_adresse]
            if len(miennes) >= par_adresse:
                self._faire_place(
                    miennes,
                    "Trop de conversations sont ouvertes depuis votre adresse : terminez celle "
                    "en cours, ou réessayez dans quelques minutes.",
                )
            while len(self._conversations) >= maximum:
                self._faire_place(
                    list(self._conversations.values()),
                    "L'assistant est très sollicité : réessayez dans un instant.",
                )
            conversation = Conversation(
                identifiant=secrets.token_urlsafe(16),
                cle_adresse=cle_adresse,
                derniere_activite=temps.maintenant(),
            )
            self._conversations[conversation.identifiant] = conversation
            return conversation

    def retirer(self, identifiant: str) -> None:
        """Oublie une conversation et ses échanges."""
        with self._verrou:
            self._retirer(identifiant)

    def enregistrer_echange(self, echange: Echange) -> None:
        with self._verrou:
            self._echanges[echange.jeton] = echange

    def echange(self, jeton: str) -> Echange | None:
        """L'échange `jeton`, s'il est encore connu."""
        with self._verrou:
            return self._echanges.get(jeton)

    def nombre(self) -> int:
        """Conversations en mémoire."""
        with self._verrou:
            return len(self._conversations)

    def nb_echanges_en_cours(self) -> int:
        with self._verrou:
            return sum(1 for c in self._conversations.values() if c.echange is not None)

    def _faire_place(self, candidates: list[Conversation], refus: str) -> None:
        retirables = [c for c in candidates if c.retirable]
        if not retirables:
            raise LimiteAtteinte(Limite.MEMOIRE, refus)
        ancienne = min(retirables, key=lambda c: c.derniere_activite)
        journal.info("Conversation %s retirée (place)", ancienne.court)
        self._retirer(ancienne.identifiant)

    def _retirer(self, identifiant: str) -> None:
        conversation = self._conversations.pop(identifiant, None)
        if conversation is None:
            return
        for jeton in [j for j, e in self._echanges.items() if e.conversation_id == identifiant]:
            del self._echanges[jeton]

    def _purger(self, expiration_s: float) -> None:
        limite = temps.maintenant() - expiration_s
        for conversation in list(self._conversations.values()):
            if conversation.echange is None and conversation.derniere_activite < limite:
                journal.info("Conversation %s oubliée (inactive)", conversation.court)
                self._retirer(conversation.identifiant)
