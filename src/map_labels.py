#!/usr/bin/env python3
"""Carry hand labels across a change of corpus.

The 613 hand labels are keyed to the old Wikipedia-corpus slugs. TMDB uses its own ids,
so without a re-key they are stranded — and they are the only independent reference
`validate.py` has. This joins them on normalised (title, year) with a small year window.

    python3 src/map_labels.py --old data/labels.jsonl --sample data/sample.json \\
                             --old-sample data/sample_wikipedia.json
"""
import argparse, json, re, unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ARTICLES = re.compile(r"^(the|a|an|le|la|les|el|los|il|der|die|das)\s+")


def norm(title):
    t = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"&", " and ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    t = ARTICLES.sub("", t)
    t = re.sub(r"\s+(part|pt|vol|volume)\s+(\d+|i{1,3}v?|i?[vx])$", r" \2", t)
    return re.sub(r"\s+", " ", t).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=str(ROOT / "data" / "labels.jsonl"))
    ap.add_argument("--old-sample", default=str(ROOT / "data" / "sample_wikipedia.json"),
                    help="the sample.json the old labels were made against")
    ap.add_argument("--sample", default=str(ROOT / "data" / "sample.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "labels_mapped.jsonl"))
    ap.add_argument("--window", type=int, default=1, help="years of slack on the join")
    a = ap.parse_args()

    for p in (a.old, a.old_sample, a.sample):
        if not Path(p).exists():
            raise SystemExit(f"{p} not found")

    old_films = {r["id"]: r for r in json.loads(Path(a.old_sample).read_text())}
    new_films = json.loads(Path(a.sample).read_text())
    labels = [json.loads(l) for l in Path(a.old).read_text().split("\n") if l.strip()]

    index = defaultdict(list)
    for r in new_films:
        index[norm(r["title"])].append(r)

    out, unmatched, ambiguous = [], [], 0
    for lab in labels:
        src = old_films.get(lab["id"])
        if not src:
            unmatched.append((lab["id"], "not in old sample")); continue
        cands = [r for r in index.get(norm(src["title"]), [])
                 if abs(r["year"] - src["year"]) <= a.window]
        if not cands:
            unmatched.append((src["title"], src["year"])); continue
        if len(cands) > 1:
            ambiguous += 1
            cands.sort(key=lambda r: (abs(r["year"] - src["year"]), -r.get("vote_count", 0)))
        rec = dict(lab)
        rec["id"] = cands[0]["id"]
        rec["mapped_from"] = lab["id"]
        out.append(rec)

    # a TMDB id can only carry one label; keep the first, drop later collisions
    seen, final = set(), []
    for r in out:
        if r["id"] in seen:
            continue
        seen.add(r["id"]); final.append(r)

    Path(a.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in final) + "\n")
    print(f"labels in       {len(labels)}")
    print(f"matched         {len(final)}  ({100*len(final)/len(labels):.0f}%)"
          f"   [{ambiguous} resolved from multiple candidates,"
          f" {len(out)-len(final)} collided on the same film]")
    print(f"unmatched       {len(unmatched)}")
    for t, y in unmatched[:12]:
        print(f"    {t} ({y})")
    if len(unmatched) > 12:
        print(f"    … and {len(unmatched)-12} more")
    print(f"\nwrote {a.out}")
    print("These are the reference labels for validate.py. Anything unmatched is simply "
          "absent from the new corpus — usually a silent short TMDB never catalogued.")


if __name__ == "__main__":
    main()
