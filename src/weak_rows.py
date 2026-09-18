#!/usr/bin/env python3
"""Find the weak spots in a labelset: rows the classifier only guessed from
knowledge (no plot text backing it up), broken down by confidence, decade
and region — and, separately, the two boundary gazes validate.py scores worst
on (multi-timeline, atemporal).

    python3 src/weak_rows.py                        # default: labels_api.jsonl, cut 0.65
    python3 src/weak_rows.py --cut 0.35              # tighter cut
    python3 src/weak_rows.py --mode boundary         # multi/atemporal candidates

Writes data/weak_ids.txt (one id per line) in the default mode, or
data/boundary_ids.txt in --mode boundary. Both are plain id lists so they can
be fed straight to `classify_api.py --ids`.
"""
import argparse, json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CUTS = (0.35, 0.65)          # the two real confidence levels below "high"


def resolve(name, default_dir=ROOT / "data"):
    p = Path(name)
    return p if p.is_absolute() or "/" in name else default_dir / name


def load_jsonl(path):
    rows = {}
    for line in Path(path).read_text().split("\n"):
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r                      # later lines win
    return rows


def load_sample(path):
    return {r["id"]: r for r in json.loads(Path(path).read_text())}


def decade(year):
    return (year // 10) * 10 if year is not None else None


def print_breakdown(title, ids, sample):
    print(f"\n{title}  (n={len(ids)})")
    if not ids:
        return
    by_decade = Counter()
    by_region = Counter()
    missing = 0
    for i in ids:
        f = sample.get(i)
        if not f:
            missing += 1
            continue
        by_decade[decade(f.get("year"))] += 1
        by_region[f.get("region") or "?"] += 1
    if missing:
        print(f"  ({missing} ids not found in sample.json — skipped from the breakdown)")
    print("  by decade:")
    for d in sorted(k for k in by_decade if k is not None):
        print(f"    {d}s  {by_decade[d]:>5}")
    print("  by region:")
    for r, n in sorted(by_region.items(), key=lambda kv: -kv[1]):
        print(f"    {r:<16} {n:>5}")


def cmd_weak(a, labels, sample):
    print(f"labelset   {Path(a.labelset).name}   {len(labels)} rows")
    knowledge = [r for r in labels.values() if r.get("basis") == "knowledge"]
    print(f"basis=knowledge  {len(knowledge)} of {len(labels)}"
          f"  ({100 * len(knowledge) / len(labels):.1f}%)")

    # print counts at BOTH standard cuts regardless of which one is written out,
    # so a run always shows the shape of the tradeoff
    for cut in CUTS:
        ids = sorted(r["id"] for r in knowledge if r["confidence"] <= cut)
        print_breakdown(f"weak rows @ cut<={cut}", ids, sample)

    chosen = sorted(r["id"] for r in knowledge if r["confidence"] <= a.cut)
    out_path = resolve(a.out)
    out_path.write_text("\n".join(chosen) + ("\n" if chosen else ""))
    print(f"\nwrote {len(chosen)} ids (cut<={a.cut}) to {out_path}")


def cmd_boundary(a, labels, sample):
    boundary_gazes = {"multi", "atemporal"}
    ids = sorted(i for i, r in labels.items() if r.get("gaze") in boundary_gazes)
    by_gaze = Counter(labels[i]["gaze"] for i in ids)
    print(f"labelset   {Path(a.labelset).name}   {len(labels)} rows")
    print(f"boundary candidates (gaze in {sorted(boundary_gazes)})  n={len(ids)}")
    for g, n in sorted(by_gaze.items(), key=lambda kv: -kv[1]):
        print(f"    {g:<10} {n:>5}")
    print_breakdown("boundary candidates", ids, sample)

    hand_path = resolve(a.hand)
    if hand_path.exists():
        hand = load_jsonl(hand_path)
        overlap = sorted(set(ids) & set(hand))
        print(f"\n{len(overlap)} of {len(ids)} boundary candidates are also in the hand set"
              f" ({hand_path.name}) — validate.py can score a v1-vs-v2 prompt run on exactly"
              " this subset:")
        print(f"    python3 src/validate.py {hand_path.name} <v2 candidate> "
              f"--tol {a.tol}   # restrict to --ids below first")
        ov_path = resolve(a.out.replace("boundary_ids", "boundary_hand_ids")
                           if "boundary_ids" in a.out else "boundary_hand_ids.txt")
        ov_path.write_text("\n".join(overlap) + ("\n" if overlap else ""))
        print(f"wrote {len(overlap)} ids (boundary ∩ hand set) to {ov_path}")

    out_path = resolve(a.out)
    out_path.write_text("\n".join(ids) + ("\n" if ids else ""))
    print(f"\nwrote {len(ids)} ids to {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labelset", default=str(ROOT / "data" / "labels_api.jsonl"))
    ap.add_argument("--sample", default=str(ROOT / "data" / "sample.json"))
    ap.add_argument("--hand", default="labels_tmdb.jsonl",
                    help="reference/hand labelset, for --mode boundary overlap")
    ap.add_argument("--cut", type=float, default=0.65,
                    help="confidence cutoff for the ids written out (default 0.65)")
    ap.add_argument("--tol", type=int, default=2)
    ap.add_argument("--mode", choices=["weak", "boundary"], default="weak")
    ap.add_argument("--out", default=None,
                    help="defaults to data/weak_ids.txt or data/boundary_ids.txt by mode")
    a = ap.parse_args()
    if a.out is None:
        a.out = "weak_ids.txt" if a.mode == "weak" else "boundary_ids.txt"

    labels = load_jsonl(resolve(a.labelset))
    sample = load_sample(resolve(a.sample))

    if a.mode == "weak":
        cmd_weak(a, labels, sample)
    else:
        cmd_boundary(a, labels, sample)


if __name__ == "__main__":
    main()
