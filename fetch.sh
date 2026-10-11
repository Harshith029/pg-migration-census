#!/usr/bin/env bash
# Fetch only the migration directories listed in manifest.csv, each at its pinned commit.
#
#   bash fetch.sh [output-dir]        # default: ./repos
#
# Needs git and python3. Uses a blobless, depth-1 fetch of the exact commit plus a sparse
# checkout of the migrations directory (and extra_dirs, used only for Windmill's extras), so each
# project costs a few MB, not a full clone. Safe to re-run: a project already at its pinned
# commit only has its sparse paths re-applied.
set -euo pipefail
export GIT_TERMINAL_PROMPT=0

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="${1:-$here/repos}"
mkdir -p "$out"

python3 -I - "$here/manifest.csv" > "$out/.manifest.tsv" <<'PY'
import csv, sys
for r in csv.DictReader(open(sys.argv[1], newline="")):
    dirs = "|".join([r["migrations_dir"]] + [d for d in r.get("extra_dirs", "").split("|") if d])
    print("\t".join([r["project"], r["repo_url"], r["commit"], dirs]))
PY

while IFS=$'\t' read -r project repo commit dirs; do
  d="$out/${project//\//__}"
  IFS='|' read -ra paths <<< "$dirs"
  if [ -d "$d/.git" ] && [ "$(git -C "$d" rev-parse HEAD 2>/dev/null || true)" = "$commit" ]; then
    git -C "$d" sparse-checkout set "${paths[@]}"
    git -C "$d" checkout -q "$commit"
    echo "ok (already at pinned commit)  $project"
    continue
  fi
  rm -rf "$d"
  mkdir -p "$d"
  git -C "$d" init -q
  git -C "$d" remote add origin "$repo"
  git -C "$d" fetch -q --depth 1 --filter=blob:none origin "$commit"
  git -C "$d" sparse-checkout init --cone
  git -C "$d" sparse-checkout set "${paths[@]}"
  git -C "$d" checkout -q FETCH_HEAD
  echo "fetched                        $project @ ${commit:0:10}"
done < "$out/.manifest.tsv"
rm -f "$out/.manifest.tsv"
echo "done: $out"
