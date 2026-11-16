"""Conversations et propositions : échange dans son fil, limites, confirmation, refus, secret.

Le faux client rejoue les réponses du modèle ; les échanges tournent dans
leur fil, comme sur le serveur (`attendre` attend leur fin).
"""

from __future__ import annotations

import logging

import anthropic
import pytest
from sqlalchemy import func, select
from tests.outils.assistant.faux_client import erreur_api, message, reponse_outil, reponse_texte
from tests.outils.assistant.fixtures import CLE_FACTICE, attendre

from edt.db import modeles as m
from edt.db.moteur import session_base
from edt.domaine.regles import EtatRegle, OrigineRegle
from edt.services import assistant, regles
from edt.services.assistant import client as module_client
from edt.services.assistant import temps
from edt.services.assistant.conversations import RoleTour
from edt.services.assistant.propositions import EtatProposition

ADRESSE = "203.0.113.10"
REGLE_CM = {
    "type": "CRENEAUX_INTERDITS",
    "portee": {"nature": "type_seance", "cible": "CM"},
    "parametres": {"jours": ["mercredi"], "debut": "13:45", "fin": "18:00"},
    "niveau": None,
    "periode": {},
    "commentaire": None,
}


def _envoyer(texte="Bonjour", identifiant=None, adresse=ADRESSE, page="/salles"):
    return assistant.envoyer(identifiant, texte, page, adresse)


def _echanger(texte="Bonjour", identifiant=None, adresse=ADRESSE):
    resultat = _envoyer(texte, identifiant, adresse=adresse)
    assert resultat.accepte, resultat.refus
    attendre(resultat.echange)
    return resultat


def _nombre(modele) -> int:
    with session_base() as session:
        return session.scalar(select(func.count()).select_from(modele))


# --------------------------------------------------------------------------- #
# Échange
# --------------------------------------------------------------------------- #


def test_un_echange_complet(base, faux_client, jour_fige):
    faux_client.reponses = [reponse_texte("Bonjour, que puis-je faire ?")]
    resultat = _echanger()
    assert resultat.nouvelle and resultat.tour_utilisateur.texte == "Bonjour"
    vue = assistant.vue(resultat.conversation_id)
    assert [t.role for t in vue.tours] == [RoleTour.UTILISATEUR, RoleTour.ASSISTANT]
    assert vue.tours[1].texte == "Bonjour, que puis-je faire ?"
    assert vue.messages_restants == 9 and vue.echange is None
    systeme = faux_client.appels[0]["messages"][1]
    assert systeme["role"] == "system" and "lundi 5 octobre 2026" in systeme["content"]


def test_echange_en_echec_ne_laisse_rien_dans_l_historique(base, faux_client, jour_fige):
    faux_client.reponses = [message([], "refusal"), reponse_texte("Réponse.")]
    premier = _echanger("Question piège")
    vue = assistant.vue(premier.conversation_id)
    erreur = vue.tours[-1]
    assert erreur.role is RoleTour.ERREUR and erreur.renvoyer == "Question piège"
    _echanger("Vraie question", premier.conversation_id)
    envoyes = faux_client.appels[1]["messages"]
    assert [m["content"][0]["text"] for m in envoyes if m["role"] == "user"] == ["Vraie question"]


def test_echange_inconnu_ou_d_une_autre_conversation(base, faux_client, jour_fige):
    faux_client.reponses = [reponse_texte("Oui.")]
    resultat = _echanger()
    assert (
        assistant.lire_echange(resultat.conversation_id, resultat.echange.jeton) is resultat.echange
    )
    assert assistant.lire_echange("autre", resultat.echange.jeton) is None
    assert assistant.lire_echange(resultat.conversation_id, "inconnu") is None
    assistant.reinitialiser()  # redémarrage du serveur
    assert assistant.lire_echange(resultat.conversation_id, resultat.echange.jeton) is None
    assert assistant.vue(resultat.conversation_id).perdue


