#!/usr/bin/env bash
# =============================================================================
# deploy/deployer-distant.sh — partie SERVEUR du déploiement de l'application EDT.
#
# Ce script est envoyé et lancé par deploy/deployer.sh (via ssh, en root avec
# sudo). Il travaille sur une version dont le code vient d'être envoyé dans
# /opt/edt/versions/<version>/code. Pour déboguer, on peut le relancer à la
# main sur le serveur, sur une version déjà envoyée :
#   sudo bash deployer-distant.sh <version> <commit> <référence> <reset 0|1> <auteur> <arbre_propre 0|1>
#
# Déroulement :
#   PRÉPARATION : le service en place n'est pas touché ; un échec laisse
#   l'ancienne version en ligne.
#     1. verrou (un seul déploiement à la fois) et contrôles ;
#     2. environnement Python (venv) propre à la version + dépendances ;
#     3. test de chargement de edt.web.asgi (base et journaux jetables) ;
#   BASCULE : tout échec déclenche le retour arrière automatique.
#     4. arrêt du service ;
#     5. sauvegarde de la base (API de sauvegarde SQLite + contrôle
#        d'intégrité), 10 sauvegardes conservées ;
#     6. python -m edt init-db (avec --reset si demandé) ;
#     7. bascule atomique du lien /opt/edt/courant vers la nouvelle version ;
#     8. démarrage, puis attente de GET http://127.0.0.1:8000/sante → 200
#        (30 s maximum) ;
#   FINALISATION : version marquée « réussie », 5 versions conservées,
#   historique.
#
# Retour arrière : affiche les 50 dernières lignes du journal du service,
# restaure la base sauvegardée, remet le lien sur la version précédente et
# redémarre. Premier déploiement (aucune version précédente) : le service
# reste arrêté et le lien /opt/edt/courant est retiré ; le site affiche alors
# la page « application indisponible ».
#
# Toute la sortie est aussi écrite dans /var/log/edt/deploiements/<version>.log
# et le script va au bout même si la connexion SSH est coupée en cours de route.
# =============================================================================
set -euo pipefail
set -o errtrace   # le piège ERR s'applique aussi à l'intérieur des fonctions

# --- Paramètres (cohérents avec edt.service, installer-serveur.sh, nginx) ----
readonly SERVICE="edt"
readonly UTILISATEUR_APP="edt"
readonly DOSSIER_VERSIONS="/opt/edt/versions"
readonly LIEN_COURANT="/opt/edt/courant"
readonly FICHIER_ENV="/etc/edt/edt.env"
readonly DOSSIER_DONNEES="/var/lib/edt"
readonly DOSSIER_SAUVEGARDES="/var/lib/edt/sauvegardes"
readonly DOSSIER_JOURNAUX_DEPLOIEMENT="/var/log/edt/deploiements"
readonly HISTORIQUE="/var/log/edt/historique-deploiements.log"
readonly VERROU="/run/lock/edt-deploiement.lock"
readonly URL_SANTE="http://127.0.0.1:8000/sante"
readonly DELAI_SANTE=30            # secondes
readonly VERSIONS_A_GARDER=5
readonly SAUVEGARDES_A_GARDER=10
readonly LIGNES_JOURNAL=50
readonly ESPACE_MINIMAL_MO=1024    # place libre exigée avant de créer un venv
readonly MARQUE_REUSSITE="deploiement-reussi"
readonly PYTHON_SYSTEME="python3"
export PIP_CACHE_DIR="/var/cache/edt/pip"

