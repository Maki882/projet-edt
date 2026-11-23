export const meta = {
  name: 'equipe-issues',
  description: "Équipe d'agents autour des issues GitHub : des éclaireurs ouvrent des issues, puis tri, correction, relecture et recette, chaque agent ne parlant aux autres que par GitHub",
  whenToUse: "Pour faire vivre le carnet d'issues du projet : trouver de vrais défauts, ou traiter des issues déjà ouvertes, jusqu'à des pull requests relues et recettées. La fusion reste une décision de l'équipe.",
  phases: [
    { title: 'Éclaireurs', detail: 'chaque éclaireur cherche un défaut réel sous son angle et ouvre au plus une issue', model: 'opus' },
    { title: 'Tri', detail: "reproduction à partir du seul texte de l'issue, étiquettes, plan conseillé", model: 'opus' },
    { title: 'Correction', detail: 'une copie de travail et une branche par issue, test vu au rouge, pull request', model: 'opus' },
    { title: 'Relecture', detail: 'relecture adversariale dans une copie propre, en plusieurs tours au besoin', model: 'opus' },
    { title: 'Recette', detail: "critère de réussite rejoué pour de vrai, famille des tests d'intégration", model: 'opus' },
  ],
}

// Organisation décrite dans docs/organisation-agents.md. Les fiches de rôle sont dans
// .claude/agents/ : ce script ne fait qu'enchaîner les rôles et leur passer des numéros.
// Tout le contenu (constat, plan, relecture, recette) passe par GitHub.
//
// Arguments (un argument de forme inattendue arrête le script avant tout agent) :
//   racine   : racine du dépôt local, chemin absolu, avec .claude/agents/   (obligatoire)
//   python   : interpréteur Python de l'environnement du projet, absolu   (obligatoire)
//   depot    : dépôt GitHub « propriétaire/nom » (sinon l'agent le lit par « gh repo view »)
//   copies   : dossier où créer les copies de travail      (défaut : <racine>/../copies-edt)
//   essais   : dossier d'essai hors du dépôt               (défaut : <racine>/../essais-edt)
//   gh       : commande GitHub                             (défaut : « gh »)
//   locales  : consignes propres au poste, ajoutées à chaque agent
//   angles   : angles de recherche des éclaireurs, un éclaireur par angle (20 au plus)
//   issues   : numéros d'issues à traiter en plus de celles des éclaireurs
//   exclues  : numéros d'issues auxquelles aucun agent ne touche (le salon l'est toujours)
//   tours    : nombre de tours de relecture permis par pull request, de 1 à 5 (défaut : 3)
// Exemple : {"racine": "/chemin/vers/le/depot", "python": "/chemin/vers/le/depot/.venv/bin/python",
//            "issues": [57], "exclues": [60], "tours": 3}

// Salon des agents : issue épinglée où se coordonnent les assistants des membres.
const SALON = 56

const A = args === undefined || args === null ? {} : args
if (typeof A !== 'object' || Array.isArray(A)) throw new Error(`arguments : objet JSON attendu, reçu ${JSON.stringify(A)}`)
const CONNUS = ['depot', 'racine', 'copies', 'essais', 'python', 'gh', 'locales', 'angles', 'issues', 'exclues', 'tours']
const inconnus = Object.keys(A).filter(cle => !CONNUS.includes(cle))
if (inconnus.length) throw new Error(`argument(s) inconnu(s) : ${inconnus.join(', ')} ; attendus : ${CONNUS.join(', ')}`)

function texte(nom, defaut) {
  const v = A[nom]
  if (v === undefined) return defaut
  if (typeof v !== 'string' || !v.trim()) throw new Error(`argument ${nom} : texte non vide attendu, reçu ${JSON.stringify(v)}`)
  return v.trim()
}
function numeros(nom) {
  const v = A[nom]
  if (v === undefined) return []
  if (!Array.isArray(v) || !v.every(n => Number.isInteger(n) && n > 0)) {
    throw new Error(`argument ${nom} : tableau de numéros d'issues (entiers positifs) attendu, reçu ${JSON.stringify(v)}`)
  }
  return v
}

