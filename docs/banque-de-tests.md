# Banque de tests des cas limites

Ce document présente les tests de cas limites du projet : ce qu'ils sont, les
conventions qui les régissent, leur catalogue complet et les vérifications qui
restent manuelles. Il s'adresse aux développeurs. La stratégie de test
d'ensemble (familles unitaire et intégration, règle de classement,
arborescence, nombres de tests) est décrite dans [tests/README.md](../tests/README.md).

## Ce qu'est un cas limite

Un cas limite est une situation rare mais plausible qui ferait planter
l'application, perdre des données ou afficher un résultat faux : saisie
aberrante (nombre de cinq mille chiffres, texte piégé), base verrouillée ou
corrompue, suppression pendant un calcul, fichier d'import hostile, semestre
d'un seul jour, coupure pendant un déploiement. Chacun est protégé par un
test qui échoue si le défaut revient.

## Conventions

- **Marqueur.** Le test porte `@pytest.mark.cas_limite`, ou son module déclare
  `pytestmark = pytest.mark.cas_limite` quand tous ses tests sont des cas
  limites. Le marqueur est déclaré dans `pyproject.toml`.
- **Docstring.** Elle suit la forme « Cas : … Risque : … ». Le cas décrit la
  situation ; le risque dit ce qui casserait sans le test (erreur 500, donnée
  perdue, message trompeur). Le catalogue ci-dessous est lu dans ces
  docstrings.
- **Déterminisme.** Aucun test ne dépend de l'heure réelle (la date de
  référence est la fixture `aujourdhui`), du hasard, de l'ordre des tests, de
  la charge de la machine ni d'une solution du solveur qui n'est pas forcée
  (`tests/outils/solveur/fabrique.py` permet de forcer un placement).
- **Isolement.** Chaque test travaille dans un dossier temporaire (fixtures
  `environnement`, `base` et `jeu_minimal` de `tests/conftest.py`), jamais
  sur la base de travail.
- **Durée.** Moins de deux secondes par test ; au-delà, le test porte aussi le
  marqueur `perf` et sort de l'intégration continue.
- **Rangement.** Un cas limite se range, comme tout test, dans
  `tests/unitaires/<composant>/` ou `tests/integration/<composant>/` selon la
  règle de [tests/README.md](../tests/README.md).

## Lancer les cas limites

Depuis la racine du dépôt :

```sh
python -m pytest -m cas_limite                         # tous les cas limites
python -m pytest -m "cas_limite and not perf"          # sans les tests de charge
python -m pytest -m "unitaire and cas_limite"          # ceux d'une famille
python -m pytest -m cas_limite tests/unitaires/solveur tests/integration/solveur   # un composant
```

`pytest -m cas_limite` compte les cas collectés (une fonction paramétrée en
donne plusieurs) ; le catalogue compte les fonctions de test.

## Catalogue des cas limites

Le catalogue est produit à partir des tests par `tests/outils/catalogue.py` :
une ligne par fonction de test marquée `cas_limite`, rangée par famille, par
composant et par fichier. Le nom du test est celui de la fonction, sans le
préfixe `test_`. Il ne se modifie pas à la main : après l'ajout ou la
modification d'un cas limite, le régénérer depuis la racine du dépôt.

```sh
python -m tests.outils.catalogue            # affiche le catalogue
python -m tests.outils.catalogue --ecrire   # met à jour ce document
```

`tests/unitaires/socle/test_catalogue.py` échoue si la partie générée diffère
de ce que produit le générateur, ou si un test `cas_limite` n'a pas de « Cas »
ou de « Risque » dans sa docstring.

<!-- catalogue : début de la partie générée par `python -m tests.outils.catalogue --ecrire`, ne pas modifier à la main -->

Le catalogue compte 329 cas : 190 cas en tests unitaires et 139 cas en tests d'intégration.

| Composant | Tests unitaires | Tests d'intégration |
|---|---:|---:|
| `echange` | 39 | 5 |
| `exploitation` | 30 | 23 |
| `socle` | 48 | 24 |
| `solveur` | 18 | 16 |
| `synthese` | 28 | 5 |
| `verification` | 25 | 0 |
| `web_donnees` | 1 | 36 |
| `web_edt` | 1 | 30 |
| **Total** | **190** | **139** |

### Tests unitaires (190 cas)

#### `echange` (39 cas)

[`tests/unitaires/echange/test_bornes.py`](../tests/unitaires/echange/test_bornes.py)

| Test | Cas | Risque |
|---|---|---|
| Entier hors bornes sqlite rejette la ligne sans planter | une capacité, un effectif ou un nombre de séances de 10**20 parmi des lignes valides. | OverflowError au flush de SQLite, non capturée : tout l'import échouait (erreur 500 ou code 2) et aucune ligne du fichier n'entrait. |
| Debordement d entier de sqlite rejette sa ligne seule | un entier au-delà de 2**63 atteint la base (borne de lecture levée pour le test). | sans filet, OverflowError au flush fait échouer tout l'import au lieu de la seule ligne fautive ; c'est la défense en profondeur derrière la borne. |
| Import reserve la base des son ouverture | une autre connexion veut écrire pendant qu'un import a ouvert sa transaction. | en transaction différée (BEGIN émis par le moteur), l'import lisait sans verrou puis échouait aussitôt « database is locked » si une autre écriture avait validé entre-temps (SQLite n'attend pas dans ce cas). simulation_possible ouvre la transaction par edt.db.moteur.transaction_immediate (BEGIN IMMEDIATE). |
| Deux imports simultanes sont serialises sans erreur | deux imports appliqués au même moment (deux onglets du navigateur). | en transaction différée, le second échouait « base occupée » au lieu d'attendre la fin du premier, ou l'un écrasait l'autre. Chaque import ouvre sa transaction par edt.db.moteur.transaction_immediate (BEGIN IMMEDIATE) : le second attend son tour, dans la limite du délai de verrou du socle. |
| Import de dix mille lignes valides sous le seuil | un fichier de 10 000 enseignants valides, simulé (un point de sauvegarde par ligne). | une régression de performance (index perdu, requête par ligne) qui rendrait l'import d'un vrai établissement interminable, sans aucun test pour le voir. |
| Archive aux chemins hostiles lue sans ecriture sur disque | une archive ZIP aux membres « ../../etc/campus.csv » et « C:\Windows\salles.csv ». | sûr tant que l'archive est lue en mémoire ; une évolution qui l'extrairait écrirait hors du dossier (zip slip), et le rapport afficherait des chemins trompeurs. |
| Lecture ical faite avant de reserver la base | un import d'emploi du temps iCal dont les récurrences prennent du temps à calculer. | lu dans la transaction (BEGIN IMMEDIATE), l'agenda réservait la base pendant tout son calcul : une saisie simultanée attendait, puis échouait « base occupée ». |
| Budget de calcul ical partage par l envoi | un envoi de quatre fichiers iCal, chacun aux récurrences coûteuses. | avec un délai par fichier, un envoi de N fichiers hostiles occupait le serveur N fois DELAI_RECURRENCES ; le délai vaut pour tout l'envoi. |
| Service appele avec un identifiant hors base repond introuvable | un appelant du service (commande, futur assistant) passe un identifiant de 10**20. | session.get levait OverflowError, « erreur inattendue » au lieu de « n'existe pas » : les bornes des adresses et des options ne protègent pas un appel direct du service. |

[`tests/unitaires/echange/test_export_edt.py`](../tests/unitaires/echange/test_export_edt.py)

| Test | Cas | Risque |
|---|---|---|
| Ical hebdo tolere un semestre raccourci apres la version | la fin du semestre est avancée au 20/09/2026 après l'enregistrement d'une version. | « Semaine 3 absent(e) » : page 404 « Élément introuvable » qui ne parle ni de la version ni des dates, alors que l'export CSV de la même version passe. |

[`tests/unitaires/echange/test_format.py`](../tests/unitaires/echange/test_format.py)

| Test | Cas | Risque |
|---|---|---|
| Longueurs alignees sur le modele | la longueur d'une colonne du modèle change (un code de salle passe à 64 caractères). | la borne de l'import, recopiée dans LONGUEURS, refuserait des codes valides ou laisserait entrer des textes trop longs pour la base. |
| Toute colonne de texte a une longueur | une nouvelle colonne de texte ou de code est ajoutée au format d'échange. | sans entrée dans LONGUEURS, elle accepterait un texte de plusieurs milliers de caractères (SQLite ne l'arrête pas). |

[`tests/unitaires/echange/test_ical.py`](../tests/unitaires/echange/test_ical.py)

| Test | Cas | Risque |
|---|---|---|
| Fuseau inconnu signale en avertissement | DTSTART et DTEND portent TZID=Mars/Phobos, fuseau inconnu et sans VTIMEZONE. | heure prise pour une heure de Paris sans un mot : séances décalées de plusieurs heures si l'agenda venait d'un autre fuseau. |
| Recurrence ancienne bornee par la periode sans refus | une série quotidienne depuis 1998 ou hebdomadaire depuis 1800, lue sur 2026-2027. | les occurrences antérieures à la période comptaient dans la borne de 10 000 pas : « trop d'occurrences », et les séances du semestre étaient perdues. |
| Regle de recurrence hostile refusee avant tout calcul | une règle à intervalle nul, trop fine ou sans occurrence possible (fichier abîmé ou hostile). | dateutil cherchait sans fin (INTERVAL=0) ou des secondes durant une occurrence inexistante, base réservée ; d'autres formes faisaient planter tout l'import (ValueError, IndexError). |
| Regle de cours ordinaire toujours admise | des règles de cours ordinaires ou rares (tous les quinze jours, le 1er lundi, le 29 février). | la vérification des règles hostiles, trop large, refuserait des séries légitimes et des séances seraient perdues. |
| Evenement aux dates incalculables ecarte sans arreter la lecture | un événement à la durée immense, au-delà de l'an 9999 à Paris, ou au RECURRENCE-ID illisible. | OverflowError ou erreur d'icalendar non capturée : tout l'import planté (« erreur inattendue ») à cause d'un seul événement. |
| Fichier aux recurrences trop couteuses refuse en entier | des règles qui, ensemble, épuisent le temps de calcul d'un fichier (DELAI_RECURRENCES). | un fichier de quelques kilo-octets aux règles impossibles (près d'une seconde chacune) occupait le serveur et réservait la base des minutes durant. |

[`tests/unitaires/echange/test_import_donnees.py`](../tests/unitaires/echange/test_import_donnees.py)

| Test | Cas | Risque |
|---|---|---|
| Export xlsx tolere les caracteres de controle | un libellé de matière contient un caractère de contrôle invisible (U+0001 collé). | openpyxl refusait la cellule (IllegalCharacterError) et tout l'export XLSX de l'établissement échouait en 500 ; un réimport perdait le caractère. |
| Caractere de controle importe est garde et signale | un CSV dont un libellé contient U+0001 (copier-coller depuis un autre logiciel). | valeur corrigée en silence (perte), ou caractère invisible gardé sans que personne ne le sache. |
| Besoin dont le debut impose et la duree depassent minuit est rejete | un besoin de 24 h imposé à 23:00, ou de 3 h imposé à 22:30. | durée et heure contrôlées séparément, le besoin entrait ; toute séance placée à ce début violait la contrainte de plage horaire à l'enregistrement de la version : plantage différé, au calcul, sans lien visible avec l'import. |
| Salle au code contenant arobase reste referencable | une salle de code « A@B » imposée à un besoin, puis export et réimport. | la référence était coupée au premier « @ » (salle « A » du campus « B ») : « Salle inconnue », ligne rejetée, salle imposée perdue à l'aller-retour. |
| Durees absurdes signalees | un trajet de 100 h entre deux campus, un maximum de 90 h par jour. | accepté sans borne, il rendait le calcul infaisable (trajet impossible) ou n'avait aucun sens, sans lien visible avec l'import. |
| Groupe parent de lui meme rejete | un groupe parent de lui-même, nouveau (BOUCLE) ou déjà en base (A1-G1). | l'auto-parenté finissait en IntegrityError générique, ou en boucle sans fin dans le parcours des parents. |
| Ordre des parents lineaire sur une chaine profonde | 20 000 groupes en chaîne, chaque enfant écrit avant son parent. | un ordonnancement récursif lèverait RecursionError au-delà de 1 000 niveaux, un algorithme quadratique prendrait des minutes. |
| Hierarchie profonde et auto parente | l'import réel de 1 500 groupes en chaîne, enfants avant parents, plus une boucle. | un import de hiérarchie profonde qui s'effondre (pile, temps) ou qui laisse entrer la boucle ; mesuré de bout en bout, base comprise. |
| Ical en mode donnees signale le mauvais mode | un agenda .ics envoyé avec le mode « données de l'établissement ». | correct aujourd'hui mais sans test ; une régression ignorerait l'agenda en silence, l'utilisateur croyant son emploi du temps importé. |
| Import en cours signale pendant tout l import | un calcul est demandé depuis un autre onglet pendant qu'un import lit ses fichiers. | sans marqueur consultable, le lancement d'un calcul ne pouvait pas savoir qu'un import tournait (calcul sur des données en train de changer), alors que l'import, lui, refuse un calcul en cours. |
| Import sur une session deja engagee refuse clairement | un appelant lit avec la session, puis lui confie un import (simulation_possible). | la transaction, déjà ouverte en mode différé, ne réserverait pas la base : deux imports simultanés échoueraient au hasard « database is locked », sans que rien ne dise pourquoi. |

[`tests/unitaires/echange/test_import_edt.py`](../tests/unitaires/echange/test_import_edt.py)

| Test | Cas | Risque |
|---|---|---|
| Reimport sans enseignants signale le remplacement des enseignants existants | un tableau de séances sans colonne enseignants pour le besoin IM.1101 CM A1 (ENS001). | le besoin retrouvé était « mis à jour » avec l'enseignant fictif à la place d'ENS001, sans autre trace qu'« enseignant fictif créé » : affectation perdue. |

