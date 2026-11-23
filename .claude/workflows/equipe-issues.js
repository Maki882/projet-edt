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
// Arguments (tous facultatifs) :
//   depot    : dépôt GitHub « propriétaire/nom » (sinon l'agent le lit par « gh repo view »)
//   racine   : racine du dépôt local                       (défaut : « . »)
//   copies   : dossier où créer les copies de travail      (défaut : « ../copies-edt »)
//   essais   : dossier d'essai hors du dépôt               (défaut : « ../essais-edt »)
//   python   : interpréteur Python                         (défaut : « python »)
//   gh       : commande GitHub                             (défaut : « gh »)
//   locales  : consignes propres au poste, ajoutées à chaque agent
//   angles   : angles de recherche des éclaireurs, un éclaireur par angle (défaut : aucun)
//   issues   : numéros d'issues à traiter en plus de celles des éclaireurs
//   exclues  : numéros d'issues auxquelles aucun agent ne touche
//   tours    : nombre de tours de relecture permis par pull request (défaut : 3)

const A = args || {}
const depot = A.depot || '(à lire par « gh repo view --json nameWithOwner »)'
const racine = A.racine || '.'
const copies = A.copies || '../copies-edt'
const essais = A.essais || '../essais-edt'
const python = A.python || 'python'
const gh = A.gh || 'gh'
const locales = A.locales || ''
const angles = A.angles || []
const exclues = A.exclues || []
const tours = A.tours || 3

function consigne(role, mission, dossier, complement) {
  return `Tu tiens le rôle « ${role} » dans l'équipe d'agents qui travaille autour des issues GitHub du projet EDT.

Ta fiche de rôle est le fichier ${racine}/.claude/agents/${role}.md : lis-la en entier avant toute action et applique-la exactement. L'organisation d'ensemble est décrite dans ${racine}/docs/organisation-agents.md.

Ta mission : ${mission}

Ce que le pilote te donne :
- dépôt GitHub : ${depot}
- racine du dépôt local : ${racine}
- dossier des copies de travail : ${copies}
- ton dossier d'essai, hors du dépôt : ${essais}/${dossier}
- interpréteur Python : ${python}
- commande GitHub : ${gh}
- issues auxquelles aucun agent ne touche : ${exclues.length ? exclues.map(n => '#' + n).join(', ') : 'aucune'}
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
    decision: { type: 'string', enum: ['prête', 'à préciser', "à décider par l'équipe", 'fermée'] },
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
    verdict: { type: 'string', enum: ['approuvée', 'à corriger'] },
    points: { type: 'integer', description: 'nombre de points à corriger' },
    resume: { type: 'string' },
  },
  required: ['verdict', 'points', 'resume'],
}
const S_RECETTE = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['conforme', 'non conforme'] },
    tests: { type: 'string', description: "résultat mesuré de la famille des tests d'intégration" },
    controle: { type: 'string', description: 'état du contrôle automatique lu sur la pull request' },
    resume: { type: 'string' },
  },
  required: ['verdict', 'tests', 'controle', 'resume'],
}

async function traiter(n, _origine, rang) {
  const tri = await agent(
    consigne('trieur', `trier l'issue #${n}.`, `tri-${n}`),
    { label: `tri #${n}`, phase: 'Tri', schema: S_TRI, model: 'opus' })
  if (!tri || tri.decision !== 'prête') {
    log(`#${n} : tri « ${tri ? tri.decision : 'sans réponse'} », pas de correction`)
    return { issue: n, arret: 'tri', tri }
  }

  const dev = await agent(
    consigne('developpeur', `corriger l'issue #${n}, triée « prête », et ouvrir la pull request (première passe de ta fiche).`, `correction-${n}`),
    { label: `correction #${n}`, phase: 'Correction', schema: S_DEV, model: 'opus' })
  if (!dev || !dev.pr) {
    log(`#${n} : pas de pull request (${dev ? dev.resume : 'sans réponse'})`)
    return { issue: n, arret: 'correction', tri, dev }
  }

  let relecture = null
  let recette = null
  let tour = 0
  let recettes = 0
  while (true) {
    tour++
    relecture = await agent(
      consigne('relecteur', `relire la pull request #${dev.pr}, qui répond à l'issue #${n}.`, `relecture-${n}`,
        `- tour de relecture : ${tour} sur ${tours} permis\n`),
      { label: `relecture #${dev.pr} (tour ${tour})`, phase: 'Relecture', schema: S_RELECTURE, model: 'opus' })
    if (!relecture) break
    if (relecture.verdict !== 'approuvée') {
      if (tour >= tours) break
      await agent(
        consigne('developpeur', `reprendre ta pull request #${dev.pr} (issue #${n}) d'après la dernière relecture « à corriger » (passes suivantes de ta fiche). Ta copie de travail existe déjà : ${copies}/issue-${n}.`, `correction-${n}`),
        { label: `reprise #${dev.pr} (après relecture ${tour})`, phase: 'Correction', schema: S_DEV, model: 'opus' })
      continue
    }
    recettes++
    recette = await agent(
      consigne('recetteur', `recetter la pull request #${dev.pr}, relue et approuvée, qui répond à l'issue #${n}.`, `recette-${n}`,
        `- port de ton serveur d'essai, s'il t'en faut un : ${9100 + rang}\n`),
      { label: `recette #${dev.pr}`, phase: 'Recette', schema: S_RECETTE, model: 'opus' })
    if (!recette || recette.verdict === 'conforme' || recettes >= 2 || tour >= tours) break
    await agent(
      consigne('developpeur', `reprendre ta pull request #${dev.pr} (issue #${n}) d'après le dernier commentaire « Recette : non conforme » (passes suivantes de ta fiche). Ta copie de travail existe déjà : ${copies}/issue-${n}.`, `correction-${n}`),
      { label: `reprise #${dev.pr} (après recette)`, phase: 'Correction', schema: S_DEV, model: 'opus' })
  }

  const prete = !!relecture && relecture.verdict === 'approuvée' && !!recette && recette.verdict === 'conforme'
  log(`#${n} : pull request #${dev.pr}, relecture « ${relecture ? relecture.verdict : 'sans réponse'} » en ${tour} tour(s), recette « ${recette ? recette.verdict : 'non faite'} »`)
  return { issue: n, pr: dev.pr, branche: dev.branche, tours: tour, tri, relecture, recette, prete_a_fusionner: prete }
}

