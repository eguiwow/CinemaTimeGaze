#!/usr/bin/env python3
"""Fetch a longer plot summary from English Wikipedia for weak-row films.

TMDB's own longer fields are not worth chasing first: the flattened corpus
(data/tmdb_films.jsonl) carries only "overview" (the same text sample.json
calls "extract") and no tagline, no full synopsis field — /movie/{id} has
nothing longer than overview either. Wikipedia's "Plot" section is
routinely several times longer and is where this pass gets its extra text.

Pipeline per film: MediaWiki search for "<title> <year> film" -> best hit's
page title -> that page's section list -> the "Plot" (or "Synopsis"/"Story")
section's wikitext -> stripped to plain text.

Resumable and cached: every attempt (success or not) is cached per film id
under data/plots_raw/ (gitignored — Wikipedia article text, not ours to
commit), so a rerun only fetches ids it has not seen before. Rate-limited
and identifies itself, per Wikimedia API etiquette.

    python3 src/fetch_plots.py --dry-run              # show the queue, no network calls
    python3 src/fetch_plots.py --limit 20              # try it on a small batch first
    python3 src/fetch_plots.py                         # the real run, all of data/weak_ids.txt
    python3 src/fetch_plots.py --ids data/boundary_ids.txt --out data/plots_boundary.jsonl

No network here (this environment blocks it) — this script is written and unit-tested with
a mocked client; it is meant to be run from a normal terminal.
"""
import argparse, json, re, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://en.wikipedia.org/w/api.php"
PLOT_HEADINGS = {"plot", "plot summary", "synopsis", "story", "premise"}
MIN_WORDS = 20          # shorter than this isn't worth swapping in for the TMDB extract


# ---------------------------------------------------------------- http

class WikiClient:
    """Polite MediaWiki client: paced, retrying, identifies itself. No API key needed —
    the public action API is open, but Wikimedia asks for a descriptive User-Agent."""

    def __init__(self, rps=1.0, timeout=20,
                 contact="https://github.com/eguiwow/CinemaTimeGaze"):
        self.min_gap = 1.0 / rps if rps > 0 else 0
        self._last = 0.0
        self.timeout = timeout
        self.ua = f"CinemaTimeGaze/1.0 ({contact}) plot-fetcher"

    def _wait(self):
        gap = time.monotonic() - self._last
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)

    def get(self, params, tries=4):
        params = dict(params)
        params["format"] = "json"
        url = f"{API}?{urllib.parse.urlencode(params)}"
        for attempt in range(tries):
            self._wait()
            self._last = time.monotonic()
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": self.ua, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After", "5")) + 1
                    time.sleep(wait); continue
                if 500 <= e.code < 600 and attempt < tries - 1:
                    time.sleep(2 ** attempt); continue
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < tries - 1:
                    time.sleep(2 ** attempt); continue
                raise
        raise RuntimeError("gave up after repeated failures")


# ---------------------------------------------------------------- wikitext -> plain text

