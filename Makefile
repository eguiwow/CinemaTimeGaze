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
