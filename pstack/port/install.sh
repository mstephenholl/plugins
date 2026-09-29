#!/usr/bin/env bash
# Links the pstack skills that work outside Cursor into Claude Code and Codex.
# Idempotent: re-run after pulling upstream or editing SKILLS.
#
# The clone must live outside ~/.agents/skills. Codex scans that directory
# recursively and would load every pstack skill, ported or not.
set -euo pipefail

# Skills ported so far. Anything not listed stays uninstalled.
SKILLS=(
  blast-radius
  bro
  pstack-tdd
  technical-writing
  typescript-best-practices
  unslop
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

src="$(cd "$(dirname "$0")/../skills" && pwd)"
targets=("$HOME/.claude/skills" "$HOME/.codex/skills")
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

link_skill() {
  local name=$1 target=$2/$1
  if [[ -L $target && $(readlink "$target") == "$src/$name" ]]; then
    return 0
  fi
  if [[ -e $target || -L $target ]]; then
    echo "skip: $target already exists and is not a pstack link" >&2
    failed=1
    return 0
  fi
  ln -s "$src/$name" "$target"
  echo "linked $target"
}

# Removes links into this clone for skills no longer in SKILLS.
prune_links() {
  local dir=$1 link name
  for link in "$dir"/*; do
    [[ -L $link && $(readlink "$link") == "$src/"* ]] || continue
    name=$(basename "$link")
    [[ " ${SKILLS[*]} " == *" $name "* ]] && continue
    rm "$link"
    echo "unlinked $link"
  done
}

for name in "${SKILLS[@]}"; do
  if [[ ! -f $src/$name/SKILL.md ]]; then
    echo "missing: $src/$name/SKILL.md" >&2
    failed=1
    continue
  fi
  write_codex_policy "$src/$name"
done

for dir in "${targets[@]}"; do
  mkdir -p "$dir"
  prune_links "$dir"
  for name in "${SKILLS[@]}"; do
    [[ -f $src/$name/SKILL.md ]] && link_skill "$name" "$dir"
  done
done

exit $failed
