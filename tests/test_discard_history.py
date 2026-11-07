"""Repairing a history that cannot be read (8b): ``history_text()``,
``baseline(..., discard_history=True)`` and ``aim baseline FILE
--discard-history``.

The repair keeps the body and the pending lane, drops the stored history
block without parsing it, and starts a fresh history from one baseline. A
pending card that names an id the repair replaced is rejected. The
CLI keeps the dropped text in a side file next to the document (owner-only,
never overwritten, durable before the document changes) and replaces the
document atomically, so a failure at any step leaves it as it was.
"""

from __future__ import annotations

import errno
import itertools
import os
import re
import stat

import pytest

import aimformat as aim
from aimformat import ids
from aimformat.cli import main

ADA = aim.human("ada")
BOT = aim.agent("model-x")
OPEN = '<script type="application/aim-history+jsonl">'
LANE = re.compile(r"<aim-proposals>.*?</aim-proposals>", re.S)
SIDE = ".discarded-history.jsonl"


def ts(i: int) -> str:
    return f"2026-11-07T12:{i // 60:02d}:{i % 60:02d}Z"


def _block(text: str) -> str:
    start = text.index(OPEN) + len(OPEN)
    return text[start : text.index("</script>", start)]


def _with_block(text: str, content: str) -> str:
    start = text.index(OPEN) + len(OPEN)
    return text[:start] + content + text[text.index("</script>", start) :]


def _cut_third_line(text: str) -> str:
    """The 2026-11-06 probe's damage: one history line cut in half."""
    lines = _block(text).strip("\n").split("\n")
    lines[2] = lines[2][: len(lines[2]) // 2]
    return _with_block(text, "\n" + "\n".join(lines) + "\n")


def _contract() -> aim.AimDocument:
    """Three adds (seq 1-3), a recorded modify (seq 4), and a pending modify
    whose payload names its target's id."""
    doc = aim.new_document(title="Probe")
    for i, markup in enumerate(
        (
            '<h1 data-aim="title">Contract</h1>',
            '<p data-aim="fee">Fee: CHF 9,000.</p>',
            '<p data-aim="term">Term: 12 months.</p>',
        )
    ):
        doc.add_chunk(markup, author=BOT, at=ts(i))
    doc.modify_chunk("fee", '<p data-aim="fee">Fee: CHF 10,000.</p>', author=ADA, at=ts(3))
    doc.propose_modify(
        "term", '<p data-aim="term">Term: 24 months.</p>', author=BOT, explanation="x", at=ts(4)
    )
    return doc


def _damaged() -> str:
    return _cut_third_line(_contract().dumps())


def _loose(text: str) -> str:
    """*text* with an unrelated body paragraph that has no id."""
    return text.replace('<p data-aim="term">', '<p>Loose paragraph.</p>\n<p data-aim="term">', 1)


def _invalid_id(text: str, old: str) -> str:
    """*text* with the id *old* respelled in upper case, an invalid id
    (S015), outside the history block: on the body unit, in pending
    payloads, and in every card that targets or anchors on it. A hand edit;
    no SDK write produces it."""
    cut = text.index(OPEN)
    head = text[:cut]
    for attr in (
        "data-aim",
        "data-aim-container",
        "data-for",
        "data-anchor-after",
        "data-anchor-container",
    ):
        head = head.replace(f'{attr}="{old}"', f'{attr}="{old.upper()}"')
    return head + text[cut:]


def _errors(text: str) -> list:
    return [f for f in aim.lint_text(text) if f.level == "error"]


def _tiny_png() -> bytes:
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
        + chunk(b"IEND", b"")
    )


class TestHistoryText:
    def test_is_the_block_as_stored_unparsed(self):
        text = _damaged()
        assert aim.loads(text).history_text() == _block(text)

    def test_keeps_carriage_returns(self):
        text = _with_block(_damaged(), _block(_damaged()).replace("\n", "\r\n"))
        assert "\r\n" in aim.loads(text).history_text()

    def test_is_empty_without_a_block(self):
        doc = _contract()
        doc._drop_history(drop_embeddings=False)
        assert doc.history_text() == ""


