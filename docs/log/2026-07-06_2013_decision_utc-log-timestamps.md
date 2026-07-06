---
date: 2026-07-06 20:13
type: decision
status: active
related: []
---

# Decision: log timestamps are UTC

**Context.** The two-tier memory system was ported from a reference
implementation whose convention stamped log entries in CET/CEST (Europe/Rome)
— sensible there (single-region project where local time carries meaning).
This repo and its sibling `aimformat-landing` are public, with contributors
potentially in any timezone.

**Decision.** All `docs/log/` timestamps — the `HHMM` in filenames and the
frontmatter `date:` — are **UTC**, in every tndmhq repo, effective
2026-07-06. The scaffolder (`scripts/new_log_entry.py`) stamps UTC.

**Why.**

- One fixed zone is what makes filenames sort chronologically (the original
  goal); UTC does that *and* has no DST — a CET/CEST fall-back hour can
  produce out-of-order names once a year.
- Neutral and directly legible for contributors anywhere, which matters in
  the public repos; a single-country zone is a parochial default here.
- One uniform rule beats per-repo zones: these conventions are canonical here
  and vendored into the sibling repos.

**Applied.** Convention text (`AGENTS.md`, `docs/README.md`) and the
scaffolder updated here; vendored copies and scaffolders re-synced in the
sibling repos. The single log entry that predated this decision (in the
private workspace repo, stamped 21:03 CEST) was renamed to its UTC equivalent
(19:03) the same day it was written, before any other clones existed — a
one-time exception to the append-only rule so the history stays uniform;
entry content untouched.

**Revisit.** Nothing anticipated; supersede this entry if the zone ever
changes.
