"""Computed paint: what colour a browser actually renders an element in.

Converters that have to agree with the browser (DOCX today, anything
box-oriented tomorrow) need the *computed* value, not the declarations. That
is not a lookup: the generated stylesheet emits class rules sorted by name,
CSS is last-wins, and shorthands reset the longhands they omit — so
``class="border-t border-red-600"`` renders GREY, because
``.border-t{border-top:1px solid #e5e7eb}`` is emitted after
``.border-red-600{border-color:#dc2626}``. Matching declarations by property
name gets that backwards.

So this module runs the cascade the stylesheet describes, against the
stylesheet itself (:func:`aimformat.css.generate_aim_css`) rather than a
second hand-written copy of the vocabulary, and resolves a whole tree in one
traversal into immutable records keyed by object identity. Nothing here
writes to the tree: an exporter that copied resolved classes back onto source
elements corrupted an in-memory document during a tracked export, and the
records exist so no caller ever needs to.

Two deliberate asymmetries, both from the same rule — *only what the author
declared crosses into another format*:

- **Base-layer values compute but do not paint.** ``a{color:var(--aim-brand-1)}``
  means a link inside a red block is not red in a browser either, so the link
  gets no colour here — and Word's own template owns hyperlink ink. The
  base layer still participates in the cascade, because it is what stops the
  inherited colour.
- **Borders exist independently of their colour.** ``border-color`` recolours
  a border; it never creates one. Which sides exist comes from the utilities
  and from base-layer rules (``hr``, ``blockquote``, ``th``/``td`` carry a
  border with no utility present).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import lru_cache

from .css import generate_aim_css
from .dom import Element
from .registry import REGISTRY

__all__ = ["BorderSide", "Paint", "PaintContext", "PaintResolver", "brand_defaults", "rgb_of"]

SIDES = ("top", "right", "bottom", "left")

_HEX6 = re.compile(r"^#?([0-9a-fA-F]{6})$")
_HEX3 = re.compile(r"^#?([0-9a-fA-F]{3})$")
_RGB_FUNC = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.I)
_VAR_REF = re.compile(r"^var\(\s*(--[a-z0-9-]+)\s*\)$", re.I)
_PX = re.compile(r"^(\d+(?:\.\d+)?)px$")

_BORDER_STYLES = frozenset(
    {"none", "hidden", "solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset"}
)
_INVISIBLE_STYLES = frozenset({"none", "hidden"})

#: Properties through which an author picks a COLOUR. A colour arriving only
#: as a component of a border shorthand is the utility's default ink, not a
#: choice. If an explicit colour declaration also participated, however, a
#: later shorthand reset is the computed result and must cross into another
#: format; otherwise `.border-t.border-red-600` is grey in a browser and
#: absent in Word.
_COLOUR_PROPS = frozenset(
    {"color", "background-color", "border-color", *(f"border-{s}-color" for s in SIDES)}
)


def brand_defaults() -> dict[str, str]:
    """The registry's default value for every colour theme slot."""
    return {
        name: slot["default"]
        for name, slot in REGISTRY.theme_slots.items()
        if slot.get("type") == "color"
    }


def rgb_of(value: str, palette: Mapping[str, str] | None = None) -> str | None:
    """A CSS colour as six upper-case hex digits, or None when unsure.

    Covers what the format can produce: the paint grammar's ``#rrggbb``, the
    looser theme grammar's ``#rgb``/``rgb(r, g, b)``, and ``var(--slot)``
    through *palette*. Anything else returns None — a converter wants a
    concrete value, and writing a guessed colour is worse than leaving the
    recipient's default.
    """
    value = value.strip()
    var = _VAR_REF.match(value)
    if var:
        slot = (palette or {}).get(var.group(1))
        return rgb_of(slot, palette) if slot else None
    m = _HEX6.match(value)
    if m:
        return m.group(1).upper()
    m = _HEX3.match(value)
    if m:
        return "".join(c * 2 for c in m.group(1)).upper()
    m = _RGB_FUNC.match(value)
    if m:
        # the theme grammar accepts any 1-3 digit component, so a lint-clean
        # theme can carry rgb(999,0,0). Browsers CLAMP to 0-255; match them
        # rather than dropping the colour.
        return "".join(f"{min(255, int(g)):02X}" for g in m.groups())
    return None