class TestDiscardHistory:
    def test_a_history_line_cut_in_half_is_repaired_in_one_call(self):
        text = _damaged()
        assert "H002" in {f.code for f in aim.lint_text(text)}
        doc = aim.loads(text)
        lane = LANE.search(text)
        assert lane is not None
        event = doc.baseline("repair", author=ADA, discard_history=True, at=ts(9))
        # the cut line was seq 3; the highest readable seq is the modify's 4
        assert (event.kind, event.seq, event.get("label")) == ("baseline", 5, "repair")
        assert [e.kind for e in doc.history] == ["baseline"]
        repaired = doc.dumps()
        assert aim.lint_text(repaired) == []
        assert doc.verify() == []
        assert LANE.search(repaired).group(0) == lane.group(0)  # pending lane unchanged
        # a later hand edit reconciles with its true `before` (the snapshot
        # spells "<\/p>", so this hits the body line only)
        edited = aim.loads(repaired.replace(">Fee: CHF 10,000.</p>", ">Fee: CHF 11,000.</p>"))
        (edit,) = edited.reconcile(at=ts(10)).events
        assert (edit.action, edit.target, edit.get("before")) == (
            "modify",
            "fee",
            '<p data-aim="fee">Fee: CHF 10,000.</p>',
        )
        assert edited.verify() == [] and _errors(edited.dumps()) == []

    @pytest.mark.parametrize(
        ("lines", "seq"),
        [
            (['{"seq":7}', '{"seq":3}'], 8),
            (['{"seq":0}'], 1),
            (['{"seq":-4}'], 1),
            (['{"seq":true}'], 1),
            (['{"seq":true}', '{"seq":2}'], 3),
            (['{"kind":"checkpoint"}'], 1),
            (
                [
                    '{"seq":5',
                    '{"kind":"x","seq":6}',
                    "not json",
                    '[{"seq":9}]',
                    '{"seq":"11"}',
                    '{"seq":12.5}',
                    "[" * 50_000,  # nests deeper than the JSON decoder recurses
                ],
                7,
            ),
        ],
        ids=["positive", "zero", "negative", "boolean", "boolean-and-int", "missing", "mixed"],
    )
    @pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
    def test_seq_continues_the_highest_readable_seq(self, lines, seq, newline):
        text = _with_block(_contract().dumps(), newline + newline.join(lines) + newline)
        doc = aim.loads(text)
        assert doc.baseline("repair", discard_history=True, at=ts(9)).seq == seq
        assert not [f for f in aim.lint_text(doc.dumps()) if f.code.startswith("H")]

    def test_a_readable_log_gives_the_same_result_as_without_the_flag(self):
        import base64

        doc = _contract()
        uri = "data:image/png;base64," + base64.b64encode(_tiny_png()).decode()
        doc.add_chunk(f'<figure data-aim="fig"><img alt="dot" src="{uri}"></figure>', author=ADA)
        doc.pack_assets(author=aim.external("packer"), at=ts(5))
        doc.delete_chunk("fig", author=ADA, at=ts(6))  # only the history keeps the image
        text = doc.dumps()
        plain, flagged = aim.loads(text), aim.loads(text)
        kwargs = {"author": ADA, "explanation": "why", "at": ts(9)}
        assert plain.baseline("restart", **kwargs).data == (
            flagged.baseline("restart", discard_history=True, **kwargs).data
        )
        assert flagged.dumps() == plain.dumps()
        assert "<aim-assets>" not in flagged.dumps()  # gc ran as the final pass

    @pytest.mark.parametrize("shape", ["no block", "empty block"])
    def test_a_document_without_history(self, shape):
        doc = _contract()
        if shape == "no block":
            doc._drop_history(drop_embeddings=False)
        else:
            doc = aim.loads(_with_block(doc.dumps(), "\n"))
        event = doc.baseline("start", discard_history=True, at=ts(9))
        assert event.seq == 1 and [e.kind for e in doc.history] == ["baseline"]
        assert _errors(doc.dumps()) == [] and doc.verify() == []

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ('<p data-aim="fee">', "<p>"),  # missing
            ('<p data-aim="fee">', '<p data-aim="title">'),  # duplicated
            ('<p data-aim="fee">', '<p data-aim="p-fee">'),  # invalid (p- is reserved)
        ],
        ids=["missing", "duplicated", "invalid"],
    )
    def test_units_without_a_usable_id_get_fresh_ids_with_the_flag(self, old, new):
        readable = _contract().dumps().replace(old, new, 1)
        with pytest.raises(aim.InvalidOperation, match="usable id"):
            aim.loads(readable).baseline("again")
        for source in (readable, _cut_third_line(readable)):
            doc = aim.loads(source)
            doc.baseline("repair", discard_history=True, at=ts(9))
            body = doc.body_ids
            assert len(set(body)) == 3 and body[0] == "title" and body[2] == "term"
            assert _errors(doc.dumps()) == [] and doc.verify() == []

    def test_a_pending_modify_keeps_its_target_when_another_unit_gets_an_id(self):
        # Plan review round 1 (P1): adoption's id repair, an empty
        # expected_alive set, renames a body id a pending payload names (a
        # pending modify carries its target's id), so the card dangles (P008)
        doc = aim.loads(_loose(_damaged()))
        doc.baseline("repair", discard_history=True, at=ts(9))
        assert aim.lint_text(doc.dumps()) == []
        (card,) = doc.proposals
        assert card.target == "term"
        doc.accept_all(decided_by=ADA, at=ts(10))
        assert "Term: 24 months." in [c.text for c in doc.chunks]
        assert aim.lint_text(doc.dumps()) == []

    @pytest.mark.parametrize("damaged", [True, False], ids=["unreadable", "readable"])
    def test_a_pending_modify_whose_invalid_target_id_is_replaced_is_rejected(self, damaged):
        # A hand edit respells a unit's id and the pending modify aimed at
        # it as an invalid id. The repair replaces
        # the unit's id, so the card would name an id that no longer exists
        # (P008, and accept_all() fails). It is rejected and the rejection
        # recorded, as reconcile rejects a card that no longer applies; a
        # card on a usable id stays.
        doc = _contract()
        doc.propose_modify("fee", '<p data-aim="fee">Fee: CHF 12,000.</p>', author=BOT, at=ts(5))
        text = _invalid_id(doc.dumps(), "term")
        doc = aim.loads(_cut_third_line(text) if damaged else text)
        lost, kept = doc.proposals
        assert (lost.target, kept.target) == ("TERM", "fee")
        event = doc.baseline("repair", author=ADA, discard_history=True, at=ts(9))
        assert event.kind == "baseline" and event.seq == doc.history[0].seq
        assert [e.kind for e in doc.history] == ["baseline", "resolution"]
        rejection = doc.history[1]
        assert (rejection.get("proposal"), rejection.decision, rejection.seq) == (
            lost.id,
            "rejected",
            event.seq + 1,
        )
        assert rejection.get("decided_by") == ADA.to_obj()
        assert "'TERM'" in rejection.get("explanation")
        assert [p.id for p in doc.proposals] == [kept.id]
        assert "TERM" not in doc.body_ids and len(doc.body_ids) == 3
        assert aim.lint_text(doc.dumps()) == [] and doc.verify() == []
        doc.accept_all(decided_by=ADA, at=ts(10))
        assert "Fee: CHF 12,000." in [c.text for c in doc.chunks]
        assert aim.lint_text(doc.dumps()) == []

    def test_pending_adds_anchored_on_a_replaced_id_are_rejected_with_their_chain(self):
        doc = _contract()
        doc.reject(doc.proposals[0].id, decided_by=ADA, at=ts(5))
        first = doc.propose_add('<p data-aim="n1">One.</p>', after="term", author=BOT, at=ts(6))
        doc.propose_add('<p data-aim="n2">Two.</p>', after=first.id, author=BOT, at=ts(7))
        kept = doc.propose_add('<p data-aim="n3">Kept.</p>', after="fee", author=BOT, at=ts(8))
        doc = aim.loads(_cut_third_line(_invalid_id(doc.dumps(), "term")))
        assert doc.proposals[0].anchor_after == "TERM"
        doc.baseline("repair", discard_history=True, at=ts(9))
        # the chained add is rebound onto 'TERM' when its parent is
        # rejected, so it is rejected next; no actor given: an external one
        assert [e.kind for e in doc.history] == ["baseline", "resolution", "resolution"]
        assert {e.get("decided_by", {}).get("type") for e in doc.history[1:]} == {"external"}
        assert [p.id for p in doc.proposals] == [kept.id]
        assert aim.lint_text(doc.dumps()) == [] and doc.verify() == []
        doc.accept_all(decided_by=ADA, at=ts(10))
        assert [c.text for c in doc.chunks][-2:] == ["Kept.", "Term: 12 months."]

    def test_a_pending_add_into_a_container_whose_invalid_id_is_replaced_is_rejected(self):
        doc = _contract()
        doc.add_chunk('<ul data-aim-container="box"><li data-aim="i1">x</li></ul>', author=ADA)
        doc.propose_add('<li data-aim="i2">y</li>', container="box", after="i1", author=BOT)
        doc = aim.loads(_cut_third_line(_invalid_id(doc.dumps(), "box")))
        card = doc.proposals[1]
        assert card.anchor_container == "BOX"
        doc.baseline("repair", discard_history=True, at=ts(9))
        assert [e.get("proposal") for e in doc.history[1:]] == [card.id]
        assert [p.target for p in doc.proposals] == ["term"]
        assert aim.lint_text(doc.dumps()) == [] and doc.verify() == []

    def test_an_anchor_naming_a_live_card_is_kept_when_a_unit_had_that_id(self):
        # A body unit hand-respelled to a pending card's id (p- is reserved,
        # so the id is invalid) is renamed; a card chained onto that pending
        # card still names the card, not the unit, and stays.
        doc = _contract()
        doc.reject(doc.proposals[0].id, decided_by=ADA, at=ts(5))
        first = doc.propose_add('<p data-aim="n1">One.</p>', after="title", author=BOT, at=ts(6))
        doc.propose_add('<p data-aim="n2">Two.</p>', after=first.id, author=BOT, at=ts(7))
        text = doc.dumps().replace('<p data-aim="fee">', f'<p data-aim="{first.id}">', 1)
        doc = aim.loads(_cut_third_line(text))
        doc.baseline("repair", discard_history=True, at=ts(9))
        assert first.id not in doc.body_ids
        assert [e.kind for e in doc.history] == ["baseline"] and len(doc.proposals) == 2
        assert aim.lint_text(doc.dumps()) == [] and doc.verify() == []
        doc.accept_all(decided_by=ADA, at=ts(10))
        assert [c.text for c in doc.chunks][:3] == ["Contract", "One.", "Two."]

    def test_fresh_ids_avoid_every_id_the_pending_lane_uses(self, monkeypatch):
        pools: list[set[str]] = []
        real = ids.new_id

        def spy(taken, **kwargs):
            pools.append(set(taken))
            return real(taken, **kwargs)

        doc = aim.loads(_loose(_damaged()))
        (card,) = doc.proposals
        monkeypatch.setattr(ids, "new_id", spy)
        doc.baseline("repair", discard_history=True, at=ts(9))
        assert pools and all({card.id, "term"} <= pool for pool in pools)

    def test_an_H008_body_is_refused_and_nothing_changes(self):
        doc = _contract()
        doc.add_chunk('<p data-aim="bad" class="bogus">Not fine.</p>', author=ADA, at=ts(5))
        text = _loose(_cut_third_line(doc.dumps()))
        doc = aim.loads(text)
        before = doc.dumps()
        with pytest.raises(aim.InvalidOperation, match="V005"):
            doc.baseline("repair", discard_history=True, at=ts(9))
        assert doc.dumps() == before  # no id assigned, no history dropped
        assert doc.history_text() == _block(text)

    def test_without_the_flag_an_unreadable_log_points_at_the_repair(self):
        doc = aim.loads(_damaged())
        with pytest.raises(aim.HistoryError, match="discard_history"):
            doc.baseline("repair")

    def test_the_id_refusal_names_the_repair_when_reconcile_refuses_the_log(self):
        # A readable but pruned 0.6 log (a checkpoint first, above seq 1)
        # and a hand-added unit without an id. reconcile() refuses the log
        # and points at baseline(), so baseline() and flatten() must not
        # point back at reconcile(): the one call that gets out is the repair
        doc = _contract()
        doc.checkpoint("sent", at=ts(5))
        doc.add_chunk('<p data-aim="late">Late.</p>', author=ADA, at=ts(6))
        doc.prune(before="sent")
        text = _loose(doc.dumps())
        with pytest.raises(aim.HistoryError, match="pruned"):
            aim.loads(text).reconcile(at=ts(9))
        for refused in (
            lambda d: d.baseline("accept", at=ts(9)),
            lambda d: d.flatten(at=ts(9)),
        ):
            doc = aim.loads(text)
            with pytest.raises(aim.InvalidOperation, match="usable id") as caught:
                refused(doc)
            message = str(caught.value)
            assert "discard_history=True" in message and "--discard-history" in message
            assert "reconcile() assigns them" not in message
            assert doc.dumps() == aim.loads(text).dumps()  # refused before any change
        doc = aim.loads(text)
        doc.baseline("accept", discard_history=True, at=ts(9))
        assert aim.lint_text(doc.dumps()) == [] and doc.verify() == []

    def test_the_id_refusal_names_reconcile_when_reconcile_can_assign_the_ids(self):
        text = _loose(_contract().dumps())
        with pytest.raises(aim.InvalidOperation, match=r"reconcile\(\) assigns them") as caught:
            aim.loads(text).baseline("accept", at=ts(9))
        assert "discard" not in str(caught.value)
        doc = aim.loads(text)
        doc.reconcile(at=ts(9))
        doc.baseline("accept", at=ts(10))
        assert aim.lint_text(doc.dumps()) == []


