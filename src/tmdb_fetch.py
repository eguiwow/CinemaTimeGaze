#!/usr/bin/env python3
"""Fetch a film corpus from TMDB — stdlib only, no pip install.

Walks (country x release-year) cells through /discover/movie, caching every raw page
to disk so the run is fully restartable. Country comes from the query itself
(`with_origin_country`), so no per-film detail call is needed.

    python3 src/tmdb_fetch.py --check                 # one request: auth + shape
    python3 src/tmdb_fetch.py --list-regions          # the country presets
    python3 src/tmdb_fetch.py --dry-run               # the plan and the request count
    python3 src/tmdb_fetch.py --from 1980 --to 1989   # a slice, to feel out the pace
    python3 src/tmdb_fetch.py                         # the lot (west, the legacy default)
    python3 src/tmdb_fetch.py --countries global      # every curated national cinema
    python3 src/tmdb_fetch.py --countries east_asia,latin_america
    python3 src/tmdb_fetch.py --flatten-only          # rebuild the jsonl from cache

Countries can be region presets (see --list-regions) or bare ISO codes, mixed freely.
Going global multiplies the cell count, so the walk runs newest-year-first per country and
gives up on a country once it hits a long run of empty years — see --give-up-after.

Credentials come from src/.env:  API_READ_TOKEN (v4 bearer, preferred) or API_KEY (v3).
Nothing is ever printed that could leak them.
"""
import argparse, collections, json, os, random, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "tmdb_raw"
OUT = ROOT / "data" / "tmdb_films.jsonl"
GENRE_CACHE = ROOT / "data" / "tmdb_genres.json"
COVERAGE = ROOT / "data" / "tmdb_coverage.json"
BASE = "https://api.themoviedb.org/3"

# Curated national cinemas, grouped by region. The point of the grouping is that a
# popularity-ranked global query returns an overwhelmingly American corpus — the only way
# international films reach the raw data is to ask for them by country, one query each.
# The list is deliberately curated rather than "every country": a country with no TMDB
# coverage costs requests and contributes nothing.
REGIONS = {
    "north_america":  ["US", "CA", "MX"],
    "west_europe":    ["GB", "FR", "DE", "IT", "ES", "NL", "SE", "DK", "BE", "AT",
                       "CH", "IE", "PT", "NO", "FI", "IS", "GR"],
    "east_europe":    ["RU", "PL", "CZ", "HU", "RO", "UA", "RS", "BG", "HR", "SK",
                       "EE", "LT", "LV", "GE"],
    "east_asia":      ["JP", "KR", "CN", "HK", "TW"],
    "south_asia":     ["IN", "PK", "BD", "LK", "NP"],
    "southeast_asia": ["TH", "PH", "ID", "VN", "MY", "SG"],
    "latin_america":  ["BR", "AR", "CL", "CO", "PE", "VE", "UY", "CU"],
    "middle_east":    ["TR", "IR", "IL", "EG", "LB", "AE", "SA"],
    "africa":         ["ZA", "NG", "MA", "DZ", "TN", "SN", "KE", "GH", "ET"],
    "oceania":        ["AU", "NZ"],
}

# The territory the original brief locked in, kept verbatim so the existing cache and the
# published findings stay reproducible. "global" is every region above.
LEGACY_WEST = ["US", "GB", "FR", "DE", "IT", "ES", "NL", "SE", "DK", "BE",
               "AT", "CH", "IE", "PT", "NO", "FI"]

REGION_OF = {c: r for r, cs in REGIONS.items() for c in cs}
ALL_COUNTRIES = [c for cs in REGIONS.values() for c in cs]
DEFAULT_COUNTRIES = "west"


def resolve_countries(spec):
    """'west' | 'global' | region names | bare ISO codes — mixed, in any order."""
    out, seen = [], set()
    for tok in spec.split(","):
        tok = tok.strip()
        if not tok:
            continue
        key = tok.lower().replace("-", "_")
        if key == "west":
            group = LEGACY_WEST
        elif key in ("global", "all"):
            group = ALL_COUNTRIES
        elif key in REGIONS:
            group = REGIONS[key]
        else:
            code = tok.upper()
            if not re.fullmatch(r"[A-Z]{2}", code):
                sys.exit(f"'{tok}' is neither a region (see --list-regions) nor an ISO country code")
            group = [code]
        for c in group:
            if c not in seen:
                seen.add(c); out.append(c)
    if not out:
        sys.exit("no countries selected")
    return out


