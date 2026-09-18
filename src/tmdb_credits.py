#!/usr/bin/env python3
"""Enrich the sample with per-film credits from TMDB — stdlib only, no pip install.

/discover/movie (src/tmdb_fetch.py) returns none of this: /movie/{id}?append_to_response=credits
is a separate, per-film request that yields full crew, full cast and production_companies
together. ~6,000 calls, so it is its own pass with its own cache rather than bolted onto the
fetch. Every response is slimmed to what src/build_credits.py needs and cached to
data/tmdb_credits_raw/<tmdb_id>.json, so the run is fully restartable. A 404 (the film has been
pulled from TMDB, or the id is stale) is cached as a tombstone so it is not retried every run.

    python3 src/tmdb_credits.py --check              # one request: auth + shape
    python3 src/tmdb_credits.py --dry-run             # how many calls remain, ETA at --rps
    python3 src/tmdb_credits.py --limit 200           # fetch up to 200 films this run
    python3 src/tmdb_credits.py                       # the rest of data/sample.json

Credentials, pacing and retries are reused from tmdb_fetch.py (same src/.env, same Client) —
nothing here talks to TMDB directly. Run this from a normal terminal: TMDB is unreachable from
a sandboxed shell, and this file is written to be *run*, not run here.
"""
import argparse, json, os, sys, time, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmdb_fetch import Client, credentials  # reused, not copied

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample.json"
CACHE = Path(os.environ.get("TMDB_CREDITS_CACHE", str(ROOT / "data" / "tmdb_credits_raw")))

TOMBSTONE_KEY = "_missing"   # {"_missing": true, "code": 404} — a real slim record never has this key


def load_sample_ids(sample_path=None):
    """tmdb_id for every row in sample.json, de-duplicated, order preserved.

    No filtering by `source`: wishlist rows (contract C3) get enriched exactly like sampled
    ones — the whole point of the wishlist is that those films behave like any other film
    once they are in the corpus, just excluded from aggregate figures downstream.
    """
    path = Path(sample_path) if sample_path else SAMPLE
    rows = json.loads(path.read_text())
    ids, seen = [], set()
    for r in rows:
        tid = r.get("tmdb_id")
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        ids.append(tid)
    return ids


def cache_path(tmdb_id, cache_dir=None):
    return (cache_dir or CACHE) / f"{tmdb_id}.json"


def is_cached(tmdb_id, cache_dir=None):
    return cache_path(tmdb_id, cache_dir).exists()


def slim(detail):
    """Keep only what build_credits.py reads: directors, top-billed cast, companies."""
    credits = detail.get("credits") or {}
    crew = credits.get("crew") or []
    cast = credits.get("cast") or []
    companies = detail.get("production_companies") or []

    seen_d, directors = set(), []
    for c in crew:
        if c.get("job") == "Director" and c.get("id") is not None and c["id"] not in seen_d:
            seen_d.add(c["id"])
            directors.append({"id": c["id"], "name": c.get("name")})

    cast_ranked = sorted(
        (c for c in cast if c.get("id") is not None),
        key=lambda c: c["order"] if c.get("order") is not None else 10**9)
    top_cast = [{"id": c["id"], "name": c.get("name"), "order": c.get("order")}
                for c in cast_ranked[:8]]

    comps = [{"id": c["id"], "name": c.get("name")} for c in companies if c.get("id") is not None]

    return {"directors": directors, "cast": top_cast, "companies": comps}


def write_cache(tmdb_id, payload, cache_dir=None):
    d = cache_dir or CACHE
    d.mkdir(parents=True, exist_ok=True)
    p = cache_path(tmdb_id, d)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(p)


def fetch_one(client, tmdb_id):
    """One /movie/{id} call, slimmed. Returns a slim dict, or a tombstone on 404."""
    try:
        detail = client.get(f"/movie/{tmdb_id}", {"append_to_response": "credits"})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {TOMBSTONE_KEY: True, "code": 404}
        raise
    return slim(detail)


