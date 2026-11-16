#!/usr/bin/env bash
# =============================================================================
# deploy/deployer.sh — met en ligne une version de l'application EDT.
#
# À lancer depuis le POSTE DU DÉVELOPPEUR (Git Bash), depuis n'importe quel
# dossier :
#   deploy/deployer.sh                  déploie le commit HEAD
#   deploy/deployer.sh --ref v1.2       déploie une étiquette, une branche ou un commit
#   deploy/deployer.sh --reset-base     recrée la base vide (sauvegarde + taper EFFACER)
#                                       obligatoire après un changement de schéma
#
# Déroulement (détail dans docs/deploiement.md) :
#   1. contrôles locaux : outils, clé, référence git, arbre de travail ;
#   2. contrôles du serveur : connexion, sudo, installation faite et à jour ;
#   3. envoi du code : git archive <commit> | ssh ... tar -x
#      vers /opt/edt/versions/<date UTC>-<sha>/code, puis témoin envoi-termine ;
#   4. sur le serveur (deploy/deployer-distant.sh) : aucune commande « sudo edt »
#      en cours, venv et dépendances, chargement, arrêt du service, sauvegarde
#      de la base, init-db, verifier-schema, bascule du lien /opt/edt/courant,
#      redémarrage, attente de /sante (30 s) ; en cas d'échec : journal + retour
#      arrière automatique ;
#   5. vérification informative de l'URL publique (n'annule rien).
#
# Paramètres : EDT_SSH_HOTE, EDT_SSH_UTILISATEUR, EDT_SSH_CLE, EDT_DOMAINE,
# lus dans deploy/serveur.env (modèle deploy/serveur.env.exemple ; détail dans
# deploy/commun.sh).
# Code de sortie : 0 si la nouvelle version est en service (éventuellement
# avec avertissements), non nul sinon.
# =============================================================================
set -euo pipefail
set -o errtrace   # le piège ERR s'applique aussi à l'intérieur des fonctions

DOSSIER_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/commun.sh
. "$DOSSIER_DEPLOY/commun.sh"
trap 'sur_erreur $? $LINENO "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR

usage() { sed -n '3,11p' "${BASH_SOURCE[0]}"; }

REF="HEAD"
RESET_BASE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ref)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        erreur "--ref attend une valeur."
        exit 2
      fi
      REF="$2"
      shift 2
      ;;
    --ref=*) REF="${1#--ref=}"; shift ;;
    --reset-base) RESET_BASE=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *)
      erreur "option inconnue : $1"
      usage >&2
      exit 2
      ;;
  esac
done

# -----------------------------------------------------------------------------
etape "Contrôles sur le poste local"
verifier_poste_local
RACINE="$(git -C "$DOSSIER_DEPLOY" rev-parse --show-toplevel)"
info "Dépôt : $RACINE"

etape "Résolution de la référence git « $REF »"
if ! SHA="$(git -C "$RACINE" rev-parse --verify --quiet "$REF^{commit}")"; then
  erreur "référence git inconnue : « $REF » (essayez « git fetch » si elle vient d'être poussée)."
  exit 1
fi
SHA_COURT="${SHA:0:8}"
for chemin in pyproject.toml src/edt; do
  if ! git -C "$RACINE" cat-file -e "$SHA:$chemin"; then
    erreur "le commit $SHA_COURT ne contient pas « $chemin » : ce n'est pas une version déployable (il faut pyproject.toml à la racine et le paquet dans src/edt)."
    exit 1
  fi
done
RESUME_COMMIT="$(git -C "$RACINE" log -1 --format='%s (%an, %ad)' --date=short "$SHA")"
info "Commit $SHA_COURT : $RESUME_COMMIT"

etape "Vérification de l'arbre de travail"
MODIFICATIONS="$(git -C "$RACINE" status --porcelain)"
if [ -n "$MODIFICATIONS" ]; then
  ARBRE_PROPRE=0
  avertir "le dossier local contient des modifications non commitées. Elles ne seront PAS déployées : seul le commit $SHA_COURT est envoyé. Fichiers concernés (20 premiers) :"
  printf '%s\n' "$MODIFICATIONS" | sed -n '1,20p' >&2
else
  ARBRE_PROPRE=1
  info "Arbre de travail propre."
fi
BRANCHES_DISTANTES="$(git -C "$RACINE" branch -r --contains "$SHA")"
if [ -z "$BRANCHES_DISTANTES" ]; then
  avertir "le commit $SHA_COURT n'est sur aucune branche distante connue (git push oublié ?) : l'équipe ne le retrouvera pas sur GitHub."
fi

