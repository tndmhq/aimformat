---
date: 2026-11-06 11:23
type: plan
status: done
related: [2026-11-05_1816_plan_pr40-rework.md, 2026-10-01_2135_decision_flatten-collapses-to-checkpoint.md, 2026-10-01_2135_decision_baseline-lifecycle-operation.md, 2026-11-05_1931_decision_import-packs-images-before-baseline.md]
---

# The #40 final batch: round-3 fixes, history repair, flatten keeps a baseline, three size readers

## Why

The maintainer answered every open #40 choice on 2026-11-06. Everything the
[rework plan](2026-11-05_1816_plan_pr40-rework.md) built provisionally is approved as
built: IMPORT-D1 to D11, the rest of D13 (P5), D14b, D16, and P1 to P21. Three answers add
work, and Codex round 3 left two findings, already fixed on side branches. This plan is
the last work on the PR before one push and one review round. It does not merge #40.

## Decided by the maintainer (2026-11-06)

1. **Round 3 is fixed in #40.** Later review findings in code older than the round under
   review go on a tracked list kept outside this PR and are fixed after the 0.6 format work.
   A regression that the PR's own latest change introduces is still fixed in the PR.
2. **8b, a one-command repair for an unreadable history:** `aim baseline FILE
   --discard-history` and its Python form keep the text and the pending proposals, start a
   fresh history from a full copy (a baseline), and save the dropped lines in a file next
   to the document.
3. **8c, `flatten()` keeps a copy at 0.6 and above:** a flattened history starts with a
   baseline instead of a checkpoint, as an import, an adoption (IMPORT-D16) and
   `baseline()` already do, so a hand edit after a flatten reconciles with its true
   `before`. Only in documents declared 0.6 or later, so a flatten never raises a 0.5 file
   to 0.6. Supersedes IMPORT-D12. Measured cost on the five DOCX fixtures: a flattened
   file grows 11% to 85% in tokens.
4. **7b (P10 resolved to its alternative):** the SDK reads the size of BMP, WebP and TIFF
   images too, standard library only, so importers store every image they accept once and
   `pack_assets()` stops giving those formats a 100x100 frame.

## Work packages

### WP0. Codex round 3: check, then merge

- `tmp/aim40-r3-tblprchange` (374d33c): a tracked table style or banding change
  (`w:tblPrChange`) becomes row modifies instead of converter noise.
- `tmp/aim40-r3-noopmove` (d24e58e): the tracked DOCX export orders a body anchor's
  arrivals by the accept-all replay, so moves made redundant by earlier ones land where
  `accept_all()` puts them.
- Each fix is checked independently from two angles: (a) does it hold: the finding's own
  scenario fails on 69298f4 and passes on the fix, the new test fails without the source
  change, and neighbouring variants of the scenario pass too; (b) what does it break: the
  full suite, the property and randomized tests, and the two fixes merged together. A
  repair goes on the side branch with a failing-first test when a check finds something in
  the fix itself; a finding in older code goes on the tracked list.
- Then both are merged into `wt/aim-import-tracked` with merge commits (the stacked PRs
  sit on this branch: no rebase, no force push).

### WP1. 8b: `baseline(..., discard_history=True)` and `aim baseline --discard-history`

- `AimDocument.baseline(label, *, author=None, explanation=None, source=None, at=None,
  discard_history=False) -> Event`. With `discard_history=True` the stored history block is
  dropped **without being parsed**, readable or not, and the baseline is recorded exactly
  as on a readable log: one event, a full snapshot, the declared version raised to at
  least 0.6, pending proposals, caches and assets kept, `gc_assets()` as the final pass.
  Nothing on the path may parse the old log (today `_clone()` builds the history index, so
  the id probe would fail on H002 first).
- **seq:** `max(0, h) + 1`, where `h` is the highest integer `seq` (JSON booleans excluded)
  among the dropped lines that parse as JSON objects, and 0 when there is none. So the new
  event's seq is always a positive integer, and never goes backwards as far as the file can
  tell.
- **New public read** `AimDocument.history_text() -> str`: the history block's content
  exactly as stored, unparsed, `""` when there is no block. The caller keeps what it is
  about to drop with it. `baseline()` keeps returning the `Event`.
