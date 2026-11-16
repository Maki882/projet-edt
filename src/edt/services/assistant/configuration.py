"""Configuration de l'assistant conversationnel, lue dans les variables d'environnement.

| Variable | Défaut | Rôle |
|---|---|---|
| `ANTHROPIC_API_KEY` (ou `ANTHROPIC_AUTH_TOKEN`) | absente | identifiant du service, lu par le SDK seulement |
| `EDT_ASSISTANT_ACTIF` | `oui` | `non` coupe l'assistant sans retirer la clé |
| `EDT_ASSISTANT_MODELE` | `claude-opus-5-5` | modèle ; doit figurer dans `PRIX_PAR_MODELE` |
| `EDT_ASSISTANT_EFFORT` | `low` | `low`, `medium`, `high`, `xhigh` ou `max` |
| `EDT_ASSISTANT_JETONS_SORTIE_MAX` | `4096` | jetons de sortie par appel (réflexion comprise) |
| `EDT_ASSISTANT_APPELS_MAX` | `5` | appels au modèle par échange |
| `EDT_ASSISTANT_DELAI_S` | `90` | échéance d'un échange, en secondes |
| `EDT_ASSISTANT_DELAI_APPEL_S` | `40` | délai d'un appel, en secondes |
| `EDT_ASSISTANT_LONGUEUR_MAX` | `800` | caractères par message |
| `EDT_ASSISTANT_MESSAGES_PAR_CONVERSATION` | `10` | messages de l'utilisateur par conversation |
| `EDT_ASSISTANT_CONTEXTE_MAX` | `40000` | jetons d'entrée d'un appel ; au-delà, nouvelle conversation |
| `EDT_ASSISTANT_MESSAGES_PAR_ADRESSE` | `20` | messages par adresse IP et par jour |
| `EDT_ASSISTANT_MESSAGES_PAR_JOUR` | `100` | messages par jour pour tout le site |
| `EDT_ASSISTANT_BUDGET_JOUR` | `2.00` | dollars par jour pour tout le site (coût estimé) |
| `EDT_ASSISTANT_SIMULTANES` | `2` | échanges en même temps |
| `EDT_ASSISTANT_CONVERSATIONS_MAX` | `200` | conversations gardées en mémoire |
| `EDT_ASSISTANT_EXPIRATION_MIN` | `30` | minutes sans activité avant l'oubli d'une conversation |
| `EDT_ASSISTANT_JOURNAL_CONTENU` | `non` | `oui` : textes des messages au niveau DEBUG |

Contrairement à `edt.config.charger_configuration`, une valeur invalide ne
lève rien : l'assistant devient « indisponible (configuration) », chaque
erreur nomme la variable et la valeur lue, et le reste de l'application
fonctionne. La configuration est relue à chaque appel (aucun état global).

La clé n'est jamais lue ni recopiée par l'application : seule sa présence est
testée (`variable_cle`), et c'est le SDK qui la lit au moment de construire
le client (`edt.services.assistant.client`).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from functools import lru_cache
from importlib import metadata

journal = logging.getLogger(__name__)

VARIABLES_CLE = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
"""Variables d'identifiant reconnues par le SDK, dans son ordre de préférence."""

EFFORTS = ("low", "medium", "high", "xhigh", "max")
VRAI = frozenset({"oui", "o", "true", "vrai", "1", "on", "yes"})
FAUX = frozenset({"non", "n", "false", "faux", "0", "off", "no"})
MICRO = 1_000_000
"""Micro-dollars par dollar : les coûts sont comptés en entiers de micro-dollars."""


@dataclass(frozen=True)
class Prix:
    """Prix d'un modèle en dollars par million de jetons.

    `ecriture_cache` vaut pour une écriture en cache de 5 minutes. Un nombre
    de jetons multiplié par l'un de ces prix donne directement des micro-dollars.
    """

    entree: float
    sortie: float
    ecriture_cache: float
    lecture_cache: float


