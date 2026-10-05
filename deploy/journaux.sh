#!/usr/bin/env bash
# =============================================================================
# deploy/journaux.sh — suit en direct les journaux de l'application EDT.
#
# À lancer depuis le POSTE DU DÉVELOPPEUR (Git Bash) ; Ctrl+C pour arrêter :
#   deploy/journaux.sh            tout : journal du service + edt.log + erreurs nginx
#   deploy/journaux.sh service    journal systemd du service edt (sortie d'uvicorn)
#   deploy/journaux.sh appli      fichier de l'application /var/log/edt/edt.log
#   deploy/journaux.sh nginx      accès et erreurs nginx du site
#   deploy/journaux.sh calcul     journal du calcul d'emploi du temps le plus récent
#
# Paramètres : EDT_SSH_HOTE, EDT_SSH_UTILISATEUR, EDT_SSH_CLE (deploy/commun.sh).
# =============================================================================
set -euo pipefail
set -o errtrace

DOSSIER_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/commun.sh
. "$DOSSIER_DEPLOY/commun.sh"
trap 'sur_erreur $? $LINENO "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR

MODE="${1:-tout}"

# Scripts exécutés en root sur le serveur, selon le mode choisi.
case "$MODE" in
  tout)
    SCRIPT_DISTANT='set -euo pipefail
journalctl -u edt -f -n 30 -o short-iso --no-pager &
pid_journal=$!
trap "kill $pid_journal" EXIT
tail -n 30 -F /var/log/edt/edt.log /var/log/nginx/edt-erreurs.log'
    ;;
  service)
    SCRIPT_DISTANT='set -euo pipefail
exec journalctl -u edt -f -n 50 -o short-iso --no-pager'
    ;;
  appli)
    SCRIPT_DISTANT='set -euo pipefail
exec tail -n 50 -F /var/log/edt/edt.log'
    ;;
  nginx)
    SCRIPT_DISTANT='set -euo pipefail
exec tail -n 20 -F /var/log/nginx/edt-acces.log /var/log/nginx/edt-erreurs.log'
    ;;
  calcul)
    SCRIPT_DISTANT='set -euo pipefail
dernier=""
for fichier in /var/log/edt/generations/*.log; do
  [ -e "$fichier" ] || continue
  if [ -z "$dernier" ] || [ "$fichier" -nt "$dernier" ]; then dernier="$fichier"; fi
done
if [ -z "$dernier" ]; then
  echo "Aucun journal de calcul dans /var/log/edt/generations/ (aucun calcul lancé ?)." >&2
  exit 1
fi
echo "Journal du calcul le plus récent : $dernier"
exec tail -n 50 -F "$dernier"'
    ;;
  -h | --help)
    sed -n '3,12p' "${BASH_SOURCE[0]}"
    exit 0
    ;;
  *)
    erreur "mode inconnu : « $MODE » (tout, service, appli, nginx ou calcul)."
    exit 2
    ;;
esac

etape "Contrôles locaux et connexion à $SSH_CIBLE"
verifier_poste_local
verifier_connexion

etape "Journaux en direct (mode « $MODE ») — Ctrl+C pour arrêter"
# -t : terminal distant, pour que Ctrl+C arrête aussi les commandes sur le
# serveur. Le script est passé en argument, protégé par printf %q (le shell
# de connexion du compte ubuntu est bash).
CODE=0
ssh -t "${SSH_OPTIONS[@]}" "$SSH_CIBLE" "sudo -n bash -c $(printf '%q' "$SCRIPT_DISTANT")" || CODE=$?
case "$CODE" in
  0 | 130) info "Suivi terminé." ;;
  255) info "Suivi terminé (connexion fermée : Ctrl+C ou coupure réseau)." ;;
  *)
    erreur "le suivi des journaux s'est arrêté avec le code $CODE (voir le message ci-dessus)."
    exit "$CODE"
    ;;
esac