# ---------------------------------------------------------------- credentials

def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def credentials():
    env = load_env(Path(__file__).resolve().parent / ".env")
    token = env.get("API_READ_TOKEN") or os.environ.get("TMDB_READ_TOKEN")
    key = env.get("API_KEY") or os.environ.get("TMDB_API_KEY")
    if not token and not key:
        sys.exit("no credentials — put API_READ_TOKEN or API_KEY in src/.env")
    return token, key


# ---------------------------------------------------------------- http

class Client:
    """Polite TMDB client: paced, retrying, and it never logs the secret."""

    def __init__(self, token, key, rps, pause_every, pause, timeout=25):
        self.token, self.key = token, key
        self.min_gap = 1.0 / rps if rps > 0 else 0
        self.pause_every, self.pause = pause_every, pause
        self.timeout = timeout
        self.n = 0
        self._last = 0.0

    def _wait(self):
        gap = time.monotonic() - self._last
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        if self.pause_every and self.n and self.n % self.pause_every == 0:
            print(f"    … pausing {self.pause}s after {self.n} requests", flush=True)
            time.sleep(self.pause)

    def get(self, path, params=None, tries=5):
        params = dict(params or {})
        headers = {"Accept": "application/json", "User-Agent": "CinemaTimeGaze/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            params["api_key"] = self.key
        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"

        for attempt in range(tries):
            self._wait()
            self._last = time.monotonic()
            self.n += 1
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After", "5")) + 1
                    print(f"    429 rate limited — sleeping {wait}s", flush=True)
                    time.sleep(wait); continue
                if e.code in (401, 403):
                    sys.exit(f"TMDB rejected the credentials (HTTP {e.code}). "
                             "Check API_READ_TOKEN / API_KEY in src/.env.")
                if 500 <= e.code < 600 and attempt < tries - 1:
                    time.sleep(2 ** attempt + random.random()); continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < tries - 1:
                    time.sleep(2 ** attempt + random.random()); continue
                raise SystemExit(
                    f"cannot reach api.themoviedb.org ({e}).\n"
                    "If you are running this inside a sandboxed shell, its egress policy is "
                    "probably blocking TMDB — run it from a normal terminal instead.")
        raise SystemExit("gave up after repeated rate limiting")


# ---------------------------------------------------------------- fetch

def genres(client):
    if GENRE_CACHE.exists():
        return json.loads(GENRE_CACHE.read_text())
    data = client.get("/genre/movie/list", {"language": "en-US"})
    m = {str(g["id"]): g["name"] for g in data["genres"]}
    GENRE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    GENRE_CACHE.write_text(json.dumps(m, indent=1))
    return m


# JSON escapes \n but not U+2028/U+2029 and friends, while Python's splitlines() breaks on
# all of them — so one film summary carrying a Unicode line separator splits a JSONL row in
# half and takes the whole corpus read down with it. Found exactly one in 22,922 films.
_ODD_BREAKS = str.maketrans({c: " " for c in "\u2028\u2029\x0b\x0c\x85"})


def oneline(s):
    return (s or "").translate(_ODD_BREAKS).strip()


def cell_path(country, year, page):
    return RAW / country / f"{year}-p{page}.json"


def load_coverage():
    return json.loads(COVERAGE.read_text()) if COVERAGE.exists() else {}


def save_coverage(cov):
    COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE.write_text(json.dumps(cov, indent=1, sort_keys=True))


def fetch(client, countries, years, pages, min_votes, give_up_after, floor_year, reprobe):
    """Walk each country newest year first, and stop once its archive runs dry.

    Going global turns a 16-country list into ~70, and most of those cinemas have no TMDB
    coverage before roughly 1970 — walking every one of them back to 1900 would spend
    thousands of requests to learn nothing. So: descend the years, count consecutive empty
    ones, and give up on the country after `give_up_after` of them. The year we gave up at
    is written to data/tmdb_raw/_coverage.json, so a later run resumes instead of re-probing.
    `floor_year` is the year above which we never give up, so a country with a genuine gap
    (a war, a studio collapse) is not cut off by it.
    """
    years_desc = sorted(years, reverse=True)
    cov = load_coverage()
    if reprobe:
        for c in countries:
            cov.pop(c, None)

    print(f"{len(countries)} countries x up to {len(years)} years x {pages} page"
          f"{'s' if pages > 1 else ''} — walking newest first, giving up after "
          f"{give_up_after} consecutive empty years below {floor_year}")

    fetched = skipped = empty = 0
    for ci, c in enumerate(countries, 1):
        rec = cov.get(c) or {}
        floor_seen = rec.get("stopped_before")
        run, took, oldest = 0, 0, None
        stopped = None
        for y in years_desc:
            if floor_seen is not None and y < floor_seen:
                stopped = floor_seen
                break
            n_year = 0
            for pg in range(1, pages + 1):
                path = cell_path(c, y, pg)
                if path.exists():
                    blob = json.loads(path.read_text())
                    n = len(blob.get("results", []))
                    skipped += 1
                else:
                    data = client.get("/discover/movie", {
                        "with_origin_country": c,
                        "primary_release_year": y,
                        "sort_by": "vote_count.desc",
                        "vote_count.gte": min_votes,
                        "include_adult": "false",
                        "language": "en-US",
                        "page": pg,
                    })
                    results = data.get("results", [])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(
                        {"country": c, "year": y, "page": pg,
                         "total_results": data.get("total_results", 0),
                         "results": results}, ensure_ascii=False))
                    n = len(results)
                    fetched += 1
                    if not n:
                        empty += 1
                n_year += n
                if not n:                       # no page 2 if page 1 came back empty
                    break
            took += n_year
            if n_year:
                run = 0
                oldest = y
            else:
                run += 1
                if run >= give_up_after and y < floor_year:
                    stopped = y + run           # first year of the empty run
                    break
        cov[c] = {"films_seen": took, "oldest_year": oldest,
                  "stopped_before": stopped, "checked_to": years_desc[-1] if stopped is None else stopped}
        save_coverage(cov)
        tail = (f"back to {oldest}" if oldest else "no films at all")
        note = f" (gave up below {stopped})" if stopped else ""
        print(f"  [{ci}/{len(countries)}] {c}: {took} films, {tail}{note}", flush=True)

    print(f"\nfetched {fetched}, reused {skipped}, {empty} empty cells "
          f"({client.n} requests this run)")
    print(f"coverage map -> {COVERAGE}")