PRIX_PAR_MODELE: dict[str, Prix] = {
    "claude-opus-5-5": Prix(entree=4.00, sortie=20.00, ecriture_cache=5.00, lecture_cache=0.20),
    "claude-opus-5": Prix(entree=5.00, sortie=25.00, ecriture_cache=6.25, lecture_cache=0.50),
    "claude-sonnet-5-5": Prix(entree=2.00, sortie=10.00, ecriture_cache=2.50, lecture_cache=0.10),
}
"""Prix de référence (dollars par million de jetons). Le modèle de repli d'une demande
refusée n'est pas connu d'avance : son coût est compté au prix du plus cher de la table."""


def prix_le_plus_cher() -> Prix:
    """Le prix le plus élevé de la table (borne des coûts d'un modèle inconnu ou de repli)."""
    return max(PRIX_PAR_MODELE.values(), key=lambda prix: (prix.sortie, prix.entree))


class Raison(StrEnum):
    """Pourquoi l'assistant est disponible ou non."""

    DISPONIBLE = "disponible"
    DESACTIVE = "desactive"
    NON_CONFIGURE = "non_configure"
    CONFIGURATION = "configuration"


TEXTES_RAISON = {
    Raison.DISPONIBLE: "L'assistant est prêt.",
    Raison.DESACTIVE: "L'assistant est désactivé par l'administrateur.",
    Raison.NON_CONFIGURE: "L'assistant n'est pas configuré sur ce serveur.",
    Raison.CONFIGURATION: (
        "L'assistant est indisponible : sa configuration est à corriger par l'administrateur."
    ),
}
"""Phrase affichée dans le panneau pour chaque raison."""


@dataclass(frozen=True)
class ConfigurationAssistant:
    """Réglages de l'assistant pour le processus courant.

    `variable_cle` : nom de la variable d'identifiant présente (jamais sa
    valeur) ; `deux_cles` : les deux variables sont posées (le SDK prend la
    clé d'API) ; `erreurs` : une phrase par variable invalide ; `version_sdk` :
    version du paquet `anthropic` installé, `None` s'il manque.
    """

    actif: bool = True
    modele: str = "claude-opus-5-5"
    effort: str = "low"
    jetons_sortie_max: int = 4096
    appels_max: int = 5
    delai_s: int = 90
    delai_appel_s: int = 40
    longueur_max: int = 800
    messages_par_conversation: int = 10
    contexte_max: int = 40_000
    messages_par_adresse: int = 20
    messages_par_jour: int = 100
    budget_jour_micro: int = 2 * MICRO
    simultanes: int = 2
    conversations_max: int = 200
    expiration_min: int = 30
    journal_contenu: bool = False
    variable_cle: str | None = None
    deux_cles: bool = False
    version_sdk: str | None = None
    erreurs: tuple[str, ...] = field(default=())

    @property
    def raison(self) -> Raison:
        """Raison de l'état : désactivé, non configuré, configuration invalide ou disponible."""
        if not self.actif:
            return Raison.DESACTIVE
        if self.variable_cle is None:
            return Raison.NON_CONFIGURE
        if self.erreurs:
            return Raison.CONFIGURATION
        return Raison.DISPONIBLE

    @property
    def disponible(self) -> bool:
        """Vrai si l'assistant peut répondre."""
        return self.raison is Raison.DISPONIBLE

    @property
    def raison_texte(self) -> str:
        """Phrase d'état pour l'utilisateur."""
        return TEXTES_RAISON[self.raison]

    @property
    def prix(self) -> Prix:
        """Prix du modèle configuré (celui du plus cher si le modèle est hors table)."""
        return PRIX_PAR_MODELE.get(self.modele, prix_le_plus_cher())

    @property
    def budget_jour_texte(self) -> str:
        """Budget du jour en dollars, deux décimales (« 2,00 $ »)."""
        return dollars(self.budget_jour_micro)


def dollars(micro: int) -> str:
    """Montant en micro-dollars écrit en dollars (« 0,024 $ » ; deux décimales au moins)."""
    texte = f"{micro / MICRO:.3f}"
    if texte.endswith("0"):
        texte = texte[:-1]
    return texte.replace(".", ",") + " $"


@dataclass(frozen=True)
class _Entier:
    nom: str
    attribut: str
    minimum: int
    maximum: int


