#!/usr/bin/env python3
"""Unit tests for the label-quality tooling (v4 item 8). Stdlib only, no network.

    python3 -m unittest discover -s tests
"""
import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import classify_api
import fetch_plots
import handlabel_queue
import merge_labelsets
import validate
import weak_rows


# =====================================================================
# 1. prompt v1 must never change
# =====================================================================

class TestPromptV1Unchanged(unittest.TestCase):
    # sha256 of classify_api.SYSTEM_V1 at the moment v2 was introduced. If this test
    # fails, someone edited the v1 prompt text — add the new rule to v2 instead.
    V1_SHA256 = "dd7ee84ee2297b169f6a03f6bda14cae221724dc13675d78d07023a88889194f"

    def test_hash_pinned(self):
        digest = hashlib.sha256(classify_api.SYSTEM_V1.encode()).hexdigest()
        self.assertEqual(digest, self.V1_SHA256,
                          "SYSTEM_V1 text changed — pass-1 labels were produced with the "
                          "old wording, so this prompt must stay byte-for-byte identical. "
                          "Put boundary fixes in SYSTEM_V2 instead.")

    def test_default_prompt_is_v1(self):
        ap_default = classify_api.PROMPTS["v1"]
        self.assertEqual(ap_default, classify_api.SYSTEM_V1)
        self.assertEqual(classify_api.SYSTEM, classify_api.SYSTEM_V1)

    def test_v2_is_a_superset_of_v1_rules(self):
        # v2 must not delete or reword anything from v1 — only append boundary rules.
        v1_lines = [l for l in classify_api.SYSTEM_V1.split("\n") if l.strip()]
        v2_text = classify_api.SYSTEM_V2
        for line in v1_lines:
            self.assertIn(line, v2_text, f"v1 line dropped or reworded in v2: {line!r}")
        self.assertNotEqual(classify_api.SYSTEM_V1, classify_api.SYSTEM_V2)


# =====================================================================
# 2. classify_api: --ids filtering, wishlist survival, plots override
# =====================================================================

class TestPick(unittest.TestCase):
    def setUp(self):
        self.sample = [
            {"id": "a", "year": 2000, "notability": 100},
            {"id": "b", "year": 2000, "notability": 90},
            {"id": "c", "year": 2000, "notability": 80},
            {"id": "w1", "year": 2000, "notability": 1, "source": "wishlist"},
            {"id": "w2", "year": 1950, "notability": 1, "source": "wishlist"},
        ]

    def test_ids_bypasses_per_year_cap(self):
        # per_year=1 would normally only ever admit the top-notability film per year;
        # --ids must still return every requested id regardless of rank.
        out = classify_api.pick(self.sample, per_year=1, done=set(), limit=0,
                                 ids=["c", "w2", "a"])
        self.assertEqual([r["id"] for r in out], ["c", "w2", "a"])   # id-list order preserved

    def test_ids_skips_done_and_unknown(self):
        out = classify_api.pick(self.sample, per_year=1, done={"c"}, limit=0,
                                 ids=["c", "b", "nonexistent"])
        self.assertEqual([r["id"] for r in out], ["b"])

    def test_wishlist_survives_the_per_year_cap(self):
        # per_year=1 alone would only pick "a" (highest notability at year 2000) and
        # nothing at 1950. Both wishlist rows must still appear.
        out = classify_api.pick(self.sample, per_year=1, done=set(), limit=0)
        ids = {r["id"] for r in out}
        self.assertIn("w1", ids)
        self.assertIn("w2", ids)
        self.assertIn("a", ids)               # the normal per-year pick still happens
        self.assertNotIn("b", ids)            # still capped for non-wishlist films
        self.assertNotIn("c", ids)

    def test_wishlist_done_is_not_reoffered(self):
        out = classify_api.pick(self.sample, per_year=1, done={"w1"}, limit=0)
        self.assertNotIn("w1", {r["id"] for r in out})

    def test_no_duplicate_if_a_row_is_somehow_wishlist_and_top_ranked(self):
        sample = [{"id": "x", "year": 1999, "notability": 5, "source": "wishlist"}]
        out = classify_api.pick(sample, per_year=3, done=set(), limit=0)
        self.assertEqual([r["id"] for r in out], ["x"])   # not doubled


