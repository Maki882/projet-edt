---
name: equipe-messagerie
description: Lance et pilote une équipe d'agents qui réalise une fonctionnalité en se parlant par messages directs (un chef de projet et trois spécialistes : calcul, écrans, tests). À utiliser quand un membre de l'équipe demande de confier une fonctionnalité à l'équipe avec messagerie.
argument-hint: "[fonctionnalité à réaliser]"
disable-model-invocation: true
---

# Piloter l'équipe avec messagerie

Tu es le **pilote** : la conversation principale. Tu lances quatre agents en arrière-plan, tu leur
donnes le moyen de s'écrire, tu approuves leur plan, tu observes, et tu reprends la main à la
livraison. L'organisation et les règles de la messagerie sont décrites dans
`docs/organisation-agents.md` ; les fiches de rôle sont dans `.claude/agents/`.

Fonctionnalité à réaliser : $ARGUMENTS

## 1. Préparer

1. Mets `main` à jour, puis crée la branche d'équipe et quatre copies de travail hors du dépôt :
   une pour le chef (branche d'équipe, par exemple `feat/<nom>`) et une par spécialiste
   (`feat/<nom>-calcul`, `feat/<nom>-ecrans`, `feat/<nom>-tests`), toutes créées depuis la branche
   d'équipe.
2. Crée le dossier de l'équipe, hors du dépôt : le chef y tiendra `PLAN.md` et `TABLEAU.md`.
3. Réserve un port de serveur d'essai pour le spécialiste des écrans.

## 2. Lancer les quatre agents

Lance en arrière-plan un agent par rôle : `chef-de-projet`, `specialiste-calcul`,
`specialiste-ecrans`, `specialiste-tests`. Si ces types d'agents ne sont pas reconnus dans ta
session, lance un agent généraliste en lui demandant de lire et d'appliquer sa fiche
`.claude/agents/<rôle>.md`. Le premier message de chacun donne : son rôle, la fonctionnalité, le
dossier de l'équipe, sa copie et sa branche, l'interpréteur Python, et la consigne de répondre
« prêt » puis d'attendre l'annuaire.

Note l'identifiant et le chemin de la transcription que le lancement rend pour chaque agent.

## 3. Donner l'annuaire

Quand les quatre ont répondu, envoie à chacun le même message : l'identifiant de chaque membre
(c'est son adresse pour `SendMessage`), `main` pour le pilote, et le rappel « adresse-toi
directement au membre concerné ». Envoie ensuite au chef le signal de départ.

## 4. Approuver le plan

Le chef t'envoie un message `[plan]`. Lis `PLAN.md` en entier avant de répondre. Refuse un plan
qui change le schéma de la base ou la spécification sans décision de l'équipe, qui laisse un
fichier à deux propriétaires, ou dont une tâche n'a pas de preuve de réussite. Réponds par
`[décision] plan approuvé` ou par la liste des points à reprendre.

## 5. Observer sans relayer

- Chaque arrêt d'un agent te revient en notification : n'y réponds que si elle demande une
  décision. Les spécialistes se parlent entre eux ; tu n'es pas leur passe-plat.
- Pour lire les échanges, reconstitue le fil :
  `python .claude/skills/equipe-messagerie/fil.py --membre chef=<identifiant>=<transcription> …
  --sortie <dossier de l'équipe>/fil.md`.
- Si toute l'équipe est à l'arrêt et qu'il reste des tâches ouvertes dans `TABLEAU.md`, relance
  le chef par un message qui cite les tâches en attente.

## 6. Reprendre la main à la livraison

Le chef t'envoie un message `[livraison]`. Ne le crois pas sur parole :

1. relis le diff de la branche d'équipe par rapport à `main` ;
2. relance la suite complète hors tests de charge, `ruff check`, `ruff format --check` ;
3. fais vérifier la fonctionnalité par un agent qui n'a pas participé, contre le plan ;
4. alors seulement, pousse la branche et ouvre la pull request. La fusion et la mise en ligne
   restent une décision d'un membre de l'équipe.

Garde le fil des messages et le tableau final avec la pull request : ils montrent comment le
travail s'est réparti.
