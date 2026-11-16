#!/usr/bin/env bash
# =============================================================================
# deploy/etat.sh — état de l'application EDT en production, en une commande.
#
# À lancer depuis le POSTE DU DÉVELOPPEUR (Git Bash) :
#   deploy/etat.sh
#
# Affiche : état du service et de nginx (arrêts pour mémoire épuisée compris),
# version déployée et versions disponibles, derniers déploiements, réponse de
# /sante (sur le serveur), certificat HTTPS, espace disque et mémoire,
# diagnostic de l'application (« edt doctor » : base utilisée, schéma ; sauté
# pendant un déploiement), fichiers de la base, présence de la clé de
# l'assistant (jamais son contenu), sauvegardes (quotidienne récente,
# minuteur, fichiers partiels), 30 dernières lignes du journal du service,
# puis l'URL publique vue depuis ce poste. Ne modifie rien.
#
# Code de sortie : 0 si tout va bien, 1 si un problème a été détecté (lignes
# « !!! »), autre valeur si l'état n'a pas pu être lu.
# Paramètres : deploy/serveur.env (voir deploy/commun.sh).
# =============================================================================
set -euo pipefail
set -o errtrace

DOSSIER_DEPLOY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/commun.sh
. "$DOSSIER_DEPLOY/commun.sh"
trap 'sur_erreur $? $LINENO "$BASH_COMMAND" "${BASH_SOURCE[0]}"' ERR

etape "Contrôles locaux et connexion au serveur"
verifier_poste_local
verifier_connexion

etape "État du serveur ($EDT_DOMAINE)"
CODE_DISTANT=0
# Partie exécutée sur le serveur, en root. Chaque vérification signale son
# résultat ; une vérification en échec n'empêche pas les suivantes.
executer_script_distant etat "$EDT_DOMAINE" <<'DISTANT' || CODE_DISTANT=$?
set -euo pipefail
# Erreur imprévue : signalée avec sa ligne, sortie avec le code 3
# (distinct du code 1 qui signifie « problèmes détectés »).
trap 'echo "!!! erreur inattendue (ligne $LINENO de la partie serveur de etat.sh) : $BASH_COMMAND" >&2; exit 3' ERR
DOMAINE="$1"
PROBLEMES=0
# Verrou pris en exclusif par un déploiement (deployer-distant.sh) et en
# partagé par le lanceur « sudo edt » : s'il est pris, le lanceur refuse de
# démarrer et le diagnostic de l'application est sauté.
VERROU="/run/lock/edt-deploiement.lock"
DEPLOIEMENT_EN_COURS=0
if [ -e "$VERROU" ] && ! flock -s -n "$VERROU" true; then
  DEPLOIEMENT_EN_COURS=1
fi
titre() { printf '\n[%s] [serveur] --- %s ---\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
probleme() { printf '!!! %s\n' "$*"; PROBLEMES=$((PROBLEMES + 1)); }

if [ "$DEPLOIEMENT_EN_COURS" = 1 ]; then
  titre "Déploiement en cours"
  echo "Un déploiement est en cours : le service et /sante peuvent être arrêtés quelques secondes (journal : /var/log/edt-deploiements/)."
fi

titre "Services"
for unite in edt nginx; do
  if etat_unite="$(systemctl is-active "$unite")"; then
    echo "$unite : $etat_unite"
  else
    probleme "le service $unite n'est pas actif (état : $etat_unite)"
  fi
done
systemctl show edt -p SubState -p ActiveEnterTimestamp -p NRestarts -p MainPID -p MemoryCurrent -p Result
# Mémoire épuisée (MemoryMax de edt.service) : le noyau tue le service, systemd
# écrit « A process of this unit has been killed by the OOM killer » puis, le plus
# souvent, « Failed with result 'oom-kill' » (parfois 'signal'), et le redémarre ;
# un calcul en cours est alors perdu. Chaque panne laisse au moins une des deux
# lignes : le plus grand des deux nombres est retenu.
if journal_oom="$(journalctl -u edt --since "-7d" --no-pager -o cat)"; then
  nb_oom="$(awk '/killed by the OOM killer/ { tue++ } /result .oom-kill./ { echec++ } END { n = (tue > echec) ? tue : echec; print n + 0 }' <<< "$journal_oom")"
  if [ "$nb_oom" -gt 0 ]; then
    probleme "$nb_oom arrêt(s) du service pour mémoire épuisée ces 7 derniers jours (oom-kill) : réduire le temps limite ou le nombre de fils des calculs (docs/deploiement.md, « Épuisement de la mémoire »)"
  fi
else
  probleme "lecture du journal du service impossible (journalctl)"
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
echo "Versions présentes sur le serveur (OK = mise en service réussie ; -- = envoyée, en cours ou annulée ; incomplet = envoi coupé ; les deux dernières sont supprimées au prochain déploiement réussi) :"
for dossier in /opt/edt/versions/*/; do
  [ -e "$dossier" ] || continue
  if [ -e "${dossier}deploiement-reussi" ]; then
    marque="OK"
  elif [ -e "${dossier}envoi-termine" ]; then
    marque="--"
  else
    marque="incomplet"
  fi
  nom="${dossier%/}"
  echo "  $marque ${nom##*/}"
