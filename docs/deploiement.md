# Déploiement de l'application EDT

Ce guide explique comment l'application est installée sur le serveur, mise à
jour, remise en arrière, sauvegardée, observée et réparée. Tous les scripts
sont dans le dossier `deploy/` du dépôt.

| Quand | Où | Commande |
|---|---|---|
| Une fois (et après toute modification de `deploy/`) | serveur | `installer-serveur.sh` : nginx, Python, dossiers, service, lanceur `edt`, sauvegarde quotidienne |
| Une fois | serveur | `activer-https.sh` : certificat Let's Encrypt et passage en HTTPS |
| À chaque mise à jour | poste | `deploy/deployer.sh` : envoie et met en service une version |
| Chaque nuit, tout seul | serveur | minuteur `edt-sauvegarde.timer` : sauvegarde vérifiée de la base |
| Pour agir sur les données | serveur | `sudo edt <commande>` (ex. `sudo edt jeu --taille grand --graine 0`) |
| Pour regarder | poste | `deploy/etat.sh` (état complet), `deploy/journaux.sh` (en direct) |

Adresse publique : **https://pts.jorund.xyz** (http:// avant l'activation du HTTPS).

## 1. Schéma de l'installation

```
  Navigateur
     │  https://pts.jorund.xyz            http://pts.jorund.xyz ou http://92.222.10.188
     │  (port 443)                        (port 80 : redirigé vers https, sauf les défis
     ▼                                     Let's Encrypt /.well-known/acme-challenge/)
┌──────────────── Serveur Ubuntu 26.04 — 92.222.10.188 ─────────────────────────┐
│ nginx (ports 80 et 443)                                                         │
│   ├─ /static/…                    → /opt/edt/courant/code/src/edt/web/static    │
│   │                                 (sinon transmis à l'application)            │
│   ├─ /.well-known/acme-challenge/ → /var/www/certbot (renouvellement certificat)│
│   └─ tout le reste                → 127.0.0.1:8000 ; si injoignable : page 502  │
│                    │                envoi de plus de 21 Mo : page 413           │
│ service systemd « edt » (utilisateur système edt, sans shell)                   │
│   uvicorn edt.web.asgi:app, UN seul processus, écoute 127.0.0.1:8000 seulement   │
│   code : /opt/edt/courant  →  /opt/edt/versions/<date UTC>-<commit>             │
│                    │                                                            │
│ base SQLite /var/lib/edt/edt.db   journaux : journald + /var/log/edt            │
│ sauvegarde quotidienne (edt-sauvegarde.timer) → /var/lib/edt/sauvegardes        │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Seul nginx écoute sur Internet (ports 80 et 443) ; l'application n'écoute que
sur 127.0.0.1.

| Chemin sur le serveur | Contenu |
|---|---|
| `/opt/edt/versions/<date UTC>-<commit>/` | `code/` (copie du commit), `venv/` (Python de cette version), `envoi-termine` (témoin d'un envoi complet), `DEPLOIEMENT.txt` (commit, auteur, dates), `installation-pip.log`, `paquets-installes.txt`, `deploiement-reussi` |
| `/opt/edt/courant` | lien vers la version en service |
| `/etc/edt/edt.env` | variables de production (modèle : `deploy/edt.env.exemple`) |
| `/var/lib/edt/edt.db` | la base |
| `/var/lib/edt/sauvegardes/` | sauvegardes : `quotidienne-*` (minuteur, 14 gardées), `edt-*-avant-<version>` (déploiement, 10 gardées), `manuelle-*` (jamais supprimées) |
| `/var/log/edt/edt.log`, `/var/log/edt/generations/` | journaux de l'application et des calculs |
| `/var/log/edt-deploiements/` | `<version>.log` (journal complet de chaque déploiement), `historique.log` (une ligne par déploiement, date avec décalage horaire) |
| `/var/log/nginx/edt-acces.log`, `edt-erreurs.log` | journaux nginx du site |
| `/usr/local/bin/edt` | lanceur des commandes de l'application (section 6) |
| `/etc/systemd/system/edt.service` | le service |
| `/etc/systemd/system/edt-sauvegarde.service`, `/etc/systemd/system/edt-sauvegarde.timer` | la sauvegarde quotidienne et son minuteur |
| `/etc/nginx/sites-available/edt`, `/etc/nginx/snippets/edt-application.conf` | configuration nginx |
| `/var/www/edt-erreurs/erreur-502.html`, `/var/www/edt-erreurs/erreur-413.html` | pages « application indisponible » et « envoi trop volumineux » |
| `/etc/letsencrypt/` | certificat HTTPS (géré par certbot) |
| `/run/lock/edt-deploiement.lock` | verrou : un déploiement exclut les commandes `sudo edt`, et inversement |

Fichiers du dossier `deploy/` :

- serveur, idempotents : `installer-serveur.sh` (installation) et `activer-https.sh` (certificat) ;
- poste : `deployer.sh`, `etat.sh`, `journaux.sh`, et `commun.sh` (leurs paramètres et fonctions communes) ;
- `deployer-distant.sh` : partie serveur de `deployer.sh`, envoyée automatiquement ;
- `lanceur-edt.sh` : devient `/usr/local/bin/edt` ;
- unités systemd : `edt.service`, `edt-sauvegarde.service`, `edt-sauvegarde.timer` ;
- `edt.env.exemple` : modèle de `/etc/edt/edt.env` ;
- nginx : `nginx-edt.conf` (site en HTTP), `nginx-edt-https.conf` (site en HTTPS),
  `nginx-edt-application.conf` (proxy, statiques, pages d'erreur, envois de 21 Mo au plus) ;
- pages d'erreur : `erreur-502.html` et `erreur-413.html`.

Les tests automatiques de ces fichiers sont dans `tests/exploitation/` (syntaxe des
scripts, cohérence entre fichiers, sauvegardes, arrêt brutal) ; ce qui ne peut se
vérifier que sur le serveur est décrit en section 10.

## 2. Avant de commencer (poste du développeur)

- Ouvrir **Git Bash** à la racine du dépôt (le chemin contient une espace,
  d'où les guillemets) : `cd "/c/Users/Axel/projet edt"`.
- La clé SSH dédiée `~/.ssh/edt_vps_ed25519` doit exister. Se connecter une
  première fois à la main pour accepter l'empreinte du serveur :
  `ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188` puis `exit`.
- Variables lues par les scripts du poste (défaut entre parenthèses) :
  `EDT_SSH_HOTE` (92.222.10.188), `EDT_SSH_UTILISATEUR` (ubuntu),
  `EDT_SSH_CLE` (~/.ssh/edt_vps_ed25519), `EDT_DOMAINE` (pts.jorund.xyz).
- Seul du **code commité** est déployé ; il faut `pyproject.toml` à la racine
  et le paquet dans `src/edt/`.

## 3. Première installation, pas à pas

**Étape 1 : copier le dossier `deploy/` sur le serveur** (c'est le contenu de
votre dossier local qui est copié : faites d'abord `git pull`) :

```bash
cd "/c/Users/Axel/projet edt"
tar -cf - deploy | ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'mkdir -p ~/edt-installation && tar -xf - -C ~/edt-installation'
```

**Étape 2 : préparer le serveur** (quelques minutes, surtout `apt-get`) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo bash ~/edt-installation/deploy/installer-serveur.sh'
```