class TestPlotsOverride(unittest.TestCase):
    def test_load_plots(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "plots.jsonl"
            p.write_text('{"id": "a", "plot": "long text here", "source": "wikipedia:A"}\n')
            plots = classify_api.load_plots(str(p))
            self.assertEqual(plots["a"]["plot"], "long text here")

    def test_load_plots_missing_file_exits(self):
        with self.assertRaises(SystemExit):
            classify_api.load_plots("/no/such/file.jsonl")

    def test_load_plots_none_returns_empty(self):
        self.assertEqual(classify_api.load_plots(None), {})

    def test_film_line_uses_override_over_extract(self):
        r = {"id": "a", "title": "T", "year": 2000, "genres": ["Drama"],
             "extract": "short tmdb overview"}
        line = classify_api.film_line(r, words=50, plot_override="a much longer wiki plot")
        self.assertIn("a much longer wiki plot", line)
        self.assertNotIn("short tmdb overview", line)

    def test_film_line_falls_back_to_extract(self):
        r = {"id": "a", "title": "T", "year": 2000, "genres": ["Drama"],
             "extract": "short tmdb overview"}
        line = classify_api.film_line(r, words=50, plot_override=None)
        self.assertIn("short tmdb overview", line)

    def test_film_line_respects_word_budget(self):
        r = {"id": "a", "title": "T", "year": 2000, "genres": []}
        text = " ".join(f"w{i}" for i in range(500))
        line = classify_api.film_line(r, words=10, plot_override=text)
        body = line.split("\n", 1)[1].strip()
        self.assertEqual(len(body.split()), 10)


class TestLabelsetStamping(unittest.TestCase):
    def test_parse_reply_then_stamp(self):
        reply = json.dumps([{"id": "a", "gaze": "present", "confidence": "high",
                              "targets": [{"year_start": 2000, "year_end": 2000,
                                           "prominence": "primary", "basis": "text"}]}])
        ok, bad = classify_api.parse_reply(reply, ["a"])
        self.assertEqual(bad, [])
        for r in ok:
            r["labelset"] = "pass2"
            r["model"] = "claude-haiku-4-5"
            r["prompt"] = "v2"
        self.assertEqual(ok[0]["labelset"], "pass2")
        self.assertEqual(ok[0]["prompt"], "v2")


# =====================================================================
# 3. fetch_plots: wikitext stripping (pure function, no network)
# =====================================================================

class TestStripWikitext(unittest.TestCase):
    def test_removes_refs_templates_and_links(self):
        raw = ("'''Bold''' text with a [[Some Page|link]] and a ref"
               "<ref>Cite, p.1</ref> and {{cite web|url=x}} more, plus an "
               "[http://example.com external link].")
        out = fetch_plots.strip_wikitext(raw)
        self.assertNotIn("{{", out)
        self.assertNotIn("<ref", out)
        self.assertNotIn("[[", out)
        self.assertIn("link", out)
        self.assertIn("external link", out)
        self.assertIn("Bold", out)

    def test_strips_headings_and_bullets(self):
        raw = "==Plot==\n* first point\n# second point\nplain line"
        out = fetch_plots.strip_wikitext(raw)
        self.assertNotIn("==", out)
        self.assertIn("first point", out)
        self.assertIn("plain line", out)


# =====================================================================
# 4. merge_labelsets: precedence rule
# =====================================================================

class TestMergePrecedence(unittest.TestCase):
    def rec(self, gaze="present", conf=0.65, basis="knowledge"):
        return {"id": "x", "gaze": gaze, "earthbound": True, "confidence": conf,
                "basis": basis, "targets": [{"year_start": 2000, "year_end": 2000,
                                              "prominence": "primary"}]}

    def test_hand_always_wins(self):
        hand = {"x": self.rec(basis="hand")}
        pass1 = {"x": self.rec(basis="knowledge", conf=0.9)}
        pass2 = {"x": self.rec(basis="text", conf=0.9)}
        merged, frm = merge_labelsets.merge(hand, pass1, pass2)
        self.assertEqual(frm["x"], "hand")

    def test_pass2_wins_on_text_basis(self):
        pass1 = {"x": self.rec(basis="knowledge", conf=0.65)}
        pass2 = {"x": self.rec(basis="text", conf=0.35)}     # lower confidence, but has text
        merged, frm = merge_labelsets.merge({}, pass1, pass2)
        self.assertEqual(frm["x"], "pass2")

    def test_pass2_wins_on_higher_confidence(self):
        pass1 = {"x": self.rec(basis="knowledge", conf=0.35)}
        pass2 = {"x": self.rec(basis="knowledge", conf=0.9)}
        merged, frm = merge_labelsets.merge({}, pass1, pass2)
        self.assertEqual(frm["x"], "pass2")

    def test_pass1_kept_when_pass2_no_better(self):
        pass1 = {"x": self.rec(basis="knowledge", conf=0.9)}
        pass2 = {"x": self.rec(basis="knowledge", conf=0.65)}   # same basis, lower confidence
        merged, frm = merge_labelsets.merge({}, pass1, pass2)
        self.assertEqual(frm["x"], "pass1")

    def test_pass1_only_and_pass2_only(self):
        merged, frm = merge_labelsets.merge({}, {"x": self.rec()}, {})
        self.assertEqual(frm["x"], "pass1")
        merged, frm = merge_labelsets.merge({}, {}, {"y": self.rec()})
        self.assertEqual(frm["y"], "pass2")

    def test_from_field_written_on_output_record(self):
        pass1 = {"x": self.rec()}
        merged, frm = merge_labelsets.merge({}, pass1, {})
        rec = dict(merged["x"]); rec["from"] = frm["x"]
        self.assertEqual(rec["from"], "pass1")
        self.assertEqual(rec["gaze"], "present")

    def test_never_touches_input_dicts(self):
        pass1 = {"x": self.rec(conf=0.5)}
        pass2 = {"x": self.rec(conf=0.9)}
        pass1_copy, pass2_copy = json.loads(json.dumps(pass1)), json.loads(json.dumps(pass2))
        merge_labelsets.merge({}, pass1, pass2)
        self.assertEqual(pass1, pass1_copy)
        self.assertEqual(pass2, pass2_copy)


# =====================================================================
# 5. validate.py: --history append-only, --json unaffected
# =====================================================================

class TestValidateHistory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self.tmpdir.name)
        (self.tmp_root / "data").mkdir()
        self._real_root = validate.ROOT
        validate.ROOT = self.tmp_root

        ref = {"a": {"id": "a", "gaze": "past",
                     "targets": [{"year_start": 1950, "year_end": 1950, "prominence": "primary"}]}}
        cand = {"a": {"id": "a", "gaze": "past",
                      "targets": [{"year_start": 1951, "year_end": 1951, "prominence": "primary"}]}}
        self.ref_path = self.tmp_root / "ref.jsonl"
        self.cand_path = self.tmp_root / "cand.jsonl"
        self.ref_path.write_text(json.dumps(ref["a"]) + "\n")
        self.cand_path.write_text(json.dumps(cand["a"]) + "\n")

    def tearDown(self):
        validate.ROOT = self._real_root
        self.tmpdir.cleanup()

    def _run(self, argv):
        old_argv = sys.argv
        sys.argv = ["validate.py"] + argv
        try:
            validate.main()
        finally:
            sys.argv = old_argv

    def test_history_appends_without_erasing(self):
        hist_path = self.tmp_root / "data" / "validation_history.json"
        self._run([str(self.ref_path), str(self.cand_path), "--history", "run1"])
        h1 = json.loads(hist_path.read_text())
        self.assertEqual(len(h1), 1)
        self.assertEqual(h1[0]["name"], "run1")

        self._run([str(self.ref_path), str(self.cand_path), "--history", "run2"])
        h2 = json.loads(hist_path.read_text())
        self.assertEqual(len(h2), 2)
        self.assertEqual(h2[0], h1[0])              # first entry byte-for-byte untouched
        self.assertEqual(h2[1]["name"], "run2")

    def test_json_output_unaffected_by_history(self):
        json_path = self.tmp_root / "out.json"
        self._run([str(self.ref_path), str(self.cand_path), "--json", str(json_path),
                   "--history", "run1"])
        summary = json.loads(json_path.read_text())
        self.assertEqual(summary["gaze_agreement"], 1.0)

    def test_candidate_required_without_compare(self):
        old_argv = sys.argv
        sys.argv = ["validate.py", str(self.ref_path)]
        try:
            with self.assertRaises(SystemExit):
                validate.main()
        finally:
            sys.argv = old_argv


