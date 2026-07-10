#!/usr/bin/env python3
"""Regenerate Appendix A of spec.md from the registry.

The registry (src/aimformat/registry.json) is the single source of truth for
the vocabulary; this script rewrites the section between the BEGIN/END
GENERATED markers so the spec can never drift from the linter and the
stylesheet. Run from the repo root after any registry change:

    python3 scripts/gen_spec_appendix.py

tests/test_spec.py fails if the committed appendix is stale.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from aimformat.registry import REGISTRY  # noqa: E402

SPEC = pathlib.Path(__file__).parent.parent / "spec.md"
BEGIN = "<!-- BEGIN GENERATED REGISTRY REFERENCE (scripts/gen_spec_appendix.py) -->"
END = "<!-- END GENERATED REGISTRY REFERENCE -->"


def code(items) -> str:
    return " ".join(f"`{i}`" for i in items)


def build() -> str:
    r = REGISTRY
    raw = r.raw
    out: list[str] = []
    a = out.append

    a("*This appendix is generated from `src/aimformat/registry.json` — the")
    a("machine-readable registry that also drives the linter and the")
    a("stylesheet. Do not edit it by hand.*")
    a("")

    a("### A.1 Elements")
    a("")
    a(f"- **Block chunk carriers** (top level and inside slides): "
      f"{code(raw['elements']['block_carriers'])}")
    a(f"- **Item chunk carriers**: " + "; ".join(
        f"`{k}` inside {code(v)}" for k, v in raw["elements"]["item_carriers"].items()))
    a(f"- **Containers** (`data-aim-container`): "
      f"{code(raw['elements']['containers'])} plus `aim-slide`")
    a(f"- **Table shells** (scaffolding between container and row chunks): "
      f"{code(raw['elements']['table_shells'])}")
    a(f"- **Allowed inside chunk subtrees**: "
      f"{code(raw['elements']['chunk_content'])}")
    a(f"- **Asset registry content**: "
      f"{code(raw['elements']['asset_registry_content'])}")
    a(f"- **Explicitly forbidden** (security, X001): "
      f"{code(raw['elements']['forbidden'])}")
    a("")

    a("### A.2 Class vocabulary")
    a("")
    c = raw["classes"]
    a(f"- **Type scale** `text-*`: {code(c['type_scale'])}")
    a(f"- **Weights** `font-*`: {code(c['font_weights'])}")
    a(f"- **Leading** `leading-*`: {code(c['leadings'])}")
    a(f"- **Alignment** `text-*`: {code(c['alignments'])}")
    pal = "; ".join(f"`{fam}` ({', '.join(shades)})"
                    for fam, shades in c["palette"].items())
    a(f"- **Palette** for `text-` / `bg-` / `border-`: {pal}; plus "
      f"`white` and theme-backed `brand-1…{c['brand_slot_count']}`")
    a(f"- **Spacing** `{'`, `'.join(c['spacing_props'])}` × scale "
      f"{code(c['spacing_scale'])}")
    a(f"- **Singles**: {code(sorted(c['singles']))}")
    a("")
    a(f"Total registered utilities: **{len(r.allowed_classes)}**.")
    a("")

    a("### A.3 Inline style properties")
    a("")
    a("| property | value grammar |")
    a("|---|---|")
    for prop in r.style_prop_order:
        a(f"| `{prop}` | `{raw['style_props']['patterns'][prop]}` |")
    a("")

    a("### A.4 Theme slots")
    a("")
    a("| slot | type | default |")
    a("|---|---|---|")
    for name, spec in r.theme_slots.items():
        a(f"| `{name}` | {spec['type']} | `{spec['default']}` |")
    a("")

    a("### A.5 Proposal attributes and event fields")
    a("")
    a(f"- `aim-proposal` attributes: "
      f"{code(raw['attributes']['per_element']['aim-proposal'])}")
    for action, spec in raw["proposal_actions"].items():
        payload = "payload" if spec["payload"] else "payloadless"
        req = code(spec["requires"]) if spec["requires"] else "—"
        a(f"- `{action}`: {payload}; requires {req}")
    a("")
    for kind, fields in raw["events"]["fields"].items():
        a(f"- `{kind}` events — required: {code(fields['required'])}"
          + (f"; optional: {code(fields['optional'])}"
             if fields["optional"] else ""))
    a("")

    a("### A.6 Page setup")
    a("")
    page = raw["page"]
    a("| size | portrait (mm) |")
    a("|---|---|")
    for name, (w, h) in page["sizes_mm"].items():
        a(f"| `{name}` | {w} × {h} |")
    a("")
    a(f"- **Orientations**: {code(page['orientations'])}")
    a(f"- **Margin grammar**: `{page['margin_pattern']}`, at most "
      f"{page['margin_max_mm']}mm per side, and the margins MUST leave a "
      "positive content area")
    d = page["default"]
    a(f"- **Default**: `{d['size']}` {d['orientation']}, margins "
      + " ".join(f"{k} `{v}`" for k, v in d["margins"].items()))
    a("")

    a("### A.7 Verifier rule codes")
    a("")
    a("| code | level | rule |")
    a("|---|---|---|")
    for rule, (level, summary) in raw["lint_rules"].items():
        a(f"| {rule} | {level} | {summary} |")
    a("")
    return "\n".join(out)


def main() -> None:
    text = SPEC.read_text("utf-8")
    start = text.index(BEGIN) + len(BEGIN)
    end = text.index(END)
    new = text[:start] + "\n" + build() + text[end:]
    SPEC.write_text(new, "utf-8")
    print(f"regenerated appendix in {SPEC.name}")


if __name__ == "__main__":
    main()
