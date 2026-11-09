# Banque de tests

Ce document décrit la banque de tests du projet : comment elle est organisée,
comment la lancer en entier ou par lot, les conventions à respecter pour y
ajouter un test, et l'inventaire des cas limites couverts. Il est destiné aux
développeurs de l'équipe ; il se complète à chaque campagne.

## Lancer les tests

```bash
python -m pytest                          # toute la banque (quelques minutes)
python -m pytest -m "not perf"            # sans les essais de charge : ce que lance la CI
python -m pytest -m cas_limite            # la banque des cas limites seule
python -m pytest -m perf                  # les essais de charge (jeux moyen et grand)
python -m pytest tests/solveur            # un lot
python -m pytest tests/socle/test_regles.py -k samedi   # un fichier, un mot du nom
```

Un test porte le marqueur `cas_limite` quand il protège contre un cas limite
ou une panne plausible relevés par une campagne ; sa docstring commence par
« Cas : … Risque : … » et dit ce qui casserait sans lui.

## Organisation

| Dossier | Lot | Ce qu'il couvre |
|---|---|---|
| `tests/socle/` | socle | domaine (règles, paramètres, dépliage, calendrier), base, services transverses, ligne de commande, cycle de vie du serveur |
| `tests/solveur/` | solveur | préparation, modèle CP-SAT, salles, moteur, instanciation et replacement des séances datées, service de génération |
| `tests/verification/` | vérificateur | règles du catalogue et règles personnalisées, couche datée, indicateurs, services de vérification |
| `tests/web_donnees/` | écrans de données | écrans génériques, règles personnalisées, paramètres, jeux, suppression, concurrence |
| `tests/web_edt/` | page Générer et emploi du temps | calculs, suivi, grille, retouches, À traiter, accueil |
| `tests/echange/` | import/export | gabarit, import XLSX et CSV, rapport, exports, feuille des règles |
| `tests/synthese/` | générateur de données | jeux petit, moyen, grand, témoin, règles d'exemple |
| `tests/exploitation/` | exploitation | scripts de déploiement, unité systemd, nginx, commandes d'administration |
| `tests/integration/` | tous (marqueur `integration`) | six parcours de bout en bout : web, ligne de commande, aller-retour des données, règle personnalisée, retouche, jeu fictif |

Les tests d'intégration assemblent les vrais composants (web, services,
solveur, vérificateur, base SQLite temporaire) sans rien simuler ; ils se
lancent par `python -m pytest -m integration` et font partie de la CI. Voir
[tests-integration.md](tests-integration.md).

## Conventions

- Noms en français, qui disent le cas (`test_une_regle_hors_des_heures_d_ouverture_est_refusee`).
- Un test vérifie un comportement observable, jamais une implémentation.
- Déterministe : pas d'horloge, pas de dépendance à l'ordre des tests ni à une
  solution du moteur qui n'est pas forcée (la fabrique de `tests/solveur/fabrique.py`
  permet de forcer un placement).
- Rapide : moins de deux secondes, sinon marqué `perf`.
- Base dans un dossier temporaire par test (fixtures `environnement`, `base`,
  `jeu_minimal` de `tests/conftest.py`) ; jamais la base de travail.
- Un défaut trouvé par un test se corrige dans le même lot ; s'il appartient à
  un autre lot, le test reste en `xfail(strict=True)` avec la raison, jusqu'à la
  correction.

## Inventaire des cas limites

Campagne du 2 novembre 2026 : sept relecteurs indépendants ont relevé 225 cas
(181 non couverts), une synthèse en a retenu 131 répartis par lot ; chaque lot a
écrit les tests ci-dessous et corrigé les défauts qu'ils ont révélés. Les
rapports sont reproduits tels quels, lot par lot : la colonne « État » dit si le
cas était déjà juste (couvert), corrigé pendant la campagne, laissé en `xfail`
vers un autre lot, manuel (procédure dans la documentation du lot) ou écarté.

### Socle (domaine, base, services transverses)

Validation : ruff propre ; pytest tests/socle : 340 réussis, 9 xfail stricts, 1 skip Windows ; suite complète 1329 réussis, 9 xfail, 1 skip, 1 échec arbitré (synthèse : ponctuels hors des dates d'un semestre d'un jour → générateur adapté). 65 tests cas_limite ; nouveaux fichiers tests/socle/test_moteur.py, test_config.py, test_journal.py, test_parametres.py, test_jeux.py ; aides dans tests/socle/conftest.py (lanceur, compter, delai_verrou_court, verrou_ecriture, lecture_seule, corrompre_base).

Défauts corrigés : salles homonymes sur deux campus (un code → tous ses identifiants, code ambigu refusé) ; base corrompue ou verrouillée au démarrage (cycle de vie protégé, 503 « pas une base SQLite ») ; paramètre corrompu réparable (lire_parametres tolerant, anomalies_parametres) ; EDT_DB_URL sans fichier, pilote ou schéma inconnus refusés ; garde commune edt.db.moteur.identifiant_valide (instantané, niveaux, règles) ; règles hors heures d'ouverture refusées par champ ; jours incompatibles signalés (ReglesJours.regles_incompatibles) ; règle sans heures sans exception brute ; préconditions du jeu minimal ; doctor détecte lecture seule et verrou ; SAVEPOINT pysqlite (isolation_level=None) ; DatabaseError → message et code 1 en CLI ; edt.log non ouvrable → ErreurConfiguration, repli console pour doctor ; init-db --reset avec confirmation en terminal (--oui), refus pendant un calcul, sauvegarde avant reset en prod ; purge au-delà de 50 journaux de calcul ; panne après remise à zéro dite ; service des règles (brouillon non objet, modifier annee/code refusés, filtres inconnus, code en majuscules, année mal formée) ; CHECK maxima > 0 ; ecarts_schema compare index et unicités, deux versions actives refusées ; listes par défaut posées dans une base neuve seulement ; ponctuel hors des dates du semestre = problème de dépliage ; variables EDT_* validées ; rotation de journal tolérante ; besoin sans groupe signalé ; jeu sur schéma périmé → --reset ; marqueur d'opération d'un autre processus ; normalisation Unicode NFC des codes et identifiants (domaine/normalisation.py) ; tests du cycle de vie du serveur.
Complément 80e8258 (demande d'intégration) : transaction SQLite IMMEDIATE à la demande pour les écrivains longs (`OPTION_ECRITURE_IMMEDIATE`, `transaction_immediate(session)`, à appeler avant la première requête, sinon `ErreurConfiguration`) ; BEGIN différé par défaut (lecteurs servis sous verrou long) ; utilisée par l'import de données.
Complément 959aa16 : les écrivains longs du socle ouvrent leur transaction en IMMEDIATE : `connexion_immediate(moteur)` (gestionnaire de contexte pour les écrivains hors session), `supprimer_schema`/`creer_schema`/`initialiser_base` (init-db, --reset, remise à zéro), `services/jeux.py::_charger` (première instruction, contrôle « base vide » refait sous le verrou), `jeu_minimal` ; fixture `verrou_ecriture_bref(duree_s)` dans tests/socle/conftest.py.
Points d'attention : ecarts_schema compare désormais index et unicités (une base créée avant un index ajouté sera dite périmée → --reset-base, données synthétiques) ; les listes par défaut ne sont plus reposées dans une base existante ; init-db --reset ne demande confirmation que dans un terminal (le déploiement l'appelle sans TTY).