# --------------------------------------------------------------------------
# the stylesheet, read as rules


@dataclass(frozen=True)
class _Rule:
    """One supported rule of the generated stylesheet, in emission order."""

    kind: str  # "type" | "descendant" | "class"
    key: str  # tag, "ancestor descendant", or class name
    declarations: tuple[tuple[str, str], ...]


_SELECTOR_TYPE = re.compile(r"^[a-z][a-z0-9-]*$")
_SELECTOR_DESCENDANT = re.compile(r"^([a-z][a-z0-9-]*)\s+([a-z][a-z0-9-]*)$")
_SELECTOR_CLASS = re.compile(r"^\.([A-Za-z0-9_-]+)$")


def _iter_top_level_rules(css: str) -> Iterator[tuple[str, str]]:
    """(selector list, declaration block) for every rule at the top level.

    At-rules are skipped whole: ``@media print`` and the responsive slide
    chrome describe presentation contexts no document converter is in.
    """
    i, n = 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            return
        selector = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        if not selector.startswith("@"):
            yield selector, css[brace + 1 : j - 1]
        i = j


def _split_declarations(block: str) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for piece in block.split(";"):
        prop, sep, value = piece.partition(":")
        if sep:
            out.append((prop.strip().lower(), value.strip()))
    return tuple(out)


@lru_cache(maxsize=1)
def _stylesheet_rules() -> tuple[_Rule, ...]:
    """The generated stylesheet reduced to the selectors paint needs.

    Besides simple type/class selectors, the base layer has two type-only
    descendant rules whose backgrounds affect conversion (`thead th` and
    `pre code`). Attribute and pseudo-element selectors carry no paint.
    """
    rules: list[_Rule] = []
    for selector_list, block in _iter_top_level_rules(generate_aim_css()):
        declarations = _split_declarations(block)
        if not declarations:
            continue
        for selector in (s.strip().replace("\\/", "/") for s in selector_list.split(",")):
            if _SELECTOR_TYPE.match(selector):
                rules.append(_Rule("type", selector, declarations))
            else:
                descendant = _SELECTOR_DESCENDANT.match(selector)
                class_rule = _SELECTOR_CLASS.match(selector)
                if descendant:
                    rules.append(_Rule("descendant", selector, declarations))
                elif class_rule:
                    rules.append(_Rule("class", class_rule.group(1), declarations))
    return tuple(rules)


@lru_cache(maxsize=1)
def _class_rules_by_key() -> dict[str, list[_Rule]]:
    by_class: dict[str, list[_Rule]] = {}
    for rule in _stylesheet_rules():
        if rule.kind == "class":
            by_class.setdefault(rule.key, []).append(rule)
    return by_class


# --------------------------------------------------------------------------
# shorthand expansion


def _expand(prop: str, value: str) -> Iterator[tuple[str, str]]:
    """One declaration as the paint longhands it sets.

    Shorthands reset the longhands they omit to their initial values — the
    whole reason ``.border-t`` can un-paint ``.border-red-600``.
    """
    if prop == "color":
        yield "color", value
    elif prop in ("background", "background-color"):
        yield "background-color", "transparent" if value == "none" else value
    elif prop == "border":
        yield from _expand_border_shorthand(SIDES, value)
    elif prop.startswith("border-") and prop[len("border-") :] in SIDES:
        yield from _expand_border_shorthand((prop[len("border-") :],), value)
    elif prop in ("border-color", "border-width", "border-style"):
        for side in SIDES:
            yield f"border-{side}-{prop[len('border-') :]}", value
    elif prop.startswith("border-"):
        rest = prop[len("border-") :]
        side, _, facet = rest.partition("-")
        if side in SIDES and facet in ("color", "width", "style"):
            yield prop, value


def _expand_border_shorthand(sides: tuple[str, ...], value: str) -> Iterator[tuple[str, str]]:
    width, style, colour = "medium", "none", "currentcolor"
    for token in value.split():
        low = token.lower()
        if low in _BORDER_STYLES:
            style = low
        elif _PX.match(low) or low == "0":
            width = low
        else:
            colour = token
    for side in sides:
        yield f"border-{side}-width", width
        yield f"border-{side}-style", style
        yield f"border-{side}-color", colour


