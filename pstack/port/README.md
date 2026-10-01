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

`main` takes changes only through pull requests, so merge upstream on a branch.

```sh
cd ~/.agents/src/pstack
git remote add upstream https://github.com/cursor/plugins.git   # once
git switch -c upstream-merge origin/main
pstack/port/merge-upstream.py
```

The script fetches the cursor/plugins remote, merges its `main` without committing, and drops every path outside the port, which upstream's changes to other plugins would otherwise bring back. It keeps our two READMEs over upstream's. It resolves every conflict where our only change to a file is the pointer line: it takes upstream's text and restores the pointer. It lists every other conflict for you. It also reports new upstream skills with the Cursor terms they use, ported skills that upstream removed, and Cursor terms that upstream newly added to skills already ported. Those are what need porting.

After resolving, run `install.sh` and then the smoke test. Then commit the merge, push the branch, and open a pull request. Merge it with a merge commit, because a squash would drop upstream's history from `main`. Name the repository in `GH_REPO`, since the clone also has cursor/plugins as a remote.

```sh
git commit -m "Merge upstream cursor/plugins"
git push -u origin upstream-merge
GH_REPO=<owner>/<repo> gh pr create --fill
GH_REPO=<owner>/<repo> gh pr merge --merge
```

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
  - The `CI gate` job passes only when every one of those jobs succeeded. It is the one check `main` requires, as Repository settings explains. It runs even when a job fails, because GitHub counts a skipped required check as passing.
  - On a push to `main`, the `Release` job runs after the gate and cuts the release. See Releases.
- **`pstack-upstream-sync.yml`** runs daily and on demand. It runs `port/sync-upstream.sh`, which merges cursor/plugins with `merge-upstream.py`. When the script resolves everything, it opens or updates a pull request from the `upstream-sync` branch, labels it `needs-porting` if upstream added something to port, and starts CI on it. When conflicts need a person, it opens or updates an issue labeled `upstream-sync` instead.

`check.py` fails when a skill folder is not installed, a skill's name does not match its folder, a user-only skill lacks its Codex policy file, a skill that uses Cursor terms lacks the pointer, `pstack-harness` stops mapping a Cursor term a skill uses, a relative reference or README link is broken, or the README catalog drifts from the installed skills. It warns about Cursor mentions that no known term covers.

Run the same checks locally:

```sh
python3 pstack/port/check.py
python3 -m unittest discover -s pstack/port/tests -p 'test_*.py'
pstack/port/tests/install_test.sh            # add /bin/bash to test macOS's stock bash
DRY_RUN=1 pstack/port/sync-upstream.sh       # from a clone whose origin is the fork; prints pushes and GitHub writes
```

The sync needs three repository settings: Actions enabled (GitHub disables them on new forks), "Allow GitHub Actions to create and approve pull requests", and Issues enabled (also off on new forks, and set by `has_issues` in `github.json`).

## Releases

Merging to `main` is the release process. The `Release` job in `pstack-ci.yml` runs `port/release.py --publish` on the pushed commit once the `CI gate` job passes. It releases the commit unless the newest plain `vX.Y.Z` tag already covers it, and it creates the tag and the GitHub release in one API call. Release jobs queue in the `pstack-release` concurrency group and run one at a time, so overlapping merges never race for a tag, and a rerun of a covered commit does nothing.

The script reads the first-parent history of `main` since the newest tag. Each change asks for a level, and the release takes the highest. Upstream commits that a sync brings in sit on a second parent, so only the sync's own merge counts.

| A change since the last release | Level |
|---|---|
| Any merged pull request or direct commit, including a plain title or an upstream sync | Patch |
| A `feat` title | Minor |
| A breaking change, which is `!` after the type or a `BREAKING CHANGE:` line in the body | Minor before 1.0.0, major after |
| A `Release-As: X.Y.Z` line in the pull request body | The level of `X.Y.Z`, when it is the next patch, minor, or major |

`Release-As` sets the level of its own pull request and no other. One pull request can lower itself, so a `feat` with `Release-As: 0.4.3` asks only for a patch, but it never hides another pull request's `feat` or breaking change. `Release-As: 1.0.0` is the way from 0.x to 1.0.0, because the breaking-change rule stops at a minor before then. Any other value is ignored and that pull request keeps the level its title earns. The ignored value shows as a warning in the job summary and as an annotation on the run.

Titles follow Conventional Commits, and a title that does not is a patch. A `BREAKING CHANGE:` or `Release-As:` line inside a fenced code block is not read, so documenting one never triggers it. A tag cannot be moved or deleted once users have it, which is why `Release-As` accepts only the next version.

The notes list the pull request titles under Breaking changes, Features, Fixes, and Other changes, so write each title as the line users read.

Preview a release with `pstack/port/release.py` on an up-to-date `main`. It prints the decision and the notes and publishes nothing. Add `--target <ref>` for another commit.

