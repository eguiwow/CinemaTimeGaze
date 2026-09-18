#!/usr/bin/env python3
"""Classify sampled films with the Anthropic API.

Emits the same JSONL that src/ingest.py produces, so build_targets.py reads either.
Resumable: already-classified ids in the output file are skipped, so a killed run
picks up where it stopped.

    Stdlib only - no pip install. Put ANTHROPIC_API_KEY in src/.env next to the TMDB
    creds, or export it in the shell.

    python3 src/classify_api.py --dry-run            # one prompt + cost estimate, no calls
    python3 src/classify_api.py --list-models        # model ids this key can reach
    python3 src/classify_api.py --per-year 50        # the real run
"""
import argparse, json, os, random, re, sys, threading, time
import urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=Path(__file__).resolve().parent / ".env"):
    """Tiny .env reader. Duplicated from tmdb_fetch.py on purpose - these two scripts
    stay independent so one can be edited while the other is mid-run."""
    env = {}
    if path.exists():
        for line in path.read_text().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
DEFAULT_MODEL = (ENV.get("ANTHROPIC_MODEL")
                 or os.environ.get("ANTHROPIC_MODEL")
                 or "claude-haiku-4-5")


def api_key():
    """src/.env wins over the shell, so the key survives a new terminal."""
    key = ENV.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("no Anthropic key - add ANTHROPIC_API_KEY to src/.env "
                 "(alongside the TMDB creds) or export it in the shell")
    return key

API = "https://api.anthropic.com/v1"


