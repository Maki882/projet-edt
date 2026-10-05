#!/usr/bin/env bash
# =============================================================================
# deploy/installer-serveur.sh — prépare le serveur pour l'application EDT.
#
# À lancer SUR LE SERVEUR, en root, depuis une copie du dossier deploy/
# (voir docs/deploiement.md, « Première installation ») :
#   sudo bash ~/edt-installation/deploy/installer-serveur.sh
#   sudo bash ~/edt-installation/deploy/installer-serveur.sh --nginx-seulement
#
# IDEMPOTENT : on peut le relancer sans risque, par exemple après une
# modification de edt.service ou des fichiers nginx dans le dépôt.
#
# Ce qu'il fait :
#   1. apt-get update, installation de nginx, Python (venv, en-têtes de
#      compilation), sqlite3, curl ;
#   2. utilisateur système « edt » (sans shell, sans mot de passe) ;
#   3. dossiers /opt/edt (code + venvs), /var/lib/edt (base + sauvegardes),
#      /var/log/edt (journaux), /etc/edt (environnement), /var/cache/edt (pip) ;
#   4. /etc/edt/edt.env créé depuis edt.env.exemple s'il n'existe pas
#      (jamais écrasé ensuite) ;
#   5. unité systemd edt.service installée et activée ;
#   6. nginx : page d'erreur 502, partie commune, site « edt » en mode HTTP,
#      ou en mode HTTPS si un certificat existe déjà pour EDT_DOMAINE ; le site
#      par défaut de nginx est désactivé. Avec --nginx-seulement, seule cette
#      étape est faite (utilisé par activer-https.sh).
#
# Il ne touche NI à la configuration SSH NI au pare-feu.
# Paramètre : EDT_DOMAINE (défaut pts.jorund.xyz). Pour le changer :
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
readonly DOSSIER_PAGES_ERREUR="/var/www/edt-erreurs"
readonly DOSSIER_ACME="/var/www/certbot"
readonly CERTIFICAT="/etc/letsencrypt/live/$EDT_DOMAINE/fullchain.pem"
readonly CLE_CERTIFICAT="/etc/letsencrypt/live/$EDT_DOMAINE/privkey.pem"

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
for fichier in edt.service edt.env.exemple nginx-edt.conf nginx-edt-https.conf \
  nginx-edt-application.conf erreur-502.html; do
  if [ ! -e "$DOSSIER_SCRIPT/$fichier" ]; then
    echouer "fichier $DOSSIER_SCRIPT/$fichier absent : copiez le dossier deploy/ complet sur le serveur."
  fi
done
if [ -e /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  info "Système : ${PRETTY_NAME:-inconnu}"
  if [ "${ID:-}" != "ubuntu" ]; then
    avertir "ce script est prévu pour Ubuntu ; système détecté : ${ID:-inconnu}."
  fi
fi
info "Nom de domaine : $EDT_DOMAINE"

# -----------------------------------------------------------------------------
# Installation (ou réinstallation) de la configuration nginx. Le mode dépend
# de la présence du certificat : HTTPS s'il existe, HTTP sinon. En cas de
# configuration refusée par « nginx -t », l'ancienne est remise en place.
installer_nginx() {
  etape "Configuration nginx"
  if ! command -v nginx > /dev/null; then
    echouer "nginx n'est pas installé : lancez d'abord ce script sans option."
  fi

  local modele mode
  if [ -e "$CERTIFICAT" ] && [ -e "$CLE_CERTIFICAT" ]; then
    modele="nginx-edt-https.conf"
    mode="HTTPS"
  else
    modele="nginx-edt.conf"
    mode="HTTP"
  fi
  info "Mode : $mode (modèle $modele)"

  install -d -o root -g root -m 755 "$DOSSIER_PAGES_ERREUR" "$DOSSIER_ACME" /etc/nginx/snippets

  # Copie de l'existant, pour pouvoir revenir en arrière si nginx -t refuse.
  local copie
  copie="$(mktemp -d)"
  if [ -e "$SITE_NGINX" ]; then cp -p "$SITE_NGINX" "$copie/site"; fi
  if [ -e "$PARTIE_COMMUNE_NGINX" ]; then cp -p "$PARTIE_COMMUNE_NGINX" "$copie/commun"; fi
  local defaut_actif=0
  if [ -e "$SITE_DEFAUT" ] || [ -L "$SITE_DEFAUT" ]; then defaut_actif=1; fi

  install -o root -g root -m 644 "$DOSSIER_SCRIPT/erreur-502.html" "$DOSSIER_PAGES_ERREUR/erreur-502.html"
  install -o root -g root -m 644 "$DOSSIER_SCRIPT/nginx-edt-application.conf" "$PARTIE_COMMUNE_NGINX"
  sed "s/@EDT_DOMAINE@/$EDT_DOMAINE/g" "$DOSSIER_SCRIPT/$modele" > "$SITE_NGINX.nouveau"
  if grep -q '@EDT_DOMAINE@' "$SITE_NGINX.nouveau"; then
    rm -rf -- "$copie"
    echouer "substitution du nom de domaine incomplète dans $SITE_NGINX.nouveau."
  fi
  chmod 644 "$SITE_NGINX.nouveau"
  mv -f "$SITE_NGINX.nouveau" "$SITE_NGINX"
  ln -sfn "$SITE_NGINX" "$LIEN_SITE_NGINX"
  if [ "$defaut_actif" = 1 ]; then
    rm -f "$SITE_DEFAUT"
    info "Site nginx par défaut désactivé (le fichier /etc/nginx/sites-available/default est conservé)."
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
    info "nginx rechargé."
  else
    systemctl start nginx
    info "nginx démarré."
  fi

  # Contrôle : nginx répond-il sur le port 80 pour ce nom de domaine ?
  local code
  if ! code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Host: $EDT_DOMAINE" http://127.0.0.1/sante)"; then
    echouer "nginx ne répond pas sur le port 80 (port déjà pris par un autre programme ? « sudo ss -ltnp 'sport = :80' »)."
  fi
  case "$mode:$code" in
    HTTP:200) info "nginx répond sur le port 80 et l'application aussi (200)." ;;
    HTTP:502) info "nginx répond sur le port 80 (502 : normal tant que l'application n'est pas déployée ou démarrée)." ;;
    HTTPS:301) info "nginx répond sur le port 80 par une redirection vers https://$EDT_DOMAINE (301), comme prévu." ;;
    *) avertir "réponse inattendue de nginx sur le port 80 : code $code (mode $mode)." ;;
  esac
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
  nginx python3 python3-venv python3-dev build-essential sqlite3 curl ca-certificates openssl

