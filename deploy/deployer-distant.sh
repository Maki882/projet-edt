#!/usr/bin/env bash
# =============================================================================
# deploy/deployer-distant.sh — partie SERVEUR du déploiement de l'application EDT.
#
# Envoyé et lancé par deploy/deployer.sh (ssh, root via sudo), sur une version
# dont le code vient d'être envoyé dans /opt/edt/versions/<version>/code.
# Pour déboguer à la main, copier d'abord la version du dépôt sur le serveur :
#   ssh ... 'cat > /tmp/deployer-distant.sh' < deploy/deployer-distant.sh
#   ssh ... 'sudo bash /tmp/deployer-distant.sh <version> <commit> <référence> <reset 0|1> <auteur> <arbre_propre 0|1>'
#
# PRÉPARATION (le service en place n'est pas touché ; un échec le laisse en ligne) :
#   verrou, contrôles (envoi complet, fichiers de la base à l'utilisateur edt,
#   aucune commande « sudo edt » en cours), venv propre à la version +
#   dépendances, chargement de l'application et base qu'elle utilise, port 8000.
# BASCULE (tout échec déclenche le retour arrière automatique) :
#   arrêt du service, plus aucun processus de l'utilisateur edt, sauvegarde de
#   la base, init-db, verifier-schema, bascule du lien /opt/edt/courant,
#   démarrage, GET /sante → 200 en 30 s maximum.
# FINALISATION : version marquée réussie, nettoyage (5 versions, 10 sauvegardes,
#   versions jamais mises en service et sauvegardes partielles d'un échec antérieur).
#
# Retour arrière : 50 dernières lignes du journal du service, base restaurée,
# lien remis sur la version précédente, redémarrage. Sans version précédente
# (premier déploiement) : service laissé arrêté, lien /opt/edt/courant retiré.
#
# Les commandes de l'application sont lancées par systemd-run, comme le
# service : utilisateur edt et /etc/edt/edt.env lu PAR SYSTEMD (ce script ne
# relit jamais ce fichier lui-même). Codes de sortie : 0 réussi ; 1 échec, la
# version précédente (ou rien) est en service ; 3 nouvelle version en service
# mais finalisation incomplète. Sortie copiée dans
# /var/log/edt-deploiements/<version>.log ; une coupure SSH n'interrompt rien.
# =============================================================================
set -euo pipefail
set -o errtrace   # le piège ERR s'applique aussi à l'intérieur des fonctions

# Même fuseau que l'application (TZ de edt.env.exemple) : les heures de ce
# journal se comparent directement à celles de edt.log. Les noms de version et
# de sauvegarde sont en UTC (aucune heure répétée au passage à l'heure d'hiver)
# et l'historique porte le décalage horaire. Ordre des noms : octet par octet.
export TZ="Europe/Paris"
export LC_COLLATE=C

readonly SERVICE="edt"
readonly UTILISATEUR_APP="edt"
readonly DOSSIER_VERSIONS="/opt/edt/versions"
readonly LIEN_COURANT="/opt/edt/courant"
readonly FICHIER_ENV="/etc/edt/edt.env"
# Clé de l'assistant (installer-cle-assistant.sh) : facultative, lue comme le
# fait le service (EnvironmentFile=-/etc/edt/assistant.env dans edt.service).
readonly FICHIER_ASSISTANT="/etc/edt/assistant.env"
readonly DOSSIER_DONNEES="/var/lib/edt"
readonly DOSSIER_SAUVEGARDES="/var/lib/edt/sauvegardes"
readonly DOSSIER_JOURNAUX_DEPLOIEMENT="/var/log/edt-deploiements"
readonly HISTORIQUE="/var/log/edt-deploiements/historique.log"
# Verrou pris en exclusif ici et en partagé par le lanceur « sudo edt » : pas
# de commande de l'application pendant un déploiement, ni l'inverse.
readonly VERROU="/run/lock/edt-deploiement.lock"
readonly PORT_APP=8000
readonly URL_SANTE="http://127.0.0.1:$PORT_APP/sante"
readonly DELAI_SANTE=30            # secondes
readonly VERSIONS_A_GARDER=5
readonly SAUVEGARDES_A_GARDER=10
readonly LIGNES_JOURNAL=50
readonly ESPACE_MINIMAL_MO=1024
readonly MARQUE_ENVOI="envoi-termine"        # écrit par deployer.sh après un envoi complet
readonly MARQUE_REUSSITE="deploiement-reussi"
readonly EXPLICATION_SCHEMA="le schéma de la base ne correspond plus au modèle de cette version (table ou colonne ajoutée, renommée ou supprimée). Il n'y a pas de migrations : cette version ne peut être déployée qu'avec « deploy/deployer.sh --reset-base », qui EFFACE les données (une sauvegarde est faite juste avant)."
export PIP_CACHE_DIR="/var/cache/edt/pip"

