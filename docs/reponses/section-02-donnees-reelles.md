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


## 2\. Données réelles du directeur et confidentialité

**2.1** 🔴 Sous quel format le directeur fournira-t-il les données : export d'un logiciel (CSV, XLSX, ICS) en tableau plat, grille hebdomadaire mise en forme (cellules fusionnées), PDF, captures d'écran ? Pouvez-vous obtenir un extrait dès maintenant pour figer le format et confirmer la grille horaire ?

* *Pourquoi* : Un tableau plat s'importe en une journée, alors qu'une grille visuelle ou un PDF impose un parseur dédié ou une ressaisie manuelle, et le module d'import ne peut pas être écrit sans connaître les colonnes.
* *Défaut proposé* : On développe l'import CSV/XLSX en tableau plat en priorité et on écrit un convertisseur ad hoc dès réception ; si c'est une grille, on redemande un « export liste » au directeur.

Réponse : Nous allons générer des données synthétiques donc tu peux utiliser le format qui te convient le mieux. 



**2.2** 🔴 Les données seront-elles l'emploi du temps réalisé (séances datées avec jour, heure, salle, enseignant, groupe) ou la demande (maquette pédagogique : matières, volumes, enseignants, salles, contraintes) ? Si c'est l'EDT réalisé, acceptez-vous qu'on reconstitue les volumes par agrégation avant de relancer le solveur ?

* *Pourquoi* : Un EDT réalisé est une solution, pas une entrée : il faut un script de désassemblage pour en extraire les volumes et les affectations, et il sert ensuite de référence de comparaison.
* *Défaut proposé* : On suppose un EDT placé de type Hyperplanning/ADE ; un script agrège par (matière, type, groupe, enseignant) en « N séances de 2 h par semaine ».

Réponses : Nous serons uniquement les différentes matières et le but est de de générer l'emploi du temps par l'ia.



**2.3** 🔴 Quel ordre de grandeur attendre des données réelles : nombre de promotions, classes et groupes, d'enseignants, de salles par campus, de campus et de séances par semaine ?

* *Pourquoi* : Cela dimensionne la formulation CP-SAT (une formulation booléenne séance × créneau × salle peut dépasser le million de variables), le budget de temps du solveur, la pagination des listes et les tests de charge.
* *Défaut proposé* : On dimensionne pour 800 séances par semaine, 100 groupes, 200 enseignants, 100 salles et 3 campus, avec un test de charge à cette échelle ; au-delà, on découpe la génération par promotion.

Nous ne connaissons pas encore précisément la quantité de données qui sera fournie. Pour éviter de rendre le projet trop complexe, notre application doit pouvoir gérer plusieurs classes, professeurs, salles et campus, mais la démonstration pourra être réalisée sur une partie représentative des données. À confirmer lorsque nous recevrons les fichiers.



**2.4** 🔴 Les noms des enseignants et les données réelles peuvent-ils apparaître dans le dépôt GitHub Maki882/projet-edt (est-il public ou privé ?), ou faut-il les pseudonymiser (« PROF\_042 ») avant tout commit ?

* *Pourquoi* : Un dépôt public interdit tout commit de données personnelles, même pseudonymisées de façon réversible, et cela conditionne l'existence d'un script d'anonymisation et la séparation données brutes / données committables.
* *Défaut proposé* : On considère le dépôt public : jamais de données brutes committées, dossier data/prive/ ignoré par git, script de pseudonymisation irréversible produisant la fixture versionnée, table de correspondance conservée localement, jeu synthétique pour les tests.

Les données personnelles réelles ne seront pas mises sur GitHub. Les noms des enseignants seront remplacés par des identifiants ou des noms fictifs avant qu'un fichier soit ajouté au dépôt. Les fichiers originaux resteront en dehors du dépôt.



**2.5** 🔴 Les fichiers contiendront-ils des données nominatives d'étudiants (listes de groupes, emplois du temps individuels) ?

* *Pourquoi* : On ne veut ni stocker ni committer d'étudiant identifiable ; le modèle doit rester au niveau du groupe.
* *Défaut proposé* : Aucune table étudiant ; si des listes arrivent, on ne conserve que l'effectif par groupe et on supprime le fichier source.

