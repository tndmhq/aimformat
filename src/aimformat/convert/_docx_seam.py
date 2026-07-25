"""The docx-parser-converter adapter seam (extra ``docx``).

Every import of ``docx_parser_converter`` in this package lives HERE, and
only the converter module (``_docx_in``) consumes what this module exposes —
the dependency's Pydantic models never reach aimformat's public API. That
one boundary keeps every future option cheap: bump the pin, wrap a fix,
or (worst case, MIT) vendor the parse layer, all without touching the
converter proper.

Beside the re-exports, this module fills the gaps the dependency leaves:

- **relationships** — upstream's ``extract_external_hyperlinks`` /
  ``extract_image_relationships`` search the *officeDocument* relationship
  namespace, but ``.rels`` files use the *package* namespace, so both
  always return ``{}`` (upstream bug, fix PR planned). ``_relationships``
  reads the part directly.
- **theme** — ``word/theme/theme1.xml`` (major/minor latin faces, the
  colour scheme) has no upstream parser; ingestion needs it both for the
  document theme slots and to resolve ``themeColor`` references on runs.
- **colour math** — OOXML ``themeTint``/``themeShade`` are hex fractions
  applied against white/black; Word's highlight enum is a fixed named
  palette.
- **numbering** — :class:`NumberingEngine` parses ``numbering.xml`` and
  counts the way Word counts: per shared *definition*, not per instance.
  Upstream's tracker keys counters per instance and drops the ``w:lvl``
  bodies inside a ``w:lvlOverride``, either of which misnumbers a contract.

Style resolution note: the resolver merges docDefaults → basedOn chain →
direct formatting with override semantics. True OOXML *toggle* semantics
(``w:b`` XOR-ing across style layers, ECMA-376 §17.7.3) differ only in the
rare char-style-over-bold-para-style case; the divergence is accepted for
now and recorded in the module tests' expectations.

Content that dpc's model drops but real documents carry is recovered from
the source XML alongside the typed parse (:func:`parse_docx` pairs each
body item with its ``w:p``/``w:tbl`` element, so the recovery is positional
by construction — no index guessing): textbox paragraphs (``w:txbxContent``,
DrawingML and VML), content-control checkbox state (``w14:checkbox``), OMML
equations as their literal text (``m:t``), and symbol-font glyphs
(``w:sym``). The Strict-OOXML → Transitional namespace normalization (so
Strict ``.docx`` files parse at all) is adapted from docling's MIT
``msword_backend`` (github.com/docling-project/docling), including its
zip-slip / zip-bomb guards.
"""

from __future__ import annotations

import base64
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, BinaryIO

try:
    from docx_parser_converter import api as _api
    from docx_parser_converter.converters.common.style_resolver import StyleResolver
    from docx_parser_converter.parsers.document.paragraph_parser import parse_paragraph
    from docx_parser_converter.parsers.document.table_parser import parse_table
    from docx_parser_converter.parsers.utils import find_child, get_local_name
except ImportError as exc:  # pragma: no cover - exercised without the extra
    raise ImportError(
        "DOCX import requires docx-parser-converter (extra 'docx'): pip install 'aimformat[docx]'"
    ) from exc

from lxml import etree

__all__ = [
    "DocxTheme",
    "NumberingEngine",
    "NumberLevel",
    "ParsedDocx",
    "data_uri",
    "effective_run_props",
    "font_of",
    "half_points_to_pt",
    "highlight_hex",
    "model_dump",
    "paragraph_checkbox",
    "paragraph_math_text",
    "paragraph_run_baseline",
    "parse_docx",
    "resolve_color",
    "shading_hex",
    "symbol_char",
    "format_number",
    "table_look_val",
    "table_style_looks",
    "textbox_paragraphs",
    "picture_relationships",
    "twips_to_mm",
]

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_V_NS = "urn:schemas-microsoft-com:vml"
_O_NS = "urn:schemas-microsoft-com:office:office"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

#: Word's fixed highlight palette (ST_HighlightColor) as lowercase hex.
_HIGHLIGHTS = {
    "yellow": "#ffff00",
    "green": "#00ff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "blue": "#0000ff",
    "red": "#ff0000",
    "darkBlue": "#00008b",
    "darkCyan": "#008b8b",
    "darkGreen": "#006400",
    "darkMagenta": "#8b008b",
    "darkRed": "#8b0000",
    "darkYellow": "#808000",
    "darkGray": "#a9a9a9",
    "lightGray": "#d3d3d3",
    "black": "#000000",
}

#: run/paragraph ``themeColor`` names → clrScheme element names.
_THEME_COLOR_KEYS = {
    "dark1": "dk1",
    "text1": "dk1",
    "light1": "lt1",
    "background1": "lt1",
    "dark2": "dk2",
    "text2": "dk2",
    "light2": "lt2",
    "background2": "lt2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hyperlink": "hlink",
    "followedHyperlink": "folHlink",
}

#: image extensions the .aim registry can actually embed (data:image/*
#: that browsers render); vector office formats (emf/wmf) degrade to text.
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

#: A curated Wingdings position (low byte of ``w:sym@char``) → Unicode map.
#: Targets are the practical BMP equivalents that render in any font — the
#: exact Wingdings glyphs are often astral (barb arrows, bold-script ballot
#: marks) that most fonts lack — so a check reads as ✓, not a missing box.
#: Values follow the published Wingdings correspondence (alanwood.net); only
#: the common bullets, arrows, checks, and ballot boxes are mapped, not all
#: 224 glyphs. An unmapped Wingdings glyph is dropped, never leaked as its
#: hex code.
_WINGDINGS = {
    0x6C: "●",  # ● black circle
    0xA0: "▪",  # ▪ black small square
    0xA8: "□",  # □ white square
    0xB7: "•",  # • bullet
    0xE0: "→",  # → rightwards arrow
    0xE1: "↑",  # ↑ upwards arrow
    0xE2: "↓",  # ↓ downwards arrow
    0xEF: "⇦",  # ⇦ leftwards white arrow
    0xF0: "⇨",  # ⇨ rightwards white arrow
    0xF1: "⇧",  # ⇧ upwards white arrow
    0xF2: "⇩",  # ⇩ downwards white arrow
    0xFB: "✗",  # ✗ ballot X
    0xFC: "✓",  # ✓ check mark
    0xFD: "☒",  # ☒ ballot box with X
    0xFE: "☑",  # ☑ ballot box with check
}

