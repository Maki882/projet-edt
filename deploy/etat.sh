#!/usr/bin/env bash
# =============================================================================
# deploy/etat.sh — état de l'application EDT en production, en une commande.
#
# À lancer depuis le POSTE DU DÉVELOPPEUR (Git Bash) :
#   deploy/etat.sh
#
# Affiche : état du service et de nginx, version déployée et versions
# disponibles, derniers déploiements, réponse de /sante (sur le serveur),
# certificat HTTPS, espace disque et mémoire, taille de la base et des
# sauvegardes, 30 dernières lignes du journal du service, puis vérifie l'URL
# publique depuis ce poste. Ne modifie rien.
#
# Code de sortie : 0 si tout va bien, 1 si un problème a été détecté (lignes
# « !!! »), autre valeur si l'état n'a pas pu être lu.
# Paramètres : EDT_SSH_HOTE, EDT_SSH_UTILISATEUR, EDT_SSH_CLE, EDT_DOMAINE
# (voir deploy/commun.sh).
# =============================================================================
set -euo pipefail
set -o errtrace

DOSSIER_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/commun.sh
. "$DOSSIER_DEPLOY/commun.sh"
trap 'sur_erreur $? $LINENO "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR

etape "Contrôles locaux et connexion à $SSH_CIBLE"
verifier_poste_local
verifier_connexion

etape "État du serveur ($EDT_DOMAINE)"
CODE_DISTANT=0
# Partie exécutée sur le serveur, en root. Chaque vérification signale son
# résultat ; une vérification en échec n'empêche pas les suivantes.
executer_script_distant etat "$EDT_DOMAINE" <<'DISTANT' || CODE_DISTANT=$?
set -euo pipefail
# Erreur imprévue : on la signale avec sa ligne et on sort avec le code 3
# (distinct du code 1 qui signifie « problèmes détectés »).
trap 'echo "!!! erreur inattendue (ligne $LINENO de la partie serveur de etat.sh) : $BASH_COMMAND" >&2; exit 3' ERR
DOMAINE="$1"
PROBLEMES=0
titre() { printf '\n[%s] [serveur] --- %s ---\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
probleme() { printf '!!! %s\n' "$*"; PROBLEMES=$((PROBLEMES + 1)); }

titre "Services"
for unite in edt nginx; do
  if etat_unite="$(systemctl is-active "$unite")"; then
    echo "$unite : $etat_unite"
  else
    probleme "le service $unite n'est pas actif (état : $etat_unite)"
  fi
done
if systemctl status edt --no-pager --lines=0; then
  :
else
  code=$?
  # 3 = service arrêté, déjà signalé ci-dessus ; autre code = vraie erreur.
  if [ "$code" -ne 3 ]; then probleme "systemctl status edt a renvoyé le code $code"; fi
fi

titre "Version déployée"
if [ -L /opt/edt/courant ]; then
  cible="$(readlink -f /opt/edt/courant)"
  echo "/opt/edt/courant -> $cible"
  if [ -e "$cible/DEPLOIEMENT.txt" ]; then
    cat "$cible/DEPLOIEMENT.txt"
  else
    probleme "$cible/DEPLOIEMENT.txt absent"
  fi
else
  probleme "aucune version déployée (/opt/edt/courant absent) : lancez deploy/deployer.sh"
fi
echo
echo "Versions présentes sur le serveur (OK = mise en service réussie ; -- = en cours ou en échec) :"
for dossier in /opt/edt/versions/*/; do
  [ -e "$dossier" ] || continue
  if [ -e "${dossier}deploiement-reussi" ]; then marque="OK"; else marque="--"; fi
  nom="${dossier%/}"
  echo "  $marque ${nom##*/}"
done
if [ -e /var/log/edt/historique-deploiements.log ]; then
  echo
  echo "Derniers déploiements (date, version, commit, référence, auteur, résultat) :"
  tail -n 5 /var/log/edt/historique-deploiements.log
fi

