#!/usr/bin/env bash
# =============================================================================
# deploy/installer-cle-assistant.sh — installe, remplace ou retire la clé de
# l'API Claude utilisée par l'assistant conversationnel de l'application EDT.
#
# À lancer SUR LE SERVEUR, en root, dans un terminal (la clé est saisie au
# clavier). Le script fait partie du code déployé :
#   sudo bash /opt/edt/courant/code/deploy/installer-cle-assistant.sh            installer ou remplacer
#   sudo bash /opt/edt/courant/code/deploy/installer-cle-assistant.sh --retirer  retirer
# Depuis le poste, en une commande (terminal interactif obligatoire : -t) :
#   ssh -t -i <clé> <compte>@<serveur> 'sudo bash /opt/edt/courant/code/deploy/installer-cle-assistant.sh'
# (paramètres de deploy/serveur.env ; docs/deploiement.md, « Assistant »).
#
# La clé est demandée en saisie masquée. Elle n'est manipulée que par des
# commandes internes de bash (read, printf, test) : elle ne passe jamais en
# argument d'une commande, n'est jamais exportée, affichée ni journalisée,
# même si le script est lancé avec « bash -x ». Elle est écrite dans
# /etc/edt/assistant.env (root:edt, mode 640), lu par systemd pour le service
# (EnvironmentFile=-/etc/edt/assistant.env dans edt.service) et par le lanceur
# « sudo edt » ; jamais dans /etc/edt/edt.env, que installer-serveur.sh affiche.
#
# Ensuite : redémarrage du service, attente de /sante, puis essai
# « sudo edt assistant --essai » si la version en service a cette commande.
# Codes de sortie : 0 clé installée (ou retirée) et service prêt ; 1 échec,
# avec la cause ; 2 mauvaise utilisation (option inconnue, pas root, pas de
# terminal).
# =============================================================================
set -euo pipefail
set +x   # la clé ne doit jamais apparaître dans une trace d'exécution

readonly FICHIER_CLE="/etc/edt/assistant.env"
readonly DOSSIER_ENV="/etc/edt"
readonly GROUPE_APP="edt"
readonly SERVICE="edt"
readonly LANCEUR="/usr/local/bin/edt"
readonly COURANT="/opt/edt/courant"
readonly URL_SANTE="http://127.0.0.1:8000/sante"
readonly DELAI_SANTE=30   # secondes