const racine = texte('racine', null)
const python = texte('python', null)
if (!racine || !python) throw new Error('arguments racine et python obligatoires : chemins absolus de la racine du dépôt local (avec .claude/agents/) et de son interpréteur Python')
const depot = texte('depot', '(à lire par « gh repo view --json nameWithOwner »)')
const copies = texte('copies', `${racine}/../copies-edt`)
const essais = texte('essais', `${racine}/../essais-edt`)
const gh = texte('gh', 'gh')
const locales = A.locales === undefined ? '' : texte('locales', '')
const issuesDonnees = numeros('issues')
const exclues = [...new Set([...numeros('exclues'), SALON])]
if (A.angles !== undefined && (!Array.isArray(A.angles) || A.angles.length > 20 || !A.angles.every(a => typeof a === 'string' && a.trim()))) {
  throw new Error(`argument angles : tableau de 20 phrases non vides au plus attendu, reçu ${JSON.stringify(A.angles)}`)
}
const angles = (A.angles || []).map(a => a.trim())
const tours = A.tours === undefined ? 3 : A.tours
if (!Number.isInteger(tours) || tours < 1 || tours > 5) throw new Error(`argument tours : entier de 1 à 5 attendu, reçu ${JSON.stringify(A.tours)}`)

// Un port de serveur d'essai par agent : les éclaireurs (finis avant le reste) de 9000 à 9019,
// puis dix ports par issue à partir de 9100, un par rôle.
const DECALAGE = { trieur: 1, developpeur: 2, relecteur: 3, recetteur: 4 }
const port = (role, rang) => 9100 + 10 * rang + DECALAGE[role]

function consigne(role, mission, dossier, complement) {
  return `Tu tiens le rôle « ${role} » dans l'équipe d'agents qui travaille autour des issues GitHub du projet EDT.

Ta fiche de rôle est le fichier ${racine}/.claude/agents/${role}.md : lis-la en entier avant toute action et applique-la exactement. Si ce fichier est introuvable, n'agis pas sur GitHub : rends immédiatement la donnée structurée avec la valeur qui dit que rien n'a été fait (issue 0, pull request 0, décision ou verdict « impossible ») et un résumé qui le dit. L'organisation d'ensemble est décrite dans ${racine}/docs/organisation-agents.md.

Ta mission : ${mission}

Ce que le pilote te donne :
- dépôt GitHub : ${depot}
- racine du dépôt local : ${racine}
- dossier des copies de travail : ${copies}
- ton dossier d'essai, hors du dépôt : ${essais}/${dossier}
- interpréteur Python : ${python}
- commande GitHub : ${gh}
- issues auxquelles aucun agent ne touche : ${exclues.map(n => '#' + n).join(', ')} (le salon des agents est l'issue #${SALON})
- tu es lancé par le script .claude/workflows/equipe-issues.js
${complement || ''}${locales ? '\nConsignes propres à ce poste :\n' + locales + '\n' : ''}
Ta réponse finale est la donnée structurée demandée, rien d'autre.`
}

const S_ECLAIREUR = {
  type: 'object',
  properties: {
    issue: { type: 'integer', description: "numéro de l'issue ouverte, ou 0 si aucune" },
    titre: { type: 'string' },
    resume: { type: 'string', description: "ce qui a été trouvé, ou pourquoi rien n'a été ouvert" },
  },
  required: ['issue', 'titre', 'resume'],
}
const S_TRI = {
  type: 'object',
  properties: {
    decision: { type: 'string', enum: ['prête', 'à préciser', "à décider par l'équipe", 'fermée', 'déjà corrigée', 'déjà prise', 'impossible'] },
    taille: { type: 'string', enum: ['petite', 'moyenne', 'grande', 'sans objet'] },
    resume: { type: 'string' },
  },
  required: ['decision', 'taille', 'resume'],
}
const S_DEV = {
  type: 'object',
  properties: {
    pr: { type: 'integer', description: 'numéro de la pull request, ou 0 si aucune' },
    branche: { type: 'string' },
    commit: { type: 'string' },
    resume: { type: 'string' },
  },
  required: ['pr', 'branche', 'commit', 'resume'],
}
const S_RELECTURE = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['approuvée', 'à corriger', 'impossible'], description: '« impossible » : rien publié (fiche introuvable, copie sale, pull request déjà prise)' },
    points: { type: 'integer', description: 'nombre de points à corriger' },
    resume: { type: 'string' },
  },
  required: ['verdict', 'points', 'resume'],
}
const S_RECETTE = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['conforme', 'non conforme', 'impossible'], description: '« impossible » : rien publié' },
    tests: { type: 'string', description: "résultat mesuré de la famille des tests d'intégration" },
    controle: { type: 'string', enum: ['vert', 'rouge', 'en cours', 'absent'], description: 'état du contrôle automatique lu sur la pull request' },
    resume: { type: 'string' },
  },
  required: ['verdict', 'tests', 'controle', 'resume'],
}