# --- Messages horodatés ------------------------------------------------------
ETAPE="démarrage"
horodatage() { date '+%Y-%m-%d %H:%M:%S'; }
horodatage_iso() { date '+%Y-%m-%dT%H:%M:%S%z'; }   # avec le décalage : sans ambiguïté
info()    { printf '[%s] [serveur] %s\n' "$(horodatage)" "$*"; }
etape()   { ETAPE="$*"; printf '\n[%s] [serveur] === %s ===\n' "$(horodatage)" "$*"; }
avertir() { printf '[%s] [serveur] ATTENTION : %s\n' "$(horodatage)" "$*" >&2; }
erreur()  { printf '[%s] [serveur] ERREUR : %s\n' "$(horodatage)" "$*" >&2; }

# --- Arguments ---------------------------------------------------------------
if [ $# -ne 6 ]; then
  echo "usage : sudo bash deployer-distant.sh <version> <commit> <référence> <reset 0|1> <auteur> <arbre_propre 0|1>" >&2
  exit 2
fi
readonly VERSION="$1" SHA="$2" REF="$3" RESET_BASE="$4" AUTEUR="$5" ARBRE_PROPRE="$6"
if ! [[ "$VERSION" =~ ^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$ && "$SHA" =~ ^[0-9a-f]{40,64}$ \
  && "$REF" =~ ^[A-Za-z0-9._/-]+$ && "$RESET_BASE" =~ ^[01]$ \
  && "$AUTEUR" =~ ^[A-Za-z0-9._-]+$ && "$ARBRE_PROPRE" =~ ^[01]$ ]]; then
  erreur "arguments invalides : $*"
  exit 2
fi
readonly DOSSIER_VERSION="$DOSSIER_VERSIONS/$VERSION"
readonly DOSSIER_CODE="$DOSSIER_VERSION/code"
readonly PYTHON_VENV="$DOSSIER_VERSION/venv/bin/python"
readonly FICHIER_INFOS="$DOSSIER_VERSION/DEPLOIEMENT.txt"

# --- État du déploiement (lu par la gestion d'erreur) -------------------------
PHASE="preparation"     # preparation | bascule | finalisation
PRECEDENTE=""           # dossier de la version en service avant ce déploiement
SAUVEGARDE=""           # sauvegarde de la base faite par ce déploiement
BASE=""                 # fichier SQLite, tel que l'application le voit
BASE_MODIFIEE=0         # 1 dès que init-db a été lancé
RESULTAT_RETOUR=""      # résultat du retour arrière, pour l'historique
JOURNAL_DEPLOIEMENT=""

# --- Gestion des erreurs -----------------------------------------------------
# Toute commande en échec (hors condition testée) arrive ici : on dit quelle
# étape a échoué et à quelle ligne, puis on agit selon la phase.
sur_erreur() {
  local code=$1 ligne=$2 commande=$3
  case "$commande" in
    return*) commande="une fonction appelée à cette ligne (cause dans le message juste au-dessus)" ;;
  esac
  if [ "$BASH_SUBSHELL" -gt 0 ]; then
    # Erreur dans « $( ) » ou « ( ) » : signalée ici, gérée une seule fois par le script principal.
    erreur "« $commande » a renvoyé le code $code (ligne $ligne de deployer-distant.sh)."
    exit "$code"
  fi
  echouer "échec à l'étape « $ETAPE » : « $commande » a renvoyé le code $code (ligne $ligne de deployer-distant.sh)."
}
trap 'sur_erreur $? $LINENO "$BASH_COMMAND"' ERR