def eta_seconds(n_calls, rps, pause_every, pause):
    if rps <= 0:
        return 0.0
    secs = n_calls / rps
    if pause_every:
        secs += (n_calls // pause_every) * pause
    return secs


def fmt_eta(secs):
    if secs < 90:
        return f"{secs:.0f}s"
    if secs < 5400:
        return f"{secs/60:.1f} min"
    return f"{secs/3600:.1f} h"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", default=None, help="path to sample.json (default data/sample.json)")
    ap.add_argument("--cache-dir", default=None, help="path to the credits cache dir")
    ap.add_argument("--limit", type=int, default=None, help="fetch at most this many films this run")
    ap.add_argument("--rps", type=float, default=3.0, help="requests per second")
    ap.add_argument("--pause-every", type=int, default=120)
    ap.add_argument("--pause", type=int, default=8, help="seconds to rest at each pause")
    ap.add_argument("--check", action="store_true", help="one request, then stop")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, make no calls")
    a = ap.parse_args()

    cache_dir = Path(a.cache_dir) if a.cache_dir else CACHE
    ids = load_sample_ids(a.sample)
    if not ids:
        sys.exit(f"no films with tmdb_id found in {a.sample or SAMPLE}")

    remaining = [i for i in ids if not is_cached(i, cache_dir)]

    if a.dry_run:
        todo = remaining if a.limit is None else remaining[:a.limit]
        secs = eta_seconds(len(todo), a.rps, a.pause_every, a.pause)
        print(f"sample films   {len(ids)}")
        print(f"already cached {len(ids) - len(remaining)}  -> {cache_dir}")
        print(f"remaining      {len(remaining)}")
        print(f"this run       {len(todo)} calls at {a.rps}/s "
              f"(resting {a.pause}s every {a.pause_every})")
        print(f"est. time      ~{fmt_eta(secs)}")
        print("\nNo calls made. Drop --dry-run to run it.")
        return

    token, key = credentials()
    client = Client(token, key, a.rps, a.pause_every, a.pause)
    print(f"auth: {'v4 bearer token' if token else 'v3 api key'}")

    if a.check:
        probe = ids[0]
        detail = client.get(f"/movie/{probe}", {"append_to_response": "credits"})
        s = slim(detail)
        print(f"OK — {detail.get('title')} ({(detail.get('release_date') or '')[:4]}): "
              f"{len(s['directors'])} director(s), {len(s['cast'])} cast (of "
              f"{len((detail.get('credits') or {}).get('cast') or [])}), "
              f"{len(s['companies'])} companies")
        print("\nAuth and shape are good. Next: --dry-run, then just run it.")
        return

    todo = remaining if a.limit is None else remaining[:a.limit]
    if not todo:
        print(f"nothing to do — all {len(ids)} films already cached in {cache_dir}")
        return

    print(f"{len(remaining)} of {len(ids)} films uncached; fetching {len(todo)} this run "
          f"-> {cache_dir}")
    ok = missing = errors = 0
    t0 = time.monotonic()
    for i, tmdb_id in enumerate(todo, 1):
        try:
            payload = fetch_one(client, tmdb_id)
        except Exception as e:
            errors += 1
            print(f"  [{i}/{len(todo)}] tmdb-{tmdb_id}: ERROR {e}", flush=True)
            continue
        write_cache(tmdb_id, payload, cache_dir)
        if payload.get(TOMBSTONE_KEY):
            missing += 1
        else:
            ok += 1
        if i % 25 == 0 or i == len(todo):
            elapsed = time.monotonic() - t0
            print(f"  [{i}/{len(todo)}] {ok} ok, {missing} missing, {errors} errors "
                  f"({elapsed:.0f}s, {client.n} requests)", flush=True)

    print(f"\ndone: {ok} cached, {missing} tombstoned (404), {errors} errors, "
          f"{len(ids) - len(remaining) + ok} of {len(ids)} films now in cache")
    if errors:
        print("re-run to retry the ones that errored (cached films are skipped)")
    print("\nnext:  python3 src/build_credits.py")


if __name__ == "__main__":
    main()
