"""Transforme le questionnaire JSON en Markdown. Les identifiants stockés sont conservés
(les doublons supprimés laissent un trou dans la numérotation, volontairement)."""
import json
import sys
from collections import Counter

src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))
sections = data["sections"]
ORDER = {"bloquante": 0, "importante": 1, "detail": 2}
MARK = {"bloquante": "🔴", "importante": "🟠", "detail": "⚪"}

total = Counter(q["priorite"] for s in sections for q in s["questions"])
n_total = sum(total.values())
lines = ["# Questions avant le développement\n",
    "Questionnaire à remplir avant de coder la première version. Chaque question propose un **défaut** : "
    "si la réponse est « OK », ce défaut sera appliqué. Répondre par le numéro suffit (ex. « 3.2 : non, plutôt… »). "
    "Les numéros sont stables : les trous correspondent à des doublons retirés.\n",
    f"**{n_total} questions** : 🔴 {total['bloquante']} bloquantes (impossible de bien coder sans réponse), "
    f"🟠 {total['importante']} importantes (changent la conception), ⚪ {total['detail']} détails (réglables plus tard).\n",
    "## Sommaire\n"]
for s in sections:
    c = Counter(q["priorite"] for q in s["questions"])
    num = s["questions"][0]["id"].split(".")[0]
    lines.append(f"{num}. {s['titre']} ({len(s['questions'])} questions, {c['bloquante']} bloquantes)")
lines.append("")
for s in sections:
    num = s["questions"][0]["id"].split(".")[0]
    lines.append(f"## {num}. {s['titre']}\n")
    for q in sorted(s["questions"], key=lambda q: (ORDER[q["priorite"]], int(q["id"].split(".")[1]))):
        lines.append(f"**{q['id']}** {MARK[q['priorite']]} {q['question'].strip()}")
        lines.append(f"- *Pourquoi* : {q['pourquoi'].strip()}")
        lines.append(f"- *Défaut proposé* : {q['defaut'].strip()}\n")
open(dst, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print(f"{n_total} questions, {len(sections)} sections -> {dst}", dict(total))
