---
name: pstack-harness
description: "Maps pstack's Cursor tools, model slugs, and paths to Claude Code and Codex. Other pstack skills read it before they spawn subagents, pick models, ask questions, or read transcripts."
disable-model-invocation: true
---

# pstack harness

pstack was written for Cursor. This file translates its Cursor vocabulary for Claude Code and Codex. When a pstack skill or playbook names a Cursor tool, model slug, or path, apply the mapping here and otherwise follow the skill as written.

You are in Claude Code if your tools include `Agent`. You are in Codex if your tools include `spawn_agent`.

## Paths

- pstack lives at `~/.agents/pstack`. Skills are `~/.agents/pstack/skills/<name>/SKILL.md`. Agent definitions are in `~/.agents/pstack/agents/`.
- Model config is `~/.agents/pstack-models.md`. Read it wherever a skill says `~/.cursor/rules/pstack-models.mdc` or "the `pstack-models.mdc` rule".
- A skill that writes a project-local skill to `.cursor/skills/<name>/` writes it to `.agents/skills/<name>/` instead, with `.claude/skills/<name>` symlinked to it so both harnesses find it.

## Running another pstack skill

Most pstack skills are user-only, so they are hidden from you and the `Skill` tool cannot load them. When a skill says to run, use, apply, or read another skill (`how`, the **arena** skill, the **laziness-protocol** principle skill), read `~/.agents/pstack/skills/<name>/SKILL.md` and follow it. Principle skills are named `principle-<name>`.

| pstack says | Use |
|---|---|
| `tdd` | `pstack-tdd` |
| `teach` | `pstack-teach` |
| `create-skill` (Cursor built-in) | The harness's `skill-creator` skill |
| `deslop`, `control-ui`, `control-cli` (`cursor-team-kit`) | Installed under the same names. Invoke them normally. |
| `verify-this` (`cursor-team-kit`) | Not installed. Save the before/after evidence yourself. |
| Cursor's built-in `babysit` | Not present. The Babysit playbook is the only one. |

## Tools

| Cursor | Claude Code | Codex |
|---|---|---|
| `Task` subagent | `Agent` | `spawn_agent`, then `wait_agent`. `close_agent` when done. |
| `subagent_type: generalPurpose` | `general-purpose` | The default agent type |
| `readonly: true` | `Explore`, or tell a `general-purpose` agent not to edit | Say "read-only, do not edit files" in the message |
| `run_in_background: true` | Agents already run in the background and notify you when done | `spawn_agent` returns at once. Collect with `wait_agent`. |
| `model` | `model`: `opus`, `sonnet`, `haiku`, or `fable`. Effort cannot be set per spawn. | `model` and `reasoning_effort` |
| `environment: "cloud"`, `cloud_base_branch` | No cloud workers. Run locally. A writer gets its own worktree (`isolation: "worktree"`). | No cloud workers. Run locally. Create a git worktree per writer and name it in the message. |
| `AskQuestion` | `AskUserQuestion` | `request_user_input` when available, else ask in plain text |
| todolist | Your task or todo tool | `update_plan` |
| `/loop` | The `loop` skill, or `Monitor` for an event | No loop command. Poll in a shell loop with a long sleep. |
| MCP discovery (the `mcps/` directory) | MCP tools are named `mcp__<server>__<tool>`. Search deferred tools too. | `codex mcp list`, plus the MCP tools in your tool list |

Named agents:

| pstack agent | Claude Code | Codex |
|---|---|---|
| `poteto-agent` | `subagent_type: "poteto-agent"` | A default agent whose message starts: "Before any work, read `~/.agents/pstack/skills/poteto-mode/SKILL.md` in full, including its Principles section." |
| `comment-sicko` | `subagent_type: "comment-sicko"` | A default agent whose message starts with the body of `~/.agents/pstack/agents/comment-sicko.md` |

## Models

