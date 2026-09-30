# pstack for Claude Code and Codex

This fork of [cursor/plugins](https://github.com/cursor/plugins) carries one plugin: [pstack](pstack/README.md), ported from Cursor to Claude Code and Codex. It also carries three skills from Cursor's team kit that pstack calls: `deslop`, `control-ui`, and `control-cli`. Everything else from upstream is removed.

## Quick start

```sh
curl -fsSL https://raw.githubusercontent.com/mstephenholl/plugins/claude-codex/pstack/port/bootstrap.sh | bash
pstack configure
pstack doctor
```

Then start any task that needs rigor with `/poteto-mode <task>` in Claude Code, or pick `$pstack:poteto-mode` in Codex.

- [`pstack/README.md`](pstack/README.md) covers the `pstack` command, using the skills, models, and differences from Cursor's pstack.
- [`pstack/port/README.md`](pstack/port/README.md) covers updating from upstream, porting a new skill, CI, releases, and the smoke test.
- [`pstack/CHANGELOG.md`](pstack/CHANGELOG.md) lists the releases.