# Strict-OOXML → Transitional normalization (adapted from docling's MIT
# msword_backend). Strict .docx files carry purl.oclc.org namespaces that
# python-docx / dpc do not recognize; rewriting them to the Transitional
# host lets the ordinary parse path handle the file.
_STRICT_PREFIX = "http://purl.oclc.org/ooxml/"
_TRANSITIONAL_HOST = "http://schemas.openxmlformats.org/"
_STRICT_MARKER = b"purl.oclc.org/ooxml"
_ROOT_RELS = "_rels/.rels"
_STRICT_NS_RE = re.compile(r"http://purl\.oclc\.org/ooxml/[A-Za-z0-9_./-]+")
_STRICT_NS_OVERRIDES = {
    "http://purl.oclc.org/ooxml/officeDocument/relationships/customXml": (
        "http://schemas.openxmlformats.org/officeDocument/2006/customXml"
    ),
    "http://purl.oclc.org/ooxml/officeDocument/relationships/metadata/thumbnail": (
        "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
    ),
}
_MAX_MEMBER_BYTES = 512 * 1024 * 1024  # 512 MiB per part
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB total (zip-bomb guard)


@dataclass
class DocxTheme:
    """What ``theme1.xml`` contributes: two latin faces and the colour map."""

    major_font: str | None = None
    minor_font: str | None = None
    colors: dict[str, str] = field(default_factory=dict)  # clrScheme name → "#rrggbb"

    def slots(self) -> dict[str, str]:
        """The document-theme slots this file's own theme defines."""
        out: dict[str, str] = {}
        if self.major_font:
            out["--aim-font-heading"] = self.major_font
        if self.minor_font:
            out["--aim-font-body"] = self.minor_font
        for i in range(1, 5):
            hexval = self.colors.get(f"accent{i}")
            if hexval:
                out[f"--aim-brand-{i}"] = hexval
        return out


@dataclass
class ParsedDocx:
    """Everything the converter needs, parsed once."""

    document: Any  # dpc Document (never leaves the convert package) — for sect_pr
    content: list[tuple[Any, Any]]  # (dpc item, source w:p/w:tbl element) in body order
    resolver: StyleResolver
    numbering: Any | None
    #: styleId → conditional look (shaded header row, banded rows). Most
    #: Word tables carry their whole appearance here, not on the cells.
    table_looks: dict[str, dict[str, dict[str, str]]]
    #: Stateful label generator for numbered paragraphs ("1.1.1"): its
    #: counters advance per call, so it is shared for one walk in document
    #: order and never reused across documents.
    numbering_engine: NumberingEngine
    hyperlinks: dict[str, str]  # rId → external URL
    images: dict[str, tuple[bytes, str]]  # rId → (bytes, mime)
    theme: DocxTheme
    default_style_id: str | None  # the default paragraph style ("Normal")
    baseline_run: dict[str, Any]  # document-default effective run props


def parse_docx(source: str | bytes | BinaryIO) -> ParsedDocx:
    """Open and parse *source* through the pinned parse layer + gap-fillers."""
    zf = _api.open_docx(source)
    _guard_archive(zf)  # every input, not only the Strict-OOXML branch
    if _is_strict_ooxml(zf):
        zf = _api.open_docx(_normalize_strict_ooxml(zf))
    doc_elem = _api.extract_document_xml(zf)
    document = _api.parse_document(doc_elem)
    if document is None:
        raise ValueError("not a WordprocessingML document (no document body)")
    body_elem = find_child(doc_elem, "body") if doc_elem is not None else None
    content = _body_content_pairs(body_elem)
    styles = _api.parse_styles(_api.extract_styles_xml(zf))
    numbering = _api.parse_numbering(_api.extract_numbering_xml(zf))
    resolver = StyleResolver(styles, getattr(styles, "doc_defaults", None))
    hyperlinks, image_targets = _relationships(zf)
    images = _load_images(zf, image_targets)
    theme = _parse_theme(zf)
    default = resolver.get_default_paragraph_style()
    default_id = getattr(default, "style_id", None) if default is not None else None
    baseline = resolver.resolve_paragraph_properties(default_id).get("r_pr", {}) or {}
    return ParsedDocx(
        document=document,
        content=content,
        resolver=resolver,
        numbering=numbering,
        table_looks=table_style_looks(zf, theme),
        numbering_engine=NumberingEngine(_part_bytes(zf, "word/numbering.xml")),
        hyperlinks=hyperlinks,
        images=images,
        theme=theme,
        default_style_id=default_id,
        baseline_run=baseline,
    )


#: ``%1.%2.%3`` — the decimal chain that legal clause numbering is built from.
_CHAIN = re.compile(r"^%1(?:\.%(\d))*\.?$")


@dataclass
class NumberDraw:
    """Everything needed to render one numbered paragraph, from one advance
    of the counters — so a caller cannot accidentally count twice."""

    label: str
    """What Word draws ("1.1.11"), always computed: it is the baked fallback
    and the oracle the dynamic rendering is checked against."""

    restarted: bool
    """This paragraph reset its level's counter (Word's "Restart at 1")."""

    level: int | None = None
    """1-based clause level when this paragraph can be numbered dynamically,
    None when it must carry a baked label."""

    prefix: str = ""
    """The literal before the counter ("Article "), when the level draws one."""

    chained: bool = False
    """This level shows its ancestors' counters too ("1.1.1"). Such a
    paragraph is a *clause*, not a list item, whatever style it carries: an
    ``<ol>`` renders 1, 2, 3 at every depth and cannot express the chain at
    all — which is the defect that started this."""


@dataclass
class NumberLevel:
    """One level of a numbering definition, as Word declares it."""

    ilvl: int
    num_fmt: str  # decimal, lowerLetter, upperRoman, bullet, none, …
    lvl_text: str  # the template: "%1.%2.%3", "Article %1", "(%3)"
    start: int
    lvl_restart: int | None  # 0 = never restart; None = default (any shallower)

    @property
    def is_ordered(self) -> bool:
        return self.num_fmt not in ("bullet", "none")


