#!/usr/bin/env bash
# =============================================================================
# deploy/journaux.sh — suit en direct les journaux de l'application EDT.
#
# À lancer depuis le POSTE DU DÉVELOPPEUR (Git Bash) ; Ctrl+C pour arrêter :
#   deploy/journaux.sh           service et application : journal systemd du
#                                service edt (uvicorn + messages de l'application,
#                                les mêmes que /var/log/edt/edt.log)
#   deploy/journaux.sh nginx     accès et erreurs nginx du site
#   deploy/journaux.sh calcul    journal du calcul d'emploi du temps le plus récent
#
# Paramètres : deploy/serveur.env (voir deploy/commun.sh).
# =============================================================================
set -euo pipefail
set -o errtrace

DOSSIER_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/commun.sh
. "$DOSSIER_DEPLOY/commun.sh"
trap 'sur_erreur $? $LINENO "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR

MODE="${1:-service}"
case "$MODE" in
  service) COMMANDE_DISTANTE='journalctl -u edt -f -n 50 -o short-iso --no-pager' ;;
  nginx) COMMANDE_DISTANTE='tail -n 20 -F /var/log/nginx/edt-acces.log /var/log/nginx/edt-erreurs.log' ;;
  calcul)
    COMMANDE_DISTANTE='set -euo pipefail
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
  -h | --help) sed -n '3,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
  *)
    erreur "mode inconnu : « $MODE » (service, nginx ou calcul)."
    exit 2
    ;;
esac

etape "Contrôles locaux et connexion au serveur"
verifier_poste_local
verifier_connexion

etape "Journaux en direct (mode « $MODE ») — Ctrl+C pour arrêter"
# -t : terminal distant, pour que Ctrl+C arrête aussi la commande sur le
# serveur. Le script est passé en argument, protégé par printf %q : le shell
# de connexion du compte SSH doit être bash (cas du compte par défaut des
# images Ubuntu).
CODE=0
ssh -t "${SSH_OPTIONS[@]}" "$SSH_CIBLE" "sudo -n bash -c $(printf '%q' "$COMMANDE_DISTANTE")" || CODE=$?
case "$CODE" in
  0 | 130 | 255) info "Suivi terminé (code $CODE : fin normale, Ctrl+C ou connexion fermée)." ;;
  *)
    erreur "le suivi des journaux s'est arrêté avec le code $CODE (voir le message ci-dessus)."
    exit "$CODE"
    ;;
esac
