#!/usr/bin/env bash
# =============================================================================
# deploy/activer-https.sh — active HTTPS (Let's Encrypt) pour l'application EDT.
#
# À lancer SUR LE SERVEUR, en root, APRÈS installer-serveur.sh, depuis la même
# copie du dossier deploy/ :
#   sudo bash ~/edt-installation/deploy/activer-https.sh
#   sudo env EDT_CERTBOT_EMAIL=prenom.nom@example.org bash ~/edt-installation/deploy/activer-https.sh
#
# Ce qu'il fait :
#   1. vérifie, SANS RIEN MODIFIER, que EDT_DOMAINE se résout vers une adresse
#      de ce serveur et que nginx y répond sur le port 80 (sinon : message et
#      sortie non nulle) ;
#   2. installe certbot (apt) ;
#   3. obtient le certificat en mode non interactif (méthode « webroot » : le
#      défi est servi par nginx depuis /var/www/certbot, emplacement
#      /.well-known/acme-challenge/ prévu dans nginx-edt*.conf) ;
#   4. installe un crochet qui recharge nginx après chaque renouvellement ;
#   5. passe nginx en mode HTTPS (installer-serveur.sh --nginx-seulement), avec
#      redirection de HTTP (nom de domaine et adresse IP) vers HTTPS ;
#   6. vérifie HTTPS et la redirection, puis le renouvellement automatique
#      (minuteur certbot.timer et « certbot renew --dry-run »).
#
# IDEMPOTENT : relancé alors que le certificat est valide, certbot ne demande
# rien à Let's Encrypt (--keep-until-expiring) et la même configuration est
# réinstallée ; seul le test « --dry-run » contacte le serveur de test.
#
# Paramètres (variables d'environnement) :
#   EDT_DOMAINE        nom de domaine (défaut pts.jorund.xyz)
#   EDT_CERTBOT_EMAIL  adresse de contact du compte Let's Encrypt (avis liés au
#                      compte ; Let's Encrypt n'envoie plus d'alertes d'expiration
#                      depuis juin 2025) ; vide = inscription sans adresse
#   EDT_IP_PUBLIQUE    adresse IPv4 publique du serveur, si la détection
#                      automatique (adresses des interfaces) ne convient pas
# =============================================================================
set -euo pipefail
set -o errtrace

EDT_DOMAINE="${EDT_DOMAINE:-pts.jorund.xyz}"
EDT_CERTBOT_EMAIL="${EDT_CERTBOT_EMAIL:-}"
EDT_IP_PUBLIQUE="${EDT_IP_PUBLIQUE:-}"
export EDT_DOMAINE   # transmis à installer-serveur.sh --nginx-seulement

DOSSIER_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DOSSIER_SCRIPT
readonly DOSSIER_ACME="/var/www/certbot"
readonly CERTIFICAT="/etc/letsencrypt/live/$EDT_DOMAINE/fullchain.pem"
readonly CROCHET="/etc/letsencrypt/renewal-hooks/deploy/edt-recharger-nginx.sh"

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
    erreur "« $commande » a renvoyé le code $code (ligne $ligne de activer-https.sh)."
    exit "$code"
  fi
  erreur "échec à l'étape « $ETAPE » : « $commande » a renvoyé le code $code (ligne $ligne de activer-https.sh)."
  erreur "Le script est idempotent : corrigez la cause puis relancez-le."
  exit "$code"
}
trap 'sur_erreur $? $LINENO "$BASH_COMMAND"' ERR

# Fichier de test déposé dans le dossier des défis : toujours retiré en sortie.
FICHIER_TEST=""
nettoyer() {
  if [ -n "$FICHIER_TEST" ] && [ -e "$FICHIER_TEST" ]; then
    rm -f -- "$FICHIER_TEST"
  fi
}
trap nettoyer EXIT

# -----------------------------------------------------------------------------
etape "Contrôles"
if [ "$EUID" -ne 0 ]; then
  echouer "lancez ce script en root : sudo bash $0"
