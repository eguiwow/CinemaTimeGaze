#!/usr/bin/env python3
"""Build-side SQLite view of the corpus — a build/inspection tool, not part of the served page.

v4 item 4: the published pipeline stays flat files (data/*.json[l]) all the way through
build_viz.py — nothing about how the page is served changes. But the enrichment pass, the
sampler and the classifier all want joins and partial updates, and grepping JSONL for those is
painful. This script loads every flat file the build already produces into data/corpus.db
(gitignored, never committed) so `sql "..."` and `stats` can answer questions the flat files
can't easily answer on their own.

    python3 src/corpus_db.py build             # (re)build data/corpus.db from scratch
    python3 src/corpus_db.py stats             # useful counts
    python3 src/corpus_db.py sql "select country, count(*) from films group by 1 order by 2 desc"

`build` is idempotent: it always starts from an empty database (the file is removed and
recreated), so a stale row never survives a source file changing shape. Every input is
optional except data/sample.json — a missing tmdb_films.jsonl, credits.json or wishlist.json
just means that table stays empty.
"""
import argparse, json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "corpus.db"

THRESHOLD = 25  # same bar build_credits.py / the page's aggregate-view cutoff uses
WEAK_CONF_CUTOFF = 0.7  # item 8's "weak rows": basis == knowledge and confidence below this


SCHEMA = """
create table films (
  id text primary key, tmdb_id integer, title text, original_title text, year integer,
  country text, countries text, region text, language text,
  vote_count integer, notability integer, source text, extract text
);
create table tmdb_films (
  id text primary key, tmdb_id integer, title text, original_title text, year integer,
  country text, countries text, language text, genres text,
  overview text, vote_count integer, vote_average real, popularity real
);
create table labels (
  film_id text, labelset text, gaze text, earthbound integer, confidence real,
  basis text, targets text, model text, prompt text,
  primary key (film_id, labelset)
);
create table credits_people (idx integer primary key, tmdb_person_id integer, name text);
create table credits_companies (idx integer primary key, key text, label text);
create table film_directors (film_id text, person_idx integer);
create table film_actors (film_id text, person_idx integer, billing integer);
create table film_companies (film_id text, company_idx integer);
create table wishlist (
  tmdb_id integer primary key, title text, year integer, issue integer,
  added text, status text, note text
);
create index idx_films_country on films(country);
create index idx_labels_film on labels(film_id);
create index idx_fd_person on film_directors(person_idx);
create index idx_fa_person on film_actors(person_idx);
create index idx_fc_company on film_companies(company_idx);
"""


def _read_jsonl(path):
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().split("\n"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build(db_path=None, data_dir=None):
    db_path = Path(db_path) if db_path else DB_PATH
    data_dir = Path(data_dir) if data_dir else DATA
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_path))
    con.executescript(SCHEMA)

    counts = {}

    # ---- films (data/sample.json — required) ----
    sample_path = data_dir / "sample.json"
    if not sample_path.exists():
        con.close()
        raise SystemExit(f"{sample_path} not found — nothing to build from")
    sample = json.loads(sample_path.read_text())
    con.executemany(
        "insert into films values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["id"], r.get("tmdb_id"), r.get("title"), r.get("original_title"), r.get("year"),
          r.get("country"), json.dumps(r.get("countries") or []), r.get("region"),
          r.get("language"), r.get("vote_count") or 0, r.get("notability") or 0,
          r.get("source"), r.get("extract"))
         for r in sample])
    counts["films"] = len(sample)

    # ---- tmdb_films.jsonl (optional — the full discover corpus, not just the sample) ----
    tf_path = data_dir / "tmdb_films.jsonl"
    tf_rows = _read_jsonl(tf_path)
    if tf_rows:
        con.executemany(
            "insert or replace into tmdb_films values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["id"], r.get("tmdb_id"), r.get("title"), r.get("original_title"), r.get("year"),
              r.get("country"), json.dumps(r.get("countries") or []), r.get("language"),
              json.dumps(r.get("genres") or []), r.get("overview"),
              r.get("vote_count") or 0, r.get("vote_average") or 0, r.get("popularity") or 0)
             for r in tf_rows])
    counts["tmdb_films"] = len(tf_rows)

    # ---- every data/labels_*.jsonl — each file is its own labelset, never merged in place ----
    label_files = sorted(data_dir.glob("labels_*.jsonl"))
    n_labels = 0
    for lf in label_files:
        labelset = lf.stem  # e.g. "labels_api"
        rows = _read_jsonl(lf)
        con.executemany(
            "insert or replace into labels values (?,?,?,?,?,?,?,?,?)",
            [(r["id"], r.get("labelset", labelset), r.get("gaze"),
              int(bool(r.get("earthbound"))) if r.get("earthbound") is not None else None,
              r.get("confidence"), r.get("basis"), json.dumps(r.get("targets") or []),
              r.get("model"), r.get("prompt"))
             for r in rows])
        n_labels += len(rows)
        counts[f"labels:{labelset}"] = len(rows)
    counts["labels_total"] = n_labels

    # ---- credits: prefer the built data/credits.json, else the raw per-film cache ----
    credits_path = data_dir / "credits.json"
    if credits_path.exists():
        c = json.loads(credits_path.read_text())
        con.executemany("insert into credits_people values (?,?,?)",
                         [(i, pid, name) for i, (pid, name) in enumerate(c.get("people") or [])])
        con.executemany("insert into credits_companies values (?,?,?)",
                         [(i, key, label) for i, (key, label) in enumerate(c.get("companies") or [])])
        fd, fa, fc = [], [], []
        for fid, f in (c.get("films") or {}).items():
            fd += [(fid, i) for i in f.get("d") or []]
            fa += [(fid, i, order) for order, i in enumerate(f.get("a") or [])]
            fc += [(fid, i) for i in f.get("co") or []]
        con.executemany("insert into film_directors values (?,?)", fd)
        con.executemany("insert into film_actors values (?,?,?)", fa)
        con.executemany("insert into film_companies values (?,?)", fc)
        counts["credits_films"] = len(c.get("films") or {})
    else:
        counts["credits_films"] = 0

    # ---- wishlist.json (optional) ----
    wl_path = data_dir / "wishlist.json"
    if wl_path.exists():
        wl = json.loads(wl_path.read_text())
        con.executemany(
            "insert or replace into wishlist values (?,?,?,?,?,?,?)",
            [(r["tmdb_id"], r.get("title"), r.get("year"), r.get("issue"),
              r.get("added"), r.get("status"), r.get("note")) for r in wl])
        counts["wishlist"] = len(wl)
    else:
        counts["wishlist"] = 0

    con.commit()
    con.close()
    return counts


