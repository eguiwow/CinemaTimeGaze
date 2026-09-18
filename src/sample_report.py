#!/usr/bin/env python3
"""Compare two data/sample.json-shaped files — the published sample and a sampler candidate
— on the numbers that actually decide whether a re-sample is worth shipping (item 7).

    python3 src/sample_report.py candidate.json                  # vs data/sample.json
    python3 src/sample_report.py --baseline a.json candidate.json
    python3 src/sample_report.py candidate.json --json

Reports: film counts, US share, country/region counts, how many countries and regions
clear the 25-film aggregate-eligibility floor (a flat count over the whole sample, not
per decade — the same bar the page already applies to a country/region slice), how many
candidate films are not yet in a labels file (new classification work a re-sample would
create), and the pre-1960 non-Western film count (the gap TMDB itself can't fully close).
"""
import argparse, collections, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmdb_fetch import LEGACY_WEST             # the 16 countries "Western" means here

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "data" / "sample.json"
DEFAULT_LABELS = ROOT / "data" / "labels_api.jsonl"
AGG_FLOOR = 25                                  # the page's own minimum for a country/region slice
WESTERN = set(LEGACY_WEST)


def load_sample(path):
    return json.loads(Path(path).read_text())


def load_label_ids(path):
    path = Path(path)
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text().split("\n"):
        if line.strip():
            ids.add(json.loads(line)["id"])
    return ids


def stats(rows, label_ids):
    n = len(rows)
    countries = collections.Counter(r.get("country") for r in rows)
    regions = collections.Counter(r.get("region") for r in rows)
    us = countries.get("US", 0)
    wishlisted = sum(1 for r in rows if r.get("source") == "wishlist")
    pre1960_non_west = sum(1 for r in rows
                           if (r.get("year") or 0) < 1960 and r.get("country") not in WESTERN)
    unlabelled = sum(1 for r in rows if r.get("id") not in label_ids)
    return {
        "films": n,
        "wishlisted": wishlisted,
        "us_films": us,
        "us_share": (us / n) if n else 0.0,
        "countries": len(countries),
        "regions": len(regions),
        "countries_clearing_25": sum(1 for c in countries.values() if c >= AGG_FLOOR),
        "regions_clearing_25": sum(1 for c in regions.values() if c >= AGG_FLOOR),
        "pre1960_non_western": pre1960_non_west,
        "unlabelled": unlabelled,
        "top_countries": countries.most_common(8),
    }


def compare(baseline_rows, candidate_rows, label_ids):
    b = stats(baseline_rows, label_ids)
    c = stats(candidate_rows, label_ids)
    new_films = {r["id"] for r in candidate_rows} - {r["id"] for r in baseline_rows}
    c["new_vs_baseline"] = len(new_films)
    c["new_needing_classification"] = sum(1 for r in candidate_rows
                                          if r["id"] in new_films and r["id"] not in label_ids)
    return b, c


def render_text(name_a, b, name_b, c):
    wa = max(10, len(name_a) + 1)
    wb = max(10, len(name_b) + 1)

    def row(label, ak, unit=""):
        return f"  {label:<28}{b[ak]!s:>{wa}}{unit:<1}{c[ak]!s:>{wb}}{unit}"

    us_a = "{:.1f}%".format(100 * b["us_share"])
    us_b = "{:.1f}%".format(100 * c["us_share"])
    out = []
    out.append(f"  {'':28}{name_a:>{wa}}{name_b:>{wb + 1}}")
    out.append(row("films", "films"))
    out.append(row("wishlisted (excluded)", "wishlisted"))
    out.append(f"  {'US share':<28}{us_a:>{wa}}{us_b:>{wb + 1}}")
    out.append(row("countries", "countries"))
    out.append(row("  clearing 25 films", "countries_clearing_25"))
    out.append(row("regions", "regions"))
    out.append(row("  clearing 25 films", "regions_clearing_25"))
    out.append(row("pre-1960 non-Western", "pre1960_non_western"))
    out.append(row("unlabelled (of the whole file)", "unlabelled"))
    out.append("")
    out.append(f"  {name_b} vs {name_a}:")
    out.append(f"    new films              {c['new_vs_baseline']}")
    out.append(f"    of those, unlabelled   {c['new_needing_classification']}")
    out.append("")
    out.append(f"  top countries ({name_b}):")
    for code, n in c["top_countries"]:
        out.append(f"    {code:<6}{n:>6}  {100*n/c['films']:>5.1f}%")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("candidate", nargs="?", help="the sample to evaluate")
    ap.add_argument("--candidate", dest="candidate_opt", help="same as the positional arg")
    ap.add_argument("--baseline", default=str(DEFAULT_BASELINE),
                    help="what to compare against (default: data/sample.json)")
    ap.add_argument("--labels", default=str(DEFAULT_LABELS),
                    help="labels file used to count films needing classification")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()

    candidate = a.candidate_opt or a.candidate
    if not candidate:
        sys.exit("need a candidate sample.json — pass it as an argument")

    baseline_rows = load_sample(a.baseline)
    candidate_rows = load_sample(candidate)
    label_ids = load_label_ids(a.labels)
    b, c = compare(baseline_rows, candidate_rows, label_ids)

    if a.json:
        print(json.dumps({"baseline": {"path": str(a.baseline), **b},
                          "candidate": {"path": str(candidate), **c}}, indent=2))
        return

    print(render_text(Path(a.baseline).name, b, Path(candidate).name, c))


if __name__ == "__main__":
    main()