def strip_wikitext(text):
    """Rough but dependency-free wikitext -> plain text. Good enough for a plot paragraph;
    not a general MediaWiki renderer."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)                    # comments
    text = re.sub(r"<ref[^>]*/>", "", text)                               # self-closed refs
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)           # refs with body
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)                                   # remaining html tags

    prev = None
    while prev != text:                                                  # nested {{templates}}
        prev = text
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)

    def wikilink(m):
        return m.group(1).split("|")[-1]
    text = re.sub(r"\[\[([^\]]+)\]\]", wikilink, text)

    def extlink(m):
        parts = m.group(1).split(" ", 1)
        return parts[1] if len(parts) > 1 else ""
    text = re.sub(r"\[([^\]]+)\]", extlink, text)

    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"^=+\s*.*?\s*=+$", "", text, flags=re.M)               # stray headings
    text = re.sub(r"^[\*#:;]+\s*", "", text, flags=re.M)                  # list/indent markers
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------- pipeline

def search_page(client, title, year):
    """Best-guess page for a film: search '<title> <year> film', take the top hit."""
    data = client.get({"action": "query", "list": "search",
                        "srsearch": f"{title} {year} film", "srlimit": 5})
    hits = data.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def plot_section_index(client, page_title):
    data = client.get({"action": "parse", "page": page_title, "prop": "sections"})
    if "error" in data:
        return None
    for s in data.get("parse", {}).get("sections", []):
        if s.get("line", "").strip().lower() in PLOT_HEADINGS:
            return s.get("index")
    return None


def section_wikitext(client, page_title, index):
    data = client.get({"action": "parse", "page": page_title,
                        "prop": "wikitext", "section": index})
    if "error" in data:
        return None
    return data.get("parse", {}).get("wikitext", {}).get("*", "")


def fetch_one(client, film):
    """Returns a cache record: always has 'status'; 'plot'/'source' only when status=='ok'."""
    title, year = film["title"], film["year"]
    try:
        page = search_page(client, title, year)
        if not page:
            return {"status": "no_page"}
        idx = plot_section_index(client, page)
        if idx is None:
            return {"status": "no_plot_section", "page": page}
        wikitext = section_wikitext(client, page, idx)
        if not wikitext:
            return {"status": "empty_section", "page": page}
        plot = strip_wikitext(wikitext)
        if len(plot.split()) < MIN_WORDS:
            return {"status": "too_short", "page": page}
        return {"status": "ok", "page": page, "plot": plot,
                "source": f"wikipedia:{page}#Plot"}
    except Exception as e:
        return {"status": "error", "error": repr(e)}


# ---------------------------------------------------------------- CLI

def resolve(name, default_dir=ROOT / "data"):
    p = Path(name)
    return p if p.is_absolute() or "/" in name else default_dir / name


def load_ids(path):
    return [line.strip() for line in Path(path).read_text().split("\n") if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default=str(ROOT / "data" / "weak_ids.txt"))
    ap.add_argument("--sample", default=str(ROOT / "data" / "sample.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "plots.jsonl"))
    ap.add_argument("--cache-dir", default=str(ROOT / "data" / "plots_raw"))
    ap.add_argument("--limit", type=int, default=0, help="0 = no cap")
    ap.add_argument("--rps", type=float, default=1.0, help="requests per second, be polite")
    ap.add_argument("--force", action="store_true", help="ignore the cache, refetch everything")
    ap.add_argument("--dry-run", action="store_true", help="print the queue, no network calls")
    a = ap.parse_args()

    ids = load_ids(a.ids)
    if a.limit:
        ids = ids[:a.limit]
    sample = {r["id"]: r for r in json.loads(Path(a.sample).read_text())}
    cache_dir = resolve(a.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    missing = [i for i in ids if i not in sample]
    ids = [i for i in ids if i in sample]
    if missing:
        print(f"  {len(missing)} ids not in {Path(a.sample).name} — skipped")

    print(f"{len(ids)} films queued from {Path(a.ids).name}")
    if a.dry_run:
        for i in ids[:20]:
            f = sample[i]
            print(f"  {i:<16} {f['title']} ({f['year']})  -> search '{f['title']} {f['year']} film'")
        if len(ids) > 20:
            print(f"  ... and {len(ids) - 20} more")
        print("\nNo calls made. Drop --dry-run to run it.")
        return

    client = WikiClient(rps=a.rps)
    status_tally = {}
    n_fetched = 0
    for i in ids:
        cache_path = cache_dir / f"{i}.json"
        if cache_path.exists() and not a.force:
            rec = json.loads(cache_path.read_text())
        else:
            rec = fetch_one(client, sample[i])
            cache_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
            n_fetched += 1
            print(f"  {i:<16} {sample[i]['title'][:40]:<40} {rec['status']}")
        status_tally[rec["status"]] = status_tally.get(rec["status"], 0) + 1

    ok = []
    for i in ids:
        rec = json.loads((cache_dir / f"{i}.json").read_text())
        if rec.get("status") == "ok":
            ok.append({"id": i, "plot": rec["plot"], "source": rec["source"]})

    out_path = resolve(a.out)
    out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ok)
                         + ("\n" if ok else ""))

    print(f"\nfetched {n_fetched} this run ({len(ids) - n_fetched} served from cache)")
    print("outcomes:", dict(sorted(status_tally.items(), key=lambda kv: -kv[1])))
    print(f"wrote {len(ok)} plots to {out_path}")
    print(f"\nnext:  python3 src/classify_api.py --ids {a.ids} --plots {out_path} "
          f"--words 250 --out data/labels_pass2.jsonl --labelset pass2")


if __name__ == "__main__":
    main()
