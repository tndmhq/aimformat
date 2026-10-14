---
date: 2026-10-01 21:05
type: decision
status: active
related: [2026-07-22_0844_plan_literal-element-paint.md]
---

# Decision: the HTML `to_pdf` prints becomes public API (`to_print_html`)

## Problem

`to_pdf` builds a print copy of the document and hands it to Playwright's
Chromium. That print copy is more than `to_html`: it splices in the
document's `@page` rule, one CSS named page per slide sized to its canvas
(with the ×4/3 canvas-pt zoom), and the caller's `extra_css`, all placed
*before* the document's theme block so the theme keeps winning. With
`pending="accept-all"` / `"reject-all"` it also resolves the pending lane on
a throwaway copy first, so the page geometry and the slide pages follow the
resolved state.

A caller that already ships a Chromium of its own (for example an Electron
app, whose `webContents.printToPDF` prints in-process) has had two options:

- install Playwright and download a second Chromium just to call `to_pdf`;
- import the private `aimformat.convert._pdf_out._print_html`, which can
  change or vanish in any release without notice.

Neither is acceptable for a downstream app that wants the same pages
`to_pdf` produces.

## Decision

A public, keyword-only function, exported from `aimformat` and
`aimformat.convert`:

```python
def to_print_html(
    doc: AimDocument, *, pending: str = "keep", extra_css: str | None = None
) -> str: ...
```

- `to_pdf` prints exactly `to_print_html(doc, pending=pending,
  extra_css=extra_css)`. There is one implementation, so the two cannot
  drift; a test runs `to_pdf` against a recording fake Playwright and asserts
  the printed HTML is byte-equal to `to_print_html` for the same arguments.
- `pending` behaves as in `to_pdf`: `"accept-all"` / `"reject-all"` resolve
  on a throwaway copy first; any other value goes to `to_html`, which raises
  `InvalidOperation` on unknown ones.
- Stdlib only. It needs no optional extra; Playwright stays a lazy import
  inside `to_pdf`.
- The keyword-only arguments replace the private function's positional
  ones, so a later option (for example a different pending fate) can be
  added without breaking callers.
- No CLI or MCP surface: printing is a host concern, and the CLI already has
  `to_pdf`.

## Contract

- **Print settings.** To match `to_pdf` page for page, print with background
  graphics on and the CSS page size preferred (Playwright
  `print_background=True, prefer_css_page_size=True`; Electron
  `printBackground: true, preferCSSPageSize: true`).
- **Not a `.aim` file.** The output carries a free `<style>` block (lint
  X005) and must never be saved as a document.
- **Bytes are not stable across versions.** What is stable is "the HTML
  `to_pdf` prints". A caller must not diff or cache it across aimformat
  upgrades.

## Alternatives rejected

- **Keep it private.** Downstream code would import `_print_html` anyway and
  break on the next refactor, with no changelog entry to warn it.
- **Expose only the CSS** (`page_css` already is public; add the slide page
  CSS). Callers would then re-implement the splice-before-theme ordering and
  the resolve-before-CSS step, which are the subtle parts, and both have had
  regressions before.
- **A `to_pdf(engine=callable)` hook.** More surface for the same result,
  and it inverts control awkwardly for a printer that is asynchronous and
  lives in another process (an IPC call to an Electron main process).

## Status

Implemented on branch `wt/desktop-io` with tests; awaiting maintainer review
in its PR. Flip to `done` when it merges.