Chaque étape est affichée et horodatée ; la fin dit « Serveur prêt ». Le script
installe aussi la sauvegarde quotidienne de la base (sautée tant qu'aucune
version n'est déployée) et les pages d'erreur. Il ne touche ni à SSH ni au
pare-feu. À ce stade, http://pts.jorund.xyz affiche la page « application
momentanément indisponible » : normal, rien n'est déployé.

**Étape 3 : activer le HTTPS** (pts.jorund.xyz doit déjà pointer sur le serveur) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo env EDT_CERTBOT_EMAIL=adresse@example.org bash ~/edt-installation/deploy/activer-https.sh'
```

`EDT_CERTBOT_EMAIL` est l'adresse de contact du compte Let's Encrypt (facultative ;
sans elle, un avertissement est affiché). Let's Encrypt n'envoie plus d'alerte
d'expiration depuis juin 2025 : le seul garde-fou est `deploy/etat.sh`, à lancer
régulièrement (il signale un certificat qui expire dans moins de 14 jours). Le
script vérifie d'abord, sans rien modifier, que le DNS pointe sur le serveur et
que le port 80 répond ; puis il obtient le certificat, passe nginx en HTTPS
(http:// et l'adresse IP redirigent vers https://pts.jorund.xyz) et teste le
renouvellement automatique.

**Étape 4 : premier déploiement** (depuis le poste ; la première installation
des dépendances, OR-Tools compris, prend plusieurs minutes) :

```bash
cd "/c/Users/Axel/projet edt"
deploy/deployer.sh
```

**Étape 5 : charger le jeu de données fictif** (section 6) puis vérifier :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo edt jeu --taille grand --graine 0'
deploy/etat.sh
```

Pour appliquer plus tard une modification de `deploy/` (service, minuteur,
lanceur, nginx, pages d'erreur) : refaire les étapes 1 et 2 ; `deployer.sh`
signale les fichiers du serveur qui ne sont plus ceux du dépôt. Le script
conserve `/etc/edt/edt.env`, ne redemande rien à Let's Encrypt et ne redémarre
le service que si son unité a changé ou s'il était arrêté.

## 4. Mise à jour courante

```bash
cd "/c/Users/Axel/projet edt"
git switch main && git pull          # en général on déploie main à jour
deploy/deployer.sh                   # déploie HEAD
deploy/deployer.sh --ref 1a2b3c4d    # ou un commit, une branche, une étiquette
```

Ce que fait `deployer.sh` (chaque étape est affichée et horodatée) :

1. contrôles locaux ; avertit si des fichiers ne sont pas commités (ils ne sont
   **pas** envoyés) ou si le commit n'est pas sur GitHub ;
2. contrôles du serveur : connexion, sudo, installation faite ; avertit si des
   fichiers installés par `installer-serveur.sh` sont absents du serveur ou
   différents du dépôt ;
3. envoi du commit (`git archive | ssh … tar -x`) vers
   `/opt/edt/versions/<date UTC>-<commit>/code`, puis écriture du témoin
   `envoi-termine` : un envoi coupé laisse un dossier sans témoin, refusé par le
   serveur et supprimé par le déploiement réussi suivant ;
4. sur le serveur (`deployer-distant.sh`) :
   - **préparation**, l'ancienne version reste en ligne : verrou, envoi complet,
     fichiers de la base à l'utilisateur `edt`, **aucune commande `sudo edt`
     ni autre processus de l'application hors du service** (sinon refus, avec
     la liste des processus), venv propre à la version, `pip install`,
     chargement de l'application avec la configuration de production (elle
     indique elle-même la base qu'elle utilise), contrôle du port 8000 ;
   - **bascule** : arrêt du service (au plus 20 s d'attente, puis arrêt forcé),
     nouveau contrôle qu'aucun processus de l'application ne tient la base,
     sauvegarde de la base, `edt init-db`, `edt verifier-schema`, bascule du
     lien `/opt/edt/courant`, démarrage, `GET http://127.0.0.1:8000/sante` → 200
     en 30 s au plus ;
   - **finalisation** : version marquée réussie, `edt version` et `edt doctor`
     (informatifs), nettoyage (5 versions, 10 sauvegardes de déploiement,
     versions jamais mises en service, sauvegardes partielles), historique ;
5. vérification de l'URL publique depuis le poste : un échec est signalé, rien
   n'est annulé.

Codes de sortie : 0 si la nouvelle version est en service (éventuellement avec
avertissements), autre chose sinon. L'interruption de service dure quelques
secondes ; **un calcul d'emploi du temps en cours est interrompu** (il est clos
« interrompu » au redémarrage) : prévenir avant de déployer. Une coupure réseau
ou un Ctrl+C pendant l'étape « Déploiement sur le serveur » n'arrête pas le
travail côté serveur ; son journal est dans `/var/log/edt-deploiements/<version>.log`.

**Heures.** Les noms de version et de sauvegarde sont en **heure UTC** (pas
d'heure répétée au passage à l'heure d'hiver, l'ordre des noms suit l'ordre des
déploiements) ; les messages du déploiement et de l'application sont en heure de
Paris ; `historique.log` porte le décalage horaire (`2026-11-02T15:05:12+0100`).

**Changement de schéma : `--reset-base` obligatoire.** Il n'y a pas de
migrations. Un commit qui ajoute, renomme ou supprime une table ou une colonne
existante ne se déploie qu'avec :

```bash
deploy/deployer.sh --reset-base
```

Sans cette option, `init-db` ou `verifier-schema` le détecte, le déploiement est
annulé (retour arrière) et le message l'explique. Avec l'option, le script
demande de taper `EFFACER`, sauvegarde la base, la recrée vide
(`init-db --reset`, sans autre question) ; il reste à la repeupler (section 6).
Si la version déployée ne fournit pas encore `verifier-schema`, un avertissement
le dit et le schéma n'est contrôlé que par `init-db` et `/sante` (qui répond 503
si le schéma est périmé).

