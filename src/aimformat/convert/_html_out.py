"""`.aim` → standalone HTML.

A `.aim` file already *is* a self-contained styled HTML page; "export as
HTML" therefore means: a flattened copy — history and embeddings stripped —
suitable for sharing/publishing, with a caller-chosen fate for the pending
lane. The default keeps the proposals appendix: pending changes must stay
visible to a plain reader (spec §5.5), and the appendix renders as the
pure-CSS change memo.
"""

from __future__ import annotations

from ..document import AimDocument
from ..errors import InvalidOperation

__all__ = ["to_html"]


def to_html(doc: AimDocument, *, pending: str = "keep") -> str:
    """Render *doc* as a standalone HTML page (flattened copy).

    ``pending``: ``"keep"`` (default — appendix stays, visible as the change
    memo), ``"accept-all"`` or ``"reject-all"`` (resolved on a throwaway
    copy through the real accept/reject machinery; *doc* is never mutated).
    """
    if pending == "keep":
        copy = AimDocument.loads(doc.dumps())
    elif pending in ("accept-all", "reject-all"):
        from ..export_docx import _resolve_copy

        copy = _resolve_copy(doc, pending)
    else:
        raise InvalidOperation(
            f"pending must be 'keep', 'accept-all', or 'reject-all', got {pending!r}"
        )
    copy.flatten(drop_embeddings=True)
    return copy.dumps()
