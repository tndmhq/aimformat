"""Registry loader — the machine-readable single source of truth.

``registry.json`` (shipped as package data) defines the closed v0.1
vocabulary: elements, attributes, class utilities, inline-style whitelist,
theme slots, event schemas, and canonical-form tables. The linter, the
``aim.css`` generator, and the generated spec appendix all read from here so
they can never drift from each other.
"""

from __future__ import annotations

import json
import re
from functools import cached_property
from importlib import resources


def version_key(value: str) -> tuple[int, ...] | None:
    """A dotted numeric spec version as a comparable tuple, or None."""
    parts = value.split(".")
    if not all(p.isdigit() for p in parts) or not parts:
        return None
    return tuple(int(p) for p in parts)


class Registry:
    """Typed accessors over the raw registry tables."""

    def __init__(self, raw: dict):
        self.raw = raw

    # -- versions ------------------------------------------------------------
    @property
    def spec_version(self) -> str:
        return self.raw["spec_version"]

    def implements(self, declared: str | None) -> bool:
        """Whether this build understands a document declaring *declared*.

        Spec versions are backward-compatible in one direction only: a 0.3
        tool reads every 0.2 document, but a 0.2 tool rejects 0.3 markup it
        has no rule for. So "same or older" is understood and only a NEWER
        version is a finding — the alternative (any difference warns) fires
        on every existing valid file the moment the toolkit moves on. An
        unparseable version is treated as not understood.
        """
        if declared is None:
            return False
        mine = version_key(self.spec_version)
        theirs = version_key(declared)
        return theirs is not None and mine is not None and theirs <= mine

    @staticmethod
    def version_includes(declared: str | None, introduced: str) -> bool:
        """Whether *declared* is at least the version that introduced a feature."""
        theirs = version_key(declared) if declared is not None else None
        floor = version_key(introduced)
        return theirs is not None and floor is not None and theirs >= floor

    # -- elements ------------------------------------------------------------
    @cached_property
    def block_carriers(self) -> frozenset[str]:
        return frozenset(self.raw["elements"]["block_carriers"])

    @cached_property
    def item_carriers(self) -> dict[str, list[str]]:
        return self.raw["elements"]["item_carriers"]

    @cached_property
    def containers(self) -> frozenset[str]:
        return frozenset(self.raw["elements"]["containers"])

    @cached_property
    def table_shells(self) -> frozenset[str]:
        return frozenset(self.raw["elements"]["table_shells"])

    @cached_property
    def chunk_content(self) -> frozenset[str]:
        return frozenset(self.raw["elements"]["chunk_content"])

    @cached_property
    def asset_content(self) -> frozenset[str]:
        return frozenset(self.raw["elements"]["asset_registry_content"])

    @cached_property
    def void_elements(self) -> frozenset[str]:
        return frozenset(self.raw["elements"]["void"])

    @cached_property
    def forbidden_elements(self) -> frozenset[str]:
        return frozenset(self.raw["elements"]["forbidden"])

    # -- attributes ----------------------------------------------------------
    def allowed_attrs(self, tag: str) -> frozenset[str]:
        per = self.raw["attributes"]["per_element"].get(tag, [])
        if tag in (
            "html",
            "script",
            "style",
            "template",
            "symbol",
            "image",
            "rect",
            "circle",
            "ellipse",
            "path",
            "g",
            "use",
        ):
            return frozenset(per)  # non-content elements: exact lists only
        base = set(self.raw["attributes"]["global"]) | {self.raw["attributes"]["chunk_marker"]}
        if tag in self.containers or tag == "aim-slide":
            base.add(self.raw["attributes"]["container_marker"])
        return frozenset(base | set(per))

    def url_schemes(self, key: str) -> list[str]:
        return self.raw["attributes"]["url_schemes"].get(key, [])

    def url_allowed(self, key: str, value: str) -> bool:
        """Whether *value* matches a registered scheme for ``key`` (e.g.
        ``"a.href"``). Single source of truth for URL policy: bare scheme
        tokens (http, mailto) must be the value's actual scheme — the text
        before the first ':' — '#' is fragment-only, and tokens carrying a
        ':' (data:image/) are exact prefixes. No registered schemes means
        no restriction. The linter (V009) and converters both call this."""
        schemes = self.url_schemes(key)
        if not schemes:
            return True
        low = value.lower()
        if "#" in schemes and low.startswith("#"):
            return True
        if any(low.startswith(s.lower()) for s in schemes if ":" in s):
            return True
        bare = {s.lower() for s in schemes if ":" not in s and s != "#"}
        return ":" in low and low.split(":", 1)[0] in bare

    # -- classes -------------------------------------------------------------
    @cached_property
    def class_declarations(self) -> dict[str, str]:
        """Expand the compact class tables into ``{class: css-declaration}``."""
        c = self.raw["classes"]
        out: dict[str, str] = {}
        for k, (fs, lh) in c["type_scale"].items():
            out[f"text-{k}"] = f"font-size:{fs};line-height:{lh}"
        for k, v in c["font_weights"].items():
            out[f"font-{k}"] = f"font-weight:{v}"
        for k, v in c["leadings"].items():
            out[f"leading-{k}"] = f"line-height:{v}"
        for k in c["alignments"]:
            out[f"text-{k}"] = f"text-align:{k}"
        for family, shades in c["palette"].items():
            for shade, color in shades.items():
                out[f"text-{family}-{shade}"] = f"color:{color}"
                out[f"bg-{family}-{shade}"] = f"background-color:{color}"
                out[f"border-{family}-{shade}"] = f"border-color:{color}"
        for i in range(1, c["brand_slot_count"] + 1):
            out[f"text-brand-{i}"] = f"color:var(--aim-brand-{i})"
            out[f"bg-brand-{i}"] = f"background-color:var(--aim-brand-{i})"
            out[f"border-brand-{i}"] = f"border-color:var(--aim-brand-{i})"
        for prefix, props in c["spacing_props"].items():
            for k, v in c["spacing_scale"].items():
                out[f"{prefix}-{k}"] = ";".join(f"{p}:{v}" for p in props)
        out.update(c["singles"])
        return out

    @cached_property
    def allowed_classes(self) -> frozenset[str]:
        return frozenset(self.class_declarations)

    # -- inline styles ---------------------------------------------------------
    @cached_property
    def style_prop_order(self) -> list[str]:
        return self.raw["style_props"]["order"]

    @cached_property
    def style_patterns(self) -> dict[str, re.Pattern]:
        return {k: re.compile(v) for k, v in self.raw["style_props"]["patterns"].items()}

    @cached_property
    def paint_props(self) -> frozenset[str]:
        """The inline-style properties that carry literal paint (spec §3.3).

        Named in the registry rather than derived, so a consumer asking "is
        this declaration geometry or paint?" reads one table instead of
        re-deriving the split from property names."""
        return frozenset(self.raw["style_props"]["paint"])

    @property
    def paint_since(self) -> str:
        """The first spec version whose style grammar includes literal paint."""
        return self.raw["style_props"]["paint_since"]

    @cached_property
    def typography_props(self) -> frozenset[str]:
        """The inline-style properties that carry literal typography (spec
        §3.3): per-element font size and font family. Split from paint the
        same way paint is split from geometry — one table to ask "which era
        does this declaration need"."""
        return frozenset(self.raw["style_props"]["typography"])

    @property
    def typography_since(self) -> str:
        """The first spec version whose grammar includes literal typography
        (the inline props above and the since-gated classes below)."""
        return self.raw["style_props"]["typography_since"]

    @cached_property
    def class_floors(self) -> dict[str, str]:
        """Classes introduced after 0.1, mapped to the spec version that
        introduced them. Absent means "since the beginning"."""
        return self.raw["classes"].get("since", {})

    @cached_property
    def type_scale_pt(self) -> dict[str, str]:
        """The normative pt value for each type-scale step (rem × 12) —
        what class-based sizes mean in point-based exports (DOCX, print)."""
        return self.raw["classes"]["type_scale_pt"]

    # -- page setup --------------------------------------------------------------
    @cached_property
    def page_sizes_mm(self) -> dict[str, list[float]]:
        return self.raw["page"]["sizes_mm"]

    @cached_property
    def page_orientations(self) -> frozenset[str]:
        return frozenset(self.raw["page"]["orientations"])

    @cached_property
    def page_default(self) -> dict:
        return self.raw["page"]["default"]

    @cached_property
    def margin_pattern(self) -> re.Pattern:
        return re.compile(self.raw["page"]["margin_pattern"])

    @property
    def margin_max_mm(self) -> float:
        return self.raw["page"]["margin_max_mm"]

    # -- theme -----------------------------------------------------------------
    @cached_property
    def theme_slots(self) -> dict[str, dict]:
        return self.raw["theme_slots"]

    @cached_property
    def theme_patterns(self) -> dict[str, re.Pattern]:
        return {k: re.compile(v) for k, v in self.raw["theme_value_patterns"].items()}

    # -- events / proposals ------------------------------------------------------
    @cached_property
    def event_fields(self) -> dict[str, dict[str, list[str]]]:
        return self.raw["events"]["fields"]

    @cached_property
    def proposal_actions(self) -> dict[str, dict]:
        return self.raw["proposal_actions"]

    # -- canonical form ----------------------------------------------------------
    @cached_property
    def attr_first(self) -> list[str]:
        return self.raw["attr_order"]["first"]

    @cached_property
    def attr_last(self) -> list[str]:
        return self.raw["attr_order"]["last"]

    @cached_property
    def svg_case_adjust(self) -> dict[str, str]:
        return self.raw["svg_case_adjust"]

    @cached_property
    def script_types(self) -> dict[str, str]:
        return self.raw["sections"]["script_types"]


def _load() -> Registry:
    text = resources.files("aimformat").joinpath("registry.json").read_text("utf-8")
    return Registry(json.loads(text))


REGISTRY = _load()
