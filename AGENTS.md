# AGENTS.md

Guidance for AI coding agents working in this repository. (Cross-agent standard
file; read by Cursor, Codex, Aider, Gemini CLI, etc. Claude Code reads it via
the pointer in `CLAUDE.md`.)

> **The committed agent memory lives in [`docs/`](docs/README.md), in two tiers:**
> [`docs/knowledge/`](docs/knowledge/) — curated project knowledge — and
> [`docs/log/`](docs/log/) — the timestamped episodic log (used sparingly here;
> see the governance notes). **Read `knowledge/` + skim
> [`docs/log/index.md`](docs/log/index.md) before starting; write back when you
> learn something durable.**

## Repository

`aimformat` — the open `.aim` document format: an AI-native format where AI
proposals and human accept/reject are first-class file primitives. This repo
holds the **spec (v0.1 draft, [`spec.md`](spec.md))**, the Python SDK +
verifier + CLI (`src/aimformat/`), the conformance suite, and the developer
docs; the MCP server and reference viewer are planned. Open design questions
(spec Appendix C) are decided by the maintainer, not inferred — propose,
don't assume. Read
[`docs/knowledge/architecture.md`](docs/knowledge/architecture.md) before
touching code. Overview → [`README.md`](README.md).

This is a public repo. It must contain **no business/strategy material** —
technical content only (see Governance below).

## Layout

- [`README.md`](README.md) — what `.aim` is; quickstart; API tour.
- [`spec.md`](spec.md) — the normative spec; every ` ```aim ` snippet is
  linted in CI; Appendix A is generated from the registry (never hand-edit).
- [`src/aimformat/`](src/aimformat/) — SDK, verifier, CLI, css generator,
  ingest/export; [`registry.json`](src/aimformat/registry.json) is the
  single source of truth for the vocabulary.
- [`tests/`](tests/) — the suite; [`tests/fixtures/`](tests/fixtures/) is
  the ok/nok conformance kit (regenerate via `scripts/gen_fixtures.py`).
- [`examples/`](examples/) — SDK-generated (`scripts/gen_examples.py`).
- [`AGENTS.md`](AGENTS.md) — this file; **canonical home of the shared
  conventions** below.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how humans and agents produce a
  conforming PR (dev setup, registry workflow).
- [`docs/`](docs/README.md) — two-tier agent memory
  ([`knowledge/`](docs/knowledge/) + [`log/`](docs/log/));
  [`scripts/new_log_entry.py`](scripts/new_log_entry.py) scaffolds entries.
- MCP server and reference viewer — planned, not yet in-repo.

## Conventions (canonical — source of truth for all tndmhq repos)

> **This section is the canonical copy of the generic agent conventions for
> all `tndmhq` repositories.** Other repos vendor a copy under a
> "Source of truth: tndmhq/aimformat AGENTS.md" header. If you improve a
> convention, change it **here** and re-sync the vendored copies.

1. **Package management — search, then pin.** Always web-search a package's
   latest stable version before installing it (PyPI: `https://pypi.org/pypi/<name>/json`;
   npm: `npm view <name> version`), then install and record **that exact
   version** (`pip install <name>==<version>` / `npm install <name>@<version>`,
   pinned in the requirements/lock file). Never install unpinned and never
   trust a version number from memory — training-data versions are stale.

2. **Plan before implementing.** For any non-trivial task, write the plan as a
   `…_plan_…` entry in `docs/log/` (scaffold with
   `python3 scripts/new_log_entry.py plan <slug> --what "…"`) **before**
   writing code, and get it confirmed when working interactively.

3. **Two-tier memory.** Durable, curated facts live in `docs/knowledge/`
   (edit in place, keep current — stale knowledge is worse than none).
   Episodic records — plans, reviews, reports, status, decisions, events —
   are timestamped, append-only files in `docs/log/`
   (`YYYY-MM-DD_HHMM_<type>_<slug>.md`, time of day in **UTC**,
   type ∈ plan|review|report|status|decision|event), each with
   YAML frontmatter (`status: active|done|superseded`) and a one-line row in
   `docs/log/index.md`. Never rewrite a log entry — supersede it and flip the
   old entry's `status`. Full rules → [`docs/README.md`](docs/README.md).

4. **Consolidate on completion** (part of the definition of done): flip the
   log entry's `status`, update its index row, and promote durable,
   non-discoverable lessons into the right `docs/knowledge/` file.

5. **Git — no autonomous commits in local checkouts.** In a user's local
   checkout, prepare changes and let the user review/commit (or commit only
   when explicitly asked). In cloud/sandboxed environments (disposable
   worktrees, CI), committing as part of the work is fine.

6. **Agent-friendliness.** Proactively look for ways to make the repo easier
   for future agents — missing or stale knowledge entries, pointers worth
   adding to `AGENTS.md`, conventions worth codifying, reusable tooling — and
   suggest them; fold accepted ones into `docs/knowledge/` or `AGENTS.md`.

## Governance (public repo)

- **`docs/knowledge/` is maintainer-authoritative.** Changes to it go through
  PR review like code; external PRs may propose knowledge edits but the
  maintainer decides what is recorded as fact.
- **The log stays light and technical.** Durable decisions and major
  plans/reviews only — this is a fast-moving pre-v0.1 repo, not a working
  journal. External contributors are **not** required to append log entries
  (see [`CONTRIBUTING.md`](CONTRIBUTING.md)).
- **No strategy in public.** Business reasoning, positioning, pricing, or
  competitive material never lands in this repo — technical rationale only.
