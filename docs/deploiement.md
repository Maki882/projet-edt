# Déploiement de l'application EDT

Ce guide explique comment l'application est installée sur le serveur, comment
la mettre à jour, revenir en arrière, lire les journaux et réparer les pannes
courantes. Tous les scripts sont dans le dossier `deploy/` du dépôt.

En bref :

| Quand | Où | Commande |
|---|---|---|
| Une fois | serveur | `installer-serveur.sh` : prépare le serveur (nginx, Python, dossiers, service) |
| Une fois | serveur | `activer-https.sh` : certificat Let's Encrypt et passage en HTTPS |
| À chaque mise à jour | poste | `deploy/deployer.sh` : envoie et met en service une version |
| Pour regarder | poste | `deploy/etat.sh` (état complet), `deploy/journaux.sh` (journaux en direct) |

Adresse publique : **https://pts.jorund.xyz** (http:// avant l'activation du HTTPS).

## 1. Schéma de l'installation

```
  Navigateur
     │  https://pts.jorund.xyz            http://pts.jorund.xyz ou http://92.222.10.188
     │  (port 443)                        (port 80 : redirigé vers https, sauf les défis
     ▼                                     Let's Encrypt /.well-known/acme-challenge/)
┌──────────────── Serveur Ubuntu 26.04 — 92.222.10.188 ─────────────────────────┐
│ nginx (ports 80 et 443)                                                         │
│   ├─ /static/…                    → fichiers de /opt/edt/courant/code/src/edt/web/static
│   │                                 (sinon transmis à l'application)            │
│   ├─ /.well-known/acme-challenge/ → /var/www/certbot (renouvellement certificat)│
│   └─ tout le reste                → 127.0.0.1:8000 ; si injoignable : page 502  │
│                    │                                                            │
│ service systemd « edt » (utilisateur système edt, sans shell)                   │
│   uvicorn edt.web.asgi:app, UN seul processus, écoute 127.0.0.1:8000 seulement   │
│   code : /opt/edt/courant  →  /opt/edt/versions/<date>-<commit>                 │
│                    │                                                            │
│ base SQLite /var/lib/edt/edt.db   journaux : journald + /var/log/edt            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

L'application n'est jamais joignable directement depuis Internet : seul nginx
écoute à l'extérieur (ports 80 et 443).

| Chemin sur le serveur | Contenu |
|---|---|
| `/opt/edt/versions/<date>-<commit>/code` | code envoyé (copie du commit) |
| `/opt/edt/versions/<…>/venv` | environnement Python propre à cette version |
| `/opt/edt/versions/<…>/DEPLOIEMENT.txt` | commit, référence, auteur, dates de la version |
| `/opt/edt/versions/<…>/installation-pip.log`, `paquets-installes.txt` | détail de l'installation des dépendances |
| `/opt/edt/courant` | lien vers la version en service |
| `/etc/edt/edt.env` | variables d'environnement de production (modèle : `deploy/edt.env.exemple`) |
| `/var/lib/edt/edt.db` | la base de données |
| `/var/lib/edt/sauvegardes/` | sauvegardes automatiques (10 dernières) et manuelles |
| `/var/log/edt/edt.log`, `/var/log/edt/generations/` | journaux de l'application et des calculs |
| `/var/log/edt/deploiements/<version>.log` | journal complet de chaque déploiement |
| `/var/log/edt/historique-deploiements.log` | une ligne par déploiement (date, version, résultat) |
| `/var/log/nginx/edt-acces.log`, `edt-erreurs.log` | journaux nginx du site |
| `/etc/systemd/system/edt.service` | unité systemd (modèle : `deploy/edt.service`) |
| `/etc/nginx/sites-available/edt`, `/etc/nginx/snippets/edt-application.conf` | configuration nginx |
| `/var/www/edt-erreurs/erreur-502.html` | page « application indisponible » |
| `/etc/letsencrypt/` | certificat HTTPS (géré par certbot) |

Fichiers du dossier `deploy/` :

| Fichier | Rôle | Lancé où |
|---|---|---|
| `installer-serveur.sh` | prépare le serveur, idempotent | serveur |
| `activer-https.sh` | certificat Let's Encrypt + nginx en HTTPS, idempotent | serveur |
| `deployer.sh` | déploie une version | poste |
| `deployer-distant.sh` | partie serveur de `deployer.sh` (envoyée automatiquement) | serveur, via `deployer.sh` |
| `etat.sh` | état complet en une commande | poste |
| `journaux.sh` | journaux en direct | poste |
| `commun.sh` | paramètres et fonctions communs aux scripts du poste | (chargé par les autres) |
| `edt.service` | unité systemd | — |
| `edt.env.exemple` | modèle de `/etc/edt/edt.env` | — |
| `nginx-edt.conf` | site nginx, mode HTTP (avant le certificat) | — |
| `nginx-edt-https.conf` | site nginx, mode HTTPS (dès que le certificat existe) | — |
| `nginx-edt-application.conf` | partie commune : proxy, statiques, page 502, limite de 20 Mo | — |
| `erreur-502.html` | page affichée quand l'application ne répond pas | — |

## 2. Avant de commencer (poste du développeur)

- Ouvrir **Git Bash** et se placer à la racine du dépôt. Le chemin contient une
  espace, il faut donc des guillemets :
  `cd "/c/Users/Axel/projet edt"`
- La clé SSH dédiée doit exister : `~/.ssh/edt_vps_ed25519`. Se connecter une
  première fois à la main pour accepter l'empreinte du serveur :
  `ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188` puis `exit`.
- Les scripts du poste lisent ces variables (valeurs par défaut entre parenthèses) :
  `EDT_SSH_HOTE` (92.222.10.188), `EDT_SSH_UTILISATEUR` (ubuntu),
  `EDT_SSH_CLE` (~/.ssh/edt_vps_ed25519), `EDT_DOMAINE` (pts.jorund.xyz).
  Exemple : `EDT_SSH_HOTE=203.0.113.7 deploy/etat.sh`.
- Seul du **code commité** est déployé. Une version déployable contient
  `pyproject.toml` à la racine et le paquet dans `src/edt/` ; sinon `deployer.sh`
  refuse avec un message clair.

## 3. Première installation, pas à pas

Le serveur doit être installé une fois, puis le HTTPS activé, puis la première
version déployée.

**Étape 1 : copier le dossier `deploy/` sur le serveur** (depuis Git Bash, à la
racine du dépôt ; c'est le contenu de votre dossier local qui est copié, faites
donc d'abord `git pull`) :

```bash
cd "/c/Users/Axel/projet edt"
tar -cf - deploy | ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'mkdir -p ~/edt-installation && tar -xf - -C ~/edt-installation'
```

**Étape 2 : préparer le serveur** (quelques minutes, surtout `apt-get`) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo bash ~/edt-installation/deploy/installer-serveur.sh'
```

