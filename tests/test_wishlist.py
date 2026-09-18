import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import wishlist  # noqa: E402


ISSUE_BODY = """### Film title

Spirited Away

### Release year

2001

### TMDB link or id

https://www.themoviedb.org/movie/129-spirited-away

### Why it belongs

_No response_
"""

ISSUE_BODY_NO_TMDB = """### Film title

A Made-Up Movie

### Release year

_No response_

### TMDB link or id

_No response_

### Why it belongs

Because it does.
"""


class FakeTMDB:
    """Mimics tmdb_fetch.Client's .get(path, params) surface, no network."""

    def __init__(self, movies=None, search=None):
        self.movies = movies or {}                 # {id: detail dict}
        self.search = search or {}                  # {(query, year_str): [result, ...]}

    def get(self, path, params=None):
        params = params or {}
        if path.startswith("/movie/"):
            mid = int(path.rsplit("/", 1)[-1])
            if mid not in self.movies:
                raise RuntimeError(f"404 no such movie {mid}")
            return self.movies[mid]
        if path == "/search/movie":
            key = (params.get("query", ""), str(params.get("year") or ""))
            return {"results": self.search.get(key, [])}
        raise AssertionError(f"unexpected path {path}")


def movie(mid, title, year, country="JP", genres=("Animation", "Fantasy"), votes=9000):
    return {
        "id": mid, "title": title, "original_title": title,
        "release_date": f"{year}-07-20",
        "production_countries": [{"iso_3166_1": country, "name": country}],
        "original_language": "ja",
        "genres": [{"id": i, "name": g} for i, g in enumerate(genres)],
        "overview": f"A long enough overview of {title} to pass any word-count filter easily.",
        "vote_count": votes, "vote_average": 8.5, "popularity": 50.0,
    }