def flatten(genre_map):
    """Cache -> one JSONL row per film, with country and genre names attached."""
    seen, rows = {}, 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as fh:
        for path in sorted(RAW.rglob("*.json")):
            if path.name.startswith("_"):      # coverage/bookkeeping, not a cell
                continue
            blob = json.loads(path.read_text())
            for m in blob["results"]:
                mid = m.get("id")
                date = (m.get("release_date") or "")[:4]
                if not mid or not date.isdigit():
                    continue
                year = int(date)
                if year != blob["year"]:            # TMDB occasionally disagrees with the filter
                    continue
                if mid in seen:                     # co-productions appear under several countries
                    seen[mid]["countries"].append(blob["country"])
                    continue
                seen[mid] = {
                    "id": f"tmdb-{mid}",
                    "tmdb_id": mid,
                    "title": oneline(m.get("title") or m.get("original_title")),
                    "original_title": oneline(m.get("original_title")),
                    "year": year,
                    "countries": [blob["country"]],
                    "language": m.get("original_language"),
                    "genres": [genre_map.get(str(g), str(g)) for g in (m.get("genre_ids") or [])],
                    "overview": oneline(m.get("overview")),
                    "vote_count": m.get("vote_count") or 0,
                    "vote_average": m.get("vote_average") or 0,
                    "popularity": m.get("popularity") or 0,
                }
        for rec in seen.values():
            rec["countries"] = sorted(set(rec["countries"]))
            rec["country"] = rec["countries"][0]     # primary = first alphabetically, stable
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rows += 1
    print(f"wrote {rows} unique films to {OUT}")
    return rows


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--countries", default=DEFAULT_COUNTRIES,
                    help="region presets and/or ISO codes, comma separated "
                         "(west | global | east_asia,BR,...) — see --list-regions")
    ap.add_argument("--from", dest="y0", type=int, default=1900)
    ap.add_argument("--to", dest="y1", type=int, default=2025)
    ap.add_argument("--pages", type=int, default=1, help="20 films per page")
    ap.add_argument("--min-votes", type=int, default=5,
                    help="drop films almost nobody has rated")
    ap.add_argument("--give-up-after", type=int, default=12,
                    help="stop walking a country back after this many empty years running")
    ap.add_argument("--floor-year", type=int, default=1970,
                    help="never give up on a country above this year, gaps happen")
    ap.add_argument("--reprobe", action="store_true",
                    help="ignore the cached coverage map and walk each country back again")
    ap.add_argument("--rps", type=float, default=3.0, help="requests per second")
    ap.add_argument("--pause-every", type=int, default=120)
    ap.add_argument("--pause", type=int, default=8, help="seconds to rest at each pause")
    ap.add_argument("--check", action="store_true", help="one request, then stop")
    ap.add_argument("--list-regions", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--flatten-only", action="store_true")
    a = ap.parse_args()

    if a.list_regions:
        print(f"{'west':<16} {len(LEGACY_WEST):>3}  {' '.join(LEGACY_WEST)}")
        print(f"{'global':<16} {len(ALL_COUNTRIES):>3}  every region below\n")
        for r, cs in REGIONS.items():
            print(f"{r:<16} {len(cs):>3}  {' '.join(cs)}")
        print("\nMix freely:  --countries west,east_asia,BR,NG")
        return

    countries = resolve_countries(a.countries)
    years = list(range(a.y0, a.y1 + 1))
    n_cells = len(countries) * len(years) * a.pages
    have = sum(1 for y in years for c in countries for p in range(1, a.pages + 1)
               if cell_path(c, y, p).exists())

    if a.dry_run:
        cov = load_coverage()
        # the backstop means the real count is unknowable up front; bound it instead
        worst = n_cells - have
        likely = 0
        for c in countries:
            stop = (cov.get(c) or {}).get("stopped_before")
            lo = max(a.y0, stop) if stop else a.y0
            likely += sum(1 for y in years if y >= lo
                          for p in range(1, a.pages + 1) if not cell_path(c, y, p).exists())
        secs = likely / max(a.rps, .01) + likely // max(a.pause_every, 1) * a.pause
        by_region = collections.Counter(REGION_OF.get(c, "other") for c in countries)
        print(f"countries   {len(countries)}  ({', '.join(f'{n} {r}' for r, n in by_region.most_common())})")
        print(f"            {' '.join(countries)}")
        print(f"years       {a.y0}-{a.y1}  ({len(years)})")
        print(f"pages/cell  {a.pages}  (up to {a.pages * 20} films per country-year)")
        print(f"cells       {n_cells} if every country goes back to {a.y0}  ({have} cached)")
        print(f"to fetch    ~{likely} expected, {worst} worst case")
        print(f"            (the walk stops a country after {a.give_up_after} empty years "
              f"below {a.floor_year}, so most of the early cells are never asked for)")
        print(f"known       {len(cov)} countries already have a coverage floor recorded")
        print(f"pace        {a.rps}/s, resting {a.pause}s every {a.pause_every}")
        print(f"est. time   ~{secs / 60:.0f} min")
        print("\nNo calls made. Drop --dry-run to run it.")
        return

    if a.flatten_only:                       # pure local work, no credentials needed
        gmap = json.loads(GENRE_CACHE.read_text()) if GENRE_CACHE.exists() else {}
        if not gmap:
            print("note: no data/tmdb_genres.json yet — genre names will show as ids")
        flatten(gmap)
        print("\nnext:  python3 src/sample_tmdb.py 50")
        return

    token, key = credentials()
    client = Client(token, key, a.rps, a.pause_every, a.pause)
    print(f"auth: {'v4 bearer token' if token else 'v3 api key'}")

    if a.check:
        g = genres(client)
        data = client.get("/discover/movie", {
            "with_origin_country": "FR", "primary_release_year": 1965,
            "sort_by": "vote_count.desc", "language": "en-US", "page": 1})
        res = data.get("results", [])
        print(f"OK — {len(g)} genres, {data.get('total_results', 0)} French films from 1965")
        for m in res[:3]:
            gn = ", ".join(g.get(str(i), "?") for i in (m.get("genre_ids") or [])[:3])
            print(f"  {m.get('title')} ({(m.get('release_date') or '')[:4]}) [{gn}] "
                  f"votes={m.get('vote_count')}")
            print(f"    {(m.get('overview') or '')[:150]}")
        print("\nAuth and shape are good. Next: --dry-run, then a --from/--to slice.")
        return

    gmap = genres(client)
    fetch(client, countries, years, a.pages, a.min_votes,
          a.give_up_after, a.floor_year, a.reprobe)
    flatten(gmap)
    print("\nnext:  python3 src/sample_tmdb.py 50")


if __name__ == "__main__":
    main()
