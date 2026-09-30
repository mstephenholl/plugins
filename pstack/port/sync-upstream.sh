#!/usr/bin/env bash
# Merges upstream cursor/plugins into the port branch and hands the result to
# a person on GitHub: a pull request when merge-upstream.py resolves
# everything, or an issue when it leaves conflicts. The scheduled workflow
# .github/workflows/pstack-upstream-sync.yml runs it.
#
#   DRY_RUN=1 sync-upstream.sh   prints the pushes and GitHub writes instead of making them
#
# Needs git, gh (authenticated), jq, and python3. Run it from a clean checkout
# of the port branch with full history.
set -euo pipefail

base=${BASE_BRANCH:-claude-codex}
branch=${SYNC_BRANCH:-upstream-sync}
root="$(cd "$(dirname "$0")/../.." && pwd)"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cd "$root"

write() {
  if [[ ${DRY_RUN:-} == 1 ]]; then
    echo "DRY_RUN: $*"
  else
    "$@"
  fi
}

remote=$(git remote -v | awk '$2 ~ /github\.com[:\/]cursor\/plugins(\.git)?$/ {print $1; exit}')
if [[ -z $remote ]]; then
  remote=upstream
  git remote add "$remote" https://github.com/cursor/plugins.git
fi
git fetch --quiet "$remote" main
upstream=$(git rev-parse "$remote/main")

open_pr=$(gh pr list --head "$branch" --base "$base" --state open --json number --jq '.[0].number // empty')
if [[ -n $open_pr ]] && git fetch --quiet origin "$branch" 2>/dev/null &&
  git merge-base --is-ancestor "$upstream" "origin/$branch"; then
  echo "Pull request #$open_pr already carries upstream ${upstream:0:12}."
  exit 0
fi

python3 pstack/port/merge-upstream.py --no-fetch --report "$work/report.json" | tee "$work/merge.log" || true
status=$(jq -r .status "$work/report.json")
echo "status: $status"

body() {
  python3 - "$work/report.json" "$1" <<'PY'
import json, sys
report, kind = json.load(open(sys.argv[1])), sys.argv[2]
lines = [f"Upstream: cursor/plugins@{report['incoming'][:12]}", ""]
if report.get("dropped"):
    lines.append(f"Dropped {report['dropped']} upstream paths outside the port.")
if report.get("resolved"):
    lines += ["", "Resolved by restoring the pstack-harness pointer:"] + [f"- `{p}`" for p in report["resolved"]]
if report.get("manual"):
    lines += ["", "## Conflicts that need a person"] + [f"- `{p}`" for p in report["manual"]]
if report.get("to_port"):
    lines += ["", "## Needs porting"] + [f"- {item}" for item in report["to_port"]]
else:
    lines += ["", "Nothing needs porting."]
if kind == "pr":
    lines += ["", "Merging records upstream progress, so the next sync only sees newer changes. "
              "CI runs on this branch; run `pstack/port/smoke/smoke.py` before merging if a ported skill changed."]
else:
    lines += ["", "To resolve, run `pstack/port/merge-upstream.py` in a clone of this branch, fix the files above, "
              "commit the merge, and push. The next sync then opens a pull request for anything newer."]
print("\n".join(lines))
PY
}

close_conflict_issue() {
  local issue
  issue=$(gh issue list --label upstream-sync --state open --json number --jq '.[0].number // empty')
  if [[ -n $issue ]]; then
    write gh issue close "$issue" --comment "Upstream ${upstream:0:12} now merges without a person."
  fi
}

case $status in
up_to_date)
  echo "Already up to date with upstream ${upstream:0:12}."
  close_conflict_issue
  ;;
merged)
  git commit --quiet -m "Merge upstream cursor/plugins ${upstream:0:12}"
  write git push --force origin "HEAD:refs/heads/$branch"
  body pr >"$work/body.md"
  cat "$work/body.md"
  if [[ -n $open_pr ]]; then
    write gh pr edit "$open_pr" --body-file "$work/body.md"
  else
    write gh pr create --base "$base" --head "$branch" --title "Merge upstream cursor/plugins" --body-file "$work/body.md"
  fi
  if [[ $(jq '.to_port | length' "$work/report.json") -gt 0 ]]; then
    write gh label create needs-porting --color d93f0b --description "Upstream changes that need porting" --force
    write gh pr edit "$branch" --add-label needs-porting
  fi
  # A pull request opened with the workflow token does not trigger other workflows, so start CI directly.
  write gh workflow run pstack-ci.yml --ref "$branch"
  close_conflict_issue
  ;;
conflicts)
  git merge --abort
  body issue >"$work/body.md"
  cat "$work/body.md"
  write gh label create upstream-sync --color fbca04 --description "Upstream merges that need a person" --force
  issue=$(gh issue list --label upstream-sync --state open --json number --jq '.[0].number // empty')
  if [[ -n $issue ]]; then
    write gh issue edit "$issue" --body-file "$work/body.md"
  else
    write gh issue create --title "Upstream cursor/plugins needs a manual merge" --label upstream-sync --body-file "$work/body.md"
  fi
  ;;
*)
  echo "merge-upstream.py did not report a status" >&2
  exit 1
  ;;
esac
