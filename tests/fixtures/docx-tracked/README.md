# Tracked-change shapes

The DOCX importer turns Word tracked changes into pending proposals. Its
tests do not trust the importer's own reading of the revision markup.
Instead, each shape in [`tests/tracked_docx_kit.py`](../../tracked_docx_kit.py)
states what Word leaves behind after **Reject All** and after **Accept All**,
written by hand from ECMA-376 Part 1 §17.13.5. Those two lists of texts are
the goldens.

The suite builds the shapes in memory. To check the goldens against a real
word processor, write the shapes out as files:

```sh
python3 scripts/gen_docx_tracked.py            # writes tests/fixtures/docx-tracked/out/
```

Each shape gives three files: `NAME.docx`, `NAME.reject.txt` and
`NAME.accept.txt`. The `out/` directory is not committed, because
python-docx stamps every file with the time it was written.

## Confirming a shape by hand

In Microsoft Word:

1. Open `NAME.docx`.
2. Choose **Review → Reject → Reject All Changes**.
3. Compare the paragraphs, list items and table rows with
   `NAME.reject.txt`, one line each. A table row reads as its cells run
   together.
4. Close without saving, open the file again, and choose
   **Review → Accept → Accept All Changes**. Compare with
   `NAME.accept.txt`.

In LibreOffice Writer, use **Edit → Track Changes → Reject All** and
**Accept All** instead.

Two shapes differ from Word on purpose, and the import report says so:

- `baked_numbering_insertion`: a number label the format cannot draw is
  stored as text, so labels keep the original numbering after Accept All.
  Word renumbers them.
- `textbox_revision`: the synthetic textbox has no picture, so the
  importer writes a `[picture: Box]` placeholder in both views.

When a shape disagrees with Word for any other reason, the golden is wrong.
Fix the shape's declaration in the kit, not the importer's expectations.
