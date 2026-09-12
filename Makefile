CORPUS = data/movies.json
URL = https://raw.githubusercontent.com/prust/wikipedia-movie-data/master/movies.json
LABELS ?= labels_tmdb.jsonl

.PHONY: data sample targets viz all clean-out
.PHONY: tmdb-check tmdb-plan tmdb tmdb-flatten tmdb-sample labels tmdb-regions tmdb-global tmdb-plan-global classify classify-cli whoami cli-probe validate nightly

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

validate:
	python3 src/validate.py data/labels_tmdb.jsonl data/labels_api.jsonl --by-basis

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