| Cas | Test (fichier::nom, relatif à tests/socle/) | État |
|---|---|---|
| 0 Règle SALLES, salles homonymes | test_instantane.py::test_regle_salles_avec_salles_homonymes_sur_deux_campus ; test_regles.py::test_des_salles_homonymes_sur_deux_campus_sont_toutes_resolues | corrigé |
| 1 Base corrompue ou verrouillée au démarrage | test_web.py::test_le_serveur_demarre_et_repond_base_indisponible_malgre_une_base_corrompue ; ::test_le_serveur_demarre_malgre_une_base_verrouillee_et_reporte_la_cloture_des_calculs ; ::test_une_route_sur_une_base_corrompue_repond_503_avec_le_remede ; ::test_une_panne_du_pilote_de_base_est_un_503_qui_dit_quoi_faire | corrigé |
| 2 Paramètre corrompu | test_parametres.py::test_un_parametre_corrompu_en_base_reste_reparable_par_l_ecriture_et_la_reinitialisation ; ::test_l_ecran_des_parametres_reste_ouvrable_avec_une_valeur_corrompue | corrigé (écran : web données c32c81c, xfail levé) |
| 3 EDT_DB_URL sans fichier | test_moteur.py::test_une_url_sqlite_sans_fichier_est_refusee | corrigé |
| 4 Identifiant au-delà de 2^63 | test_moteur.py::test_un_identifiant_hors_des_entiers_sqlite_est_invalide ; test_instantane.py::test_un_identifiant_hors_des_entiers_sqlite_est_introuvable ; ::test_une_commande_avec_un_identifiant_hors_bornes_repond_n_existe_pas | corrigé ; xfail vers solveur |
| 5 Règles hors heures d'ouverture | test_regles.py::test_une_regle_hors_des_heures_d_ouverture_est_refusee_avec_le_champ_fautif | corrigé (jours sans créneau : accepté par conception) |
| 6 Intersection vide des jours | test_regles.py::test_des_regles_de_jours_incompatibles_sont_signalees_avec_leurs_codes | corrigé |
| 7 Règle sans début ni fin | test_regles.py::test_une_regle_sans_heures_a_une_phrase_ou_une_erreur_nommee_jamais_une_exception_brute ; test_regles_service.py::test_une_regle_enregistree_sans_heures_reste_lisible | corrigé |
| 8 Préconditions du jeu minimal | test_base.py::test_le_jeu_minimal_refuse_proprement_une_base_avec_regles_ou_sans_semestre_s1 | corrigé |
| 9 Base en lecture seule | test_cli.py::test_doctor_signale_une_base_en_lecture_seule ; test_web.py::test_sante_signale_une_base_en_lecture_seule ; ::test_une_base_en_lecture_seule_ou_un_disque_plein_sont_nommes | corrigé (doctor ; /sante et page 503 : web EDT bbe664d, xfail levés) |
| 10 SAVEPOINT pysqlite | test_moteur.py::test_un_point_de_sauvegarde_libere_reste_annule_par_la_transaction_englobante | corrigé |
| 11 init-db et jeu-minimal sur base inaccessible | test_cli.py::test_une_base_verrouillee_lecture_seule_ou_corrompue_donne_un_message_clair_et_le_code_1 | corrigé |
| 12 edt.log non ouvrable | test_journal.py::test_fichier_journal_non_ouvrable_leve_une_erreur_de_configuration_et_doctor_le_dit ; ::test_dossier_de_journaux_sous_un_fichier_est_une_erreur_de_configuration | corrigé |
| 13 init-db --reset | test_cli.py::test_init_db_reset_exige_une_confirmation_dans_un_terminal_et_aucun_calcul_en_cours ; ::test_init_db_reset_non_interactif_ne_pose_pas_de_question | corrigé |
| 14 HEAD 405 | test_web.py::test_head_repond_comme_get_sans_corps ; ::test_une_methode_non_permise_donne_une_page_405 | corrigé (IntergicielHead, web EDT bbe664d) ; couvert |
| 15 Journaux de calcul | test_journal.py::test_les_journaux_de_calcul_les_plus_anciens_sont_purges | corrigé (disque plein réel : manuel) |
| 16 Panne après remise à zéro | test_jeux.py::test_panne_de_generation_apres_remise_a_zero_est_dite | corrigé |
| 17 Brouillon non objet | test_regles_service.py::test_brouillon_qui_n_est_pas_un_objet_est_une_validation_invalide | corrigé |
| 18 modifier annee/code | test_regles_service.py::test_modifier_refuse_un_changement_d_annee_ou_de_code_et_garde_le_controle_des_doublons | corrigé |
| 19 EDT_DB_URL pilote ou schéma inconnus | test_moteur.py::test_une_url_sans_pilote_ou_de_schema_inconnu_est_une_erreur_de_configuration | corrigé |
| 20 CHECK et maxima | test_base.py::test_contraintes_de_controle_du_modele_et_maxima_positifs | corrigé |
| 21 Versions actives, index | test_instantane.py::test_deux_versions_actives_sont_refusees_et_l_index_absent_est_un_ecart ; test_schema.py::test_un_index_unique_partiel_absent_est_un_ecart ; ::test_une_unicite_absente_ou_en_trop_est_un_ecart | corrigé |
| 22 init-db relancé | test_base.py::test_init_db_relance_ne_recree_pas_un_referentiel_supprime_volontairement | corrigé |
| 23 Ponctuel hors des dates du semestre | test_depliage.py::test_ponctuel_hors_des_dates_du_semestre_est_un_probleme_de_depliage | corrigé (générateur adapté en conséquence) |
| 24 Variables EDT_* | test_config.py::test_variables_d_environnement_invalides_levent_une_erreur_de_configuration ; ::test_valeurs_vides_ou_entourees_d_espaces ; ::test_doctor_signale_une_adresse_d_ecoute_introuvable | corrigé |
| 25 Rotation concurrente des journaux | test_journal.py::test_une_rotation_impossible_ne_perd_ni_ligne_ni_console ; ::test_le_fichier_journal_suit_une_rotation_faite_par_un_autre_processus (skip Windows) | corrigé ; deux processus : manuel |
| 26 Écritures concurrentes | test_moteur.py::test_une_ecriture_attend_la_fin_d_une_transaction_courte ; ::test_sous_un_verrou_tenu_l_ecriture_echoue_apres_le_delai_et_les_lecteurs_sont_servis | couvert |
| 27 Besoin sans groupe | test_depliage.py::test_un_besoin_sans_groupe_est_signale_et_non_deplie ; test_instantane.py::test_un_besoin_sans_groupe_est_signale_dans_l_instantane | corrigé |
| 28 Filtre inconnu | test_regles_service.py::test_filtre_inconnu_est_une_erreur_metier | corrigé |
| 29 Code en minuscules | test_regles_service.py::test_le_code_est_normalise_en_majuscules | corrigé |
| 30 Année mal formée | test_regles_service.py::test_annee_mal_formee_indique_le_format_attendu | corrigé |
| 31 Jeu sur schéma périmé | test_jeux.py::test_jeu_fictif_sur_schema_perime_indique_le_reset | corrigé |
| 32 Commandes de calcul | test_cli.py::test_commandes_de_calcul_avec_un_identifiant_inexistant ; ::test_options_de_niveau_insensibles_a_la_casse_et_aux_espaces | couvert ; xfail vers solveur |
| 33 Opération d'un autre processus | test_jeux.py::test_une_operation_d_un_autre_processus_est_vue_par_son_marqueur ; test_web.py::test_remise_a_zero_par_un_autre_processus_donne_base_occupee | corrigé |
| Erreurs du pilote SQLite (verrou, lecture seule, corruption, disque plein) expliquées avec leur remède | test_moteur.py::test_les_erreurs_du_pilote_sont_expliquees_avec_leur_remede | corrigé (expliquer_erreur_base) |
| Transaction immédiate à la demande : verrou d'écriture pris dès l'ouverture, lecteur servi, appel tardif refusé, politique différée préservée (demande d'intégration pour l'import) | test_moteur.py::test_transaction_immediate_prend_le_verrou_d_ecriture_des_l_ouverture ; ::test_transaction_immediate_apres_une_requete_est_une_erreur_de_configuration ; ::test_une_transaction_qui_lit_ne_bloque_pas_les_autres_ecrivains | corrigé (80e8258) |
| Chargement d'un jeu ou remise à zéro pendant qu'un verrou d'écriture est tenu brièvement : attend et réussit | test_jeux.py::test_un_chargement_attend_un_verrou_d_ecriture_tenu_brievement_et_reussit ; ::test_une_remise_a_zero_attend_un_verrou_d_ecriture_tenu_brievement | corrigé (959aa16) |
| Deux chargements simultanés (deux fils, puis deux transactions sans le verrou du processus) : un seul réussit, l'autre est refusé clairement, jamais « database is locked » | test_jeux.py::test_deux_chargements_simultanes_un_seul_reussit_l_autre_est_refuse_clairement ; ::test_deux_chargements_de_deux_processus_sont_serialises_sous_le_verrou | corrigé (959aa16) |
| Unicode des codes | test_base.py::test_un_code_aux_espaces_invisibles_est_le_meme_code ; ::test_les_codes_et_identifiants_sont_normalises_en_nfc | corrigé |
| Cycle de vie du serveur | test_web.py::test_cycle_de_vie_du_serveur_clot_les_calculs_interrompus_et_journalise ; ::test_cycle_de_vie_sans_base_initialisee_avertit_sans_echouer | couvert |
| Graine ou année invalides avant la remise à zéro (transmis par la synthèse) | test_jeux.py::test_une_graine_ou_une_annee_invalides_sont_refusees_avant_la_remise_a_zero | corrigé |
| decomposer_id_seance aux chiffres non décimaux (transmis par le vérificateur) | test_instantane.py::test_decomposer_un_identifiant_aux_chiffres_non_decimaux_leve_une_erreur_metier | corrigé |
| doctor et dossier generations/ (transmis par le solveur) | test_cli.py::test_doctor_signale_le_dossier_des_journaux_de_calcul_inutilisable | corrigé |
| Cible numérique hors bornes (transmis par le solveur) | test_regles_service.py::test_regle_dont_la_cible_numerique_est_hors_bornes_est_invalide | corrigé |

### Solveur et service de génération