Nous ne souhaitons pas utiliser de données permettant d'identifier individuellement les étudiants. Notre application travaille principalement avec les classes/groupes et leurs effectifs. Si le fichier contient des noms d'étudiants, ceux-ci ne seront pas conservés.



**2.6** 🔴 Le directeur transmettra-t-il des informations sur des étudiants en situation de handicap (donnée de santé), et à quel niveau : étudiant nommé, groupe, ou simplement « ce cours doit être en salle accessible » ?

* *Pourquoi* : Une donnée de santé impose des règles strictes de traitement ; le plus sûr est de ne pas la recevoir, et cela détermine si le modèle contient une entité étudiant ou un attribut de groupe.
* *Défaut proposé* : On demande explicitement de ne pas la fournir ; le besoin d'accessibilité est un drapeau posé par le planificateur sur un groupe, exercé sur un groupe fictif pour la démo.

Nous ne demanderons aucune donnée médicale ou nominative concernant les étudiants. L'application doit simplement savoir qu'une classe ou un groupe nécessite une salle accessible PMR. Pour la démonstration, nous pouvons créer un groupe fictif ayant cette contrainte. Pour les épreuves nous devons faire un groupe séparé pour les tier temps.



**2.7** 🔴 Vers quelle date les données doivent-elles arriver, qui relance le directeur, et à quelle date bascule-t-on sur le jeu synthétique ?

* *Pourquoi* : Cela fixe la date de bascule pour ne pas bloquer le jalon tests unitaires du 12/10.
* *Défaut proposé* : Si rien n'est reçu au 12/10, on code sur du synthétique et on branche le réel plus tard sans changer l'architecture.


données doivent être demandées au directeur le plus rapidement possible. En attendant leur réception, nous développons avec un jeu de données fictif, afin que le projet ne soit jamais bloqué. Lorsque les données réelles seront reçues, nous adapterons l'import si nécessaire


**2.8** 🔴 Si l'EDT réel viole les contraintes de la spec (cours de 1 h 30, début à 9 h, fin après 18 h, samedi après-midi, changement de campus sans battement), que fait-on : rejeter les lignes, arrondir, assouplir la spec, rendre chaque règle paramétrable ?

* *Pourquoi* : Cela décide si les règles sont codées en dur ou paramétrables et évite que la démo affiche « aucune solution » sur des données réelles ; l'import ne doit pas planter dessus.
* *Défaut proposé* : Import tel quel avec un drapeau « hors grille », rapport d'audit listant les violations, séances inclassables exclues de la génération et jamais arrondies silencieusement ; chaque règle a un paramètre ; l'équipe tranche contrainte par contrainte en réunion sans modifier la spec unilatéralement.


Nous ne modifierons pas automatiquement les données. L'application signalera les séances qui ne respectent pas les contraintes prévues, par exemple un horaire inhabituel ou un changement de campus impossible. Elles seront affichées comme anomalies/conflits afin de permettre à l'utilisateur de les identifier.


**2.9** 🔴 Les assistants IA des quatre membres (Claude Code, Codex) peuvent-ils lire les fichiers réels bruts, sachant qu'ils sont alors transmis aux serveurs de l'éditeur, ou seulement la version pseudonymisée ? Comment l'empêcher concrètement (fichiers hors du dossier du projet, règle dans AGENTS.md, pseudonymisation lancée à la main) ?

* *Pourquoi* : Chaque membre développe avec un assistant qui lit le dossier de travail ; un simple affichage de l'export du directeur envoie des noms d'enseignants à un tiers hors de tout accord, ce qui est difficile à justifier devant le directeur ou le professeur.
* *Défaut proposé* : Fichiers bruts stockés hors du dépôt (EDT\_DATA\_DIR pointant hors du dossier du projet), règle explicite dans AGENTS.md interdisant aux assistants de lire ce dossier, pseudonymisation lancée à la main par un humain, seuls les fichiers pseudonymisés entrent dans le dossier de travail.


Les assistants IA ne doivent travailler que sur les données fictives ou pseudonymisées. Les fichiers originaux fournis par l'ISEP restent hors du dépôt et hors du dossier utilisé par les assistants.


