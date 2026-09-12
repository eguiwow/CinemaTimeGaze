import json, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
d = json.loads((ROOT/"data"/"targets.json").read_text())
tpl = (ROOT/"src"/"viz_template.html").read_text()

# genres and country ride on films only; targets join via film_id — keeps the payload small
slim_f = [{"id": f["id"], "year": f["year"], "gaze": f["gaze"],
           "g": f.get("genres") or [], "c": f.get("country"), "r": f.get("region")}
          for f in d["films"]]
slim_t = [{k: t[k] for k in ("film_id","title","release_year","year_start","year_end","year_mid",
                             "prominence","weight","delta","direction","confidence","basis")}
          for t in d["targets"]]
payload = {"films":slim_f, "targets":slim_t}
ys = [f["year"] for f in d["films"]]
kn = sum(1 for f in d["films"] if f["basis"]=="knowledge")

# ---- attribution -------------------------------------------------------------
# TMDB's terms require the disclaimer wording verbatim and the *official* logo file,
# unmodified. So the logo is never drawn here: it is inlined from assets/tmdb.svg if that
# file has been downloaded, and otherwise degrades to a plain linked wordmark, which is
# still compliant. An approximation of someone's trademark would not be.
# any assets/tmdb*.svg — the file keeps whatever name it was downloaded under
_logos = sorted((ROOT/"assets").glob("tmdb*.svg")) if (ROOT/"assets").exists() else []
LOGO_SRC = _logos[0] if _logos else None
if LOGO_SRC:
    logo = LOGO_SRC.read_text().strip()
    i = logo.find("<svg")                      # drop any xml prolog / doctype
    logo = logo[i:] if i > 0 else logo
    logo = ('<a href="https://www.themoviedb.org" target="_blank" rel="noopener noreferrer" '
            'aria-label="The Movie Database">' + logo + '</a>')
else:
    logo = ('<a class="wordmark" href="https://www.themoviedb.org" target="_blank" '
            'rel="noopener noreferrer">TMDB</a>')
    print("note: no assets/tmdb*.svg found — using a text wordmark for the TMDB credit.\n"
          "      Download the official logo from themoviedb.org/about/logos-attribution\n"
          "      and save it there; it will be inlined on the next build.")

# ---- who actually produced the loaded data -----------------------------------
n = len(d["films"])
is_tmdb = sum(1 for f in d["films"] if str(f["id"]).startswith("tmdb-")) > n / 2
countries = collections.Counter(f.get("country") for f in d["films"] if f.get("country"))
regions = collections.Counter(f.get("region") for f in d["films"] if f.get("region"))

if is_tmdb:
    top, topn = (countries.most_common(1) or [(None, 0)])[0]
    reach = (f"{len(countries)} national cinemas"
             + (f" across {len(regions)} regions" if len(regions) > 1 else "")
             ) if countries else "an unrecorded set of countries"
    skew = ""
    if top and topn / n > 0.35:
        skew = (f" It is not evenly spread: {top} alone is {round(100*topn/n)}% of it, so any "
                f"figure you read without picking an origin is mostly a statement about {top}.")
    source_note = (f"{n} films from <b>TMDB</b>, {min(ys)}–{max(ys)}, sampled per release "
                   f"year and ranked within the year by TMDB vote count, covering {reach}."
                   f"{skew} Vote count is a popularity measure, and popularity travels badly "
                   f"between film industries — a well-known film from a small country can sit "
                   f"below an obscure one from a large one.")
    data_credit = ('Film metadata — titles, release years, countries, genres and summaries — '
                   'comes from <a href="https://www.themoviedb.org" target="_blank" '
                   'rel="noopener noreferrer">TMDB</a>.')
else:
    source_note = (f"{n} films drawn from a Wikipedia-derived dataset of American cinema, "
                   f"{min(ys)}–{max(ys)}, sampled per release year and ranked by a crude "
                   f"notability proxy (length of the Wikipedia lead plus cast size). It is "
                   f"<b>American film only</b> — not \"cinema\".")
    data_credit = ('Film metadata in this build comes from a Wikipedia-derived dataset, not '
                   'from <a href="https://www.themoviedb.org" target="_blank" '
                   'rel="noopener noreferrer">TMDB</a>.')

html = (tpl.replace("__DATA__", json.dumps(payload, separators=(",",":")))
           .replace("__NFILMS__", str(n))
           .replace("__YMIN__", str(min(ys))).replace("__YMAX__", str(max(ys)))
           .replace("__KPCT__", str(round(100*kn/len(d["films"]))))
           .replace("__SOURCE_NOTE__", source_note)
           .replace("__DATA_CREDIT__", data_credit)
           .replace("__TMDB_LOGO__", logo))
# two outputs: the artifact body (no doctype - the Artifact tool supplies the skeleton)
# and a full standalone document you can open straight from disk.
out = ROOT/"out"/"timeline.html"
out.write_text(html)

i = html.index('<div class="root">')
doc = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
       '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
       '<style>html,body{margin:0;padding:0}</style>\n'
       + html[:i] + '</head>\n<body>\n' + html[i:] + '\n</body>\n</html>\n')
alone = ROOT/"out"/"standalone.html"
alone.write_text(doc)
print("wrote", out, f"{len(html)/1024:.0f} KB")
print("wrote", alone, f"{len(doc)/1024:.0f} KB  (open this one directly)")