# Informations transmises au serveur : uniquement des caractères sûrs, car
# elles passent dans une ligne de commande ssh.
if ! AUTEUR="$(git -C "$RACINE" config user.name)"; then
  info "git user.name non configuré : auteur noté « inconnu »."
  AUTEUR="inconnu"
fi
AUTEUR="$(printf '%s' "$AUTEUR" | LC_ALL=C tr -c 'A-Za-z0-9._-' '_')"
REF_NETTOYEE="$(printf '%s' "$REF" | LC_ALL=C tr -c 'A-Za-z0-9._/-' '_')"

if [ "$RESET_BASE" = 1 ]; then
  etape "Confirmation de la remise à zéro de la base"
  avertir "--reset-base : la base de PRODUCTION de $EDT_DOMAINE ($EDT_SSH_HOTE) va être EFFACÉE puis recréée vide (à repeupler ensuite, par exemple avec « sudo edt jeu ... »). Une sauvegarde vérifiée est faite juste avant, dans /var/lib/edt/sauvegardes ; si elle échoue, rien n'est effacé."
  printf 'Pour confirmer, tapez exactement EFFACER puis Entrée : '
  if ! read -r REPONSE; then
    erreur "aucune réponse lue : remise à zéro annulée, rien n'a été fait."
    exit 1
  fi
  if [ "$REPONSE" != "EFFACER" ]; then
    erreur "confirmation incorrecte (« $REPONSE ») : rien n'a été fait."
    exit 1
  fi
  info "Remise à zéro confirmée."
fi

# -----------------------------------------------------------------------------
etape "Connexion au serveur $SSH_CIBLE"
verifier_connexion

# Nom de version horodaté par l'horloge du SERVEUR, en UTC : la rotation des
# versions se fie à l'ordre des noms, qui suit ainsi l'ordre des déploiements
# quel que soit le poste qui les lance ; pas d'heure répétée au passage à
# l'heure d'hiver.
if ! HORODATAGE_SERVEUR="$(ssh_serveur "date -u '+%Y%m%d-%H%M%S'")" \
  || ! [[ "$HORODATAGE_SERVEUR" =~ ^[0-9]{8}-[0-9]{6}$ ]]; then
  erreur "lecture de l'heure du serveur impossible (réponse : « ${HORODATAGE_SERVEUR:-aucune} ») : rien n'a été envoyé."
  exit 1
fi
VERSION="$HORODATAGE_SERVEUR-$SHA_COURT"
DOSSIER_VERSION_DISTANT="/opt/edt/versions/$VERSION"
info "Nom de la version : $VERSION"