**2.10** 🟠 Quel logiciel gère les emplois du temps à l'ISEP (Hyperplanning, ADE, Aurion, Celcat) et est-ce lui qui produira l'export ? Faut-il que nos vues et exports ressemblent à ce que les étudiants ont l'habitude de voir ?

* *Pourquoi* : Les colonnes, libellés et conventions dépendent du logiciel source (un adaptateur par source), et imiter un format connu guide la mise en page.
* *Défaut proposé* : On suppose un export Hyperplanning ; un adaptateur = un module Python isolé ; format d'affichage propre, inspiré de l'affichage habituel des étudiants.


Nous ne devons pas supposer qu'il s'agit d'Hyperplanning tant que nous n'avons pas vu l'export. Notre système d'import sera conçu de manière à pouvoir s'adapter au format reçu. L'affichage de notre application restera simple et proche d'un emploi du temps classique.


**2.11** 🟠 Confirmez-vous le double usage de l'EDT réel : dériver les besoins à placer, puis servir de référence pour comparer la sortie du solveur et valider notre vérificateur ?

* *Pourquoi* : Cela implique deux tables distinctes (besoins et seance\_reference), un module de comparaison et deux tests d'intégration sur données réelles.
* *Défaut proposé* : Oui : table seance\_reference en lecture seule alimentée uniquement par l'import, vérificateur commun appliqué aux deux EDT.


Réponse : Oui. L'emploi du temps réel pourra servir à deux choses : récupérer les besoins nécessaires à la génération et disposer d'un EDT de référence permettant de comparer les résultats de notre générateur.


**2.12** 🟠 Quel périmètre couvriront les données : une promotion ou toute l'école, une semaine ou un semestre, un ou deux campus ? Contiendront-elles les salles avec capacité, les effectifs et les disponibilités des enseignants, ou seulement les séances ?

* *Pourquoi* : Ce qui manque devra être saisi à la main ou inventé, et la démo doit être planifiée en conséquence.
* *Défaut proposé* : Minimum demandé : liste des salles avec capacité et campus, séances d'un semestre pour une ou deux promotions ; le reste est saisi dans l'interface.


Réponse : À confirmer avec le directeur. Dans l'idéal, nous souhaitons obtenir au minimum les séances, les groupes/classes, les enseignants, les salles, leur campus et leur capacité. Les informations absentes pourront être complétées manuellement dans l'application.


**2.13** 🟠 Quelle année scolaire et quels semestres seront couverts (2024-2025 ou 2025-2026, S1 seul ou année complète) ?

* *Pourquoi* : Le calendrier (vacances, fériés) et la nomenclature des groupes changent d'une année à l'autre.
* *Défaut proposé* : 2025-2026 complète, chaque semestre traité séparément.


Il serait préférable d'utiliser les données de l'année scolaire précédente complète, afin de disposer d'un emploi du temps terminé et réellement utilisé. Les semestres pourront être traités séparément par notre application. L'année exacte dépendra des fichiers disponibles auprès du directeur.


**2.14** 🟠 Peut-on demander au directeur un export brut avec toutes les colonnes (identifiants internes, codes modules, types de séance) plutôt qu'un extrait nettoyé, plus un export séparé du référentiel des salles ?

* *Pourquoi* : Les colonnes techniques valent plus qu'un fichier lisible mais appauvri, et le référentiel salles est indispensable aux contraintes de capacité et de type.
* *Défaut proposé* : On envoie au directeur une liste de colonnes souhaitées et un gabarit salles.csv, et on accepte ce qui arrive.


Nous préférons recevoir un export aussi complet que possible, contenant les différentes colonnes disponibles : matière, groupe, professeur, salle, campus, type de cours, horaires, etc. Un fichier séparé décrivant les salles, leurs capacités et leur accessibilité serait également très utile.

**2.15** 🟠 Recevra-t-on un seul fichier pour toute l'école ou un fichier par promotion, par semaine ou par campus ?

* *Pourquoi* : Le multi-fichiers impose une fusion et un dédoublonnage, car une séance commune à deux promotions apparaît dans deux fichiers.
* *Défaut proposé* : Import multi-fichiers avec dédoublonnage sur (date, heure, salle, enseignant).