def test_journal_de_fin_d_echange_sans_texte_des_messages(base, faux_client, jour_fige, caplog):
    faux_client.reponses = [reponse_outil("lire_contexte", {}), reponse_texte("Réponse secrète.")]
    with caplog.at_level(logging.DEBUG, logger="edt.services.assistant"):
        _echanger("Texte confidentiel de l'utilisateur")
    fin = [r.getMessage() for r in caplog.records if "terminé (succes)" in r.getMessage()]
    assert len(fin) == 1
    assert "2 appel(s), outils [lire_contexte]" in fin[0] and "coût estimé" in fin[0]
    assert "Texte confidentiel" not in caplog.text and "Réponse secrète" not in caplog.text
    assert ADRESSE not in caplog.text


def test_texte_des_messages_journalise_sur_demande(
    base, faux_client, jour_fige, monkeypatch, caplog
):
    monkeypatch.setenv("EDT_ASSISTANT_JOURNAL_CONTENU", "oui")
    faux_client.reponses = [reponse_texte("Réponse.")]
    with caplog.at_level(logging.DEBUG, logger="edt.services.assistant"):
        _echanger("Texte à suivre")
    assert "Texte à suivre" in caplog.text and "Réponse." in caplog.text


# --------------------------------------------------------------------------- #
# Limites
# --------------------------------------------------------------------------- #


@pytest.mark.cas_limite
@pytest.mark.parametrize(
    ("texte", "accepte", "extrait"),
    [
        ("x" * 800, True, None),
        ("x" * 801, False, "dépasse 800 caractères (lu : 801)"),
        ("   \n  ", False, "Votre message est vide"),
        ("", False, "Votre message est vide"),
        ("Bonjour\x00", False, "caractères invisibles"),
    ],
)
def test_longueur_et_contenu_du_message(base, faux_client, jour_fige, texte, accepte, extrait):
    """Cas : 800 caractères pile, 801, un message fait d'espaces, un caractère de contrôle.

    Risque : un message géant qui coûte cher, ou un caractère qui casse l'affichage.
    """
    faux_client.reponses = [reponse_texte("ok")]
    resultat = _envoyer(texte)
    assert resultat.accepte is accepte
    if accepte:
        attendre(resultat.echange)
    else:
        assert extrait in resultat.refus.texte and faux_client.appels == []


@pytest.mark.cas_limite
def test_limite_de_messages_par_conversation(base, faux_client, jour_fige, monkeypatch):
    """Cas : la conversation a atteint son nombre de messages.

    Risque : une conversation sans fin dont chaque appel coûte plus cher (contexte croissant).
    """
    monkeypatch.setenv("EDT_ASSISTANT_MESSAGES_PAR_CONVERSATION", "1")
    faux_client.reponses = [reponse_texte("ok")]
    premier = _echanger()
    second = _envoyer("Encore", premier.conversation_id)
    assert (
        not second.accepte and "Cette conversation est terminée (1 messages)" in second.refus.texte
    )
    assert len(faux_client.appels) == 1
    assert assistant.vue(premier.conversation_id).messages_restants == 0


@pytest.mark.cas_limite
def test_un_seul_echange_a_la_fois_et_echanges_simultanes(
    base, faux_client, jour_fige, monkeypatch
):
    """Cas : deux envois rapides dans la même conversation ; deux visiteurs en même temps
    avec une seule place.

    Risque : deux réponses entremêlées dans un historique, ou un serveur submergé.
    """
    monkeypatch.setenv("EDT_ASSISTANT_SIMULTANES", "1")
    faux_client.reponses = [reponse_texte("ok")]
    faux_client.bloquer()
    premier = _envoyer()
    assert premier.accepte
    assert faux_client.en_attente.wait(5)
    meme = _envoyer("Encore", premier.conversation_id)
    assert not meme.accepte and "Une réponse est en préparation" in meme.refus.texte
    autre = _envoyer("Autre visiteur", adresse="203.0.113.11")
    assert not autre.accepte and "très sollicité" in autre.refus.texte
    assert assistant.nouvelle_conversation(premier.conversation_id).startswith(
        "Une réponse est en préparation"
    )
    faux_client.liberer()
    attendre(premier.echange)
    assert assistant.nouvelle_conversation(premier.conversation_id) is None
    assert assistant.vue(premier.conversation_id).perdue


