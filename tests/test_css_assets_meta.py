"""aim.css generation, asset pack/gc, and the cache layer (meta/embeddings)."""
import base64
import gzip
import hashlib

import pytest

import aimformat as aim
from aimformat.css import css_stats, generate_aim_css

from conftest import BOT, ME, ts

# 1x1 red pixel PNG
PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4"
           "z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
DATA_URI = "data:image/png;base64," + PNG_B64


class TestCss:
    def test_deterministic(self):
        assert generate_aim_css() == generate_aim_css()

    def test_within_size_budget(self):
        s = css_stats()
        assert s["raw_bytes"] < 40 * 1024, "spec budget: ~20-40 KB embedded"
        assert s["gzip_bytes"] < 10 * 1024

    def test_every_theme_slot_has_default(self):
        css = generate_aim_css()
        for slot in aim.REGISTRY.theme_slots:
            assert slot + ":" in css

    def test_every_registry_class_present(self):
        css = generate_aim_css()
        for name in list(aim.REGISTRY.allowed_classes)[:50]:
            assert f".{name}{{" in css

    def test_chrome_present(self):
        css = generate_aim_css()
        assert "aim-proposal::" in css and "aim-slide{" in css
        assert "zoom:var(--aim-slide-scale" in css

    def test_new_document_embeds_css(self):
        text = aim.new_document(title="T").dumps()
        assert '<style data-aim-css="0.1">' in text
        assert "aim-proposals::before" in text


class TestAssets:
    def make_doc(self):
        doc = aim.new_document(title="T")
        doc.add_chunk(f'<figure data-aim="fig"><img alt="dot" src="{DATA_URI}">'
                      "<figcaption>A dot.</figcaption></figure>",
                      author=BOT, at=ts(0))
        return doc

    def test_pack_hoists_data_uri(self):
        doc = self.make_doc()
        n = doc.pack_assets(author=aim.external("packer"), at=ts(1))
        assert n == 1
        html = doc.chunk("fig").html
        assert "<use href=\"#asset-" in html and "data:image" not in html
        assert "<aim-assets>" in doc.dumps()

    def test_pack_is_a_recorded_modify(self):
        doc = self.make_doc()
        doc.pack_assets(author=aim.external("packer"), at=ts(1))
        ev = doc.history[-1]
        assert ev.action == "modify" and ev.target == "fig"
        assert doc.verify() == []

    def test_asset_id_is_content_addressed(self):
        doc = self.make_doc()
        doc.pack_assets(author=aim.external("packer"), at=ts(1))
        blob = base64.b64decode(PNG_B64)
        expect = "asset-" + hashlib.sha256(blob).hexdigest()[:12]
        assert expect in doc.chunk("fig").html

    def test_pack_dedupes_identical_images(self):
        doc = self.make_doc()
        doc.add_chunk(f'<figure data-aim="fig2"><img alt="same" '
                      f'src="{DATA_URI}"></figure>', author=BOT, at=ts(1))
        doc.pack_assets(author=aim.external("packer"), at=ts(2))
        assert doc.dumps().count("<symbol id=\"asset-") == 1

    def test_gc_respects_history_liveness(self):
        doc = self.make_doc()
        doc.pack_assets(author=aim.external("packer"), at=ts(1))
        doc.delete_chunk("fig", author=ME, at=ts(2))
        # the delete event's before-payload still references the asset
        assert doc.gc_assets() == 0
        doc.flatten()  # drop history -> the asset is now dead
        assert doc.gc_assets() == 1
        assert "<aim-assets>" not in doc.dumps()

    def test_packed_doc_lints_clean(self):
        doc = self.make_doc()
        doc.pack_assets(author=aim.external("packer"), at=ts(1))
        assert not [f for f in aim.lint_text(doc.dumps())
                    if f.level == "error"]


class TestCaches:
    def test_set_summary_stamps_freshness(self, basic_doc):
        basic_doc.set_summary("Two chunks about nothing.", model="m-1")
        meta = basic_doc.meta
        assert meta["summary"]["as_of_seq"] == basic_doc.seq
        assert meta["summary"]["doc_hash"] == basic_doc.doc_hash

    def test_generate_toc_groups_under_headings(self, rich_doc):
        toc = rich_doc.generate_toc()
        first = toc[0]
        assert first["title"] == "Report" and first["level"] == 1
        assert {"intro", "scope", "list", "tbl"} <= set(first["chunks"])
        slide_entry = next(t for t in toc if "s1" in t["chunks"])
        assert slide_entry["title"] == "Deck"

    def test_embeddings_roundtrip_and_staleness(self, basic_doc):
        basic_doc.set_embedding("intro", model="m", vec=[0.25, -0.5])
        assert basic_doc.stale_embeddings() == []
        basic_doc.modify_chunk("intro", '<p data-aim="intro">Changed.</p>',
                               author=ME, at=ts(5))
        assert [e["chunk"] for e in basic_doc.stale_embeddings()] == ["intro"]
        basic_doc.set_embedding("intro", model="m", vec=[0.3, -0.4])
        assert basic_doc.stale_embeddings() == []
        assert len(basic_doc.embeddings) == 1  # replaced, not appended

    def test_multiple_models_per_chunk(self, basic_doc):
        basic_doc.set_embedding("intro", model="m1", vec=[0.1])
        basic_doc.set_embedding("intro", model="m2", vec=[0.2])
        assert len(basic_doc.embeddings) == 2

    def test_meta_lives_in_head_and_orders_before_css(self, basic_doc):
        basic_doc.set_summary("s", model="m")
        text = basic_doc.dumps()
        assert text.index("aim-meta+json") < text.index("data-aim-css")
