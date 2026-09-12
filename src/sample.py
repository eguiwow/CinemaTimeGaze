"""Build the stratified sample from the Wikipedia movie dataset.

Popularity note: this dataset carries no vote counts, so notability is proxied by
Wikipedia lead length + cast-list size. Crude, but it reliably separates films
people wrote about from films that got a stub.
"""
import json, re, sys, collections
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "movies.json"
OUT = ROOT / "data" / "sample.json"

PER_YEAR = int(sys.argv[1]) if len(sys.argv) > 1 else 50
MIN_YEAR = int(sys.argv[2]) if len(sys.argv) > 2 else 1900
MIN_PER_YEAR = 5          # drop years too thin to plot honestly

DOC_GENRES = {"documentary", "docudrama", "concert film", "nature documentary"}

def notability(m):
    ex = len(m.get("extract", "").split())
    cast = len(m.get("cast") or [])
    return ex + 3 * min(cast, 20)

def main():
    movies = json.loads(RAW.read_text())
    seen, rows = set(), []
    for m in movies:
        y, t = m.get("year"), (m.get("title") or "").strip()
        if not y or not t or y < MIN_YEAR:
            continue
        ex = (m.get("extract") or "").strip()
        if len(ex.split()) < 15:              # no usable text to judge from
            continue
        gl = {g.lower() for g in (m.get("genres") or [])}
        if gl & DOC_GENRES:
            continue
        key = (t.lower(), y)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "id": re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:60] + f"-{y}",
            "title": t, "year": y,
            "genres": m.get("genres") or [],
            "extract": ex,
            "href": m.get("href"),
            "notability": notability(m),
        })

    by_year = collections.defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)

    sample, thin = [], []
    for y in sorted(by_year):
        pool = sorted(by_year[y], key=lambda r: -r["notability"])
        if len(pool) < MIN_PER_YEAR:
            thin.append((y, len(pool)))
            continue
        sample.extend(pool[:PER_YEAR])

    OUT.write_text(json.dumps(sample, ensure_ascii=False, indent=None))
    yrs = collections.Counter(r["year"] for r in sample)
    full = sum(1 for y in yrs if yrs[y] == PER_YEAR)
    print(f"eligible films: {len(rows)}")
    print(f"sampled: {len(sample)} across {len(yrs)} years ({min(yrs)}-{max(yrs)})")
    print(f"years at full quota of {PER_YEAR}: {full}; short years: {len(yrs)-full}")
    if thin:
        print("dropped (under %d films): %s" % (MIN_PER_YEAR, ", ".join(f"{y}({n})" for y, n in thin)))
    print("median extract words:", sorted(len(r["extract"].split()) for r in sample)[len(sample)//2])

if __name__ == "__main__":
    main()
