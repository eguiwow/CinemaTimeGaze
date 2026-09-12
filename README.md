# CinemaTimeGaze

*Nostalgic or speculative?* — measuring, across a century of film, the gap between the year a
movie was **released** and the year (or years) it is **set in**, then drawing where each
release year's films look.

Open **`out/standalone.html`** in a browser. It is fully self-contained — the data is inlined,
no server, no build step. (Only external reference is the Google Fonts stylesheet; offline it
falls back to Georgia / system sans / system mono.)

## What it measures

```
delta  =  setting_year  -  release_year
```

Negative, the film looks back; near zero, it is set in its own present; positive, it looks
ahead. Aggregate that over a century and you can ask what no single film answers: was 1975
cinema about 1975, or about 1940?

Four rules make the number mean something:

- **The setting year is the year *depicted*, taken literally** — not the era evoked. *Star
  Wars* depicts no Earth calendar year, so it is `atemporal` and drops out of the delta
  rather than being filed under "the far past".
- **A film can point at several years, and each is its own target.** Collapsing *The
  Godfather Part II* to one "primary" year would discard exactly what this exists to show.
  Prominence maps to a weight, normalised per film, so **every film contributes exactly one
  unit of gaze** however many timelines it has.
- **Medians, never means.** One film set in the year 802,701 outweighs a thousand set last
  Tuesday. The observed mean delta is about -2,000 years, which is not a fact about cinema.
- **Three curves, not one:** share per direction, median lookback *given* it looks back,
  median lookahead *given* it looks ahead. A single average delta is drowned by the ~65% of
  films set in their own present.

## Known biases — read these before quoting a number

- **Present is the default, and the default is sticky.** A film whose setting is never stated
  and that the model does not recognise lands on "present". The bias runs *toward* the
  diagonal, so real nostalgia is probably understated.
- **55% of labels rest on model world knowledge, not on a stated year.** The classifier sees a
  ~90-word TMDB summary, which usually does not name the setting. That is a finding about the
  method as much as a caveat: the cheap-model plan is only as good as the text.
- **The labelled slice is 61% American** and currently covers 18 countries, against 51 in the
  sample. Any figure read without picking an origin is mostly a statement about American
  cinema. The rest of the world is fetched and sampled but not yet classified.
- **Popularity travels badly between film industries.** Films are ranked within each year by
  TMDB vote count, so a well-known film from a small country can sit below an obscure one from
  a large one. `--min-per-region` exists to push back on this.
- **Nothing has been scored against an independent ground truth yet.** Treat every shape here
  as a hypothesis, not a measurement.

Read **`brief.md`** for the full plan, schema, method, findings and open blockers. It is the
canonical document; the same text lives in the claude.ai project as `claude/brief.md`.

---

## Layout

```
brief.md                 canonical brief - plan, design, implementation, findings
src/tmdb_fetch.py        fetch the TMDB corpus (country x year)    -> data/tmdb_films.jsonl
                         region presets + adaptive year backstop   -> data/tmdb_coverage.json
src/sample_tmdb.py       stratified sample from TMDB               -> data/sample.json
src/map_labels.py        carry hand labels across a corpus change  -> data/labels_mapped.jsonl
src/merge_labels.py      fold label files together, later wins     -> chosen --out
src/sample.py            stratified sample from the Wikipedia corpus -> data/sample.json
src/batch.py             print the next unlabelled batch            (stdout)
src/ingest.py            validate + append compact labels          -> data/labels.jsonl
src/build_targets.py     expand into weighted targets + summary    -> data/targets.json
src/classify_api.py      classify with the Anthropic API           -> data/labels_api.jsonl
src/validate.py          score one labelset against another         (stdout)
src/build_viz.py         inject data into the template             -> out/*.html
src/viz_template.html    the page: styles, markup, chart code
data/sample.json         6,188 sampled films (50/year, 1900-2026) - gitignored, regenerable
data/labels*.jsonl       classifications, one JSON object per line (672 hand, 3,750 model)
data/targets.json        films + weighted targets, what the page reads
out/standalone.html      the visualization - open this (4 views + filters + facts)
out/timeline.html        same page as an artifact body (no doctype/head wrapper)
out/preview.png          static frame, 1980s selected
```

