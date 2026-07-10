"""Page setup — the `aim:doc` document settings (spec §3.6).

One resolution path for every consumer: the registry defines the named page
sizes, the margin grammar, and the defaults; a document may override them in
its head settings block::

    <script type="application/aim-doc+json">
    {"page":{"margins":{"bottom":"15mm","left":"15mm","right":"15mm",
    "top":"15mm"},"orientation":"portrait","size":"A4"}}
    </script>

Everything that needs geometry — an editor's page view, the Chromium PDF
printer (via :func:`page_css`), the DOCX section properties — reads the same
:class:`PageSetup`, so the three can never disagree about what a page is.

Soft (automatic) page breaks are deliberately **not** representable: they are
a function of fonts and renderer and would go stale on every edit (OOXML's
``w:lastRenderedPageBreak`` is the cautionary precedent). The format stores
intent only: this setup block plus explicit ``<aim-page-break>`` chunks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .errors import InvalidOperation
from .registry import REGISTRY

__all__ = ["PageSetup", "page_setup_from_obj", "page_css"]

_SIDES = ("top", "right", "bottom", "left")


def _invalid(msg: str, lint_code: str) -> InvalidOperation:
    """An :class:`InvalidOperation` tagged with the verifier rule it maps to
    (D001 shape / D003 size / D004 margins), so the linter reports the same
    grammar violation under the right code without re-parsing messages."""
    exc = InvalidOperation(msg)
    exc.lint_code = lint_code  # type: ignore[attr-defined]
    return exc


@dataclass(frozen=True)
class PageSetup:
    """A resolved page setup (always valid by construction)."""

    size: str = ""
    orientation: str = ""
    margins_mm: dict[str, float] = field(default_factory=dict)

    # -- derived geometry ------------------------------------------------------
    @property
    def page_width_mm(self) -> float:
        w, h = REGISTRY.page_sizes_mm[self.size]
        return float(h if self.orientation == "landscape" else w)

    @property
    def page_height_mm(self) -> float:
        w, h = REGISTRY.page_sizes_mm[self.size]
        return float(w if self.orientation == "landscape" else h)

    @property
    def content_width_mm(self) -> float:
        return self.page_width_mm - self.margins_mm["left"] - self.margins_mm["right"]

    @property
    def content_height_mm(self) -> float:
        return self.page_height_mm - self.margins_mm["top"] - self.margins_mm["bottom"]

    # -- serializations ----------------------------------------------------------
    def to_obj(self) -> dict:
        """The ``page`` field as stored in the aim-doc block."""
        return {
            "size": self.size,
            "orientation": self.orientation,
            "margins": {s: _fmt_mm(self.margins_mm[s]) for s in _SIDES},
        }

    def resolved(self) -> dict:
        """Flat geometry dict for renderers (all lengths in mm)."""
        return {
            "size": self.size,
            "orientation": self.orientation,
            "margins_mm": dict(self.margins_mm),
            "page_width_mm": self.page_width_mm,
            "page_height_mm": self.page_height_mm,
            "content_width_mm": self.content_width_mm,
            "content_height_mm": self.content_height_mm,
        }


def _fmt_mm(value: float) -> str:
    # fixed-point, never "%g": exponent spellings ("1e-06mm") are valid
    # floats but violate the margin grammar, so a tiny-but-legal input
    # would serialize into a block the linter itself rejects
    s = f"{value:.6f}".rstrip("0").rstrip(".") or "0"
    return f"{s}mm"


def default_page_setup() -> PageSetup:
    return page_setup_from_obj(REGISTRY.page_default)


def page_setup_from_obj(obj: object) -> PageSetup:
    """Validate one ``page`` object against the registry grammars.

    Raises :class:`InvalidOperation` — the write path must not produce
    documents its own linter rejects (D003/D004), mirroring theme slots.
    Unknown fields are ignored (spec forward-compat rule); missing fields
    fall back to the registry default.
    """
    if not isinstance(obj, dict):
        raise _invalid("page setup must be a JSON object", "D001")
    base = REGISTRY.page_default
    size = obj.get("size", base["size"])
    orientation = obj.get("orientation", base["orientation"])
    if not isinstance(size, str) or size not in REGISTRY.page_sizes_mm:
        raise _invalid(
            f"unknown page size {size!r} (registered: {', '.join(sorted(REGISTRY.page_sizes_mm))})",
            "D003",
        )
    if not isinstance(orientation, str) or orientation not in REGISTRY.page_orientations:
        raise _invalid(
            f"unknown orientation {orientation!r} (registered: "
            f"{', '.join(sorted(REGISTRY.page_orientations))})",
            "D003",
        )
    raw_margins = obj.get("margins", base["margins"])
    if not isinstance(raw_margins, dict):
        raise _invalid("page margins must be an object", "D004")
    margins: dict[str, float] = {}
    for side in _SIDES:
        value = raw_margins.get(side, base["margins"][side])
        if not isinstance(value, str) or not REGISTRY.margin_pattern.match(value):
            raise _invalid(
                f"page margin {side} {value!r} does not match the margin "
                f"grammar {REGISTRY.margin_pattern.pattern}",
                "D004",
            )
        mm = float(value[:-2])
        if mm > REGISTRY.margin_max_mm:
            raise _invalid(
                f"page margin {side} {value!r} exceeds the maximum {REGISTRY.margin_max_mm}mm",
                "D004",
            )
        margins[side] = mm
    setup = PageSetup(size=size, orientation=orientation, margins_mm=margins)
    if setup.content_width_mm <= 0 or setup.content_height_mm <= 0:
        raise _invalid(f"margins leave no content area on {size} {orientation}", "D004")
    return setup


def doc_settings_element(markup: str):
    """Parse + shape-check a whole settings-block payload; returns the
    single ``<script type="application/aim-doc+json">`` Element.

    The one gate every aim:doc payload goes through — the setter, the
    proposal validator, and the linter all call this, so the "single typed
    script block" rule cannot drift between writer and verifier."""
    from .dom import Element, parse_fragment

    nodes = [n for n in parse_fragment(markup) if isinstance(n, Element)]
    el = nodes[0] if len(nodes) == 1 else None
    if el is None or el.tag != "script" or el.get("type") != REGISTRY.script_types["doc"]:
        raise _invalid(
            "settings payload must be a single "
            f'<script type="{REGISTRY.script_types["doc"]}"> block',
            "D001",
        )
    if el.self_closing:
        raise _invalid(
            "settings payload must use explicit open+close script tags — "
            "in HTML a self-closed <script/> is still an open tag and "
            "swallows the following markup",
            "D001",
        )
    return el