class NumberingEngine:
    """Word's numbering counters, keyed the way Word keys them.

    ``numbering.xml`` is parsed here rather than through the dependency's
    tracker for two reasons: that tracker keys counters by numbering
    *instance* (``w:num``) where Word keys them by the shared *definition*
    (``w:abstractNum``), and it discards the ``w:lvl`` bodies inside a
    ``w:lvlOverride``, which redefine a level's format for one instance.

    The rule, which took three wrong heuristics and two documents to pin
    down — see ``tests/test_docx_numbering.py`` for the evidence:

        Counters are shared per ``(abstract definition, level)``. A
        ``startOverride`` resets that shared counter to the given value when
        its instance is FIRST ENCOUNTERED in the document — it does not open
        a separate sequence.

    So a single visible run of clauses ("1.1.1 … 1.1.14") stays unbroken
    across the fresh ``w:num`` Word emits every time a list is interrupted,
    while a deliberate "Restart at 1" still restarts.

    Counters advance on every :meth:`label` call, so an engine belongs to one
    walk in document order and is never reused across documents.

    Known gaps, each verified rather than assumed:

    - ``w:numStyleLink`` chains are not followed. An abstract definition that
      delegates to a numbering *style* (Word's built-in "List Paragraph"
      family) carries no ``w:lvl`` of its own, so its lists degrade to
      bullets. Same behaviour as the dependency's tracker; no document in the
      corpus uses it.
    - A level that exists ONLY as an instance ``lvlOverride`` body is not
      reset by a parent advancing, and its ``lvlRestart`` is read from the
      abstract rather than the override — ``_reset_deeper`` walks the
      abstract's levels.

    Deliberately unsettled: whether a ``startOverride`` applies on the
    instance's first use at ANY level or per level on first use at THAT
    level. This implements per level; both readings explain every fixture we
    have. A Word-authored document that separates them would decide it.

    Also unsettled, and left alone on purpose: when a deeper level is drawn
    before the shallower one it references, this renders the shallower
    level's start value. Word may instead phantom-instantiate that level, so
    the next item at it would be 2 rather than 1. Needs a Word-authored probe
    document, not a guess.
    """

    def __init__(self, numbering_xml: bytes | None) -> None:
        self._levels: dict[int, dict[int, NumberLevel]] = {}  # abstract → ilvl → level
        self._abstract: dict[int, int] = {}  # num_id → abstract id
        self._overrides: dict[tuple[int, int], NumberLevel] = {}  # (num, ilvl) → level
        self._start_overrides: dict[tuple[int, int], int] = {}
        self._pending: set[tuple[int, int]] = set()  # start overrides not yet applied
        self._counters: dict[tuple[int, int], int] = {}  # (abstract, ilvl) → value
        if numbering_xml:
            self._parse(numbering_xml)

    # -- definitions -------------------------------------------------------

    def _parse(self, xml: bytes) -> None:
        try:
            root = etree.fromstring(xml)
        except etree.XMLSyntaxError:
            # A corrupt part must degrade, never abort the import: the parse
            # layer tolerates this one and so did we before this engine, and
            # from_docx ingests arbitrary uploads. Numbering is lost; the
            # document still arrives.
            return
        w = f"{{{_W_NS}}}"
        for ab in root.iter(f"{w}abstractNum"):
            aid = _int_or_none(ab.get(f"{w}abstractNumId"))
            if aid is None:
                continue
            self._levels[aid] = {
                lvl.ilvl: lvl
                for lvl in (_parse_level(el, w) for el in ab.findall(f"{w}lvl"))
                if lvl
            }
        for num in root.iter(f"{w}num"):
            nid = _int_or_none(num.get(f"{w}numId"))
            ref = num.find(f"{w}abstractNumId")
            aid = _int_or_none(ref.get(f"{w}val")) if ref is not None else None
            if nid is None or aid is None:
                continue
            self._abstract[nid] = aid
            for ov in num.findall(f"{w}lvlOverride"):
                ilvl = _int_or_none(ov.get(f"{w}ilvl"))
                if ilvl is None:
                    continue
                start = ov.find(f"{w}startOverride")
                if start is not None:
                    value = _int_or_none(start.get(f"{w}val"))
                    if value is not None:
                        self._start_overrides[(nid, ilvl)] = value
                        self._pending.add((nid, ilvl))
                # a lvlOverride may also REDEFINE the level (format, template)
                # for this instance alone, which is not a restart at all
                body = ov.find(f"{w}lvl")
                if body is not None:
                    redefined = _parse_level(body, w)
                    if redefined is not None:
                        self._overrides[(nid, ilvl)] = redefined

    def level(self, num_id: int, ilvl: int) -> NumberLevel | None:
        """The level definition in force for this instance, override first."""
        own = self._overrides.get((num_id, ilvl))
        if own is not None:
            return own
        abstract = self._abstract.get(num_id)
        if abstract is None:
            return None
        return self._levels.get(abstract, {}).get(ilvl)

    def is_ordered(self, num_id: int, ilvl: int) -> bool:
        """Whether this level draws numbers (``<ol>``) or bullets (``<ul>``)."""
        level = self.level(num_id, ilvl)
        return level is not None and level.is_ordered

    # -- counting ----------------------------------------------------------

    def label(self, num_id: int, ilvl: int) -> str:
        """Advance the counters for one numbered paragraph and return the
        label Word would draw ("1.1.1", "(a)", "Article 3"), or "" when the
        level draws none. Call exactly once per numbered paragraph, in
        document order."""
        return self.draw(num_id, ilvl).label

    def draw(self, num_id: int, ilvl: int) -> NumberDraw:
        """Advance the counters once and describe how to render this
        paragraph — the label Word draws, whether it restarts, and whether
        the level can be expressed dynamically instead of baked."""
        level = self.level(num_id, ilvl)
        abstract = self._abstract.get(num_id)
        if level is None or abstract is None:
            return NumberDraw(label="", restarted=False)
        restarted = (num_id, ilvl) in self._pending
        self._advance(abstract, num_id, ilvl, level)
        if not level.is_ordered:
            # a bullet level draws a glyph we do not carry, a "none" level
            # draws nothing — but the counter above still moved, so the
            # numbered siblings around it stay correct
            return NumberDraw(label="", restarted=restarted)
        clause_level, prefix = self._dynamic_style(num_id, ilvl, level)
        return NumberDraw(
            label=self._render(abstract, num_id, level),
            restarted=restarted,
            level=clause_level,
            prefix=prefix,
            chained=(level.lvl_text or "").count("%") > 1,
        )

    def _dynamic_style(self, num_id: int, ilvl: int, level: NumberLevel) -> tuple[int | None, str]:
        """Whether this level is one CSS counters can draw, and its literal
        prefix if so.

        Two shapes qualify, and only these two:

        * the decimal chain ``%1.%2.%3`` — this level plus every ancestor,
          uniform ``.`` separators, every referenced level decimal;
        * ``Article %1`` — a literal followed by this level's own counter,
          decimal, at the top level.

        Everything else (mixed formats down one chain, mixed separators,
        parenthesised sub-items) keeps a baked label. ``content:`` cannot
        read an ancestor level's *format* from a fixed stylesheet, and a
        per-(depth × format) rule matrix is how a closed vocabulary turns
        into per-document CSS.
        """
        text = level.lvl_text
        if not text or level.num_fmt != "decimal":
            return None, ""
        chain = _CHAIN.match(text)
        if chain and text.count("%") == ilvl + 1:
            # every referenced ancestor must be decimal too, or the chain
            # renders one style where the source draws another
            for ref in range(ilvl + 1):
                ancestor = self.level(num_id, ref)
                if ancestor is None or ancestor.num_fmt != "decimal":
                    return None, ""
            return ilvl + 1, ""
        own = f"%{ilvl + 1}"
        if text.count("%") == 1 and text.endswith(own):
            prefix = text[: -len(own)]
            if "%" not in prefix:
                return ilvl + 1, prefix
        return None, ""

    def _advance(self, abstract: int, num_id: int, ilvl: int, level: NumberLevel) -> None:
        key = (abstract, ilvl)
        pending = (num_id, ilvl) in self._pending
        if pending:
            # Word's "Restart at 1": it resets the SHARED counter the first
            # time this instance is seen, rather than opening a sequence of
            # its own — which is why a later instance on the same definition
            # carries on from here instead of starting over.
            self._pending.discard((num_id, ilvl))
            self._counters[key] = self._start_overrides[(num_id, ilvl)]
        elif key in self._counters:
            self._counters[key] += 1
        else:
            # Seeding, either the first time or after a restart popped the
            # counter. A startOverride applies "when this level initially
            # starts in a given document, as well as whenever it is
            # restarted" (§17.9.27) — so it is the start value here too, not
            # only on first encounter. Invisible while override == start (the
            # common Restart-at-1), wrong whenever they differ.
            self._counters[key] = self._start_value(num_id, ilvl, level)
        self._reset_deeper(abstract, ilvl)

    def _start_value(self, num_id: int, ilvl: int, level: NumberLevel) -> int:
        return self._start_overrides.get((num_id, ilvl), level.start)

    def _reset_deeper(self, abstract: int, ilvl: int) -> None:
        """A level moving on restarts the levels below it — 1.2.1 follows
        1.1.9 — unless a level declares otherwise (``w:lvlRestart``)."""
        for deeper, level in self._levels.get(abstract, {}).items():
            if deeper <= ilvl:
                continue
            restart = level.lvl_restart
            if restart == 0:  # explicitly never
                continue
            if restart is not None and ilvl > restart - 1:
                # lvlRestart names a level (1-based); this level restarts when
                # THAT level "or any lower level" is used (§17.9.10) — so a
                # SHALLOWER level advancing resets it too. Reading it as "only
                # that exact level" leaves a stale deep counter whenever a
                # document skips a level, which is the ordinary
                # heading-then-clause shape.
                continue
            self._counters.pop((abstract, deeper), None)

    def _render(self, abstract: int, num_id: int, level: NumberLevel) -> str:
        """Fill a level's ``lvlText`` template ("%1.%2.%3") with the current
        counter of each referenced level, in that level's own format."""
        out = level.lvl_text or f"%{level.ilvl + 1}"
        for ref in range(9):
            token = f"%{ref + 1}"
            if token not in out:
                continue
            ref_level = self.level(num_id, ref)
            value = self._counters.get((abstract, ref))
            if value is None:
                # referenced before that level has been used: show what it
                # WOULD start at, override included — otherwise the same
                # level reads 1 here and 5 the moment it is first used
                value = self._start_value(num_id, ref, ref_level) if ref_level else 1
            fmt = ref_level.num_fmt if ref_level is not None else "decimal"
            out = out.replace(token, format_number(value, fmt))
        return out.strip()