titre "Santé de l'application (http://127.0.0.1:8000/sante, sans passer par nginx)"
if reponse="$(curl -sS --max-time 5 -w '\n(HTTP %{http_code})' http://127.0.0.1:8000/sante)"; then
  echo "$reponse"
  case "$reponse" in
    *"(HTTP 200)") ;;
    *) probleme "/sante ne répond pas 200" ;;
  esac
else
  probleme "/sante injoignable (code curl $?) : le service est-il démarré ?"
fi

titre "Certificat HTTPS ($DOMAINE)"
certificat="/etc/letsencrypt/live/$DOMAINE/fullchain.pem"
if [ -e "$certificat" ]; then
  if fin="$(openssl x509 -enddate -noout -in "$certificat")"; then
    echo "expire le : ${fin#notAfter=}"
    if ! openssl x509 -checkend 1209600 -noout -in "$certificat" > /dev/null; then
      probleme "le certificat expire dans moins de 14 jours : vérifiez le renouvellement (sudo certbot renew --dry-run)"
    fi
  else
    probleme "lecture du certificat $certificat impossible"
  fi
  if systemctl is-active --quiet certbot.timer; then
    echo "renouvellement automatique : certbot.timer actif"
  else
    probleme "certbot.timer inactif : le certificat ne sera pas renouvelé (relancez deploy/activer-https.sh)"
  fi
else
  echo "aucun certificat : le site est servi en HTTP seulement (voir deploy/activer-https.sh)"
fi

titre "Espace disque et mémoire"
df -h /
for dossier in /opt/edt/versions /var/lib/edt /var/lib/edt/sauvegardes /var/log/edt /var/cache/edt; do
  if [ -e "$dossier" ]; then du -sh "$dossier"; fi
done
free -h

titre "Base de données"
if [ -e /etc/edt/edt.env ] && url="$(grep -E '^EDT_DB_URL=' /etc/edt/edt.env | tail -n 1)"; then
  url="${url#EDT_DB_URL=}"
  base="/${url#sqlite:////}"
  echo "EDT_DB_URL=$url"
  if [ -e "$base" ]; then
    for fichier in "$base" "$base-wal" "$base-shm"; do
      if [ -e "$fichier" ]; then ls -lh "$fichier"; fi
    done
  else
    probleme "base absente : $base (normal avant le premier déploiement)"
  fi
else
  probleme "EDT_DB_URL introuvable dans /etc/edt/edt.env"
fi
nombre_sauvegardes=0
derniere=""
for fichier in /var/lib/edt/sauvegardes/*.db; do
  [ -e "$fichier" ] || continue
  nombre_sauvegardes=$((nombre_sauvegardes + 1))
  derniere="$fichier"
done
echo "sauvegardes : $nombre_sauvegardes (la plus récente par ordre de nom : ${derniere:-aucune})"

titre "30 dernières lignes du journal du service edt"
if ! journalctl -u edt -n 30 --no-pager -o short-iso; then
  probleme "lecture du journal impossible (journalctl)"
fi

titre "Bilan côté serveur"
if [ "$PROBLEMES" -eq 0 ]; then
  echo "aucun problème détecté."
  exit 0
fi
echo "$PROBLEMES problème(s) détecté(s) : voir les lignes « !!! » ci-dessus."
exit 1
DISTANT

case "$CODE_DISTANT" in
  0) ETAT_SERVEUR="ok" ;;
  1) ETAT_SERVEUR="problème(s) détecté(s)" ;;
  *)
    erreur "l'état du serveur n'a pas pu être lu entièrement (code $CODE_DISTANT)."
    exit "$CODE_DISTANT"
    ;;
esac

etape "URL publique, vue depuis ce poste"
if verifier_url_publique; then
  ETAT_PUBLIC="ok"
else
  ETAT_PUBLIC="en échec"
fi

etape "Bilan"
info "Serveur : $ETAT_SERVEUR. URL publique : $ETAT_PUBLIC."
if [ "$CODE_DISTANT" -ne 0 ] || [ "$ETAT_PUBLIC" != "ok" ]; then
  exit 1
fi
