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
| direct edits | `doc.add_chunk(markup, author=…, container="body", after=…)` · `doc.modify_chunk(id, markup, author=…)` · `doc.delete_chunk(id, author=…)` · `doc.move_chunk(id, author=…, container=…, after=…)` · `doc.set_theme({…}, author=…)` · `with doc.batch(): …` |
| propose | `doc.propose_modify(id, markup, author=…, explanation=…)` · `propose_add(markup, …)` · `propose_delete(id, …)` · `propose_move(id, …)` · `propose_theme({…}, …)` → `Proposal` (`.id`) |
| resolve | `doc.accept(pid, decided_by=…, applied=None, explanation=…)` · `doc.reject(pid, decided_by=…)` → resolution `Event` |
| agent note | `doc.note` · `doc.set_note()` · `doc.remove_note()` · `doc.has_canonical_note()` |
| verify / repair | `aim.lint(doc)` / `aim.lint_path(p)` → `[Finding]` · `doc.verify()` → `[problems]` · `doc.reconcile()` → `ReconcileReport` |
| time travel | `doc.state_at(seq)` · `doc.checkpoint(label)` · `doc.undo(author=…)` · `doc.redo(author=…)` · `doc.flatten()` · `doc.prune(before=…)` |
| caches | `doc.set_summary(text, model=…)` · `doc.generate_toc()` · `doc.set_embedding(…)` · `doc.stale_embeddings()` |
| interop | `aim.from_path(p)` (md/txt/docx/pdf/.aim) · `aim.from_text` · `aim.from_markdown` · `aim.from_docling` · `aim.to_docx(doc, p, pending=…)` · `aim.to_markdown` · `aim.to_html` · `aim.to_pdf` |

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
aim normalize FILE [-o OUT] [--check]
aim propose {modify,add,delete,move,theme} FILE …
aim accept FILE [PID...] [--all]
aim reject FILE [PID...] [--all]
aim flatten FILE [-o OUT] [--keep-embeddings]
aim reconcile FILE [--check] [-o OUT]
aim css [--stats]
aim import IN -o FILE.aim [--title T]
aim export FILE.aim -o OUT.{docx,md,html,pdf} [--pending …]
aim mcp
```

Shared flags on propose/accept/reject: `--author human:ID | agent:MODEL |
external:ID` (default `external:aim-cli`), `--explanation STR`, `-o OUT`
(default: in place), `--format text|json`. Errors go to stderr prefixed
`aim:`; exit codes 0 ok · 1 lint/domain failure · 2 usage.

## MCP server

`pip install 'aimformat[mcp]'`, then configure
`{"mcpServers": {"aimformat": {"command": "aimformat", "args": ["mcp"]}}}`
(the `aimformat` command is collision-proof; `aim` works too when nothing
else claims it).
Tools: `aim_read` (projected view), `aim_edit`, `aim_propose`,
`aim_resolve`, `aim_lint`, `aim_export`. Local stdio; absolute file paths.