def _parse_level(el: Any, w: str) -> NumberLevel | None:
    ilvl = _int_or_none(el.get(f"{w}ilvl"))
    if ilvl is None:
        return None

    def val(tag: str) -> str | None:
        node = el.find(f"{w}{tag}")
        return node.get(f"{w}val") if node is not None else None

    return NumberLevel(
        ilvl=ilvl,
        num_fmt=val("numFmt") or "decimal",
        lvl_text=val("lvlText") or "",
        start=_int_or_none(val("start")) or 1,
        lvl_restart=_int_or_none(val("lvlRestart")),
    )


def _part_bytes(zf: zipfile.ZipFile, name: str) -> bytes | None:
    """A package part's bytes, or None when the document has no such part
    (a document with no lists carries no ``numbering.xml`` at all)."""
    try:
        return zf.read(name)
    except KeyError:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_number(value: int, num_fmt: str) -> str:
    """One counter value in an OOXML ``numFmt``. Formats beyond these degrade
    to decimal rather than vanish — a wrong glyph is recoverable, a missing
    clause number is not."""
    if num_fmt in ("bullet", "none"):
        return ""
    if num_fmt in ("lowerLetter", "upperLetter"):
        out = ""
        n = value
        while n > 0:
            n, rem = divmod(n - 1, 26)
            out = chr(ord("a") + rem) + out
        return out if num_fmt == "lowerLetter" else out.upper()
    if num_fmt in ("lowerRoman", "upperRoman"):
        numerals = (
            (1000, "m"),
            (900, "cm"),
            (500, "d"),
            (400, "cd"),
            (100, "c"),
            (90, "xc"),
            (50, "l"),
            (40, "xl"),
            (10, "x"),
            (9, "ix"),
            (5, "v"),
            (4, "iv"),
            (1, "i"),
        )
        out, n = "", value
        for size, glyph in numerals:
            count, n = divmod(n, size)
            out += glyph * count
        return out if num_fmt == "lowerRoman" else out.upper()
    return str(value)


def _body_content_pairs(body_elem: Any) -> list[tuple[Any, Any]]:
    """Pair each body-level ``w:p``/``w:tbl`` with its dpc item, mirroring
    dpc's own ``parse_body`` walk so the pairing is exact — the converter
    needs the source element to recover content dpc drops (textboxes, OMML,
    checkboxes). ``sectPr`` and wrappers dpc skips (``w:sdt``, ``customXml``)
    are skipped here too, so index alignment can never drift."""
    pairs: list[tuple[Any, Any]] = []
    if body_elem is None:
        return pairs
    for child in body_elem:
        name = get_local_name(child)
        if name == "p":
            item = parse_paragraph(child)
        elif name == "tbl":
            item = parse_table(child)
        else:
            continue
        if item is not None:
            pairs.append((item, child))
    return pairs


def _guard_archive(zf: zipfile.ZipFile) -> None:
    """Reject zip-slip member names and zip-bomb sizes on ANY archive before
    a single member is read — ``from_docx`` ingests arbitrary user uploads,
    so the guards cannot live only on the (rare) Strict-OOXML rewrite path."""
    total = 0
    for info in zf.infolist():
        if not _is_safe_zip_member(info.filename):
            raise ValueError(f"unsafe zip member (zip-slip): {info.filename}")
        if info.file_size > _MAX_MEMBER_BYTES:
            raise ValueError(f"oversized OOXML part: {info.filename}")
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("OOXML package exceeds the uncompressed size limit")


