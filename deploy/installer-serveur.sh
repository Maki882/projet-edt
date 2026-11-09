#!/usr/bin/env bash
# =============================================================================
# deploy/installer-serveur.sh — prépare le serveur pour l'application EDT.
#
# À lancer SUR LE SERVEUR, en root, depuis une copie du dossier deploy/
# (docs/deploiement.md, « Première installation ») :
#   sudo bash ~/edt-installation/deploy/installer-serveur.sh
#   sudo bash ~/edt-installation/deploy/installer-serveur.sh --nginx-seulement
#
# IDEMPOTENT : à relancer après toute modification de edt.service, du lanceur
# ou des fichiers nginx du dépôt (deployer.sh le signale).
#   1. apt : nginx, Python (venv, en-têtes de compilation), sqlite3, curl ;
#   2. utilisateur système « edt » (sans shell, sans mot de passe) ;
#   3. dossiers /opt/edt (code + venvs, root), /var/lib/edt (base, edt),
#      /var/log/edt (journaux de l'application, edt), /var/log/edt-deploiements
#      (root), /etc/edt (root:edt), /var/cache/edt/pip (root) ;
#   4. /etc/edt/edt.env créé depuis edt.env.exemple s'il manque (jamais écrasé) ;
#   5. lanceur /usr/local/bin/edt (« sudo edt doctor ») et unité edt.service ;
#   6. sauvegarde quotidienne de la base : edt-sauvegarde.service et son
#      minuteur edt-sauvegarde.timer (activé) ;
#   7. nginx : pages 502 et 413, partie commune, site « edt » en HTTP, ou en
#      HTTPS si un certificat existe pour EDT_DOMAINE ; site par défaut
#      désactivé. --nginx-seulement ne fait que cette étape (activer-https.sh).
# Il ne touche NI à la configuration SSH NI au pare-feu.
# Paramètre : EDT_DOMAINE (défaut pts.jorund.xyz) :
#   sudo env EDT_DOMAINE=autre.exemple.fr bash .../installer-serveur.sh
# =============================================================================
set -euo pipefail
set -o errtrace

EDT_DOMAINE="${EDT_DOMAINE:-pts.jorund.xyz}"

DOSSIER_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DOSSIER_SCRIPT
readonly UTILISATEUR_APP="edt"
readonly UNITE="/etc/systemd/system/edt.service"
readonly FICHIER_ENV="/etc/edt/edt.env"
readonly SITE_NGINX="/etc/nginx/sites-available/edt"
readonly LIEN_SITE_NGINX="/etc/nginx/sites-enabled/edt"
readonly SITE_DEFAUT="/etc/nginx/sites-enabled/default"
readonly PARTIE_COMMUNE_NGINX="/etc/nginx/snippets/edt-application.conf"
readonly CERTIFICAT="/etc/letsencrypt/live/$EDT_DOMAINE/fullchain.pem"

ETAPE="démarrage"
horodatage() { date '+%Y-%m-%d %H:%M:%S'; }
info()    { printf '[%s] %s\n' "$(horodatage)" "$*"; }
etape()   { ETAPE="$*"; printf '\n[%s] === %s ===\n' "$(horodatage)" "$*"; }
avertir() { printf '[%s] ATTENTION : %s\n' "$(horodatage)" "$*" >&2; }
erreur()  { printf '[%s] ERREUR : %s\n' "$(horodatage)" "$*" >&2; }
echouer() { erreur "$*"; exit 1; }
sur_erreur() {
  local code=$1 ligne=$2 commande=$3
  if [ "$BASH_SUBSHELL" -gt 0 ]; then
    erreur "« $commande » a renvoyé le code $code (ligne $ligne de installer-serveur.sh)."
    exit "$code"
  fi
  erreur "échec à l'étape « $ETAPE » : « $commande » a renvoyé le code $code (ligne $ligne de installer-serveur.sh)."
  erreur "Le script est idempotent : corrigez la cause puis relancez-le."
  exit "$code"
}
trap 'sur_erreur $? $LINENO "$BASH_COMMAND"' ERR

# Interroge une URL jusqu'à obtenir l'un des codes HTTP attendus ($2, motif
# « 200|502 ») ; affiche le dernier code obtenu. Renvoie 1 au bout de $3 essais.
attendre_code() {
  local url=$1 attendus=$2 essais=$3 code=000 i
  for (( i = 1; i <= essais; i++ )); do
    if code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 -H "Host: $EDT_DOMAINE" "$url")"; then
      if [[ "$code" =~ ^($attendus)$ ]]; then echo "$code"; return 0; fi
    else
      code="pas de réponse"
    fi
    sleep 1
  done
  echo "$code"
  return 1
}

NGINX_SEULEMENT=0
case "${1:-}" in
  "") ;;
  --nginx-seulement) NGINX_SEULEMENT=1 ;;
  *) echouer "option inconnue : $1 (seule option : --nginx-seulement)" ;;
esac

# -----------------------------------------------------------------------------
etape "Contrôles"
if [ "$EUID" -ne 0 ]; then
  echouer "lancez ce script en root : sudo bash $0"