[`tests/unitaires/echange/test_lecture.py`](../tests/unitaires/echange/test_lecture.py)

| Test | Cas | Risque |
|---|---|---|
| Fichier sans cellule remplie est signale comme vide | un fichier sans aucune cellule de données (BOM seul, « ;;; », Lisez-moi seul...). | rapport sans bilan ni signalement, affiché « Aucune ligne rejetée » : l'utilisateur croyait son fichier pris en compte. |
| Fichier d en tetes seuls dit rien a traiter | un gabarit rempli de ses seuls en-têtes (aucune ligne de données). | confondu avec un fichier vide, il serait signalé en erreur ; il doit au contraire passer et dire « rien à traiter » pour sa feuille. |
| Classeur protege par mot de passe signale clairement | un classeur .xlsx enregistré avec un mot de passe (conteneur OLE2 chiffré). | annoncé « format Excel 97-2003 (.xls) », il laissait l'utilisateur, qui envoie déjà un .xlsx, sans comprendre qu'il fallait retirer le mot de passe. |
| Cellule date dans une colonne code est refusee avec explication | un code « 1-2 » (ou « 8:30 ») que le tableur a converti en date ou en heure. | « 2026-01-02 00:00:00 » enregistré comme code, sans avertissement ; les références à ce groupe échouaient ensuite sans explication. |
| Valeur trop longue pour sa colonne rejetee | une feuille salles avec un code de 5 000 caractères, un bâtiment de 10 000. | SQLite n'impose pas la longueur VARCHAR : la ligne entrait et déformait listes, menus et noms de fichiers d'export. |
| Colonne en double lue une seule fois et signalee une fois | en-têtes « code;campus;capacite;type;Capacité », capacité 30 puis 99. | deux avertissements contradictoires, et la seconde valeur rangée parmi les colonnes libres (extra) de la salle : trace trompeuse. |
| Cellule fusionnee signalee comme telle | un onglet salles où le code N904 est fusionné sur deux lignes (A2:A3). | openpyxl rend vide la cellule absorbée : ligne 3 rejetée « Valeur obligatoire manquante » sans que la fusion, vraie cause, soit nommée. |
| Nombre de serie excel refuse avec un indice | une date (46300) ou une heure (0,354166 pour 8 h 30) gardée en nombre par le tableur. | refus sans explication pour un non-informaticien, ou conversion silencieuse en une date ou une heure fausse. |
| Heure entiere ecrite en nombre reste une heure | une heure saisie « 8 », ou 8.0 dans une cellule de format Nombre. | l'indice « nombre de série » refuserait aussi une heure entière légitime ; un nombre qui n'est pas une heure garde le message ordinaire. |

#### `exploitation` (30 cas)

[`tests/unitaires/exploitation/test_commandes_exploitation.py`](../tests/unitaires/exploitation/test_commandes_exploitation.py)

| Test | Cas | Risque |
|---|---|---|
| Une commande inconnue donne le code 2 attendu par le deploiement | on déploie une version qui n'a pas encore la commande verifier-schema. | si une commande inconnue ne donnait plus le code 2, le déploiement échouerait au lieu d'avertir (deployer-distant.sh teste « verifier-schema --help »). |
| Le message d un calcul interrompu aide a eviter la recidive | un calcul du jeu « grand » épuise la mémoire du service (MemoryMax) et le tue. | l'utilisateur relance le même calcul, qui retombe pour la même raison. |

[`tests/unitaires/exploitation/test_scripts_deploiement.py`](../tests/unitaires/exploitation/test_scripts_deploiement.py)

| Test | Cas | Risque |
|---|---|---|
| Chaque script s arrete a la premiere erreur | un script sans « set -euo pipefail » rencontre une commande en échec. | le script continue sur un état faux (base non sauvegardée, lien mal basculé). |
| Fichiers du deploiement en utf8 et fins de ligne lf | un fichier de deploy/ enregistré sous Windows avec des fins de ligne CRLF. | copié par tar sur le serveur, il casse bash (« $'\r': command not found »), systemd ou nginx. |
| Le depot impose des fins de ligne lf | un poste Windows en core.autocrlf=true extrait le dépôt. | sans eol=lf dans .gitattributes, les scripts arrivent en CRLF dans le dossier local. |
| L exemple d environnement est accepte par l application | edt.env.exemple reçoit une valeur invalide, un commentaire en fin de ligne ou des guillemets (systemd les garde dans la valeur). | installer-serveur.sh copie ce modèle et le service refuse de démarrer (page 502). |
| Un seul port et une seule adresse pour l application | le port de l'application est changé dans un seul des fichiers qui le recopient. | nginx répond 502 ou la vérification de santé interroge le mauvais port, et chaque déploiement finit en retour arrière sans cause visible. |
| Le deploiement compare chaque fichier installe | un fichier est ajouté à installer-serveur.sh sans l'ajouter à la liste comparée par deployer.sh (ou avec une autre destination). | sa modification n'est jamais signalée « à réinstaller » et le serveur garde l'ancienne version sans que personne le sache. |
| La bascule attend la fin des commandes qui tiennent la base | « sudo edt jeu --taille grand » tourne quand un déploiement commence. | sauvegarde au milieu de sa transaction, init-db bloqué par le verrou, base remplacée sous ses pieds au retour arrière : écritures perdues, cause masquée. |
| Fichiers de la base appartenant a root refuses avant la bascule | une commande lancée en root hors du lanceur a créé edt.db-wal (ou -shm) à root. | le service, utilisateur edt, ne peut plus écrire et ne redémarre pas. |
| La finalisation purge les sauvegardes partielles du deploiement | un déploiement précédent a échoué pendant la sauvegarde (disque plein). | des fichiers .partiel s'accumulent dans /var/lib/edt/sauvegardes sans alerte. |
| Horodatages du deploiement sans ambiguite | deux déploiements pendant l'heure répétée du passage à l'heure d'hiver ; journaux du déploiement en heure du serveur (UTC), ceux de l'application en heure de Paris. | nom de version déjà pris (déploiement abandonné sans explication), rotation qui garde les mauvaises sauvegardes, incidents mal recoupés d'une à deux heures. |
| L envoi du code ecrit un temoin de fin controle par le serveur | la connexion est coupée pendant l'envoi du code (git archive \| ssh tar -x). | un dossier de version tronqué reste parmi les versions gardées, et une relance manuelle l'installerait (gabarits manquants : pages en 500 avec /sante à 200). |
| Seules les versions mises en service comptent dans la rotation | plusieurs déploiements de suite sont annulés (envoi coupé, retour arrière). | leurs dossiers occupent les 5 places gardées et poussent dehors les versions saines : plus de version vers laquelle revenir à la main. |
| La remise a zero du deploiement reste non interactive | init-db --reset se met à demander une confirmation (option --oui). | « deployer.sh --reset-base » échoue sur le serveur, où personne ne peut répondre. |
| Le deploiement reconnait une version sans verifier schema | on redéploie une ancienne version qui n'a pas la commande verifier-schema. | sans la convention « commande inconnue = code 2 », le déploiement échoue au lieu d'avertir (contrat vérifié côté application dans test_commandes_exploitation.py). |
| La ci teste la version de python du serveur | une dépendance ou une syntaxe ne fonctionne pas avec le Python du serveur (3.14). | la CI est verte et le déploiement échoue à l'étape « Environnement Python ». |

[`tests/unitaires/exploitation/test_systemd_nginx.py`](../tests/unitaires/exploitation/test_systemd_nginx.py)

