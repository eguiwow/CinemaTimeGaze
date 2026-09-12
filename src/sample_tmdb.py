#!/usr/bin/env python3
"""Build data/sample.json from the TMDB corpus.

Same output shape as src/sample.py, plus `country`, `countries` and `language`, so
everything downstream keeps working and the country/genre slices become possible.

    python3 src/sample_tmdb.py 50                 # 50 films per release year
    python3 src/sample_tmdb.py 50 --min-per-country 3
"""
import argparse, collections, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmdb_fetch import REGION_OF               # one country->region map, defined once

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "tmdb_films.jsonl"
OUT = ROOT / "data" / "sample.json"

DOCU = {"documentary"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("per_year", nargs="?", type=int, default=50)
    ap.add_argument("--min-year", type=int, default=1900)
    ap.add_argument("--min-films", type=int, default=5,
                    help="drop years thinner than this rather than padding")
    ap.add_argument("--min-words", type=int, default=8,
                    help="minimum overview length; 0 keeps films with no summary")
    ap.add_argument("--min-per-country", type=int, default=0,
                    help="reserve this many slots per country per year, then fill by votes")
    ap.add_argument("--min-per-region", type=int, default=0,
                    help="reserve this many slots per region per year, filled before countries. "
                         "This is the lever that stops a global corpus collapsing back into "
                         "Hollywood: vote counts are a popularity measure, and popularity is "
                         "not distributed evenly across the world's film industries.")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"{SRC} not found — run src/tmdb_fetch.py first")

    rows, dropped = [], collections.Counter()
    for line in SRC.read_text().split("\n"):
        if not line.strip():
            continue
        m = json.loads(line)
        if m["year"] < a.min_year:
            dropped["before min-year"] += 1; continue
        if {g.lower() for g in m["genres"]} & DOCU:
            dropped["documentary"] += 1; continue
        ov = m.get("overview") or ""
        if a.min_words and len(ov.split()) < a.min_words:
            dropped["no usable summary"] += 1; continue
        rows.append({
            "id": m["id"],
            "tmdb_id": m["tmdb_id"],
            "title": m["title"],
            # kept so the page can find "Sen to Chihiro" as well as "Spirited Away".
            # Only stored when it actually differs, which is most of world cinema.
            "original_title": (m.get("original_title")
                               if (m.get("original_title") or "") != m["title"] else None),
            "year": m["year"],
            "genres": m["genres"],
            "extract": ov,
            "country": m["country"],
            "countries": m["countries"],
            "region": REGION_OF.get(m["country"], "other"),
            "language": m.get("language"),
            "vote_count": m.get("vote_count", 0),
            "notability": m.get("vote_count", 0),
        })

    by_year = collections.defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)

    sample, thin = [], []
    for y in sorted(by_year):
        pool = sorted(by_year[y], key=lambda r: -r["notability"])
        if len(pool) < a.min_films:
            thin.append((y, len(pool))); continue
        picked, taken = [], set()
        # regions first, then countries, then straight popularity. Each pass only ever adds
        # films the later passes would have had to leave out, so the quota is never exceeded.
        if a.min_per_region:
            per = collections.defaultdict(int)
            for r in pool:
                if per[r["region"]] < a.min_per_region and len(picked) < a.per_year:
                    picked.append(r); taken.add(r["id"]); per[r["region"]] += 1
        if a.min_per_country:
            per = collections.defaultdict(int)
            for r in pool:
                if r["id"] in taken:
                    per[r["country"]] += 1; continue
                if per[r["country"]] < a.min_per_country and len(picked) < a.per_year:
                    picked.append(r); taken.add(r["id"]); per[r["country"]] += 1
        for r in pool:
            if len(picked) >= a.per_year:
                break
            if r["id"] not in taken:
                picked.append(r); taken.add(r["id"])
        sample.extend(picked)

    Path(a.out).write_text(json.dumps(sample, ensure_ascii=False))

    yrs = collections.Counter(r["year"] for r in sample)
    ctry = collections.Counter(r["country"] for r in sample)
    print(f"corpus        {len(rows)} eligible films"
          f"   (dropped: {', '.join(f'{v} {k}' for k, v in dropped.most_common()) or 'none'})")
    print(f"sampled       {len(sample)} across {len(yrs)} years ({min(yrs)}-{max(yrs)})")
    print(f"full quota    {sum(1 for y in yrs if yrs[y] == a.per_year)} years at {a.per_year}"
          f", {sum(1 for y in yrs if yrs[y] < a.per_year)} short")
    if thin:
        print(f"dropped years {', '.join(f'{y}({n})' for y, n in thin)}")
    print(f"median words  {sorted(len(r['extract'].split()) for r in sample)[len(sample)//2]}")
    reg = collections.Counter(r["region"] for r in sample)
    print("\nregion mix")
    for c, n in reg.most_common():
        print(f"  {c:<16}{n:>6}  {100*n/len(sample):>5.1f}%")
    print("\ncountry mix")
    for c, n in ctry.most_common():
        print(f"  {c}  {n:>5}  {100*n/len(sample):>5.1f}%"
              + ("   <- everything else is a rounding error" if n / len(sample) > .8 else ""))
    top = ctry.most_common(1)[0]
    if top[1] / len(sample) > .35:
        print(f"\nNOTE  {top[0]} is {100*top[1]/len(sample):.0f}% of the sample. A country or "
              f"region slice will work, but any\n      unfiltered figure is mostly a statement "
              f"about {top[0]}. --min-per-region evens this out.")
    print(f"\nwrote {a.out}")
    print("next:  python3 src/map_labels.py   (carry the hand labels over)")


if __name__ == "__main__":
    main()
