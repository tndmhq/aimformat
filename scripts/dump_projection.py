#!/usr/bin/env python3
"""Dump the Python SDK's read projection of each parity source to golden JSON.

The goldens under ``tests/parity/goldens/`` are the cross-implementation
contract: the TypeScript reader (``ts/``) asserts field-for-field equality
against them, and ``tests/test_parity_goldens.py`` asserts that regenerating
them is byte-stable (catching Python-side drift). Sources are the shipped
``examples/*.aim`` plus the edge fixtures from ``gen_parity_fixtures.py``.

The golden schema uses the TypeScript reader's field names; where the Python
SDK has no public accessor for a derived view (the recursive node tree, the
stylesheet block, raw trailer text), this script derives it from the same
primitives ``AimDocument`` itself uses.

Run from the repo root: python3 scripts/dump_projection.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

import aimformat as aim  # noqa: E402
from aimformat.document import AimDocument, DocState  # noqa: E402
from aimformat.dom import Element  # noqa: E402
from aimformat.registry import REGISTRY  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "parity" / "fixtures"
GOLDEN_DIR = ROOT / "tests" / "parity" / "goldens"


def sources() -> list[pathlib.Path]:
    return sorted((ROOT / "examples").glob("*.aim")) + sorted(FIXTURE_DIR.glob("*.aim"))


def conformance_sources() -> list[pathlib.Path]:
    """The ok_* conformance kit: canonical by definition, so every file is a
    cheap extra parser/hash parity probe (hash-only, no full golden)."""
    return sorted((ROOT / "tests" / "fixtures").glob("ok_*.aim"))


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _opaque_block(state: DocState, kind: str) -> dict | None:
    el = state.script(kind)
    if el is None:
        return None
    raw = el.raw or ""
    return {
        "sha256": _sha(raw),
        "lines": len([ln for ln in raw.split("\n") if ln.strip()]),
    }


def _chunk_obj(c: aim.Chunk) -> dict:
    return {
        "kind": "chunk",
        "id": c.id,
        "container": c.container,
        "tags": list(c.tags),
        "html": c.html,
        "text": c.text,
        "tag": c.tag,
        "isRun": c.is_run,
    }


def _parent_container(state: DocState, el: Element) -> str:
    node = state._parent_of(el)
    while node is not None and node is not state.body:
        if node.container_id:
            return node.container_id
        node = state._parent_of(node)
    return "body"


def _container_obj(state: DocState, el: Element, chunk_by_id: dict[str, dict]) -> dict:
    members: list[dict] = []
    seen: set[str] = set()

    def add(m: Element) -> None:
        if m.container_id:
            members.append(_container_obj(state, m, chunk_by_id))
        elif m.chunk_id and m.chunk_id not in seen:
            seen.add(m.chunk_id)  # run members collapse into one chunk node
            members.append(chunk_by_id[m.chunk_id])

    for child in el.elements():
        if el.tag == "table" and child.tag in REGISTRY.table_shells:
            for row in child.elements():
                add(row)
        else:
            add(child)

    attrs: dict[str, str] = {}
    for k, v in el.attrs:
        attrs.setdefault(k, v if v is not None else "")
    return {
        "kind": "container",
        "id": el.container_id or "",
        "tag": el.tag,
        "container": _parent_container(state, el),
        "attrs": attrs,
        "members": members,
    }


def _nodes(state: DocState, chunk_by_id: dict[str, dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for top in state.constructs():
        if top.container_id:
            out.append(_container_obj(state, top, chunk_by_id))
        elif top.chunk_id and top.chunk_id not in seen:
            seen.add(top.chunk_id)
            out.append(chunk_by_id[top.chunk_id])
    return out


def _proposal_obj(p: aim.Proposal) -> dict:
    return {
        "id": p.id,
        "action": p.action,
        "target": p.target,
        "author": {"type": p.author.type, "id": p.author.id, "model": p.author.model},
        "at": p.at,
        "explanation": p.explanation,
        "payloadHtml": p.payload_html,
        "anchorContainer": p.anchor_container,
        "anchorAfter": p.anchor_after,
        "anchorShell": p.anchor_shell,
        "dependsOn": p.depends_on,
        "batch": p.batch,
    }


def _stylesheet(state: DocState) -> dict | None:
    el = state.css_el()
    if el is None:
        return None
    return {"version": el.get("data-aim-css") or "", "sha256": _sha(el.raw or "")}


def _asset_ids(state: DocState) -> list[str]:
    sec = state.section("aim-assets")
    if sec is None:
        return []
    svg = next((e for e in sec.elements() if e.tag == "svg"), None)
    if svg is None:
        return []
    return [s.get("id") or "" for s in svg.elements() if s.tag == "symbol"]


def projection(doc: AimDocument) -> dict:
    state = doc._state
    resolved = doc.page_setup.resolved()
    chunks = [_chunk_obj(c) for c in doc.chunks]
    chunk_by_id = {c["id"]: c for c in chunks}
    return {
        "specVersion": doc.spec_version,
        "lang": doc.lang,
        "title": doc.title,
        "docHash": doc.doc_hash,
        "note": doc.note,
        "hasCanonicalNote": doc.has_canonical_note(),
        "stylesheet": _stylesheet(state),
        "theme": doc.theme,
        "meta": doc.meta,
        "docSettings": doc.doc_settings,
        "pageSetup": {
            "size": resolved["size"],
            "orientation": resolved["orientation"],
            "marginsMm": resolved["margins_mm"],
            "pageWidthMm": resolved["page_width_mm"],
            "pageHeightMm": resolved["page_height_mm"],
            "contentWidthMm": resolved["content_width_mm"],
            "contentHeightMm": resolved["content_height_mm"],
        },
        "nodes": _nodes(state, chunk_by_id),
        "chunks": chunks,
        "containers": doc.containers,
        "bodyIds": doc.body_ids,
        "proposals": [_proposal_obj(p) for p in doc.proposals],
        "assetIds": _asset_ids(state),
        "history": _opaque_block(state, "history"),
        "embeddings": _opaque_block(state, "embeddings"),
    }


def render(doc: AimDocument) -> str:
    return json.dumps(projection(doc), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_conformance_hashes() -> str:
    hashes = {p.name: aim.load(p).doc_hash for p in conformance_sources()}
    return json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    expected = {f"{p.stem}.json" for p in sources()} | {"conformance-hashes.json"}
    for stale in GOLDEN_DIR.glob("*.json"):
        if stale.name not in expected:
            stale.unlink()
            print(f"removed stale {stale.relative_to(ROOT)}")
    for path in sources():
        golden = GOLDEN_DIR / f"{path.stem}.json"
        golden.write_text(render(aim.load(path)), "utf-8")
        print(f"wrote {golden.relative_to(ROOT)}")
    hashes = GOLDEN_DIR / "conformance-hashes.json"
    hashes.write_text(render_conformance_hashes(), "utf-8")
    print(f"wrote {hashes.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