## 5. Retour arrière

**Automatique.** Si une étape échoue après l'arrêt du service (processus de
l'application apparu, sauvegarde, init-db, schéma, démarrage, `/sante` qui ne
répond pas 200 en 30 s, interruption par un signal), `deployer.sh` affiche les
50 dernières lignes du journal du service, restaure la base sauvegardée juste
avant, remet le lien sur la version précédente, remet à zéro le compteur de
démarrages de systemd, redémarre et vérifie `/sante`. Si l'échec a lieu pendant
la préparation (dépendance, import, port occupé, commande en cours), le service
n'a jamais été arrêté : rien à défaire.

Au **premier déploiement** (aucune version précédente), le service reste arrêté,
le lien `/opt/edt/courant` est retiré et le site affiche la page 502. Corriger
puis relancer `deploy/deployer.sh`.

**Manuel, méthode conseillée : redéployer un ancien commit** (`deploy/etat.sh`
liste les versions présentes et les derniers déploiements avec leur commit) :

```bash
deploy/deployer.sh --ref <commit-qui-marchait>
```

C'est un déploiement normal (sauvegarde, schéma, santé, retour arrière). Si un
`--reset-base` a eu lieu entre les deux versions, le schéma a pu changer : il
faudra alors `--reset-base` aussi.

**Manuel, méthode express : rebasculer sur une version présente** (sur le
serveur, vers une version marquée `OK` par `etat.sh`, sans changement de schéma
entre les deux) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188
sudo systemctl stop edt
sudo ln -sfn /opt/edt/versions/<version> /opt/edt/courant.nouveau
sudo mv -Tf /opt/edt/courant.nouveau /opt/edt/courant
sudo systemctl reset-failed edt && sudo systemctl start edt
curl -s http://127.0.0.1:8000/sante
```

**« LE RETOUR ARRIÈRE A ÉCHOUÉ »** : le service est probablement arrêté. Lire
`sudo journalctl -u edt -n 100`, vérifier `ls -l /opt/edt/courant`, puis la
méthode express vers la dernière version `OK`. La sauvegarde de la base reste
dans `/var/lib/edt/sauvegardes/` (section 9).

## 6. Lancer une commande de l'application en production

Toujours par le lanceur, sur le serveur :

```bash
sudo edt --help                          # liste des commandes
sudo edt doctor                          # diagnostic : base utilisée, schéma, journaux, OR-Tools
sudo edt jeu --taille grand --graine 0   # charge le jeu fictif (base initialisée et vide)
sudo edt verifier                        # vérifie l'emploi du temps
sudo edt sauvegarder --prefixe manuelle --garder 0   # sauvegarde vérifiée, jamais supprimée
sudo edt verifier-sauvegarde /var/lib/edt/sauvegardes/<fichier>.db   # contrôle sans modifier
```

Depuis le poste : `ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo edt doctor'`
(ajouter `-t` après `ssh` pour une commande qui pose une question).

