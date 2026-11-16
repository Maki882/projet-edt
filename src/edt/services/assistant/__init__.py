"""Assistant conversationnel : répond sur les données et propose des règles à confirmer.

Point d'entrée des interfaces (panneau web, ligne de commande) ; conception
et limites dans `docs/assistant.md`.

- `etat()` : disponible, ou pourquoi pas (ne lit que l'environnement) ;
- `vue(conversation)` : ce que le panneau affiche ; `exemples()` : trois
  demandes d'exemple tirées des données présentes ;
- `envoyer(...)` : contrôle les limites puis lance l'échange dans un fil
  (`assistant-<jeton>`) ; `lire_echange(...)` : où en est l'échange ;
- `confirmer(...)`, `refuser(...)` : suite donnée à une proposition (seule
  la confirmation écrit, par les services habituels) ;
- `nouvelle_conversation(...)` : oublie la conversation courante ;
- `essayer()`, `repondre(texte)` : un appel d'essai, une réponse synchrone
  (ligne de commande ; les propositions n'y sont pas confirmables).

État en mémoire du processus (seconde exception, après le suivi des
calculs, à la règle « aucun état global ») : registre des conversations,
compteurs par adresse, sel des empreintes d'adresse, pause de l'assistant.
La consommation du jour est aussi en base. `reinitialiser()` repart d'un
état neuf (tests).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import DatabaseError

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.enumerations import NatureGroupe
from edt.erreurs import ErreurEDT
from edt.journal import contexte_requete
from edt.services.assistant import boucle, temps
from edt.services.assistant import client as module_client
from edt.services.assistant.boucle import Issue, ResultatBoucle
from edt.services.assistant.configuration import (
    ConfigurationAssistant,
    Raison,
    annoncer,
    charger_configuration_assistant,
)
from edt.services.assistant.conversations import (
    Conversation,
    Echange,
    RegistreConversations,
    RoleTour,
    Tour,
)
from edt.services.assistant.limites import (
    Budget,
    CompteurAdresses,
    Compteurs,
    LimiteAtteinte,
    cout_maximal_appel,
    cout_reel,
)
from edt.services.assistant.outils import ContexteOutil
from edt.services.assistant.propositions import EtatProposition, Proposition, enregistrer
from edt.services.calendrier import semestre_par_defaut

journal = logging.getLogger(__name__)

EXEMPLES_GENERIQUES = (
    "Quelles salles de plus de 40 places sont libres le jeudi après-midi ?",
    "Quelles règles personnalisées sont actives ?",
    "Y a-t-il des points à traiter ce semestre ?",
)


@dataclass(frozen=True)
class EtatAssistant:
    """État affiché par le panneau : disponible, raison et phrase d'explication."""

    disponible: bool
    raison: Raison
    raison_texte: str


@dataclass
class _Etat:
    registre: RegistreConversations = field(default_factory=RegistreConversations)
    budget: Budget = field(default_factory=Budget)
    adresses: CompteurAdresses = field(default_factory=CompteurAdresses)
    sel: bytes = field(default_factory=lambda: secrets.token_bytes(16))


_etat = _Etat()
_verrou_envoi = threading.Lock()


def reinitialiser() -> None:
    """Repart d'un état neuf : conversations, compteurs en mémoire, sel, pause (tests)."""
    global _etat
    _etat = _Etat()
    boucle.lever_la_pause()


def etat() -> EtatAssistant:
    """État de l'assistant d'après l'environnement ; journalisé à chaque changement."""
    config = charger_configuration_assistant()
    annoncer(config)
    return EtatAssistant(config.disponible, config.raison, config.raison_texte)


def empreinte(adresse: str | None) -> str:
    """Empreinte d'une adresse IP (HMAC d'un sel tiré au démarrage) : jamais l'adresse en clair."""
    return hmac.new(_etat.sel, (adresse or "inconnue").encode(), hashlib.sha256).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Vue du panneau
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VueConversation:
    """Ce que le panneau affiche pour une conversation (ou une conversation à commencer).

    `perdue` : un identifiant a été présenté mais la conversation n'existe
    plus (redémarrage ou inactivité) ; `close` : pourquoi elle ne peut plus
    continuer ; `messages_restants` : messages encore possibles.
    """

    conversation_id: str | None
    tours: tuple[Tour, ...]
    echange: Echange | None
    messages_restants: int
    perdue: bool = False
    close: str | None = None