# --- Messages horodatés ------------------------------------------------------
ETAPE="démarrage"
horodatage() { date '+%Y-%m-%d %H:%M:%S'; }
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
if ! [[ "$VERSION" =~ ^[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$ ]] \
  || ! [[ "$SHA" =~ ^[0-9a-f]{40,64}$ ]] \
  || ! [[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] \
  || ! [[ "$RESET_BASE" =~ ^[01]$ ]] \
  || ! [[ "$AUTEUR" =~ ^[A-Za-z0-9._-]+$ ]] \
  || ! [[ "$ARBRE_PROPRE" =~ ^[01]$ ]]; then
  erreur "arguments invalides : $*"
  exit 2
fi
readonly DOSSIER_VERSION="$DOSSIER_VERSIONS/$VERSION"
readonly DOSSIER_CODE="$DOSSIER_VERSION/code"
readonly DOSSIER_VENV="$DOSSIER_VERSION/venv"
readonly PYTHON_VENV="$DOSSIER_VENV/bin/python"
readonly FICHIER_INFOS="$DOSSIER_VERSION/DEPLOIEMENT.txt"

# --- État du déploiement (lu par la gestion d'erreur) -------------------------
PHASE="preparation"     # preparation | bascule | finalisation
PRECEDENTE=""           # dossier de la version en service avant ce déploiement
SAUVEGARDE=""           # sauvegarde de la base faite par ce déploiement
BASE=""                 # chemin du fichier SQLite (lu dans edt.env)
BASE_MODIFIEE=0         # 1 dès que init-db a été lancé
RESULTAT_RETOUR=""      # résultat du retour arrière, pour l'historique
VARIABLES_APP=()        # contenu de edt.env (NOM=valeur), pour lancer_comme_app

# --- Gestion des erreurs -----------------------------------------------------
# Toute commande en échec (hors condition testée) arrive ici : on dit quelle
# étape a échoué et à quelle ligne, puis on décide quoi faire selon la phase.
sur_erreur() {
  local code=$1 ligne=$2 commande=$3
  case "$commande" in
    return*) commande="une fonction appelée à cette ligne (cause dans le message juste au-dessus)" ;;
  esac
  if [ "$BASH_SUBSHELL" -gt 0 ]; then
    # Erreur dans une sous-commande « $( ) » ou « ( ) » : on la signale et on
    # sort ; c'est le script principal qui gère l'échec (une seule fois).
    erreur "« $commande » a renvoyé le code $code (ligne $ligne de deployer-distant.sh)."
    exit "$code"
  fi
  trap - ERR
  set +e
  erreur "échec à l'étape « $ETAPE » : « $commande » a renvoyé le code $code (ligne $ligne de deployer-distant.sh)."
  abandonner
}
trap 'sur_erreur $? $LINENO "$BASH_COMMAND"' ERR

# Échec détecté par le script lui-même (contrôle, délai dépassé...).
echouer() {
  if [ "$BASH_SUBSHELL" -gt 0 ]; then
    erreur "$*"
    exit 1
  fi
  trap - ERR
  set +e
  erreur "$*"
  abandonner
}

abandonner() {
  local etape_en_echec="$ETAPE"
  case "$PHASE" in
    preparation)
      info "Le service en place n'a pas été touché : l'ancienne version reste en ligne."
      if [ -d "$DOSSIER_VERSION" ]; then
        info "Le dossier $DOSSIER_VERSION reste sur le disque pour diagnostic ; un déploiement réussi le supprimera (après un délai de 2 h)."
      fi
      noter_historique "ECHEC en préparation (étape : $etape_en_echec)"
      ;;
    bascule)
      afficher_journal_service
      retour_arriere
      noter_historique "ECHEC en bascule (étape : $etape_en_echec) ; $RESULTAT_RETOUR"
      ;;
    *)
      erreur "la nouvelle version est en service, mais la finalisation a échoué (voir ci-dessus)."
      noter_historique "EN SERVICE, finalisation en échec (étape : $etape_en_echec)"
      ;;
  esac
  info "Journal complet de ce déploiement : ${JOURNAL_DEPLOIEMENT:-non créé}"
  exit 1
}