ENTIERS: tuple[_Entier, ...] = (
    _Entier("EDT_ASSISTANT_JETONS_SORTIE_MAX", "jetons_sortie_max", 256, 64_000),
    _Entier("EDT_ASSISTANT_APPELS_MAX", "appels_max", 1, 10),
    # L'échéance d'un échange reste sous le délai de nginx (300 s) avec la marge d'un appel.
    _Entier("EDT_ASSISTANT_DELAI_S", "delai_s", 10, 240),
    _Entier("EDT_ASSISTANT_DELAI_APPEL_S", "delai_appel_s", 5, 120),
    _Entier("EDT_ASSISTANT_LONGUEUR_MAX", "longueur_max", 50, 4000),
    _Entier("EDT_ASSISTANT_MESSAGES_PAR_CONVERSATION", "messages_par_conversation", 1, 100),
    _Entier("EDT_ASSISTANT_CONTEXTE_MAX", "contexte_max", 5000, 200_000),
    _Entier("EDT_ASSISTANT_MESSAGES_PAR_ADRESSE", "messages_par_adresse", 1, 100_000),
    _Entier("EDT_ASSISTANT_MESSAGES_PAR_JOUR", "messages_par_jour", 0, 100_000),
    _Entier("EDT_ASSISTANT_SIMULTANES", "simultanes", 1, 20),
    _Entier("EDT_ASSISTANT_CONVERSATIONS_MAX", "conversations_max", 1, 10_000),
    _Entier("EDT_ASSISTANT_EXPIRATION_MIN", "expiration_min", 1, 1440),
)
"""Variables entières : nom, attribut de `ConfigurationAssistant`, bornes incluses."""

BUDGET_MAX = Decimal("1000")


@lru_cache(maxsize=1)
def version_sdk() -> str | None:
    """Version du paquet `anthropic` installé, `None` s'il manque (lue une fois par processus)."""
    try:
        return metadata.version("anthropic")
    except metadata.PackageNotFoundError:
        return None


def variable_cle_presente(environ: Mapping[str, str] | None = None) -> str | None:
    """Nom de la première variable d'identifiant non vide, ou `None` (la valeur n'est pas lue)."""
    source = os.environ if environ is None else environ
    for nom in VARIABLES_CLE:
        if source.get(nom, "").strip():
            return nom
    return None