# Échec : message, puis selon la phase rien (préparation), retour arrière
# (bascule) ou simple signalement (finalisation). Ne revient jamais.
echouer() {
  if [ "$BASH_SUBSHELL" -gt 0 ]; then
    erreur "$*"
    exit 1
  fi
  trap - ERR
  trap '' INT TERM   # le retour arrière va au bout
  set +e
  erreur "$*"
  local etape_en_echec="$ETAPE" code_sortie=1
  case "$PHASE" in
    preparation)
      info "Le service en place n'a pas été touché : l'ancienne version reste en ligne."
      noter_historique "ECHEC en préparation (étape : $etape_en_echec)"
      ;;
    bascule)
      afficher_journal_service
      retour_arriere
      noter_historique "ECHEC en bascule (étape : $etape_en_echec) ; $RESULTAT_RETOUR"
      ;;
    *)
      erreur "la nouvelle version EST en service, seule la finalisation a échoué (voir ci-dessus)."
      noter_historique "EN SERVICE, finalisation en échec (étape : $etape_en_echec)"
      code_sortie=3
      ;;
  esac
  info "Journal complet de ce déploiement : ${JOURNAL_DEPLOIEMENT:-non créé}"
  exit "$code_sortie"
}

afficher_journal_service() {
  info "----- $LIGNES_JOURNAL dernières lignes du journal du service $SERVICE -----"
  if ! journalctl -u "$SERVICE" -n "$LIGNES_JOURNAL" --no-pager -o short-iso; then
    erreur "lecture du journal impossible (journalctl)."
  fi
  info "----- fin du journal du service -----"
}

noter_historique() {
  if ! printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(horodatage_iso)" "$VERSION" "$SHA" "$REF" "$AUTEUR" "$1" >> "$HISTORIQUE"; then
    erreur "impossible d'écrire l'historique $HISTORIQUE."
  fi
}

# Phase de bascule, après une erreur (set +e actif) : chaque commande est
# vérifiée une par une et tout échec est signalé.
retour_arriere() {
  etape "Retour arrière"
  local ok=1
  if ! systemctl stop "$SERVICE"; then
    erreur "arrêt du service impossible."
    ok=0
  fi
  if [ "$BASE_MODIFIEE" = 1 ] && [ -n "$SAUVEGARDE" ]; then
    if ! restaurer_base "$SAUVEGARDE"; then
      erreur "restauration de la base impossible ; la sauvegarde reste disponible : $SAUVEGARDE"
      ok=0
    fi
  elif [ "$BASE_MODIFIEE" = 1 ]; then
    avertir "pas de base avant ce déploiement : rien à restaurer ; la base éventuellement créée par init-db est conservée (« $BASE »)."
  fi
  if [ -z "$PRECEDENTE" ]; then
    if [ -L "$LIEN_COURANT" ] && ! rm -f -- "$LIEN_COURANT"; then
      erreur "impossible de retirer le lien $LIEN_COURANT."
    fi
    RESULTAT_RETOUR="premier déploiement : service laissé arrêté"
    erreur "aucune version précédente (premier déploiement) : le service reste arrêté et le lien $LIEN_COURANT est retiré. Le site affiche la page « application indisponible ». Corrigez puis relancez deploy/deployer.sh."
    return 0
  fi
  if ! basculer_lien "$PRECEDENTE"; then
    erreur "impossible de remettre le lien $LIEN_COURANT sur $PRECEDENTE."
    ok=0
  fi
  if [ "$ok" = 1 ] && demarrer_service && attendre_sante; then
    RESULTAT_RETOUR="retour à ${PRECEDENTE##*/} réussi"
    info "Retour arrière réussi : la version ${PRECEDENTE##*/} est de nouveau en service."
  else
    RESULTAT_RETOUR="RETOUR ARRIÈRE EN ÉCHEC"
    erreur "LE RETOUR ARRIÈRE A ÉCHOUÉ : intervention manuelle nécessaire (docs/deploiement.md, section « Retour arrière »)."
    afficher_journal_service
  fi
}

# --- Outils ------------------------------------------------------------------
# Ces fonctions peuvent être appelées dans une condition (où bash ignore
# « set -e ») : chaque commande y est donc suivie de « || return 1 ».