def _is_strict_ooxml(zf: zipfile.ZipFile) -> bool:
    """Whether the archive is a Strict OOXML package — decided from the tiny
    root relationships part only, so Transitional files pay nothing."""
    try:
        with zf.open(_ROOT_RELS) as rels:
            return _STRICT_MARKER in rels.read(64 * 1024)
    except KeyError:
        return False


def _is_safe_zip_member(name: str) -> bool:
    """Guard against zip-slip: reject absolute, drive-letter, and ``..`` paths."""
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        return False
    return not any(part == ".." for part in normalized.split("/"))


def _strict_ns_to_transitional(strict_ns: str) -> str:
    if strict_ns in _STRICT_NS_OVERRIDES:
        return _STRICT_NS_OVERRIDES[strict_ns]
    rest = strict_ns[len(_STRICT_PREFIX) :]
    rest = rest.replace("extendedProperties", "extended-properties")
    rest = rest.replace("customProperties", "custom-properties")
    segment, separator, tail = rest.partition("/")
    if not separator:
        return f"{_TRANSITIONAL_HOST}{segment}/2006"
    return f"{_TRANSITIONAL_HOST}{segment}/2006/{tail}"


def _normalize_strict_ooxml(zf: zipfile.ZipFile) -> BytesIO:
    """Rewrite a Strict OOXML package to Transitional namespaces in memory.
    Only XML/relationship parts carrying a Strict namespace are decoded and
    rewritten; every other member is copied through. The archive has already
    passed :func:`_guard_archive` (zip-slip / zip-bomb) in ``parse_docx``."""
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in zf.infolist():
            content = zf.read(info.filename)
            if info.filename.endswith((".xml", ".rels")) and _STRICT_MARKER in content:
                content = _STRICT_NS_RE.sub(
                    lambda m: _strict_ns_to_transitional(m.group(0)),
                    content.decode("utf-8"),
                ).encode("utf-8")
            target.writestr(info, content)
    out.seek(0)
    return out


def _relationships(zf: zipfile.ZipFile) -> tuple[dict[str, str], dict[str, str]]:
    """(hyperlinks, image part paths) from ``word/_rels/document.xml.rels``.

    Read directly in the *package* relationship namespace — upstream's
    helpers search the officeDocument namespace and always come back empty.
    """
    hyperlinks: dict[str, str] = {}
    images: dict[str, str] = {}
    try:
        root = etree.fromstring(zf.read("word/_rels/document.xml.rels"))
    except (KeyError, etree.XMLSyntaxError):
        return hyperlinks, images
    for rel in root.findall(f"{{{_PKG_REL_NS}}}Relationship"):
        rid, rtype, target = rel.get("Id"), rel.get("Type") or "", rel.get("Target") or ""
        if not rid or not target:
            continue
        if rtype.endswith("/hyperlink") and rel.get("TargetMode") == "External":
            hyperlinks[rid] = target
        elif rtype.endswith("/image") and rel.get("TargetMode") != "External":
            images[rid] = posixpath.normpath(posixpath.join("word", target))
    return hyperlinks, images


def _load_images(zf: zipfile.ZipFile, targets: dict[str, str]) -> dict[str, tuple[bytes, str]]:
    out: dict[str, tuple[bytes, str]] = {}
    for rid, path in targets.items():
        mime = _IMAGE_MIME.get(posixpath.splitext(path)[1].lower())
        if mime is None:
            continue  # emf/wmf/svg-in-docx: no embeddable raster bytes
        try:
            out[rid] = (zf.read(path), mime)
        except KeyError:
            continue
    return out


def data_uri(image: tuple[bytes, str]) -> str:
    raw, mime = image
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


#: The conditional formats a table style can define, in the order Word
#: applies them (later wins). Corner conditions and vertical banding are
#: deliberately out of scope: they need the full cell-position algebra and
#: contribute far less to how a table reads.
_TABLE_CONDITIONS = ("wholeTable", "band2Horz", "band1Horz", "lastRow", "firstRow")


def table_look_val(elem: Any) -> str | None:
    """The raw ``w:tblLook@w:val`` bitmask of a ``w:tbl`` element, or None.

    dpc reads only ``tblLook``'s named attributes, but Word-2007-era files
    (and plenty of generators) write the flags ONLY as this bitmask. With no
    flags at all a caller cannot tell "no header row" from "unspecified".
    """
    if elem is None:
        return None
    look = elem.find(f"./{{{_W_NS}}}tblPr/{{{_W_NS}}}tblLook")
    return look.get(f"{{{_W_NS}}}val") if look is not None else None