Le script affiche chaque étape horodatée et se termine par « Serveur prêt ». Il
installe nginx, Python (venv, outils de compilation), sqlite3 et curl, crée
l'utilisateur `edt` et les dossiers, crée `/etc/edt/edt.env` depuis le modèle,
installe le service `edt` et le site nginx (en HTTP), et désactive le site
nginx par défaut. Il ne touche ni à SSH ni au pare-feu. À ce stade,
http://pts.jorund.xyz affiche la page « application momentanément
indisponible » : c'est normal, rien n'est encore déployé.

**Étape 3 : activer le HTTPS** (le nom pts.jorund.xyz doit déjà pointer sur le serveur) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo env EDT_CERTBOT_EMAIL=adresse@example.org bash ~/edt-installation/deploy/activer-https.sh'
```

Remplacez `adresse@example.org` par l'adresse de l'équipe : Let's Encrypt y
envoie les alertes d'expiration. Sans `EDT_CERTBOT_EMAIL`, l'inscription se fait
sans adresse et le script affiche un avertissement. Le script vérifie d'abord,
sans rien modifier, que le DNS pointe sur le serveur et que le port 80 répond ;
puis il installe certbot, obtient le certificat, passe nginx en HTTPS (avec
redirection de http:// et de l'adresse IP vers https://pts.jorund.xyz) et teste
le renouvellement automatique.

**Étape 4 : premier déploiement** (depuis le poste) :

```bash
cd "/c/Users/Axel/projet edt"
deploy/deployer.sh
```

La première fois, l'installation des dépendances (OR-Tools, etc.) peut prendre
plusieurs minutes ; les fois suivantes, le cache pip du serveur l'accélère.

**Étape 5 : vérifier** : `deploy/etat.sh`, puis ouvrir https://pts.jorund.xyz.

Pour appliquer plus tard une modification de `edt.service` ou des fichiers
nginx : refaire les étapes 1 et 2. Le script est idempotent : il conserve
`/etc/edt/edt.env`, ne redemande rien à Let's Encrypt et ne redémarre le
service que si l'unité a changé.

## 4. Mise à jour courante

```bash
cd "/c/Users/Axel/projet edt"
git switch main && git pull          # en général on déploie main à jour
deploy/deployer.sh                   # déploie HEAD
deploy/deployer.sh --ref 1a2b3c4d    # ou un commit, une branche, une étiquette précise
```

Ce que fait `deployer.sh`, dans l'ordre (chaque étape est affichée et horodatée) :

1. contrôles locaux : outils, clé, référence git ; avertit si des fichiers
   ne sont pas commités (ils ne sont **pas** envoyés) ou si le commit n'est pas
   sur GitHub ;
2. contrôles du serveur : connexion, sudo sans mot de passe, installation faite ;
3. envoi du commit : `git archive <commit> | ssh … tar -x` vers
   `/opt/edt/versions/<date>-<commit>/code` ;
4. sur le serveur (`deployer-distant.sh`) :
   - **préparation**, l'ancienne version reste en ligne : venv propre à la
     version, `pip install`, test de chargement de `edt.web.asgi` avec une base
     jetable ;
   - **bascule** : arrêt du service, sauvegarde de la base, `python -m edt init-db`,
     bascule du lien `/opt/edt/courant`, démarrage, attente de
     `GET http://127.0.0.1:8000/sante` → 200 pendant 30 s au plus ;
   - **finalisation** : version marquée réussie, `edt version` et `edt doctor`
     (informatifs), nettoyage (5 versions, 10 sauvegardes), historique ;
