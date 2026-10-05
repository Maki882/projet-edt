# Déploiement de l'application EDT

Ce guide explique comment l'application est installée sur le serveur, mise à
jour, remise en arrière, observée et réparée. Tous les scripts sont dans le
dossier `deploy/` du dépôt.

| Quand | Où | Commande |
|---|---|---|
| Une fois | serveur | `installer-serveur.sh` : nginx, Python, dossiers, service, lanceur `edt` |
| Une fois | serveur | `activer-https.sh` : certificat Let's Encrypt et passage en HTTPS |
| À chaque mise à jour | poste | `deploy/deployer.sh` : envoie et met en service une version |
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
│                    │                                                            │
│ service systemd « edt » (utilisateur système edt, sans shell)                   │
│   uvicorn edt.web.asgi:app, UN seul processus, écoute 127.0.0.1:8000 seulement   │
│   code : /opt/edt/courant  →  /opt/edt/versions/<date>-<commit>                 │
│                    │                                                            │
│ base SQLite /var/lib/edt/edt.db   journaux : journald + /var/log/edt            │
└─────────────────────────────────────────────────────────────────────────────────┘
```

Seul nginx écoute sur Internet (ports 80 et 443) ; l'application n'écoute que
sur 127.0.0.1.

| Chemin sur le serveur | Contenu |
|---|---|
| `/opt/edt/versions/<date>-<commit>/` | `code/` (copie du commit), `venv/` (Python de cette version), `DEPLOIEMENT.txt` (commit, auteur, dates), `installation-pip.log`, `paquets-installes.txt` |
| `/opt/edt/courant` | lien vers la version en service |
| `/etc/edt/edt.env` | variables de production (modèle : `deploy/edt.env.exemple`) |
| `/var/lib/edt/edt.db`, `/var/lib/edt/sauvegardes/` | la base ; ses sauvegardes (10 automatiques + les manuelles) |
| `/var/log/edt/edt.log`, `/var/log/edt/generations/` | journaux de l'application et des calculs |
| `/var/log/edt-deploiements/` | `<version>.log` (journal complet de chaque déploiement), `historique.log` (une ligne par déploiement) |
| `/var/log/nginx/edt-acces.log`, `edt-erreurs.log` | journaux nginx du site |
| `/usr/local/bin/edt` | lanceur des commandes de l'application (section 6) |
| `/etc/systemd/system/edt.service`, `/etc/nginx/sites-available/edt`, `/etc/nginx/snippets/edt-application.conf`, `/var/www/edt-erreurs/erreur-502.html` | fichiers installés depuis `deploy/` |
| `/etc/letsencrypt/` | certificat HTTPS (géré par certbot) |

Fichiers du dossier `deploy/` : `installer-serveur.sh` et `activer-https.sh`
(serveur, idempotents) ; `deployer.sh`, `etat.sh`, `journaux.sh` (poste) et
`commun.sh` (leurs paramètres et fonctions communes) ; `deployer-distant.sh`
(partie serveur de `deployer.sh`, envoyée automatiquement) ; `lanceur-edt.sh`
(devient `/usr/local/bin/edt`) ; `edt.service`, `edt.env.exemple`,
`nginx-edt.conf` (site en HTTP), `nginx-edt-https.conf` (site en HTTPS),
`nginx-edt-application.conf` (proxy, statiques, page 502, envois de 20 Mo) et
`erreur-502.html`.

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
ne touche ni à SSH ni au pare-feu. À ce stade, http://pts.jorund.xyz affiche la
page « application momentanément indisponible » : normal, rien n'est déployé.

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

Pour appliquer plus tard une modification de `edt.service`, du lanceur ou des
fichiers nginx : refaire les étapes 1 et 2 (`deployer.sh` signale quand c'est
nécessaire). Le script conserve `/etc/edt/edt.env`, ne redemande rien à
Let's Encrypt et ne redémarre le service que si l'unité a changé ou s'il était arrêté.

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
2. contrôles du serveur : connexion, sudo, installation faite ; avertit si les
   fichiers installés par `installer-serveur.sh` ne sont plus ceux du dépôt ;
3. envoi du commit (`git archive | ssh … tar -x`) vers `/opt/edt/versions/<date>-<commit>/code` ;
4. sur le serveur (`deployer-distant.sh`) :
   - **préparation**, l'ancienne version reste en ligne : venv propre à la
     version, `pip install`, chargement de l'application avec la configuration
     de production (elle indique elle-même la base qu'elle utilise), contrôle du
     port 8000 ;
   - **bascule** : arrêt du service, sauvegarde de la base, `edt init-db`,
     `edt verifier-schema`, bascule du lien `/opt/edt/courant`, démarrage,
     `GET http://127.0.0.1:8000/sante` → 200 en 30 s au plus ;
   - **finalisation** : version marquée réussie, `edt version` et `edt doctor`
     (informatifs), nettoyage (5 versions, 10 sauvegardes), historique ;