@pytest.mark.cas_limite
def test_limite_par_adresse_et_limite_du_site(base, faux_client, jour_fige, monkeypatch):
    """Cas : une adresse envoie trop de messages ; le site atteint son nombre du jour.

    Risque : un seul visiteur épuise le budget de la journée.
    """
    monkeypatch.setenv("EDT_ASSISTANT_MESSAGES_PAR_ADRESSE", "1")
    faux_client.reponses = [reponse_texte("ok")]
    _echanger()
    refuse = _envoyer("Encore")
    assert not refuse.accepte and "limite de 1 messages pour aujourd'hui" in refuse.refus.texte
    monkeypatch.setenv("EDT_ASSISTANT_MESSAGES_PAR_ADRESSE", "20")
    monkeypatch.setenv("EDT_ASSISTANT_MESSAGES_PAR_JOUR", "1")
    refuse = _envoyer("Autre", adresse="203.0.113.12")
    assert not refuse.accepte and "limite du jour" in refuse.refus.texte
    assert len(faux_client.appels) == 1


@pytest.mark.cas_limite
def test_cle_refusee_met_l_assistant_en_pause(base, faux_client, jour_fige, monkeypatch, caplog):
    """Cas : le service refuse la clé (401).

    Risque : chaque visiteur relance un appel voué à l'échec ; la clé apparaît dans le journal.
    """
    faux_client.reponses = [erreur_api(anthropic.AuthenticationError, 401)]
    with caplog.at_level(logging.DEBUG):
        premier = _echanger()
    assert (
        assistant.vue(premier.conversation_id)
        .tours[-1]
        .texte.startswith("L'assistant est momentanément indisponible")
    )
    refuse = _envoyer("Encore", premier.conversation_id)
    assert not refuse.accepte and "momentanément indisponible" in refuse.refus.texte
    assert len(faux_client.appels) == 1
    assert CLE_FACTICE not in caplog.text


@pytest.mark.cas_limite
def test_identifiant_introuvable_par_le_sdk(base, cle_factice, jour_fige, monkeypatch):
    """Cas : la variable de clé est posée mais le SDK ne construit pas le client.

    Risque : une trace dans le fil de l'échange et une attente sans fin dans le panneau.
    """

    def sans_identifiant(reglages):
        raise anthropic.CredentialsError("aucun identifiant")

    monkeypatch.setattr(module_client, "creer_client", sans_identifiant)
    resultat = _echanger()
    assert "configuration est à corriger" in resultat.echange.tour.texte


@pytest.mark.cas_limite
def test_erreur_inattendue_dans_le_fil_rendue_en_bulle(
    base, faux_client, jour_fige, monkeypatch, caplog
):
    """Cas : une exception non prévue dans le fil de l'échange.

    Risque : un fil mort, un panneau qui attend indéfiniment, la clé dans la trace.
    """
    faux_client.reponses = [ZeroDivisionError(f"bogue avec {CLE_FACTICE[:3]}")]
    with caplog.at_level(logging.ERROR):
        resultat = _echanger()
    tour = resultat.echange.tour
    assert tour.role is RoleTour.ERREUR and tour.reference == resultat.echange.reference
    assert "interrompu par une erreur inattendue" in caplog.text
    assert CLE_FACTICE not in caplog.text


@pytest.mark.cas_limite
def test_un_message_refuse_ne_cree_pas_de_conversation(base, faux_client, jour_fige, monkeypatch):
    """Cas : une adresse au-delà de sa limite envoie des messages sans cookie, à la chaîne.

    Risque : chaque refus crée une conversation vide ; le registre se remplit et évince les
    conversations des autres visiteurs, propositions en attente comprises, sans aucun appel.
    """
    monkeypatch.setenv("EDT_ASSISTANT_MESSAGES_PAR_ADRESSE", "1")
    faux_client.reponses = [reponse_texte("ok")]
    _echanger()
    nombre = assistant._etat.registre.nombre()
    for _ in range(5):
        refus = _envoyer("Encore")
        assert not refus.accepte and refus.conversation_id is None
    monkeypatch.setenv("EDT_ASSISTANT_MESSAGES_PAR_JOUR", "1")
    refus = _envoyer("Autre adresse", adresse="203.0.113.99")
    assert not refus.accepte and refus.conversation_id is None
    assert assistant._etat.registre.nombre() == nombre
    assert len(faux_client.appels) == 1