fi
if ! [[ "$EDT_DOMAINE" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echouer "EDT_DOMAINE invalide : « $EDT_DOMAINE »."
fi
for fichier in edt.service edt-sauvegarde.service edt-sauvegarde.timer edt.env.exemple \
  lanceur-edt.sh nginx-edt.conf nginx-edt-https.conf nginx-edt-application.conf \
  erreur-502.html erreur-413.html; do
  if [ ! -e "$DOSSIER_SCRIPT/$fichier" ]; then
    echouer "fichier $DOSSIER_SCRIPT/$fichier absent : copiez le dossier deploy/ complet sur le serveur."
  fi
done
info "Nom de domaine : $EDT_DOMAINE"

# -----------------------------------------------------------------------------
# Configuration nginx : HTTPS si le certificat existe, HTTP sinon. Si
# « nginx -t » refuse la nouvelle configuration, l'ancienne est remise.
installer_nginx() {
  etape "Configuration nginx"
  if ! command -v nginx > /dev/null; then
    echouer "nginx n'est pas installé : lancez d'abord ce script sans option."
  fi
  local modele="nginx-edt.conf" mode="HTTP" attendus="200|502" copie defaut_actif=0 code
  if [ -e "$CERTIFICAT" ]; then
    modele="nginx-edt-https.conf"; mode="HTTPS"; attendus="301"
  fi
  info "Mode : $mode (modèle $modele)"
  install -d -o root -g root -m 755 /var/www/edt-erreurs /var/www/certbot /etc/nginx/snippets

  copie="$(mktemp -d)"
  if [ -e "$SITE_NGINX" ]; then cp -p "$SITE_NGINX" "$copie/site"; fi
  if [ -e "$PARTIE_COMMUNE_NGINX" ]; then cp -p "$PARTIE_COMMUNE_NGINX" "$copie/commun"; fi
  if [ -e "$SITE_DEFAUT" ] || [ -L "$SITE_DEFAUT" ]; then defaut_actif=1; fi

  install -o root -g root -m 644 "$DOSSIER_SCRIPT/erreur-502.html" /var/www/edt-erreurs/erreur-502.html
  install -o root -g root -m 644 "$DOSSIER_SCRIPT/erreur-413.html" /var/www/edt-erreurs/erreur-413.html
  install -o root -g root -m 644 "$DOSSIER_SCRIPT/nginx-edt-application.conf" "$PARTIE_COMMUNE_NGINX"
  sed "s/@EDT_DOMAINE@/$EDT_DOMAINE/g" "$DOSSIER_SCRIPT/$modele" > "$SITE_NGINX.nouveau"
  chmod 644 "$SITE_NGINX.nouveau"
  mv -f "$SITE_NGINX.nouveau" "$SITE_NGINX"
  ln -sfn "$SITE_NGINX" "$LIEN_SITE_NGINX"
  if [ "$defaut_actif" = 1 ]; then
    rm -f "$SITE_DEFAUT"
    info "Site nginx par défaut désactivé (/etc/nginx/sites-available/default est conservé)."
  fi

  if ! nginx -t; then
    erreur "nginx refuse la nouvelle configuration (message ci-dessus) : retour à la précédente."
    if [ -e "$copie/site" ]; then cp -p "$copie/site" "$SITE_NGINX"; else rm -f "$SITE_NGINX" "$LIEN_SITE_NGINX"; fi
    if [ -e "$copie/commun" ]; then cp -p "$copie/commun" "$PARTIE_COMMUNE_NGINX"; else rm -f "$PARTIE_COMMUNE_NGINX"; fi
    if [ "$defaut_actif" = 1 ]; then ln -sfn /etc/nginx/sites-available/default "$SITE_DEFAUT"; fi
    rm -rf -- "$copie"
    if nginx -t; then
      echouer "configuration précédente remise en place (et valide). Corrigez $modele ou nginx-edt-application.conf puis relancez."
    fi
    echouer "la configuration précédente est elle aussi refusée par nginx -t : intervention manuelle nécessaire."
  fi
  rm -rf -- "$copie"

  systemctl enable nginx
  if systemctl is-active --quiet nginx; then
    systemctl reload nginx
  else
    systemctl start nginx
  fi
  # Le rechargement de nginx est asynchrone : on réessaie pendant 5 s.
  if code="$(attendre_code http://127.0.0.1/sante "$attendus" 5)"; then
    case "$code" in
      200) info "nginx répond sur le port 80 et l'application aussi (200)." ;;
      502) info "nginx répond sur le port 80 (502 : normal tant que l'application n'est pas déployée ou démarrée)." ;;
      301) info "nginx répond sur le port 80 par une redirection vers https://$EDT_DOMAINE (301), comme prévu." ;;
    esac
  else
    echouer "nginx ne répond pas comme prévu sur le port 80 (dernier résultat : $code ; attendu : $attendus). Port pris par un autre programme ? « sudo ss -ltnp 'sport = :80' »"
  fi
}

if [ "$NGINX_SEULEMENT" = 1 ]; then
  installer_nginx
  etape "Terminé (configuration nginx seulement)"
  exit 0
fi

