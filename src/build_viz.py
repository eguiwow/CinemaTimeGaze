import json, collections
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
d = json.loads((ROOT/"data"/"targets.json").read_text())
tpl = (ROOT/"src"/"viz_template.html").read_text()

# This page has to open fast on a phone over a cold cache, so the payload is trimmed to
# what cannot be recomputed. Everything that is a property OF THE FILM lives on the film
# once; targets carry only what varies per target and are rehydrated on load.
#
#   film_id   -> an index into films (an int, not an 18-char string)
#   delta     -> year_mid - film.year, so it is not stored
#   direction -> a function of delta and PRESENT_TOL, so it is not stored
#   weight    -> omitted when 1.0, which is every single-timeline film
#   e/s       -> year_end / year_start, omitted when equal to year_mid, which is most films
#   prominence-> one letter
PRESENT_TOL = 2
PROM = {"primary": "p", "secondary": "s", "minor": "m"}

slim_f = [{"id": f["id"], "t": f["title"], "year": f["year"], "gaze": f["gaze"],
           "g": f.get("genres") or [], "c": f.get("country"), "r": f.get("region"),
           "cf": f.get("confidence"), "b": f.get("basis"), "v": f.get("votes") or 0,
           **({"ot": f["original_title"]} if f.get("original_title") else {})}
          for f in d["films"]]
fidx = {f["id"]: k for k, f in enumerate(slim_f)}

slim_t = []
for t in d["targets"]:
    o = {"f": fidx[t["film_id"]], "y": t["year_mid"]}
    if t["year_start"] != t["year_mid"]: o["s"] = t["year_start"]
    if t["year_end"]   != t["year_mid"]: o["e"] = t["year_end"]
    if abs(t["weight"] - 1.0) > 1e-9:    o["w"] = t["weight"]
    if t["prominence"] != "primary":     o["p"] = PROM[t["prominence"]]
    slim_t.append(o)

payload = {"tol": PRESENT_TOL, "films": slim_f, "targets": slim_t}
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

# The page's JS has a full ISO->name map; this caveat is built in Python and was
# printing the bare code ("mostly a statement about US"). Only the handful of
# countries that can realistically dominate a sample need naming here.
CNAME = {"US":"the United States","GB":"the United Kingdom","FR":"France","DE":"Germany",
         "IT":"Italy","ES":"Spain","JP":"Japan","IN":"India","CN":"China","KR":"South Korea",
         "RU":"Russia","CA":"Canada","BR":"Brazil","MX":"Mexico","AU":"Australia",
         "NL":"the Netherlands","SE":"Sweden","HK":"Hong Kong","TR":"Turkey","PH":"the Philippines"}
# "a statement about the United States cinema" is not English; the sentence needs the
# adjective, not the country name.
ADJ = {"US":"American","GB":"British","FR":"French","DE":"German","IT":"Italian",
       "ES":"Spanish","JP":"Japanese","IN":"Indian","CN":"Chinese","KR":"South Korean",
       "RU":"Russian","CA":"Canadian","BR":"Brazilian","MX":"Mexican","AU":"Australian",
       "NL":"Dutch","SE":"Swedish","HK":"Hong Kong","TR":"Turkish","PH":"Filipino"}
cname = lambda c: CNAME.get(c, c)
cadj  = lambda c: ADJ.get(c, CNAME.get(c, c))