async function traiter(n, _origine, rang) {
  const tri = await agent(
    consigne('trieur', `trier l'issue #${n}.`, `tri-${n}`,
      `- ta copie de travail pour reproduire : ${copies}/tri-${n}\n- port de ton serveur d'essai, s'il t'en faut un : ${port('trieur', rang)}\n`),
    { label: `tri #${n}`, phase: 'Tri', schema: S_TRI, model: 'opus' })
  if (!tri || tri.decision !== 'prête') {
    log(`#${n} : tri « ${tri ? tri.decision : 'sans réponse'} », pas de correction`)
    return { issue: n, arret: 'tri', tri }
  }

  const dev = await agent(
    consigne('developpeur', `corriger l'issue #${n}, triée « prête », et ouvrir la pull request (première passe de ta fiche).`, `correction-${n}`,
      `- port de ton serveur d'essai, s'il t'en faut un : ${port('developpeur', rang)}\n`),
    { label: `correction #${n}`, phase: 'Correction', schema: S_DEV, model: 'opus' })
  if (!dev || !Number.isInteger(dev.pr) || dev.pr <= 0) {
    log(`#${n} : pas de pull request (${dev ? dev.resume : 'développeur sans réponse'})`)
    return { issue: n, arret: 'correction', tri, dev }
  }

  // Reprise par le développeur ; rend faux si elle n'a pas abouti sur la même pull request.
  async function reprendre(apres) {
    const quoi = apres === 'relecture' ? 'la dernière relecture « à corriger »' : 'le dernier commentaire « Recette : non conforme »'
    const reprise = await agent(
      consigne('developpeur', `reprendre ta pull request #${dev.pr} (issue #${n}) d'après ${quoi} (passes suivantes de ta fiche). Ta copie de travail existe déjà : ${copies}/issue-${n}.`, `correction-${n}`,
        `- port de ton serveur d'essai, s'il t'en faut un : ${port('developpeur', rang)}\n`),
      { label: `reprise #${dev.pr} (après ${apres}, tour ${tour})`, phase: 'Correction', schema: S_DEV, model: 'opus' })
    if (!reprise || reprise.pr !== dev.pr) {
      log(`#${n} : reprise de #${dev.pr} sans réponse ou abandonnée (${reprise ? reprise.resume : 'sans réponse'})`)
      return false
    }
    return true
  }

  let relecture = null
  let recette = null
  let arret = null
  let tour = 0
  let recettes = 0
  // Chaque passage consomme un tour et tours est borné : la boucle se termine toujours.
  while (tour < tours) {
    tour++
    recette = null
    relecture = await agent(
      consigne('relecteur', `relire la pull request #${dev.pr}, qui répond à l'issue #${n}.`, `relecture-${n}`,
        `- tour de relecture : ${tour} sur ${tours} permis\n- port de ton serveur d'essai, s'il t'en faut un : ${port('relecteur', rang)}\n`),
      { label: `relecture #${dev.pr} (tour ${tour})`, phase: 'Relecture', schema: S_RELECTURE, model: 'opus' })
    if (!relecture || relecture.verdict === 'impossible') { arret = 'relecture impossible ou sans réponse'; break }
    if (relecture.verdict !== 'approuvée') {
      if (tour >= tours) { arret = 'tours épuisés'; break }
      if (!(await reprendre('relecture'))) { arret = 'reprise'; break }
      continue
    }
    recettes++
    recette = await agent(
      consigne('recetteur', `recetter la pull request #${dev.pr}, relue et approuvée, qui répond à l'issue #${n}.`, `recette-${n}`,
        `- port de ton serveur d'essai, s'il t'en faut un : ${port('recetteur', rang)}\n`),
      { label: `recette #${dev.pr} (tour ${tour})`, phase: 'Recette', schema: S_RECETTE, model: 'opus' })
    if (!recette || recette.verdict === 'impossible') { arret = 'recette impossible ou sans réponse'; break }
    if (recette.verdict === 'conforme') break
    if (recettes >= 2 || tour >= tours) { arret = 'recettes non conformes'; break }
    if (!(await reprendre('recette'))) { arret = 'reprise'; break }
  }

  const prete = !arret && !!relecture && relecture.verdict === 'approuvée'
    && !!recette && recette.verdict === 'conforme' && recette.controle === 'vert'
  if (!arret && !prete) arret = recette ? `contrôle automatique « ${recette.controle} »` : 'tours épuisés'
  log(`#${n} : pull request #${dev.pr}, relecture « ${relecture ? relecture.verdict : 'sans réponse'} » en ${tour} tour(s), recette « ${recette ? recette.verdict : 'non faite'} »${arret ? `, arrêt : ${arret}` : ''}`)
  return { issue: n, pr: dev.pr, branche: dev.branche, tours: tour, tri, dev, relecture, recette, arret, prete_a_fusionner: prete }
}

