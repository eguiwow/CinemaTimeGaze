# Nostalgic or speculative? — canonical brief

> **This file is a snapshot.** The canonical brief lives in the Claude project
> (`claude/brief.md`) and is ahead of this copy as of the global-corpus build:
> region presets in the fetch, the region grain through the pipeline, and TMDB
> attribution in the footer. See `README.md` for the current usage.

Plan, design and implementation for the project, compacted into one doc.
Supersedes the standalone v0.1–v0.4 briefs. Idea backlog lives in `claude/v2-ideas.md`.

**Status:** proof of concept built and working, 613 films labelled, interactive timeline
published. The labels are unvalidated and the corpus is a substitute for the intended one —
see *Divergences* and *Blockers*.

---

## 1. The question

Every film is made in one year and *set* in some year. The gap between them is the measurement:

```
Δ = setting_year − release_year
```

Δ < 0 looks back · Δ ≈ 0 looks at its own present · Δ > 0 looks ahead.

Aggregate Δ and you can ask what no single film answers: was 1975 cinema about 1975, 1950 or
1999? Is there a stable "nostalgia distance"? Has the future horizon contracted?

## 2. Decisions

| | |
|---|---|
| Corpus | Feature films only (no TV), 50 per release year, ranked by popularity within the year |
| Span | As far back as the data allows; early years take whatever they can fill |
| Territory | Well-covered national cinemas (US / UK / Western Europe) — global later |
| Classifier | Cheapest capable model (Haiku-class) over plot text — no frontier model |
| Ground truth | Wikidata narrative-time properties where they overlap the sample |
| Setting year | The year **depicted** on screen, taken literally — not the era evoked |
| Multi-timeline | Every year a film points to is its own weighted target, never collapsed to one |
| PoC output | A dataset + an accuracy number + an interactive year/decade timeline |

The literal rule resolves the hard cases cleanly: *Star Wars* depicts no Earth calendar year,
so it is `atemporal` and drops out of Δ rather than being filed under "the far past".

## 3. Schema — two tables, not one

The unit of analysis is a **target** (a year a film points at), not a film. Collapsing
*The Godfather Part II* to one "primary" year would discard exactly what the timeline exists
to show.

**`films`** — `id`, `title`, `release_year`, `gaze` (`past`|`present`|`future`|`multi`|
`atemporal`), `earthbound`, `n_targets`, `confidence`, `basis`

**`targets`** — `film_id`, `seq`, `year_start`, `year_end`, `year_mid`, `prominence`
(`primary`|`secondary`|`minor`), `weight`, `delta`, `direction` (`back`|`present`|`forward`),
`basis`

**Why `weight` exists.** Without it a three-timeline film counts three times as much as a
period drama and quietly dominates every average. Prominence maps to raw weights
(1.0 / 0.5 / 0.25), normalised per film, so **every film contributes exactly one unit of gaze**
split across the years it looks at.

**`basis`** — `text` (the year is stated in the source), `knowledge` (the model recognised the
film), `both`. This turned out to be the most diagnostic field in the dataset; see §7.

Buckets: atemporal/secondary worlds are counted but excluded from Δ; documentaries are out of
the PoC; **present is the default and the default is sticky** — an unrecognised film with no
stated year lands on "present", biasing everything toward the diagonal.

## 4. Method

1. **Sample** — stratify by release year first, then rank within the year by popularity.
   Stratification is load-bearing: a popularity-only sample is crushingly modern and makes the
   time series meaningless. The quota is a ceiling, not a target; thin years take what exists
   and years under ~5 usable films are dropped rather than padded.
2. **Classify** — small model over `title + release_year + genres + plot text`, strict JSON,
   `targets` as an **array** (prompt for it explicitly or every multi-timeline film silently
   flattens to one), `prominence` and `basis` per target, batched ~10 films per request.
   Route low-confidence cases to richer text before routing them to a bigger model.
3. **Validate** — score against a hand-labelled hold-out and the Wikidata overlap. Report the
   accuracy next to every chart. *Not done yet.*
4. **Aggregate and draw** — see §5 and §6.

## 5. Analysis rules

- **Medians, never means.** One film set in 802,701 outweighs a thousand set last Tuesday.
  Observed means are absurd (−1.5M years); medians are stable.
- **Three curves, not one.** Share per direction, median lookback given past-looking, median
  lookahead given future-looking. A single mean Δ is dominated by the present-day mass.