# ---- measured accuracy -------------------------------------------------------
# validate.py scores the model labels against the independent hand-labelled set and
# writes data/validation.json. The page prints THAT, never a number typed into the
# template — hardcoding is how the corpus caveat ended up describing the wrong dataset
# for a fortnight. No file, no claim: the page says plainly that nothing was checked.
vpath = ROOT/"data"/"validation.json"
if vpath.exists():
    v = json.loads(vpath.read_text())
    g  = round(100*v["gaze_agreement"])
    y  = round(100*v["year_within_tol"]) if v.get("year_within_tol") is not None else None
    bb = v.get("by_basis") or {}
    kn_s = round(100*bb["knowledge"]["gaze"]) if "knowledge" in bb else None
    tx_s = round(100*bb["text"]["gaze"]) if "text" in bb else None
    split = ""
    if kn_s is not None and tx_s is not None:
        split = (f" That splits: <b>{tx_s}%</b> where the summary actually names a year, "
                 f"<b>{kn_s}%</b> where the model is going on what it knows about the film.")
    accuracy = (f"Checked against {v['n_overlap']} films labelled independently by hand, the "
                f"classifier puts a film in the right category <b>{g}%</b> of the time"
                + (f" and lands the depicted year within {v['tol']} years <b>{y}%</b> of the time"
                   if y is not None else "") + "." + split)
    accuracy_caveat = (
        f"<b>The labels have now been scored.</b> Against {v['n_overlap']} films labelled by hand "
        f"through a different process, the model agrees on gaze {g}% of the time"
        + (f" and on the depicted year (within {v['tol']}y) {y}% of the time" if y is not None else "")
        + ". "
        + (f"The split by basis is the interesting part: {tx_s}% where the source text states a "
           f"year against {kn_s}% where it does not, so the cheap model is meaningfully worse — "
           f"but not guessing — when the text is silent. " if kn_s is not None and tx_s is not None else "")
        + "This is agreement between two labelsets, not truth: both could be wrong the same way, "
          "and the hand set is itself a judgment call on the hard cases.")
else:
    accuracy = ("These labels have <b>not</b> been checked against any independent reference. "
                "Treat the shape as a hypothesis, not a measurement.")
    accuracy_caveat = ("<b>Nothing here has been validated.</b> The labels have not been scored "
                       "against an independent ground truth, so every figure is a hypothesis.")

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
        skew = (f" It is not evenly spread: {cname(top)} alone is {round(100*topn/n)}% of it, so "
                f"any figure you read without picking an origin is mostly a statement about "
                f"{cadj(top)} cinema.")
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
           .replace("__TMDB_LOGO__", logo)
           .replace("__ACCURACY__", accuracy)
           .replace("__ACCURACY_CAVEAT__", accuracy_caveat))
# Three outputs: the artifact body (no doctype — the Artifact tool supplies the
# skeleton), a standalone document to open straight from disk, and the published
# copy under docs/, which is what GitHub Pages serves.
# ---- full documents -----------------------------------------------------------
# SITE is where the published copy lives. Open Graph needs absolute URLs — a
# relative og:image is simply dropped by every scraper, which is how a shared
# link ends up looking bare.
SITE = "https://eguiwow.github.io/CinemaTimeGaze/"
DESC = ("Every film is made in one year and set in another. This measures the gap across "
        "a century of cinema: how far back films look, how much further ahead they reach "
        "when they do, and how that has moved decade by decade.")

i = html.index('<div class="root">')

def document(head_extra=""):
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            + head_extra +
            '<style>html,body{margin:0;padding:0}</style>\n'
            + html[:i] + '</head>\n<body>\n' + html[i:] + '\n</body>\n</html>\n')

out = ROOT/"out"/"timeline.html"
out.write_text(html)
alone = ROOT/"out"/"standalone.html"
alone.write_text(document())

social = f'''<meta name="description" content="{DESC}">
<meta name="author" content="Ander Eguiluz">
<meta name="theme-color" content="#fcfcfb" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1a1a19" media="(prefers-color-scheme: dark)">
<link rel="canonical" href="{SITE}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Cinema&#39;s Temporal Gaze">
<meta property="og:title" content="Cinema&#39;s Temporal Gaze">
<meta property="og:description" content="{DESC}">
<meta property="og:url" content="{SITE}">
<meta property="og:image" content="{SITE}preview.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="An arc chart fanning left into the past and right into the future from a single release decade.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Cinema&#39;s Temporal Gaze">
<meta name="twitter:description" content="{DESC}">
<meta name="twitter:image" content="{SITE}preview.png">
'''
docs = ROOT/"docs"
docs.mkdir(exist_ok=True)
(docs/"index.html").write_text(document(social))
(docs/".nojekyll").write_text("")          # stop Pages running Jekyll over it

print("wrote", out, f"{len(html)/1024:.0f} KB")
print("wrote", alone, f"{len(document())/1024:.0f} KB  (open this one directly)")
print("wrote", docs/"index.html", f"{len(document(social))/1024:.0f} KB  (what GitHub Pages serves)")
if not (docs/"preview.png").exists():
    print("note: docs/preview.png missing — the social card will be blank until it is generated.")
