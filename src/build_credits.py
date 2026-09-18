#!/usr/bin/env python3
"""Build data/credits.json from the per-film credits cache + the studio atlas.

Reads data/sample.json (which films exist), data/tmdb_credits_raw/<tmdb_id>.json (what
src/tmdb_credits.py cached for each) and data/studio_atlas.json (the alias map), and writes
the slim, film-index-aligned data/credits.json that src/build_viz.py inlines into the page
(contract C1 in the v4 plan). Deterministic: same inputs always produce the same bytes, so a
rebuild with nothing changed produces an empty diff.

    python3 src/build_credits.py                 # write data/credits.json
    python3 src/build_credits.py --report         # print coverage + threshold stats, no write
    python3 src/build_credits.py --out /tmp/x.json --cache-dir /tmp/cache --sample /tmp/s.json

No network. Safe to run repeatedly; a missing cache directory or a partially-fetched one just
means fewer films get credits — it never fails the build.
"""
import argparse, datetime, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample.json"
CACHE = ROOT / "data" / "tmdb_credits_raw"
ATLAS = ROOT / "data" / "studio_atlas.json"
OUT = ROOT / "data" / "credits.json"

THRESHOLD = 25  # same bar the country/genre aggregate view uses elsewhere in the app


# ---------------------------------------------------------------- atlas matching

def load_atlas(path=None):
    p = Path(path) if path else ATLAS
    if not p.exists():
        return []
    data = json.loads(p.read_text())
    studios = []
    for s in data.get("studios", []):
        patterns = [re.compile(pat, re.I) for pat in s.get("name_patterns", [])]
        studios.append({
            "key": s["key"], "label": s.get("label", s["key"]),
            "ids": set(s.get("ids", [])), "patterns": patterns,
        })
    return studios


def map_company(company_id, company_name, atlas):
    """-> (key, label). key is 's:<slug>' on a match, else 'c:<tmdb_company_id>' raw."""
    name = company_name or ""
    for s in atlas:
        if company_id in s["ids"]:
            return f"s:{s['key']}", s["label"]
    for s in atlas:
        if any(p.search(name) for p in s["patterns"]):
            return f"s:{s['key']}", s["label"]
    return f"c:{company_id}", name


# ---------------------------------------------------------------- cache loading

def load_sample(sample_path=None):
    return json.loads((Path(sample_path) if sample_path else SAMPLE).read_text())


def load_cached(tmdb_id, cache_dir):
    p = cache_dir / f"{tmdb_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------- build

