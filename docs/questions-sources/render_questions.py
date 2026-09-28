"""Transforme le questionnaire JSON produit par le workflow en Markdown numéroté."""
import json
import sys
from collections import Counter

src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src, encoding="utf-8"))
sections = data["questionnaire"]["sections"] if "questionnaire" in data else data["sections"]

ORDER = {"bloquante": 0, "importante": 1, "detail": 2}
MARK = {"bloquante": "🔴", "importante": "🟠", "detail": "⚪"}

lines = []
total = Counter()
for s in sections:
    for q in s["questions"]:
        total[q["priorite"]] += 1
n_total = sum(total.values())

lines.append("# Questions avant le développement\n")
lines.append(
    "Questionnaire à remplir avant de coder la première version. Chaque question "
    "propose un **défaut** : si la réponse est « OK », ce défaut sera appliqué. "
    "Répondre par le numéro de la question suffit (ex. « 3.2 : non, plutôt… »).\n"
)
lines.append(
    f"**{n_total} questions** : 🔴 {total['bloquante']} bloquantes (impossible de bien coder sans réponse), "
    f"🟠 {total['importante']} importantes (changent la conception), "
    f"⚪ {total['detail']} détails (réglables plus tard).\n"
)
lines.append("## Sommaire\n")
for i, s in enumerate(sections, 1):
    c = Counter(q["priorite"] for q in s["questions"])
    lines.append(f"{i}. {s['titre']} ({len(s['questions'])} questions, {c['bloquante']} bloquantes)")
lines.append("")

for i, s in enumerate(sections, 1):
    lines.append(f"## {i}. {s['titre']}\n")
    qs = sorted(s["questions"], key=lambda q: ORDER[q["priorite"]])
    for j, q in enumerate(qs, 1):
        lines.append(f"**{i}.{j}** {MARK[q['priorite']]} {q['question'].strip()}")
        lines.append(f"- *Pourquoi* : {q['pourquoi'].strip()}")
        lines.append(f"- *Défaut proposé* : {q['defaut'].strip()}")
        lines.append("")

open(dst, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print(f"{n_total} questions, {len(sections)} sections -> {dst}")
print(dict(total))