def _config() -> ConfigurationAssistant:
    config = charger_configuration_assistant()
    annoncer(config)
    return config


def vue(identifiant: str | None) -> VueConversation:
    """La conversation `identifiant` telle que le panneau l'affiche."""
    config = _config()
    conversation = _etat.registre.trouver(identifiant, config.expiration_min * 60)
    if conversation is None:
        return VueConversation(
            None, (), None, config.messages_par_conversation, perdue=bool(identifiant)
        )
    with conversation.verrou:
        return VueConversation(
            conversation.identifiant,
            tuple(conversation.tours),
            conversation.echange,
            max(0, config.messages_par_conversation - conversation.nb_envois),
            close=conversation.close,
        )


def exemples() -> tuple[str, ...]:
    """Trois demandes d'exemple qui ne citent que des éléments présents dans les données.

    Sans données (ou base indisponible, journalisé) : trois exemples génériques.
    """
    try:
        with session_base() as session:
            if session.scalar(select(m.Semestre.id).limit(1)) is None:
                return EXEMPLES_GENERIQUES
            semestre = semestre_par_defaut(session, temps.aujourdhui())
            enseignant = session.scalar(
                select(m.Enseignant)
                .where(m.Enseignant.fictif.is_(False), m.Enseignant.civilite.is_not(None))
                .order_by(m.Enseignant.nom, m.Enseignant.identifiant)
                .limit(1)
            )
            classe = session.scalar(
                select(m.Groupe)
                .where(
                    m.Groupe.annee_scolaire_id == semestre.annee_scolaire_id,
                    m.Groupe.nature == NatureGroupe.CLASSE,
                )
                .order_by(m.Groupe.code)
                .limit(1)
            )
            td = session.scalar(
                select(m.Besoin)
                .join(m.TypeSeance, m.Besoin.type_seance_id == m.TypeSeance.id)
                .where(m.Besoin.semestre_id == semestre.id, m.TypeSeance.code == "TD")
                .order_by(m.Besoin.id)
                .limit(1)
            )
            choisis = [EXEMPLES_GENERIQUES[0]]
            if enseignant is not None:
                choisis.append(f"{enseignant.nom_affiche} n'est pas disponible le mercredi matin.")
            else:
                choisis.append(EXEMPLES_GENERIQUES[1])
            if td is not None and classe is not None:
                matiere = td.matiere.libelle
                choisis.append(
                    f"Ajouter 2 h de TD de {matiere[:1].lower()}{matiere[1:]} par semaine à la "
                    f"classe {classe.code}."
                )
            else:
                choisis.append(EXEMPLES_GENERIQUES[2])
            return tuple(choisis)
    except (DatabaseError, ErreurEDT) as erreur:
        journal.warning("Exemples de l'assistant tirés sans les données (%s)", erreur)
        return EXEMPLES_GENERIQUES


# --------------------------------------------------------------------------- #
# Envoi d'un message
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ResultatEnvoi:
    """Suite d'un message : l'échange lancé, ou la bulle de refus (limite, configuration)."""

    conversation_id: str | None
    echange: Echange | None = None
    tour_utilisateur: Tour | None = None
    refus: Tour | None = None
    nouvelle: bool = False
    perdue: bool = False

    @property
    def accepte(self) -> bool:
        """Vrai si le message a été accepté et l'échange lancé."""
        return self.echange is not None


def _refus(message: str, conversation_id: str | None = None, **options) -> ResultatEnvoi:
    return ResultatEnvoi(conversation_id, refus=Tour(RoleTour.ATTENTION, message), **options)


def verifier_texte(texte: object, config: ConfigurationAssistant) -> str:
    """Le message nettoyé ; `LimiteAtteinte` s'il est vide, trop long ou illisible."""
    from edt.services.assistant.limites import Limite

    brut = texte if isinstance(texte, str) else ""
    nettoye = brut.replace("\r\n", "\n").strip()
    if not nettoye:
        raise LimiteAtteinte(Limite.VIDE, "Votre message est vide : écrivez votre demande.")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in nettoye):
        raise LimiteAtteinte(
            Limite.LONGUEUR,
            "Votre message contient des caractères invisibles non permis : retirez-les.",
        )
    if len(nettoye) > config.longueur_max:
        raise LimiteAtteinte(
            Limite.LONGUEUR,
            f"Votre message dépasse {config.longueur_max} caractères (lu : {len(nettoye)}) : "
            "raccourcissez-le.",
        )
    return nettoye


