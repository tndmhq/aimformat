# Contributing

Thanks for your interest in `.aim`. The repo is **pre-v0.1**: the spec is not
published yet, so the most useful contributions right now are design
discussion via issues, not spec PRs.

Whether you are a human, an AI agent, or a human driving an AI agent, the path
to a good PR is the same:

1. **Read [`AGENTS.md`](AGENTS.md)** — repo purpose, layout, and the working
   conventions (package pinning, planning, memory rules, git etiquette). If
   you use Claude Code, Cursor, or any agent that honors `AGENTS.md` /
   `CLAUDE.md`, it will pick these up automatically.
2. **Skim [`docs/knowledge/`](docs/knowledge/)** (and
   [`docs/log/index.md`](docs/log/index.md) for recent context) so your change
   fits decisions already made.
3. **Open a PR that conforms to the conventions**: focused commits, clear
   messages, pinned dependency versions, no speculative spec content.

Notes specific to external PRs:

- You are **not required to append to `docs/log/`** — the episodic log is a
  maintainer tool, kept light.
- Changes to **`docs/knowledge/` are maintainer-authoritative**: you may
  propose edits, but expect stricter review there than on code.
- By contributing you agree your contributions are licensed under the
  [MIT license](LICENSE).
