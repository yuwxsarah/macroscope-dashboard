#!/usr/bin/env bash
set -euo pipefail

remote="${1:-origin}"
branch="${2:-main}"
max_attempts="${3:-3}"

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Keep the freshly generated artifacts outside the checkout. If main advances
# while this workflow is running, start from that new main and overlay only the
# generated data/site. Source changes from the concurrent push are preserved,
# while generated-file rebase conflicts cannot discard this refresh.
snapshot_dir="$(mktemp -d)"
trap 'rm -rf "$snapshot_dir"' EXIT
mkdir -p "$snapshot_dir/data" "$snapshot_dir/public"
cp -a data/. "$snapshot_dir/data/"
cp -a public/. "$snapshot_dir/public/"

for attempt in $(seq 1 "$max_attempts"); do
  echo "Sync and push attempt ${attempt}/${max_attempts}"
  git fetch "$remote" "$branch"
  git reset --hard "$remote/$branch"

  mkdir -p data public
  cp -a "$snapshot_dir/data/." data/
  cp -a "$snapshot_dir/public/." public/
  git add data public

  if git diff --cached --quiet; then
    echo "No refreshed files to commit."
    exit 0
  fi

  git commit -m "chore: refresh public dashboard data [skip ci]"

  if git push "$remote" "HEAD:$branch"; then
    echo "Push succeeded."
    exit 0
  fi

  echo "Remote changed again before push; rebuilding the data commit on the latest ${branch}."
  sleep $((attempt * 10))
done

echo "::error::Push failed after ${max_attempts} attempts."
exit 1
