#!/usr/bin/env python3
"""Merge the labelsets (pass1, pass2, hand) into one, by the precedence rule recorded in
data/labelsets.json — never in place, never editing any input.

    python3 src/merge_labelsets.py                 # writes data/labels_merged.jsonl
    python3 src/merge_labelsets.py --report         # + pass1-vs-pass2 disagreement digest

Precedence (see data/labelsets.json, "precedence"):
  hand always wins when present.
  Between pass1 and pass2 on the same id: pass2 wins only if it read real text
  (pass2.basis in {"text","both"}) or is more confident than pass1
  (pass2.confidence > pass1.confidence); otherwise pass1 is kept.

Every emitted record gets a "from" field naming the labelset it came from, and (for a
pass2 win) the pass2 record's own "labelset"/"model"/"prompt" fields if classify_api.py
stamped them.
"""
import argparse, json, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def resolve(name, default_dir=ROOT / "data"):
    p = Path(name)
    return p if p.is_absolute() or "/" in name else default_dir / name


def load_jsonl(path):
    rows = {}
    p = resolve(path)
    if not p.exists():
        return rows
    for line in p.read_text().split("\n"):
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r
    return rows


def primary_year(r):
    order = {"primary": 0, "secondary": 1, "minor": 2}
    ts = sorted(r.get("targets") or [], key=lambda t: order.get(t["prominence"], 3))
    return (ts[0]["year_start"] + ts[0]["year_end"]) // 2 if ts else None


def pass2_wins(base, cand):
    """cand is pass2's record for an id pass1 (base) also has. True if cand should replace
    base — see the module docstring / data/labelsets.json for the rule."""
    if cand.get("basis") in ("text", "both"):
        return True
    return cand.get("confidence", 0) > base.get("confidence", 0)


def merge(hand, pass1, pass2):
    merged, from_ = {}, {}
    ids = set(hand) | set(pass1) | set(pass2)
    for i in ids:
        if i in hand:
            merged[i], from_[i] = hand[i], "hand"
        elif i in pass1 and i in pass2:
            if pass2_wins(pass1[i], pass2[i]):
                merged[i], from_[i] = pass2[i], "pass2"
            else:
                merged[i], from_[i] = pass1[i], "pass1"
        elif i in pass2:
            merged[i], from_[i] = pass2[i], "pass2"
        else:
            merged[i], from_[i] = pass1[i], "pass1"
    return merged, from_


def report_disagreements(pass1, pass2, tol):
    shared = sorted(set(pass1) & set(pass2))
    if not shared:
        print("\nno ids overlap between pass1 and pass2 — nothing to compare"); return
    gaze_diff = [i for i in shared if pass1[i]["gaze"] != pass2[i]["gaze"]]
    year_shifts = []
    for i in shared:
        y1, y2 = primary_year(pass1[i]), primary_year(pass2[i])
        if y1 is not None and y2 is not None and abs(y1 - y2) > tol:
            year_shifts.append((i, y1, y2, abs(y1 - y2)))

    print(f"\nPASS1 vs PASS2 DISAGREEMENTS   ({len(shared)} films re-run)")
    print(f"  gaze changed        {len(gaze_diff)} / {len(shared)}"
          f"  ({100 * len(gaze_diff) / len(shared):.1f}%)")
    print(f"  year shifted >{tol}y  {len(year_shifts)} / {len(shared)}"
          f"  ({100 * len(year_shifts) / len(shared):.1f}%)")
    wins = sum(pass2_wins(pass1[i], pass2[i]) for i in shared)
    print(f"  pass2 wins precedence on {wins} / {len(shared)} of the overlap")

    if gaze_diff:
        print(f"\n  gaze changes (first 12 of {len(gaze_diff)}):")
        for i in gaze_diff[:12]:
            print(f"    {i[:40]:<40} {pass1[i]['gaze']:>9} -> {pass2[i]['gaze']:<9}"
                  f"  (pass2 basis={pass2[i].get('basis')} conf={pass2[i].get('confidence')})")
    if year_shifts:
        year_shifts.sort(key=lambda t: -t[3])
        print(f"\n  biggest year shifts (top 12 of {len(year_shifts)}):")
        for i, y1, y2, d in year_shifts[:12]:
            print(f"    {i[:40]:<40} {y1!s:>7} -> {y2!s:<7}  (Δ{d}y)")
        deltas = [d for *_, d in year_shifts]
        print(f"\n  median shift {statistics.median(deltas):.0f}y  "
              f"max {max(deltas)}y")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labelsets", default=str(ROOT / "data" / "labelsets.json"))
    ap.add_argument("--hand", default=None, help="override the hand/reference file")
    ap.add_argument("--pass1", default=None, help="override the pass1 file")
    ap.add_argument("--pass2", default=None, help="override the pass2 file")
    ap.add_argument("--out", default=str(ROOT / "data" / "labels_merged.jsonl"))
    ap.add_argument("--tol", type=int, default=2, help="years of slack before a year 'shifted'")
    ap.add_argument("--no-hand", action="store_true",
                    help="model labels only (pass2 over pass1). This is the merge to VALIDATE: "
                         "scoring a merge that already contains the hand set against the hand "
                         "set is circular and reads as ~100%% on the overlap.")
    ap.add_argument("--report", action="store_true",
                    help="print the pass1-vs-pass2 disagreement digest")
    a = ap.parse_args()

    cfg = json.loads(Path(a.labelsets).read_text())
    files = {s["name"]: s["file"] for s in cfg["sets"]}
    hand_f = a.hand or files.get("hand")
    pass1_f = a.pass1 or files.get("pass1")
    pass2_f = a.pass2 or files.get("pass2")

    hand = {} if a.no_hand else load_jsonl(hand_f)
    pass1 = load_jsonl(pass1_f)
    pass2 = load_jsonl(pass2_f)
    print(f"hand   {hand_f:<24} {len(hand):>5} labels")
    print(f"pass1  {pass1_f:<24} {len(pass1):>5} labels")
    print(f"pass2  {pass2_f:<24} {len(pass2):>5} labels"
          f"{'  (not found yet)' if not pass2 else ''}")

    merged, from_ = merge(hand, pass1, pass2)
    out_path = resolve(a.out)
    lines = []
    for i in sorted(merged):
        rec = dict(merged[i])
        rec["from"] = from_[i]
        lines.append(json.dumps(rec, ensure_ascii=False))
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""))

    tally = {}
    for f in from_.values():
        tally[f] = tally.get(f, 0) + 1
    print(f"\nwrote {len(merged)} labels to {out_path}")
    for f in ("hand", "pass2", "pass1"):
        if tally.get(f):
            print(f"  {tally[f]:>5} from {f}")

    if a.report:
        report_disagreements(pass1, pass2, a.tol)


if __name__ == "__main__":
    main()
