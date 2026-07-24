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
    from docx_parser_converter.converters.common.numbering_tracker import (
        NumberingTracker,
    )
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
    "NumberingTracker",
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
    #: numId → the instance whose counters it must share. Word clones a
    #: numbering instance whenever a list is interrupted, so one visible
    #: list can span several numIds that all point at the same abstract
    #: definition; counting per numId restarts it mid-document.
    num_alias: dict[int, int]
    #: Stateful label generator for numbered paragraphs ("1.1.1"): its
    #: counters advance per call, so it is shared for one walk in document
    #: order and never reused across documents.
    numbering_tracker: Any | None
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
        num_alias=_num_aliases(numbering),
        numbering_tracker=NumberingTracker(numbering) if numbering is not None else None,
        hyperlinks=hyperlinks,
        images=images,
        theme=theme,
        default_style_id=default_id,
        baseline_run=baseline,
    )


def _num_aliases(numbering: Any) -> dict[int, int]:
    """numId → the lowest numId sharing its abstract definition.

    Word emits a fresh ``w:num`` whenever a numbered list is interrupted, so
    a single visible sequence ("1.1.1 … 1.1.14") routinely arrives as two or
    three numIds over one ``abstractNumId``. Counters therefore belong to the
    abstract definition, not the instance — keying them per numId restarts
    the numbering mid-list. Instances carrying their own level overrides
    (``w:lvlOverride``, i.e. a deliberate restart) are left alone.
    """
    canonical: dict[int, int] = {}
    for inst in getattr(numbering, "num", None) or []:
        num_id = getattr(inst, "num_id", None)
        abstract = getattr(inst, "abstract_num_id", None)
        if num_id is None or abstract is None or getattr(inst, "lvl_override", None):
            continue
        first = canonical.setdefault(abstract, num_id)
        canonical[abstract] = min(first, num_id)
    out: dict[int, int] = {}
    for inst in getattr(numbering, "num", None) or []:
        num_id = getattr(inst, "num_id", None)
        abstract = getattr(inst, "abstract_num_id", None)
        if num_id is not None and abstract in canonical:
            out[num_id] = canonical[abstract]
    return out


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
    for child in elem:
        if child.tag == f"{{{_MC_NS}}}AlternateContent":
            branch = child.find(f"{{{_MC_NS}}}Choice")
            if branch is None:
                branch = child.find(f"{{{_MC_NS}}}Fallback")
            if branch is not None:
                yield from _effective_descendants(branch)
            continue
        yield child
        yield from _effective_descendants(child)


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


def _picture_width_px(node: Any, is_vml: bool) -> int | None:
    """The width Word draws this picture at, in CSS px, or None.

    A picture inside a group is authored in the GROUP's coordinate space, so
    its real width is ``group_px * own_ext / group_child_ext``. Without that
    scaling a 1.5-inch logo lands at its full pixel size and swamps the page.
    """
    if is_vml:
        cur = node  # v:shape / v:group carry CSS-ish geometry in @style
        while cur is not None:
            m = re.search(r"width:\s*([\d.]+)pt", cur.get("style") or "")
            if m:
                return max(1, round(float(m.group(1)) / 0.75))
            cur = cur.getparent()
        return None

    # this picture's own extent (pic → pic:spPr/a:xfrm/a:ext)
    pic = node
    while pic is not None and _local(pic) != "pic":
        pic = pic.getparent()
    # NB: a:extLst holds unrelated <a:ext uri="…"> extension elements, so
    # only an ext that actually carries geometry (@cx) counts
    own_ext = _geometry_ext(pic) if pic is not None else None

    # the drawing/group that gives the extent in real units, plus the child
    # coordinate space the picture's own extent is expressed in
    group_px: float | None = None
    child_space: float | None = None
    cur = pic.getparent() if pic is not None else node
    while cur is not None:
        ch = cur.find(f".//{{{_A_NS}}}chExt")
        ext = _geometry_ext(cur)
        if ch is not None and ext is not None and ch.get("cx"):
            try:
                group_px, child_space = int(ext.get("cx")) / _EMU_PER_PX, float(ch.get("cx"))
            except (TypeError, ValueError):
                return None
            break
        cur = cur.getparent()
    if group_px is None:  # ungrouped: the drawing's own extent is the size
        cur = node
        while cur is not None:
            ext = cur.find(f".//{{{_WP_NS}}}extent")
            if ext is not None and ext.get("cx"):
                try:
                    return max(1, round(int(ext.get("cx")) / _EMU_PER_PX))
                except (TypeError, ValueError):
                    return None
            cur = cur.getparent()
        return None
    if own_ext is not None and child_space and own_ext.get("cx"):
        try:
            return max(1, round(group_px * int(own_ext.get("cx")) / child_space))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return max(1, round(group_px))


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
    for node in _effective_descendants(elem):
        if node.tag == blip:
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