5. vérification de l'URL publique depuis le poste (`https://pts.jorund.xyz/sante`,
   ou `http://` s'il n'y a pas de certificat) : en cas d'échec, un avertissement
   clair est affiché, mais rien n'est annulé.

L'interruption de service dure quelques secondes (entre l'arrêt et la réponse de
`/sante`). **Un calcul d'emploi du temps en cours est interrompu** : prévenir
avant de déployer. Une coupure réseau ou un Ctrl+C pendant l'étape « Déploiement
sur le serveur » n'arrête pas le travail côté serveur : il va au bout, et son
journal complet est dans `/var/log/edt/deploiements/<version>.log`.

Le code de sortie vaut 0 si la nouvelle version est en service, autre chose sinon.

**Recréer la base vide** : `deploy/deployer.sh --reset-base`. Le script demande
de taper `EFFACER` ; la base est sauvegardée juste avant (`/var/lib/edt/sauvegardes`),
puis `python -m edt init-db --reset` est lancé.

## 5. Retour arrière

**Automatique.** Si une étape échoue après l'arrêt du service (init-db,
démarrage, `/sante` qui ne répond pas 200 en 30 s), `deployer.sh` affiche les 50
dernières lignes du journal du service, restaure la base sauvegardée juste avant,
remet le lien sur la version précédente, redémarre et vérifie `/sante`. Il
sort avec un code non nul. Si l'échec a lieu pendant la préparation (dépendance,
erreur d'import), le service n'a jamais été arrêté : rien à défaire.

Cas du **premier déploiement** (aucune version précédente) : le service reste
arrêté, le lien `/opt/edt/courant` est retiré et le site affiche la page 502.
Corriger puis relancer `deploy/deployer.sh`.

**Manuel, méthode conseillée : redéployer un ancien commit.** `deploy/etat.sh`
liste les versions présentes et les derniers déploiements avec leur commit.

