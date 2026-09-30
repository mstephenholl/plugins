# pstack for Claude Code and Codex

This fork of [cursor/plugins](https://github.com/cursor/plugins) carries one plugin: [pstack](pstack/README.md), ported from Cursor to Claude Code and Codex. It also carries three skills from Cursor's team kit that pstack calls: `deslop`, `control-ui`, and `control-cli`. Everything else from upstream is removed.

## Quick start

```sh
git clone -b claude-codex https://github.com/mstephenholl/plugins.git ~/.agents/src/pstack
~/.agents/src/pstack/pstack/port/install.sh
```

Then run `/setup-pstack` in Claude Code, or `$setup-pstack` in Codex, and start with `/poteto-mode <task>`.

- [`pstack/README.md`](pstack/README.md) covers setup, using the skills, models, and differences from Cursor's pstack.
- [`pstack/port/README.md`](pstack/port/README.md) covers updating from upstream, porting a new skill, and the smoke test.