- **Body checks.** A construct that cannot be a snapshot entry (H008) still refuses, before
  anything changes. Units without a usable id (missing, duplicated, invalid): today's
  refusal tells the caller to run `reconcile()`, which refuses an unreadable log. With
  `discard_history=True` those units get fresh ids, so the repair stays one command; without
  the flag, today's refusal stays. **Every body id that is already usable (valid, and the
  first occurrence in document order) keeps it**, even when a pending payload names it (a
  pending modify's payload carries its target's id): `_fixup_ids` is called with those ids
  as `expected_alive`, not with the empty set adoption passes, and fresh ids avoid every id
  the pending lane uses. Otherwise a valid pending modify would lose its target (P008, and
  `accept_all()` fails).
- **Burned ids:** ids named only in the dropped text are not burned on the instance (the
  text cannot be trusted to parse). The saved file carries no burn ledger anyway (as after
  `flatten()`); the side file keeps them.
- **CLI:** `aim baseline FILE --discard-history [--label L] [--author A] [--explanation E]
  [-o OUT]`.
  - **Input read without newline translation:** the file is read as bytes and decoded as
    UTF-8 before `loads()` (`Path.read_text()` translates CRLF and lone CR). The side file
    receives the history block's content as it appears in the input, byte for byte, CR
    included; if the parser normalizes line endings inside the block, the CLI takes the
    block from the decoded input text rather than from `history_text()`.
  - **Side file:** when that content is not empty, it goes to
    `<OUT>.discarded-history.jsonl`, next to the file being written. It is created
    exclusively and readable by its owner only (`os.open` with `O_CREAT | O_EXCL` and mode
    `0o600`, whatever the umask, numbered names included): on `FileExistsError` the next
    name is tried (`.1`, `.2`, …), so an existing file is never overwritten, and a
    restricted document's text never lands in a world-readable copy. It is written,
    fsynced, and its directory entry fsynced, before the document is replaced; a failure
    there stops the repair before `OUT` changes.
  - **Atomic document write:** the repaired document is written in full to a temporary
    file in the same directory, flushed and fsynced, then moved over `OUT` with
    `os.replace()` (keeping the original file's permission bits), and the directory is
    fsynced after the replace. A failure at any step leaves `OUT` as it was; a side file already written stays (it only copies lines the
    document still holds until the replace succeeds).
  - The command prints both paths.
- No MCP tool (IMPORT-D11).
- Tests (failing first): the 2026-11-06 probe's case, a history line cut in half; after the
  repair lint has no H002, `verify()` passes, pending proposals are unchanged, a later hand
  edit reconciles with its true `before`; seq for highest readable seqs that are positive,
  zero, negative, boolean, missing, and mixed valid and corrupt lines, with no history
  finding afterwards; the side file holds the dropped text byte for byte (LF, CRLF, lone CR
  and mixed line endings), is never overwritten, and is created `0600` from a `0600`
  document under a `022` umask; a readable log with the flag gives the
  same result as without it; a document without history; a body with a missing or
  duplicated id gets fresh ids with the flag and is refused without it; **damaged history
  plus a valid pending modify plus an unrelated unit without an id: full lint clean and
  `accept_all()` succeeds**; an H008 body is refused with the flag and the document is
  unchanged; injected failures while creating, writing or fsyncing the side file, writing
  the temporary file and replacing leave `OUT` byte-identical.
- Docs: spec §6.8 (the reference toolkit's baseline is also the repair for a log it cannot
  read), CLI help, README, `docs/for-agents.md`, the skill's references, CHANGELOG.

### WP2. 8c: `flatten()` records a baseline at 0.6+

- Declared version 0.6 or later (`REGISTRY.baseline_since`): `flatten()` replaces the log
  with one baseline at seq+1 (label as passed, default `"flatten"`; no author), through the
  same code as `baseline()`. Below 0.6: one checkpoint, as today; the declared version is
  never raised.
- A document without history stays without it, as today.
- A body that cannot be a snapshot (H008, units without a usable id) is refused at 0.6+
  with `baseline()`'s messages, before anything changes (embeddings included). The 0.6
  line is unreleased, so this breaks no published behaviour; a silent fallback to a
  checkpoint would lose what 8c is for.
- An unreadable log is refused as today; the repair is WP1.
- Spec: §6.8's flatten text; §6.9's sentence on documents that need only a hash-anchored
  origin (prune, and flatten below 0.6). The H001 and H009 texts are checked for wording
  that now misleads.
- Tests: update the flatten tests that expect a checkpoint at 0.6; new: a 0.6 flatten gives
  a baseline whose snapshot verifies; flatten, then a hand edit, then `reconcile()` adopts
  the edit with its true `before` (the probe's case); a 0.5 flatten gives a checkpoint and
  keeps the version; a refused flatten leaves the document byte-identical; gc after.
- Conformance fixtures and examples that flatten are regenerated and the diff checked.

### WP3. 7b: size readers for BMP, WebP and TIFF

- `AimDocument._image_dimensions` also reads:
  - BMP: `BM`, then the header size; BITMAPCOREHEADER (12 bytes, uint16 width and height)
    or BITMAPINFOHEADER and later (int32; a negative height means top-down, take its
    absolute value).
  - WebP: `RIFF....WEBP` with a `VP8 ` (lossy frame header, 14-bit sizes after the start
    code), `VP8L` (signature 0x2F, 14-bit width-1 and height-1) or `VP8X` (24-bit canvas
    width-1 and height-1) chunk.
  - TIFF: `II*\0` or `MM\0*`, the first IFD only, tags 256 and 257 as SHORT or LONG.
- Bounded on hostile input: every offset checked against the blob's length, only the first
  IFD read, its entry count limited by what fits; anything malformed, zero or truncated
  returns `None` and never raises.
- Effects to update: `ingest`'s pack helper and its "stay inline" warning text (it must
  list only what still stays inline), `_markdown_in` and the other docstrings, README and
  CHANGELOG lines that name PNG, JPEG and GIF only.
- DOCX export: python-docx cannot embed WebP, so a stored WebP exports as the `[image:
  alt]` placeholder, exactly as an inline WebP does today. A test pins that it never
  raises. BMP and TIFF embed.
- Tests: each reader on real files written with Pillow (already a dependency through the
  docling extra; skipped when absent) plus hand-built headers; truncated and malformed
  inputs return `None`; a DOCX or Markdown import with a BMP, WebP or TIFF picture stores
  it once with its true `viewBox`; `pack_assets()` sizes them.

### WP4. The LibreOffice oracle in a container

- `scripts/oracle-docker/Dockerfile`: Ubuntu 24.04 with `libreoffice-writer` and
  `python3-uno` pinned to `4:24.2.7-0ubuntu0.24.04.7`, the versions CI installed when it
  was written. `scripts/run_oracle_docker.sh <worktree> <outdir>`: write the shapes with
  the worktree's code, then resolve and compare inside the container with `--network none`
  and the source mounted read-only, as CI does.
- CONTRIBUTING: how to run the oracle locally (on Linux, as CI does; on macOS, through the
  container, never LibreOffice's bundled Python, which macOS kills when a shell starts it).
- CI is unchanged. The runner is not run on the machine this batch is built on (no image
  builds there); it reproduced CI's result exactly when it was first written.

### WP5. Records

- Decision entries: IMPORT-D12b (flatten keeps a baseline at 0.6+, supersedes D12, whose
  status flips to superseded); the discard-history repair (answers the open question
  IMPORT-D11 left: "not yet a recovery for a corrupt log"); the 2026-11-06 approvals,
  including P10's resolution against D15b point 3. The approved entries' statuses flip from
  `todo` to `active` (approved, unreleased), with their index rows.
- The rework plan flips to `done` with a pointer here; this plan gets a status section and
  flips when the push and the review round are done.
- CHANGELOG, the PR body (the decisions marked decided, this batch, the review rounds).
- `docs/knowledge/architecture.md`: its module map says flatten always leaves a checkpoint,
  reconcile refuses flattened histories, and imports pack only PNG, JPEG and GIF. Updated to
  the version-dependent flatten, reconciliation after a flatten that leaves a baseline, and
  the six packed formats.

## Order and gates

WP0 first: its merge is the base of the rest. Then WP1 and WP2 together in one worktree
(the same code in `document.py`, sharing the snapshot checks) and WP3 in another, in
parallel; WP4 alongside. Each package is reviewed adversarially by a second agent before
integration. Integration order: WP3, then WP1+WP2. Then regenerate fixtures, examples,
parity goldens and the spec appendix where a change reaches them.

Gates before the push, on the integrated head: pytest, `ruff check`, `ruff format
--check`, `mypy src`, the spec snippets, the TS reader's tests. The build machine is
shared, so at most two test suites run at once.

Then one push, one Codex round. Not merged.

## Decisions made without the maintainer (convention 9)

| Decision | Why | Where |
|---|---|---|
| `discard_history=True` assigns ids to units without a usable id instead of refusing | Otherwise no single command repairs a file whose log is unreadable and whose body lacks ids (reconcile refuses the log) | WP1 |
| Flattening a 0.6+ body that cannot be a snapshot is refused, not downgraded to a checkpoint | Consistent with `baseline()`; a silent checkpoint would lose what 8c is for; 0.6 is unreleased | WP2 |
| The side file is `<OUT>.discarded-history.jsonl`, created exclusively, never overwritten | Next to the document as decided; numbered so a second repair keeps the first one's lines | WP1 |
| Only the repair path writes atomically; `AimDocument.save()` and the other CLI verbs keep their plain write | The repair is the one write that runs on a file already damaged; changing every save's file semantics (a new inode, permissions) is outside this PR | WP1 |
| A new public read, `history_text()` | The Python form needs a way to keep what it drops; `baseline()` keeps its return type | WP1 |
| seq after a discard is `max(0, highest readable integer seq) + 1` | Seq never goes backwards as far as the file can tell, and stays a positive integer | WP1 |
| A history-less document stays without history after `flatten()` | Unchanged behaviour; nothing to keep | WP2 |
| The container pins LibreOffice 24.2.7 while CI installs whatever Ubuntu ships | The pin reproduces the CI result it was checked against; changing CI is out of scope | WP4 |

## Risks

- The spec changes in §6.8 and §6.9 (decided by the maintainer).
- Flattened files grow 11% to 85% in tokens (accepted with 8c).
- The container runner is untested on the build machine.
- Other open PRs stacked on this branch (#42, #41, #39) will meet the flatten change when
  they rebase.

## Plan review

Round 1: Codex `gpt-6-astra` at xhigh, 2026-11-06 11:25 UTC: **REWORK**, four findings,
all applied in WP1.

1. [P1] Adoption-style id repair (`_fixup_ids` with an empty `expected_alive`) renames a
   body id a pending modify's payload names, so the proposal loses its target (P008,
   `accept_all()` fails). **Applied:** usable body ids are passed as `expected_alive`; a
   regression test combines damaged history, a valid pending modify and an unrelated unit
   without an id. (Whether reconcile's own adoption path has the same fault is checked
   separately; if it does, it goes on the tracked list as older code.)
2. [P1] Writing the side file first does not protect the document: `save()` truncates
   before it writes. **Applied:** temporary sibling, fsync, `os.replace()`; the side file
   created exclusively; failure-injection tests.
3. [P2] `AimDocument.load()` translates line endings, so the side file would not be byte
   for byte. **Applied:** bytes read and decoded before `loads()`; CRLF, lone CR and mixed
   tests.
4. [P2] "Highest seq plus one" gives 0 or a negative seq on some damaged input.
   **Applied:** `max(0, h) + 1`, booleans excluded, with tests.

Round 2: Codex `gpt-6-astra` at xhigh, 2026-11-07 17:35 UTC: **APPROVE-WITH-CHANGES**,
three findings, all applied.

1. [P2] The side file must be durable before the document is replaced. **Applied:** fsync
   the side file and its directory entry first, the directory again after the replace;
   failure injection covers the fsyncs.
2. [P2] The side file must not expose a restricted document's text. **Applied:** created
   exclusively with mode `0o600`, tested under a permissive umask.
3. [P3] `docs/knowledge/architecture.md` states the old flatten, reconcile and packing
   behaviour. **Applied:** added to WP5.


## Status, 2026-11-08

Built on a shared Linux machine, nothing merged. Every package was built in its own worktree
with failing-first tests, then checked by two independent reviewers (does it hold, what
does it break), with up to three repair rounds; the integrated head then passed the gates.

- **WP0.** Both round-3 fixes were checked from two angles; 19 findings, 13 survived an
  attempt to refute each. Four were regressions of the fixes themselves, repaired here:
  the table fix credited a restyle to rows it did not repaint (one card lost its author),
  and the move fix replayed the whole lane on every tracked export with a pending move
  (about 30x slower, output right). The rest were older code and went to the tracked list.
- **WP1, WP2.** As planned, plus: the repair rejects (with an explanation) a pending card
  that names an id it had to replace, as reconcile does, and replays the cards left when
  it replaced any id; a reserved word keeps its meaning only as "body" in the container
  role and as a theme or settings card's target. A read-only output is refused, a symlink
  stays a link. H001's text names the files that really have no history block.
- **WP3.** As planned. Stored symbols are never re-gridded: a reference packed now takes
  the frame of the symbol it points to.
- **WP4, WP5.** Done; the container runner was not run here (no image builds on this
  machine).
- **Not done here:** the check in Word itself of the export layouts (P20 and the move-order
  fix), which needs a machine with Word.

Decisions made without the maintainer, added during the build (convention 9):

| Decision | Why | Where |
|---|---|---|
| A restyle's tblPrChange is cited by a row when it was needed for the row's final paint or gives that paint on its own | Rows with no revision of their own come out the same either way; this rule credits the restyle's author least where another author's card already carries the row | `_repainted`, `convert/_docx_tracked.py` |
| The tracked export replays the lane only for a pool whose order a move, a moved or deleted block, a hand-written shell or a backward chain can change | The full replay on every export with a pending move cost up to 30x; probes over about 30,000 lanes found no difference from the full replay | `_unsettled_cards`, `export_docx.py` |
| The discard repair rejects cards naming a replaced id instead of refusing the body | Reconcile cannot run on an unreadable log, so a refusal would leave only a hand edit | `_reject_cards_naming` |
| The repaired document is parsed from newline-translated text; the side file takes the raw bytes | A CR kept in the body ends up escaped in the snapshot and the next load fails verification | `cli.py` |
| A read-only output is refused; a symlinked output is replaced at its real path | A rename would get past a write permission a plain save respects; the link stays a link | `cli.py` |
| A stored symbol is never re-gridded; a new reference takes its frame | The registry is content (P14); re-gridding changed how SDK 0.2.0 references draw | `_stored_grid`, `document.py` |
| A flatten at 0.6 keeps inline data-URI images twice (body and snapshot); documented, with `aim pack` as the remedy, not packed automatically | Packing in flatten changes what flatten writes; a proposal for the maintainer | `flatten()` docstring, CHANGELOG |
| The side file keeps the dropped lines' image references, not the images | One byte-for-byte side file as planned; a second file of symbols is a proposal for the maintainer | spec §6.8, CHANGELOG |

## The maintainer's review (2026-11-08)

Every call made without the maintainer in this batch was put to the maintainer on 2026-11-08,
and the choices the batch left open were answered:

| Call or choice | Answer |
|---|---|
| Which round-3 repairs stay: these, or the parallel run's on `tmp/aim40-r3-noopmove` and `tmp/aim40-r3-tblprchange` | Kept as merged. The maintainer asked for a swap only if the parallel run's were better; a comparison found them not better |
| Who is credited for a table restyle (`w:tblPrChange`) | Confirmed |
| When the tracked export replays the lane | Confirmed: the shortcut stays (no difference from the full replay over about 150,000 fuzzed lanes) |
| The repair and ids (`discard_history=True`) | Confirmed |
| Reserved words as ids | Confirmed |
| `flatten()` refuses a 0.6+ body that cannot be a snapshot | Confirmed |
| A stored symbol is never re-gridded; a new reference takes its frame | Confirmed |
| The repair's file handling: newline-translated parse, raw side file, atomic replace, read-only and symlinked outputs, only the repair writes atomically | Confirmed |
| The smaller calls: `history_text()`, the side file's name, seq after a discard, `baseline()`'s message, H001's text, a history-less flatten, the container's LibreOffice pin, the plan-order change | Confirmed, one by one |
| `flatten()` storing inline images once | Yes, at 0.6 and above, in the pass over the tracked list after the format work; this PR keeps the documented behaviour |
| The side file keeping the images it drops | Not now; the docs say so |
| A move left anchored on its own target by §5.4's reject fallback loses its block in Word's Accept All | Fixed in this PR: [decision](2026-11-08_1442_decision_pr40-x5-self-anchored-move.md), [plan](2026-11-08_1442_plan_pr40-x5-self-anchored-move.md) |
| Merging | Into `next`, where the 0.6 work collects until its release, on a clean ready round |

The P20 layout was revisited separately after the check in Word itself:
[W1](2026-11-08_0918_decision_pr40-p20-paragraph-per-block.md).