etape "Python"
version_python="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echouer "Python $version_python est trop ancien (3.11 minimum)."
fi
dossier_test="$(mktemp -d)"
if ! python3 -m venv "$dossier_test/venv"; then
  rm -rf -- "$dossier_test"
  echouer "« python3 -m venv » ne fonctionne pas (paquet python3-venv ?)."
fi
rm -rf -- "$dossier_test"
info "Python $version_python avec venv : ok."

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
install -d -o root -g root -m 750 /var/log/edt/deploiements
install -d -o root -g "$UTILISATEUR_APP" -m 750 /etc/edt
install -d -o root -g root -m 700 /var/cache/edt /var/cache/edt/pip
info "/opt/edt (code, root), /var/lib/edt et /var/log/edt (edt), /etc/edt (root:edt), /var/cache/edt (root) : ok."

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

etape "Service systemd edt"
unite_modifiee=1
if [ -e "$UNITE" ] && cmp -s "$DOSSIER_SCRIPT/edt.service" "$UNITE"; then
  unite_modifiee=0
fi
install -o root -g root -m 644 "$DOSSIER_SCRIPT/edt.service" "$UNITE"
systemctl daemon-reload
systemctl enable edt
if [ -L /opt/edt/courant ]; then
  if ! systemd-analyze verify "$UNITE"; then
    avertir "systemd-analyze verify signale des remarques sur $UNITE (ci-dessus)."
  fi
  if [ "$unite_modifiee" = 1 ]; then
    info "Unité modifiée et version déjà déployée : redémarrage du service."
    systemctl restart edt
    sante="pas de réponse"
    for _ in $(seq 1 30); do
      if sante="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8000/sante)" && [ "$sante" = "200" ]; then
        break
      fi
      sleep 1
    done
    if [ "$sante" = "200" ]; then
      info "Service redémarré, /sante répond 200."
    else
      avertir "après redémarrage, /sante ne répond pas 200 (dernier résultat : $sante) : « sudo journalctl -u edt -n 50 » ou deploy/etat.sh."
    fi
  else
    info "Unité inchangée : le service n'est pas redémarré."
  fi
else
  info "Aucune version déployée : le service démarrera au premier deploy/deployer.sh."
fi

installer_nginx

etape "Terminé"
info "Serveur prêt. Étapes suivantes (docs/deploiement.md) :"
info "  1. HTTPS (sur le serveur) : sudo bash $DOSSIER_SCRIPT/activer-https.sh"
info "  2. Déploiement (sur le poste) : deploy/deployer.sh"
info "Rappel : ce script n'a touché ni à SSH ni au pare-feu. Un pare-feu activé plus tard devra ouvrir les ports 22, 80 et 443."