5. vérification de l'URL publique depuis le poste : un échec est signalé, rien
   n'est annulé.

Codes de sortie : 0 si la nouvelle version est en service (éventuellement avec
avertissements), autre chose sinon. L'interruption de service dure quelques
secondes ; **un calcul d'emploi du temps en cours est interrompu** : prévenir
avant de déployer. Une coupure réseau ou un Ctrl+C pendant l'étape « Déploiement
sur le serveur » n'arrête pas le travail côté serveur ; son journal est dans
`/var/log/edt-deploiements/<version>.log`.

**Changement de schéma : `--reset-base` obligatoire.** Il n'y a pas de
migrations. Un commit qui ajoute, renomme ou supprime une table ou une colonne
existante ne se déploie qu'avec :

```bash
deploy/deployer.sh --reset-base
```

Sans cette option, `init-db` ou `verifier-schema` le détecte, le déploiement est
annulé (retour arrière) et le message l'explique. Avec l'option, le script
demande de taper `EFFACER`, sauvegarde la base, la recrée vide
(`init-db --reset`) ; il reste à la repeupler (section 6). Si la version
déployée ne fournit pas encore `verifier-schema`, un avertissement le dit et le
schéma n'est contrôlé que par `init-db` et `/sante` (qui répond 503 si le
schéma est périmé).

## 5. Retour arrière

**Automatique.** Si une étape échoue après l'arrêt du service (init-db, schéma,
démarrage, `/sante` qui ne répond pas 200 en 30 s, interruption par un signal),
`deployer.sh` affiche les 50 dernières lignes du journal du service, restaure la
base sauvegardée juste avant, remet le lien sur la version précédente, remet à
zéro le compteur de démarrages de systemd, redémarre et vérifie `/sante`. Si
l'échec a lieu pendant la préparation (dépendance, import, port occupé), le
service n'a jamais été arrêté : rien à défaire.

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
dans `/var/lib/edt/sauvegardes/` (section 8).

## 6. Lancer une commande de l'application en production

Toujours par le lanceur, sur le serveur :

```bash
sudo edt --help                          # liste des commandes
sudo edt doctor                          # diagnostic : base utilisée, schéma, journaux, OR-Tools
sudo edt jeu --taille grand --graine 0   # charge le jeu fictif (base initialisée et vide)
sudo edt verifier                        # vérifie l'emploi du temps
```

Depuis le poste : `ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo edt doctor'`
(ajouter `-t` après `ssh` pour une commande qui pose une question).

Le lanceur exécute `python -m edt …` avec la version en service, sous
l'utilisateur `edt`, avec `/etc/edt/edt.env` lu par systemd : exactement comme
le service. Il refuse tout autre compte que root ou ubuntu via sudo. **Ne jamais
lancer `/opt/edt/courant/venv/bin/python -m edt …` à la main** : sans
l'environnement de production, la commande crée ou modifie une autre base
(`data/edt.db` dans le dossier courant) sans aucune erreur, ou laisse des
fichiers appartenant à root qui empêchent le service de démarrer.

- Charger un jeu ou remettre la base à zéro **pendant un calcul** est refusé
  par l'application : attendre la fin du calcul ou l'arrêter.
- Pour repartir d'une base vide, préférer `deploy/deployer.sh --reset-base`
  (sauvegarde faite avant) puis `sudo edt jeu …`. `sudo edt jeu --reset`
  efface la base **sans sauvegarde** ; faire d'abord une sauvegarde manuelle
  (section 8). Sa question de confirmation demande `ssh -t`, ou l'option `--oui`.

## 7. Journaux et état

Depuis le poste :

```bash
deploy/etat.sh                # photo complète (service, version, /sante, certificat, disque, edt doctor, journal)
deploy/journaux.sh            # en direct : service + edt.log + erreurs nginx (Ctrl+C pour arrêter)
deploy/journaux.sh service    # journal systemd du service (uvicorn, tracebacks)
deploy/journaux.sh appli      # /var/log/edt/edt.log
deploy/journaux.sh nginx      # accès et erreurs nginx
deploy/journaux.sh calcul     # journal du dernier calcul d'emploi du temps
```

Sur le serveur : `sudo journalctl -u edt -n 100` (ou `-f`, `--since "1 hour ago"`),
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
non initialisée, ou schéma périmé. `sudo edt doctor` dit laquelle ; schéma
périmé : `deploy/deployer.sh --reset-base` (section 4).

**Le service redémarre en boucle.** Même diagnostic. `oom-kill` dans le journal :
le calcul a dépassé 3 Go (`MemoryMax` dans `edt.service`).