def table_style_looks(
    zf: zipfile.ZipFile, theme: DocxTheme | None = None
) -> dict[str, dict[str, dict[str, str]]]:
    """``{styleId: {condition: {"fill": "#rrggbb", "color": "#rrggbb"}}}``.

    Word's built-in table styles ("Medium Shading 1 Accent 1" and friends)
    carry the whole look of a table — the shaded header row, the banded body
    rows, the white header text — in ``w:tblStylePr`` conditional formats.
    Most real tables use one INSTEAD of shading cells directly, so reading
    only ``w:tcPr/w:shd`` (as the typed model offers) renders every such
    table flat and unstyled. ``basedOn`` is followed so derived styles
    inherit their parent's look.
    """
    try:
        root = etree.fromstring(zf.read("word/styles.xml"))
    except (KeyError, etree.XMLSyntaxError):
        return {}
    w = f"{{{_W_NS}}}"
    raw: dict[str, dict[str, dict[str, str]]] = {}
    based: dict[str, str] = {}

    palette = theme or DocxTheme()

    def _fill_of(shd: Any) -> str | None:
        """A w:shd fill as hex — literal, or resolved through the theme.
        Built-in Word table styles name fills indirectly far more often than
        they spell out hex, so literal-only reading leaves most real tables
        unstyled."""
        if shd is None:
            return None
        literal = (shd.get(f"{w}fill") or "").strip()
        if re.fullmatch(r"[0-9A-Fa-f]{6}", literal):
            return f"#{literal.lower()}"
        name = shd.get(f"{w}themeFill")
        if not name:
            return None
        return resolve_color(
            {
                "theme_color": name,
                "theme_tint": shd.get(f"{w}themeFillTint"),
                "theme_shade": shd.get(f"{w}themeFillShade"),
            },
            palette,
        )

    def _color_of(color: Any) -> str | None:
        if color is None:
            return None
        literal = (color.get(f"{w}val") or "").strip()
        if re.fullmatch(r"[0-9A-Fa-f]{6}", literal):
            return f"#{literal.lower()}"
        name = color.get(f"{w}themeColor")
        if not name:
            return None
        return resolve_color(
            {
                "theme_color": name,
                "theme_tint": color.get(f"{w}themeTint"),
                "theme_shade": color.get(f"{w}themeShade"),
            },
            palette,
        )

    def _look_direct(scope: Any) -> dict[str, str]:
        """What a scope declares directly — never inherited from a nested
        conditional block."""
        out: dict[str, str] = {}
        shd = scope.find(f"{w}shd")
        if shd is None:
            tc = scope.find(f"{w}tcPr")
            shd = tc.find(f"{w}shd") if tc is not None else None
        fill = _fill_of(shd)
        if fill:
            out["fill"] = fill
        rpr = scope.find(f"{w}rPr")
        color = _color_of(rpr.find(f"{w}color") if rpr is not None else None)
        if color:
            out["color"] = color
        return out

    def _look(scope: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        fill = _fill_of(scope.find(f".//{w}shd"))
        if fill:
            out["fill"] = fill
        color = _color_of(scope.find(f".//{w}rPr/{w}color"))
        if color:
            out["color"] = color
        return out

    for style in root.iter(f"{w}style"):
        if style.get(f"{w}type") != "table":
            continue
        style_id = style.get(f"{w}styleId")
        if not style_id:
            continue
        parent = style.find(f"{w}basedOn")
        if parent is not None and parent.get(f"{w}val"):
            based[style_id] = parent.get(f"{w}val")
        conds: dict[str, dict[str, str]] = {}
        for spr in style.findall(f"{w}tblStylePr"):
            kind = spr.get(f"{w}type")
            if kind in _TABLE_CONDITIONS:
                look = _look(spr)
                if look:
                    conds.setdefault(kind, {}).update(look)
        # The table-wide look is ONLY what the style declares directly. A
        # subtree search here hoists a conditional block's formatting — a
        # dark firstRow band — onto every row of every table using the style.
        whole: dict[str, str] = {}
        for scope in (style.find(f"{w}tblPr"), style.find(f"{w}tcPr"), style):
            if scope is not None:
                whole.update(_look_direct(scope))
        if whole:
            # an explicit tblStylePr type="wholeTable" refines these
            conds["wholeTable"] = {**whole, **conds.get("wholeTable", {})}
        raw[style_id] = {k: v for k, v in conds.items() if v}

    resolved: dict[str, dict[str, dict[str, str]]] = {}
    for style_id in raw:
        merged: dict[str, dict[str, str]] = {}
        chain, seen = [], set()
        cur: str | None = style_id
        while cur and cur in raw and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = based.get(cur)
        for ancestor in reversed(chain):  # parent first, child overrides
            for cond, look in raw[ancestor].items():
                merged.setdefault(cond, {}).update(look)
        # A band recolours its text BECAUSE it shades its background, so a
        # colour with no fill would paint (usually white) text onto white
        # paper. Applied once HERE, after inheritance: a child style that
        # supplies only the text colour is completing its parent's fill, and
        # dropping it per-style would break exactly the case the rule exists
        # to protect.
        for look in merged.values():
            if "color" in look and "fill" not in look:
                look.pop("color")
        resolved[style_id] = {k: v for k, v in merged.items() if v}
    return resolved


def _parse_theme(zf: zipfile.ZipFile) -> DocxTheme:
    theme = DocxTheme()
    try:
        root = etree.fromstring(zf.read("word/theme/theme1.xml"))
    except (KeyError, etree.XMLSyntaxError):
        return theme
    ns = {"a": _A_NS}
    major = root.find(".//a:fontScheme/a:majorFont/a:latin", ns)
    minor = root.find(".//a:fontScheme/a:minorFont/a:latin", ns)
    theme.major_font = (major.get("typeface") or None) if major is not None else None
    theme.minor_font = (minor.get("typeface") or None) if minor is not None else None
    scheme = root.find(".//a:clrScheme", ns)
    if scheme is not None:
        for child in scheme:
            name = etree.QName(child).localname
            srgb = child.find("a:srgbClr", ns)
            sysc = child.find("a:sysClr", ns)
            val = None
            if srgb is not None:
                val = srgb.get("val")
            elif sysc is not None:
                val = sysc.get("lastClr")
            if val and len(val) == 6:
                theme.colors[name] = "#" + val.lower()
    return theme


# -- effective properties ---------------------------------------------------


def paragraph_run_baseline(resolver: StyleResolver, para_style_id: str | None) -> dict[str, Any]:
    """The run look a paragraph's own context supplies: docDefaults plus
    the paragraph style's resolved run properties. What matches this is
    the document's rhythm; what a run adds on top is local intent."""
    props = dict(resolver.resolve_run_properties(None))  # docDefaults
    if para_style_id:
        para_rpr = resolver.resolve_paragraph_properties(para_style_id).get("r_pr") or {}
        props = resolver.merge_with_direct(props, para_rpr)
    return props


def effective_run_props(
    resolver: StyleResolver,
    para_style_id: str | None,
    run_style_id: str | None,
    direct: dict[str, Any] | None,
) -> dict[str, Any]:
    """docDefaults → paragraph style chain → character style chain → direct.

    The resolver's own two-layer helpers cover one style id at a time; runs
    need the full stack, so the layers merge here (later layers win).
    """
    props = paragraph_run_baseline(resolver, para_style_id)
    if run_style_id:
        props = resolver.merge_with_direct(props, resolver.resolve_run_properties(run_style_id))
    return resolver.merge_with_direct(props, direct or {})


def half_points_to_pt(sz: int | float | None) -> float | None:
    if sz is None:
        return None
    return float(sz) / 2.0


def twips_to_mm(twips: int | float | None) -> float | None:
    if twips is None:
        return None
    return float(twips) * 25.4 / 1440.0


def font_of(props: dict[str, Any], theme: DocxTheme) -> str | None:
    """The effective latin face of resolved run props, theme refs resolved."""
    fonts = props.get("r_fonts") or {}
    if not isinstance(fonts, dict):
        fonts = dict(fonts)
    face = fonts.get("ascii") or fonts.get("h_ansi")
    if face:
        return str(face)
    ref = fonts.get("ascii_theme") or fonts.get("h_ansi_theme")
    if ref:
        ref = str(ref)
        if ref.startswith("major"):
            return theme.major_font
        if ref.startswith("minor"):
            return theme.minor_font
    return None


def resolve_color(color: dict[str, Any] | None, theme: DocxTheme) -> str | None:
    """A run/paragraph colour model → lowercase ``#rrggbb``, or None.

    ``auto`` means "let the renderer choose" and resolves to nothing.
    Theme references resolve through the colour scheme with the OOXML
    tint/shade fractions (hex 00–FF): tint blends toward white, shade
    toward black, FF meaning "unchanged".
    """
    if not color:
        return None
    if not isinstance(color, dict):
        color = dict(color)
    val = color.get("val")
    if isinstance(val, str) and val.lower() != "auto" and len(val) == 6:
        base = val.lower()
    else:
        key = _THEME_COLOR_KEYS.get(str(color.get("theme_color") or ""))
        base = (theme.colors.get(key) or "").lstrip("#") if key else ""
        if not base:
            return None
    rgb = [int(base[i : i + 2], 16) for i in (0, 2, 4)]
    tint = color.get("theme_tint")
    shade = color.get("theme_shade")
    if isinstance(tint, str) and tint:
        f = int(tint, 16) / 255.0
        rgb = [round(c * f + 255 * (1 - f)) for c in rgb]
    elif isinstance(shade, str) and shade:
        f = int(shade, 16) / 255.0
        rgb = [round(c * f) for c in rgb]
    return "#" + "".join(f"{min(255, max(0, c)):02x}" for c in rgb)


def highlight_hex(name: str | None) -> str | None:
    """A Word highlight enum value → hex, or None for absent/none."""
    if not name or name == "none":
        return None
    return _HIGHLIGHTS.get(name)


def shading_hex(shd: dict[str, Any] | None) -> str | None:
    """Run/paragraph/cell shading fill → lowercase hex, or None."""
    if not shd:
        return None
    if not isinstance(shd, dict):
        shd = dict(shd)
    fill = shd.get("fill")
    if isinstance(fill, str) and len(fill) == 6 and fill.lower() != "auto":
        return "#" + fill.lower()
    return None


def model_dump(obj: Any) -> dict[str, Any]:
    """A model's set fields as a plain dict ({} for None)."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    return obj.model_dump(exclude_none=True)


# -- content dpc's model drops, recovered from the source w:p element -------


def symbol_char(font: str | None, char: str | None) -> str | None:
    """A ``w:sym`` (font, char-hex) → a renderable Unicode character, or None.

    ``char`` is a hex string like ``"F0FC"``. Wingdings positions map through
    the curated table (unmapped Wingdings glyphs drop — never leak the hex);
    any other font's private-use glyph also drops, while a real BMP character
    passes through. Dropping beats emitting a wrong glyph or raw ``"F0FC"``.
    """
    if not char:
        return None
    try:
        code = int(str(char), 16)
    except ValueError:
        return None
    if font and str(font).strip().lower() == "wingdings":
        return _WINGDINGS.get(code & 0xFF)
    if code < 0x20 or 0xE000 <= code <= 0xF8FF:
        return None  # control or private-use: not meaningful without its font
    try:
        return chr(code)
    except (ValueError, OverflowError):
        return None


def paragraph_checkbox(elem: Any) -> str | None:
    """A ``w14:checkbox`` content control's state as ☑ / ☐, or None. The
    plain Wingdings/□ form-field checkbox is handled by the symbol map."""
    cb = elem.find(f".//{{{_W14_NS}}}checkbox")
    if cb is None:
        return None
    checked = cb.find(f"{{{_W14_NS}}}checked")
    val = checked.get(f"{{{_W14_NS}}}val") if checked is not None else None
    return "☑" if val in ("1", "true") else "☐"


def _effective_descendants(elem: Any) -> Any:
    """Descendants of *elem* with Markup Compatibility (MCE) applied: inside
    an ``mc:AlternateContent``, exactly one branch is read — the first
    ``mc:Choice`` (the richer representation), else the ``mc:Fallback``.
    Word emits every inserted shape as AlternateContent carrying the *same*
    ``w:txbxContent`` in both a DrawingML Choice and a VML Fallback, so a
    naive ``.//`` search sees all duplicated content twice."""
    for child, _, _ in _effective_descendants_scoped(elem):
        yield child


def _effective_descendants_scoped(
    elem: Any, in_textbox: bool = False, boxed_mce: bool = False
) -> Any:
    """``_effective_descendants``, each node paired with whether dpc's typed
    model will already have carried it.

    Textbox content is walked a second time by ``textbox_paragraphs`` through
    dpc, so a caller that recovers content itself needs to know what dpc
    covers there — and dpc's run parser handles a bare ``w:drawing`` but has
    no branch for ``mc:AlternateContent``. Two flags, because both matter:

    ``in_textbox``
        inside a ``w:txbxContent``.
    ``boxed_mce``
        inside an ``mc:AlternateContent`` that is itself inside that textbox
        — content dpc cannot reach. Entering a textbox clears it, because
        Word wraps the *whole shape* in AlternateContent (so the textbox is
        usually inside one already, and that outer wrapper says nothing
        about what dpc sees within).
    """
    txbx = f"{{{_W_NS}}}txbxContent"
    for child in elem:
        if child.tag == f"{{{_MC_NS}}}AlternateContent":
            branch = child.find(f"{{{_MC_NS}}}Choice")
            if branch is None:
                branch = child.find(f"{{{_MC_NS}}}Fallback")
            if branch is not None:
                yield from _effective_descendants_scoped(
                    branch, in_textbox, boxed_mce or in_textbox
                )
            continue
        if child.tag == txbx:
            nested, mce = True, False
        else:
            nested, mce = in_textbox, boxed_mce
        yield child, nested, mce
        yield from _effective_descendants_scoped(child, nested, mce)


def paragraph_math_text(elem: Any) -> str:
    """Any OMML equations in the paragraph as their literal text (``m:t``
    joined). A text-only fallback — .aim carries no math markup — so an
    equation survives as its characters. Ordering is approximate for an
    equation interleaved mid-line (it trails the paragraph's run text)."""
    m_t = f"{{{_M_NS}}}t"
    return "".join(t.text or "" for t in _effective_descendants(elem) if t.tag == m_t)


_EMU_PER_PX = 9525


def _local(node: Any) -> str:
    tag = getattr(node, "tag", "")
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _geometry_ext(root: Any) -> Any | None:
    """The first ``a:ext`` under *root* that carries geometry (``@cx``).
    Plain ``.//a:ext`` also matches the ``a:extLst`` extension elements,
    which have a uri and no size."""
    for el in root.iter():
        if _local(el) == "ext" and el.get("cx"):
            return el
    return None


def _own_xfrm(el: Any) -> tuple[int | None, int | None]:
    """``(ext.cx, chExt.cx)`` from an element's OWN ``a:xfrm``, which sits
    one level down (``wpg:grpSpPr/a:xfrm``, ``pic:spPr/a:xfrm``). Never a
    descendant shape's — a subtree search would read a child picture's
    extent as if it were the group's."""
    for child in el:
        xfrm = child.find(f"{{{_A_NS}}}xfrm")
        if xfrm is None:
            continue
        ext = xfrm.find(f"{{{_A_NS}}}ext")
        ch = xfrm.find(f"{{{_A_NS}}}chExt")
        return (
            _int_or_none(ext.get("cx")) if ext is not None else None,
            _int_or_none(ch.get("cx")) if ch is not None else None,
        )
    return (None, None)


_VML_WIDTH = re.compile(r"width:\s*([\d.]+)\s*(pt|px|in|mm|cm)?")


def _vml_width(el: Any) -> tuple[float | None, str | None]:
    """A VML element's declared width and its unit, if any. A shape inside a
    ``v:group`` states its width in the GROUP's coordinate units, with no
    unit suffix — which is what distinguishes it from a real measurement."""
    match = _VML_WIDTH.search(el.get("style") or "")
    if match is None:
        return None, None
    try:
        return float(match.group(1)), match.group(2)
    except ValueError:
        return None, None


_PT_PER_PX = 0.75
_UNIT_PX = {"pt": 1 / _PT_PER_PX, "px": 1.0, "in": 96.0, "mm": 96 / 25.4, "cm": 96 / 2.54}


def _picture_width_px(node: Any, is_vml: bool) -> int | None:
    """The width Word draws this picture at, in CSS px, or None.

    A picture inside a group is authored in the GROUP's coordinate space, and
    groups nest, so the conversion is a product of ``ext/chExt`` over every
    group ancestor — only the outermost one states a real measurement.
    Without it a 1.5-inch logo lands at its full pixel size and swamps the
    page; with only one level of it, a picture inside a nested group is
    scaled by the inner group's ratio alone and lands wrong by the outer
    group's factor.
    """
    if is_vml:
        return _vml_width_px(node)

    # this picture's own extent (pic → pic:spPr/a:xfrm/a:ext)
    pic = node
    while pic is not None and _local(pic) != "pic":
        pic = pic.getparent()
    own_cx = _own_xfrm(pic)[0] if pic is not None else None

    # every group ancestor contributes its own coordinate-space ratio
    scale = 1.0
    grouped = False
    cur = pic.getparent() if pic is not None else node
    while cur is not None and _local(cur) != "drawing":  # never leave this drawing
        ext_cx, ch_cx = _own_xfrm(cur)
        if ext_cx and ch_cx:
            scale *= ext_cx / ch_cx
            grouped = True
        cur = cur.getparent()

    if grouped and own_cx:
        return max(1, round(own_cx * scale / _EMU_PER_PX))
    if grouped:  # a group whose child states no extent of its own
        return None
    # ungrouped: the drawing's own extent is the size
    cur = node
    while cur is not None and _local(cur) != "p":
        ext = cur.find(f".//{{{_WP_NS}}}extent")
        if ext is not None and (cx := _int_or_none(ext.get("cx"))):
            return max(1, round(cx / _EMU_PER_PX))
        cur = cur.getparent()
    return None


def _vml_width_px(node: Any) -> int | None:
    """VML geometry lives in a CSS-ish ``@style``. A shape inside a
    ``v:group`` is sized in that group's ``coordsize`` units, so its bare
    number has to be scaled by the group's real width — read literally, every
    child of a group renders at the group's own size."""
    own_w = own_unit = None
    cur = node
    while cur is not None:
        width, unit = _vml_width(cur)
        if width is not None and own_w is None:
            own_w, own_unit = width, unit
        if _local(cur) == "group":
            group_w, group_unit = _vml_width(cur)
            coords = (cur.get("coordsize") or "").split(",")
            if own_unit is None and own_w is not None and group_w is not None and coords:
                span = _int_or_none(coords[0])
                factor = _UNIT_PX.get(group_unit or "pt")
                if span and factor:
                    return max(1, round(group_w * factor * own_w / span))
        cur = cur.getparent()
    if own_w is None:
        return None
    # a unitless width outside any group has no scale to resolve it against
    return max(1, round(own_w * _UNIT_PX[own_unit])) if own_unit else None


def picture_relationships(elem: Any) -> list[tuple[str, str, int | None]]:
    """[(relationship id, alt text)] for EVERY picture in this paragraph, in
    document order and MCE-resolved — both DrawingML (``a:blip``) and legacy
    VML (``v:imagedata``).

    dpc's typed model exposes only the common shape: one ``w:drawing``
    wrapping a single ``pic:pic``. Real documents also carry grouped artwork
    (a ``wpg:wgp`` of several pictures — a row of logos on a title page) and
    VML pictures, and both vanish silently from that model. The converter
    uses this to emit whatever the typed walk did not already place, so the
    recovery is additive rather than a second source of truth.
    """
    blip = f"{{{_A_NS}}}blip"
    imagedata = f"{{{_V_NS}}}imagedata"
    embed, rel_id = f"{{{_R_NS}}}embed", f"{{{_R_NS}}}id"
    out: list[tuple[str, str, int | None]] = []
    seen: set[str] = set()
    # Inside a textbox, recover exactly what dpc cannot reach. Its run parser
    # carries a bare w:drawing but has no VML model and no mc:AlternateContent
    # branch, so the rule has to be that precise: recovering everything there
    # doubles plain pictures, skipping everything loses VML and every shape
    # Word wraps in AlternateContent (grouped art, picture fills, SmartArt).
    # All three variants shipped in turn before tests pinned them together.
    for node, in_textbox, boxed_mce in _effective_descendants_scoped(elem):
        if node.tag == blip:
            if in_textbox and not boxed_mce:
                continue  # a plain textbox drawing: dpc emits this one
            rid, alt = node.get(embed), "image"
        elif node.tag == imagedata:
            rid = node.get(rel_id)
            alt = node.get(f"{{{_O_NS}}}title") or node.get("alt") or "image"
        else:
            continue
        # one relationship can legitimately repeat (the Choice and Fallback of
        # the same shape); dedupe so a logo is not emitted several times
        if not rid or rid in seen:
            continue
        seen.add(rid)
        out.append((rid, alt, _picture_width_px(node, node.tag == imagedata)))
    return out


def textbox_paragraphs(elem: Any) -> list[Any]:
    """dpc Paragraphs parsed from every textbox in this paragraph
    (``w:txbxContent``, covering DrawingML and VML — one representation per
    shape, MCE-resolved), deduped by identity so a nested textbox is not
    counted twice. One level deep: a paragraph emitted from here is not
    itself re-scanned for textboxes."""
    txbx_tag = f"{{{_W_NS}}}txbxContent"
    seen: set[int] = set()
    out: list[Any] = []
    for txbx in (c for c in _effective_descendants(elem) if c.tag == txbx_tag):
        for p in txbx.findall(f".//{{{_W_NS}}}p"):
            if id(p) in seen:
                continue
            seen.add(id(p))
            parsed = parse_paragraph(p)
            if parsed is not None:
                out.append(parsed)
    return out