`data/movies.json` (the legacy 25 MB Wikipedia corpus) is not committed — `make data` fetches it.
Neither is the TMDB fetch output; see `DATA-LICENSE.md` for why.

## Run order

```bash
# 0. one-time - no pip install, everything here is stdlib
#    put both credentials in src/.env:  API_READ_TOKEN, API_KEY, ANTHROPIC_API_KEY

# 1. corpus - resumable, run it as many times as you like
make tmdb-check                            # one request: auth + shape
make tmdb-regions                          # the country presets
make tmdb-plan                             # request count + time estimate
python3 src/tmdb_fetch.py --from 1970 --to 1989    # or a slice at a time
make tmdb                                  # the rest (west, the legacy 16)
make tmdb-global                           # or the whole curated world, ~76 countries

# 2. sample + labels  (re-flattens first; merges labels without losing any)
make tmdb-sample
make tmdb-sample PER_REGION=3              # ...or reserve slots per region first

# 3. classify the corpus   <- must run in a normal Terminal, not a sandboxed shell
python3 src/classify_api.py --dry-run      # prompt + token estimate, no calls
make classify-cli                          # on the Claude Code subscription (no API credit)
#   or, once API billing is sorted:
make whoami                                # which org does the key actually bill?
make classify                              # -> data/labels_api.jsonl, ~$2.50 on Haiku 4.5

# 4. score the model against the hand labels
make validate

# 5. build the page
make targets LABELS=labels_api.jsonl
make viz                                   # -> out/standalone.html
```

Both `api.themoviedb.org` and authenticated calls to `api.anthropic.com` are blocked from
sandboxed shells, so run every step from a normal Terminal. (An unauthenticated request to
the Anthropic API gets through; the moment an `x-api-key` header is attached, the egress proxy
answers a plain-text 401 - identical for a valid key and for garbage, which is how you can
tell it is the proxy and not a bad key.)

### Leaving it running

`scripts/nightly_classify.sh` does one pass on the personal Claude Code account and exits.
It is resumable and idempotent, so once the corpus is fully labelled it does nothing and
returns 0 - safe to leave scheduled forever. It takes a lock so a long run cannot collide
with the next night's, logs to `logs/`, and rebuilds the page at the end.

```bash
./scripts/nightly_classify.sh          # tonight, by hand
cp scripts/com.eguiwow.cinematimegaze.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.eguiwow.cinematimegaze.plist   # 02:15 nightly
launchctl list | grep cinematimegaze   # verify
```

**A shell alias will not work as `--cli-cmd`** - a subprocess never reads `~/.zshrc`. The
`claude-ander` alias only exports `CLAUDE_CONFIG_DIR`, so the scripts do the same thing
directly: real binary plus `--claude-config-dir ~/.claude-ander`.

An unattended run stops itself after 5 consecutive failures rather than burning through
every remaining batch against a rate limit. Nothing is lost - rerunning resumes.

Steps 1 and 2 are safe to repeat at any point - the fetch skips cached cells and the label
merge keeps hand labels over re-derived ones. Step 5 defaults to `LABELS=labels_tmdb.jsonl`
(the hand set) if you leave the variable off.

## Label format

`id|gaze|targets|conf|basis`

| field | values |
|---|---|
| `gaze` | `past` · `present` · `future` · `multi` · `atemporal` |
| `targets` | `;`-separated `YEAR:prom` or `Y1-Y2:prom`; prom is `p`rimary / `s`econdary / `m`inor. Empty for `atemporal`. Negative years are BC. |
| `conf` | `h` · `m` · `l` |
| `basis` | `t` year stated in the source text · `k` model world knowledge · `b` both |

`build_targets.py` normalises prominence into a `weight` so every film contributes exactly one
unit of gaze however many timelines it has. This is the contract an API classifier must satisfy.

## The corpus