phase('Éclaireurs')
let ouvertes = []
if (angles.length) {
  const trouvailles = (await parallel(angles.map((angle, i) => () => agent(
    consigne('eclaireur', `chercher un défaut réel sous l'angle suivant, et ouvrir au plus une issue : ${angle}`, `eclaireur-${i + 1}`,
      `- ta copie de travail pour reproduire : ${copies}/eclaireur-${i + 1}\n- port de ton serveur d'essai, s'il t'en faut un : ${9000 + i}\n`),
    { label: `éclaireur ${i + 1}`, phase: 'Éclaireurs', schema: S_ECLAIREUR, model: 'opus' })
    .then(r => (r ? { angle, ...r } : null))))).filter(Boolean)
  for (const t of trouvailles) log(t.issue ? `issue #${t.issue} ouverte : ${t.titre}` : `aucune issue pour l'angle « ${t.angle} » : ${t.resume}`)
  ouvertes = trouvailles.filter(t => Number.isInteger(t.issue) && t.issue > 0).map(t => t.issue)
  if (trouvailles.length < angles.length) log(`${angles.length - trouvailles.length} éclaireur(s) sans réponse`)
}

const aTraiter = [...new Set([...issuesDonnees, ...ouvertes])].filter(n => !exclues.includes(n))
const ecartees = [...new Set([...issuesDonnees, ...ouvertes])].filter(n => exclues.includes(n))
if (ecartees.length) log(`issues exclues, non traitées : ${ecartees.map(n => '#' + n).join(', ')}`)
log(aTraiter.length ? `issues à traiter : ${aTraiter.map(n => '#' + n).join(', ')}` : 'aucune issue à traiter')

const suivis = (await pipeline(aTraiter, traiter)).map((s, i) => s || { issue: aTraiter[i], arret: 'erreur' })

function raison(s) {
  if (s.arret === 'erreur') return 'erreur du script pour cette issue'
  if (s.arret === 'correction') return s.dev ? s.dev.resume : 'développeur sans réponse'
  return s.tri ? s.tri.decision + ' : ' + s.tri.resume : 'trieur sans réponse'
}

return {
  issues_ouvertes: ouvertes,
  pretes_a_fusionner: suivis.filter(s => s.prete_a_fusionner).map(s => ({
    issue: s.issue, pr: s.pr, branche: s.branche, controle: s.recette.controle,
  })),
  a_reprendre: suivis.filter(s => s.pr && !s.prete_a_fusionner).map(s => ({
    issue: s.issue, pr: s.pr, tours: s.tours, arret: s.arret || 'tours épuisés',
    relecture: s.relecture ? s.relecture.verdict : 'sans réponse',
    recette: s.recette ? s.recette.verdict : 'non faite',
    controle: s.recette ? s.recette.controle : 'non lu',
  })),
  sans_pull_request: suivis.filter(s => !s.pr).map(s => ({ issue: s.issue, arret: s.arret, raison: raison(s) })),
  rappel: "Aucune fusion n'a été faite : elle revient à l'équipe, après relecture et approbation par un autre membre que celui qui a lancé le script, et contrôle automatique au vert au moment de fusionner.",
}