phase('Éclaireurs')
let ouvertes = []
if (angles.length) {
  const trouvailles = (await parallel(angles.map((angle, i) => () => agent(
    consigne('eclaireur', `chercher un défaut réel sous l'angle suivant, et ouvrir au plus une issue : ${angle}`, `eclaireur-${i + 1}`,
      `- port de ton serveur d'essai, s'il t'en faut un : ${9150 + i}\n`),
    { label: `éclaireur ${i + 1}`, phase: 'Éclaireurs', schema: S_ECLAIREUR, model: 'opus' })
    .then(r => (r ? { angle, ...r } : null))))).filter(Boolean)
  for (const t of trouvailles) log(t.issue ? `issue #${t.issue} ouverte : ${t.titre}` : `aucune issue pour l'angle « ${t.angle} » : ${t.resume}`)
  ouvertes = trouvailles.filter(t => t.issue).map(t => t.issue)
  if (trouvailles.length < angles.length) log(`${angles.length - trouvailles.length} éclaireur(s) sans réponse`)
}

const aTraiter = [...new Set([...(A.issues || []), ...ouvertes])].filter(n => !exclues.includes(n))
log(aTraiter.length ? `issues à traiter : ${aTraiter.map(n => '#' + n).join(', ')}` : 'aucune issue à traiter')

const suivis = (await pipeline(aTraiter, traiter)).map((s, i) => s || { issue: aTraiter[i], arret: 'erreur' })

return {
  issues_ouvertes: ouvertes,
  pretes_a_fusionner: suivis.filter(s => s.prete_a_fusionner).map(s => ({ issue: s.issue, pr: s.pr, branche: s.branche })),
  a_reprendre: suivis.filter(s => s.pr && !s.prete_a_fusionner).map(s => ({
    issue: s.issue, pr: s.pr, tours: s.tours,
    relecture: s.relecture ? s.relecture.verdict : 'sans réponse',
    recette: s.recette ? s.recette.verdict : 'non faite',
  })),
  sans_pull_request: suivis.filter(s => !s.pr).map(s => ({
    issue: s.issue, arret: s.arret,
    raison: s.arret === 'correction' && s.dev ? s.dev.resume
      : s.tri ? s.tri.decision + ' : ' + s.tri.resume : 'sans réponse',
  })),
  rappel: "Aucune fusion n'a été faite : elle revient à l'équipe, une fois le contrôle automatique au vert.",
}
