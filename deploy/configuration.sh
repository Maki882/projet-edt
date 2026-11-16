#!/usr/bin/env bash
# =============================================================================
# deploy/configuration.sh — lecture des paramètres du serveur, chargée avec
# « source » par commun.sh (scripts du poste), installer-serveur.sh et
# activer-https.sh (scripts du serveur).
#
# Les paramètres propres à une installation (adresse du serveur, compte SSH,
# clé, nom de domaine) ne sont dans aucun fichier suivi par git : ils sont lus
# dans deploy/serveur.env, fichier local ignoré par git, créé à partir du
# modèle deploy/serveur.env.exemple. Une variable déjà présente dans
# l'environnement l'emporte sur le fichier :
#   EDT_SSH_HOTE=203.0.113.10 deploy/etat.sh
#
# Format du fichier : une ligne NOM=valeur par paramètre, sans espace autour
# du signe égal ; valeur éventuellement entre guillemets ; « ~/ » en tête de
# valeur désigne le dossier personnel ; lignes vides et lignes en # ignorées.
# Le fichier est LU, jamais exécuté : une ligne d'un autre format ou un nom
# inconnu arrête le script avec la ligne fautive.
# =============================================================================
set -euo pipefail

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "deploy/configuration.sh se charge avec « source » : lancez deploy/deployer.sh, deploy/etat.sh ou deploy/journaux.sh." >&2
  exit 2
fi

# Paramètres acceptés dans serveur.env (les autres noms sont refusés).
readonly PARAMETRES_SERVEUR=(EDT_SSH_HOTE EDT_SSH_UTILISATEUR EDT_SSH_CLE EDT_DOMAINE
  EDT_CERTBOT_EMAIL EDT_IP_PUBLIQUE)
FICHIER_CONFIGURATION=""

# Lit le fichier $1 s'il existe et définit chaque paramètre qui n'a pas déjà
# une valeur dans l'environnement. Un fichier absent n'est pas une erreur :
# exiger_parametres dit ensuite ce qui manque.
charger_configuration_serveur() {
  local fichier=$1 ligne nom valeur numero=0
  FICHIER_CONFIGURATION="$fichier"
  if [ ! -e "$fichier" ]; then return 0; fi
  if [ ! -r "$fichier" ]; then
    printf 'ERREUR : %s existe mais n'"'"'est pas lisible.\n' "$fichier" >&2
    exit 1
  fi
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    numero=$((numero + 1))
    ligne="${ligne%$'\r'}"                       # fichier enregistré sous Windows
    ligne="${ligne#"${ligne%%[![:space:]]*}"}"   # blancs de début de ligne
    case "$ligne" in '' | '#'*) continue ;; esac
    if ! [[ "$ligne" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
      printf 'ERREUR : %s, ligne %d illisible (attendu NOM=valeur) : « %s »\n' "$fichier" "$numero" "$ligne" >&2
      exit 1
    fi
    nom="${BASH_REMATCH[1]}"
    valeur="${BASH_REMATCH[2]}"
    valeur="${valeur%"${valeur##*[![:space:]]}"}"   # blancs de fin de ligne
    if [[ " ${PARAMETRES_SERVEUR[*]} " != *" $nom "* ]]; then
      printf 'ERREUR : %s, ligne %d : paramètre inconnu « %s » (paramètres acceptés : %s).\n' \
        "$fichier" "$numero" "$nom" "${PARAMETRES_SERVEUR[*]}" >&2
      exit 1
    fi
    if [[ "$valeur" =~ ^\"(.*)\"$ ]] || [[ "$valeur" =~ ^\'(.*)\'$ ]]; then
      valeur="${BASH_REMATCH[1]}"
    fi
    if [[ "$valeur" == "~/"* ]]; then valeur="$HOME/${valeur#\~/}"; fi
    if [ -n "${!nom:-}" ]; then continue; fi    # l'environnement l'emporte
    printf -v "$nom" '%s' "$valeur"
  done < "$fichier"
}

# Arrête le script, avant toute action, si un des paramètres nommés est vide.
exiger_parametres() {
  local nom manquants=()
  for nom in "$@"; do
    if [ -z "${!nom:-}" ]; then manquants+=("$nom"); fi
  done
  if [ "${#manquants[@]}" -eq 0 ]; then return 0; fi
  printf 'ERREUR : paramètre(s) manquant(s) : %s. Rien n'"'"'a été fait.\n' "${manquants[*]}" >&2
  if [ -e "$FICHIER_CONFIGURATION" ]; then
    printf 'Complétez %s (modèle commenté : deploy/serveur.env.exemple).\n' "$FICHIER_CONFIGURATION" >&2
  else
    printf 'Créez %s à partir du modèle deploy/serveur.env.exemple et renseignez-y ces valeurs ; sur le poste, à la racine du dépôt (puis, pour les scripts du serveur, recopier le dossier deploy/ : docs/deploiement.md, section 3) :\n  cp deploy/serveur.env.exemple deploy/serveur.env\n' "$FICHIER_CONFIGURATION" >&2
  fi
  printf 'Une variable d'"'"'environnement convient aussi, par exemple : %s=valeur %s\n' "${manquants[0]}" "$0" >&2
  exit 1
}