etape "Vérification de l'installation du serveur"
# Fichiers que installer-serveur.sh copie tels quels : source dans deploy/ puis
# destination sur le serveur. Leurs empreintes sont comparées pour signaler une
# installation à refaire. Non comparés : edt.env.exemple (copié une seule fois,
# jamais écrasé) et les sites nginx (nom de domaine substitué à l'installation).
FICHIERS_INSTALLES=(
  edt.service                /etc/systemd/system/edt.service
  edt-sauvegarde.service     /etc/systemd/system/edt-sauvegarde.service
  edt-sauvegarde.timer       /etc/systemd/system/edt-sauvegarde.timer
  lanceur-edt.sh             /usr/local/bin/edt
  nginx-edt-application.conf /etc/nginx/snippets/edt-application.conf
  erreur-502.html            /var/www/edt-erreurs/erreur-502.html
  erreur-413.html            /var/www/edt-erreurs/erreur-413.html
)
SOURCES=()
DESTINATIONS=()
for (( i = 0; i < ${#FICHIERS_INSTALLES[@]}; i += 2 )); do
  SOURCES+=("${FICHIERS_INSTALLES[i]}")
  DESTINATIONS+=("${FICHIERS_INSTALLES[i + 1]}")
done
# Le serveur renvoie une empreinte (ou « absent ») par destination, dans l'ordre.
if ! EMPREINTES_SERVEUR="$(executer_script_distant verification-installation "${DESTINATIONS[@]}" <<'DISTANT'
set -euo pipefail
manquant=0
for chemin in /etc/edt/edt.env /etc/systemd/system/edt.service /opt/edt/versions \
  /var/lib/edt/sauvegardes /var/log/edt-deploiements /etc/nginx/sites-enabled/edt; do
  if [ ! -e "$chemin" ]; then
    echo "manquant sur le serveur : $chemin" >&2
    manquant=1
  fi
done
if [ "$manquant" = 1 ]; then exit 1; fi
for chemin in "$@"; do
  if [ -e "$chemin" ]; then sha256sum "$chemin" | cut -d' ' -f1; else echo "absent"; fi
done
DISTANT
)"; then
  erreur "le serveur n'est pas (complètement) installé : lancez deploy/installer-serveur.sh (docs/deploiement.md, « Première installation »)."
  exit 1
fi
EMPREINTES_LOCALES="$(cd "$DOSSIER_DEPLOY" && sha256sum "${SOURCES[@]}" | cut -d' ' -f1)"
mapfile -t SERVEUR <<< "$EMPREINTES_SERVEUR"
mapfile -t LOCALES <<< "$EMPREINTES_LOCALES"
DIFFERENTS=()
for i in "${!SOURCES[@]}"; do
  if [ "${SERVEUR[i]:-absent}" != "${LOCALES[i]}" ]; then DIFFERENTS+=("${SOURCES[i]}"); fi
done
if [ "${#DIFFERENTS[@]}" -eq 0 ]; then
  info "Installation du serveur : ok, à jour."
else
  avertir "${DIFFERENTS[*]} : absent(s) du serveur ou différent(s) de ce dossier deploy/ : relancez installer-serveur.sh pour appliquer la version du dépôt (docs/deploiement.md, section 3, étapes 1 et 2). Le déploiement continue avec la configuration actuelle du serveur."
fi

etape "Envoi du code ($SHA_COURT) vers $DOSSIER_VERSION_DISTANT/code"
# Le témoin envoi-termine n'est écrit qu'après une extraction complète : un
# envoi coupé laisse un dossier sans témoin, refusé par le serveur puis
# supprimé par le déploiement réussi suivant.
CODE_ENVOI=0
git -C "$RACINE" archive --format=tar "$SHA" \
  | ssh_serveur "if sudo -n test -e '$DOSSIER_VERSION_DISTANT'; then exit 3; fi
sudo -n mkdir '$DOSSIER_VERSION_DISTANT' '$DOSSIER_VERSION_DISTANT/code' \
  && sudo -n tar -x --no-same-owner --no-same-permissions -f - -C '$DOSSIER_VERSION_DISTANT/code' \
  && sudo -n touch '$DOSSIER_VERSION_DISTANT/envoi-termine'" || CODE_ENVOI=$?
case "$CODE_ENVOI" in
  0) info "Code envoyé." ;;
  3)
    erreur "le dossier $DOSSIER_VERSION_DISTANT existe déjà sur le serveur (même commit déployé dans la même seconde ?) : relancez deploy/deployer.sh."
    exit 1
    ;;
  *)
    erreur "envoi du code interrompu ou refusé (code $CODE_ENVOI) : rien n'est en service de cette version ; le dossier incomplet sera supprimé par le prochain déploiement réussi. Relancez deploy/deployer.sh."
    exit 1
    ;;
esac

etape "Déploiement sur le serveur"
info "(Une coupure de la connexion ou Ctrl+C n'interrompt pas le travail côté serveur, qui va au bout.)"
CODE_DISTANT=0
executer_script_distant deployer-distant.sh "$VERSION" "$SHA" "$REF_NETTOYEE" "$RESET_BASE" "$AUTEUR" "$ARBRE_PROPRE" \
  < "$DOSSIER_DEPLOY/deployer-distant.sh" || CODE_DISTANT=$?
case "$CODE_DISTANT" in
  0) ;;
  3) avertir "version $VERSION en service, mais finalisation incomplète : voir les messages [serveur] ci-dessus et /var/log/edt-deploiements/$VERSION.log." ;;
  255)
    erreur "connexion SSH coupée (code 255) : le déploiement a pu continuer sur le serveur. Journal complet : /var/log/edt-deploiements/$VERSION.log ; lancez deploy/etat.sh pour voir où il en est."
    exit 1
    ;;
  *)
    erreur "le déploiement de $VERSION a échoué sur le serveur (code $CODE_DISTANT) : voir les messages [serveur] ci-dessus."
    exit 1
    ;;
esac

etape "Vérification de l'URL publique (informative)"
if verifier_url_publique; then
  ETAT_PUBLIC="ok"
else
  ETAT_PUBLIC="EN ÉCHEC (voir l'avertissement ci-dessus ; la version reste en service)"
fi

etape "Terminé"
info "Version $VERSION en service (commit $SHA_COURT)."
info "Santé locale sur le serveur : ok. URL publique : $ETAT_PUBLIC."
if [ "$RESET_BASE" = 1 ]; then
  info "La base est vide : chargez des données, par exemple « sudo edt jeu --taille moyen » sur le serveur (docs/deploiement.md, « Lancer une commande de l'application »). L'ancienne base est dans /var/lib/edt/sauvegardes (nom affiché à l'étape « Sauvegarde de la base »)."
fi
