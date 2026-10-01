# pstack for Claude Code and Codex

This repository runs pstack in Claude Code and Codex. Upstream's skill text stays as written wherever possible. Each ported skill gets one line under its title that points to the `pstack-harness` skill, which maps Cursor's tools, model slugs, paths, and transcripts to both harnesses. Keeping the diff small keeps upstream merges cheap.

## Layout

| Path | What it is |
|---|---|
| `~/.local/share/pstack`, or wherever you cloned | The clone. The bootstrap puts it here and checks out the newest release. A maintainer's clone tracks `main` instead, with cursor/plugins added as a remote, which the merge script finds by URL. |
| `~/.local/bin/pstack` | Symlink to the `pstack` command, `port/pstack`. |
| `~/.config/pstack/state.json` | The harnesses you installed for, so `update` reinstalls the same ones. |
| `~/.agents/pstack` | Symlink to the clone's `pstack/`. Skills and agents find each other through it. |
| `~/.claude/skills/<name>`, `~/.codex/skills/<name>` | Symlinks to each ported skill. |
| `~/.claude/agents/` | Symlinks to `comment-sicko` and `poteto-agent`, plus the generated `pstack-effort-<level>` agents. |
| `~/.agents/pstack-models.md` | Model per role, written by `pstack configure` or `/setup-pstack`. |

Keep the clone outside `~/.agents/skills`. Codex scans that directory recursively and would load every pstack skill, ported or not.

## Install

Users install with the bootstrap one-liner in [`../README.md`](../README.md), which clones the repository and runs `pstack install`. The `pstack` command (`port/pstack`) is the interface for installing, configuring, checking, updating, and removing. It drives `port/install.sh`, which does the linking:

- It links the skills in `SKILLS` and `TEAM_KIT_SKILLS` into each harness in `PSTACK_HARNESSES`, and the agents into Claude Code. It removes the links from a harness left out.
- For every skill marked `disable-model-invocation: true`, it writes `agents/openai.yaml` with `allow_implicit_invocation: false`, because Codex ignores the frontmatter flag.
- It generates a `pstack-effort-<level>` agent for each effort a `claude:` entry in the models file asks for, since Claude Code's `Agent` tool cannot set effort per spawn.
- It takes over links that point into another pstack clone, or at a clone that was deleted, so moving the clone just works. It never overwrites anything else.

`pstack configure` holds the model logic: detection (`claude --help` and `codex debug models`), defaults per role, the budget, foreign seats, and your overrides. `/setup-pstack` asks the questions and calls it. `PSTACK_DETECT_JSON` fakes detection for tests.

In Codex, user-only skills appear in the `$` picker under the `pstack` namespace, for example `$pstack:poteto-mode`. Typing `$poteto-mode` as plain text in `codex exec` does not load the skill.

## Update from upstream

```sh
cd ~/.agents/src/pstack
git remote add upstream https://github.com/cursor/plugins.git   # once
pstack/port/merge-upstream.py
```

The script fetches the cursor/plugins remote, merges its `main` without committing, and drops every path outside the port, which upstream's changes to other plugins would otherwise bring back. It keeps our two READMEs over upstream's. It resolves every conflict where our only change to a file is the pointer line: it takes upstream's text and restores the pointer. It lists every other conflict for you. It also reports new upstream skills with the Cursor terms they use, ported skills that upstream removed, and Cursor terms that upstream newly added to skills already ported. Those are what need porting.

After resolving and committing, run `install.sh` and then the smoke test.

Expect manual conflicts in the files the port rewrote or edited beyond the pointer: `setup-pstack`, `no-comments`, `poteto-mode` (frontmatter name), the `bug-fix` playbook, the three `reflect` reviewer prompts, `worktree-audit.sh`, and the two agent files.

## Port a new skill

1. Put the pointer line under its title. Copy it from any ported skill, including the words "in full". A skill that reads transcripts also names `transcripts.py`.
2. Map any Cursor term it uses that `pstack-harness` does not cover yet. Add the mapping to the harness, not to the skill.
3. Add it to `SKILLS` in `install.sh` and run `install.sh`.
4. Add a scenario to `smoke/smoke.py` if the skill spawns subagents, picks models, or reads transcripts.

## Smoke test

```sh
~/.agents/pstack/port/smoke/smoke.py                      # all ten scenarios, both harnesses
~/.agents/pstack/port/smoke/smoke.py --only how,reflect   # a subset
~/.agents/pstack/port/smoke/smoke.py --recheck --out DIR  # re-score kept runs without rerunning
```