def build(sample_path=None, cache_dir=None, atlas_path=None):
    """Returns (payload_dict, stats_dict). No I/O beyond reading the inputs."""
    cache_dir = Path(cache_dir) if cache_dir else CACHE
    atlas = load_atlas(atlas_path)
    sample = load_sample(sample_path)

    people = {}     # tmdb_person_id -> name (first name seen wins)
    companies = {}  # key -> label (first label seen wins)
    films = {}      # "tmdb-<id>" -> {"d":[ids],"a":[ids in order],"co":set(keys)}

    n_have = n_missing_cache = n_tombstoned = 0
    unmapped_counts = {}   # raw company name -> film count (only for 'c:' keys)

    for row in sample:
        tid = row.get("tmdb_id")
        fid = row["id"]
        if tid is None:
            n_missing_cache += 1
            continue
        rec = load_cached(tid, cache_dir)
        if rec is None:
            n_missing_cache += 1
            continue
        if rec.get("_missing"):
            n_tombstoned += 1
            continue

        d_ids, a_ids, co_keys = [], [], []
        seen_co = set()
        for p in rec.get("directors") or []:
            if p.get("id") is None:
                continue
            people.setdefault(p["id"], p.get("name") or str(p["id"]))
            d_ids.append(p["id"])
        for p in (rec.get("cast") or [])[:8]:
            if p.get("id") is None:
                continue
            people.setdefault(p["id"], p.get("name") or str(p["id"]))
            a_ids.append(p["id"])
        for c in rec.get("companies") or []:
            if c.get("id") is None:
                continue
            key, label = map_company(c["id"], c.get("name"), atlas)
            companies.setdefault(key, label)
            if key not in seen_co:      # a film belongs to a studio once, even if TMDB
                seen_co.add(key)        # listed two of its companies (e.g. Columbia + Sony
                co_keys.append(key)     # Pictures both map to "s:sony")

        films[fid] = {"d": d_ids, "a": a_ids, "co": co_keys}
        n_have += 1

    # Per-FILM counts of unmapped ("c:") companies, so a film with two raw companies of the
    # same name only counts once — matches how co_keys above was already deduped per film.
    unmapped_counts = {}
    for f in films.values():
        for key in f["co"]:
            if key.startswith("c:"):
                label = companies[key]
                unmapped_counts[(key, label)] = unmapped_counts.get((key, label), 0) + 1

    # ---- deterministic ordering ----
    people_ids = sorted(people)                       # ascending tmdb person id
    people_index = {pid: i for i, pid in enumerate(people_ids)}
    people_list = [[pid, people[pid]] for pid in people_ids]

    company_keys = sorted(companies)                   # ascending string key
    company_index = {k: i for i, k in enumerate(company_keys)}
    company_list = [[k, companies[k]] for k in company_keys]

    films_out = {}
    for fid in sorted(films):                           # deterministic key order
        f = films[fid]
        films_out[fid] = {
            "d": [people_index[i] for i in f["d"]],
            "a": [people_index[i] for i in f["a"]],
            "co": sorted(company_index[k] for k in f["co"]),
        }

    payload = {
        "v": 1,
        "built": datetime.date.today().isoformat(),
        "people": people_list,
        "companies": company_list,
        "films": films_out,
    }

    # ---- threshold stats (>=25 films in THIS sample, not career output) ----
    # `films` (not films_out) still holds raw tmdb person ids / company keys, not indices.
    dir_counts, act_counts, co_counts = {}, {}, {}
    for f in films.values():
        for pid in f["d"]:
            dir_counts[pid] = dir_counts.get(pid, 0) + 1
        for pid in f["a"]:
            act_counts[pid] = act_counts.get(pid, 0) + 1
        for k in f["co"]:
            co_counts[k] = co_counts.get(k, 0) + 1

    def over_threshold(counts, name_of):
        rows = [(name_of(k), n) for k, n in counts.items() if n >= THRESHOLD]
        return sorted(rows, key=lambda t: -t[1])

    stats = {
        "n_sample": len(sample),
        "n_with_credits": n_have,
        "n_missing_cache": n_missing_cache,
        "n_tombstoned": n_tombstoned,
        "n_people": len(people_list),
        "n_directors": len({pid for f in films.values() for pid in f["d"]}),
        "n_actors": len({pid for f in films.values() for pid in f["a"]}),
        "n_companies": len(company_list),
        "dir_over": over_threshold(dir_counts, lambda pid: people[pid]),
        "act_over": over_threshold(act_counts, lambda pid: people[pid]),
        "co_over": over_threshold(co_counts, lambda k: companies[k]),
        "unmapped_top": sorted(unmapped_counts.items(), key=lambda kv: -kv[1])[:40],
    }
    return payload, stats


# ---------------------------------------------------------------- report / write

def print_report(stats):
    print(f"sample films          {stats['n_sample']}")
    print(f"  with credits cached  {stats['n_with_credits']}")
    print(f"  missing from cache   {stats['n_missing_cache']}")
    print(f"  tombstoned (404)     {stats['n_tombstoned']}")
    print(f"people (directors+cast) {stats['n_people']}   "
          f"distinct directors {stats['n_directors']}   distinct actors {stats['n_actors']}")
    print(f"companies              {stats['n_companies']}")

    def show(label, rows):
        print(f"\n{label} with >= {THRESHOLD} films in this sample: {len(rows)}")
        for name, n in rows[:20]:
            print(f"  {n:>4}  {name}")

    show("directors", stats["dir_over"])
    show("actors", stats["act_over"])
    show("companies", stats["co_over"])

    print(f"\ntop {len(stats['unmapped_top'])} unmapped raw companies (extend data/studio_atlas.json):")
    for (key, name), n in stats["unmapped_top"]:
        print(f"  {n:>4}  {name}  ({key})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", default=None)
    ap.add_argument("--cache-dir", default=None)
    ap.add_argument("--atlas", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--report", action="store_true",
                    help="print coverage + threshold stats instead of writing the file")
    a = ap.parse_args()

    payload, stats = build(a.sample, a.cache_dir, a.atlas)

    if a.report:
        print_report(stats)
        return

    out_path = Path(a.out) if a.out else OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")))

    if stats["n_missing_cache"] or stats["n_tombstoned"]:
        print(f"note: {stats['n_missing_cache']} films have no cached credits yet "
              f"(run src/tmdb_credits.py) and {stats['n_tombstoned']} are tombstoned (404) — "
              f"wrote credits for the {stats['n_with_credits']} that do have them.")
    print(f"wrote {out_path}  ({stats['n_with_credits']} films, {stats['n_people']} people, "
          f"{stats['n_companies']} companies)")
    print("run  python3 src/build_credits.py --report  for coverage + threshold detail")


if __name__ == "__main__":
    main()