afficher_journal_service() {
  info "----- $LIGNES_JOURNAL dernières lignes du journal du service $SERVICE -----"
  if ! journalctl -u "$SERVICE" -n "$LIGNES_JOURNAL" --no-pager -o short-iso; then
    erreur "lecture du journal impossible (journalctl)."
  fi
  info "----- fin du journal du service -----"
}

noter_historique() {
  if ! printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$(horodatage)" "$VERSION" "$SHA" "$REF" "$AUTEUR" "$1" >> "$HISTORIQUE"; then
    erreur "impossible d'écrire l'historique $HISTORIQUE."
  fi
}

# Appelé en phase de bascule, après une erreur (set +e actif) : chaque commande
# est vérifiée une par une et tout échec est signalé.
retour_arriere() {
  etape "Retour arrière"
  local ok=1
  if ! systemctl stop "$SERVICE"; then
    erreur "arrêt du service impossible."
    ok=0
  fi
  if [ "$BASE_MODIFIEE" = 1 ]; then
    if [ -n "$SAUVEGARDE" ]; then
      if ! restaurer_base "$SAUVEGARDE"; then
        erreur "restauration de la base impossible ; la sauvegarde reste disponible : $SAUVEGARDE"
        ok=0
      fi
    else
      avertir "il n'y avait pas de base avant ce déploiement : rien à restaurer ; la base éventuellement créée par init-db est conservée ($BASE)."
    fi
  fi
  if [ -z "$PRECEDENTE" ]; then
    if [ -L "$LIEN_COURANT" ] && ! rm -f -- "$LIEN_COURANT"; then
      erreur "impossible de retirer le lien $LIEN_COURANT."
    fi
    RESULTAT_RETOUR="premier déploiement : service laissé arrêté"
    erreur "aucune version précédente (premier déploiement) : le service reste arrêté et le lien $LIEN_COURANT est retiré. Le site affiche la page « application indisponible ». Corrigez le problème puis relancez deploy/deployer.sh."
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
# Les fonctions ci-dessous peuvent être appelées dans une condition (où bash
# ignore « set -e ») : chaque commande y est donc suivie de « || return 1 ».

# Lit /etc/edt/edt.env (une ligne NOM=valeur, comme systemd) dans VARIABLES_APP.
charger_environnement() {
  local ligne numero=0
  VARIABLES_APP=()
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    numero=$((numero + 1))
    ligne="${ligne%$'\r'}"
    case "$ligne" in '' | '#'* | ';'*) continue ;; esac
    if ! [[ "$ligne" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      echouer "ligne $numero de $FICHIER_ENV illisible : « $ligne » (format attendu : NOM=valeur, sans espace autour du =)."
    fi
    case "${ligne#*=}" in
      \"* | \'*) echouer "ligne $numero de $FICHIER_ENV : retirez les guillemets autour de la valeur (« $ligne »)." ;;
    esac
    VARIABLES_APP+=("$ligne")
  done < "$FICHIER_ENV"
}

# Affiche la valeur d'une variable de edt.env (dernière occurrence ; vide si absente).
valeur_env() {
  local nom=$1 valeur="" ligne
  for ligne in "${VARIABLES_APP[@]}"; do
    if [ "${ligne%%=*}" = "$nom" ]; then valeur="${ligne#*=}"; fi
  done
  printf '%s' "$valeur"
}

# Lance une commande en tant qu'utilisateur edt, avec l'environnement de edt.env
# (comme le service), dans le dossier du code de la nouvelle version.
lancer_comme_app() {
  (
    cd "$DOSSIER_CODE" || exit 1
    runuser -u "$UTILISATEUR_APP" -- env -i \
      PATH=/usr/local/bin:/usr/bin:/bin HOME="$DOSSIER_DONNEES" LANG=C.UTF-8 PYTHONUNBUFFERED=1 \
      "${VARIABLES_APP[@]}" "$@"
  )
}

basculer_lien() {
  local cible=$1
  ln -sfn "$cible" "$LIEN_COURANT.nouveau" || return 1
  mv -Tf "$LIEN_COURANT.nouveau" "$LIEN_COURANT" || return 1
  info "Lien $LIEN_COURANT → $cible"
}

demarrer_service() {
  if systemctl is-failed --quiet "$SERVICE"; then
    systemctl reset-failed "$SERVICE" || return 1
  fi
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
  return 1
}

# Copie cohérente de la base, faite par l'utilisateur edt (les fichiers -wal et
# -shm éventuellement créés lui appartiennent ainsi) puis contrôle d'intégrité.
sauvegarder_base() {
  if [ ! -e "$BASE" ]; then
    info "Aucune base existante ($BASE) : rien à sauvegarder."
    return 0
  fi
  local cible taille
  cible="$DOSSIER_SAUVEGARDES/edt-$(date '+%Y%m%d-%H%M%S')-avant-$VERSION.db"
  taille="$(du -h "$BASE" | cut -f1)"
  info "Sauvegarde de $BASE ($taille) vers $cible"
  runuser -u "$UTILISATEUR_APP" -- "$PYTHON_SYSTEME" -I - "$BASE" "$cible" <<'PY'
import sqlite3
import sys

source, cible = sys.argv[1], sys.argv[2]
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
    sys.exit(f"contrôle d'intégrité de la sauvegarde en échec : {verdict}")
print("contrôle d'intégrité de la sauvegarde : ok")
PY
  SAUVEGARDE="$cible"
  info "Sauvegarde faite : $SAUVEGARDE"
}

restaurer_base() {
  local sauvegarde=$1 temporaire="$BASE.restauration" fichier
  info "Restauration de la base depuis $sauvegarde"
  install -o "$UTILISATEUR_APP" -g "$UTILISATEUR_APP" -m 640 "$sauvegarde" "$temporaire" || return 1
  # Les fichiers annexes de la base remplacée ne doivent pas être rejoués sur la sauvegarde.
  for fichier in "$BASE-wal" "$BASE-shm" "$BASE-journal"; do
    if [ -e "$fichier" ]; then rm -f -- "$fichier" || return 1; fi
  done
  mv -f -- "$temporaire" "$BASE" || return 1
  info "Base restaurée."
}

nettoyer_sauvegardes() {
  local liste nom rang=0
  liste="$(find "$DOSSIER_SAUVEGARDES" -maxdepth 1 -type f -name 'edt-*.db' -printf '%f\n' | sort -r)"
  while IFS= read -r nom; do
    [ -n "$nom" ] || continue
    rang=$((rang + 1))
    if [ "$rang" -gt "$SAUVEGARDES_A_GARDER" ]; then
      rm -f -- "${DOSSIER_SAUVEGARDES:?}/$nom"
      info "Ancienne sauvegarde supprimée : $nom"
    fi
  done <<< "$liste"
}

supprimer_version() {
  local chemin=$1 raison=$2
  case "$chemin" in
    "$DOSSIER_VERSIONS"/?*) ;;
    *) echouer "refus de supprimer un chemin inattendu : $chemin" ;;
  esac
  rm -rf -- "$chemin"
  info "Version supprimée ($raison) : ${chemin##*/}"
}