def envoyer(
    identifiant: str | None, texte: object, page: str | None, adresse: str | None
) -> ResultatEnvoi:
    """Contrôle les limites, puis lance l'échange ; ne lève jamais pour une limite (voir l'en-tête).

    Ordre des contrôles : assistant disponible, pause, message, conversation
    (close, échange en cours, nombre de messages), échanges simultanés, limite
    de l'adresse, limite du site et budget.
    """
    config = _config()
    if not config.disponible:
        return _refus(config.raison_texte, identifiant)
    if boucle.en_pause() is not None:
        return _refus(boucle.INDISPONIBLE, identifiant)
    try:
        texte = verifier_texte(texte, config)
    except LimiteAtteinte as limite:
        return _refus(limite.message, identifiant)
    cle = empreinte(adresse)
    registre = _etat.registre
    with _verrou_envoi:
        conversation = registre.trouver(identifiant, config.expiration_min * 60)
        perdue = bool(identifiant) and conversation is None
        nouvelle = conversation is None
        if conversation is not None:
            with conversation.verrou:
                if conversation.close:
                    return _refus(conversation.close, conversation.identifiant)
                if conversation.echange is not None:
                    return _refus(
                        "Une réponse est en préparation : patientez.", conversation.identifiant
                    )
                if conversation.nb_envois >= config.messages_par_conversation:
                    return _refus(
                        f"Cette conversation est terminée ({config.messages_par_conversation} "
                        "messages). Commencez-en une nouvelle.",
                        conversation.identifiant,
                    )
        if registre.nb_echanges_en_cours() >= config.simultanes:
            journal.info("Assistant : %d échanges en cours, message refusé", config.simultanes)
            return _refus(
                "L'assistant est très sollicité : réessayez dans un instant.",
                None if nouvelle else conversation.identifiant,
            )
        try:
            if conversation is None:
                conversation = registre.creer(
                    cle, config.conversations_max, config.expiration_min * 60
                )
            echange = Echange(
                jeton=secrets.token_urlsafe(9),
                conversation_id=conversation.identifiant,
                texte=texte,
                page=page,
                debut=temps.maintenant(),
            )
            conversation.echange = echange  # réserve la place avant les contrôles en base
        except LimiteAtteinte as limite:
            return _refus(limite.message, None, perdue=perdue)
    try:
        _etat.adresses.compter(cle, config)
        try:
            _etat.budget.ouvrir_echange(config)
        except LimiteAtteinte:
            _etat.adresses.rendre(cle)
            raise
    except LimiteAtteinte as limite:
        with conversation.verrou:
            conversation.echange = None
        return _refus(limite.message, conversation.identifiant, nouvelle=nouvelle, perdue=perdue)
    tour = Tour(RoleTour.UTILISATEUR, texte)
    with conversation.verrou:
        conversation.nb_envois += 1
        conversation.tours.append(tour)
        conversation.derniere_activite = temps.maintenant()
        suites = list(conversation.suites)
        historique = list(conversation.messages)
        taille = conversation.taille_contexte
    registre.enregistrer_echange(echange)
    journal.info(
        "Assistant : échange %s commencé (conversation %s, adresse %s, %d caractères, page %s)",
        echange.reference,
        conversation.court,
        cle[:8],
        len(texte),
        page or "-",
    )
    if config.journal_contenu:
        journal.debug("Assistant : message de %s : %s", echange.reference, texte)
    fil = threading.Thread(
        target=_executer,
        args=(conversation, echange, config, historique, suites, taille),
        name=f"assistant-{echange.jeton}",
        daemon=True,
    )
    fil.start()
    return ResultatEnvoi(conversation.identifiant, echange, tour, nouvelle=nouvelle, perdue=perdue)