def _width_px(value: str) -> float:
    m = _PX.match(value)
    if m:
        return float(m.group(1))
    return {"0": 0.0, "thin": 1.0, "medium": 3.0, "thick": 5.0}.get(value, 0.0)


# --------------------------------------------------------------------------
# the computed record


@dataclass(frozen=True)
class BorderSide:
    """One painted border side: the colour the author asked for, and how
    wide the side that carries it is."""

    color: str
    width_px: float


@dataclass(frozen=True)
class Paint:
    """What an element paints, as far as another format can reproduce it.

    ``color`` is the inherited authored text colour; ``own_background`` is a
    background declared on this element (the one an inline run carries) and
    ``background`` is the box showing behind it (the one a block or cell
    shades with). ``borders`` holds only sides that are both visible and
    authored — everything else is the recipient template's business.
    """

    color: str | None = None
    background: str | None = None
    own_background: str | None = None
    borders: Mapping[str, BorderSide] = field(default_factory=dict)

    @property
    def any_border(self) -> BorderSide | None:
        """The border a format with no per-side notion should use."""
        for side in SIDES:
            if side in self.borders:
                return self.borders[side]
        return None


@dataclass(frozen=True)
class PaintContext:
    """What an element inherits from its ancestors."""

    color: str | None = None
    background: str | None = None


EMPTY = Paint()
ROOT_CONTEXT = PaintContext()