fi
if ! [[ "$EDT_DOMAINE" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echouer "EDT_DOMAINE invalide : « $EDT_DOMAINE »."
fi
if [ -n "$EDT_CERTBOT_EMAIL" ] && ! [[ "$EDT_CERTBOT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echouer "EDT_CERTBOT_EMAIL ne ressemble pas à une adresse e-mail : « $EDT_CERTBOT_EMAIL »."
fi
if [ ! -e "$DOSSIER_SCRIPT/installer-serveur.sh" ]; then
  echouer "$DOSSIER_SCRIPT/installer-serveur.sh absent : copiez le dossier deploy/ complet sur le serveur."
fi
if ! command -v nginx > /dev/null || [ ! -e /etc/nginx/sites-enabled/edt ] || [ ! -d "$DOSSIER_ACME" ]; then
  echouer "le serveur n'est pas installé (nginx, site « edt » ou $DOSSIER_ACME absent) : lancez d'abord installer-serveur.sh. Rien n'a été modifié."
fi
info "Nom de domaine : $EDT_DOMAINE"

# -----------------------------------------------------------------------------
etape "Vérification DNS (rien n'est modifié)"
if ! resolution="$(getent ahostsv4 "$EDT_DOMAINE")"; then
  echouer "le nom $EDT_DOMAINE ne se résout pas en adresse IPv4 depuis le serveur. Créez (ou corrigez) l'enregistrement DNS de type A chez le registraire, attendez la propagation, puis relancez. Rien n'a été modifié."
fi
adresses_dns="$(printf '%s\n' "$resolution" | awk '{ print $1 }' | sort -u)"

if [ -n "$EDT_IP_PUBLIQUE" ]; then
  adresses_serveur="$EDT_IP_PUBLIQUE"
  info "Adresse du serveur imposée par EDT_IP_PUBLIQUE : $adresses_serveur"
else
  adresses_serveur="$(ip -4 -o addr show scope global | awk '{ split($4, a, "/"); print a[1] }' | sort -u)"
  if [ -z "$adresses_serveur" ]; then
    echouer "aucune adresse IPv4 globale trouvée sur les interfaces du serveur : précisez EDT_IP_PUBLIQUE. Rien n'a été modifié."
  fi
fi
info "$EDT_DOMAINE → ${adresses_dns//$'\n'/ }"
info "Adresse(s) du serveur : ${adresses_serveur//$'\n'/ }"

while IFS= read -r adresse; do
  [ -n "$adresse" ] || continue
  case $'\n'"$adresses_serveur"$'\n' in
    *$'\n'"$adresse"$'\n'*) ;;
    *) echouer "$EDT_DOMAINE pointe vers $adresse, qui n'est pas une adresse de ce serveur. Corrigez l'enregistrement A chez le registraire (il doit valoir l'adresse du serveur), attendez la propagation (durée de vie TTL de l'ancien enregistrement), puis relancez. Rien n'a été modifié." ;;
  esac
done <<< "$adresses_dns"
info "Le DNS pointe bien sur ce serveur."

# Un enregistrement AAAA (IPv6) est préféré par Let's Encrypt ; nginx n'écoute
# pas en IPv6 dans cette configuration : on prévient.
if resolution_v6="$(getent ahostsv6 "$EDT_DOMAINE")"; then
  # Les adresses « ::ffff:a.b.c.d » ne sont que les adresses IPv4 réécrites.
  adresses_v6="$(printf '%s\n' "$resolution_v6" | awk '$1 !~ /^::ffff:/ { print $1 }' | sort -u)"
  if [ -n "$adresses_v6" ]; then
    avertir "$EDT_DOMAINE a aussi un enregistrement AAAA (IPv6 : ${adresses_v6//$'\n'/ }). Let's Encrypt essaie l'IPv6 en premier et nginx n'écoute pas en IPv6 ici : en cas d'échec de validation, supprimez l'enregistrement AAAA."
  fi
fi

# -----------------------------------------------------------------------------
etape "Vérification du port 80 (fichier de test temporaire)"
# On sert un fichier de test exactement comme Let's Encrypt le demandera.
install -d -o root -g root -m 755 "$DOSSIER_ACME/.well-known" "$DOSSIER_ACME/.well-known/acme-challenge"
jeton="edt-test-$(date '+%s')-$$"
FICHIER_TEST="$DOSSIER_ACME/.well-known/acme-challenge/$jeton"
printf '%s' "$jeton" > "$FICHIER_TEST"
chmod 644 "$FICHIER_TEST"
url_test="http://$EDT_DOMAINE/.well-known/acme-challenge/$jeton"
code_curl=0
reponse="$(curl -sS --max-time 10 "$url_test")" || code_curl=$?
if [ "$code_curl" -ne 0 ]; then
  echouer "le port 80 ne répond pas pour $url_test (code curl $code_curl). nginx tourne-t-il (« systemctl status nginx ») ? Rien n'a été modifié."
fi
if [ "$reponse" != "$jeton" ]; then
  echouer "le port 80 répond, mais pas avec le fichier de test (réponse : « ${reponse:0:200} »). Vérifiez que le site nginx « edt » est actif (installer-serveur.sh). Rien n'a été modifié."
fi
nettoyer
FICHIER_TEST=""
info "Le port 80 répond et sert bien /.well-known/acme-challenge/."
info "(Test fait depuis le serveur lui-même : un pare-feu externe n'est pas détecté ici ; certbot le signalerait.)"

# -----------------------------------------------------------------------------
etape "Installation de certbot"
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=l
apt-get -o DPkg::Lock::Timeout=300 update
apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends certbot
certbot --version

# -----------------------------------------------------------------------------
etape "Certificat Let's Encrypt pour $EDT_DOMAINE"
arguments_certbot=(
  certonly --webroot -w "$DOSSIER_ACME"
  -d "$EDT_DOMAINE" --cert-name "$EDT_DOMAINE"
  --non-interactive --agree-tos --keep-until-expiring
)
if [ -n "$EDT_CERTBOT_EMAIL" ]; then
  arguments_certbot+=(--email "$EDT_CERTBOT_EMAIL" --no-eff-email)
  info "Adresse de contact : $EDT_CERTBOT_EMAIL"
else
  avertir "EDT_CERTBOT_EMAIL est vide : inscription Let's Encrypt SANS adresse e-mail (--register-unsafely-without-email). Aucune alerte d'expiration n'est envoyée de toute façon : surveillez la date avec deploy/etat.sh (seuil de 14 jours)."
  arguments_certbot+=(--register-unsafely-without-email)
fi
certbot "${arguments_certbot[@]}"
if [ ! -e "$CERTIFICAT" ]; then
  echouer "certbot a terminé sans erreur mais $CERTIFICAT est absent."
fi
info "Certificat présent : $CERTIFICAT"

etape "Crochet de renouvellement (rechargement de nginx)"
install -d -o root -g root -m 755 "${CROCHET%/*}"
cat > "$CROCHET.nouveau" <<'CROCHET'
#!/usr/bin/env bash
# Installé par deploy/activer-https.sh : après chaque renouvellement réussi
# d'un certificat, certbot lance ce script pour que nginx utilise le nouveau.
set -euo pipefail
nginx -t -q
systemctl reload nginx
CROCHET
chmod 755 "$CROCHET.nouveau"
mv -f "$CROCHET.nouveau" "$CROCHET"
info "Crochet installé : $CROCHET"

# -----------------------------------------------------------------------------
etape "Passage de nginx en HTTPS"
bash "$DOSSIER_SCRIPT/installer-serveur.sh" --nginx-seulement

etape "Vérification de HTTPS"
code_curl=0
code_https="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://$EDT_DOMAINE/sante")" || code_curl=$?
if [ "$code_curl" -ne 0 ]; then
  echouer "https://$EDT_DOMAINE ne répond pas correctement (code curl $code_curl : certificat refusé ou port 443 injoignable)."
fi
case "$code_https" in
  200) info "https://$EDT_DOMAINE/sante : 200, l'application répond en HTTPS." ;;
  502) info "https://$EDT_DOMAINE/sante : 502 ; HTTPS fonctionne, l'application n'est simplement pas encore déployée ou démarrée." ;;
  *) avertir "https://$EDT_DOMAINE/sante : code $code_https inattendu ; HTTPS fonctionne mais vérifiez l'application (deploy/etat.sh)." ;;
