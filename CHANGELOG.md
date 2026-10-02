# Changelog

All notable changes to the spec and the reference toolkit. The package
version tracks the spec version it implements (0.x minors may break).

## 0.6.0 — unreleased

Spec v0.6. Decisions IMPORT-D9 to IMPORT-D16 in `docs/log/` (2026-10-01).
Every v0.5 document stays valid and unchanged, and nothing is migrated.
Documents these importers write declare 0.6, which a 0.5 tool reports as a
newer version (S002) with an unknown event kind (H003).

### Baseline history and TOC cache (format change)

- **New `baseline` history event (§6.9).** It makes the current state the
  origin of the log and carries that state once, as a `snapshot` of exactly
  the lines `doc_hash` hashes. Verification, time travel and reconcile work
  from the file alone: reconcile's expected state is the snapshot plus the
  later events, so a hand edit to imported content no later event touched is
  still adopted with its true `before`. Theme and settings in the snapshot
  are tracked origin state. Recording a baseline raises the declared version
  to at least 0.6, with no version event (§3.7). The `<html>` line is
  compared on the declared version only: no event records `lang` or `dir`,
  so editing them by hand is not a snapshot mismatch.
- **Importers record one baseline instead of one `add` per block**
  (`from_docx`, `from_markdown`, `from_text`, `from_docling`/`from_pdf`). On
  the five DOCX fixtures a fresh import is 15–21% smaller in o200k tokens for
  text-heavy files (long-report 130,274 → 103,077) and 5–6% for image- or
  table-heavy ones, including the new TOC. The history is one line, `undo()`
  right after an import has nothing to undo (it used to delete the last
  imported block), and the save-path history replay starts from one event.
  The DOCX baseline records `source: ["sha256:<input bytes>"]`.
- **`AimDocument.baseline(label)` and `aim baseline FILE`.** SDK and CLI
  only, never an MCP tool: it discards undo and provenance. It is also the
  recovery for a file whose history no longer explains its body.
- **`flatten()` keeps one checkpoint.** It now collapses the history to a
  checkpoint at the next seq instead of deleting it. The file stays valid at
  any version (a pruned log, H004), seq never goes backwards, `verify()`
  still catches a later hand edit, and `reconcile()` refuses with the
  pruned-log message. Before, flatten followed by an SDK edit made the next
  reconcile crash, and a hand edit after flatten could not be detected. A
  document without history stays without one. `to_html()` still writes a
  page with no history.
- **Reconcile adopts a history-less file declared at 0.6 or later as one
  baseline**; below 0.6 it keeps writing one `add` per construct, so no
  declaration is raised as a side effect.
- **TOC cache (§8.1).** `summary` is optional; the TOC records
  `toc_doc_hash`; `dumps()` refreshes a present TOC the way it refreshes the
  stylesheet. Importers build the TOC when the body has a heading or slide.
  MCP `aim_read` never serves a stale outline: it derives one from the
  headings when the cache is missing or stale and says which in
  `toc_source` (both null when there is no heading or slide). New `doc.outline()` and `doc.toc_is_fresh()`.
