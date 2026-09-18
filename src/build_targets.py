"""Expand labels into the weighted targets table the timeline reads."""
import argparse, json, collections, statistics
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--labels", default="labels.jsonl", help="file in data/, or a path")
ap.add_argument("--out", default="targets.json", help="file in data/, or a path")
args = ap.parse_args()
lab_path = Path(args.labels) if "/" in args.labels else ROOT/"data"/args.labels
out_path = Path(args.out) if "/" in args.out else ROOT/"data"/args.out

sample = {r["id"]: r for r in json.loads((ROOT/"data"/"sample.json").read_text())}
raw, orphan = [], 0
for l in lab_path.read_text().split("\n"):          # later lines win on duplicate ids
    if l.strip():
        r = json.loads(l)
        if r["id"] in sample:
            raw.append(r)
        else:
            orphan += 1
labels = list({r["id"]: r for r in raw}.values())

if not labels:
    raise SystemExit(
        f"\nNONE of the {orphan} labels in {lab_path.name} match data/sample.json.\n"
        "The labels belong to a different corpus. Either point --labels at the right file\n"
        "(e.g. labels_mapped.jsonl after a corpus switch) or re-classify the current sample.")
if orphan:
    share = 100 * len(labels) / (len(labels) + orphan)
    print(f"WARNING  {orphan} labels ignored — not in the current sample "
          f"({share:.0f}% of the label file matched)")
cover = 100 * len(labels) / len(sample)
if cover < 25:
    print(f"WARNING  only {len(labels)} of {len(sample)} sampled films are labelled "
          f"({cover:.1f}%) — the charts will be sparse and the facts strip will stay silent")

RAW_W = {"primary":1.0, "secondary":0.5, "minor":0.25}
PRESENT_TOL = 2          # |delta| <= 2 years counts as contemporary

films, targets = [], []
for lab in labels:
    f = sample[lab["id"]]
    films.append({"id":f["id"], "title":f["title"],
                  "original_title":f.get("original_title"), "year":f["year"],
                  "genres":f["genres"], "country":f.get("country"),
                  "countries":f.get("countries"), "region":f.get("region"),
                  "votes":f.get("vote_count") or 0,
                  "gaze":lab["gaze"],
                  "earthbound":lab["earthbound"], "confidence":lab["confidence"],
                  "basis":lab["basis"], "n_targets":len(lab["targets"]),
                  # contract C3: sample.json rows may carry source="wishlist"; carried onto
                  # targets.json verbatim so build_viz.py can flag them and the page can
                  # exclude them from aggregates. Omitted (not written as null) when absent,
                  # so a sample.json with no wishlist rows produces byte-identical output.
                  **({"source": f["source"]} if f.get("source") else {})})
    tot = sum(RAW_W[t["prominence"]] for t in lab["targets"]) or 1.0
    for i, t in enumerate(lab["targets"]):
        mid = (t["year_start"] + t["year_end"]) // 2
        d = mid - f["year"]
        targets.append({
            "film_id":f["id"], "seq":i, "title":f["title"], "release_year":f["year"],
            "year_start":t["year_start"], "year_end":t["year_end"], "year_mid":mid,
            "prominence":t["prominence"], "weight":round(RAW_W[t["prominence"]]/tot, 4),
            "delta":d,
            "direction":"present" if abs(d) <= PRESENT_TOL else ("back" if d < 0 else "forward"),
            "confidence":lab["confidence"], "basis":lab["basis"],
        })

out_path.write_text(json.dumps({"films":films,"targets":targets}, ensure_ascii=False))

# --- summary ---
gz = collections.Counter(f["gaze"] for f in films)
print(f"labels: {lab_path.name} -> {out_path.name}")
print(f"films: {len(films)}   targets: {len(targets)}   ({len(targets)/len(films):.2f} per film)")
print("gaze:", dict(gz))
print("basis:", dict(collections.Counter(f["basis"] for f in films)))
dirs = collections.Counter()
for t in targets: dirs[t["direction"]] += 1
print("target directions:", dict(dirs))
back = [t["delta"] for t in targets if t["direction"]=="back"]
fwd  = [t["delta"] for t in targets if t["direction"]=="forward"]
if back: print(f"lookback  n={len(back)} median={statistics.median(back):.0f}y  mean={statistics.mean(back):.0f}y  range={min(back)}..{max(back)}")
if fwd:  print(f"lookahead n={len(fwd)} median={statistics.median(fwd):.0f}y  mean={statistics.mean(fwd):.0f}y  range={min(fwd)}..{max(fwd)}")
