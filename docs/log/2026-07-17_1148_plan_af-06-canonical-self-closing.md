---
date: 2026-07-17 11:48
type: plan
status: done
related: []
---

# Plan: AF-06 canonical self-closing normalization

Implement the maintainer-selected durable fix for AF-06, accepting the
intentional `doc_hash` change for legacy non-void self-closing spellings.

1. Make `canonical.serialize()` ignore authored `self_closing` on non-void,
   non-foreign elements while preserving slashless HTML void elements and
   self-closed empty foreign/SVG elements.
2. Add a registry-backed lint error for authored self-closing non-void,
   non-foreign elements, including the required ok/nok fixture pair.
3. Add focused review-regression tests for canonical equivalence, hashes,
   sibling containment, and lint context behavior.
4. Update spec §11.1 and the changelog, regenerate all affected fixtures,
   examples, generated spec content, and accepted hashes, and inventory the
   resulting changes.
5. Run Ruff formatting/checks, mypy, full pytest, and example lint; commit in
   logical units, push the topic branch, and open the requested draft PR.