# ===========================================================================
def _mixed(block: str) -> str:
    parts = block.split("\n")
    seps = itertools.cycle(["\r\n", "\r", "\n"])
    return parts[0] + "".join(next(seps) + part for part in parts[1:])


NEWLINES = {
    "lf": lambda block: block,
    "crlf": lambda block: block.replace("\n", "\r\n"),
    "cr": lambda block: block.replace("\n", "\r"),
    "mixed": _mixed,
}


class TestCli:
    def _write(self, path, text: str) -> bytes:
        data = text.encode("utf-8")
        path.write_bytes(data)
        return data

    @pytest.mark.parametrize("newline", list(NEWLINES))
    def test_the_side_file_holds_the_dropped_text_byte_for_byte(self, tmp_path, capsys, newline):
        text = _damaged()
        content = NEWLINES[newline](_block(text))
        path = tmp_path / "contract.aim"
        self._write(path, _with_block(text, content))
        assert main(["baseline", str(path), "--discard-history", "--label", "repair"]) == 0
        side = tmp_path / f"contract.aim{SIDE}"
        assert side.read_bytes() == content.encode("utf-8")
        out = capsys.readouterr().out
        assert str(path) in out and str(side) in out
        doc = aim.load(path)
        assert [e.get("label") for e in doc.history] == ["repair"]
        assert doc.verify() == [] and not [f for f in aim.lint_path(path) if f.level == "error"]

    def test_a_crlf_document_is_repaired_as_aim_load_reads_it(self, tmp_path):
        # The side file keeps the input's bytes; the document is parsed as
        # load() reads it, or a CR in body text would land in the snapshot and
        # stop matching the body the next load() translates.
        text = _damaged().replace("\n", "\r\n")
        path = tmp_path / "contract.aim"
        self._write(path, text)
        assert main(["baseline", str(path), "--discard-history"]) == 0
        assert (tmp_path / f"contract.aim{SIDE}").read_bytes() == _block(text).encode()
        assert b"\r" not in path.read_bytes()
        assert aim.load(path).verify() == []
        assert not [f for f in aim.lint_path(path) if f.level == "error"]

    def test_the_side_file_is_never_overwritten(self, tmp_path):
        path = tmp_path / "c.aim"
        self._write(path, _damaged())
        taken = tmp_path / f"c.aim{SIDE}"
        taken.write_bytes(b"an earlier repair's lines")
        assert main(["baseline", str(path), "--discard-history"]) == 0
        assert taken.read_bytes() == b"an earlier repair's lines"
        first = tmp_path / "c.aim.discarded-history.1.jsonl"
        assert first.read_bytes() == _block(_damaged()).encode()
        dropped_now = aim.load(path).history_text().encode()
        assert main(["baseline", str(path), "--discard-history"]) == 0
        assert first.read_bytes() == _block(_damaged()).encode()
        assert (tmp_path / "c.aim.discarded-history.2.jsonl").read_bytes() == dropped_now

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    @pytest.mark.parametrize("mode", [0o600, 0o644])
    def test_the_side_file_is_owner_only_and_the_document_keeps_its_mode(self, tmp_path, mode):
        path = tmp_path / "c.aim"
        self._write(path, _damaged())
        os.chmod(path, mode)
        old = os.umask(0o022)
        try:
            assert main(["baseline", str(path), "--discard-history"]) == 0
        finally:
            os.umask(old)
        assert stat.S_IMODE((tmp_path / f"c.aim{SIDE}").stat().st_mode) == 0o600
        assert stat.S_IMODE(path.stat().st_mode) == mode

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_output_elsewhere_gets_the_side_file_and_a_plain_new_file(self, tmp_path):
        src = tmp_path / "in.aim"
        original = self._write(src, _damaged())
        (tmp_path / "out").mkdir()
        out = tmp_path / "out" / "fixed.aim"
        old = os.umask(0o022)
        try:
            assert main(["baseline", str(src), "--discard-history", "-o", str(out)]) == 0
        finally:
            os.umask(old)
        assert src.read_bytes() == original
        assert (tmp_path / "out" / f"fixed.aim{SIDE}").exists()
        assert stat.S_IMODE(out.stat().st_mode) == 0o644  # as a plain write makes it
        assert aim.load(out).verify() == []

    @pytest.mark.skipif(os.name == "nt", reason="symlinks")
    def test_a_symlinked_document_stays_a_symlink(self, tmp_path):
        real = tmp_path / "real.aim"
        self._write(real, _damaged())
        link = tmp_path / "link.aim"
        link.symlink_to(real)
        assert main(["baseline", str(link), "--discard-history"]) == 0
        assert link.is_symlink() and aim.load(real).verify() == []

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_a_read_only_document_is_refused_before_anything_is_written(self, tmp_path):
        path = tmp_path / "c.aim"
        original = self._write(path, _damaged())
        os.chmod(path, 0o444)
        if os.access(path, os.W_OK):
            pytest.skip("running with privileges that ignore permission bits")
        assert main(["baseline", str(path), "--discard-history"]) == 2
        assert path.read_bytes() == original
        assert sorted(p.name for p in tmp_path.iterdir()) == ["c.aim"]

    def test_it_names_the_pending_proposals_the_repair_rejected(self, tmp_path, capsys):
        path = tmp_path / "c.aim"
        self._write(path, _invalid_id(_damaged(), "term"))
        (card,) = aim.loads(path.read_text()).proposals
        assert main(["baseline", str(path), "--discard-history"]) == 0
        out = capsys.readouterr().out
        assert f"rejected 1 pending proposal that named a replaced id: {card.id}" in out
        assert aim.load(path).proposals == []
        assert aim.lint_path(path) == []

    def test_no_side_file_when_nothing_is_dropped(self, tmp_path, capsys):
        doc = _contract()
        doc._drop_history(drop_embeddings=False)
        path = tmp_path / "c.aim"
        self._write(path, doc.dumps())
        assert main(["baseline", str(path), "--discard-history"]) == 0
        assert sorted(p.name for p in tmp_path.iterdir()) == ["c.aim"]
        assert SIDE not in capsys.readouterr().out
        assert aim.load(path).verify() == []

    def test_a_refused_repair_writes_nothing(self, tmp_path, capsys):
        doc = _contract()
        doc.add_chunk('<p data-aim="bad" class="bogus">Not fine.</p>', author=ADA, at=ts(5))
        path = tmp_path / "c.aim"
        original = self._write(path, _cut_third_line(doc.dumps()))
        assert main(["baseline", str(path), "--discard-history"]) == 1
        assert "V005" in capsys.readouterr().err
        assert path.read_bytes() == original
        assert sorted(p.name for p in tmp_path.iterdir()) == ["c.aim"]

    def test_without_the_flag_the_cli_points_at_it(self, tmp_path, capsys):
        path = tmp_path / "c.aim"
        original = self._write(path, _damaged())
        assert main(["baseline", str(path)]) == 1
        assert "--discard-history" in capsys.readouterr().err
        assert path.read_bytes() == original

    def test_a_failure_at_any_step_leaves_the_document_as_it_was(
        self, tmp_path, monkeypatch, capsys
    ):
        """Every filesystem call the repair makes fails once, in turn: up to
        and including the replace, the document stays byte-identical, no
        temporary file is left, and a side file is either complete or gone."""
        path = tmp_path / "c.aim"
        argv = ["baseline", str(path), "--discard-history"]
        damaged = _damaged()  # one text: each build mints a new proposal id
        block = _block(damaged).encode()
        side = tmp_path / f"c.aim{SIDE}"

        def instrument(m, fail_at=None) -> list[str]:
            seen: list[str] = []
            for name in ("open", "write", "fsync", "fchmod", "replace"):
                real = getattr(os, name, None)
                if real is None:
                    continue

                def wrapper(*args, _real=real, _name=name, **kwargs):
                    seen.append(_name)
                    if len(seen) - 1 == fail_at:
                        raise OSError(errno.EIO, f"injected {_name} failure")
                    return _real(*args, **kwargs)

                m.setattr(os, name, wrapper)
            return seen

        original = self._write(path, damaged)
        with monkeypatch.context() as m:
            calls = instrument(m)
            assert main(argv) == 0
        assert {"open", "write", "fsync", "replace"} <= set(calls)
        capsys.readouterr()
        replaced = calls.index("replace")
        temp_opened = [i for i, name in enumerate(calls) if name == "open"][2]

        for fail_at, name in enumerate(calls):
            for leftover in tmp_path.iterdir():
                leftover.unlink()
            self._write(path, damaged)
            with monkeypatch.context() as m:
                instrument(m, fail_at)
                code = main(argv)
            err = capsys.readouterr().err
            where = f"failure in call {fail_at} ({name})"
            assert code == 2 and "injected" in err, where
            names = sorted(p.name for p in tmp_path.iterdir())
            assert all(n == "c.aim" or n.startswith(f"c.aim{SIDE[:-6]}") for n in names), where
            if fail_at <= replaced:
                assert path.read_bytes() == original, where
                assert "unchanged" in err, where
            else:  # the directory sync after the replace
                assert aim.load(path).verify() == [], where
            if fail_at < temp_opened:
                assert not side.exists(), where
            else:
                assert side.read_bytes() == block, where
