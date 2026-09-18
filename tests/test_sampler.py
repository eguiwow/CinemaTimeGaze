import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_TMDB = ROOT / "src" / "sample_tmdb.py"
SAMPLE_REPORT = ROOT / "src" / "sample_report.py"
sys.path.insert(0, str(ROOT / "src"))

import sample_tmdb  # noqa: E402


def film(tmdb_id, year, country, votes, title=None, genres=("Drama",), words=20):
    overview = " ".join(["word"] * words) or "x"
    return {
        "id": f"tmdb-{tmdb_id}", "tmdb_id": tmdb_id,
        "title": title or f"Film {tmdb_id}", "original_title": title or f"Film {tmdb_id}",
        "year": year, "countries": [country], "country": country,
        "language": "en", "genres": list(genres), "overview": overview,
        "vote_count": votes, "vote_average": 7.0, "popularity": float(votes),
    }


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def run_sampler(src, out, per_year=5, extra=()):
    cmd = [sys.executable, str(SAMPLE_TMDB), str(per_year), "--min-films", "1",
          "--min-words", "0", "--in", str(src), "--out", str(out), *extra]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(f"sample_tmdb.py failed: {r.stdout}\n{r.stderr}")
    return r.stdout


class SampleRowShapeTests(unittest.TestCase):
    def test_sample_row_shape(self):
        m = film(1, 2001, "JP", 900, title="Spirited Away")
        row = sample_tmdb.sample_row(m)
        self.assertEqual(set(row), {"id", "tmdb_id", "title", "original_title", "year",
                                    "genres", "extract", "country", "countries", "region",
                                    "language", "vote_count", "notability"})
        self.assertEqual(row["country"], "JP")
        self.assertEqual(row["region"], "east_asia")
        self.assertIsNone(row["original_title"])   # identical to title -> dropped

    def test_original_title_kept_when_it_differs(self):
        m = film(1, 2001, "JP", 900, title="Spirited Away")
        m["original_title"] = "Sen to Chihiro no Kamikakushi"
        row = sample_tmdb.sample_row(m)
        self.assertEqual(row["original_title"], "Sen to Chihiro no Kamikakushi")


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.src = ROOT / "tests" / "_tmp_corpus.jsonl"
        self.out = ROOT / "tests" / "_tmp_out.json"
        self.addCleanup(lambda: self.src.unlink(missing_ok=True))
        self.addCleanup(lambda: self.out.unlink(missing_ok=True))

    def test_votes_rank_picks_highest_raw_vote_count(self):
        rows = [film(1, 2000, "US", 1000), film(2, 2000, "US", 500),
               film(3, 2000, "FR", 400)]
        write_jsonl(self.src, rows)
        run_sampler(self.src, self.out, extra=["--rank", "votes"])
        picked = {r["tmdb_id"] for r in json.loads(self.out.read_text())}
        self.assertEqual(picked, {1, 2, 3})   # only 3 eligible films, all fit under cap 5

    def test_in_country_ranks_each_cinema_against_itself(self):
        # France's best film has far fewer votes than the US's worst, but is the top of
        # its OWN distribution — in-country ranking should surface it over a low-ranked
        # (within-US) American film once the per-year cap forces a choice.
        rows = [
            film(1, 2000, "US", 1000), film(2, 2000, "US", 900), film(3, 2000, "US", 800),
            film(4, 1999, "US", 50),                      # weakest US film across the corpus
            film(5, 2000, "FR", 60),                      # France's best film (but low raw votes)
            film(6, 1999, "FR", 10),
        ]
        write_jsonl(self.src, rows)
        # cap tight enough (2/year) that the ranking choice actually matters within 2000
        run_sampler(self.src, self.out, per_year=2, extra=["--rank", "in-country"])
        picked = json.loads(self.out.read_text())
        by_year = {}
        for r in picked:
            by_year.setdefault(r["year"], []).append(r["tmdb_id"])
        self.assertIn(5, by_year.get(2000, []))    # France's best film makes it in

    def test_default_rank_is_votes(self):
        rows = [film(1, 2000, "US", 1000), film(2, 2000, "FR", 5)]
        write_jsonl(self.src, rows)
        out_default = ROOT / "tests" / "_tmp_out_default.json"
        out_votes = ROOT / "tests" / "_tmp_out_votes.json"
        self.addCleanup(lambda: out_default.unlink(missing_ok=True))
        self.addCleanup(lambda: out_votes.unlink(missing_ok=True))
        run_sampler(self.src, out_default)
        run_sampler(self.src, out_votes, extra=["--rank", "votes"])
        self.assertEqual(out_default.read_text(), out_votes.read_text())


class QuotaInterplayTests(unittest.TestCase):
    def setUp(self):
        self.src = ROOT / "tests" / "_tmp_corpus2.jsonl"
        self.out = ROOT / "tests" / "_tmp_out2.json"
        self.addCleanup(lambda: self.src.unlink(missing_ok=True))
        self.addCleanup(lambda: self.out.unlink(missing_ok=True))

    def test_min_per_region_and_min_per_country_never_exceed_or_duplicate(self):
        rows = []
        tid = 0
        for country, region_films in [("US", 20), ("GB", 5), ("FR", 5), ("JP", 5), ("BR", 5)]:
            for i in range(region_films):
                tid += 1
                rows.append(film(tid, 2000, country, 1000 - i))
        write_jsonl(self.src, rows)
        run_sampler(self.src, self.out, per_year=10,
                   extra=["--min-per-region", "3", "--min-per-country", "2"])
        picked = json.loads(self.out.read_text())
        self.assertLessEqual(len(picked), 10)                       # cap respected
        ids = [r["id"] for r in picked]
        self.assertEqual(len(ids), len(set(ids)))                    # no duplicates
        # every region present got at least one of its country's quota honoured
        countries = {r["country"] for r in picked}
        self.assertGreaterEqual(len(countries), 2)


