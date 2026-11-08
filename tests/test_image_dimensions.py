"""The size readers behind packed images (spec §9.1; plan 7b).

``AimDocument._image_dimensions`` reads an image's intrinsic size from its
header, standard library only, for every format an importer packs: PNG,
JPEG, GIF, BMP, WebP and TIFF. The packed reference's ``viewBox`` and the
asset symbol's grid come from it. Anything it cannot read (another format,
a truncated or malformed header, a zero size) is ``None``, never an
exception, so a hostile image stays inline instead of breaking an import.

Each reader is checked on real files written with Pillow (skipped when
Pillow is absent) and on hand-built headers that reach what Pillow does not
write: BITMAPCOREHEADER and top-down BMPs, the high bits of the WebP size
fields, big-endian TIFF with SHORT sizes.
"""

from __future__ import annotations

import base64
import io
import random
import struct
import zlib

import pytest

import aimformat as aim

size_of = aim.AimDocument._image_dimensions


# -- hand-built headers ---------------------------------------------------------


def _png(width: int, height: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def _bmp_core(width: int, height: int) -> bytes:
    """A BMP with a BITMAPCOREHEADER: 12 bytes, uint16 sizes."""
    info = struct.pack("<IHHHH", 12, width, height, 1, 24)
    return b"BM" + struct.pack("<IHHI", 26, 0, 0, 26) + info


def _bmp_info(width: int, height: int, header: int = 40) -> bytes:
    """A BMP with a BITMAPINFOHEADER or a later one (*header* bytes): int32
    sizes, a negative height meaning top-down rows."""
    info = struct.pack("<Iii", header, width, height) + b"\x00" * (header - 12)
    return b"BM" + struct.pack("<IHHI", 14 + header, 0, 0, 14 + header) + info


def _riff(fourcc: bytes, payload: bytes) -> bytes:
    chunk = fourcc + struct.pack("<I", len(payload)) + payload + b"\x00" * (len(payload) % 2)
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def _vp8(width: int, height: int, scale: int = 0) -> bytes:
    """Lossy WebP: a key frame's tag, the start code, then 14-bit sizes whose
    two top bits are an upscaling hint, not part of the size."""
    sizes = struct.pack("<HH", width | scale << 14, height | scale << 14)
    return _riff(b"VP8 ", b"\x10\x02\x00" + b"\x9d\x01\x2a" + sizes + b"\x00" * 8)


def _vp8l(width: int, height: int, version: int = 0) -> bytes:
    """Lossless WebP: signature 0x2F, then width-1 and height-1 in 14 bits."""
    bits = (width - 1) | (height - 1) << 14 | version << 29
    return _riff(b"VP8L", b"\x2f" + struct.pack("<I", bits) + b"\x00" * 4)


def _vp8x(width: int, height: int) -> bytes:
    """Extended WebP: the canvas width-1 and height-1 in 24 bits."""
    canvas = (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return _riff(b"VP8X", b"\x10\x00\x00\x00" + canvas)


SHORT, LONG = 3, 4


def _tiff(
    order: str,
    entries: list[tuple[int, int, int]],
    *,
    count: int = 1,
    gap: int = 0,
    declared: int | None = None,
) -> bytes:
    """A TIFF whose first IFD holds *entries* (tag, type, value), after
    *gap* bytes of filler; *declared* overrides the IFD's entry count."""
    o = "<" if order == "II" else ">"
    magic = b"II*\x00" if order == "II" else b"MM\x00*"
    ifd = struct.pack(o + "H", len(entries) if declared is None else declared)
    for tag, typ, value in entries:
        # a SHORT value sits in the field's first two bytes in both orders
        field = struct.pack(o + "HH", value, 0) if typ == SHORT else struct.pack(o + "I", value)
        ifd += struct.pack(o + "HHI", tag, typ, count) + field
    ifd += struct.pack(o + "I", 0)  # no next IFD
    return magic + struct.pack(o + "I", 8 + gap) + b"\x00" * gap + ifd


def _tiff_sized(order: str, typ: int, width: int, height: int, **kw) -> bytes:
    entries = [(254, LONG, 0), (256, typ, width), (257, typ, height), (258, SHORT, 8)]
    return _tiff(order, entries, **kw)


HAND_BUILT = {
    "png": (_png(3, 2), (3, 2)),
    "bmp-core": (_bmp_core(300, 7), (300, 7)),
    "bmp-info": (_bmp_info(70000, 3), (70000, 3)),
    "bmp-info-top-down": (_bmp_info(300, -7), (300, 7)),
    "bmp-v2": (_bmp_info(5, 4, header=52), (5, 4)),
    "bmp-v3": (_bmp_info(5, 4, header=56), (5, 4)),
    "bmp-os2": (_bmp_info(5, 4, header=64), (5, 4)),
    "bmp-v4": (_bmp_info(5, 4, header=108), (5, 4)),
    "bmp-v5": (_bmp_info(5, 4, header=124), (5, 4)),
    "webp-vp8": (_vp8(300, 7), (300, 7)),
    "webp-vp8-max": (_vp8(16383, 16383), (16383, 16383)),
    "webp-vp8-scaled": (_vp8(300, 7, scale=3), (300, 7)),
    "webp-vp8l": (_vp8l(300, 7), (300, 7)),
    "webp-vp8l-max": (_vp8l(16384, 16384), (16384, 16384)),
    "webp-vp8x": (_vp8x(70000, 3), (70000, 3)),
    "webp-vp8x-max": (_vp8x(1 << 24, 1), (1 << 24, 1)),
    "tiff-ii-short": (_tiff_sized("II", SHORT, 300, 7), (300, 7)),
    "tiff-ii-long": (_tiff_sized("II", LONG, 70000, 7), (70000, 7)),
    "tiff-mm-short": (_tiff_sized("MM", SHORT, 300, 7), (300, 7)),
    "tiff-mm-long": (_tiff_sized("MM", LONG, 70000, 7), (70000, 7)),
    "tiff-mixed-types": (_tiff("MM", [(256, LONG, 9), (257, SHORT, 4)]), (9, 4)),
    "tiff-ifd-after-data": (_tiff_sized("II", SHORT, 300, 7, gap=40), (300, 7)),
    "tiff-height-first": (_tiff("II", [(257, SHORT, 4), (256, SHORT, 9)]), (9, 4)),
}


class TestHandBuiltHeaders:
    @pytest.mark.parametrize("name", sorted(HAND_BUILT))
    def test_the_size_is_read(self, name):
        blob, size = HAND_BUILT[name]
        assert size_of(blob) == size


# -- real files written with Pillow ---------------------------------------------

#: (format, mode, save options, (start, stop, bytes) that prove the variant)
PILLOW = {
    "bmp-rgb": ("BMP", "RGB", {}, (14, 18, struct.pack("<I", 40))),
    "bmp-rgba": ("BMP", "RGBA", {}, (0, 2, b"BM")),
    "bmp-1bit": ("BMP", "1", {}, (0, 2, b"BM")),
    "bmp-palette": ("BMP", "P", {}, (0, 2, b"BM")),
    "webp-lossy": ("WEBP", "RGB", {}, (12, 16, b"VP8 ")),
    "webp-lossless": ("WEBP", "RGB", {"lossless": True}, (12, 16, b"VP8L")),
    "webp-alpha": ("WEBP", "RGBA", {}, (12, 16, b"VP8X")),
    "webp-animated": ("WEBP", "RGB", {"save_all": True}, (12, 16, b"VP8X")),
    "tiff-little-endian": ("TIFF", "RGB", {}, (0, 4, b"II*\x00")),
    "tiff-lzw": ("TIFF", "RGB", {"compression": "tiff_lzw"}, (0, 4, b"II*\x00")),
    "tiff-big-endian": ("TIFF", "I;16B", {}, (0, 4, b"MM\x00*")),
}
PILLOW_SIZE = (301, 7)


def _pillow(name: str) -> bytes:
    image = pytest.importorskip("PIL.Image")
    fmt, mode, options, (start, stop, magic) = PILLOW[name]
    options = dict(options)
    if options.get("save_all"):
        options["append_images"] = [image.new(mode, PILLOW_SIZE, (9, 9, 9))]
    buf = io.BytesIO()
    try:
        image.new(mode, PILLOW_SIZE).save(buf, fmt, **options)
    except (KeyError, OSError, ValueError):  # pragma: no cover - a Pillow built without it
        pytest.skip(f"Pillow cannot write {name}")
    blob = buf.getvalue()
    assert blob[start:stop] == magic, f"{name} did not come out as the variant it tests"
    return blob


class TestPillowFiles:
    @pytest.mark.parametrize("name", sorted(PILLOW))
    def test_the_size_is_read(self, name):
        assert size_of(_pillow(name)) == PILLOW_SIZE

    def test_jpeg_and_gif_still_read(self):
        image = pytest.importorskip("PIL.Image")
        for fmt in ("JPEG", "GIF", "PNG"):
            buf = io.BytesIO()
            image.new("RGB", PILLOW_SIZE).save(buf, fmt)
            assert size_of(buf.getvalue()) == PILLOW_SIZE, fmt


# -- malformed, truncated and hostile input -------------------------------------

MALFORMED = {
    "empty": b"",
    "not-an-image": b"not an image",
    "svg": b'<svg xmlns="http://www.w3.org/2000/svg" width="3" height="2"/>',
    "bmp-zero-width": _bmp_info(0, 7),
    "bmp-zero-height": _bmp_info(7, 0),
    "bmp-negative-width": _bmp_info(-300, 7),
    "bmp-core-zero": _bmp_core(0, 7),
    "bmp-unknown-header": _bmp_info(5, 4, header=20),
    "bmp-huge-header": b"BM" + b"\x00" * 12 + b"\xff\xff\xff\xff" + struct.pack("<ii", 5, 4),
    "riff-not-webp": _vp8(300, 7).replace(b"WEBP", b"WAVE", 1),
    "webp-alph-first": _riff(b"ALPH", b"\x00" * 10),
    "webp-vp8-no-start-code": _vp8(300, 7).replace(b"\x9d\x01\x2a", b"\x00\x00\x00", 1),
    "webp-vp8-interframe": _vp8(300, 7).replace(b"\x10\x02\x00", b"\x11\x02\x00", 1),
    "webp-vp8-zero": _vp8(0, 7),
    "webp-vp8-short-chunk": _vp8(300, 7)[:16] + struct.pack("<I", 4) + _vp8(300, 7)[20:],
    "webp-vp8l-bad-signature": _vp8l(300, 7).replace(b"\x2f", b"\x2e", 1),
    "webp-vp8l-version-1": _vp8l(300, 7, version=1),
    "webp-vp8x-short-chunk": _vp8x(5, 4)[:16] + struct.pack("<I", 6) + _vp8x(5, 4)[20:],
    "tiff-no-width": _tiff("II", [(257, SHORT, 7)]),
    "tiff-no-height": _tiff("MM", [(256, SHORT, 7)]),
    "tiff-zero": _tiff_sized("II", LONG, 0, 7),
    "tiff-rational-size": _tiff("II", [(256, 5, 9), (257, SHORT, 4)]),
    "tiff-byte-size": _tiff("MM", [(256, 1, 9), (257, SHORT, 4)]),
    "tiff-count-2": _tiff_sized("II", SHORT, 300, 7, count=2),
    "tiff-count-0": _tiff_sized("II", SHORT, 300, 7, count=0),
    "tiff-no-entries": _tiff("II", []),
    "tiff-offset-past-end": b"II*\x00" + struct.pack("<I", 4096) + b"\x00" * 32,
    "tiff-offset-max": b"MM\x00*" + b"\xff\xff\xff\xff" + b"\x00" * 32,
    "tiff-offset-into-header": b"II*\x00" + struct.pack("<I", 2) + b"\x00" * 32,
    "tiff-wrong-magic": b"II+\x00" + _tiff_sized("II", SHORT, 300, 7)[4:],
    "tiff-mixed-order-magic": b"IM*\x00" + _tiff_sized("II", SHORT, 300, 7)[4:],
}


class TestMalformed:
    @pytest.mark.parametrize("name", sorted(MALFORMED))
    def test_none(self, name):
        assert size_of(MALFORMED[name]) is None

    def test_a_lying_entry_count_reads_only_the_entries_that_fit(self):
        # the IFD claims 65,535 entries; only the three written are read
        blob = _tiff_sized("II", SHORT, 300, 7, declared=0xFFFF)
        assert size_of(blob) == (300, 7)
        cut = _tiff("MM", [(254, LONG, 0), (258, SHORT, 8)], declared=0xFFFF)
        assert size_of(cut) is None

    def test_the_first_occurrence_of_a_tag_counts(self):
        blob = _tiff("II", [(256, SHORT, 9), (256, SHORT, 1), (257, SHORT, 4)])
        assert size_of(blob) == (9, 4)


#: the classic 1×1 GIF
GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def _samples() -> list:
    out = [pytest.param(blob, size, id=name) for name, (blob, size) in sorted(HAND_BUILT.items())]
    return [*out, pytest.param(GIF, (1, 1), id="gif")]


def _assert_prefixes(blob: bytes, size: tuple[int, int]) -> None:
    """Every prefix of *blob* is ``None`` until the header is whole, then the
    full size: a cut never raises and never yields a wrong size."""
    results = [size_of(blob[:n]) for n in range(len(blob) + 1)]
    first = results.index(size)
    assert set(results[:first]) == {None}
    assert set(results[first:]) == {size}


class TestTruncated:
    @pytest.mark.parametrize(("blob", "size"), _samples())
    def test_every_prefix_of_a_hand_built_header(self, blob, size):
        _assert_prefixes(blob, size)

    @pytest.mark.parametrize("name", sorted(PILLOW))
    def test_every_prefix_of_a_pillow_file(self, name):
        _assert_prefixes(_pillow(name), PILLOW_SIZE)


class TestHostileBytes:
    @pytest.mark.parametrize("name", sorted(HAND_BUILT))
    def test_corrupted_headers_never_raise(self, name):
        blob, _ = HAND_BUILT[name]
        rng = random.Random(name)  # deterministic per sample
        for _ in range(300):
            damaged = bytearray(blob)
            for _ in range(rng.randint(1, 4)):
                damaged[rng.randrange(min(len(damaged), 64))] = rng.randrange(256)
            got = size_of(bytes(damaged))
            assert got is None or (len(got) == 2 and all(isinstance(v, int) and v > 0 for v in got))

    def test_random_bytes_behind_each_signature_never_raise(self):
        rng = random.Random(7)
        signatures = [b"BM", b"RIFF", b"II*\x00", b"MM\x00*", b"\x89PNG\r\n\x1a\n", b"GIF89a"]
        for sig in signatures:
            for _ in range(300):
                tail = bytes(rng.randrange(256) for _ in range(rng.randrange(40)))
                blob = sig + tail
                if sig == b"RIFF" and len(blob) >= 12:
                    blob = blob[:8] + b"WEBP" + blob[12:]
                got = size_of(blob)
                assert got is None or all(isinstance(v, int) and v > 0 for v in got)