- **Weight before you average** — aggregate over `targets`, using `weight`.
- **Small n.** At 50/year a per-year direction share rests on a handful of films; smooth over
  5-year windows for trend lines and show the raw points behind them.
- **Known contaminants** — re-releases (a 2013 3-D re-release of a 1993 film reads as a 20-year
  lookback), and release-year lag against production year.

## 6. The visualization

`out/timeline.html` — self-contained, data inlined as JSON, no build step, no local
dependencies. Only external reference is the Google Fonts stylesheet, which degrades to
Georgia / system-sans / system-mono offline.

- **The year explorer.** Horizontal year axis; select a year or decade; every film released in
  that slice throws one arc per target — left-and-below for the past, right-and-above for the
  future. Thickness = target weight. Hover names the film and its basis.
- **Present collapses to a bar** at the origin. 60% of targets sit at Δ≈0 and would otherwise
  render as thousands of zero-length lines burying the arcs that actually travel.
- **Piecewise year axis.** The dense 1850–2050 band takes most of the width; deep past and far
  future are compressed, with dashed breaks marking the joins. Distances across a break are not
  comparable — this is the fix for the far-future-outlier problem, not a cosmetic choice.
- **Master scatter** below: release year vs. depicted year, one dot per target, diagonal drawn
  in. It is the sanity check — if it and the explorer disagree, the aggregation is wrong.
- Diverging blue↔red palette (past↔future) with a grey neutral, validated for colour-vision
  deficiency in both themes; position above/below the axis is a second, redundant encoding.
- **Decade bands.** A third chart: per release decade, the share of *travelling* films by
  distance — three back bands, three forward bands, sequential shading by distance, with the
  present share reported separately on the right. This is the distribution view; the timeline
  and the stat tiles are the summary.
- **Filters.** Genre (with an *exclude* inversion) and country, applied to all four views at
  once. Genre is a control rather than a finding — the question it answers is whether the
  decade pattern survives holding genre constant. The country control hides itself when the
  loaded corpus carries no country field, so the page works on either corpus. Filtering below
  25 targets prints a warning instead of quietly drawing noise.
- **Facts strip.** Ten fact *shapes* computed from the filtered data, never hand-written, each
  carrying its own minimum sample and printing the n it rests on. Shapes: most future-facing
  and most past-facing decade, the decade the forward gaze appears, whether the backward reach
  lengthened or shortened, median lookback against the generational cycle, back-vs-forward
  counts, the forward/back distance ratio (*only* when the ratio clears 1.5x or 0.67x — at the
  current 39y/54y it is suppressed rather than overclaimed), deepest-reaching and most
  forward-looking genre, the country gap, and the two extreme films. It rotates every 9s
  unless the viewer prefers reduced motion.
- Data table and a full caveats section ship inside the page.

## 7. What was actually built

```
src/tmdb_fetch.py     fetch the TMDB corpus (country x year)    -> data/tmdb_films.jsonl
src/sample_tmdb.py    stratified sample from TMDB               -> data/sample.json
src/map_labels.py     re-key labels across a corpus change      -> data/labels_mapped.jsonl
src/sample.py         stratified sample from the Wikipedia corpus -> data/sample.json
src/batch.py          print the next unlabelled batch
src/ingest.py         validate + append compact labels          -> data/labels.jsonl
src/build_targets.py  expand to weighted targets + summary      -> data/targets.json
src/classify_api.py   classify with the Anthropic API           -> data/labels_api.jsonl
src/validate.py       score one labelset against another         (stdout)
src/build_viz.py      inject data into the template             -> out/*.html
```

Label line format: `id|gaze|targets|conf|basis`, where targets is a `;`-separated list of
`YEAR:prom` or `Y1-Y2:prom` (`p`/`s`/`m`), conf is `h`/`m`/`l`, basis is `t`/`k`/`b`.
Negative years are BC. This is the contract the API classifier has to satisfy.

**Divergences from the plan, all forced:**

- **Corpus.** IMDb, Wikipedia, Wikidata and TMDB are *all* denied by the egress policy, from
  the cloud sandbox and from the Linux VM behind the Mac's folder access. GitHub is the only
  reachable data host. Substituted `prust/wikipedia-movie-data` — 36,273 American films
  1900–2023 with Wikipedia lead paragraphs. So: **American film only**, not US/UK/EU.
  **A TMDB key now exists** (`src/.env`) and `tmdb_fetch.py` is written against it, but the
  firewall is upstream of the key — TMDB has to be fetched from a normal terminal, not from
  inside the sandbox. Once that runs, the territory decision in §2 is finally satisfiable and
  the country slice stops being blocked.
