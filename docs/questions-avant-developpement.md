# Questions avant le développement

Questionnaire à remplir avant de coder la première version. Chaque question propose un **défaut** : si la réponse est « OK », ce défaut sera appliqué. Répondre par le numéro suffit (ex. « 3.2 : non, plutôt… »). Les numéros sont stables : les trous correspondent à des doublons retirés.

**400 questions** : 🔴 88 bloquantes (impossible de bien coder sans réponse), 🟠 197 importantes (changent la conception), ⚪ 115 détails (réglables plus tard).

## Sommaire

1. Périmètre, utilisateurs et démonstration (14 questions, 5 bloquantes)
2. Données réelles du directeur et confidentialité (23 questions, 9 bloquantes)
3. Import, identifiants et jeu de données synthétique (22 questions, 4 bloquantes)
4. Calendrier et horizon de génération (10 questions, 2 bloquantes)
5. Grille horaire et créneaux (17 questions, 4 bloquantes)
6. Structure académique et groupes (6 questions, 3 bloquantes)
7. Séances, volumes et règles de répartition (19 questions, 2 bloquantes)
8. Enseignants (5 questions, 1 bloquantes)
9. Salles, campus et déplacements (17 questions, 4 bloquantes)
10. Accessibilité et cas particuliers (21 questions, 4 bloquantes)
11. Moteur de résolution (22 questions, 5 bloquantes)
12. Interface utilisateur (32 questions, 6 bloquantes)
13. Exports et impression (6 questions, 1 bloquantes)
14. Architecture technique et stockage (30 questions, 8 bloquantes)
15. Tests, qualité et intégration continue (20 questions, 0 bloquantes)
16. Organisation d'équipe, Git, documentation et notation (43 questions, 6 bloquantes)
17. Compléments issus de la critique (1 questions, 0 bloquantes)
18. Hébergement et multi-utilisateurs (plus tard) (23 questions, 3 bloquantes)
19. Réaménagement temporaire en cas d'imprévu (36 questions, 11 bloquantes)
20. Pilotage par requêtes en langage naturel (LLM) (33 questions, 10 bloquantes)

## 1. Périmètre, utilisateurs et démonstration

**1.1** 🔴 Quels niveaux l'application doit-elle couvrir (cycle ingénieur A1-A3, cycle intégré I1-I2, apprentissage, bachelors) et quelle promotion servira de cas de démonstration ? Importe-t-on toute l'école ou un périmètre réduit ?
- *Pourquoi* : Le nombre de promotions et de groupes fixe la taille du modèle CP-SAT, le périmètre de l'import et le temps de calcul de la démo ; un périmètre réduit fait disparaître les conflits sur les salles et les enseignants partagés.
- *Défaut proposé* : On modélise une entité générique « promotion » à nom libre, on importe tout ce qui est fourni, la génération est filtrable par promotion et la démo porte sur une seule promotion (ex. A1) sur une semaine type.

**1.2** 🔴 Le professeur a-t-il validé le sujet et la stack (Python 3.11, OR-Tools CP-SAT, SQLite, interface web locale et non desktop Tkinter/PyQt) ? A-t-il imposé ou refusé quelque chose (base serveur, langage, patron MVC strict) ?
- *Pourquoi* : Un changement de stack après le 05/10 invaliderait le squelette, la CI et les spécifications techniques.
- *Défaut proposé* : On considère la stack validée et on l'écrit telle quelle dans les spécifications techniques ; SQLite reste derrière une couche d'accès isolée pour pouvoir migrer si une base serveur est exigée.

**1.3** 🔴 Validez-vous ce classement des fonctionnalités ? Obligatoire : saisie des référentiels, import CSV, contraintes de la section 2 de la spec, génération, vues groupe/enseignant/salle, message d'infaisabilité, modification par formulaire, export CSV, jeu de démo. Utile : contraintes de la section 3, vérification d'un EDT importé, glisser-déposer, verrouillage, historique, PDF, iCal, tableau de bord, panneau de conflits. Optionnelle : authentification, mobile, thème sombre, préférences souples, multi-projets, occupation des salles, diagnostic fin.
- *Pourquoi* : Ce classement fixe le périmètre de la V1, l'ordre de développement entre le 05/10 et le 16/11, ce qui est annoncé au professeur dans le livret et ce qui doit être testé et démontrable le 14/12.
- *Défaut proposé* : On adopte cette répartition telle quelle ; les « obligatoires » sont développés avant la Toussaint, les « utiles » entre le 02/11 et le 16/11.

**1.4** 🔴 Pour qui conçoit-on l'interface : un responsable scolarité de l'ISEP non informaticien, ou d'abord l'équipe et le professeur pour la démo ? La documentation utilisateur du 23/11 s'adresse-t-elle au même public ?
- *Pourquoi* : Le vocabulaire des écrans, le niveau de guidage et la tolérance aux erreurs de saisie ne sont pas les mêmes pour un non-informaticien que pour des développeurs.
- *Défaut proposé* : On conçoit pour un responsable scolarité non informaticien (vocabulaire métier, aucune notion de solveur visible), en sachant que l'équipe et le professeur la manipuleront réellement.

