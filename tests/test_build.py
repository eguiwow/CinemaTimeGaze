import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def run(args, cwd=None, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                           timeout=60, cwd=cwd, env=full_env)


class TestBuildTargetsSourcePassthrough(unittest.TestCase):
    """Contract C3: sample.json rows may carry source="wishlist"; build_targets.py carries
    it onto targets.json films verbatim, and otherwise leaves output unchanged."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        (self.tmp_path / "data").mkdir()

    def _sample(self, rows):
        (self.tmp_path / "data" / "sample.json").write_text(json.dumps(rows))

    def _labels(self, rows):
        (self.tmp_path / "data" / "labels.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")

    def _run_build_targets(self):
        # build_targets.py resolves data/ relative to its own ROOT (the real repo), but
        # --labels/--out accept bare paths under data/ or full paths — use full paths
        # pointing at this test's temp data dir, and monkeypatch its notion of "sample.json"
        # by running from a copy of the script against a symlinked data dir instead.
        data_link = self.tmp_path / "data"
        script = ROOT / "src" / "build_targets.py"
        # build_targets.py always reads ROOT/data/sample.json (ROOT = its own parent.parent),
        # which is NOT overridable by flags — so point --labels/--out at temp paths but run
        # against a private copy of the repo tree via a temp ROOT with a symlinked src/.
        priv_root = self.tmp_path / "root"
        (priv_root / "src").mkdir(parents=True)
        (priv_root / "data").mkdir(parents=True)
        for name in ("build_targets.py",):
            (priv_root / "src" / name).write_text((ROOT / "src" / name).read_text())
        (priv_root / "data" / "sample.json").write_text(
            (self.tmp_path / "data" / "sample.json").read_text())
        (priv_root / "data" / "labels.jsonl").write_text(
            (self.tmp_path / "data" / "labels.jsonl").read_text())
        r = run([str(priv_root / "src" / "build_targets.py")], cwd=str(priv_root))
        out = json.loads((priv_root / "data" / "targets.json").read_text())
        return r, out

    def test_source_carried_onto_targets_film(self):
        self._sample([
            {"id": "tmdb-1", "title": "Wishlisted", "year": 2020, "genres": [],
             "source": "wishlist"},
            {"id": "tmdb-2", "title": "Sampled", "year": 2020, "genres": []},
        ])
        self._labels([
            {"id": "tmdb-1", "gaze": "present", "earthbound": True, "confidence": 0.9,
             "basis": "text", "targets": [{"year_start": 2020, "year_end": 2020,
                                            "prominence": "primary"}]},
            {"id": "tmdb-2", "gaze": "present", "earthbound": True, "confidence": 0.9,
             "basis": "text", "targets": [{"year_start": 2020, "year_end": 2020,
                                            "prominence": "primary"}]},
        ])
        r, out = self._run_build_targets()
        self.assertEqual(r.returncode, 0, r.stderr)
        films = {f["id"]: f for f in out["films"]}
        self.assertEqual(films["tmdb-1"]["source"], "wishlist")
        self.assertNotIn("source", films["tmdb-2"])   # not written as null, just absent

    def test_output_unchanged_shape_when_no_source_present(self):
        self._sample([{"id": "tmdb-1", "title": "Plain", "year": 2020, "genres": []}])
        self._labels([{"id": "tmdb-1", "gaze": "present", "earthbound": True,
                        "confidence": 0.9, "basis": "text",
                        "targets": [{"year_start": 2020, "year_end": 2020,
                                     "prominence": "primary"}]}])
        r, out = self._run_build_targets()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("source", out["films"][0])


class TestBuildTargetsRealCommittedOutput(unittest.TestCase):
    """Guards the exact invocation the repo relies on: labels_api.jsonl -> targets.json,
    byte-identical (as parsed JSON) to what is committed, after the source-passthrough
    change — since today's real sample.json has no `source` rows."""

    def test_real_run_matches_committed_targets_json(self):
        committed = ROOT / "data" / "targets.json"
        if not committed.exists():
            self.skipTest("data/targets.json not present in this checkout")
        if not (ROOT / "data" / "sample.json").exists():
            self.skipTest("data/sample.json is gitignored (TMDB content); not in a clean clone")
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "targets.json"
            r = run([str(ROOT / "src" / "build_targets.py"),
                     "--labels", "labels_api.jsonl", "--out", str(out_path)])
            self.assertEqual(r.returncode, 0, r.stderr)
            before = json.loads(committed.read_text())
            after = json.loads(out_path.read_text())
            self.assertEqual(before, after)


