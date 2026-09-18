#!/usr/bin/env python3
"""Build data/sample.json from the TMDB corpus.

Same output shape as src/sample.py, plus `country`, `countries` and `language`, so
everything downstream keeps working and the country/genre slices become possible.

    python3 src/sample_tmdb.py 50                 # 50 films per release year
    python3 src/sample_tmdb.py 50 --min-per-country 3
    python3 src/sample_tmdb.py 50 --rank in-country --min-per-region 8

Ranking (--rank):
    votes       (default, unchanged since v1) rank each year's pool by raw TMDB vote_count.
                Simple and reproducible, but vote_count is a popularity signal that travels
                badly between film industries — a well-known Japanese film can carry a
                fraction of the votes of an obscure American one, so an unweighted global
                pool is dominated by whichever country TMDB users vote on most (the US).
    in-country  rank each film by where its vote_count falls within the distribution of
                vote_count for ALL eligible films from the SAME country (a percentile,
                0..1, computed once over the whole corpus — every year that country has
                a film in, not a rolling window). Each cinema is then judged against
                itself: a top decile Japanese film competes fairly with a top decile
                American one, even though its raw vote_count is far smaller. Chosen over
                a per-decade or rolling-year window because most non-US countries have too
                few eligible films in an early decade for a windowed percentile to mean
                anything; the whole-corpus distribution is the smallest one that is stable
                for a niche cinema. The tradeoff: it can rank a well-voted 1920s French film
                below a barely-voted one, if France's *other* films (often clustered in
                later decades) are even better voted — the percentile is relative to the
                country's entire output, not to that year. --min-per-region/--min-per-country
                still apply on top of whichever ranking is chosen; they reserve slots before
                any ranking gets a say, in-country ranking only changes who fills what is left.
"""
import argparse, collections, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmdb_fetch import REGION_OF               # one country->region map, defined once

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "tmdb_films.jsonl"
OUT = ROOT / "data" / "sample.json"
WISHLIST = ROOT / "data" / "wishlist.json"
WISHLIST_FILMS = ROOT / "data" / "wishlist_films.jsonl"

DOCU = {"documentary"}

# statuses whose film is meant to be visible in the published sample already
WISHLIST_LIVE_STATUSES = {"resolved", "in_sample"}


def sample_row(m):
    """The one place a flattened TMDB film (a data/tmdb_films.jsonl row, or an equivalent
    dict from wishlist.py's cache) turns into a data/sample.json row. Shared so a wishlist
    film that bypasses the per-year sampling still comes out byte-shaped like a sampled one."""
    ov = m.get("overview") or ""
    return {
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
    }


def in_country_percentiles(rows):
    """Return {id: percentile} where percentile is the fraction of the SAME country's
    eligible films with a vote_count <= this film's, computed once over every year that
    country appears in (see the --rank docstring above for why whole-corpus, not windowed)."""
    by_country = collections.defaultdict(list)
    for r in rows:
        by_country[r["country"]].append(r["vote_count"])
    for c in by_country:
        by_country[c].sort()
    import bisect
    out = {}
    for r in rows:
        pool = by_country[r["country"]]
        n = len(pool)
        # rank position (1-indexed midpoint of the tie band) turned into a 0..1 fraction;
        # a country with exactly one eligible film gets that film a percentile of 1.0
        # (it IS the top of its own, tiny, distribution).
        lo = bisect.bisect_left(pool, r["vote_count"])
        hi = bisect.bisect_right(pool, r["vote_count"])
        out[r["id"]] = ((lo + hi) / 2) / n if n else 0.0
    return out


def load_wishlist_films(sample_ids_by_tmdb, no_wishlist, wishlist_path, films_path):
    """Wishlist films are appended OUTSIDE the per-year cap (contract C3/C6): a resolved
    wishlist film must never be silently dropped just because its year already filled its
    quota on votes. Returns (rows, skipped_dup, skipped_uncached)."""
    if no_wishlist or not wishlist_path.exists() or not films_path.exists():
        return [], 0, 0
    wl = json.loads(wishlist_path.read_text())
    live_ids = {w["tmdb_id"] for w in wl
               if w.get("tmdb_id") and w.get("status") in WISHLIST_LIVE_STATUSES}
    if not live_ids:
        return [], 0, 0
    cache = {}
    for line in films_path.read_text().split("\n"):
        if line.strip():
            m = json.loads(line)
            cache[m["tmdb_id"]] = m          # later lines win on a re-resolved film

    rows, dup = [], 0
    for tmdb_id in sorted(live_ids):
        if tmdb_id in sample_ids_by_tmdb:
            dup += 1; continue                # already in the sample on its own merits
        m = cache.get(tmdb_id)
        if m is None:
            continue                          # resolved but never cached — resolve() didn't run yet
        row = sample_row(m)
        row["source"] = "wishlist"
        rows.append(row)
    uncached = sum(1 for t in live_ids if t not in cache and t not in sample_ids_by_tmdb)
    return rows, dup, uncached