def _executer(
    conversation: Conversation,
    echange: Echange,
    config: ConfigurationAssistant,
    historique: list,
    suites: list[str],
    taille: int,
) -> None:
    """Corps du fil d'un échange : rien n'en sort, tout finit en bulle (réponse ou erreur)."""
    jeton_contexte = contexte_requete.set(echange.reference)
    contexte = ContexteOutil(
        reference=echange.reference,
        aujourdhui=temps.aujourdhui(),
        numeroter=conversation.prochain_numero,
    )
    debut = time.monotonic()
    try:
        try:
            client = module_client.creer_client(config)
        except Exception as erreur:  # traduite par issue_de, qui relance ce qui n'est pas du SDK
            resultat = ResultatBoucle(boucle.issue_de(erreur, 0))
            resultat.message = boucle.MESSAGES.get(resultat.issue, boucle.MESSAGES[Issue.INTERNE])
        else:
            resultat = boucle.derouler(
                historique=historique,
                texte=echange.texte,
                message_systeme=boucle.message_du_tour(contexte.aujourdhui, echange.page, suites),
                client=client,
                config=config,
                contexte_outils=contexte,
                budget=_etat.budget,
                taille_connue=taille,
                signaler=echange.signaler,
            )
    except Exception:
        journal.exception(
            "Assistant : échange %s interrompu par une erreur inattendue", echange.reference
        )
        resultat = ResultatBoucle(Issue.INTERNE, message=boucle.MESSAGES[Issue.INTERNE])
    try:
        _terminer(conversation, echange, resultat, contexte, len(suites), config)
        journal.info(
            "Assistant : échange %s terminé (%s) en %.1f s : %d appel(s), outils [%s], jetons "
            "%d (entrée %d, cache lu %d, cache écrit %d, sortie %d), coût estimé %d µ$, modèle(s) %s",
            echange.reference,
            resultat.issue.value,
            time.monotonic() - debut,
            resultat.appels,
            ", ".join(resultat.outils),
            resultat.consommation.jetons,
            resultat.consommation.entree,
            resultat.consommation.cache_lus,
            resultat.consommation.cache_ecrits,
            resultat.consommation.sortie,
            resultat.consommation.cout,
            ", ".join(resultat.modeles) or "-",
        )
        if config.journal_contenu and resultat.texte:
            journal.debug("Assistant : réponse de %s : %s", echange.reference, resultat.texte)
    finally:
        contexte_requete.reset(jeton_contexte)


def _terminer(
    conversation: Conversation,
    echange: Echange,
    resultat: ResultatBoucle,
    contexte: ContexteOutil,
    nb_suites: int,
    config: ConfigurationAssistant,
) -> None:
    with conversation.verrou:
        if resultat.reussi:
            conversation.messages = resultat.messages
            conversation.taille_contexte = resultat.taille_contexte
            del conversation.suites[:nb_suites]
            for proposition in contexte.propositions:
                conversation.propositions[proposition.jeton] = proposition
            tour = Tour(
                RoleTour.ASSISTANT,
                resultat.texte or "Je n'ai pas de réponse à donner : reformulez votre demande.",
                liens=tuple(contexte.liens),
                propositions=list(contexte.propositions),
            )
        else:
            for proposition in contexte.propositions:
                journal.info("Proposition %s abandonnée (échange en échec)", proposition.code)
            role = (
                RoleTour.ATTENTION
                if resultat.issue in (Issue.LIMITE, Issue.CONTEXTE)
                else RoleTour.ERREUR
            )
            reference = echange.reference if resultat.issue in boucle.AVEC_REFERENCE else None
            tour = Tour(role, resultat.message, reference=reference, renvoyer=echange.texte)
            if resultat.issue in boucle.CLOT_LA_CONVERSATION:
                conversation.close = resultat.message
        conversation.tours.append(tour)
        conversation.echange = None
        conversation.derniere_activite = temps.maintenant()
    echange.terminer(tour)


def lire_echange(identifiant: str | None, jeton: str) -> Echange | None:
    """L'échange `jeton` de la conversation `identifiant`, ou `None` (inconnu, redémarrage)."""
    echange = _etat.registre.echange(jeton)
    if echange is None or echange.conversation_id != identifiant:
        return None
    return echange


# --------------------------------------------------------------------------- #
# Suite donnée aux propositions
# --------------------------------------------------------------------------- #

PERDUE = "Cette proposition n'existe plus (redémarrage du serveur ou inactivité) : redemandez-la."


@dataclass(frozen=True)
class ResultatProposition:
    """La proposition après le clic (ou `None` si elle n'existe plus) et un message éventuel."""

    proposition: Proposition | None
    message: str | None = None


def _proposition(identifiant: str | None, jeton: str) -> tuple[Conversation, Proposition] | None:
    config = _config()
    conversation = _etat.registre.trouver(identifiant, config.expiration_min * 60)
    if conversation is None:
        return None
    proposition = conversation.propositions.get(jeton)
    return None if proposition is None else (conversation, proposition)


