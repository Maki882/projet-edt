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
#   sudo edt sauvegarder                     sauvegarde vérifiée de la base
# Depuis le poste : ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo edt doctor'
#
# Le code de sortie est celui de la commande. Lancée autrement (python du venv
# à la main, sans sudo...), une commande utiliserait une AUTRE base sans le
# dire : ce lanceur refuse donc tout compte autre que root ou ubuntu via sudo.
# Il refuse aussi de démarrer pendant un déploiement (verrou partagé).
# =============================================================================
set -euo pipefail

readonly COURANT="/opt/edt/courant"
readonly FICHIER_ENV="/etc/edt/edt.env"
readonly UTILISATEUR_APP="edt"
readonly COMPTES_AUTORISES=" root ubuntu "
# Même verrou que deployer-distant.sh, pris ici en partagé : refus pendant un
# déploiement (qui le prend en exclusif), et le déploiement attend la fin de
# cette commande au lieu de remplacer la base sous ses pieds.
readonly VERROU="/run/lock/edt-deploiement.lock"

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
# Le descripteur 9 reste ouvert dans systemd-run (exec) : le verrou est tenu
# jusqu'à la fin de la commande.
exec 9>> "$VERROU"
if ! flock -s -n 9; then
  echo "edt : un déploiement est en cours sur ce serveur ; relancez la commande quand il sera terminé (deploy/etat.sh)." >&2
  exit 1
fi

# Terminal interactif : --pty transmet Ctrl+C à la commande ; sinon --pipe.
if [ -t 0 ] && [ -t 1 ]; then mode="--pty"; else mode="--pipe"; fi
exec systemd-run --quiet --wait --collect "$mode" --service-type=exec \
  --uid="$UTILISATEUR_APP" --gid="$UTILISATEUR_APP" -p UMask=0027 \
  -p EnvironmentFile="$FICHIER_ENV" -p WorkingDirectory="$COURANT/code" \
  -E PYTHONUNBUFFERED=1 "$COURANT/venv/bin/python" -m edt "$@"
