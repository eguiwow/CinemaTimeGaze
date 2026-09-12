#!/usr/bin/env bash
# Publish CinemaTimeGaze: safety-check the history, create the GitHub repo,
# push, and turn on Pages from main /docs.
#
# Run from a NORMAL macOS Terminal — the sandboxed shell Claude uses has no
# gh and no credentials. Safe to re-run: every step is skipped if already done.
#
#   ./scripts/publish.sh            # public repo
#   VISIBILITY=private ./scripts/publish.sh
set -euo pipefail

REPO="${REPO:-eguiwow/CinemaTimeGaze}"
VISIBILITY="${VISIBILITY:-public}"
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

say "0. Preflight"
command -v gh >/dev/null || die "gh is not installed — brew install gh"
gh auth status || die "gh is not signed in — run: gh auth login"
ACCOUNT=$(gh api user -q .login)
echo "signed in as: $ACCOUNT"
[ "$ACCOUNT" = "${REPO%%/*}" ] || \
  echo "  WARNING: signed in as '$ACCOUNT' but pushing to '${REPO%%/*}'. Ctrl-C now if that is wrong."

say "1. The API key must not be in the history"
if git log --all --full-history --oneline -- src/.env .env | grep -q .; then
  die "src/.env appears in the git history. Do NOT push. Scrub it first."
fi
echo "  ok: src/.env never committed"

LEAKED=0
while IFS='=' read -r k v; do
  case "$k" in ''|\#*) continue ;; esac
  v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
  [ "${#v}" -ge 12 ] || continue
  if git grep -I -q -F -e "$v" $(git rev-list --all) -- 2>/dev/null; then
    echo "  LEAK: the value of $k appears somewhere in the history"; LEAKED=1
  fi
done < src/.env
[ "$LEAKED" -eq 0 ] || die "a secret value is in the history. Do NOT push."
echo "  ok: no secret value from src/.env appears in any commit"

say "2. Page is built from the current data"
make viz >/dev/null
if ! git diff --quiet -- docs/ ; then
  git add docs/ && git commit -q -m "rebuild published page"
  echo "  committed a rebuilt docs/index.html"
else
  echo "  ok: docs/ already current"
fi

say "3. Create the repo and push"
if gh repo view "$REPO" >/dev/null 2>&1; then
  echo "  repo exists; pushing"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$REPO.git"
  git push -u origin main
else
  gh repo create "$REPO" --"$VISIBILITY" --source=. --remote=origin --push \
    --description "Measuring the gap between the year a film was released and the year it is set in, across a century of cinema."
fi

say "4. Turn on GitHub Pages (main /docs)"
if gh api "repos/$REPO/pages" >/dev/null 2>&1; then
  echo "  Pages already configured"
else
  gh api -X POST "repos/$REPO/pages" \
    -f 'source[branch]=main' -f 'source[path]=/docs' >/dev/null && echo "  Pages enabled"
fi

say "5. Verify"
URL=$(gh api "repos/$REPO/pages" -q .html_url 2>/dev/null || echo "")
echo "  repo:  https://github.com/$REPO"
echo "  pages: ${URL:-(building — check Settings -> Pages in a minute)}"
echo
echo "  First build takes a minute or two. Then check:"
echo "    curl -sI \"${URL:-https://eguiwow.github.io/CinemaTimeGaze/}\" | head -1"
echo "    open \"${URL:-https://eguiwow.github.io/CinemaTimeGaze/}\""
echo
echo "  And confirm the repo really has no key in it:"
echo "    gh api repos/$REPO/contents/src --jq '.[].name'   # must NOT list .env"