**Port occupé.** Le déploiement refuse de commencer si un autre programme que le
service tient le port 8000 ; nginx ne démarre pas si le port 80 ou 443 est pris.
`sudo ss -ltnp 'sport = :8000'` (ou `:80`, `:443`) montre le programme : un
uvicorn lancé à la main et oublié (`sudo kill <pid>`), un autre serveur web
(`sudo systemctl disable --now apache2`).

**Base verrouillée (« database is locked »).** Une commande lancée en même temps
que le service (`sqlite3`, script) bloque la base. `sudo fuser -v /var/lib/edt/edt.db`,
fermer la commande en trop, puis `sudo systemctl restart edt`. Ne jamais
supprimer `edt.db-wal` pendant que le service tourne.

**Base introuvable au déploiement.** « base introuvable alors qu'une version est
en service » : `EDT_DB_URL` a changé dans `edt.env`, ou la base a été déplacée.
Si c'est voulu (base corrompue mise de côté, ci-dessous), relancer avec `--reset-base`.

**Base corrompue** (le contrôle d'intégrité de la sauvegarde échoue : le
déploiement s'arrête avant de toucher la base). La mettre de côté puis recréer
une base vide :

```bash
sudo systemctl stop edt
for f in edt.db edt.db-wal edt.db-shm; do [ -e /var/lib/edt/$f ] && sudo mv /var/lib/edt/$f /var/lib/edt/sauvegardes/manuelle-corrompue-$(date +%Y%m%d-%H%M%S)-$f; done
```

puis, depuis le poste, `deploy/deployer.sh --reset-base` et `sudo edt jeu …`.

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

**Disque plein.** Le déploiement refuse de commencer sous 1 Go libre ;
`deploy/etat.sh` montre la place prise par chaque dossier.

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

**Automatique** : à chaque déploiement, juste après l'arrêt du service, avec
l'API de sauvegarde de SQLite et un contrôle d'intégrité :
`/var/lib/edt/sauvegardes/edt-<date>-avant-<version>.db` (10 gardées).

**Manuelle** (jamais supprimée automatiquement) :

```bash
sudo -u edt sqlite3 /var/lib/edt/edt.db ".backup '/var/lib/edt/sauvegardes/manuelle-$(date +%Y%m%d-%H%M%S).db'"
sudo -u edt sqlite3 /var/lib/edt/sauvegardes/<fichier>.db "PRAGMA integrity_check"   # doit répondre ok
```

Rapatrier une sauvegarde sur le poste (Git Bash) :

```bash
ssh -i ~/.ssh/edt_vps_ed25519 ubuntu@92.222.10.188 'sudo cat /var/lib/edt/sauvegardes/<fichier>.db' > "<fichier>.db"
```

**Restaurer** (sur le serveur, tout en utilisateur `edt`) :

```bash
sudo systemctl stop edt
sudo -u edt sqlite3 /var/lib/edt/edt.db ".backup '/var/lib/edt/sauvegardes/manuelle-avant-restauration-$(date +%Y%m%d-%H%M%S).db'"
sudo -u edt rm -f /var/lib/edt/edt.db-wal /var/lib/edt/edt.db-shm
sudo -u edt install -m 640 /var/lib/edt/sauvegardes/<sauvegarde>.db /var/lib/edt/edt.db
sudo systemctl start edt
```

Puis `deploy/etat.sh`. La sauvegarde doit venir d'une version de même schéma
que celle en service (sinon `/sante` répond 503).

## 10. Modifier la configuration

- **Variables d'environnement** : `sudo nano /etc/edt/edt.env`, puis
  `sudo systemctl restart edt`. Le fichier est lu par systemd (pour le service,
  le lanceur `edt` et les déploiements) : une ligne `NOM=valeur` par variable.
  Le modèle du dépôt ne l'écrase jamais.
- **Service, lanceur ou nginx** : modifier le fichier dans `deploy/`, committer,
  puis refaire les étapes 1 et 2 de la section 3. Si `nginx -t` refuse la
  nouvelle configuration, l'ancienne est remise automatiquement.
- **Autre nom de domaine** : `EDT_DOMAINE=autre.exemple.fr` pour les scripts du
  poste, `sudo env EDT_DOMAINE=autre.exemple.fr bash …` pour ceux du serveur.

## 11. À faire plus tard

- **Désactiver la connexion SSH par mot de passe** (clé uniquement), après avoir
  vérifié la connexion par clé, en gardant une session ouverte pendant le changement.
- **Pare-feu** : s'il est activé un jour, il devra laisser passer les ports
  **22** (SSH), **80** (HTTP, indispensable au renouvellement du certificat) et
  **443** (HTTPS).
- **Sauvegardes hors du serveur** : copier régulièrement une sauvegarde ailleurs
  (commande « Rapatrier » de la section 9).