def stats(db_path=None):
    db_path = Path(db_path) if db_path else DB_PATH
    if not db_path.exists():
        sys.exit(f"{db_path} not found — run `python3 src/corpus_db.py build` first")
    con = sqlite3.connect(str(db_path))

    n_films = con.execute("select count(*) from films").fetchone()[0]
    n_wishlist_films = con.execute(
        "select count(*) from films where source = 'wishlist'").fetchone()[0]
    print(f"films             {n_films}  ({n_wishlist_films} wishlist, outside every aggregate)")

    print("\nlabelled per labelset:")
    for labelset, n in con.execute(
            "select labelset, count(*) from labels group by 1 order by 1"):
        print(f"  {labelset:<20} {n}")

    weak = con.execute(
        "select count(*) from labels where basis = 'knowledge' and confidence < ?",
        (WEAK_CONF_CUTOFF,)).fetchone()[0]
    print(f"\nweak rows (basis=knowledge, confidence < {WEAK_CONF_CUTOFF}): {weak}")

    print("\nfilms per country (top 20):")
    for country, n in con.execute(
            "select country, count(*) from films group by 1 order by 2 desc limit 20"):
        print(f"  {country or '?':<4} {n}")

    print(f"\ndirectors clearing {THRESHOLD} films in this sample:")
    rows = con.execute("""
        select p.name, count(distinct fd.film_id) as n
        from film_directors fd join credits_people p on p.idx = fd.person_idx
        group by fd.person_idx having n >= ? order by n desc limit 20
    """, (THRESHOLD,)).fetchall()
    if rows:
        for name, n in rows:
            print(f"  {n:>4}  {name}")
    else:
        print("  (none — no credits loaded yet, or none clear the bar)")

    con.close()


def run_sql(query, db_path=None):
    db_path = Path(db_path) if db_path else DB_PATH
    if not db_path.exists():
        sys.exit(f"{db_path} not found — run `python3 src/corpus_db.py build` first")
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(query)
    except sqlite3.Error as e:
        con.close()
        sys.exit(f"sql error: {e}")
    cols = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchall()
    con.close()
    if cols:
        print("\t".join(cols))
    for r in rows:
        print("\t".join("" if v is None else str(v) for v in r))
    if cols:
        print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="path to corpus.db (default data/corpus.db)")
    ap.add_argument("--data-dir", default=None, help="path to the data/ dir to build from")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("stats")
    p_sql = sub.add_parser("sql")
    p_sql.add_argument("query")
    a = ap.parse_args()

    if a.cmd == "build":
        counts = build(a.db, a.data_dir)
        print(f"built {a.db or DB_PATH}")
        for k, v in counts.items():
            print(f"  {k:<20} {v}")
    elif a.cmd == "stats":
        stats(a.db)
    elif a.cmd == "sql":
        run_sql(a.query, a.db)


if __name__ == "__main__":
    main()
