# DOCX round trip: the identity convention

Informative. This is not part of the `.aim` specification (spec §10 points
here). It describes how the reference toolkit keeps chunk ids through
`.aim` → DOCX → edited in Word → DOCX → `.aim`, so that another exporter
can write files the reference importer understands, and another importer
can read files the reference exporter writes. It may become normative once
a second implementation exists.

Namespace and version: `urn:aimformat:docx-roundtrip:1`. A change to anything
below that an existing reader would misread gets a new trailing number.

## Why it exists

A DOCX file has no field for a chunk id. Without one, a file that comes back
from a colleague imports as a new document: every id is new, and every
id-keyed tool (`aim diff`, `reconcile`, an editor's review view) reports the
whole document as replaced. With the convention, the returned file becomes a
revision of the original and only the colleague's changes show.

## Markers

Every paragraph the exporter writes for a unit carries one collapsed
bookmark (a `w:bookmarkStart` immediately followed by its `w:bookmarkEnd`),
placed after the paragraph's `w:pPr`. A unit is a top-level chunk, a
container item (a list item or table row; a run of same-id items counts as
one unit), or a container.

| bookmark name | meaning |
|---|---|
| `_aim_<id>` | the first paragraph of unit `<id>` |
| `_aim<k>_<id>` | its k-th paragraph, k ≥ 2 (a section, a figure with a caption) |
| `_aimh_<hex>` / `_aimh<k>_<hex>` | the same, for an id that cannot be a bookmark name |

- `<id>` is used verbatim when it matches `[a-z0-9_]{1,32}`, which covers
  every id the reference tooling mints, and the whole name fits Word's 40
  characters. Otherwise `<hex>` is the first 12
  hex digits of SHA-256 of the id, and a reader resolves it by hashing the
  ids it knows.
- One grammar parses every marker: `^_aim(h?)([0-9]*)_([A-Za-z0-9_-]{1,64})$`.
  Digits before the separator are the ordinal. Anything else, including
  LibreOffice's `… Copy 1` duplicates, is not a marker.
- A container's own marker sits on its first paragraph, next to its first
  item's. A table row's markers sit in its first cell.
- Names start with `_`, so Word treats them as hidden bookmarks: they do not
  show in the bookmark list unless "Hidden bookmarks" is ticked.
- Synthetic paragraphs (a page break before a slide) and paragraphs of
  pending additions exported as tracked changes carry no marker.

**Markers are hints, not identity.** The importer checks each one against
the paragraph's content and may move or ignore it. Identity is declared by
the proposals or events the import writes (spec §4.5).

## The manifest

One custom XML part (`customXml/itemN.xml`, with its `itemPropsN.xml`),
related from the main document part:

```xml
<aim:roundtrip xmlns:aim="urn:aimformat:docx-roundtrip:1"
    exporter="aimformat 0.5.2" base-doc-hash="sha256:…" base-seq="42"
    pending="tracked" salt="…32 hex…" exported-at="2026-10-01T12:00:00Z">
  <aim:u id="intro" scope="body" x="…16 hex…"/>
  <aim:u id="r1" scope="fees" shell="tbody" x="…"/>
  <aim:card id="p-ab12cd34"/>
</aim:roundtrip>
```

- `base-doc-hash` / `base-seq`: the document's `doc_hash` and `seq` at export.
- `pending`: the export's pending mode (`tracked`, `accept-all`,
  `reject-all`).
- One `aim:u` per unit, in document order. `x` is SHA-256 of
  `salt + "\0" + <the unit's canonical serialization as exported>`,
  truncated to 16 hex digits.
- One `aim:card` per proposal pending at export.
- Readers take the first part in this namespace (LibreOffice duplicates it),
  ignore other versions, refuse a part over 1 MB or one with a DTD, and
  never evaluate any of it.

**The manifest never contains document text.** It still discloses something:
anyone holding the file learns the revision count (`base-seq`), and can
confirm a guess about the exact text of a short unit, because the salt sits
in the same file. To send a file outward with no metadata, export without
marks, or remove the part with Word's Document Inspector ("Custom XML
data"). The bookmarks that remain carry only ids.

## What an importer does with it

The reference importer (`AimDocument.import_revision`, `aim import X.docx
--onto F.aim`):

1. Reconstructs the exported state from the manifest (the document at
   `base-seq`, with the cards listed in it).
2. Recomputes the null round trip N, export then import of that state, with
   the SDK that is running now. A returned unit equal to its counterpart in N
   was not edited, whatever the conversion did to it. **Changes introduced by
   the round trip itself are never recorded.**
3. Aligns the returned paragraphs to units: markers first, then exact text,
   then bounded fuzzy matching, then moves of long, unique paragraphs.
4. Replays text-only edits onto the original markup, so formatting DOCX could
   not carry survives. **Markup the export could not carry is never removed:**
   a change to a unit with structure DOCX does not show (a slide's geometry,
   a figure's extra images) applies only as a text edit, or is reported as a
   conflict.
5. Treats a unit that changed both in the file and in the document since the
   export as a conflict, using the `x` hashes.
6. Writes one batch: proposals by the colleague (default), or direct edits
   with `origin: "reconcile"` and `source: ["docx-sha256:…"]`.

A file without markers (a tool that drops bookmarks) still imports by
content. A file without the manifest is compared with the current document,
or with the state at `--base-seq`.

## Known behaviour of word processors

- LibreOffice 25.8 keeps every collapsed bookmark and the manifest through a
  re-save (it duplicates the custom XML part). The test suite carries
  LibreOffice-saved fixtures.
- Microsoft Word is documented to keep hidden bookmarks through editing and
  to keep custom XML parts unless Document Inspector removes them. This has
  not yet been checked by hand against the reference exporter; until it is,
  marks are off by default (`roundtrip_marks=False`).
- Google Docs and Pages are expected to drop the custom XML part and possibly
  the bookmarks; the import then falls back to content alignment.