- **Popularity proxy.** That dataset has no vote counts; notability is proxied by
  Wikipedia lead length + cast-list size. Crude but it separates written-about films from stubs.
- **Classifier.** No API key in the sandbox, so the 613 labels were produced by hand in-session.
  `classify_api.py` is now written and dry-run tested (prompt, batching, resume, JSON repair,
  cost estimate, `--list-models`) but has never made a real call.
- **Plot text.** A ~90-word lead, not a plot section — which is why **85% of labels rest on
  model world knowledge rather than a stated year**. That is a finding, not just a caveat: the
  cheap-model plan is only as good as the text, and this text mostly does not name the setting.

## 8. Current findings — measured, with a stated error

The full corpus: 6,187 films, 6,112 targets, 51 countries, 1900–2026, at 90% agreement with
the hand set. **Median lookback 62 years, median lookahead 35 years.**

| decade | 1900s | 1910s | 1920s | 1930s | 1940s | 1950s | 1960s | 1970s | 1980s | 1990s | 2000s | 2010s | 2020s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| % back | 11 | 20 | 28 | 26 | 29 | 37 | 36 | 34 | 25 | 26 | 28 | 29 | 26 |
| median back | 182y | 69y | 108y | 52y | 50y | 68y | 81y | 54y | 50y | 57y | 60y | 55y | 69y |
| % forward | 0 | 0 | 1 | 1 | 0 | 1 | 2 | 4 | 3 | 5 | 6 | 8 | 5 |
| median forward | — | 20y | 36y | 20y | — | 12y | 34y | 49y | 34y | 40y | 46y | 30y | 42y |
| n targets | 319 | 456 | 466 | 473 | 504 | 500 | 517 | 492 | 505 | 521 | 511 | 503 | 345 |

**Going global changed the picture, and that is itself the finding.** On the old US-heavy
build the backward share swung from 17% to 49%; on the global corpus it sits in a narrow
25–37% band for every decade from the 1920s on. Much of what looked like a dramatic
mid-century nostalgia peak was a fact about *which films were in the sample*, not about
cinema. The US share fell from 61% to 38% and the shape flattened.

Three readings:

1. **The ~20-year nostalgia hypothesis still does not hold.** Typical lookback is 50–70
   years across the whole century — two generations, not one — and it is remarkably stable.
   This is the most robust finding here: it survived the corpus tripling and going global.
2. **The mid-century backward peak is real but modest.** The 1950s–60s do sit highest (37%,
   36%), but against a 25–29% baseline rather than the 19–20% the US-only build suggested.
   It is a bump, not a regime change.
3. **The forward gaze is still the cleanest signal, and it is monotonic.** Essentially zero
   before 1960, then 2 → 4 → 3 → 5 → 6 → 8%, peaking in the 2010s. It is also the more
   volatile in distance: median lookahead swings 12–49y on small n, so read the share, not
   the reach.

Caveats that still bite: the 1900s median lookback of 182y rests on a handful of targets;
`% forward` before 1960 is built on single-digit counts; and the corpus is still 38%
American, so the unfiltered numbers lean that way. The page prints the sample behind every
figure for this reason.

## 9. What is left

1. **Turn on GitHub Pages** — Settings → Pages → main → `/docs`. The repo is public and
   pushed; this is the only thing between the project and a public URL.
2. **Wikidata cross-check.** `validate.py` compares the model against *our own* hand labels.
   Both could be wrong the same way. Wikidata's narrative-time properties are the only
   genuinely independent reference in the plan, and the egress policy still blocks them.
   Until that runs, "90% agreement" is the honest claim, and "90% accurate" is not.
3. **Multi-timeline recall is the weakest number (77%).** A quarter of multi-timeline films
   flatten to one target, which biases the target count down and understates films like
   *The Godfather Part II*. Likely a prompt fix rather than a model one — the array
   instruction may need reinforcing.
4. **Atemporal/present boundary (78%).** The messiest confusion in the matrix. Worth deciding
   whether the rule needs sharpening or whether the hand labels were wrong on these.
5. **Rebalance the sample.** The corpus is fetched globally but sampled by vote count, so it
   is still 38% US. `--min-per-region` exists and has never been used in anger; a rerun with
   it would be a different and arguably better dataset.
6. **v4** — director/actor/company filters, which need a data-enrichment pass. See
   `claude/v4-ideas.md`.