# Lance une commande dans les conditions du service : utilisateur edt,
# variables de /etc/edt/edt.env (et de /etc/edt/assistant.env s'il existe)
# lues par systemd lui-même, dossier du code de la NOUVELLE version. Le code
# de sortie de la commande est transmis.
lancer_comme_app() {
  local environnement=(-p EnvironmentFile="$FICHIER_ENV")
  if [ -e "$FICHIER_ASSISTANT" ]; then environnement+=(-p EnvironmentFile="$FICHIER_ASSISTANT"); fi
  systemd-run --quiet --wait --pipe --collect --service-type=exec \
    --uid="$UTILISATEUR_APP" --gid="$UTILISATEUR_APP" -p UMask=0027 \
    "${environnement[@]}" -p WorkingDirectory="$DOSSIER_CODE" \
    -E PYTHONUNBUFFERED=1 "$@"
}

# Processus de l'utilisateur edt qui ne font pas partie du service : commandes
# « sudo edt … » (unités systemd-run), sauvegarde quotidienne, sqlite3 lancé à
# la main... Ils tiennent la base ouverte : on ne la sauvegarde ni ne la
# remplace sous leurs pieds. Affiche une ligne par processus (numéro, unité
# systemd, durée, commande) ; ne renvoie 1 que si la recherche elle-même échoue.
processus_hors_service() {
  local pids pid cgroup unite commande code=0
  pids="$(pgrep -u "$UTILISATEUR_APP")" || code=$?
  if [ "$code" = 1 ]; then return 0; fi   # aucun processus de cet utilisateur
  if [ "$code" != 0 ]; then
    erreur "recherche des processus de $UTILISATEUR_APP impossible (pgrep, code $code)."
    return 1
  fi
  for pid in $pids; do
    # Un processus peut se terminer pendant la recherche : il est alors ignoré.
    if ! cgroup="$(cat "/proc/$pid/cgroup" 2> /dev/null)"; then continue; fi
    unite="${cgroup##*/}"
    if [ "$unite" = "$SERVICE.service" ]; then continue; fi
    if ! commande="$(ps -o etime=,args= -p "$pid")"; then continue; fi
    printf '  processus %s (%s), depuis %s\n' "$pid" "${unite:-hors unité}" "$commande"
  done
}

basculer_lien() {
  ln -sfn "$1" "$LIEN_COURANT.nouveau" || return 1
  mv -Tf "$LIEN_COURANT.nouveau" "$LIEN_COURANT" || return 1
  info "Lien $LIEN_COURANT → $1"
}

# reset-failed remet aussi à zéro le compteur de démarrages de systemd
# (StartLimitBurst) : sans lui, un démarrage qui suit plusieurs plantages
# rapprochés (retour arrière, redéploiement) serait refusé.
demarrer_service() {
  systemctl reset-failed "$SERVICE" || return 1
  systemctl start "$SERVICE" || return 1
  info "Service $SERVICE démarré."
}

# Attend que /sante réponde 200 ; renvoie 1 après DELAI_SANTE secondes.
attendre_sante() {
  local debut=$SECONDS code="aucune tentative"
  while [ $((SECONDS - debut)) -lt "$DELAI_SANTE" ]; do
    if code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$URL_SANTE")"; then
      if [ "$code" = "200" ]; then
        info "$URL_SANTE répond 200 après $((SECONDS - debut)) s."
        return 0
      fi
      code="HTTP $code"
    else
      code="pas de réponse (code curl $?)"
    fi
    sleep 1
  done
  erreur "$URL_SANTE n'a pas répondu 200 en $DELAI_SANTE s (dernier résultat : $code)."
  if [ "$code" = "HTTP 503" ]; then
    erreur "503 = l'application tourne mais juge la base inutilisable (non initialisée ou schéma périmé) ; si c'est le schéma : $EXPLICATION_SCHEMA"
  fi
  return 1
}