class TestValidateCompare(unittest.TestCase):
    def test_compare_scores_on_triple_overlap(self):
        ref = {"a": {"gaze": "past", "targets": [{"year_start": 1950, "year_end": 1950,
                                                    "prominence": "primary"}]},
               "b": {"gaze": "present", "targets": [{"year_start": 2000, "year_end": 2000,
                                                       "prominence": "primary"}]}}
        A = {"a": {"gaze": "past", "targets": ref["a"]["targets"]},
             "b": {"gaze": "future", "targets": ref["b"]["targets"]}}   # b wrong
        B = {"a": {"gaze": "past", "targets": ref["a"]["targets"]},
             "b": {"gaze": "present", "targets": ref["b"]["targets"]}}  # b right -> B improves
        shared = ["a", "b"]
        sA = validate.score_against(ref, A, shared, tol=2)
        sB = validate.score_against(ref, B, shared, tol=2)
        self.assertEqual(sA["gaze"], 0.5)
        self.assertEqual(sB["gaze"], 1.0)


# =====================================================================
# 6. weak_rows: selection logic
# =====================================================================

class TestWeakRows(unittest.TestCase):
    def test_confidence_cut_selects_knowledge_only(self):
        with tempfile.TemporaryDirectory() as d:
            labels = [
                {"id": "a", "basis": "knowledge", "confidence": 0.35, "gaze": "past", "targets": []},
                {"id": "b", "basis": "knowledge", "confidence": 0.9, "gaze": "past", "targets": []},
                {"id": "c", "basis": "text", "confidence": 0.35, "gaze": "past", "targets": []},
            ]
            p = Path(d) / "labels.jsonl"
            p.write_text("\n".join(json.dumps(r) for r in labels))
            loaded = weak_rows.load_jsonl(p)
            weak = [r["id"] for r in loaded.values()
                    if r["basis"] == "knowledge" and r["confidence"] <= 0.65]
            self.assertEqual(weak, ["a"])       # b too confident, c not "knowledge"

    def test_boundary_mode_selects_multi_and_atemporal(self):
        with tempfile.TemporaryDirectory() as d:
            labels = {
                "a": {"id": "a", "gaze": "multi"},
                "b": {"id": "b", "gaze": "atemporal"},
                "c": {"id": "c", "gaze": "present"},
            }
            ids = sorted(i for i, r in labels.items() if r["gaze"] in {"multi", "atemporal"})
            self.assertEqual(ids, ["a", "b"])


