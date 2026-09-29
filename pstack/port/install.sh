#!/usr/bin/env bash
# Links the pstack skills that work outside Cursor into Claude Code and Codex.
# Idempotent: re-run after pulling upstream or editing the lists below.
#
# The clone must live outside ~/.agents/skills. Codex scans that directory
# recursively and would load every pstack skill, ported or not.
set -euo pipefail

# pstack skills ported so far. Anything not listed stays uninstalled.
# pstack-harness is read by path through ~/.agents/pstack, so it is not linked.
SKILLS=(
  architect
  arena
  blast-radius
  bro
  figure-it-out
  how
  interrogate
  no-comments
  poteto-mode
  pstack-tdd
  pstack-teach
  setup-pstack
  show-me-your-work
  swarm
  technical-writing
  typescript-best-practices
  unslop
  why
  principle-attack-the-premise
  principle-boundary-discipline
  principle-build-the-lever
  principle-encode-lessons-in-structure
  principle-exhaust-the-design-space
  principle-experience-first
  principle-fix-root-causes
  principle-foundational-thinking
  principle-guard-the-context-window
  principle-laziness-protocol
  principle-make-operations-idempotent
  principle-migrate-callers-then-delete-legacy-apis
  principle-minimize-reader-load
  principle-model-the-domain
  principle-never-block-on-the-human
  principle-outcome-oriented-execution
  principle-prove-it-works
  principle-redesign-from-first-principles
  principle-separate-before-serializing-shared-state
  principle-sequence-verifiable-units
  principle-subtract-before-you-add
  principle-test-behavior-not-implementation
  principle-type-system-discipline
)

# cursor-team-kit skills that poteto-mode calls. They need no porting.
# A sparse clone needs `git sparse-checkout add cursor-team-kit`.
TEAM_KIT_SKILLS=(
  control-cli
  control-ui
  deslop
)

# pstack subagents. Claude Code only; Codex spawns them per pstack-harness.
AGENTS=(
  comment-sicko
  poteto-agent
)

pstack="$(cd "$(dirname "$0")/.." && pwd)"
root="$(dirname "$pstack")"
skill_targets=("$HOME/.claude/skills" "$HOME/.codex/skills")
agent_target="$HOME/.claude/agents"
failed=0

# Codex ignores `disable-model-invocation` and reads agents/openai.yaml instead.
write_codex_policy() {
  local dir=$1 policy=$1/agents/openai.yaml
  grep -q '^disable-model-invocation: true' "$dir/SKILL.md" || return 0
  if [[ -e $policy ]]; then
    grep -q 'allow_implicit_invocation: false' "$policy" ||
      echo "warn: $policy exists without allow_implicit_invocation: false" >&2
    return 0
  fi
  mkdir -p "$dir/agents"
  printf 'policy:\n  allow_implicit_invocation: false\n' >"$policy"
}

# link <source> <link path>
link() {
  local source=$1 target=$2
  if [[ -L $target && $(readlink "$target") == "$source" ]]; then
    return 0
  fi
  if [[ -e $target || -L $target ]]; then
    echo "skip: $target already exists and is not a pstack link" >&2
    failed=1
    return 0
  fi
  ln -s "$source" "$target"
  echo "linked $target"
}

# Removes links into this clone whose names are not in the given list.
# prune <dir> <suffix> <name>...
prune() {
  local dir=$1 suffix=$2 link name
  shift 2
  for link in "$dir"/*; do
    [[ -L $link && $(readlink "$link") == "$root/"* ]] || continue
    name=$(basename "$link" "$suffix")
    [[ " $* " == *" $name "* ]] && continue
    rm "$link"
    echo "unlinked $link"
  done
}

sources=()
for name in "${SKILLS[@]}"; do sources+=("$pstack/skills/$name"); done
for name in "${TEAM_KIT_SKILLS[@]}"; do sources+=("$root/cursor-team-kit/skills/$name"); done

installed=()
for dir in "${sources[@]}"; do
  if [[ ! -f $dir/SKILL.md ]]; then
    echo "missing: $dir/SKILL.md" >&2
    failed=1
    continue
  fi
  write_codex_policy "$dir"
  installed+=("$dir")
done

link "$pstack" "$HOME/.agents/pstack"

for target in "${skill_targets[@]}"; do
  mkdir -p "$target"
  prune "$target" "" "${SKILLS[@]}" "${TEAM_KIT_SKILLS[@]}"
  for dir in "${installed[@]}"; do
    link "$dir" "$target/$(basename "$dir")"
  done
done

mkdir -p "$agent_target"
prune "$agent_target" .md "${AGENTS[@]}"
for name in "${AGENTS[@]}"; do
  link "$pstack/agents/$name.md" "$agent_target/$name.md"
done

exit $failed