# Copie cohérente (API de sauvegarde SQLite) faite par l'utilisateur edt, dans
# un fichier « .partiel » renommé seulement si le contrôle d'intégrité passe ;
# en cas d'échec (disque plein, base illisible), le fichier partiel est retiré.
sauvegarder_base() {
  if [ ! -e "$BASE" ]; then
    info "Aucune base existante (« $BASE ») : rien à sauvegarder."
    return 0
  fi
  local cible taille
  cible="$DOSSIER_SAUVEGARDES/edt-$(date -u '+%Y%m%dT%H%M%SZ')-avant-$VERSION.db"
  taille="$(du -h "$BASE" | cut -f1)"
  info "Sauvegarde de « $BASE » ($taille) vers $cible"
  runuser -u "$UTILISATEUR_APP" -- python3 -I - "$BASE" "$cible.partiel" <<'PY'
import os
import sqlite3
import sys

source, cible = sys.argv[1], sys.argv[2]
try:
    connexion_source = sqlite3.connect(source)
    try:
        connexion_cible = sqlite3.connect(cible)
        try:
            connexion_source.backup(connexion_cible)
            verdict = connexion_cible.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connexion_cible.close()
    finally:
        connexion_source.close()
    if verdict != "ok":
        raise RuntimeError(f"contrôle d'intégrité de la copie en échec : {verdict}")
except BaseException as erreur:
    if os.path.exists(cible):
        os.remove(cible)
    sys.exit(f"sauvegarde impossible : {erreur}")
print("contrôle d'intégrité de la sauvegarde : ok")
PY
  runuser -u "$UTILISATEUR_APP" -- mv -f "$cible.partiel" "$cible"
  SAUVEGARDE="$cible"
}

# Faite par l'utilisateur edt (root n'écrit pas dans ses dossiers). Les
# fichiers annexes de la base remplacée ne doivent pas être rejoués.
restaurer_base() {
  local fichier
  info "Restauration de la base depuis $1"
  runuser -u "$UTILISATEUR_APP" -- install -m 640 "$1" "$BASE.restauration" || return 1
  for fichier in "$BASE-wal" "$BASE-shm" "$BASE-journal"; do
    if [ -e "$fichier" ]; then runuser -u "$UTILISATEUR_APP" -- rm -f "$fichier" || return 1; fi
  done
  runuser -u "$UTILISATEUR_APP" -- mv -f "$BASE.restauration" "$BASE" || return 1
  info "Base restaurée."
}

