---
date: 2026-11-05 19:31
type: decision
status: todo
related: [2026-10-01_2135_decision_import-versioning-and-sequencing.md, 2026-11-05_1816_plan_pr40-rework.md]
---

# Decision IMPORT-D14b: the tracked-change import ships in spec and SDK 0.6

Id: **IMPORT-D14b**. Proposed: the maintainer's current leaning (2026-11-05), not final.
Supersedes [IMPORT-D14](2026-10-01_2135_decision_import-versioning-and-sequencing.md).

## Context

IMPORT-D14 shipped the tracked-change fix (D1-D8) as SDK 0.5.3 and the rest as 0.6. Version
0.5.3 shipped on 2026-10-14 with `to_print_html` only; merging `main` (4eb432e) moved the
tracked-change notes under 0.6.0 in the CHANGELOG. The stacked PRs (#42, #41, #39) all
target 0.6.

## Decision (proposed)

One 0.6 release carries this PR and the stacked ones; the branch is not split. A 0.5.x fix
would be cut from v0.5.3 as 0.5.4. Pre-releases (0.6.0a1, …) may give downstream tools an
installable version to pin before the release.

## Consequences

The "Ships in SDK 0.5.3" lines of IMPORT-D1 to D8 read as 0.6. The data-loss fix waits for
0.6, the cost D14 meant to avoid. D14's downstream pin-bump steps apply to the single
release.
