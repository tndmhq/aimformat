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
"""

from __future__ import annotations

import base64
import posixpath
import zipfile
from dataclasses import dataclass, field
from typing import Any, BinaryIO

try:
    from docx_parser_converter import api as _api
    from docx_parser_converter.converters.common.numbering_tracker import (
        NumberingTracker,
    )
    from docx_parser_converter.converters.common.style_resolver import StyleResolver
except ImportError as exc:  # pragma: no cover - exercised without the extra
    raise ImportError(
        "DOCX import requires docx-parser-converter (extra 'docx'): pip install 'aimformat[docx]'"
    ) from exc

from lxml import etree

__all__ = [
    "ParsedDocx",
    "parse_docx",
    "effective_run_props",
    "resolve_color",
    "highlight_hex",
    "half_points_to_pt",
    "twips_to_mm",
    "font_of",
    "NumberingTracker",
]

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

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

    document: Any  # dpc Document (never leaves the convert package)
    resolver: StyleResolver
    numbering: Any | None
    hyperlinks: dict[str, str]  # rId → external URL
    images: dict[str, tuple[bytes, str]]  # rId → (bytes, mime)
    theme: DocxTheme
    default_style_id: str | None  # the default paragraph style ("Normal")
    baseline_run: dict[str, Any]  # document-default effective run props


def parse_docx(source: str | bytes | BinaryIO) -> ParsedDocx:
    """Open and parse *source* through the pinned parse layer + gap-fillers."""
    zf = _api.open_docx(source)
    document = _api.parse_document(_api.extract_document_xml(zf))
    if document is None:
        raise ValueError("not a WordprocessingML document (no document body)")
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
        resolver=resolver,
        numbering=numbering,
        hyperlinks=hyperlinks,
        images=images,
        theme=theme,
        default_style_id=default_id,
        baseline_run=baseline,
    )


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