Validation : ruff propre ; pytest tests/solveur : 575 réussis, 5 xfail stricts ; suite complète 1189 réussis, 4 xfail (3 échecs sous charge corrigés à la racine : tests synchronisés sur les événements du moteur, bornes larges, variante perf). Aides : tests/solveur/conftest.py (lanceur, processus_vivant, guetter_journal), Fabrique.verrou(fin=), _calcul_fictif / _calcul_en_cours_d_un_autre_processus.
Défauts transmis, tous corrigés par leurs lots (xfail levés) : socle — doctor et le dossier generations/ ; vérificateur — version_a_verifier hors bornes ; socle/règles — _resoudre_cible hors bornes ; échange — marqueur « import en cours » (`import_en_cours()`).
Compléments d'intégration : 57ed9c4 garde commune `edt.db.moteur.identifiant_valide` (copie du lot retirée), `_semestre` et options `--niveau`/`-d` tolérantes à la casse et aux espaces, xfail du socle levés ; 5052909 `lancer_calcul` refuse un calcul pendant un import (`import_en_cours`, journalisé) ; 832c164 arbitrage du socle : deux besoins sans groupe → calcul refusé en nommant chaque besoin, unité fictive retirée de `modele.py`, codes de sortie par commande dans le test du socle (1 pre-verifier, 2 verifier) ; 28cc0bf puis 13ca7fe : verrou d'écriture pris en tête de transaction au lancement, à la mise à jour des paramètres, à l'enregistrement de la version et de l'état final (deux processus lançant la même seconde), par `transaction_immediate` du socle (quatre points d'appel, sessions ouvertes par le service lui-même). Constat transmis : une transaction qui lit puis écrit pendant une autre écriture échoue aussitôt « database is locked » (règle anti-interblocage de SQLite) → politique « écrivains longs et requêtes d'écriture en transaction immédiate » confiée au socle, au web et à l'échange.
Non implémenté, volontairement : fils CP-SAT bornés par la mémoire (manque la mémoire disponible du cgroup et le coût par fil ; piste : mesurer le RSS par fil sur le VPS puis borner dans resoudre).

| Cas | Test (fichier::nom) | État |
|---|---|---|
| 1 Besoins sans groupe : calcul refusé en nommant chaque besoin (arbitrage du socle, unité fictive retirée) | tests/solveur/test_contraintes.py::test_deux_besoins_sans_groupe_sont_refuses_en_les_nommant | corrigé (832c164) |
| 2 Charges parasites « 0 min disponibles » | tests/solveur/test_preverification.py::test_une_seance_sans_horaire_ne_produit_pas_de_fausse_charge | corrigé (preverification.py) |
| 3 Options sans bornes (NaN, inf, 1e9, 1 000 fils) | test_moteur.py::test_options_invalides_refusees, ::test_options_aux_bornes_acceptees ; test_services.py::test_options_hors_bornes_refusees_par_la_commande, ::test_temps_limite_enregistre_illisible_ne_bloque_pas_le_suivi | corrigé (contrat.py : TEMPS_LIMITE_MAX_S 24 h, NB_WORKERS_MAX 64 ; generation.py lecture robuste) |
| 4 Salle/besoin supprimé pendant le calcul | test_services.py::test_donnees_modifiees_pendant_le_calcul_sont_dites | corrigé (IntegrityError → message clair) |
| 5 Semestre sans besoin → version vide activée | test_services.py::test_semestre_sans_besoin_ne_cree_pas_de_version_vide | corrigé |
| 6 Journal de calcul impossible à ouvrir | test_services.py::test_journal_de_calcul_impossible_donne_la_vraie_cause ; ::test_doctor_signale_le_dossier_des_journaux_de_calcul_inutilisable | corrigé (doctor : socle, xfail levé) |
| 7 Ligne calcul corrompue (JSON mal typés) | test_services.py::test_un_calcul_illisible_est_clos_sans_bloquer_l_application | corrigé (pages / et /calculs en 200) |
| 8 Version enregistrée sans instanciation | test_services.py::test_version_enregistree_sans_instanciation_dit_de_relancer_l_instanciation, ::test_cloture_renseigne_la_duree_et_nomme_la_version_sans_dates | corrigé |
| 9 Calcul pendant chargement / import | test_services.py::test_lancer_un_calcul_pendant_un_chargement_est_refuse ; ::test_lancer_un_calcul_pendant_un_import_est_refuse | corrigé (operation_en_cours ; import_en_cours de l'échange, 5052909) |
| 10 Identifiant hors bornes SQLite | test_services.py::test_identifiant_hors_bornes_est_introuvable ; ::test_version_a_verifier_identifiant_hors_bornes_est_introuvable ; ::test_regle_dont_la_cible_est_hors_bornes_est_invalide | corrigé (garde commune identifiant_valide ; vérificateur et règles corrigés, xfail levés) |
| 11 Séance finissant après minuit | test_contraintes.py::test_seance_finissant_apres_minuit_refusee_meme_sans_horaires | couvert |
| 12 Verrou incohérent / salle inconnue / durée | test_preverification.py::test_verrou_incoherent_ou_contredisant_son_besoin_est_nomme | corrigé |
| 13 Indisponibilité incohérente | test_preverification.py::test_indisponibilite_incoherente_refuse_le_calcul | couvert |
| 14 niveaux_regles sur règle inconnue | tests/solveur/test_regles.py::test_niveau_d_une_regle_inconnue_est_signale | corrigé |
| 15 Toutes séances verrouillées | test_moteur.py::test_toutes_les_seances_verrouillees | couvert |
| 16 Grille vide | test_preverification.py::test_grille_vide_refusee_en_clair | corrigé (HORAIRES ignorée posait une séance un jour sans créneau) |
| 17 Arrêt avant la première solution | test_moteur.py::test_arret_pendant_la_premiere_recherche_rend_la_main_vite | couvert |
| 18 Paramètres extrêmes | test_moteur.py::test_parametres_extremes_restent_valides | couvert |
| 19 Deux processus lancent la même seconde | test_services.py::test_deux_lancements_simultanes_dans_deux_processus (perf) | corrigé (recomptage sous verrou d'écriture) |
| 20 Semestre en milieu de semaine / d'un jour | tests/solveur/test_couche_datee.py::test_semestre_commencant_en_milieu_de_semaine_et_semestre_d_un_jour | couvert |
| 21 Moteur sans salle / salle inconnue | test_services.py::test_solution_sans_salle_ou_a_salle_inconnue_du_moteur_est_refusee | couvert |
| 22 Placement hors bornes à l'instanciation | test_couche_datee.py::test_placement_hors_bornes_refuse_en_nommant_la_seance | corrigé |
| 23 Verrous contradictoires tolérés bout en bout | test_services.py::test_verrous_contradictoires_toleres_de_bout_en_bout | corrigé (conseil « tolérer » dans le message) |
| 24 Clôture « 0 s » | test_services.py::test_cloture_renseigne_la_duree_et_nomme_la_version_sans_dates | corrigé |
| 25 Écriture de l'état final en échec | test_services.py::test_echec_de_l_ecriture_finale_est_retente | corrigé (3 tentatives) |
| 26 Saut d'horloge | test_services.py::test_un_saut_d_horloge_ne_clot_pas_un_calcul_vivant | corrigé (demarre_epoch) |
| Message d'un calcul tué (mémoire), transmis par exploitation | tests/exploitation/test_commandes_exploitation.py::test_le_message_d_un_calcul_interrompu_aide_a_eviter_la_recidive | corrigé, xfail levé |

### Vérificateur

Validation : ruff propre ; pytest tests/verification : 278 réussis, 2 xfail stricts ; -m cas_limite : 25 tests ; suite complète 1193 réussis (2 échecs chronométrés d'autres lots sous charge : tests/socle/test_charge.py 0,361 s pour 0,3 s ; tests/solveur/test_moteur.py 2,01 s pour 2 s).
Aides réutilisables : tests/verification/versions.py (emploi du temps, enregistrer_version), conftest.py (version_id, version_sans_seances_datees, salle_n204_supprimee, salle_n204_orpheline), dates repères dans fabrique.py.
Choix validés : séance hors semestre → JOUR_FERME (comme l'instanciation) ; vérificateur pur tolère une liste de contraintes vide (services la refusent) ; doublons de séance tus seulement si VOLUME vérifiée ; salle inconnue → « salle à définir » nommant la salle disparue, verifier_modification refuse une salle proposée inconnue ; seuil de performance 10 s processeur.
Défauts corrigés (12) : voir tableau. Complément 8e0dd97 : `_version` et `version_a_verifier` utilisent la garde commune `edt.db.moteur.identifiant_valide` (plus de copie locale) ; xfail de `decomposer_id_seance` levé après la correction du socle ; plus aucun xfail dans tests/verification. Défauts transmis : socle — decomposer_id_seance accepte « ² » (isdigit) → isascii() and isdecimal() ; niveaux — règles copiées par le calcul mais introuvables ignorées sans journal (correction confiée au vérificateur lui-même).

| Cas | Test (fichier::nom) | État |
|---|---|---|
| 1. Séance datée hors du semestre | tests/verification/test_datee.py::test_occurrence_hors_du_semestre_signalee ; tests/verification/test_service.py::test_seances_datees_apres_la_fin_d_un_semestre_raccourci | corrigé |
| 2. Doublon de séance (datée, semaine type) | tests/verification/test_datee.py::test_occurrence_dupliquee_signalee_comme_doublon ; tests/verification/test_paires.py::test_identifiant_duplique | corrigé |
| 3. Version sans séance datée | tests/verification/test_service.py::test_version_sans_seance_datee_signalee_explicitement | corrigé |
| 4. Salle supprimée ou orpheline | tests/verification/test_service.py::test_salle_supprimee_apres_le_calcul_degrade_en_salle_a_definir ; ::test_salle_orpheline_nommee_sans_rendre_la_verification_impossible ; tests/verification/test_salles.py::test_salle_inconnue_verifiee_comme_salle_a_definir ; ::test_salles_a_definir_regroupees_par_seance_dans_la_couche_datee | corrigé |
| 5. Boucle de parenté entre groupes | tests/verification/test_dejeuner_libre.py::test_cycle_de_groupes_ne_rend_pas_le_verificateur_muet | corrigé |
| 6. Identifiant « ²-1 » (vérificateur) | tests/verification/test_volume_verrou_maxima.py::test_identifiant_de_seance_illisible_refuse_proprement (3 variantes) | corrigé |
| 6 bis. decomposer_id_seance("²-1") | tests/verification/test_volume_verrou_maxima.py::test_decomposer_un_identifiant_aux_chiffres_non_decimaux_leve_une_erreur_metier | corrigé par le socle (isascii() et isdecimal()), xfail levé |
| 7. Date reçue en datetime | tests/verification/test_datee.py::test_date_sous_forme_de_datetime_refusee | corrigé |
| 8. Nom propre « Le Gall » mis en minuscule | tests/verification/test_niveaux.py::test_prefixe_garde_la_majuscule_d_un_nom_propre | corrigé |
| 9. Jour sans créneau signalé deux fois | tests/verification/test_horaires.py::test_jour_sans_creneau_signale_une_seule_fois | corrigé |
| 10. Période jusqu'à date.max | tests/verification/test_service.py::test_periode_aux_bornes_extremes_du_calendrier | corrigé |
| 11. Semestre de 12 900 séances datées (perf) | tests/verification/test_performance.py::test_un_semestre_entier_de_13000_occurrences_en_moins_de_10_secondes | couvert |
| 12. Règle supprimée ou désactivée après le calcul | tests/verification/test_service_regles.py::test_regle_supprimee_ou_desactivee_apres_le_calcul_ignoree_sans_erreur (2 variantes) | couvert |
| 12 bis. Trace des règles introuvables | tests/verification/test_service_regles.py::test_regles_du_calcul_introuvables_nommees_dans_le_journal | corrigé (services/niveaux.py) |
| 13. Contrainte désactivée au code inconnu | tests/verification/test_service_niveaux.py::test_contrainte_desactivee_inconnue_nommee_dans_l_erreur | corrigé |
| 14. Grille vide ou d'un seul créneau | tests/verification/test_indicateurs.py::test_grille_vide_ou_d_un_seul_creneau | couvert |
| 15. Liste de contraintes vide | tests/verification/test_service.py::test_liste_de_contraintes_vide_refusee ; tests/verification/test_paires.py::test_aucune_contrainte_a_verifier_journalisee | corrigé |
| 16. Semaine réelle à cheval sur deux années | tests/verification/test_datee.py::test_maxima_hebdomadaires_a_cheval_sur_deux_annees_civiles | couvert |
| Identifiant de version ou de semestre hors bornes (2^70) dans une adresse ou la commande | tests/verification/test_service.py::test_identifiant_hors_bornes_de_la_base_introuvable | corrigé (8e0dd97, garde commune identifiant_valide) |

### Écrans de données

Validation : ruff propre ; pytest tests/web_donnees : 145 réussis, 7 xfail stricts ; suite complète 1195 réussis, 7 xfail. 49 tests cas_limite (test_cas_limites.py, test_performance.py perf, et dans test_parametres.py, test_saisies_extremes.py, test_regles.py, test_suppression.py). Aides dans tests/web_donnees/conftest.py : fixtures version_generee, calcul_retenu (calcul retenu jusqu'à liberer()) ; aides depuis, formulaire_de, saisie_salle, saisie_besoin, saisie_regle, texte_visible, NOMBRE_TROP_GRAND, NOMBRE_DE_5000_CHIFFRES. Contre-épreuve : les 16 tests des cas corrigés échouent sur l'ancien code.
Défauts corrigés (10) : suppression refusée pendant un calcul (lien « Suivre le calcul » ; règles personnalisées restent supprimables car copiées au lancement) ; année scolaire 2000-2100 et ≤ 2 ans, semestre ≤ 1 an ; adresse de retour avec caractère de contrôle écartée ; paramètres refusés pendant un calcul (409, saisie gardée) ; HX-Current-URL illisible ignoré et journalisé ; nombre de 5 000 chiffres sans 500 (conversion.entier_naturel ; routage global `{n:int}` limité à 30 chiffres dans gabarits.py ; cible de règle à 20 chiffres) ; textes libres bornés (5 000 caractères, commentaire de règle 2 000) ; fragments sans HTMX redirigés ; règle ou année énorme introuvable ; /regles sans année explique et renvoie au calendrier.
Complément c32c81c : `/parametres` s'ouvre (200) malgré une valeur corrompue en base (lecture tolérante, valeur fautive affichée sous son champ avec la valeur par défaut à sa place, l'enregistrement répare la base) ; xfail du socle levé ; xfail `[calcul_etat]` et `[formulaire_seance]` levés (corrigés par le web EDT).
Défauts transmis au web EDT, tous corrigés par lui (bbe664d, xfail levés) : urlsplit non protégé dans le panneau d'une séance ; seance=h<5000 chiffres> ; formulaire de séance sans HTMX ; niveau_regle_<5000 chiffres> ; /calculs/<n>/etat et /a-traiter/compteur sans HTMX ; motif d'annulation non borné. Marge : Générer à froid sur le jeu grand = 1,65 s CPU pour 2 s admis.

| Cas | Test (fichier::nom) | État |
|---|---|---|
| 1 Suppression d'un besoin ou d'une salle pendant le calcul | test_cas_limites.py::test_suppression_d_un_besoin_ou_d_une_salle_pendant_le_calcul_est_refusee ; ::test_regles_personnalisees_supprimables_pendant_un_calcul | corrigé / couvert |
| 2 Suppression du semestre pendant son calcul | test_cas_limites.py::test_suppression_du_semestre_refusee_pendant_son_calcul | corrigé |
| 3 Durées d'année et de semestre | test_cas_limites.py::test_duree_d_une_annee_bornee, ::test_duree_d_un_semestre_bornee, ::test_calculs_d_un_semestre_sain_sans_erreur | corrigé / couvert |
| 4 Adresse de retour avec saut de ligne | test_cas_limites.py::test_adresse_de_retour_avec_saut_de_ligne_est_ecartee | corrigé |
| 5 Paramètres enregistrés pendant un calcul | test_parametres.py::test_parametres_refuses_pendant_un_calcul | corrigé |
| Valeur de paramètre corrompue en base : écran ouvrable, valeur signalée, réparée à l'enregistrement (transmis par le socle) | test_parametres.py::test_valeur_corrompue_signalee_puis_reparee_depuis_l_ecran | corrigé (c32c81c) |
| 6 HX-Current-URL illisible | test_cas_limites.py::test_hx_current_url_illisible_est_ignore ; ::test_hx_current_url_illisible_dans_le_panneau_d_une_seance | corrigé (panneau : web EDT bbe664d) |
| 7 Nombres de 5 000 chiffres | test_saisies_extremes.py::test_nombres_de_cinq_mille_chiffres_refuses ; ::test_seance_de_cinq_mille_chiffres_dans_l_emploi_du_temps ; ::test_niveau_de_regle_de_cinq_mille_chiffres_au_lancement | corrigé (séance et niveau_regle : web EDT bbe664d) |
| 8 Textes libres non bornés | test_saisies_extremes.py::test_note_de_besoin_bornee_cote_serveur, ::test_commentaire_de_regle_borne_cote_serveur, ::test_motif_d_annulation_borne_cote_serveur | corrigé (motif : web EDT bbe664d) |
| 9 Fragments ouverts sans HTMX | test_cas_limites.py::test_fragments_sans_htmx_redirigent_vers_la_page ; ::test_fragments_des_autres_ecrans_sans_htmx | corrigé ; couvert (/calculs/verifications) ; compteur, calcul_etat, formulaire_seance : web EDT bbe664d |
| 10 Identifiants énormes des règles | test_regles.py::test_identifiants_enormes_des_regles_sont_introuvables | corrigé |
| 11 Règles sans année scolaire | test_regles.py::test_regles_sans_annee_scolaire_affiche_un_bandeau | corrigé |
| 12 Rejeu d'une suppression et d'une annulation | test_suppression.py::test_rejeu_d_une_suppression_et_d_une_annulation | couvert |
| 13 Échappement des champs libres | test_cas_limites.py::test_champs_libres_echappes_sur_les_pages_de_donnees, ::test_champs_libres_echappes_sur_les_pages_de_l_emploi_du_temps | couvert |
| 14 Pages du jeu grand sous deux secondes CPU, export sous dix | test_performance.py::test_pages_du_jeu_grand_sous_deux_secondes, ::test_export_des_donnees_du_jeu_grand_sous_dix_secondes (perf) | couvert |
| Saisies extrêmes antérieures (11 tests : adresses aberrantes, année de début exotique, capacité et effectif trop grands, chiffres exotiques, graine exotique, identifiant trop grand dans un choix, jours fériés d'une année inexistante, listes vides avec ou sans filtre, service bornant les entiers) | test_saisies_extremes.py::test_adresses_aberrantes ; ::test_annee_de_debut_exotique ; ::test_capacite_trop_grande ; ::test_chiffres_exotiques_refuses ; ::test_effectif_trop_grand ; ::test_graine_exotique ; ::test_identifiant_trop_grand_dans_un_choix ; ::test_jours_feries_d_une_annee_inexistante ; ::test_liste_vide_avec_filtre_pose ; ::test_liste_vide_sans_filtre_pose ; ::test_service_borne_les_entiers | couvert (marqués et documentés) |

### Page Générer et emploi du temps

Validation : ruff propre ; pytest tests/web_edt : 150 réussis, 2 xfail stricts ; -m cas_limite : 20 tests (31 cas paramétrés) ; suite complète 1183 réussis.
Aides partagées : tests/web_edt/conftest.py (ENORME, HX, reperes, client_sans_trace, creer_version_vide, premiere_occurrence, calcul_qui_tourne).
Défauts corrigés : paramètre vide (?semestre_id=) → « non précisé » ; identifiants > 2^63 ou négatifs sur 24 adresses → 404 ; cible/séance énorme ou cookie forgé → affichage par défaut ; jour sans créneau possible → formulaire lisible ; arrêt d'un calcul terminé → message juste ; fragments HTMX sans HX-Request → page complète.
Défaut transmis à l'échange et corrigé par lui : export CSV/iCal avec version_id énorme → 500 (xfail levé).
Complément bbe664d (neuf cas transmis par le web données et le socle, xfail levés) : HX-Current-URL illisible dans le panneau d'une séance (`gabarits.adresse_courante`) ; nombres reçus en texte (`seance=h…`, `niveau_regle_…`, `salle_id`, `cible=`, durée de pause) lus par `entier_ecrit` (chiffres ASCII, 30 au plus ; « ² », 5 000 chiffres et `niveau_regle_<5000>` des vérifications préalables donnaient 500), saisies recopiées coupées à 40 caractères ; compteur d'« À traiter » sans HTMX mène à la page ; motif d'annulation borné à 2 000 caractères (maxlength + 422 sans annuler) ; HEAD répond comme GET sans corps (`IntergicielHead`) ; `/sante` expose `base.inscriptible` (écriture d'essai annulée, sans attente sur verrou ; 503 si lecture seule, null si une autre écriture tient la base) et l'accueil le signale ; page 503 nommant la lecture seule et le disque plein avec le remède. Complément 4a34b8d (politique de verrou) : `session_requete` (`web/dependances.py`) ouvre la transaction par `transaction_immediate` pour POST, PUT, PATCH et DELETE (GET et HEAD restent différés), sauf lancer/relancer/arrêter un calcul (`ROUTES_ECRITURE_DELEGUEE`, leur session ne fait que lire et le service de génération écrit dans ses propres transactions ; `creer_app` refuse de démarrer si un de ces noms ne correspond plus à une route) ; la pause déjeuner choisie au lancement s'enregistre aussi en transaction immédiate ; au-delà du délai de verrou, page 503 « Base de données occupée » sans trace ; `test_bornes_des_identifiants` recentré sur `exiger_identifiant` et `obtenir` (la garde elle-même est testée par le socle). Risque résiduel transmis au solveur : `calcul_en_cours()` peut écrire (clôture d'un calcul abandonné) depuis une requête qui tient déjà le verrou. Mesure : Générer à froid sur le jeu grand = 1,65 s (0,3 s instantané du récapitulatif + 0,3 s second chargement du même instantané par `pre_verifier_semestre` + 0,9 s préparation du solveur) ; piste côté solveur : `pre_verifier_semestre` acceptant un instantané déjà chargé (−0,3 s).

| Cas | Test (fichier::nom) | État |
|---|---|---|
| Option « Tous les semestres » (`semestre_id=` vide) sur À traiter, Générer, historique, grille | `tests/web_edt/test_a_traiter.py::test_option_tous_les_semestres_affiche_tous_les_semestres` | corrigé |
| Identifiant nul, négatif, au-delà de 2^63, booléen, texte, décimal | `tests/web_edt/test_cas_limites.py::test_bornes_des_identifiants` | corrigé |
| Identifiant énorme, négatif ou inconnu sur 24 adresses | `tests/web_edt/test_cas_limites.py::test_identifiants_enormes_ou_negatifs_donnent_introuvable` | corrigé |
| Groupe, enseignant ou salle énorme dans l'adresse de la grille | `tests/web_edt/test_cas_limites.py::test_cible_enorme_dans_l_adresse_montre_la_premiere_classe` | corrigé |
| Séance à ouvrir énorme, nulle ou illisible | `tests/web_edt/test_cas_limites.py::test_seance_enorme_dans_l_adresse_ouvre_la_page_sans_panneau` | corrigé |
| Cookie de dernière cible forgé ou énorme | `tests/web_edt/test_cas_limites.py::test_cookie_de_cible_illisible_ou_enorme_est_ignore` | corrigé |
| Jour où la séance ne tient sur aucun créneau ; jour non proposé | `tests/web_edt/test_emploi_du_temps.py::test_formulaire_seance_sans_debut_possible_reste_lisible` | corrigé |
| Arrêter un calcul déjà terminé (deux onglets) | `tests/web_edt/test_calculs.py::test_arret_d_un_calcul_termine_dit_qu_il_est_fini` | corrigé |
| Fragment HTMX ouvert sans HTMX (suivi, vérifications, formulaires, panneaux) | `tests/web_edt/test_cas_limites.py::test_fragments_ouverts_sans_htmx_menent_a_la_page` | corrigé / couvert |
| Panneau ou enregistrement d'une séance supprimée entre-temps | `tests/web_edt/test_cas_limites.py::test_panneau_d_une_seance_supprimee_entre_temps` | couvert |
| Déplacement vers un début hors grille, un jour hors semaine ou illisible | `tests/web_edt/test_cas_limites.py::test_deplacement_vers_un_creneau_ou_un_jour_inexistant` | couvert |
| Séance datée annulée deux fois puis déplacée | `tests/web_edt/test_cas_limites.py::test_seance_annulee_ni_reannulee_ni_deplacee` | couvert |
| Séance datée déplacée hors du semestre | `tests/web_edt/test_cas_limites.py::test_deplacer_une_seance_datee_hors_du_semestre` | couvert |
| Grille datée au 01/01/0001 ou au 31/12/9999 | `tests/web_edt/test_cas_limites.py::test_date_tres_ancienne_ou_lointaine_sans_erreur` | couvert |
| Filtres d'affichage inconnus ou contradictoires | `tests/web_edt/test_cas_limites.py::test_filtres_inconnus_refuses_lisiblement` | couvert |
| Relancer pendant qu'un calcul tourne | `tests/web_edt/test_cas_limites.py::test_relance_pendant_un_calcul_mene_au_calcul_en_cours` | couvert |
| Version sans séance : grille, historique, export CSV et iCal | `tests/web_edt/test_cas_limites.py::test_version_sans_seance_s_affiche_et_s_exporte` | couvert |
| Version active vide sur À traiter et l'accueil | `tests/web_edt/test_cas_limites.py::test_version_active_vide_sur_a_traiter_et_l_accueil` | couvert |
| Semestre dont la grille de créneaux est vidée | `tests/web_edt/test_cas_limites.py::test_semestre_sans_grille_reste_utilisable` | couvert |
| Export CSV ou iCal d'une version à identifiant énorme | `tests/web_edt/test_cas_limites.py::test_export_d_une_version_enorme_dit_introuvable` | corrigé par l'échange, xfail levé |
| Base en lecture seule ou disque plein : `/sante` répond 503 avec `base.inscriptible` faux, l'accueil le signale, la page 503 nomme la cause et le remède (transmis par le socle) | `tests/web_edt/test_accueil.py::test_base_qui_refuse_l_ecriture_signalee_sur_l_accueil_et_sante` | corrigé (bbe664d) |
| Base tenue par une autre écriture : `/sante` rend `inscriptible` null sans attendre, ce n'est pas une panne | `tests/web_edt/test_accueil.py::test_base_occupee_par_une_ecriture_n_est_pas_une_panne` | corrigé (bbe664d) |
| HX-Current-URL illisible dans le panneau d'une séance (transmis par le web données) | `tests/web_edt/test_cas_limites.py::test_hx_current_url_illisible_dans_les_panneaux` | corrigé (bbe664d) |
| Nombres de 5 000 chiffres ou « ² » au lancement (`seance=h…`, `niveau_regle_…`, `salle_id`, `cible=`, durée de pause) refusés sans 500 (transmis par le web données) | `tests/web_edt/test_cas_limites.py::test_saisies_de_cinq_mille_chiffres_au_lancement` | corrigé (bbe664d) |
| Motif d'annulation de 5 000 caractères refusé (422) sans annuler la séance (transmis par le web données) | `tests/web_edt/test_cas_limites.py::test_motif_d_annulation_borne` | corrigé (bbe664d) |
| HEAD sur les écrans de l'emploi du temps : même réponse que GET, sans corps (transmis par le socle) | `tests/web_edt/test_cas_limites.py::test_head_sur_les_ecrans_de_l_emploi_du_temps` | corrigé (bbe664d) |
| Écriture web pendant qu'un autre écrivain valide 0,3 s plus tard : la requête attend son tour et les deux écritures sont enregistrées | `tests/web_edt/test_cas_limites.py::test_une_ecriture_attend_son_tour_pendant_un_autre_ecrivain` | corrigé (4a34b8d) |
| Lecture (GET, HEAD) servie pendant qu'un verrou d'écriture est tenu | `tests/web_edt/test_cas_limites.py::test_une_lecture_est_servie_pendant_un_autre_ecrivain` | couvert (4a34b8d) |
| Verrou tenu au-delà du délai : page 503 « base occupée », séance non annulée | `tests/web_edt/test_cas_limites.py::test_verrou_tenu_au_dela_du_delai_donne_la_page_base_occupee` | corrigé (4a34b8d) |
| Double clic sur « Lancer le calcul » | `tests/web_edt/test_calculs.py::test_suivi_sans_rechargement_second_lancement_refuse_puis_arret` | couvert (antérieur) |
| Rendu sur téléphone, impression, grille très chargée | — | manuel : rendu visuel |
| Coupure réseau pendant le suivi d'un calcul | — | manuel : comportement du navigateur |

### Import et export

Validation : ruff propre ; pytest tests/echange : 239 réussis (perf compris) ; suite complète 1230 réussis, 0 échec ; contre-épreuve sur main : 61 des nouveaux tests échouent sur l'ancien code, 15 protègent un comportement déjà juste. 40 fonctions (78 variantes, 2 perf) marquées cas_limite avec docstring Cas/Risque. Aides : tests/echange/fabrique.py (CSV, classeur avec fusions, ZIP, iCal en mémoire ; compter, messages, erreurs, seul), fixture version_reference dans le conftest, test_format vérifie LONGUEURS.

Défauts corrigés : cas 1 à 13, 15 à 17, 19, 20, noms d'archive du 18 ; iCal (ical.py) : INTERVAL=0 (lecture sans fin, base réservée), INTERVAL=-1 / BYDAY=9MO / durée immense / an 10000 / RECURRENCE-ID illisible (plantage), règles impossibles coûteuses (2 à 20 s) → refus avant calcul, erreur par événement sans arrêter la lecture, 3 s de calcul par fichier ; cas transmis par web EDT : version_id négatif/nul/énorme → 404, texte → 422.
Complément (b6402dc, services/echange.py) : les fichiers, récurrences iCal comprises, sont lus avant l'ouverture de la transaction (seule l'écriture réserve la base) ; le budget de 3 s de calcul iCal vaut pour tout l'envoi ; le semestre de l'import et la version de resoudre_filtre passent par obtenir_par_id ; `import_en_cours()` (vrai pendant tout import, simulé ou appliqué, faux même après une erreur) est exposé pour `lancer_calcul`. Validation du complément : pytest tests/echange 243 réussis ; suite complète 1234 réussis.
Complément d'intégration (a52ca6b, 309e3d9) : la fusion du socle (BEGIN émis par l'événement de début de transaction, `isolation_level=None`) rendait sans effet le `BEGIN IMMEDIATE` de l'import (deux tests de verrou en échec) ; `simulation_possible` appelle désormais `edt.db.moteur.transaction_immediate(session)` en tête (`ouvrir_transaction` supprimée), le semestre de l'import se lit dans une session à part pour que la session de l'import n'ait encore rien exécuté, une session déjà engagée est refusée explicitement (`ErreurConfiguration`). pytest tests/echange tests/socle : 626 réussis. 11401cd : `obtenir_par_id` et `exporter-edt --version` reposent sur la garde commune `edt.db.moteur.identifiant_valide` (`ENTIER_SQLITE_MAX` supprimée, plus aucune borne écrite à la main dans le lot).
Cas manuels : classeur chiffré par Excel ; fusions et formats réels d'un tableur ; imports simultanés depuis deux onglets. Écarté : zéros de tête perdus par le tableur (indétectable, documenté). Résiduel : 10 000 lignes ≈ 20 s (seuil 30 s) ; 50 000 lignes réserveraient la base ≈ 100 s (piste d'optimisation).

| Cas | Test (fichier::nom, relatif à tests/echange/) | État |
|---|---|---|
| 1. Entier énorme qui fait planter l'import | test_bornes.py::test_entier_hors_bornes_sqlite_rejette_la_ligne_sans_planter (6 variantes) ; test_bornes.py::test_debordement_d_entier_de_sqlite_rejette_sa_ligne_seule | corrigé |
| 2. Caractère de contrôle qui casse l'export XLSX | test_import_donnees.py::test_export_xlsx_tolere_les_caracteres_de_controle (xlsx, zip) ; test_import_donnees.py::test_caractere_de_controle_importe_est_garde_et_signale | corrigé |
| 3. Identifiant énorme dans l'adresse ou la commande | test_web.py::test_export_d_une_version_negative_nulle_ou_enorme_dit_introuvable (csv, ics) ; test_web.py::test_ressource_hostile_dans_l_adresse_donne_une_erreur_lisible (csv, ics) ; test_web.py::test_semestre_enorme_ou_negatif_a_l_import_donne_une_erreur_lisible ; test_commandes.py::test_version_hors_limites_en_ligne_de_commande_donne_un_message (3 variantes) | corrigé (CLI -7 et 0 : couvert) |
| 4. Deux imports simultanés | test_bornes.py::test_import_reserve_la_base_des_son_ouverture ; test_bornes.py::test_deux_imports_simultanes_sont_serialises_sans_erreur | corrigé (+ manuel ; verrou par `transaction_immediate` du socle depuis 309e3d9) |
| 5. Réimport sans enseignants | test_import_edt.py::test_reimport_sans_enseignants_signale_le_remplacement_des_enseignants_existants | corrigé |
| 6. iCal hebdo après un semestre raccourci | test_export_edt.py::test_ical_hebdo_tolere_un_semestre_raccourci_apres_la_version | corrigé |
| 7. Fichier sans cellule remplie | test_lecture.py::test_fichier_sans_cellule_remplie_est_signale_comme_vide (6 variantes) ; test_lecture.py::test_fichier_d_en_tetes_seuls_dit_rien_a_traiter | corrigé / couvert |
| 8. Classeur protégé par mot de passe | test_lecture.py::test_classeur_protege_par_mot_de_passe_signale_clairement | corrigé (+ manuel) |
| 9. Début imposé + durée au-delà de minuit | test_import_donnees.py::test_besoin_dont_le_debut_impose_et_la_duree_depassent_minuit_est_rejete | corrigé |
| 10. Salle au code contenant « @ » | test_import_donnees.py::test_salle_au_code_contenant_arobase_reste_referencable | corrigé |
| 11. Date dans une colonne de codes | test_lecture.py::test_cellule_date_dans_une_colonne_code_est_refusee_avec_explication (3 variantes) | corrigé (+ manuel) |
| 12. Fuseau iCal inconnu | test_ical.py::test_fuseau_inconnu_signale_en_avertissement | corrigé |
| 13. Récurrence ancienne refusée | test_ical.py::test_recurrence_ancienne_bornee_par_la_periode_sans_refus (2 variantes) | corrigé |
| 14. 10 000 lignes valides | test_bornes.py::test_import_de_dix_mille_lignes_valides_sous_le_seuil (perf) | couvert |
| 15. Durées absurdes | test_import_donnees.py::test_durees_absurdes_signalees | corrigé |
| 16. Texte trop long | test_lecture.py::test_valeur_trop_longue_pour_sa_colonne_rejetee ; test_format.py::test_longueurs_alignees_sur_le_modele ; test_format.py::test_toute_colonne_de_texte_a_une_longueur | corrigé |
| 17. Colonne en double | test_lecture.py::test_colonne_en_double_lue_une_seule_fois_et_signalee_une_fois | corrigé |
| 18. ZIP aux chemins hostiles | test_bornes.py::test_archive_aux_chemins_hostiles_lue_sans_ecriture_sur_disque | couvert ; noms affichés corrigés |
| 19. Cellules fusionnées | test_lecture.py::test_cellule_fusionnee_signalee_comme_telle | corrigé (+ manuel) |
| 20. Nombre de série Excel | test_lecture.py::test_nombre_de_serie_excel_refuse_avec_un_indice (5 variantes) ; test_lecture.py::test_heure_entiere_ecrite_en_nombre_reste_une_heure | corrigé |
| 21. Hiérarchie profonde, auto-parenté | test_import_donnees.py::test_groupe_parent_de_lui_meme_rejete ; ::test_ordre_des_parents_lineaire_sur_une_chaine_profonde ; ::test_hierarchie_profonde_et_auto_parente (perf) | couvert |
| 22. iCal en mode données, dossier mélangé | test_import_donnees.py::test_ical_en_mode_donnees_signale_le_mauvais_mode ; test_commandes.py::test_dossier_melange_signale_l_agenda_en_mode_donnees | couvert |
| Règle iCal hostile (boucle sans fin, plantage, 2 à 20 s) | test_ical.py::test_regle_de_recurrence_hostile_refusee_avant_tout_calcul (10 variantes) ; test_ical.py::test_regle_de_cours_ordinaire_toujours_admise (6 variantes) | corrigé |
| Dates iCal incalculables | test_ical.py::test_evenement_aux_dates_incalculables_ecarte_sans_arreter_la_lecture (4 variantes) | corrigé |
| Récurrences trop coûteuses | test_ical.py::test_fichier_aux_recurrences_trop_couteuses_refuse_en_entier | corrigé |
| Version négative, nulle ou énorme à l'export (transmis par web EDT) | test_web.py::test_export_d_une_version_negative_nulle_ou_enorme_dit_introuvable | corrigé |
| Lecture iCal dans la transaction (base réservée pendant le développement des récurrences) | test_bornes.py::test_lecture_ical_faite_avant_de_reserver_la_base | corrigé (b6402dc) |
| Budget de calcul iCal par fichier contourné par N fichiers hostiles | test_bornes.py::test_budget_de_calcul_ical_partage_par_l_envoi | corrigé (b6402dc) |
| Service appelé avec un identifiant hors bornes (semestre, version) : OverflowError | test_bornes.py::test_service_appele_avec_un_identifiant_hors_base_repond_introuvable | corrigé (b6402dc) |
| Marqueur « import en cours » consultable par le lancement d'un calcul | test_import_donnees.py::test_import_en_cours_signale_pendant_tout_l_import | corrigé (b6402dc) |
| Import lancé sur une session déjà engagée (lecture préalable) : refus explicite au lieu d'une transaction différée silencieuse | test_import_donnees.py::test_import_sur_une_session_deja_engagee_refuse_clairement | corrigé (309e3d9) |
| Zéros de tête perdus par le tableur | — | écarté : indétectable, documenté |

### Générateur de données fictives

Validation : ruff propre ; pytest tests/synthese : 101 réussis, 0 xfail (perf compris, après le complément 4c4f38e et la correction du socle) ; 22 tests dans tests/synthese/test_cas_limites.py (38 cas paramétrés), 8 cas existants annotés ; aides : conftest (generer_dans, total_prevu, lanceur), controle_temoin.problemes_du_temoin, empreinte.py.
Défauts corrigés (14) : année de début bornée 2000-2100 ; graine bornée 0..2^31-1 (une graine négative donnait le même jeu que son opposée) ; jeu --reset vérifie tout avant la remise à zéro ; S2 trop court sans stage ; configuration sans site ou salles négatives refusée ; division par zéro des taux de charge ; semaines d'intégration et partiels fermés du lundi au dimanche ; conférences et épreuves sans date comptées avec raison ; boucle infinie au-delà de 800 enseignants (noms Faker épuisés) ; tirages découplés de Faker, empreinte figée ; témoin relancé jusqu'à quatre fois (aucun besoin écarté sur 40 graines du moyen, 30 du grand) ; message « grille vide » ; contrôle des jours permis ; graine négative refusée par la commande.
Défaut transmis au socle et corrigé par lui (c96a1b7) : services/jeux.py remettait la base à zéro avant le refus d'une graine invalide. Complément 4c4f38e : le générateur ne crée aucun besoin ponctuel (amphi de rentrée, rencontres avec les tuteurs, conférence métiers, partiels) hors des dates ouvertes du semestre ; sans date possible, le besoin est écarté avec sa raison (demande du socle, dont le dépliage refuse désormais un ponctuel hors semestre).

| Cas | Test (fichier::nom) | État |
|---|---|---|
| Année de début hors bornes (0, 1999, 2101, 9999) | test_cas_limites.py::test_annee_de_debut_hors_bornes_refusee_lisiblement | corrigé |
| Année passée ou lointaine (2019, 2035) | test_cas_limites.py::test_annee_passee_ou_lointaine_donne_un_jeu_valide | couvert |
| Graine négative, trop grande ou non entière | test_cas_limites.py::test_graine_hors_bornes_refusee | corrigé |
| Graines extrêmes admises (0, 2^31-1), déterministes | test_cas_limites.py::test_graines_extremes_admises_et_deterministes | couvert |
| Même borne de graine à l'écran et en commande | test_cas_limites.py::test_borne_de_graine_identique_au_formulaire_web | couvert |
| `jeu --reset --oui` avec graine ou année refusée | test_cas_limites.py::test_commande_refuse_sans_toucher_a_la_base | corrigé |
| Service : remise à zéro avant le refus | test_cas_limites.py::test_service_refuse_une_graine_invalide_avant_la_remise_a_zero | corrigé par le socle (c96a1b7) |
| Configuration sans site ou sans salle | test_cas_limites.py::test_configuration_sans_salle_refusee_a_la_construction | corrigé |
| Capacité ≤ 0, nombre de salles négatif | test_cas_limites.py::test_salles_site_negatives_refusees | corrigé |
| Une seule salle pour une promotion | test_cas_limites.py::test_configuration_qui_ne_tient_pas_dans_les_salles | couvert |
| Classe d'un étudiant (demi-groupe vide) | test_cas_limites.py::test_configuration_minimale_un_etudiant_par_classe | couvert |
| Semestre d'un jour, un créneau, samedi seul, dimanche sans créneau, pause de 2 h | test_cas_limites.py::test_calendrier_et_grille_degeneres_sans_plantage | corrigé (ponctuels bornés aux dates du semestre, 4c4f38e) |
| Besoin impossible d'avance (conférence sans date) | test_cas_limites.py::test_besoin_impossible_d_avance_ecarte_et_explique | corrigé |
| S2 trop court pour un stage | test_cas_limites.py::test_semestre_tres_court_avec_stage_ne_plante_pas | corrigé |
| Grille vide | test_cas_limites.py::test_grille_vide_refusee_lisiblement | corrigé |
| Semestre manquant | test_cas_limites.py::test_semestre_manquant_refuse_lisiblement | couvert |
| Jeu différent d'une machine à l'autre | test_cas_limites.py::test_empreinte_figee_du_jeu_petit_graine_0 | corrigé (3.13 à confirmer en CI) |
| Autre version de Faker | test_cas_limites.py::test_jeu_independant_des_noms_proposes_par_faker | corrigé |
| Noms épuisés (plus de 800 enseignants) | test_cas_limites.py::test_noms_epuises_homonymes_admis_sans_boucle_infinie | corrigé |
| Graine difficile (moyen, graine 13) | test_cas_limites.py::test_graine_difficile_du_moyen_sans_besoin_ecarte | corrigé |
| Témoin et règles valides sur 3 graines du petit | test_cas_limites.py::test_temoin_et_regles_valides_sur_trois_graines_du_petit | couvert |
| Témoin et règles valides sur 10 graines par taille | test_cas_limites.py::test_temoin_et_regles_valides_sur_dix_graines (perf) | couvert |
| Génération bornée en temps | test_cas_limites.py::test_generation_bornee_en_temps ; test_performance.py::test_jeu_grand_en_moins_d_une_minute | couvert |
| Taille inconnue | test_commande.py::test_taille_inconnue | couvert |
| Remise à zéro refusée à la confirmation | test_commande.py::test_remise_a_zero_demande_confirmation | couvert |
| `jeu --reset` sur une base neuve | test_commande.py::test_remise_a_zero_sur_une_base_neuve | couvert |
| Second chargement | test_commande.py::test_second_chargement_refuse_avec_un_message_clair | couvert |
| Base non initialisée | test_commande.py::test_base_non_initialisee_refusee | couvert |
| Chargement pendant un calcul | test_commande.py::test_refus_pendant_un_calcul | couvert |
| Configuration incohérente (apprentis en 1re année) | test_generation.py::test_configuration_invalide_refusee_avec_le_detail | couvert |
| Base déjà garnie | test_generation.py::test_base_deja_garnie_refusee | couvert |
| Déjeuner libre pour tout l'établissement | test_regles.py::test_dejeuner_global_en_mode_libre | couvert |
| Rechargements répétés (fuite mémoire) | — | manuel : objets stables sur une même base |
| Lenteur sous pytest | — | écarté : charge de la machine, profil identique |
| Chargements simultanés | — | écarté : verrou du socle, hors lot |

### Exploitation et déploiement

Validation : pytest tests/exploitation + test_cli + test_schema : 120 réussis, 2 sautés (Linux seulement), 2 xfail stricts ; bash -n propre ; suite complète 1236 réussis. 53 tests (95 cas) dans tests/exploitation/ (test_scripts_deploiement.py 20, test_commandes_exploitation.py 20, test_systemd_nginx.py 13), fixtures dans conftest.py.

Défauts corrigés : déploiement pendant un « sudo edt » (verrou) ; sauvegarde du déploiement en échec (.partiel purgés, etat.sh) ; horodatages UTC/Paris (versions nommées en UTC) ; envoi coupé (témoin envoi-termine, versions jamais mises en service purgées) ; liste des fichiers installés complète ; fichiers de /var/lib/edt appartenant à root refusés ; sauvegarde quotidienne (commandes `edt sauvegarder`, `edt verifier-sauvegarde`, service + minuteur systemd 3 h 30 Paris) ; page erreur-413.html et style de la 502 limité ; `serve` borne le port ; etat.sh compte « killed by the OOM killer » ; guide (sudo, bloc base corrompue, commandes coupées, message de démarrage).

Défauts transmis : CI sans Python 3.14 (xfail test_la_ci_teste_la_version_de_python_du_serveur) ; services : MESSAGE_INTERROMPU (generation.py:116) ne parle pas de mémoire ni de réduire temps/fils (xfail test_le_message_d_un_calcul_interrompu_aide_a_eviter_la_recidive) ; suggestion solveur : borner les fils CP-SAT selon la mémoire (3 Go) ; docs/architecture.md devrait citer sauvegarder/verifier-sauvegarde ; si un lot ajoute une confirmation à init-db --reset, l'option doit s'appeler --oui.

Procédures manuelles (docs/deploiement.md § 10) : 10.1 déploiement pendant sudo edt ; 10.2 échec de sauvegarde (chattr +i) ; 10.3 fuseaux ; 10.4 envoi interrompu ; 10.5 SIGKILL pendant une écriture ; 10.6 mémoire épuisée (MemoryMax=400M) ; 10.7 page 413 ; 10.8 sauvegarde quotidienne et exercice de restauration (mensuel) ; 10.9 fichiers appartenant à root.

Après fusion : 1) deployer.sh ; 2) recopier deploy/ et relancer installer-serveur.sh (APRÈS le code : le minuteur lance `edt sauvegarder`) ; 3) `sudo systemctl start edt-sauvegarde` ; 4) etat.sh = 0 problème ; 5) rejouer § 10 hors heures d'usage (10.5 et 10.6 arrêtent le service). Versions nommées en UTC désormais ; anciennes versions en échec purgées au prochain déploiement.