Le lanceur exécute `python -m edt …` avec la version en service, sous
l'utilisateur `edt`, avec `/etc/edt/edt.env` lu par systemd : exactement comme
le service. Il refuse tout autre compte que root ou ubuntu via sudo, et **refuse
de démarrer pendant un déploiement** (« un déploiement est en cours sur ce
serveur ») ; inversement, un déploiement refuse de commencer tant qu'une commande
`sudo edt` tourne. **Ne jamais lancer `/opt/edt/courant/venv/bin/python -m edt …`
à la main** : sans l'environnement de production, la commande crée ou modifie une
autre base (`data/edt.db` dans le dossier courant) sans aucune erreur, ou laisse
des fichiers appartenant à root (`edt.db-wal`…) qui empêchent le service d'écrire.

- Charger un jeu ou remettre la base à zéro **pendant un calcul** est refusé
  par l'application : attendre la fin du calcul ou l'arrêter.
- Pour repartir d'une base vide, préférer `deploy/deployer.sh --reset-base`
  (sauvegarde faite avant) puis `sudo edt jeu …`. `sudo edt jeu --reset`
  efface la base **sans sauvegarde** ; faire d'abord
  `sudo edt sauvegarder --prefixe manuelle --garder 0` (section 9). Sa question
  de confirmation demande `ssh -t`, ou l'option `--oui`.

## 7. Journaux et état

Depuis le poste :

```bash
deploy/etat.sh                # photo complète : service (arrêts pour mémoire épuisée compris), version,
                              # /sante, certificat, disque, edt doctor, fichiers de la base, sauvegardes, journal
deploy/journaux.sh            # en direct : service et application (uvicorn + edt.log), Ctrl+C pour arrêter
deploy/journaux.sh nginx      # accès et erreurs nginx
deploy/journaux.sh calcul     # journal du dernier calcul d'emploi du temps
```

Sur le serveur : `sudo journalctl -u edt -n 100` (ou `-f`, `--since "1 hour ago"`),
`sudo journalctl -u edt-sauvegarde -n 30` (sauvegarde quotidienne),
`/var/log/edt/edt.log`, `/var/log/edt/generations/`, `/var/log/nginx/edt-*.log`,
`/var/log/edt-deploiements/`, `/opt/edt/versions/<version>/installation-pip.log`,
`/var/log/letsencrypt/letsencrypt.log`. Le champ `id=` du journal d'accès nginx
est l'identifiant attribué par l'application à la requête (en-tête
`X-Request-ID`, repris dans `edt.log` et sur les pages d'erreur) : il relie une
ligne nginx aux lignes de l'application.

## 8. Pannes classiques

**Page « application momentanément indisponible » (erreur 502).** nginx ne
joint pas 127.0.0.1:8000. `deploy/etat.sh`, puis `sudo journalctl -u edt -n 100`.
Causes habituelles : exception au démarrage (traceback dans le journal),
`/etc/edt/edt.env` incorrect, aucune version déployée (`/opt/edt/courant`
absent), service abandonné après trop d'échecs (« start-limit-hit » : corriger
puis `sudo systemctl reset-failed edt && sudo systemctl start edt`, ou redéployer).

**Erreur 503 sur `/sante`.** L'application tourne mais juge la base inutilisable :
non initialisée, schéma périmé, ou écriture refusée (`base.inscriptible` à
`false` : fichier de la base ou son dossier en lecture seule, disque plein).
`sudo edt doctor` dit laquelle ; schéma périmé :
`deploy/deployer.sh --reset-base` (section 4) ; lecture seule : voir
« Fichiers de la base appartenant à root » ci-dessous. `base.inscriptible` à
`null` n'est pas une panne : une autre écriture tenait le verrou, l'essai a
été sauté. `/sante` accepte aussi HEAD (`curl -I`).

**Épuisement de la mémoire.** Le service est limité à 3 Go (`MemoryMax=3G` dans
`edt.service`, sur 3,7 Go) : au-delà, le noyau le tue, systemd note « killed by
the OOM killer » (et le plus souvent « oom-kill ») et le redémarre trois secondes
plus tard ; un calcul en cours est perdu et clos
« interrompu par l'arrêt de l'application ». `deploy/etat.sh` compte ces arrêts
sur 7 jours. Remède : relancer le calcul avec un temps limite plus court ou
moins de fils de calcul (`--fils`), ou réduire le périmètre ; procédure de
vérification en section 10.

**Le service redémarre en boucle.** Même diagnostic que l'erreur 502 ; `oom-kill`
dans le journal : voir ci-dessus.

**Envoi trop volumineux.** Jusqu'à 20 Mo, l'application lit l'envoi ; un peu
au-delà, elle le refuse avec son message ; au-delà de 21 Mo, nginx répond lui-même
413 avec la page `erreur-413.html` (« 20 Mo au plus par envoi »), affichée dans
la zone des messages de la page d'import. Découper l'envoi.

**Port occupé.** Le déploiement refuse de commencer si un autre programme que le
service tient le port 8000 ; nginx ne démarre pas si le port 80 ou 443 est pris.
`sudo ss -ltnp 'sport = :8000'` (ou `:80`, `:443`) montre le programme : un
uvicorn lancé à la main et oublié (`sudo kill <pid>`), un autre serveur web
(`sudo systemctl disable --now apache2`).

