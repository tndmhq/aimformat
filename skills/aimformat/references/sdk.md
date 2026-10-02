# aimformat SDK + CLI reference (condensed)

`pip install aimformat` — Python ≥ 3.10, zero runtime dependencies.
Console scripts: `aim` and `aimformat` (identical; the alias avoids the
name collision with AimStack's `aim`). `python -m aimformat.cli` also works.

## Python API

| Operation | Call |
|---|---|
| load / parse | `aim.load(path)` · `aim.loads(text)` |
| create | `aim.new_document(title=…, lang="en", theme={…})` |
| save / serialize | `doc.save(path)` · `doc.dumps()` (canonical) |
| read | `doc.title` · `doc.chunks` · `doc.chunk(id)` · `doc.containers` · `doc.proposals` · `doc.proposal(pid)` · `doc.history` · `doc.meta` · `doc.theme` · `doc.doc_hash` · `doc.seq` |
| actors | `aim.human("ada")` · `aim.agent("model-id")` · `aim.external("tool")` · `aim.parse_actor("agent:model-id")` |
| direct edits | `doc.add_chunk(markup, author=…, container="body", after=…)` · `doc.modify_chunk(id, markup, author=…)` · `doc.replace_text(id, old_text, new_text, author=…)` (words inside a chunk, markup kept) · `doc.delete_chunk(id, author=…)` · `doc.move_chunk(id, author=…, container=…, after=…)` · `doc.set_theme({…}, author=…)` · `with doc.batch(): …` |
| propose | `doc.propose_modify(id, markup, author=…, explanation=…)` · `propose_replace_text(id, old_text, new_text, …)` · `propose_add(markup, …)` · `propose_delete(id, …)` · `propose_move(id, …)` · `propose_theme({…}, …)` → `Proposal` (`.id`) |
| resolve | `doc.accept(pid, decided_by=…, applied=None, explanation=…)` · `doc.reject(pid, decided_by=…)` → resolution `Event` |
| agent note | `doc.note` · `doc.set_note()` · `doc.remove_note()` · `doc.has_canonical_note()` |
| verify / repair | `aim.lint(doc)` / `aim.lint_path(p)` → `[Finding]` · `doc.verify()` → `[problems]` · `doc.reconcile()` → `ReconcileReport` |
| compare versions | `aim.diff_documents(old, new)` → `DocumentDiff` (added/deleted/modified/moved unit ids) · `aim.classify_divergence(old, new)` → `Divergence` (new_events, new/removed proposals, history_rewritten, content_drift) |
| time travel | `doc.state_at(seq)` · `doc.checkpoint(label)` · `doc.undo(author=…)` · `doc.redo(author=…)` · `doc.flatten()` (→ one checkpoint) · `doc.prune(before=…)` · `doc.baseline(label)` (current state becomes the origin; discards undo — only when asked) |
| caches | `doc.set_summary(text, model=…)` · `doc.generate_toc()` (stored with `toc_doc_hash`, kept fresh by `dumps()`) · `doc.outline()` (live, no cache) · `doc.set_embedding(…)` · `doc.stale_embeddings()` |
| agent read views (`aimformat.views`) | `views.render_toc(doc)` · `views.render_skeleton(doc, words=8)` · `views.render_text(doc)` (lossy, reading only) · `views.render_chunks(doc, ["id", "a..b"])` · `views.search(doc, query, k=8)` → `[Hit]` · `views.outline(doc)` → `[OutlineEntry]` · `views.units(doc)` → `[Unit]` · `views.numbering_labels(doc)` → `{id: "1.1.8"}` · `views.resolve_refs(doc, refs)` · `views.elide(html)` · `views.full_projection(doc)` |
| interop | `aim.from_path(p)` (md/txt/docx/pdf/.aim) · `aim.from_text` · `aim.from_markdown` · `aim.from_docx(p, tracked="propose"\|"accept"\|"reject")` · `aim.import_docx(p)` → `ImportResult(document, report)` · `aim.from_docling` · `aim.to_docx(doc, p, pending=…)` · `aim.to_markdown` · `aim.to_html` · `aim.to_pdf` |

Notes: `after=` accepts an id, `None` (first position), or the default
`aim.LAST` (end of container). Direct edits and resolutions append history
events automatically. `authors` are required on every mutation — pass
`aim.agent("<your-exact-model-id>")`.

## CLI

```
aim lint FILE... [--format json] [--quiet]      exit 1 on errors
aim hash FILE
aim new -o FILE [--title T] [--lang L]
aim note FILE... [--check | --remove] [--format json]
aim show FILE [--format json]
aim show FILE --mode toc|skeleton|text|chunks|full [--ids ID,A..B] [--words N] [--format json]
aim search FILE QUERY [-k N] [--format json]
aim normalize FILE [-o OUT] [--check]
aim propose {modify,add,delete,move,theme} FILE …
aim propose batch FILE OPS.json|-                 up to 25 cards, all-or-nothing
aim edit {modify,add,delete,move,theme} FILE …    direct edits, same arguments
aim edit batch FILE OPS.json|-                    up to 100 edits, one history batch
aim accept FILE [PID...] [--all]
aim reject FILE [PID...] [--all]
aim flatten FILE [-o OUT] [--keep-embeddings]
aim baseline FILE [--label L] [--author A] [-o OUT]
aim reconcile FILE [--check] [-o OUT]
aim diff OLD NEW [--format json]
aim css [--stats]
aim import IN -o FILE.aim [--title T] [--tracked propose|accept|reject]
aim export FILE.aim -o OUT.{docx,md,html,pdf} [--pending …]
aim mcp
```

Shared flags on propose/edit/accept/reject: `--author human:ID | agent:MODEL |
external:ID` (default `external:aim-cli`), `--explanation STR`, `-o OUT`
(default: in place), `--format text|json`. Errors go to stderr prefixed
`aim:`; exit codes 0 ok · 1 lint/domain failure · 2 usage.

## MCP server

`pip install 'aimformat[mcp]'`, then configure
`{"mcpServers": {"aimformat": {"command": "aimformat", "args": ["mcp"]}}}`
(the `aimformat` command is collision-proof; `aim` works too when nothing
else claims it).
Tools: `aim_read` (`mode`: full | toc | skeleton | text | chunks),
`aim_search`, `aim_edit` and `aim_propose` (one op or an atomic `ops`
batch), `aim_resolve`, `aim_lint`, `aim_export`. Local stdio; absolute file
paths. Every result is one compact text block.
