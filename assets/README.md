# assets/

## tmdb.svg — the TMDB logo (not committed by the build)

The page footer credits TMDB. TMDB's terms require the **official** logo file, used
unmodified — no recolouring, no change of aspect ratio, no redrawing. So this project
never draws an approximation of it. `src/build_viz.py` inlines `assets/tmdb.svg` if the
file is here, and otherwise falls back to a plain linked "TMDB" wordmark, which still
satisfies the required disclaimer wording.

To get the real logo in:

1. Open https://www.themoviedb.org/about/logos-attribution
2. Download a logo variant — the short/square blue mark suits a footer at 20px tall.
3. Save it here as `tmdb.svg`.
4. `make viz` — the build stops printing the "not found" note and inlines it.

The file is fetched by hand because TMDB is blocked by the egress policy on both the
cloud sandbox and the VM behind the Mac's folder access, the same firewall that forces
`tmdb_fetch.py` to be run from a normal terminal.

The required sentence, which the footer prints verbatim and which must not be reworded:

> This product uses the TMDB API but is not endorsed or certified by TMDB.