| Cas | Test (fichier::nom) ou procédure | État |
|---|---|---|
| Déploiement pendant une commande « sudo edt » | test_scripts_deploiement.py::test_la_bascule_attend_la_fin_des_commandes_qui_tiennent_la_base ; ::test_processus_hors_service_liste_une_commande_de_l_utilisateur ; ::test_processus_hors_service_ignore_les_processus_du_service (Linux) ; procédure 10.1 | corrigé |
| Sauvegarde du déploiement en échec, `.partiel` jamais purgés | test_scripts_deploiement.py::test_la_sauvegarde_du_deploiement_reussit_puis_retire_sa_copie_en_cas_d_echec ; ::test_la_finalisation_purge_les_sauvegardes_partielles_du_deploiement ; procédure 10.2 | corrigé |
| Horodatages UTC / Paris, collision au changement d'heure | test_scripts_deploiement.py::test_horodatages_du_deploiement_sans_ambiguite ; procédure 10.3 | corrigé |
| Syntaxe bash, fins de ligne, exemple d'environnement | test_scripts_deploiement.py::test_chaque_script_passe_le_controle_de_syntaxe_de_bash ; ::test_chaque_script_s_arrete_a_la_premiere_erreur ; ::test_fichiers_du_deploiement_en_utf8_et_fins_de_ligne_lf ; ::test_le_depot_impose_des_fins_de_ligne_lf ; ::test_l_exemple_d_environnement_est_accepte_par_l_application | couvert |
| Port et fichiers installés recopiés d'un script à l'autre | test_scripts_deploiement.py::test_un_seul_port_et_une_seule_adresse_pour_l_application ; ::test_le_deploiement_compare_chaque_fichier_installe | corrigé |
| CI sans le Python du serveur (3.14) | test_scripts_deploiement.py::test_la_ci_teste_la_version_de_python_du_serveur | xfail vers CI |
| Envoi du code interrompu | test_scripts_deploiement.py::test_l_envoi_du_code_ecrit_un_temoin_de_fin_controle_par_le_serveur ; ::test_seules_les_versions_mises_en_service_comptent_dans_la_rotation ; procédure 10.4 | corrigé |
| Arrêt brutal (SIGKILL) en pleine écriture, WAL | test_commandes_exploitation.py::test_la_base_reste_saine_apres_un_arret_brutal_en_pleine_ecriture ; ::test_le_redemarrage_clot_le_calcul_tue_avec_le_service ; procédure 10.5 | couvert |
| Fichier `-wal` appartenant à root | test_scripts_deploiement.py::test_fichiers_de_la_base_appartenant_a_root_refuses_avant_la_bascule ; procédure 10.9 | corrigé |
| Sauvegarde faite seulement au déploiement | test_systemd_nginx.py::test_la_sauvegarde_quotidienne_utilise_la_commande_de_l_application ; test_commandes_exploitation.py::test_sauvegarder_* (7) et ::test_verifier_sauvegarde_* (3) ; procédure 10.8 | corrigé |
| Page 413 brute de nginx | test_systemd_nginx.py::test_nginx_sert_une_page_d_erreur_installee ; ::test_la_page_413_annonce_la_limite_de_l_application ; ::test_une_page_d_erreur_inseree_par_htmx_ne_restyle_pas_l_application ; ::test_la_limite_d_envoi_de_nginx_laisse_l_application_repondre ; procédure 10.7 | corrigé |
| Mémoire épuisée : redémarrage, lecture du journal | test_systemd_nginx.py::test_le_service_lance_l_application_comme_le_decrit_le_guide ; test_scripts_deploiement.py::test_etat_compte_les_arrets_pour_memoire_epuisee ; procédure 10.6 | corrigé |
| Mémoire épuisée : message à l'utilisateur | test_commandes_exploitation.py::test_le_message_d_un_calcul_interrompu_aide_a_eviter_la_recidive | xfail vers services (generation.py) |
| Fils CP-SAT bornés par la mémoire | — | écarté : suggestion transmise au solveur |
| Lanceur et service : même environnement | test_systemd_nginx.py::test_le_lanceur_et_le_service_partagent_le_meme_environnement | couvert |
| Sites nginx HTTP et HTTPS : partie commune | test_systemd_nginx.py::test_chaque_site_nginx_inclut_la_partie_commune_installee | couvert |
| `init-db` relancé à chaque déploiement, `--reset` sans terminal | test_commandes_exploitation.py::test_init_db_relance_a_chaque_deploiement_n_ajoute_rien ; ::test_init_db_reset_du_deploiement_ne_pose_aucune_question ; test_scripts_deploiement.py::test_la_remise_a_zero_du_deploiement_reste_non_interactive | couvert |
| Version sans `verifier-schema` (commande inconnue = code 2) | test_commandes_exploitation.py::test_une_commande_inconnue_donne_le_code_2_attendu_par_le_deploiement ; test_scripts_deploiement.py::test_le_deploiement_reconnait_une_version_sans_verifier_schema | couvert |
| Base annoncée au déploiement, `doctor` pendant que le service tourne | test_commandes_exploitation.py::test_la_base_annoncee_au_deploiement_est_celle_du_service ; ::test_doctor_tolere_le_port_tenu_par_le_service | couvert |
| `serve` : même application que le service, port ou adresse invalide | test_commandes_exploitation.py::test_serve_et_le_service_lancent_la_meme_application ; ::test_serve_refuse_un_port_ou_une_adresse_invalide | corrigé |
| Guide : procédures complètes et rejouables | test_systemd_nginx.py::test_le_guide_decrit_chaque_verification_d_exploitation ; ::test_le_guide_cite_chaque_fichier_de_deploy_et_chaque_chemin_installe ; ::test_les_procedures_visent_le_serveur_des_scripts ; ::test_les_commandes_du_guide_passent_par_sudo_pour_les_dossiers_proteges ; ::test_aucune_commande_du_guide_n_est_coupee_sur_deux_lignes | corrigé |