@pytest.mark.cas_limite
def test_une_proposition_en_attente_n_est_jamais_evincee(
    jeu_minimal, faux_client, jour_fige, monkeypatch
):
    """Cas : la mémoire est pleine ; la conversation de la démonstratrice a une proposition
    en attente ; un autre visiteur commence une conversation.

    Risque : au clic, « Cette proposition n'existe plus » : la conversation a été évincée.
    """
    monkeypatch.setenv("EDT_ASSISTANT_CONVERSATIONS_MAX", "1")
    conversation, proposition = _proposer(faux_client, "proposer_regle", REGLE_CM)
    faux_client.reponses = [reponse_texte("ok")]
    refus = _envoyer("Autre visiteur", adresse="203.0.113.50")
    assert not refus.accepte and "très sollicité" in refus.refus.texte
    assert refus.conversation_id is None
    assert assistant.confirmer(conversation, proposition.jeton).proposition.etat is (
        EtatProposition.CONFIRMEE
    )
    accepte = _envoyer("Autre visiteur", adresse="203.0.113.50")  # plus rien en attente
    assert accepte.accepte
    attendre(accepte.echange)


@pytest.mark.cas_limite
def test_conversations_bornees_par_adresse(base, faux_client, jour_fige, monkeypatch):
    """Cas : une même adresse ouvre conversation sur conversation (cookies effacés).

    Risque : une seule adresse occupe toute la mémoire et évince les autres visiteurs.
    """
    monkeypatch.setenv("EDT_ASSISTANT_CONVERSATIONS_PAR_ADRESSE", "2")
    faux_client.reponses = [reponse_texte(str(i)) for i in range(4)]
    autre = _echanger("Autre visiteur", adresse="203.0.113.60")
    premieres = [_echanger(f"Conversation {i}") for i in range(3)]
    assert assistant.vue(premieres[0].conversation_id).perdue  # sa plus ancienne, pas une autre
    assert not assistant.vue(premieres[2].conversation_id).perdue
    assert not assistant.vue(autre.conversation_id).perdue


@pytest.mark.cas_limite
def test_conversations_expirees_et_place_limitee(base, faux_client, jour_fige, monkeypatch):
    """Cas : une conversation inactive depuis plus de 30 minutes ; la mémoire est pleine.

    Risque : la mémoire du serveur grossit sans fin avec les visiteurs.
    """
    instant = {"t": 0.0}
    monkeypatch.setattr(temps, "maintenant", lambda: instant["t"])
    monkeypatch.setenv("EDT_ASSISTANT_CONVERSATIONS_MAX", "1")
    faux_client.reponses = [reponse_texte("un"), reponse_texte("deux")]
    premiere = _echanger()
    seconde = _echanger("Autre visiteur")  # place limitée : la première est retirée
    assert assistant.vue(premiere.conversation_id).perdue
    instant["t"] += 31 * 60
    assert assistant.vue(seconde.conversation_id).perdue


# --------------------------------------------------------------------------- #
# Propositions
# --------------------------------------------------------------------------- #


def _proposition_regle(faux_client):
    faux_client.reponses = [
        reponse_outil("proposer_regle", REGLE_CM),
        reponse_texte("Je vous propose une règle."),
    ]
    resultat = _echanger("Pas de CM le mercredi après-midi.")
    (proposition,) = assistant.vue(resultat.conversation_id).tours[-1].propositions
    return resultat.conversation_id, proposition


def test_proposition_confirmee_une_seule_fois(jeu_minimal, faux_client, jour_fige):
    avant = _nombre(m.Regle)
    conversation, proposition = _proposition_regle(faux_client)
    assert proposition.en_attente and _nombre(m.Regle) == avant
    resultat = assistant.confirmer(conversation, proposition.jeton)
    assert resultat.proposition.etat is EtatProposition.CONFIRMEE
    assert resultat.proposition.compte_rendu.startswith("Enregistrée : règle R005, active.")
    assert resultat.proposition.lien.adresse.endswith("&q=R005")
    with session_base() as session:
        regle = regles.lire(session, jeu_minimal.annee_scolaire_id, "R005")
    assert regle.regle.origine is OrigineRegle.ASSISTANT and regle.etat is EtatRegle.ACTIVE
    again = assistant.confirmer(conversation, proposition.jeton)
    assert again.message.startswith("Déjà confirmée") and _nombre(m.Regle) == avant + 1