# Garde la version en service et les plus récentes versions réussies
# (VERSIONS_A_GARDER au total). Les versions jamais mises en service (échec)
# sont supprimées après 2 heures (délai laissé à un déploiement concurrent en
# cours d'envoi).
nettoyer_versions() {
  local courante liste nom chemin recente rang=0
  courante="$(readlink -f "$LIEN_COURANT")"
  liste="$(find "$DOSSIER_VERSIONS" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort -r)"
  while IFS= read -r nom; do
    [ -n "$nom" ] || continue
    chemin="$DOSSIER_VERSIONS/$nom"
    if [ "$chemin" = "$courante" ]; then
      rang=$((rang + 1))
    elif [ -e "$chemin/$MARQUE_REUSSITE" ]; then
      rang=$((rang + 1))
      if [ "$rang" -gt "$VERSIONS_A_GARDER" ]; then
        supprimer_version "$chemin" "au-delà des $VERSIONS_A_GARDER plus récentes"
      fi
    else
      recente="$(find "$chemin" -maxdepth 0 -mmin -120)"
      if [ -z "$recente" ]; then
        supprimer_version "$chemin" "jamais mise en service"
      fi
    fi
  done <<< "$liste"
}

# =============================================================================
# PRÉPARATION
# =============================================================================
etape "Contrôles du serveur"
if [ "$EUID" -ne 0 ]; then
  echouer "ce script doit être lancé en root (sudo)."
