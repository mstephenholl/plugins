---
name: setup-pstack
description: Configure which models pstack uses per role and at what reasoning budget, across Claude Code and Codex. Detects your available models and writes ~/.agents/pstack-models.md, which overrides the skill defaults. Use for /setup-pstack, "configure pstack models", "pstack budget", or changing pstack's model choices.
---

# Setup pstack

Write `~/.agents/pstack-models.md`, the per-role model config every pstack skill reads in both Claude Code and Codex. First read `~/.agents/pstack/skills/pstack-harness/SKILL.md` for the entry syntax, how each role resolves to a native subagent or a foreign CLI seat, and the question tool for your harness.

## Steps

### 1. Detect available models

- Codex. Run `codex debug models`. It prints the catalog as JSON. Keep each model whose `visibility` is `list`, with the efforts in its `supported_reasoning_levels`.
- Claude Code. The aliases `opus`, `sonnet`, `haiku`, and `fable`. Efforts are the `--effort` choices `claude --help` lists.
- Check `command -v codex` and `command -v claude`. A harness whose CLI is missing cannot serve foreign seats. Mark its entries as needing a choice.

Never write a model you have not confirmed. `inherit-parent` and `auto` are always valid.

### 2. Load current state

The defaults are the file shape in step 5. If `~/.agents/pstack-models.md` exists, read it and treat its `# budget` line and role lines as the current choices. A line whose role is not in step 5 is from a retired role. Drop it.

### 3. Budget, map, and confirm

**(a) Ask for a budget.** Use your harness's question tool. Offer these four options with these exact labels, and name the current budget when the file records one.

- `unlimited — keep max`
- `large — xhigh reasoning`
- `medium — high reasoning`
- `small — medium reasoning`

**(b) Apply it.** Start from the step 5 defaults, and on a re-run keep any role the user changed. Set every entry's effort to `@max`, `@xhigh`, `@high`, or `@medium` for the four budgets in order. If a Codex model does not support that effort, use its highest supported effort below it. `inherit-parent` and `auto` do not change.

**(c) Show the roles and confirm.** Show every role with its entries, mark any unconfirmed entry as needing a choice, and list each line step 2 dropped. Ask whether to accept as-is or change specific roles, offering the detected models plus `inherit-parent`. Panel roles (`arena runners`, `architect runners`, `interrogate reviewers`) run one seat per entry, so the list length sets the count, and entries from both harnesses give cross-family review. Say that native Claude Code subagents ignore effort, because the `Agent` tool cannot set it per spawn. Effort still applies to Codex subagents and to every foreign seat.

### 4. Validate

Every entry must name a model detected for its harness, or be `inherit-parent` or `auto`. If a chosen entry is not available, stop and ask again.

### 5. Write the file

Overwrite the whole file so re-runs stay idempotent. Shape, shown with the `unlimited` budget:

```
# pstack model configuration, written by /setup-pstack. One line per role.
# Delete a line to fall back to the pstack-harness default.
# Entry: <harness>:<model>[@<effort>], or inherit-parent. pstack-harness says how each role resolves.
# budget: unlimited (max)
feature, refactoring: claude:sonnet@max, codex:gpt-6.1-sol@max
bug-fix: claude:sonnet@max, codex:gpt-6.1-sol@max
perf-issue: claude:sonnet@max, codex:gpt-6.1-sol@max
hillclimb: claude:sonnet@max, codex:gpt-6.1-sol@max
judgment and prose: claude:opus@max, codex:gpt-6-astra@max
hardest tasks: claude:opus@max, codex:gpt-6-astra@max
how explorer: claude:sonnet@max, codex:gpt-6.1-sol@max
how explainer: claude:opus@max, codex:gpt-6-astra@max
why investigators: claude:sonnet@max, codex:gpt-6.1-sol@max
why synthesizer: claude:opus@max, codex:gpt-6-astra@max
reflect tooling: claude:opus@max, codex:gpt-6-astra@max
reflect judgment, divergent, synthesizer: claude:opus@max, codex:gpt-6-astra@max
arena runners: claude:opus@max, codex:gpt-6-astra@max
arena cross-judge pool: claude:opus@max, codex:gpt-6-astra@max
swarm workers: claude:sonnet@max, codex:gpt-6.1-sol@max
architect runners: claude:opus@max, codex:gpt-6-astra@max
interrogate reviewers: claude:opus@max, codex:gpt-6-astra@max
```

### 6. Confirm

Tell the user the file was written. Skills read it each time they run, so it applies from the next skill run in either harness. Re-running this skill updates it.
