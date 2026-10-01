---
date: 2026-10-01 22:52
type: decision
status: todo  # proposed — awaiting maintainer approval
related:
  - 2026-10-01_2132_decision_docx-roundtrip-three-way-drift.md
  - 2026-10-01_2132_decision_docx-roundtrip-changes-as-proposals.md
  - 2026-10-01_2252_review_docx-roundtrip-adversarial.md
---

# Decision: changes the document already contains are skipped

**Status:** proposed — awaiting maintainer approval. Implemented on
`wt/aim-docx-roundtrip` during the adversarial review.

## Context

The import compares the returned file with what was exported (three-way,
ROUNDTRIP-D9) and writes the difference against the current document. Three
cases wrote changes that were already there or were not the colleague's:

1. **Re-import after accepting.** Import a file, accept its cards, import the
   same file again: the modified units read as "changed since export"
   (conflicts) and every added paragraph was proposed a second time.
2. **Cards decided after the export.** A card pending at export (shown as a
   tracked change, or applied in an `accept-all` export) and accepted or
   rejected in the document before the file came back: the untouched file
   re-proposed it as the colleague's change (a rejected agent edit came back
   under the colleague's name; an accepted add was duplicated).
3. **Moves made after the export.** A block moved in the document after the
   export was moved back by importing an untouched file (in edits mode,
   directly), because order was compared with the current document instead
   of the exported one.

## Options considered

1. **Skip what is already in the document; compare order three-way;
   reconstruct decided cards.** A returned modify whose payload equals the
   unit now, or an addition equal to a unit added since the export (same
   container), is counted unchanged. A move is the colleague's only when the
   unit is out of order against the *exported* order, and is skipped when the
   unit already sits after the same predecessor. Cards listed in the manifest
   but resolved since are rebuilt on the reconstructed export from their
   resolution events (`proposed`, `proposed_by`, `proposed_at`, `anchor`/`to`),
   so the null round trip shows them exactly as the file does; cards that
   cannot be rebuilt (pruned history) produce a warning.
2. Record a source fingerprint on each proposal card and skip known ones —
   needs a new card attribute (format change) and still misses case 3.
3. Leave it, and document re-import as "only while the cards are pending".

## Decision

Option 1. Re-importing a file writes nothing new while its cards are
pending (as before) or once they were accepted. A change that was
**rejected** is proposed again on re-import: the document does not contain
it, and nothing in the format records that this file proposed it before.

## Consequences

- `report.unchanged` counts already-applied changes; no new report field.
- Two identical paragraphs added independently (here and by the colleague)
  at the same container collapse into one — the right outcome for a
  convergent edit.
- The tndm editor would see fewer, not different, cards from a re-upload.

## Compatibility and migration

None: behaviour of the new, unreleased `import_revision`. No `.aim` change.