# -----------------------------------------------------------------------------
etape "Paquets système (apt)"
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=l   # needrestart : lister seulement, ne redémarrer aucun service
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends \
  nginx python3 python3-venv python3-dev build-essential sqlite3 curl ca-certificates openssl \
  iproute2 procps
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echouer "le Python du système est trop ancien (3.11 minimum)."
fi

etape "Utilisateur système $UTILISATEUR_APP"
if id -u "$UTILISATEUR_APP" > /dev/null 2>&1; then
  info "L'utilisateur $UTILISATEUR_APP existe déjà."
else
  shell_nologin="$(command -v nologin)"
  useradd --system --user-group --home-dir /var/lib/edt --no-create-home \
    --shell "$shell_nologin" "$UTILISATEUR_APP"
  info "Utilisateur $UTILISATEUR_APP créé (sans shell, sans mot de passe)."
fi

etape "Dossiers"
install -d -o root -g root -m 755 /opt/edt /opt/edt/versions
install -d -o "$UTILISATEUR_APP" -g "$UTILISATEUR_APP" -m 750 /var/lib/edt /var/lib/edt/sauvegardes /var/log/edt
install -d -o root -g root -m 750 /var/log/edt-deploiements
install -d -o root -g "$UTILISATEUR_APP" -m 750 /etc/edt
install -d -o root -g root -m 700 /var/cache/edt /var/cache/edt/pip

etape "Fichier d'environnement $FICHIER_ENV"
if [ -e "$FICHIER_ENV" ]; then
  info "$FICHIER_ENV existe déjà : il est conservé tel quel."
  if diff -u "$FICHIER_ENV" "$DOSSIER_SCRIPT/edt.env.exemple"; then
    info "Il est identique au modèle edt.env.exemple."
  else
    code_diff=$?
    if [ "$code_diff" -ne 1 ]; then
      echouer "comparaison avec le modèle impossible (diff, code $code_diff)."
    fi
    info "Différences avec le modèle ci-dessus (« + » = modèle) : à reporter à la main si besoin."
  fi
else
  install -o root -g "$UTILISATEUR_APP" -m 640 "$DOSSIER_SCRIPT/edt.env.exemple" "$FICHIER_ENV"
  info "$FICHIER_ENV créé depuis le modèle."
fi
chown root:"$UTILISATEUR_APP" "$FICHIER_ENV"
chmod 640 "$FICHIER_ENV"

etape "Lanceur /usr/local/bin/edt et service systemd edt"
install -o root -g root -m 755 "$DOSSIER_SCRIPT/lanceur-edt.sh" /usr/local/bin/edt
unite_modifiee=1
if [ -e "$UNITE" ] && cmp -s "$DOSSIER_SCRIPT/edt.service" "$UNITE"; then
  unite_modifiee=0
fi
install -o root -g root -m 644 "$DOSSIER_SCRIPT/edt.service" "$UNITE"
systemctl daemon-reload
systemctl enable edt
if [ ! -L /opt/edt/courant ]; then
  info "Aucune version déployée : le service démarrera au premier deploy/deployer.sh."
elif [ "$unite_modifiee" = 0 ] && systemctl is-active --quiet edt; then
  info "Unité inchangée et service actif : pas de redémarrage."
else
  info "Unité modifiée ou service inactif : redémarrage du service."
  systemctl reset-failed edt   # remet aussi à zéro le compteur de démarrages
  systemctl restart edt
  if code="$(attendre_code http://127.0.0.1:8000/sante 200 30)"; then
    info "Service redémarré, /sante répond 200."
  else
    avertir "après redémarrage, /sante ne répond pas 200 (dernier résultat : $code) : « sudo journalctl -u edt -n 50 » ou deploy/etat.sh."
  fi
fi

etape "Sauvegarde quotidienne de la base (edt-sauvegarde.timer)"
install -o root -g root -m 644 "$DOSSIER_SCRIPT/edt-sauvegarde.service" /etc/systemd/system/edt-sauvegarde.service
install -o root -g root -m 644 "$DOSSIER_SCRIPT/edt-sauvegarde.timer" /etc/systemd/system/edt-sauvegarde.timer
systemctl daemon-reload
systemctl enable --now edt-sauvegarde.timer
prochaine="$(systemctl show -p NextElapseUSecRealtime --value edt-sauvegarde.timer)"
info "Minuteur actif ; prochaine sauvegarde : ${prochaine:-calculée par systemd} (dans /var/lib/edt/sauvegardes, 14 gardées)."
if [ ! -L /opt/edt/courant ]; then
  info "Aucune version déployée : la sauvegarde est sautée tant que deploy/deployer.sh n'a pas été lancé."
fi

installer_nginx

etape "Terminé"
info "Serveur prêt. Étapes suivantes (docs/deploiement.md) :"
info "  1. HTTPS (sur le serveur) : sudo bash $DOSSIER_SCRIPT/activer-https.sh"
info "  2. Déploiement (sur le poste) : deploy/deployer.sh"
info "Ce script n'a touché ni à SSH ni au pare-feu (un pare-feu activé plus tard devra ouvrir 22, 80 et 443)."