**Déploiement refusé : commande en cours.** « des processus de l'application
tournent hors du service » ou « une commande « sudo edt » est en cours » : la
liste donne le numéro, l'unité systemd et la commande. Attendre sa fin, ou
l'arrêter : `sudo systemctl stop <unité>` (une commande `sudo edt` tourne dans
une unité `run-u….service`), puis relancer `deploy/deployer.sh`.

**Fichiers de la base appartenant à root.** « fichiers de /var/lib/edt qui
n'appartiennent pas à edt » (déploiement ou `etat.sh`) : une commande lancée en
root hors du lanceur a créé `edt.db-wal` ou `edt.db-shm`. Service arrêté :
`sudo systemctl stop edt`, puis `sudo chown edt:edt <fichier>`, puis
`sudo systemctl start edt`.

**Base verrouillée (« database is locked »).** Une commande lancée en même temps
que le service (`sqlite3`, script) bloque la base. `sudo fuser -v /var/lib/edt/edt.db`,
fermer la commande en trop, puis `sudo systemctl restart edt`. Ne jamais
supprimer `edt.db-wal` pendant que le service tourne : il contient les dernières
écritures (un arrêt brutal, lui, ne les abîme pas : SQLite les rejoue ou les
ignore au redémarrage).

**Base introuvable au déploiement.** « base introuvable alors qu'une version est
en service » : `EDT_DB_URL` a changé dans `edt.env`, ou la base a été déplacée.
Si c'est voulu (base corrompue mise de côté, ci-dessous), relancer avec `--reset-base`.

**Base corrompue** (la sauvegarde du déploiement ou la sauvegarde quotidienne
échoue au contrôle d'intégrité ; `sudo edt doctor` en échec). La mettre de côté
puis recréer une base vide, ou restaurer la dernière sauvegarde saine (section 9) :

```bash
sudo systemctl stop edt
d=$(date +%Y%m%d-%H%M%S)
for f in edt.db edt.db-wal edt.db-shm; do if sudo test -e /var/lib/edt/$f; then sudo mv /var/lib/edt/$f /var/lib/edt/sauvegardes/manuelle-corrompue-$d-$f; fi; done
sudo ls -l /var/lib/edt /var/lib/edt/sauvegardes
```

puis, depuis le poste, `deploy/deployer.sh --reset-base` et `sudo edt jeu …`.

**Sauvegarde quotidienne en échec.** `deploy/etat.sh` le signale (unité
`edt-sauvegarde` en échec, ou aucune sauvegarde de moins de 2 jours). Lire
`sudo journalctl -u edt-sauvegarde -n 30` : base illisible (voir « Base
corrompue »), disque plein (`df -h /`), ou aucune version déployée.

**Une dépendance ne s'installe pas** (échec à l'étape « Environnement Python et
dépendances » ; l'ancienne version reste en ligne). Lire le message de pip et
`/opt/edt/versions/<version>/installation-pip.log`. Causes : pas de paquet
précompilé pour Python 3.14 (compilation : `build-essential` et `python3-dev` sont
installés ; sinon fixer une version compatible dans `pyproject.toml`), version
introuvable sur PyPI, serveur sans Internet, disque plein (`df -h /`).

**« Read-only file system » ou « Permission denied » dans le journal.** Le service
n'écrit que dans `/var/lib/edt` et `/var/log/edt` (`ProtectSystem=strict`).
Ajouter un dossier à `ReadWritePaths` dans `deploy/edt.service`, puis
`installer-serveur.sh`.

**Disque plein.** Le déploiement refuse de commencer sous 1 Go libre ; la
sauvegarde refuse d'écrire sans la place de la base et 10 Mo ; `deploy/etat.sh`
montre la place prise par chaque dossier.

**Connexion SSH refusée, ou sudo qui demande un mot de passe.** Le message de
`deployer.sh` distingue les deux cas. « Host key verification failed » : le
serveur a été réinstallé ; vérifier puis corriger `~/.ssh/known_hosts`.

### Pannes du HTTPS

**DNS pas (encore) propagé.** `activer-https.sh` s'arrête sans rien modifier et
indique vers quelle adresse pointe le nom. Corriger l'enregistrement A chez le
registraire (92.222.10.188), attendre l'expiration de l'ancien (durée TTL), puis
relancer. Vérifier depuis le poste : `nslookup pts.jorund.xyz`. Un
enregistrement AAAA (IPv6) vers une autre machine fait aussi échouer la
validation : le script le signale.

**Port 80 fermé vu de l'extérieur.** Let's Encrypt valide le domaine par
http://pts.jorund.xyz/.well-known/acme-challenge/ : le port 80 doit rester
ouvert, même en HTTPS (renouvellements). Le test du script est fait depuis le
serveur ; si certbot signale « Timeout during connect », un pare-feu (serveur ou
hébergeur) bloque le port 80.

**Limite de débit de Let's Encrypt** (« too many certificates », « too many failed
authorizations ») : 5 certificats identiques par semaine, 5 validations ratées
par heure. Les scripts ne redemandent jamais un certificat valide
(`--keep-until-expiring`) et testent le renouvellement sur le serveur de test
(`--dry-run`). Corriger la cause, puis attendre le délai indiqué.

**Certificat expiré.** Il dure 90 jours ; `certbot.timer` le renouvelle environ
30 jours avant la fin et `etat.sh` alerte sous 14 jours. Diagnostic :
`systemctl list-timers certbot.timer`, `sudo certbot renew --dry-run`,
`/var/log/letsencrypt/letsencrypt.log`. Ensuite `sudo certbot renew` (nginx est
rechargé par `/etc/letsencrypt/renewal-hooks/deploy/edt-recharger-nginx.sh`) ou
relancer `activer-https.sh`.