fi
if [ ! -d /var/log/edt ]; then
  echouer "/var/log/edt absent : le serveur n'est pas installé (deploy/installer-serveur.sh)."
fi
install -d -o root -g root -m 750 "$DOSSIER_JOURNAUX_DEPLOIEMENT"
JOURNAL_DEPLOIEMENT="$DOSSIER_JOURNAUX_DEPLOIEMENT/$VERSION.log"
# Une coupure SSH ne doit pas interrompre le déploiement : SIGHUP est ignoré et
# la sortie passe par « tee -p », qui continue d'écrire le fichier journal même
# quand plus personne ne lit l'écran.
trap '' HUP
exec > >(tee -p -a "$JOURNAL_DEPLOIEMENT") 2>&1
info "Déploiement de $VERSION (commit $SHA, référence $REF, par $AUTEUR)."
info "Journal complet : $JOURNAL_DEPLOIEMENT"

exec 9> "$VERROU"
if ! flock -n 9; then
  echouer "un autre déploiement est en cours sur ce serveur (verrou $VERROU). Attendez sa fin puis relancez."
fi

for commande in "$PYTHON_SYSTEME" curl runuser systemctl journalctl flock find; do
  if ! command -v "$commande" > /dev/null; then
    echouer "commande « $commande » introuvable sur le serveur (relancez deploy/installer-serveur.sh)."
  fi
done
for chemin in "$FICHIER_ENV" /etc/systemd/system/edt.service "$DOSSIER_SAUVEGARDES"; do
  if [ ! -e "$chemin" ]; then
    echouer "$chemin absent : le serveur n'est pas (complètement) installé (deploy/installer-serveur.sh)."
  fi
done
if ! id -u "$UTILISATEUR_APP" > /dev/null; then
  echouer "utilisateur système « $UTILISATEUR_APP » absent (deploy/installer-serveur.sh)."
fi
if [ ! -e "$DOSSIER_CODE/pyproject.toml" ]; then
  echouer "code absent ou incomplet dans $DOSSIER_CODE (pyproject.toml introuvable) : l'envoi par deployer.sh a-t-il réussi ?"
fi

version_python="$("$PYTHON_SYSTEME" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
if ! "$PYTHON_SYSTEME" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echouer "Python $version_python est trop ancien (3.11 minimum)."
fi
info "Python du serveur : $version_python"

espace_libre_mo="$(df --output=avail -m "$DOSSIER_VERSIONS" | tail -n 1 | tr -d ' ')"
if [ "$espace_libre_mo" -lt "$ESPACE_MINIMAL_MO" ]; then
  echouer "seulement $espace_libre_mo Mo libres sous $DOSSIER_VERSIONS (minimum $ESPACE_MINIMAL_MO Mo) : faites de la place (voir deploy/etat.sh)."
fi
info "Espace libre : $espace_libre_mo Mo"

charger_environnement
url_base="$(valeur_env EDT_DB_URL)"
case "$url_base" in
  sqlite:////*) BASE="/${url_base#sqlite:////}"; BASE="${BASE%%\?*}" ;;
  "") echouer "EDT_DB_URL absente de $FICHIER_ENV." ;;
  *) echouer "EDT_DB_URL=« $url_base » : seule une base SQLite à chemin absolu est prise en charge (exemple : sqlite:////var/lib/edt/edt.db)." ;;
