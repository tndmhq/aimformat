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
| propose | `doc.propose_modify(id, markup, author=…, explanation=…)` · `propose_replace_text(id, old_text, new_text, …)` · `propose_add(markup, …)` · `propose_delete(id, …)` · `propose_move(id, …)` · `propose_theme({…}, …)` · `propose_page_setup({…}, …)` → `Proposal` (`.id`, `.resolution` when auto-accepted); every `propose_*` takes `accept=True, accept_by=aim.human(…)` (apply now, only when the person asked) |
| auto-accept (§5.6) | `doc.review_policy` → `ReviewPolicy(agents, by)` or None · `doc.set_review_policy("auto", by=aim.human(…), author=…, explanation=…)` / `(None, author=…)` · `with doc.batch(auto_accept=None/True/False): …` (accepts at the close of the outermost batch) · `doc.last_auto_accept` → `AutoAcceptOutcome(accepted, deferred, reason, batch, …)` · `doc.resolution_of(pid)` · `doc.auto_accept(pids)` · `doc.auto_accepted_batches()` |
| resolve | `doc.accept(pid, decided_by=…, applied=None, explanation=…)` · `doc.reject(pid, decided_by=…)` → resolution `Event` |
| agent note | `doc.note` · `doc.set_note()` · `doc.remove_note()` · `doc.has_canonical_note()` |
| verify / repair | `aim.lint(doc)` / `aim.lint_path(p)` → `[Finding]` · `doc.verify()` → `[problems]` · `doc.reconcile()` → `ReconcileReport` |
| compare versions | `aim.diff_documents(old, new)` → `DocumentDiff` (added/deleted/modified/moved unit ids) · `aim.classify_divergence(old, new)` → `Divergence` (new_events, new/removed proposals, history_rewritten, content_drift) |
| time travel | `doc.state_at(seq)` · `doc.checkpoint(label)` · `doc.undo(author=…, whole_batch=False)` · `doc.redo(author=…, whole_batch=False)` · `doc.revert_batch(batch, author=…)` · `doc.unrevert_batch(revert_batch, author=…)` · `doc.flatten()` (→ one checkpoint) · `doc.prune(before=…)` · `doc.baseline(label)` (current state becomes the origin; discards undo — only when asked) |
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
aim propose … --accept [--accept-for human:ID]   apply now (person asked)
aim accept FILE [PID...] [--all]
aim reject FILE [PID...] [--all]
aim review FILE [--agents auto|off --request "…" --by human:ID]
aim undo FILE [--batch B | --one]               default: the newest batch
aim redo FILE [--batch B | --one]
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
Tools: `aim_read` (`mode`: full | toc | skeleton | text | chunks; full also
carries `review` and `recent_auto_batches`), `aim_search`, `aim_edit` and
`aim_propose` (one op or an atomic `ops` batch; `aim_propose` takes `accept`,
`accept_for`), `aim_resolve`, `aim_lint`, `aim_export`, `aim_import_revision`,
`aim_review` (switch auto-accept; on needs `user_request`),
`aim_undo(path, batch)`, `aim_redo(path, batch)`. Local stdio; absolute file
paths. Every result is one compact text block. `AIMFORMAT_MCP_REVIEW=off`
stops agents from switching auto-accept on or passing `accept=true` to
`aim_propose` (`aim_resolve` and `aim_edit` are unaffected).