**1.5** 🔴 Si la démo ne porte que sur une promotion alors que salles et enseignants sont partagés avec toute l'école, comment représenter l'occupation des ressources par les promotions non importées : séances « occupation externe » figées (sans groupe géré, tirées de l'EDT réel des autres promotions), simples indisponibilités de salle et d'enseignant, ou rien du tout en acceptant une démo où toutes les salles sont libres ?
- *Pourquoi* : Sans occupation externe, le solveur trouve une solution triviale (toutes les salles et tous les enseignants libres) qui ne prouve rien devant le jury ; avec toute l'école importée, le modèle explose. L'occupation externe est aussi ce qui fait apparaître les vrais conflits de trajet pour un enseignant qui donne cours sur l'autre campus juste avant, et c'est la forme la plus courante de « séance déjà placée à respecter ».
- *Défaut proposé* : Type de séance « occupation externe » : figée, sans groupe géré, avec salle, enseignant et créneau, créée automatiquement à l'import pour tout ce qui est hors périmètre de génération ; elle consomme la salle et l'enseignant, entre dans la règle de trajet des enseignants, apparaît en grisé dans les vues et n'est jamais modifiable.

**1.6** 🟠 L'application est-elle strictement mono-utilisateur (un seul navigateur sur la machine locale), ou faut-il tolérer plusieurs onglets ou plusieurs personnes sur le réseau local en même temps ?
- *Pourquoi* : Le multi-utilisateur impose sessions, verrous d'écriture SQLite, gestion des conflits d'édition et générations concurrentes, ce qui alourdit fortement la couche web.
- *Défaut proposé* : Mono-utilisateur : le serveur écoute sur 127.0.0.1, une seule génération à la fois avec un message si un calcul tourne déjà.

**1.8** 🟠 Qui saisit les indisponibilités des enseignants : uniquement la scolarité depuis la fiche de chaque professeur, ou chaque enseignant en libre-service via une page dédiée ou un lien partageable ?
- *Pourquoi* : Le libre-service impose une page publique par professeur avec un accès distinct, ce qui change le modèle de navigation et de droits.
- *Défaut proposé* : La scolarité saisit tout depuis la fiche de chaque professeur ; pas de libre-service.

**1.9** 🟠 Faut-il placer les examens et partiels (semaines dédiées, surveillants, salles multiples, capacité réduite, tiers-temps), ou uniquement les cours, les périodes d'examens étant des plages bloquées ?
- *Pourquoi* : Les examens ont des règles propres (capacité divisée, surveillants au lieu d'enseignants, un examen par jour, tiers-temps en salle séparée) qui doublent le modèle.
- *Défaut proposé* : Hors V1 : le type « examen » existe dans l'énumération des séances mais n'est pas placé ; une période d'examens est une plage fermée pour les classes concernées.

**1.10** 🟠 Lors de la soutenance du 14/12, le professeur manipulera-t-il lui-même l'application (clics libres, saisies imprévues) ou s'agit-il d'une démo entièrement pilotée par l'équipe ?
- *Pourquoi* : Une manipulation libre impose des validations partout, des messages d'erreur soignés et aucun écran sans issue ; une démo pilotée tolère un parcours plus étroit.
- *Défaut proposé* : Démo pilotée par l'équipe, mais chaque écran doit survivre à cinq minutes de manipulation libre sans plantage ni erreur brute.

**1.11** 🟠 Quel scénario de démo : base pré-remplie chargeable en un clic, import en direct d'un petit fichier, génération, cas infaisable (retrait des salles accessibles), export ? Faut-il un bouton de remise à zéro si quelque chose déraille ?
- *Pourquoi* : Cela définit le contenu de data/demo/, les commandes de chargement et de réinitialisation, et le test d'intégration qui rejoue la démo.
- *Défaut proposé* : Commande `edt demo` et bouton « Charger le jeu de démonstration » (2 promotions, 15 enseignants, 12 salles sur 2 campus, un groupe PMR, un TP le samedi), un bouton qui bascule une salle en inaccessible, et `edt reset --demo` avec confirmation.

**1.12** 🟠 Sur quelle machine, quel système et quel navigateur tournera la soutenance (portable Windows 11 d'un membre projeté, poste de l'école, Chrome, Edge, Firefox, Safari) ?
- *Pourquoi* : Il faut une répétition sur la machine exacte de démo et des tests de compatibilité CSS/JS sur le navigateur retenu.
- *Défaut proposé* : Votre portable Windows 11 projeté, Chrome ou Edge récent ; Firefox vérifié une fois, Safari non garanti ; répétition à blanc la semaine du 07/12.

**1.13** 🟠 Prévoit-on un filet de sécurité pour la démo si la génération en direct échoue ou dépasse le temps (bug, machine lente, infaisabilité imprévue) : un placement pré-calculé chargeable en un clic (« Charger la dernière génération réussie »), et un drapeau « mode démo » qui masque les fonctionnalités instables (XLSX, glisser-déposer, écrans non terminés) ?
- *Pourquoi* : Le test d'intégration garantit que le scénario passe en CI, pas qu'il passe sur le portable projeté à 14 h avec le jury qui attend ; un résultat pré-calculé stocké avec le jeu de démo permet d'enchaîner sur les vues, l'export et le cas infaisable sans dépendre du solveur, et la vidéo de secours ne remplace pas une interface qui répond.
- *Défaut proposé* : Le jeu de démo inclut un placement pré-calculé versionné (data/demo/placement.json) chargé par `edt demo` ; le bouton Générer est utilisé en direct, mais un raccourci discret recharge le placement de secours ; variable EDT_MODE_DEMO=1 qui retire du menu les écrans classés utile non terminés.

**1.14** 🟠 Le jour de la soutenance, la démonstration tourne-t-elle sur le jeu synthétique (2 promotions, 15 enseignants) ou sur les données réelles pseudonymisées du directeur, ou les deux à la suite (synthétique pour le parcours guidé, réel pour la comparaison) ? Si le réel arrive tard ou incomplet, la partie réelle de la démo est-elle abandonnée sans regret ?
- *Pourquoi* : Les défauts actuels se contredisent (scénario de démo synthétique d'un côté, « soutenance sur données pseudonymisées » de l'autre) ; le choix fixe le contenu de data/demo/, la taille du modèle résolu en direct, le temps de calcul à garantir, et si le scénario rejoué en CI porte sur un jeu ou sur deux.
- *Défaut proposé* : Deux temps : parcours complet sur le jeu synthétique verrouillé par la CI, puis un seul écran « comparaison avec l'année réelle » sur les données pseudonymisées, préchargé, que l'on coupe de la démo sans conséquence si le réel n'est pas prêt.

**1.15** 🟠 Le professeur installera-t-il et lancera-t-il lui-même l'application depuis le README sur sa propre machine (avant ou après la soutenance) pour noter la documentation et les tests, ou ne verra-t-il l'application que projetée par l'équipe ?
- *Pourquoi* : S'il installe lui-même, la robustesse de l'installation à froid (Python absent, OR-Tools qui ne s'installe pas, port occupé, dossier avec accents) pèse plus que le glisser-déposer, et il faut un test d'installation sur machine vierge avant le 23/11.
- *Défaut proposé* : On suppose qu'il peut le faire : README avec installation en trois commandes testée sur une machine vierge (Windows et macOS) par un membre qui n'a pas écrit le script, et commande `edt doctor` qui vérifie Python, OR-Tools et le port.

## 2. Données réelles du directeur et confidentialité

**2.1** 🔴 Sous quel format le directeur fournira-t-il les données : export d'un logiciel (CSV, XLSX, ICS) en tableau plat, grille hebdomadaire mise en forme (cellules fusionnées), PDF, captures d'écran ? Pouvez-vous obtenir un extrait dès maintenant pour figer le format et confirmer la grille horaire ?
- *Pourquoi* : Un tableau plat s'importe en une journée, alors qu'une grille visuelle ou un PDF impose un parseur dédié ou une ressaisie manuelle, et le module d'import ne peut pas être écrit sans connaître les colonnes.
- *Défaut proposé* : On développe l'import CSV/XLSX en tableau plat en priorité et on écrit un convertisseur ad hoc dès réception ; si c'est une grille, on redemande un « export liste » au directeur.

**2.2** 🔴 Les données seront-elles l'emploi du temps réalisé (séances datées avec jour, heure, salle, enseignant, groupe) ou la demande (maquette pédagogique : matières, volumes, enseignants, salles, contraintes) ? Si c'est l'EDT réalisé, acceptez-vous qu'on reconstitue les volumes par agrégation avant de relancer le solveur ?
- *Pourquoi* : Un EDT réalisé est une solution, pas une entrée : il faut un script de désassemblage pour en extraire les volumes et les affectations, et il sert ensuite de référence de comparaison.
- *Défaut proposé* : On suppose un EDT placé de type Hyperplanning/ADE ; un script agrège par (matière, type, groupe, enseignant) en « N séances de 2 h par semaine ».

**2.3** 🔴 Quel ordre de grandeur attendre des données réelles : nombre de promotions, classes et groupes, d'enseignants, de salles par campus, de campus et de séances par semaine ?
- *Pourquoi* : Cela dimensionne la formulation CP-SAT (une formulation booléenne séance × créneau × salle peut dépasser le million de variables), le budget de temps du solveur, la pagination des listes et les tests de charge.
- *Défaut proposé* : On dimensionne pour 800 séances par semaine, 100 groupes, 200 enseignants, 100 salles et 3 campus, avec un test de charge à cette échelle ; au-delà, on découpe la génération par promotion.

**2.4** 🔴 Les noms des enseignants et les données réelles peuvent-ils apparaître dans le dépôt GitHub Maki882/projet-edt (est-il public ou privé ?), ou faut-il les pseudonymiser (« PROF_042 ») avant tout commit ?
- *Pourquoi* : Un dépôt public interdit tout commit de données personnelles, même pseudonymisées de façon réversible, et cela conditionne l'existence d'un script d'anonymisation et la séparation données brutes / données committables.
- *Défaut proposé* : On considère le dépôt public : jamais de données brutes committées, dossier data/prive/ ignoré par git, script de pseudonymisation irréversible produisant la fixture versionnée, table de correspondance conservée localement, jeu synthétique pour les tests.

**2.5** 🔴 Les fichiers contiendront-ils des données nominatives d'étudiants (listes de groupes, emplois du temps individuels) ?
- *Pourquoi* : On ne veut ni stocker ni committer d'étudiant identifiable ; le modèle doit rester au niveau du groupe.
- *Défaut proposé* : Aucune table étudiant ; si des listes arrivent, on ne conserve que l'effectif par groupe et on supprime le fichier source.

**2.6** 🔴 Le directeur transmettra-t-il des informations sur des étudiants en situation de handicap (donnée de santé), et à quel niveau : étudiant nommé, groupe, ou simplement « ce cours doit être en salle accessible » ?
- *Pourquoi* : Une donnée de santé impose des règles strictes de traitement ; le plus sûr est de ne pas la recevoir, et cela détermine si le modèle contient une entité étudiant ou un attribut de groupe.
- *Défaut proposé* : On demande explicitement de ne pas la fournir ; le besoin d'accessibilité est un drapeau posé par le planificateur sur un groupe, exercé sur un groupe fictif pour la démo.

**2.7** 🔴 Vers quelle date les données doivent-elles arriver, qui relance le directeur, et à quelle date bascule-t-on sur le jeu synthétique ?
- *Pourquoi* : Cela fixe la date de bascule pour ne pas bloquer le jalon tests unitaires du 12/10.
- *Défaut proposé* : Si rien n'est reçu au 12/10, on code sur du synthétique et on branche le réel plus tard sans changer l'architecture.

**2.8** 🔴 Si l'EDT réel viole les contraintes de la spec (cours de 1 h 30, début à 9 h, fin après 18 h, samedi après-midi, changement de campus sans battement), que fait-on : rejeter les lignes, arrondir, assouplir la spec, rendre chaque règle paramétrable ?
- *Pourquoi* : Cela décide si les règles sont codées en dur ou paramétrables et évite que la démo affiche « aucune solution » sur des données réelles ; l'import ne doit pas planter dessus.
- *Défaut proposé* : Import tel quel avec un drapeau « hors grille », rapport d'audit listant les violations, séances inclassables exclues de la génération et jamais arrondies silencieusement ; chaque règle a un paramètre ; l'équipe tranche contrainte par contrainte en réunion sans modifier la spec unilatéralement.

**2.9** 🔴 Les assistants IA des quatre membres (Claude Code, Codex) peuvent-ils lire les fichiers réels bruts, sachant qu'ils sont alors transmis aux serveurs de l'éditeur, ou seulement la version pseudonymisée ? Comment l'empêcher concrètement (fichiers hors du dossier du projet, règle dans AGENTS.md, pseudonymisation lancée à la main) ?
- *Pourquoi* : Chaque membre développe avec un assistant qui lit le dossier de travail ; un simple affichage de l'export du directeur envoie des noms d'enseignants à un tiers hors de tout accord, ce qui est difficile à justifier devant le directeur ou le professeur.
- *Défaut proposé* : Fichiers bruts stockés hors du dépôt (EDT_DATA_DIR pointant hors du dossier du projet), règle explicite dans AGENTS.md interdisant aux assistants de lire ce dossier, pseudonymisation lancée à la main par un humain, seuls les fichiers pseudonymisés entrent dans le dossier de travail.

**2.10** 🟠 Quel logiciel gère les emplois du temps à l'ISEP (Hyperplanning, ADE, Aurion, Celcat) et est-ce lui qui produira l'export ? Faut-il que nos vues et exports ressemblent à ce que les étudiants ont l'habitude de voir ?
- *Pourquoi* : Les colonnes, libellés et conventions dépendent du logiciel source (un adaptateur par source), et imiter un format connu guide la mise en page.
- *Défaut proposé* : On suppose un export Hyperplanning ; un adaptateur = un module Python isolé ; format d'affichage propre, inspiré de l'affichage habituel des étudiants.

**2.11** 🟠 Confirmez-vous le double usage de l'EDT réel : dériver les besoins à placer, puis servir de référence pour comparer la sortie du solveur et valider notre vérificateur ?
- *Pourquoi* : Cela implique deux tables distinctes (besoins et seance_reference), un module de comparaison et deux tests d'intégration sur données réelles.
- *Défaut proposé* : Oui : table seance_reference en lecture seule alimentée uniquement par l'import, vérificateur commun appliqué aux deux EDT.

**2.12** 🟠 Quel périmètre couvriront les données : une promotion ou toute l'école, une semaine ou un semestre, un ou deux campus ? Contiendront-elles les salles avec capacité, les effectifs et les disponibilités des enseignants, ou seulement les séances ?
- *Pourquoi* : Ce qui manque devra être saisi à la main ou inventé, et la démo doit être planifiée en conséquence.
- *Défaut proposé* : Minimum demandé : liste des salles avec capacité et campus, séances d'un semestre pour une ou deux promotions ; le reste est saisi dans l'interface.

**2.13** 🟠 Quelle année scolaire et quels semestres seront couverts (2024-2025 ou 2025-2026, S1 seul ou année complète) ?
- *Pourquoi* : Le calendrier (vacances, fériés) et la nomenclature des groupes changent d'une année à l'autre.
- *Défaut proposé* : 2025-2026 complète, chaque semestre traité séparément.

**2.14** 🟠 Peut-on demander au directeur un export brut avec toutes les colonnes (identifiants internes, codes modules, types de séance) plutôt qu'un extrait nettoyé, plus un export séparé du référentiel des salles ?
- *Pourquoi* : Les colonnes techniques valent plus qu'un fichier lisible mais appauvri, et le référentiel salles est indispensable aux contraintes de capacité et de type.
- *Défaut proposé* : On envoie au directeur une liste de colonnes souhaitées et un gabarit salles.csv, et on accepte ce qui arrive.

**2.15** 🟠 Recevra-t-on un seul fichier pour toute l'école ou un fichier par promotion, par semaine ou par campus ?
- *Pourquoi* : Le multi-fichiers impose une fusion et un dédoublonnage, car une séance commune à deux promotions apparaît dans deux fichiers.
- *Défaut proposé* : Import multi-fichiers avec dédoublonnage sur (date, heure, salle, enseignant).

**2.16** 🟠 Y a-t-il un accord explicite (mail) du directeur sur l'usage des données : tests, démo en soutenance devant le professeur, partage entre les quatre membres ?
- *Pourquoi* : Cela définit ce qu'on peut projeter le 14/12 et où l'on peut stocker les fichiers.
- *Défaut proposé* : Usage interne à l'équipe, soutenance sur données pseudonymisées, fichiers sur un espace partagé privé.

**2.17** 🟠 Où stocker les fichiers réels bruts (espace partagé d'équipe, dépôt privé séparé, machine d'Axel uniquement) et qui y a accès ?
- *Pourquoi* : Cela définit le chemin de configuration (EDT_DATA_DIR) et ce que les tests d'intégration peuvent supposer.
- *Défaut proposé* : Dossier local data/prive/ ignoré par git, copie sur un espace partagé privé, tests d'intégration sur synthétique uniquement.

**2.18** 🟠 Peut-on obtenir, en plus des fichiers, 30 minutes avec la personne qui fait réellement les emplois du temps à l'ISEP (responsable scolarité) pour recueillir ses règles tacites, son ordre de priorité entre contraintes et ce qu'elle attendrait d'un tel outil ? Et que doit-on au directeur en retour (démonstration, rapport des conflits détectés dans son EDT, accès à l'outil) ?
- *Pourquoi* : La spec écrite par l'équipe n'est pas celle de la scolarité ; un entretien évite de coder des contraintes que personne n'applique et d'ignorer celles qui comptent, et le retour d'un utilisateur réel est un argument de soutenance incontestable. Un livrable promis au directeur devient une tâche datée du planning.
- *Défaut proposé* : On demande l'entretien (compte rendu dans docs/reunions/), on promet au directeur une démonstration et le rapport d'audit de son EDT, rien d'autre, et cette tâche est ajoutée au planning avant le 16/11.

**2.19** 🟠 L'export de l'année passée contiendra-t-il des séances annulées, déplacées ou remplacées (colonne statut, doublons prévu/réalisé, historique des modifications), et s'agit-il de l'emploi du temps prévu en début de semestre ou de celui réellement réalisé après toutes les modifications ?
- *Pourquoi* : Une séance annulée non filtrée gonfle les volumes reconstitués et crée de faux conflits ; deux versions d'une même séance faussent la référence de comparaison ; et prévu ou réalisé ne racontent pas la même chose sur ce que la scolarité arrive à faire.
- *Défaut proposé* : On demande la version réalisée ; le lecteur reconnaît une colonne statut ou des libellés « annulé / reporté » (expression régulière paramétrable), exclut ces lignes des besoins et les compte dans le rapport d'import ; sans colonne, tout est supposé réalisé.

**2.20** 🟠 Le professeur (et, via le directeur, l'établissement) sont-ils informés que le projet utilise des données réelles de l'ISEP ? Faut-il le mentionner explicitement dans les spécifications techniques et à la soutenance, ou vaut-il mieux ne montrer que le synthétique et garder le réel pour les tests ?
- *Pourquoi* : Un enseignant peut reprocher l'usage de données nominatives dans un projet étudiant s'il l'apprend en soutenance ; annoncé dès le 05/10 avec les mesures prises (pseudonymisation, dépôt sans données, suppression), c'est au contraire un point fort sur la rigueur.
- *Défaut proposé* : On l'annonce au professeur à la séance du 05/10 et dans une section « Données » des spécifications techniques, avec les mesures de protection ; s'il s'y oppose, le réel reste hors dépôt et hors démo et ne sert qu'aux tests locaux.

**2.21** 🟠 Jusqu'à quand conserve-t-on les fichiers réels bruts et la table de correspondance de pseudonymisation, qui s'assure de leur suppression sur les quatre machines, l'espace partagé et les sauvegardes, et le directeur attend-il une confirmation écrite ?
- *Pourquoi* : Sans date ni responsable, les fichiers restent indéfiniment sur des portables personnels ; une date de suppression convenue avec le directeur clôt proprement l'usage des données et se place dans le planning.
- *Défaut proposé* : Suppression des fichiers bruts et de la table de correspondance dans la semaine suivant la soutenance (au plus tard le 21/12), chaque membre confirme dans le canal d'équipe, le référent qualité envoie un mail de confirmation au directeur ; seule la fixture pseudonymisée irréversible reste dans le dépôt.

**2.22** ⚪ Quelles métriques de comparaison entre EDT généré et EDT réel intéressent la démo (conflits détectés dans le réel, pourcentage de séances placées, occupation des salles, trous, changements de campus par jour) ?
- *Pourquoi* : Chaque métrique est du code et des tests supplémentaires, mais c'est un argument fort en soutenance.
- *Défaut proposé* : Quatre métriques : conflits dans le réel, pourcentage de séances placées, taux d'occupation des salles, nombre de changements de campus par jour.

**2.25** ⚪ Quel style de pseudonymisation : codes techniques (« PROF_042 ») ou noms fictifs réalistes générés avec une graine fixe (Faker en français), plus lisibles à l'écran en soutenance ? Accepte-t-on qu'un enseignant reste ré-identifiable par recoupement (seule personne à enseigner telle matière sur tel campus) ?
- *Pourquoi* : Des codes rendent les captures et la démo illisibles ; des faux noms réalistes sont plus crédibles mais peuvent être pris pour de vrais ; et pseudonymiser le nom ne suffit pas si la matière et le campus identifient la personne.
- *Défaut proposé* : Noms fictifs réalistes générés avec Faker et graine fixe, bandeau « données pseudonymisées » dans l'interface en mode démo ; la ré-identification par recoupement est acceptée pour un usage interne et en soutenance, mais les matières rares sont renommées si le dépôt est public.

## 3. Import, identifiants et jeu de données synthétique

**3.1** 🔴 Les enseignants ont-ils un identifiant stable (matricule, email) ou seulement « NOM Prénom », avec des variantes (« DUPONT J. », « Dupont Jean », accents) et des homonymes possibles ?
- *Pourquoi* : Un enseignant éclaté en deux entités fait disparaître des conflits réels ; deux homonymes fusionnés créent de faux conflits.
- *Défaut proposé* : Clé = nom + prénom normalisés (sans accents, majuscules), identifiant interne créé à l'import, rapport des quasi-doublons par distance de Levenshtein à valider à la main.

**3.2** 🔴 Quelle est la nomenclature des salles (« N204 », « I-105 », « Amphi Issy ») et le campus est-il déductible du nom ?
- *Pourquoi* : La contrainte de changement de campus est impossible sans un campus fiable par salle.
- *Défaut proposé* : Table de correspondance salle → campus générée à l'import par préfixe puis corrigée manuellement dans l'interface.

**3.3** 🔴 L'import se fait-il depuis l'interface (téléversement ou glisser un fichier, prévisualisation, rapport d'erreurs ligne par ligne), en ligne de commande, ou les deux ? Quels formats : CSV, Excel (.xlsx), ICS ?
- *Pourquoi* : L'upload impose une gestion de fichier côté serveur, une page dédiée et la dépendance openpyxl ; la CLI sert à la CI et aux scripts.
- *Défaut proposé* : Les deux, la commande CLI appelant la même fonction que l'interface ; CSV obligatoire, XLSX via openpyxl classé utile ; prévisualisation des 20 premières lignes et rapport (numéro, colonne, message) ; rien n'est écrit tant qu'il reste une erreur.

**3.4** 🔴 Si les données réelles n'arrivent pas, quelle taille et quel réalisme pour le jeu synthétique (promotions ISEP, matières plausibles, deux campus), qui le fabrique ?
- *Pourquoi* : Le générateur synthétique est un module à part entière avec ses propres tests et il sert de base aux tests d'intégration et à la démo.
- *Défaut proposé* : Générateur paramétrable (promotions, groupes, enseignants, salles, campus) et trois jeux à seed fixe versionnés dans tests/fixtures/ : petit (2 groupes, 10 séances) pour les tests unitaires, moyen (1 promotion) pour la démo, grand (école entière) pour la performance.

**3.5** 🟠 Quel format officiel d'import l'application doit-elle supporter (classeur XLSX multi-onglets, un CSV par entité, JSON) et fournit-on un gabarit téléchargeable ? Faut-il un assistant de correspondance des colonnes ou un gabarit fixe à adapter à la main ?
- *Pourquoi* : Cela définit les schémas de validation, une partie de la documentation utilisateur du 23/11, et l'assistant de correspondance est un écran supplémentaire avec état intermédiaire.
- *Défaut proposé* : Gabarit fixe téléchargeable depuis l'écran d'import (un CSV par entité, XLSX multi-onglets accepté), JSON comme format pivot interne, message listant les colonnes manquantes ; l'assistant de correspondance est classé optionnel.

**3.6** 🟠 Lorsqu'une ligne est invalide (salle inconnue, heure mal formée), l'import est-il rejeté entièrement ou accepté partiellement avec rapport ?
- *Pourquoi* : Le « tout ou rien » exige une validation complète avant toute écriture en base.
- *Défaut proposé* : Validation complète, rejet total, rapport ligne par ligne téléchargeable.

**3.7** 🟠 En cas de réimport d'un fichier déjà importé, que fait-on : refuser les doublons, mettre à jour par identifiant métier, tout remplacer après confirmation, ou créer un nouveau scénario ?
- *Pourquoi* : La stratégie de fusion conditionne les messages, la présence d'un identifiant métier dans les formulaires, et éventuellement une colonne scenario_id sur toutes les tables.
- *Défaut proposé* : Mise à jour par identifiant métier (code salle, nom+prénom enseignant, code groupe) avec récapitulatif « X créés, Y mis à jour, Z ignorés » avant validation ; bouton séparé « Tout remplacer » avec confirmation ; les scénarios nommés sont classés utile.

**3.8** 🟠 Quelles conventions attendre du fichier : séparateur « ; » ou « , », BOM Excel, dates « JJ/MM/AAAA », heures « 8h30 » ou « 08:30 », accents en cp1252 ou UTF-8 ?
- *Pourquoi* : Un CSV en cp1252 avec accents et séparateur « ; » est la cause la plus fréquente d'un import qui échoue en démo et altère le rapprochement des noms.
- *Défaut proposé* : Détection automatique du séparateur, du BOM et de l'encodage (UTF-8 puis repli cp1252 avec avertissement), parsing tolérant des dates et heures, un test unitaire par variante rencontrée.

**3.9** 🟠 Les matières ont-elles un code module (ex. « II.2409 ») ou seulement un libellé, et un même enseignement peut-il apparaître sous plusieurs libellés (« Maths 1 » / « Mathématiques I ») ?
- *Pourquoi* : Le comptage des séances par matière et l'affichage exigent une clé fiable ; sans code, il faut une table de correspondance.
- *Défaut proposé* : Clé = code si présent, sinon libellé normalisé ; alias gérés par une table matiere_alias éditable.

**3.10** 🟠 Si l'export contient des doublons stricts (même date, heure, salle, enseignant, groupe) ou des chevauchements réels, faut-il fusionner, ignorer ou signaler ?
- *Pourquoi* : Cela définit ce que le rapport d'import doit afficher et si l'import est « tout ou rien ».
- *Défaut proposé* : Doublons stricts fusionnés silencieusement ; chevauchements conservés mais listés dans le rapport d'audit.

**3.11** 🟠 Chaque occurrence hebdomadaire est-elle listée, ou l'export contient-il des règles de récurrence (iCal RRULE, « toutes les semaines du 15/09 au 20/12 ») ?
- *Pourquoi* : Il faut développer les récurrences en occurrences datées en tenant compte des vacances.
- *Défaut proposé* : Développement des récurrences avec dateutil.rrule, exclusion des semaines de vacances du calendrier.

**3.12** 🟠 Une séance à plusieurs enseignants, plusieurs salles ou plusieurs groupes apparaît-elle sur une seule ligne (valeurs séparées par virgule) ou sur plusieurs lignes ?
- *Pourquoi* : Cela détermine les tables de jointure séance-enseignant, séance-salle, séance-groupe et la logique de dédoublonnage.
- *Défaut proposé* : Modèle N-N pour les trois relations, parseur qui éclate les listes séparées par virgule ou point-virgule.

**3.13** 🟠 Comment traiter une séance réelle sans enseignant nommé (« à recruter », « intervenant extérieur »), sans salle (« à définir ») ou avec un groupe inconnu ?
- *Pourquoi* : Le schéma actuel impose NOT NULL sur enseignant, salle et classe, et une séance sans enseignant ne doit ni créer de faux conflit ni faire échouer l'import.
- *Défaut proposé* : Colonnes rendues nullable ; enseignant inconnu = enseignant fictif propre à la matière sans contrainte de disponibilité ; séance importée avec avertissement et exclue de la référence de comparaison.

**3.14** 🟠 Les séances de l'export indiquent-elles leur type (CM, TD, TP, examen, projet, soutenance, autonomie) ?
- *Pourquoi* : La règle « TP possible le samedi » et le choix du type de salle en dépendent.
- *Défaut proposé* : Si absent, on infère par expression régulière sur le libellé (« TD », « TP », « Exam »), sinon « CM ».

**3.15** 🟠 En attendant les données, chaque membre peut-il exporter son propre emploi du temps (.ics depuis le logiciel de l'école ou son agenda) pour servir d'échantillon réel ?
- *Pourquoi* : Cela donne dès maintenant un vrai format à parser et des cas limites réels sans attendre le directeur.
- *Défaut proposé* : Oui, on écrit d'abord un lecteur .ics qui devient le premier adaptateur d'import.

**3.16** 🟠 L'import d'un emploi du temps déjà placé doit-il pouvoir créer, en plus de la table de référence en lecture seule, des placements verrouillés que le solveur doit compléter (cas réel : la scolarité a déjà placé les vacataires et les amphis avant de lancer le reste) ? Faut-il un choix à l'import « importer comme référence / comme verrous / comme séances à replacer » ?
- *Pourquoi* : Compléter un EDT partiel est le cas d'usage réel d'un générateur en scolarité (on ne part jamais de zéro) et un scénario de démo fort ; il impose que l'import sache produire trois objets différents (référence, séance à placer, placement verrouillé) à partir d'une même ligne, ce que le schéma doit prévoir dès le départ.
- *Défaut proposé* : Trois modes dans l'écran d'import, « référence » par défaut ; en mode « verrous », chaque ligne crée une séance à placer plus un placement verrouillé ; en mode « à replacer », seule la séance ; un même fichier peut être réimporté dans un autre mode.

**3.17** 🟠 Les heures des exports (ICS, CSV issus d'Hyperplanning ou d'un agenda) sont-elles en heure locale ou en UTC avec fuseau, et que fait l'import des séances situées après le passage à l'heure d'hiver (fin octobre), qui apparaissent décalées d'une heure en UTC ? Même question pour l'export iCal produit par l'application.
- *Pourquoi* : Un semestre réel enjambe le changement d'heure : un parseur naïf place les cours de novembre à 7 h 30 ou 9 h 30, ce qui fait tomber la moitié des séances « hors grille » sans que personne comprenne pourquoi, et un iCal exporté sans fuseau se décale dans Google Agenda.
- *Défaut proposé* : Toutes les heures sont converties en heure locale Europe/Paris à l'import (zoneinfo), stockées sans fuseau, et l'export iCal déclare explicitement TZID=Europe/Paris ; un test unitaire couvre une séance avant et une après le 25/10.

**3.18** 🟠 Quelles sont les clés d'unicité réelles : une matière est-elle rattachée à une promotion (« Mathématiques » de A1 et de A2 sont deux matières distinctes) ou globale ; deux salles peuvent-elles porter le même nom sur deux campus (« 101 » à Issy et à NDC) ; un même nom de groupe peut-il désigner deux groupes différents d'une année à l'autre ?
- *Pourquoi* : Une clé trop large fusionne des entités distinctes (deux salles homonymes deviennent une seule salle, donc de faux conflits ; le CM de maths A1 et A2 une seule matière) ; une clé trop fine éclate une même entité ; c'est la source la plus fréquente d'un rapprochement d'import faux et cela fixe les contraintes UNIQUE du schéma.
- *Défaut proposé* : Matière = (code ou libellé normalisé, promotion) ; salle = (nom, campus) ; groupe = (nom, année scolaire) ; les contraintes UNIQUE correspondantes sont posées en base et le rapport d'import signale les homonymes.

**3.19** ⚪ Les événements spéciaux du réel (examens, conférences, forums, soutenances, journées d'intégration) doivent-ils être importés comme séances figées, ignorés, ou marqués « autre » ?
- *Pourquoi* : Ils occupent salles et amphis mais ne sont pas des cours à replacer par le solveur.
- *Défaut proposé* : Importés avec type « autre » et drapeau figé, exclus des besoins.

**3.20** ⚪ Les noms utilisés dans insert_data.py (classe « SI2I-A », salles « B204 », « C105 ») suivent-ils la nomenclature réelle de l'ISEP ? Faut-il l'adopter dans les fixtures et l'interface ?
- *Pourquoi* : Des fixtures réalistes évitent de refaire les tests et les captures d'écran quand les données réelles arrivent.
- *Défaut proposé* : Libellés libres importés tels quels avec un code interne stable ; on adopte la nomenclature réelle dans les fixtures dès qu'elle est connue.

**3.21** ⚪ Conserve-t-on pour chaque séance importée sa provenance (fichier, numéro de ligne, texte brut de la ligne) et les colonnes non reconnues dans un champ JSON `extra`, afin de remonter à la source quand le modèle ne représente pas quelque chose que contient le fichier réel ?
- *Pourquoi* : Quand l'emploi du temps généré diffère du réel ou qu'une séance semble aberrante, la première question est « d'où vient cette ligne ? » ; sans provenance, le débogage de l'import sur 800 lignes consomme une séance entière, et les colonnes ignorées sont précisément celles qui révèlent qu'une notion manque au modèle.
- *Défaut proposé* : Colonnes `source_fichier`, `source_ligne` et `extra` (JSON) sur seance_reference et seance ; affichées dans le panneau latéral en vue planificateur ; jamais exportées.

**3.22** ⚪ Dans l'export réel, une séance longue (TP de 4 h) apparaît-elle comme une seule ligne ou comme deux lignes consécutives de 2 h avec même matière, groupe, enseignant et salle ? Faut-il fusionner automatiquement les lignes contiguës identiques, et la fusion peut-elle enjamber la pause déjeuner ?
- *Pourquoi* : Sans fusion, on reconstitue « deux TP de 2 h » au lieu d'un bloc de 4 h et on perd la contrainte de consécutivité ; avec une fusion trop large, on fabrique des blocs qui enjambent le déjeuner ou fusionnent deux TD distincts.
- *Défaut proposé* : Fusion automatique des lignes strictement identiques et contiguës (écart ≤ 15 min) dans la même demi-journée, jamais à travers le déjeuner, signalée dans le rapport d'import et désactivable par option.

## 4. Calendrier et horizon de génération

**4.1** 🔴 Faut-il générer une semaine type répétée à l'identique tout le semestre, ou un calendrier réel avec dates, vacances, jours fériés, semaines banalisées et matières qui ne durent que quelques semaines ? Quel horizon pour la démo ? Si semaine type, acceptez-vous qu'un imprévu daté (« mardi 12 novembre ») ne puisse pas être représenté ?
- *Pourquoi* : Le passage au calendrier daté multiplie les variables par le nombre de semaines (22 créneaux contre plus de 300), impose un calendrier de semaines actives par promotion et change le modèle de données (date au lieu de jour de semaine).
- *Défaut proposé* : Semaine type générée par le solveur, puis instanciée sur un calendrier réel (table des semaines datées, vacances et jours fériés) ; les dérogations s'appliquent aux instances datées, jamais à la semaine type.

**4.2** 🔴 Le nombre de séances à placer est-il exprimé par semaine (2 TD de maths chaque semaine) ou par semestre (24 h à répartir sur 12 semaines) ? Les semaines réelles sont-elles quasi identiques, ou très différentes (matière qui démarre en semaine 5, TP une semaine sur deux) ?
- *Pourquoi* : Une charge semestrielle impose un horizon multi-semaines et une contrainte de répartition, incompatible avec une semaine type.
- *Défaut proposé* : Volume exprimé en nombre de séances par semaine ; si les semaines atypiques sont majoritaires, la V2 modélise une matière comme un total de séances à placer entre une semaine de début et de fin avec un maximum par semaine.

**4.3** 🟠 Comment extraire la semaine type des données datées : la plus chargée, la plus fréquente, une semaine médiane, ou un choix manuel ?
- *Pourquoi* : Cela définit le script d'import et le jeu de données du test d'intégration.
- *Défaut proposé* : Import de toutes les semaines datées ; choix manuel de la semaine dans l'écran d'import, la plus fréquente par promotion proposée par défaut.

**4.4** 🟠 Comment l'année est-elle découpée à l'ISEP (deux semestres ou périodes plus courtes), à quelles dates, et combien de semaines de cours effectives ? L'emploi du temps change-t-il complètement à chaque période ?
- *Pourquoi* : L'unité de génération (semestre ou période) détermine la structure de l'import, de la base et le nombre de variables en mode daté.
- *Défaut proposé* : Génération par semestre de 15 semaines paramétrable par une date de début et de fin saisies par l'utilisateur.

**4.5** 🟠 Le calendrier scolaire (vacances, jours fériés, ponts, semaines d'examens, stages, semaine internationale) sera-t-il fourni, ou faut-il le reconstituer ? Saisi à la main ou calculé automatiquement ?
- *Pourquoi* : Sans lui, l'agrégation compte des semaines vides et le solveur placerait des cours sur des semaines inactives.
- *Défaut proposé* : Table jours_fermes (plages de dates ou semaines, portée globale ou par promotion) saisie à la main, jours fériés fixes préremplis, pas de calcul de Pâques en V1 ; semaines fermées exclues des statistiques.

**4.6** 🟠 Le cycle par apprentissage est-il dans le périmètre ? Si oui, les alternants forment-ils des classes dédiées, quel est le rythme école / entreprise, et est-il le même pour toute la promotion ?
- *Pourquoi* : Les apprentis exigent un calendrier de semaines actives par promotion, incompatible avec une semaine type unique.
- *Défaut proposé* : Classes d'alternants distinctes ; en V1 on génère leur semaine type école ; attribut « semaine entreprise » par groupe et par semaine importé s'il existe, calendrier d'alternance reporté en V2.

**4.7** 🟠 Y a-t-il des séances en alternance semaine A / semaine B (TD toutes les deux semaines, deux demi-groupes qui alternent sur le même créneau) ? Quelle référence de parité : numéro ISO ou numéro de semaine depuis la rentrée ?
- *Pourquoi* : Cela impose un horizon d'au moins deux semaines même en mode semaine type, et une contrainte de couplage entre les deux séances.
- *Défaut proposé* : Pas d'alternance en V1 ; champ `parite` (toutes / paires / impaires) prévu dans le schéma, alternance stricte sur le même créneau et numéro de semaine depuis la rentrée quand ce sera exploité.

**4.8** 🟠 Quand une séance de la semaine type tombe sur un jour férié ou de vacances, doit-elle être rattrapée (replacée ou reportée) ou simplement annulée ?
- *Pourquoi* : Le rattrapage impose un compteur de séances par matière sur l'horizon et un solveur multi-semaines.
- *Défaut proposé* : La séance est annulée et signalée dans un rapport, sans rattrapage automatique.

**4.9** ⚪ Existe-t-il des semaines spéciales (rentrée, projet, intégration, banalisée, stage) à modéliser avec une grille particulière ?
- *Pourquoi* : Elles se traitent soit comme des semaines fermées, soit avec une grille différente.
- *Défaut proposé* : Saisies comme plages fermées, sans grille particulière.

**4.10** ⚪ Pendant les partiels d'une promotion, les autres promotions continuent-elles les cours normalement avec des salles en moins ?
- *Pourquoi* : Cela nécessite des indisponibilités de salle datées combinées à des fermetures par classe.
- *Défaut proposé* : On gère les deux séparément : indisponibilité de salle avec plage de dates et plage fermée par classe.

## 5. Grille horaire et créneaux

**5.1** 🔴 Pouvez-vous confirmer la grille de quatre créneaux fixes : 8 h 30-10 h 30, 10 h 45-12 h 45, 13 h 45-15 h 45, 16 h 00-18 h 00, avec 15 min de pause et un déjeuner de 12 h 45 à 13 h 45 ? Sinon, quelle est la grille réellement utilisée par la scolarité ?
- *Pourquoi* : Toute la modélisation en créneaux discrets (variables CP-SAT, affichage, import) dépend de cette grille, la seule qui termine exactement à 18 h avec un déjeuner d'une heure.
- *Défaut proposé* : Cette grille de quatre créneaux, définie dans une table de configuration (heure de début, durée, pauses) plutôt que codée en dur.

**5.2** 🔴 Le placement est-il discret (un cours commence obligatoirement au début d'un créneau prédéfini) ou continu (un cours peut commencer à n'importe quel quart d'heure, par exemple 9 h 00 ou 14 h 15) ?
- *Pourquoi* : Grille fixe = une variable entière « créneau » par séance ; horaires libres = variables d'intervalle au quart d'heure avec NoOverlap, modèle environ dix fois plus gros et pauses à écrire explicitement.
- *Défaut proposé* : Modèle discret par créneaux, avec la durée stockée en minutes pour pouvoir étendre plus tard.

**5.3** 🔴 Quelle est la durée et la plage de la pause déjeuner (1 h de 12 h 45 à 13 h 45, 1 h 30, plage 12 h 30-14 h 00) ? Est-elle fixe et identique pour tous, classes et enseignants, ou glissante (déjeuners décalés par groupe pour désengorger la cafétéria) ?
- *Pourquoi* : Fixe = un créneau absent de la grille, sans contrainte à écrire ; glissante = grille plus fine et une contrainte « au moins 1 h libre dans la fenêtre » par classe, par enseignant et par jour.
- *Défaut proposé* : Pause fixe d'une heure, 12 h 45-13 h 45, identique pour toutes les classes et garantie aussi pour les enseignants.

**5.4** 🔴 Existe-t-il à l'ISEP des séances d'une durée autre que 2 h (1 h, 1 h 30, 3 h, TP de 4 h), pour quels types, et quelles heures de début sont réellement observées ? Le sélecteur de durée de l'interface doit-il proposer autre chose que 2 h ?
- *Pourquoi* : Toute durée non multiple de 2 h casse la grille discrète ; il faut savoir si l'on gère des blocs de plusieurs créneaux ou une grille plus fine, et cela change formulaire et hauteur des cases.
- *Défaut proposé* : Durée exprimée en nombre de créneaux (1 ou 2) et stockée en minutes ; un script d'analyse liste les durées et débuts observés dans les données réelles pour figer la grille ; les durées non multiples de 2 h sont refusées en V1 avec message ; sélecteur 1 h / 1 h 30 / 2 h / 3 h / 4 h avec 2 h présélectionné.

**5.5** 🟠 Un TP de 4 h occupe-t-il deux créneaux consécutifs de la grille (pause de 15 min incluse) dans la même demi-journée, même salle et même enseignant, ou peut-il enjamber la pause déjeuner ?
- *Pourquoi* : Cela détermine les paires de créneaux autorisées pour les séances longues et si le déjeuner admet une exception par type.
- *Défaut proposé* : Bloc de deux créneaux consécutifs dans la même demi-journée, même salle, même enseignant ; aucune séance ne chevauche jamais le déjeuner.

**5.6** 🟠 Pour le samedi matin : quels créneaux exactement (8 h 30-10 h 30 et 10 h 45-12 h 45 ?), quels TP sont éligibles (tous, ou seulement ceux marqués « samedi possible », par matière, enseignant ou promotion), et est-ce un secours si la semaine est pleine ou une obligation pour certains TP ?
- *Pourquoi* : Il faut un drapeau sur la bonne entité et des créneaux supplémentaires dans la grille ouverts à ces seules séances.
- *Défaut proposé* : Deux créneaux le samedi matin sans pause déjeuner, ouverts uniquement aux séances de type TP avec `samedi_autorise = vrai` (faux par défaut) ; le samedi est un secours, jamais une obligation.

**5.7** 🟠 Les enseignants et les salles sont-ils disponibles le samedi par défaut, ou faut-il une disponibilité explicite pour ce jour ?
- *Pourquoi* : Sinon le solveur placera des TP le samedi avec des enseignants ou dans des salles qui ne sont en réalité pas ouverts.
- *Défaut proposé* : Un enseignant est indisponible le samedi sauf disponibilité déclarée ; une salle est disponible sauf indisponibilité déclarée.

**5.8** 🟠 La grille est-elle identique du lundi au vendredi, ou existe-t-il des demi-journées banalisées et des créneaux réservés (sport ou associations le jeudi après-midi, réunions pédagogiques, conférences) qu'aucun cours ne doit occuper ? À quelle échelle : école, promotion, classe, campus ?
- *Pourquoi* : Ces créneaux doivent être exclus du domaine des variables, sinon le solveur les remplit ; le choix entre grille par jour et indisponibilités par groupe change la structure des créneaux.
- *Défaut proposé* : Grille identique tous les jours ; table creneaux_fermes (jour, créneau, portée globale / promotion / classe) vide par défaut, les demi-journées banalisées y sont saisies.

**5.9** 🟠 Toutes les classes et tous les campus suivent-ils la même grille, ou certains peuvent-ils être décalés (classe qui commence à 8 h 45 ou 9 h 00) ?
- *Pourquoi* : Avec des grilles décalées, les conflits salle/enseignant se détectent par chevauchement d'intervalles et non par égalité de créneau.
- *Défaut proposé* : Grille unique pour toutes les classes et tous les campus.

**5.10** 🟠 « Se terminent au plus tard à 18 h » : un cours qui finit exactement à 18 h 00 est-il autorisé ?
- *Pourquoi* : Cela décide si le quatrième créneau 16 h 00-18 h 00 existe ou si la journée n'a que trois créneaux pleins.
- *Défaut proposé* : 18 h 00 est une heure de fin incluse : le créneau 16 h 00-18 h 00 est valide.

**5.11** 🟠 Y a-t-il des cours après 18 h (cours du soir, langues, options, 18 h 15-20 h 15) ou avant 8 h 30 ?
- *Pourquoi* : Cela ajouterait un créneau hors plage à la grille et une exception à la contrainte « 18 h au plus tard ».
- *Défaut proposé* : 8 h 30 et 18 h sont des bornes strictes.

**5.12** 🟠 Les paramètres structurels (heure d'ouverture, fermeture, durée d'un cours, pause, plage et durée du déjeuner, samedi, temps de trajet, limite de temps du solveur) doivent-ils être modifiables dans un écran « Paramètres », ou seulement dans un fichier de configuration ?
- *Pourquoi* : Un écran Paramètres permet d'adapter la grille aux données réelles pendant la démo, mais impose que le calendrier et le solveur lisent ces valeurs dynamiquement.
- *Défaut proposé* : Table `parametres` en SQLite avec les valeurs de la spec préremplies, écran Paramètres avec bouton « Rétablir les valeurs par défaut » ; la grille et le calendrier se régénèrent à partir de ces valeurs.

**5.14** ⚪ Le samedi matin concerne-t-il toutes les semaines, ou seulement certains samedis précis (rattrapages, semaines chargées) ?
- *Pourquoi* : Avec un calendrier réel il faudrait une liste de samedis ouverts au lieu d'un simple jour de la semaine.
- *Défaut proposé* : Tous les samedis sont ouverts si la fonctionnalité est activée.

**5.15** ⚪ La pause de 15 min doit-elle aussi séparer deux cours d'un même enseignant et deux cours dans une même salle (temps de libération), ou seulement deux cours d'une même classe ?
- *Pourquoi* : Avec la pause intégrée à la grille elle s'applique de fait à tout le monde ; avec des horaires libres il faudrait une contrainte d'écart par ressource.
- *Défaut proposé* : Pause intégrée à la grille, donc garantie pour la classe, l'enseignant et la salle sans contrainte spécifique.

**5.16** ⚪ Existe-t-il des interdictions par type de séance ou par jour, du type « pas de TP à 8 h 30 », « pas de CM le vendredi 16 h », « pas de cours le lundi matin pour les apprentis » ?
- *Pourquoi* : Ces règles se traduisent par des domaines interdits sur les variables de créneau et doivent être connues pour l'import et l'interface.
- *Défaut proposé* : Aucune interdiction de ce type en V1 ; prévues comme préférences en V2.

**5.17** ⚪ S'il existe des cours de 1 h, deux cours de 1 h peuvent-ils partager un même créneau de 2 h sans pause entre eux ?
- *Pourquoi* : Cela imposerait une granularité de 1 h ou de 15 min au lieu de créneaux de 2 h.
- *Défaut proposé* : Pas de cours de 1 h en V1.

**5.18** ⚪ Existe-t-il des activités placées sur la pause déjeuner (langues, sport, conférences 12 h 45-13 h 45) qu'il faudrait faire apparaître dans l'emploi du temps sans bloquer les cours ?
- *Pourquoi* : Elles nécessiteraient un type de séance hors grille qui ne consomme pas la contrainte de déjeuner.
- *Défaut proposé* : Rien n'est placé sur le déjeuner en V1.

## 6. Structure académique et groupes

**6.1** 🔴 Quelle est la hiérarchie exacte des groupes à l'ISEP (promotion > classe ou groupe de TD > demi-groupe de TP), combien de classes par promotion et de sous-groupes par classe, un sous-groupe est-il toujours une partition fixe de sa classe, et comment cette hiérarchie est-elle encodée dans les noms (« I1-G3 », « SI2I-A ») ?
- *Pourquoi* : La contrainte « un groupe ne peut pas avoir deux cours en même temps » exige de savoir quels groupes se recouvrent (une promotion en amphi bloque toutes ses classes et demi-groupes), et l'arbre doit être reconstruit depuis les noms à l'import.
- *Défaut proposé* : Trois niveaux (promotion, classe, demi-groupe) ; un cours cible un niveau quelconque de l'arbre et bloque tous les groupes inclus ou englobants ; arbre reconstruit par expression régulière sur le nom, vérifiable et corrigeable dans l'interface.

**6.2** 🔴 Existe-t-il des groupes transverses qui mélangent des étudiants de plusieurs classes ou promotions (anglais par niveau, LV2, électifs, parcours, projets), et un étudiant peut-il appartenir à plusieurs groupes non hiérarchiques ? Dispose-t-on de la composition de ces groupes ou seulement de leur nom ?
- *Pourquoi* : La contrainte « pas deux cours en même temps » passe de « même classe » à « groupes partageant des membres » ; sans composition, un groupe transverse doit bloquer toute sa promotion.
- *Défaut proposé* : Appartenance multiple via une table groupe_membre ou groupe_conflit déclarant les paires incompatibles ; deux séances sont en conflit si leurs groupes se recouvrent ; sans liste d'étudiants, un groupe transverse est en conflit avec toute sa promotion.

**6.3** 🔴 Faut-il une table `etudiant` dans la base (le schéma actuel n'en a pas), ou l'application ne manipule-t-elle que des classes et groupes ?
- *Pourquoi* : Cela change le schéma SQLite, le diagramme UML, la propagation des besoins d'accessibilité et le niveau de données personnelles stockées.
- *Défaut proposé* : Pas de table étudiant en V1 ; les besoins, indisponibilités et effectifs sont portés par le groupe.

**6.4** 🟠 Les cours d'anglais, LV2, électifs, options et parcours fonctionnent-ils en « barrette » (plusieurs groupes en parallèle sur un même créneau) ? Combien de groupes en parallèle, donc combien de salles nécessaires au même moment, et les étudiants choisissent-ils individuellement ?
- *Pourquoi* : Une barrette est un ensemble de séances forcées sur le même créneau, contrainte spécifique et gourmande en salles ; sans composition connue, elle bloque toute la promotion.
- *Défaut proposé* : Notion de « barrette » : séances liées sur un créneau commun saisi par le planificateur, bloquant toute la promotion, chacune avec sa salle et son enseignant.

**6.5** 🟠 Connaît-on les effectifs réels par promotion, classe et demi-groupe, et seront-ils dans les données du directeur ?
- *Pourquoi* : La contrainte de capacité des salles ne peut se vérifier que si chaque groupe placé a un effectif.
- *Défaut proposé* : Effectif saisi par groupe feuille (fichier groupes ou interface), calculé par somme pour les parents ; à défaut, valeurs par défaut 35 par classe et 18 par demi-groupe, ou estimation par la plus petite salle utilisée dans le réel.

**6.6** 🟠 Faut-il pouvoir déclarer des indisponibilités récurrentes par groupe ou classe (sport, journée entreprise, stage le vendredi) ?
- *Pourquoi* : Même mécanisme que les indisponibilités des enseignants, appliqué aux groupes ; à prévoir dans le schéma et le modèle.
- *Défaut proposé* : Oui : (jour, créneau) indisponibles par groupe, même table et même écran que pour les enseignants.

## 7. Séances, volumes et règles de répartition

**7.1** 🔴 Pour chaque matière, connaît-on le nombre de séances CM, TD et TP à placer par groupe et par semaine (ex. 1 CM en promotion, 2 TD par classe, 1 TP par demi-groupe) ? Existe-t-il une maquette pédagogique ou un syllabus ? Confirmez-vous que l'unité à placer est la « demande » (matière, type, groupes, enseignants, durée, nombre par semaine) dépliée en séances individuelles ?
- *Pourquoi* : C'est l'entrée principale du générateur : sans volumes par (matière, type, groupe), il n'y a rien à placer, et toute autre granularité change la génération des variables.
- *Défaut proposé* : Saisie dans l'interface par matière du nombre de séances par type et par niveau de groupe ; à défaut, reconstitué à partir de l'EDT réel fourni ou de ce que connaissent les membres de l'équipe ; les demandes sont dépliées en séances avant résolution.

**7.2** 🔴 Les CM sont-ils donnés à toute la promotion réunie en amphi (plusieurs classes ensemble) ou par classe ? Combien d'amphis existent sur chaque campus et quelle est leur capacité par rapport à l'effectif d'une promotion ?
- *Pourquoi* : Une séance qui concerne plusieurs groupes bloque tous ces groupes et exige une salle dont la capacité couvre la somme des effectifs.
- *Défaut proposé* : Une séance porte une liste de groupes (1 à n) ; capacité de salle supérieure ou égale à la somme des effectifs ; les amphis sont importés avec leur capacité.

**7.3** 🟠 Quelle est la liste exacte des types de séances (CM, TD, TP, projet, examen, soutenance, conférence, distanciel, autre) et, pour chacun, quel type de salle par défaut ? Seul le type TP est-il éligible au samedi ?
- *Pourquoi* : Le type de séance conditionne le type de salle, la durée, l'autorisation du samedi et le regroupement de classes.
- *Défaut proposé* : Enum {CM, TD, TP, projet, examen, autre} : CM en amphi ou grande salle, TD en salle banalisée, TP en salle info ou labo, projet en salle projet ; examen stocké mais non placé ; seul TP éligible au samedi.

**7.4** 🟠 Une séance peut-elle avoir plusieurs enseignants en même temps (TP encadré à deux, cours en binôme, jury) ?
- *Pourquoi* : Cela impose une relation N:N séance-enseignant, absente du schéma actuel, et une contrainte de conflit pour chacun.
- *Défaut proposé* : Liste de 1 à n enseignants par séance, tous rendus indisponibles sur le créneau.

**7.5** 🟠 Existe-t-il des séances sans salle physique (visio, autonomie, sport à l'extérieur, projet en autonomie) ? Comment sont-elles marquées dans les données et comptent-elles pour la règle de changement de campus ?
- *Pourquoi* : Une séance sans salle viole le NOT NULL du schéma actuel, ne consomme pas de salle et ne déclenche ni la contrainte de trajet ni celle d'accessibilité.
- *Défaut proposé* : Salle virtuelle « Distanciel » de capacité illimitée sans campus, hors règle de trajet et d'accessibilité ; les séances « autonomie » sont ignorées ; non exercé dans la démo.

**7.6** 🟠 Deux séances de la même matière pour le même groupe peuvent-elles avoir lieu le même jour, voire sur deux créneaux consécutifs (maths 8 h 30 puis 10 h 45) ? Y a-t-il un étalement minimum entre elles dans la semaine ?
- *Pourquoi* : C'est une contrainte dure fréquente en scolarité qui n'est pas dans la spec, peut rendre infaisables des données réelles, et certains TP doivent au contraire être consécutifs.
- *Défaut proposé* : Autorisé en V1 ; contrainte optionnelle « au plus une séance de la même matière par jour et par groupe » activable, et champ `bloc` sur la séance pour les TP qui doivent être consécutifs ; pas d'étalement supplémentaire.

**7.7** 🟠 Faut-il une limite dure de créneaux par jour pour une classe (ex. pas plus de 3 créneaux sur 4) ?
- *Pourquoi* : Contrainte de somme par jour et par groupe qui réduit la faisabilité et ajoute des variables de comptage.
- *Défaut proposé* : Aucune limite au-delà de la grille (4 créneaux max de fait) ; paramètre `max_creneaux_jour_classe` prévu, désactivé par défaut.

**7.8** 🟠 Les trous dans la journée d'une classe (cours 8 h 30-10 h 30 puis 13 h 45-15 h 45) sont-ils interdits, à éviter, ou sans importance ? Qu'est-ce qu'un trou exactement : créneau vide encadré par deux cours le même jour, déjeuner exclu ? Une matinée libre suivie de cours compte-t-elle ?
- *Pourquoi* : Une contrainte de compacité est coûteuse en CP-SAT et peut rendre le problème infaisable ; la définition précise conditionne la formule de la pénalité et du vérificateur.
- *Défaut proposé* : Trous tolérés en V1 ; un trou = créneau vide encadré par deux cours le même jour, déjeuner et demi-journées libres exclus ; pénalisé uniquement si l'objectif de compacité est activé.

**7.9** 🟠 Une séance mutualisée entre plusieurs promotions (cours commun A2/A3, option ouverte à deux cycles) : quand on génère avec un périmètre limité à une promotion, cette séance est-elle replacée, figée ou exclue ? Dans quelle vue et sous quelle promotion apparaît-elle, et l'import doit-il fusionner les deux lignes issues des fichiers de chaque promotion en une seule séance ?
- *Pourquoi* : Le périmètre par promotion suppose qu'une séance appartient à une promotion, ce qui est faux pour une séance multi-groupes inter-promotions ; sans règle, elle est soit placée deux fois (faux conflit d'enseignant), soit verrouillée d'un côté et libre de l'autre, et la vue par promotion la perd.
- *Défaut proposé* : Une séance est dans le périmètre dès qu'un de ses groupes l'est ; elle est replacée et tous ses groupes, même hors périmètre, sont bloqués sur le créneau ; l'import fusionne par (jour, heure, salle, enseignant, matière) et concatène les groupes ; elle apparaît dans la vue de chacun de ses groupes.

**7.10** 🟠 Quand une séance porte des groupes qui se recouvrent hiérarchiquement (une classe et l'un de ses demi-groupes, ou une promotion et une classe), l'effectif retenu pour la capacité est-il la somme (double comptage) ou l'union ? Une telle combinaison doit-elle être refusée à la saisie et à l'import, ou normalisée automatiquement en ne gardant que le groupe englobant ?
- *Pourquoi* : Le défaut « capacité ≥ somme des effectifs » compte deux fois les mêmes étudiants dès que l'import produit une ligne « A1 + A1-TD2 », ce qui exclut des salles valides et peut rendre le réel infaisable ; l'arbre des groupes reconstruit par expression régulière rend ce cas probable.
- *Défaut proposé* : Normalisation à l'import et à la saisie : on ne conserve que les groupes maximaux (englobants) de la liste, avec avertissement dans le rapport ; effectif = somme des groupes maximaux ; le vérificateur signale toute séance non normalisée comme erreur d'intégrité.

**7.11** ⚪ Une séance peut-elle être répartie sur plusieurs salles simultanément (TP sur deux salles info voisines, examen sur trois salles) ?
- *Pourquoi* : Cela imposerait une relation N:N séance-salle et une logique de somme de capacités.
- *Défaut proposé* : Non en V1 : on découpe en deux séances distinctes avec chacune sa salle.

**7.12** ⚪ Faut-il respecter un ordre pédagogique dans la semaine (le CM avant le TD de la même matière, le TD avant le TP) ou un écart minimal ?
- *Pourquoi* : Une contrainte de précédence ajoute des relations d'ordre dans le modèle et complique l'import.
- *Défaut proposé* : Aucune précédence en V1 ; contrainte optionnelle en V2.

**7.13** ⚪ Certaines séances ont-elles une salle imposée (labo unique, amphi réservé) ou faut-il toujours laisser le solveur choisir ?
- *Pourquoi* : Salle imposée = domaine réduit à une valeur ; facile à coder mais à prévoir dans le modèle de données.
- *Défaut proposé* : Champ optionnel `salle_imposee` sur la séance.

**7.14** ⚪ Une matière ou un groupe doit-il conserver la même salle pour toutes ses séances de la semaine ?
- *Pourquoi* : Contrainte d'égalité entre les variables salle des séances concernées ; réduit la faisabilité.
- *Défaut proposé* : Non en V1.

**7.15** ⚪ Les séances de projet (PTS et autres) et les soutenances doivent-elles être placées par le générateur ? Ont-elles des formats particuliers (demi-journées, salles projet, jury de plusieurs enseignants) ?
- *Pourquoi* : Cela ajoute un type de séance avec durée longue et enseignants multiples.
- *Défaut proposé* : Type « projet » = séance normale de 2 h ou 4 h en salle projet ; soutenances hors V1.

**7.16** ⚪ Faut-il un plafond hebdomadaire d'heures par enseignant (ex. 20 h) ou par classe (ex. 30 h) ?
- *Pourquoi* : Contrainte de somme hebdomadaire supplémentaire et vérification à l'import si le volume dépasse le plafond.
- *Défaut proposé* : Pas de plafond hebdomadaire en V1, seulement ce qui doit être placé.

**7.17** ⚪ Faut-il limiter l'amplitude d'une journée pour une classe (pas à la fois 8 h 30 et 18 h le même jour) ?
- *Pourquoi* : Contrainte d'amplitude sur le premier et le dernier créneau du jour, différente d'un plafond d'heures.
- *Défaut proposé* : Pas de limite d'amplitude en V1.

**7.18** ⚪ Une classe peut-elle avoir une journée entièrement libre en semaine, ou faut-il répartir les cours sur les cinq jours ?
- *Pourquoi* : Contrainte « au moins une séance par jour » ou « au plus N jours vides » à ajouter.
- *Défaut proposé* : Journée libre autorisée.

**7.19** ⚪ Les trous doivent-ils aussi être évités pour les enseignants ?
- *Pourquoi* : Cela doublerait la contrainte de compacité sur une autre ressource.
- *Défaut proposé* : Aucune contrainte de trou pour les enseignants en V1.

## 8. Enseignants

**8.1** 🔴 L'affectation enseignant-groupe est-elle une donnée d'entrée fixée (M. X fait les TD du groupe 3), ou le solveur doit-il choisir l'enseignant parmi une liste de personnes capables d'assurer la matière ?
- *Pourquoi* : Enseignant fixé = simple contrainte de non-chevauchement ; enseignant à choisir = variable supplémentaire, conflits conditionnels et équilibrage de charge.
- *Défaut proposé* : Affectation fixée en entrée (comme dans l'EDT réel) ; choix par le solveur parmi une liste en V2.

**8.2** 🟠 Sous quelle forme les indisponibilités des enseignants sont-elles connues : récurrentes (« jamais le mercredi »), datées (« absent semaine 12 »), au créneau près, à la demi-journée ou à la journée, en plages (« pas avant 10 h le lundi ») ou en texte libre ?
- *Pourquoi* : Le format des domaines jour/créneau par enseignant, le schéma de la table et l'écran de saisie en dépendent ; le texte libre n'est pas exploitable par le solveur.
- *Défaut proposé* : Liste de (jour, créneau) indisponibles récurrents en V1, convertie depuis des plages horaires à l'import, avec raccourci « journée entière » ; indisponibilités datées prévues dans le schéma pour la V2 ; le texte libre est conservé en commentaire et ignoré par le solveur.

**8.3** 🟠 Faut-il distinguer enseignants permanents et vacataires ? Les vacataires ont-ils des disponibilités très restreintes (un seul jour par semaine) et souhaitent-ils regrouper leurs séances sur une journée ?
- *Pourquoi* : Les disponibilités deviennent l'entrée centrale pour les vacataires, qui doivent être placés en priorité par le solveur ; le regroupement est une préférence à prévoir.
- *Défaut proposé* : Champ statut informatif (permanent / vacataire), non utilisé par le solveur en V1 ; regroupement en préférence V2.

**8.4** 🟠 Y a-t-il des règles de charge pour les enseignants : maximum de séances par jour, pas plus de trois séances consécutives, maximum d'heures par semaine ? Quelles contraintes enseignants sont dures en V1 (indisponibilités, campus autorisés, charge, déjeuner) ?
- *Pourquoi* : Chaque règle est une contrainte CP-SAT distincte à écrire, tester et afficher dans le diagnostic d'infaisabilité.
- *Défaut proposé* : V1 : indisponibilités par créneau et campus autorisés en dur ; paramètre `max_creneaux_jour_prof` prévu (4 = grille), aucun maximum hebdomadaire ; le reste en préférences V2.

**8.5** 🟠 Doit-on déduire les indisponibilités des enseignants à partir de l'année passée (un enseignant qui n'a jamais cours le lundi serait-il indisponible le lundi) ?
- *Pourquoi* : Déduire produit des contraintes fausses ; ne pas déduire laisse le solveur beaucoup plus libre que le réel.
- *Défaut proposé* : Aucune déduction ; indisponibilités saisies dans l'interface, avec import optionnel d'un CSV indisponibilites.csv.

## 9. Salles, campus et déplacements

**9.1** 🔴 Disposera-t-on de la liste réelle des salles avec campus, bâtiment, capacité, type, équipements et accessibilité (étage, ascenseur) ? Combien de salles par campus ? Sinon, faut-il un fichier « référentiel salles » séparé rempli par l'équipe ?
- *Pourquoi* : C'est une entrée obligatoire des contraintes de capacité, de type et d'accessibilité ; sans elle, on génère des salles fictives et la démo perd sa crédibilité.
- *Défaut proposé* : Fichier salles.csv complémentaire avec colonnes optionnelles (type, équipements, étage, ascenseur) ; capacité manquante = « inconnue », non contraignante avec avertissement ; accessibilité tri-état (oui / non / inconnu, inconnu traité comme non) ; jeu fictif de 20 salles sur deux campus à défaut.

**9.2** 🔴 Comment décrire la compatibilité séance-salle : un type unique exact (amphi, banalisée, informatique, labo électronique, labo physique, projet, langue), une liste de types acceptables, et/ou des équipements requis (vidéoprojecteur, PC, oscilloscopes) ? Une salle peut-elle avoir plusieurs usages ?
- *Pourquoi* : Cela détermine le pré-filtrage du domaine `salle` de chaque séance, donc le nombre de variables et le schéma de données.
- *Défaut proposé* : Un type principal par salle plus une liste d'équipements (tags) ; une séance exige une liste de types acceptables (vide = n'importe lequel) et un ensemble d'équipements inclus dans ceux de la salle.

**9.3** 🔴 Quelle est la liste exacte des campus (Notre-Dame-des-Champs Paris 6e, Issy-les-Moulineaux, sites loués ?), chaque salle est-elle liée à un campus unique, et le temps de trajet est-il le même pour tout couple de campus ou faut-il une matrice éditable dans l'interface ?
- *Pourquoi* : Une constante de 1 h et une matrice campus × campus ne se codent pas de la même façon, et un tableau de trajets éditable est un écran de plus.
- *Défaut proposé* : Deux campus saisis dans un écran dédié (nom, adresse facultative), chaque salle rattachée à exactement un campus, matrice symétrique paramétrable avec 60 min entre campus différents et 0 dans le même campus ; le tableau par paire dans l'interface est classé optionnel.

**9.4** 🔴 Confirmez-vous cette lecture du « battement d'une heure » : deux créneaux séparés de 15 min (8 h 30→10 h 45, 13 h 45→16 h 00) doivent être sur le même campus, alors que la pause déjeuner (12 h 45→13 h 45 = 1 h) ou un créneau vide autorise un changement de campus ? Est-ce 1 h au total ou 1 h en plus de la pause ?
- *Pourquoi* : C'est littéralement la contrainte à coder et le cas limite le plus fréquent ; si le déjeuner ne compte pas, un changement de campus n'est possible qu'en sautant un créneau, ce qui réduit fortement la faisabilité.
- *Défaut proposé* : Écart fin → début d'au moins 60 min, déjeuner inclus : un déjeuner d'une heure suffit pour changer de campus.

**9.5** 🟠 La contrainte de déplacement entre campus s'applique-t-elle aussi aux enseignants qui donnent cours sur les deux campus dans la même journée ?
- *Pourquoi* : Cela double les contraintes de trajet (par groupe et par enseignant) et peut réduire fortement les solutions.
- *Défaut proposé* : Oui, même règle et même battement d'une heure pour les enseignants que pour les groupes.

**9.6** 🟠 La scolarité tolère-t-elle réellement un changement de campus dans la journée pour un groupe, ou l'usage est-il « une demi-journée ou une journée = un campus » ? Peut-on imposer cela en dur, ou en faire une préférence ?
- *Pourquoi* : Une variable « campus de la demi-journée » est bien plus simple et rapide qu'une contrainte par paire de séances ; « même campus toute la journée » en dur réduit fortement la faisabilité.
- *Défaut proposé* : On modélise « même campus par demi-journée » pour les classes (équivalent au battement avec un déjeuner d'une heure) et la contrainte par paire pour les enseignants ; « minimiser les changements de campus par jour » en objectif optionnel V2.

**9.7** 🟠 La capacité est-elle une contrainte dure stricte (capacité ≥ effectif), ou tolère-t-on un dépassement (ex. 5 %) ou un effectif minimal pour les amphis ?
- *Pourquoi* : Fixe l'inégalité exacte à écrire ; une tolérance ou un minimum change les domaines de salles admissibles.
- *Défaut proposé* : Dure et stricte : capacité ≥ effectif total des groupes présents, sans minimum.

**9.8** 🟠 Peut-on placer un petit TD dans un amphi de 200 places libre, ou un cours sans besoin de PC dans une salle informatique, si rien d'autre n'est disponible ? Toléré, à éviter, ou interdit ?
- *Pourquoi* : Fait la différence entre « types compatibles » et « type exact » ; si c'est à éviter, c'est un objectif d'optimisation.
- *Défaut proposé* : Toléré en V1 (seul l'inverse est interdit), sauf si un type est explicitement exclu pour la séance ; pénalisé en V2.

**9.9** 🟠 Comment les indisponibilités de salles (travaux, examens, événements, réservations BDE ou administration) sont-elles exprimées : par (jour, créneau) récurrents dans la semaine type, ou par dates précises ?
- *Pourquoi* : Des dates précises ne sont représentables que si l'horizon est le semestre ; le format détermine le schéma de la table et le formulaire.
- *Défaut proposé* : Table indisponibilite_salle par (jour, créneau) récurrents en V1, même grille cliquable que pour les enseignants ; plages datées activées seulement si l'horizon devient le semestre.

**9.10** 🟠 Faut-il pouvoir restreindre un groupe ou un enseignant à une liste de campus autorisés (« toujours Issy » pour une personne dont le trajet est contraint) ?
- *Pourquoi* : Ajoute une contrainte de domaine sur les salles possibles d'une séance.
- *Défaut proposé* : Liste de campus autorisés facultative sur le groupe et sur l'enseignant ; vide = tous.

**9.11** 🟠 Que fait la contrainte de changement de campus avec une salle dont le campus n'a pas pu être déduit à l'import (préfixe inconnu, salle « à définir », salle louée) : campus « inconnu » compatible avec tout (la règle devient silencieusement inopérante), incompatible avec tout (le réel devient infaisable), ou refus de lancer la génération tant qu'une salle utilisée n'a pas de campus ?
- *Pourquoi* : C'est la façon la plus discrète de rater la contrainte de déplacement en démo : elle semble respectée parce qu'aucune salle n'a de campus renseigné ; le vérificateur et le solveur doivent appliquer la même politique, sinon l'un trouve des violations que l'autre ignore.
- *Défaut proposé* : Génération refusée avec la liste des salles sans campus et lien vers l'écran de correction ; le vérificateur signale « campus inconnu » comme violation d'intégrité distincte ; les salles virtuelles (distanciel) sont les seules autorisées sans campus.

**9.12** 🟠 Le vérificateur appliqué à l'emploi du temps réel doit-il évaluer la règle de déplacement et la pause de 15 min en minutes réelles (écart fin → début, y compris pour les séances hors grille comme 9 h-11 h puis 13 h-15 h) plutôt qu'en indices de créneau comme le solveur, et comment garantit-on que les deux implémentations rendent le même verdict sur les séances de la grille ?
- *Pourquoi* : Le solveur raisonne en indices mais le réel ne respecte pas la grille ; si le vérificateur ne sait raisonner qu'en indices, le test « notre vérificateur accepte l'EDT réel » et le comptage des violations du réel (argument de soutenance) sont impossibles, et deux implémentations divergentes d'une même règle sont la source classique des faux conflits.
- *Défaut proposé* : Vérificateur en minutes réelles pour toutes les contraintes temporelles, solveur en indices avec conversion à la frontière ; test croisé qui génère des EDT aléatoires sur la grille et vérifie que solveur et vérificateur concordent contrainte par contrainte.

**9.13** ⚪ Chaque promotion a-t-elle un campus de rattachement (A1 à Issy, A2 et A3 à NDC) ? Sur un même campus, y a-t-il des bâtiments assez éloignés pour que la pause de 15 min ne suffise pas ?
- *Pourquoi* : Un rattachement devient une préférence ou une contrainte ; des bâtiments distants obligeraient à modéliser des sous-campus.
- *Défaut proposé* : Pas de rattachement ; un campus = un lieu unique, la pause de 15 min suffit en interne.

**9.14** ⚪ La contrainte de déplacement s'applique-t-elle aussi entre le dernier cours d'un jour et le premier du lendemain, ou entre le vendredi et le samedi ?
- *Pourquoi* : Sans remise à zéro quotidienne, la contrainte s'appliquerait à travers la nuit.
- *Défaut proposé* : La contrainte est remise à zéro chaque jour.

**9.15** ⚪ Un enseignant peut-il avoir un temps de trajet inter-campus différent de celui des étudiants (par exemple en voiture, 30 min) ?
- *Pourquoi* : Un deuxième jeu de paramètres de trajet double la contrainte de succession.
- *Défaut proposé* : Même règle pour tous (1 h) ; pas de paramètre par enseignant.

**9.16** ⚪ Existe-t-il des salles partagées avec un autre établissement, louées ponctuellement, ou des amphis réservables seulement certains jours ?
- *Pourquoi* : Cela se traduit en indisponibilités de salle à importer.
- *Défaut proposé* : Table indisponibilite_salle vide à l'import et remplie dans l'interface.

**9.17** ⚪ Certaines salles sont-elles réservées à une promotion, un département ou une matière (laboratoire d'électronique des A2, salle projet du cycle intégré), c'est-à-dire une restriction portée par la salle et non par la séance ? Comment la connaîtra-t-on dans les données réelles ?
- *Pourquoi* : Une réservation côté salle se traduit par un domaine interdit pour toutes les autres séances, inverse de la « salle imposée » côté séance ; sans elle, le solveur remplit un labo réservé avec des TD banals et la scolarité ne reconnaît pas le résultat.
- *Défaut proposé* : Champ facultatif `reserve_a` sur la salle (liste de promotions ou de matières, vide = tout le monde), déduit du réel par « salle utilisée par une seule promotion sur l'année » puis confirmé à la main.

## 10. Accessibilité et cas particuliers

**10.1** 🔴 À quel niveau rattacher le besoin d'accessibilité (étudiant nommé, groupe, séance) et comment se propage-t-il : à toutes les séances de la classe, des demi-groupes, des options et langues de l'étudiant, et aux amphis de promotion ? Quand un amphi réunit plusieurs classes dont une seule contient une personne PMR, doit-il être accessible ?
- *Pourquoi* : La propagation du besoin aux groupes englobants, inclus ou transverses change la contrainte, les données à saisir et impose l'agrégation des exigences d'une séance multi-groupes.
- *Défaut proposé* : Drapeau « nécessite une salle accessible » posé par le planificateur sur chaque groupe concerné (classe, TD, TP, option), sans étudiant ; tout groupe contenant transitivement un groupe PMR exige une salle accessible ; l'exigence d'une séance est l'union de celles de tous ses groupes ; les amphis sont supposés accessibles dans les données de test.

**10.2** 🔴 Peut-on stocker le nom d'un étudiant ou la nature du handicap (« fauteuil roulant », « malvoyant ») à côté du besoin, ou uniquement des exigences techniques anonymes (« sans escalier », « boucle magnétique ») ?
- *Pourquoi* : Un vocabulaire d'exigences techniques minimise la sensibilité, s'aligne directement sur les attributs des salles et fixe ce qui peut apparaître dans l'interface et les exports.
- *Défaut proposé* : Aucun nom ni diagnostic stocké : un enregistrement « besoin » à identifiant technique rattaché au groupe, liste de tags d'exigences, commentaire interne facultatif.

**10.3** 🔴 Quels attributs d'accessibilité stocker par salle : étage, bâtiment, ascenseur, rampe, porte large, ou un simple booléen « accessible PMR » ? L'ascenseur est-il un attribut du bâtiment ou de la salle, et existe-t-il des salles inaccessibles malgré un ascenseur (marches intérieures) ?
- *Pourquoi* : Fixe les colonnes des tables salle et bâtiment (le schéma actuel n'a que nom et capacité) et la hiérarchie campus > bâtiment > salle.
- *Défaut proposé* : Salle : campus, bâtiment, étage (entier), booléen `acces_plain_pied` forçable à faux ; bâtiment : booléen `ascenseur` ; « accessible_sans_escalier » est calculé (rez-de-chaussée OU ascenseur), pas saisi.

**10.4** 🔴 Le défaut « accessibilité inconnue = non accessible » rend infaisable toute séance d'un groupe PMR dès que le référentiel réel des salles n'indique pas l'accessibilité (cas très probable) : préfère-t-on « inconnu = accessible avec avertissement » sur les données réelles, imposer la saisie manuelle de l'accessibilité des salles réellement utilisées avant la démo, ou n'exercer la contrainte PMR que sur le jeu synthétique ?
- *Pourquoi* : Deux défauts du questionnaire se contredisent (inconnu = non, et démo PMR sur données réelles pseudonymisées) ; le problème se découvre en général la veille de la démo sous la forme d'un « aucune solution » sans cause visible, imputé à tort au solveur.
- *Défaut proposé* : Tri-état conservé ; à l'import, une salle sans information est marquée « inconnu » et la pré-vérification affiche, pour chaque groupe PMR, le nombre de salles compatibles connues ; si ce nombre est nul, avertissement explicite proposant de basculer le paramètre `accessibilite_inconnue_bloque` (vrai par défaut) ; pour la démo, on renseigne à la main les 10 à 20 salles utilisées.

**10.5** 🟠 Quels types de besoins la V1 doit-elle couvrir : uniquement « accès sans escalier » comme dans la spec, ou aussi malvoyance, malentendance, matériel spécifique (table réglable, boucle magnétique), place PMR ? Un besoin est-il un ET de tags, ou peut-il contenir des alternatives ?
- *Pourquoi* : Booléen = simple filtrage du domaine des salles ; multi-besoins = ensemble de tags de la salle devant inclure ceux du groupe ; le OU côté besoin complique l'expression CP-SAT.
- *Défaut proposé* : Liste de tags extensible {sans_escalier, ascenseur, place_pmr, boucle_magnetique, table_reglable} en ET, chaque exigence étant une propriété booléenne dérivée de la salle ; seul « sans_escalier » est exercé dans les tests et la démo.

**10.6** 🟠 Un enseignant en situation de handicap doit-il bénéficier du même mécanisme d'exigences d'accessibilité, appliqué à toutes ses séances ?
- *Pourquoi* : Généralise le besoin à un « participant » (groupe ou enseignant) au lieu de le lier au seul groupe.
- *Défaut proposé* : Oui : la table des besoins pointe vers un groupe ou un enseignant ; la contrainte de salle agrège les deux.

**10.7** 🟠 Une salle peut-elle perdre temporairement son accessibilité (ascenseur en panne deux semaines) ? Faut-il le modéliser comme une indisponibilité de salle classique ou comme un événement daté sur le bâtiment ?
- *Pourquoi* : Une panne d'ascenseur rend inaccessibles toutes les salles en étage du bâtiment, mais seulement pour les groupes avec besoin PMR : c'est une indisponibilité conditionnelle.
- *Défaut proposé* : Événement daté « ascenseur indisponible » sur le bâtiment, qui exclut ses salles en étage uniquement pour les séances avec exigence PMR ; ignoré tant que l'horizon est une semaine type.

**10.8** 🟠 Pour un groupe avec personne à mobilité réduite, faut-il un temps de déplacement inter-campus plus long que l'heure standard, un temps minimal entre deux bâtiments, ou préférez-vous interdire totalement le changement de campus dans la journée ?
- *Pourquoi* : Le paramètre de trajet devient dépendant du groupe et non global ; une interdiction est une contrainte dure simple, un allongement un paramètre.
- *Défaut proposé* : Temps inter-campus paramétrable par profil (1 h standard, 1 h 30 PMR) ; pas de temps inter-bâtiment en V1 ; l'interdiction totale sera une préférence V2.

**10.9** 🟠 Faut-il gérer des besoins temporaires (jambe cassée pendant six semaines) avec une date de début et de fin ?
- *Pourquoi* : Ajoute une période de validité au besoin et suppose un horizon daté plutôt qu'une semaine type.
- *Défaut proposé* : Le besoin porte des dates début/fin facultatives (sans dates = toute l'année), ignorées tant que l'horizon est une semaine type.

**10.10** 🟠 Connaissez-vous l'état réel des campus ISEP (bâtiments sans ascenseur, amphis en sous-sol, salles en étage accessibles uniquement par escalier) ?
- *Pourquoi* : Permet un jeu de test réaliste et évite que la contrainte soit triviale (toutes salles accessibles) ou impossible (aucune) en démo.
- *Défaut proposé* : Jeu de test inventé : 2 campus, 3 bâtiments dont un sans ascenseur, une vingtaine de salles dont environ un tiers non accessibles.

**10.11** 🟠 L'emploi du temps affiché ou exporté doit-il cacher la raison d'une attribution de salle (ne pas révéler qu'un groupe contient une personne handicapée) ?
- *Pourquoi* : Sépare une vue « consultation » neutre d'une vue « diagnostic » du planificateur qui explique les choix.
- *Défaut proposé* : L'EDT public montre seulement la salle ; la justification (« salle sans escalier requise ») n'apparaît que dans la vue planificateur.

**10.12** 🟠 Si les données réelles ne contiennent aucun cas de handicap ni de cas particulier, acceptez-vous que nous injections des cas fictifs clairement marqués pour les tests d'intégration et la démo ?
- *Pourquoi* : Sans cas réel, la contrainte d'accessibilité ne serait jamais exercée par les tests d'intégration exigés par le module.
- *Défaut proposé* : Oui : fichier de scénarios de test avec cas fictifs (« le groupe A2-TD3 contient une personne PMR »), séparé des données réelles.

**10.13** 🟠 Y a-t-il à l'ISEP des sportifs de haut niveau avec des plages bloquées récurrentes, à modéliser comme une indisponibilité de leur groupe entier ou comme un suivi individuel ?
- *Pourquoi* : Sans table étudiant, seule une indisponibilité de groupe est possible ; un suivi individuel impose l'entité étudiant.
- *Défaut proposé* : Indisponibilité récurrente rattachée à un groupe (même mécanisme que pour les enseignants) ; pas de suivi individuel.

**10.14** 🟠 Un redoublant ou un étudiant en double cursus qui suit des matières de deux promotions : faut-il éviter les chevauchements entre ces séances, et comment saurez-vous quels étudiants sont concernés dans les données réelles ?
- *Pourquoi* : Un participant assistant à des séances de deux promotions casse l'hypothèse « une séance = une classe » et exige une contrainte de non-chevauchement inter-promotions.
- *Défaut proposé* : On modélise un « groupe virtuel » rattaché à des séances de plusieurs promotions ; le solveur interdit le chevauchement pour ce groupe ; si les données réelles ne l'indiquent pas, cas de test fictif, sinon hors périmètre.

**10.15** ⚪ Qui saisit le besoin d'accessibilité dans l'application, et doit-il être modifiable en cours d'année avec regénération ?
- *Pourquoi* : Définit l'écran de saisie et le déclenchement d'une nouvelle génération après modification.
- *Défaut proposé* : Saisie par le planificateur dans un formulaire dédié, modifiable à tout moment, regénération manuelle ensuite.

**10.16** ⚪ Un étudiant malvoyant ou malentendant a-t-il des contraintes de placement dans la salle (premier rang, éclairage) que l'application doit connaître ?
- *Pourquoi* : Cela reviendrait à modéliser l'intérieur d'une salle, ce qui n'a aucun sens pour le solveur.
- *Défaut proposé* : Hors périmètre : on ne modélise pas les places dans la salle.

**10.17** ⚪ Un besoin d'accessibilité doit-il pouvoir être accompagné d'une référence à un aménagement validé (mission handicap ISEP, notification MDPH) ou d'une pièce jointe ?
- *Pourquoi* : Stocker des documents impose une gestion de fichiers et accroît la sensibilité des données.
- *Défaut proposé* : Aucune pièce jointe ; un champ texte « commentaire interne » suffit.

**10.18** ⚪ Existe-t-il des durées différentes pour certains étudiants (départ anticipé de 15 min pour un transport adapté, tiers-temps mobilisant un surveillant supplémentaire) ?
- *Pourquoi* : Des durées par étudiant obligeraient à modéliser des sous-séances et une ressource surveillant par examen aménagé.
- *Défaut proposé* : Non : la durée est celle de la séance pour tous ; un départ anticipé et le tiers-temps se gèrent hors application.

**10.19** ⚪ Les étudiants en double cursus ont-ils des jours bloqués récurrents assez fréquents pour justifier une contrainte, ou est-ce géré au cas par cas hors application ?
- *Pourquoi* : Même mécanisme que les sportifs si un groupe entier est concerné ; sinon inutile à coder.
- *Défaut proposé* : Géré via l'indisponibilité de groupe si un groupe entier est concerné ; ignoré sinon.

**10.20** ⚪ Un groupe avec personne PMR doit-il rester dans la même salle toute la journée pour limiter les déplacements ?
- *Pourquoi* : Contrainte dure supplémentaire de stabilité de salle, coûteuse pour le solveur.
- *Défaut proposé* : Préférence V2 ; rien en V1.

**10.21** ⚪ Faut-il que la vue planificateur mette en évidence les séances contraintes par l'accessibilité (icône, filtre « séances PMR ») pour prouver au jury que la contrainte est respectée ?
- *Pourquoi* : Fonctionnalité d'affichage et de filtrage supplémentaire dans la grille.
- *Défaut proposé* : Icône discrète et filtre dans la vue planificateur uniquement.

## 11. Moteur de résolution

**11.1** 🔴 La V1 doit-elle seulement trouver un emploi du temps valide (première solution faisable, potentiellement pleine de trous et de cours à 8 h 30 tous les jours), ou faut-il déjà un critère de qualité minimal ?
- *Pourquoi* : Sans objectif, CP-SAT s'arrête à la première solution faisable (rapide mais peu convaincante en démo) ; avec objectif, il faut définir les termes et accepter un temps de calcul plus long.
- *Défaut proposé* : Mode « faisable » par défaut, plus un objectif de compacité optionnel (minimiser les trous des classes) activable dans l'interface.

**11.2** 🔴 En cas d'infaisabilité, que doit afficher l'application : un simple message, des pré-vérifications arithmétiques (« le groupe A2-G1 demande 22 h pour 20 h disponibles », « 3 TP info le lundi matin pour 2 salles info »), le noyau des contraintes en conflit, ou des suggestions de relaxation ?
- *Pourquoi* : C'est la fonctionnalité qui illustre le principe « aucune contrainte n'est abandonnée » devant le jury ; message = trivial, noyau = littéraux d'hypothèse CP-SAT, suggestions = résolutions successives et temps multiplié.
- *Défaut proposé* : Pré-vérifications rapides par famille (charge par groupe, enseignant, type de salle, salles accessibles) affichées comme avertissements avant même de lancer, puis panneau « Pourquoi ça échoue » listant le noyau de contraintes en conflit obtenu par hypothèses ; suggestions en V2.

**11.3** 🔴 Quelle formulation CP-SAT retient-on : une variable booléenne par (séance, créneau, salle), ou une variable entière « créneau » plus une variable entière « salle » par séance avec non-chevauchement conditionnel, ou une résolution en deux phases (créneaux et campus d'abord, salles ensuite par appariement) ? Comment casse-t-on les symétries entre salles interchangeables et entre séances identiques (deux TD de la même matière pour le même groupe) ?
- *Pourquoi* : C'est le premier facteur de lenteur : 800 séances × 22 créneaux × 100 salles font 1,7 million de booléens, et des dizaines de salles interchangeables multiplient les solutions équivalentes que le solveur explore une à une ; l'affectation des salles est un appariement quasi trivial une fois les créneaux fixés, mais la contrainte de campus et l'accessibilité doivent alors être connues dès la phase 1.
- *Défaut proposé* : Variables entières créneau + salle par séance, domaine salle pré-filtré par type, capacité et accessibilité, non-chevauchement par ressource via booléens d'égalité ; bris de symétrie par ordre imposé entre séances identiques ; deux phases seulement si le test de charge dépasse la limite ; choix documenté dans docs/solveur.md.

**11.4** 🔴 À quelle date au plus tard fait-on la « preuve de faisabilité sur le réel » (génération réussie en moins de 60 s sur les données réelles à l'échelle de la démo, toutes contraintes dures actives), quel est le plan B si elle échoue (réduction à une promotion, une semaine ou un campus ; découpage par promotion ; deux phases), et ajoute-t-on un test de performance en CI avec seuil sur le jeu « grand » ?
- *Pourquoi* : Les projets de ce type échouent quand on découvre à J-7 que le solveur ne termine pas sur la vraie taille ; la date de bascule vers le synthétique (12/10) protège le jalon tests, pas la soutenance ; un test `perf` empêche qu'une contrainte ajoutée en novembre multiplie le temps par vingt sans que personne le voie avant la répétition.
- *Défaut proposé* : Jalon « réalité » le 09/11, une semaine avant la fin des développements, avec compte rendu ; plan B décidé le jour même dans l'ordre découpage par promotion puis réduction du périmètre de démo ; test tests/perf/test_charge.py marqué `perf`, seuil 120 s sur le jeu grand, exécuté en job nocturne et avant chaque jalon.

**11.18** 🔴 Pendant le calcul, la page peut-elle être bloquée, ou faut-il un lancement en arrière-plan avec barre de progression, temps écoulé, solutions intermédiaires et bouton « Arrêter » ?
- *Pourquoi* : Le mode arrière-plan impose un thread, un état de tâche partagé, une route d'interrogation et le `StopSearch` de CP-SAT, ce qui structure l'API entre moteur et interface.
- *Défaut proposé* : Arrière-plan dans un thread, page qui interroge `/generation/{id}/etat` chaque seconde, indicateur animé, temps écoulé et bouton Arrêter ; un seul job à la fois.

**11.5** 🟠 Les sept contraintes « envisagées » de la section 3 de la spec (capacité, type de salle, indisponibilités enseignants, indisponibilités salles, groupes, nombre de séances, matériel) sont-elles toutes attendues en V1 ?
- *Pourquoi* : Chaque contrainte ajoute des variables et trois tests ; l'ordre d'implémentation et la charge du lot moteur en dépendent.
- *Défaut proposé* : Toutes en V1, en dur, chacune activable ou désactivable.

**11.6** 🟠 Parmi les contraintes présentées comme dures dans la spec, y en a-t-il que vous considérez en réalité comme des préférences (changement de campus, samedi) ?
- *Pourquoi* : Une contrainte souple devient un terme pénalisé de l'objectif au lieu d'une contrainte, ce qui change la formulation et le comportement en cas d'infaisabilité.
- *Défaut proposé* : Tout ce qui est dans la section 2 de la spec est dur, campus compris.

**11.7** 🟠 Si un objectif est retenu, quel ordre de priorité entre trous des classes, changements de campus, cours à 8 h 30 ou 16 h, samedi, équilibrage des jours, regroupement des cours d'un enseignant ?
- *Pourquoi* : Pondération ou ordre lexicographique à coder ; un mauvais ordre produit des emplois du temps contre-intuitifs devant le jury.
- *Défaut proposé* : Somme pondérée : 10 × trous + 5 × changements de campus + 2 × samedi + 1 × créneaux extrêmes.

**11.8** 🟠 Faut-il déjà stocker les préférences d'enseignants (éviter 8 h 30, regrouper sur deux jours) même si la V1 ne les optimise pas, et doivent-elles apparaître grisées dans l'interface ou être masquées ?
- *Pourquoi* : Prévoir dès le départ un champ `dure` et un `poids` sur chaque contrainte évite une refonte du schéma et du solveur en V2.
- *Défaut proposé* : Chaque contrainte porte `dure: bool` et `poids: int`, seules les dures sont actives en V1 ; préférences masquées dans l'interface en V1.

**11.9** 🟠 Quel temps de calcul est acceptable pour une génération complète pendant la démo (5 s, 30 s, 2 min) et sur quelle machine (nombre de cœurs) ? Que se passe-t-il au dépassement ?
- *Pourquoi* : Fixe `max_time_in_seconds`, le nombre de workers, l'ambition de l'objectif et l'affichage d'une progression.
- *Défaut proposé* : Limite de 60 s réglable dans les Paramètres, 4 à 8 workers ; au dépassement, on affiche la meilleure solution trouvée si toutes les contraintes dures sont satisfaites, sinon l'échec ; cible 200 séances en moins de 30 s sur un portable.

**11.10** 🟠 Le même jeu de données doit-il toujours produire exactement le même emploi du temps (tests reproductibles, démo prévisible), ou un résultat différent d'une exécution à l'autre est-il acceptable ?
- *Pourquoi* : CP-SAT en multi-threads n'est pas déterministe ; la reproductibilité impose `random_seed` fixe et `num_workers = 1`, donc un calcul plus lent.
- *Défaut proposé* : Mode déterministe (graine fixe, 1 worker) pour les tests et en option dans l'interface ; multi-workers sinon.

**11.11** 🟠 Est-il acceptable, dans un mode « diagnostic » explicitement signalé, de produire un emploi du temps partiel (maximum de séances placées, liste des non placées) sans contredire le principe « aucune contrainte n'est abandonnée » ?
- *Pourquoi* : Nécessite une variable « placée » par séance et un objectif de maximisation : chemin de code distinct du mode normal.
- *Défaut proposé* : Échec global par défaut ; mode diagnostic séparé, jamais présenté comme un emploi du temps valide.

**11.12** 🟠 Si le temps limite est atteint sans solution, faut-il distinguer « prouvé impossible » de « pas trouvé dans le temps imparti » et proposer de relancer plus longtemps ?
- *Pourquoi* : Deux statuts CP-SAT différents (INFEASIBLE vs UNKNOWN) qui appellent deux messages et deux cas de test.
- *Défaut proposé* : Deux messages distincts et un bouton « relancer 5 min ».

**11.13** 🟠 L'utilisateur doit-il pouvoir placer manuellement ou verrouiller certaines séances (jour, créneau, salle imposés, cadenas sur la case) puis relancer le solveur pour compléter le reste, par exemple après l'absence d'un enseignant ?
- *Pourquoi* : Le verrouillage transforme des variables en constantes, impose une génération incrémentale, une validation préalable des verrous entre eux, et c'est un excellent scénario de démo (forcer une salle inaccessible et voir l'échec).
- *Défaut proposé* : Champ `verrouille` avec créneau et salle fixés, respecté comme contrainte dure ; conflit entre verrous signalé avant résolution ; cadenas dans le calendrier classé utile.

**11.14** 🟠 Après une modification (enseignant malade, salle en travaux, besoin PMR ajouté), le solveur doit-il minimiser les changements par rapport à l'emploi du temps précédent, ou peut-il tout recalculer ?
- *Pourquoi* : Minimiser = solution précédente injectée comme indice (AddHint) et objectif sur le nombre de séances déplacées ; recalcul = rien à faire.
- *Défaut proposé* : Regénération complète avec indice systématique et option « conserver au maximum l'emploi du temps actuel ».

**11.15** 🟠 La génération doit-elle pouvoir se limiter à un périmètre (une promotion, quelques classes, un jour, un campus) en gardant tout le reste figé ?
- *Pourquoi* : Équivaut à verrouiller automatiquement tout ce qui est hors périmètre, et ajoute un écran de sélection avant lancement.
- *Défaut proposé* : Écran de lancement avec cases à cocher par promotion, tout coché par défaut ; le résultat remplace uniquement les groupes du périmètre, le reste étant verrouillé automatiquement.

**11.16** 🟠 Souhaitez-vous un vérificateur indépendant du solveur (fonction Python pure qui liste les violations d'un emploi du temps donné), utilisable pour valider une modification manuelle, l'EDT réel de l'année passée ou un EDT saisi à la main, et servant d'oracle aux tests ?
- *Pourquoi* : Double implémentation de chaque contrainte, mais indispensable pour les tests exigés par AGENTS.md, la détection de conflits dans l'interface et la comparaison avec le réel.
- *Défaut proposé* : Module `verificateur` avec une fonction par contrainte et le même catalogue que le solveur, renvoyant une liste de violations typées (contrainte, séances, message) ; liste vide = EDT valide.

**11.17** 🟠 L'utilisateur doit-il pouvoir activer ou désactiver une contrainte avant génération (« ignorer la capacité pour ce test ») sans qu'elle soit jamais abandonnée automatiquement ?
- *Pourquoi* : Chaque contrainte devient un module avec un indicateur d'activation ; utile pour la démo, le diagnostic et les tests, sans violer le principe de la spec.
- *Défaut proposé* : Cases à cocher dans l'interface, tout activé par défaut, désactivation tracée dans le résultat.

**11.19** 🟠 Quand des séances figées ou verrouillées (occupations externes, événements importés, verrous du planificateur, EDT réel importé comme verrous) violent déjà entre elles une contrainte dure (deux séances figées dans la même salle, trajet inter-campus de 15 min dans le réel), le solveur doit-il refuser de démarrer, ignorer ces violations héritées en n'exigeant que de ne pas en créer de nouvelles, ou exclure les séances fautives ?
- *Pourquoi* : Le réel importé contiendra des violations de nos règles ; si le solveur applique ses contraintes à des séances figées incohérentes, le modèle est infaisable avant même de placer quoi que ce soit, et le message « aucune solution » sera imputé à tort au générateur pendant la démo.
- *Défaut proposé* : Pré-vérification qui liste les violations héritées entre séances figées ; par défaut génération refusée avec la liste ; option « tolérer les violations héritées » qui n'applique les contraintes qu'aux paires impliquant au moins une séance libre et marque le résultat « valide hors violations héritées ».

**11.20** ⚪ L'utilisateur doit-il pouvoir demander « une autre solution » différente de la précédente ?
- *Pourquoi* : Relance avec une graine différente, ou contrainte d'exclusion de la solution précédente.
- *Défaut proposé* : Bouton « regénérer » avec une nouvelle graine.

**11.21** ⚪ Le moteur doit-il être appelable en ligne de commande (JSON ou SQLite en entrée, résultat en sortie) indépendamment de l'interface web, pour la CI et les tests d'intégration ?
- *Pourquoi* : Définit une API stable `generer(donnees, options) -> resultat` et un point d'entrée CLI séparé du serveur web.
- *Défaut proposé* : Oui : `python -m edt generer` plus API Python.

**11.23** ⚪ Faut-il injecter l'EDT réel de l'année passée comme indice de départ (AddHint) du solveur pour que l'emploi du temps généré ressemble à celui de la scolarité (mêmes jours, mêmes salles quand c'est possible) et converge plus vite, ou préfère-t-on une solution « neutre » qui montre que l'outil ne recopie pas le réel ?
- *Pourquoi* : Un indice tiré du réel accélère fortement la résolution sur l'instance réelle et produit un résultat que la scolarité reconnaît, mais il brouille l'argument « généré automatiquement » et impose un rapprochement séance à séance entre besoins et référence.
- *Défaut proposé* : Option « partir de l'emploi du temps réel » désactivée par défaut, disponible seulement quand une référence est importée et rapprochée ; la démo montre d'abord la génération sans indice.

## 12. Interface utilisateur

**12.1** 🔴 Quelle technologie front : pages servies par Python (FastAPI ou Flask + Jinja2 + HTMX ou JavaScript vanilla, sans build) ou application monopage (React/Vue avec Vite et Node) ? Quel est le niveau de l'équipe en JavaScript et quelqu'un a-t-il prévu Node (le .gitignore contient node_modules/ et .vite/) ?
- *Pourquoi* : Ce choix structure tout le dossier web, les tests d'intégration, l'installation sur la machine de démo et la répartition du travail ; un front séparé impose Node chez les quatre et double la surface de tests.
- *Défaut proposé* : FastAPI + Jinja2 + HTMX, aucune dépendance Node, section Node retirée du .gitignore ; on passe à une SPA seulement si au moins deux membres sont à l'aise en React ou Vue.

**12.2** 🔴 Quelles entités doivent être créées et modifiées directement dans l'interface (enseignants, salles, campus, classes et groupes, matières, cours à placer, indisponibilités), ou certaines sont-elles uniquement importées et en lecture seule ?
- *Pourquoi* : Chaque entité éditable demande un écran liste + formulaire + validation + suppression, soit environ une journée de travail par entité.
- *Défaut proposé* : Formulaires complets pour les sept entités avec liste triable et recherche ; l'import fichier vient en complément, pas en remplacement.

**12.3** 🔴 Faut-il un écran « Cours à placer » (plan de charge) sous forme de tableau éditable où chaque ligne indique matière × groupe × type × enseignant × nombre de séances par semaine × durée × type de salle requis ?
- *Pourquoi* : C'est l'écran central avant la génération et le plus gros morceau d'interface de saisie.
- *Défaut proposé* : Tableau éditable en ligne avec ajout rapide, duplication de ligne et compteur de séances par groupe ; l'import CSV alimente ce même tableau.

**12.4** 🔴 Quelles vues du calendrier sont nécessaires : par classe ou groupe, par enseignant, par salle ? Faut-il aussi une vue par campus, par matière, ou une vue globale « toute l'école » en colonnes parallèles ?
- *Pourquoi* : Chaque vue est un filtre sur les mêmes séances, mais la vue globale multi-groupes demande une mise en page bien plus complexe, et chaque vue impose des index en base.
- *Défaut proposé* : Trois vues (groupe, enseignant, salle) avec un sélecteur commun et des filtres par campus et par matière ; la vue globale est classée optionnelle.

**12.5** 🔴 Si l'horizon devient le semestre, le calendrier doit-il proposer une navigation semaine par semaine (flèches, sélecteur de date, vacances grisées) ? En semaine type, faut-il afficher un numéro de semaine ou une date ?
- *Pourquoi* : La navigation temporelle change la structure de l'URL, l'état du calendrier et le format des exports.
- *Défaut proposé* : Semaine type unique sans date en V1 ; si l'horizon devient le semestre, ajout d'un sélecteur de semaine avec vacances grisées, sans rien changer aux cases.

**12.6** 🔴 Après génération, l'emploi du temps est-il en lecture seule, modifiable par formulaire (panneau latéral pour changer créneau et salle), ou par glisser-déposer des cases ? Le glisser-déposer est-il attendu pour la soutenance ?
- *Pourquoi* : Le glisser-déposer est la fonctionnalité front la plus coûteuse (souris, retour visuel, re-vérification serveur à chaque déplacement) ; le formulaire couvre le même besoin en trois fois moins de temps.
- *Défaut proposé* : Formulaire de modification obligatoire (panneau latéral avec créneau et salle, liste des salles compatibles) ; glisser-déposer classé utile, livré si le temps le permet.

**12.7** 🟠 La grille hebdomadaire doit-elle être développée maison en CSS Grid sous forme de tableau HTML sémantique lisible par un lecteur d'écran, ou une bibliothèque de calendrier tierce (FullCalendar) ou un rendu canvas/SVG sont-ils acceptables ?
- *Pourquoi* : Une bibliothèque tierce accélère le glisser-déposer mais impose ses conventions, une dépendance lourde à justifier et le fonctionnement hors ligne ; le choix est structurant pour tout le front.
- *Défaut proposé* : Tableau HTML sémantique avec en-têtes jour/heure en CSS Grid (contrôle total, hors ligne, cases proportionnelles à la durée), sans canvas ni bibliothèque ; choix documenté dans une issue.

**12.8** 🟠 Pour les indisponibilités des enseignants, des salles et des groupes, préférez-vous une grille hebdomadaire cliquable (cocher les créneaux interdits) ou une liste de plages « jour + début + fin » ?
- *Pourquoi* : La grille cliquable nécessite un composant JavaScript dédié mais rend la saisie et la vérification bien plus rapides pendant la démo.
- *Défaut proposé* : Grille hebdomadaire cliquable réutilisée à l'identique pour les trois entités ; une liste texte est générée en dessous pour vérification.

**12.10** 🟠 Le calendrier doit-il mettre en évidence les conflits (cours superposés, salle trop petite, enseignant indisponible), par exemple après une modification manuelle ou un import incohérent, avec un panneau listant les conflits ?
- *Pourquoi* : La détection côté interface repose sur le vérificateur indépendant du solveur, réutilisé dans les tests d'intégration.
- *Défaut proposé* : Cases rouges avec icône d'alerte et panneau « Conflits » cliquable qui centre le calendrier sur la séance concernée.

**12.11** 🟠 Quand une modification manuelle viole une contrainte dure (salle déjà prise, enseignant indisponible), l'interface doit-elle bloquer avec explication, autoriser avec un avertissement rouge, ou proposer les alternatives valides ?
- *Pourquoi* : Bloquer respecte le principe « aucune contrainte abandonnée » mais impose une vérification serveur avant chaque changement ; autoriser demande un état « en conflit » persistant.
- *Défaut proposé* : Blocage avec message expliquant la contrainte violée, et liste des créneaux et salles valides proposée dans le panneau de modification.

**12.12** 🟠 Quel niveau de finition visuelle est attendu : fonctionnel et propre (composants standards, palette neutre) ou une identité visuelle avec logo et couleurs de l'ISEP ? A-t-on le droit d'utiliser le logo ISEP ?
- *Pourquoi* : Une identité visuelle demande du temps de design et une charte ; le logo pose une question d'autorisation.
- *Défaut proposé* : Interface propre et cohérente (une police, une palette de six couleurs, composants uniformes), nom « Générateur d'EDT ISEP » en texte, sans logo officiel.

**12.13** 🟠 L'application doit-elle être utilisable sur tablette ou téléphone (étudiant qui consulte son emploi du temps), ou uniquement sur PC et vidéoprojecteur ? Quels navigateurs supporter ?
- *Pourquoi* : Un calendrier à six colonnes est illisible sur téléphone ; le mobile impose une vue jour et des menus repliables ; cibler les navigateurs récents autorise CSS Grid, dialog et fetch sans polyfill.
- *Défaut proposé* : Priorité au bureau (1280 px et plus), lisible sur tablette en paysage, navigateurs à mise à jour automatique des deux dernières années ; le téléphone est classé optionnel avec une simple vue jour en lecture.

**12.14** 🟠 L'interface doit-elle viser un niveau formel d'accessibilité (WCAG 2.1 AA : contrastes, navigation clavier complète, labels, lecteur d'écran), ce qui serait cohérent avec la contrainte handicap traitée par le projet ?
- *Pourquoi* : Cela impose dès le début des composants sémantiques, un ordre de tabulation et une alternative clavier à tout glisser-déposer, difficiles à rajouter après coup.
- *Défaut proposé* : WCAG AA de base : contraste 4.5:1, focus visible, formulaires utilisables au clavier, en-têtes de tableau, alternative par formulaire au glisser-déposer ; pas d'audit lecteur d'écran.

**12.15** 🟠 Faut-il une page d'accueil sous forme de tableau de bord (compteurs, avertissements de données incohérentes, dernier emploi du temps généré) avec un parcours guidé (1 Référentiels → 2 Cours → 3 Contraintes → 4 Génération → 5 Résultat) ?
- *Pourquoi* : Le tableau de bord structure la navigation et le scénario de démo ; le parcours guidé impose un état d'avancement calculé à partir des données.
- *Défaut proposé* : Tableau de bord avec compteurs, avertissements et les cinq étapes en cartes cliquables ; menu latéral permanent en complément.

**12.16** 🟠 Que doit faire l'interface quand on supprime une entité utilisée ailleurs (enseignant qui a des cours, salle avec séances placées) : refuser avec la liste des dépendances, supprimer en cascade, ou demander confirmation ?
- *Pourquoi* : Le comportement détermine les clés étrangères de la base (le schéma actuel ne les active pas) et le message affiché.
- *Défaut proposé* : Refus avec message listant les dépendances (« utilisé par 6 cours ») et lien vers celles-ci ; jamais de cascade silencieuse.

**12.17** 🟠 Livre-t-on d'abord une tranche verticale minimale de bout en bout (import CSV en ligne de commande → génération → vue groupe en HTML brut → export CSV) fonctionnelle avant le 12/10, puis on enrichit écran par écran, ou construit-on d'abord les sept écrans de saisie des référentiels avant de brancher le solveur ?
- *Pourquoi* : Les interfaces « jamais finies » sont celles qu'on commence par les formulaires : à la Toussaint on a sept écrans de saisie et aucun emploi du temps affiché ; une tranche verticale garantit qu'il existe à tout moment une version démontrable et donne aux tests d'intégration du 02/11 quelque chose à tester.
- *Défaut proposé* : Tranche verticale au 12/10 (import CLI, génération, une vue groupe, export CSV), écrans de saisie ajoutés ensuite par ordre d'utilité pour la démo : salles, enseignants, cours à placer, groupes, indisponibilités, matières, campus.

**12.18** 🟠 Deux défauts du questionnaire se contredisent : le scénario de démo « forcer une salle inaccessible pour un groupe PMR et voir l'échec » suppose que le verrou est accepté, alors que la règle « toute modification manuelle violant une contrainte dure est bloquée » l'interdit à la saisie. Lequel s'applique : blocage immédiat (la démo montre alors le message de blocage), ou verrou accepté avec avertissement puis échec expliqué à la génération ?
- *Pourquoi* : Cela fixe si l'écran de modification appelle le vérificateur avant chaque écriture (une route et un état d'erreur par contrainte) ou si des verrous incohérents peuvent exister en base ; les deux comportements ne se testent pas de la même façon et le script de démo dépend du choix.
- *Défaut proposé* : Blocage immédiat pour la modification d'un placement existant ; un verrou posé sur une séance non encore placée est accepté avec avertissement et c'est la génération qui échoue en citant le verrou ; la démo utilise ce second chemin.

**12.19** ⚪ Faut-il afficher, après une génération réussie, un panneau technique repliable avec les statistiques du solveur (temps de calcul, séances placées, taux d'occupation des salles, nombre de contraintes) ?
- *Pourquoi* : Cela demande de remonter des métriques du solveur vers l'interface, mais valorise le travail devant le jury.
- *Défaut proposé* : Panneau repliable « Détails de la génération » avec cinq indicateurs, replié par défaut.

**12.20** ⚪ Comment colorer les cases du calendrier : par matière, par type (CM/TD/TP), par campus ou par enseignant ? Couleurs automatiques ou choisies dans la fiche matière ?
- *Pourquoi* : Le critère détermine s'il faut un champ couleur et une légende, et une palette automatique doit rester lisible avec 30 matières.
- *Défaut proposé* : Couleur automatique par matière (palette de 12 teintes accessibles), bordure ou icône selon le type, badge de campus en petit ; champ couleur modifiable dans la fiche matière.

**12.21** ⚪ Quelles informations afficher dans chaque case selon la vue (matière, type, enseignant, salle, campus, groupe), et lesquelles réserver à l'infobulle ou au panneau latéral ?
- *Pourquoi* : Une case de 2 h sur six jours a peu de place ; le choix des champs fixe tailles de police et hauteur minimale.
- *Défaut proposé* : Dans la case : matière en gras, puis salle et enseignant (ou groupe dans la vue enseignant) ; au clic, panneau latéral avec tous les détails et les boutons d'action.

**12.22** ⚪ La colonne du samedi doit-elle être toujours visible, visible seulement si des séances y sont placées, ou activable par un bouton ?
- *Pourquoi* : Une sixième colonne réduit la largeur de chaque jour et change les exports PDF.
- *Défaut proposé* : Colonne masquée par défaut, affichée automatiquement dès qu'une séance y est placée, avec un bouton « Afficher le samedi ».

**12.23** ⚪ En plus de la vue semaine, faut-il une vue jour ou une vue mois ?
- *Pourquoi* : Chaque vue supplémentaire est un gabarit et un jeu de tests de plus ; la vue jour est surtout utile sur petit écran.
- *Défaut proposé* : Vue semaine uniquement ; la vue jour n'est ajoutée que si l'affichage mobile est demandé.

**12.24** ⚪ Faut-il une vue de synthèse « occupation des salles » (tableau ou carte de chaleur salles × créneaux, taux par campus) ?
- *Pourquoi* : C'est un écran de plus, mais un argument fort en soutenance pour montrer que le générateur exploite bien les ressources.
- *Défaut proposé* : Classé optionnel : simple tableau des taux d'occupation par salle, sans carte de chaleur.

**12.25** ⚪ Faut-il des boutons Annuler / Rétablir pour les modifications manuelles, et sur quelle profondeur ?
- *Pourquoi* : L'undo/redo impose un journal des modifications côté serveur ou une pile d'états côté navigateur.
- *Défaut proposé* : Classé optionnel ; en V1, l'utilisateur peut restaurer la version générée depuis l'historique.

**12.26** ⚪ Un thème sombre ou fort contraste est-il souhaité, et toute information portée par une couleur (conflit, salle accessible, séance verrouillée) doit-elle être doublée d'un texte ou d'une icône ?
- *Pourquoi* : Le thème sombre double le travail sur la palette du calendrier ; le doublage par icône fixe les règles de design des composants et des légendes.
- *Défaut proposé* : Pas de thème sombre en V1, couleurs en variables CSS pour l'ajouter plus tard ; chaque couleur est doublée d'un texte ou d'une icône avec libellé.

**12.27** ⚪ Le calendrier doit-il rester lisible sur un vidéoprojecteur de salle de cours (1024×768 ou 1280×720, faible contraste) pendant la soutenance ?
- *Pourquoi* : Cela impose une taille de police minimale, des contrastes renforcés et un mode présentation à tester avant le 14/12.
- *Défaut proposé* : Police minimale 14 px dans les cases, contraste AA, bouton « Mode présentation » qui masque les menus et agrandit le calendrier.

**12.28** ⚪ La validation des saisies doit-elle être immédiate (champ rouge dès la frappe) ou seulement à l'envoi ? Quelles actions demandent une confirmation (réinitialisation, remplacement de l'emploi du temps, import « Tout remplacer ») ?
- *Pourquoi* : La validation immédiate demande du JavaScript sur chaque formulaire en plus de la validation serveur, et chaque confirmation est une boîte de dialogue à concevoir.
- *Défaut proposé* : Validation immédiate sur les champs simples, validation complète côté serveur avec messages en français sous le champ ; confirmation pour toute action destructrice ou qui écrase un emploi du temps.

**12.29** ⚪ L'interface est-elle en français uniquement, ou faut-il prévoir l'anglais ? Quel format pour les heures et jours (« 08:30 » ou « 8h30 », « Lun. » ou « Lundi », semaine ISO) ?
- *Pourquoi* : L'internationalisation impose d'externaliser tous les libellés dès le début ; les formats conditionnent les filtres et les exports.
- *Défaut proposé* : Français uniquement sans i18n ; heures « 08:30 », jours en toutes lettres dans le calendrier et abrégés dans les tableaux, semaine ISO si l'horizon est le semestre.

**12.30** ⚪ Comment gérer les listes volumineuses (150 enseignants, 80 salles) dans les écrans et les sélecteurs ?
- *Pourquoi* : Le volume conditionne la pagination, la recherche et l'ergonomie des sélecteurs (liste déroulante ou autocomplétion).
- *Défaut proposé* : Listes avec recherche et tri, sélecteurs avec autocomplétion au-delà de 20 éléments.

**12.31** ⚪ L'EDT réel importé (référence) doit-il être consultable dans les mêmes vues calendrier que l'EDT généré, avec une bascule « Réel / Généré » ou un affichage côte à côte, et les conflits détectés dans le réel doivent-ils s'y afficher en rouge ?
- *Pourquoi* : C'est l'écran qui rend la comparaison visible en soutenance ; sans lui, les métriques restent des chiffres, mais l'affichage côte à côte double la largeur du calendrier et impose un état « source » dans toutes les routes.
- *Défaut proposé* : Bascule « Réel / Généré » dans les trois vues existantes (même gabarit, paramètre de requête `source`), conflits du réel en rouge via le vérificateur ; pas de côte à côte.

**12.32** ⚪ Un emploi du temps généré a-t-il un cycle de vie (brouillon en cours de modification → validé, verrouillé contre toute modification), ou n'y a-t-il qu'un « emploi du temps courant » toujours modifiable ?
- *Pourquoi* : Un statut « validé » protège la version démontrée contre une fausse manipulation et donne un sens à l'historique ; il ajoute un champ, deux boutons et un contrôle sur chaque modification.
- *Défaut proposé* : Statut sur chaque génération (brouillon / validé), une seule validée à la fois, modification interdite sur une version validée sauf « dupliquer en brouillon ».

**12.33** ⚪ Faut-il une feuille de style CSS entièrement maison, ou un micro-framework CSS sans JavaScript embarqué localement (Pico.css, Simple.css, Bootstrap) pour les formulaires, tableaux et boutons ?
- *Pourquoi* : Un micro-framework donne des formulaires propres en une journée et une cohérence que quatre personnes n'obtiendront pas à la main, mais c'est une dépendance à justifier et à embarquer hors ligne ; le CSS maison reste indispensable pour la grille du calendrier.
- *Défaut proposé* : Pico.css versionné dans web/static (un fichier, sans JS) pour les composants standards, CSS maison pour la grille du calendrier, couleurs en variables CSS.

## 13. Exports et impression

**13.1** 🔴 Quels formats d'export sont attendus et lesquels sont obligatoires pour la soutenance : CSV, Excel, PDF, iCal (.ics importable dans Google Agenda ou Outlook), image PNG, export textuel accessible ?
- *Pourquoi* : Chaque format est un module distinct avec ses tests et parfois une bibliothèque (openpyxl, reportlab), et les tests bout en bout ont besoin de formats figés pour comparer le fichier exporté au résultat attendu.
- *Défaut proposé* : CSV obligatoire (une ligne par séance, il tient lieu d'export accessible) ; iCal et PDF (via l'impression du navigateur) classés utiles ; Excel et image optionnels.

**13.2** ⚪ L'export se fait-il sur la vue affichée (un groupe, un enseignant), par lot (tous les groupes en un fichier ou une archive zip par entité), ou les deux ?
- *Pourquoi* : L'export par lot demande une génération de fichiers multiples et un écran de sélection.
- *Défaut proposé* : Bouton « Exporter » sur la vue courante, plus un export global CSV de toutes les séances ; l'archive zip par entité est classée optionnelle.

**13.3** ⚪ L'impression doit-elle produire une page A4 paysage propre par groupe ou enseignant (sans menus, avec en-tête et légende), utilisable telle quelle par une scolarité ?
- *Pourquoi* : Une feuille de style d'impression dédiée est nécessaire pour que le calendrier tienne sur une page avec des couleurs lisibles en noir et blanc.
- *Défaut proposé* : Feuille de style d'impression A4 paysage par vue, en-tête avec nom de l'entité et semaine, légende, couleurs assombries pour le noir et blanc.

**13.4** ⚪ Un scénario complet (données + emploi du temps) doit-il pouvoir être exporté et importé sous forme d'un seul fichier JSON pour l'échanger entre membres ?
- *Pourquoi* : Ce format sert aussi de fixture pour les tests d'intégration et de sauvegarde de secours pour la démo.
- *Défaut proposé* : Oui : `edt export scenario.json` et `edt import scenario.json`, classé utile.

**13.5** ⚪ Dans l'export CSV « une ligne par séance », comment sont écrits les champs multi-valeurs (plusieurs groupes, plusieurs enseignants) : une seule ligne avec les valeurs séparées à l'intérieur de la cellule, ou une ligne par couple séance × groupe avec un identifiant de séance commun ? Le format est-il identique à celui de l'import pour permettre l'aller-retour ?
- *Pourquoi* : Excel gère mal les listes dans une cellule séparées par « ; », les tests bout en bout comparent des fichiers, et une séance multi-groupes exportée sur n lignes sans identifiant sera réimportée comme n séances en conflit d'enseignant.
- *Défaut proposé* : Une ligne par séance avec listes séparées par « | » dans la cellule et une colonne `seance_id` stable ; format d'export = format d'import ; option « une ligne par groupe » pour l'usage tableur, non réimportable.

**13.6** ⚪ L'export CSV doit-il être réimportable tel quel par l'application (aller-retour sans perte, testé automatiquement), et faut-il aussi un export au format du logiciel de l'ISEP pour qu'une scolarité puisse réinjecter l'emploi du temps généré dans son outil ?
- *Pourquoi* : L'aller-retour est un test d'intégration simple et convaincant qui fige le format ; l'export vers le logiciel de l'école demande de connaître son format d'import, souvent non documenté, et n'a de valeur que si quelqu'un l'utilise.
- *Défaut proposé* : Aller-retour garanti et testé (export → import → même EDT) ; export au format du logiciel de l'école hors périmètre, sauf si le directeur en fournit le gabarit.

## 14. Architecture technique et stockage

**14.1** 🔴 Validez-vous une structure avec un seul paquet installable `src/edt/` contenant modele/, contraintes/, solveur/, stockage/, web/ (le `web/` racine d'AGENTS.md devenant `src/edt/web`), plus tests/, data/ et docs/ ?
- *Pourquoi* : Un paquet unique simplifie les imports, `pip install -e .`, le point d'entrée `python -m edt` et un éventuel packaging.
- *Défaut proposé* : Structure `src/edt/` unique ; AGENTS.md et le README sont mis à jour en conséquence.

**14.2** 🔴 Acceptez-vous de scinder le schéma actuel (table `cours` avec salle, jour et heure obligatoires) en « séance à placer », « placement » (résultat du solveur) et « référence » (réel importé), et d'étendre le schéma du coéquipier (campus, bâtiments, groupes hiérarchiques, type de séance, type/équipement/accessibilité de salle, indisponibilités, calendrier, scénario, génération) en remplaçant database.py, insert_data.py et test_database.py dans une PR qu'il relit ?
- *Pourquoi* : Sans cette séparation on ne peut ni exprimer un besoin non placé ni comparer réel et généré ; il faut éviter deux schémas concurrents dans le dépôt et respecter la notation individuelle des commits.
- *Défaut proposé* : Trois tables (seance, placement, seance_reference), schéma étendu vers une dizaine de tables dans une PR dédiée avec Co-authored-by pour le coéquipier, en gardant SQLite et les noms de tables en français ; les fichiers racine sont supprimés dans une PR qu'il relit lui-même.

**14.3** 🔴 Le fichier `emploi_du_temps.db` (28 Ko) est déjà commité : doit-on continuer à versionner la base ou l'ignorer et la régénérer depuis des fichiers texte ?
- *Pourquoi* : Un binaire SQLite dans git provoque des conflits de fusion irrésolubles à quatre, un diff à chaque test qui écrit dedans, et embarque potentiellement des données réelles.
- *Défaut proposé* : On ignore `*.db` et `*.sqlite`, on retire le fichier du dépôt, et on versionne un jeu de démo en CSV/JSON chargé par `edt init-db` et `edt seed`.

**14.4** 🔴 Quel outil de gestion des dépendances (pip + requirements.txt, uv, Poetry) et faut-il verrouiller les versions exactes pour garantir la même installation chez les quatre et en démo ?
- *Pourquoi* : Cela fixe la commande d'installation documentée, le fichier de verrouillage et le workflow CI ; OR-Tools change d'API entre versions et une mise à jour silencieuse peut casser le solveur la veille de la soutenance.
- *Défaut proposé* : `pyproject.toml` (PEP 621) avec `uv` si les quatre peuvent l'installer, sinon pip avec `requirements.txt` épinglé généré depuis le pyproject, plus `requirements-dev.txt` pour pytest et ruff ; `.venv` obligatoire.

**14.5** 🔴 Comment l'application se lance-t-elle sur la machine de démo : `python -m edt`, scripts `lancer.bat`/`lancer.sh`, exécutable PyInstaller, Docker ? Le serveur doit-il ouvrir automatiquement le navigateur, sur quel port par défaut avec quel repli s'il est occupé, et sur quelle interface (127.0.0.1 ou réseau local) ?
- *Pourquoi* : PyInstaller avec OR-Tools est fragile, Docker n'est pas disponible partout ; un échec de bind pendant la soutenance est un cas classique de démo ratée, et écouter sur 0.0.0.0 déclenche le pare-feu Windows.
- *Défaut proposé* : `python -m edt` comme point d'entrée, plus `lancer.bat` et `lancer.sh` qui créent le venv, installent les dépendances et ouvrent le navigateur (option `--no-browser`) ; port 8000 par défaut, premier port libre entre 8000 et 8010 sinon avec URL affichée ; 127.0.0.1 uniquement, option `--host` ; l'exécutable est classé optionnel.

**14.6** 🔴 Quels systèmes d'exploitation et versions de Python ont les trois autres membres (Windows, macOS Intel ou Apple Silicon, Linux) ? Votre machine : Windows 11, Python 3.11.9, OR-Tools non installé. Version minimale : 3.11 strict, ou 3.10 si un membre ne peut pas mettre à jour ?
- *Pourquoi* : OR-Tools ne fournit des roues binaires que pour certaines combinaisons OS/Python ; 3.11 apporte tomllib et StrEnum ; scripts, chemins et encodages diffèrent selon l'OS.
- *Défaut proposé* : `requires-python >= 3.11`, chaque membre installe 3.11 ou 3.12 via python.org ou `uv python install`, et on vérifie `pip install ortools` chez chacun dès la séance du 05/10.

**14.12** 🔴 ORM (SQLAlchemy, SQLModel, peewee) ou `sqlite3` de la bibliothèque standard comme dans le code existant ?
- *Pourquoi* : Un ORM est une dépendance lourde et une couche à expliquer en soutenance ; le SQL brut demande plus de code mais reste lisible et proche de l'existant.
- *Défaut proposé* : `sqlite3` standard + dataclasses, un module `depot` par entité, clés étrangères activées.

**14.29** 🔴 Les routes HTMX renvoyant du HTML, faut-il aussi exposer une API JSON documentée (préfixe /api, schéma OpenAPI généré par FastAPI) pour les tests, les scripts et une éventuelle interface future, ou est-ce une double surface à maintenir inutilement ?
- *Pourquoi* : Une API JSON rend les tests d'intégration lisibles et donne un argument d'architecture en soutenance, mais chaque écran existe alors en deux versions à tester ; le choix fixe la structure de web/.
- *Défaut proposé* : API JSON minimale limitée à ce que la CLI et les tests utilisent (import, génération, état du calcul, export), documentation OpenAPI automatique, écrans HTML par-dessus ; pas de duplication des formulaires CRUD.

**14.7** 🟠 Les machines des membres sont-elles des PC personnels avec droits administrateur, ou des postes ISEP verrouillés (installation impossible, proxy, pas de Docker) ?
- *Pourquoi* : Un poste verrouillé impose un mode portable (venv relatif, pas d'installateur) et interdit certaines dépendances.
- *Défaut proposé* : PC personnels, installation libre via pip dans un venv du projet.

**14.8** 🟠 L'application doit-elle fonctionner sans aucune connexion Internet (salle de soutenance sans wifi fiable) ?
- *Pourquoi* : Charger une bibliothèque ou une police depuis un CDN casse l'affichage sans réseau ; embarquer tout localement change la gestion des dépendances front.
- *Défaut proposé* : Tout est embarqué dans `web/static` (CSS, JS, polices système ou fichiers versionnés), zéro requête externe.

**14.9** 🟠 Si `pip install ortools` échoue définitivement chez un membre, accepte-t-on un solveur de repli (backtracking maison, python-constraint) ou impose-t-on OR-Tools ?
- *Pourquoi* : Une interface `Solveur` abstraite coûte peu maintenant et beaucoup plus tard, et elle conditionne le diagramme de classes.
- *Défaut proposé* : OR-Tools obligatoire, mais le solveur est derrière une interface abstraite pour permettre un repli ou un test sans OR-Tools.

**14.10** 🟠 Où placer la base par défaut : dossier `data/` du projet ou dossier utilisateur (%APPDATA%, ~/.local/share) ?
- *Pourquoi* : Le code actuel ouvre `emploi_du_temps.db` relativement au dossier courant, ce qui casse dès qu'on lance depuis un autre répertoire.
- *Défaut proposé* : `data/edt.sqlite` résolu depuis la racine du projet, surchargeable par la variable `EDT_DB_PATH` ou `--db`.

**14.11** 🟠 Faut-il un mécanisme de migration de schéma (scripts SQL numérotés, Alembic) ou peut-on recréer la base depuis les fichiers d'import à chaque changement ?
- *Pourquoi* : Quatre développeurs modifieront le schéma en parallèle ; sans stratégie, une base locale obsolète provoque des erreurs SQL incompréhensibles.
- *Défaut proposé* : Scripts SQL numérotés dans `edt/stockage/migrations/` appliqués selon `PRAGMA user_version`, pas d'Alembic.

**14.13** 🟠 La base est-elle la source de vérité (l'interface modifie la base, les fichiers ne servent qu'à l'import), ou les fichiers du directeur restent-ils la référence à réimporter à chaque lancement ?
- *Pourquoi* : Cela décide si les écrans CRUD écrivent en base et si un import écrase ou fusionne les données existantes.
- *Défaut proposé* : La base est la source de vérité ; l'import écrit en base avec un choix « remplacer » ou « ajouter », l'export permet de repartir en fichiers.

**14.14** 🟠 Comment stocker horaires et jours : texte « 08:30 » (existant), minutes depuis minuit, index de créneau, entier jour (0 = lundi), date ISO ? Le moteur peut-il raisonner uniquement en indices (jour 0..5, créneau 0..3) et convertir à la frontière ?
- *Pourquoi* : La colonne `heure_debut TEXT` actuelle empêche de trier et de comparer ; le solveur travaille sur des entiers et le texte oblige des conversions à chaque lecture.
- *Défaut proposé* : Table `creneau` de référence, le solveur travaille en indices ; en base, minutes depuis minuit + jour entier 0-5 + colonne `date` ISO nullable pour la V2, vue SQL lisible pour le débogage, affichage « 8h30 » et import acceptant « HH:MM » et « HhMM ».

**14.16** 🟠 Validez-vous la liste minimale de classes Campus, Batiment, Salle, Enseignant, Promotion, Groupe, Matiere, Seance (à placer), Creneau, Placement (résultat), Contrainte (abstraite), Solveur, EmploiDuTemps ?
- *Pourquoi* : Les noms de classes fixent les tables SQL, les modules et le vocabulaire de toute la documentation.
- *Défaut proposé* : Liste retenue, en français, avec `Seance` distincte de `Placement` pour séparer la demande du résultat.

**14.17** 🟠 Les contraintes doivent-elles être des classes séparées (une par contrainte, activables individuellement) ou codées directement dans le solveur ?
- *Pourquoi* : Une classe par contrainte permet le test unitaire par contrainte exigé par AGENTS.md et l'affichage des contraintes actives dans l'interface.
- *Défaut proposé* : Classe abstraite `Contrainte` avec `appliquer(modele)` et `verifier(edt)`, une sous-classe courte avec docstring par contrainte, enregistrées dans un registre.

**14.18** 🟠 Le solveur tourne-t-il dans un thread du serveur web ou dans un sous-processus isolé ? Un plantage natif d'OR-Tools (segfault, mémoire) ou un calcul qui ne répond plus à StopSearch doit-il pouvoir être tué sans redémarrer le serveur, et que voit l'utilisateur dans ce cas ?
- *Pourquoi* : Un thread partage le processus : une erreur native ou un blocage du GIL fait tomber tout le serveur en pleine démo, sans message, avec une transaction SQLite potentiellement ouverte ; un sous-processus se tue proprement et l'interface affiche un échec explicite.
- *Défaut proposé* : Sous-processus (multiprocessing avec spawn) qui reçoit les données en JSON et renvoie le résultat en JSON, délai maximal = limite solveur + 10 s puis arrêt forcé ; l'interface affiche « le calcul a été interrompu » avec le journal ; la base n'est écrite qu'au retour du résultat.

**14.19** ⚪ Quelle est la machine la moins puissante de l'équipe (nombre de cœurs, RAM) ?
- *Pourquoi* : CP-SAT est multi-thread et peut saturer un petit portable ; cela fixe le nombre de workers par défaut.
- *Défaut proposé* : On suppose 4 cœurs / 8 Go et on limite le solveur à 4 workers par défaut.

**14.20** ⚪ Docker est-il installé et acceptable chez les quatre membres et sur la machine de démo ?
- *Pourquoi* : Investir dans un Dockerfile n'a de sens que si au moins une personne peut le lancer.
- *Défaut proposé* : On n'en dépend pas ; un Dockerfile optionnel pourra être ajouté en fin de projet.

**14.21** ⚪ Peut-on ajouter `*.bat text eol=crlf` au `.gitattributes` qui force LF partout actuellement ?
- *Pourquoi* : Certains scripts batch Windows se comportent mal avec des fins de ligne LF.
- *Défaut proposé* : Exception CRLF pour les `.bat` et `.cmd`.

**14.22** ⚪ Le dossier de travail peut-il contenir des espaces ou accents (le vôtre s'appelle `projet edt`) sur toutes les machines ?
- *Pourquoi* : Les scripts et certains outils cassent avec des espaces si les chemins ne sont pas cités et gérés par `pathlib`.
- *Défaut proposé* : `pathlib` partout, tous les chemins cités dans les scripts, un test avec un chemin contenant un espace.

**14.23** ⚪ Comment configurer l'application (port, chemin de base, timeout solveur, mode démo) : fichier config.toml, variables d'environnement, arguments en ligne de commande ?
- *Pourquoi* : Un fichier de configuration versionné ou non est une source de conflits git et de différences entre machines.
- *Défaut proposé* : Valeurs par défaut dans le code, surchargeables par variables `EDT_*` puis par arguments CLI ; pas de fichier de config en V1.

**14.24** ⚪ Faut-il des journaux dans un fichier pour diagnostiquer un échec après coup, ou la console suffit-elle ?
- *Pourquoi* : Sans journal persistant, un échec du solveur pendant la soutenance ne peut pas être expliqué ni reproduit.
- *Défaut proposé* : Module `logging` standard, console + fichier `logs/edt.log` ignoré par git, niveau INFO, `--debug` pour DEBUG.

**14.25** ⚪ L'utilisateur doit-il pouvoir gérer plusieurs « projets » (une base par semestre ou par année, création, ouverture, duplication), ou une seule base SQLite suffit-elle ?
- *Pourquoi* : Le multi-projets ajoute un écran « Ouvrir un projet » et un état global de fichier courant dans toute l'interface.
- *Défaut proposé* : Une seule base en V1, avec « Exporter la base » et « Importer une base » dans les Paramètres ; le multi-projets est classé optionnel.

**14.26** ⚪ Quel IDE utilisent les membres (VS Code, PyCharm) et faut-il fournir une configuration partagée alors que `.vscode/` est ignoré ?
- *Pourquoi* : Sans réglage commun de l'interpréteur et de ruff, chaque membre reformate différemment et produit des diffs parasites.
- *Défaut proposé* : `.vscode/` reste ignoré, mais `docs/installation.md` décrit les réglages recommandés (venv, ruff, pytest) pour VS Code et PyCharm.

**14.27** ⚪ Quel minimum de sécurité web attend-on d'une application locale sans authentification : jeton CSRF sur les formulaires, taille maximale des fichiers téléversés, vérification du type et du nom de fichier (pas de chemin dans le nom), échappement systématique dans les gabarits, en-têtes de sécurité ?
- *Pourquoi* : Même en local, un jury peut poser la question, un fichier de 500 Mo téléversé par erreur bloque le serveur, et un nom de fichier contenant « ../ » est un cas de test classique ; ce sont des décisions à prendre avant d'écrire les routes.
- *Défaut proposé* : Auto-échappement Jinja2 activé, limite de 20 Mo par fichier, nom de fichier ignoré (fichier temporaire nommé par l'application), CSRF non implémenté tant que le serveur écoute sur 127.0.0.1, choix justifié dans la spec technique.

**14.28** ⚪ Faut-il une sauvegarde automatique de la base SQLite avant chaque opération destructrice (import « Tout remplacer », réinitialisation, suppression d'une génération), avec un moyen de la restaurer depuis l'interface ?
- *Pourquoi* : C'est le filet de sécurité le moins cher contre une fausse manipulation pendant la démo ou une séance notée, mais il faut décider où vont les copies, combien on en garde et si la restauration est un écran.
- *Défaut proposé* : Copie `data/sauvegardes/edt-AAAAMMJJ-HHMMSS.sqlite` avant chaque opération destructrice, dix copies conservées, restauration par la commande `edt restaurer` et par une liste dans l'écran Paramètres.

**14.30** ⚪ Comment valider les données entrantes (lignes d'import, formulaires) : modèles pydantic (déjà tirés par FastAPI) partagés entre l'import et les formulaires, ou validation manuelle dans les dataclasses du modèle ?
- *Pourquoi* : Deux mécanismes de validation divergent vite (une règle vérifiée à l'import et pas dans le formulaire) ; pydantic centralise les messages d'erreur en français mais introduit une deuxième famille de classes à côté des dataclasses du modèle et dans le diagramme UML.
- *Défaut proposé* : Schémas pydantic dans une couche `validation` utilisée par l'import et par les routes, dataclasses pures dans le modèle métier, conversion explicite entre les deux ; le diagramme UML ne montre que le modèle métier.

**14.31** ⚪ Quelle licence pour le dépôt (MIT, aucune, tous droits réservés), et faut-il un numéro de version affiché dans l'interface (`edt --version`, pied de page) avec un CHANGELOG.md tenu à chaque jalon ?
- *Pourquoi* : Un dépôt public sans licence est juridiquement « tous droits réservés » ; un numéro de version visible permet de dire en soutenance quelle version a été testée, et le CHANGELOG raconte l'avancement au professeur sans qu'il lise l'historique git.
- *Défaut proposé* : Licence MIT, version dans pyproject.toml lue par l'application et affichée en pied de page, CHANGELOG.md mis à jour à chaque tag de jalon.

## 15. Tests, qualité et intégration continue

**15.1** 🟠 Souhaitez-vous une CI GitHub Actions dès le squelette (ruff check, ruff format --check, pytest) sur chaque push et PR, sur quels OS et versions de Python, et doit-elle être un check obligatoire pour fusionner ?
- *Pourquoi* : Le workflow doit être écrit dans le premier commit du squelette ; une matrice multi-OS prouve la compatibilité OR-Tools chez tous et sert de preuve pour les critères Git et tests.
- *Défaut proposé* : `.github/workflows/ci.yml` avec matrice ubuntu / windows / macos et Python 3.11 et 3.12, check requis pour fusionner.

**15.2** 🟠 Qu'entend le professeur par « test unitaire » et « test d'intégration » ? A-t-il donné des exemples ou attendu un framework précis lors des séances du 21/09 ou du 28/09 ?
- *Pourquoi* : Cela structure tests/unit/ et tests/integration/ et le vocabulaire de la documentation.
- *Défaut proposé* : Unitaire = une classe, une fonction ou une contrainte isolée sur une micro-instance ; intégration = chaîne import → BDD → génération → export sur un jeu complet.

**15.3** 🟠 Faut-il tester l'interface web dans un navigateur (Playwright, Selenium) ou seulement les routes HTTP via le client de test du framework ?
- *Pourquoi* : Un test navigateur ajoute une dépendance lourde, l'installation d'un navigateur chez chacun et en CI, et des tests fragiles.
- *Défaut proposé* : TestClient FastAPI + un test de bout en bout « CSV → génération → export » en pytest, pas de Playwright, plus un scénario de recette manuelle décrit dans docs/.

**15.4** 🟠 Les tests doivent-ils vérifier une solution exacte (snapshot) ou seulement qu'aucune contrainte n'est violée ?
- *Pourquoi* : CP-SAT peut renvoyer une solution différente selon le nombre de workers ; les snapshots deviennent instables et la validation par propriétés exige le vérificateur indépendant.
- *Défaut proposé* : Validation par propriétés via le vérificateur indépendant ; dans les tests, `num_workers=1` et `random_seed` fixé.

**15.5** 🟠 Faut-il mesurer la couverture de tests (pytest-cov) et imposer un seuil bloquant en CI ? À quel niveau ?
- *Pourquoi* : Un seuil change la configuration CI et la quantité de tests à écrire pour l'interface web, plus coûteuse à couvrir.
- *Défaut proposé* : Couverture mesurée et publiée dans le résumé CI ; seuil bloquant de 80 % sur le moteur (src/edt hors web), pas de seuil sur web/.

**15.6** 🟠 Quel budget de temps maximal pour la suite de tests en CI, sachant que le solveur CP-SAT peut être long ?
- *Pourquoi* : Cela fixe le time limit du solveur dans les tests et la séparation entre tests rapides et tests lents.
- *Défaut proposé* : Tests unitaires inférieurs à 1 s chacun sur des micro-instances ; tests sur données réelles marqués `slow` avec time limit de 60 s ; suite complète sous 5 min.

**15.7** 🟠 Que faut-il montrer au professeur aux séances jalons du 12/10 (tests unitaires) et du 02/11 (tests d'intégration) : pytest lancé en direct, rapport CI, nombre de tests ?
- *Pourquoi* : Cela fixe un objectif chiffré minimal par jalon et ce que chacun doit avoir poussé la veille.
- *Défaut proposé* : 12/10 : pytest vert avec au moins trois tests par contrainte de la section 2 ; 02/11 : un test bout en bout vert sur le jeu de démo, montré via la CI.

**15.8** 🟠 Le scénario de démo (import → génération → affichage → cas infaisable → export) doit-il exister sous forme d'un test d'intégration rejoué en CI, garantissant que la démo fonctionne la veille de la soutenance ?
- *Pourquoi* : Cela fige le jeu de démo, garantit qu'il est faisable, et impose que chaque écran de la démo ait une route testable.
- *Défaut proposé* : `tests/integration/test_scenario_demo.py` rejoue exactement le scénario de soutenance ; le cas infaisable est scripté à part.

**15.9** 🟠 Faut-il deux tests sur les données réelles : « notre vérificateur accepte l'EDT réel » et « notre solveur produit un EDT valide sur les mêmes entrées » ?
- *Pourquoi* : C'est l'argument le plus convaincant en soutenance, mais il exige que le vérificateur et l'import gèrent tout ce que contient l'EDT réel.
- *Défaut proposé* : Oui, les deux, marqués `slow`, exécutés en CI sur la version pseudonymisée et cités dans la documentation technique.

**15.10** 🟠 Le script `test_database.py` actuel ouvre la vraie base et imprime sans assertion : le convertit-on en vrai test pytest avec base temporaire, et supprime-t-on le script ?
- *Pourquoi* : pytest le collecterait par son nom et ouvrirait la base réelle, ce qui rend la suite non isolée, et un test sans assertion ne compte pas pour le critère « tests ».
- *Défaut proposé* : Remplacé par `tests/unit/test_stockage.py` utilisant `tmp_path`, base créée et détruite par fixture ; script racine supprimé.

**15.11** 🟠 Exige-t-on un test aller-retour sur les données réelles pseudonymisées : import du fichier → export CSV → réimport → même contenu en base (aucune séance, groupe, enseignant ou salle perdu ou dédoublé), et le rapport d'import doit-il comptabiliser exactement lignes lues, séances créées, fusionnées et ignorées avec motif, pour que rien ne disparaisse silencieusement ?
- *Pourquoi* : C'est le test qui détecte un modèle de données qui ne colle pas au fichier réel : un import qui « réussit » en perdant 12 % des lignes produit un emploi du temps trop facile à générer et une comparaison réel / généré fausse, sans qu'aucun test unitaire ne le voie.
- *Défaut proposé* : tests/integration/test_aller_retour.py marqué `slow` ; l'import renvoie un objet Bilan (lues, créées, fusionnées, ignorées par motif) et le test vérifie lues = créées + fusionnées + ignorées ; toute ligne ignorée porte un motif codifié.

**15.12** 🟠 Le test « notre vérificateur accepte l'EDT réel » échouera si le réel contient des violations connues (cours de 1 h 30, samedi après-midi, changement de campus sans battement). Faut-il maintenir une liste d'écarts attendus versionnée (type et nombre de violations tolérées), ne tester que les contraintes « physiques » (salle, enseignant, groupe en double), ou comparer contrainte par contrainte ?
- *Pourquoi* : Sans décision, ce test sera rouge en permanence ou supprimé la veille de la soutenance ; une liste d'écarts attendus transforme au contraire les violations du réel en argument (« notre outil détecte N problèmes dans l'EDT réel »).
- *Défaut proposé* : Le test compare le rapport du vérificateur à un fichier d'écarts attendus versionné (type de contrainte, nombre) et échoue seulement si des violations nouvelles apparaissent ; les contraintes physiques doivent être à zéro.

**15.13** ⚪ AGENTS.md exige par contrainte un test « respectée » et un test « infaisabilité signalée ». Faut-il aussi un test « contrainte désactivée → la violation apparaît » pour prouver que la contrainte agit réellement ?
- *Pourquoi* : Ce troisième test impose que chaque contrainte soit activable individuellement, ce qui change l'API du solveur.
- *Défaut proposé* : Trois tests par contrainte ; le solveur accepte une liste de contraintes actives.

**15.14** ⚪ Les tests doivent-ils pouvoir tourner sans OR-Tools installé (membre ou poste où l'installation échoue) ?
- *Pourquoi* : Cela impose des imports différés et un marqueur pytest pour ignorer les tests solveur quand la bibliothèque manque.
- *Défaut proposé* : Tests modèle, stockage et web sans OR-Tools ; tests solveur marqués `@pytest.mark.solveur` et ignorés si l'import échoue.

**15.15** ⚪ Faut-il un badge CI et un badge de couverture dans le README, et un rapport de tests visible dans le résumé GitHub Actions pour le professeur ?
- *Pourquoi* : C'est la preuve la plus visible du critère « qualité des tests » sans avoir à lancer pytest devant lui.
- *Défaut proposé* : Badge CI dans le README dès le premier workflow, résumé pytest dans le job summary.

**15.16** ⚪ Où et dans quel format stocker les fixtures de test : JSON pour les micro-instances, CSV pour les jeux complets, ou tout en Python ?
- *Pourquoi* : Le format des fixtures doit coïncider avec le format d'import pour que les tests d'intégration exercent réellement l'import.
- *Défaut proposé* : `tests/fixtures/` : micro-instances construites en Python via des fabriques (`faire_salle()`, `faire_seance()`), jeux complets en CSV identiques au format d'import.

**15.17** ⚪ Faut-il imposer des hooks `pre-commit` locaux (ruff format, ruff check) ou laisser la CI seule faire respecter le formatage ?
- *Pourquoi* : Les hooks évitent des allers-retours de CI rouge mais demandent une installation sur chaque poste.
- *Défaut proposé* : `pre-commit` facultatif documenté dans le README ; la CI fait foi.

**15.18** ⚪ Pour chaque contrainte dure, construit-on une instance minimale infaisable nommée (par exemple « deux TP info le lundi 8 h 30 pour une seule salle info »), versionnée en fixture et réutilisée à trois endroits : test unitaire d'infaisabilité, test du vérificateur, et scénario du panneau « Pourquoi ça échoue » en démo ?
- *Pourquoi* : Cela garantit que le diagnostic d'infaisabilité désigne la bonne contrainte pour chaque famille et non un noyau illisible, et c'est le moyen le moins cher de prouver au jury que chaque contrainte agit réellement.
- *Défaut proposé* : Fabrique `instance_infaisable(nom_contrainte)` dans tests/fixtures/infaisables.py, test paramétré qui vérifie que le noyau retourné contient la contrainte attendue, et liste déroulante « Charger un cas infaisable » dans l'écran de lancement en mode démo.

**15.19** ⚪ Les tests `slow` sur données réelles (limite 60 s chacun) tournent-ils sur toute la matrice CI (trois systèmes × deux versions de Python) à chaque PR, ou seulement sur ubuntu / 3.11 avec un job nocturne pour la matrice complète ?
- *Pourquoi* : Avec la matrice complète, chaque PR attendrait 10 à 20 minutes de solveur, ce qui pousse l'équipe à ignorer la CI ou à contourner le check obligatoire ; mais OR-Tools doit quand même être vérifié sur Windows et macOS avant la démo.
- *Défaut proposé* : Tests rapides sur toute la matrice à chaque PR ; tests `slow` et `perf` sur ubuntu / 3.11 uniquement à chaque PR ; matrice complète en job nocturne et lancement manuel avant chaque jalon.

**15.20** ⚪ Faut-il des annotations de type systématiques et une vérification statique (mypy ou pyright) en CI, en mode strict ou non, alors que quatre personnes et quatre assistants écrivent du code en parallèle ?
- *Pourquoi* : Le typage statique attrape à la relecture les erreurs d'interface entre les couches (modèle, solveur, stockage, web) écrites par des personnes différentes, mais un mode strict bloque des débutants sur des erreurs d'annotation.
- *Défaut proposé* : Annotations obligatoires sur les signatures publiques du moteur, mypy en mode non strict en CI (bloquant sur src/edt hors web), pyright facultatif dans l'IDE.

## 16. Organisation d'équipe, Git, documentation et notation

**16.1** 🔴 Qui sont les quatre membres (pseudos GitHub exacts) et quel lot chacun prend-il ? À ce jour, seuls Maki882 et dzzmimou48-cmd ont des commits. Le coéquipier auteur de la base SQLite garde-t-il la responsabilité de la couche stockage pour sa note individuelle ?
- *Pourquoi* : L'arborescence du code doit être découpée pour que chaque membre travaille dans ses propres fichiers sans conflits de fusion et ait des PR à son nom sur un périmètre clair.
- *Défaut proposé* : Quatre lots : (1) modèle de données + SQLite + import/export, confié à dzzmimou48-cmd qui l'a commencé ; (2) modèle CP-SAT et contraintes ; (3) interface web locale ; (4) vérificateur indépendant + tests d'intégration + CI + documentation ; tests portés par chacun sur sa couche.

**16.2** 🔴 La « première version très complète » sera codée par votre assistant depuis votre machine : comment éviter que les trois autres membres n'aient aucun commit significatif à leur nom (8 pts individuels + 3 pts Git) ?
- *Pourquoi* : Cela décide si l'on livre une V1 monolithique ou un squelette (interfaces, classes, tests écrits mais rouges) que chaque membre complète par PR depuis son propre compte.
- *Défaut proposé* : On code un squelette complet avec les tests écrits et marqués xfail, puis une issue par contrainte ou fonctionnalité assignée à un membre ; chacun implémente et pousse depuis sa machine et son compte.

**16.3** 🔴 Le propriétaire du dépôt Maki882 est-il vous ? Les trois autres membres sont-ils déjà collaborateurs avec droit d'écriture, ou faut-il créer une organisation GitHub ?
- *Pourquoi* : Sans droit d'écriture, les membres devront travailler par fork, ce qui complique les PR, la CI et l'attribution des commits.
- *Défaut proposé* : Dépôt personnel de Maki882, trois collaborateurs ajoutés avec le droit « write » avant le 05/10.

**16.4** 🔴 La branche main est-elle réellement protégée sur GitHub (les commits du coéquipier sont arrivés directement sur main) ? Le dépôt est-il public ou privé, sachant que la protection de branche sur un dépôt privé exige GitHub Pro (gratuit via le Student Developer Pack) ?
- *Pourquoi* : AGENTS.md l'annonce mais sans réglage effectif, un push direct peut contourner la CI et la relecture, et les points Git/GitHub en équipe sont en risque.
- *Défaut proposé* : Chaque membre active le Student Developer Pack ; main protégée dès aujourd'hui avec PR obligatoire, une approbation, CI verte requise et push direct interdit y compris pour l'administrateur.

**16.5** 🔴 Comment les quatre lots avancent-ils sans se bloquer mutuellement (le web attend le solveur, le solveur attend le stockage, les tests d'intégration attendent tout) : fige-t-on dès le 05/10 un contrat d'interface (dataclasses du modèle, fonction `generer(donnees, options) -> resultat`, format JSON d'échange) et des fixtures JSON partagées permettant à chaque lot d'être développé et testé seul avec des doublures ?
- *Pourquoi* : Un membre qui ne commite pas ou un lot en retard bloque les trois autres si les dépendances sont en chaîne ; avec un contrat figé, l'interface se construit sur un faux solveur, le solveur sur des fixtures, et le retard d'un lot reste circonscrit à ce lot au lieu de vider les PR de tout le monde.
- *Défaut proposé* : PR « contrat » fusionnée le 05/10 : paquet edt/modele (dataclasses), edt/solveur/interface.py (classe abstraite plus SolveurFactice qui renvoie un placement fixe), fixtures JSON dans tests/fixtures/ ; toute modification du contrat passe par une PR relue par les quatre membres.

**16.6** 🔴 Les trois autres membres ont-ils donné leur accord au plan « Axel et son assistant produisent le squelette complet, chacun complète par PR » ? Comment les embarque-t-on dans une architecture qu'ils n'ont pas écrite : séance de présentation, docs/architecture.md, une première issue « facile » par membre, binôme avec Axel la première semaine ?
- *Pourquoi* : Un squelette imposé sans adhésion se traduit par des membres qui ne savent pas où écrire, des PR qui contournent l'architecture, et un professeur qui constate en séance que trois personnes sur quatre ne maîtrisent pas le code ; c'est le premier risque sur les 8 points individuels.
- *Défaut proposé* : Accord demandé avant d'écrire le squelette, présentation de 30 min à la séance du 05/10, docs/architecture.md avec « où ajouter une contrainte / un écran / une table » en dix lignes, et une issue d'échauffement par membre fusionnable en une séance.

**16.7** 🟠 Le compte dzzmimou48-cmd (adresse gmail) est-il bien l'un des quatre membres, et son `git config user.email` correspond-il à l'adresse de son compte GitHub ?
- *Pourquoi* : Un commit dont l'adresse n'est pas rattachée au compte n'apparaît pas dans l'onglet Contributors, ce qui fausse la note individuelle.
- *Défaut proposé* : Chaque membre vérifie avant le 05/10 que son avatar apparaît sur ses commits GitHub ; la procédure est ajoutée au README.

**16.8** 🟠 Quel est le niveau réel de chaque membre en Python, Git et programmation par contraintes ? Qui peut raisonnablement maintenir le modèle CP-SAT, et qui connaît déjà un framework web pour porter l'interface ?
- *Pourquoi* : Cela fixe la complexité acceptable du code, la taille des issues, et la techno doit être maintenable par la personne qui la porte pendant les séances notées.
- *Défaut proposé* : Niveau intermédiaire en Python, débutant en CP-SAT : chaque contrainte est une classe courte avec docstring, un membre est désigné référent solveur et un autre référent interface.

**16.9** 🟠 Le professeur regarde-t-il l'activité GitHub datée du jour de séance pour la note « travail en séance » ? Faut-il que chacun commite pendant chacune des huit séances ?
- *Pourquoi* : Cela impose de préparer avant chaque lundi un backlog d'issues prêtes à coder pour que personne ne soit bloqué en séance.
- *Défaut proposé* : Avant chaque séance, au moins deux issues assignées par membre, et chacun ouvre au moins une PR pendant la séance.

**16.10** 🟠 Le professeur sait-il que chaque membre utilise un assistant IA (Claude Code, Codex) ? A-t-il fixé des règles (déclaration, interdiction, obligation d'expliquer le code oralement) ?
- *Pourquoi* : Cela détermine si l'on mentionne les assistants dans les spécifications techniques et le niveau de simplicité exigé du code pour que chacun puisse le défendre en soutenance.
- *Défaut proposé* : On mentionne les assistants dans la section « environnement de développement » ; l'auteur d'une PR doit pouvoir expliquer chaque fonction et le relecteur pose une question de compréhension dans la revue.

**16.11** 🟠 Comment attribue-t-on les relecteurs : binômes fixes, rotation, ou au choix ? Le relecteur doit-il être un humain et non l'assistant de l'auteur ?
- *Pourquoi* : Un fichier CODEOWNERS et une règle écrite dans les spécifications d'organisation en dépendent.
- *Défaut proposé* : Binômes croisés fixes (A↔B, C↔D) avec rotation après les vacances ; relecture humaine obligatoire, l'auteur ne fusionne jamais sa propre PR.

**16.12** 🟠 Quelle stratégie de fusion des PR : merge commit, squash ou rebase ?
- *Pourquoi* : Le squash remplace les commits individuels par un seul ; si le professeur compte les commits par membre sur main, la visibilité de chacun diminue.
- *Défaut proposé* : Merge commit (les commits individuels restent visibles), squash désactivé dans les réglages du dépôt.

**16.13** 🟠 Qui rédige chaque livrable : partie 2 des spécifications fonctionnelles (fonctionnalités classées, maquettes), diagramme UML, spécifications techniques et organisation, documentation utilisateur ?
- *Pourquoi* : Chaque document a une échéance différente et doit être relu comme du code ; sans rédacteur nommé, rien n'est prêt à temps.
- *Défaut proposé* : Un rédacteur principal par document, relu par un autre membre via PR ; le référent qualité (lot 4) coordonne.

**16.14** 🟠 Quel format de livret le professeur attend-il (Word, PDF, dépôt Moodle, lien vers le dépôt), quels rendus intermédiaires à chaque séance (documents, lien GitHub, démonstration), et y a-t-il une date de rendu pour les spécifications techniques liée à la séance du 28/09 ?
- *Pourquoi* : Cela fixe la chaîne Markdown → PDF, l'urgence des spécifications techniques et des dates de gel avant chaque séance.
- *Défaut proposé* : Source Markdown dans docs/, export PDF avec pandoc, le .docx original reste la référence de forme ; un tag Git par jalon (v0.1 le 05/10, v0.2 le 12/10, v0.3 le 02/11, v1.0 le 16/11) et les PDF déposés la veille au soir.

**16.15** 🟠 Quel outil pour le diagramme de classes UML : texte versionné (Mermaid, PlantUML) généré ou relu dans les PR, ou outil graphique (draw.io, StarUML) ?
- *Pourquoi* : La règle « le code reflète le diagramme » n'est tenable que si le diagramme se relit dans les PR ; un fichier texte est diffable, un PNG non.
- *Défaut proposé* : Mermaid dans docs/uml.md (rendu natif par GitHub), export PNG pour le livret, règle « toute PR qui change une classe met à jour le diagramme ».

**16.16** 🟠 Un diagramme de classes a-t-il déjà été présenté ou validé lors de la séance du 21/09 ? Si oui, le code doit-il s'y conformer strictement ?
- *Pourquoi* : S'il existe, la V1 doit reprendre ses noms de classes et ses relations ; sinon on produit le diagramme depuis le code.
- *Défaut proposé* : Aucun diagramme validé : on propose un diagramme tiré du modèle de la V1 et on le fait valider par le professeur le 05/10.

**16.17** 🟠 Les maquettes d'écran demandées dans la spécification fonctionnelle (partie 2) sont-elles à produire avant le code (croquis, Excalidraw, Figma) et par qui, ou des captures de l'application réelle suffiront-elles ?
- *Pourquoi* : Des maquettes préalables fixent les écrans avant de coder et servent de contrat entre les quatre membres.
- *Défaut proposé* : Maquettes basse fidélité en Excalidraw (un fichier par écran) versionnées dans docs/maquettes/ avant le 05/10, remplacées par des captures pour la version finale.

**16.18** 🟠 Le professeur a-t-il fixé des exigences chiffrées (nombre de tests, couverture, nombre de PR par membre) ou des interdictions (nombre de bibliothèques, frameworks, code généré, obligation de justifier chaque ajout) ?
- *Pourquoi* : Des seuils imposés priment sur nos défauts et doivent être vérifiés en CI ; une limite sur les bibliothèques remettrait en cause FastAPI ou OR-Tools.
- *Défaut proposé* : Aucun seuil imposé : on applique les nôtres (80 % de couverture moteur, au moins une PR par membre et par séance) et chaque dépendance est justifiée en deux lignes dans la spec technique.

**16.19** 🟠 Quel outil de suivi : GitHub Projects (kanban lié aux issues et PR) ou Trello comme cité par le professeur ?
- *Pourquoi* : GitHub Projects se remplit automatiquement depuis les issues ; Trello demande une double saisie mais est peut-être attendu.
- *Défaut proposé* : GitHub Projects avec colonnes À faire / En cours / En revue / Fait, visible publiquement, cité dans les spécifications techniques à la place de Trello.

**16.20** 🟠 Qui rédige les comptes rendus de réunion, à quelle fréquence, et où sont-ils stockés ?
- *Pourquoi* : Le professeur cite explicitement les comptes rendus comme méthode de suivi ; il faut un emplacement versionné et un modèle.
- *Défaut proposé* : Un compte rendu par séance et par réunion hebdomadaire dans docs/reunions/AAAA-MM-JJ.md, rédigé à tour de rôle, relu en PR, avec décisions et actions assignées.

**16.21** 🟠 Quel est le format de la soutenance du 14/12 : durée, nombre de slides, démo en direct ou vidéo, chaque membre doit-il parler ?
- *Pourquoi* : Cela détermine ce qu'on prépare (script de démo, vidéo de secours, répartition des parties) et le temps à y consacrer après le 23/11.
- *Défaut proposé* : 20 min de présentation + 10 min de questions, démo en direct avec vidéo de secours, chaque membre présente son lot.

**16.22** 🟠 Fixe-t-on une date de gel des fonctionnalités (par exemple le 09/11) après laquelle main n'accepte que des correctifs et de la documentation, avec une branche ou un tag de soutenance figé après la répétition ? Que fait-on des écrans et options non terminés à cette date : retirés du menu, masqués derrière un drapeau, ou laissés visibles avec la mention « à venir » ?
- *Pourquoi* : Un écran à moitié fini visible en démo est un plantage annoncé si le jury clique dessus, et une PR de fonctionnalité fusionnée la veille casse la répétition ; le gel force aussi la rédaction de la documentation utilisateur du 23/11 sur une interface stable dont les captures ne changent plus.
- *Défaut proposé* : Gel le 09/11, tag v1.0 le 16/11, branche release/soutenance créée le 07/12 après répétition et modifiée uniquement avec relecture de deux membres ; tout écran non terminé est retiré du menu, route conservée derrière EDT_EXPERIMENTAL=1.

**16.23** 🟠 Que se passe-t-il concrètement si, à un jalon, un membre n'a aucune PR fusionnée : lot réattribué, découpé en tâches plus petites, binôme imposé pendant la séance suivante, ou signalement au professeur ? Affiche-t-on chaque semaine un tableau des contributions (PR fusionnées et commits sur main par membre) en réunion pour le voir venir ?
- *Pourquoi* : Le membre silencieux est la première cause d'échec des projets à quatre : on s'en aperçoit à la fin, quand son lot est vide et non testable, et la note des autres en pâtit à la soutenance ; une règle écrite à l'avance évite un conflit d'équipe le 16/11 et rend la répartition défendable devant le professeur.
- *Défaut proposé* : Sortie de `git shortlog -sn --since` et liste des PR par membre en ouverture de chaque point hebdomadaire ; à deux semaines sans PR, binôme imposé en séance sur des issues d'une demi-journée ; à un jalon vide, lot réattribué et professeur informé par le membre concerné lui-même.

**16.24** 🟠 La « première version très complète » doit-elle être poussée d'un bloc avant le 05/10 (jour officiel de démarrage des développements), ou publiée par étapes alignées sur les jalons (squelette le 05/10, contraintes et tests unitaires le 12/10, intégration le 02/11) ? Comment éviter que le professeur juge qu'une V1 de plusieurs milliers de lignes n'a pas été faite par quatre étudiants en séance ?
- *Pourquoi* : Arriver le 05/10 avec l'application finie vide les séances notées de leur contenu et rend le « travail pendant les séances » invisible ; un rythme aligné sur les jalons montre une progression crédible et laisse à chacun de vrais commits à faire.
- *Défaut proposé* : Squelette (modèle, interfaces, tests rouges) poussé pour le 05/10 ; le reste est découpé en issues livrées par PR entre le 05/10 et le 16/11 dans l'ordre des jalons ; on annonce au professeur que l'équipe s'aide d'assistants.

**16.25** 🟠 Combien d'heures par semaine chaque membre peut-il réellement consacrer au projet en dehors des séances d'ici le 16/11, et quelles indisponibilités connues (partiels ISEP, autres projets, vacances du 19/10 au 01/11, stage, alternance) réduisent cette capacité ?
- *Pourquoi* : Le périmètre « obligatoire avant la Toussaint, utile avant le 16/11 » n'est réaliste que si la capacité totale est connue ; un membre absent pendant les vacances ou une semaine de partiels en novembre déplace des lots entiers.
- *Défaut proposé* : Hypothèse : 4 h par semaine et par membre hors séances, rien pendant les vacances sauf Axel, semaine de partiels considérée comme perdue ; le Gantt est recalé sur ces chiffres et le classement obligatoire/utile sert de variable d'ajustement.

**16.26** 🟠 Y a-t-il un chef de projet ou un arbitre désigné (vous ?) reconnu par l'équipe et présenté comme tel au professeur, et comment tranche-t-on un désaccord technique ou de périmètre (vote, décision du référent du lot, décision du chef de projet, arbitrage du professeur) ?
- *Pourquoi* : Ce questionnaire produira des dizaines de décisions ; sans règle d'arbitrage, elles se rediscutent à chaque PR, et le professeur attend dans les spécifications d'organisation un rôle par membre.
- *Défaut proposé* : Axel chef de projet et référent architecture, chaque référent de lot tranche dans son lot, désaccord entre lots tranché par le chef de projet et consigné dans docs/decisions.md ; le professeur n'est sollicité que sur le périmètre.

**16.27** 🟠 Quelle date de gel fonctionnel (plus aucune nouvelle fonctionnalité, seulement corrections, documentation et répétition) et quelle date de gel du jeu de démo et des captures d'écran ? Le 16/11 « finalisation » est-il le gel, ou reste-t-il une marge jusqu'au 23/11 ?
- *Pourquoi* : Sans gel, les fonctionnalités « utiles » continuent d'entrer jusqu'à la veille de la soutenance, les captures de la documentation deviennent fausses et la démo tourne sur un code jamais répété.
- *Défaut proposé* : Gel fonctionnel le 16/11 (tag v1.0), gel du jeu de démo et des captures le 23/11, seules les corrections de bugs accompagnées d'un test entrent entre le 16/11 et le 14/12.

**16.28** 🟠 Quelles décisions ont déjà été prises oralement pendant les séances du 14/09, 21/09 et 28/09 (choix du sujet, répartition, promesses faites au professeur, retours sur la spec ou sur les spécifications techniques présentées aujourd'hui) qui ne figurent dans aucun document du dépôt ?
- *Pourquoi* : Le dépôt ne contient aucun compte rendu de séance ; un engagement oral oublié (une fonctionnalité promise, une technologie annoncée) refait surface à la soutenance, et l'assistant ne peut pas le deviner.
- *Défaut proposé* : Axel rédige un compte rendu rétroactif des trois séances dans docs/reunions/ avant le 05/10, relu par les trois autres, qui devient la liste des engagements pris envers le professeur.

**16.29** 🟠 Pour parer au reproche « vous avez juste appelé une bibliothèque », la soutenance doit-elle contenir une explication pédagogique de la modélisation (variables, encodage de chaque contrainte, ce qui se passe sans CP-SAT), et faut-il garder dans le dépôt un solveur naïf par retour arrière servant de comparaison de temps de calcul et de solution de repli ?
- *Pourquoi* : Le cœur algorithmique est délégué à OR-Tools ; le jury peut considérer que la valeur ajoutée de l'équipe est la modélisation et l'interface, à condition qu'elle sache l'expliquer, et une comparaison chiffrée avec un algorithme maison prouve la pertinence du choix.
- *Défaut proposé* : Un slide « comment on modélise » par famille de contrainte, et un solveur de repli par retour arrière limité aux micro-instances, derrière la même interface `Solveur`, avec un tableau de temps de calcul dans la spec technique.

**16.30** 🟠 Sous quelle forme souhaitez-vous répondre à ce questionnaire (fichier docs/reponses.md, tableau, réunion d'équipe), et où consigner ensuite chaque décision : mise à jour de docs/spec.md par PR relue par les quatre, ou journal de décisions séparé (docs/decisions.md, une entrée datée par décision avec l'alternative rejetée) ? Vos réponses engagent-elles l'équipe ou seulement vous ?
- *Pourquoi* : AGENTS.md interdit à un assistant de modifier la spec sur le fond, et des réponses données par un seul membre dans une conversation privée ne sont ni visibles par les autres ni opposables en soutenance ; un journal de décisions est aussi une pièce que le professeur valorise dans la documentation.
- *Défaut proposé* : Réponses dans docs/reponses-questionnaire.md poussées par PR, chaque décision structurante recopiée dans docs/decisions.md (format ADR court), spec.md mise à jour ensuite par une PR relue par les trois autres ; les réponses d'Axel sont provisoires jusqu'à validation en réunion.

**16.31** 🟠 Faut-il fixer un glossaire unique avant d'écrire le squelette (enseignant ou professeur, groupe ou classe, séance ou cours, créneau ou slot, promotion ou niveau, génération ou emploi du temps) valable dans le code, les tables, l'interface, l'UML et le livret, sachant que le coéquipier a déjà nommé ses tables `professeur`, `classe` et `cours` ?
- *Pourquoi* : Quatre membres et quatre assistants produiront quatre vocabulaires ; un terme différent entre l'écran, le diagramme et le livret coûte des points de documentation et complique la relecture ; renommer les tables du coéquipier est aussi un sujet de diplomatie.
- *Défaut proposé* : docs/glossaire.md écrit avant le squelette : enseignant, groupe (promotion comme racine), séance (demande) et placement (résultat), créneau, génération ; les tables du coéquipier sont renommées dans la PR de schéma avec son accord.

**16.32** ⚪ Que se passe-t-il si une PR reste sans relecture plus de 48 h, ou si le relecteur désigné est absent ?
- *Pourquoi* : Une PR bloquée empêche les commits suivants du même membre d'être fusionnés et fausse le rythme visible par le professeur.
- *Défaut proposé* : Relance dans le canal d'équipe après 48 h ; n'importe quel autre membre peut relire ; PR limitées à une contrainte ou un écran pour être rapides à relire.

**16.33** ⚪ Les messages de commit du coéquipier (« Ajout de… ») ne suivent pas la convention à l'impératif d'AGENTS.md. Applique-t-on la règle strictement (hook, vérification en CI) ou avec tolérance ?
- *Pourquoi* : Un hook bloquant peut décourager un débutant ; une tolérance impose de corriger dans la revue.
- *Défaut proposé* : Tolérance : pas de hook bloquant, rappel dans le modèle de PR et correction demandée par le relecteur.

**16.34** ⚪ Chaque PR doit-elle référencer une issue (« Ferme #n ») et crée-t-on des milestones alignés sur les séances (05/10, 12/10, 02/11, 16/11, 23/11) ?
- *Pourquoi* : Cela permet de montrer au professeur un tableau lisible « qui a fait quoi et quand » sans reconstruire l'historique.
- *Défaut proposé* : Une issue par tâche via le modèle existant, cinq milestones datés, chaque PR ferme au moins une issue.

**16.35** ⚪ Sous quelle forme la documentation utilisateur du 23/11 est-elle attendue (PDF avec captures, page « Aide » intégrée à l'application, README), et faut-il des infobulles dans l'interface ?
- *Pourquoi* : Une aide intégrée est une fonctionnalité à coder et tester, et double le travail de rédaction si elle n'est pas générée du même texte ; un PDF impose des captures à refaire à chaque changement d'écran.
- *Défaut proposé* : docs/guide_utilisateur.md avec captures, exporté en PDF ; infobulles courtes sur les champs non évidents et page « Aide » qui affiche ce même Markdown pour ne l'écrire qu'une fois.

**16.36** ⚪ Faut-il des docstrings systématiques et une documentation technique générée (mkdocs, pdoc), ou une documentation technique rédigée à la main suffit-elle ?
- *Pourquoi* : Une doc générée impose une discipline de docstrings vérifiable en CI (règles D de ruff).
- *Défaut proposé* : Docstrings obligatoires sur les classes et fonctions publiques du moteur, vérifiées par ruff ; pas de site généré.

**16.37** ⚪ Les spécifications techniques doivent-elles inclure une section « stratégie de tests » (pyramide, fixtures, CI, couverture) et un tableau « qui a fait quoi » avec liens vers les PR ?
- *Pourquoi* : C'est là que le professeur peut croiser les critères documentation et tests, et cela documente la répartition noir sur blanc pour la soutenance.
- *Défaut proposé* : Sections « Stratégie de tests » et « Contributions » avec liens vers les PR, mises à jour à chaque jalon ; docs/organisation.md liste les lots par membre.

**16.38** ⚪ Quel canal de communication l'équipe utilise-t-elle (WhatsApp, Discord, Teams) et souhaitez-vous y recevoir les notifications GitHub (PR ouvertes, CI rouge) ?
- *Pourquoi* : Un webhook GitHub vers Discord est trivial ; WhatsApp ne le permet pas, ce qui change la façon de demander les relectures.
- *Défaut proposé* : Discord avec un salon #github alimenté par webhook (PR, revues, CI) et un salon #decisions ; WhatsApp seulement pour l'urgent.

**16.39** ⚪ Y a-t-il une réunion d'équipe en dehors des séances de cours ? Fréquence, créneau, présentiel ou visio ?
- *Pourquoi* : Le rythme de relecture et de fusion des PR dépend du moment où l'équipe se synchronise.
- *Défaut proposé* : Point hebdomadaire de 30 min en visio le jeudi soir, plus un point de 10 min en début de chaque séance.

**16.40** ⚪ Le planning / Gantt demandé dans les spécifications d'organisation : qui le tient et avec quel outil ?
- *Pourquoi* : Un Gantt texte (Mermaid) se met à jour en PR ; un Gantt Excel ou GanttProject se versionne mal.
- *Défaut proposé* : Gantt Mermaid dans docs/organisation.md, mis à jour à chaque jalon par le référent qualité.

**16.41** ⚪ Le jury pose-t-il des questions individuelles sur le code (« qui a écrit cette partie, expliquez-la ») ?
- *Pourquoi* : Cela impose que chaque membre maîtrise son lot et que la répartition soit documentée.
- *Défaut proposé* : Oui par hypothèse : chacun répète sa partie et docs/organisation.md fait foi de la répartition.

**16.42** ⚪ Prévoit-on une répétition générale de la soutenance et de la démo sur la machine exacte, avec un jeu de données « garanti faisable » verrouillé par la CI ?
- *Pourquoi* : Cela ajoute une date au planning et garantit que la démo ne tombe pas sur une instance infaisable.
- *Défaut proposé* : Répétition le 07/12 sur le portable de démo, jeu de démo verrouillé par le test d'intégration, cas infaisable scripté à part.

**16.43** ⚪ Le professeur attend-il uniquement un diagramme de classes, ou aussi des diagrammes de cas d'utilisation, de séquence (import → génération → affichage) ou d'états (cycle de vie d'une génération) que l'on pourrait produire à peu de frais en Mermaid ?
- *Pourquoi* : Le livret demande explicitement un diagramme de classes ; un diagramme de cas d'utilisation aide à structurer la partie 2 (fonctionnalités et cas d'usage) et un diagramme de séquence explique l'architecture en soutenance, mais chacun doit rester synchronisé avec le code.
- *Défaut proposé* : Diagramme de classes obligatoire ; un diagramme de cas d'utilisation pour la partie 2 et un diagramme de séquence de la génération dans la spec technique, tous en Mermaid, rien d'autre.

## 17. Compléments issus de la critique

**17.1** 🟠 Avant de coder chaque contrainte, rédige-t-on une fiche d'une page (énoncé, paramètres, exemple valide, exemple violé, cas limites : déjeuner, samedi, TP de 4 h, séance multi-groupes, séance figée) relue par l'équipe, et qui a autorité pour trancher une interprétation : le professeur le 05/10, le directeur ou la scolarité de l'ISEP lors d'un entretien d'une heure, ou l'équipe seule ? Où consigne-t-on les décisions ?
- *Pourquoi* : Les contraintes de la spec sont en prose et chaque membre les comprend différemment ; sans exemples écrits, le solveur et le vérificateur divergent, les tests encodent l'interprétation de leur auteur, et une infaisabilité devient inexplicable ; un entretien avec la scolarité vaut mieux que quatre suppositions et remplace des dizaines de questions de ce questionnaire.
- *Défaut proposé* : Un fichier docs/contraintes/<nom>.md par contrainte servant de docstring et de source des cas de test ; l'équipe tranche, le professeur valide le 05/10, et une liste de questions est jointe à la demande de données adressée au directeur ; journal des décisions dans docs/decisions.md (une ligne par décision avec date et auteur).

## 18. Hébergement et multi-utilisateurs (plus tard)

**18.1** 🔴 L'hébergement est-il visé avant la soutenance du 14/12 (démo en ligne devant le jury) ou seulement après, une fois le projet stabilisé ?
- *Pourquoi* : Si c'est avant, il faut réserver du temps dans un planning déjà serré (fin des développements le 16/11) pour le Dockerfile, le déploiement et le HTTPS, et figer une architecture hébergeable dès la V1.
- *Défaut proposé* : Après la soutenance : la V1 tourne en local, mais est écrite pour être hébergeable sans réécriture (configuration par variables d'environnement, aucun chemin absolu, aucun état hors base).

**18.2** 🔴 Qui utilisera l'instance hébergée : la scolarité de l'ISEP en conditions réelles, seulement l'équipe et le jury pour une démo, ou aussi les étudiants en lecture ?
- *Pourquoi* : Une utilisation réelle par la scolarité impose authentification, sauvegardes testées et conformité RGPD, alors qu'une démo peut se contenter de données anonymisées et d'un accès simplifié.
- *Défaut proposé* : Démo avec données anonymisées, accessible à l'équipe et au jury ; aucune utilisation en production réelle sans accord écrit de l'ISEP.

**18.3** 🔴 Quels rôles devront exister à terme : administrateur scolarité (tout modifier, lancer les générations), enseignant (voir son emploi du temps, saisir ses indisponibilités), étudiant (lecture seule), autre ?
- *Pourquoi* : La liste des rôles fixe le modèle utilisateur et impose de séparer dès la V1 les routes de lecture des routes de modification pour y accrocher des permissions sans tout réécrire.
- *Défaut proposé* : Trois rôles prévus dans le modèle (admin, enseignant, lecteur) ; la V1 locale n'en active qu'un seul, admin, sans écran de connexion.

**18.9** 🟠 Où pensez-vous héberger : PaaS gratuit (Render, Fly.io, Railway), VPS loué (OVH, Hetzner), serveur de l'ISEP via la DSI, ou machine personnelle / Raspberry Pi ?
- *Pourquoi* : Un PaaS a un disque éphémère (un fichier SQLite y est perdu à chaque redéploiement) et coupe les requêtes longues, alors qu'un VPS ou une Raspberry impose de gérer soi-même reverse-proxy, HTTPS et sauvegardes.
- *Défaut proposé* : Une image Docker unique déployable partout, validée sur un PaaS gratuit avec volume persistant ; le serveur ISEP n'est envisagé que si un contact DSI est obtenu avant novembre.

**18.10** 🟠 Le directeur qui fournit les données réelles d'une année passée est-il d'accord pour qu'elles soient copiées sur un serveur extérieur à l'ISEP, ou doivent-elles rester sur vos postes ?
- *Pourquoi* : Si elles doivent rester locales, il faut écrire dès la V1 un script d'anonymisation (noms remplacés, drapeaux d'accessibilité dissociés) qui produit le jeu de données hébergé.
- *Défaut proposé* : Données réelles uniquement en local ; l'instance hébergée reçoit un jeu anonymisé produit par une commande `edt anonymiser`.

**18.11** 🟠 La V1 locale doit-elle déjà avoir un écran de connexion, ou l'accès est-il libre sur localhost avec l'authentification ajoutée seulement à l'hébergement ? Dans les deux cas, chaque route et chaque appel de service (y compris un outil déclenché par le LLM) doit-il déjà porter l'identité et le rôle du demandeur, vérifiés côté serveur ?
- *Pourquoi* : Ajouter l'authentification après coup n'est simple que si chaque route passe dès le départ par une dépendance « utilisateur courant » et si chaque service reçoit un paramètre utilisateur avec rôle, même s'il renvoie un admin fictif en local ; sinon toutes les fonctions sont à réécrire à l'hébergement.
- *Défaut proposé* : Pas d'écran de connexion en V1, mais une dépendance d'authentification présente sur toutes les routes (désactivable par EDT_AUTH=off) et un objet Utilisateur avec rôle dans la signature de chaque service ; en local, un utilisateur admin/scolarité fictif par défaut.

**18.12** 🟠 Pour l'instance hébergée, préférez-vous des comptes locaux (email + mot de passe), une connexion via le compte ISEP (Microsoft/Google, OAuth), ou un simple mot de passe partagé pour la démo ?
- *Pourquoi* : Le SSO impose un enregistrement OAuth auprès de l'ISEP et une bibliothèque OIDC, les comptes locaux imposent hachage et réinitialisation des mots de passe, et le mot de passe partagé ne permet aucun rôle.
- *Défaut proposé* : Comptes locaux avec mots de passe hachés (argon2), compte admin créé par une commande CLI, pas de réinitialisation par email en V1.

**18.14** 🟠 Combien d'utilisateurs simultanés faut-il viser sur l'instance hébergée : une dizaine (scolarité + équipe), ou des centaines (étudiants qui consultent leur emploi du temps) ?
- *Pourquoi* : Une dizaine d'utilisateurs tient sur un seul processus et SQLite, des centaines imposent PostgreSQL, plusieurs workers et une mise en cache des lectures.
- *Défaut proposé* : Moins de 20 utilisateurs simultanés ; les étudiants ne consultent pas via l'application en 2026-2027.

**18.15** 🟠 L'emploi du temps publié doit-il être consultable sans compte (lien public par classe, flux ICS), ou tout doit-il rester derrière une connexion ?
- *Pourquoi* : Une page publique oblige à séparer strictement ce qui est visible de tous (créneaux, salles, matières) des données personnelles (noms complets, indisponibilités, accessibilité).
- *Défaut proposé* : Rien de public en hébergé ; en local tout est ouvert ; les exports par classe sont servis derrière authentification.

**18.18** 🟠 Le passage à PostgreSQL en hébergé est-il acté, ou souhaitez-vous rester sur SQLite (fichier sur volume persistant) tant que le nombre d'utilisateurs reste faible ?
- *Pourquoi* : Cela décide si la CI teste sur les deux moteurs, si on s'interdit les types et fonctions propres à SQLite, et si le déploiement doit provisionner une base externe.
- *Défaut proposé* : SQLite pour la V1 et la démo, code compatible PostgreSQL ; migration seulement si l'usage réel par la scolarité est confirmé.

**18.21** 🟠 Les fichiers importés (CSV/Excel des données du directeur) et les exports (ICS, PDF) doivent-ils être conservés sur le serveur, ou traités à la volée sans stockage ?
- *Pourquoi* : Sur un PaaS le disque est éphémère ; conserver des fichiers impose un volume ou un stockage objet, alors qu'un traitement en mémoire n'a aucune exigence d'infrastructure.
- *Défaut proposé* : Import traité en mémoire puis jeté, exports générés à la demande ; aucun fichier persistant en dehors de la base.

**18.22** 🟠 Voulez-vous un Dockerfile dès la V1 (même si tout le monde lance en local avec Python), ou seulement au moment d'héberger ?
- *Pourquoi* : OR-Tools est une dépendance binaire lourde ; la tester tôt dans une image Linux évite de découvrir des incompatibilités le jour du déploiement et donne un environnement identique aux quatre membres (Windows et autres).
- *Défaut proposé* : Dockerfile minimal (python:3.11-slim + uvicorn) ajouté à la V1 et construit par la CI ; docker-compose seulement quand PostgreSQL arrivera.

**18.23** 🟠 Plusieurs utilisateurs pourront-ils modifier les données ou lancer et appliquer des générations ou des réaménagements en même temps, et que doit-il se passer si deux calculs sont demandés simultanément : verrou ou file d'attente ?
- *Pourquoi* : SQLite supporte mal les écritures concurrentes et deux solveurs en parallèle saturent une petite machine ; il faut un verrou explicite, un état « occupé » exposé par l'API et un numéro de version de l'EDT à prévoir dans le modèle même si un seul utilisateur en V1.
- *Défaut proposé* : Une seule génération à la fois (verrou en base, la seconde est refusée en renvoyant le job en cours) ; modifications concurrentes des données acceptées avec horodatage updated_at ; numéro de version sur l'EDT courant vérifié au moment d'appliquer une proposition, refus si la version a changé entre le calcul et l'application.

**18.24** 🟠 Quelle fréquence et quel lieu de sauvegarde voulez-vous pour la base hébergée, et qui vérifie qu'une restauration fonctionne ?
- *Pourquoi* : Un fichier SQLite copié pendant une écriture est corrompu ; il faut une commande de sauvegarde dédiée (VACUUM INTO) et un emplacement hors du serveur.
- *Défaut proposé* : Commande `edt backup` (dump horodaté) lancée quotidiennement par cron vers un stockage externe, rétention 30 jours, restauration testée une fois avant la soutenance.

**18.25** 🟠 La CI GitHub Actions doit-elle seulement lancer tests et ruff sur chaque PR, ou aussi construire l'image Docker et déployer automatiquement main ?
- *Pourquoi* : Un déploiement automatique exige que main soit toujours déployable et stocke des secrets de déploiement dans GitHub, ce qui change la discipline de fusion des PR.
- *Défaut proposé* : Tests + ruff sur chaque PR, construction de l'image Docker sur main, déploiement déclenché manuellement.

**18.26** ⚪ Quel budget mensuel acceptez-vous pour l'hébergement : 0 € (offres gratuites avec mise en veille et 30 à 60 s de réveil), ou quelques euros pour une instance permanente et une base PostgreSQL managée ?
- *Pourquoi* : Les offres gratuites imposent la mise en veille, un disque éphémère et souvent une base externe, ce qui oriente le choix SQLite/PostgreSQL et la persistance.
- *Défaut proposé* : 0 € : PaaS gratuit avec volume persistant ou base PostgreSQL gratuite, mise en veille acceptée pour la démo.

**18.27** ⚪ Un sous-domaine fourni par la plateforme (ex. edt-isep.onrender.com) suffit-il, ou voulez-vous un nom de domaine propre ?
- *Pourquoi* : Le domaine détermine les hôtes autorisés, les cookies de session et le certificat HTTPS, à mettre en variable d'environnement plutôt qu'en dur.
- *Défaut proposé* : Sous-domaine de la plateforme, avec une variable EDT_ALLOWED_HOSTS pour changer plus tard.

**18.28** ⚪ L'email des professeurs (présent dans le schéma actuel) sert-il à un usage prévu (notifications, connexion), ou peut-on ne stocker que nom et prénom ?
- *Pourquoi* : Chaque champ personnel hébergé ajoute une obligation d'information et de conservation ; l'email n'est utile que si des notifications ou des comptes enseignants sont prévus.
- *Défaut proposé* : Email optionnel, vide dans le jeu anonymisé, conservé dans le schéma pour un futur compte enseignant.

**18.30** ⚪ Doit-on tracer qui a modifié quoi et quand sur toutes les tables (utile en multi-utilisateurs et pour justifier un réaménagement auprès des enseignants) ?
- *Pourquoi* : Ajouter des colonnes d'audit après coup sur toutes les tables demande une migration et une reprise de tout le code d'écriture.
- *Défaut proposé* : Colonnes created_at, updated_at, created_by sur chaque table principale dès la V1 ; pas de journal d'audit complet.

**18.32** ⚪ Le HTTPS sera-t-il fourni par la plateforme d'hébergement, ou devrez-vous l'installer vous-mêmes (Caddy ou nginx + Let's Encrypt sur un VPS) ?
- *Pourquoi* : Derrière un proxy, l'application doit faire confiance aux en-têtes X-Forwarded-* et marquer ses cookies « secure » ; sur un VPS il faut en plus livrer une configuration de reverse-proxy.
- *Défaut proposé* : HTTPS par la plateforme ; l'application écoute en HTTP sur un port configurable (EDT_PORT) et honore X-Forwarded-Proto.

**18.33** ⚪ Suffit-il d'un endpoint /health et de logs sur la sortie standard, ou voulez-vous une supervision (alerte si le site est tombé, remontée d'erreurs type Sentry) ?
- *Pourquoi* : Les plateformes redémarrent l'application selon /health et n'affichent que les logs stdout ; un outil d'alerte ajoute une dépendance et un compte à gérer.
- *Défaut proposé* : /health (vérifie la base) et logs structurés sur stdout, niveau réglable par EDT_LOG_LEVEL ; pas d'outil externe.

**18.34** ⚪ L'application est-elle réservée à l'ISEP, ou doit-elle pouvoir servir plusieurs écoles sur la même instance ?
- *Pourquoi* : Le multi-établissement impose une colonne d'organisation sur chaque table et un filtrage systématique, impossible à ajouter proprement après coup.
- *Défaut proposé* : Mono-établissement ; les campus sont des entités dans la base, pas des locataires.

**18.35** ⚪ Le serveur hébergé tournera en UTC : stocke-t-on les horaires en heure locale Europe/Paris (ce que fait le schéma actuel avec des TEXT), ou en UTC avec conversion ?
- *Pourquoi* : Un mélange d'heures locales et UTC décale les séances d'une heure au changement d'heure d'octobre et casse les exports ICS.
- *Défaut proposé* : Date + heure locale Europe/Paris stockées explicitement, fuseau fixé dans la configuration, conversion uniquement à l'export ICS.

## 19. Réaménagement temporaire en cas d'imprévu

**19.1** 🔴 Quels types d'imprévus doivent être gérés en priorité : absence d'un enseignant, salle indisponible, campus entier fermé, événement réquisitionnant plusieurs salles, sortie scolaire d'une classe, grève de transports, jour férié non prévu ? Pouvez-vous les classer par fréquence réelle à l'ISEP ?
- *Pourquoi* : Chaque type se modélise comme une indisponibilité d'une ressource différente (enseignant, salle, classe, campus) et les types retenus fixent le schéma de la table des imprévus et les cas de test prioritaires.
- *Défaut proposé* : V1 : absence d'enseignant et indisponibilité de salle sur des dates données ; les autres types sont représentés comme des indisponibilités multiples sans code dédié.

**19.3** 🔴 Le réaménagement remplace-t-il l'emploi du temps sur la période, ou est-il une couche de dérogations datées (« séance X, le 12/11, remplacée par… ») au-dessus d'un EDT de référence unique qui redevient la référence après l'imprévu ? Ou préférez-vous une copie complète de l'emploi du temps pour chaque semaine du semestre ?
- *Pourquoi* : Le choix fixe le schéma central : dérogations = table « exception » liée à une séance de référence et une date, ce qui oblige l'API de lecture d'une semaine à fusionner base + exceptions mais permet le retour automatique à la normale et la liste exacte de ce qui a changé ; copies = table « seance_datee » avec un écart calculé par rapport à la référence, ce qui change toutes les requêtes d'affichage et d'export.
- *Défaut proposé* : Séances de référence (semaine type) + table de dérogations datées avec statut (déplacée, salle changée, annulée, rattrapage) liées à un imprévu ; la lecture d'une semaine renvoie la fusion des deux et le détail des écarts.

**19.4** 🔴 L'imprévu lui-même doit-il être une entité en base (type, ressources touchées, dates, motif, statut ouvert/clos) reliée aux dérogations qu'il a produites ?
- *Pourquoi* : Relier chaque dérogation à son imprévu permet le retour à la normale automatique, l'annulation d'un réaménagement en bloc et la traçabilité qu'un futur pilotage par LLM devra exploiter.
- *Défaut proposé* : Oui : table « imprevu » avec un lien 1-N vers les dérogations générées et un statut.

**19.5** 🔴 Faut-il conserver un historique des emplois du temps et des réaménagements (version initiale, réaménagements successifs, qui a réaménagé quoi, quand, pourquoi) avec possibilité d'annuler un réaménagement ou une action déjà appliquée, y compris via le LLM, ou une seule version courante écrasée à chaque génération ?
- *Pourquoi* : Le réaménagement « en dérangeant le moins possible » exige de comparer un avant et un après, ce que la table cours actuelle (définition et placement mélangés) ne permet pas ; un historique impose de ne jamais écraser une séance mais d'ajouter des versions horodatées, ce qui alourdit les écritures en base dès la V1.
- *Défaut proposé* : Table emploi_du_temps (version, statut brouillon/publié/archivé, version parente) et table seance rattachée à une version ; la génération crée toujours une nouvelle version ; chaque dérogation garde sa date de création, son auteur et son imprévu ; instantané de version avant chaque écriture appliquée (interface ou LLM), annulation de la dernière action possible depuis l'interface, et l'annulation d'un imprévu supprime ses dérogations non encore passées.

**19.6** 🔴 Sur quelle fenêtre le réaménagement peut-il reporter des séances : uniquement pendant la période de l'imprévu, la même semaine, les deux semaines suivantes, ou jusqu'à une date limite (fin de semestre) ? Et pour une demande type « le prof X est absent jeudi », le report dans cette fenêtre est-il la réponse par défaut, ou faut-il proposer systématiquement les options annulation et remplacement ?
- *Pourquoi* : La fenêtre fixe la taille du sous-problème envoyé au solveur (une semaine reste interactif, un semestre entier ne l'est plus sans découpage), et chaque option de réponse est un outil avec des paramètres et une fonction objectif différents.
- *Défaut proposé* : Fenêtre par défaut = période de l'imprévu étendue à la fin de la semaine suivante, paramètre modifiable à chaque calcul (formulaire ou requête LLM) ; le report en minimisant les changements est la réponse par défaut, l'annulation n'est signalée que si le report est impossible, jamais de remplacement automatique d'enseignant.

**19.7** 🔴 Comment mesure-t-on « déranger le moins possible » : nombre de séances modifiées, nombre de personnes impactées (effectif de la classe + enseignant), changements de salle, changements d'horaire, ou un score pondéré combinant ces éléments ?
- *Pourquoi* : C'est la fonction objectif du solveur en mode réaménagement, potentiellement un paramètre de l'outil exposé au LLM ; compter des séances ou des personnes produit des solutions très différentes (déplacer un TP de 12 étudiants ou un amphi de 120).
- *Défaut proposé* : Score pondéré par séance modifiée selon le type de changement, multiplié par le nombre de personnes concernées (effectif du groupe + 1 enseignant) ; poids fixes en V1.

**19.8** 🔴 Quelle hiérarchie entre types de changement : un changement de salle sans changer l'horaire coûte-t-il moins qu'un changement d'horaire le même jour, lui-même moins qu'un changement de jour, lui-même moins qu'une annulation ? Pouvez-vous proposer des poids ?
- *Pourquoi* : Chaque type de changement devient un terme distinct de l'objectif avec son coefficient ; sans hiérarchie, le solveur traite un changement de salle comme un déplacement de jour.
- *Défaut proposé* : Salle = 1, horaire même jour = 3, autre jour même semaine = 5, autre semaine = 8, annulation sans rattrapage = 20 (poids par personne concernée).

**19.9** 🔴 Le solveur peut-il déplacer des séances non touchées par l'imprévu pour libérer une place (cascade de 2 ou 3 mouvements), ou seulement les séances directement impactées ?
- *Pourquoi* : Interdire les cascades verrouille toutes les séances non impactées (problème beaucoup plus petit et rapide) mais réduit fortement les chances de trouver une solution sans annulation.
- *Défaut proposé* : Cascades autorisées mais pénalisées par la métrique ; option « séances non impactées verrouillées » disponible pour un calcul rapide.

**19.10** 🔴 Que devient une séance qui ne peut pas être replacée dans la fenêtre : rattrapage obligatoire plus tard, rattrapage si possible, ou annulation définitive marquée « non dispensée » ?
- *Pourquoi* : Le rattrapage obligatoire rend le problème potentiellement infaisable (le solveur doit alors répondre « impossible »), alors qu'un rattrapage optionnel devient un terme pénalisé de l'objectif.
- *Défaut proposé* : Rattrapage souhaité mais optionnel, fortement pénalisé ; une séance non replacée reçoit le statut « à rattraper » et reste visible dans une liste d'attente.

**19.11** 🔴 L'application applique-t-elle le réaménagement automatiquement ou propose-t-elle une solution que le gestionnaire valide ? Qui a le droit de valider ?
- *Pourquoi* : Une proposition à valider impose un état « brouillon » pour les dérogations et un cycle proposer → relire → appliquer, alors que l'application directe écrit immédiatement en base.
- *Défaut proposé* : Proposition en brouillon, application explicite par le gestionnaire ; un seul rôle utilisateur en V1.

**19.12** 🔴 Acceptez-vous que le modèle CP-SAT de la V1 intègre déjà une fonction objectif (nulle en génération initiale) pour préparer la minimisation de perturbation, alors que la spec limite la V1 aux contraintes dures sans préférences ?
- *Pourquoi* : Sans objectif dans le modèle dès le départ, le réaménagement exigera de réécrire le solveur ; avec, le réaménagement n'est qu'un changement d'objectif et de verrouillages sur le même modèle.
- *Défaut proposé* : Oui : le modèle porte un objectif dès la V1, nul en génération initiale, égal au score de perturbation en réaménagement.

**19.13** 🟠 Un imprévu peut-il concerner plusieurs ressources en même temps (ex. : un événement bloque 5 salles et 2 enseignants sur une journée) et doit-il être saisi comme un seul objet ou comme plusieurs indisponibilités indépendantes ?
- *Pourquoi* : Un imprévu composite impose une table de liaison imprévu→ressources et un traitement groupé dans le solveur, alors que des indisponibilités unitaires réutilisent la logique des indisponibilités enseignants déjà prévues dans la spec.
- *Défaut proposé* : Un imprévu = une liste d'indisponibilités (ressource, début, fin) regroupées sous un même identifiant et un même motif.

**19.14** 🟠 Un imprévu qui ne touche qu'un seul groupe de TD/TP (et non toute la promotion) doit-il être géré, sachant que les autres groupes continuent normalement ?
- *Pourquoi* : Cela suppose que la séance datée référence le groupe et non seulement la classe, et que les conflits de créneaux se calculent au niveau du groupe dès la V1.
- *Défaut proposé* : Oui : l'unité d'étudiants dans le modèle est le groupe, une classe étant un ensemble de groupes.

**19.15** 🟠 À quelle granularité un imprévu est-il exprimé : un créneau précis, une demi-journée, une journée entière, ou une plage de dates avec heures de début et de fin ?
- *Pourquoi* : La granularité détermine si l'indisponibilité se stocke en créneaux discrets alignés sur la grille du solveur ou en intervalles datés à convertir avant résolution.
- *Défaut proposé* : Intervalle daté [début, fin] à la minute, converti en ensemble de créneaux de la grille au moment du calcul.

**19.16** 🟠 Quel est le délai de prévenance habituel : l'imprévu est-il connu plusieurs jours avant, la veille, ou le matin même ? Faut-il gérer un imprévu qui commence dans l'heure ?
- *Pourquoi* : Un imprévu de dernière minute impose de verrouiller automatiquement toutes les séances déjà commencées ou passées, donc de comparer chaque séance à une horloge de référence dans le modèle.
- *Défaut proposé* : Toute séance dont le début est antérieur à « maintenant » est verrouillée ; « maintenant » est un paramètre injecté (horloge système par défaut, surchargeable dans les tests).

**19.17** 🟠 Un étudiant, un enseignant et une salle ont-ils le même poids ? Par exemple, déplacer deux classes pour épargner un enseignant est-il acceptable, et l'utilisateur peut-il moduler ces poids dans sa demande (« surtout ne touchez pas aux étudiants ») ?
- *Pourquoi* : Des poids différents par catégorie d'acteur changent les coefficients de l'objectif et la façon d'agréger les impacts dans le résumé présenté à l'utilisateur ; des poids modulables par requête deviennent un paramètre de l'outil de réaménagement.
- *Défaut proposé* : Poids par personne identique ; une salle n'a pas de poids propre, seul son changement compte ; poids fixes en V1, non modulables par requête.

**19.18** 🟠 Un réaménagement peut-il dégrader le confort des personnes non impactées : créer un trou dans la journée d'une classe, allonger une journée jusqu'à 18 h, ajouter un changement de campus ? Ces dégradations doivent-elles être interdites ou seulement pénalisées ?
- *Pourquoi* : Une interdiction est une contrainte dure supplémentaire propre au mode réaménagement ; une pénalité ajoute des termes d'objectif (trous, amplitude, campus) à calculer.
- *Défaut proposé* : Interdiction de tout nouveau changement de campus dans la journée ; trous et amplitude uniquement pénalisés.

**19.19** 🟠 Quand un enseignant est absent, un autre enseignant habilité sur la même matière peut-il reprendre la séance à l'horaire prévu ? Si oui, l'information « qui peut enseigner quoi » sera-t-elle présente dans les données réelles du directeur ?
- *Pourquoi* : Le remplacement impose une relation enseignant↔matière multiple et une variable d'affectation d'enseignant dans le solveur, absente si un cours a un enseignant fixe.
- *Défaut proposé* : Pas de remplacement automatique en V1 : un cours garde son enseignant ; un remplacement est saisi manuellement.

**19.20** 🟠 Le rattrapage peut-il utiliser des créneaux hors grille normale : samedi matin pour un cours non TP, créneau 18 h-20 h, pause déjeuner ?
- *Pourquoi* : Cela ajoute des créneaux « dégradés » dans la grille du solveur, activés uniquement en mode réaménagement, avec un coût spécifique.
- *Défaut proposé* : Non : le rattrapage respecte exactement la même grille et les mêmes contraintes dures que la génération initiale.

**19.21** 🟠 En cas d'imprévu, certaines contraintes peuvent-elles être assouplies temporairement (pause de 15 min réduite, délai inter-campus ramené à 45 min, capacité de salle dépassée de 10 %), ou toutes les contraintes dures restent-elles absolues, y compris quand l'utilisateur demande explicitement de forcer (« force le cours dans cette salle »), par formulaire ou par requête LLM ?
- *Pourquoi* : Le principe « aucune contrainte abandonnée » interdit l'assouplissement ; s'il existe des exceptions en mode urgence, il faut un second jeu de paramètres de contraintes sélectionné selon le mode ; sinon les outils renvoient un résultat « impossible sans violer X » que l'interface et le LLM doivent restituer.
- *Défaut proposé* : Aucun assouplissement ni forçage : mêmes contraintes dures ; si aucune solution, l'application renvoie la liste des contraintes violées et propose la solution avec le moins d'annulations.

**19.22** 🟠 Le réaménagement doit-il recalculer uniquement les classes, enseignants et salles touchés (sous-problème local) ou l'établissement entier peut-il être remis en jeu ?
- *Pourquoi* : Un sous-problème local se construit en extrayant le voisinage de l'imprévu (séances partageant une ressource) et reste interactif ; le problème global réutilise le générateur complet avec un objectif de distance à l'EDT courant.
- *Défaut proposé* : Sous-problème local : séances des ressources impactées + séances partageant une salle ou un créneau avec elles ; le reste est verrouillé.

**19.23** 🟠 Quelle durée maximale est acceptable pour un réaménagement (quelques secondes en interactif, 30 s, 1 min) et pour une génération complète (1 min, 10 min) ?
- *Pourquoi* : La limite dimensionne la machine hébergée (CPU/RAM), se passe au solveur comme paramètre configurable et décide si l'exécution doit être asynchrone ; un réaménagement d'urgence n'a pas la même tolérance qu'une génération annuelle.
- *Défaut proposé* : Réaménagement : 30 s ; génération complète : 10 min ; les deux en variables d'environnement, avec renvoi de la meilleure solution trouvée à l'échéance.

**19.25** 🟠 À la fin de l'imprévu, les séances reviennent-elles automatiquement à l'emploi du temps de référence, et les rattrapages placés après cette date restent-ils en place ?
- *Pourquoi* : Détermine si les dérogations portent une date de fin de validité et si un traitement automatique (ou une action de l'utilisateur) les clôture.
- *Défaut proposé* : Retour automatique : les dérogations sont bornées par la période de l'imprévu ; les rattrapages hors période sont des dérogations valables jusqu'à leur propre date.

**19.26** 🟠 Deux imprévus peuvent-ils être ouverts en même temps sur la même période (ex. : un enseignant absent et une salle fermée la même semaine) ? Le second calcul doit-il respecter les dérogations du premier ou tout reconsidérer ?
- *Pourquoi* : Décide si l'état de départ du solveur est la référence ou l'EDT courant, et si les dérogations d'un imprévu encore ouvert sont verrouillées ou remises en jeu.
- *Défaut proposé* : Le calcul part de l'EDT courant ; les dérogations des imprévus ouverts restent modifiables mais pénalisées comme tout changement.

**19.27** 🟠 Souhaitez-vous plusieurs propositions alternatives (par exemple 3) présentées avec leur score, ou une seule solution optimale ? Si plusieurs, doivent-elles être différentes par nature (l'une minimise les changements de salle, l'autre les changements d'horaire) ?
- *Pourquoi* : Plusieurs propositions imposent soit plusieurs résolutions avec des pondérations différentes, soit une collecte de solutions diverses, ce qui multiplie le temps de calcul, impose une structure « proposition » en base et un score de dérangement comparable dès la V1.
- *Défaut proposé* : Une solution optimale plus une variante « sans cascade » quand elle existe ; l'utilisateur (formulaire ou LLM) peut demander « une autre proposition » qui exclut la précédente.

**19.30** 🟠 Les données réelles de l'année passée contiendront-elles des imprévus réellement survenus et les réaménagements faits à la main, afin de comparer la solution de l'application avec ce que les gestionnaires ont fait ?
- *Pourquoi* : Sans cas réels, la métrique de perturbation sera calibrée à l'aveugle ; avec, on peut écrire des tests d'intégration « imprévu réel → solution au plus aussi perturbante que la solution humaine ».
- *Défaut proposé* : On construira des scénarios synthétiques à partir de l'EDT réel (absence d'un enseignant tiré au hasard, salle fermée une journée).

**19.31** ⚪ Une grève de transports ou une journée « campus fermé » doit-elle être traitée comme une simple indisponibilité (tout est annulé ou reporté) ou comme une contrainte spéciale (regrouper les cours d'une classe sur un seul campus ce jour-là, passer en distanciel) ?
- *Pourquoi* : Une contrainte spéciale par type d'imprévu demande du code dédié dans le solveur, alors qu'une indisponibilité générique réutilise le mécanisme standard sans rien ajouter.
- *Défaut proposé* : Indisponibilité générique du campus ou des salles ; pas de mode distanciel ni de regroupement spécial en V1.

**19.33** ⚪ Quand une classe entière est absente (sortie, voyage), faut-il libérer ses enseignants et ses salles pour d'autres réaménagements de la même période, et rattraper ses cours ensuite ?
- *Pourquoi* : Une classe absente est une indisponibilité de ressource « classe » qui libère implicitement enseignants et salles : le solveur doit savoir que ces créneaux redeviennent disponibles pour d'autres séances.
- *Défaut proposé* : Oui : la classe est indisponible, ses séances passent « à rattraper », enseignants et salles sont libérés sur la période.

**19.34** ⚪ Une séance peut-elle être avancée (placée avant l'imprévu) lorsque celui-ci est connu à l'avance, ou seulement reportée après ?
- *Pourquoi* : Autoriser l'avance élargit le domaine des variables aux créneaux entre « maintenant » et le début de l'imprévu et pose une question métier (supports de cours prêts ?).
- *Défaut proposé* : Report uniquement ; l'avance est une option désactivée par défaut.

**19.36** ⚪ Un rattrapage placé dans un trou existant d'une classe et d'un enseignant est-il toujours acceptable, même s'il produit une journée 8 h 30-18 h pleine ?
- *Pourquoi* : Détermine si des contraintes de confort (amplitude journalière maximale, nombre de cours par jour) doivent exister dès la V1 comme contraintes dures.
- *Défaut proposé* : Acceptable tant que la pause déjeuner et les contraintes dures sont respectées ; pas de plafond d'heures par jour en V1.

**19.37** ⚪ Faut-il tenir un compteur d'heures dues par matière et par classe (séances prévues, dispensées, annulées, rattrapées) consultable dans l'interface ?
- *Pourquoi* : Un compteur impose un statut par séance datée et une vue agrégée, et fournit au futur LLM une donnée exploitable (« il manque 4 h de maths à la classe A »).
- *Défaut proposé* : Oui, calculé à la volée à partir des statuts des séances datées, sans table dédiée.

**19.38** ⚪ À partir de quelle durée une indisponibilité n'est plus un « imprévu » mais une modification durable (congé long, salle en travaux pour un semestre) qui justifie une régénération complète de l'EDT de référence ?
- *Pourquoi* : Cela définit la frontière entre le mode réaménagement (objectif de perturbation minimale) et le mode génération (objectif nul ou préférences), donc deux points d'entrée distincts dans le code.
- *Défaut proposé* : Seuil de 3 semaines : au-delà, l'application propose une régénération de l'EDT de référence à partir de la date.

**19.39** ⚪ Que se passe-t-il si un imprévu est prolongé (l'enseignant absent une semaine de plus) : on étend l'imprévu existant et on recalcule à partir de l'état courant, ou on crée un nouvel imprévu ?
- *Pourquoi* : L'extension impose de recalculer à partir de l'EDT courant (dérogations incluses) et non de la référence, ce qui change l'état de départ passé au solveur.
- *Défaut proposé* : Nouvel imprévu chaîné au précédent ; le calcul part toujours de l'EDT courant appliqué.

**19.40** ⚪ Quand un réaménagement est publié, faut-il notifier les personnes impactées (enseignants, classes) et par quel canal : liste affichée dans l'interface, export, e-mail, mise à jour ICS ? Doit-on garder trace de qui a été notifié ?
- *Pourquoi* : Un envoi d'e-mail impose des adresses personnelles en base, un serveur SMTP ou un service tiers et une file d'envoi asynchrone ; une liste affichée ne demande qu'un calcul de différence entre deux états de l'EDT.
- *Défaut proposé* : Aucune notification en V1 : liste des changements affichée et exportable (CSV) par classe et par enseignant ; l'API expose la liste des séances modifiées par réaménagement pour un futur envoi.

**19.41** ⚪ Sous quelle forme le résumé des changements doit-il être présenté : par séance (« Maths SI2I-A, mardi 10 h 30 : salle B204 → C105 »), par personne impactée, ou les deux ?
- *Pourquoi* : Détermine la structure de l'objet « diff » que la fonction de réaménagement renvoie et que l'interface puis le futur LLM consomment.
- *Défaut proposé* : Diff par séance avec champs avant/après, agrégé ensuite par classe et par enseignant côté interface.

**19.42** ⚪ Souhaitez-vous un mode « et si » : évaluer l'impact d'un imprévu hypothétique (« si le prof X est absent lundi, combien de séances bougent ? ») sans rien appliquer ?
- *Pourquoi* : C'est le même calcul que la proposition mais sans écriture en base ; cela confirme le besoin de séparer strictement le calcul de l'application en base.
- *Défaut proposé* : Oui, obtenu gratuitement si la proposition est un brouillon non appliqué que l'on peut jeter.

## 20. Pilotage par requêtes en langage naturel (LLM)

**20.1** 🔴 La couche de pilotage par langage naturel fait-elle partie de ce qui sera démontré à la soutenance du 14/12/2026, ou est-ce une évolution prévue après ?
- *Pourquoi* : Si c'est avant la soutenance, on code l'agent (appels LLM, outils, UI de chat) dès la V1 ; sinon on se contente de structurer les fonctionnalités en outils typés « prêts à brancher » sans aucun appel LLM.
- *Défaut proposé* : Après la soutenance. En V1, on structure seulement chaque fonctionnalité comme une fonction typée avec schéma JSON, sans dépendance à un fournisseur de LLM.

**20.2** 🔴 Qui tape les requêtes en langage naturel : uniquement la scolarité / l'administration, aussi les enseignants, aussi les étudiants ?
- *Pourquoi* : Détermine les rôles à modéliser et quels outils chaque rôle peut déclencher ; si des étudiants ont accès, aucun outil d'écriture ne doit leur être exposé.
- *Défaut proposé* : Scolarité seule en V1. Les rôles enseignant et étudiant, en lecture seule, viendront avec l'hébergement.

**20.3** 🔴 Pouvez-vous écrire 10 à 15 requêtes réelles, mot pour mot comme un utilisateur les taperait, que l'application devra comprendre (absences, recherche de salle, déplacement de cours, questions sur l'EDT, explications) ?
- *Pourquoi* : Ces exemples deviennent directement le catalogue d'outils à exposer et le jeu de tests de la couche LLM ; sans eux on devine les paramètres des fonctions.
- *Défaut proposé* : On part des quatre exemples déjà cités (absence prof, salle pour 30 personnes, génération du semestre, « pourquoi ce cours à 8h30 ») plus « déplace le TD de maths de SI2I-A à vendredi » et « combien d'heures fait le prof X cette semaine ? ».

**20.4** 🔴 Pour « pourquoi ce cours est à 8h30 ? », attendez-vous une vraie explication issue du solveur (les contraintes qui ont forcé ce placement) ou une reformulation en français d'informations simples (prof disponible seulement ce jour-là, salle occupée ailleurs) ?
- *Pourquoi* : Une vraie explication oblige à faire exporter par le solveur, dès la V1, les contraintes actives sur chaque séance placée ; une reformulation ne demande qu'un outil de lecture des disponibilités.
- *Défaut proposé* : En V1, on stocke pour chaque séance placée la liste des contraintes qui restreignaient ses créneaux possibles ; le LLM se contente de les reformuler.

**20.5** 🔴 Confirmez-vous que toutes les fonctionnalités V1 (génération, recherche de salle, réaménagement, explication, consultation) doivent être des fonctions Python typées avec paramètres explicites (modèles Pydantic ou dataclasses, schéma JSON généré), dans une couche de services sans dépendance à l'interface web, et que les vues web, le futur LLM et l'API hébergée appellent exactement cette même couche plutôt que deux chemins séparés ?
- *Pourquoi* : C'est la condition pour brancher un agent et un client distant plus tard sans réécrire : impose une couche services/ séparée de web/ et du solveur, des tests unitaires par service ; un seul chemin garantit que le LLM ne peut rien faire de plus que l'interface et évite de doubler le code.
- *Défaut proposé* : Oui : module src/edt/services/, une fonction = un outil (par exemple reamenager(imprevu, fenetre, options) → proposition), schéma JSON généré automatiquement depuis les modèles Pydantic ; les vues web et le LLM appellent les mêmes fonctions de service.

**20.6** 🔴 Validez-vous que le « critère précis » produit par le LLM est le même objet `Contrainte` que celui saisi à la main, sous la forme type + cible (professeur, salle, classe, séance) + période + paramètres + dure/souple, sérialisable en JSON ?
- *Pourquoi* : Si le format est unique, le LLM n'est qu'un traducteur vers ce modèle et rien n'est à convertir ; sinon il faut une couche de correspondance à maintenir et tester.
- *Défaut proposé* : Un seul modèle `Contrainte` sérialisable en JSON, utilisé par l'interface, le solveur et le LLM.

**20.7** 🔴 L'application doit-elle rester complète et entièrement utilisable sans connexion Internet ni clé LLM, c'est-à-dire que chaque fonctionnalité (réaménagement, recherche de salle, génération) reste accessible par un formulaire ou un bouton classique, le LLM n'étant qu'un raccourci optionnel ?
- *Pourquoi* : Impose que chaque outil exposé au LLM ait aussi un chemin dans l'interface web (le LLM ne peut jamais être la seule façon de déclencher une action) et garantit que la V1, les tests et la démo de soutenance ne dépendent ni d'un service externe ni d'une clé.
- *Défaut proposé* : Oui : chaque outil est aussi une action de l'interface web ; le LLM est une extension optionnelle activée uniquement si la clé est présente, et le champ de requête est masqué sinon.

**20.8** 🔴 Le LLM propose une action et l'humain confirme, ou il exécute directement ? Le comportement est-il le même pour les lectures et pour les écritures ?
- *Pourquoi* : Définit si les outils d'écriture renvoient une proposition de critères et de changements sans l'appliquer, ou appliquent la modification ; une confirmation impose un format de critères lisible et un mode « simulation », une exécution directe exige des garde-fous côté serveur ; change l'interface (bouton « Appliquer ») et le modèle de données (propositions en attente).
- *Défaut proposé* : Lecture exécutée directement ; écriture = le LLM produit un objet de critères JSON (même schéma que le formulaire classique) et une proposition affichée sous forme de liste de changements, exécutée seulement après clic sur « Appliquer », avec un bouton « Refuser ».

**20.9** 🔴 Quelles actions le LLM a-t-il le droit de déclencher : générer l'EDT complet du semestre (opération longue qui remplace l'EDT en cours) ? réaménager sur une période ? déplacer une séance précise ? créer ou modifier une contrainte ? modifier les données de référence (ajouter un prof, une salle) ?
- *Pourquoi* : Chaque action autorisée devient un outil à exposer avec son schéma, les actions interdites ne figurent pas dans le catalogue ; autoriser la génération complète impose un outil asynchrone avec suivi de tâche, sinon la couche LLM reste entièrement synchrone.
- *Défaut proposé* : Lecture (EDT d'une entité, salles libres, charge d'un prof), réaménagement temporaire, déplacement d'une séance. Pas de génération complète (lancée uniquement depuis l'interface) ni de modification des données de référence via le LLM en V1.

**20.10** 🔴 Quelles informations peuvent être envoyées au LLM : uniquement la phrase de l'utilisateur et la description du format de critères, les résultats des outils qu'il appelle, ou l'emploi du temps complet avec noms d'enseignants, groupes et besoins d'accessibilité ?
- *Pourquoi* : Envoyer des données personnelles à un service tiers impose une information des personnes et un accord de traitement ; l'EDT complet coûte en plus des tokens à chaque requête et dépasse vite la taille du contexte, alors que par outils il faut plus d'appels mais un coût maîtrisé.
- *Défaut proposé* : Le LLM ne reçoit que la requête, la date du jour, la description des outils et du format de critères, et les seuls résultats des outils qu'il appelle (noms et identifiants nécessaires à la désambiguïsation, jamais de besoin d'accessibilité ni d'e-mail) ; jamais l'EDT complet dans le prompt.

**20.11** 🟠 Si le LLM est dans le périmètre de la soutenance, quelle est la démonstration minimale acceptable (par exemple une seule requête « le prof X est absent jeudi » qui aboutit à une proposition de réaménagement) ?
- *Pourquoi* : Fixe le nombre d'outils à exposer au LLM avant le 16/11 (fin des développements) et évite de disperser l'effort sur des cas non démontrés.
- *Défaut proposé* : Deux cas : une requête de lecture (« quelles salles libres vendredi 14h ? ») et une requête d'action avec confirmation (absence d'un prof sur une journée).

**20.12** 🟠 Parmi ces requêtes, lesquelles sont de la simple consultation et lesquelles modifient l'emploi du temps ?
- *Pourquoi* : Sépare les outils de lecture (exécutés directement) des outils d'écriture (qui passent par une proposition et une confirmation), ce qui change la signature et le comportement de chaque fonction.
- *Défaut proposé* : Tout ce qui change une séance, une salle, un horaire ou une contrainte est une écriture ; le reste est de la lecture.

**20.13** 🟠 Quel fournisseur de LLM souhaitez-vous utiliser : API cloud (Claude/Anthropic, OpenAI, Mistral), un modèle local via Ollama sur le serveur, ou un accès fourni par l'école ? Et faut-il pouvoir en changer facilement (abstraction avec plusieurs implémentations) ou fixe-t-on un fournisseur unique ?
- *Pourquoi* : Change le SDK, le format des appels d'outils, la latence, le coût et la possibilité de fonctionner hors ligne ; un modèle local demande une machine bien plus grosse que l'application elle-même ; l'abstraction coûte un peu de code mais permet un fournisseur factice pour les tests et un basculement vers un modèle local si le budget est nul.
- *Défaut proposé* : API Claude avec appel d'outils natif, derrière une interface abstraite `FournisseurLLM` avec une implémentation réelle et une implémentation factice pour les tests ; un seul fournisseur réel branché.

**20.14** 🟠 Qui paye la clé API (un membre de l'équipe, l'école, des crédits gratuits) et quel budget mensuel maximum acceptez-vous (0 €, 5 €, 20 €) ?
- *Pourquoi* : Une clé d'API est un secret qui doit rester côté serveur avec un plafond de dépense ; un budget nul impose un modèle local, un budget faible impose un petit modèle, un plafond de requêtes codé en dur et l'absence d'envoi de l'EDT complet dans le contexte.
- *Défaut proposé* : Clé personnelle d'un membre avec plafond de 10 €/mois, petit modèle rapide, limite de 50 requêtes par jour codée en dur.

**20.15** 🟠 Le `.gitignore` ignore déjà `.env` : confirmez-vous que la clé sera lue depuis une variable d'environnement (chargée depuis un `.env` local, avec `.env.example` versionné) et que chaque membre utilise sa propre clé en développement ?
- *Pourquoi* : Fixe le module de configuration de l'application et le comportement quand la variable est absente (mode dégradé plutôt qu'erreur au démarrage).
- *Défaut proposé* : Variable `EDT_LLM_API_KEY` lue depuis `.env` ; si absente, la fonctionnalité LLM est désactivée silencieusement et l'interface masque le champ de requête.

**20.16** 🟠 Acceptez-vous une boucle d'agent où le LLM enchaîne plusieurs appels d'outils pour une seule requête (trouver le prof → lister ses séances de jeudi → proposer un réaménagement), avec un plafond d'itérations, ou voulez-vous exactement un appel d'outil par requête ?
- *Pourquoi* : La boucle demande une gestion d'état, un plafond d'appels et un coût par requête variable ; un seul appel oblige à créer des outils composites plus gros.
- *Défaut proposé* : Boucle avec au plus 6 appels d'outils par requête, puis réponse finale obligatoire.

**20.17** 🟠 Quelles opérations sont strictement interdites au LLM même avec confirmation humaine : suppression de données, modification d'une contrainte dure, écrasement de l'EDT publié, modification hors de la période de réaménagement ?
- *Pourquoi* : Ces interdits se codent dans la couche de services (refus systématique), pas dans le prompt, et définissent quels outils n'existent pas du tout.
- *Défaut proposé* : Aucune suppression, aucune modification de contrainte dure, aucune modification en dehors de la période de réaménagement demandée.

**20.18** 🟠 Quand la requête est ambiguë (deux profs nommés Martin, « jeudi » sans date, « la salle habituelle »), le LLM doit-il demander une précision ou choisir par défaut (jeudi prochain, premier résultat) ?
- *Pourquoi* : Demander une précision impose un échange multi-tours minimal et des choix cliquables ; choisir par défaut impose des règles de désambiguïsation écrites dans le code.
- *Défaut proposé* : Il demande, en proposant une liste de choix cliquables ; jamais de choix silencieux pour une écriture.

**20.19** 🟠 Le LLM désigne-t-il les entités par identifiant interne ou par nom (« M. Dupont », « la B204 »), avec résolution nom vers identifiant dans le code et gestion des homonymes et fautes de frappe ?
- *Pourquoi* : Impose des outils de recherche renvoyant des candidats et des outils d'action n'acceptant que des identifiants, pour qu'aucune écriture ne cible une mauvaise entité.
- *Défaut proposé* : Outils `trouver_professeur(nom)`, `trouver_salle(nom)` avec recherche approchée renvoyant des candidats ; les outils d'action n'acceptent que des identifiants.

**20.20** 🟠 Quels critères de recherche de salle doivent exister : capacité minimale, type de salle, équipements, campus, accessibilité, créneau, proximité du cours précédent ?
- *Pourquoi* : Définit les paramètres de l'outil `chercher_salles` et donc les colonnes à ajouter à la table `salle` (équipements, campus, accessible), absentes du schéma SQLite actuel.
- *Défaut proposé* : Capacité, type, liste d'équipements, campus, accessible (oui/non), créneau.

**20.21** 🟠 Comment interpréter les expressions temporelles relatives : « jeudi » = jeudi prochain ou le jeudi de la semaine en cours ? « 14h » = 14:00 ? « semaine prochaine » = lundi à vendredi suivants ? « ce semestre » = quelles dates ?
- *Pourquoi* : Fixe les règles de normalisation en dates ISO dans les schémas d'outils et la date de référence à injecter dans le prompt ; dépend aussi de l'horizon (semaine type ou semestre réel) encore ouvert dans la spec.
- *Défaut proposé* : Le LLM convertit en ISO 8601 avec la date du jour injectée dans le prompt ; « jeudi » = prochain jeudi à partir d'aujourd'hui inclus ; les outils refusent toute date non ISO.

**20.22** 🟠 Quelle latence est acceptable pour une réponse simple du LLM (2 s, 10 s, 30 s), et comment l'afficher quand la requête déclenche en plus le solveur pour un réaménagement ?
- *Pourquoi* : Décide si on affiche la réponse en flux, un indicateur de progression, et comment combiner la latence du modèle avec la limite de temps du solveur en mode réaménagement.
- *Défaut proposé* : 10 s maximum pour une réponse simple ; pour un réaménagement, la limite du solveur (30 s) s'ajoute, avec message d'attente et résultat « meilleure solution trouvée dans le temps imparti ».

**20.23** 🟠 Conversation multi-tours (le LLM garde le contexte : « et vendredi ? », « applique la deuxième option ») ou requête unique indépendante à chaque fois ?
- *Pourquoi* : Multi-tours = stockage de l'historique par utilisateur, gestion de la taille du contexte et des propositions en attente ; requête unique = code beaucoup plus simple.
- *Défaut proposé* : Requête unique en V1, avec au plus un échange de clarification et une proposition en attente à la fois.

**20.24** 🟠 Faut-il journaliser chaque requête (texte saisi, utilisateur, outils appelés avec leurs paramètres, résultat, action appliquée ou non, durée, coût estimé) ? Dans quel support (table SQLite, fichier) et pour combien de temps ?
- *Pourquoi* : Ajoute une table au modèle de données dès la V1 et permet de rejouer les requêtes réelles comme cas de test ; sans journal, impossible d'améliorer le prompt.
- *Défaut proposé* : Table SQLite `journal_llm` avec tous ces champs, conservation illimitée en V1.

**20.25** 🟠 Pour tester la couche LLM sans appeler une API payante en CI GitHub, acceptez-vous un fournisseur factice qui rejoue des réponses enregistrées, plus des tests unitaires sur chaque outil, sans test de « compréhension » réel automatisé ?
- *Pourquoi* : Définit l'architecture de tests : la CI ne dépend d'aucune clé, et la qualité de compréhension est vérifiée à la main ; sinon il faut une clé secrète dans GitHub Actions.
- *Défaut proposé* : `FournisseurLLMFactice` rejouant des scénarios JSON (requête → appels d'outils attendus) ; tests avec la vraie API lancés manuellement uniquement.

**20.26** ⚪ Une fois l'application hébergée, qui détient la clé : une seule clé côté serveur partagée par tous les utilisateurs, ou chaque utilisateur saisit la sienne ?
- *Pourquoi* : Clé serveur = quota partagé et limite par utilisateur à coder ; clé par utilisateur = champ de saisie, stockage chiffré et gestion des erreurs de clé invalide.
- *Défaut proposé* : Une seule clé côté serveur, avec un quota de requêtes par utilisateur et par jour.

**20.27** ⚪ Faut-il se protéger contre des textes malveillants injectés dans les données (nom de cours ou commentaire contenant des instructions pour le LLM), ou est-ce hors sujet pour ce projet scolaire ?
- *Pourquoi* : Décide si on ajoute un nettoyage des données injectées dans le contexte ; dans tous les cas la validation stricte des paramètres d'outils (identifiants existants, dates valides) limite les dégâts.
- *Défaut proposé* : Hors sujet en V1, mais chaque outil valide strictement ses paramètres et les données lues sont présentées au LLM comme des données, jamais comme des instructions.

**20.28** ⚪ L'historique des requêtes doit-il être consultable dans l'interface (page « historique ») ou seulement en base pour les développeurs ?
- *Pourquoi* : Une page d'historique ajoute un écran et une fonction de lecture ; en base seulement, rien à faire côté interface.
- *Défaut proposé* : En base seulement ; une page administrateur simple si le temps le permet.

**20.29** ⚪ Les requêtes sont en français : faut-il aussi accepter l'anglais, et les réponses doivent-elles toujours être en français quelle que soit la langue de la requête ?
- *Pourquoi* : Fixe le prompt système, la langue des descriptions d'outils et les jeux de tests.
- *Défaut proposé* : Français uniquement : prompt système, descriptions d'outils et réponses en français.

**20.30** ⚪ Si multi-tours : quelle durée de vie pour une conversation (perdue au rechargement de la page, session serveur, persistante en base) ?
- *Pourquoi* : Persistante en base = nouvelle table et lien avec l'utilisateur ; session = mémoire serveur uniquement.
- *Défaut proposé* : Mémoire de session serveur, perdue au rechargement.

**20.31** ⚪ Voulez-vous maintenir dans le dépôt un jeu d'évaluation (N requêtes en français → outil et paramètres attendus) avec un seuil de réussite, exécuté avec une vraie clé avant chaque démo ?
- *Pourquoi* : Permet de mesurer les régressions quand on change le prompt ou le modèle ; le seuil décide si une baisse bloque une fusion.
- *Défaut proposé* : Fichier `tests/llm/scenarios.json` avec 20 cas, exécution manuelle, sans seuil bloquant.

**20.32** ⚪ Où se trouve le champ de requête dans l'interface : barre globale en haut, panneau de conversation latéral, page dédiée ?
- *Pourquoi* : Un panneau latéral suppose que les propositions et confirmations s'affichent en superposition de la grille de l'EDT ; une page dédiée est plus simple à coder.
- *Défaut proposé* : Panneau latéral repliable, présent sur toutes les pages, avec la grille de l'EDT visible à côté.

**20.33** ⚪ Le LLM doit-il répondre à des questions statistiques (« combien d'heures fait le prof X cette semaine ? », « quel est le taux d'occupation de la salle B204 ? ») ?
- *Pourquoi* : Chaque type de question ajoute un outil de lecture agrégé et les requêtes SQL correspondantes.
- *Défaut proposé* : Trois outils de lecture en V1 : EDT d'une entité sur une période, salles libres sur un créneau, charge horaire d'un prof ou d'une classe.