def main():
    ap = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("per_year", nargs="?", type=int, default=50)
    ap.add_argument("--min-year", type=int, default=1900)
    ap.add_argument("--min-films", type=int, default=5,
                    help="drop years thinner than this rather than padding")
    ap.add_argument("--min-words", type=int, default=8,
                    help="minimum overview length; 0 keeps films with no summary")
    ap.add_argument("--min-per-country", type=int, default=0,
                    help="reserve this many slots per country per year, then fill by rank")
    ap.add_argument("--min-per-region", type=int, default=0,
                    help="reserve this many slots per region per year, filled before countries. "
                         "This is the lever that stops a global corpus collapsing back into "
                         "Hollywood: vote counts are a popularity measure, and popularity is "
                         "not distributed evenly across the world's film industries.")
    ap.add_argument("--rank", choices=["votes", "in-country"], default="votes",
                    help="'votes' (default, unchanged): raw TMDB vote_count. 'in-country': "
                         "vote_count's percentile within its own country's films, so each "
                         "cinema is ranked against itself rather than against Hollywood's "
                         "vote totals. See the module docstring for the full tradeoff.")
    ap.add_argument("--in", dest="src", default=str(SRC), help="input jsonl (testing)")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-wishlist", action="store_true",
                    help="skip re-appending resolved wishlist films from "
                         "data/wishlist_films.jsonl (they are outside the per-year cap)")
    ap.add_argument("--wishlist", default=str(WISHLIST), help="path (testing)")
    ap.add_argument("--wishlist-films", default=str(WISHLIST_FILMS), help="path (testing)")
    a = ap.parse_args()

    src_path = Path(a.src)
    if not src_path.exists():
        raise SystemExit(f"{src_path} not found — run src/tmdb_fetch.py first")

    rows, dropped = [], collections.Counter()
    for line in src_path.read_text().split("\n"):
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
        rows.append(sample_row(m))

    if a.rank == "in-country":
        pct = in_country_percentiles(rows)
        for r in rows:
            r["notability"] = pct[r["id"]]

    by_year = collections.defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r)

    sample, thin = [], []
    for y in sorted(by_year):
        pool = sorted(by_year[y], key=lambda r: -r["notability"])
        if len(pool) < a.min_films:
            thin.append((y, len(pool))); continue
        picked, taken = [], set()
        # regions first, then countries, then straight rank. Each pass only ever adds
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

    sample_tmdb_ids = {r["tmdb_id"] for r in sample}
    wl_rows, wl_dup, wl_uncached = load_wishlist_films(
        sample_tmdb_ids, a.no_wishlist, Path(a.wishlist), Path(a.wishlist_films))
    sample.extend(wl_rows)

    Path(a.out).write_text(json.dumps(sample, ensure_ascii=False))

    yrs = collections.Counter(r["year"] for r in sample if r.get("source") != "wishlist")
    ctry = collections.Counter(r["country"] for r in sample)
    print(f"corpus        {len(rows)} eligible films"
          f"   (dropped: {', '.join(f'{v} {k}' for k, v in dropped.most_common()) or 'none'})")
    print(f"ranking       {a.rank}")
    print(f"sampled       {len(sample) - len(wl_rows)} across {len(yrs)} years"
          f" ({min(yrs)}-{max(yrs)})")
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
    if not a.no_wishlist:
        print(f"\nwishlist      {len(wl_rows)} film(s) re-appended outside the per-year cap"
              + (f", {wl_dup} already in the sample (skipped)" if wl_dup else "")
              + (f", {wl_uncached} resolved but not yet cached (run wishlist.py resolve)"
                 if wl_uncached else ""))
    print(f"\nwrote {a.out}")
    print("next:  python3 src/map_labels.py   (carry the hand labels over)")


if __name__ == "__main__":
    main()