# Supprime les plus anciens éléments d'une liste triée (noms horodatés) pour
# n'en garder que $1 ; ne supprime jamais la version en service.
supprimer_les_plus_anciens() {
  local a_garder=$1 courante i
  shift
  courante="$(readlink -f "$LIEN_COURANT")"
  for (( i = 1; i <= $# - a_garder; i++ )); do
    local chemin="${!i}"
    if [ "$chemin" != "$courante" ]; then
      rm -rf -- "$chemin"
      info "Supprimé (au-delà des $a_garder plus récents) : ${chemin##*/}"
    fi
  done
}

# =============================================================================
# PRÉPARATION
# =============================================================================
etape "Contrôles du serveur"
if [ "$EUID" -ne 0 ]; then
  echouer "ce script doit être lancé en root (sudo)."
fi
for chemin in "$DOSSIER_JOURNAUX_DEPLOIEMENT" "$FICHIER_ENV" /etc/systemd/system/edt.service \
  "$DOSSIER_SAUVEGARDES" /var/cache/edt/pip; do
  if [ ! -e "$chemin" ]; then
    echouer "$chemin absent : le serveur n'est pas (complètement) installé ; relancez deploy/installer-serveur.sh."
  fi
done
# tee doit accepter -i (ignorer Ctrl+C) et -p (survivre à une coupure SSH) :
# vérifié avant de lui confier toute la sortie.
if ! tee -i -p -a /dev/null < /dev/null; then
  echouer "la commande tee de ce serveur n'accepte pas les options -i -p."
fi
JOURNAL_DEPLOIEMENT="$DOSSIER_JOURNAUX_DEPLOIEMENT/$VERSION.log"
trap '' HUP
exec > >(tee -i -p -a "$JOURNAL_DEPLOIEMENT") 2>&1
trap 'echouer "interrompu par un signal (INT ou TERM), étape « $ETAPE »."' INT TERM
info "Déploiement de $VERSION (commit $SHA, référence $REF, par $AUTEUR)."
info "Journal complet : $JOURNAL_DEPLOIEMENT"

for commande in python3 curl runuser systemctl systemd-run journalctl flock ss pgrep ps; do
  if ! command -v "$commande" > /dev/null; then
    echouer "commande « $commande » introuvable sur le serveur (relancez deploy/installer-serveur.sh)."
  fi
done
exec 9> "$VERROU"
if ! flock -n 9; then
  autres="$(processus_hors_service)"
  if [ -n "$autres" ]; then
    autres="
Processus de l'application hors du service :
$autres"
  fi
  echouer "un autre déploiement, ou une commande « sudo edt », est en cours sur ce serveur (verrou $VERROU). Attendez sa fin puis relancez.$autres"
fi
if ! id -u "$UTILISATEUR_APP" > /dev/null; then
  echouer "utilisateur système « $UTILISATEUR_APP » absent (deploy/installer-serveur.sh)."
fi
if [ ! -e "$DOSSIER_VERSION/$MARQUE_ENVOI" ]; then
  echouer "envoi du code incomplet dans $DOSSIER_VERSION (témoin « $MARQUE_ENVOI » absent : connexion coupée pendant l'envoi ?). Relancez deploy/deployer.sh ; ce dossier sera supprimé par un prochain déploiement réussi."
fi
# df -P -k : format POSIX, compris par les coreutils GNU comme par uutils.
espace_libre_ko="$(df -P -k "$DOSSIER_VERSIONS" | awk 'NR == 2 { print $4 }')"
if ! [[ "$espace_libre_ko" =~ ^[0-9]+$ ]]; then
  echouer "espace libre illisible sous $DOSSIER_VERSIONS (df a renvoyé « $espace_libre_ko »)."
fi
if [ $((espace_libre_ko / 1024)) -lt "$ESPACE_MINIMAL_MO" ]; then
  echouer "seulement $((espace_libre_ko / 1024)) Mo libres sous $DOSSIER_VERSIONS (minimum $ESPACE_MINIMAL_MO Mo) : faites de la place (voir deploy/etat.sh)."
fi
info "Espace libre : $((espace_libre_ko / 1024)) Mo"

# Un fichier de la base (ou un -wal, -shm) créé par root, par exemple par une
# commande lancée hors du lanceur « sudo edt », empêcherait le service d'écrire :
# on le dit avant de toucher à quoi que ce soit.
etrangers="$(find "$DOSSIER_DONNEES" -maxdepth 2 ! -user "$UTILISATEUR_APP" -print)"
if [ -n "$etrangers" ]; then
  echouer "fichiers de $DOSSIER_DONNEES qui n'appartiennent pas à $UTILISATEUR_APP (créés par une commande lancée en root hors du lanceur « sudo edt » ?) : le service ne pourrait pas les modifier.
$etrangers
Corrigez avec « sudo chown $UTILISATEUR_APP:$UTILISATEUR_APP <fichier> » puis relancez."
fi

# Une commande « sudo edt » (chargement d'un jeu, import...) ou la sauvegarde
# quotidienne tient la base ouverte : la bascule la couperait en pleine écriture.
autres="$(processus_hors_service)"
if [ -n "$autres" ]; then
  echouer "des processus de l'application tournent hors du service et tiennent la base ouverte :
$autres
Attendez leur fin (ou arrêtez-les : « sudo systemctl stop <unité> ») puis relancez deploy/deployer.sh."
fi

if [ -L "$LIEN_COURANT" ]; then
  PRECEDENTE="$(readlink -f "$LIEN_COURANT")"
  if [ ! -d "$PRECEDENTE" ]; then
    avertir "le lien $LIEN_COURANT pointe vers un dossier absent ($PRECEDENTE) : pas de version précédente utilisable."
    PRECEDENTE=""
  fi
elif [ -e "$LIEN_COURANT" ]; then
  echouer "$LIEN_COURANT existe mais n'est pas un lien symbolique : corrigez à la main avant de déployer."
fi
if [ "$PRECEDENTE" = "$DOSSIER_VERSION" ]; then
  echouer "la version $VERSION est déjà celle en service."
fi
info "Version en service avant ce déploiement : ${PRECEDENTE:-aucune (premier déploiement)}"

cat > "$FICHIER_INFOS" <<EOF
version=$VERSION
commit=$SHA
reference=$REF
auteur=$AUTEUR
arbre_local_propre=$ARBRE_PROPRE
reset_base=$RESET_BASE
envoi=$(horodatage_iso)
EOF

etape "Environnement Python et dépendances"
python3 -m venv "$DOSSIER_VERSION/venv"
info "Installation du paquet edt et de ses dépendances (journal détaillé : $DOSSIER_VERSION/installation-pip.log)"
"$PYTHON_VENV" -m pip install --no-input --disable-pip-version-check --progress-bar off \
  --log "$DOSSIER_VERSION/installation-pip.log" "$DOSSIER_CODE"
"$PYTHON_VENV" -m pip freeze --all > "$DOSSIER_VERSION/paquets-installes.txt"
nombre_paquets="$(wc -l < "$DOSSIER_VERSION/paquets-installes.txt")"
info "$nombre_paquets paquets installés (liste : $DOSSIER_VERSION/paquets-installes.txt)."

etape "Chargement de la nouvelle version et base utilisée"
# L'application elle-même dit quelle base elle utilisera : mêmes valeurs que
# le service (blancs, valeurs par défaut...). Aucune base n'est ouverte ici.
if ! sortie="$(lancer_comme_app "$PYTHON_VENV" -c 'import edt.web.asgi
from sqlalchemy.engine import make_url
from edt.config import charger_configuration
print(make_url(charger_configuration().db_url).database or "")')"; then
  echouer "la nouvelle version ne se charge pas avec la configuration de production (erreur Python ci-dessus) : rien n'a été touché."
fi
BASE="${sortie##*$'\n'}"
info "import de edt.web.asgi : ok ; base utilisée : « $BASE »"
if [[ "$BASE" != /* ]]; then
  echouer "EDT_DB_URL doit désigner une base SQLite par un chemin absolu (exemple : sqlite:////var/lib/edt/edt.db) ; l'application lit « $BASE » (vérifiez $FICHIER_ENV)."
fi
if [[ "$BASE" != "$DOSSIER_DONNEES"/* ]]; then
  avertir "la base « $BASE » est hors de $DOSSIER_DONNEES : le service ne pourra pas y écrire (ReadWritePaths dans edt.service)."
fi
if [ ! -e "$BASE" ] && [ -n "$PRECEDENTE" ] && [ "$RESET_BASE" = 0 ]; then
  echouer "base « $BASE » introuvable alors qu'une version est en service : vérifiez EDT_DB_URL dans $FICHIER_ENV. Si la base a été retirée exprès, relancez avec --reset-base."
fi

if aide="$(lancer_comme_app "$PYTHON_VENV" -m edt verifier-schema --help 2>&1)"; then
  VERIFIER_SCHEMA=1
else
  code=$?
  if [ "$code" -ne 2 ]; then
    echouer "« python -m edt verifier-schema --help » a échoué (code $code) : $aide"
  fi
  VERIFIER_SCHEMA=0
  avertir "cette version ne fournit pas « python -m edt verifier-schema » (commande inconnue) : le schéma de la base ne sera PAS vérifié ; un changement de colonne passerait inaperçu."
fi

# init-db --reset sans question : si la version demande une confirmation
# (option --oui annoncée dans son aide), elle est donnée ici, la remise à zéro
# ayant déjà été confirmée sur le poste (taper EFFACER).
options_init=()
if [ "$RESET_BASE" = 1 ]; then
  options_init=(--reset)
  if ! aide="$(lancer_comme_app "$PYTHON_VENV" -m edt init-db --help 2>&1)"; then
    echouer "« python -m edt init-db --help » a échoué : $aide"
  fi
  if [[ "$aide" == *--oui* ]]; then options_init+=(--oui); fi
fi

pid_service="$(systemctl show -p MainPID --value "$SERVICE")"
ecouteurs="$(ss -Hltnp "sport = :$PORT_APP")"
if [ -n "$ecouteurs" ] && ! grep -q "pid=$pid_service," <<< "$ecouteurs"; then
  echouer "le port $PORT_APP est tenu par un autre programme que le service $SERVICE : $ecouteurs (arrêtez-le : docs/deploiement.md, « Port occupé »)."
fi

# =============================================================================
# BASCULE (à partir d'ici, tout échec déclenche le retour arrière)
# =============================================================================
PHASE="bascule"

etape "Arrêt du service $SERVICE"
systemctl stop "$SERVICE"
info "Service arrêté (un calcul d'emploi du temps en cours est interrompu)."
# Un processus démarré entre le contrôle de la préparation et l'arrêt : on ne
# touche pas à la base sous ses pieds. Le lanceur « sudo edt » est refusé
# pendant le déploiement (verrou) ; la sauvegarde quotidienne et un sqlite3
# lancé à la main ne prennent pas ce verrou. Limite : un tel processus qui
# démarre APRÈS ce contrôle (minuteur de 3 h 30 pendant la bascule) n'est pas
# détecté ; la sauvegarde quotidienne lit alors la base pendant son
# remplacement et peut échouer (unité en échec, signalée par etat.sh).
autres="$(processus_hors_service)"
if [ -n "$autres" ]; then
  echouer "un processus de l'application a démarré pendant le déploiement et tient la base ouverte :
$autres
Retour arrière : la version précédente est redémarrée ; relancez le déploiement après la fin de ce processus."
fi

etape "Sauvegarde de la base"
sauvegarder_base

etape "Initialisation de la base (python -m edt init-db${options_init:+ ${options_init[*]}})"
if [ "$RESET_BASE" = 1 ]; then
  info "Remise à zéro demandée : la base actuelle est sauvegardée dans « ${SAUVEGARDE:-aucune (pas de base)} » ; elle va être effacée puis recréée vide."
fi
BASE_MODIFIEE=1
if ! lancer_comme_app "$PYTHON_VENV" -m edt init-db "${options_init[@]}"; then
  echouer "init-db a échoué (message de l'application ci-dessus). S'il parle de colonnes ou d'un schéma différent du modèle : $EXPLICATION_SCHEMA"
fi

if [ "$VERIFIER_SCHEMA" = 1 ]; then
  etape "Vérification du schéma (python -m edt verifier-schema)"
  if ! lancer_comme_app "$PYTHON_VENV" -m edt verifier-schema; then
    echouer "$EXPLICATION_SCHEMA"
  fi
fi

etape "Bascule du lien $LIEN_COURANT"
basculer_lien "$DOSSIER_VERSION"

etape "Démarrage du service $SERVICE"
demarrer_service

etape "Vérification de santé ($URL_SANTE, $DELAI_SANTE s maximum)"
if ! attendre_sante; then
  echouer "la nouvelle version ne répond pas correctement : retour arrière."
fi

# =============================================================================
# FINALISATION (la nouvelle version est en service)
# =============================================================================
PHASE="finalisation"
etape "Finalisation"
horodatage_iso > "$DOSSIER_VERSION/$MARQUE_REUSSITE"
printf 'mise_en_service=%s\n' "$(horodatage_iso)" >> "$FICHIER_INFOS"
if version_appli="$(lancer_comme_app "$PYTHON_VENV" -m edt version)"; then
  info "Version annoncée par l'application : $version_appli"
  printf 'version_application=%s\n' "$version_appli" >> "$FICHIER_INFOS"
else
  avertir "« python -m edt version » a échoué (code $?) ; la version reste en service."
fi
if ! lancer_comme_app "$PYTHON_VENV" -m edt doctor; then
  avertir "« python -m edt doctor » signale un échec (détails ci-dessus) ; la version reste en service."
fi

# Versions : un dossier sans témoin de réussite, antérieur à ce déploiement
# (envoi coupé, ou déploiement annulé par un retour arrière), ne servira jamais :
# il est supprimé (son journal reste dans /var/log/edt-deploiements). Parmi les
# versions mises en service, les 5 plus récentes sont gardées.
versions=()
for chemin in "$DOSSIER_VERSIONS"/*/; do
  chemin="${chemin%/}"
  if [ ! -d "$chemin" ]; then continue; fi
  if [ ! -e "$chemin/$MARQUE_REUSSITE" ] && [[ "${chemin##*/}" < "$VERSION" ]]; then
    rm -rf -- "$chemin"
    info "Supprimé (jamais mis en service : envoi coupé ou déploiement annulé) : ${chemin##*/}"
    continue
  fi
  versions+=("$chemin")
done
supprimer_les_plus_anciens "$VERSIONS_A_GARDER" "${versions[@]}"
# Sauvegardes du déploiement : partielles laissées par un échec antérieur, puis
# les 10 plus récentes. Les sauvegardes quotidiennes (quotidienne-*) et
# manuelles (manuelle-*) ont leur propre rotation.
for chemin in "$DOSSIER_SAUVEGARDES"/edt-*.partiel; do
  if [ -e "$chemin" ]; then
    rm -f -- "$chemin"
    info "Sauvegarde partielle supprimée : ${chemin##*/}"
  fi
done
sauvegardes=()
for chemin in "$DOSSIER_SAUVEGARDES"/edt-*.db; do
  if [ -e "$chemin" ]; then sauvegardes+=("$chemin"); fi
done
supprimer_les_plus_anciens "$SAUVEGARDES_A_GARDER" "${sauvegardes[@]}"
noter_historique "RÉUSSI"
info "Déploiement de $VERSION réussi."