def test_proposition_refusee_et_suite_donnee_au_modele(jeu_minimal, faux_client, jour_fige):
    avant = _nombre(m.Regle)
    conversation, proposition = _proposition_regle(faux_client)
    resultat = assistant.refuser(conversation, proposition.jeton)
    assert resultat.proposition.etat is EtatProposition.REFUSEE
    assert resultat.proposition.compte_rendu == "Refusée : rien n'a été enregistré."
    assert _nombre(m.Regle) == avant
    assert (
        assistant.confirmer(conversation, proposition.jeton).proposition.etat
        is EtatProposition.REFUSEE
    )
    faux_client.reponses = [reponse_texte("Entendu.")]
    _echanger("Et maintenant ?", conversation)
    systeme = faux_client.appels[-1]["messages"][-1]
    assert systeme["role"] == "system" and "P1 refusée" in systeme["content"]


@pytest.mark.cas_limite
def test_confirmation_devenue_impossible(jeu_minimal, faux_client, jour_fige):
    """Cas : entre la proposition et le clic, quelqu'un enregistre une règle qui fait doublon.

    Risque : deux réglages contradictoires actifs, ou une erreur brute.
    """
    regle = {
        "type": "PAUSE_DEJEUNER",
        "portee": {"nature": "groupe", "cible": "A1-G2"},
        "parametres": {"mode": "fixe", "debut": "12:00", "fin": "13:00"},
        "niveau": None,
        "periode": {"semestre": "S1"},
        "commentaire": None,
    }
    faux_client.reponses = [
        reponse_outil("proposer_regle", regle),
        reponse_texte("Je vous propose."),
    ]
    resultat = _echanger()
    (proposition,) = assistant.vue(resultat.conversation_id).tours[-1].propositions
    with session_base() as session:
        regles.creer(session, {**regle, "annee": "2026-2027"})
    avant = _nombre(m.Regle)
    confirmee = assistant.confirmer(resultat.conversation_id, proposition.jeton).proposition
    assert confirmee.etat is EtatProposition.ECHOUEE
    assert confirmee.compte_rendu.startswith("Impossible d'enregistrer :")
    assert confirmee.compte_rendu.endswith("Rien n'a été enregistré.")
    assert _nombre(m.Regle) == avant


@pytest.mark.cas_limite
def test_base_occupee_a_la_confirmation(
    jeu_minimal, faux_client, jour_fige, delai_verrou_court, verrou_ecriture
):
    """Cas : un autre processus tient la base au moment du clic sur Confirmer.

    Risque : proposition perdue ou enregistrée à moitié ; ici rien n'est écrit, la carte reste
    à confirmer.
    """
    conversation, proposition = _proposition_regle(faux_client)
    avant = _nombre(m.Regle)
    with verrou_ecriture():
        resultat = assistant.confirmer(conversation, proposition.jeton)
    assert resultat.message == "Base occupée : rien n'a été enregistré, réessayez."
    assert resultat.proposition.etat is EtatProposition.EN_ATTENTE and _nombre(m.Regle) == avant
    assert (
        assistant.confirmer(conversation, proposition.jeton).proposition.etat
        is EtatProposition.CONFIRMEE
    )


def test_indisponibilite_confirmee(jeu_minimal, faux_client, jour_fige):
    avant = _nombre(m.Indisponibilite)
    faux_client.reponses = [
        reponse_outil(
            "proposer_indisponibilite",
            {
                "nature": "enseignant",
                "code": "ENS002",
                "forme": "recurrente",
                "jour": "mercredi",
                "date_debut": None,
                "date_fin": None,
                "moment": "matin",
                "debut": None,
                "fin": None,
                "motif": None,
            },
        ),
        reponse_texte("Je vous propose une indisponibilité."),
    ]
    resultat = _echanger("M. Bernard n'est pas disponible le mercredi matin.")
    (proposition,) = assistant.vue(resultat.conversation_id).tours[-1].propositions
    confirmee = assistant.confirmer(resultat.conversation_id, proposition.jeton).proposition
    assert confirmee.etat is EtatProposition.CONFIRMEE, confirmee.compte_rendu
    assert confirmee.lien.adresse.startswith("/indisponibilites/")
    assert _nombre(m.Indisponibilite) == avant + 1


