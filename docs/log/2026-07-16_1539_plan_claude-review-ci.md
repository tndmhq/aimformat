---
date: 2026-07-16 15:39
type: plan
status: active
related: []
---

# claude-review CI for a public repo

> Add an automated PR review workflow (Claude Code action) to this repo,
> mirroring the editor repo's `claude-review.yml`, hardened for a **public**
> repository. Reviews are bound by [`REVIEW.md`](../../REVIEW.md) (≤5
> severity-tagged findings, no nits) like every other AI reviewer here.

## Design

- **Trigger**: `pull_request` with `types: [opened, ready_for_review,
  synchronize]` and `paths-ignore` for docs-only churn **including this
  workflow file itself** (self-skip: the reviewer must not review PRs that
  edit its own instructions).
- **Maintainer allowlist + fork hardening** (the public-repo delta): the
  review job runs only for PRs authored by approved maintainers — today just
  the repo owner (resolve the exact GitHub login at implementation time, e.g.
  `gh api user -q .login`) — and only from same-repo branches:

  ```yaml
  if: >
    !github.event.pull_request.draft &&
    github.event.pull_request.head.repo.full_name == github.repository &&
    contains(fromJSON('["<maintainer-login>"]'), github.event.pull_request.user.login)
  ```

  All other PRs (forks, or future collaborators not yet on the list) skip
  the job cleanly — no secret exposure, no red ✗. Note that plain
  `pull_request` already withholds secrets from fork-originated runs; the
  explicit guard exists for clean skips and to future-proof against the
  trigger ever being switched to `pull_request_target` (never do that). A
  label-gated variant for reviewing external PRs is explicitly deferred.
- **Permissions / concurrency**: `contents: read`, `pull-requests: write`;
  concurrency group per PR number, cancel-in-progress — same as the editor
  repo's workflow.
- **Secret**: `CLAUDE_CODE_OAUTH_TOKEN` added as a repo secret (same auth
  mechanism as the existing `claude.yml` @-mention workflow).
- **Prompt**: port the editor repo's structure (read `REVIEW.md` first, then
  the full PR discussion via `gh`, then the diff; `--max-turns 90`), with the
  review focus adapted to this repo:
  - spec conformance: `spec.md` snippets lint, conformance fixtures
    (`tests/fixtures/` ok/nok kit) updated when behavior changes;
  - registry discipline: `registry.json` is the vocabulary source of truth;
    Appendix A and `examples/` are generated, never hand-edited;
  - round-trip integrity: parse → serialize canonicality;
  - public API stability of the SDK/CLI/MCP tools (pre-1.0, but breaking
    changes must be called out);
  - docs sync: README/for-agents/skill remain consistent with code changes.

## Steps

1. Port + adapt the workflow file (`.github/workflows/claude-review.yml`).
2. Add the repo secret (maintainer, GitHub UI).
3. Verify on a same-repo test PR authored by the maintainer: review lands,
   findings respect REVIEW.md.
4. Verify the skip path: a PR from any non-allowlisted author (fork or
   otherwise) shows the job as skipped, with no secret-bearing run.
5. `CONTRIBUTING.md`: one line — automated review runs on approved
   maintainers' PRs (allowlist in the workflow); other PRs get maintainer
   review.

## Non-goals

- No replication of the editor repo's e2e/visual testing program here — this
  repo's deterministic layer (conformance kit, round-trip, MCP tool tests)
  is already proportionate to a spec + SDK.
- A spec-§ × test conformance matrix is deferred (revisit after the
  2026-07-28 spec revision, when §-numbering settles).