### Cas écartés par la synthèse

- Deux lancements dans le même processus (double clic) — déjà couvert par tests/socle/test_generation.py::test_un_seul_calcul_a_la_fois
- Moteur rendant une solution incomplète, sans salle ou avec une salle inconnue — variante « un placement manquant » déjà couverte par test_solution_incomplete_refusee ; les variantes salle None / 999 sont gardées dans le lot services
- EDT_DB_URL=sqlite:// (base en mémoire) : le fil du calcul voit une base vide — doublon, fusionné dans « EDT_DB_URL sans fichier » (socle)
- EDT_DB_URL=sqlite:// (base mémoire) acceptée : chaque fil voit une base vide différente — doublon, fusionné dans « EDT_DB_URL sans fichier » (socle)
- EDT_DB_URL vers un pilote absent (postgresql://…) : doctor plante avec une trace — doublon, fusionné dans « schéma inconnu ou pilote absent » (socle)
- Paramètre invalide en base : l'écran des paramètres lui-même est inaccessible (422) — doublon, fusionné dans « Paramètre corrompu en base » (socle)
- init-db ressuscite à chaque déploiement un type de séance, type de salle, équipement ou la grille supprimés exprès — doublon, fusionné dans « init-db relancé … ressuscite » (socle)
- init-db --reset sans confirmation ni contrôle de calcul en production — doublon, fusionné dans « init-db --reset sans confirmation » (socle)
- Validation d'une pause déjeuner libre sans paramètres globaux : KeyError 'fin' — doublon, fusionné dans « Règle sans debut/fin : KeyError » (socle)
- jeu-minimal quand l'année 2026-2027 n'a pas de semestre « S1 » : StopIteration — doublon, fusionné dans « creer_jeu_minimal : préconditions incomplètes » (socle)
- Verrou réel d'un autre processus : lecteurs servis, écrivain qui patiente — doublon, fusionné dans « Écritures concurrentes : busy_timeout et WAL » (socle)
- Maxima horaires négatifs ou incohérents acceptés par la base — doublon, fusionné dans « Contraintes CHECK du modèle » (socle)
- Changement d'unicité, de clé étrangère ou d'index non détecté — doublon, fusionné dans « Deux versions actives / ecarts_schema » (socle)
- Dossier des journaux impossible à créer : toute commande échoue, doctor compris — doublon, fusionné dans « edt.log ou son dossier non ouvrable » (socle)
- init-db ou jeu-minimal sur un fichier qui n'est pas une base SQLite : erreur brute — doublon, fusionné dans « init-db sur une base verrouillée, en lecture seule ou corrompue » (socle)
- Fichier SQLite corrompu : pages en 500 « erreur inattendue » au lieu de « base indisponible » — doublon, fusionné dans « Base corrompue ou verrouillée au démarrage » (socle)
- Supervision par requête HEAD : /sante répond 405 — doublon, fusionné dans « Requête HEAD refusée (405) » (socle)
- Deux processus écrivent edt.log : rotation concurrente — doublon, fusionné dans « Rotation d'edt.log par deux processus » (socle)
- EDT_PORT avec un chiffre Unicode non décimal (²) — doublon, fusionné dans « Variables EDT_* invalides » (socle)
- EDT_HOST invalide ou IPv6 : doctor dit « le serveur tourne peut-être déjà » — doublon, fusionné dans « Variables EDT_* invalides » (socle)
- Règle SALLES et salles homonymes sur deux campus (inventaire vérification) — doublon, fusionné dans le cas bloquant du socle
- Dossier de la base impossible à créer : ErreurConfiguration lisible — hors quota socle : comportement sain constaté, à reprendre en seconde vague
- convertir_texte avec un texte None : AttributeError — hors quota socle : chemin peu probable (porte d'entrée protégée par les formulaires), seconde vague
- Arbre des groupes dégénéré (cycle A↔B, parent d'une autre année) : refus nommant le groupe — hors quota socle : comportement sain constaté ; le cycle est gardé côté vérification où il fait taire le vérificateur
- Version de base d'un autre semestre refusée — hors quota socle : comportement sain constaté
- Bornes de dates : semestre d'un seul jour, à cheval, 29 février — hors quota socle : comportement sain constaté ; les bornes datées sont gardées côté services (instanciation) et vérification (semaine à cheval)
- Sessions imbriquées en écriture dans un même fil : attente de 10 s — hors quota socle : preuve de contrat pour développeurs, seconde vague
- Limites de taille : 10 000 séances et 1 000 règles — hors quota socle : 0,25 s mesuré, aucune dérive constatée
- serve --port 0 ignoré en silence, --port 70000 plante — hors quota socle : usage de développement, seconde vague
- serve --port 0 / --host '' : repli silencieux sur la configuration — doublon du précédent (écarté)
- Cookie de messages corrompu ou mal typé — hors quota socle : les quatre formes sont déjà ignorées sans erreur
- Salle (ou besoin) supprimée pendant un calcul : calcul perdu en « erreur inattendue » — doublon, fusionné dans « Salle, besoin ou semestre supprimé pendant un calcul » (services)
- Suppression d'un besoin (ou d'une salle) pendant un calcul : échec masqué — doublon, fusionné dans le même cas (services)
- Semestre sans aucun besoin : version vide créée et activée (second inventaire) — doublon, fusionné (services)
- Dossier des journaux de calcul impossible à créer : tout calcul échoue — doublon, fusionné dans « Journal de calcul impossible à ouvrir » (services)
- Journal dédié impossible à ouvrir : tous les calculs échouent — doublon, fusionné (services)
- Journal de calcul impossible à ouvrir : le calcul échoue en renvoyant à un journal qui n'existe pas — doublon, fusionné (services)
- Colonne JSON corrompue (statistiques ou parametres en liste) — doublon, fusionné dans « Ligne calcul corrompue » (services)
- Ligne « calcul » illisible en base : accueil et écran des calculs en 500 — doublon, fusionné (services)
- Avertissements enregistrés en texte : un avertissement par caractère — doublon, fusionné (services)
- Calcul enregistré avec des niveaux illisibles : l'historique doit rester consultable — doublon, fusionné (services)
- Clôture d'un calcul abandonné après l'enregistrement de la version — doublon, fusionné dans « Interruption ou échec entre l'enregistrement de la version et l'instanciation » (services)
- Échec de l'instanciation après l'enregistrement de la version — doublon, fusionné (services)
- Lancement d'un calcul pendant un import de données — doublon, fusionné dans « Lancement d'un calcul pendant un chargement, une remise à zéro ou un import » (services)
- modifier ignore en silence un changement de code (et d'année) — doublon, fusionné dans « modifier() avec annee ou code » (services)
- Cible numérique énorme dans un brouillon : OverflowError — doublon, fusionné dans « Identifiant hors bornes SQLite dans generation, verification et regles » (services)
- Identifiant hors bornes SQLite : OverflowError au lieu d'introuvable (inventaire services) — fusionné dans le même cas
- Deux lancements au même instant depuis deux processus : contrôle puis insertion non atomiques — doublon, fusionné dans « Deux processus lancent un calcul » (services)
- Semestre d'un seul jour (instanciation) — doublon, fusionné dans « Semestre d'un seul jour ou commençant en milieu de semaine » (services)
- Semestre qui commence ou finit en milieu de semaine — doublon, fusionné (services)
- Séances hebdomadaires orphelines après réduction du nombre d'un besoin — hors quota services : comportement sain constaté (« ne correspondent plus à aucun besoin »)
- Casse des codes et niveaux incohérente entre --niveau, -d et --regle — doublon, fusionné dans « Commandes generer / pre-verifier / instancier » (services)
- Identifiants inexistants ou négatifs en ligne de commande — doublon, fusionné (services)
- Saut d'horloge (changement d'heure, correction NTP) : un calcul vivant est clôturé — doublon, fusionné dans « Changement d'heure ou saut d'horloge » (services)
- Options de calcul sans bornes en ligne de commande : temps limite infini, NaN, 1 000 fils — doublon, fusionné dans « Options du solveur sans bornes » (solveur)
- Export CSV/iCal avec version_id ou groupe_id énorme → 500 — doublon, fusionné dans « Identifiant énorme dans l'adresse ou le formulaire d'échange » (echange) ; gravité ramenée à sérieux (adresse forgée, pas un chemin normal)
- Besoin supprimé pendant un calcul : le calcul finit en « erreur inattendue » (inventaire web-edt) — fusionné dans le cas bloquant web-donnees « Suppression d'un besoin ou d'une salle acceptée pendant un calcul »
- Panne de base au démarrage : le serveur ne démarre pas du tout — gardé mais fusionné avec le cas des pages 500 sous le titre « Base corrompue ou verrouillée au démarrage » (socle)
- Identifiant au-delà de 2^63 : OverflowError (inventaire socle, commandes pre-verifier/instancier) — gardé comme cas socle ; les routes web-edt et web-donnees restent des cas distincts car les correctifs sont dans chaque couche

### Manques relevés par la synthèse

- Aucune authentification ni protection anti-CSRF sur un site public (pts.jorund.xyz) : tout POST destructeur (/jeux/charger avec remise à zéro, suppressions, import « tout remplacer », /parametres/defaut) est exécutable par quiconque connaît l'adresse ou pose un formulaire sur un autre site. Hors périmètre V1 décidé (docs/decisions.md), mais risque de perte de données réel tant que l'application est en ligne : au minimum un test qui vérifie la protection nginx (auth_basic ou liste d'IP) ou un jeton anti-rejeu sur les actions irréversibles.
- Arrêt systemd pendant l'écriture d'une version : TimeoutStopSec=20 s puis SIGKILL alors qu'un calcul de 15 min tourne ; la clôture au redémarrage est couverte, mais pas l'intégrité du fichier WAL après SIGKILL en pleine écriture (checkpoint, -wal orphelin) ni le cas d'un fichier -wal appartenant à root après une commande sudo edt lancée hors du lanceur.
- Normalisation Unicode des codes : « Étude » en NFC et en NFD, ou un code avec espace insécable (U+00A0) collé depuis Word, passent l'unicité et créent des doublons invisibles ; les références (import, règles par code) ne se résolvent pas sans explication. Aucun inventaire ne teste la normalisation NFC + strip des espaces exotiques à la saisie et à l'import.
- Sauvegarde uniquement au moment d'un déploiement : aucune sauvegarde périodique (timer systemd) ni exercice de restauration ; une panne de disque entre deux déploiements perd toutes les saisies. Test d'exploitation : présence d'un timer de sauvegarde et d'un script de restauration vérifiable (integrity_check + comptage de tables).
- Au-delà de client_max_body_size (21 Mo) nginx répond une page 413 brute, hors de la mise en page et sans le message « 20 Mo au plus, découper l'envoi » que l'application sait donner : à vérifier manuellement et à documenter (page d'erreur nginx personnalisée).
- Cycle de vie du serveur jamais exercé en test : aucun test n'utilise `with TestClient(creer_app())`, donc le lifespan (base_initialisee, cloturer_calculs_interrompus, journalisation au démarrage) n'est couvert par rien ; une fixture de session qui entre dans le contexte au moins une fois protégerait tous les cas de démarrage.
- Épuisement mémoire pendant un calcul (MemoryMax=3G, 8 fils CP-SAT sur le jeu grand) : le processus est tué par le noyau, systemd redémarre, le calcul est clos « interrompu par l'arrêt de l'application » : message juste mais qui n'invite ni à réduire les fils ni le temps ; aucun test ne couvre la lecture d'un OOM dans le journal ni une borne de fils dépendant de la mémoire disponible.

Le manque sur l'authentification est une décision d'équipe (site de démonstration
sans connexion) ; les autres sont traités par le lot exploitation (sauvegarde
quotidienne, page 413, mémoire, arrêt du service) ou par le socle (Unicode des
codes, cycle de vie du serveur).