def parse_doc_settings(raw: str | None) -> dict:
    """The aim-doc block's JSON object (``{}`` when absent/blank).

    Raises :class:`InvalidOperation` when present but not a JSON object —
    a malformed settings block is corrupt data, not a missing one (D001).
    """
    if raw is None or not raw.strip():
        return {}
    try:
        obj = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise _invalid(f"aim-doc settings block is not valid JSON: {exc}", "D001") from exc
    if not isinstance(obj, dict):
        raise _invalid("aim-doc settings block is not a JSON object", "D001")
    return obj


def page_setup_from_settings(settings: dict) -> PageSetup:
    """The page setup a settings object implies (defaults when unset)."""
    page = settings.get("page")
    if page is None:
        return default_page_setup()
    return page_setup_from_obj(page)


def page_css(setup: PageSetup) -> str:
    """The ``@page`` rule realizing *setup* in a print context.

    Emitted with explicit millimetre dimensions (not the CSS size keyword)
    so custom sizes keep working when a future version registers them, and
    paired with a print reset: inside a print box the screen layout's
    centered column must not add its own margins — the ``@page`` margins
    alone govern the content inset.
    """
    m = setup.margins_mm
    return (
        "@page{size:"
        + _fmt_mm(setup.page_width_mm)
        + " "
        + _fmt_mm(setup.page_height_mm)
        + ";margin:"
        + " ".join(_fmt_mm(m[s]) for s in _SIDES)
        + "}"
    )
