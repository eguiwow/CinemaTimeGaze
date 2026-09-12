"""Validate and store compact classification lines.

Line format:  id|gaze|targets|conf|basis
  gaze     past|present|future|multi|atemporal
  targets  semicolon list of  YEAR:prom  or  Y1-Y2:prom   (prom = p|s|m); empty if atemporal
  conf     h|m|l          basis  t|k|b   (text / model knowledge / both)
"""
import json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sample = {r["id"]: r for r in json.loads((ROOT/"data"/"sample.json").read_text())}
out = ROOT/"data"/(sys.argv[1] if len(sys.argv) > 1 else "labels.jsonl")

GAZE = {"past","present","future","multi","atemporal"}
PROM = {"p":"primary","s":"secondary","m":"minor"}
CONF = {"h":0.9,"m":0.65,"l":0.35}
BASIS = {"t":"text","k":"knowledge","b":"both"}

ok, bad = [], []
for raw in sys.stdin.read().splitlines():
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        continue
    parts = raw.split("|")
    if len(parts) != 5:
        bad.append((raw, "want 5 fields")); continue
    fid, gaze, tgt, conf, basis = [p.strip() for p in parts]
    if fid not in sample: bad.append((raw, "unknown id")); continue
    if gaze not in GAZE:  bad.append((raw, "bad gaze")); continue
    if conf not in CONF:  bad.append((raw, "bad conf")); continue
    if basis not in BASIS:bad.append((raw, "bad basis")); continue
    targets = []
    if tgt:
        for chunk in tgt.split(";"):
            m = re.fullmatch(r"(-?\d+)(?:-(-?\d+))?:([psm])", chunk.strip())
            if not m: bad.append((raw, f"bad target {chunk!r}")); targets=None; break
            a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else a
            targets.append({"year_start":min(a,b), "year_end":max(a,b), "prominence":PROM[m.group(3)]})
        if targets is None: continue
    if gaze == "atemporal" and targets: bad.append((raw,"atemporal with targets")); continue
    if gaze != "atemporal" and not targets: bad.append((raw,"no targets")); continue
    ok.append({"id":fid, "gaze":gaze, "earthbound": gaze!="atemporal",
               "confidence":CONF[conf], "basis":BASIS[basis], "targets":targets})

with out.open("a") as f:
    for r in ok:
        f.write(json.dumps(r, ensure_ascii=False)+"\n")
print(f"stored {len(ok)}; rejected {len(bad)}")
for r,why in bad[:15]: print("  REJECT", why, "::", r[:90])