Skill text names Cursor slugs (`grok-4.7-xhigh-fast`, `gpt-5.6-sol-max`, `claude-opus-5-5-max`) as defaults. Ignore them. Resolve every role from `~/.agents/pstack-models.md`, and use the table below when the file or the role's line is missing.

An entry is `<harness>:<model>[@<effort>]`, where harness is `claude` or `codex`, or `inherit-parent` (also written `auto`), which means a native subagent on your own model. An entry is native when its harness is yours and foreign otherwise.

- Panel roles (`arena runners`, `architect runners`, `interrogate reviewers`) get one seat per entry.
- `arena cross-judge pool` gets one seat. Prefer a foreign entry, since that is a different model family from yours.
- Every other role takes the first native entry. With no native entry, it takes the first entry and runs it foreign.

Run a native entry as a subagent with its model. Codex also takes its effort. Run a foreign entry through the other harness's CLI, below.

| Role | Default |
|---|---|
| `feature, refactoring`, `bug-fix`, `perf-issue`, `hillclimb`, `how explorer`, `why investigators`, `swarm workers` | `claude:sonnet, codex:gpt-6.1-sol` |
| `judgment and prose`, `hardest tasks`, `how explainer`, `why synthesizer`, `reflect tooling`, `reflect judgment, divergent, synthesizer` | `claude:opus, codex:gpt-6-astra` |
| `arena runners`, `architect runners`, `interrogate reviewers`, `arena cross-judge pool` | `claude:opus, codex:gpt-6-astra` |

If a model is rejected, use that role's default for the same harness and say so.

## Foreign seats

A foreign seat cannot see your conversation. Write its full prompt to a file that stands alone (task, paths, rubric, output shape), run the CLI in the background, and read its final message from the output file. Run it in the seat's own worktree when it writes files, otherwise in the repo root. Omit the effort flag when the entry has none.

From Claude Code, a `codex:` seat:

```bash
codex exec -m <model> -c model_reasoning_effort=<effort> -s read-only --skip-git-repo-check \
  -C <dir> -o <out.md> - < <prompt.md>
```

A seat that writes files uses `-s workspace-write`.

From Codex, a `claude:` seat:

```bash
cd <dir> && claude -p --model <model> --effort <effort> \
  --disallowedTools "Edit,Write,NotebookEdit" < <prompt.md> > <out.md>
```

A seat that writes files uses `--permission-mode auto` in place of `--disallowedTools`.

Each CLI needs network access and its own home directory. Codex's sandbox blocks both, so request escalation for the `claude` command. If Claude Code's Bash sandbox is on, run `codex` outside it.

## Transcripts

Wherever a skill reads the active workspace's `agent-transcripts/` directory:

- Claude Code: `~/.claude/projects/<slug>/<session-id>.jsonl`, where `<slug>` is the working directory with every `/` and `.` replaced by `-`. Subagent transcripts are in `<session-id>/subagents/agent-*.jsonl`. The newest file is usually this session. Confirm by searching it for a phrase from this conversation.
- Codex: sessions since August 2026 are in `~/.codex/thread_history_1.sqlite`, not JSONL. Older ones are `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. If you cannot read this session's transcript, skip the transcript check and say so.

Stay inside the active project's transcripts, as the Cursor rule says.

## Not available outside Cursor

- Cloud agents, the Cursor dashboard, and Cursor restarts. Every worker is local, and local subagents do not survive a restart.
- Cursor automations and webhooks (`make-bot-ui`, `benny`).
- `poteto-mode`'s `mode` and `reminder` frontmatter. Invoke `/poteto-mode` or `$poteto-mode` per task.
- Origin. Use it only when `command -v origin` succeeds, as the playbooks say. Otherwise `gh`.
- Bugbot is a GitHub app. It applies only where the repo has it installed.
- `scripts/watch-pr` and `scripts/orch` need `bun`. Without it, read PR state with `gh pr view --json` and `gh pr checks`, and keep the orchestrate ledger as a TSV by hand.
