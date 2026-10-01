#!/usr/bin/env bash
# Installs pstack for Claude Code and Codex in one step:
#
#   curl -fsSL https://raw.githubusercontent.com/mstephenholl/pstack-claude-codex/main/pstack/port/bootstrap.sh | bash
#
# It clones the repository to ~/.local/share/pstack (or $PSTACK_HOME), checks
# out the newest release, links the `pstack` command into ~/.local/bin (or
# $PSTACK_BIN), and runs `pstack install`. Arguments pass through to install,
# for example `bash -s -- --codex` to install for Codex only. Rerunning it
# updates an existing clone.
#
# PSTACK_REPO and PSTACK_REF override the repository and the ref to check out.
set -euo pipefail

repo=${PSTACK_REPO:-https://github.com/mstephenholl/pstack-claude-codex.git}
home=${PSTACK_HOME:-$HOME/.local/share/pstack}

for tool in git python3; do
  command -v "$tool" >/dev/null || {
    echo "pstack needs $tool; install it and rerun." >&2
    exit 1
  }
done

case "$home/" in
  "$HOME/.agents/skills/"*)
    echo "PSTACK_HOME is inside ~/.agents/skills, which Codex scans recursively. Choose another location." >&2
    exit 1
    ;;
esac

if [[ -d $home/.git ]]; then
  echo "Updating the clone at $home"
  git -C "$home" fetch --quiet --tags origin
else
  echo "Cloning $repo into $home"
  mkdir -p "$(dirname "$home")"
  git clone --quiet "$repo" "$home"
fi

plain_tag='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
ref=${PSTACK_REF:-$(git -C "$home" tag --list 'v*' --sort=-version:refname | sed -nE "/$plain_tag/p" | head -1)}
if [[ -n $ref ]]; then
  git -C "$home" checkout --quiet "$ref"
else
  git -C "$home" merge --quiet --ff-only '@{u}' 2>/dev/null || true
fi

exec "$home/pstack/port/pstack" install "$@"