**Alerte de certificat sur https://92.222.10.188** : normal, le certificat est au
nom de pts.jorund.xyz (http://92.222.10.188 redirige d'ailleurs vers lui).

## 9. Sauvegarde et restauration de la base

**Quotidienne** : chaque nuit vers 3 h 30 (heure de Paris), le minuteur
`edt-sauvegarde.timer` lance `edt-sauvegarde.service`, c'est-à-dire
`python -m edt sauvegarder --dossier /var/lib/edt/sauvegardes --garder 14` de la
version en service, sous l'utilisateur `edt`. La copie est cohérente même
pendant le service (API de sauvegarde de SQLite), écrite en `.partiel` puis
renommée seulement si son contrôle d'intégrité passe : en cas d'échec (base
illisible, disque plein) il ne reste rien. Les 14 plus récentes
`quotidienne-<date UTC>.db` sont gardées. Une nuit manquée (serveur éteint) est
rattrapée au démarrage. État : `systemctl list-timers edt-sauvegarde.timer`,
`deploy/etat.sh`.

**À chaque déploiement** : juste après l'arrêt du service, même méthode,
`edt-<date UTC>-avant-<version>.db` (10 gardées).

**Manuelle** (jamais supprimée automatiquement) :

```bash
sudo edt sauvegarder --prefixe manuelle --garder 0
sudo edt verifier-sauvegarde /var/lib/edt/sauvegardes/<fichier>.db   # code 0 = restaurable
```

`verifier-sauvegarde` travaille sur une copie temporaire (la sauvegarde n'est
jamais modifiée) : intégrité, nombre de lignes par table, schéma comparé au
modèle de la version en service. Code 0 : restaurable telle quelle avec cette
version ; code 1 : abîmée, étrangère à l'application, ou d'un autre schéma (à
restaurer avec la version qui l'a produite, voir section 5).

Rapatrier une sauvegarde sur le poste (Git Bash) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo cat /var/lib/edt/sauvegardes/<fichier>.db' > "<fichier>.db"
```

**Restaurer** (sur le serveur, tout en utilisateur `edt`) :

```bash
sudo edt verifier-sauvegarde /var/lib/edt/sauvegardes/<sauvegarde>.db   # d'abord : code 0 attendu
sudo edt sauvegarder --prefixe manuelle --garder 0                     # la base actuelle, au cas où
sudo systemctl stop edt
sudo -u edt rm -f /var/lib/edt/edt.db-wal /var/lib/edt/edt.db-shm
sudo -u edt install -m 640 /var/lib/edt/sauvegardes/<sauvegarde>.db /var/lib/edt/edt.db
sudo systemctl start edt
```

Puis `deploy/etat.sh`. Exercice de restauration à faire chaque mois : section 10.

## 10. Vérifications d'exploitation

Ces procédures vérifient sur le serveur ce que les tests automatiques ne peuvent
pas reproduire (systemd, nginx, droits Unix, coupures réseau). Chacune se rejoue
telle quelle ; les faire **hors des heures d'utilisation** (certaines arrêtent
le service quelques secondes), après chaque modification de `deploy/` et au
moins une fois par trimestre. Dans chaque fenêtre Git Bash utilisée, à la racine
du dépôt, définir d'abord le raccourci utilisé par toutes les procédures :

```bash
serveur() { ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 "$@"; }
```

`serveur 'commande'` lance la commande sur le serveur ; `serveur -t 'commande'`
lui donne un terminal (commande qui affiche sa progression ou pose une question).

| Ce qui est vérifié | Automatiquement (`tests/exploitation/`) | À la main (ci-dessous) |
|---|---|---|
| Syntaxe, fins de ligne, cohérence des scripts et unités | oui | non |
| Code Python de la sauvegarde du déploiement | oui | non |
| `edt sauvegarder`, `edt verifier-sauvegarde` | oui | sur le serveur : 10.8 |
| Base intacte après un arrêt brutal, calcul tué clos au redémarrage | oui | sur le serveur : 10.5 |
| Verrou déploiement / `sudo edt`, recherche des processus | en partie (Linux) | 10.1 |
| Échec de sauvegarde pendant la bascule, envoi interrompu | en partie | 10.2, 10.4 |
| Mémoire épuisée : lecture du journal par `etat.sh` | oui | arrêt réel : 10.6 |
| Page 413 : configuration nginx, texte, styles | oui | affichage réel : 10.7 |
| Fichiers à root, fuseaux horaires | en partie (contrôles présents) | 10.3, 10.9 |
| Commandes du guide : sudo, lignes entières, cible SSH | oui | non |

### 10.1 Déploiement pendant une commande « sudo edt »

**Prérequis** : une version en service avec le jeu fictif chargé ; deux
fenêtres Git Bash (A et B) à la racine du dépôt.

**Étapes**
1. Fenêtre B : lancer une commande longue, par exemple un calcul qui n'active
   rien : `serveur -t 'sudo edt generer --temps-limite 120 --sans-activer'`.
2. Fenêtre A, pendant que le calcul tourne : `deploy/deployer.sh`.
3. Une fois le calcul fini, fenêtre A : relancer `deploy/deployer.sh` et, pendant
   l'étape « Environnement Python et dépendances », fenêtre B :
   `serveur 'sudo edt doctor'`.
4. Processus hors du lanceur, fenêtre B :
   `serveur 'sudo systemd-run --uid=edt --gid=edt --unit=essai-edt sleep 300'`,
   puis fenêtre A : `deploy/deployer.sh`.
5. Nettoyage : `serveur 'sudo systemctl stop essai-edt'`.

**Résultat attendu**
- étape 2 : refus en préparation, « un autre déploiement, ou une commande « sudo
  edt », est en cours », avec la liste des processus (numéro, unité `run-u….service`,
  commande) ; « Le service en place n'a pas été touché » ; le calcul de B va au bout ;
- étape 3 : B répond « edt : un déploiement est en cours sur ce serveur » (code 1),
  le déploiement de A réussit ;
- étape 4 : refus en préparation, « des processus de l'application tournent hors
  du service », avec `essai-edt.service` dans la liste.

### 10.2 Échec de la sauvegarde pendant un déploiement

**Prérequis** : une version en service ; le système de fichiers de `/var/lib/edt`
accepte `chattr` (ext4, cas du serveur).

**Étapes**
1. Rendre le dossier des sauvegardes non modifiable :
   `serveur 'sudo chattr +i /var/lib/edt/sauvegardes'`.
2. `deploy/deployer.sh`.
3. `deploy/etat.sh`.
4. Nettoyage : `serveur 'sudo chattr -i /var/lib/edt/sauvegardes'`.

**Résultat attendu**
- étape 2 : échec à l'étape « Sauvegarde de la base » (« sauvegarde impossible »),
  puis « Retour arrière réussi : la version … est de nouveau en service » ; code 1 ;
  `historique.log` : « ECHEC en bascule (étape : Sauvegarde de la base) » ;
- étape 3 : `/sante` à 200, aucun fichier `.partiel` signalé.

### 10.3 Fuseau horaire et noms de version

**Prérequis** : aucun (un déploiement normal suffit).

**Étapes**
1. Noter l'heure de Paris, puis `deploy/deployer.sh`.
2. `serveur 'sudo tail -n 1 /var/log/edt-deploiements/historique.log; sudo grep "Application EDT" /var/log/edt/edt.log | tail -n 1'`.

**Résultat attendu**
- le nom de version (`Nom de la version : 20261102-140512-…`) est en heure UTC
  (une heure de moins que Paris en hiver, deux en été) ;
- les messages `[serveur]` du déploiement, la ligne « Application EDT …
  démarrée » de `edt.log` et la date de `historique.log` sont à la même heure de Paris, et
  `historique.log` porte le décalage (`+0100` ou `+0200`).

### 10.4 Envoi du code interrompu

**Prérequis** : une version en service.

**Étapes**
1. Simuler un envoi coupé : `serveur 'sudo mkdir -p /opt/edt/versions/20260101-000000-deadbeef/code'`.
2. `deploy/etat.sh`.
3. Copier le script du serveur puis le lancer sur ce dossier :
   `serveur 'cat > /tmp/deployer-distant.sh' < deploy/deployer-distant.sh`, puis
   `serveur 'sudo bash /tmp/deployer-distant.sh 20260101-000000-deadbeef 0000000000000000000000000000000000000000 essai 0 essai 1'`.
4. `deploy/deployer.sh` (déploiement normal).
5. Nettoyage : `serveur 'rm -f /tmp/deployer-distant.sh'`.

**Résultat attendu**
- étape 2 : la version `20260101-000000-deadbeef` est marquée « incomplet » ;
- étape 3 : refus en préparation, « envoi du code incomplet … (témoin
  « envoi-termine » absent …) », rien n'est touché ;
- étape 4 : la finalisation affiche « Supprimé (jamais mis en service : envoi
  coupé ou déploiement annulé) : 20260101-000000-deadbeef ».

### 10.5 Arrêt brutal pendant une écriture

**Prérequis** : une version en service avec le jeu fictif chargé.

**Étapes**
1. Dans l'application, lancer un calcul (page Générer) avec un temps limite d'au
   moins deux minutes.
2. Pendant le calcul : `serveur 'sudo systemctl kill --signal=SIGKILL edt'`
   (le noyau tue le service comme à la fin du délai d'arrêt de 20 s ou quand la
   mémoire est épuisée).
3. Attendre 5 secondes, puis :
   `serveur 'sudo edt doctor; sudo -u edt sqlite3 /var/lib/edt/edt.db "PRAGMA integrity_check"'`.
4. Dans l'application, ouvrir la liste des calculs, puis relancer un calcul.

**Résultat attendu**
- systemd a redémarré le service tout seul (`deploy/etat.sh` : actif, `NRestarts` +1) ;
- `doctor` : « Tout est prêt » ; `integrity_check` : `ok` ;
- le calcul tué est clos : « Le calcul a été interrompu par un arrêt de
  l'application… Il peut être relancé » ; le nouveau calcul démarre.

### 10.6 Épuisement de la mémoire pendant un calcul

**Prérequis** : le jeu fictif « grand » chargé sur une base vide
(`serveur 'sudo edt jeu --taille grand --graine 0'`) ; hors des heures
d'utilisation.

**Étapes**
1. Abaisser la limite pour la durée de l'essai :
   `serveur 'sudo systemctl set-property --runtime edt.service MemoryMax=400M'`.
2. Dans l'application, lancer un calcul sur le semestre du jeu « grand ».
3. Suivre : `deploy/journaux.sh` (Ctrl+C pour arrêter), puis `deploy/etat.sh`.
4. Remettre la limite : `serveur 'sudo systemctl set-property --runtime edt.service MemoryMax=3G'`
   (la valeur d'essai disparaît de toute façon au redémarrage du serveur).

**Résultat attendu**
- le journal montre « A process of this unit has been killed by the OOM killer »
  et « Failed with result 'oom-kill' » (ou 'signal'), puis un redémarrage automatique ;
- `etat.sh` : « 1 arrêt(s) du service pour mémoire épuisée ces 7 derniers jours » ;
  `/sante` à 200 ; nginx et SSH n'ont pas été touchés ;
- le calcul est clos « interrompu par l'arrêt de l'application ».

### 10.7 Envoi trop volumineux (page 413)

**Prérequis** : HTTPS actif ; sur le poste, un fichier de 23 Mo et un de 20,3 Mo :
`head -c 23000000 /dev/urandom > gros.xlsx` et `head -c 21300000 /dev/urandom > moyen.xlsx`.

**Étapes**
1. Dans l'application, page Import : choisir `gros.xlsx`, « Simuler l'import ».
2. Depuis le poste :
   `curl -s -o /dev/null -w '%{http_code}\n' -F "fichiers=@gros.xlsx" https://pts.jorund.xyz/import/simuler`.
3. Recommencer l'étape 1 avec `moyen.xlsx`.

**Résultat attendu**
- étape 1 : un message rouge « Envoi trop volumineux : 20 Mo au plus par envoi »
  dans la zone des messages, la page d'import garde sa mise en page ;
- étape 2 : `413` (le corps de la réponse est la page française `erreur-413.html`) ;
- étape 3 : le message de l'application elle-même (« … Mo reçus, 20 Mo au plus »).

### 10.8 Sauvegarde quotidienne et exercice de restauration

**Prérequis** : `installer-serveur.sh` relancé avec cette version de `deploy/` ;
une version en service. À faire une fois par mois.

**Étapes**
1. `serveur 'systemctl list-timers edt-sauvegarde.timer'`.
2. `serveur 'sudo systemctl start edt-sauvegarde; sudo journalctl -u edt-sauvegarde -n 20'`.
3. Chemin de la plus récente :
   `serveur 'sudo sh -c "ls -t /var/lib/edt/sauvegardes/quotidienne-*.db | head -n 1"'`,
   puis `serveur 'sudo edt verifier-sauvegarde <chemin affiché>'`.
4. Restauration d'essai sur le poste, sans toucher à la production : rapatrier ce
   fichier (section 9) sous `essai.db`, puis à la racine du dépôt :
   `EDT_DB_URL=sqlite:///essai.db python -m edt doctor` et
   `EDT_DB_URL=sqlite:///essai.db python -m edt serve`, et parcourir l'application
   (http://127.0.0.1:8000). Supprimer `essai.db` ensuite.

**Résultat attendu**
- étape 1 : prochaine exécution vers 3 h 30, heure de Paris ;
- étape 2 : « Sauvegarde vérifiée : /var/lib/edt/sauvegardes/quotidienne-… (… Mo,
  … tables, intégrité ok) » ;
- étape 3 : code 0, « Schéma conforme au modèle : sauvegarde restaurable avec cette version » ;
- étape 4 : `doctor` « Tout est prêt », les données du serveur s'affichent.

### 10.9 Fichiers de la base appartenant à root

**Prérequis** : une version en service.

**Étapes**
1. Simuler un fichier laissé par une commande lancée en root :
   `serveur 'sudo touch /var/lib/edt/essai-root'`.
2. `deploy/etat.sh`, puis `deploy/deployer.sh`.
3. Nettoyage : `serveur 'sudo rm /var/lib/edt/essai-root'`.

**Résultat attendu**
- `etat.sh` : « fichiers de /var/lib/edt qui n'appartiennent pas à edt … /var/lib/edt/essai-root » ;
- `deployer.sh` : refus en préparation avec la même liste et la commande
  `sudo chown edt:edt <fichier>` ; le service n'a pas été touché.

## 11. Modifier la configuration

- **Variables d'environnement** : `sudo nano /etc/edt/edt.env`, puis
  `sudo systemctl restart edt`. Le fichier est lu par systemd (pour le service,
  le lanceur `edt`, la sauvegarde quotidienne et les déploiements) : une ligne
  `NOM=valeur` par variable. Le modèle du dépôt ne l'écrase jamais.
- **Service, minuteur, lanceur, nginx ou pages d'erreur** : modifier le fichier
  dans `deploy/`, committer, puis refaire les étapes 1 et 2 de la section 3. Si
  `nginx -t` refuse la nouvelle configuration, l'ancienne est remise automatiquement.
- **Autre nom de domaine** : `EDT_DOMAINE=autre.exemple.fr` pour les scripts du
  poste, `sudo env EDT_DOMAINE=autre.exemple.fr bash …` pour ceux du serveur.

## 12. À faire plus tard

- **Désactiver la connexion SSH par mot de passe** (clé uniquement), après avoir
  vérifié la connexion par clé, en gardant une session ouverte pendant le changement.
- **Pare-feu** : s'il est activé un jour, il devra laisser passer les ports
  **22** (SSH), **80** (HTTP, indispensable au renouvellement du certificat) et
  **443** (HTTPS).
- **Sauvegardes hors du serveur** : la sauvegarde quotidienne reste sur le même
  disque ; copier régulièrement une sauvegarde ailleurs (commande « Rapatrier »
  de la section 9).