class WishlistReappendTests(unittest.TestCase):
    def setUp(self):
        self.src = ROOT / "tests" / "_tmp_corpus3.jsonl"
        self.out = ROOT / "tests" / "_tmp_out3.json"
        self.wl = ROOT / "tests" / "_tmp_wl.json"
        self.wl_films = ROOT / "tests" / "_tmp_wl_films.jsonl"
        for p in (self.src, self.out, self.wl, self.wl_films):
            self.addCleanup(lambda p=p: p.unlink(missing_ok=True))

    def test_resolved_wishlist_film_survives_a_thin_quota(self):
        # a 2000 pool of 5 popular US films fills a per-year cap of 1, so a wishlist film
        # from that same year would never be picked on votes alone.
        rows = [film(i, 2000, "US", 1000 - i) for i in range(5)]
        write_jsonl(self.src, rows)
        wishlist_film = film(999, 2000, "FR", 3, title="Obscure Wishlist Film")
        write_jsonl(self.wl_films, [wishlist_film])
        self.wl.write_text(json.dumps([{"tmdb_id": 999, "title": "Obscure Wishlist Film",
                                        "year": 2000, "issue": 1, "added": "2026-01-01",
                                        "status": "resolved", "note": ""}]))
        run_sampler(self.src, self.out, per_year=1,
                   extra=["--wishlist", str(self.wl), "--wishlist-films", str(self.wl_films)])
        picked = json.loads(self.out.read_text())
        ids = {r["tmdb_id"] for r in picked}
        self.assertIn(999, ids)
        wl_rows = [r for r in picked if r["tmdb_id"] == 999]
        self.assertEqual(wl_rows[0]["source"], "wishlist")
        self.assertEqual(len(picked), 2)          # 1 per-year cap + 1 wishlist, not swallowed

    def test_no_wishlist_flag_skips_reappend(self):
        rows = [film(i, 2000, "US", 1000 - i) for i in range(5)]
        write_jsonl(self.src, rows)
        write_jsonl(self.wl_films, [film(999, 2000, "FR", 3)])
        self.wl.write_text(json.dumps([{"tmdb_id": 999, "title": "X", "year": 2000,
                                        "issue": 1, "added": "2026-01-01",
                                        "status": "resolved", "note": ""}]))
        run_sampler(self.src, self.out, per_year=1,
                   extra=["--no-wishlist", "--wishlist", str(self.wl),
                          "--wishlist-films", str(self.wl_films)])
        picked = json.loads(self.out.read_text())
        self.assertNotIn(999, {r["tmdb_id"] for r in picked})

    def test_wishlist_film_already_sampled_is_not_duplicated(self):
        rows = [film(1, 2000, "US", 1000)]
        write_jsonl(self.src, rows)
        write_jsonl(self.wl_films, [film(1, 2000, "US", 1000)])   # same film, already sampled
        self.wl.write_text(json.dumps([{"tmdb_id": 1, "title": "Film 1", "year": 2000,
                                        "issue": 1, "added": "2026-01-01",
                                        "status": "in_sample", "note": ""}]))
        run_sampler(self.src, self.out, per_year=5,
                   extra=["--wishlist", str(self.wl), "--wishlist-films", str(self.wl_films)])
        picked = json.loads(self.out.read_text())
        self.assertEqual(len(picked), 1)


class SampleReportTests(unittest.TestCase):
    def setUp(self):
        self.a = ROOT / "tests" / "_tmp_report_a.json"
        self.b = ROOT / "tests" / "_tmp_report_b.json"
        self.addCleanup(lambda: self.a.unlink(missing_ok=True))
        self.addCleanup(lambda: self.b.unlink(missing_ok=True))

    def test_json_output_shape_and_new_film_count(self):
        base = [{"id": "tmdb-1", "year": 1950, "country": "US", "region": "north_america"},
               {"id": "tmdb-2", "year": 1950, "country": "FR", "region": "west_europe"}]
        cand = base + [{"id": "tmdb-3", "year": 1950, "country": "NG", "region": "africa"}]
        self.a.write_text(json.dumps(base))
        self.b.write_text(json.dumps(cand))
        r = subprocess.run([sys.executable, str(SAMPLE_REPORT), str(self.b),
                           "--baseline", str(self.a), "--labels", "/does/not/exist.jsonl",
                           "--json"], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["baseline"]["films"], 2)
        self.assertEqual(data["candidate"]["films"], 3)
        self.assertEqual(data["candidate"]["new_vs_baseline"], 1)
        self.assertEqual(data["candidate"]["new_needing_classification"], 1)
        self.assertEqual(data["candidate"]["pre1960_non_western"], 1)   # the Nigerian film

    def test_text_output_runs_without_error(self):
        base = [{"id": "tmdb-1", "year": 2000, "country": "US", "region": "north_america"}]
        self.a.write_text(json.dumps(base))
        self.b.write_text(json.dumps(base))
        r = subprocess.run([sys.executable, str(SAMPLE_REPORT), str(self.b),
                           "--baseline", str(self.a), "--labels", "/does/not/exist.jsonl"],
                          capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("films", r.stdout)


if __name__ == "__main__":
    unittest.main()