def charger_configuration_assistant(
    environ: Mapping[str, str] | None = None,
    *,
    version: Callable[[], str | None] = version_sdk,
) -> ConfigurationAssistant:
    """Construit la configuration de l'assistant ; ne lève jamais (voir l'en-tête)."""
    source = os.environ if environ is None else environ
    erreurs: list[str] = []
    valeurs: dict[str, object] = {}

    def lire(nom: str) -> str | None:
        brut = source.get(nom, "").strip()
        return brut or None

    for nom, attribut in (
        ("EDT_ASSISTANT_ACTIF", "actif"),
        ("EDT_ASSISTANT_JOURNAL_CONTENU", "journal_contenu"),
    ):
        brut = lire(nom)
        if brut is None:
            continue
        if brut.lower() in VRAI:
            valeurs[attribut] = True
        elif brut.lower() in FAUX:
            valeurs[attribut] = False
        else:
            erreurs.append(f"{nom} doit valoir « oui » ou « non » (valeur lue : {brut!r}).")

    modele = lire("EDT_ASSISTANT_MODELE")
    if modele is not None:
        if modele in PRIX_PAR_MODELE:
            valeurs["modele"] = modele
        else:
            erreurs.append(
                f"EDT_ASSISTANT_MODELE : modèle sans prix connu (valeur lue : {modele!r}) ; "
                "modèles connus : " + ", ".join(sorted(PRIX_PAR_MODELE)) + "."
            )

    effort = lire("EDT_ASSISTANT_EFFORT")
    if effort is not None:
        if effort.lower() in EFFORTS:
            valeurs["effort"] = effort.lower()
        else:
            erreurs.append(
                "EDT_ASSISTANT_EFFORT doit valoir "
                + ", ".join(EFFORTS)
                + f" (valeur lue : {effort!r})."
            )

    for entier in ENTIERS:
        brut = lire(entier.nom)
        if brut is None:
            continue
        if not (brut.isascii() and brut.isdigit()) or not (
            entier.minimum <= int(brut) <= entier.maximum
        ):
            erreurs.append(
                f"{entier.nom} doit être un entier de {entier.minimum} à {entier.maximum} "
                f"(valeur lue : {brut!r})."
            )
            continue
        valeurs[entier.attribut] = int(brut)

    budget = lire("EDT_ASSISTANT_BUDGET_JOUR")
    if budget is not None:
        try:
            montant = Decimal(budget.replace(",", "."))
        except InvalidOperation:
            montant = None
        if montant is None or not montant.is_finite() or not Decimal(0) <= montant <= BUDGET_MAX:
            erreurs.append(
                "EDT_ASSISTANT_BUDGET_JOUR doit être un montant en dollars de 0 à "
                f"{BUDGET_MAX} (valeur lue : {budget!r})."
            )
        else:
            valeurs["budget_jour_micro"] = int(montant * MICRO)

    delai = valeurs.get("delai_s", ConfigurationAssistant.delai_s)
    delai_appel = valeurs.get("delai_appel_s", ConfigurationAssistant.delai_appel_s)
    if isinstance(delai, int) and isinstance(delai_appel, int) and delai_appel > delai:
        erreurs.append(
            "EDT_ASSISTANT_DELAI_APPEL_S doit être au plus égal à EDT_ASSISTANT_DELAI_S "
            f"(valeurs lues : {delai_appel} et {delai})."
        )

    presentes = [nom for nom in VARIABLES_CLE if source.get(nom, "").strip()]
    installee = version()
    if installee is None:
        erreurs.append(
            "Le paquet Python « anthropic » (version 2) n'est pas installé dans l'environnement "
            "du serveur : l'installer (dépendance du projet), puis redémarrer le service."
        )
    elif not installee.split(".")[0].isdigit() or int(installee.split(".")[0]) < 2:
        erreurs.append(
            f"Le paquet Python « anthropic » installé est en version {installee} : la version 2 "
            "est attendue (dépendance du projet)."
        )
    return ConfigurationAssistant(
        **valeurs,  # type: ignore[arg-type]
        variable_cle=presentes[0] if presentes else None,
        deux_cles=len(presentes) > 1,
        version_sdk=installee,
        erreurs=tuple(erreurs),
    )


_annonce = {"signature": None}
_verrou_annonce = threading.Lock()


def annoncer(config: ConfigurationAssistant) -> None:
    """Journalise l'état de l'assistant la première fois, puis à chaque changement d'état.

    INFO s'il est prêt (modèle, effort, limites) ou non configuré ; ERROR pour
    une configuration invalide, avec chaque variable fautive. Jamais la clé.
    """
    signature = (config.raison, config.modele, config.effort, config.erreurs, config.deux_cles)
    with _verrou_annonce:
        if _annonce["signature"] == signature:
            return
        _annonce["signature"] = signature
    if config.raison is Raison.DISPONIBLE:
        journal.info(
            "Assistant configuré (identifiant : %s ; modèle %s, effort %s ; %d appels et %d s par "
            "échange ; %d messages par conversation, %d par adresse et %d par jour ; budget %s par "
            "jour)",
            config.variable_cle,
            config.modele,
            config.effort,
            config.appels_max,
            config.delai_s,
            config.messages_par_conversation,
            config.messages_par_adresse,
            config.messages_par_jour,
            config.budget_jour_texte,
        )
    elif config.raison is Raison.CONFIGURATION:
        journal.error("Assistant indisponible (configuration) : %s", " ".join(config.erreurs))
    elif config.raison is Raison.DESACTIVE:
        journal.info("Assistant désactivé (EDT_ASSISTANT_ACTIF=non)")
    else:
        journal.info("Assistant non configuré (aucune clé dans l'environnement)")
    if config.deux_cles:
        journal.warning(
            "ANTHROPIC_API_KEY et ANTHROPIC_AUTH_TOKEN sont posées toutes les deux : le service "
            "utilise la clé d'API ; retirer celle qui ne sert pas"
        )


def oublier_annonce() -> None:
    """Oublie le dernier état journalisé (tests : chaque test repart d'un processus neuf)."""
    with _verrou_annonce:
        _annonce["signature"] = None
