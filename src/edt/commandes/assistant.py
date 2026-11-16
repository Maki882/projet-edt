"""Commande de l'assistant : `python -m edt assistant` (état, essai, question).

    python -m edt assistant                      # état, limites, consommation du jour
    python -m edt assistant --essai              # un appel d'essai au service
    python -m edt assistant --question "…"       # une question, réponse affichée

L'identifiant du service n'est jamais affiché : la commande dit seulement
quelle variable le porte. L'essai fait un appel avec exactement les
paramètres d'un échange (consignes, outils stricts, repli, effort) : il
vérifie la clé, le modèle, l'en-tête bêta et l'acceptation des schémas ; son
coût compte dans la consommation du jour. Les propositions d'une question
posée ici s'affichent mais ne se confirment que dans le panneau.

Codes de sortie : 0 l'assistant est utilisable (état), l'essai a réussi, la
question a reçu sa réponse ; 1 l'assistant n'est pas utilisable (non
configuré, désactivé, configuration invalide, clé refusée, modèle
introuvable, requête refusée, limite atteinte), message et remède affichés ;
2 erreur inattendue (trace dans le journal).
"""

from __future__ import annotations

import logging
import os
from typing import Annotated

import typer

from edt.services import assistant
from edt.services.assistant.boucle import Issue
from edt.services.assistant.configuration import ConfigurationAssistant, dollars

commandes = typer.Typer()
journal = logging.getLogger(__name__)

REMEDES = {
    Issue.CLE_REFUSEE: "La clé est refusée par le service (401 ou 403) : vérifier la clé "
    "installée sur le serveur, puis redémarrer le service.",
    Issue.FACTURATION: "La facturation du compte est refusée (402) : vérifier le compte du "
    "service.",
    Issue.MODELE_INTROUVABLE: "Le modèle est inconnu ou non autorisé pour ce compte : vérifier "
    "EDT_ASSISTANT_MODELE.",
    Issue.REQUETE_INVALIDE: "Le service refuse la requête (400 : en-tête bêta, schémas des outils "
    "ou paramètres) : le détail est dans le journal (edt.log).",
    Issue.IDENTIFIANT: "Le SDK ne trouve pas d'identifiant : vérifier la variable de clé dans "
    "l'environnement du service.",
}


def _avertissements(config: ConfigurationAssistant) -> list[str]:
    lignes = []
    if config.deux_cles:
        lignes.append(
            "Avertissement : ANTHROPIC_API_KEY et ANTHROPIC_AUTH_TOKEN sont posées toutes les "
            "deux ; le service utilise la clé d'API : retirer celle qui ne sert pas."
        )
    if os.environ.get("ANTHROPIC_LOG"):
        lignes.append(
            "Avertissement : ANTHROPIC_LOG est posée : le SDK journaliserait le détail de ses "
            "requêtes ; la retirer en production."
        )
    return lignes


def _etat(config: ConfigurationAssistant) -> None:
    libelles = {
        "disponible": "prêt",
        "non_configure": "non configuré",
        "desactive": "désactivé (EDT_ASSISTANT_ACTIF=non)",
        "configuration": "indisponible (configuration à corriger)",
    }
    typer.echo(f"Assistant : {libelles[config.raison.value]}. {config.raison_texte}")
    typer.echo(
        "Identifiant : "
        + (
            f"variable {config.variable_cle} présente (valeur jamais affichée)."
            if config.variable_cle
            else "aucune variable ANTHROPIC_API_KEY ni ANTHROPIC_AUTH_TOKEN."
        )
    )
    for erreur in config.erreurs:
        typer.echo(f"Erreur de configuration : {erreur}")
    for ligne in _avertissements(config):
        typer.echo(ligne)
    typer.echo(
        f"Modèle : {config.modele}, effort {config.effort}, {config.jetons_sortie_max} jetons de "
        "sortie au plus par appel."
    )
    typer.echo(
        f"Limites : {config.appels_max} appels et {config.delai_s} s par échange ; "
        f"{config.longueur_max} caractères par message ; {config.messages_par_conversation} "
        f"messages par conversation ; {config.messages_par_adresse} par adresse et "
        f"{config.messages_par_jour} par jour ; {config.simultanes} échanges en même temps."
    )
    consommation = assistant.consommation_du_jour()
    typer.echo(
        f"Aujourd'hui : {consommation.echanges} échange(s), {consommation.appels} appel(s), "
        f"{consommation.jetons} jetons, coût estimé {dollars(consommation.cout)} sur un budget "
        f"de {config.budget_jour_texte}."
    )


def _essai() -> int:
    config = assistant.configuration()
    for ligne in _avertissements(config):
        typer.echo(ligne)
    if not config.disponible:
        typer.echo(f"Essai impossible : {config.raison_texte}", err=True)
        for erreur in config.erreurs:
            typer.echo(f"  - {erreur}", err=True)
        return 1
    typer.echo(f"Identifiant : variable {config.variable_cle} présente (valeur jamais affichée).")
    resultat = assistant.essayer()
    c = resultat.consommation
    if resultat.reussi:
        typer.echo(
            f"Essai réussi en {resultat.duree_s:.1f} s : arrêt {resultat.arret}, modèle servi "
            f"{resultat.modele}, jetons d'entrée {c.entree + c.cache_lus + c.cache_ecrits} "
            f"(dont {c.cache_lus} lus en cache, {c.cache_ecrits} écrits en cache), sortie "
            f"{c.sortie}, coût estimé {dollars(c.cout)}, requête {resultat.requete or '-'}."
        )
        return 0
    typer.echo(f"Essai en échec : {resultat.message}", err=True)
    if resultat.issue in REMEDES:
        typer.echo(REMEDES[resultat.issue], err=True)
    return 1


def _question(texte: str) -> int:
    reponse = assistant.repondre(texte)
    if not reponse.reussi:
        typer.echo(f"Pas de réponse : {reponse.texte}", err=True)
        return 1
    typer.echo(reponse.texte)
    for proposition in reponse.propositions:
        typer.echo(
            f"Proposition {proposition.code} ({proposition.titre}) : {proposition.phrase} "
            "(à confirmer dans le panneau Assistant ; non confirmable en ligne de commande)."
        )
    for lien in reponse.liens:
        typer.echo(f"Lien : {lien.libelle} : {lien.adresse}")
    return 0


@commandes.command("assistant")
def commande_assistant(
    essai: Annotated[
        bool,
        typer.Option(
            "--essai", help="Fait un appel d'essai au service (coût compté dans le jour)."
        ),
    ] = False,
    question: Annotated[
        str | None,
        typer.Option("--question", help="Pose une question à l'assistant et affiche sa réponse."),
    ] = None,
) -> None:
    """État de l'assistant conversationnel (configuration, limites, consommation du jour)."""
    if essai and question:
        typer.echo("Choisir --essai ou --question, pas les deux.", err=True)
        raise typer.Exit(1)
    if essai:
        raise typer.Exit(_essai())
    if question is not None:
        raise typer.Exit(_question(question))
    config = assistant.configuration()
    _etat(config)
    raise typer.Exit(0 if config.disponible else 1)