done
if [ -e /var/log/edt-deploiements/historique.log ]; then
  echo
  echo "Derniers déploiements (date, version, commit, référence, auteur, résultat) :"
  tail -n 5 /var/log/edt-deploiements/historique.log
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
for dossier in /opt/edt/versions /var/lib/edt /var/lib/edt/sauvegardes /var/log/edt /var/log/edt-deploiements /var/cache/edt; do
  if [ -e "$dossier" ]; then du -sh "$dossier"; fi
done
free -h

# La base est décrite par l'application elle-même (sudo edt doctor : même
# configuration que le service), jamais en relisant edt.env ici.
titre "Base de données et diagnostic de l'application (sudo edt doctor)"
if [ ! -x /usr/local/bin/edt ]; then
  probleme "lanceur /usr/local/bin/edt absent : relancez installer-serveur.sh"
elif [ "$DEPLOIEMENT_EN_COURS" = 1 ]; then
  echo "déploiement en cours : diagnostic de l'application sauté (relancez deploy/etat.sh après le déploiement)"
elif ! /usr/local/bin/edt doctor; then
  probleme "« edt doctor » signale un échec (lignes ci-dessus)"
fi
echo "(Le contrôle du port 8000 est en « ATTENTION » quand le service tourne : c'est normal.)"
echo "Fichiers de /var/lib/edt :"
ls -lh /var/lib/edt
# Un fichier créé par root (commande lancée hors du lanceur) bloque le service.
etrangers="$(find /var/lib/edt -maxdepth 2 ! -user edt -print)"
if [ -n "$etrangers" ]; then
  probleme "fichiers de /var/lib/edt qui n'appartiennent pas à edt (le service ne peut pas les modifier ; sudo chown edt:edt <fichier>) : $etrangers"
fi

# Existence du fichier seulement : son contenu (la clé) n'est jamais lu ici.
if [ -e /etc/edt/assistant.env ]; then
  echo "Assistant : clé installée (/etc/edt/assistant.env)"
else
  echo "Assistant : clé absente (deploy/installer-cle-assistant.sh pour l'installer)"
fi

titre "Sauvegardes de la base (/var/lib/edt/sauvegardes)"
for prefixe in quotidienne edt manuelle corrompue; do
  nombre=0
  derniere=""
  for fichier in /var/lib/edt/sauvegardes/"$prefixe"-*.db; do
    [ -e "$fichier" ] || continue
    nombre=$((nombre + 1))
    derniere="${fichier##*/}"
  done
  echo "$prefixe : $nombre (la plus récente : ${derniere:-aucune})"
done
for fichier in /var/lib/edt/sauvegardes/*.partiel; do
  [ -e "$fichier" ] || continue
  probleme "sauvegarde partielle laissée par un échec (disque plein ?) : $fichier"
done
if systemctl is-active --quiet edt-sauvegarde.timer; then
  echo "sauvegarde quotidienne : minuteur actif"
  systemctl list-timers edt-sauvegarde.timer --no-pager
else
  probleme "minuteur edt-sauvegarde.timer inactif : aucune sauvegarde quotidienne (relancez installer-serveur.sh)"
fi
if systemctl is-failed --quiet edt-sauvegarde.service; then
  probleme "la dernière sauvegarde quotidienne a échoué : sudo journalctl -u edt-sauvegarde -n 30"
fi
# Une sauvegarde quotidienne de moins de 2 jours n'est exigée que si une
# version est en service depuis au moins 2 jours (témoin deploiement-reussi) :
# juste après la première installation, le minuteur n'a pas encore tourné.
if [ -L /opt/edt/courant ]; then
  recentes="$(find /var/lib/edt/sauvegardes -maxdepth 1 -name 'quotidienne-*.db' -mtime -2 -print)"
  anciennes="$(find /opt/edt/versions -mindepth 2 -maxdepth 2 -name deploiement-reussi -mtime +1 -print)"
  if [ -z "$recentes" ] && [ -n "$anciennes" ]; then
    probleme "aucune sauvegarde quotidienne de moins de 2 jours (sudo systemctl start edt-sauvegarde, puis sudo journalctl -u edt-sauvegarde)"
  elif [ -z "$recentes" ]; then
    echo "première sauvegarde quotidienne attendue à la prochaine exécution du minuteur (version en service depuis moins de 2 jours)"
  fi
fi

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