esac

code_curl=0
redirection="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 10 "http://$EDT_DOMAINE/sante")" || code_curl=$?
if [ "$code_curl" -ne 0 ]; then
  echouer "http://$EDT_DOMAINE ne répond plus sur le port 80 (code curl $code_curl)."
fi
if [ "$redirection" = "301 https://$EDT_DOMAINE/sante" ]; then
  info "http://$EDT_DOMAINE redirige vers HTTPS (301)."
else
  echouer "la redirection HTTP → HTTPS ne fonctionne pas (réponse : « $redirection »)."
fi
premiere_adresse="${adresses_dns%%$'\n'*}"
code_curl=0
redirection_ip="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 10 "http://$premiere_adresse/")" || code_curl=$?
if [ "$code_curl" -ne 0 ]; then redirection_ip="pas de réponse (code curl $code_curl)"; fi
if [ "$redirection_ip" = "301 https://$EDT_DOMAINE/" ]; then
  info "http://$premiere_adresse/ (adresse IP) redirige vers https://$EDT_DOMAINE/ (301)."
else
  avertir "l'adresse IP en HTTP ne redirige pas comme prévu (réponse : « $redirection_ip »)."
fi

# -----------------------------------------------------------------------------
etape "Renouvellement automatique"
if ! systemctl is-active --quiet certbot.timer; then
  info "certbot.timer inactif : activation."
  systemctl enable --now certbot.timer
fi
systemctl list-timers certbot.timer --all --no-pager
if ! systemctl is-active --quiet certbot.timer; then
  echouer "le minuteur certbot.timer n'est pas actif : le certificat ne sera pas renouvelé automatiquement."
fi
info "Test du renouvellement auprès du serveur de test de Let's Encrypt (--dry-run, sans effet sur le vrai certificat) :"
if ! certbot renew --dry-run --cert-name "$EDT_DOMAINE" --no-random-sleep-on-renew; then
  echouer "le test de renouvellement a échoué (voir ci-dessus et /var/log/letsencrypt/letsencrypt.log). Le certificat actuel reste valable jusqu'à son expiration."
fi

etape "Terminé"
fin_validite="$(openssl x509 -enddate -noout -in "$CERTIFICAT")"
info "HTTPS actif : https://$EDT_DOMAINE/ (certificat valable jusqu'au ${fin_validite#notAfter=})."
info "Renouvellement : automatique (certbot.timer), nginx rechargé par $CROCHET."