# =====================================================================
# 7. handlabel_queue: export -> import round trip
# =====================================================================

class TestHandlabelRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)
        self.sample_path = self.tmp / "sample.json"
        self.labelset_path = self.tmp / "labels_api.jsonl"
        self.hand_path = self.tmp / "labels_tmdb.jsonl"
        self.hand2_path = self.tmp / "labels_hand2.jsonl"
        self.csv_path = self.tmp / "queue.csv"

        sample = [
            {"id": "a", "title": "Film A", "year": 2000, "country": "US",
             "extract": "some overview", "notability": 10},
            {"id": "b", "title": "Film B", "year": 1990, "country": "FR",
             "extract": "another overview", "notability": 20},
            {"id": "hand1", "title": "Already hand-labelled", "year": 1980,
             "country": "US", "extract": "x", "notability": 5},
        ]
        self.sample_path.write_text(json.dumps(sample))

        labels = [
            {"id": "a", "gaze": "present", "earthbound": True, "confidence": 0.35,
             "basis": "knowledge", "targets": [{"year_start": 2000, "year_end": 2000,
                                                  "prominence": "primary"}]},
            {"id": "b", "gaze": "past", "earthbound": True, "confidence": 0.65,
             "basis": "knowledge", "targets": [{"year_start": 1990, "year_end": 1990,
                                                  "prominence": "primary"}]},
            {"id": "hand1", "gaze": "past", "earthbound": True, "confidence": 0.35,
             "basis": "knowledge", "targets": [{"year_start": 1980, "year_end": 1980,
                                                  "prominence": "primary"}]},
        ]
        self.labelset_path.write_text("\n".join(json.dumps(r) for r in labels))
        self.hand_path.write_text(json.dumps(
            {"id": "hand1", "gaze": "past", "earthbound": True, "confidence": 0.9,
             "basis": "text", "targets": [{"year_start": 1980, "year_end": 1980,
                                            "prominence": "primary"}]}) + "\n")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _ns(self, **kw):
        class NS: pass
        ns = NS()
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns

    def test_export_excludes_hand_set(self):
        a = self._ns(n=10, labelset=str(self.labelset_path), sample=str(self.sample_path),
                      hand=str(self.hand_path), hand2=str(self.hand2_path),
                      out=str(self.csv_path))
        handlabel_queue.cmd_export(a)
        rows = list(csv.DictReader(self.csv_path.open()))
        ids = {r["id"] for r in rows}
        self.assertNotIn("hand1", ids)
        self.assertIn("a", ids)
        self.assertIn("b", ids)

    def test_export_orders_by_confidence(self):
        a = self._ns(n=10, labelset=str(self.labelset_path), sample=str(self.sample_path),
                      hand=str(self.hand_path), hand2=str(self.hand2_path),
                      out=str(self.csv_path))
        handlabel_queue.cmd_export(a)
        rows = list(csv.DictReader(self.csv_path.open()))
        self.assertEqual(rows[0]["id"], "a")     # confidence 0.35 before b's 0.65

    def test_import_round_trip(self):
        rows = [
            {"id": "a", "title": "Film A", "year": "2000", "country": "US",
             "summary": "x", "model_guess": "present 2000",
             "gaze": "present", "year_start": "2000", "year_end": "2000", "notes": "ok"},
            {"id": "b", "title": "Film B", "year": "1990", "country": "FR",
             "summary": "x", "model_guess": "past 1990",
             "gaze": "", "year_start": "", "year_end": "", "notes": ""},   # left blank
        ]
        with self.csv_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=handlabel_queue.FIELDS)
            w.writeheader(); w.writerows(rows)

        a = self._ns(csv=str(self.csv_path), out=str(self.hand2_path))
        handlabel_queue.cmd_import(a)

        out = [json.loads(l) for l in self.hand2_path.read_text().split("\n") if l.strip()]
        self.assertEqual(len(out), 1)             # only the filled-in row
        self.assertEqual(out[0]["id"], "a")
        self.assertEqual(out[0]["gaze"], "present")
        self.assertEqual(out[0]["targets"][0]["year_start"], 2000)
        self.assertEqual(out[0]["basis"], "text")

    def test_import_rejects_bad_gaze(self):
        rows = [{"id": "a", "title": "", "year": "", "country": "", "summary": "",
                 "model_guess": "", "gaze": "sometime", "year_start": "", "year_end": "",
                 "notes": ""}]
        with self.csv_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=handlabel_queue.FIELDS)
            w.writeheader(); w.writerows(rows)
        a = self._ns(csv=str(self.csv_path), out=str(self.hand2_path))
        handlabel_queue.cmd_import(a)
        self.assertFalse(self.hand2_path.exists() and self.hand2_path.read_text().strip())

    def test_atemporal_needs_no_years(self):
        rows = [{"id": "a", "title": "", "year": "", "country": "", "summary": "",
                 "model_guess": "", "gaze": "atemporal", "year_start": "", "year_end": "",
                 "notes": ""}]
        with self.csv_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=handlabel_queue.FIELDS)
            w.writeheader(); w.writerows(rows)
        a = self._ns(csv=str(self.csv_path), out=str(self.hand2_path))
        handlabel_queue.cmd_import(a)
        out = [json.loads(l) for l in self.hand2_path.read_text().split("\n") if l.strip()]
        self.assertEqual(out[0]["targets"], [])
        self.assertFalse(out[0]["earthbound"])

    def test_second_import_merges_not_overwrites(self):
        # first import: film "a"
        rows1 = [{"id": "a", "title": "", "year": "", "country": "", "summary": "",
                  "model_guess": "", "gaze": "present", "year_start": "2000",
                  "year_end": "2000", "notes": ""}]
        with self.csv_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=handlabel_queue.FIELDS)
            w.writeheader(); w.writerows(rows1)
        handlabel_queue.cmd_import(self._ns(csv=str(self.csv_path), out=str(self.hand2_path)))

        # second import: a different film "b", via a second CSV
        csv2 = self.tmp / "queue2.csv"
        rows2 = [{"id": "b", "title": "", "year": "", "country": "", "summary": "",
                  "model_guess": "", "gaze": "past", "year_start": "1990",
                  "year_end": "1990", "notes": ""}]
        with csv2.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=handlabel_queue.FIELDS)
            w.writeheader(); w.writerows(rows2)
        handlabel_queue.cmd_import(self._ns(csv=str(csv2), out=str(self.hand2_path)))

        out = {json.loads(l)["id"] for l in self.hand2_path.read_text().split("\n") if l.strip()}
        self.assertEqual(out, {"a", "b"})        # both survive — second import did not erase the first


if __name__ == "__main__":
    unittest.main()
