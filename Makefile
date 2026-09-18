CORPUS = data/movies.json
URL = https://raw.githubusercontent.com/prust/wikipedia-movie-data/master/movies.json
LABELS ?= labels_tmdb.jsonl

.PHONY: data sample targets viz all clean-out
.PHONY: tmdb-check tmdb-plan tmdb tmdb-flatten tmdb-sample labels tmdb-regions tmdb-global tmdb-plan-global classify classify-cli whoami cli-probe validate nightly ci-check

# --- TMDB corpus ---
# COUNTRIES is a region preset, an ISO list, or a mix — `make tmdb-regions` prints them.
COUNTRIES ?= west

tmdb-check:   ; python3 src/tmdb_fetch.py --check
tmdb-regions: ; python3 src/tmdb_fetch.py --list-regions
tmdb-plan:    ; python3 src/tmdb_fetch.py --countries $(COUNTRIES) --dry-run
tmdb:         ; python3 src/tmdb_fetch.py --countries $(COUNTRIES)
tmdb-flatten: ; python3 src/tmdb_fetch.py --flatten-only

# the whole curated world rather than the original 16. Same cache, so it only fetches
# what is missing, and it gives up on each country once its archive runs dry.
tmdb-global:      ; $(MAKE) tmdb COUNTRIES=global
tmdb-plan-global: ; $(MAKE) tmdb-plan COUNTRIES=global

# PER_REGION reserves slots per region per year before popularity gets a say. 0 keeps the
# pure vote-count ranking, which on a global corpus means a mostly-American sample.
PER_REGION ?= 0

# always re-flatten before sampling: sampling a stale jsonl is the easy mistake
tmdb-sample: tmdb-flatten
	python3 src/sample_tmdb.py 50 --min-per-region $(PER_REGION)
	$(MAKE) labels

# fold the re-derived old labels together with labels made against this corpus
labels:
	python3 src/map_labels.py
	python3 src/merge_labels.py --out labels_tmdb.jsonl labels_mapped.jsonl labels_tmdb.jsonl

classify:
	python3 src/classify_api.py --per-year 50

# same job on your Claude Code subscription instead of API credit.
# CLAUDE_BIN must be the real binary - a shell alias is invisible to a subprocess.
CLAUDE_BIN ?= claude
ACCOUNT_DIR ?= $(HOME)/.claude-ander

classify-cli:
	python3 src/classify_api.py --per-year 50 --backend cli \
	  --cli-cmd "$$(command -v $(CLAUDE_BIN))" --claude-config-dir "$(ACCOUNT_DIR)"

nightly:
	./scripts/nightly_classify.sh

whoami:
	python3 src/classify_api.py --whoami

cli-probe:
	python3 src/classify_api.py --cli-probe \
	  --cli-cmd "$$(command -v $(CLAUDE_BIN))" --claude-config-dir "$(ACCOUNT_DIR)"

# --json writes data/validation.json, which build_viz.py reads so the page prints
# the measured accuracy rather than a number typed into the template.
validate:
	python3 src/validate.py data/labels_tmdb.jsonl data/labels_api.jsonl --by-basis --json

targets:
	python3 src/build_targets.py --labels $(LABELS)

viz:
	python3 src/build_viz.py

# --- offline Wikipedia corpus ---
data: $(CORPUS)
$(CORPUS):
	@mkdir -p data
	curl -sSLo $@ $(URL)
	@echo "fetched $$(du -h $@ | cut -f1)"

sample: $(CORPUS)
	python3 src/sample.py 50 1900

all: targets viz