esac
case "$BASE" in
  "$DOSSIER_DONNEES"/*) ;;
  *) avertir "la base $BASE est hors de $DOSSIER_DONNEES : le service ne pourra pas y écrire (ReadWritePaths dans edt.service)." ;;
esac
info "Base de données : $BASE"

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
envoi=$(horodatage)
EOF

etape "Environnement Python et dépendances"
if [ -e "$DOSSIER_VENV" ]; then
  info "Le venv $DOSSIER_VENV existe déjà (relance manuelle) : il est complété."
fi
"$PYTHON_SYSTEME" -m venv "$DOSSIER_VENV"
info "Installation du paquet edt et de ses dépendances (journal détaillé : $DOSSIER_VERSION/installation-pip.log)"
"$PYTHON_VENV" -m pip install --no-input --disable-pip-version-check --progress-bar off \
  --log "$DOSSIER_VERSION/installation-pip.log" "$DOSSIER_CODE"
"$PYTHON_VENV" -m pip freeze --all > "$DOSSIER_VERSION/paquets-installes.txt"
nombre_paquets="$(wc -l < "$DOSSIER_VERSION/paquets-installes.txt")"
info "$nombre_paquets paquets installés (liste : $DOSSIER_VERSION/paquets-installes.txt)."

etape "Test de chargement de la nouvelle version"
# Base et journaux jetables : ce test ne touche ni la vraie base ni les vrais journaux.
dossier_test="$(runuser -u "$UTILISATEUR_APP" -- mktemp -d /tmp/edt-test-import.XXXXXX)"
if ! lancer_comme_app env "EDT_DB_URL=sqlite:///$dossier_test/test.db" "EDT_LOG_DIR=$dossier_test" \
  "$PYTHON_VENV" -c 'import edt.web.asgi; print("import de edt.web.asgi : ok")'; then
  rm -rf -- "$dossier_test"
  echouer "la nouvelle version ne se charge pas (import de edt.web.asgi impossible : voir l'erreur Python ci-dessus)."
fi
rm -rf -- "$dossier_test"

# =============================================================================
# BASCULE (à partir d'ici, tout échec déclenche le retour arrière)
# =============================================================================
PHASE="bascule"

etape "Arrêt du service $SERVICE"
systemctl stop "$SERVICE"
info "Service arrêté (un calcul d'emploi du temps en cours est interrompu)."

etape "Sauvegarde de la base"
sauvegarder_base
nettoyer_sauvegardes

if [ "$RESET_BASE" = 1 ]; then
  etape "Remise à zéro de la base (python -m edt init-db --reset)"
  BASE_MODIFIEE=1
  lancer_comme_app "$PYTHON_VENV" -m edt init-db --reset
else
  etape "Initialisation de la base (python -m edt init-db)"
  BASE_MODIFIEE=1
  lancer_comme_app "$PYTHON_VENV" -m edt init-db
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
horodatage > "$DOSSIER_VERSION/$MARQUE_REUSSITE"
printf 'mise_en_service=%s\n' "$(horodatage)" >> "$FICHIER_INFOS"

if version_appli="$(lancer_comme_app "$PYTHON_VENV" -m edt version)"; then
  info "Version annoncée par l'application : $version_appli"
  printf 'version_application=%s\n' "$version_appli" >> "$FICHIER_INFOS"
else
  avertir "« python -m edt version » a échoué (code $?) ; la version reste en service."
fi
if lancer_comme_app "$PYTHON_VENV" -m edt doctor; then
  info "Diagnostic « python -m edt doctor » : ok."
else
  avertir "« python -m edt doctor » signale un problème (code $?, détails ci-dessus) ; la version reste en service."
fi

nettoyer_versions
noter_historique "RÉUSSI"
info "Déploiement de $VERSION réussi."