info()    { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
erreur()  { printf '[%s] ERREUR : %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2; }
echouer() { erreur "$*"; exit 1; }

# Clé saisie : variable globale non exportée, effacée dès qu'elle est écrite.
CLE=""

# Lit la clé sur l'entrée standard, sans écho dans un terminal. Refuse une
# saisie vide ou un caractère qui n'appartient pas à une clé de l'API (espace,
# guillemet, retour chariot d'un copier-coller...). Renvoie 1 avec le motif.
demander_cle() {
  CLE=""
  IFS= read -r -s -p "Clé de l'API Claude (saisie masquée, Entrée pour valider) : " CLE || true
  printf '\n'
  if [ -z "$CLE" ]; then
    erreur "aucune clé saisie : rien n'est modifié."
    return 1
  fi
  case "$CLE" in
    *[!A-Za-z0-9_-]*)
      CLE=""
      erreur "la clé contient un caractère inattendu (espace, guillemet, retour à la ligne...) : rien n'est modifié."
      return 1
      ;;
  esac
}

# Écrit la clé dans $1 (fichier d'environnement lu par systemd) : fichier
# temporaire du même dossier, propriétaire root et groupe $2, mode 640, puis
# renommage atomique. Un échec laisse l'ancien fichier intact. La clé est
# effacée de la mémoire du script dans tous les cas.
ecrire_cle() {
  local cible=$1 groupe=$2 temporaire ecrite=0
  if temporaire="$(mktemp "${cible%/*}/.assistant.env.XXXXXX")"; then
    if printf '# Écrit par deploy/installer-cle-assistant.sh. Ne pas copier ce fichier ailleurs.\nANTHROPIC_API_KEY=%s\n' "$CLE" > "$temporaire" \
      && chown "root:$groupe" "$temporaire" \
      && chmod 640 "$temporaire" \
      && mv -f "$temporaire" "$cible"; then
      ecrite=1
    else
      rm -f -- "$temporaire"
    fi
  fi
  CLE=""
  [ "$ecrite" = 1 ]
}

# Redémarre le service s'il a une version déployée, puis attend /sante → 200.
redemarrer_service() {
  if [ ! -L "$COURANT" ]; then
    info "Aucune version déployée : la clé sera lue au premier démarrage du service (deploy/deployer.sh)."
    return 2
  fi
  systemctl reset-failed "$SERVICE" || true
  if ! systemctl restart "$SERVICE"; then
    erreur "redémarrage du service impossible : sudo journalctl -u $SERVICE -n 50"
    return 1
  fi
  local debut=$SECONDS code=""
  while [ $((SECONDS - debut)) -lt "$DELAI_SANTE" ]; do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL_SANTE")" || true
    if [ "$code" = "200" ]; then
      info "Service redémarré : $URL_SANTE répond 200."
      return 0
    fi
    sleep 1
  done
  erreur "après redémarrage, $URL_SANTE ne répond pas 200 en $DELAI_SANTE s (dernier code : ${code:-aucun}) : sudo journalctl -u $SERVICE -n 50"
  return 1
}

# Essai de bout en bout par l'application (sudo edt assistant --essai). Une
# commande inconnue répond le code 2 : version sans assistant, essai sauté.
essayer_assistant() {
  local code=0
  "$LANCEUR" assistant --help > /dev/null 2>&1 || code=$?
  case "$code" in
    0) ;;
    2)
      info "La version en service n'a pas la commande « edt assistant » : essai sauté (la clé servira dès qu'une version avec l'assistant sera déployée)."
      return 0
      ;;
    *)
      erreur "« $LANCEUR assistant --help » a échoué (code $code) : déploiement en cours ? Relancer plus tard « sudo edt assistant --essai »."
      return 1
      ;;
  esac
  info "Essai de l'assistant (sudo edt assistant --essai) :"
  if ! "$LANCEUR" assistant --essai; then
    erreur "essai en échec : la clé est installée mais l'assistant ne répond pas (cause ci-dessus). Clé refusée : relancer ce script pour la remplacer."
    return 1
  fi
  info "Essai réussi : l'assistant répond."
}

# --- Programme principal -------------------------------------------------------
main() {
  local action="installer"
  case "${1:-}" in
    "") ;;
    --retirer) action="retirer" ;;
    -h | --help) sed -n '3,27p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *)
      erreur "option inconnue : $1 (seule option : --retirer)."
      exit 2
      ;;
  esac
  if [ "$EUID" -ne 0 ]; then
    erreur "à lancer en root : sudo bash $0"
    exit 2
  fi
  if [ ! -d "$DOSSIER_ENV" ] || ! getent group "$GROUPE_APP" > /dev/null; then
    echouer "$DOSSIER_ENV ou le groupe « $GROUPE_APP » absent : serveur non installé (deploy/installer-serveur.sh)."
  fi

  if [ "$action" = "retirer" ]; then
    if [ ! -e "$FICHIER_CLE" ]; then
      info "Aucune clé installée ($FICHIER_CLE absent) : rien à retirer."
      exit 0
    fi
    rm -f -- "$FICHIER_CLE"
    info "Clé retirée ($FICHIER_CLE supprimé)."
    local code=0
    redemarrer_service || code=$?
    if [ "$code" = 1 ]; then exit 1; fi
    info "L'assistant est « non configuré » ; le reste de l'application fonctionne."
    exit 0
  fi

  if [ ! -t 0 ]; then
    erreur "saisie masquée impossible : pas de terminal. Depuis le poste, ajouter -t à ssh (ssh -t ...)."
    exit 2
  fi
  if [ -e "$FICHIER_CLE" ]; then
    info "Une clé est déjà installée : elle sera remplacée par celle saisie."
  fi
  demander_cle || exit 1
  if [[ "$CLE" != sk-ant-* ]]; then
    local reponse=""
    read -r -p "La clé ne commence pas par « sk-ant- » (clé de l'API Claude). L'installer quand même ? (o/N) " reponse || true
    if [ "$reponse" != "o" ]; then
      CLE=""
      echouer "installation annulée : rien n'est modifié."
    fi
  fi
  ecrire_cle "$FICHIER_CLE" "$GROUPE_APP" || echouer "écriture de $FICHIER_CLE impossible : rien n'est modifié."
  info "Clé installée dans $FICHIER_CLE (root:$GROUPE_APP, mode 640)."

  local code=0
  redemarrer_service || code=$?
  case "$code" in
    0) essayer_assistant || exit 1 ;;
    2) ;;
    *) exit 1 ;;
  esac
  info "Terminé."
}

main "$@"