def confirmer(identifiant: str | None, jeton: str) -> ResultatProposition:
    """Écrit la proposition (une seule fois) ; refus et base occupée rendus en message."""
    trouvee = _proposition(identifiant, jeton)
    if trouvee is None:
        return ResultatProposition(None, PERDUE)
    conversation, proposition = trouvee
    with conversation.verrou:
        if proposition.etat is EtatProposition.CONFIRMEE:
            return ResultatProposition(
                proposition, "Déjà confirmée : rien de plus n'a été enregistré."
            )
        if proposition.etat is EtatProposition.EN_COURS:
            return ResultatProposition(proposition, "Enregistrement en cours : patientez.")
        if proposition.etat is not EtatProposition.EN_ATTENTE:
            return ResultatProposition(proposition)
        proposition.etat = EtatProposition.EN_COURS
        conversation.derniere_activite = temps.maintenant()
    try:
        compte_rendu, lien = enregistrer(proposition)
    except ErreurEDT as refus:
        raisons = " ; ".join(refus.details) or refus.message
        with conversation.verrou:
            proposition.etat = EtatProposition.ECHOUEE
            proposition.compte_rendu = (
                f"Impossible d'enregistrer : {raisons}. Rien n'a été enregistré."
            )
            conversation.suites.append(f"{proposition.code} : confirmation impossible ({raisons})")
        journal.info("Proposition %s refusée par le service : %s", proposition.code, raisons)
        return ResultatProposition(proposition)
    except DatabaseError as panne:
        with conversation.verrou:
            proposition.etat = EtatProposition.EN_ATTENTE
        journal.warning(
            "Proposition %s non enregistrée : base occupée (%s)", proposition.code, panne.orig
        )
        return ResultatProposition(
            proposition, "Base occupée : rien n'a été enregistré, réessayez."
        )
    except Exception:
        with conversation.verrou:
            proposition.etat = EtatProposition.EN_ATTENTE
        journal.exception("Proposition %s : erreur inattendue à l'enregistrement", proposition.code)
        return ResultatProposition(proposition, "Erreur interne : rien n'a été enregistré.")
    with conversation.verrou:
        proposition.etat = EtatProposition.CONFIRMEE
        proposition.compte_rendu, proposition.lien = compte_rendu, lien
        conversation.suites.append(f"{proposition.code} confirmée : {compte_rendu}")
    journal.info("Proposition %s confirmée : %s", proposition.code, compte_rendu)
    return ResultatProposition(proposition)


def refuser(identifiant: str | None, jeton: str) -> ResultatProposition:
    """Refuse la proposition : rien n'est écrit, la suite est notée pour le prochain échange."""
    trouvee = _proposition(identifiant, jeton)
    if trouvee is None:
        return ResultatProposition(None, PERDUE)
    conversation, proposition = trouvee
    with conversation.verrou:
        if proposition.etat is EtatProposition.EN_ATTENTE:
            proposition.etat = EtatProposition.REFUSEE
            proposition.compte_rendu = "Refusée : rien n'a été enregistré."
            conversation.suites.append(f"{proposition.code} refusée")
            journal.info("Proposition %s refusée par l'utilisateur", proposition.code)
        conversation.derniere_activite = temps.maintenant()
    return ResultatProposition(proposition)


def nouvelle_conversation(identifiant: str | None) -> str | None:
    """Oublie la conversation ; renvoie un message de refus si un échange est en cours."""
    config = _config()
    conversation = _etat.registre.trouver(identifiant, config.expiration_min * 60)
    if conversation is None:
        return None
    with conversation.verrou:
        if conversation.echange is not None:
            return "Une réponse est en préparation : attendez-la avant de commencer une nouvelle conversation."
    _etat.registre.retirer(conversation.identifiant)
    journal.info("Conversation %s close à la demande de l'utilisateur", conversation.court)
    return None


# --------------------------------------------------------------------------- #
# Ligne de commande : état, essai, réponse synchrone
# --------------------------------------------------------------------------- #


def configuration() -> ConfigurationAssistant:
    """La configuration courante (journalisée à chaque changement d'état)."""
    return _config()


def consommation_du_jour() -> Compteurs:
    """Consommation du jour du site (base et mémoire)."""
    return _etat.budget.consommation()