| Test | Cas | Risque |
|---|---|---|
| Le service lance l application comme le decrit le guide | edt.service est modifié (point d'entrée, nombre de processus, journal, mémoire). | service qui ne démarre plus, calculs dupliqués entre processus, ou serveur qui s'effondre faute de mémoire au lieu du seul service. |
| La sauvegarde quotidienne utilise la commande de l application | la base n'est sauvegardée qu'au moment d'un déploiement. | une panne de disque entre deux déploiements perd toutes les saisies ; un minuteur mal écrit ne sauvegarderait rien sans que personne le voie. |
| Le lanceur et le service partagent le meme environnement | « sudo edt … » est lancé avec une autre version, un autre utilisateur ou un autre fichier d'environnement que le service. | la commande travaille sur une autre base sans le dire, ou laisse des fichiers appartenant à root qui empêchent le service de démarrer. |
| La limite d envoi de nginx laisse l application repondre | un classeur de 20,3 Mo est envoyé, ou la limite de nginx change. | nginx coupe avant l'application (page 413 au lieu de son message « 20 Mo au plus »), ou laisse passer des envois énormes jusqu'à l'application. |
| Nginx sert une page d erreur installee | l'application est arrêtée (502) ou un envoi dépasse la limite de nginx (413). | page brute de nginx, en anglais et hors de la mise en page, ou page absente du serveur parce que l'installateur ne la copie pas. |
| Une page d erreur inseree par htmx ne restyle pas l application | la page d'erreur répond à une requête HTMX (import, bouton) : app.js l'insère telle quelle dans la zone des messages de la page ouverte. | ses styles visant body, html ou main défigurent toute la page de l'application. |
| La page 413 annonce la limite de l application | la limite d'envoi de l'application change (20 Mo aujourd'hui). | la page 413 de nginx annonce une limite fausse à l'utilisateur. |
| Chaque site nginx inclut la partie commune installee | un des deux modes du site (HTTP avant le certificat, HTTPS après) diverge. | pages d'erreur, limite d'envoi ou proxy absents dans un seul des deux modes. |
| Le guide decrit chaque verification d exploitation | un développeur doit rejouer à la main une vérification impossible à automatiser. | procédure absente, ou sans prérequis, étapes et résultat attendu. |
| Le guide cite chaque fichier de deploy et chaque chemin installe | un fichier est ajouté à deploy/ ou installé ailleurs sur le serveur. | personne ne sait qu'il existe ni où le chercher en cas de panne. |
| Les procedures visent le serveur des scripts | l'adresse du serveur, le compte SSH ou la clé change dans deploy/commun.sh. | les procédures manuelles et les commandes du guide visent encore l'ancien serveur, ou échouent faute de clé. |
| Les commandes du guide passent par sudo pour les dossiers proteges | une commande du guide lit un dossier réservé à root ou à edt (/var/lib/edt, /var/log/edt-deploiements…) avec le compte SSH ubuntu, sans sudo. | « Permission denied » au milieu d'une procédure, ou pire un test [ -e … ] toujours faux qui saute une étape sans erreur (base corrompue jamais mise de côté). |
| Aucune commande du guide n est coupee sur deux lignes | une commande citée dans le texte (entre accents graves) est coupée par un retour à la ligne du fichier. | copiée depuis le fichier, elle part en deux commandes : la seconde échoue et la première s'exécute seule, sans ses options. |

#### `socle` (48 cas)

[`tests/unitaires/socle/test_base.py`](../tests/unitaires/socle/test_base.py)

| Test | Cas | Risque |
|---|---|---|
| Contraintes de controle du modele et maxima positifs | une écriture qui contourne les services viole une règle de cohérence du modèle (créneau à l'envers, besoin de durée nulle, ponctuel sans date, trajet inversé, indisponibilité ni récurrente ni datée, maxima horaires négatifs ou nuls, groupe parent de lui-même). | dernière ligne de défense avant un instantané incohérent ; un remaniement de `modeles.py` perdrait ces CHECK sans signal, et les maxima négatifs rendaient `MAX_ENSEIGNANT` infaisable sans dire que la donnée est absurde. |
| Un code aux espaces invisibles est le meme code | un code saisi ou importé porte une espace insécable, une espace de largeur nulle, des espaces aux bords ou une tabulation (collé depuis un traitement de texte). | « N105 » et « N105 » passaient l'unicité et créaient un doublon invisible, introuvable à la recherche ; le code doit être normalisé dès l'affectation. |
| Les codes et identifiants sont normalises en nfc | le même code en forme Unicode décomposée (« e » + accent) et précomposée ; identifiant d'enseignant avec une espace insécable. | doublons invisibles de campus ou d'enseignants ; la forme enregistrée doit être NFC et l'unicité doit jouer. |
| Init db relance ne recree pas un referentiel supprime volontairement | l'utilisateur a supprimé un type de séance, un équipement et toute la grille pour la ressaisir ; le déploiement suivant relance `init-db`. | lignes ressuscitées sans un mot (choix de l'utilisateur annulé à chaque mise à jour) ; seul un paramètre absent doit être reposé. |
| Le jeu minimal refuse proprement une base avec regles ou sans semestre s1 | `jeu-minimal` sur une base sans référentiel mais avec une règle déjà créée, puis sur une base dont les semestres ont été renommés. | `IntegrityError` brute (unicité (année, code) de la règle) puis `StopIteration` (semestre S1 introuvable) : code 2 / 500 au lieu d'un refus qui dit quoi faire. |

[`tests/unitaires/socle/test_cli.py`](../tests/unitaires/socle/test_cli.py)

| Test | Cas | Risque |
|---|---|---|
| Options de niveau insensibles a la casse et aux espaces | `generer -d pause --niveau " Pause = Facultatif "` (casse et espaces libres). | refus surprenant alors que `--regle r007=Facultatif` tolère casse et espaces ; fragile pour les scripts. |

[`tests/unitaires/socle/test_config.py`](../tests/unitaires/socle/test_config.py)

| Test | Cas | Risque |
|---|---|---|
| Variables d environnement invalides levent une erreur de configuration | une variable EDT_* porte une valeur hors domaine (port « ² », hôte avec espace, niveau de journal ou environnement inconnus). | `ValueError` brute au démarrage (code 2, trace) ou repli silencieux sur le défaut ; le message doit nommer la variable et la valeur lue. |
| Valeurs vides ou entourees d espaces | variables présentes mais vides, ou avec espaces et casse libres. | une variable vide refusée au lieu de prendre son défaut, ou « PROD » non reconnu. |

[`tests/unitaires/socle/test_depliage.py`](../tests/unitaires/socle/test_depliage.py)

| Test | Cas | Risque |
|---|---|---|
| Ponctuel hors des dates du semestre est un probleme de depliage | le semestre commence un mercredi ; une séance ponctuelle est datée du lundi de la même semaine (saisie avant un raccourcissement du semestre). | le dépliage jugeait à la semaine et acceptait, le solveur plaçait, puis l'instanciation rejetait la date avec une raison vague : la cause réelle (hors semestre) doit être dite avant le calcul. |
| Un besoin sans groupe est signale et non deplie | un besoin dont la liste des groupes est vide (import partiel, groupe retiré par un script) ; la base l'accepte, la conception non. | déplié avec un effectif nul (toute salle convient), planifié comme si de rien n'était ; la demande incomplète doit être signalée, pas placée. |

[`tests/unitaires/socle/test_instantane.py`](../tests/unitaires/socle/test_instantane.py)

| Test | Cas | Risque |
|---|---|---|
| Regle salles avec salles homonymes sur deux campus | une seconde salle « N105 » existe à Issy ; une règle SALLES impose « N105 » à la classe A1-G1. | la règle résolue ne visait qu'une des deux salles, au hasard de l'ordre de lecture : résultat faux sans message ; les deux identifiants doivent être retenus. |
| Deux versions actives sont refusees et l index absent est un ecart | base créée sans l'index unique partiel « une version active par semestre » ; deux versions ont été activées. | l'instantané prenait la première ligne : verrous repris d'une version arbitraire, affichage d'une version et calcul sur l'autre, sans message. |
| Un besoin sans groupe est signale dans l instantane | un besoin du jeu perd tous ses groupes (script, import partiel) puis le semestre est chargé. | besoin déplié avec un effectif nul et planifié ; il doit figurer dans les problèmes de dépliage et n'avoir aucune séance. |
| Decomposer un identifiant aux chiffres non decimaux leve une erreur metier | un identifiant de séance venu d'une adresse ou d'un fichier porte un chiffre Unicode (« ² », chiffres d'une autre écriture) ou une forme bancale. | `isdigit()` acceptait « ² » puis `int()` levait une `ValueError` brute (500 / code 2) ; l'erreur doit être métier et nommer l'identifiant (cas transmis par le lot vérificateur). |

[`tests/unitaires/socle/test_jeux.py`](../tests/unitaires/socle/test_jeux.py)

| Test | Cas | Risque |
|---|---|---|
| Panne de generation apres remise a zero est dite | `recharger_jeu_fictif(..., reinitialiser=True)` et la génération du jeu plante (bogue, disque plein) après que la remise à zéro a été validée. | `RuntimeError` brute (500 / code 2) sans dire que la base a été effacée ni qu'aucun jeu n'est chargé ; la docstring promettait « rien n'est écrit sinon ». |
| Jeu fictif sur schema perime indique le reset | une table a une colonne en trop (base d'une autre version) ; on charge un jeu sans remise à zéro, par le service puis par le chemin direct. | « lancer init-db » conseillé alors qu'init-db refusera ; le chemin direct continuait jusqu'à une `OperationalError` (code 2). Le bon remède est « --reset ». |
| Une operation d un autre processus est vue par son marqueur | `edt jeu --reset` ou `init-db --reset` tourne dans un autre processus pendant que le serveur sert des pages. | le verrou étant propre au processus, le serveur répondait « base non initialisée : lancer init-db » (faux) pendant la recréation des tables ; le marqueur posé à côté de la base doit être vu, et un marqueur périmé ignoré. |
| Une graine ou une annee invalides sont refusees avant la remise a zero | l'écran (ou l'assistant) demande un rechargement avec remise à zéro et une graine ou une année de début hors bornes. | la base était remise à zéro, puis la génération refusait la demande : toutes les données perdues pour une faute de saisie (cas transmis par le lot synthèse). |
| Un chargement attend un verrou d ecriture tenu brievement et reussit | un jeu est chargé pendant qu'une autre connexion (autre processus) tient le verrou d'écriture quelques dixièmes de seconde. | avec un BEGIN différé, le chargement lisait puis échouait aussitôt « database is locked » à sa première écriture (SQLite n'attend pas pour une transaction qui a déjà lu) ; ouvert en IMMEDIATE, il attend son tour et réussit. |
| Une remise a zero attend un verrou d ecriture tenu brievement | `init-db --reset` ou la remise à zéro de l'écran pendant qu'une autre connexion écrit brièvement. | suppression du schéma (lecture du catalogue puis DROP) en échec « database is locked », base à moitié vidée ; ouverte en IMMEDIATE, elle attend puis aboutit. |
| Deux chargements simultanes un seul reussit l autre est refuse clairement | deux chargements du même jeu lancés en même temps (deux fils : deux clics, deux onglets). | les deux passent le contrôle « base vide » puis l'un échoue « database is locked » brut ou les données sont doublées ; un seul doit réussir, l'autre être refusé par un message clair (opération en cours, ou base déjà garnie). |
| Deux chargements de deux processus sont serialises sous le verrou | deux chargements depuis deux processus (le verrou du processus ne joue pas) : deux transactions distinctes, lancées ensemble, sur la même base. | les deux lisent « base vide », le second échoue « database is locked » brut à sa première écriture ; en IMMEDIATE le second attend la fin du premier puis est refusé « la base contient déjà des données ». |

[`tests/unitaires/socle/test_journal.py`](../tests/unitaires/socle/test_journal.py)

| Test | Cas | Risque |
|---|---|---|
| Dossier de journaux sous un fichier est une erreur de configuration | EDT_LOG_DIR désigne un chemin sous un fichier existant. | `NotADirectoryError` brute au lieu d'un message qui nomme la variable. |
| Une rotation impossible ne perd ni ligne ni console | la rotation d'`edt.log` échoue parce qu'un autre processus tient le fichier (service web et commande `edt` en même temps, cas de Windows). | « --- Logging error --- » répété sur la console et lignes perdues ; la ligne doit être écrite dans le fichier courant, sans bruit. |
| Le fichier journal suit une rotation faite par un autre processus | un autre processus (ou logrotate) a renommé `edt.log` pendant que celui-ci écrit. | les lignes suivantes partiraient dans l'archive ou un fichier délié (incidents introuvables) ; le flux doit être rouvert sur le nouveau fichier. |
| Les journaux de calcul les plus anciens sont purges | des centaines de calculs ont laissé chacun leur fichier dans `generations/`. | disque plein à terme (« database or disk is full » sur toute écriture) ; seuls les `NB_JOURNAUX_CALCUL` fichiers les plus récents doivent rester, et la purge est dite. |

[`tests/unitaires/socle/test_moteur.py`](../tests/unitaires/socle/test_moteur.py)

| Test | Cas | Risque |
|---|---|---|
| Un point de sauvegarde libere reste annule par la transaction englobante | un service ouvre un point de sauvegarde avant toute écriture, écrit, le libère, puis la route lève (rollback de la transaction). | le pilote sqlite3 n'ouvre une transaction qu'avant une écriture ; sans le contournement du moteur, le RELEASE du SAVEPOINT validait l'insertion malgré le rollback. |
| Un identifiant hors des entiers sqlite est invalide | un identifiant venu d'une adresse ou d'une option vaut 0, -1, 2**63, 10**30… | `session.get` ferait lever `OverflowError` au pilote (500 / code 2) au lieu de répondre « n'existe pas » ; la garde `identifiant_valide` doit tout refuser. |
| Les erreurs du pilote sont expliquees avec leur remede | le pilote SQLite refuse une opération (verrou, lecture seule, fichier corrompu, disque plein). | un message technique sans remède ; l'opérateur pourrait effacer la base pour un simple verrou. |
| Une ecriture attend la fin d une transaction courte | deux fils écrivent presque en même temps (fil de calcul et requête web). | sans `busy_timeout`, la seconde écriture échouerait aussitôt « database is locked » et la saisie ou la version serait perdue ; elle doit attendre puis réussir. |
| Sous un verrou tenu l ecriture echoue apres le delai et les lecteurs sont servis | un autre processus (sqlite3, commande) tient le verrou d'écriture longtemps. | sans mode WAL les lectures seraient bloquées aussi (/sante rouge à chaque import) ; sans délai borné l'écriture attendrait sans fin. Elle doit échouer après le délai, « locked », sans rien écrire, pendant que les lecteurs sont servis. |
| Transaction immediate prend le verrou d ecriture des l ouverture | un écrivain long (l'import) demande le verrou d'écriture dès l'ouverture de sa transaction, avant toute lecture. | avec le BEGIN différé par défaut, une transaction qui a déjà lu et veut écrire échoue aussitôt « database is locked » face à un autre écrivain (SQLite n'attend pas), et deux imports pouvaient commencer en même temps ; les lecteurs doivent rester servis. |
| Transaction immediate apres une requete est une erreur de configuration | le verrou immédiat est demandé après une première requête de la transaction. | SQLAlchemy ignore alors l'option avec un simple avertissement : la transaction resterait différée en silence et l'import croirait tenir le verrou. |
| Une transaction qui lit ne bloque pas les autres ecrivains | une transaction ordinaire a lu (BEGIN différé) pendant qu'un autre processus veut écrire. | si le défaut devenait IMMEDIATE, chaque page lue tiendrait le verrou d'écriture et bloquerait les écrivains ; la politique par défaut doit rester le BEGIN différé. |

[`tests/unitaires/socle/test_parametres.py`](../tests/unitaires/socle/test_parametres.py)

| Test | Cas | Risque |
|---|---|---|
| Un parametre corrompu en base reste reparable par l ecriture et la reinitialisation | `parametre.valeur` mal typée après un import, un script ou une requête SQL (« quinze » pour une durée, « vrai » pour un booléen). | la lecture levait partout (422 sur /parametres et /calculs) et l'écriture comme la réinitialisation commençaient par lire : l'application ne pouvait plus se réparer, seul `init-db --reset` restait (perte des données). |

[`tests/unitaires/socle/test_regles.py`](../tests/unitaires/socle/test_regles.py)

| Test | Cas | Risque |
|---|---|---|
| Une regle hors des heures d ouverture est refusee avec le champ fautif | une règle d'horaires ou de déjeuner cite une heure hors des heures d'ouverture de l'établissement (8 h 30 - 18 h 00 par défaut), ou un créneau interdit y est entièrement extérieur. | règle acceptée alors que le paramètre global équivalent est refusé ; un déjeuner « réservé » à 6 h ne protège rien, une plage 20 h - 22 h rend sa portée infaisable sans nommer la règle, un créneau interdit à 22 h n'interdit rien. |
| Des regles de jours incompatibles sont signalees avec leurs codes | la classe n'a cours que le lundi et son enseignant que le mardi : aucun jour ne convient au TD. | `jours_permis()` vide sans un mot ; la pré-vérification ne savait dire que « la grille n'offre aucun jour permis », blocage inexplicable parmi des dizaines de règles. |
| Une regle sans heures a une phrase ou une erreur nommee jamais une exception brute | une règle enregistrée sans `debut` ni `fin` (import ancien, script), ou une pause libre validée sans paramètres globaux. | `KeyError` dans `phrase()` et `_coherence` : toute la liste des règles répondait 500 pour une seule ligne. Les défauts globaux complètent la phrase ; sans eux, l'erreur nomme le champ. |
| Des salles homonymes sur deux campus sont toutes resolues | deux salles « N105 » sur deux campus (l'unicité est (code, campus)) et une règle SALLES qui cite « N105 ». | un dictionnaire code → identifiant n'en gardait qu'une : le solveur imposait la mauvaise salle et le vérificateur signalait « en N105, hors des salles imposées (N105) ». |

[`tests/unitaires/socle/test_regles_service.py`](../tests/unitaires/socle/test_regles_service.py)

| Test | Cas | Risque |
|---|---|---|
| Brouillon qui n est pas un objet est une validation invalide | l'assistant ou un client JSON envoie une liste, un texte, `null` ou un nombre à `valider()` ou `creer()`. | `dict(brouillon)` levait `ValueError`/`TypeError` (500) : le contrat « valider ne lève pas, renvoie une validation invalide » était rompu ; `creer` doit refuser sans écrire. |
| Modifier refuse un changement d annee ou de code et garde le controle des doublons | `modifier()` reçoit « annee » (autre année) ou « code » dans les changements. | le doublon était cherché dans l'autre année mais la règle restait dans la sienne : deux réglages actifs pour la même cible, et le semestre entier devenait inchargeable ; le changement de code était ignoré en silence. |
| Filtre inconnu est une erreur metier | `lister()` ou `regles_visant()` reçoit un filtre hors énumération (paramètre de requête, valeur de l'assistant). | `ValueError` brute (500) au lieu d'une erreur métier (422) qui liste les valeurs admises. |
| Le code est normalise en majuscules | `creer()` reçoit le code « r002 » alors que R002 existe, puis « r007 » ; la ligne de commande met les codes en majuscules. | « r002 » créé à côté de R002 (unicité sensible à la casse), ignoré par l'attribution automatique des codes et introuvable par la commande. |
| Annee mal formee indique le format attendu | le brouillon donne l'année comme entier, « 2026 », booléen ou « 2026/2027 ». | « L'année scolaire 2026 n'existe pas » : l'assistant ne savait ni le format « 2026-2027 » ni les années existantes, et bouclait sur des corrections. |
| Une regle enregistree sans heures reste lisible | une règle enregistrée (import ancien, script) de type PLAGE_HORAIRE sans `debut` ni `fin` ; la liste et la fiche des règles sont ouvertes. | `KeyError` dans la phrase : toute la liste répondait 500 pour une ligne ; la phrase doit se compléter avec les heures d'ouverture de l'établissement. |
| Regle dont la cible numerique est hors bornes est invalide | brouillon de règle dont la cible est un identifiant numérique hors des entiers de SQLite, négatif ou nul (assistant, adresse forgée). | `OverflowError` du pilote au lieu d'une validation « portee.cible » invalide : page 500 sur l'écran des règles (cas transmis par le lot solveur). |

[`tests/unitaires/socle/test_schema.py`](../tests/unitaires/socle/test_schema.py)

| Test | Cas | Risque |
|---|---|---|
| Un index unique partiel absent est un ecart | la base date d'avant l'index unique partiel « une seule version active par semestre ». | `verifier-schema` disait « à jour », deux versions pouvaient être actives et l'instantané en choisissait une au hasard ; l'index absent doit être un écart de schéma. |
| Une unicite absente ou en trop est un ecart | une table recréée par une ancienne version sans l'unicité du modèle (ou avec une autre). | doublons acceptés (ou refusés à tort) sans que rien ne signale le schéma périmé. |

#### `solveur` (18 cas)

[`tests/unitaires/solveur/test_contraintes.py`](../tests/unitaires/solveur/test_contraintes.py)

| Test | Cas | Risque |
|---|---|---|
| Deux besoins sans groupe sont refuses en les nommant | deux besoins sans aucun groupe inscrit (données hors interface), d'enseignants différents, un seul créneau et deux salles. | une donnée incomplète tolérée en silence, ou une exception brute. Le dépliage ne produit aucune séance pour un tel besoin et la pré-vérification refuse le calcul en nommant chaque besoin ; autrefois, le moteur réunissait les séances sans groupe en une unité fictive « aucun groupe » et prouvait une infaisabilité fausse. |
| Seance finissant apres minuit refusee meme sans horaires | besoin de 25 h, ou besoin de 2 h au début imposé 23 h, la grille active puis ignorée. | placement dont la fin dépasse minuit (occurrence débordant sur le lendemain, refusée ensuite par la base), ou refus muet qui ne dit pas pourquoi. |

[`tests/unitaires/solveur/test_couche_datee.py`](../tests/unitaires/solveur/test_couche_datee.py)

| Test | Cas | Risque |
|---|---|---|
| Placement hors bornes refuse en nommant la seance | semaine type importée avec une séance au jour 7, une séance finissant après minuit (23 h - 25 h) ou une séance de durée nulle. | « Jour invalide : 7 » sans nommer la séance, ou des occurrences débordant minuit enregistrées (contrainte de plage de la base violée plus tard, ou acceptée). |
| Semestre commencant en milieu de semaine et semestre d un jour | semestre du mercredi 09/09 au vendredi 18/09 avec une séance le lundi ; semestre d'un seul jour (un mercredi) avec cinq séances par semaine pour quatre créneaux ce jour-là. | occurrence hors semestre créée ou perdue, replacée dans une mauvaise semaine ; séances « à programmer » en cascade sans raison dite. |

[`tests/unitaires/solveur/test_moteur.py`](../tests/unitaires/solveur/test_moteur.py)

| Test | Cas | Risque |
|---|---|---|
| Options invalides refusees | options du solveur hors bornes : temps limite NaN, infini, démesuré (1e9 s), nul, négatif, en texte ou booléen ; nombre de fils négatif, mille fils, fils ou graine non entiers. | « Aucun emploi du temps trouvé en nan s », calcul sans fin que l'échéance d'abandon ne sait pas calculer (OverflowError), mémoire du serveur épuisée par les fils. |
| Options aux bornes acceptees | options aux bornes mêmes (24 h, 64 fils, 0 fil, 0,5 s) et graines négative ou 2^40. | des bornes trop serrées refuseraient des réglages légitimes ; une graine hors [0, 2^31[ est ramenée dans les bornes de CP-SAT par le moteur, pas refusée. |
| Toutes les seances verrouillees | chaque séance porte un verrou avec sa salle : le modèle n'a ni variable ni choix. | plantage sur un modèle vide (objectif sans terme, statistiques) ou solution non rendue, en mode faisable comme en mode optimisé. |
| Arret pendant la premiere recherche rend la main vite | instantané de 300 séances, arrêt demandé dès que le modèle est construit, pendant la première recherche, avant toute solution (la première solution demande ici plus d'une minute). | le moteur ignore l'arrêt tant qu'aucune solution n'existe, ou met des secondes à rendre la main : bouton « Arrêter » sans effet avant la première solution. |
| Parametres extremes restent valides | pause de 120 min, trajet de 100 000 min entre deux campus, poids 1000, graines -5 et 2^40, mode optimisé. | coefficients CP-SAT qui débordent (MODEL_INVALID), graine refusée, codage jour × pas des débuts faux quand le pas dépasse 100 000. |

[`tests/unitaires/solveur/test_preverification.py`](../tests/unitaires/solveur/test_preverification.py)

| Test | Cas | Risque |
|---|---|---|
| Une seance sans horaire ne produit pas de fausse charge | grille du lundi, une salle, l'enseignante indisponible tout le lundi, un besoin. | trois impossibilités au lieu d'une, dont deux charges parasites « ... pour 0 min disponibles : c'est impossible » qui envoient corriger la charge du groupe ou de l'enseignant. |
| Verrou incoherent ou contredisant son besoin est nomme | séance verrouillée au jour 7, verrou sur une salle absente, verrou de 2 h sur un besoin passé à 1 h 30 (version importée ou besoin modifié après coup), avec et sans la grille. | refus sans nommer la cause, charges parasites en prime, ou verrou appliqué en silence avec la mauvaise durée quand la grille (HORAIRES) est ignorée. |
| Indisponibilite incoherente refuse le calcul | indisponibilité récurrente d'enseignant à début ≥ fin (13 h - 9 h) ou au jour 7 (import ou saisie ancienne). | indisponibilité ignorée en silence (jamais de recoupement) ou plantage ; le calcul doit être refusé en la nommant, en pré-vérification comme au calcul. |
| Grille vide refusee en clair | aucun créneau enregistré et un besoin à placer, la grille active puis ignorée. | refus incompréhensible ou plantage (minimum d'une liste vide) ; la grille ignorée, une séance posée un jour sans créneau, que le vérificateur refuse (« défaut du moteur »). |

[`tests/unitaires/solveur/test_regles.py`](../tests/unitaires/solveur/test_regles.py)

| Test | Cas | Risque |
|---|---|---|
| Niveau d une regle inconnue est signale | options avec un niveau pour la règle n° 999, absente de l'instantané (supprimée, désactivée ou d'une autre année), à côté d'une règle connue. | l'utilisateur croit avoir réglé une règle ; ni le résultat ni la copie des règles du calcul n'en garderaient trace. |

[`tests/unitaires/solveur/test_services.py`](../tests/unitaires/solveur/test_services.py)

| Test | Cas | Risque |
|---|---|---|
| Version a verifier identifiant hors bornes est introuvable | `version_a_verifier(session, version_id=2**70)`. | OverflowError de SQLite au lieu d'ErreurIntrouvable : page 500 au lieu de 404. |
| Regle dont la cible est hors bornes est invalide | brouillon de règle dont la cible est l'identifiant numérique 10^20. | OverflowError de SQLite au lieu d'une validation « portee.cible » invalide : page 500 sur l'écran des règles. |
| Cloture renseigne la duree et nomme la version sans dates | calculs « en cours » abandonnés il y a sept minutes (processus tué), l'un juste après l'enregistrement de sa version, clos par `cloturer_calculs_interrompus`. | « erreur en 0 s » dans l'historique pour un calcul qui a tourné des minutes, et une version active sans séances datées dont personne ne sait qu'il faut relancer l'instanciation. |
| Calcul en cours ne s auto bloque pas sur un calcul abandonne | une requête d'écriture tient le verrou d'écriture (transaction immédiate et une écriture) dans le fil courant, un calcul « en cours » abandonné traîne en base, et la requête consulte `calcul_en_cours()` (import, remise à zéro, paramètres). | la clôture du calcul abandonné attend, dans le même fil, le verrou que la requête tient elle-même : dix secondes puis « base occupée » (auto-blocage), alors qu'il suffit de la reporter et de répondre d'après ce qu'on a lu. |

#### `synthese` (28 cas)

[`tests/unitaires/synthese/test_cas_limites.py`](../tests/unitaires/synthese/test_cas_limites.py)

| Test | Cas | Risque |
|---|---|---|
| Annee de debut hors bornes refusee lisiblement | année scolaire de début absurde (0, 9999) ou hors de 2000-2100. | `ValueError` « year 10000 is out of range » au lieu d'un refus lisible. |
| Annee passee ou lointaine donne un jeu valide | jeu demandé pour une année passée ou lointaine (autres fériés, autres lundis). | calendrier ou témoin faux hors de l'année courante. |
| Graine hors bornes refusee | graine négative, trop grande ou non entière. | `random.Random(-5)` redonne silencieusement le jeu de la graine 5. |
| Graines extremes admises et deterministes | plus petite et plus grande graine admises. | jeu différent d'une génération à l'autre pour une même graine extrême. |
| Borne de graine identique au formulaire web | même graine saisie à l'écran et en ligne de commande. | une graine acceptée par la commande refusée par l'interface, ou l'inverse. |
| Service refuse une graine invalide avant la remise a zero | le service de rechargement (écran, futur assistant) reçoit une graine refusée avec la remise à zéro. | toutes les données perdues pour une faute de saisie. |
| Configuration sans salle refusee a la construction | configuration sur mesure sans site, ou avec un site sans aucune salle. | `IndexError` en pleine génération au lieu d'un refus qui liste le problème. |
| Salles site negatives refusees | capacité d'amphithéâtre nulle ou négative, nombre de salles négatif. | `IntegrityError` SQLite brute, ou salles ignorées sans explication. |
| Configuration qui ne tient pas dans les salles | une seule salle de cours pour toute une promotion (pas d'amphithéâtre). | plantage, besoins perdus en silence ou témoin faux au lieu d'écarts expliqués. |
| Configuration minimale un etudiant par classe | une classe et une classe d'apprentis d'un seul étudiant (demi-groupe vide, parcours minuscules). | division par zéro, groupe vide mal compté, témoin faux. |
| Calendrier et grille degeneres sans plantage | semestre d'un jour, grille d'un créneau, seul jour travaillé le samedi ou un dimanche sans créneau, pause de deux heures. | division par zéro dans les taux, semaine d'intégration mal fermée, besoins perdus en silence, témoin faux. |
| Besoin impossible d avance ecarte et explique | la conférence métiers n'a aucune date ouverte (semestre d'un jour). | besoin omis sans laisser de trace dans le rapport. |
| Semestre tres court avec stage ne plante pas | semestre 2 de deux semaines pour une promotion qui finit l'année en stage. | `IndexError` (le stage prend les quatre dernières semaines) au lieu d'un jeu. |
| Grille vide refusee lisiblement | tous les créneaux de la grille supprimés avant de charger un jeu. | jeu vide incompréhensible ou erreur technique. |
| Semestre manquant refuse lisiblement | le semestre 2 a été supprimé et la taille demandée en a besoin. | `KeyError` au lieu d'un refus qui nomme le semestre manquant. |
| Empreinte figee du jeu petit graine 0 | même taille et même graine sur une autre machine (CI en 3.11 et 3.13, serveur). | les jeux divergent sans que personne ne le voie. |
| Jeu independant des noms proposes par faker | autre version de Faker (autre liste de noms), donc autres collisions de noms. | les tirages du générateur suivent les noms et tout le jeu change avec Faker. |
| Noms epuises homonymes admis sans boucle infinie | plus d'enseignants que de noms distincts chez Faker (800 couples civilité-nom). | boucle infinie, la génération ne se termine jamais. |
| Graine difficile du moyen sans besoin ecarte | graine 13 du jeu moyen, où un premier placement glouton laisse deux TP sans place. | jeu incomplet selon la graine, ou relance du témoin non déterministe. |
| Temoin et regles valides sur trois graines du petit | autres graines que 0 (enseignants, vacataires et règles d'exemple tirés autrement). | règle d'exemple invalide ou témoin faux pour une graine que personne n'essaie. |
| Temoin et regles valides sur dix graines | dix graines par taille (10 à 19). | besoin écarté, témoin faux ou règle d'exemple invalide pour une graine donnée (le jeu de démonstration se recharge souvent). |
| Generation bornee en temps | chargement d'un jeu depuis l'écran (la requête attend la fin de la génération). | génération qui s'emballe et bloque l'interface (moins d'une seconde d'habitude). |

[`tests/unitaires/synthese/test_commande.py`](../tests/unitaires/synthese/test_commande.py)

| Test | Cas | Risque |
|---|---|---|
| Base non initialisee refusee | chargement d'un jeu sans remise à zéro dans une base jamais initialisée. | erreur SQL « no such table » au lieu de l'indication « init-db ». |
| Refus pendant un calcul | chargement ou remise à zéro demandés pendant un calcul d'emploi du temps. | le calcul lit une base qui change ou disparaît sous lui. |

[`tests/unitaires/synthese/test_generation.py`](../tests/unitaires/synthese/test_generation.py)

| Test | Cas | Risque |
|---|---|---|
| Configuration invalide refusee avec le detail | configuration sur mesure incohérente (classes d'apprentis en 1re année). | jeu absurde ou erreur tardive sans la liste des problèmes. |
| Base deja garnie refusee | génération directe dans une base qui contient déjà des référentiels. | doublons ou mélange de deux établissements. |

[`tests/unitaires/synthese/test_performance.py`](../tests/unitaires/synthese/test_performance.py)

| Test | Cas | Risque |
|---|---|---|
| Jeu grand en moins d une minute | *docstring « Cas : … Risque : … » absente ou incomplète* | *docstring « Cas : … Risque : … » absente ou incomplète* |

[`tests/unitaires/synthese/test_regles.py`](../tests/unitaires/synthese/test_regles.py)

| Test | Cas | Risque |
|---|---|---|
| Dejeuner global en mode libre | pause déjeuner réglée en mode libre pour tout l'établissement avant le chargement. | témoin qui prive une personne de sa pause (jeu invalide dès sa création). |

#### `verification` (25 cas)

[`tests/unitaires/verification/test_datee.py`](../tests/unitaires/verification/test_datee.py)

| Test | Cas | Risque |
|---|---|---|
| Occurrence hors du semestre signalee | séances datées avant le premier ou après le dernier jour du semestre (semestre raccourci, import qui déborde). | aucune anomalie (rapport « valide »), et les règles personnalisées, bornées aux semaines du semestre, muettes sur ces dates. |
| Occurrence dupliquee signalee comme doublon | la même séance datée deux fois à la même date, heure et salle (réimport, instanciation reprise). | trois conflits « X et X » (salle, enseignant, groupe) pour un seul défaut, dont la cause, le doublon, n'est jamais nommée. |
| Date sous forme de datetime refusee | une séance datée reçue avec un `datetime` (date et heure) au lieu d'une date. | `TypeError` au tri des jours (erreur 500), ou conflit manqué entre ce `datetime` et une séance du même jour, jamais égaux. |
| Maxima hebdomadaires a cheval sur deux annees civiles | un enseignant au maximum hebdomadaire a cours le 29 décembre 2026 et le 2 janvier 2027, même semaine réelle. | un regroupement par année civile couperait la semaine du Nouvel An et masquerait le dépassement. |

[`tests/unitaires/verification/test_dejeuner_libre.py`](../tests/unitaires/verification/test_dejeuner_libre.py)

| Test | Cas | Risque |
|---|---|---|
| Cycle de groupes ne rend pas le verificateur muet | deux groupes parents l'un de l'autre (import, retouche de la base), un cours de B à midi en déjeuner libre. | plus aucun groupe feuille, donc plus aucune personne : déjeuner, pause, maxima et règles personnalisées ne vérifient plus rien, sans un mot. |

[`tests/unitaires/verification/test_horaires.py`](../tests/unitaires/verification/test_horaires.py)

| Test | Cas | Risque |
|---|---|---|
| Jour sans creneau signale une seule fois | une séance placée un jour sans créneau dans la grille (dimanche, ou grille d'un jour vidée). | la même cause signalée deux fois (HORAIRES et JOURS) : compteurs gonflés et panneau À traiter redondant. |

[`tests/unitaires/verification/test_indicateurs.py`](../tests/unitaires/verification/test_indicateurs.py)

| Test | Cas | Risque |
|---|---|---|
| Grille vide ou d un seul creneau | grille horaire vide, ou réduite à un seul créneau (lundi 8 h 30-10 h 30). | division par zéro ou `min()`/`max()` sur une liste vide (utilisation des salles, créneaux extrêmes), limite de demi-journée absente. |

[`tests/unitaires/verification/test_niveaux.py`](../tests/unitaires/verification/test_niveaux.py)

| Test | Cas | Risque |
|---|---|---|
| Prefixe garde la majuscule d un nom propre | l'enseignant « Le Gall » dépasse un maximum à un niveau important, ou enfreint une règle personnalisée. | « Entorse à une règle importante : le Gall dépasse… » : nom propre déformé dans l'interface, la commande et le JSON. |

[`tests/unitaires/verification/test_paires.py`](../tests/unitaires/verification/test_paires.py)

| Test | Cas | Risque |
|---|---|---|
| Identifiant duplique | la même séance placée deux fois au même créneau de la semaine type. | les deux copies en conflit « avec elles-mêmes » (salle, enseignant, groupe) : trois erreurs de plus pour un seul défaut, le doublon. |
| Aucune contrainte a verifier journalisee | le vérificateur pur reçoit une liste de contraintes vide, ou des niveaux vides. | une liste vide renvoyée sans un mot, prise pour « emploi du temps valide » (les services, eux, la refusent : voir test_service). |

[`tests/unitaires/verification/test_performance.py`](../tests/unitaires/verification/test_performance.py)

| Test | Cas | Risque |
|---|---|---|
| Un semestre entier de 13000 occurrences en moins de 10 secondes | tout un semestre de séances datées (12 900) avec dix règles personnalisées, comme l'écran À traiter le vérifie. | une régression de coût (règles reconstruites par séance et par date, emplois par personne) rendrait À traiter lent de plusieurs secondes sans qu'aucun test ne le voie. |

[`tests/unitaires/verification/test_salles.py`](../tests/unitaires/verification/test_salles.py)

| Test | Cas | Risque |
|---|---|---|
| Salle inconnue verifiee comme salle a definir | un placement désigne une salle absente de l'instantané (supprimée depuis le calcul, base importée). | `ErreurDonneesInvalides` « salle inconnue » : toute la vérification de la version devient impossible. |
| Salles a definir regroupees par seance dans la couche datee | une séance a plusieurs séances datées sans salle, et une dans une salle disparue. | un avertissement « salle à définir » par date (une quinzaine par séance) noie le panneau des anomalies. |

[`tests/unitaires/verification/test_service.py`](../tests/unitaires/verification/test_service.py)

| Test | Cas | Risque |
|---|---|---|
| Version sans seance datee signalee explicitement | une version sans aucune séance datée (instanciation absente ou échouée) est vérifiée sur le calendrier. | rapport vide dit « valide » ; un écran afficherait « Aucune anomalie » pour un calendrier qui n'existe pas. |
| Seances datees apres la fin d un semestre raccourci | le semestre est raccourci de deux semaines après l'instanciation de la version. | les séances datées d'après la nouvelle fin passent sans anomalie, et les règles personnalisées, bornées aux semaines du semestre, se taisent sur elles. |
| Salle supprimee apres le calcul degrade en salle a definir | la salle N204 est supprimée après l'enregistrement et l'instanciation de la version. | un avertissement « salle à définir » par séance datée (une quinzaine pour une seule cause), ou une vérification impossible. |
| Salle orpheline nommee sans rendre la verification impossible | la salle N204 est effacée sans que ses séances soient mises à vide (base importée, retouche SQL). | `ErreurDonneesInvalides` « salle inconnue » : plus aucune vérification de la version (À traiter en erreur, commande en code 2). |
| Periode aux bornes extremes du calendrier | séances datées vérifiées du 1er janvier de l'an 1 au 31 décembre 9999 (`date.min`, `date.max`). | `OverflowError` en étendant la période à la fin de sa semaine (erreur 500, ou code 2 avec une trace pour `--au 9999-12-31`). |
| Liste de contraintes vide refusee | un appelant transmet une liste de contraintes vide (aucune case cochée) ou des niveaux vides. | rapport « valide » sans rien avoir vérifié : une modification manuelle acceptée sans contrôle. |
| Identifiant hors bornes de la base introuvable | un identifiant de version ou de semestre hors des bornes de la base (2**70, saisi dans une adresse ou la commande). | `OverflowError` de SQLite (erreur 500, trace dans la commande) au lieu de « n'existe pas ». |

[`tests/unitaires/verification/test_service_niveaux.py`](../tests/unitaires/verification/test_service_niveaux.py)

| Test | Cas | Risque |
|---|---|---|
| Contrainte desactivee inconnue nommee dans l erreur | un ancien calcul a enregistré une contrainte désactivée au code inconnu (code disparu, retouche). | « Contrainte inconnue : 'PAS_UN_CODE'. » sans version ni calcul : une erreur de vérification inexplicable dans À traiter. |

[`tests/unitaires/verification/test_service_regles.py`](../tests/unitaires/verification/test_service_regles.py)

| Test | Cas | Risque |
|---|---|---|
| Regle supprimee ou desactivee apres le calcul ignoree sans erreur | une règle copiée par le calcul de la version est désactivée ou supprimée ensuite. | `KeyError` ou règle fantôme passée au vérificateur : la vérification de toutes les anciennes versions casserait. |
| Regles du calcul introuvables nommees dans le journal | la copie des règles d'un calcul cite une règle qui n'est plus active (n° 999). | la règle disparaît de la vérification sans laisser de trace : impossible de comprendre pourquoi une entorse d'hier n'est plus signalée. |

[`tests/unitaires/verification/test_volume_verrou_maxima.py`](../tests/unitaires/verification/test_volume_verrou_maxima.py)

| Test | Cas | Risque |
|---|---|---|
| Identifiant de seance illisible refuse proprement | un placement ou une séance datée porte un identifiant de séance aux chiffres non ASCII (« ²-1 », « ٣-1 »). | `str.isdigit` les accepte mais `int` lève `ValueError` : erreur 500 ou trace au lieu d'une anomalie ou d'une erreur métier. |
| Decomposer un identifiant aux chiffres non decimaux leve une erreur metier | `decomposer_id_seance` reçoit « ²-1 » (identifiant cité par une anomalie, une saisie, un import). | `ValueError` au lieu d'`ErreurDonneesInvalides` dans les services qui décomposent les identifiants (anomalies de l'emploi du temps, instanciation). |

#### `web_donnees` (1 cas)

[`tests/unitaires/web_donnees/test_saisies_extremes.py`](../tests/unitaires/web_donnees/test_saisies_extremes.py)

| Test | Cas | Risque |
|---|---|---|
| Service borne les entiers | un appel programmatique (API, assistant) passe un entier Python de 2**70. | le service, sans formulaire pour le borner, le transmet à la base. |

#### `web_edt` (1 cas)

[`tests/unitaires/web_edt/test_cas_limites.py`](../tests/unitaires/web_edt/test_cas_limites.py)

| Test | Cas | Risque |
|---|---|---|
| Bornes des identifiants | identifiant nul, négatif, au-delà de 2^63, booléen, texte, absent ou décimal reçu par les services de ces écrans (`exiger_identifiant`, `obtenir`), à côté de la borne 2^63 - 1, acceptée. | un identifiant qu'aucune ligne SQLite ne peut porter atteint la base (OverflowError, erreur 500) au lieu d'un « introuvable », ou `True` passe pour l'identifiant 1. |

### Tests d'intégration (139 cas)

#### `echange` (5 cas)

[`tests/integration/echange/test_commandes.py`](../tests/integration/echange/test_commandes.py)

| Test | Cas | Risque |
|---|---|---|
| Dossier melange signale l agenda en mode donnees | « python -m edt importer dossier/ » avec salles.csv et agenda.ics, mode données. | l'agenda ignoré en silence, l'utilisateur croyant son emploi du temps importé avec les salles ; le code de sortie doit signaler le problème. |
| Version hors limites en ligne de commande donne un message | « exporter-edt --version 100000000000000000000 » (ou -7, ou 0). | OverflowError dans la recherche de la version : « Erreur inattendue » et trace au lieu de « n'existe pas ». |

[`tests/integration/echange/test_web.py`](../tests/integration/echange/test_web.py)

| Test | Cas | Risque |
|---|---|---|
| Export d une version negative nulle ou enorme dit introuvable | GET /export/emploi-du-temps.csv (ou .ics) avec version_id=-7, 0 ou vingt chiffres. | OverflowError de SQLite au-delà de 2**63 (ValueError au-delà de 4 300 chiffres) : page « erreur inattendue » 500 au lieu du 404 « n'existe pas » des écrans Données (cas transmis par le lot web EDT). |
| Ressource hostile dans l adresse donne une erreur lisible | un filtre groupe, enseignant ou salle énorme ou négatif, ou un identifiant non numérique. | OverflowError dans la recherche du filtre : « erreur inattendue » 500 au lieu de « n'existe pas » (404), ou de « nombre attendu » (422) pour « ² ». |
| Semestre enorme ou negatif a l import donne une erreur lisible | un import d'emploi du temps envoyé avec semestre_id=99999999999999999999 ou -7. | OverflowError dans la recherche du semestre : « erreur inattendue » au lieu de dire que la valeur est hors limites ou que le semestre n'existe pas. |

#### `exploitation` (23 cas)

[`tests/integration/exploitation/test_commandes_exploitation.py`](../tests/integration/exploitation/test_commandes_exploitation.py)

| Test | Cas | Risque |
|---|---|---|
| Init db relance a chaque deploiement n ajoute rien | init-db est relancé à chaque déploiement sur une base déjà initialisée. | doublons de données par défaut, ou échec qui annule tous les déploiements. |
| Init db reset du deploiement ne pose aucune question | « deployer.sh --reset-base » lance init-db --reset sur le serveur, sans terminal. | une question de confirmation bloque ou fait échouer la remise à zéro. |
| La base annoncee au deploiement est celle du service | EDT_DB_URL est saisie avec des blancs en fin de ligne dans /etc/edt/edt.env. | le déploiement sauvegarde ou cherche une autre base que celle du service (base jugée absente, remise à zéro sans sauvegarde). |
| Doctor tolere le port tenu par le service | la finalisation du déploiement (ou etat.sh) lance doctor pendant que le service tient le port 8000. | un contrôle en échec à chaque déploiement réussi, qui masquerait les vrais échecs. |
| Serve et le service lancent la meme application | le point d'entrée change dans « serve » ou dans edt.service, pas dans l'autre. | le poste de développement et le serveur ne servent plus la même application. |
| Serve refuse un port ou une adresse invalide | « serve » reçoit un port hors de 1-65535 ou une adresse vide. | trace « erreur inattendue » (OverflowError), ou valeur remplacée en silence par celle de la configuration (port 0 → 8000). |
| La base reste saine apres un arret brutal en pleine ecriture | systemd tue le service (fin de TimeoutStopSec, mémoire épuisée) pendant qu'il écrit une grosse transaction, déjà en partie dans le journal WAL. | base abîmée ou à moitié écrite au redémarrage ; init-db, verifier-schema ou doctor qui refusent ensuite de travailler. |
| Le redemarrage clot le calcul tue avec le service | le service est tué (mémoire épuisée, arrêt forcé) pendant un calcul, puis systemd le redémarre. | le calcul reste « en cours » pour toujours et bloque tout nouveau calcul ; /sante ne revient pas à 200. |
| Sauvegarder ecrit une copie verifiee et garde les plus recentes | la sauvegarde quotidienne tourne chaque nuit depuis des semaines. | copie incohérente ou invérifiée, ou disque rempli faute de rotation. |
| Sauvegarder garde toutes les sauvegardes manuelles | un développeur sauvegarde à la main avant une opération risquée. | sa sauvegarde est emportée par la rotation des sauvegardes quotidiennes. |
| Sauvegarder une base illisible echoue sans laisser de fichier | la base de production est abîmée (fichier écrasé, disque défaillant). | une « sauvegarde » de la base abîmée remplace les bonnes dans la rotation. |
| Sauvegarder refuse quand le disque est plein | le disque du serveur est presque plein au moment de la sauvegarde. | copie interrompue, fichier partiel, et disque rempli au point de bloquer le service. |
| Sauvegarder sans base le dit | la sauvegarde quotidienne tourne alors que la base a été déplacée ou supprimée. | réussite silencieuse sans rien sauvegarder, ou base vide créée à sa place. |
| Sauvegarder refuse un prefixe reserve ou invalide | on choisit le préfixe « edt », celui des sauvegardes du déploiement, ou un nom avec des espaces ou des majuscules. | la rotation du déploiement efface des sauvegardes qu'elle n'a pas faites. |
| Sauvegarder retire un fichier partiel abandonne | une sauvegarde précédente a été interrompue (serveur éteint, disque plein). | fichiers « .partiel » accumulés, pris à tort pour des sauvegardes. |
| Verifier sauvegarde accepte une sauvegarde conforme sans la modifier | exercice de restauration : on contrôle la dernière sauvegarde avant d'en avoir besoin. | on découvre le jour de la panne qu'elle est abîmée, ou le contrôle la modifie. |
| Verifier sauvegarde signale un schema different | la sauvegarde vient d'une version dont le modèle avait une autre colonne. | restaurée sous cette version, elle donnerait une application en 503 ou en 500. |
| Verifier sauvegarde refuse un fichier abime vide ou absent | on contrôle un fichier abîmé, une base qui n'est pas celle de l'application, ou un chemin mal tapé. | un fichier inutilisable est pris pour une sauvegarde restaurable. |

[`tests/integration/exploitation/test_scripts_deploiement.py`](../tests/integration/exploitation/test_scripts_deploiement.py)

| Test | Cas | Risque |
|---|---|---|
| Chaque script passe le controle de syntaxe de bash | un script de deploy/ modifié par une PR contient une erreur de syntaxe. | le déploiement ou l'installation s'arrête sur le serveur, au pire en pleine bascule. |
| Processus hors service liste une commande de l utilisateur | un processus de l'utilisateur de l'application tourne hors du service. | la recherche ne le voit pas (lecture de /proc, format de ps) et la bascule remplace la base qu'il est en train d'écrire. |
| Processus hors service ignore les processus du service | seuls les processus du service lui-même tiennent la base. | la recherche les compte comme étrangers et chaque déploiement est refusé. |
| La sauvegarde du deploiement reussit puis retire sa copie en cas d echec | la base à sauvegarder pendant la bascule est illisible (ou le disque se remplit). | une copie partielle reste sous un nom de sauvegarde et le script continue. |
| Etat compte les arrets pour memoire epuisee | un calcul dépasse MemoryMax ; le noyau tue le service, que systemd redémarre aussitôt (/sante répond de nouveau 200). | la panne passe inaperçue et se répète à chaque gros calcul, parce que etat.sh cherche une ligne que systemd n'a pas écrite cette fois-là (résultat 'signal'). |

#### `socle` (24 cas)

[`tests/integration/socle/test_cli.py`](../tests/integration/socle/test_cli.py)

| Test | Cas | Risque |
|---|---|---|
| Une base verrouillee lecture seule ou corrompue donne un message clair et le code 1 | `init-db` ou `jeu-minimal` tourne alors qu'un autre processus tient le verrou d'écriture, que le fichier est en lecture seule, ou qu'il n'est plus une base SQLite. | trace brute et code 2, et le script de déploiement enchaînait sur « --reset-base » (qui efface les données) pour un simple verrou ; il faut un message qui nomme la cause et le remède, code 1. |
| Init db reset exige une confirmation dans un terminal et aucun calcul en cours | `init-db --reset` tapé dans un terminal, puis demandé pendant qu'un calcul tourne dans un autre processus, puis en production hors calcul. | base de production effacée par mégarde sans question ni sauvegarde, calcul en cours qui meurt « no such table » ; ici refus dans les deux premiers cas et copie « edt-<date>-avant-reset.db » dans le troisième. |
| Init db reset non interactif ne pose pas de question | `init-db --reset` lancé par un script (déploiement), sans terminal, en développement. | une question posée à un script le bloquerait ou le ferait échouer ; en développement, pas de copie de sauvegarde qui encombre le dossier. |
| Doctor signale une base en lecture seule | `edt.db` a été copié avec les droits d'un autre utilisateur (lecture seule). | doctor disait « Tout est prêt » alors qu'aucune écriture n'était possible ; il doit échouer en nommant la lecture seule. |
| Commandes de calcul avec un identifiant inexistant | `generer`, `pre-verifier` ou `instancier` reçoivent un identifiant inexistant, nul ou négatif. | trace Python (code 2) ou ligne de calcul créée pour un semestre inexistant ; il faut le code 1, « n'existe pas », et aucun calcul enregistré. |
| Doctor signale le dossier des journaux de calcul inutilisable | `<EDT_LOG_DIR>/generations` est un fichier (ou n'est pas inscriptible) alors que le dossier racine des journaux va bien. | doctor disait « Journaux accessibles » et « Tout est prêt » alors qu'aucun calcul n'aurait de journal (cas transmis par le lot solveur). |

[`tests/integration/socle/test_config.py`](../tests/integration/socle/test_config.py)

| Test | Cas | Risque |
|---|---|---|
| Doctor signale une adresse d ecoute introuvable | EDT_HOST désigne un nom qui ne se résout pas. | `serve` mourrait sur `gaierror` avec une trace, et doctor concluait à tort « le serveur tourne peut-être déjà » (code 0) au lieu d'un échec qui nomme EDT_HOST. |

[`tests/integration/socle/test_instantane.py`](../tests/integration/socle/test_instantane.py)

| Test | Cas | Risque |
|---|---|---|
| Un identifiant hors des entiers sqlite est introuvable | un identifiant de semestre ou de version au-delà de 2**63, négatif ou nul arrive par une adresse web ou une option de commande. | `OverflowError` brute du pilote (500 côté web, code 2 en ligne de commande) alors que -1 donnait proprement « n'existe pas » ; tous doivent être introuvables. |
| Une commande avec un identifiant hors bornes repond n existe pas | `pre-verifier --semestre 99999999999999999999` ou `verifier --version 2**63` (identifiant au-delà des entiers de SQLite). | `OverflowError` brute du pilote, « Erreur inattendue » avec trace, au lieu de « n'existe pas » avec le code de sortie que la commande rend déjà pour -1 : 1 (erreur métier) pour `pre-verifier`, 2 (« vérification impossible ») pour `verifier`. |

[`tests/integration/socle/test_journal.py`](../tests/integration/socle/test_journal.py)

| Test | Cas | Risque |
|---|---|---|
| Fichier journal non ouvrable leve une erreur de configuration et doctor le dit | `edt.log` ne peut pas être ouvert (un dossier porte son nom, ou il appartient à un autre utilisateur). | `PermissionError` brute à chaque commande (code 2, « la trace est dans le journal » qui n'existe pas), message en double, boucle de redémarrage du service ; doctor, censé diagnostiquer, ne démarrait pas. |

[`tests/integration/socle/test_moteur.py`](../tests/integration/socle/test_moteur.py)

| Test | Cas | Risque |
|---|---|---|
| Une url sqlite sans fichier est refusee | EDT_DB_URL désigne une base SQLite en mémoire (chemin oublié dans edt.env). | chaque fil d'exécution (serveur, calcul, commande) verrait sa propre base vide ; init-db dirait « 59 lignes » et le calcul échouerait « no such table ». |
| Une url sans pilote ou de schema inconnu est une erreur de configuration | EDT_DB_URL vise un autre moteur (pilote absent) ou porte une faute de frappe. | exception brute de SQLAlchemy (code 2, trace), service qui ne démarre pas, au lieu d'un message de configuration ; le mot de passe de l'URL ne doit pas s'afficher. |

[`tests/integration/socle/test_parametres.py`](../tests/integration/socle/test_parametres.py)

| Test | Cas | Risque |
|---|---|---|
| L ecran des parametres reste ouvrable avec une valeur corrompue | l'utilisateur ouvre /parametres pour corriger une valeur corrompue en base. | l'écran répond 422 et ne peut pas servir à réparer ; il doit s'ouvrir (200) et signaler la valeur enregistrée invalide à réenregistrer. |

[`tests/integration/socle/test_web.py`](../tests/integration/socle/test_web.py)

| Test | Cas | Risque |
|---|---|---|
| Cycle de vie du serveur clot les calculs interrompus et journalise | démarrage puis arrêt du serveur sur une base prête, avec un calcul resté « en cours » depuis un processus disparu. | le cycle de vie n'était testé nulle part : journalisation du démarrage, clôture des calculs abandonnés et message d'arrêt pouvaient casser sans signal. |
| Cycle de vie sans base initialisee avertit sans echouer | le serveur démarre avant `init-db` (base vide). | démarrage en échec ou silence ; il doit démarrer et dire quoi lancer. |
| Le serveur demarre et repond base indisponible malgre une base corrompue | `edt.db` a été tronqué ou remplacé (« file is not a database ») avant le démarrage du serveur. | « Application startup failed », cinq redémarrages puis 502 pour tout le monde et `/sante` jamais servi ; une fois servi, 500 « erreur inattendue » car `sqlite3.DatabaseError` n'est pas une `OperationalError`. |
| Une route sur une base corrompue repond 503 avec le remede | une page qui écrit dans la base alors que le fichier n'est plus une base SQLite. | 500 « erreur inattendue » au lieu du 503 « base indisponible » qui nomme le remède. |
| Le serveur demarre malgre une base verrouillee et reporte la cloture des calculs | un autre processus tient le verrou d'écriture au démarrage, et un calcul abandonné attend d'être clos. | la clôture (une écriture) faisait échouer le démarrage ; elle doit être reportée (avertissement), les lectures servies (mode WAL) et une écriture répondre « occupée ». |
| Une panne du pilote de base est un 503 qui dit quoi faire | une route reçoit du pilote SQLite une erreur de verrou, de table absente ou de fichier illisible. | page 500 sans remède ; chaque cause doit donner un 503 avec sa marche à suivre. |
| Une base en lecture seule ou un disque plein sont nommes | la base est en lecture seule (fichier copié en root) ou le disque est plein. | 503 générique « réessayer » : le travail saisi est perdu et rien ne nomme les droits ni l'espace disque. |
| Sante signale une base en lecture seule | `edt.db` est en lecture seule (droits), le déploiement a réussi. | `/sante` vert et supervision rassurée alors qu'aucune écriture n'est possible. |
| Head repond comme get sans corps | la supervision (sonde, `curl -I`) envoie HEAD / et HEAD /sante. | 405 « Action non permise » partout : la supervision conclut à une panne permanente. |
| Une methode non permise donne une page 405 | un client envoie PUT ou DELETE sur une page. | trace ou page blanche ; la page « Action non permise » doit répondre 405. |
| Remise a zero par un autre processus donne base occupee | un autre processus (`edt jeu --reset`) a supprimé les tables et les recrée pendant que le serveur sert une requête. | « base non initialisée : lancer init-db » (faux et dangereux) ; le marqueur posé par l'autre processus doit faire répondre « occupée, réessayer ». |

#### `solveur` (16 cas)

[`tests/integration/solveur/test_services.py`](../tests/integration/solveur/test_services.py)

| Test | Cas | Risque |
|---|---|---|
| Options hors bornes refusees par la commande | `generer --temps-limite nan`, `--temps-limite inf` ou `--fils 1000` (Typer accepte nan et inf comme flottants). | « Aucun emploi du temps trouvé en nan s » après quelques millisecondes, un calcul sans fin dont l'échéance d'abandon déborde, ou mille fils qui épuisent la mémoire du serveur. |
| Temps limite enregistre illisible ne bloque pas le suivi | calcul « en cours » d'un autre processus vivant dont le temps limite enregistré vaut l'infini ou un texte (ligne ancienne ou corrompue). | `timedelta(seconds=inf)` lève OverflowError et `float("abc")` ValueError dans le contrôle des calculs abandonnés : `calcul_en_cours()` plante, et toutes les pages avec lui. |
| Identifiant hors bornes est introuvable | identifiant 2^70 (hors bornes des entiers SQLite) donné au suivi, à l'arrêt, au lancement, aux pré-vérifications, à l'instanciation ou au replacement. | `session.get` lève OverflowError au lieu de répondre « introuvable » : page 500 « erreur inattendue » avec trace, alors que -1 est correctement refusé. |
| Donnees modifiees pendant le calcul sont dites | un besoin du semestre est supprimé pendant que le moteur calcule (les écrans Données ne consultent pas le calcul en cours). | IntegrityError à l'enregistrement de la version, état « erreur inattendue » avec trace, minutes de calcul perdues sans que la cause soit dite. |
| Semestre sans besoin ne cree pas de version vide | Générer sur un semestre sans aucun besoin, qui a une version de référence active. | « Aucune séance à placer » pris pour un succès, version vide enregistrée et activée, l'ancienne désactivée : l'emploi du temps affiché se vide sans confirmation. |
| Journal de calcul impossible donne la vraie cause | `<EDT_LOG_DIR>/generations` est un fichier (ou un dossier non inscriptible). | tout calcul échouerait « Le détail est dans le journal du calcul » avec un journal inexistant ; plus aucun emploi du temps productible tant que personne ne lit edt.log. |
| Doctor signale le dossier des journaux de calcul inutilisable | `doctor` alors que `<EDT_LOG_DIR>/generations` est un fichier (corrigé par le socle à la demande de ce lot ; le socle a son propre test, celui-ci garde le point de vue du calcul). | « Journaux accessibles » et « Tout est prêt » alors qu'aucun calcul n'aura de journal. |
| Un calcul illisible est clos sans bloquer l application | lignes de calcul aux JSON mal typés : « en cours » avec statistiques en texte et temps limite « abc » ; « réussi » avec avertissements en texte, niveaux « pause », niveaux de règles et règles illisibles, contraintes désactivées en texte. | AttributeError ou ValueError dans le suivi : accueil et écran Calculs en 500, lancement impossible jusqu'à une réparation SQL ; avertissements affichés lettre à lettre. |
| Version enregistree sans instanciation dit de relancer l instanciation | l'instanciation échoue (exception) ou est interrompue (KeyboardInterrupt) juste après l'enregistrement de la version. | version active sans aucune séance datée et personne de prévenu (« erreur inattendue » ou succès muet) : l'utilisateur regarde un calendrier vide. |
| Lancer un calcul pendant un chargement est refuse | Générer pendant qu'un chargement de jeu fictif ou une remise à zéro tient le verrou de la base (tables supprimées puis recréées). | calcul sur des tables en recréation (« no such table », erreur inattendue) ou version qui référence des besoins disparus ; l'inverse est refusé et l'asymétrie surprend. |
| Lancer un calcul pendant un import est refuse | import de données en cours (fichier en cours de lecture), Générer depuis un autre onglet. | calcul sur des données en train de changer, version qui référence des besoins remplacés ; l'inverse (import pendant un calcul) est refusé, l'asymétrie surprend. Le test exerce le vrai marqueur `edt.services.echange.import_en_cours` (seule la lecture du fichier est remplacée, pour tenir l'import ouvert). |
| Deux lancements simultanes dans deux processus | la commande `generer` et le bouton Générer lancent un calcul au même instant depuis deux processus (le verrou de `generation` est local à un processus). | deux calculs parallèles sur les deux cœurs du serveur, deux versions activées l'une après l'autre : la règle « un seul calcul à la fois » ne tiendrait qu'au sein d'un processus. |
| Solution sans salle ou a salle inconnue du moteur est refusee | un moteur de remplacement rend des placements sans salle, ou avec une salle inconnue de l'instantané (le placement manquant est couvert par `test_solution_incomplete_refusee`). | version partielle ou incohérente enregistrée (contraire au tout-ou-rien), ou « erreur inattendue » à l'enregistrement. |
| Verrous contradictoires toleres de bout en bout | deux séances verrouillées sur le même créneau et la même salle dans la version active ; nouveau calcul sans l'option, puis avec « tolérer les violations héritées ». | refus sans le conseil de tolérer, ou solution refusée par le vérificateur (« défaut du moteur ») alors que l'utilisateur a demandé de tolérer ces verrous. |
| Echec de l ecriture finale est retente | la base est verrouillée au moment où le fil du calcul écrit son état final (longue écriture d'un import ou d'une remise à zéro au-delà du délai d'attente). | état final perdu, calcul « en cours » puis clos « interrompu par l'arrêt de l'application » alors que l'application tourne ; message et statistiques perdus. |
| Un saut d horloge ne clot pas un calcul vivant | l'heure locale avance d'une heure (passage à l'heure d'été, correction NTP) pendant qu'un calcul d'un autre processus tourne depuis trente secondes. | échéance « temps limite + grâce » atteinte une heure trop tôt, calcul vivant clos « interrompu » à tort ; temps écoulé affiché majoré d'une heure. |

#### `synthese` (5 cas)

[`tests/integration/synthese/test_cas_limites.py`](../tests/integration/synthese/test_cas_limites.py)

| Test | Cas | Risque |
|---|---|---|
| Commande refuse sans toucher a la base | `python -m edt jeu --reset --oui` avec une graine ou une année refusée. | la base est vidée par la remise à zéro, puis la génération échoue. |

[`tests/integration/synthese/test_commande.py`](../tests/integration/synthese/test_commande.py)

| Test | Cas | Risque |
|---|---|---|
| Remise a zero demande confirmation | `jeu --reset` sans `--oui`, et l'utilisateur répond non à la confirmation. | toutes les données supprimées sans accord explicite. |
| Remise a zero sur une base neuve | `jeu --reset` alors que le fichier de base n'existe pas encore. | « no such table » en cherchant un calcul en cours dans une base sans tables. |
| Second chargement refuse avec un message clair | second chargement d'un jeu dans une base qui en contient déjà un. | doublons de codes (erreur d'unicité brute) ou mélange de deux jeux. |
| Taille inconnue | taille de jeu mal orthographiée (« geant »). | `KeyError` brute au lieu de la liste des tailles disponibles. |

#### `web_donnees` (36 cas)

[`tests/integration/web_donnees/test_cas_limites.py`](../tests/integration/web_donnees/test_cas_limites.py)

| Test | Cas | Risque |
|---|---|---|
| Suppression d un besoin ou d une salle pendant le calcul est refusee | pendant un calcul, on supprime un besoin ou une salle du semestre calculé (page ou ligne). | la suppression passe, le calcul enregistre des séances qui pointent vers elle, échoue sur une contrainte d'intégrité et ses minutes de travail sont perdues. |
| Suppression du semestre refusee pendant son calcul | on supprime le semestre (ou l'année) pendant son calcul. | le calcul est supprimé en cascade sous son fil : le suivi répond 404 chaque seconde, le verrou « un seul calcul à la fois » tombe et un second calcul peut démarrer en parallèle. |
| Regles personnalisees supprimables pendant un calcul | pendant un calcul, on supprime les règles personnalisées actives. | si le calcul relisait ses règles à la fin, il échouerait ; il les copie à son lancement, et ce test garde la décision de ne pas bloquer leur suppression (contrairement aux autres données). |
| Duree d une annee bornee | une année scolaire saisie jusqu'au 31/12/9999, ou « 2227 » pour « 2027 ». | acceptée, elle fait déborder le calcul des semaines (erreur 500 sur Générer) ou produit des centaines de milliers de séances datées. |
| Duree d un semestre bornee | un semestre de plus d'un an dans une année longue (deux ans, permise). | un calcul sur ce semestre instancie des milliers de semaines et gèle le serveur. |
| Calculs d un semestre sain sans erreur | la page Générer d'un semestre ordinaire, après les bornes posées sur les dates. | une borne trop stricte ferait refuser ou planter un semestre normal. |
| Adresse de retour avec saut de ligne est ecartee | le champ caché `_retour` d'un formulaire est modifié en « /salles », un retour à la ligne et « X-Injecte: 1 ». | la valeur part telle quelle dans l'en-tête HX-Redirect (en-tête injecté, ou réponse perdue), ou dans une redirection vers une adresse mutilée. |
| Hx current url illisible est ignore | une requête HTMX arrive avec l'en-tête HX-Current-URL « http://[::1 » (navigateur ou extension fautifs). | urlsplit lève ValueError, erreur 500 et trace dans le journal pour une simple ligne de tableau. |
| Hx current url illisible dans le panneau d une seance | le panneau d'une séance (grille de l'emploi du temps) demandé avec un HX-Current-URL illisible. | erreur 500 au clic sur une séance. |
| Fragments sans htmx redirigent vers la page | l'adresse d'un fragment (ligne, confirmation dans la ligne, aperçu de règle) est ouverte directement : lien copié, historique, navigateur sans JavaScript. | un morceau de tableau nu, sans menu ni styles, dont les boutons perdent le contexte de la liste. |
| Fragments des autres ecrans sans htmx | un fragment des écrans Générer, Emploi du temps ou À traiter ouvert directement. | HTML nu sans mise en page (le compteur d'onglet, le suivi d'un calcul...). |
| Champs libres echappes sur les pages de donnees | un code de salle « <b>x</b> », un bâtiment « "><img onerror=...> », un libellé de groupe et un commentaire de règle « <script> », affichés dans les listes, fiches, confirmations, lignes HTMX et messages. | un seul \|safe oublié et du script s'exécute chez le responsable de scolarité. |
| Champs libres echappes sur les pages de l emploi du temps | les mêmes textes piégés, plus un motif d'annulation « <script> », dans la grille, les panneaux de séance, l'export et À traiter. | script exécuté au simple affichage de l'emploi du temps. |

[`tests/integration/web_donnees/test_parametres.py`](../tests/integration/web_donnees/test_parametres.py)

| Test | Cas | Risque |
|---|---|---|
| Parametres refuses pendant un calcul | pendant un calcul, on enregistre les paramètres ou on les remet à leur valeur par défaut. | le calcul, qui relit les paramètres pour contrôler et dater sa solution, produit une version vérifiée avec d'autres réglages (anomalies inexpliquées dans À traiter), alors que Générer refuse déjà de changer la pause déjeuner pendant un calcul. |
| Valeur corrompue signalee puis reparee depuis l ecran | une valeur de paramètre corrompue en base (« quinze » pour la pause entre deux séances), puis l'utilisateur ouvre /parametres et enregistre. | l'écran, seul moyen de la corriger, ne s'ouvre plus (422), ne dit pas quelle valeur est en cause, ou refuse l'enregistrement qui la répare. |

[`tests/integration/web_donnees/test_performance.py`](../tests/integration/web_donnees/test_performance.py)

| Test | Cas | Risque |
|---|---|---|
| Pages du jeu grand sous deux secondes | les pages les plus lourdes (besoins de tous les semestres, arbre des groupes, formulaires de règles avec toutes les salles, Générer à froid) sur l'établissement complet. | une page de plusieurs secondes gèle le serveur pour tout le monde, un tableau non paginé pèse des mégaoctets ; sans seuil, la régression passe inaperçue. |
| Export des donnees du jeu grand sous dix secondes | l'export Excel de toutes les données de l'établissement complet. | un export qui dépasse dix secondes bloque le serveur (une requête à la fois). |

[`tests/integration/web_donnees/test_regles.py`](../tests/integration/web_donnees/test_regles.py)

| Test | Cas | Risque |
|---|---|---|
| Identifiants enormes des regles sont introuvables | une adresse des règles retouchée avec une année ou un numéro de règle de vingt chiffres. | OverflowError de SQLite, erreur 500 là où les autres écrans répondent « introuvable ». |
| Regles sans annee scolaire affiche un bandeau | toutes les années scolaires ont été supprimées, puis on ouvre « Règles personnalisées ». | une page d'erreur 404 pour une entrée du menu, sans dire quoi faire. |

[`tests/integration/web_donnees/test_saisies_extremes.py`](../tests/integration/web_donnees/test_saisies_extremes.py)

| Test | Cas | Risque |
|---|---|---|
| Capacite trop grande | une capacité de vingt chiffres. | OverflowError de SQLite (erreur 500) ou capacité absurde enregistrée. |
| Effectif trop grand | un effectif de groupe de vingt chiffres. | erreur 500 ou effectif absurde qui fausse le contrôle des capacités. |
| Identifiant trop grand dans un choix | une liste de choix (campus) renvoyée avec un identifiant de vingt chiffres. | OverflowError à la recherche de l'élément choisi. |
| Chiffres exotiques refuses | une capacité « ² », « ٣ » (chiffre arabe), « 1e3 » ou « 2**70 ». | Python les lit comme des chiffres (`isdigit`) et une valeur inattendue est enregistrée. |
| Annee de debut exotique | « Ajouter l'année » avec une année de début « ² ». | int() lève ValueError, erreur 500 sur le calendrier. |
| Graine exotique | la graine du jeu fictif saisie « ² ». | int() lève ValueError, erreur 500. |
| Adresses aberrantes | une adresse retouchée à la main (page « ² », identifiant ou filtre de vingt chiffres). | erreur 500 au lieu de « introuvable » ou « valeur invalide ». |
| Jours feries d une annee inexistante | « Ajouter les fériés » envoyé pour une année de vingt chiffres. | erreur 500 au lieu de « introuvable ». |
| Liste vide sans filtre pose | une liste vide alors que seul le filtre par défaut (l'année en cours) s'applique. | le message « rien ne correspond à vos filtres » fait chercher un filtre inexistant au lieu de proposer d'ajouter un élément. |
| Liste vide avec filtre pose | une recherche qui ne trouve rien. | liste vide sans moyen visible de revenir à la liste complète. |
| Nombres de cinq mille chiffres refuses | un nombre de 5 000 chiffres dans une adresse (page, année, identifiant) ou un formulaire (capacité, graine, règles confirmées, cible, trajet). | Python refuse de convertir plus de 4 300 chiffres, ValueError non rattrapée, erreur 500. |
| Seance de cinq mille chiffres dans l emploi du temps | l'adresse de l'emploi du temps avec « seance=h » suivi de 5 000 chiffres (lien abîmé). | erreur 500 au lieu d'ignorer la séance mise en évidence. |
| Niveau de regle de cinq mille chiffres au lancement | le formulaire « Lancer le calcul » avec un champ niveau_regle_<5 000 chiffres>. | erreur 500 au lancement au lieu d'un refus clair. |
| Note de besoin bornee cote serveur | la note d'un besoin envoyée avec 5 001 caractères ou un mégaoctet (le navigateur borne, pas un envoi fabriqué). | tout est enregistré, les pages et la base s'alourdissent. |
| Commentaire de regle borne cote serveur | le commentaire d'une règle envoyé avec 200 000 caractères (maxlength du navigateur contourné). | enregistré tel quel et recopié dans l'infobulle de chaque ligne de la liste des règles (des centaines de kilo-octets par page). |
| Motif d annulation borne cote serveur | le motif d'annulation d'une séance datée envoyé avec 200 000 caractères. | enregistré dans la note de la séance et réaffiché dans son panneau (page de 200 Ko). |

[`tests/integration/web_donnees/test_suppression.py`](../tests/integration/web_donnees/test_suppression.py)

| Test | Cas | Risque |
|---|---|---|
| Rejeu d une suppression et d une annulation | la même suppression envoyée deux fois (deux onglets, double clic, bouton « Précédent »), une annulation de séance rejouée, un ajout envoyé deux fois. | une erreur 500, une double écriture ou un message trompeur ; correct aujourd'hui, ce test fige le comportement. |

#### `web_edt` (30 cas)

[`tests/integration/web_edt/test_a_traiter.py`](../tests/integration/web_edt/test_a_traiter.py)

| Test | Cas | Risque |
|---|---|---|
| Option tous les semestres affiche tous les semestres | option « Tous les semestres » d'« À traiter » (`semestre_id=` vide), même adresse gardée en favori sur Générer, l'historique et la grille. | la chaîne vide refusée pour un entier remplace la page par « Saisie invalide » : impossible de revenir à tous les semestres. |

[`tests/integration/web_edt/test_accueil.py`](../tests/integration/web_edt/test_accueil.py)

| Test | Cas | Risque |
|---|---|---|
| Base qui refuse l ecriture signalee sur l accueil et sante | la base refuse l'écriture (fichier copié en root, disque plein) alors qu'elle se lit. | accueil muet et `/sante` vert : la supervision est rassurée et chaque saisie de la journée échoue ensuite. |
| Base occupee par une ecriture n est pas une panne | un autre processus tient le verrou d'écriture (calcul, import, commande) pendant que la supervision interroge `/sante` et qu'on ouvre l'accueil. | `/sante` rouge à chaque écriture longue, ou une sonde bloquée le temps du délai d'attente sur verrou (dix secondes) ; l'écriture d'essai doit être sautée, sans attente. |

[`tests/integration/web_edt/test_calculs.py`](../tests/integration/web_edt/test_calculs.py)

| Test | Cas | Risque |
|---|---|---|
| Suivi sans rechargement second lancement refuse puis arret | *docstring « Cas : … Risque : … » absente ou incomplète* | *docstring « Cas : … Risque : … » absente ou incomplète* |
| Arret d un calcul termine dit qu il est fini | « Arrêter » cliqué dans un second onglet resté sur le suivi d'un calcul déjà terminé. | « Arrêt demandé » affiché : on croit avoir interrompu un calcul fini dont la version est déjà active. |

[`tests/integration/web_edt/test_cas_limites.py`](../tests/integration/web_edt/test_cas_limites.py)

| Test | Cas | Risque |
|---|---|---|
| Identifiants enormes ou negatifs donnent introuvable | lien mal copié ou périmé dont l'identifiant a vingt chiffres, est négatif ou n'existe pas, sur chaque adresse des onglets Générer, versions, Emploi du temps, À traiter. | OverflowError de SQLite non rattrapée, donc erreur 500 et ligne ERROR au journal, au lieu d'un « introuvable » (404). |
| Cible enorme dans l adresse montre la premiere classe | groupe, enseignant ou salle de vingt chiffres dans l'adresse de la grille. | erreur 500 au lieu de la première classe, affichée en disant que la cible demandée n'existe pas. |
| Seance enorme dans l adresse ouvre la page sans panneau | séance à ouvrir (`seance=h…` ou `o…`) de vingt ou 5 000 chiffres, nulle, en chiffres exotiques (« ² ») ou de préfixe inconnu dans l'adresse de la grille (lien abîmé, favori ancien). | erreur 500 (`int()` refuse « ² » et plus de 4 300 chiffres) au lieu de la page sans panneau, avec un message qui ne recopie pas les 5 000 chiffres. |
| Cookie de cible illisible ou enorme est ignore | cookie de dernière cible forgé ou laissé par une autre instance du même hôte (identifiant énorme ou négatif, valeur illisible, nature inconnue). | l'onglet Emploi du temps répond 500 à chaque ouverture tant que le cookie n'est pas effacé. |
| Fragments ouverts sans htmx menent a la page | fragment HTMX (suivi d'un calcul, vérifications préalables, formulaire ou panneau d'une séance, compteur d'« À traiter ») ouvert sans en-tête HX-Request : adresse saisie, nouvel onglet, lien. | un morceau de page nu, sans menu ni style, au lieu de la page complète. |
| Panneau d une seance supprimee entre temps | panneau, enregistrement ou lien d'une séance supprimée entre-temps (autre onglet, version régénérée). | erreur 500, ou page entière dans le panneau, au lieu d'un « n'existe pas (ou plus) ». |
| Deplacement vers un creneau ou un jour inexistant | séance déplacée vers un début hors grille, un jour hors semaine, un jour ou une salle illisibles (« ² », 5 000 chiffres : formulaire forgé ou périmé). | séance enregistrée à une place impossible, ou erreur 500, au lieu d'un refus lisible qui la laisse en place. |
| Seance annulee ni reannulee ni deplacee | séance datée annulée deux fois (deux onglets ouverts), puis déplacée. | motif répété dans le commentaire, ou séance annulée qui revient en étant déplacée. |
| Deplacer une seance datee hors du semestre | séance datée déplacée au 4 janvier 1999 ou au 4 janvier 2100 (date saisie à la main). | séance placée hors du semestre, donc absente de la grille et des vérifications. |
| Date tres ancienne ou lointaine sans erreur | grille datée demandée au 1er janvier de l'an 1 ou au 31 décembre 9999 (adresse modifiée à la main). | la semaine autour sort du calendrier de Python (OverflowError, erreur 500) au lieu de la semaine en cours du semestre, affichée en le disant. |
| Filtres inconnus refuses lisiblement | paramètre d'affichage inconnu ou contradictoire dans l'adresse de la grille (couche, affichage, étendu, date, jour, deux cibles, nature de cible). | erreur 500, ou affichage faux sans le dire, au lieu d'une page 422 qui nomme le paramètre. |
| Hx current url illisible dans les panneaux | panneau d'une séance ou d'une séance datée demandé avec un en-tête HX-Current-URL que `urlsplit` refuse (« http://[::1 » : navigateur ou extension qui l'abîme). | erreur 500 au clic sur chaque séance de la grille ; le panneau doit s'ouvrir avec son adresse de retour par défaut, l'en-tête ignoré et journalisé. |
| Relance pendant un calcul mene au calcul en cours | « Relancer » (plus long, autre solution, simple) depuis le suivi d'un ancien calcul alors qu'un calcul du semestre tourne déjà. | deux calculs en parallèle sur le même semestre, ou une erreur, au lieu d'être mené au calcul en cours. |
| Arret quand un autre ecrivain valide entre lecture et ecriture | « Arrêter le calcul » pendant qu'un autre écrivain (le fil du calcul qui enregistre son état final, réveillé par l'arrêt) valide entre la lecture du calcul et l'écriture de la demande d'arrêt. | `demander_arret` lit dans une transaction différée puis écrit sur un instantané périmé : SQLite répond aussitôt « database is locked » sans attendre, la page renvoie 503 et la demande d'arrêt est perdue (vu en CI sous Linux, où le fil réveillé valide plus vite). |
| Saisies de cinq mille chiffres au lancement | formulaire « Lancer le calcul » fabriqué : règle personnalisée `niveau_regle_<5 000 chiffres>` ou `niveau_regle_²`, durée de pause de 5 000 chiffres ; même règle dans l'adresse des vérifications préalables. | `int()` lève ValueError, erreur 500 au lancement au lieu d'un refus clair ; ou un calcul lancé sur une saisie illisible. |
| Motif d annulation borne | motif d'annulation d'une séance datée envoyé avec un caractère de plus que la limite (le navigateur borne le champ, pas un envoi fabriqué), puis exactement à la limite. | 200 Ko enregistrés dans la remarque de la séance et réaffichés dans son panneau ; ou une séance annulée malgré le refus. |
| Head sur les ecrans de l emploi du temps | HEAD (sonde de supervision, `curl -I`, vérificateur de liens) sur les pages Générer, versions, grille, suivi d'un calcul et « À traiter », et sur une action POST. | 405 « Action non permise » sur toute page (FastAPI ne déclare que GET) ; ou une réponse HEAD qui porte la page entière. |
| Une ecriture attend son tour pendant un autre ecrivain | une séance datée est annulée pendant qu'un import, dans un autre onglet, tient le verrou d'écriture puis valide ses changements environ 0,3 s plus tard. | la requête lit d'abord puis écrit ; SQLite la refuse aussitôt (« database is locked », page « base occupée ») au lieu de la faire attendre la fin de l'import. |
| Une lecture est servie pendant un autre ecrivain | la grille, la page Générer et « À traiter » sont ouvertes (GET, HEAD) pendant qu'un import tient le verrou d'écriture. | une lecture qui prendrait le verrou d'emblée attendrait la fin de l'import (dix secondes au plus) puis répondrait « base occupée » : chaque onglet gelé pendant l'import. |
| Verrou tenu au dela du delai donne la page base occupee | l'autre écrivain garde le verrou au-delà du délai d'attente de la base (import très long, commande bloquée sur le serveur). | trace ou erreur 500 « inattendue » au lieu de la page « base occupée » (503) qui dit de réessayer ; ou une séance annulée à moitié. |
| Version sans seance s affiche et s exporte | version sans aucune séance (import resté vide, référence non remplie) affichée en semaine type, datée et par jour, listée dans l'historique, exportée en CSV et en iCal. | erreur 500 (minimum ou maximum d'une liste vide), blocs inventés, ou fichier d'export illisible par le tableur ou l'agenda. |
| Version active vide sur a traiter et l accueil | la version active du semestre n'a aucune séance (import resté vide). | erreur 500 sur « À traiter » ou l'accueil, ou version vide présentée comme un emploi du temps sans défaut. |
| Semestre sans grille reste utilisable | la grille de créneaux est vidée après la génération (grille en cours de refonte). | erreur 500 sur Générer ou sur la grille, ou séance proposée à une place qui n'existe plus. |
| Export d une version enorme dit introuvable | export CSV ou iCal d'une version de vingt chiffres (lien mal copié). | OverflowError de SQLite non rattrapée : erreur 500 au lieu d'un « introuvable » (404). Corrigé par le lot échange ; `client_sans_trace` montre un retour du 500 comme un statut. |

[`tests/integration/web_edt/test_emploi_du_temps.py`](../tests/integration/web_edt/test_emploi_du_temps.py)

| Test | Cas | Risque |
|---|---|---|
| Formulaire seance sans debut possible reste lisible | dans le panneau d'une séance, choix d'un jour où elle ne tient sur aucun créneau avant la fermeture, ou d'un jour non proposé (le dimanche). | liste de débuts vide lue au rang 0 (IndexError), donc fragment 500 à la place du formulaire. |

<!-- catalogue : fin de la partie générée -->

## Vérifications manuelles

Certains cas ne se reproduisent pas dans un test automatique, parce qu'ils
dépendent d'un vrai navigateur, d'un vrai tableur ou du serveur de
production. Ils se vérifient à la main avant une démonstration ou après un
changement de la partie concernée.

**Rendu de l'emploi du temps.** Ouvrir la page « Emploi du temps » du jeu
`moyen` sur un téléphone (ou avec l'émulation mobile du navigateur), puis en
aperçu avant impression, sur la semaine la plus chargée. Attendu : la grille
reste lisible, aucune séance n'est tronquée ni masquée.

**Coupure réseau pendant le suivi d'un calcul.** Lancer un calcul, couper le
réseau du navigateur (outils de développement, mode hors ligne) pendant
quelques secondes, puis le rétablir. Attendu : le calcul continue sur le
serveur, n'est pas relancé, et son état se lit de nouveau sur la page
« Générer » (le suivi interroge le serveur chaque seconde).

**Classeurs produits par un vrai tableur.** Importer : un classeur protégé
par un mot de passe dans Excel ; un classeur aux cellules fusionnées et aux
formats de date réels ; le même fichier depuis deux onglets au même moment.
Attendu : un refus qui nomme la cause pour le classeur protégé et les
cellules fusionnées, des dates refusées avec explication dans les colonnes de
codes, et deux imports traités l'un après l'autre sans erreur « base
occupée ». Les tests automatiques couvrent ces cas sur des classeurs
fabriqués par `openpyxl`, pas sur des fichiers d'Excel.

**Journaux sur un vrai disque.** Rotation de `edt.log` par deux processus :
le test automatique est sauté sous Windows, qui interdit de renommer un
fichier ouvert ; le lancer sous Linux (`python -m pytest
tests/unitaires/socle/test_journal.py`). Dossier des journaux inutilisable :
sur un serveur d'essai, rendre le dossier `generations/` de `EDT_LOG_DIR`
non inscriptible (ou remplir son disque), puis lancer un calcul. Attendu : le
calcul a lieu et signale que son journal dédié est indisponible, en renvoyant
au journal général `edt.log` et à `EDT_LOG_DIR`.

**Rechargements répétés d'un jeu fictif.** Recharger dix fois de suite le
jeu `moyen` avec remise à zéro (`python -m edt jeu --taille moyen --reset
--oui`) en observant la mémoire du processus. Attendu : la mémoire se
stabilise après le premier chargement.

**Exploitation du serveur.** Les procédures qui touchent au serveur
(déploiement pendant une commande d'administration, échec de la sauvegarde,
fuseau horaire, envoi interrompu, arrêt brutal pendant une écriture,
épuisement de la mémoire, envoi trop volumineux, sauvegarde quotidienne et
restauration, fichiers appartenant à root) sont décrites dans
[docs/deploiement.md](deploiement.md), section « Vérifications
d'exploitation ». Certaines arrêtent le service : elles se jouent hors des
heures d'usage.
