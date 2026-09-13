# assets/fonts/

Self-hosted web fonts. The page makes **no external requests**; it previously loaded
these three families from `fonts.googleapis.com`, which sent every visitor's IP to a
third party.

All three are **OFL-1.1**, which permits redistribution provided the licence travels with
the font. `LICENSE-*.txt` here are those licences, and `build_viz.py` copies them into
`docs/fonts/` next to the `.woff2` files.

`faces.json` is the manifest the build reads: family, weight, style, filename and the
`unicode-range` for each face. The ranges are **Fontsource's own**, lifted from its
generated CSS rather than typed by hand.

## Regenerating

Run anywhere with npm and network access (the files come from the npm registry, not from
Google):

```bash
mkdir -p /tmp/f && cd /tmp/f
for p in ibm-plex-sans ibm-plex-mono instrument-serif; do
  npm pack "@fontsource/$p" >/dev/null && tar xzf "fontsource-$p-"*.tgz && mv package "$p"
done
```

Then run the extractor below, adjusting `SPEC` for the faces you want, and copy the
resulting `.woff2` files plus `faces.json` into this directory.

```python
import re, os, shutil, json
SPEC = [  # (package dir, CSS family name, [(weight, style, [subsets])])
 ("instrument-serif", "Instrument Serif",
   [(400,"normal",["latin"]), (400,"italic",["latin"])]),
 ("ibm-plex-sans", "IBM Plex Sans",
   [(400,"normal",["latin","latin-ext"]), (400,"italic",["latin","latin-ext"]),
    (500,"normal",["latin","latin-ext"]), (600,"normal",["latin","latin-ext"])]),
 ("ibm-plex-mono", "IBM Plex Mono",
   [(400,"normal",["latin"]), (500,"normal",["latin"])]),
]
faces = []
for pkg, fam, want in SPEC:
    for w, style, subsets in want:
        css = f"{pkg}/{w}{'-italic' if style=='italic' else ''}.css"
        for block in re.findall(r"@font-face\s*\{.*?\}", open(css).read(), re.S):
            m = re.search(r"url\(\./files/([^)]+\.woff2)\)", block)
            if not m: continue
            fn = m.group(1)
            suffix = 'italic' if style == 'italic' else 'normal'
            if not any(fn == f"{pkg}-{s}-{w}-{suffix}.woff2" for s in subsets): continue
            ur = re.search(r"unicode-range:\s*([^;]+);", block)
            shutil.copy(f"{pkg}/files/{fn}", fn)
            faces.append({"family": fam, "weight": w, "style": style, "file": fn,
                          "unicode_range": ur.group(1).strip() if ur else None,
                          "bytes": os.path.getsize(fn)})
json.dump(faces, open("faces.json", "w"), indent=1)
print(f"{len(faces)} faces, {sum(f['bytes'] for f in faces)/1024:.1f} KB")
```

## Why these subsets

`latin-ext` is on **IBM Plex Sans only**. Search results list film titles in the body
font, and the corpus carries Polish, Czech, Hungarian, Turkish and Romanian titles that
latin-only would drop to a fallback face mid-list. Display and mono never render those
strings, so they stay latin-only.

Cyrillic, Greek, CJK and Korean titles fall back to system faces in every case — none of
these three families covers them, and shipping subsets that do would cost far more than
it buys.
