#!/usr/bin/env bash
# =============================================================================
# deploy/commun.sh — paramètres et fonctions partagés par les scripts lancés
# depuis le POSTE DU DÉVELOPPEUR (Git Bash sous Windows) : deployer.sh,
# etat.sh et journaux.sh. Ce fichier n'est pas lancé seul : il est chargé
# (« source ») par ces scripts.
#
# Paramètres, surchargeables par variables d'environnement :
#   EDT_SSH_HOTE         adresse du serveur             (défaut 92.222.10.188)
#   EDT_SSH_UTILISATEUR  compte SSH, sudo sans mot de passe (défaut ubuntu)
#   EDT_SSH_CLE          clé privée SSH dédiée          (défaut ~/.ssh/edt_vps_ed25519)
#   EDT_DOMAINE          nom de domaine public          (défaut pts.jorund.xyz)
# Exemple : EDT_SSH_HOTE=203.0.113.7 deploy/etat.sh
# =============================================================================
# Déjà actif dans les scripts qui chargent ce fichier ; répété pour qu'il ne
# soit jamais utilisé sans.
set -euo pipefail

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  echo "deploy/commun.sh se charge avec « source » ; lancez plutôt deploy/deployer.sh, deploy/etat.sh ou deploy/journaux.sh." >&2
  exit 2
fi

EDT_SSH_HOTE="${EDT_SSH_HOTE:-92.222.10.188}"
EDT_SSH_UTILISATEUR="${EDT_SSH_UTILISATEUR:-ubuntu}"
EDT_SSH_CLE="${EDT_SSH_CLE:-$HOME/.ssh/edt_vps_ed25519}"
EDT_DOMAINE="${EDT_DOMAINE:-pts.jorund.xyz}"

# Options SSH : uniquement la clé dédiée, jamais de question interactive
# (BatchMode), délai de connexion court, détection des connexions mortes.
SSH_OPTIONS=(
  -i "$EDT_SSH_CLE"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o ServerAliveInterval=20
  -o ServerAliveCountMax=3
)
SSH_CIBLE="$EDT_SSH_UTILISATEUR@$EDT_SSH_HOTE"

# --- Messages horodatés ------------------------------------------------------
ETAPE="initialisation"
horodatage() { date '+%Y-%m-%d %H:%M:%S'; }
info()    { printf '[%s] %s\n' "$(horodatage)" "$*"; }
etape()   { ETAPE="$*"; printf '\n[%s] === %s ===\n' "$(horodatage)" "$*"; }
avertir() { printf '[%s] ATTENTION : %s\n' "$(horodatage)" "$*" >&2; }
erreur()  { printf '[%s] ERREUR : %s\n' "$(horodatage)" "$*" >&2; }

# Appelée par le piège ERR de chaque script :
#   trap 'sur_erreur $? $LINENO "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR
# Dit quelle étape a échoué, quelle commande, à quelle ligne de quel fichier.
sur_erreur() {
  local code=$1 ligne=$2 commande=$3 fichier=${4:-?}
  case "$commande" in
    return*) commande="une fonction appelée à cette ligne (cause dans le message juste au-dessus)" ;;
  esac
  if [ "${BASH_SUBSHELL:-0}" -gt 0 ]; then
    erreur "« $commande » a renvoyé le code $code (ligne $ligne de ${fichier##*/})."
    exit "$code"
  fi
  erreur "échec à l'étape « $ETAPE » : « $commande » a renvoyé le code $code (ligne $ligne de ${fichier##*/})."
  exit "$code"
}

# --- Accès au serveur --------------------------------------------------------
ssh_serveur() {
  ssh "${SSH_OPTIONS[@]}" "$SSH_CIBLE" "$@"
}

# Exécute sur le serveur, en root, le script bash lu sur l'entrée standard.
# Le script est d'abord lu en entier (« $(cat) ») puis passé à « bash -c » :
# ainsi aucune commande du script ne peut avaler la suite du script, et les
# numéros de ligne des messages d'erreur sont ceux du script.
# Les arguments doivent être des mots simples (sans espace ni caractère spécial).
executer_script_distant() {
  local nom=$1
  shift
  ssh_serveur "sudo -n bash -c \"\$(cat)\" $nom $*"
}

# Vérifie les outils du poste, la clé et les paramètres.
verifier_poste_local() {
  local outil
  for outil in ssh git curl sed tr; do
    if ! command -v "$outil" > /dev/null; then
      erreur "commande « $outil » introuvable : lancez ce script depuis Git Bash."
      exit 1
    fi
  done
  if [ ! -e "$EDT_SSH_CLE" ]; then
    erreur "clé SSH introuvable : $EDT_SSH_CLE (variable EDT_SSH_CLE)."
    exit 1
  fi
  if ! [[ "$EDT_DOMAINE" =~ ^[A-Za-z0-9.-]+$ ]]; then
    erreur "EDT_DOMAINE invalide : « $EDT_DOMAINE »."
    exit 1
  fi
}

# Vérifie la connexion SSH et le sudo sans mot de passe, avec un message clair
# pour chaque cas.
verifier_connexion() {
  local code=0
  ssh_serveur 'sudo -n true' || code=$?
  case "$code" in
    0) info "Connexion à $SSH_CIBLE et sudo : ok." ;;
    255)
      erreur "connexion SSH impossible à $SSH_CIBLE avec la clé $EDT_SSH_CLE. Vérifiez l'adresse et la clé, et qu'une connexion manuelle « ssh -i $EDT_SSH_CLE $SSH_CIBLE » fonctionne (la toute première fois, elle demande d'accepter l'empreinte du serveur)."
      exit 1
      ;;
    *)
      erreur "connecté, mais « sudo » demande un mot de passe ou est refusé (code $code) pour $EDT_SSH_UTILISATEUR : ces scripts ont besoin du sudo sans mot de passe."
      exit 1
      ;;
  esac
}

# Renvoie 0 si un certificat Let's Encrypt existe sur le serveur pour
# EDT_DOMAINE, 1 sinon. Quitte si la question ne peut pas être posée.
certificat_present() {
  local code=0
  ssh_serveur "sudo -n test -e /etc/letsencrypt/live/$EDT_DOMAINE/fullchain.pem" || code=$?
  case "$code" in
    0) return 0 ;;
    1) return 1 ;;
    *)
      erreur "impossible de savoir si un certificat existe sur le serveur (code $code)."
      exit 1
      ;;
  esac
}

# Vérification INFORMATIVE de l'URL publique, depuis ce poste : traverse le
# DNS, nginx et le certificat. Renvoie 1 en cas de problème, sans rien annuler.
verifier_url_publique() {
  local url code
  if certificat_present; then
    url="https://$EDT_DOMAINE/sante"
  else
    url="http://$EDT_DOMAINE/sante"
    info "Aucun certificat sur le serveur : vérification en HTTP (voir deploy/activer-https.sh)."
  fi
  if code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$url")"; then
    if [ "$code" = "200" ]; then
      info "URL publique $url : 200, ok."
      return 0
    fi
    avertir "URL publique $url : code HTTP $code au lieu de 200 (502 = nginx ne joint pas l'application ; 301 = redirection inattendue)."
  else
    avertir "URL publique $url injoignable depuis ce poste (code curl $?) : DNS, port fermé ou certificat invalide. Voir docs/deploiement.md, « Pannes classiques »."
  fi
  return 1
}