- **Verifier.** New H007 (baseline not first, or twice), H008 (snapshot
  does not hash to its `doc_hash`, or an entry is not one conforming
  construct; snapshots get the same element, URL, handler and style checks as
  pending payloads), H009 (warning: a seq-1 log that does not explain the
  document's origin), M005 (warning: stale TOC). M004 now fires only for a
  meta block with neither summary nor TOC. A baseline under a declaration
  below 0.6 is S034. New conformance fixtures for each, plus `ok_baseline`
  and `ok_historyless`.

### Agent read and edit surface (no format change)

The agent read and edit surface: what a read costs now follows what the
agent needs, not the size of the file, and related edits land in one call.
No format, conformance or history change of its own; its spec text is
informative only (§8.3, Appendix B). Decisions: `docs/log/` entries
READS-D1…D13 (2026-10-01/02, `agent-read-*`, `agent-*`, `cli-*`, `mcp-*`,
`sdk-views-module`, `spec-partial-reads-informative`,
`elision-stubs-round-trip`).

#### Added

- **`aimformat.views`** — pure read views over a document: `units`,
  `numbering_labels` (the outline numbers of §3.8, which exist only as CSS
  counters in the file), `outline`, `resolve_refs`, `search`, and the
  renderers `render_toc`, `render_skeleton`, `render_text`, `render_chunks`,
  `render_search`; plus `elide` and `full_projection`.
- **`aim_read(mode=…)`** — `full` (default, unchanged keys), `toc` (headings,
  slides and `num-1`/`num-2` blocks with their id ranges), `skeleton` (every
  id with tag, classes and the first `words` words), `text` (a deliberately
  lossy plain-text view keyed by id, never an edit payload) and `chunks`
  (exact HTML for `ids`: chunk, container or proposal ids, `aim:theme`, or
  inclusive `a..b` ranges, each under a context line with its container,
  numbering label and pending cards).
- **`aim_search(path, query, k)`** — the seventh MCP tool: BM25 over each
  chunk's text-view rendering, numbering labels included; dotted numbers
  (`1.1.8`) stay whole, CJK text is indexed as character pairs, a term with
  no exact match matches near forms (`subprocessors` → `Subprocessor`), and
  quoted phrases filter. Standard library only.
- **Batch ops** — `aim_edit` and `aim_propose` accept `ops: [...]`:
  all-or-nothing, one history batch (or one proposal batch), `$N`
  back-references to earlier ops, at most 100 edits / 25 proposals per call.
  Single-op arguments keep working; a single `aim_edit` now returns the `id`
  it created or targeted, and `aim_propose` reports `superseded` cards.
- **`replace_text`** — change words inside a chunk without resending its
  HTML: `action: "replace_text"` with `target`, `old_text` (exactly once in
  the chunk's plain text) and `new_text`, in `aim_edit`, `aim_propose`,
  their `ops`, `aim edit|propose replace-text … --old --new`, and the SDK
  (`doc.replace_text`, `doc.propose_replace_text`). The id, attributes and
  inline markup are kept; only the changed span must sit inside one text
  run, otherwise the call is refused (a pure deletion may cross runs).
  Text inserted at a cell, list-item or line boundary stays on the side
  `old_text` was quoted from.
  Recorded as an ordinary `modify`. A proposal composes with the
  caller's own pending modify of the chunk (so successive word fixes add
  up) and refuses to replace anyone else's pending modify or delete; the
  single-proposal CLI output now lists superseded cards. On the benchmark corpus a one-word edit
  call is 2.0 to 6.0 times smaller (median per document), 3.45 times over
  613 chunks (READS-D13).
- **CLI parity** — `aim show --mode toc|skeleton|text|chunks|full` (with
  `--ids`, `--words`, `--format json`; the default overview is
  byte-identical), `aim search`, a new `aim edit
  {modify,add,delete,move,theme,batch}` verb for direct edits, and `aim
  propose batch`.

#### Changed

- **MCP results are sent once, as one compact text block** — no
  `structuredContent` and no `outputSchema` (FastMCP sent every result twice:
  structured, and as an indented JSON copy). JSON results are compact and
  keep non-ASCII text as characters. Clients reading `structuredContent`
  must parse the text block instead. On a 81-chunk contract an `aim_read`
  dropped from 13.8k to 6.4k tokens on the wire (6.5k to 5.6k in the text
  block).
- **A leaner tool list** — no generated titles, no `anyOf [T, null]`, no
  `$ref`/`$defs`, one-line descriptions, `Literal` enums (including inside
  `ops` items): 1,815 → 1,321 tokens for seven tools instead of six, guarded
  by a byte-budget test. An unknown `action` is now rejected by argument
  validation (the error lists the allowed values).
- **Elision stubs follow §8.3**: `[elided: <size>, sha256:<16 hex>]` instead
  of `[data-uri elided]`.

#### Fixed

- **Editing a chunk with an inline image no longer destroys the image.**
  The MCP and CLI write paths restore each elision stub to the data URI it
  stands for (by hash), and refuse a stub that matches nothing in the
  document; previously the stub was written into `src`.

#### Performance

- `AimDocument.chunks` is one tree walk and `chunk()` a direct lookup
  (both were quadratic). A 100-edit batch on a 775-chunk document takes
  0.35 s; the same document's `doc.chunks` went from about 0.9 s to a few
  milliseconds. Results are unchanged (pinned against the old walk).

### DOCX round trip with colleagues (no format change)

DOCX round trip with colleagues: a Word file that comes back can be imported
as a revision of the document it was exported from, with the same chunk ids,
so only the colleague's changes show. No `.aim` grammar, event, or version
change. Decisions: `docs/log/2026-10-01_2132_decision_docx-roundtrip-*.md`
(ROUNDTRIP-D1 to D13, awaiting review).

- **`to_docx(..., roundtrip_marks=True)` / `aim export --roundtrip-marks`
  / MCP `aim_export(roundtrip_marks=True)`** write one hidden point bookmark
  per exported paragraph of every unit and a text-free manifest part
  (`urn:aimformat:docx-roundtrip:1`, described in
  `docs/interop/docx-roundtrip.md`). Off by default until the convention has
  been checked against Microsoft Word; default exports are byte-for-byte
  unchanged.
- **`AimDocument.import_revision(source)` / `aim import X.docx --onto F.aim`
  / MCP `aim_import_revision`** import the returned file onto the original:
  one batch of pending proposals by `human:docx:<name from the file>`
  (or `--as edits`: direct edits with `origin: "reconcile"` and a
  `docx-sha256:` source). Conversion noise is not reported, text edits keep
  the original markup, splits and merges are linked with `data-depends-on`
  (when the first part of a split or merge is a conflict, so is the other
  part: nothing is deleted or repeated), changes colliding with edits made
  since the export are reported as conflicts, and re-importing the same file writes nothing new (unless the
  first import's proposals were rejected). Returns a
  `RevisionImportReport` (`--format json`).
- `propose_delete(..., depends_on=)` — a delete card can name the card it is
  coupled with, like the other proposal kinds.
- `aim_import_revision` is the eighth MCP tool.

## 0.5.3 — unreleased

### Word tracked changes import as proposals (no format change)

Decisions IMPORT-D1 to IMPORT-D8 in `docs/log/` (2026-10-01).

- **DOCX tracked changes are no longer lost on import.** Before, the importer
  skipped every `w:ins`/`w:del` wrapper: an inline edit came out as text that
  matched neither version ("within  days."), a moved clause vanished from both
  places, and inserted or deleted table rows arrived as rows with empty cells.
  `from_docx` now keeps the ORIGINAL text as the body and writes each tracked
  change as a pending proposal attributed to its Word author (`w:author`,
  `w:date`). The document's `accept_all()` gives Word's Accept All and
  `reject_all()` gives Word's Reject All. Two exceptions are reported: number
  labels the format stores as text keep the original numbering, and
  formatting the format cannot express produces no proposal.
- One card per changed chunk. The two versions are aligned by the source
  paragraph and row they came from, never by position. Splits and merges
  follow the surviving paragraph mark, named Word moves become `move` cards
  (plus a `modify` when the moved text was also edited), and a deleted
  paragraph directly followed by its replacement from the same author is one
  `modify`. A difference that no revision touches (for example a list
  `start` value shifted by an inserted item) never becomes a card.
- New `tracked="propose"|"accept"|"reject"` keyword on `from_docx`,
  `from_path` and the new `import_docx`, and `aim import --tracked`. The
  resolved modes import the accepted or the rejected text with no proposals.
  In `"propose"` mode, `max_revisions` (default 5,000) refuses a document
  with more revision records, and `max_proposals` (default 500) refuses one
  whose changes would become more pending cards. Writing and checking the
  lane takes time that grows with the square of its card count, and one
  revision can produce many cards (a deleted paragraph that joins two lists
  carries every item of the second list).
- **`import_docx()` returns an `ImportResult(document, report)`.** The
  `ImportReport` lists every revision with the cards that carry it (or why
  none does), every Word comment (author, date, text, anchored text, the
  chunk or card it sits on, reply threading, resolved state), and in words
  everything the import did not carry. `from_docx` keeps its return type and
  emits one `AimImportWarning` per kind of loss. Comments are reported and
  never stored: the format has no comment construct.
- The lane is written through a new internal batch-propose primitive that
  shares id minting, card serialization and the pending-lane checks with
  `propose_*`, then validated once (accept-all, reject-all, the proposals
  lint pass). An import of 800 chunks with 300 changed paragraphs takes
  about 5 s. A document whose revisions cannot be expressed raises
  `ParseError` naming the first block that diverged, instead of importing
  wrong text.
- **`to_docx(pending="tracked")` exports pending moves** of body paragraphs
  as Word move revisions (`w:moveFrom`/`w:moveTo` in a named range), so a
  Word reviewer sees them; before, a pending move exported as unchanged
  text. A moved container, figure or rule exports as a tracked deletion plus
  insertion, and a move with a pending modify of the same block lands as
  `moveTo(del(old))` plus `ins(new)`.
- **Pending row adds and row deletes now mark the row itself** (`w:trPr`).
  Without the marker, Word's Reject All left an empty row where a pending
  add had been, and Accept All left an empty row where a pending delete had
  been.
- `modify_chunk` returns its chunk view without rebuilding every chunk view
  in the document (it was O(document) per call).
- A tracked deletion that joins two lists in Word's Accept All (the paragraph
  between them deleted) while the second list also loses an item no longer
  drops that list's untouched items: they become adds into the joined list.
- **Tracked DOCX export: schema order and list numbering.** The tracked
  paragraph mark (`w:pPr/w:rPr`) was inserted as the FIRST child of `w:pPr`,
  ahead of `w:pStyle`/`w:numPr`, which the OOXML schema forbids and Word may
  report as unreadable content; it now sits where `CT_PPr` puts it, and
  `w:numPr` is ordered too. A pending add inside a numbered list exported
  without its list's `w:numPr`, so Word showed it unnumbered and Accept All
  split the list in two; it now draws from the same list definition.

## 0.5.2 — 2026-09-29

The SDK surface a live consumer needs when a `.aim` file changes under it
(an editor pane, a watcher, a CI bot): what changed, and does the file's own
history explain it. No spec, event, or format changes — SDK/CLI surface over
existing concepts (`docs/log/2026-08-06_2022_decision_editor-reload-sdk-surface.md`).

- **`diff_documents(old, new)` / `aim diff OLD NEW`** — unit-level diff
  between two parsed versions: added / deleted / modified / moved unit ids
  (chunks, runs, containers — reconcile's unit identity) plus theme /
  doc-settings / declared-version flags. Containers compare by skeleton, so
  a member edit marks the member, not every ancestor container; movers are
  the complement of the longest stable subsequence, so an insert does not
  "move" its neighbors.
- **`classify_divergence(old, new)`** — the reload-tier classifier for a
  consumer that held *old*: `new_events` (appended log suffix),
  `history_rewritten` (flatten/prune/hand rewrite), `new_proposals` /
  `removed_proposals`, and `content_drift` (the body is not explained by the
  log — the raw-write case `doc.reconcile()` adopts).
- Skill: for edits to *existing* documents, prefer the CLI/SDK/MCP paths
  over editing the file as text (each edit lands attributed and undoable at
  write time); hand-editing stays legal with `aim reconcile` right after,
  and raw-text authoring of *new* documents stays first-class.
- **A leading UTF-8 BOM parses** (tolerant reader; previously it was a
  hard parse failure). Canonical conformance stays byte-exact: `aim lint`
  reports C001 for a BOM-prefixed file, in agreement with
  `aim normalize --check`, and one normalize pass (or any canonical save)
  drops the BOM.
- **The Agent Skill installs with strict YAML loaders again.** The skill's
  `description` carried an unquoted `: `, so `npx skills add
  tndmhq/aimformat` rejected the frontmatter and installed nothing. It is
  quoted now, with a test that keeps every plain frontmatter value parseable.

## 0.5.1 — 2026-07-31

Pending-lane resolution became order-independent where it can be, and
honest where it cannot (spec §5.2, §5.4; found fixing a demo bug where two
proposals anchored on one block applied in reverse order).

- **Same-anchor position cards land in creation order.** Accepting an
  `add`/`move` rebinds every later pending position card whose anchor —
  followed through any pending chain — bottoms out at the same spot onto
  the block that just landed. For move-free lanes (adds, chains, deletes)
  every completed acceptance order now converges to the creation-order
  result, enforced by a hypothesis property in CI.
- **A move supersedes the pending move of its target**, exactly as
  modify/delete already replaced each other; the verifier enforces it
  (new rule P018, with a conformance-kit fixture).
- **A removed block takes its zone along**: cards anchored on a deleted
  block chain onto the merged zone's last pending card; an accepted move
  does the same for cards proposed before it. Chains may target pending
  moves (§5.2 grammar extended; P011/P015/P016 cover position cards
  symmetrically, including table shells and first-position anchors).
- **Chained cards accept in any order** — a child accepted first lands at
  its chain's zone and the parent lands in front on arrival. Where an
  outcome genuinely depends on an undecided earlier card, accept refuses
  with "resolve that card first" instead of guessing; creation order
  never refuses. Direct deletes refuse while pending cards anchor on the
  target, keeping undo invertible.
- Lanes interleaving move proposals into a zone remain creation-order
  guaranteed via `accept_all`, with a documented residual for out-of-order
  interleavings (§5.4). CriticMarkup/DOCX exports mirror the resolution
  order, including previously-dropped move-chained adds.
- Resolution guards are path-compressed: a 200-card chain accepts in the
  same time band as 0.5.0.

## 0.5.0 — 2026-07-26

Everything below ships as 0.5.0: the numbering work that moves the spec to
0.5, and the 0.4 typography era that was finished but never released before
it. The 0.4 constructs keep their own section below — a document that gains
one of them upgrades to 0.4, not 0.5, and that section is the only place the
file records which construct belongs to which minor.

- **Numbering (spec §3.8)** — a number written into the text is wrong the
  moment someone edits the document: insert a section and everything below it
  is misnumbered while still reading as authoritative, and every
  cross-reference to "1.1.7" now points somewhere else. So `.aim` stops
  storing computed numbers. It states an element's *place*, and the
  stylesheet draws the number.

  `num-1` … `num-9` on a block (`p`, `h1`–`h6`) advance that level and zero
  every deeper one; the marker is generated content showing the chain of
  ancestor counters (`1.`, `1.1`, `1.1.1`). Blocks stay flat siblings, so
  body text between numbered blocks changes nothing, which a list cannot
  express without either swallowing that text into an item or fragmenting
  into separate lists. Nine levels, the depth cap of the numbering models
  this maps from. `num-restart` restarts a level; `data-aim-num-prefix`
  supplies the literal in "Article 1" as data, the same kind of thing as
  `alt` on an image, rendered by one fixed rule so no per-document CSS is
  generated.

  Lists keep `<ol>`/`<ul>` and name their marker instead:
  `list-lower-alpha`, `list-upper-alpha`, `list-lower-roman`,
  `list-upper-roman`, with the suffix as `list-paren` (`1)`) or `list-bare`
  (`1`), and `list-multilevel` for items that show their ancestor chain.
  These ride the built-in `list-item` counter, which is what keeps
  `<ol start>` working; `start` is a registered attribute now. Seventeen new
  class names, so Appendix A.2 totals 264 registered utilities.

  A scheme the vocabulary cannot express (mixed formats down one chain like
  `I.A.1`, mixed separators like `1-2.3`) must be written as text instead.
  That document renders correctly and does not renumber. Generated content
  cannot read an ancestor level's *format*, and a rule per (depth × format)
  pair is how a closed vocabulary turns into per-document CSS.

  Numbering is the construct that moves the spec to 0.5, so new documents
  declare 0.5 and carry `data-aim-css="0.5"`. An older one that gains
  numbering records the upgrade the way paint and typography already do:
  adding a `num-1` block to a 0.2 document writes a `modify` on
  `aim:version` (0.2 to 0.5) in the same batch as the edit, so replay
  restores the old value and earlier checkpoints still verify.

- **Renderer requirements (spec §3.8)** — the generated stylesheet
  instantiates the nine counters `aim-c1`…`aim-c9` once, in a single
  `counter-reset` on `body`; the levels a numbered block zeroes use
  `counter-set`. A reset on a flat sibling instantiates a new counter scoped
  to that element and the ones after it rather than zeroing the one already
  in scope, so the deeper levels keep climbing. It goes wrong only from the
  second top-level block onward, so a short document looks right: the reset
  version draws `1.`, `1.1`, `1.1.1`, `1.2`, `2.` correctly and then `2.3`,
  `2.3.3`, `3.4`. Markers are `::before` rather than `::marker`, which has
  the wider support floor and fails as a wrong number rather than an
  unstyled one. A conforming renderer needs `counter-set` and `counters()`
  in `::before`.

  A renderer that mounts the document somewhere other than `<body>`, or
  re-scopes the stylesheet into a canvas, must instantiate the counters once
  on its own root, once per document. A counter that is never instantiated
  is created implicitly by each element that increments it, so blocks
  sitting in separate wrappers number independently (`1.`, `0.1`, `0.0.1`),
  and two documents sharing one root leak counters into each other.

- **V013** (error) catches a class on an element it cannot apply to. Most
  utilities mean the same thing wherever they land, so the linter never
  checked where a class sat; the numbering vocabulary is placed, and a class
  outside its place fails two different ways. `list-paren` on a `<p>` draws
  nothing, because the rule that paints the marker is
  `.list-paren > li::before` and a paragraph has no `li` children: no
  marker, and nothing on screen to say why. `num-2` on a `<td>` fails the
  other way. `.num-2::before` is a plain class selector, so the cell does
  get a number, and `.num-2` also increments `aim-c2` and zeroes
  `aim-c3`…`aim-c9`, so every number after it is wrong. Placement lives in
  the registry beside the classes it constrains, keyed by the family before
  the first hyphen, so every `num-*` and `list-*` class inherits its
  family's placement. A class with no entry stays valid anywhere, which is
  every other utility in the vocabulary. One older thing does tighten:
  `list-disc`, `list-decimal` and `list-none` shipped in 0.1.0 and belong to
  the `list` family, so they are now errors anywhere but an `<ol>` or `<ul>`,
  including on an `<li>`, where `list-style` is legal CSS.

- **S034** generalizes the era gate. It named paint and typography
  explicitly, so every era added after them went unchecked: `num-3` in a
  document declaring 0.4 linted clean, and in a 0.3 document it reached the
  scan but matched neither test. The gate now runs for any document older
  than the version the tool implements, and reports every floor the document
  retains that its declaration does not cover: S034 where the era has no
  code of its own, while S032 (paint) and S033 (typography) keep theirs. The
  floors for the numbering vocabulary are derived from the tables that
  define the class names instead of being hand-listed in `since`, because
  seventeen names is seventeen chances to forget one, and a class with no
  floor claims by omission to have existed since 0.1.

  Attributes carry floors now too. The scan read a payload's classes and
  inline styles but never its attributes, so a 0.4 document holding
  `data-aim-num-prefix` or `<ol start>` passed, and accepting a payload whose
  only new construct is an attribute recorded no version upgrade. Both
  attributes declare `since: "0.5"` in the registry beside the classes, and
  the payload scan reads them. Neither was registered as an allowed
  attribute in the first place: `data-aim-num-prefix` was specified, styled
  and emitted, yet the importer's own output failed lint with V003, so every
  contract whose clauses read "Article 1" came out invalid. `start` on `<ol>`
  was missing the same way.

- **DOCX numbering** — Word stores no label. A clause is "item at level 2 of
  instance 19", and `1.1.11` is arithmetic over the whole document, so a
  wrong walk misnumbers a contract silently. `NumberingEngine` reads
  `numbering.xml` directly rather than going through the parse layer's
  tracker, which keys counters per numbering *instance* (`w:num`) where Word
  keys them per shared *definition* (`w:abstractNum`), and which throws away
  the `w:lvl` body inside a `w:lvlOverride` (a redefinition of that level
  for one instance, not a restart). One rule replaces the three heuristics
  that came before it: counters are shared per (definition, level), and a
  `startOverride` resets that shared counter the first time its instance is
  used at that level, rather than opening a sequence of its own. Word emits
  a fresh instance whenever a list is interrupted, so one visible run of
  clauses spans several: the legal fixture's `1.1.1 … 1.1.10` sit on `numId`
  19 and `1.1.11 … 1.1.14` on `numId` 2, both drawing on abstract definition
  16, and they now run unbroken instead of restarting at 1 mid-contract. A
  deliberate "Restart at 1" still restarts.

  Around that rule: `w:numId="0"` reads as "numbering removed here" rather
  than as a valid id (three clauses of the legal fixture had been turning
  into bullet items); `w:lvlRestart` resets a level when the level it names
  or any shallower one advances (§17.9.10); a `startOverride` is also the
  value the level restarts to, not only its first-encounter seed
  (§17.9.27), which was invisible while the override equalled the level's
  own start and wrong whenever they differed; and a level whose `numFmt` is
  `none` or `bullet` draws no label instead of a fabricated one, while still
  counting, since the levels below reference its counter and render it as
  nothing. A corrupt `numbering.xml` degrades the numbering rather than
  failing the import, since `from_docx` ingests arbitrary uploads.

- **One numbering scheme, one shape** — the importer decides how a scheme is
  drawn once, for the whole scheme, before it emits anything, and a scheme
  is keyed by its abstract definition rather than its instance, since Word
  mints a fresh `w:num` every time a list is interrupted and the counters
  are shared per definition. Deciding per paragraph cannot work: Word's
  stock multilevel list defines `%1.` at its top level and `%1.%2.` below,
  so read one paragraph at a time the first is a list item and the rest are
  outline blocks. One toolbar-button list would come apart into an `<ol>`, a
  run of flat `<p>`, and a second `<ol start="2">`, with the blocks counting
  against a level nothing increments: `0.1, 0.2` where Word draws `a., b.`.

  Outline blocks (`num-1`…`num-9`) are reserved for the schemes CSS counters
  draw exactly: every level a marker shows is used, contiguous from the top,
  entered at the top, starting at 1, with the default restart. Everything
  else the list vocabulary can draw becomes one nested `<ol>`/`<ul>`
  carrying the `list-*` classes, including lettered and roman sub-levels,
  `1)` suffixes, and a scheme that starts at 3 as `<ol start="3">`. What
  neither can draw keeps the computed number as text, as §3.8 requires:
  mixed formats down one chain (`I.A.1`), a literal prefix on a flat scheme,
  and any numbered heading style expressible as neither, since a heading can
  never be an `<li>`. So does a paragraph the classification pass never
  reaches, one inside a textbox: losing the number outright is the single
  outcome the section rules out. Both passes walk table cells, because a
  numbered paragraph in a cell that goes uncounted misnumbers every clause
  after the table, not just the cell. And a bulleted list advances no
  `aim-cN` counter, so a `<ul>` between two clauses cannot collide with the
  clause scheme.

- **Imported lists** keep their markers and their count. Word counts across
  an interruption: in `sample3.docx`, one of the real-document fixtures, an
  ordered list runs 1 to 5, a bulleted sub-list interrupts, and "Columns" is
  item 6. The importer closed the run and opened a bare `<ol>`, which draws
  `1.` again, so the document held two item 1s and no item 6, in the file
  and in anything that renders it. A list that does not begin at 1 now emits
  `<ol start>`, nested lists included, and that fixture imports with
  `start="6"`, matching what Word draws. A level whose marker is not a plain
  `1.` also carries the `list-*` classes that draw what Word draws: format
  from `w:numFmt`, suffix from the `lvlText` tail, `list-multilevel` for a
  decimal chain. Without them a lettered sub-list rendered `1.` where Word
  renders `a.`. The classes ride the `<ol>` rather than the items, because
  the marker is the built-in `list-item` counter, which is also what `start`
  seeds. Word's "restart numbering" mid-list closes the run so a fresh
  `<ol>` begins, instead of the items coalescing and the numbers climbing
  past the point the document says begin again. One knock-on: the step that
  marks imported list markup as an addressable container matched the literal
  string `"<ol>"`, so the first `<ol start="6">` fell through and the whole
  list became one atomic chunk whose items lost their `data-aim` ids,
  leaving nothing for a proposal to target. It matches on the tag now.

- **`to_docx` outline numbering** — v0.5 moves the number out of the text and
  into the `num-N` classes, so the exporter has to reconstruct it or write a
  contract with no numbers at all. A numbered block now carries `w:numPr`
  against a generated multilevel `abstractNum` rather than text that looks
  like a number, so Word draws the label itself and an exported contract
  renumbers after an edit the way the `.aim` does. A `num-restart` mints its
  own `w:num` carrying a `startOverride`, and that instance stays in effect
  for the blocks after it: a `startOverride` applies on an instance's first
  use only (§17.9.27), so a shared instance would leave every restart but
  the first counting straight on. `data-aim-num-prefix` reaches the level's
  `lvlText`, the only place Word has to put a literal, escaped, since a
  prefix is authored text and may hold `&` or `<`; a prefixed level draws
  its own counter alone rather than the chain, the same rule the stylesheet
  applies. The literal belongs to the definition, and so does the counter,
  so the prefix map is accumulated forward and cut at each scheme boundary
  (`num-restart` on a top-level block, which the importer already marks):
  one scheme is one definition sharing one counter, and a scheme carrying no
  literal does not inherit the previous one's. Numbering rides the tracked
  path too, which is the default export, and Word numbers a pending deletion
  until it is accepted, so the struck original and its replacement both
  carry it.

- **`to_docx` list numbering** — ordered lists leaned on Word's "List Number"
  style, whose numbering lives in the template. The exporter wrote no
  numbering of its own, so nothing in the file looked wrong until Word drew
  it: every ordered list in the document shared one counter, and two
  independent lists continued each other; every level drew decimal (the
  stock "List Number 2" and "List Number 3" are `%1.` too), so the lettered
  and roman sub-levels the importer had just learned to record came back as
  `1.`; and `<ol start>` had nowhere to go at all. Each list tree now gets a
  numbering definition of its own, built from its `list-*` classes (format
  and suffix from the class; for `list-multilevel`, the dotted chain the
  stylesheet draws with `counters(list-item, ".")`), plus a `w:num` of its
  own carrying a `startOverride`. That override is what keeps two lists
  apart, and where `start` lands. Definitions are keyed by their level
  specs, so a document that uses the same list shape twice shares one.
  Word's stock multilevel list now survives DOCX to `.aim` to DOCX with its
  markers intact.

- **Tracked revisions mark the paragraph mark.** Tracked is the default
  export, and a paragraph the exporter builds whole as a revision (a pending
  add, or the struck original and its replacement in a pending modify) had
  its runs marked but not its paragraph mark. Word treats the mark as the
  thing that ends a paragraph, so an unmarked one outlives the reviewer's
  decision: accepting a deletion, or rejecting an insertion, left an empty
  paragraph in the delivered file, and with clause numbering that leftover
  takes a number too. Both paths now mark the mark. A pending add or modify
  of a whole list is still marked run by run, so those paragraphs still
  survive the decision.

The rest is import fidelity, measured against the five real Word documents
now committed under `tests/fixtures/docxs/`.

- **Heading styles that resolve to body text.** Legal templates hang clause
  numbering off Heading 1-9 for the outline while formatting those clauses
  exactly like body copy, so emitting `<h2>`-`<h4>` off the style name
  rendered whole contracts at heading size and weight. The name still picks
  the level; whether there is a heading at all is now read from the style's
  resolved appearance against the document's baseline run. Bold-or-bigger
  was too narrow on its own: Word's own Heading 4 and 5 are set apart by
  italic or colour at body size, and demoting those erased the outline and
  every trace of their look at once, since style-driven appearance emits no
  markup. Italic, caps, underline, colour and face count as well, and the
  test runs on every heading-styled paragraph rather than only the numbered
  ones (the same template un-numbers some of them). Two views of the run
  properties are consulted, the paragraph's effective ones and the style's
  own, and a heading survives if either reads as one: direct formatting on
  the paragraph mark merges into the effective props and can flatten an
  emphatic style without changing how a word of it looks. A clause label no
  longer leaks into the derived title, so "1. Definitions" gives a document
  titled "Definitions".

- **DOCX table styles.** A Word table usually carries its whole look in a
  table style rather than on its cells. A merged-cell sample set, a
  multi-column document and a 50-page report all imported flat, with zero
  `w:tcPr/w:shd` between them. The conditional formats in `styles.xml` are
  now resolved: the shaded header band, the alternating body bands, the last
  row, and the header's own text colour, following `basedOn` so derived
  styles inherit. The header's text colour is carried deliberately, since a
  dark fill without its light text is less readable than no fill at all; for
  the same reason a conditional that contributes a colour but no resolvable
  fill contributes nothing, a rule applied after the `basedOn` merge so a
  child style that only recolours text still completes its parent's fill.
  Which conditional applies to a row is gated by the table's own `w:tblLook`
  as in Word, including the older spelling that writes the flags only as the
  `w:val` bitmask; a cell's own shading still beats the band it sits in; and
  `themeFill`/`themeColor` resolve through the document theme, for the
  styles that name a colour without also caching its hex. Column
  conditionals, vertical banding and the corner conditions stay out of
  scope: they need the full cell-position algebra and change far less about
  how a table reads.

- **Grouped and VML pictures.** Grouped DrawingML (`wpg:wgp`) and legacy VML
  (`v:imagedata`) never reach dpc's typed model, so a title page's row of
  logos imported as nothing at all. Recovery reads them from the source XML
  and emits only what the typed run walk missed, so an ordinary inline
  picture is never doubled; one group emits one `<figure>`, because a logo
  row is a single visual unit and a figure each would stack it down the
  page. Sizing goes through every group ancestor: a picture inside a group
  is authored in the group's coordinate space, and groups nest, so the drawn
  width is the product of `ext/chExt` up that chain, with only the outermost
  group stating a real measurement. The row that motivated this is a 511px
  group whose first logo is a 606px PNG that Word draws at 210px; without
  the scaling it lands at its full 606px and swamps the page. VML says the
  same thing in `coordsize` units with no unit suffix, so a bare width there
  is a coordinate rather than points, and the VML widths that do carry a
  unit (px/in/mm/cm) convert instead of falling through a pt-only match.
  Inside a textbox the rule is narrower: recover only what dpc cannot carry.
  It models a plain `w:drawing` there but has no VML branch and no
  `mc:AlternateContent` branch, which is how Word wraps anything richer than
  an inline picture (grouped art, picture fills, SmartArt).

- **Theme font slots** come from the styles Word renders. `theme1.xml` names
  a major and a minor face, but a style may override it, and Word renders
  the style. Of the five real documents the importer is tested against,
  three name Calibri on `Normal` while their theme's minor face is Cambria,
  and a law-firm template sets its document default to Times New Roman while
  its theme declares Calibri Light / Calibri. Taken at its word, the theme
  table puts a whole document in the wrong family. The slots now come from
  the resolved styles where those have a say (the default paragraph style
  for body, the heading styles for headings) and fall back to the theme
  table where they do not, so a document whose styles defer to its theme
  keeps the theme's faces. Those faces are read out of `styles.xml`
  directly, following `basedOn`: Word's own heading styles name their font
  through the theme (`w:asciiTheme="majorHAnsi"`), and the parse layer drops
  `rFonts` outright when it sees a theme reference. The index is keyed by
  style name as well as id, since a German Word writes
  `w:styleId="berschrift1"` while keeping `<w:name w:val="heading 1"/>`, and
  an id-only lookup loses the heading face of every non-English document.
  Heading styles the document uses beat the ones its template merely
  defines, shallowest first, since Word defines all nine whether or not the
  author touched one: a document written entirely in Heading 2 no longer
  reports Heading 1's face.

- **`from_docx` fails typed on an unreadable file.** An empty zip, a
  truncated archive, or a legacy `.doc` renamed `.docx` now raises
  `ParseError`; `aim import` prints one line and exits 1. Before, the
  exception came from whichever reader sat underneath: docling's
  `ConversionError` on the 0.3.0 path, `KeyError` or
  `docx-parser-converter`'s `DocxReadError` once the native importer
  replaced it. Either way the CLI printed a traceback for ordinary bad
  input, and every caller had to know which third-party class to catch. The
  zip-slip and size guards are the one exception: they still raise
  `ValueError` with their own message, which `aim import` reports cleanly
  but `except ParseError` does not catch.

## 0.4.0 — unreleased (folded into 0.5.0)

- **Literal per-element typography (spec §3.3)** — `style` now carries
  `font-size` (points only, `^\d+(\.\d+)?pt$`) and `font-family` (a plain
  stack string in the theme font-stack grammar) alongside geometry and
  paint. px/rem/%, keywords, quotes beyond the apostrophe, `var()` and
  `!important` all fail V008; unregistered properties still fail V007.
  Same duality as paint: the role classes (`font-heading/body/mono`) and
  the type scale say *follow this document's rhythm*; a literal says
  *this face / this size, here*. Inline typography outranks every class;
  `font-size` and `font-family` inherit natively.
- **`text-justify`** joins the alignment utilities, and the type scale
  gains `text-7xl/8xl/9xl` display steps. Each type-scale step now carries
  a **normative point equivalent** (rem × 12, Appendix A.2) so point-based
  exporters agree on what a step means.
- **S033** gates the new constructs the way S032 gates paint: a document
  declared below 0.4 that retains literal typography (or a since-gated
  class) anywhere in body, pending payloads, or history must record the
  version upgrade. Upgrades now raise the declaration to the **construct's
  own floor** — paint upgrades a 0.2 document to 0.3, typography to 0.4 —
  not to the newest version the writer implements.
- **Native DOCX importer** — `from_docx` no longer routes through docling
  (whose document model carries only five boolean formatting flags, so
  fonts, sizes, colours, highlights and alignment could never survive).
  It now walks the OOXML itself via the pinned `docx-parser-converter`
  parse layer behind a single adapter seam, and preserves styling in the
  v0.4 vocabulary: the source theme (`theme1.xml` faces and accents)
  becomes the document theme slots; style-driven looks stay rhythm
  (a Heading style's bold emits no `<strong>`); local intent becomes
  literal paint/typography, `<mark>` highlights, alignment classes, and
  the classic marks. Images embed as data URIs, hyperlinks resolve,
  explicit pagination lands inline. The `docx` extra now covers both
  directions (`docx-parser-converter` + `python-docx`) — installing
  docling/torch is no longer needed to read a DOCX; the `ingest` extra
  keeps docling for `from_pdf`/`from_docling`, whose mapper also stops
  silently dropping list-group-parented tables and stops demoting
  headings in documents without a Title.
- **DOCX importer edge cases** — content dpc's model drops is recovered
  from the source XML (each body item is paired with its `w:p`/`w:tbl`
  element, so recovery is positional by construction): Strict-OOXML
  packages are normalized to Transitional namespaces so they parse at
  all; textbox paragraphs, content-control checkboxes (☑/☐), and OMML
  equations (as literal text) survive; `w:sym` glyphs map through a
  curated Wingdings→Unicode table. Table cells carry their shading fill
  (→ `background-color`) and fixed width (→ `width:NNpx`); borders are
  deliberately not carried (the vocabulary has no per-side border
  geometry).
- **`.aim.html` targets stop being flattened** — `aim export F.aim -o
  F.aim.html` (and the `aim_export` MCP tool) wrote a *converted* copy,
  because the compatibility alias (§10) shares its suffix with the `.html`
  export: history dropped, silently, under a name that promises the whole
  file. The alias is now matched ahead of the suffix table and writes the
  document itself; `--pending` other than `keep` is refused there, since
  the alias carries the lane as-is. The Agent Skill also matches
  `**/*.aim.html`, so it triggers on an alias file the way it does on a
  bare `.aim`.
- **`to_docx` export symmetry** — the round trip is now idempotent on
  styling: inline `font-size`/`font-family` → run properties, type-scale
  classes → points via the normative table, alignment classes (incl.
  justify) → Word paragraph alignment, and theme font-stack slots → the
  exported document's Normal/Heading style fonts (previously only colour
  slots reached Word).

## 0.3.0 — 2026-07-24

Everything below shipped as 0.3.0; the 0.2.1 line was never released, and
the paint work moves the spec version, so the whole unreleased set carries
the new number.

- **Literal per-element paint (spec §3.3)** — `style` now carries
  `color`, `background-color` and `border-color` alongside slide geometry,
  on a closed grammar: lowercase six-digit sRGB (`^#[0-9a-f]{6}$`) and
  nothing else. Named colours, `#rgb`, `rgb()`, `transparent`,
  `currentColor`, `var()` and `!important` all fail V008; unregistered
  properties still fail V007. Canonical order appends paint after geometry,
  so body and authored-head markup that uses none keeps its previous ordering;
  the machine-owned stylesheet cache may still refresh independently.

  This is what "make only this heading pink" needed. Before it, the only
  way to spell an arbitrary colour was to repaint one of four
  document-global brand slots — which also recolours every link and every
  other element using that slot, and which an agent seeing part of a
  document cannot choose safely. Brand classes stay, and keep their own
  meaning: a class says *follow this document's token*, a literal says
  *use this exact paint, here*. Neither is canonicalized into the other.

  Cascade is native CSS and normative: inline paint outranks every class,
  `color` inherits, `background-color` and `border-color` do not, and
  `border-color` recolours an existing border rather than creating one.

- **`aimformat.paint`** — computed paint for content trees, resolved once per
  live construct against the generated stylesheet and stored by object
  identity. It implements the real cascade, descendant base rules and
  shorthand resets included: because
  `.border-t{border-top:1px solid #e5e7eb}` is emitted after
  `.border-red-600{border-color:#dc2626}`, `class="border-t border-red-600"`
  renders GREY, and a converter matching `border-color` declarations alone
  would disagree with every browser. Structural `<body>` paint is rejected
  (V003) and never affects export because body state is neither addressable nor
  hashed.

- **DOCX paint** — text colour now **inherits** through every leaf emitter
  (blocks, list items, `pre`/`code`, table cells, figure captions, slides
  after linearization, and each tracked-change path), closing the
  documented gap where `<div class="text-red-600"><p>Child</p></div>`
  exported in default ink, and the mixed-`pre` hole where a block holding
  both loose text and a coloured `<code>` painted nothing. Backgrounds map
  to run/paragraph/cell shading (`w:shd`, real RGB — not Word's 16-value
  highlight enum) and border colour to `w:bdr`/`w:pBdr`/`w:tcBorders`.
  Word degradations, deliberate and tested: a grouping background or border
  is approximated on every emitted descendant rather than one contiguous box
  (a descendant's own border wins by side); a run has one border, not four
  sides; and `hr` keeps its em-dash rule, painted in the authored border
  colour. Descendant base paint such as `thead th` still stops inherited
  grouping paint without itself becoming explicit Word paint. Pending payloads
  carry their future ancestor selector context too, including the recorded
  table shell for a pending header row. When a base descendant background such
  as `code` masks an authored paragraph or cell background, clean export uses
  run shading for the visible authored area rather than leaking box shading
  behind the base background. In tracked mode, block and cell box paint rides
  the deleted and inserted runs because Word keeps paragraph and cell
  properties outside revisions; `accept-all` and `reject-all` retain the exact
  paragraph/cell mapping. An unpainted document still gains no explicit Word
  colour, shading or border, so it follows the recipient's template as before.
  The generated suffix for an external link uses the link's computed paint,
  so a base link colour that stops parent-colour inheritance also clears the
  suffix instead of painting only the URL in the parent's colour.

- **Version marker semantics.** `data-aim-version` is authored state that
  writers never rewrite, and a tool now warns (S002/S006) only for a
  version it does **not** implement — a 0.3 toolkit reads every 0.2
  document without complaint. Adding paint to a 0.2 document is a recorded
  upgrade: the SDK bumps the attribute AND appends a `modify` event on the
  new reserved target `aim:version`, so replay restores the old value and
  every earlier checkpoint still verifies. That event shares a batch with the
  first painted edit. A document whose history cannot record it refuses paint
  instead, and the edit is preflighted so failure leaves the older document
  unchanged. Registry `paint_since` metadata also drives S032: declaring a
  pre-v0.3 version while retaining paint in the live body, pending payloads, or
  history payloads is an error rather than a false-clean older document. Undo
  refuses that downgrade while paint remains retained; time travel below the
  upgrade stays valid because it trims the later paint-bearing events. An
  amendment that first adds paint moves the proposal card into the upgrade
  batch. Rejection, supersession, and accept-with-unpainted-tweaks inspect the
  retained `proposed` payload and share their resolution batch with the
  upgrade. Malformed history is reported as H002 rather than a generic S000
  failure during the earlier S032 precheck; malformed retained markup reaches
  H006 for the same reason. Reconcile records an out-of-band first-paint
  upgrade when the old marker is still present. If an editor hand-bumped the
  marker too and checkpoints show that the missing `before` value matters,
  reconcile refuses the ambiguous repair without mutating the document.

  Migration: none. Existing 0.2 documents stay 0.2, serialize with an
  unchanged body, and lint clean.

## 0.2.1 — unreleased (folded into 0.3.0)

- **Canonical self-closing normalization (AF-06)**: non-void elements outside
  foreign/SVG context now always serialize with explicit open and close tags;
  authored self-closing spellings are rejected by lint rule C002. HTML void
  elements remain slashless and empty SVG-context elements remain self-closed.
  By explicit owner decision, this is an intentionally incompatible canonical
  form and `doc_hash` change that is intentionally not assigned a new format
  version: it was adopted before any `.aim` documents were deployed. No
  migration or legacy-hash preservation is provided.
- **`@aimformat/reader` (`ts/`)** — the official TypeScript read library:
  parses a canonical `.aim` document into a read-only projection (ordered
  node tree with recursive container members, chunks with first-class
  runs, proposals, theme/page setup, `docHash`) mirroring the Python
  SDK's read surface. Zero dependencies, no build step, one code path in
  browsers and Node; writes stay with the Python SDK. A parity suite
  (`tests/parity/`) pins both implementations to committed goldens —
  field-for-field projections plus byte-exact `docHash` across the
  examples, edge fixtures, and the conformance kit. No spec change.
- **`AimDocument.amend_proposal(pid, markup=None, *, explanation=None,
  at=None)`** — in-place amend of a pending proposal's payload and/or
  explanation, preserving id, anchor, author, batch, and dependencies.
  Implements what spec §5.4 already sanctions ("editing a pending payload
  in place is allowed and unrecorded"): no history event is appended;
  payload validation matches the original propose path (add payloads keep
  the proposed root id, so chained anchors stay stable). delete/move
  proposals are explanation-only. No spec change.

Fixed-layout-pages fixes (2026-07-16, final review round on the PR):

- **Accepting a modify validates the whole payload**: a hand-authored
  card whose payload hid a second root element behind a valid first one
  was written wholesale, corrupting the document past lint and history
  verification. Accept now re-validates every root (id, kind, run shape,
  nested ids) and rejects what the SDK would never have proposed; the
  linter's P010 likewise checks every payload root, so such cards fail
  `aim lint` while still pending. When the written form differs from the
  card (a tweak, or a non-canonical hand-authored payload), the
  resolution event records it as `applied`.
- **DOCX**: an empty slide keeps its page (one placeholder paragraph)
  instead of silently vanishing whenever its neighbor was another slide.
- **SDK**: `add_chunk`/`propose_add` of an `aim-slide` carrying a
  caller-supplied id on `data-aim` now moves the id to
  `data-aim-container` (slides are always containers) instead of minting
  an S031-failing document.
- **PDF**: a slide that omits its inline canvas size now gets the
  resolved default box (`960×540`) in the print CSS instead of
  collapsing to zero height and printing a blank page.

Exporter and MCP fixes (2026-07-16 deep-review round 2):

- **DOCX tracked changes**: a pending add of a whole `ul`/`ol`/`table`
  now exports as an inserted list/table instead of flattening to empty
  paragraphs; `<br>` survives inside tracked ins/del runs; a lint-clean
  table whose `rowspan`/`colspan` overruns the grid no longer crashes the
  export (the grid widens/clamps instead); chained and sibling row-adds
  land in accepted order rather than reversed; a run-chunk list item (or
  table row) with a pending modify emits its payload exactly once instead
  of once per member.
- **Markdown (CriticMarkup)**: pending adds anchored inside a slide are
  now emitted (previously silently dropped).
- **MCP server**: optional `AIMFORMAT_MCP_ROOT` confines every path
  argument (including export destinations) to one directory tree; unset
  keeps the local trusted-client default. Tool descriptions now state the
  local-only trust model.

Fixed-layout pages: slides become correct pages end to end.

- **PDF**: each `aim-slide` prints as its own page **at its own canvas
  size** via per-slide CSS named pages (previously slides landed clipped
  on the document's global page). Flowing content keeps the `aim:doc`
  page setup; mixed documents interleave both.
- **Canvas-pt convention** (spec §3.3, informative): canvas px are
  point-equivalent at print — `960×540` is the native 16:9 slide,
  paper pages are their point size (A5 portrait `420×595`). Examples,
  fixtures, and spec snippets regenerated; new `examples/booklet.aim`
  shows fixed-layout A5 paper pages with a positioned image figure.
- **DOCX**: slides now **linearize** (page break + chunks in reading
  order, in-slide proposals ride the tracked-changes lane) instead of
  being silently dropped — a deck previously exported as an empty
  document. Figures honor an authored inline-style width (CSS px at
  96 dpi, clamped to the content box) instead of a hardcoded 4.5 in.
  In tracked mode, a pending add anchored after a slide starts on the
  following page (like accepted content), and a pending whole-slide add
  linearizes per block instead of collapsing into one inserted paragraph.
  An explicit `aim-page-break` immediately before a slide no longer
  doubles into a blank Word page.
- **SDK/linter**: a payload whose root is a bare `aim-slide` (no identity
  markers) now always takes the container path — `add_chunk`/proposals
  previously demoted it to an opaque *chunk* with unaddressable children,
  and the linter accepted the result. New rule **S031** (error):
  `aim-slide` marked as a chunk. `to_markdown` gains
  `pending="accept-all"/"reject-all"` (resolve-on-a-copy, like DOCX/PDF),
  and `aim export --pending` accepts the two modes for `.md` as well.
  Spec §3.3 now credits the canvas-pt print scale to the PDF exporter
  explicitly (the frozen v0.2 embedded print layer stays CSS-native;
  folding the scale in is deferred to a future stylesheet revision).
  Replacements now keep the target's kind: an `aim-slide` payload can
  never replace a chunk (and a container never becomes a flat block) —
  `modify_chunk`, `propose_modify`, and the accept path all reject what
  would fail V003/S031 on the next lint, including proposals authored
  by external tools.

## 0.2.0 — 2026-07-10

First release published to PyPI: `pip install aimformat`.

- **Pagination** (spec §3.6): `<aim-page-break></aim-page-break>` — the
  hard page break as an ordinary empty top-level chunk: addressable,
  movable, proposable, undoable (explicit open+close tags required;
  placement enforced top-level). And the `aim:doc` settings block (head
  script, `application/aim-doc+json`) defining page setup: registered
  named size, orientation, per-side mm margins (defaults = A4 portrait
  15 mm — the previous hardcoded PDF geometry). Whole-block modify
  semantics exactly like `aim:theme`: events, undo/redo, proposals,
  accept-with-tweaks. `doc_hash` covers the settings line when present;
  documents without it hash byte-identically to v0.1.
- **The agent note** (spec §2.5): every new/imported document opens with a
  declarative head comment (`aim-note:`) telling LLM agents what the file
  is, where the docs live (aimformat.com/llms.txt), and the hand-editing
  invariants. Informative-only by spec — tools never execute anything
  because of it. SDK: `doc.note` / `set_note()` / `remove_note()` /
  `has_canonical_note()`; CLI: `aim note FILE... [--check|--remove]`;
  linter: S030 (warning) flags duplicate notes. The note text contains no
  markup, so structural substring checks never false-positive on it.
- **Pending-lane CLI verbs**: `aim propose {modify,add,delete,move,theme}`,
  `aim accept` / `aim reject` (by id or `--all`), with `--author human:ID |
  agent:MODEL | external:ID` attribution (`aim.parse_actor`), and
  `aim show --format json` for machine reads.
- **Format converters**: `from_text`, `from_markdown`, `from_docx`,
  `from_pdf`, and extension-dispatched `from_path`; `to_markdown`, `to_html`,
  `to_pdf`, and the existing `to_docx`. The matching CLI verbs are `aim import`
  and `aim export`; non-stdlib dependencies remain behind optional extras.
- **Canonical normalization**: `aim normalize FILE [-o OUT] [--check]`
  rewrites a loadable document in the spec §11 canonical form, or checks it
  without writing. The operation is idempotent. Lint the authored file first:
  normalization can discard invalid declarations and their diagnostic evidence.
- **MCP server**: `pip install 'aimformat[mcp]'` (pinned `mcp==1.28.1`)
  then `aim mcp` — local stdio, six workflow tools: `aim_read` (projected
  view), `aim_edit`, `aim_propose`, `aim_resolve`, `aim_lint`,
  `aim_export`.
- **Agent Skill** under `skills/aimformat/` (open Agent Skills standard):
  `npx skills add tndmhq/aimformat`, or in Claude Code
  `/plugin marketplace add tndmhq/aimformat` (`.claude-plugin/` manifest).
- **docs/for-agents.md** — the canonical LLM-facing guide, served as
  https://aimformat.com/llms.txt.
- **evals/** — id-preservation harness measuring invariant compliance of
  naked LLM edits with vs without the agent note.
- **Packaging**: second console script `aimformat` (AimStack `aim`
  collision), version single-sourced from `__init__.py`, PyPI
  trusted-publishing workflow (`.github/workflows/publish.yml`).
- **Reconcile** (spec §6.8): `AimDocument.reconcile()` and `aim reconcile
  FILE [-o OUT] [--check]` detect out-of-band edits — hand edits,
  corruption, files that never had history — and repair the document by
  appending `origin:"reconcile"` events (external author) that declare the
  current body truth, so `verify()` passes again. Assigns ids where missing
  or conflicting, rejects pending proposals whose target vanished, reports
  unrepairable log damage as `residual`. Also the adoption path for
  hand-written `.aim` files. Refuses pruned or damaged logs
  (`HistoryError`) rather than guessing at an unrecoverable baseline.

## 0.1.0 — 2026-07-07

First published draft of the specification and the reference toolkit.

- **Spec** (`spec.md`): document anatomy, closed HTML/Tailwind-subset
  vocabulary, semantic chunks with runs and containers, the pending lane
  (template-inert proposals with attribution and deterministic
  supersede/chain semantics), append-only invertible history with
  checkpoint hashing, versioned-state-vs-caches split, retrieval layer,
  content-addressed assets, canonical form + `doc_hash`, security
  constraints, conformance rules. Generated construct-reference appendix;
  every snippet validated in CI.
- **SDK** (`aimformat`, stdlib-only): `AimDocument` with direct edits,
  batches, proposals (accept / accept-with-tweaks / reject / supersede),
  undo/redo, checkpoints, `verify()`, `state_at()`, flatten/prune, asset
  pack/gc, summary/TOC/embedding caches.
- **Verifier**: `aim lint` — structure, vocabulary, security, pending-lane,
  history-chain, and canonical-form rules with stable codes; JSON output.
- **CLI**: `aim lint | hash | new | show | flatten | css`.
- **Interop**: `from_docling()` (DoclingDocument → .aim, dependency-free)
  and `to_docx()` (.aim → Word, pending lane as real `w:ins`/`w:del`
  tracked changes or resolved on a copy; `[docx]` extra).
- **Conformance suite**: `tests/fixtures/ok_*` / `nok_<CODE>_*`, one rule
  per file; 260+ tests.