Each scenario runs one skill headless against a throwaway repo with a planted stale-cache bug. The test then reads the transcripts of the parent, its subagents, and any foreign seat, and checks the port's plumbing and the skill's result. A full run takes 40 to 60 minutes at four parallel jobs and spends real tokens on Opus and `gpt-6-astra`. Passing runs are deleted along with their sessions. Failing runs stay in the output directory for inspection. Once you are done with them, delete their sessions and folders.

## CI

Two workflows run on GitHub.

- **`pstack-ci.yml`** runs on every push to `main`, every pull request, and on demand. It runs `port/check.py`, the unit tests in `port/tests/`, `shellcheck`, `poteto-mode`'s bun tests, and `port/tests/install_test.sh` on Ubuntu and macOS, including macOS's stock bash 3.2.
- **`pstack-upstream-sync.yml`** runs daily and on demand. It runs `port/sync-upstream.sh`, which merges cursor/plugins with `merge-upstream.py`. When the script resolves everything, it opens or updates a pull request from the `upstream-sync` branch, labels it `needs-porting` if upstream added something to port, and starts CI on it. When conflicts need a person, it opens or updates an issue labeled `upstream-sync` instead.

`check.py` fails when a skill folder is not installed, a skill's name does not match its folder, a user-only skill lacks its Codex policy file, a skill that uses Cursor terms lacks the pointer, `pstack-harness` stops mapping a Cursor term a skill uses, a relative reference or README link is broken, or the README catalog drifts from the installed skills. It warns about Cursor mentions that no known term covers.

Run the same checks locally:

```sh
python3 pstack/port/check.py
python3 -m unittest discover -s pstack/port/tests -p 'test_*.py'
pstack/port/tests/install_test.sh            # add /bin/bash to test macOS's stock bash
DRY_RUN=1 pstack/port/sync-upstream.sh       # from a clone whose origin is the fork; prints pushes and GitHub writes
```

The sync needs three repository settings: Actions enabled (GitHub disables them on new forks), "Allow GitHub Actions to create and approve pull requests", and Issues enabled (also off on new forks).

## Releases

1. Add a `## [X.Y.Z]` section to [`../CHANGELOG.md`](../CHANGELOG.md).
2. Tag the commit `vX.Y.Z` and push the tag.
3. `pstack-release.yml` reruns the checks and unit tests, then publishes a GitHub release with that section as its notes. It fails if the section is missing.

The bootstrap checks out the newest `v*` tag, and `pstack update` moves such a checkout to newer tags only. Merging an upstream sync pull request therefore reaches users only when you cut a release. A clone that tracks a branch, like a maintainer's, follows its branch instead.

## Uninstall

`pstack uninstall` removes every link and generated agent the install made, `~/.agents/pstack`, the `pstack` command, and the saved state. `--purge` also deletes the models file, and the clone if it is the bootstrap's `~/.local/share/pstack`. Claude Code settings you added stay as they are.

## Behavior to know

- **Foreign seats.** Panels (`arena`, `architect`, `interrogate`) and `reflect`'s tooling lens seat the other harness through its CLI. In Codex, `claude` runs outside the sandbox, so Codex asks for escalation. With `approvals_reviewer = "auto_review"`, the reviewer may deny a seat that would send a session transcript to the other vendor. The seat is then reported as blocked and the run finishes without it. To keep code and transcripts inside the harness you run, put `foreign seats: off` in `~/.agents/pstack-models.md`, or answer `off` in `/setup-pstack`. Every role then runs natively, and each panel seats two native models so it still gets two reviewers.
- **Claude Code prompts** before writing under `.claude/`, for example when `create-verification-skill` links `.claude/skills/verify-<app>`.
- **Read permissions.** `pstack install --claude-read-rules` adds allow rules for `Read(~/.agents/pstack/**)`, `Read(<your clone>/**)`, and `Read(~/.claude/skills/**)` to `~/.claude/settings.json`, so Claude Code stops asking before it reads skill files. An allow rule for a symlinked path must match both the link and its target.
- **Not ported:** `make-bot-ui` and the `benny` automation pack depend on Cursor automations. `automate-me` and the verification skills are ported but interactive, so the smoke test does not cover `automate-me` or `maintain-verification-skill`.