def test_proposition_d_un_echange_en_echec_abandonnee(jeu_minimal, faux_client, jour_fige):
    faux_client.reponses = [reponse_outil("proposer_regle", REGLE_CM), message([], "refusal")]
    resultat = _echanger()
    vue = assistant.vue(resultat.conversation_id)
    assert vue.tours[-1].role is RoleTour.ERREUR and vue.tours[-1].propositions == []


def _proposer(faux_client, outil, entree, texte="Je vous propose.") -> tuple[str, object]:
    faux_client.reponses = [reponse_outil(outil, entree), reponse_texte(texte)]
    resultat = _echanger()
    (proposition,) = assistant.vue(resultat.conversation_id).tours[-1].propositions
    return resultat.conversation_id, proposition


def _refusee_sans_ecriture(conversation, proposition, modele, avant) -> str:
    resultat = assistant.confirmer(conversation, proposition.jeton).proposition
    assert resultat.etat is EtatProposition.ECHOUEE, resultat.compte_rendu
    assert resultat.compte_rendu.endswith("Rien n'a été enregistré.")
    assert _nombre(modele) == avant
    return resultat.compte_rendu


@pytest.mark.cas_limite
def test_confirmer_refuse_un_enseignant_qui_n_est_plus_celui_de_la_carte(
    jeu_minimal, faux_client, jour_fige
):
    """Cas : la carte vise M. Bernard (ENS002) ; avant le clic, un visiteur recharge un
    établissement fictif et l'identifiant numérique de M. Bernard désigne une autre personne,
    qui garde le même identifiant métier.

    Risque : « Confirmée » sur la carte de M. Bernard, et l'indisponibilité écrite pour un autre.
    """
    conversation, proposition = _proposer(
        faux_client,
        "proposer_indisponibilite",
        {
            "nature": "enseignant",
            "code": "ENS002",
            "forme": "recurrente",
            "jour": "mercredi",
            "date_debut": None,
            "date_fin": None,
            "moment": "matin",
            "debut": None,
            "fin": None,
            "motif": None,
        },
    )
    with session_base() as session:
        bernard = session.scalar(select(m.Enseignant).where(m.Enseignant.identifiant == "ENS002"))
        bernard.civilite, bernard.nom, bernard.prenom = "Mme", "Dumas", "Lucie"
    avant = _nombre(m.Indisponibilite)
    message_refus = _refusee_sans_ecriture(conversation, proposition, m.Indisponibilite, avant)
    assert "ont changé depuis la proposition" in message_refus and "ENS002" in message_refus


@pytest.mark.cas_limite
def test_confirmer_refuse_une_salle_remplacee(version_minimale, faux_client, jour_fige):
    """Cas : la salle de la carte a été supprimée puis une autre salle a pris son identifiant
    (rechargement), ou elle a changé de code.

    Risque : l'indisponibilité posée sur une autre salle que celle de la carte.
    """
    conversation, proposition = _proposer(
        faux_client,
        "proposer_indisponibilite",
        {
            "nature": "salle",
            "code": "N204",
            "forme": "datee",
            "jour": None,
            "date_debut": "2026-10-06",
            "date_fin": None,
            "moment": "journee",
            "debut": None,
            "fin": None,
            "motif": None,
        },
    )
    with session_base() as session:
        session.scalar(select(m.Salle).where(m.Salle.code == "N204")).code = "N299"
    avant = _nombre(m.Indisponibilite)
    assert "N204" in _refusee_sans_ecriture(conversation, proposition, m.Indisponibilite, avant)