L'application doit pouvoir accepter plusieurs fichiers, car les données peuvent être séparées par promotion ou période. Lors de l'import, nous devrons éviter les doublons lorsqu'une même séance apparaît plusieurs fois.


**2.16** 🟠 Y a-t-il un accord explicite (mail) du directeur sur l'usage des données : tests, démo en soutenance devant le professeur, partage entre les quatre membres ?

* *Pourquoi* : Cela définit ce qu'on peut projeter le 14/12 et où l'on peut stocker les fichiers.
* *Défaut proposé* : Usage interne à l'équipe, soutenance sur données pseudonymisées, fichiers sur un espace partagé privé.

Nous demanderons au directeur son accord pour utiliser les données dans le cadre du projet. Les données réelles seront réservées au travail de l'équipe. Pour la soutenance ou les fichiers partagés, nous utiliserons uniquement des données pseudonymisées ou fictives.



**2.17** 🟠 Où stocker les fichiers réels bruts (espace partagé d'équipe, dépôt privé séparé, machine d'Axel uniquement) et qui y a accès ?

* *Pourquoi* : Cela définit le chemin de configuration (EDT\_DATA\_DIR) et ce que les tests d'intégration peuvent supposer.
* *Défaut proposé* : Dossier local data/prive/ ignoré par git, copie sur un espace partagé privé, tests d'intégration sur synthétique uniquement.


Les fichiers originaux seront stockés dans un emplacement privé et non synchronisé avec le dépôt GitHub. Ils seront ajoutés au .gitignore s'ils doivent être placés localement dans l'environnement du projet. Seuls les membres du groupe autorisés pourront y accéder.


**2.18** 🟠 Peut-on obtenir, en plus des fichiers, 30 minutes avec la personne qui fait réellement les emplois du temps à l'ISEP (responsable scolarité) pour recueillir ses règles tacites, son ordre de priorité entre contraintes et ce qu'elle attendrait d'un tel outil ? Et que doit-on au directeur en retour (démonstration, rapport des conflits détectés dans son EDT, accès à l'outil) ?

* *Pourquoi* : La spec écrite par l'équipe n'est pas celle de la scolarité ; un entretien évite de coder des contraintes que personne n'applique et d'ignorer celles qui comptent, et le retour d'un utilisateur réel est un argument de soutenance incontestable. Un livrable promis au directeur devient une tâche datée du planning.
* *Défaut proposé* : On demande l'entretien (compte rendu dans docs/reunions/), on promet au directeur une démonstration et le rapport d'audit de son EDT, rien d'autre, et cette tâche est ajoutée au planning avant le 16/11.

Oui, si cela est possible. Un court entretien avec la personne chargée des emplois du temps serait très utile pour connaître les contraintes réellement appliquées, leur ordre de priorité et les problèmes rencontrés actuellement. Nous pourrions ensuite lui présenter notre application et les résultats obtenus.



**2.19** 🟠 L'export de l'année passée contiendra-t-il des séances annulées, déplacées ou remplacées (colonne statut, doublons prévu/réalisé, historique des modifications), et s'agit-il de l'emploi du temps prévu en début de semestre ou de celui réellement réalisé après toutes les modifications ?

* *Pourquoi* : Une séance annulée non filtrée gonfle les volumes reconstitués et crée de faux conflits ; deux versions d'une même séance faussent la référence de comparaison ; et prévu ou réalisé ne racontent pas la même chose sur ce que la scolarité arrive à faire.
* *Défaut proposé* : On demande la version réalisée ; le lecteur reconnaît une colonne statut ou des libellés « annulé / reporté » (expression régulière paramétrable), exclut ces lignes des besoins et les compte dans le rapport d'import ; sans colonne, tout est supposé réalisé.


Nous souhaitons si possible récupérer l'emploi du temps réellement réalisé. Lors de l'import, les séances indiquées comme annulées ne devront pas être comptées comme des besoins. Les séances déplacées devront correspondre à leur position finale. Si aucune information de statut n'existe dans le fichier, nous considérerons les séances présentes comme réalisées.


