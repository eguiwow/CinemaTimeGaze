#!/usr/bin/env python3
"""Close the wishlist loop: GitHub issue -> TMDB lookup -> data/sample.json (outside the
per-year cap) -> a printed comment/close once it has shipped. See contract C6 in the v4 plan.

    python3 src/wishlist.py sync                       # pull open "wishlist" issues via `gh`
    python3 src/wishlist.py sync --from-file issues.json   # same, without gh (tests, offline)
    python3 src/wishlist.py resolve                    # TMDB lookup for every "requested" entry
    python3 src/wishlist.py apply                       # append resolved films to data/sample.json
    python3 src/wishlist.py status                       # a table of every wishlist entry
    python3 src/wishlist.py comment                     # print (don't run) the gh follow-up

Every step is idempotent: re-running `sync` on the same issues, or `apply` on an
already-applied entry, changes nothing. Nothing here ever overwrites an already-resolved
or already-applied entry's identity — a fresh `resolve --force` is the only way back.

data/wishlist.json   [{"tmdb_id","title","year","issue","added","status","note","tmdb_hint"}]
                      status: requested -> resolved -> in_sample  (or -> rejected, by hand)
data/wishlist_films.jsonl   flattened TMDB rows for every resolved film, gitignored (TMDB
                      content) — the cache `apply` reads from, and sample_tmdb.py re-appends
                      from on every resample so a resolved wishlist film is never silently
                      dropped just because sampling only ever writes the per-year top N.
"""
import argparse, collections, json, re, shlex, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmdb_fetch import credentials, Client, oneline  # same client/creds sample_tmdb's corpus uses
from sample_tmdb import sample_row                    # the one place a sample row is shaped

ROOT = Path(__file__).resolve().parent.parent
WISHLIST = ROOT / "data" / "wishlist.json"
FILMS = ROOT / "data" / "wishlist_films.jsonl"
SAMPLE = ROOT / "data" / "sample.json"
REPO = "eguiwow/CinemaTimeGaze"

STATUSES = {"requested", "resolved", "in_sample", "rejected"}


# ---------------------------------------------------------------- storage

def load_wishlist(path):
    return json.loads(path.read_text()) if path.exists() else []


def save_wishlist(path, entries):
    # stable order so re-running a no-op command never dirties the file
    entries = sorted(entries, key=lambda e: (e.get("issue") or 0, (e.get("title") or "").lower()))
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")


def load_films_cache(path):
    cache = {}
    if path.exists():
        for line in path.read_text().split("\n"):
            if line.strip():
                m = json.loads(line)
                cache[m["tmdb_id"]] = m          # later line wins on a re-resolved film
    return cache


