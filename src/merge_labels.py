#!/usr/bin/env python3
"""Merge label files into one, later files winning on a shared id.

Used after a corpus refresh: map_labels.py re-derives what it can from the old
hand labels, and this folds that together with labels made directly against the
current corpus, without losing either.

    python3 src/merge_labels.py --out labels_tmdb.jsonl labels_mapped.jsonl labels_tmdb.jsonl
"""
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def resolve(name):
    return Path(name) if "/" in name else ROOT / "data" / name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="label files, in increasing priority")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    merged, seen_from = {}, {}
    for name in a.inputs:
        p = resolve(name)
        if not p.exists():
            print(f"  skip {name} (not found)"); continue
        n = 0
        for line in p.read_text().split("\n"):
            if line.strip():
                r = json.loads(line)
                merged[r["id"]] = r
                seen_from[r["id"]] = p.name
                n += 1
        print(f"  read {n:>5} from {p.name}")

    out = resolve(a.out)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged.values()) + "\n")
    src = {}
    for i, f in seen_from.items():
        src[f] = src.get(f, 0) + 1
    print(f"\nwrote {len(merged)} unique labels to {out.name}")
    for f, n in sorted(src.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5} survive from {f}")


if __name__ == "__main__":
    main()
