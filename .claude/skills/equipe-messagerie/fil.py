"""Reconstitue le fil des messages échangés par une équipe d'agents.

Chaque agent lancé en arrière-plan laisse une transcription (un fichier JSONL, dont le chemin est
rendu au lancement de l'agent). Ce script y relève les messages envoyés avec l'outil
`SendMessage` et ceux reçus du pilote, puis les range par heure dans un document Markdown.
Les agents n'ont donc rien à tenir à jour : le fil vient de ce qu'ils ont réellement envoyé.

Usage :
    python fil.py --membre chef=<identifiant>=<transcription> \
                  --membre calcul=<identifiant>=<transcription> ... [--sortie fil.md]

`<identifiant>` est l'adresse de l'agent dans la messagerie ; `main` désigne le pilote.
Sans `--sortie`, le fil s'affiche.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

journal = logging.getLogger("fil")

OUTIL = "SendMessage"
PILOTE = "pilote"
# Début du texte par lequel l'environnement remet à un agent un message de la conversation
# principale, selon que l'agent travaillait ou était arrêté.
ENTETES_PILOTE = (
    "The coordinator sent a message while you were working:",
    "The coordinator sent a message:",
)
# Phrase que l'environnement ajoute après le message du pilote : elle n'en fait pas partie.
SUITE_PILOTE = "Address this before completing your current task."
# Fragment commun aux en-têtes ci-dessus : un message qui le contient sans commencer par un
# en-tête connu signale un format nouveau, à dire plutôt qu'à ignorer en silence.
INDICE_PILOTE = "sent a message"
# Préfixe d'un envoi que l'outil a refusé : le message n'est jamais arrivé.
MARQUE_REFUS = "(envoi refusé)"


class ErreurFil(Exception):
    """Erreur dont le message dit quoi corriger dans la commande ou dans les fichiers."""


@dataclass(frozen=True)
class Message:
    """Un message du fil : qui l'a écrit, pour qui, quand, et son texte."""

    heure: datetime
    de: str
    a: str
    texte: str

    @property
    def objet(self) -> str:
        """Première ligne non vide du message, qui en est le résumé."""
        for ligne in self.texte.splitlines():
            if ligne.strip():
                return ligne.strip()
        return "(message vide)"


def lire_membres(valeurs: list[str]) -> dict[str, tuple[str, Path]]:
    """Lit les arguments `--membre rôle=identifiant=transcription`."""
    membres: dict[str, tuple[str, Path]] = {}
    for valeur in valeurs:
        morceaux = valeur.split("=", 2)
        if len(morceaux) != 3 or not all(morceaux):
            raise ErreurFil(
                f"--membre « {valeur} » : la forme attendue est rôle=identifiant=transcription"
            )
        role, identifiant, chemin = morceaux
        if role in membres:
            raise ErreurFil(f"le rôle « {role} » est donné deux fois")
        fichier = Path(chemin)
        if not fichier.is_file():
            raise ErreurFil(f"transcription introuvable pour « {role} » : {fichier}")
        membres[role] = (identifiant, fichier)
    if not membres:
        raise ErreurFil("aucun membre : donner au moins un --membre rôle=identifiant=transcription")
    return membres


def _heure(valeur: object, fichier: Path, numero: int) -> datetime:
    if not isinstance(valeur, str):
        raise ErreurFil(f"{fichier}, ligne {numero} : message sans horodatage")
    try:
        return datetime.fromisoformat(valeur.replace("Z", "+00:00")).astimezone()
    except ValueError as erreur:
        raise ErreurFil(
            f"{fichier}, ligne {numero} : horodatage illisible « {valeur} »"
        ) from erreur


def _texte_recu(contenu: object) -> str | None:
    """Texte d'une entrée reçue par l'agent : une chaîne, ou les blocs de texte d'une liste."""
    if isinstance(contenu, str):
        return contenu
    if isinstance(contenu, list):
        morceaux = [
            str(bloc.get("text", ""))
            for bloc in contenu
            if isinstance(bloc, dict) and bloc.get("type") == "text"
        ]
        return "\n".join(morceaux) if morceaux else None
    return None


def _envois_refuses(entrees: list[dict]) -> dict[str, str]:
    """Identifiants des appels d'outil que l'environnement a refusés, avec le motif du refus."""
    refuses: dict[str, str] = {}
    for entree in entrees:
        contenu = (entree.get("message") or {}).get("content")
        if entree.get("type") != "user" or not isinstance(contenu, list):
            continue
        for bloc in contenu:
            if (
                isinstance(bloc, dict)
                and bloc.get("type") == "tool_result"
                and bloc.get("is_error")
                and bloc.get("tool_use_id")
            ):
                refuses[str(bloc["tool_use_id"])] = str(bloc.get("content", ""))[:200]
    return refuses