class ParsingTests(unittest.TestCase):
    def test_parse_issue_body_extracts_all_fields(self):
        fields = wishlist.parse_issue_body(ISSUE_BODY)
        self.assertEqual(fields["title"], "Spirited Away")
        self.assertEqual(fields["year"], "2001")
        self.assertEqual(fields["tmdb"], "https://www.themoviedb.org/movie/129-spirited-away")
        self.assertIsNone(fields.get("why"))          # "_No response_" -> None

    def test_no_response_fields_are_none(self):
        fields = wishlist.parse_issue_body(ISSUE_BODY_NO_TMDB)
        self.assertEqual(fields["title"], "A Made-Up Movie")
        self.assertIsNone(fields["year"])
        self.assertIsNone(fields["tmdb"])
        self.assertEqual(fields["why"], "Because it does.")

    def test_extract_year(self):
        self.assertEqual(wishlist.extract_year("2001"), 2001)
        self.assertEqual(wishlist.extract_year("released in 1965, I think"), 1965)
        self.assertIsNone(wishlist.extract_year(None))
        self.assertIsNone(wishlist.extract_year("no idea"))

    def test_parse_tmdb_hint(self):
        self.assertEqual(wishlist.parse_tmdb_hint("https://www.themoviedb.org/movie/129-x"), 129)
        self.assertEqual(wishlist.parse_tmdb_hint("129"), 129)
        self.assertEqual(wishlist.parse_tmdb_hint("  129  "), 129)
        self.assertIsNone(wishlist.parse_tmdb_hint(None))
        self.assertIsNone(wishlist.parse_tmdb_hint("not a tmdb thing"))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.wl_path = ROOT / "tests" / "_tmp_wishlist.json"
        self.addCleanup(lambda: self.wl_path.unlink(missing_ok=True))

    def _args(self, from_file):
        return argparse_ns(wishlist=str(self.wl_path), from_file=from_file, repo="x/y")

    def test_sync_creates_new_entry(self):
        issues = [{"number": 1, "title": "Wishlist: Spirited Away", "body": ISSUE_BODY,
                  "createdAt": "2026-01-02T00:00:00Z"}]
        f = ROOT / "tests" / "_tmp_issues.json"
        f.write_text(json.dumps(issues))
        self.addCleanup(lambda: f.unlink(missing_ok=True))

        wishlist.cmd_sync(self._args(str(f)))
        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["title"], "Spirited Away")
        self.assertEqual(e["year"], 2001)
        self.assertEqual(e["issue"], 1)
        self.assertEqual(e["status"], "requested")
        self.assertEqual(e["added"], "2026-01-02")
        self.assertEqual(e["tmdb_hint"], "https://www.themoviedb.org/movie/129-spirited-away")

    def test_sync_is_idempotent(self):
        issues = [{"number": 1, "title": "Wishlist: Spirited Away", "body": ISSUE_BODY,
                  "createdAt": "2026-01-02T00:00:00Z"}]
        f = ROOT / "tests" / "_tmp_issues.json"
        f.write_text(json.dumps(issues))
        self.addCleanup(lambda: f.unlink(missing_ok=True))

        wishlist.cmd_sync(self._args(str(f)))
        first = self.wl_path.read_text()
        wishlist.cmd_sync(self._args(str(f)))
        second = self.wl_path.read_text()
        self.assertEqual(first, second)

    def test_sync_does_not_revert_a_resolved_entry(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": 129, "title": "Spirited Away", "year": 2001, "issue": 1,
            "added": "2026-01-01", "status": "resolved", "note": "", "tmdb_hint": "129",
        }])
        issues = [{"number": 1, "title": "Wishlist: Spirited Away", "body": ISSUE_BODY,
                  "createdAt": "2026-01-02T00:00:00Z"}]
        f = ROOT / "tests" / "_tmp_issues.json"
        f.write_text(json.dumps(issues))
        self.addCleanup(lambda: f.unlink(missing_ok=True))

        wishlist.cmd_sync(self._args(str(f)))
        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["status"], "resolved")
        self.assertEqual(entries[0]["tmdb_id"], 129)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.wl_path = ROOT / "tests" / "_tmp_wishlist2.json"
        self.films_path = ROOT / "tests" / "_tmp_wishlist_films.jsonl"
        self.addCleanup(lambda: self.wl_path.unlink(missing_ok=True))
        self.addCleanup(lambda: self.films_path.unlink(missing_ok=True))

    def _args(self, force=False):
        return argparse_ns(wishlist=str(self.wl_path), films=str(self.films_path), force=force)

    def test_resolve_by_explicit_id(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": None, "title": "Spirited Away", "year": 2001, "issue": 1,
            "added": "2026-01-01", "status": "requested", "note": "", "tmdb_hint": "129",
        }])
        client = FakeTMDB(movies={129: movie(129, "Spirited Away", 2001)})
        a = self._args(); a.client = client
        wishlist.cmd_resolve(a)

        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["status"], "resolved")
        self.assertEqual(entries[0]["tmdb_id"], 129)
        cache = wishlist.load_films_cache(self.films_path)
        self.assertIn(129, cache)
        self.assertEqual(cache[129]["country"], "JP")
        self.assertNotIn("region", cache[129])   # region is sample_row's job, not the cache's

    def test_resolve_by_search_when_no_id(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": None, "title": "Heat", "year": 1995, "issue": 2,
            "added": "2026-01-01", "status": "requested", "note": "", "tmdb_hint": None,
        }])
        client = FakeTMDB(
            search={("Heat", "1995"): [{"id": 949, "release_date": "1995-12-15"}]},
            movies={949: movie(949, "Heat", 1995, country="US")})
        a = self._args(); a.client = client
        wishlist.cmd_resolve(a)

        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["status"], "resolved")
        self.assertEqual(entries[0]["tmdb_id"], 949)

    def test_unresolvable_keeps_status_requested_with_a_note(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": None, "title": "Totally Fictional Film Xyz", "year": None, "issue": 3,
            "added": "2026-01-01", "status": "requested", "note": "", "tmdb_hint": None,
        }])
        client = FakeTMDB(search={})
        a = self._args(); a.client = client
        wishlist.cmd_resolve(a)

        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["status"], "requested")
        self.assertTrue(entries[0]["note"])
        self.assertIsNone(entries[0]["tmdb_id"])

    def test_resolve_is_idempotent_without_force(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": 129, "title": "Spirited Away", "year": 2001, "issue": 1,
            "added": "2026-01-01", "status": "resolved", "note": "", "tmdb_hint": "129",
        }])
        client = FakeTMDB(movies={129: movie(129, "DIFFERENT TITLE", 2001)})
        a = self._args(); a.client = client
        wishlist.cmd_resolve(a)
        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["title"], "Spirited Away")   # untouched


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.wl_path = ROOT / "tests" / "_tmp_wishlist3.json"
        self.films_path = ROOT / "tests" / "_tmp_wishlist_films3.jsonl"
        self.sample_path = ROOT / "tests" / "_tmp_sample.json"
        for p in (self.wl_path, self.films_path, self.sample_path):
            self.addCleanup(lambda p=p: p.unlink(missing_ok=True))

    def _args(self):
        return argparse_ns(wishlist=str(self.wl_path), films=str(self.films_path),
                           sample=str(self.sample_path))

    def test_apply_appends_resolved_film_with_source_wishlist(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": 129, "title": "Spirited Away", "year": 2001, "issue": 1,
            "added": "2026-01-01", "status": "resolved", "note": "", "tmdb_hint": "129",
        }])
        wishlist.save_films_cache(self.films_path, {
            129: wishlist.flatten_movie(movie(129, "Spirited Away", 2001)),
        })
        self.sample_path.write_text(json.dumps([]))

        wishlist.cmd_apply(self._args())

        sample = json.loads(self.sample_path.read_text())
        self.assertEqual(len(sample), 1)
        self.assertEqual(sample[0]["source"], "wishlist")
        self.assertEqual(sample[0]["tmdb_id"], 129)
        self.assertEqual(sample[0]["title"], "Spirited Away")
        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["status"], "in_sample")

    def test_apply_skips_film_already_in_sample(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": 129, "title": "Spirited Away", "year": 2001, "issue": 1,
            "added": "2026-01-01", "status": "resolved", "note": "", "tmdb_hint": "129",
        }])
        wishlist.save_films_cache(self.films_path, {
            129: wishlist.flatten_movie(movie(129, "Spirited Away", 2001)),
        })
        self.sample_path.write_text(json.dumps([{"tmdb_id": 129, "title": "Spirited Away"}]))

        wishlist.cmd_apply(self._args())

        sample = json.loads(self.sample_path.read_text())
        self.assertEqual(len(sample), 1)                     # not duplicated
        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["status"], "in_sample")
        self.assertEqual(entries[0]["note"], "already sampled")

    def test_apply_is_idempotent(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": 129, "title": "Spirited Away", "year": 2001, "issue": 1,
            "added": "2026-01-01", "status": "resolved", "note": "", "tmdb_hint": "129",
        }])
        wishlist.save_films_cache(self.films_path, {
            129: wishlist.flatten_movie(movie(129, "Spirited Away", 2001)),
        })
        self.sample_path.write_text(json.dumps([]))

        wishlist.cmd_apply(self._args())
        wishlist.cmd_apply(self._args())

        sample = json.loads(self.sample_path.read_text())
        self.assertEqual(len(sample), 1)                     # second run is a no-op

    def test_apply_reports_missing_cache_without_crashing(self):
        wishlist.save_wishlist(self.wl_path, [{
            "tmdb_id": 129, "title": "Spirited Away", "year": 2001, "issue": 1,
            "added": "2026-01-01", "status": "resolved", "note": "", "tmdb_hint": "129",
        }])
        self.sample_path.write_text(json.dumps([]))

        wishlist.cmd_apply(self._args())          # no films cache file at all

        sample = json.loads(self.sample_path.read_text())
        self.assertEqual(sample, [])
        entries = wishlist.load_wishlist(self.wl_path)
        self.assertEqual(entries[0]["status"], "resolved")   # unchanged, not silently advanced


class StatusCommentTests(unittest.TestCase):
    def setUp(self):
        self.wl_path = ROOT / "tests" / "_tmp_wishlist4.json"
        self.addCleanup(lambda: self.wl_path.unlink(missing_ok=True))

    def test_comment_prints_gh_commands_only_for_in_sample_with_issue(self):
        wishlist.save_wishlist(self.wl_path, [
            {"tmdb_id": 1, "title": "Shipped Film", "year": 2001, "issue": 7,
            "added": "2026-01-01", "status": "in_sample", "note": ""},
            {"tmdb_id": 2, "title": "Still Requested", "year": 2001, "issue": 8,
            "added": "2026-01-01", "status": "requested", "note": ""},
        ])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wishlist.cmd_comment(argparse_ns(wishlist=str(self.wl_path), repo="x/y"))
        out = buf.getvalue()
        self.assertIn("gh issue comment 7 --repo x/y", out)
        self.assertIn("gh issue close 7 --repo x/y", out)
        self.assertNotIn(" 8 --repo", out)


def argparse_ns(**kw):
    import argparse
    return argparse.Namespace(**kw)


if __name__ == "__main__":
    unittest.main()