def api_call(path, payload=None, params=None, tries=5, timeout=120):
    """POST when payload is given, else GET. Retries 429 and 5xx; never logs the key."""
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    headers = {"x-api-key": api_key(), "anthropic-version": "2023-06-01",
               "content-type": "application/json", "accept": "application/json"}
    body = json.dumps(payload).encode() if payload is not None else None

    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST" if body else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code in (429, 529):
                wait = int(e.headers.get("retry-after", "5")) + 1
                print(f"    {e.code} - backing off {wait}s", flush=True)
                time.sleep(wait); continue
            if e.code in (401, 403):
                sys.exit(f"Anthropic rejected the key (HTTP {e.code}). {detail}")
            if 500 <= e.code < 600 and attempt < tries - 1:
                time.sleep(2 ** attempt + random.random()); continue
            raise SystemExit(f"HTTP {e.code} from {path}: {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                time.sleep(2 ** attempt + random.random()); continue
            raise SystemExit(f"cannot reach api.anthropic.com ({e})")
    raise SystemExit("gave up after repeated rate limiting")


_CLI_FLAGS = {}


def cli_flags(argv0, env, model=None):
    """Ask the CLI once which headless flags it supports, so this works across versions."""
    import subprocess
    key = (argv0, model)
    if key in _CLI_FLAGS:
        return _CLI_FLAGS[key]
    help_text = ""
    try:
        h = subprocess.run([argv0, "--help"], capture_output=True, text=True,
                           timeout=60, env=env)
        help_text = (h.stdout or "") + (h.stderr or "")
    except Exception:
        pass
    flags = []
    if "--output-format" in help_text:
        flags += ["--output-format", "text"]
    if "--permission-prompts" in help_text:
        flags += ["--permission-prompts", "none"]
    elif "--dangerously-skip-permissions" in help_text:
        flags += ["--dangerously-skip-permissions"]
    if model:
        if "--model" in help_text:
            flags += ["--model", model]
        else:
            print(f"  warning: CLI at {argv0} doesn't advertise --model in --help; "
                  f"running with its account default instead of {model}", file=sys.stderr)
    _CLI_FLAGS[key] = flags
    return flags


def cli_complete(cmd, system, prompt, timeout=600, config_dir=None, model=None):
    """Send one batch through the Claude Code CLI. Uses your subscription, not API credit.

    `cmd` may carry arguments. A shell ALIAS will not work here - a subprocess never sees
    your ~/.zshrc - so for a second account pass its config directory instead of the alias:
    the alias only exports CLAUDE_CONFIG_DIR, and config_dir does the same thing."""
    import shlex, shutil, subprocess
    argv = shlex.split(cmd)
    argv[0] = shutil.which(argv[0]) or argv[0]
    env = dict(os.environ)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(config_dir)
    full = argv + cli_flags(argv[0], env, model=model) + ["-p"]
    try:
        # prompt on stdin: keeps it off the argv length limit and matches the documented
        # `cat file | claude -p` shape
        r = subprocess.run(full, input=f"{system}\n\n{prompt}",
                           capture_output=True, text=True, timeout=timeout, env=env)
    except FileNotFoundError:
        raise SystemExit(f"'{argv[0]}' not found on PATH. Point --cli-cmd at the real binary "
                         "(a shell alias is invisible to a subprocess; run `type -a claude`), "
                         "and select an account with --claude-config-dir.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("CLI timed out")
    if r.returncode != 0:
        msg = ((r.stderr or "").strip() or (r.stdout or "").strip() or "(no output at all)")
        raise RuntimeError(f"CLI exited {r.returncode} running `{' '.join(full)}`: {msg[:400]}")
    return r.stdout


def cli_probe(cmd, config_dir, model=None):
    """Run the smallest possible prompt through the CLI and show everything it said.
    Use this when batches fail with an empty error - the CLI usually explains itself
    on stdout, which a normal run swallows."""
    import shlex, shutil, subprocess
    argv = shlex.split(cmd)
    argv[0] = shutil.which(argv[0]) or argv[0]
    env = dict(os.environ)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(config_dir)
    print(f"binary       {argv[0]}")
    print(f"config dir   {env.get('CLAUDE_CONFIG_DIR', '(default)')}")
    print(f"cwd          {os.getcwd()}")
    print(f"model        {model or '(account default)'}")
    full = argv + cli_flags(argv[0], env, model=model) + ["-p"]
    print(f"flags found  {' '.join(cli_flags(argv[0], env, model=model)) or '(none)'}")
    print(f"command      {' '.join(full)}  <- prompt on stdin\n")
    r = subprocess.run(full, input="reply with the word ok",
                       capture_output=True, text=True, timeout=120, env=env)
    print(f"exit code    {r.returncode}")
    print(f"--- stdout ---\n{r.stdout.strip() or '(empty)'}")
    print(f"--- stderr ---\n{r.stderr.strip() or '(empty)'}")
    if r.returncode != 0:
        print("\nCommon causes: the account in that config dir is not logged in "
              "non-interactively, or Claude Code has not been trusted in this folder yet. "
              "Try running the same command by hand in this directory once.")


def whoami():
    """Which organisation does this key bill to? Credits sit on an organisation, so a
    'balance too low' while the Console shows money almost always means the key belongs
    to a different org, or to a workspace with a zero spend limit."""
    k = api_key()
    print(f"key            {len(k)} chars, {k[:11]}...{k[-4:]}")
    req = urllib.request.Request(
        f"{API}/messages", method="POST",
        data=json.dumps({"model": "claude-haiku-4-5", "max_tokens": 1,
                         "messages": [{"role": "user", "content": "hi"}]}).encode(),
        headers={"x-api-key": k, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            hdrs, code, body = r.headers, r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        hdrs, code, body = e.headers, e.code, e.read().decode()
    print(f"HTTP           {code}")
    for h in ("anthropic-organization-id", "request-id",
              "anthropic-ratelimit-requests-limit"):
        if hdrs.get(h): print(f"{h:<14.14} {hdrs.get(h)}")
    try:
        err = json.loads(body).get("error", {})
        if err: print(f"error          {err.get('type')}: {err.get('message')}")
    except Exception:
        print(f"body           {body[:200]}")
    print("\nIf that organization id is not the org holding your credits, the key was made in\n"
          "the wrong org: Console -> org switcher -> API keys -> create one there.\n"
          "Also check Console -> Workspaces: a workspace spend limit of 0 blocks its own keys\n"
          "even when the org is funded. And a Claude Pro/Max subscription is not API credit.")


PROM = {"primary": 1.0, "secondary": 0.5, "minor": 0.25}
CONF = {"high": 0.9, "medium": 0.65, "low": 0.35}

# v1 is the prompt that produced data/labels_api.jsonl (pass 1). It must never change —
# tests/test_labels.py pins it against a stored hash. Add new rules for v2+ instead of
# editing these lines.
SYSTEM_V1 = """\
You classify films by the year they are SET IN, relative to the year they were released.

THE GOVERNING RULE
`setting_year` is the year DEPICTED, taken literally — the calendar year the events occur in,
as the film states or plainly implies. Not the year the film evokes, not the era it feels like.

RULES
1. A film may depict more than one period. Return ONE ENTRY PER PERIOD in `targets`.
   Framing devices, time travel and generational sagas all produce two or three targets.
   Do not flatten them to one. A film set entirely in one year has exactly one target.
2. If NO Earth calendar year is depicted — a secondary world, a fantasy realm, an unnamed
   fairy-tale past, a purely non-Earth setting — set gaze="atemporal" and targets=[].
   Star Wars is atemporal. Middle-earth is atemporal. Ancient Rome is NOT (it is 1st century).
3. `prominence` says how much of the film lives in that period: "primary" for the main
   period(s), "secondary" for a substantial thread, "minor" for a brief episode or vision.
4. `basis` says where your answer came from:
     "text"      the period is stated or plainly implied in the summary you were given
     "knowledge" you recognised the film and know its setting; the summary does not say
     "both"      both
   Be honest about this. It is the field used to audit the dataset.
5. `confidence`: "high" if you are sure, "medium" if inferring from genre and context,
   "low" if guessing. Prefer "low" over inventing a year.
6. Years before year 1 are negative (55 BC is -55). Year spans use year_start/year_end;
   a single year sets both to the same value.
7. `gaze` summarises the film: "past", "present", "future" (all targets in one direction),
   "multi" (targets in more than one direction), "atemporal" (no targets).
   "present" means within 2 years of release.

Return ONLY a JSON array, one object per film, in the order given, no prose and no code fence.
Every object: {"id","gaze","earthbound","confidence","targets":[{"year_start","year_end",
"prominence","basis"}]}
"""

# v2 keeps every v1 rule verbatim and only sharpens the two boundaries validate.py scores
# worst on: multi-timeline recall (framing/flashback/epilogue structures collapsed to one
# target) and atemporal agreement (unmoored-but-real-world "present" mislabelled atemporal,
# or vice versa). See the report for the exact diff against v1.
SYSTEM_V2 = SYSTEM_V1.replace(
    "1. A film may depict more than one period. Return ONE ENTRY PER PERIOD in `targets`.\n"
    "   Framing devices, time travel and generational sagas all produce two or three targets.\n"
    "   Do not flatten them to one. A film set entirely in one year has exactly one target.\n",
    "1. A film may depict more than one period. Return ONE ENTRY PER PERIOD in `targets`.\n"
    "   Framing devices, time travel and generational sagas all produce two or three targets.\n"
    "   Do not flatten them to one. A film set entirely in one year has exactly one target.\n"
    "   1a. A FRAMING STORY counts as its own target even if it is brief: an elderly narrator\n"
    "       recalling their life, a \"years later\" epilogue, a story told to a present-day\n"
    "       listener, a device that opens/closes the film in a different year than the body\n"
    "       of the story. Give it prominence=\"secondary\" or \"minor\" as appropriate, but do\n"
    "       not drop it — a two-minute frame is still a second period.\n"
    "   1b. A single embedded flashback/flash-forward that stays inside the SAME period as the\n"
    "       main action (a scene from earlier the same year, a brief memory with no new dated\n"
    "       anchor) is NOT a second target — only create one when the flashback/epilogue is\n"
    "       itself pinned to a distinct year or era.\n",
).replace(
    "2. If NO Earth calendar year is depicted — a secondary world, a fantasy realm, an unnamed\n"
    "   fairy-tale past, a purely non-Earth setting — set gaze=\"atemporal\" and targets=[].\n"
    "   Star Wars is atemporal. Middle-earth is atemporal. Ancient Rome is NOT (it is 1st century).\n",
    "2. If NO Earth calendar year is depicted — a secondary world, a fantasy realm, an unnamed\n"
    "   fairy-tale past, a purely non-Earth setting — set gaze=\"atemporal\" and targets=[].\n"
    "   Star Wars is atemporal. Middle-earth is atemporal. Ancient Rome is NOT (it is 1st century).\n"
    "   2a. \"atemporal\" is for settings with NO real-world calendar to anchor to at all, not for\n"
    "       settings that simply don't STATE a year. A film that is plainly our own world in what\n"
    "       looks like the ordinary present day — contemporary clothes, cars, phones, cities —\n"
    "       is gaze=\"present\" even with no date on screen; do not mark it atemporal for lack of\n"
    "       an explicit year. Reserve \"atemporal\" for secondary worlds, unmoored fables/fairy\n"
    "       tales explicitly outside real history, and settings with no discernible calendar\n"
    "       (not merely an unstated one).\n"
    "   2b. An unspecified-but-clearly-historical setting (\"a medieval kingdom\", \"an unnamed\n"
    "       17th-century war\") that is on Earth and in real history gets a best-guess year range\n"
    "       with confidence=\"low\", not \"atemporal\" — atemporal means no history, not vague\n"
    "       history.\n",
)

PROMPTS = {"v1": SYSTEM_V1, "v2": SYSTEM_V2}
SYSTEM = SYSTEM_V1          # back-compat name; several call sites default to this

USER_TMPL = """Classify these {n} films.

{films}

Return the JSON array now."""


def load_plots(path):
    """--plots FILE: JSONL {id, plot, source} — a longer plot that replaces the TMDB
    extract for that film, e.g. a Wikipedia section from fetch_plots.py."""
    plots = {}
    if not path:
        return plots
    p = Path(path)
    if not p.exists():
        sys.exit(f"--plots file not found: {p}")
    for line in p.read_text().split("\n"):
        if line.strip():
            r = json.loads(line)
            plots[r["id"]] = r
    return plots


def film_line(r, words, plot_override=None):
    g = "/".join(r["genres"][:3]) or "unknown genre"
    text = plot_override if plot_override is not None else (r.get("extract") or "")
    ex = " ".join(text.split()[:words])
    head = f'id={r["id"]} | {r["title"]} ({r["year"]}) [{g}]'
    return f"{head}\n  {ex}" if ex else head


def load_done(path):
    done = set()
    if path.exists():
        for line in path.read_text().split("\n"):
            if line.strip():
                try:
                    done.add(json.loads(line)["id"])
                except Exception:
                    pass
    return done


def pick(sample, per_year, done, limit, ids=None):
    """Select films to classify.

    `ids`, when given, is an explicit id list (e.g. from weak_rows.py) — it BYPASSES the
    per-year cap entirely: every requested id not already done is queued, in the order
    given, up to `limit`. This is the only mode used for a targeted second pass.

    Otherwise: films with "source"=="wishlist" always survive, because they were added
    outside the sampling rule on purpose (C3/C6) — the per-year notability cap must never
    be the reason a wishlisted film stays unclassified. Every other film goes through the
    existing per-year-by-notability selection, unchanged from v3.
    """
    if ids is not None:
        by_id = {r["id"]: r for r in sample}
        out = [by_id[i] for i in ids if i in by_id and i not in done]
        return out[:limit] if limit else out

    wishlist = [r for r in sample if r.get("source") == "wishlist" and r["id"] not in done]
    rest = [r for r in sample if r.get("source") != "wishlist"]

    by_year = defaultdict(list)
    for r in sorted(rest, key=lambda r: -r["notability"]):
        by_year[r["year"]].append(r)
    capped = []
    for rank in range(per_year):
        for y in sorted(by_year):
            pool = [r for r in by_year[y] if r["id"] not in done]
            if rank < len(pool):
                capped.append(pool[rank])

    seen, out = set(), []
    for r in wishlist + capped:                 # wishlist first: never cut by --limit either
        if r["id"] not in seen:
            seen.add(r["id"]); out.append(r)
    return out[:limit] if limit else out


def parse_reply(text, batch_ids):
    """Pull the JSON array out of a reply and normalise it to ingest's record shape."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        raise ValueError("no JSON array in reply")
    rows = json.loads(text[start:end + 1])
    ok, bad = [], []
    valid = set(batch_ids)
    for r in rows:
        try:
            fid = r["id"]
            if fid not in valid:
                bad.append((fid, "id not in batch")); continue
            gaze = r["gaze"]
            if gaze not in {"past", "present", "future", "multi", "atemporal"}:
                bad.append((fid, f"bad gaze {gaze!r}")); continue
            targets = []
            for t in (r.get("targets") or []):
                a, b = int(t["year_start"]), int(t["year_end"])
                p = t.get("prominence", "primary")
                if p not in PROM:
                    p = "primary"
                targets.append({"year_start": min(a, b), "year_end": max(a, b), "prominence": p})
            if gaze == "atemporal" and targets:
                targets = []
            if gaze != "atemporal" and not targets:
                bad.append((fid, "no targets")); continue
            bases = [t.get("basis") for t in (r.get("targets") or [])] or [r.get("basis")]
            basis = bases[0] if bases[0] in {"text", "knowledge", "both"} else "knowledge"
            ok.append({
                "id": fid, "gaze": gaze,
                "earthbound": bool(r.get("earthbound", gaze != "atemporal")),
                "confidence": CONF.get(r.get("confidence", "medium"), 0.65),
                "basis": basis, "targets": targets,
            })
        except Exception as e:
            bad.append((r.get("id", "?"), repr(e)))
    return ok, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default=str(ROOT / "data" / "sample.json"))
    ap.add_argument("--out", default=str(ROOT / "data" / "labels_api.jsonl"))
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch", type=int, default=10, help="films per request")
    ap.add_argument("--per-year", type=int, default=50)
    ap.add_argument("--limit", type=int, default=0, help="0 = no cap")
    ap.add_argument("--words", type=int, default=45, help="words of summary per film")
    ap.add_argument("--ids", default=None,
                    help="file of film ids (one per line, e.g. from weak_rows.py) — classify "
                         "exactly these films, bypassing the per-year cap")
    ap.add_argument("--plots", default=None,
                    help="JSONL {id, plot, source} of longer plot text (e.g. from "
                         "fetch_plots.py) that replaces the TMDB extract for those films")
    ap.add_argument("--prompt", choices=sorted(PROMPTS), default="v1",
                    help="v1 is the unchanged pass-1 prompt; v2 sharpens the multi-timeline "
                         "and atemporal boundary rules (see report/diff)")
    ap.add_argument("--labelset", default=None,
                    help="stamps \"labelset\", \"model\" and \"prompt\" fields onto every "
                         "output record — use a name like pass2 so merge_labelsets.py and "
                         "validate.py can tell passes apart without guessing from the filename")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--cli-probe", action="store_true",
                    help="run one trivial prompt through the CLI and print all output")
    ap.add_argument("--whoami", action="store_true",
                    help="which org/workspace this key bills to")
    ap.add_argument("--backend", choices=["api", "cli"], default="api",
                    help="'cli' pipes each batch through the Claude Code CLI, so it runs on "
                         "your subscription and costs no API credit")
    ap.add_argument("--cli-cmd", default=os.environ.get("CLAUDE_CLI", "claude"),
                    help="the Claude Code executable (the real path, not a shell alias)")
    ap.add_argument("--claude-config-dir", default=os.environ.get("CLAUDE_CONFIG_DIR"),
                    help="which Claude Code account to bill, e.g. ~/.claude-ander")
    ap.add_argument("--cli-timeout", type=int, default=600)
    ap.add_argument("--max-consecutive-failures", type=int, default=5,
                    help="stop the run after this many failures in a row (rate limits)")
    a = ap.parse_args()

    out_path = Path(a.out)
    sample = json.loads(Path(a.sample).read_text())
    done = load_done(out_path)
    ids_filter = None
    if a.ids:
        ids_filter = [line.strip() for line in Path(a.ids).read_text().split("\n")
                      if line.strip()]
    queue = pick(sample, a.per_year, done, a.limit, ids=ids_filter)
    batches = [queue[i:i + a.batch] for i in range(0, len(queue), a.batch)]
    plots = load_plots(a.plots)
    system_prompt = PROMPTS[a.prompt]

    if a.cli_probe:
        cli_probe(a.cli_cmd, a.claude_config_dir, model=a.model); return

    if a.whoami:
        whoami(); return

    if a.list_models:
        for m in api_call("/models", params={"limit": 100}).get("data", []):
            print(f"{m.get('id',''):42} {m.get('display_name','')}")
        return

    if a.dry_run:
        if not batches:
            print("nothing to do — every selected film is already labelled"); return
        b = batches[0]
        prompt = USER_TMPL.format(n=len(b), films="\n".join(
            film_line(r, a.words, plots.get(r["id"], {}).get("plot")) for r in b))
        print("=" * 70); print(system_prompt); print("-" * 70); print(prompt); print("=" * 70)
        pt = (len(system_prompt) + len(prompt)) / 3.6   # ~3.6 chars/token, close enough
        print(f"\nfilms queued     {len(queue)}  ({len(done)} already done)")
        print(f"requests         {len(batches)} of {a.batch}")
        print(f"input tokens     ~{int(pt * len(batches)):,}")
        print(f"output tokens    ~{int(len(queue) * 55):,}")
        print(f"model            {a.model}")
        print(f"prompt           {a.prompt}")
        if plots:
            print(f"plots            {len(plots)} overrides loaded from {a.plots}")
        print("\nNo calls made. Drop --dry-run to run it.")
        return

    if not batches:
        print(f"nothing to do — {len(done)} films already in {out_path.name}"); return

    if a.backend == "api":
        api_key()                  # fail fast, before any work
    lock = threading.Lock()
    tally = {"ok": 0, "bad": 0, "in": 0, "out": 0, "fail": 0}
    fh = out_path.open("a")

    state = {"streak": 0, "abort": False}

    def run(idx_batch):
        idx, b = idx_batch
        if state["abort"]:
            return
        prompt = USER_TMPL.format(n=len(b), films="\n".join(
            film_line(r, a.words, plots.get(r["id"], {}).get("plot")) for r in b))
        try:
            if a.backend == "cli":
                text = cli_complete(a.cli_cmd, system_prompt, prompt,
                                    timeout=a.cli_timeout,
                                    config_dir=a.claude_config_dir,
                                    model=a.model)
                msg = {}
            else:
                msg = api_call("/messages", {
                    "model": a.model, "max_tokens": a.max_tokens, "system": system_prompt,
                    "messages": [{"role": "user", "content": prompt}],
                })
                text = "".join(c.get("text", "") for c in msg.get("content", []))
            rows, bad = parse_reply(text, [r["id"] for r in b])
            if a.labelset:
                for r in rows:
                    r["labelset"] = a.labelset
                    r["model"] = a.model
                    r["prompt"] = a.prompt
        except Exception as e:
            with lock:
                tally["fail"] += 1
                state["streak"] += 1
                print(f"  batch {idx + 1}: FAILED {e}", file=sys.stderr)
                if state["streak"] >= a.max_consecutive_failures and not state["abort"]:
                    state["abort"] = True
                    print(f"\n  {state['streak']} failures in a row - stopping. "
                          "Nothing is lost: rerun and it resumes from the output file.",
                          file=sys.stderr)
            return
        with lock:
            state["streak"] = 0
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            tally["ok"] += len(rows); tally["bad"] += len(bad)
            u = msg.get("usage", {})
            tally["in"] += u.get("input_tokens", 0); tally["out"] += u.get("output_tokens", 0)
            print(f"  batch {idx + 1}/{len(batches)}  +{len(rows)}"
                  f"{f'  ({len(bad)} rejected)' if bad else ''}")
            for fid, why in bad[:3]:
                print(f"      reject {fid}: {why}", file=sys.stderr)

    if a.backend == "cli":
        print(f"{len(queue)} films / {len(batches)} batches via {a.cli_cmd} "
              f"(subscription, no API credit)")
        if a.concurrency > 2:
            print(f"  dropping concurrency {a.concurrency} -> 2 for the CLI backend")
            a.concurrency = 2
    else:
        print(f"{len(queue)} films · {len(batches)} requests · model {a.model}")
    with ThreadPoolExecutor(max_workers=a.concurrency) as pool:
        list(pool.map(run, enumerate(batches)))
    fh.close()

    if state["abort"]:
        print("\nSTOPPED EARLY after consecutive failures - rerun to continue.")
    print(f"\nwrote {tally['ok']} labels to {out_path}"
          f"  ({tally['bad']} rejected, {tally['fail']} batches failed)")
    if a.backend == "api":
        print(f"tokens: {tally['in']:,} in / {tally['out']:,} out"
              f"   (~${tally['in']/1e6*1 + tally['out']/1e6*5:.2f} at Haiku 4.5 list price)")
    else:
        print("ran on your Claude Code subscription - no API credit used")
    print(f"\nnext:  python3 src/build_targets.py --labels {out_path.name}")


if __name__ == "__main__":
    main()
