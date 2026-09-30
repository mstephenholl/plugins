# Changelog

Releases of pstack for Claude Code and Codex. Each `## [version]` section becomes the notes of the GitHub release for tag `v<version>`. `pstack update` moves to the newest release.

## [0.1.0]

The first release of pstack ported from Cursor to Claude Code and Codex.

- 46 of Cursor's pstack skills, plus `pstack-harness`, which maps Cursor's tools, model names, paths, and transcripts to both harnesses. `tdd` and `teach` are renamed `pstack-tdd` and `pstack-teach`. `make-bot-ui` and the benny automations are not ported.
- `deslop`, `control-ui`, and `control-cli` from Cursor's team kit, which pstack calls.
- The `pstack` command: `install`, `configure`, `doctor`, `update`, `uninstall`, and `status`, with a one-line bootstrap.
- Models per role in `~/.agents/pstack-models.md`, with a reasoning budget and a foreign-seats switch that keeps code and transcripts inside one vendor. Claude Code gets generated `pstack-effort-<level>` agents, since its `Agent` tool cannot set effort per spawn.
- `port/transcripts.py`, which lets `recall`, `reflect`, `automate-me`, and `show-me-your-work` read Claude Code and Codex session logs.
- CI on every push and pull request, a daily sync with upstream cursor/plugins, and a headless smoke test of ten skills in both harnesses.
