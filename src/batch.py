"""Print a compact batch of unlabelled films for classification."""
import json, sys, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sample = json.loads((ROOT/"data"/"sample.json").read_text())
lab = ROOT/"data"/"labels.jsonl"
done = set()
if lab.exists():
    for line in lab.read_text().split("\n"):
        if line.strip():
            done.add(json.loads(line)["id"])

per_year = int(sys.argv[1]) if len(sys.argv) > 1 else 8
limit    = int(sys.argv[2]) if len(sys.argv) > 2 else 120
if len(sys.argv) > 3:
    lab = ROOT/"data"/sys.argv[3]
    done = set()
    if lab.exists():
        for line in lab.read_text().split("\n"):
            if line.strip():
                done.add(json.loads(line)["id"])

by_year = collections.defaultdict(list)
for r in sorted(sample, key=lambda r: -r["notability"]):
    by_year[r["year"]].append(r)

# take the next unlabelled film per year, round-robin across years, most notable first
queue = []
for rank in range(per_year):
    for y in sorted(by_year):
        pool = [r for r in by_year[y] if r["id"] not in done]
        if rank < len(pool):
            queue.append(pool[rank])
queue = queue[:limit]

for r in queue:
    g = "/".join(r["genres"][:3]) or "-"
    n = 26 if r["year"] < 1975 else 0     # post-1975 titles are usually identifiable without the lead
    ex = " ".join(r["extract"].split()[:n])
    c = r.get("country", "")
    head = f'{r["id"]}|{r["title"]} ({r["year"]}{"/" + c if c else ""}) [{g}]'
    print(head + (f' :: {ex}' if ex else ''))
print(f"\n# {len(queue)} films | {len(done)} already labelled | {len(sample)} in sample", file=sys.stderr)