class PaintResolver:
    """Resolve computed paint for whole trees, once, by object identity.

    One instance per export. Elements from different roots (each live
    construct and parsed pending payload) share it, so a lookup never needs
    to know which tree an element came from.
    """

    def __init__(self, palette: Mapping[str, str] | None = None):
        # a document's theme over the registry defaults: aim.css ships a
        # default for every slot, so `var(--aim-brand-1)` always resolves
        self.palette = {**brand_defaults(), **(palette or {})}
        # the element is held alongside its record: id() is only unique while
        # the object is alive, and a resolver that let its keys be collected
        # could hand a later element another's paint
        self._records: dict[int, tuple[Element, Paint]] = {}

    # -- public ---------------------------------------------------------------
    def resolve(
        self,
        root: Element,
        *,
        inherited: PaintContext | None = None,
        ancestor_tags: tuple[str, ...] = (),
    ) -> None:
        """Compute and store paint for *root* and everything beneath it.

        ``ancestor_tags`` supplies the future selector context for a detached
        proposal payload. Live roots leave it empty because their descendants
        acquire the same context during the normal tree walk.
        """
        self._walk(root, inherited or ROOT_CONTEXT, ancestor_tags)

    def of(self, el: Element) -> Paint:
        """The computed record for *el* — unpainted when never resolved."""
        hit = self._records.get(id(el))
        return hit[1] if hit is not None and hit[0] is el else EMPTY

    def context_of(self, el: Element | None) -> PaintContext:
        """The context an element's children inherit — what a payload
        replacing a child of *el* should be resolved against."""
        if el is None:
            return ROOT_CONTEXT
        record = self.of(el)
        return PaintContext(color=record.color, background=record.background)

    @staticmethod
    def context(*, color: str | None = None, background: str | None = None) -> PaintContext:
        return PaintContext(color=color, background=background)

    def adopt(self, synthetic: Element, source: Element) -> None:
        """Give a synthetic block the paint of the element it stands in for.

        Exporters rebuild grouping content into fresh block elements; the
        wrapper is not in the source tree and must not be resolved as if it
        were (it has no ancestors and its own tag's base layer is not the
        source's). Its children keep the records they already have.
        """
        self._records[id(synthetic)] = (synthetic, self.of(source))

    def overlay_borders(self, el: Element, inherited: Mapping[str, BorderSide]) -> None:
        """Add a grouping box's borders to one emitted descendant block.

        This is an exporter degradation, not CSS inheritance. Direct borders
        on the descendant win where Word can represent only one side value.
        """
        if not inherited:
            return
        current = self.of(el)
        merged = {**inherited, **current.borders}
        self._records[id(el)] = (
            el,
            Paint(
                color=current.color,
                background=current.background,
                own_background=current.own_background,
                borders=merged,
            ),
        )

    # -- internals ------------------------------------------------------------
    def _walk(self, el: Element, ctx: PaintContext, ancestors: tuple[str, ...]) -> None:
        computed = self._cascade(el, ancestors)
        colour, colour_ctx = self._text_colour(computed, ctx)
        own_bg, bg_ctx = self._background(computed, ctx)
        record = Paint(
            color=colour,
            background=own_bg if own_bg is not None else bg_ctx,
            own_background=own_bg,
            borders=self._borders(computed),
        )
        self._records[id(el)] = (el, record)
        child_ctx = PaintContext(color=colour_ctx, background=record.background)
        for child in el.elements():
            self._walk(child, child_ctx, (*ancestors, el.tag))

    def _cascade(self, el: Element, ancestors: tuple[str, ...]) -> dict[str, tuple[str, bool]]:
        """Longhand -> (value, explicit-colour-seen) after the cascade.

        Sequential overwrite IS the cascade here: type rules come first in
        source order, then class rules in the stylesheet's own (alphabetical)
        emission order, then the inline style — increasing specificity, so a
        later write always wins.
        """
        by_class = _class_rules_by_key()
        out: dict[str, tuple[str, bool]] = {}

        def apply(declarations: tuple[tuple[str, str], ...], *, authored: bool) -> None:
            for prop, value in declarations:
                chosen = authored and prop in _COLOUR_PROPS
                for longhand, expanded in _expand(prop, value):
                    # Keep the explicit-colour signal through a later
                    # shorthand reset. The reset supplies the computed value;
                    # the earlier colour declaration is why that value belongs
                    # in the conversion at all.
                    seen = out.get(longhand, ("", False))[1]
                    out[longhand] = (expanded, seen or chosen)

        ancestor_tags = set(ancestors)
        for rule in _stylesheet_rules():
            if rule.kind == "type" and rule.key == el.tag:
                apply(rule.declarations, authored=False)
            elif rule.kind == "descendant":
                ancestor, descendant = rule.key.split()
                if descendant == el.tag and ancestor in ancestor_tags:
                    apply(rule.declarations, authored=False)
        for name in sorted(set((el.get("class") or "").split())):
            for rule in by_class.get(name, ()):
                apply(rule.declarations, authored=True)
        apply(_inline_declarations(el), authored=True)
        return out

    def _text_colour(
        self, computed: dict[str, tuple[str, bool]], ctx: PaintContext
    ) -> tuple[str | None, str | None]:
        declared = computed.get("color")
        if declared is None:
            return ctx.color, ctx.color
        value, authored = declared
        # a base-layer colour is a real declaration: it wins over the
        # inherited value in the browser too, so it stops inheritance here
        # even though it paints nothing
        resolved = rgb_of(value, self.palette) if authored else None
        return resolved, resolved

    def _background(
        self, computed: dict[str, tuple[str, bool]], ctx: PaintContext
    ) -> tuple[str | None, str | None]:
        declared = computed.get("background-color")
        if declared is None:
            return None, ctx.background
        value, authored = declared
        if value == "transparent":
            return None, ctx.background
        own = rgb_of(value, self.palette) if authored else None
        # an opaque background hides the ancestor box whether or not we can
        # paint it, so the ancestor colour stops here either way
        return own, own

    def _borders(self, computed: dict[str, tuple[str, bool]]) -> Mapping[str, BorderSide]:
        out: dict[str, BorderSide] = {}
        for side in SIDES:
            style = computed.get(f"border-{side}-style", ("none", False))[0]
            width = _width_px(computed.get(f"border-{side}-width", ("medium", False))[0])
            if style in _INVISIBLE_STYLES or width <= 0:
                continue
            colour = computed.get(f"border-{side}-color")
            if colour is None or not colour[1]:
                continue  # base-layer ink: leave the recipient's own border
            resolved = rgb_of(colour[0], self.palette)
            if resolved is not None:
                out[side] = BorderSide(color=resolved, width_px=width)
        return out


def _inline_declarations(el: Element) -> tuple[tuple[str, str], ...]:
    """The element's inline paint declarations, geometry dropped."""
    out: list[tuple[str, str]] = []
    for piece in (el.get("style") or "").split(";"):
        prop, sep, value = piece.partition(":")
        prop = prop.strip().lower()
        if sep and prop in REGISTRY.paint_props:
            out.append((prop, value.strip()))
    return tuple(out)