Two are supported. **TMDB** is the one to use: `with_origin_country` on `/discover/movie`
means country comes free with the query — no per-film detail call — which is what unblocks
the country slice, and `vote_count` is a real popularity signal rather than a proxy.
Credentials live in `src/.env` (`API_READ_TOKEN` preferred, `API_KEY` as fallback); that file
is gitignored and the fetcher never prints it.

The fetcher caches every raw page under `data/tmdb_raw/<country>/<year>-p<n>.json` and skips
what it already has, so you can stop it whenever and resume. It paces itself at `--rps` and
rests every `--pause-every` requests; on a 429 it honours `Retry-After`. Start with `--check`,
then a `--from/--to` slice, then the whole thing.

### Which countries

`--countries` takes region presets, bare ISO codes, or both: `west` is the original sixteen
(US / UK / Western Europe, kept verbatim so the existing cache and the published findings stay
reproducible), `global` is every curated national cinema, and `--list-regions` prints the rest.

Asking per country is not a convenience, it is the only way international films reach the raw
data at all — a popularity-ranked global query returns an overwhelmingly American corpus, and
no amount of downstream sampling can recover films that were never fetched.

Going global turns 16 countries into 76, and most of those cinemas have no TMDB coverage before
roughly 1970, so walking each one back to 1900 would spend thousands of requests to learn
nothing. The walk therefore runs **newest year first per country** and gives up on a country
after `--give-up-after` consecutive empty years (never above `--floor-year`, so a genuine gap
does not cut a country off early). Where it gave up is recorded in `data/tmdb_coverage.json`,
so the next run resumes instead of re-probing; `--reprobe` forces it to look again.

A balanced *fetch* still does not give a balanced *sample*: `sample_tmdb.py` ranks within each
year by vote count, and popularity travels badly between film industries. `--min-per-region K`
(and `--min-per-country K`) reserve slots before popularity gets a say. Both default to 0, and
the sampler prints the region and country mix plus a warning when one country takes over.

Switching corpora changes every film id, which would strand the 613 hand labels — they are the
only independent reference `validate.py` has. `map_labels.py` re-keys them onto the new sample
by normalised title and year, and reports what it could not match.

## State

| | |
|---|---|
| Corpus fetched | 76 countries probed, **51 with films in the sample** — `data/tmdb_coverage.json` records where each country's archive ran dry |
| Sampled | **6,188 films**, 1900–2026, 50 per release year ranked by TMDB vote count |
| Classified | **3,650 films / 3,682 targets** — 18 countries, 2 regions, 61% US |
| Direction split | 2,390 present · 1,154 back · 138 forward |
| Median lookback | **61 years** · median lookahead **38 years** |
| Basis | 2,011 knowledge · 972 text · 667 both |
| Validated | **not yet** — `make validate` has not been run against the 672 hand labels |

The gap that matters: the corpus is global, the *labels* are not. Classification is running
nightly on the personal Claude Code account and works through the sample in order, so the
non-Western half is fetched and sampled but still unlabelled. Every country or region
comparison is thin until it catches up.

See `brief.md` §7–§9 for the divergences, the findings and what unblocks the next step.

## Licence

Code is MIT (`LICENSE`). This project's own results — `data/labels*.jsonl` and
`data/targets.json` — are CC BY 4.0. TMDB's plot summaries are **not** redistributed, which
is why `data/sample.json` and the raw fetch are gitignored. See `DATA-LICENSE.md`.

## Attribution

The page footer credits TMDB and prints, verbatim, the sentence TMDB's terms require:
*"This product uses the TMDB API but is not endorsed or certified by TMDB."* The credit line
names whichever source actually produced the loaded data — `build_viz.py` reads that off the
film ids rather than trusting a constant, so a Wikipedia-corpus build does not claim to be a
TMDB one.

The logo is **not drawn by this project**. TMDB requires the official file, unmodified, so
`build_viz.py` inlines `assets/tmdb.svg` when it is present and degrades to a plain linked
wordmark when it is not. See `assets/README.md` for how to drop it in.
