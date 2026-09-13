#!/usr/bin/env python3
"""Score one labelset against another.

    python3 src/validate.py data/labels.jsonl data/labels_api.jsonl

The first file is the reference, the second is what you are scoring. Both use the
schema ingest.py / classify_api.py write. Only ids present in both are compared.

This measures AGREEMENT, not truth. It is meaningful when the two labelsets came from
different processes (hand labels vs. API run, two models, two prompts). Scoring a
labelset against itself, or against one derived from it, tells you nothing.
"""
import argparse, json, statistics, sys
from collections import Counter, defaultdict
from pathlib import Path

GAZES = ["past", "present", "future", "multi", "atemporal"]


def load(path):
    rows = {}
    for line in Path(path).read_text().split("\n"):
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r                      # later lines win
    return rows


def primary_year(r):
    """Midpoint of the highest-prominence target; None for atemporal."""
    order = {"primary": 0, "secondary": 1, "minor": 2}
    ts = sorted(r["targets"], key=lambda t: order.get(t["prominence"], 3))
    if not ts:
        return None
    return (ts[0]["year_start"] + ts[0]["year_end"]) // 2


def pct(n, d):
    return f"{100 * n / d:5.1f}%" if d else "    —"


def bar(label, n, d, width=28):
    filled = round(width * n / d) if d else 0
    return f"  {label:<26} {'█' * filled}{'·' * (width - filled)} {pct(n, d)}  {n}/{d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reference")
    ap.add_argument("candidate")
    ap.add_argument("--tol", type=int, default=2, help="years of slack for an exact-year match")
    ap.add_argument("--by-basis", action="store_true",
                    help="split every metric by the reference's basis field")
    ap.add_argument("--json", metavar="PATH", nargs="?", const="data/validation.json",
                    help="also write the headline numbers as JSON, so the page can print "
                         "the measured accuracy instead of a hardcoded one that rots")
    a = ap.parse_args()

    ref, cand = load(a.reference), load(a.candidate)
    shared = sorted(set(ref) & set(cand))
    if not shared:
        sys.exit("no overlapping ids — nothing to compare")

    print(f"reference  {Path(a.reference).name:<24} {len(ref):>5} labels")
    print(f"candidate  {Path(a.candidate).name:<24} {len(cand):>5} labels")
    print(f"overlap    {len(shared)} films"
          f"   ({len(ref) - len(shared)} in reference only,"
          f" {len(cand) - len(shared)} in candidate only)\n")

    gaze_hit = sum(ref[i]["gaze"] == cand[i]["gaze"] for i in shared)
    conf = Counter((ref[i]["gaze"], cand[i]["gaze"]) for i in shared)

    errs, within, exact_n = [], 0, 0
    multi_ref = multi_hit = 0
    atem_ref = atem_hit = 0
    by_basis = defaultdict(lambda: {"n": 0, "gaze": 0, "yr": []})

    for i in shared:
        R, C = ref[i], cand[i]
        b = R.get("basis", "?")
        by_basis[b]["n"] += 1
        by_basis[b]["gaze"] += R["gaze"] == C["gaze"]

        if len(R["targets"]) > 1:
            multi_ref += 1
            multi_hit += len(C["targets"]) > 1
        if R["gaze"] == "atemporal":
            atem_ref += 1
            atem_hit += C["gaze"] == "atemporal"

        ry, cy = primary_year(R), primary_year(C)
        if ry is not None and cy is not None:
            e = abs(ry - cy)
            errs.append(e); exact_n += 1
            within += e <= a.tol
            by_basis[b]["yr"].append(e)

    print(bar("gaze agreement", gaze_hit, len(shared)))
    if multi_ref:
        print(bar("multi-timeline recall", multi_hit, multi_ref))
    if atem_ref:
        print(bar("atemporal agreement", atem_hit, atem_ref))
    if errs:
        print(bar(f"primary year within {a.tol}y", within, exact_n))
        print(f"\n  primary-year error   median {statistics.median(errs):>6.0f}y"
              f"   mean {statistics.mean(errs):>8.0f}y"
              f"   p90 {sorted(errs)[int(.9 * len(errs)) - 1]:>6}y   max {max(errs):,}y")
        print("  (mean is wrecked by far-future and deep-past outliers — read the median)")

    print("\nGAZE CONFUSION   rows = reference, cols = candidate")
    w = max(len(g) for g in GAZES) + 1
    print(" " * (w + 2) + "".join(f"{g[:5]:>7}" for g in GAZES) + "     n")
    for r in GAZES:
        n = sum(conf[(r, c)] for c in GAZES)
        if not n:
            continue
        cells = "".join(f"{conf[(r, c)] or '·':>7}" for c in GAZES)
        print(f"  {r:<{w}}{cells}  {n:>5}")

    if a.by_basis and len(by_basis) > 1:
        print("\nBY REFERENCE BASIS   does agreement depend on where the label came from?")
        print(f"  {'basis':<12}{'n':>6}{'gaze':>9}{'median yr err':>16}")
        for b, d in sorted(by_basis.items(), key=lambda kv: -kv[1]["n"]):
            med = f"{statistics.median(d['yr']):.0f}y" if d["yr"] else "—"
            print(f"  {b:<12}{d['n']:>6}{pct(d['gaze'], d['n']):>9}{med:>16}")

    disagree = [i for i in shared if ref[i]["gaze"] != cand[i]["gaze"]]
    if disagree:
        print(f"\nFIRST DISAGREEMENTS  ({len(disagree)} total)")
        for i in disagree[:12]:
            ry, cy = primary_year(ref[i]), primary_year(cand[i])
            print(f"  {i[:44]:<44} {ref[i]['gaze']:>9} {str(ry):>7}"
                  f"   vs {cand[i]['gaze']:>9} {str(cy):>7}")

    # ---- machine-readable summary ------------------------------------------
    # The page should print the accuracy it was actually measured at. Hardcoding
    # it in the template is how the corpus caveat ended up describing the wrong
    # dataset for a fortnight, so this is generated the same way the rest is.
    if a.json:
        out = {
            "reference": Path(a.reference).name,
            "candidate": Path(a.candidate).name,
            "n_reference": len(ref), "n_candidate": len(cand), "n_overlap": len(shared),
            "tol": a.tol,
            "gaze_agreement": round(gaze_hit / len(shared), 4),
            "year_within_tol": round(within / exact_n, 4) if exact_n else None,
            "year_err_median": statistics.median(errs) if errs else None,
            "multi_recall": round(multi_hit / multi_ref, 4) if multi_ref else None,
            "atemporal_agreement": round(atem_hit / atem_ref, 4) if atem_ref else None,
            "by_basis": {b: {"n": d["n"], "gaze": round(d["gaze"] / d["n"], 4),
                             "year_err_median": statistics.median(d["yr"]) if d["yr"] else None}
                         for b, d in by_basis.items() if d["n"] >= 10},
        }
        Path(a.json).write_text(json.dumps(out, indent=2))
        print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
