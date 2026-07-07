"""The .aim verifier (spec §12).

Registry-driven lint over four layers, all collected in one run:

- **S** structural rules (document shape, section order, chunk coverage,
  runs, container exclusivity, ids)
- **V** vocabulary rules (elements, classes, inline styles, attributes,
  theme grammar)
- **X** security rules (no executable script, no event handlers, no
  dangerous URLs or embedding elements)
- **P/H** pending-lane and history rules, including full chain
  verification (inverse replay + checkpoint hashes)
- **C** canonical-form conformance (the file byte-equals its canonical
  serialization)

Every rule has a stable code so tools and tests can target it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from . import ids
from .canonical import document_text, serialize, sha256_prefixed, sort_class_tokens
from .document import AimDocument
from .dom import Comment, Element, Text
from .errors import AimError, HistoryError, ParseError
from .events import Event
from .registry import REGISTRY

__all__ = ["Finding", "lint", "lint_text", "lint_path"]

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    code: str
    level: str          # "error" | "warning"
    message: str
    where: str = ""     # chunk/proposal id, seq, or line hint

    def __str__(self) -> str:
        loc = f" [{self.where}]" if self.where else ""
        return f"{self.level.upper()} {self.code}{loc}: {self.message}"


class _Linter:
    def __init__(self, doc: AimDocument, source_text: Optional[str]):
        self.doc = doc
        self.text = source_text
        self.state = doc._state
        self.findings: list[Finding] = []

    def add(self, code: str, level: str, message: str, where: str = "") -> None:
        self.findings.append(Finding(code, level, message, where))

    def run(self) -> list[Finding]:
        self.structure()
        self.body_sections()
        self.chunks_and_runs()
        self.vocabulary()
        self.security()
        self.proposals()
        self.history()
        self.caches()
        self.canonical_form()
        return self.findings

    # -- S: structure ----------------------------------------------------------
    def structure(self) -> None:
        html = self.state.html
        version = html.get("data-aim-version")
        if version is None:
            self.add("S001", ERROR, "<html> is missing data-aim-version")
        elif version != REGISTRY.spec_version:
            self.add("S002", WARNING,
                     f"document targets spec {version}, this tool implements "
                     f"{REGISTRY.spec_version}")
        head = self.state.head
        if not head.find(lambda e: e.tag == "meta" and e.get("charset") == "utf-8"):
            self.add("S003", ERROR, '<head> must declare <meta charset="utf-8">')
        if not head.find(lambda e: e.tag == "title"):
            self.add("S004", ERROR, "<head> must contain a <title>")
        css = self.state.css_el()
        if css is None:
            self.add("S005", WARNING, "no embedded aim.css "
                     "(<style data-aim-css>) — raw-tier rendering degrades")
        elif css.get("data-aim-css") != REGISTRY.spec_version:
            self.add("S006", WARNING,
                     f"embedded aim.css targets {css.get('data-aim-css')!r}, "
                     f"expected {REGISTRY.spec_version!r}")
        for node in self.state.body.children:
            if isinstance(node, Comment):
                self.add("S007", ERROR, "comments are not allowed in <body> "
                         "(canonical form keeps them in <head> only)")
            elif isinstance(node, Text) and node.data.strip():
                self.add("S008", ERROR,
                         f"stray text in <body>: {node.data.strip()[:40]!r}")

    def body_sections(self) -> None:
        rank_of = {"aim-proposals": 1, "aim-assets": 2}
        seen: dict[int, int] = {}
        last = 0
        for el in self.state.body.elements():
            if el.tag == "aim-proposals":
                rank = 1
            elif el.tag == "aim-assets":
                rank = 2
            elif el.tag == "script":
                stype = el.get("type") or ""
                if stype == REGISTRY.script_types["history"]:
                    rank = 3
                elif stype == REGISTRY.script_types["embeddings"]:
                    rank = 4
                else:
                    self.add("S010", ERROR,
                             f"unexpected script type {stype!r} in <body>")
                    continue
            else:
                rank = 0
                if el.chunk_id and el.container_id:
                    self.add("S012", ERROR, "an element cannot be both a chunk "
                             "and a container", el.chunk_id)
                if not el.chunk_id and not el.container_id:
                    self.add("S011", ERROR,
                             f"<body> child <{el.tag}> is neither a chunk nor "
                             "a container")
            if rank:
                seen[rank] = seen.get(rank, 0) + 1
                if seen[rank] > 1:
                    self.add("S013", ERROR,
                             f"more than one {el.tag or 'section'} of rank {rank}")
            if rank < last:
                self.add("S014", ERROR,
                         f"body section order violated at <{el.tag}> "
                         "(content → proposals → assets → history → embeddings)")
            last = max(last, rank)

    # -- chunks / containers / runs ------------------------------------------------
    def chunks_and_runs(self) -> None:
        seen_parent: dict[str, Element] = {}
        seen_container: set[str] = set()

        def visit(parent: Element, inside_container: Optional[str]) -> None:
            prev: Optional[str] = None
            for el in parent.elements():
                if el.tag == "template":
                    continue
                cid, cont = el.chunk_id, el.container_id
                if cid:
                    if not ids.is_valid_chunk_id(cid):
                        self.add("S015", ERROR, f"invalid chunk id {cid!r}", cid)
                    if cid in seen_parent and seen_parent[cid] is not parent:
                        self.add("S016", ERROR,
                                 f"chunk {cid!r} appears under multiple parents",
                                 cid)
                    elif cid in seen_parent and prev != cid:
                        self.add("S017", ERROR,
                                 f"run {cid!r} is not consecutive", cid)
                    seen_parent[cid] = parent
                if cont:
                    if not ids.is_valid_chunk_id(cont):
                        self.add("S015", ERROR,
                                 f"invalid container id {cont!r}", cont)
                    if cont in seen_container:
                        self.add("S018", ERROR,
                                 f"duplicate container id {cont!r}", cont)
                    seen_container.add(cont)
                    self.check_container(el)
                visit(el, cont or inside_container)
                prev = cid

        for top in self.state.constructs():
            pass  # coverage handled below; runs walked from body
        visit(self.state.body, None)

        dup = seen_container & set(seen_parent)
        for d in dup:
            self.add("S019", ERROR,
                     f"id {d!r} is used as both chunk and container id", d)

    def check_container(self, cont: Element) -> None:
        cid = cont.container_id or ""
        if cont.tag == "aim-slide":
            for child in cont.elements():
                if not child.chunk_id and not child.container_id:
                    self.add("S020", ERROR,
                             f"slide child <{child.tag}> is uncovered "
                             "(needs data-aim or data-aim-container)", cid)
            return
        for child in cont.elements():
            if child.tag in REGISTRY.table_shells:
                for row in child.elements():
                    if not row.chunk_id:
                        self.add("S021", ERROR,
                                 f"row <{row.tag}> in container {cid!r} lacks "
                                 "data-aim", cid)
                continue
            if child.chunk_id:
                expected = REGISTRY.item_carriers.get(child.tag)
                if expected is not None and cont.tag not in expected:
                    self.add("S022", ERROR,
                             f"<{child.tag}> chunk inside <{cont.tag}> "
                             "container", cid)
                continue
            self.add("S023", ERROR,
                     f"container {cid!r} child <{child.tag}> is uncovered", cid)

    # -- V: vocabulary ----------------------------------------------------------------
    def vocabulary(self) -> None:
        for top in self.state.constructs():
            for el in top.iter():
                self.check_element(el, context="content")
        assets = self.state.section("aim-assets")
        if assets is not None:
            for el in assets.iter():
                if el.tag == "aim-assets":
                    continue
                if el.tag not in REGISTRY.asset_content:
                    self.add("V001", ERROR,
                             f"<{el.tag}> is not allowed in the asset registry")
        sec = self.state.section("aim-proposals")
        if sec is not None:
            for card in sec.elements():
                tmpl = next((c for c in card.elements()
                             if c.tag == "template"), None)
                if tmpl is not None:
                    for el in tmpl.elements():
                        for e in el.iter():
                            if e.tag == "style":
                                continue  # theme payloads checked in proposals()
                            self.check_element(e, context="payload",
                                               where=card.get("id") or "")

    def check_element(self, el: Element, *, context: str, where: str = "") -> None:
        tag, loc = el.tag, where or (el.chunk_id or el.container_id or "")
        if tag in REGISTRY.forbidden_elements:
            self.add("X001", ERROR, f"<{tag}> is forbidden in .aim documents", loc)
            return
        if tag in ("svg", "use", "image", "symbol", "rect", "circle",
                   "ellipse", "path", "g"):
            pass  # svg subset shares the attr tables below
        elif tag not in REGISTRY.chunk_content and tag != "aim-slide":
            self.add("V002", ERROR,
                     f"<{tag}> is not in the v{REGISTRY.spec_version} element "
                     "vocabulary", loc)
            return
        allowed = REGISTRY.allowed_attrs(tag)
        for name, value in el.attrs:
            if name.startswith("on"):
                self.add("X002", ERROR,
                         f"event-handler attribute {name!r} is forbidden", loc)
                continue
            if name not in allowed and not name.startswith("data-x-"):
                self.add("V003", ERROR,
                         f"attribute {name!r} is not allowed on <{tag}>", loc)
                continue
            if name == "class" and value:
                for token in value.split():
                    if re.search(r"[\[\]]", token):
                        self.add("V004", ERROR,
                                 f"arbitrary-value class {token!r} is invalid "
                                 "(closed utility vocabulary)", loc)
                    elif token not in REGISTRY.allowed_classes:
                        self.add("V005", ERROR,
                                 f"unknown class {token!r}", loc)
            if name == "style" and value:
                self.check_style(value, loc)
            if name == "href" or name == "src":
                self.check_url(tag, name, value or "", loc)

    def check_style(self, value: str, loc: str) -> None:
        for piece in value.split(";"):
            piece = piece.strip()
            if not piece:
                continue
            if ":" not in piece:
                self.add("V006", ERROR, f"malformed style declaration {piece!r}", loc)
                continue
            prop, val = (s.strip() for s in piece.split(":", 1))
            pattern = REGISTRY.style_patterns.get(prop)
            if prop not in REGISTRY.style_prop_order:
                self.add("V007", ERROR,
                         f"style property {prop!r} is outside the geometry "
                         f"whitelist {REGISTRY.style_prop_order}", loc)
            elif pattern and not pattern.match(val):
                self.add("V008", ERROR,
                         f"style value {val!r} does not match the {prop} "
                         "grammar", loc)

    def check_url(self, tag: str, attr: str, value: str, loc: str) -> None:
        schemes = REGISTRY.url_schemes(f"{tag}.{attr}")
        if not schemes:
            return
        low = value.lower()
        if low.startswith("javascript:") or low.startswith("data:text"):
            self.add("X003", ERROR, f"dangerous URL in {tag}@{attr}: "
                     f"{value[:40]!r}", loc)
            return
        if not any(low.startswith(s.lower()) for s in schemes):
            self.add("V009", ERROR,
                     f"{tag}@{attr} must start with one of {schemes}: "
                     f"{value[:60]!r}", loc)

    # -- X: security (script blocks) -----------------------------------------------------
    def security(self) -> None:
        for el in self.state.html.iter():
            if el.tag == "script":
                stype = el.get("type") or ""
                if stype not in REGISTRY.script_types.values():
                    self.add("X004", ERROR,
                             f"executable or unknown <script> (type={stype!r}) "
                             "is forbidden")
            if el.tag == "style":
                if not el.has("data-aim-css") and not el.has("data-aim-theme"):
                    self.add("X005", ERROR,
                             "free <style> blocks are forbidden (only the "
                             "embedded aim.css and the theme block)")

    # -- theme -----------------------------------------------------------------------------
    def check_theme_block(self, raw: str, where: str) -> None:
        m = re.fullmatch(r"\s*:root\{([^{}]*)\}\s*", raw)
        if not m:
            self.add("V010", ERROR,
                     "theme block must be exactly one :root{…} rule", where)
            return
        for piece in m.group(1).split(";"):
            piece = piece.strip()
            if not piece:
                continue
            if ":" not in piece:
                self.add("V010", ERROR, f"malformed theme declaration {piece!r}",
                         where)
                continue
            name, value = (s.strip() for s in piece.split(":", 1))
            slot = REGISTRY.theme_slots.get(name)
            if slot is None:
                self.add("V011", ERROR,
                         f"{name!r} is not a registered theme slot", where)
                continue
            pattern = REGISTRY.theme_patterns.get(slot["type"])
            if pattern and not pattern.match(value):
                self.add("V012", ERROR,
                         f"theme slot {name} value {value!r} does not match "
                         f"the {slot['type']} grammar", where)

    # -- P: proposals --------------------------------------------------------------------------
    def proposals(self) -> None:
        theme_el = self.state.theme_el()
        if theme_el is not None:
            self.check_theme_block(theme_el.raw or "", "aim:theme")
        sec = self.state.section("aim-proposals")
        if sec is None:
            return
        pending_md: dict[str, str] = {}
        pending_ids: set[str] = set()
        for node in sec.children:
            if isinstance(node, Element) and node.tag != "aim-proposal":
                self.add("P001", ERROR,
                         f"unexpected <{node.tag}> inside <aim-proposals>")
        for p in self.doc.proposals:
            pending_ids.add(p.id)
        for p in self.doc.proposals:
            where = p.id
            if not ids.is_valid_proposal_id(p.id):
                self.add("P002", ERROR, f"invalid proposal id {p.id!r}", where)
            spec = REGISTRY.proposal_actions.get(p.action)
            if spec is None:
                self.add("P003", ERROR, f"unknown action {p.action!r}", where)
                continue
            if "data-for" in spec["requires"] and not p.target:
                self.add("P004", ERROR, f"{p.action} proposal needs data-for",
                         where)
            if "data-anchor-container" in spec["requires"] and \
                    not p.anchor_container:
                self.add("P005", ERROR,
                         f"{p.action} proposal needs data-anchor-container",
                         where)
            if spec["payload"] and not p.payload_html:
                self.add("P006", ERROR,
                         f"{p.action} proposal must carry a <template> payload",
                         where)
            if not spec["payload"] and p.payload_html:
                self.add("P007", ERROR,
                         f"{p.action} proposal must be payloadless", where)
            if p.target and p.target != "aim:theme" and \
                    not self.state.exists(p.target):
                self.add("P008", ERROR,
                         f"proposal targets unknown chunk {p.target!r}", where)
            if p.action in ("modify", "delete") and p.target:
                if p.target in pending_md:
                    self.add("P009", ERROR,
                             f"second pending {p.action} on {p.target!r} "
                             f"(already pending: {pending_md[p.target]})", where)
                pending_md[p.target] = p.id
            if p.action == "modify" and p.payload_html:
                if p.target == "aim:theme":
                    inner = re.sub(r"^<style[^>]*>|</style>$", "",
                                   p.payload_html.strip())
                    self.check_theme_block(inner, where)
                else:
                    root = p.payload_html
                    m = re.search(r'data-aim="([^"]+)"', root)
                    if not m or m.group(1) != p.target:
                        self.add("P010", ERROR,
                                 "modify payload id must equal data-for", where)
            if p.action == "add" and p.anchor_after:
                ok = (self.state.exists(p.anchor_after)
                      or p.anchor_after in pending_ids)
                if not ok:
                    self.add("P011", ERROR,
                             f"add anchor {p.anchor_after!r} is neither a chunk "
                             "nor a pending proposal", where)
            if p.depends_on and p.depends_on not in pending_ids:
                self.add("P012", WARNING,
                         f"data-depends-on {p.depends_on!r} is not pending",
                         where)
            if p.at and not re.match(r"^\d{4}-\d{2}-\d{2}T", p.at):
                self.add("P013", ERROR, f"data-at is not ISO-8601: {p.at!r}",
                         where)

    # -- H: history --------------------------------------------------------------------------------
    def history(self) -> None:
        el = self.state.script("history")
        if el is None:
            self.add("H001", WARNING, "no history block (flattened document)")
            return
        try:
            events = self.doc.history
        except HistoryError as exc:
            self.add("H002", ERROR, str(exc))
            return
        for ev in events:
            for problem in ev.validate():
                self.add("H003", ERROR, f"seq {ev.data.get('seq')}: {problem}",
                         str(ev.data.get("seq")))
        if not events:
            return
        seqs = [e.seq for e in events]
        if seqs[0] != 1:
            self.add("H004", WARNING,
                     f"history starts at seq {seqs[0]} (pruned)")
        for line in (el.raw or "").split("\n"):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue  # already reported via H002/H003
            from .canonical import canonical_json
            if canonical_json(obj) != line:
                self.add("H005", ERROR,
                         f"seq {obj.get('seq')}: history line is not canonical "
                         "JSON (sorted keys, compact, <\\/ escape)")
        for problem in self.doc.verify():
            self.add("H006", ERROR, problem)

    # -- caches ------------------------------------------------------------------------------------------
    def caches(self) -> None:
        meta = self.doc.meta
        if meta is not None:
            summary = meta.get("summary")
            if summary and summary.get("doc_hash") not in (None,
                                                           self.doc.doc_hash):
                self.add("M001", WARNING,
                         "aim-meta summary is stale (doc_hash mismatch)")
        for emb in self.doc.stale_embeddings():
            self.add("M002", WARNING,
                     f"embedding for chunk {emb.get('chunk')!r} is stale or "
                     "orphaned", str(emb.get("chunk")))

    # -- C: canonical form ----------------------------------------------------------------------------------
    def canonical_form(self) -> None:
        if self.text is None:
            return
        canon = document_text(self.doc._fragment)
        if self.text == canon:
            return
        got, want = self.text.split("\n"), canon.split("\n")
        for i, (a, b) in enumerate(zip(got, want)):
            if a != b:
                self.add("C001", ERROR,
                         f"file is not in canonical form (first difference at "
                         f"line {i + 1}):\n  file:      {a[:120]}\n"
                         f"  canonical: {b[:120]}")
                return
        self.add("C001", ERROR,
                 f"file is not in canonical form (line count {len(got)} vs "
                 f"{len(want)})")


# --------------------------------------------------------------------------
def lint(doc: AimDocument, *, source_text: Optional[str] = None) -> list[Finding]:
    """Lint a document object (canonical-form check only when text given)."""
    return _Linter(doc, source_text).run()


def lint_text(text: str) -> list[Finding]:
    try:
        doc = AimDocument.loads(text)
    except (ParseError, AimError) as exc:
        return [Finding("S000", ERROR, f"parse failed: {exc}")]
    return lint(doc, source_text=text)


def lint_path(path: Union[str, Path]) -> list[Finding]:
    return lint_text(Path(path).read_text("utf-8"))
