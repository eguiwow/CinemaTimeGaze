#!/usr/bin/env python3
"""Export the worst-labelled rows for a human to fix, and import the result back.

    python3 src/handlabel_queue.py export --n 100
    ...  fill in gaze / year_start / year_end / notes by hand in a spreadsheet  ...
    python3 src/handlabel_queue.py import data/handlabel/queue.csv

Export picks the N rows with basis=="knowledge" and the lowest confidence, that are not
already in the hand/reference set (or a previous hand2 import), and writes them to a CSV
under data/handlabel/ — a GITIGNORED path, because the CSV carries the TMDB plot summary
for context and that text is not ours to commit (see .gitignore / DATA-LICENSE.md).

Import reads that CSV back — only rows where "gaze" was actually filled in — validates the
values, and merges them into data/labels_hand2.jsonl (a new labelset, per contract C7: never
overwrites an existing labelset, and a second, later import only adds to / updates this file,
never touches labels_tmdb.jsonl or labels_api.jsonl).

Limitation: the CSV holds one target per film (year_start/year_end/notes). A film that needs
more than one period (a real "multi" case) should be hand-edited straight into the JSONL
after import — the CSV round-trip is for the common single-period case.
"""
import argparse, csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAZES = {"past", "present", "future", "multi", "atemporal"}
FIELDS = ["id", "title", "year", "country", "summary", "model_guess",
          "gaze", "year_start", "year_end", "notes"]


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


def model_guess(rec):
    if not rec:
        return ""
    if rec["gaze"] == "atemporal":
        return "atemporal"
    order = {"primary": 0, "secondary": 1, "minor": 2}
    ts = sorted(rec.get("targets") or [], key=lambda t: order.get(t["prominence"], 3))
    if not ts:
        return rec["gaze"]
    t = ts[0]
    yr = str(t["year_start"]) if t["year_start"] == t["year_end"] \
        else f"{t['year_start']}-{t['year_end']}"
    return f"{rec['gaze']} {yr}"


def cmd_export(a):
    sample = {r["id"]: r for r in json.loads(resolve(a.sample).read_text())}
    labelset = load_jsonl(a.labelset)
    exclude = set(load_jsonl(a.hand)) | set(load_jsonl(a.hand2))

    candidates = [r for r in labelset.values()
                  if r.get("basis") == "knowledge" and r["id"] not in exclude
                  and r["id"] in sample]
    candidates.sort(key=lambda r: (r["confidence"], -sample[r["id"]].get("notability", 0),
                                    r["id"]))
    chosen = candidates[:a.n]

    out_path = resolve(a.out, default_dir=ROOT / "data" / "handlabel")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in chosen:
            f = sample[r["id"]]
            w.writerow({
                "id": r["id"], "title": f["title"], "year": f["year"],
                "country": f.get("country") or "",
                "summary": (f.get("extract") or "").replace("\n", " "),
                "model_guess": model_guess(r),
                "gaze": "", "year_start": "", "year_end": "", "notes": "",
            })

    print(f"labelset   {Path(a.labelset).name}   {len(labelset)} rows")
    print(f"excluded   {len(exclude)} already-hand-labelled ids"
          f" ({Path(a.hand).name} + {Path(a.hand2).name})")
    print(f"candidates {len(candidates)} eligible (knowledge, not yet hand-labelled)")
    print(f"\nwrote {len(chosen)} rows to {out_path}"
          f"\n  (gitignored — carries TMDB summary text, never commit this file)")


def cmd_import(a):
    csv_path = resolve(a.csv, default_dir=ROOT / "data" / "handlabel")
    if not csv_path.exists():
        sys.exit(f"no such file: {csv_path}")

    existing = load_jsonl(a.out)
    new, skipped_blank, rejected = {}, 0, []

    with csv_path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            gaze = (row.get("gaze") or "").strip().lower()
            if not gaze:
                skipped_blank += 1
                continue
            try:
                if gaze not in GAZES:
                    raise ValueError(f"bad gaze {gaze!r}")
                if gaze == "atemporal":
                    targets = []
                else:
                    ys_raw = (row.get("year_start") or "").strip()
                    if not ys_raw:
                        raise ValueError("year_start required for a non-atemporal gaze")
                    ys = int(ys_raw)
                    ye_raw = (row.get("year_end") or "").strip()
                    ye = int(ye_raw) if ye_raw else ys
                    targets = [{"year_start": min(ys, ye), "year_end": max(ys, ye),
                                "prominence": "primary"}]
                new[row["id"]] = {
                    "id": row["id"], "gaze": gaze,
                    "earthbound": gaze != "atemporal",
                    "confidence": 0.9, "basis": "text",
                    "targets": targets, "labelset": "hand2",
                }
            except Exception as e:
                rejected.append((row.get("id", "?"), repr(e)))

    merged = dict(existing)
    merged.update(new)                        # this import wins on a shared id
    out_path = resolve(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                   for r in merged.values()) + ("\n" if merged else ""))

    print(f"read       {csv_path}")
    print(f"filled in  {len(new)}   blank/skipped {skipped_blank}   rejected {len(rejected)}")
    for fid, why in rejected[:10]:
        print(f"  reject {fid}: {why}")
    print(f"\n{out_path.name}: {len(existing)} before + {len(new)} imported"
          f" = {len(merged)} total")
    print(f"wrote {out_path}")
    print(f"\nnext:  python3 src/merge_labelsets.py --report"
          f"   (add \"hand2\" to data/labelsets.json's precedence first)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export", help="export the N worst rows to a CSV")
    exp.add_argument("--n", type=int, default=100)
    exp.add_argument("--labelset", default=str(ROOT / "data" / "labels_api.jsonl"))
    exp.add_argument("--sample", default=str(ROOT / "data" / "sample.json"))
    exp.add_argument("--hand", default="labels_tmdb.jsonl")
    exp.add_argument("--hand2", default="labels_hand2.jsonl")
    exp.add_argument("--out", default=str(ROOT / "data" / "handlabel" / "queue.csv"))
    exp.set_defaults(func=cmd_export)

    imp = sub.add_parser("import", help="import a filled-in CSV into labels_hand2.jsonl")
    imp.add_argument("csv")
    imp.add_argument("--out", default=str(ROOT / "data" / "labels_hand2.jsonl"))
    imp.set_defaults(func=cmd_import)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