```sh
git fetch --tags
pstack/port/release.py                  # the release for HEAD, with its notes
pstack/port/release.py --target <ref>   # the release for another commit
```

On a feature branch the preview lists the branch's own commits, not the one merge commit that the pull request makes, so its version and notes can differ from the real release. Publishing needs `--publish`, which only the release job passes.

A pull request body that contains `[skip ci]` skips CI on its merge commit, so no release runs for it. That change ships with the next merge.

Never push `v*` tags by hand. The release job skips a tag whose name is not plain `vX.Y.Z` and warns about it, and `pstack update` and the bootstrap ignore it.

The bootstrap checks out the newest plain `vX.Y.Z` tag, and `pstack update` moves such a checkout to newer tags only. Every merge to `main` therefore reaches users on their next update, including a merged upstream sync. A clone that tracks a branch, like a maintainer's, follows its branch instead.

## Repository settings

`port/github.json` records the repository settings and rulesets that releases and upstream syncs depend on. `port/github-settings.sh` applies them with `gh`.

```sh
GH_REPO=<owner>/<repo> pstack/port/github-settings.sh              # apply them
DRY_RUN=1 GH_REPO=<owner>/<repo> pstack/port/github-settings.sh    # print each write instead of making it
```

Run it as an account that administers the repository. It requires `GH_REPO`, so it never guesses which repository to change. It finds each ruleset by name and updates it in place, so running it again changes nothing. To change a setting, edit `github.json` and run the script.

- **Merge commits only.** A squashed or rebased upstream sync would drop cursor/plugins from the history of `main`, and every later sync would conflict on every file. For the same reason, `main` must not require linear history. The merge commit takes the pull request title and body, which is where `release.py` reads the title and any `Release-As:` or `BREAKING CHANGE:` line. Merge pull requests with `gh pr merge --merge`. The Shipping playbook of `poteto-mode` says `--squash`, which this repository rejects.
- **A required `CI gate` check.** The `main` ruleset requires it from GitHub Actions, blocks direct pushes, force pushes, and deletion, and requires every review thread to be resolved. It requires no approvals, because one maintainer cannot approve their own pull request. No ruleset has bypass actors, but an admin can still disable a ruleset.
- **`v*` tags cannot move or be deleted.** Users' clones already hold them. The ruleset leaves creation open, since the `Release` job creates the tags. `pstack update` and the bootstrap ignore names that are not plain `vX.Y.Z`, so a stray tag never reaches a clone as a release. A plain tag that users already fetched stays in their clones even after you delete it.
- **Branches are deleted after a merge**, and a pull request offers to merge `main` into its branch. That is how an open sync pull request picks up a new `CI gate` job.

To remove a tag pushed by mistake, an admin disables the `release tags` ruleset in the repository settings, deletes the release and the tag, and enables the ruleset again. `release.py` fails when the newest plain tag is not in the history of the commit it releases, and its error points here.

`merge-upstream.py` deletes every path that its `KEEP` pattern does not match. `KEEP` covers `pstack/`, `README.md`, `.gitignore`, the team kit's license and three of its skills, and `.github/workflows/pstack-*.yml`. A file anywhere else, such as `.github/dependabot.yml` or `.github/CODEOWNERS`, disappears with the next upstream sync unless you widen `KEEP`.

## Uninstall

`pstack uninstall` removes every link and generated agent the install made, `~/.agents/pstack`, the `pstack` command, and the saved state. `--purge` also deletes the models file, and the clone if it is the bootstrap's `~/.local/share/pstack`. Claude Code settings you added stay as they are.

## Behavior to know

- **Foreign seats.** Panels (`arena`, `architect`, `interrogate`) and `reflect`'s tooling lens seat the other harness through its CLI. In Codex, `claude` runs outside the sandbox, so Codex asks for escalation. With `approvals_reviewer = "auto_review"`, the reviewer may deny a seat that would send a session transcript to the other vendor. The seat is then reported as blocked and the run finishes without it. To keep code and transcripts inside the harness you run, put `foreign seats: off` in `~/.agents/pstack-models.md`, or answer `off` in `/setup-pstack`. Every role then runs natively, and each panel seats two native models so it still gets two reviewers.
- **Claude Code prompts** before writing under `.claude/`, for example when `create-verification-skill` links `.claude/skills/verify-<app>`.
- **Read permissions.** `pstack install --claude-read-rules` adds allow rules for `Read(~/.agents/pstack/**)`, `Read(<your clone>/**)`, and `Read(~/.claude/skills/**)` to `~/.claude/settings.json`, so Claude Code stops asking before it reads skill files. An allow rule for a symlinked path must match both the link and its target.
- **Not ported:** `make-bot-ui` and the `benny` automation pack depend on Cursor automations. `automate-me` and the verification skills are ported but interactive, so the smoke test does not cover `automate-me` or `maintain-verification-skill`.