**2.20** 🟠 Le professeur (et, via le directeur, l'établissement) sont-ils informés que le projet utilise des données réelles de l'ISEP ? Faut-il le mentionner explicitement dans les spécifications techniques et à la soutenance, ou vaut-il mieux ne montrer que le synthétique et garder le réel pour les tests ?

* *Pourquoi* : Un enseignant peut reprocher l'usage de données nominatives dans un projet étudiant s'il l'apprend en soutenance ; annoncé dès le 05/10 avec les mesures prises (pseudonymisation, dépôt sans données, suppression), c'est au contraire un point fort sur la rigueur.
* *Défaut proposé* : On l'annonce au professeur à la séance du 05/10 et dans une section « Données » des spécifications techniques, avec les mesures de protection ; s'il s'y oppose, le réel reste hors dépôt et hors démo et ne sert qu'aux tests locaux.

Oui. Nous pouvons indiquer clairement que le projet est testé à partir de données réelles fournies par l'ISEP, tout en précisant que celles-ci sont protégées et pseudonymisées lorsqu'elles sont utilisées dans le projet ou lors de la démonstration.



**2.21** 🟠 Jusqu'à quand conserve-t-on les fichiers réels bruts et la table de correspondance de pseudonymisation, qui s'assure de leur suppression sur les quatre machines, l'espace partagé et les sauvegardes, et le directeur attend-il une confirmation écrite ?

* *Pourquoi* : Sans date ni responsable, les fichiers restent indéfiniment sur des portables personnels ; une date de suppression convenue avec le directeur clôt proprement l'usage des données et se place dans le planning.
* *Défaut proposé* : Suppression des fichiers bruts et de la table de correspondance dans la semaine suivant la soutenance (au plus tard le 21/12), chaque membre confirme dans le canal d'équipe, le référent qualité envoie un mail de confirmation au directeur ; seule la fixture pseudonymisée irréversible reste dans le dépôt.

Les données brutes seront conservées uniquement pendant la durée nécessaire au projet puis supprimées après la soutenance. Une fois le projet terminé, seules les données fictives ou suffisamment anonymisées nécessaires à la conservation du projet pourront rester.



**2.22** ⚪ Quelles métriques de comparaison entre EDT généré et EDT réel intéressent la démo (conflits détectés dans le réel, pourcentage de séances placées, occupation des salles, trous, changements de campus par jour) ?

* *Pourquoi* : Chaque métrique est du code et des tests supplémentaires, mais c'est un argument fort en soutenance.
* *Défaut proposé* : Quatre métriques : conflits dans le réel, pourcentage de séances placées, taux d'occupation des salles, nombre de changements de campus par jour.


Nous retiendrons quatre indicateurs simples et intéressants : pourcentage des séances correctement placées, nombre de conflits, taux d'utilisation des salles et nombre de changements de campus. On pourra éventuellement ajouter le nombre de trous dans les emplois du temps si nous avons le temps.


**2.25** ⚪ Quel style de pseudonymisation : codes techniques (« PROF\_042 ») ou noms fictifs réalistes générés avec une graine fixe (Faker en français), plus lisibles à l'écran en soutenance ? Accepte-t-on qu'un enseignant reste ré-identifiable par recoupement (seule personne à enseigner telle matière sur tel campus) ?

* *Pourquoi* : Des codes rendent les captures et la démo illisibles ; des faux noms réalistes sont plus crédibles mais peuvent être pris pour de vrais ; et pseudonymiser le nom ne suffit pas si la matière et le campus identifient la personne.
* *Défaut proposé* : Noms fictifs réalistes générés avec Faker et graine fixe, bandeau « données pseudonymisées » dans l'interface en mode démo ; la ré-identification par recoupement est acceptée pour un usage interne et en soutenance, mais les matières rares sont renommées si le dépôt est public.

Pour les démonstrations, nous utiliserons plutôt des noms fictifs afin que l'emploi du temps reste agréable à lire (Mme Martin, M. Bernard, etc.). Une mention indiquera clairement qu'il s'agit de données fictives/pseudonymisées. Les données originales permettant de retrouver l'identité des personnes ne seront jamais publiées.



