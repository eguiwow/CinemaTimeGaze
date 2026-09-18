import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import tmdb_credits           # noqa: E402
import build_credits          # noqa: E402


class FakeClient:
    """Stands in for tmdb_fetch.Client: same .get(path, params) shape, no network."""

    def __init__(self, responses):
        self.responses = responses  # {tmdb_id: detail_dict_or_HTTPError}
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        tmdb_id = int(path.rsplit("/", 1)[-1])
        resp = self.responses[tmdb_id]
        if isinstance(resp, Exception):
            raise resp
        return resp


def detail(title="A Film", year="2001", directors=(), cast=(), companies=()):
    return {
        "title": title,
        "release_date": f"{year}-01-01",
        "credits": {
            "crew": [{"id": d[0], "name": d[1], "job": "Director"} for d in directors],
            "cast": [{"id": c[0], "name": c[1], "order": c[2]} for c in cast],
        },
        "production_companies": [{"id": co[0], "name": co[1]} for co in companies],
    }


class TestSlim(unittest.TestCase):
    def test_slim_keeps_directors_top8_cast_and_companies(self):
        d = detail(
            directors=[(1, "Alice Director")],
            cast=[(i, f"Actor {i}", i) for i in range(12)],  # 12 cast members, order 0..11
            companies=[(174, "Warner Bros. Pictures")],
        )
        s = tmdb_credits.slim(d)
        self.assertEqual(s["directors"], [{"id": 1, "name": "Alice Director"}])
        self.assertEqual(len(s["cast"]), 8)
        self.assertEqual([c["order"] for c in s["cast"]], [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(s["companies"], [{"id": 174, "name": "Warner Bros. Pictures"}])

    def test_slim_ranks_cast_by_order_even_if_input_unsorted(self):
        d = detail(cast=[(3, "Third", 2), (1, "First", 0), (2, "Second", 1)])
        s = tmdb_credits.slim(d)
        self.assertEqual([c["id"] for c in s["cast"]], [1, 2, 3])

    def test_slim_dedupes_repeated_director_credit(self):
        # co-directors sometimes appear twice in TMDB's crew list under different roles
        d = {"credits": {"crew": [{"id": 5, "name": "Dupe", "job": "Director"},
                                   {"id": 5, "name": "Dupe", "job": "Director"}],
                          "cast": []},
             "production_companies": []}
        s = tmdb_credits.slim(d)
        self.assertEqual(s["directors"], [{"id": 5, "name": "Dupe"}])

    def test_slim_handles_missing_fields(self):
        s = tmdb_credits.slim({})
        self.assertEqual(s, {"directors": [], "cast": [], "companies": []})


class TestFetchOne(unittest.TestCase):
    def test_fetch_one_returns_slim_payload(self):
        client = FakeClient({1: detail(directors=[(9, "Dir")])})
        payload = tmdb_credits.fetch_one(client, 1)
        self.assertEqual(payload["directors"], [{"id": 9, "name": "Dir"}])
        self.assertEqual(client.calls, [("/movie/1", {"append_to_response": "credits"})])

    def test_fetch_one_tombstones_404(self):
        err = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        client = FakeClient({2: err})
        payload = tmdb_credits.fetch_one(client, 2)
        self.assertTrue(payload.get(tmdb_credits.TOMBSTONE_KEY))
        self.assertEqual(payload["code"], 404)

    def test_fetch_one_reraises_other_http_errors(self):
        err = urllib.error.HTTPError("url", 500, "Server Error", {}, None)
        client = FakeClient({3: err})
        with self.assertRaises(urllib.error.HTTPError):
            tmdb_credits.fetch_one(client, 3)


class TestCacheAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.cache_dir = self.tmp_path / "cache"
        self.sample_path = self.tmp_path / "sample.json"
        self.sample_path.write_text(json.dumps([
            {"id": "tmdb-1", "tmdb_id": 1, "title": "One"},
            {"id": "tmdb-2", "tmdb_id": 2, "title": "Two", "source": "wishlist"},
            {"id": "tmdb-2b", "tmdb_id": 2, "title": "Two dup"},   # duplicate tmdb_id
        ]))

    def test_load_sample_ids_dedupes_and_includes_wishlist_rows(self):
        ids = tmdb_credits.load_sample_ids(self.sample_path)
        self.assertEqual(ids, [1, 2])   # de-duplicated, wishlist row (tmdb_id 2) included

    def test_write_and_read_cache_roundtrip(self):
        tmdb_credits.write_cache(1, {"directors": [], "cast": [], "companies": []},
                                  self.cache_dir)
        self.assertTrue(tmdb_credits.is_cached(1, self.cache_dir))
        self.assertFalse(tmdb_credits.is_cached(2, self.cache_dir))
        got = json.loads((self.cache_dir / "1.json").read_text())
        self.assertEqual(got["directors"], [])

    def test_resumable_skips_already_cached(self):
        tmdb_credits.write_cache(1, {"directors": [], "cast": [], "companies": []},
                                  self.cache_dir)
        ids = tmdb_credits.load_sample_ids(self.sample_path)
        remaining = [i for i in ids if not tmdb_credits.is_cached(i, self.cache_dir)]
        self.assertEqual(remaining, [2])

    def test_dry_run_does_not_touch_network_or_cache(self):
        import subprocess
        r = subprocess.run(
            [sys.executable, str(ROOT / "src" / "tmdb_credits.py"),
             "--sample", str(self.sample_path), "--cache-dir", str(self.cache_dir),
             "--dry-run"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("remaining", r.stdout)
        self.assertFalse(self.cache_dir.exists())   # dry-run makes no calls, writes nothing


# ---------------------------------------------------------------- studio atlas / build_credits

ATLAS_FIXTURE = {
    "studios": [
        {"key": "warner", "label": "Warner Bros.", "ids": [174],
         "name_patterns": ["^Warner Bros", "^New Line"]},
        {"key": "a24", "label": "A24", "ids": [41077], "name_patterns": ["^A24$"]},
    ]
}


class TestAtlasMapping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.atlas_path = Path(self.tmp.name) / "atlas.json"
        self.atlas_path.write_text(json.dumps(ATLAS_FIXTURE))
        self.atlas = build_credits.load_atlas(self.atlas_path)

    def test_matches_by_id(self):
        key, label = build_credits.map_company(174, "Warner Bros. Pictures", self.atlas)
        self.assertEqual(key, "s:warner")
        self.assertEqual(label, "Warner Bros.")

    def test_matches_by_name_pattern_when_id_unknown(self):
        # New Line has no id in this fixture atlas, only a name pattern
        key, label = build_credits.map_company(12, "New Line Cinema", self.atlas)
        self.assertEqual(key, "s:warner")

    def test_alias_collapses_to_one_studio(self):
        # two different raw TMDB companies, both Warner, must map to the same key
        k1, _ = build_credits.map_company(174, "Warner Bros. Pictures", self.atlas)
        k2, _ = build_credits.map_company(999, "Warner Bros. Entertainment", self.atlas)
        self.assertEqual(k1, k2)

    def test_unmapped_company_keeps_raw_id_and_name(self):
        key, label = build_credits.map_company(555, "Little Indie Co", self.atlas)
        self.assertEqual(key, "c:555")
        self.assertEqual(label, "Little Indie Co")

    def test_anchored_pattern_does_not_match_substring(self):
        # "A24" pattern is exact-match; "Not A24 Productions" must not collide with it
        key, _ = build_credits.map_company(9999, "Not A24 Productions", self.atlas)
        self.assertEqual(key, "c:9999")


class TestBuildCredits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.cache_dir = self.tmp_path / "cache"
        self.cache_dir.mkdir()
        self.sample_path = self.tmp_path / "sample.json"
        self.atlas_path = self.tmp_path / "atlas.json"
        self.atlas_path.write_text(json.dumps(ATLAS_FIXTURE))

        self.sample_path.write_text(json.dumps([
            {"id": "tmdb-1", "tmdb_id": 1, "title": "Has credits, two Warner companies"},
            {"id": "tmdb-2", "tmdb_id": 2, "title": "No cache yet"},
            {"id": "tmdb-3", "tmdb_id": 3, "title": "Tombstoned (404)"},
        ]))
        (self.cache_dir / "1.json").write_text(json.dumps({
            "directors": [{"id": 10, "name": "Dir One"}],
            "cast": [{"id": 20, "name": "Actor A", "order": 0},
                     {"id": 21, "name": "Actor B", "order": 1}],
            "companies": [{"id": 174, "name": "Warner Bros. Pictures"},
                          {"id": 998, "name": "Warner Bros. Entertainment"},
                          {"id": 501, "name": "Some Indie Co"}],
        }))
        (self.cache_dir / "3.json").write_text(json.dumps({"_missing": True, "code": 404}))

    def test_shape_and_coverage(self):
        payload, stats = build_credits.build(self.sample_path, self.cache_dir, self.atlas_path)
        self.assertEqual(payload["v"], 1)
        self.assertIn("built", payload)
        self.assertEqual(set(payload["films"]), {"tmdb-1"})   # only the cached, non-tombstoned one

        self.assertEqual(stats["n_with_credits"], 1)
        self.assertEqual(stats["n_missing_cache"], 1)
        self.assertEqual(stats["n_tombstoned"], 1)

    def test_two_warner_aliases_collapse_to_one_company_on_the_film(self):
        payload, _ = build_credits.build(self.sample_path, self.cache_dir, self.atlas_path)
        f = payload["films"]["tmdb-1"]
        # two raw companies both map to "s:warner" plus one unmapped "c:501" -> 2 distinct entries
        self.assertEqual(len(f["co"]), 2)
        co_keys = {payload["companies"][i][0] for i in f["co"]}
        self.assertEqual(co_keys, {"s:warner", "c:501"})

    def test_actor_order_preserved_billing_order(self):
        payload, _ = build_credits.build(self.sample_path, self.cache_dir, self.atlas_path)
        f = payload["films"]["tmdb-1"]
        names = [payload["people"][i][1] for i in f["a"]]
        self.assertEqual(names, ["Actor A", "Actor B"])

    def test_report_counts_threshold_and_unmapped(self):
        _, stats = build_credits.build(self.sample_path, self.cache_dir, self.atlas_path)
        # nobody clears the 25-film threshold in this tiny fixture
        self.assertEqual(stats["dir_over"], [])
        self.assertEqual(stats["co_over"], [])
        unmapped_names = {name for (key, name), n in stats["unmapped_top"]}
        self.assertIn("Some Indie Co", unmapped_names)

    def test_output_is_deterministic(self):
        p1, _ = build_credits.build(self.sample_path, self.cache_dir, self.atlas_path)
        p2, _ = build_credits.build(self.sample_path, self.cache_dir, self.atlas_path)
        p1["built"] = p2["built"] = "x"   # the only field allowed to vary
        self.assertEqual(json.dumps(p1, sort_keys=True), json.dumps(p2, sort_keys=True))

    def test_write_json_is_compact_and_sorted(self):
        out_path = self.tmp_path / "credits.json"
        import subprocess
        r = subprocess.run(
            [sys.executable, str(ROOT / "src" / "build_credits.py"),
             "--sample", str(self.sample_path), "--cache-dir", str(self.cache_dir),
             "--atlas", str(self.atlas_path), "--out", str(out_path)],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = out_path.read_text()
        self.assertNotIn("  ", text)     # compact separators, no pretty-printing
        data = json.loads(text)
        self.assertEqual(list(data.keys()), sorted(data.keys()))   # top-level keys sorted


class TestRealStudioAtlas(unittest.TestCase):
    """The actual data/studio_atlas.json this repo ships, not a fixture."""

    def test_loads_and_patterns_compile(self):
        atlas = build_credits.load_atlas(ROOT / "data" / "studio_atlas.json")
        self.assertGreater(len(atlas), 10)
        keys = [s["key"] for s in atlas]
        self.assertEqual(len(keys), len(set(keys)))   # no duplicate keys

    def test_known_majors_map_by_id(self):
        atlas = build_credits.load_atlas(ROOT / "data" / "studio_atlas.json")
        cases = [
            (174, "warner"), (5, "sony"), (33, "universal"), (4, "paramount"),
            (25, "fox"), (2, "disney"), (3, "disney"), (21, "mgm"),
            (41077, "a24"), (14, "miramax"), (7, "dreamworks"), (10342, "ghibli"),
        ]
        for tmdb_id, expected_key in cases:
            key, _ = build_credits.map_company(tmdb_id, "irrelevant name", atlas)
            self.assertEqual(key, f"s:{expected_key}", f"id {tmdb_id}")

    def test_marvel_and_lucasfilm_kept_separate_from_disney(self):
        atlas = build_credits.load_atlas(ROOT / "data" / "studio_atlas.json")
        k1, _ = build_credits.map_company(None, "Marvel Studios", atlas)
        k2, _ = build_credits.map_company(None, "Lucasfilm Ltd.", atlas)
        self.assertEqual(k1, "s:marvel")
        self.assertEqual(k2, "s:lucasfilm")


if __name__ == "__main__":
    unittest.main()
