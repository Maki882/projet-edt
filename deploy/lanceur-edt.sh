#!/usr/bin/env bash
# =============================================================================
# deploy/lanceur-edt.sh — installé par installer-serveur.sh sous /usr/local/bin/edt.
#
# Lance une commande de l'application EDT en production, exactement comme le
# service : version en service (/opt/edt/courant), utilisateur edt, variables
# de /etc/edt/edt.env lues par systemd. Sur le serveur :
#   sudo edt --help                          liste des commandes
#   sudo edt doctor                          diagnostic (base, journaux, OR-Tools)
#   sudo edt jeu --taille grand --graine 0   charge le jeu de données fictif
#   sudo edt verifier                        vérifie l'emploi du temps
# Depuis le poste : ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo edt doctor'
#
# Le code de sortie est celui de la commande. Lancée autrement (python du venv
# à la main, sans sudo...), une commande utiliserait une AUTRE base sans le
# dire : ce lanceur refuse donc tout compte autre que root ou ubuntu via sudo.
# =============================================================================
set -euo pipefail

readonly COURANT="/opt/edt/courant"
readonly FICHIER_ENV="/etc/edt/edt.env"
readonly UTILISATEUR_APP="edt"
readonly COMPTES_AUTORISES=" root ubuntu "

if [ "$EUID" -ne 0 ] || [[ "$COMPTES_AUTORISES" != *" ${SUDO_USER:-root} "* ]]; then
  echo "edt : à lancer en root ou par ubuntu avec sudo, par exemple « sudo edt ${1:-doctor} »." >&2
  exit 2
fi
for chemin in "$COURANT/venv/bin/python" "$FICHIER_ENV"; do
  if [ ! -e "$chemin" ]; then
    echo "edt : $chemin introuvable : serveur non installé ou aucune version déployée (docs/deploiement.md)." >&2
    exit 1
  fi
done

# Terminal interactif : --pty transmet Ctrl+C à la commande ; sinon --pipe.
if [ -t 0 ] && [ -t 1 ]; then mode="--pty"; else mode="--pipe"; fi
exec systemd-run --quiet --wait --collect "$mode" --service-type=exec \
  --uid="$UTILISATEUR_APP" --gid="$UTILISATEUR_APP" -p UMask=0027 \
  -p EnvironmentFile="$FICHIER_ENV" -p WorkingDirectory="$COURANT/code" \
  -E PYTHONUNBUFFERED=1 "$COURANT/venv/bin/python" -m edt "$@"