def messages_d_un_membre(role: str, fichier: Path, noms: dict[str, str]) -> list[Message]:
    """Messages envoyés par ce membre, et messages qu'il a reçus du pilote.

    Un envoi que l'outil a refusé (destinataire inconnu, par exemple) reste dans le fil, marqué
    « (envoi refusé) » : il n'est jamais arrivé, mais l'agent a cru l'envoyer.
    """
    entrees: list[tuple[int, dict]] = []
    illisibles = 0
    # `utf-8-sig` : une transcription enregistrée avec une marque d'ordre d'octets reste lisible.
    for numero, ligne in enumerate(fichier.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not ligne.strip():
            continue
        try:
            entree = json.loads(ligne)
        except json.JSONDecodeError:
            illisibles += 1
            continue
        if isinstance(entree, dict):
            entrees.append((numero, entree))
        else:
            illisibles += 1
    refuses = _envois_refuses([entree for _, entree in entrees])

    trouves: list[Message] = []
    for numero, entree in entrees:
        contenu = (entree.get("message") or {}).get("content")
        if entree.get("type") == "assistant" and isinstance(contenu, list):
            for bloc in contenu:
                if not (
                    isinstance(bloc, dict)
                    and bloc.get("type") == "tool_use"
                    and bloc.get("name") == OUTIL
                ):
                    continue
                appel = bloc.get("input") or {}
                destinataire = str(appel.get("to", "?"))
                texte = str(appel.get("message", ""))
                motif = refuses.get(str(bloc.get("id")))
                if motif is not None:
                    journal.warning(
                        "%s, ligne %d : envoi à « %s » refusé par l'outil (%s)",
                        fichier,
                        numero,
                        destinataire,
                        motif,
                    )
                    texte = f"{MARQUE_REFUS} {texte}"
                trouves.append(
                    Message(
                        heure=_heure(entree.get("timestamp"), fichier, numero),
                        de=role,
                        a=noms.get(destinataire, destinataire),
                        texte=texte,
                    )
                )
        elif entree.get("type") == "user":
            texte = _texte_recu(contenu)
            if texte is None:
                continue
            entete = next((e for e in ENTETES_PILOTE if texte.startswith(e)), None)
            if entete is None:
                if INDICE_PILOTE in texte[:200]:
                    journal.warning(
                        "%s, ligne %d : message qui semble venir du pilote, en-tête non reconnu ;"
                        " il n'est pas repris dans le fil",
                        fichier,
                        numero,
                    )
                continue
            trouves.append(
                Message(
                    heure=_heure(entree.get("timestamp"), fichier, numero),
                    de=PILOTE,
                    a=role,
                    texte=texte[len(entete) :].strip().removesuffix(SUITE_PILOTE).strip(),
                )
            )
    if illisibles:
        journal.warning("%s : %d ligne(s) illisible(s) ignorée(s)", fichier, illisibles)
    journal.info("%s : %d message(s) relevé(s) dans %s", role, len(trouves), fichier)
    return trouves


def fil(membres: dict[str, tuple[str, Path]]) -> list[Message]:
    """Tous les messages de l'équipe, du plus ancien au plus récent."""
    noms = {identifiant: role for role, (identifiant, _) in membres.items()}
    noms["main"] = PILOTE
    tous: list[Message] = []
    for role, (_, fichier) in membres.items():
        tous.extend(messages_d_un_membre(role, fichier, noms))
    return sorted(tous, key=lambda message: (message.heure, message.de, message.a))


def en_markdown(messages: list[Message]) -> str:
    """Le fil en Markdown : un tableau d'ensemble, puis chaque message en entier."""
    if not messages:
        return "# Fil des messages de l'équipe\n\nAucun message relevé.\n"
    lignes = [
        "# Fil des messages de l'équipe",
        "",
        f"{len(messages)} messages, du {messages[0].heure:%d/%m/%Y à %H:%M:%S} "
        f"au {messages[-1].heure:%d/%m/%Y à %H:%M:%S}.",
        "",
        "| N° | Heure | De | À | Objet |",
        "|---|---|---|---|---|",
    ]
    for numero, message in enumerate(messages, 1):
        objet = message.objet.replace("|", "/")
        lignes.append(
            f"| {numero} | {message.heure:%H:%M:%S} | {message.de} | {message.a} | {objet} |"
        )
    for numero, message in enumerate(messages, 1):
        # Le texte est cité ligne à ligne : un titre ou un bloc de code non fermé dans un
        # message ne déforme pas la suite du document.
        texte = message.texte.strip() or "(message vide)"
        lignes += ["", f"## {numero}. {message.de} à {message.a}, {message.heure:%H:%M:%S}", ""]
        lignes += [f"> {ligne}" if ligne.strip() else ">" for ligne in texte.splitlines()]
    return "\n".join(lignes) + "\n"


def principal(arguments: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Reconstitue le fil des messages d'une équipe.")
    analyseur.add_argument(
        "--membre",
        action="append",
        default=[],
        metavar="rôle=identifiant=transcription",
        help="un membre de l'équipe ; à répéter pour chacun",
    )
    analyseur.add_argument("--sortie", type=Path, help="fichier Markdown à écrire")
    options = analyseur.parse_args(arguments)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        messages = fil(lire_membres(options.membre))
    except ErreurFil as erreur:
        journal.error("%s", erreur)
        return 1
    texte = en_markdown(messages)
    if options.sortie:
        options.sortie.parent.mkdir(parents=True, exist_ok=True)
        options.sortie.write_text(texte, encoding="utf-8", newline="\n")
        journal.info("fil écrit : %s (%d messages)", options.sortie, len(messages))
    else:
        # La console de Windows n'écrit pas tous les caractères d'un message (flèches,
        # coches) : l'affichage passe en UTF-8 plutôt que d'échouer.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(texte)
    return 0


if __name__ == "__main__":
    sys.exit(principal())