clean-out:
	rm -f out/*.html

# Reproduce the Pages workflow against a CLEAN CLONE, which is what the runner
# actually gets. The first CI run failed because out/ and docs/ are gitignored and
# the build assumed a tree that had been built in before — it passed locally and
# died on the runner. Run this before pushing a build change.
ci-check:
	@set -e; d=$$(mktemp -d); \
	 git clone -q . $$d; cd $$d; \
	 echo "--- clean clone, building as CI does ---"; \
	 python3 src/build_viz.py; \
	 test -f docs/index.html   || { echo "FAIL: no docs/index.html"; exit 1; }; \
	 test -f docs/preview.png  || { echo "FAIL: no social card"; exit 1; }; \
	 test -f docs/.nojekyll    || { echo "FAIL: no .nojekyll"; exit 1; }; \
	 grep -q 'const DATA = {' docs/index.html || { echo "FAIL: no data inlined"; exit 1; }; \
	 test -d docs/fonts && test "$$(ls docs/fonts/*.woff2 | wc -l)" -ge 8 \
	   || { echo "FAIL: fonts missing"; exit 1; }; \
	 ! grep -q 'fonts.googleapis.com\|fonts.gstatic.com' docs/index.html \
	   || { echo "FAIL: external font request"; exit 1; }; \
	 echo "OK  $$(du -h docs/index.html | cut -f1) raw, $$(gzip -9 -c docs/index.html | wc -c | awk '{printf "%d KB", $$1/1024}') gzipped"; \
	 rm -rf $$d

# ============================================================================
# v4
# ============================================================================
.PHONY: test credits-check credits-plan credits-fetch credits credits-report corpus-db corpus-stats
.PHONY: wishlist-sync wishlist-resolve wishlist-apply wishlist-status wishlist-comment tmdb-sample-intl sample-report
.PHONY: weak-rows boundary-rows plots pass2 merge-labelsets validate-history handlabel-export handlabel-import

test: ; python3 -m unittest discover -s tests

# --- items 1-3: credits for the Advanced filters (run from a normal Terminal) ---
credits-check:  ; python3 src/tmdb_credits.py --check
credits-plan:   ; python3 src/tmdb_credits.py --dry-run
credits-fetch:  ; python3 src/tmdb_credits.py
credits:        ; python3 src/build_credits.py            # -> data/credits.json (committed)
credits-report: ; python3 src/build_credits.py --report   # who clears 25; unmapped companies

# --- item 4: build-side database. Flat files are still what gets served. ---
corpus-db:    ; python3 src/corpus_db.py build
corpus-stats: ; python3 src/corpus_db.py stats

# --- item 6: the wishlist loop. GitHub issues in, sample rows (source=wishlist) out. ---
wishlist-sync:    ; python3 src/wishlist.py sync
wishlist-resolve: ; python3 src/wishlist.py resolve
wishlist-apply:   ; python3 src/wishlist.py apply
wishlist-status:  ; python3 src/wishlist.py status
wishlist-comment: ; python3 src/wishlist.py comment

# --- item 7: international sampler. RANK=votes keeps v3's behaviour exactly. ---
# Recommended after the grid: RANK=in-country PER_REGION=5 (keep PER_REGION <= 50/regions).
RANK ?= votes
tmdb-sample-intl: tmdb-flatten
	python3 src/sample_tmdb.py 50 --rank $(RANK) --min-per-region $(PER_REGION)
	$(MAKE) labels

# CANDIDATE is a sample.json-shaped file to compare against data/sample.json
sample-report: ; python3 src/sample_report.py $(CANDIDATE)

# --- item 8: chase the low-confidence years. Never overwrites a labelset. ---
weak-rows:     ; python3 src/weak_rows.py
boundary-rows: ; python3 src/weak_rows.py --mode boundary
plots:         ; python3 src/fetch_plots.py --ids data/weak_ids.txt

# second pass on the weak rows only, on the personal account like classify-cli.
# PASS2_IDS=data/boundary_hand_ids.txt PROMPT=v2 is the cheap prompt experiment.
PASS2_IDS   ?= data/weak_ids.txt
PASS2_OUT   ?= data/labels_pass2.jsonl
PASS2_MODEL ?= sonnet
PROMPT      ?= v1
pass2:
	python3 src/classify_api.py --ids $(PASS2_IDS) --prompt $(PROMPT) \
	  $(if $(wildcard data/plots.jsonl),--plots data/plots.jsonl --words 250,) \
	  --model $(PASS2_MODEL) --out $(PASS2_OUT) --labelset pass2 \
	  --backend cli --cli-cmd "$$(command -v $(CLAUDE_BIN))" --claude-config-dir "$(ACCOUNT_DIR)"

# labels_merged.jsonl (hand wins) is what a build can use; labels_model.jsonl leaves the hand
# set out and is the one to validate — scoring hand labels against themselves proves nothing.
merge-labelsets:
	python3 src/merge_labelsets.py --report
	python3 src/merge_labelsets.py --no-hand --out data/labels_model.jsonl

# NAME is required: it is the permanent label of this entry in data/validation_history.json
validate-history:
	@test -n "$(NAME)" || { echo 'usage: make validate-history NAME="pass2 (plots + v2)"'; exit 1; }
	python3 src/validate.py data/labels_tmdb.jsonl data/labels_model.jsonl --by-basis --json \
	  --history "$(NAME)"

handlabel-export: ; python3 src/handlabel_queue.py export --n $${N:-100}
handlabel-import: ; python3 src/handlabel_queue.py import $(CSV)
