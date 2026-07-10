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
from .css import generate_aim_css
from .document import AimDocument, Anchor
from .dom import Comment, Element, Text
from .errors import (AimError, HistoryError, InvalidOperation, ParseError,
                     TargetNotFound)
from .events import _ISO_RE, Event
from .pagesetup import page_setup_from_obj, parse_doc_settings
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
        self.document_shell()
        self.body_sections()
        self.chunks_and_runs()
        self.vocabulary()
        self.pagination()
        self.doc_settings()
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
        metas = [e for e in head.elements() if e.tag == "script"
                 and e.get("type") == REGISTRY.script_types["meta"]]
        if len(metas) > 1:
            self.add("S027", ERROR,
                     "more than one aim-meta script in <head>")
        settings = [e for e in head.elements() if e.tag == "script"
                    and e.get("type") == REGISTRY.script_types["doc"]]
        if len(settings) > 1:
            self.add("D002", ERROR,
                     "more than one aim-doc script in <head>")
        for node in self.state.body.children:
            if isinstance(node, Comment):
                self.add("S007", ERROR, "comments are not allowed in <body> "
                         "(canonical form keeps them in <head> only)")
            elif isinstance(node, Text) and node.data.strip():
                self.add("S008", ERROR,
                         f"stray text in <body>: {node.data.strip()[:40]!r}")

    def document_shell(self) -> None:
        """Validate the whole parsed fragment, not just the modeled `<html>`:
        a `.aim` file is exactly one `<html>` document element and nothing
        else at the top level, its `<head>` carries a closed child
        vocabulary, and the structural chrome (`<html>`, `<head>`, `<body>`,
        `<aim-proposals>`/`<aim-proposal>`, `<aim-assets>`) is not exempt from
        the security layer. Without this, forbidden markup that sits *outside*
        the modeled body — a trailing top-level `<script>`, a `<head>` child,
        an `on*` handler on a proposal card — lints clean (review AIM-01)."""
        html_count = 0
        for node in self.doc._fragment.children:
            if isinstance(node, Element):
                if node.tag == "html":
                    html_count += 1
                else:
                    self.add("S028", ERROR,
                             f"unexpected top-level <{node.tag}> outside the "
                             "<html> document element")
            elif isinstance(node, Comment):
                self.add("S028", ERROR,
                         "comment outside the <html> document element")
            elif isinstance(node, Text) and node.data.strip():
                self.add("S028", ERROR,
                         "stray text outside the <html> document element: "
                         f"{node.data.strip()[:40]!r}")
        if html_count > 1:
            self.add("S028", ERROR, "more than one top-level <html> element")

        # structural chrome: no event handlers on <html>/<body>
        self._forbid_handlers(self.state.html, "html")
        self._forbid_handlers(self.state.body, "body")

        # <head>: closed child vocabulary + a forbidden-element / handler sweep
        # (vocabulary() never visits the head)
        for el in self.state.head.elements():
            if el.tag not in ("meta", "title", "style", "script"):
                self.add("S029", ERROR,
                         f"<{el.tag}> is not allowed in <head>", "head")
        for el in self.state.head.iter():
            if el is self.state.head:
                continue
            if el.tag in REGISTRY.forbidden_elements:
                self.add("X001", ERROR,
                         f"<{el.tag}> is forbidden in .aim documents", "head")
            self._forbid_handlers(el, "head")

        # <aim-proposals> wrapper + each card's own attributes (vocabulary()
        # only reaches the payload templates, never the card chrome)
        sec = self.state.section("aim-proposals")
        if sec is not None:
            self._forbid_handlers(sec, "aim-proposals")
            allowed = REGISTRY.allowed_attrs("aim-proposal")
            for card in sec.elements():
                if card.tag != "aim-proposal":
                    continue
                where = card.get("id") or "aim-proposals"
                for name, _ in card.attrs:
                    if name.startswith("on"):
                        self.add("X002", ERROR,
                                 f"event-handler attribute {name!r} is "
                                 "forbidden", where)
                    elif name not in allowed and not name.startswith("data-x-"):
                        self.add("V003", ERROR,
                                 f"attribute {name!r} is not allowed on "
                                 "<aim-proposal>", where)
        assets = self.state.section("aim-assets")
        if assets is not None:
            self._forbid_handlers(assets, "aim-assets")

    def _forbid_handlers(self, el: Element, where: str) -> None:
        for name, _ in el.attrs:
            if name.startswith("on"):
                self.add("X002", ERROR,
                         f"event-handler attribute {name!r} is forbidden",
                         where)

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
                stype = el.get("type")
                if stype == REGISTRY.script_types["history"]:
                    rank = 3
                elif stype == REGISTRY.script_types["embeddings"]:
                    rank = 4
                elif stype is None:
                    continue  # executable script: security() reports X004
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

        def visit(parent: Element, *, in_chunk: bool) -> None:
            prev: Optional[str] = None
            for el in parent.elements():
                if el.tag == "template":
                    continue
                cid, cont = el.chunk_id, el.container_id
                if (cid or cont) and in_chunk:
                    self.add("S024", ERROR,
                             f"{'chunk' if cid else 'container'} "
                             f"{cid or cont!r} is nested inside another "
                             "chunk — chunks never nest (§4.3)", cid or cont)
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
                visit(el, in_chunk=in_chunk or bool(cid))
                prev = cid

        visit(self.state.body, in_chunk=False)

        dup = seen_container & set(seen_parent)
        for d in dup:
            self.add("S019", ERROR,
                     f"id {d!r} is used as both chunk and container id", d)

    def check_container(self, cont: Element) -> None:
        cid = cont.container_id or ""
        for node in cont.children:
            if isinstance(node, Text) and node.data.strip():
                self.add("S025", ERROR,
                         f"stray text inside container {cid!r}: "
                         f"{node.data.strip()[:40]!r}", cid)
        if cont.tag == "aim-slide":
            for child in cont.elements():
                if child.tag == "aim-slide":
                    self.add("S026", ERROR,
                             "aim-slide nested inside a slide", cid)
                elif not child.chunk_id and not child.container_id:
                    self.add("S020", ERROR,
                             f"slide child <{child.tag}> is uncovered "
                             "(needs data-aim or data-aim-container)", cid)
            return
        for child in cont.elements():
            if child.tag in REGISTRY.table_shells and cont.tag == "table":
                for node in child.children:
                    if isinstance(node, Text) and node.data.strip():
                        self.add("S025", ERROR,
                                 f"stray text inside container {cid!r}: "
                                 f"{node.data.strip()[:40]!r}", cid)
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
                else:
                    # the registry is not exempt from the security layer:
                    # attribute allowlists, on*, and URL schemes apply here too
                    self.check_element(el, context="assets",
                                       where=el.get("id") or "aim-assets")
        sec = self.state.section("aim-proposals")
        if sec is not None:
            for card in sec.elements():
                tmpl = next((c for c in card.elements()
                             if c.tag == "template"), None)
                if tmpl is not None:
                    for el in tmpl.elements():
                        for e in el.iter():
                            if e.tag in ("style", "script"):
                                # theme/settings payloads are grammar-checked
                                # in proposals(); X004/X005 still sweep them
                                continue
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
            # the tokenizer lowercases attribute names; compare against the
            # allowlist in the re-adjusted foreign-content spelling
            canonical_name = REGISTRY.svg_case_adjust.get(name, name)
            if name not in allowed and canonical_name not in allowed \
                    and not name.startswith("data-x-"):
                self.add("V003", ERROR,
                         f"attribute {canonical_name!r} is not allowed on "
                         f"<{tag}>", loc)
                continue
            if name in ("fill", "stroke") and value and "url(" in value:
                self.add("X003", ERROR,
                         f"url() is not allowed in {tag}@{name}: "
                         f"{value[:40]!r}", loc)
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
        # scheme matching lives in REGISTRY.url_allowed (single source of
        # truth shared with converters): bare tokens match the actual
        # scheme, '#' is fragment-only, ':'-carrying tokens are prefixes
        # (review AIM-06)
        if not REGISTRY.url_allowed(f"{tag}.{attr}", value):
            self.add("V009", ERROR,
                     f"{tag}@{attr} must use one of {schemes}: "
                     f"{value[:60]!r}", loc)

    # -- D: pagination + document settings ------------------------------------------------
    def pagination(self) -> None:
        """`aim-page-break` placement: a top-level body chunk, empty, with
        explicit open+close tags (a self-closed or unclosed custom element
        would swallow its siblings in a browser's HTML parse)."""
        for top in self.state.constructs():
            for el in top.iter():
                if el.tag != "aim-page-break":
                    continue
                where = el.chunk_id or ""
                if el is not top:
                    self.add("D006", ERROR,
                             "aim-page-break must be a top-level body chunk, "
                             "not nested content", where)
                if el.self_closing:
                    self.add("D005", ERROR,
                             "aim-page-break must use explicit open+close "
                             "tags (self-closing breaks HTML parsing)", where)
                if el.elements() or any(
                        isinstance(c, Text) and c.data.strip()
                        for c in el.children):
                    self.add("D005", ERROR,
                             "aim-page-break must be empty", where)

    def doc_settings(self) -> None:
        el = self.state.script("doc")
        if el is None:
            return
        self._check_doc_settings_raw(el.raw, "aim:doc")

    def _check_doc_settings_raw(self, raw: Optional[str], where: str) -> None:
        """Shared by the live block and aim:doc proposal payloads: the JSON
        shape (D001) and the page grammars (D003/D004), with the code taken
        from the tagged validation error (see pagesetup._invalid)."""
        try:
            settings = parse_doc_settings(raw)
            page = settings.get("page")
            if page is not None:
                page_setup_from_obj(page)
        except InvalidOperation as exc:
            code = getattr(exc, "lint_code", "D001")
            if code not in ("D001", "D003", "D004"):
                code = "D001"  # only the documented codes may be emitted
            self.add(code, ERROR, str(exc), where)

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
        # aim.css is machine-managed and excluded from doc_hash (spec §10), so
        # its content is trusted at the raw tier — verify it byte-equals the
        # generated stylesheet for this spec version rather than letting an
        # arbitrary (e.g. @import) replacement pass lint-clean (review AIM-02)
        css = self.state.css_el()
        if css is not None and css.get("data-aim-css") == REGISTRY.spec_version:
            raw = css.raw or ""
            # an inert placeholder (empty or comments-only, as the spec's
            # illustrative snippets use) carries no declarations and can do no
            # harm; any real CSS must be exactly the generated stylesheet
            inert = not re.sub(r"/\*.*?\*/", "", raw, flags=re.S).strip()
            if not inert and raw.strip("\n") != generate_aim_css().strip("\n"):
                self.add("X006", ERROR,
                         "embedded aim.css does not match the generated "
                         "stylesheet for this spec version (it is "
                         "machine-managed; regenerate via dumps())")

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
        if not sec.elements():
            self.add("P014", ERROR,
                     "empty <aim-proposals> section (remove it when the "
                     "pending lane is empty)")
        pending_md: dict[str, str] = {}
        pending_ids: set[str] = set()
        for node in sec.children:
            if isinstance(node, Element) and node.tag != "aim-proposal":
                self.add("P001", ERROR,
                         f"unexpected <{node.tag}> inside <aim-proposals>")
            elif isinstance(node, Element):
                for child in node.elements():
                    if child.tag != "template":
                        self.add("P001", ERROR,
                                 f"unexpected <{child.tag}> inside a proposal "
                                 "card", node.get("id") or "")
        for p in self.doc.proposals:
            # duplicate ids make accept/reject ambiguous — one card shadows
            # the other and resolution silently touches only the first (AIM-04)
            if p.id in pending_ids:
                self.add("P017", ERROR,
                         f"duplicate pending proposal id {p.id!r}", p.id)
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
            if p.target and p.target not in ("aim:theme", "aim:doc") and \
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
                elif p.target == "aim:doc":
                    from .dom import parse_fragment
                    roots = [n for n in parse_fragment(p.payload_html)
                             if isinstance(n, Element)]
                    el = roots[0] if len(roots) == 1 else None
                    if el is None or el.tag != "script" or \
                            el.get("type") != REGISTRY.script_types["doc"]:
                        self.add("D001", ERROR,
                                 "aim:doc payload must be a single aim-doc "
                                 "script block", where)
                    else:
                        self._check_doc_settings_raw(el.raw, where)
                else:
                    from .dom import parse_fragment
                    roots = [n for n in parse_fragment(p.payload_html)
                             if isinstance(n, Element)]
                    root_id = (roots[0].chunk_id or roots[0].container_id
                               if roots else None)
                    if root_id != p.target:
                        self.add("P010", ERROR,
                                 "modify payload id must equal data-for", where)
            if p.action == "add" and p.anchor_after:
                ok = (self.state.exists(p.anchor_after)
                      or p.anchor_after in pending_ids)
                if not ok:
                    self.add("P011", ERROR,
                             f"add anchor {p.anchor_after!r} is neither a chunk "
                             "nor a pending proposal", where)
                elif p.anchor_after in pending_ids:
                    # chained add: the add it anchors on must target the same
                    # container, or the resolved anchor lands elsewhere and the
                    # proposal cannot be accepted (AIM-03)
                    anchor_prop = next((q for q in self.doc.proposals
                                        if q.id == p.anchor_after), None)
                    if anchor_prop is not None and \
                            (anchor_prop.anchor_container or "body") != \
                            (p.anchor_container or "body"):
                        self.add("P016", ERROR,
                                 f"add anchors on pending {p.anchor_after!r} "
                                 "in a different container", where)
                else:
                    # existing anchor: must be a legal insertion point in this
                    # proposal's own container, not merely exist somewhere in
                    # the document (AIM-03)
                    try:
                        self.state.resolve_insert_point(
                            Anchor(p.anchor_container or "body",
                                   p.anchor_after, shell=p.anchor_shell))
                    except (TargetNotFound, InvalidOperation):
                        self.add("P016", ERROR,
                                 f"add anchor {p.anchor_after!r} is not a valid "
                                 f"position in "
                                 f"{p.anchor_container or 'body'!r}", where)
            if p.depends_on and p.depends_on not in pending_ids:
                self.add("P012", WARNING,
                         f"data-depends-on {p.depends_on!r} is not pending",
                         where)
            if p.at and not _ISO_RE.match(p.at):
                self.add("P013", ERROR,
                         f"data-at is not ISO-8601 UTC: {p.at!r}", where)
        # chained adds must form chains, not cycles — a cycle can never
        # resolve (accept order does not exist)
        add_anchor = {p.id: p.anchor_after for p in self.doc.proposals
                      if p.action == "add"}
        for start in add_anchor:
            seen = {start}
            nxt = add_anchor[start]
            while nxt in add_anchor:
                if nxt in seen:
                    self.add("P015", ERROR,
                             "pending adds anchor on each other in a cycle",
                             start)
                    break
                seen.add(nxt)
                nxt = add_anchor[nxt]

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
        if any(not isinstance(e.data.get("seq"), int) for e in events):
            return  # a malformed seq is already reported as H003; the
            # seq-ordering and chain checks below need well-formed seqs
            # and would otherwise crash into a generic S000 (AIM-05)
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
        try:
            for problem in self.doc.verify():
                self.add("H006", ERROR, problem)
        except HistoryError as exc:
            self.add("H002", ERROR, str(exc))

    # -- caches ------------------------------------------------------------------------------------------
    def caches(self) -> None:
        try:
            meta = self.doc.meta
        except ParseError as exc:
            self.add("M003", ERROR, f"aim-meta cache: {exc}")
            meta = None
        if meta is not None:
            summary = meta.get("summary")
            if summary is None:
                self.add("M004", ERROR,
                         "aim-meta block present but has no summary (§8.1)")
            elif not isinstance(summary, dict):
                self.add("M003", ERROR, "aim-meta summary is not an object")
            elif summary.get("doc_hash") not in (None, self.doc.doc_hash):
                self.add("M001", WARNING,
                         "aim-meta summary is stale (doc_hash mismatch)")
        try:
            for emb in self.doc.stale_embeddings():
                self.add("M002", WARNING,
                         f"embedding for chunk {emb.get('chunk')!r} is stale "
                         "or orphaned", str(emb.get("chunk")))
        except ParseError as exc:
            self.add("M003", ERROR, f"embeddings cache: {exc}")

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
    """Lint document text. Never raises: hostile input becomes findings."""
    try:
        doc = AimDocument.loads(text)
    except (ParseError, AimError) as exc:
        return [Finding("S000", ERROR, f"parse failed: {exc}")]
    try:
        return lint(doc, source_text=text)
    except Exception as exc:  # last-resort net: a crash is a verifier bug,
        # but the caller still deserves a finding, not a traceback
        return [Finding("S000", ERROR,
                        f"verifier internal error (please report): "
                        f"{type(exc).__name__}: {exc}")]


def lint_path(path: Union[str, Path]) -> list[Finding]:
    try:
        # raw bytes, no universal-newline translation: canonical form is
        # byte equality (spec §11), so C001 must see CRLF as-is — and stay
        # in agreement with `aim normalize --check`, which compares bytes
        text = Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [Finding("S000", ERROR, f"cannot read {path}: {exc}")]
    return lint_text(text)
