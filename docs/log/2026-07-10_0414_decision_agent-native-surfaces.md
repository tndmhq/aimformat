---
date: 2026-07-10 04:14
type: decision
status: done
related: []
---

# Decision: agent-native surfaces — the aim-note, CLI verbs, MCP, Skill

How LLM agents interact with `.aim` natively was designed and shipped in
one pass (2026-07-10). The layered architecture, cheapest-first: the file
itself carries the discovery hook; docs, CLI, skill, and MCP build on it.
Design research (skills/MCP/in-file-header landscape, verified 2026-07-09)
lives in the private workspace; this entry records what is now normative
here and why.

## Decisions

1. **The agent note (spec §2.5).** Every writer SHOULD emit one head
   comment, sigil `aim-note:`, immediately after `<meta charset>` —
   declarative self-description pointing at aimformat.com/llms.txt with the
   hand-editing invariants. Rationale for *declarative*: imperative in-file
   instructions pattern-match prompt injection and are ignored or flagged
   by 2026 agent stacks (OpenAI Model Spec treats file content as
   information, not instructions; the carve-out is exactly README-like
   format rules in coding contexts). Anthropic ships the same pattern on
   its own docs pages. **Informative-only is a MUST**: tools never execute,
   install, or fetch anything because of header content — the vim-modeline
   CVE lesson, written into the spec. The note text contains no markup so
   structural substring checks (`<aim-proposals>` etc.) never
   false-positive on it (found the hard way: three tests grep for those
   markers). S030 (warning) flags duplicates. Emission sits in
   `new_document()`, so `aim new` and every importer inherit it;
   `aim note FILE [--check|--remove]` retrofits/verifies/strips.
2. **Pending-lane CLI verbs** — `aim propose {modify,add,delete,move,theme}`,
   `aim accept`/`aim reject` (ids or `--all`), `aim show --format json`,
   `--author human:ID | agent:MODEL | external:ID` (`parse_actor`). Coding
   agents are the most common agent consumers and work through shells;
   propose/accept were SDK-only before this.
3. **MCP server: thin, stdio, in-package.** `aimformat.mcp` behind the
   `[mcp]` extra (pinned `mcp==1.28.1`; upstream advises `<2`), wired as
   `aim mcp`. Six workflow tools (`aim_read`, `aim_edit`, `aim_propose`,
   `aim_resolve`, `aim_lint`, `aim_export`) — deliberately not a 1:1 SDK
   mirror (MarkItDown/Docling shape). Deferred on purpose: remote/HTTP
   transport and registry listing until the 2026-07-28 stateless MCP spec
   settles; MCP Apps UI later. The old `tndm-mcp`/`pip install tndm`
   naming from early strategy docs is retired — everything ships neutral
   under `aimformat`.
4. **Agent Skill** (`skills/aimformat/`, MIT) per the open Agent Skills
   standard — installable via `npx skills add tndmhq/aimformat` and as a
   Claude Code plugin (`.claude-plugin/` manifest; repo doubles as its own
   marketplace). Claude Code `paths: ["**/*.aim"]` auto-activates it on
   matching files. Skills reach ~40 agent products with one SKILL.md;
   this, not MCP, is the primary teaching layer for coding agents.
5. **One source of truth for agent guidance**: `docs/for-agents.md` →
   served as https://aimformat.com/llms.txt (landing repo), condensed into
   the skill and the MCP server instructions. The site also gains
   `/editors` (Tndm listed as flagship — a product mention, no strategy).
6. **Packaging**: dual console scripts `aim` + `aimformat` (PyPI `aim` is
   AimStack's tracker with a colliding CLI; the alias also makes
   `uvx aimformat` work), version single-sourced from `__init__.py`,
   trusted-publishing workflow on GitHub release.
7. **Eval harness** (`evals/`): id-preservation A/B of naked-LLM edits with
   vs without the note — the measurable claim behind the header design.

## Outcome

Shipped in this repo in one PR (spec §2.5 + S030, note.py, CLI verbs,
mcp.py, skill, docs, evals, packaging); counterpart landing-site commit
(llms.txt route, /editors, stale on-ramp copy fixes) in
`tndmhq/aimformat-landing`. Full suite green (455+ tests incl. MCP
in-memory session tests and the S030/nok fixture); examples and fixtures
regenerated with the note.
