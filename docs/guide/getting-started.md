# Getting started with `.aim`

This guide takes you from `pip install` to a verified document with a
worked AI-review loop, then out to Word. Format concepts are explained as
they appear; the full detail lives in the [specification](../../spec.md).

```sh
pip install 'aimformat[docx]'
```

## 1. Create a document

```python
import aimformat as aim

doc = aim.new_document(title="Kickoff notes",
                       theme={"--aim-brand-1": "#1a73e8"})
me  = aim.human("ada")               # actors carry provenance everywhere
bot = aim.agent("model-id")          # use the exact model identifier
```

`new_document` gives you a complete, valid file: `<head>` with the
versioned stylesheet embedded (so the file renders standalone), an optional
theme block, and an empty history log.

## 2. Add content — every edit is an event

```python
title = doc.add_chunk("<h1>Kickoff notes</h1>", author=me)
para  = doc.add_chunk("<p>We met to plan the Q4 rollout.</p>", author=me)

# containers hold item chunks; pass explicit ids when you want stable ones
doc.add_chunk('<ul data-aim-container="todo">'
              '<li data-aim="t1">Confirm the vendor list</li>'
              '<li data-aim="t2">Draft the timeline</li></ul>', author=me)
```

Three things happened on each call: the payload was canonicalized (spec
§11), ids were assigned (yours are honored if valid and unused), and a
`direct_edit` event landed in the history with the payload, its anchor, and
your actor. `doc.batch()` groups several edits into one reviewable
intention:

```python
with doc.batch():
    doc.modify_chunk("t1", '<li data-aim="t1">Confirm vendors (done)</li>',
                     author=me)
    doc.delete_chunk("t2", author=me)
```

## 3. The pending lane — propose, decide

Agent edits shouldn't touch the document until a human says so. Proposals
live *in the file* as inert cards; any browser shows them as a readable
change memo.

```python
p = doc.propose_modify(para.id,
        f'<p data-aim="{para.id}">We met to plan the Q4 rollout; '
        "decision review is on 15 August.</p>",
        author=bot, explanation="Anchor the date.")

doc.proposals            # -> [Proposal(id='p-…', action='modify', …)]

doc.accept(p.id, decided_by=me)                       # apply verbatim
# doc.reject(p.id, decided_by=me)                     # drop it
# doc.accept(p.id, decided_by=me, applied="…")        # accept with tweaks
```

Accept-with-tweaks records both the model's `proposed` text and the
human-corrected `applied` text in one resolution event — attribution
without a phantom intermediate version. A second proposal on the same chunk
automatically supersedes the first; `add` proposals can anchor on other
pending adds and rebind deterministically when their anchor is decided.

## 4. Trust but verify

```python
doc.checkpoint("reviewed")      # pins (seq, label, doc_hash)
doc.verify()                    # [] — or precise chain-break findings
past = doc.state_at(2)          # a reconstructed document at seq 2
doc.undo(author=me)             # undo is a new event, never a rewrite
```

`verify()` replays the log backwards over a copy and byte-compares every
payload against the reconstructed document — this is what catches
out-of-band edits — and checks every checkpoint hash on the way.

## 5. Save, lint, render

```python
doc.save("kickoff.aim")
```

```sh
aim lint kickoff.aim        # the full verifier; exit code 1 on errors
aim show kickoff.aim        # chunks, pending lane, history
python3 -m http.server      # browsers render .aim when served as text/html
```

Note on double-clicking: local files are typed by extension, so a bare
`.aim` opened via `file://` shows source. Serve it, use the `.aim.html`
alias for sharing, or use an editor that registers the type (spec §10).

## 6. In and out of other formats

```python
# anything docling reads (PDF/DOCX/PPTX/HTML) -> .aim
from docling.document_converter import DocumentConverter
doc = aim.from_docling(DocumentConverter().convert("contract.docx").document)

# .aim -> Word, pending lane as real tracked changes
aim.to_docx(doc, "contract-reviewed.docx", pending="tracked")
```

The ingestion lands as ordinary history (an `external` actor), so an
imported document verifies like any other. The exporter never mutates your
document: `accept-all`/`reject-all` resolve a throwaway copy.

## Where to go next

- [`examples/`](../../examples) — worked prose + deck documents, generated
  by `scripts/gen_examples.py`.
- [`spec.md`](../../spec.md) — the normative rules, with every snippet
  validated in CI.
- [`tests/fixtures/`](../../tests/fixtures) — one `ok_*`/`nok_*` file per
  rule; point your own implementation at them.