```bash
deploy/deployer.sh --ref <commit-qui-marchait>
```

C'est un déploiement normal (sauvegarde, vérification de santé, retour arrière
automatique).

**Manuel, méthode express : rebasculer sur une version déjà présente** (sur le
serveur, sans réinstallation ; seulement vers une version marquée `OK` par
`etat.sh`) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188
ls /opt/edt/versions
sudo systemctl stop edt
sudo ln -sfn /opt/edt/versions/<version> /opt/edt/courant.nouveau
sudo mv -Tf /opt/edt/courant.nouveau /opt/edt/courant
sudo systemctl start edt
curl -s http://127.0.0.1:8000/sante
```

Si la version visée attend un autre schéma de base, restaurer aussi la
sauvegarde faite avant le déploiement fautif (section 8).

**« LE RETOUR ARRIÈRE A ÉCHOUÉ »** : le service est probablement arrêté.
Lire `sudo journalctl -u edt -n 100`, vérifier `ls -l /opt/edt/courant`,
puis appliquer la méthode express ci-dessus vers la dernière version `OK`.
La sauvegarde de la base reste dans `/var/lib/edt/sauvegardes/`.

## 6. Où lire les journaux

Depuis le poste :

```bash
deploy/journaux.sh            # tout : service + edt.log + erreurs nginx (Ctrl+C pour arrêter)
deploy/journaux.sh service    # journal systemd du service (sortie d'uvicorn, tracebacks)
deploy/journaux.sh appli      # /var/log/edt/edt.log
deploy/journaux.sh nginx      # accès et erreurs nginx
deploy/journaux.sh calcul     # journal du dernier calcul d'emploi du temps
deploy/etat.sh                # photo complète, avec les 30 dernières lignes du service
```

Sur le serveur :

| Quoi | Commande ou fichier |
|---|---|
| Service (stdout/stderr d'uvicorn) | `sudo journalctl -u edt -n 100` ; en direct : `-f` ; depuis 1 h : `--since "1 hour ago"` |
| Application | `/var/log/edt/edt.log` (fichier tournant), `/var/log/edt/generations/` (un fichier par calcul) |
| nginx | `/var/log/nginx/edt-acces.log` (avec `duree=`, `duree_appli=` et `id=`), `/var/log/nginx/edt-erreurs.log` |
| Déploiements | `/var/log/edt/deploiements/<version>.log`, `/var/log/edt/historique-deploiements.log` |
| Installation pip d'une version | `/opt/edt/versions/<version>/installation-pip.log` |
| Certificat | `/var/log/letsencrypt/letsencrypt.log`, `sudo journalctl -u certbot` |

Le champ `id=` du journal nginx est envoyé à l'application dans l'en-tête
`X-Request-ID` : il permet de relier une requête nginx aux lignes de
l'application si celle-ci reprend cet identifiant.

## 7. Pannes classiques

**Page « application momentanément indisponible » (erreur 502).** nginx tourne
mais ne joint pas 127.0.0.1:8000.
`deploy/etat.sh` puis `sudo systemctl status edt` et `sudo journalctl -u edt -n 100`.
Causes habituelles : exception Python au démarrage (traceback dans le journal),
`/etc/edt/edt.env` incorrect, aucune version déployée (`/opt/edt/courant` absent),
service arrêté après trop d'échecs de démarrage (« start-limit-hit » : corriger
puis `sudo systemctl reset-failed edt && sudo systemctl start edt`, ou redéployer).

**Le service redémarre en boucle puis passe en « failed ».** Même diagnostic.
Si le journal montre `oom-kill` ou « Memory » : le calcul a dépassé 3 Go
(`MemoryMax` dans `edt.service`) ; réduire le jeu de données ou le temps limite.

**Port occupé.** « address already in use » pour 8000 dans le journal du service,
ou nginx qui ne démarre pas (port 80 ou 443) :
`sudo ss -ltnp 'sport = :8000'` (ou `:80`, `:443`) montre quel programme tient le
port. Un uvicorn lancé à la main et oublié : l'arrêter (`sudo kill <pid>`).
Un autre serveur web (apache2) : `sudo systemctl disable --now apache2`.

**Base verrouillée (« database is locked »).** Un seul processus web écrit dans
la base, mais une commande lancée à la main en même temps (par exemple
`python -m edt …` ou `sqlite3`) peut la bloquer. Vérifier :
`sudo fuser -v /var/lib/edt/edt.db`, fermer la commande en trop, puis
`sudo systemctl restart edt`. Ne jamais supprimer `edt.db-wal` pendant que le
service tourne : il contient les dernières écritures.

**Une dépendance ne s'installe pas** (le déploiement échoue à l'étape
« Environnement Python et dépendances » ; l'ancienne version reste en ligne).
Lire le message de pip affiché et `/opt/edt/versions/<version>/installation-pip.log`.
Causes : pas de paquet précompilé pour Python 3.14 (pip tente de compiler :
il faut `build-essential` et `python3-dev`, installés par `installer-serveur.sh` ;
sinon fixer une version compatible dans `pyproject.toml`), version introuvable
sur PyPI (faute de frappe, contrainte trop stricte), serveur sans accès à
Internet, disque plein (`df -h /`).

**« Read-only file system » ou « Permission denied » dans le journal du service.**
Le service ne peut écrire que dans `/var/lib/edt` et `/var/log/edt`
(`ProtectSystem=strict` dans `edt.service`). Écrire dans ces dossiers, ou
ajouter le dossier à `ReadWritePaths` dans `deploy/edt.service` puis relancer
`installer-serveur.sh`.

**Disque plein.** `deploy/etat.sh` montre l'espace libre et la taille des
dossiers. Le déploiement refuse de commencer sous 1 Go libre. Supprimer de
vieux journaux de calcul dans `/var/log/edt/generations/` ou de vieilles
sauvegardes manuelles.

**Connexion SSH refusée ou sudo qui demande un mot de passe.** Le message de
`deployer.sh` distingue les deux cas. Tester `ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188`
à la main ; « Host key verification failed » = l'empreinte du serveur a changé
(serveur réinstallé) : vérifier puis corriger `~/.ssh/known_hosts`.

### Pannes du HTTPS

**Le DNS n'est pas (encore) propagé.** `activer-https.sh` s'arrête sans rien
modifier en indiquant vers quelle adresse pointe le nom. Corriger
l'enregistrement A chez le registraire (il doit valoir 92.222.10.188), attendre
l'expiration de l'ancien enregistrement (durée TTL, souvent de quelques minutes à
quelques heures) puis relancer. Vérifier depuis le poste : `nslookup pts.jorund.xyz`.
Un enregistrement AAAA (IPv6) vers une autre machine fait aussi échouer la
validation : le script le signale.

**Le port 80 est fermé vu de l'extérieur.** Let's Encrypt valide le domaine en
téléchargeant un fichier sur http://pts.jorund.xyz/.well-known/acme-challenge/ :
le port 80 doit rester ouvert, même une fois le HTTPS actif (renouvellements).
Le test de `activer-https.sh` est fait depuis le serveur lui-même ; si certbot
signale « Timeout during connect » ou « Connection refused », un pare-feu bloque
le port 80 (pare-feu du serveur ou de l'hébergeur).

**Limite de débit de Let's Encrypt** (« too many certificates » ou « too many
failed authorizations »). Let's Encrypt limite les certificats identiques
(5 par semaine) et les validations ratées (5 par heure). Les scripts ne
redemandent jamais un certificat encore valide (`--keep-until-expiring`) et les
tests de renouvellement utilisent le serveur de test (`--dry-run`) : en
pratique, la limite n'est atteinte qu'en relançant beaucoup après des échecs.
Corriger la cause (DNS, port 80), puis attendre l'heure ou la semaine indiquée
dans le message avant de relancer.

**Certificat expiré** (le navigateur affiche une alerte). Le certificat dure
90 jours et `certbot.timer` le renouvelle automatiquement environ 30 jours avant
la fin ; `etat.sh` signale un certificat qui expire dans moins de 14 jours.
Diagnostic : `systemctl list-timers certbot.timer`, `sudo certbot renew --dry-run`,
`/var/log/letsencrypt/letsencrypt.log`. Une fois la cause corrigée :
`sudo certbot renew` (nginx est rechargé automatiquement par le crochet
`/etc/letsencrypt/renewal-hooks/deploy/edt-recharger-nginx.sh`), ou relancer
`activer-https.sh`.

**Alerte de certificat en ouvrant https://92.222.10.188** : normal, le certificat
est au nom de pts.jorund.xyz ; utiliser le nom de domaine (http://92.222.10.188
redirige d'ailleurs vers https://pts.jorund.xyz).

## 8. Sauvegarde et restauration de la base

**Automatique** : chaque déploiement sauvegarde la base juste après l'arrêt du
service, avec l'API de sauvegarde de SQLite et un contrôle d'intégrité, dans
`/var/lib/edt/sauvegardes/edt-<date>-avant-<version>.db`. Les 10 plus récentes
sont gardées.

**Manuelle** (sur le serveur ; les fichiers nommés `manuelle-…` ne sont jamais
supprimés automatiquement) :

```bash
sudo -u edt sqlite3 /var/lib/edt/edt.db ".backup '/var/lib/edt/sauvegardes/manuelle-$(date +%Y%m%d-%H%M%S).db'"
```

Vérifier une sauvegarde :
`sudo -u edt sqlite3 /var/lib/edt/sauvegardes/<fichier>.db "PRAGMA integrity_check"` (doit répondre `ok`).

Rapatrier une sauvegarde sur le poste (depuis Git Bash) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo cat /var/lib/edt/sauvegardes/<fichier>.db' > "<fichier>.db"
```

