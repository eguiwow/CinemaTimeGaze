# Licensing — code and data are not the same thing

## Code — MIT
Everything under `src/`, `scripts/` and the `Makefile` is MIT licensed. See `LICENSE`.

## This project's own results — CC BY 4.0
`data/labels*.jsonl` and `data/targets.json` are what this project *produced*: a
classification of each film's depicted year(s), and the weighted target table derived
from it. They are released under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — reuse them, with credit.

They are committed deliberately. The whole argument of this project is a set of numbers,
and numbers nobody can check are a blog post, not a result.

## TMDB's content — not redistributed
The film metadata this is built on comes from [TMDB](https://www.themoviedb.org).
Titles, release years, countries and genres are facts and travel with the results above.
**TMDB's plot summaries do not.** `data/tmdb_films.jsonl`, `data/tmdb_raw/` and
`data/sample.json` all carry TMDB `overview` text and are therefore gitignored rather
than committed — TMDB's terms cover *using* their API, not republishing a bulk copy of
their corpus.

This costs nothing in reproducibility: `make tmdb` + `make tmdb-sample` regenerates all
three from the API with your own key, and the film ids in `data/labels*.jsonl` are TMDB
ids, so the join is exact.

> This product uses the TMDB API but is not endorsed or certified by TMDB.