def save_films_cache(path, cache):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for tmdb_id in sorted(cache):
            fh.write(json.dumps(cache[tmdb_id], ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- parsing an issue-form body

_HEADER = re.compile(r"^#{2,3}\s+(.+?)\s*$", re.M)


def parse_issue_body(body):
    """GitHub renders an issue form's body as `### <field label>\\n\\n<answer>` per field,
    in template order. Match loosely on the label so a template wording tweak doesn't break
    every issue filed before it."""
    body = body or ""
    headers = list(_HEADER.finditer(body))
    fields = {}
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        label = m.group(1).strip().lower()
        value = body[start:end].strip()
        if not value or value.lower() == "_no response_":
            value = None
        if "title" in label:
            fields["title"] = value
        elif "year" in label:
            fields["year"] = value
        elif "tmdb" in label:
            fields["tmdb"] = value
        elif "why" in label:
            fields["why"] = value
    return fields


def extract_year(text):
    if not text:
        return None
    m = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", text)
    return int(m.group(1)) if m else None


def parse_tmdb_hint(hint):
    """A raw 'TMDB link or id' answer -> an int TMDB id, or None if it names none."""
    if not hint:
        return None
    m = re.search(r"themoviedb\.org/(?:movie|tv)/(\d+)", hint)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"\s*(\d+)\s*", hint)
    if m:
        return int(m.group(1))
    return None


# ---------------------------------------------------------------- sync (gh -> wishlist.json)

def fetch_issues_via_gh(repo):
    import subprocess
    cmd = ["gh", "issue", "list", "--repo", repo, "--label", "wishlist",
           "--state", "open", "--json", "number,title,body,createdAt"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise SystemExit("`gh` is not on PATH. Install the GitHub CLI, or pass --from-file "
                         "with the same JSON shape (see `gh issue list --json ... --help`).")
    if r.returncode != 0:
        raise SystemExit(f"`gh issue list` failed: {(r.stderr or r.stdout).strip()[:400]}")
    return json.loads(r.stdout)


def cmd_sync(a):
    issues = json.loads(Path(a.from_file).read_text()) if a.from_file else fetch_issues_via_gh(a.repo)
    entries = load_wishlist(Path(a.wishlist))
    by_issue = {e["issue"]: e for e in entries if e.get("issue") is not None}

    new = updated = skipped = 0
    for iss in issues:
        num = iss.get("number")
        fields = parse_issue_body(iss.get("body") or "")
        title = fields.get("title") or re.sub(r"^wishlist:\s*", "", iss.get("title") or "",
                                               flags=re.I).strip()
        if not title:
            skipped += 1
            continue
        year = extract_year(fields.get("year"))
        hint = fields.get("tmdb")

        e = by_issue.get(num)
        if e is None:
            e = {"tmdb_id": None, "title": title, "year": year, "issue": num,
                 "added": (iss.get("createdAt") or "")[:10] or date.today().isoformat(),
                 "status": "requested", "note": "", "tmdb_hint": hint}
            entries.append(e)
            by_issue[num] = e
            new += 1
        else:
            changed = False
            # once resolved/in_sample the issue's answers stop driving identity — only a
            # fresh `resolve --force` re-reads them, so a typo edit on GitHub after the
            # fact can't silently swap out a film that already shipped.
            if e.get("status") == "requested":
                if e.get("title") != title:
                    e["title"] = title; changed = True
                if e.get("year") != year:
                    e["year"] = year; changed = True
                if e.get("tmdb_hint") != hint:
                    e["tmdb_hint"] = hint; changed = True
            if changed:
                updated += 1

    save_wishlist(Path(a.wishlist), entries)
    print(f"synced {len(issues)} open wishlist issue(s): {new} new, {updated} updated"
          + (f", {skipped} skipped (no title)" if skipped else "")
          + f", {len(entries)} total in {a.wishlist}")


# ---------------------------------------------------------------- resolve (wishlist.json -> TMDB)

def flatten_movie(detail):
    """A /movie/{id} response -> the same row shape tmdb_fetch.flatten() writes to
    data/tmdb_films.jsonl, so sample_tmdb.sample_row() can turn it into a sample row
    without caring whether it came from the bulk fetch or a single wishlist lookup."""
    mid = detail["id"]
    year = None
    date_str = (detail.get("release_date") or "")[:4]
    if date_str.isdigit():
        year = int(date_str)
    countries = sorted({c["iso_3166_1"] for c in (detail.get("production_countries") or [])
                        if c.get("iso_3166_1")})
    if not countries:
        countries = ["XX"]           # unknown; sample_tmdb's REGION_OF maps it to "other"
    return {
        "id": f"tmdb-{mid}",
        "tmdb_id": mid,
        "title": oneline(detail.get("title") or detail.get("original_title")),
        "original_title": oneline(detail.get("original_title")),
        "year": year,
        "countries": countries,
        "country": countries[0],
        "language": detail.get("original_language"),
        "genres": [g.get("name") for g in (detail.get("genres") or []) if g.get("name")],
        "overview": oneline(detail.get("overview")),
        "vote_count": detail.get("vote_count") or 0,
        "vote_average": detail.get("vote_average") or 0,
        "popularity": detail.get("popularity") or 0,
    }


def resolve_one(client, entry):
    """TMDB /movie/{id} when an id is known (explicit hint or a re-resolve), else
    /search/movie?query=&year= taking the best match. Returns (flattened_row, note);
    row is None when nothing usable was found — the caller keeps status=requested."""
    tmdb_id = entry.get("tmdb_id") or parse_tmdb_hint(entry.get("tmdb_hint"))
    detail = None
    if tmdb_id:
        try:
            detail = client.get(f"/movie/{tmdb_id}", {"language": "en-US"})
        except Exception:
            detail = None                # bad/stale id — fall through to search by title

    if detail is None or detail.get("id") is None:
        title = entry.get("title") or ""
        try:
            data = client.get("/search/movie", {
                "query": title, "year": entry.get("year") or "",
                "include_adult": "false", "language": "en-US"})
        except Exception as e:
            return None, f"TMDB search failed: {e}"
        results = [r for r in (data.get("results") or []) if r.get("id")]
        if entry.get("year"):
            exact = [r for r in results if (r.get("release_date") or "")[:4] == str(entry["year"])]
            results = exact or results
        if not results:
            return None, (f"no TMDB match for {title!r}"
                          + (f" ({entry['year']})" if entry.get("year") else ""))
        try:
            detail = client.get(f"/movie/{results[0]['id']}", {"language": "en-US"})
        except Exception as e:
            return None, f"TMDB detail lookup failed: {e}"

    if not detail or detail.get("id") is None:
        return None, f"no TMDB match for {entry.get('title')!r}"
    return flatten_movie(detail), ""


def cmd_resolve(a):
    entries = load_wishlist(Path(a.wishlist))
    cache = load_films_cache(Path(a.films))
    client = getattr(a, "client", None)
    if client is None:
        token, key = credentials()
        client = Client(token, key, rps=3.0, pause_every=120, pause=8)

    targets = [e for e in entries
              if a.force or e.get("status") in ("requested", None)]
    if not targets:
        print("nothing to resolve — every entry is already resolved (use --force to redo)")
        return

    n_ok = n_fail = 0
    for e in targets:
        row, note = resolve_one(client, e)
        if row is None:
            e["note"] = note
            n_fail += 1
            print(f"  ! {e.get('title')!r}: {note}")
            continue
        e["tmdb_id"] = row["tmdb_id"]
        e["title"] = row["title"]
        e["year"] = row["year"]
        e["status"] = "resolved"
        e["note"] = ""
        cache[row["tmdb_id"]] = row
        n_ok += 1
        print(f"  resolved {row['title']!r} ({row['year']}) -> tmdb-{row['tmdb_id']}")

    save_wishlist(Path(a.wishlist), entries)
    save_films_cache(Path(a.films), cache)
    print(f"\nresolved {n_ok}, unresolved {n_fail} (of {len(targets)} attempted)")


# ---------------------------------------------------------------- apply (-> data/sample.json)

def cmd_apply(a):
    entries = load_wishlist(Path(a.wishlist))
    cache = load_films_cache(Path(a.films))
    sample_path = Path(a.sample)
    sample = json.loads(sample_path.read_text()) if sample_path.exists() else []
    have_tmdb = {r.get("tmdb_id") for r in sample}

    added = already = missing = 0
    for e in entries:
        if e.get("status") not in ("resolved", "in_sample"):
            continue
        tmdb_id = e.get("tmdb_id")
        if not tmdb_id:
            continue
        if tmdb_id in have_tmdb:
            if e["status"] != "in_sample":
                e["status"] = "in_sample"; e["note"] = "already sampled"
            already += 1
            continue
        m = cache.get(tmdb_id)
        if m is None:
            missing += 1
            print(f"  ! {e.get('title')!r}: resolved but not cached — run `resolve` first")
            continue
        row = sample_row(m)
        row["source"] = "wishlist"
        sample.append(row)
        have_tmdb.add(tmdb_id)
        e["status"] = "in_sample"
        e["note"] = ""
        added += 1
        print(f"  added {row['title']!r} ({row['year']}) to {sample_path.name}")

    if added or already:
        sample_path.write_text(json.dumps(sample, ensure_ascii=False))
    save_wishlist(Path(a.wishlist), entries)
    print(f"\nappended {added}"
          + (f", {already} already sampled" if already else "")
          + (f", {missing} missing from cache" if missing else "")
          + f" -> {sample_path}")


# ---------------------------------------------------------------- status / comment

def cmd_status(a):
    entries = load_wishlist(Path(a.wishlist))
    if not entries:
        print(f"{a.wishlist} is empty"); return
    w = max(12, max(len(e.get("title") or "") for e in entries))
    print(f"{'title':<{w}}  {'year':>5}  {'tmdb_id':>9}  {'issue':>6}  {'status':<10}  note")
    for e in sorted(entries, key=lambda e: (e.get("status") or "", (e.get("title") or "").lower())):
        print(f"{(e.get('title') or ''):<{w}}  {str(e.get('year') or ''):>5}  "
              f"{str(e.get('tmdb_id') or ''):>9}  {str(e.get('issue') or ''):>6}  "
              f"{(e.get('status') or ''):<10}  {e.get('note') or ''}")
    counts = collections.Counter(e.get("status") for e in entries)
    print("\n" + ", ".join(f"{n} {s}" for s, n in counts.most_common()))


def cmd_comment(a):
    """Print, never run, the gh follow-up for every wishlist film that made it into a build
    (status in_sample) and still has an open issue to close the loop on."""
    entries = load_wishlist(Path(a.wishlist))
    live = [e for e in entries if e.get("status") == "in_sample" and e.get("issue")]
    if not live:
        print("nothing to comment on — no in_sample entries with an open issue"); return
    print(f"# not run automatically — review, then paste into a shell (or pipe: | sh)")
    for e in sorted(live, key=lambda e: e["issue"]):
        body = (f"Added: **{e.get('title')}** ({e.get('year', '?')}) is now in the "
                f"CinemaTimeGaze sample (source: wishlist). It won't count toward the "
                f"medians and charts unless 'Include wishlisted films' is switched on, "
                f"but it's searchable and pinnable in the timeline.")
        print(f"gh issue comment {e['issue']} --repo {a.repo} --body {shlex.quote(body)}")
        print(f"gh issue close {e['issue']} --repo {a.repo}")


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wishlist", default=str(WISHLIST))
    ap.add_argument("--films", default=str(FILMS), help="data/wishlist_films.jsonl cache")
    ap.add_argument("--sample", default=str(SAMPLE))
    ap.add_argument("--repo", default=REPO)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sync", help="pull open wishlist issues into data/wishlist.json")
    p.add_argument("--from-file", help="a JSON file shaped like `gh issue list --json ...` "
                                       "output, instead of calling gh (offline / tests)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("resolve", help="TMDB lookup for every requested entry")
    p.add_argument("--force", action="store_true", help="re-resolve already-resolved entries too")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("apply", help="append resolved films to data/sample.json")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("status", help="print a table of every wishlist entry")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("comment", help="print (don't run) the gh comment/close for shipped films")
    p.set_defaults(func=cmd_comment)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