@dataclass(frozen=True)
class ResultatEssai:
    """Résultat de l'appel d'essai."""

    reussi: bool
    issue: Issue
    message: str
    duree_s: float = 0.0
    arret: str | None = None
    modele: str | None = None
    consommation: Compteurs = field(default_factory=Compteurs)
    requete: str | None = None


MESSAGE_ESSAI = "Répondez seulement : prêt."
JETONS_ESSAI = 256


def essayer() -> ResultatEssai:
    """Un appel avec exactement les paramètres d'un échange (consignes, outils, repli, effort).

    Vérifie la clé, le modèle, l'en-tête bêta et l'acceptation des schémas
    stricts. Son coût compte dans la consommation du jour.
    """
    config = _config()
    if not config.disponible:
        return ResultatEssai(False, Issue.IDENTIFIANT, config.raison_texte)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": MESSAGE_ESSAI}]},
        boucle.message_du_tour(temps.aujourdhui(), None, ()),
    ]
    try:
        reservation = _etat.budget.reserver(
            cout_maximal_appel(boucle.estimer_jetons(messages, 0, 0), config), config
        )
    except LimiteAtteinte as limite:
        return ResultatEssai(False, Issue.LIMITE, limite.message)
    debut = time.monotonic()
    try:
        client = module_client.creer_client(config)
        reponse = client.with_options(
            timeout=float(config.delai_appel_s), max_retries=0
        ).beta.messages.create(
            **boucle.parametres_requete(messages, config, jetons_sortie=JETONS_ESSAI)
        )
    except Exception as erreur:  # traduite par issue_de, qui relance ce qui n'est pas du SDK
        _etat.budget.annuler(reservation)
        issue = boucle.issue_de(erreur, float(config.delai_appel_s))
        message = boucle.MESSAGES.get(issue, boucle.MESSAGES[Issue.INTERNE])
        return ResultatEssai(False, issue, message, time.monotonic() - debut)
    consommation = cout_reel(reponse.usage, reponse.model)
    _etat.budget.solder(reservation, consommation)
    duree = time.monotonic() - debut
    reussi = reponse.stop_reason == "end_turn"
    journal.info(
        "Assistant : essai %s en %.1f s (arrêt %s, modèle %s, coût estimé %d µ$)",
        "réussi" if reussi else "en échec",
        duree,
        reponse.stop_reason,
        reponse.model,
        consommation.cout,
    )
    return ResultatEssai(
        reussi,
        Issue.SUCCES if reussi else Issue.ARRET_INCONNU,
        "Essai réussi." if reussi else f"Arrêt inattendu du modèle : {reponse.stop_reason}.",
        duree,
        reponse.stop_reason,
        reponse.model,
        consommation,
        getattr(reponse, "_request_id", None),
    )


@dataclass(frozen=True)
class ResultatReponse:
    """Réponse synchrone (`repondre`) : texte ou message d'échec, propositions, liens."""

    reussi: bool
    texte: str
    propositions: tuple[Proposition, ...] = ()
    liens: tuple = ()
    issue: Issue = Issue.SUCCES


def repondre(texte: str) -> ResultatReponse:
    """Une question posée hors du panneau (ligne de commande), avec les limites du site."""
    config = _config()
    if not config.disponible:
        return ResultatReponse(False, config.raison_texte, issue=Issue.IDENTIFIANT)
    try:
        texte = verifier_texte(texte, config)
        _etat.budget.ouvrir_echange(config)
    except LimiteAtteinte as limite:
        return ResultatReponse(False, limite.message, issue=Issue.LIMITE)
    conversation = Conversation(
        identifiant="ligne-de-commande", cle_adresse="", derniere_activite=0.0
    )
    echange = Echange(
        jeton=secrets.token_urlsafe(9),
        conversation_id=conversation.identifiant,
        texte=texte,
        page=None,
        debut=temps.maintenant(),
    )
    conversation.echange = echange
    _executer(conversation, echange, config, [], [], 0)
    tour = echange.tour
    if tour is None or tour.role is not RoleTour.ASSISTANT:
        message = tour.texte if tour is not None else boucle.MESSAGES[Issue.INTERNE]
        return ResultatReponse(False, message, issue=Issue.INTERNE)
    return ResultatReponse(True, tour.texte, tuple(tour.propositions), tour.liens)
