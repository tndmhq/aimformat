---
date: 2026-07-18 04:24
type: report
status: done
related:
  - 2026-07-18_0251_plan_pr17-p2-parity-fixes.md
---

# Report: PR #17 Codex minor parity fixes (TS reader vs Python reader)

Three Codex minor findings on PR #17, all TS-vs-Python divergences on
non-canonical / hand-edited input. Ruling from the P2 round applied
throughout: verify Python empirically on every supported interpreter
first; where CPython versions disagree, match the spec-correct
(HTML5 / 3.13+) side and pin with TS unit tests only — goldens must stay
byte-identical across the 3.10–3.13 CI matrix.

1. **Attribute-mode semicolonless charrefs stay literal unless exact**
   (`ts/src/parser.ts`, `decodeAttrRefs`). Python 3.13+ replaced full
   `html.unescape` on attribute values with `_unescape_attrvalue`:
   numeric refs always decode; a named ref decodes only on an exact
   `html5_entities` match (name, or name+`;`) and never when followed by
   `=` — so `title="&quothi"` and `href="?a=1&ampb=2"` stay literal
   while `&amp b` still decodes. ≤3.12 prefix-decoded (version-
   dependent) → TS unit tests only, no golden. Body text unchanged.
2. **Raw-text end tags scan case-insensitively with a tag boundary**
   (`ts/src/parser.ts`, `rawText`). Ported the 3.13+ CDATA rule
   `</tag(?=[\t\n\r\f />])` (case-insensitive) + consume-through-`>`.
   `</SCRIPT>` / `</STYLE >` close and `</scriptx>` is data on every
   CPython → pinned by the `noncanonical-rawtext-closes` fixture+golden
   (diffed identical under 3.10–3.14); `</ script>` (data) and
   `</script/>` (closes) are 3.13+-only → TS unit tests.
3. **Nested chunk shadowed by a top-level id reports container "body"**
   (`ts/src/document.ts`, `chunkView`/`isTopLevelId`). Python
   `container_of_chunk` consults `top_index` (chunk OR container id)
   before walking the first hit's ancestry; html/text keep coming from
   `find_chunk`'s first (nested) group and per-container member views
   stay local. Version-stable → `noncanonical-dup-top-level`
   fixture+golden (identical under 3.10–3.14) + TS unit test.

Lessons promoted to `docs/knowledge/architecture.md` (TS-parity items
4–5). Commits: ad53a5e, 2685d34, 3604e09.