**Restaurer** (sur le serveur) :

```bash
sudo systemctl stop edt
# 1. garder une copie de la base actuelle, au cas où
sudo -u edt sqlite3 /var/lib/edt/edt.db ".backup '/var/lib/edt/sauvegardes/manuelle-avant-restauration-$(date +%Y%m%d-%H%M%S).db'"
# 2. retirer les fichiers annexes de l'ancienne base, puis mettre la sauvegarde à sa place
sudo rm -f /var/lib/edt/edt.db-wal /var/lib/edt/edt.db-shm
sudo install -o edt -g edt -m 640 /var/lib/edt/sauvegardes/<sauvegarde>.db /var/lib/edt/edt.db
sudo systemctl start edt
```

Puis `deploy/etat.sh`. Les données sont fictives : en dernier recours, la base
se recrée avec `deploy/deployer.sh --reset-base` puis se repeuple avec les
commandes de l'application.

## 9. Modifier la configuration

- **Variables d'environnement** : `sudo nano /etc/edt/edt.env` sur le serveur,
  puis `sudo systemctl restart edt`. Format strict : `NOM=valeur`, sans espace
  autour du `=` ni guillemets (le fichier est aussi lu par les scripts). Le
  modèle du dépôt (`deploy/edt.env.exemple`) n'écrase jamais ce fichier.
- **Service systemd ou nginx** : modifier le fichier dans `deploy/`, committer,
  puis refaire les étapes 1 et 2 de la section 3. En cas d'erreur nginx
  (`nginx -t`), l'ancienne configuration est remise automatiquement.
- **Autre nom de domaine** : `EDT_DOMAINE=autre.exemple.fr` pour les scripts du
  poste, `sudo env EDT_DOMAINE=autre.exemple.fr bash …` pour ceux du serveur.

## 10. À faire plus tard

- **Désactiver la connexion SSH par mot de passe** (clé uniquement), après avoir
  vérifié que la connexion par clé fonctionne, en gardant une session ouverte
  pendant le changement.
- **Pare-feu** : s'il est activé un jour, il devra laisser passer les ports
  **22** (SSH), **80** (HTTP, indispensable aussi au renouvellement du
  certificat) et **443** (HTTPS), sous peine de couper l'accès au serveur ou au site.
- **Sauvegardes hors du serveur** : copier régulièrement une sauvegarde sur un
  autre support (commande « Rapatrier » de la section 8).