@pytest.mark.cas_limite
def test_confirmer_refuse_une_regle_qui_a_change(jeu_minimal, faux_client, jour_fige):
    """Cas : la carte propose de désactiver R001 ; avant le clic, la règle R001 a été remplacée
    (rechargement d'un établissement fictif) par une règle de même code et d'un autre contenu.

    Risque : la carte confirme « Désactiver R001 » et désactive une autre règle.
    """
    conversation, proposition = _proposer(
        faux_client, "proposer_etat_regle", {"code": "R001", "etat": "inactive"}
    )
    with session_base() as session:
        regles.supprimer(session, jeu_minimal.annee_scolaire_id, "R001")
        regles.creer(
            session,
            {
                "code": "R001",
                "type": "JOURS_TRAVAILLES",
                "portee": {"nature": "groupe", "cible": "A1-G2"},
                "parametres": {"jours": ["lundi", "mardi"]},
                "annee": "2026-2027",
            },
        )
    avant = _nombre(m.Regle)
    _refusee_sans_ecriture(conversation, proposition, m.Regle, avant)
    with session_base() as session:
        assert regles.lire(session, jeu_minimal.annee_scolaire_id, "R001").etat is EtatRegle.ACTIVE


@pytest.mark.cas_limite
def test_confirmer_refuse_une_modification_sur_une_regle_modifiee_entre_temps(
    jeu_minimal, faux_client, jour_fige
):
    """Cas : la carte propose « Modifier R002 » (avant, après) ; avant le clic, quelqu'un
    modifie R002 à l'écran.

    Risque : les changements fusionnés sur l'état courant donnent une règle que la carte
    n'a jamais montrée.
    """
    conversation, proposition = _proposer(
        faux_client,
        "proposer_modification_regle",
        {
            "code": "R002",
            "portee": None,
            "parametres": {"duree": 90},
            "niveau": None,
            "periode": None,
            "commentaire": None,
        },
    )
    with session_base() as session:
        regles.modifier(
            session, jeu_minimal.annee_scolaire_id, "R002", {"parametres": {"debut": "11:30"}}
        )
        phrase = regles.lire(session, jeu_minimal.annee_scolaire_id, "R002").phrase
    _refusee_sans_ecriture(conversation, proposition, m.Regle, _nombre(m.Regle))
    with session_base() as session:
        assert regles.lire(session, jeu_minimal.annee_scolaire_id, "R002").phrase == phrase


@pytest.mark.cas_limite
def test_confirmer_refuse_une_nouvelle_regle_dont_la_phrase_a_change(
    jeu_minimal, faux_client, jour_fige
):
    """Cas : la carte propose une règle pour « la classe A1-G2 » ; avant le clic, le groupe
    A1-G2 a changé de nature (rechargement).

    Risque : une règle enregistrée dont la phrase n'est pas celle que l'utilisateur a lue.
    """
    regle = {**REGLE_CM, "portee": {"nature": "groupe", "cible": "A1-G2"}}
    conversation, proposition = _proposer(faux_client, "proposer_regle", regle)
    assert proposition.phrase.startswith("La classe A1-G2")
    with session_base() as session:
        groupe = session.scalar(select(m.Groupe).where(m.Groupe.code == "A1-G2"))
        groupe.nature = "transverse"
        groupe.parent_id = None
    _refusee_sans_ecriture(conversation, proposition, m.Regle, _nombre(m.Regle))


def test_proposition_inconnue(base):
    assert assistant.confirmer("inconnue", "jeton").message.startswith(
        "Cette proposition n'existe plus"
    )
    assert assistant.refuser(None, "jeton").proposition is None


# --------------------------------------------------------------------------- #
# Ligne de commande
# --------------------------------------------------------------------------- #


def test_essai_et_reponse_synchrone(base, faux_client, jour_fige):
    faux_client.reponses = [reponse_texte("prêt", input_tokens=7712, output_tokens=41)]
    essai = assistant.essayer()
    assert essai.reussi and essai.arret == "end_turn" and essai.modele == "claude-opus-5-5"
    assert essai.consommation.cout == 7712 * 4 + 41 * 20
    assert faux_client.appels[0]["max_tokens"] == 256 and faux_client.options[0]["max_retries"] == 0
    faux_client.reponses = [
        reponse_outil("proposer_regle", REGLE_CM),
        reponse_texte("Je vous propose."),
    ]
    reponse = assistant.repondre("Pas de CM le mercredi après-midi.")
    assert reponse.reussi and reponse.texte == "Je vous propose." and len(reponse.propositions) == 1
    assert assistant.consommation_du_jour().appels == 3
