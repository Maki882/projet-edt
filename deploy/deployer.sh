#!/usr/bin/env bash
# =============================================================================
# deploy/deployer.sh — met en ligne une version de l'application EDT.
#
# À lancer depuis le POSTE DU DÉVELOPPEUR (Git Bash), depuis n'importe quel
# dossier :
#   deploy/deployer.sh                  déploie le commit HEAD
#   deploy/deployer.sh --ref v1.2       déploie une étiquette, une branche ou un commit
#   deploy/deployer.sh --reset-base     recrée la base vide (sauvegarde + confirmation)
#
# Déroulement (détail dans docs/deploiement.md) :
#   1. contrôles locaux : outils, clé, référence git, arbre de travail ;
#   2. contrôles du serveur : connexion, sudo, installation faite ;
#   3. envoi du code : git archive <commit> | ssh ... tar -x
#      vers /opt/edt/versions/<date>-<sha>/code ;
#   4. sur le serveur (deploy/deployer-distant.sh) : venv et dépendances,
#      test de chargement, arrêt du service, sauvegarde de la base, init-db,
#      bascule du lien /opt/edt/courant, redémarrage, attente de /sante (30 s) ;
#      en cas d'échec : journal du service + retour arrière automatique ;
#   5. vérification informative de l'URL publique (n'annule rien).
#
# Paramètres : EDT_SSH_HOTE, EDT_SSH_UTILISATEUR, EDT_SSH_CLE, EDT_DOMAINE
# (valeurs par défaut et explications dans deploy/commun.sh).
# Code de sortie : 0 si la nouvelle version est en service, non nul sinon.
# =============================================================================
set -euo pipefail
set -o errtrace   # le piège ERR s'applique aussi à l'intérieur des fonctions

DOSSIER_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/commun.sh
. "$DOSSIER_DEPLOY/commun.sh"
trap 'sur_erreur $? $LINENO "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR

usage() {
  cat <<'EOF'
Usage : deploy/deployer.sh [--ref <référence git>] [--reset-base]

  --ref <réf>     commit, branche ou étiquette à déployer (défaut : HEAD)
  --reset-base    recrée la base de données vide après l'avoir sauvegardée
                  (demande de taper EFFACER pour confirmer)
  -h, --help      affiche cette aide

Variables : EDT_SSH_HOTE, EDT_SSH_UTILISATEUR, EDT_SSH_CLE, EDT_DOMAINE
EOF
}

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
if grep -q $'\r' "$DOSSIER_DEPLOY/deployer-distant.sh"; then
  erreur "deploy/deployer-distant.sh a des fins de ligne Windows (CRLF) : bash échouerait sur le serveur. Restaurez-le avec « git checkout -- deploy/deployer-distant.sh »."
  exit 1
fi
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

# Nom de version et informations transmises au serveur : uniquement des
# caractères sûrs, car ils passent dans une ligne de commande ssh.
VERSION="$(date '+%Y%m%d-%H%M%S')-$SHA_COURT"
if ! AUTEUR="$(git -C "$RACINE" config user.name)"; then
  info "git user.name non configuré : auteur noté « inconnu »."
  AUTEUR="inconnu"
fi
AUTEUR="$(printf '%s' "$AUTEUR" | LC_ALL=C tr -c 'A-Za-z0-9._-' '_')"
REF_NETTOYEE="$(printf '%s' "$REF" | LC_ALL=C tr -c 'A-Za-z0-9._/-' '_')"
DOSSIER_VERSION_DISTANT="/opt/edt/versions/$VERSION"
info "Nom de la version : $VERSION"

if [ "$RESET_BASE" = 1 ]; then
  etape "Confirmation de la remise à zéro de la base"
  avertir "--reset-base : la base de PRODUCTION de $EDT_DOMAINE ($EDT_SSH_HOTE) va être EFFACÉE puis recréée vide. Une sauvegarde est faite juste avant (/var/lib/edt/sauvegardes)."
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

etape "Vérification de l'installation du serveur"
if ! executer_script_distant verification-installation <<'DISTANT'
set -euo pipefail
manquant=0
for chemin in /etc/edt/edt.env /etc/systemd/system/edt.service /opt/edt/versions \
  /var/lib/edt/sauvegardes /var/log/edt /etc/nginx/sites-enabled/edt; do
  if [ ! -e "$chemin" ]; then
    echo "manquant sur le serveur : $chemin" >&2
    manquant=1
  fi
done
exit "$manquant"
DISTANT
then
  erreur "le serveur n'est pas (complètement) installé : lancez d'abord deploy/installer-serveur.sh (docs/deploiement.md, « Première installation »)."
  exit 1
fi
info "Installation du serveur : ok."

etape "Envoi du code ($SHA_COURT) vers $DOSSIER_VERSION_DISTANT/code"
git -C "$RACINE" archive --format=tar "$SHA" \
  | ssh_serveur "sudo -n mkdir '$DOSSIER_VERSION_DISTANT' '$DOSSIER_VERSION_DISTANT/code' && sudo -n tar -x --no-same-owner --no-same-permissions -f - -C '$DOSSIER_VERSION_DISTANT/code'"
info "Code envoyé."

etape "Déploiement sur le serveur"
info "(Une coupure de la connexion ou Ctrl+C n'interrompt pas le travail côté serveur, qui va au bout.)"
CODE_DISTANT=0
executer_script_distant deployer-distant.sh "$VERSION" "$SHA" "$REF_NETTOYEE" "$RESET_BASE" "$AUTEUR" "$ARBRE_PROPRE" \
  < "$DOSSIER_DEPLOY/deployer-distant.sh" || CODE_DISTANT=$?
if [ "$CODE_DISTANT" -ne 0 ]; then
  erreur "le déploiement de $VERSION a échoué sur le serveur (code $CODE_DISTANT) : voir les messages [serveur] ci-dessus."
  if [ "$CODE_DISTANT" -eq 255 ]; then
    erreur "code 255 = connexion SSH coupée : le déploiement a pu continuer sur le serveur. Son journal complet : /var/log/edt/deploiements/$VERSION.log ; lancez deploy/etat.sh pour voir où il en est."
  fi
  exit 1
fi

etape "Vérification de l'URL publique (informative)"
if verifier_url_publique; then
  ETAT_PUBLIC="ok"
else
  ETAT_PUBLIC="EN ÉCHEC (voir l'avertissement ci-dessus ; la version reste en service)"
fi

etape "Terminé"
info "Version $VERSION en service (commit $SHA_COURT)."
info "Santé locale sur le serveur : ok. URL publique : $ETAT_PUBLIC."
