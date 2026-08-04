#!/usr/bin/env bash
set -euo pipefail

repository_dir="${1:-premarket-history-store}"
history_subdir="${2:-history}"
history_branch="${3:-premarket-history}"

if [[ ! -d "$repository_dir/.git" ]]; then
  echo "durable history workspace is not a Git repository" >&2
  exit 2
fi

if git -C "$repository_dir" ls-remote --exit-code --heads origin \
  "refs/heads/$history_branch" >/dev/null 2>&1; then
  git -C "$repository_dir" fetch --quiet origin \
    "+refs/heads/$history_branch:refs/remotes/origin/$history_branch"
  if git -C "$repository_dir" show-ref --verify --quiet \
    "refs/heads/$history_branch"; then
    git -C "$repository_dir" switch --quiet "$history_branch"
    git -C "$repository_dir" reset --hard --quiet "origin/$history_branch"
  else
    git -C "$repository_dir" switch --quiet --create "$history_branch" \
      --track "origin/$history_branch"
  fi
  exit 0
fi

# A repeated invocation in the same workspace must reuse the local branch
# instead of attempting to create a second orphan branch.
if git -C "$repository_dir" show-ref --verify --quiet \
  "refs/heads/$history_branch"; then
  git -C "$repository_dir" switch --quiet "$history_branch"
else
  git -C "$repository_dir" switch --orphan "$history_branch"
  git -C "$repository_dir" rm -rf --ignore-unmatch .
  git -C "$repository_dir" clean -fdx
fi

mkdir -p "$repository_dir/$history_subdir"
touch "$repository_dir/$history_subdir/.gitkeep"
git -C "$repository_dir" add "$history_subdir/.gitkeep"

if ! git -C "$repository_dir" diff --cached --quiet; then
  git -C "$repository_dir" config user.name "github-actions[bot]"
  git -C "$repository_dir" config user.email \
    "41898282+github-actions[bot]@users.noreply.github.com"
  git -C "$repository_dir" commit --quiet \
    -m "Initialize durable premarket history"
  git -C "$repository_dir" push --quiet --set-upstream origin \
    "$history_branch"
fi