class TestBuildViz(unittest.TestCase):
    """build_viz.py end to end, against a small fixture data/ dir and throwaway out/docs
    dirs (via env var overrides) — never the real repo's data/credits.json or docs/."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.data_dir = self.tmp_path / "data"
        self.out_dir = self.tmp_path / "out"
        self.docs_dir = self.tmp_path / "docs"
        self.data_dir.mkdir()

        real = json.loads((ROOT / "data" / "targets.json").read_text())
        films = [dict(f) for f in real["films"][:5]]
        films[0]["source"] = "wishlist"
        ids = {f["id"] for f in films}
        targets = [t for t in real["targets"] if t["film_id"] in ids]
        (self.data_dir / "targets.json").write_text(json.dumps({"films": films,
                                                                  "targets": targets}))
        self.film_ids = [f["id"] for f in films]

    def _env(self):
        return {"CTG_DATA_DIR": str(self.data_dir), "CTG_OUT_DIR": str(self.out_dir),
                "CTG_DOCS_DIR": str(self.docs_dir)}

    def _run(self):
        return run([str(ROOT / "src" / "build_viz.py")], env=self._env())

    def test_runs_clean_with_no_credits_json(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.docs_dir / "index.html").exists())
        self.assertFalse((self.docs_dir / "credits.json").exists())
        self.assertFalse((self.data_dir / "credits.json").exists())

    def test_credits_src_null_when_no_credits_file(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        html = (self.docs_dir / "index.html").read_text()
        self.assertNotIn("__CREDITS_SRC__", html)   # placeholder always resolved, never leaked
        has_slot = "__CREDITS_SRC__" in (ROOT / "src" / "viz_template.html").read_text()
        if has_slot:
            self.assertIn("const CREDITS_SRC = null;", html)
        # if the template doesn't have the slot yet (Agent B not landed), the build must
        # still succeed cleanly — asserted above via returncode — and just skip the feature

    def test_wishlist_flag_emitted_and_note_present(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        html = (self.docs_dir / "index.html").read_text()
        self.assertIn('"src":"w"', html)
        self.assertIn("added from the visitor wishlist", html)

    def test_no_wishlist_note_text_when_no_wishlist_films(self):
        films = json.loads((self.data_dir / "targets.json").read_text())
        films["films"][0].pop("source", None)
        (self.data_dir / "targets.json").write_text(json.dumps(films))
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        html = (self.docs_dir / "index.html").read_text()
        self.assertNotIn('"src":"w"', html)
        self.assertNotIn("added from the visitor wishlist", html)

    def test_credits_inline_for_standalone_and_linked_for_docs(self):
        credits = {
            "v": 1,
            "people": [[1, "Alice Director"], [2, "Bob Actor"]],
            "companies": [["s:a24", "A24"]],
            "films": {self.film_ids[1]: {"d": [0], "a": [1], "co": [0]}},
        }
        (self.data_dir / "credits.json").write_text(json.dumps(credits))
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)

        docs_html = (self.docs_dir / "index.html").read_text()
        self.assertIn('const CREDITS_SRC = "credits.json";', docs_html)
        self.assertTrue((self.docs_dir / "credits.json").exists())
        written = json.loads((self.docs_dir / "credits.json").read_text())
        self.assertEqual(len(written["f"]), len(self.film_ids))   # index-aligned to DATA.films
        # exactly one non-zero entry, at the film we attached credits to
        non_zero = [i for i, v in enumerate(written["f"]) if v != 0]
        self.assertEqual(non_zero, [1])

        standalone = (self.out_dir / "standalone.html").read_text()
        self.assertIn('const CREDITS_SRC = {"v":1', standalone)
        self.assertNotIn('const CREDITS_SRC = "credits.json"', standalone)

        # main DATA payload must not grow because credits exist (separate placeholder)
        m = self._main_payload_len(docs_html)
        self.assertIsNotNone(m)

    def _main_payload_len(self, html):
        import re
        m = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
        return len(m.group(1)) if m else None

    def test_main_payload_size_unaffected_by_credits_presence(self):
        r1 = self._run()
        self.assertEqual(r1.returncode, 0, r1.stderr)
        html1 = (self.docs_dir / "index.html").read_text()
        len1 = self._main_payload_len(html1)

        credits = {"v": 1, "people": [[1, "A"]], "companies": [["s:a24", "A24"]],
                   "films": {self.film_ids[0]: {"d": [0], "a": [], "co": [0]}}}
        (self.data_dir / "credits.json").write_text(json.dumps(credits))
        r2 = self._run()
        self.assertEqual(r2.returncode, 0, r2.stderr)
        html2 = (self.docs_dir / "index.html").read_text()
        len2 = self._main_payload_len(html2)
        self.assertEqual(len1, len2)

    def test_invalid_credits_json_does_not_crash_build(self):
        (self.data_dir / "credits.json").write_text("{not valid json")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        html = (self.docs_dir / "index.html").read_text()
        self.assertIn("const CREDITS_SRC = null;", html)


if __name__ == "__main__":
    unittest.main()
