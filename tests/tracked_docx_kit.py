"""Build Word documents with tracked changes from DECLARED outcomes.

Every shape here states, next to the revision markup it writes, what Word's
*Reject All* and *Accept All* leave behind — as the chunk texts an importer
must produce. Those declarations are the goldens: they are the generator's
inputs, written by hand from ECMA-376 Part 1 §17.13.5, and independent of
the importer's own view resolver. ``scripts/gen_docx_tracked.py`` writes the
same shapes as ``.docx`` files for the one-time manual confirmation in Word
or LibreOffice (steps in ``tests/fixtures/docx-tracked/README.md``).

Shared by ``tests/test_docx_tracked.py`` and the script; python-docx only.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = (
    f'xmlns:w="{W_NS}" '
    'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
    'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)

ALICE = ("Alice Smith", "2026-09-01T10:00:00Z")
BOB = ("Bob Jones", "2026-09-02T11:30:00Z")
AGENT = ("agent:model-x", "2026-09-03T08:15:00Z")
NO_DATE = ("Carol White", None)


class Ids:
    """Revision ids, unique per document."""

    def __init__(self) -> None:
        self.n = 100

    def __call__(self) -> int:
        self.n += 1
        return self.n


def _who(by: tuple[str, str | None], ids: Ids) -> str:
    author, date = by
    out = f'w:id="{ids()}" w:author={quoteattr(author)}'
    if date:
        out += f' w:date="{date}"'
    return out


def run(text: str, rpr: str = "") -> str:
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def del_run(text: str, rpr: str = "") -> str:
    return f'<w:r>{rpr}<w:delText xml:space="preserve">{escape(text)}</w:delText></w:r>'


class Doc:
    """One document under construction (revision ids stay unique)."""

    def __init__(self) -> None:
        self.ids = Ids()
        self.body: list[str] = []
        self.comments: list[tuple[str, str, str | None, str, str | None, bool]] = []
        self.numbering: str | None = None

    # -- inline -----------------------------------------------------------------
    def t(self, text: str, rpr: str = "") -> str:
        return run(text, rpr)

    def ins(self, text: str, by=ALICE, rpr: str = "") -> str:
        return f"<w:ins {_who(by, self.ids)}>{run(text, rpr)}</w:ins>"

    def dele(self, text: str, by=ALICE, rpr: str = "") -> str:
        return f"<w:del {_who(by, self.ids)}>{del_run(text, rpr)}</w:del>"

    def ins_del(self, text: str, by_ins=ALICE, by_del=BOB) -> str:
        """Text one author inserted and another deleted: gone in both views."""
        inner = f"<w:ins {_who(by_ins, self.ids)}>{del_run(text)}</w:ins>"
        return f"<w:del {_who(by_del, self.ids)}>{inner}</w:del>"

    def bold_change(self, text: str, by=BOB) -> str:
        return run(
            text, f"<w:rPr><w:b/><w:rPrChange {_who(by, self.ids)}><w:rPr/></w:rPrChange></w:rPr>"
        )

    def hyperlink(self, rid: str, *parts: str) -> str:
        return f'<w:hyperlink r:id="{rid}">{"".join(parts)}</w:hyperlink>'

    def comment(
        self,
        cid: str,
        anchored: str,
        text: str,
        by=NO_DATE,
        *,
        parent: str | None = None,
        resolved: bool = False,
    ) -> str:
        """Anchored markup wrapped in a comment range (registers the comment)."""
        self.comments.append((cid, by[0], by[1], text, parent, resolved))
        return (
            f'<w:commentRangeStart w:id="{cid}"/>{anchored}<w:commentRangeEnd w:id="{cid}"/>'
            f'<w:r><w:commentReference w:id="{cid}"/></w:r>'
        )

    def reply(self, cid: str, text: str, parent: str, by=BOB) -> str:
        """A reply rides its parent's range: a reference mark only."""
        self.comments.append((cid, by[0], by[1], text, parent, False))
        return f'<w:r><w:commentReference w:id="{cid}"/></w:r>'

    # -- blocks -----------------------------------------------------------------
    def p(
        self,
        *parts: str,
        mark: tuple[str, tuple] | None = None,
        style: str | None = None,
        num: tuple[int, int] | None = None,
        ppr_change: tuple[str, tuple] | None = None,
        move: tuple[str, str] | None = None,
    ) -> str:
        """A paragraph. ``mark=("ins"|"del"|"moveFrom"|"moveTo", by)`` tracks
        the paragraph MARK; ``ppr_change=(old pPr children as XML, by)``
        records a property change (the record holds the OLD properties);
        ``move=("moveFrom"|"moveTo", name)`` wraps the content
        in a named move range."""
        ppr = ""
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if num:
            ppr += f'<w:numPr><w:ilvl w:val="{num[1]}"/><w:numId w:val="{num[0]}"/></w:numPr>'
        if mark:
            ppr += f"<w:rPr><w:{mark[0]} {_who(mark[1], self.ids)}/></w:rPr>"
        if ppr_change:
            old, by = ppr_change
            ppr += f"<w:pPrChange {_who(by, self.ids)}><w:pPr>{old}</w:pPr></w:pPrChange>"
        content = "".join(parts)
        if move:
            kind, name = move
            rid = self.ids()
            by = mark[1] if mark else ALICE
            content = (
                f'<w:{kind}RangeStart w:id="{rid}" w:name="{name}" '
                f"w:author={quoteattr(by[0])}"
                + (f' w:date="{by[1]}"' if by[1] else "")
                + "/>"
                + content
                + f'<w:{kind}RangeEnd w:id="{rid}"/>'
            )
        return f"<w:p>{'<w:pPr>' + ppr + '</w:pPr>' if ppr else ''}{content}</w:p>"

    def moved(self, kind: str, text: str, name: str, by=ALICE, *, edit: str = "") -> str:
        """A whole paragraph moved (text and mark), named range included."""
        body = f"<w:{kind} {_who(by, self.ids)}>{run(text)}</w:{kind}>" + edit
        return self.p(body, mark=(kind, by), move=(kind, name))

    def inserted_p(self, text: str, by=BOB, **kw) -> str:
        return self.p(self.ins(text, by), mark=("ins", by), **kw)

    def deleted_p(self, text: str, by=ALICE, **kw) -> str:
        return self.p(self.dele(text, by), mark=("del", by), **kw)

    def table(self, rows: list[tuple[list[str], str | None, tuple | None]]) -> str:
        """Rows of plain cell texts; a row mark is ``("ins"|"del", by)``."""
        out = ['<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/></w:tblPr>']
        cols = max(len(cells) for cells, _, _ in rows)
        out.append("<w:tblGrid>" + '<w:gridCol w:w="2000"/>' * cols + "</w:tblGrid>")
        for cells, kind, by in rows:
            trpr = f"<w:trPr><w:{kind} {_who(by, self.ids)}/></w:trPr>" if kind else ""
            tcs = []
            for text in cells:
                if kind == "ins":
                    para = self.p(self.ins(text, by), mark=("ins", by))
                elif kind == "del":
                    para = self.p(self.dele(text, by), mark=("del", by))
                else:
                    para = self.p(text if text.startswith("<w:") else run(text))
                tcs.append(f'<w:tc><w:tcPr><w:tcW w:w="2000" w:type="dxa"/></w:tcPr>{para}</w:tc>')
            out.append(f"<w:tr>{trpr}{''.join(tcs)}</w:tr>")
        out.append("</w:tbl>")
        return "".join(out)

    def textbox(self, *paragraphs: str) -> str:
        """An anchored DrawingML textbox run holding *paragraphs*."""
        return (
            '<w:r><w:drawing><wp:anchor><wp:extent cx="914400" cy="914400"/>'
            '<wp:docPr id="1" name="Box"/><a:graphic><a:graphicData '
            'uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape">'
            "<wps:wsp><wps:txbx><w:txbxContent>"
            + "".join(paragraphs)
            + "</w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic>"
            "</wp:anchor></w:drawing></w:r>"
        )

    # -- packaging --------------------------------------------------------------
    def build(self) -> bytes:
        import docx

        base = docx.Document()
        body = base.element.body
        for child in list(body):
            if not child.tag.endswith("}sectPr"):
                body.remove(child)
        buf = io.BytesIO()
        base.save(buf)
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as src:
            parts = {n: src.read(n) for n in src.namelist()}
        xml = parts["word/document.xml"].decode()
        start = xml.index("<w:body>") + len("<w:body>")
        sect = xml.index("<w:sectPr", start)
        # re-declare every prefix the shapes use on the document root
        head = xml[: xml.index(">", xml.index("<w:document")) + 1]
        head_new = head
        for decl in _NS.split(" "):
            prefix = decl.split("=")[0]
            if prefix not in head_new:
                head_new = head_new[:-1] + " " + decl + ">"
        xml = head_new + xml[len(head) : start] + "".join(self.body) + xml[sect:]
        parts["word/document.xml"] = xml.encode()
        rels = parts["word/_rels/document.xml.rels"].decode()
        types = parts["[Content_Types].xml"].decode()
        rels = rels.replace(
            "</Relationships>",
            '<Relationship Id="rIdLink1" TargetMode="External" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            'Target="https://example.com/terms"/></Relationships>',
        )
        if self.numbering is not None:
            parts["word/numbering.xml"] = (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:numbering xmlns:w="{W_NS}">{self.numbering}</w:numbering>'
            ).encode()
        if self.comments:
            parts["word/comments.xml"] = self._comments_xml().encode()
            parts["word/commentsExtended.xml"] = self._extended_xml().encode()
            ct = "application/vnd.openxmlformats-officedocument.wordprocessingml"
            types = types.replace(
                "</Types>",
                f'<Override PartName="/word/comments.xml" ContentType="{ct}.comments+xml"/>'
                f'<Override PartName="/word/commentsExtended.xml" '
                f'ContentType="{ct}.commentsExtended+xml"/></Types>',
            )
            rels = rels.replace(
                "</Relationships>",
                '<Relationship Id="rIdC1" Type="http://schemas.openxmlformats.org/'
                'officeDocument/2006/relationships/comments" Target="comments.xml"/>'
                '<Relationship Id="rIdC2" Type="http://schemas.microsoft.com/office/2011/'
                'relationships/commentsExtended" Target="commentsExtended.xml"/>'
                "</Relationships>",
            )
        parts["word/_rels/document.xml.rels"] = rels.encode()
        parts["[Content_Types].xml"] = types.encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in parts.items():
                z.writestr(name, data)
        return out.getvalue()

    def _comments_xml(self) -> str:
        items = []
        for i, (cid, author, date, text, _parent, _resolved) in enumerate(self.comments):
            d = f' w:date="{date}"' if date else ""
            items.append(
                f'<w:comment w:id="{cid}" w:author={quoteattr(author)}{d}>'
                f'<w:p w14:paraId="{0x100 + i:08X}"><w:r><w:t>{escape(text)}</w:t></w:r></w:p>'
                "</w:comment>"
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w:comments {_NS}>{''.join(items)}</w:comments>"
        )

    def _extended_xml(self) -> str:
        index = {cid: i for i, (cid, *_rest) in enumerate(self.comments)}
        items = []
        for i, (_cid, _a, _d, _t, parent, resolved) in enumerate(self.comments):
            parent_attr = (
                f' w15:paraIdParent="{0x100 + index[parent]:08X}"' if parent in index else ""
            )
            items.append(
                f'<w15:commentEx w15:paraId="{0x100 + i:08X}"{parent_attr} '
                f'w15:done="{1 if resolved else 0}"/>'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f"<w15:commentsEx {_NS}>{''.join(items)}</w15:commentsEx>"
        )


#: A numbering part with a dynamic list (numId 7: decimal "%1.") and a
#: scheme the list vocabulary cannot draw, so its labels are baked as text
#: (numId 8: upper-Roman then decimal down one chain, "I.1").
NUMBERING = (
    '<w:abstractNum w:abstractNumId="70"><w:lvl w:ilvl="0"><w:start w:val="1"/>'
    '<w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
    '<w:abstractNum w:abstractNumId="80">'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="upperRoman"/>'
    '<w:lvlText w:val="%1."/></w:lvl>'
    '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
    '<w:lvlText w:val="%1.%2"/></w:lvl></w:abstractNum>'
    '<w:num w:numId="7"><w:abstractNumId w:val="70"/></w:num>'
    '<w:num w:numId="8"><w:abstractNumId w:val="80"/></w:num>'
)


@dataclass
class Shape:
    """One tracked-change situation and its declared outcomes."""

    name: str
    docx: bytes
    reject: list[str]  # chunk texts after Word's Reject All
    accept: list[str]  # chunk texts after Word's Accept All
    #: (action, author label) per expected card, in lane order; None = unchecked
    cards: list[tuple[str, str]] | None = None
    comments: list[dict] = field(default_factory=list)
    note: str = ""


def _label(by: tuple) -> str:
    author = by[0]
    if author.startswith("agent:"):
        return f"agent:{author[6:]}"
    return f"human:{author}"


EXT = "external:docx-import"


def shapes() -> list[Shape]:
    out: list[Shape] = []

    d = Doc()
    d.body = [
        d.p(
            d.t("Deliver within "),
            d.dele("thirty", ALICE),
            d.ins("fifteen", ALICE),
            d.t(" days"),
            d.ins(" of the order date", BOB),
            d.t("."),
        )
    ]
    out.append(
        Shape(
            "inline_two_authors",
            d.build(),
            ["Deliver within thirty days."],
            ["Deliver within fifteen days of the order date."],
            [("modify", EXT)],
        )
    )

    d = Doc()
    d.body = [d.p(d.t("Payment is due.")), d.inserted_p("Late payments accrue interest.", BOB)]
    d.body.append(d.p(d.t("Fees are fixed.")))
    out.append(
        Shape(
            "inserted_paragraph",
            d.build(),
            ["Payment is due.", "Fees are fixed."],
            ["Payment is due.", "Late payments accrue interest.", "Fees are fixed."],
            [("add", _label(BOB))],
        )
    )

    d = Doc()
    d.body = [d.p(d.t("Keep me.")), d.deleted_p("Drop me.", ALICE), d.p(d.t("Keep me too."))]
    out.append(
        Shape(
            "deleted_paragraph",
            d.build(),
            ["Keep me.", "Drop me.", "Keep me too."],
            ["Keep me.", "Keep me too."],
            [("delete", _label(ALICE))],
        )
    )

    d = Doc()
    d.body = [
        d.p(d.t("Either party may terminate."), mark=("ins", BOB)),
        d.p(d.t("Accrued rights survive.")),
    ]
    out.append(
        Shape(
            "split",
            d.build(),
            ["Either party may terminate.Accrued rights survive."],
            ["Either party may terminate.", "Accrued rights survive."],
            [("add", _label(BOB)), ("modify", _label(BOB))],
        )
    )

    d = Doc()
    d.body = [
        d.p(d.t("Notices must be in writing"), mark=("del", ALICE)),
        d.p(d.t(" and signed.")),
    ]
    out.append(
        Shape(
            "merge",
            d.build(),
            ["Notices must be in writing", "and signed."],
            ["Notices must be in writing and signed."],
            [("delete", _label(ALICE)), ("modify", _label(ALICE))],
        )
    )

    d = Doc()
    d.body = [
        d.p(d.t("One.")),
        d.moved("moveFrom", "Governed by Swiss law.", "move1", ALICE),
        d.p(d.t("Two.")),
        d.moved("moveTo", "Governed by Swiss law.", "move1", ALICE),
        d.p(d.t("Three.")),
    ]
    out.append(
        Shape(
            "move",
            d.build(),
            ["One.", "Governed by Swiss law.", "Two.", "Three."],
            ["One.", "Two.", "Governed by Swiss law.", "Three."],
            [("move", _label(ALICE))],
        )
    )

    d = Doc()
    d.body = [
        d.p(d.t("One.")),
        d.p(
            f"<w:moveFrom {_who(ALICE, d.ids)}>{run('Moved text.')}</w:moveFrom>",
            mark=("moveFrom", ALICE),
        ),
        d.p(d.t("Two.")),
        d.p(
            f"<w:moveTo {_who(ALICE, d.ids)}>{run('Moved text.')}</w:moveTo>",
            mark=("moveTo", ALICE),
        ),
    ]
    out.append(
        Shape(
            "unnamed_move",
            d.build(),
            ["One.", "Moved text.", "Two."],
            ["One.", "Two.", "Moved text."],
            [("delete", _label(ALICE)), ("add", _label(ALICE))],
            note="no range name: delete + add",
        )
    )

    d = Doc()
    d.body = [
        d.moved("moveFrom", "Old clause.", "m2", ALICE),
        d.p(d.t("Anchor.")),
        d.p(
            f"<w:moveTo {_who(ALICE, d.ids)}>{run('Old clause.')}</w:moveTo>"
            + d.ins(" Amended.", BOB),
            mark=("moveTo", ALICE),
            move=("moveTo", "m2"),
        ),
    ]
    out.append(
        Shape(
            "moved_and_modified",
            d.build(),
            ["Old clause.", "Anchor."],
            ["Anchor.", "Old clause. Amended."],
            [("move", _label(ALICE)), ("modify", _label(BOB))],
        )
    )

    d = Doc()
    d.body = [d.p(d.t("All information is "), d.bold_change("confidential", BOB), d.t("."))]
    out.append(
        Shape(
            "run_formatting",
            d.build(),
            ["All information is confidential."],
            ["All information is confidential."],
            [("modify", _label(BOB))],
            note="bold: same text, different markup",
        )
    )

    d = Doc()
    d.body = [d.p(d.t("Definitions"), style="Heading2", ppr_change=("", ALICE))]
    out.append(
        Shape(
            "paragraph_style",
            d.build(),
            ["Definitions"],
            ["Definitions"],
            [("modify", _label(ALICE))],
            note="Normal → Heading 2: p → h2",
        )
    )

    d = Doc()
    d.numbering = NUMBERING
    d.body = [
        d.p(d.t("First deliverable"), num=(7, 0)),
        d.p(d.t("Second deliverable"), d.ins(" and its documentation", BOB), num=(7, 0)),
    ]
    out.append(
        Shape(
            "list_item_insertion",
            d.build(),
            ["First deliverable", "Second deliverable"],
            ["First deliverable", "Second deliverable and its documentation"],
            [("modify", _label(BOB))],
        )
    )

    d = Doc()
    d.body = [
        d.table(
            [
                (["Item", "Price"], None, None),
                (["Support", "EUR 500"], "ins", BOB),
                (["Training", "EUR 900"], "del", ALICE),
                (["Hosting", "EUR 100"], None, None),
            ]
        )
    ]
    out.append(
        Shape(
            "table_rows",
            d.build(),
            ["ItemPrice", "TrainingEUR 900", "HostingEUR 100"],
            ["ItemPrice", "SupportEUR 500", "HostingEUR 100"],
            [("add", _label(BOB)), ("delete", _label(ALICE))],
        )
    )

    d = Doc()
    d.body = [
        d.p(d.t("The fee is "), d.comment("0", run("EUR 10,000"), "Net or gross?"), d.t(".")),
    ]
    out.append(
        Shape(
            "comment",
            d.build(),
            ["The fee is EUR 10,000."],
            ["The fee is EUR 10,000."],
            [],
            comments=[{"id": "0", "author": "Carol White", "anchor_text": "EUR 10,000", "on": 0}],
        )
    )

    d = Doc()
    d.body = [d.p(d.t("Keep "), d.ins_del("never ", ALICE, BOB), d.t("this."))]
    out.append(
        Shape(
            "nested_del_ins",
            d.build(),
            ["Keep this."],
            ["Keep this."],
            [],
            note="inserted then deleted: gone in both views, no card",
        )
    )

    d = Doc()
    d.body = [
        d.p(
            d.t("See "),
            d.hyperlink("rIdLink1", d.t("the "), d.ins("full ", BOB), d.t("terms")),
            d.t("."),
        )
    ]
    out.append(
        Shape(
            "hyperlink_revision",
            d.build(),
            ["See the terms."],
            ["See the full terms."],
            [("modify", _label(BOB))],
        )
    )

    d = Doc()
    d.body = [
        d.table(
            [
                (["Item", "Price"], None, None),
                (["Support", d.t("EUR ") + d.dele("500", ALICE) + d.ins("550", ALICE)], None, None),
            ]
        )
    ]
    out.append(
        Shape(
            "cell_revision",
            d.build(),
            ["ItemPrice", "SupportEUR 500"],
            ["ItemPrice", "SupportEUR 550"],
            [("modify", _label(ALICE))],
        )
    )

    d = Doc()
    d.body = [
        d.p(
            d.t("Anchor text."),
            d.textbox(d.p(d.t("Boxed "), d.ins("new ", BOB), d.t("note."))),
        )
    ]
    out.append(
        Shape(
            "textbox_revision",
            d.build(),
            ["Anchor text.[picture: Box]", "Boxed note."],
            ["Anchor text.[picture: Box]", "Boxed new note."],
            [("modify", _label(BOB))],
        )
    )

    d = Doc()
    d.numbering = NUMBERING
    d.body = [
        d.p(d.t("Alpha"), num=(7, 0)),
        d.p(d.t("Bravo"), num=(7, 0)),
        d.inserted_p("An interjection.", BOB),
        d.p(d.t("Charlie"), num=(7, 0)),
        d.p(d.t("Delta"), num=(7, 0)),
    ]
    out.append(
        Shape(
            "list_split",
            d.build(),
            ["Alpha", "Bravo", "Charlie", "Delta"],
            ["Alpha", "Bravo", "An interjection.", "Charlie", "Delta"],
            [("delete", _label(BOB))] * 2 + [("add", _label(BOB))] * 2,
            note="the inserted paragraph splits the list in the accept view",
        )
    )

    d = Doc()
    d.numbering = NUMBERING
    old_numbering = '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/></w:numPr>'
    d.body = [
        d.p(d.t("Alpha"), num=(7, 0)),
        d.p(d.t("Bravo"), ppr_change=(old_numbering, ALICE)),
        d.p(d.t("Charlie"), num=(7, 0)),
    ]
    # the pPrChange holds the OLD properties: Bravo WAS the second list item
    # (numPr in the change record) and is plain now — Reject All re-numbers it
    out.append(
        Shape(
            "list_item_turned_plain",
            d.build(),
            ["Alpha", "Bravo", "Charlie"],
            ["Alpha", "Bravo", "Charlie"],
            [
                ("delete", _label(ALICE)),
                ("delete", _label(ALICE)),
                ("add", _label(ALICE)),
                ("add", _label(ALICE)),
            ],
            note="the list splits around the un-numbered item in the accept view",
        )
    )

    d = Doc()
    d.body = [
        d.p(d.t("Before the table.")),
        d.table(
            [
                (["Region", "Total"], "ins", BOB),
                (["North", "12"], "ins", BOB),
            ]
        ),
        d.p(d.t("After the table.")),
    ]
    out.append(
        Shape(
            "inserted_table",
            d.build(),
            ["Before the table.", "After the table."],
            ["Before the table.", "RegionTotal", "North12", "After the table."],
            [("add", _label(BOB))],
        )
    )

    d = Doc()
    d.numbering = NUMBERING
    d.body = [
        d.p(d.t("Intro.")),
        d.deleted_p("Old one", ALICE, num=(7, 0)),
        d.deleted_p("Old two", ALICE, num=(7, 0)),
        d.p(d.t("Outro.")),
    ]
    out.append(
        Shape(
            "deleted_list",
            d.build(),
            ["Intro.", "Old one", "Old two", "Outro."],
            ["Intro.", "Outro."],
            [("delete", _label(ALICE))],
        )
    )

    d = Doc()
    d.numbering = NUMBERING
    d.body = [
        d.p(d.t("Scope"), num=(8, 0)),
        d.p(d.t("First rule."), num=(8, 1)),
        d.p(d.ins("Inserted rule.", BOB), num=(8, 1), mark=("ins", BOB)),
        d.p(d.t("Second rule."), num=(8, 1)),
    ]
    out.append(
        Shape(
            "baked_numbering_insertion",
            d.build(),
            ["I.\xa0Scope", "I.1\xa0First rule.", "I.2\xa0Second rule."],
            ["I.\xa0Scope", "I.1\xa0First rule.", "I.2\xa0Inserted rule.", "I.2\xa0Second rule."],
            [("add", _label(BOB))],
            note="baked labels keep the original numbering (reported)",
        )
    )

    d = Doc()
    d.numbering = NUMBERING
    d.body = [
        d.p(d.t("Alpha"), num=(7, 0)),
        d.p(d.ins("Inserted", BOB), num=(7, 0), mark=("ins", BOB)),
        d.p(d.t("Bravo"), num=(7, 0)),
    ]
    out.append(
        Shape(
            "dynamic_numbering_insertion",
            d.build(),
            ["Alpha", "Bravo"],
            ["Alpha", "Inserted", "Bravo"],
            [("add", _label(BOB))],
        )
    )

    d = Doc()
    d.body = [d.p(d.t("Undated "), d.ins("insertion", NO_DATE), d.t("."))]
    out.append(
        Shape(
            "missing_date",
            d.build(),
            ["Undated ."],
            ["Undated insertion."],
            [("modify", _label(NO_DATE))],
        )
    )

    d = Doc()
    d.body = [
        d.p(
            d.t("Base text"),
            f"<w:ins {_who(BOB, d.ids)}>"
            + d.comment("1", run(" plus added words"), "Why add this?")
            + "</w:ins>",
            d.t("."),
        )
    ]
    out.append(
        Shape(
            "comment_on_insertion",
            d.build(),
            ["Base text."],
            ["Base text plus added words."],
            [("modify", _label(BOB))],
            comments=[{"id": "1", "anchor_text": "plus added words", "on": "card"}],
        )
    )

    d = Doc()
    anchored = d.comment("5", run("the deposit"), "Refundable?", ALICE)
    reply = d.reply("6", "Yes, within 30 days.", parent="5", by=BOB)
    d.body = [d.p(d.t("Pay "), anchored, reply, d.t(" now."))]
    out.append(
        Shape(
            "comment_reply",
            d.build(),
            ["Pay the deposit now."],
            ["Pay the deposit now."],
            [],
            comments=[
                {"id": "5", "anchor_text": "the deposit", "on": 0},
                {"id": "6", "parent_id": "5", "on": 0},
            ],
        )
    )

    d = Doc()
    d.body = [d.inserted_p("Everything is new.", BOB), d.inserted_p("All of it.", BOB)]
    out.append(
        Shape(
            "all_inserted",
            d.build(),
            [],
            ["Everything is new.", "All of it."],
            [("add", _label(BOB)), ("add", _label(BOB))],
        )
    )

    # Deleting the paragraph between two lists of one numbering joins them
    # into one list in the accept view. With an item of the second list also
    # deleted, its untouched siblings used to be dropped as "converter noise"
    # while the second list was deleted whole: accept-all lost b1 and b3.
    d = Doc()
    d.numbering = NUMBERING
    d.body = [
        d.p(d.t("Alpha."), num=(7, 0)),
        d.p(d.t("Bravo."), num=(7, 0)),
        d.deleted_p("Second part", BOB),
        d.p(d.t("Charlie."), num=(7, 0)),
        d.deleted_p("Delta.", BOB, num=(7, 0)),
        d.p(d.t("Echo."), num=(7, 0)),
    ]
    out.append(
        Shape(
            "list_join_with_deleted_item",
            d.build(),
            ["Alpha.", "Bravo.", "Second part", "Charlie.", "Delta.", "Echo."],
            ["Alpha.", "Bravo.", "Charlie.", "Echo."],
            [
                ("add", _label(BOB)),
                ("add", _label(BOB)),
                ("delete", _label(BOB)),
                ("delete", _label(BOB)),
            ],
            note="a deletion that joins two lists keeps the second list's survivors",
        )
    )

    # Two deleted headings join THREE lists; only the last list has an item
    # of its own deleted. The middle list must join too: kept in place, it
    # landed after the third list's survivors (order differed from Word).
    d = Doc()
    d.numbering = NUMBERING
    d.body = [
        d.p(d.t("One."), num=(7, 0)),
        d.deleted_p("Part two", ALICE),
        d.p(d.t("Two."), num=(7, 0)),
        d.deleted_p("Part three", ALICE),
        d.p(d.t("Three."), num=(7, 0)),
        d.deleted_p("Four.", BOB, num=(7, 0)),
        d.p(d.t("Five."), num=(7, 0)),
    ]
    out.append(
        Shape(
            "three_lists_joined",
            d.build(),
            ["One.", "Part two", "Two.", "Part three", "Three.", "Four.", "Five."],
            ["One.", "Two.", "Three.", "Five."],
            None,
            note="every list a deletion joins moves, in source order",
        )
    )
    return out


def oversized(revisions: int) -> bytes:
    """A document carrying *revisions* insertion records."""
    d = Doc()
    d.body = [d.p(*[d.ins(f"w{i} ", ALICE) for i in range(revisions)])]
    return d.build()


def hostile_paragraph(size: int) -> bytes:
    """One paragraph whose single revision carries *size* characters."""
    d = Doc()
    d.body = [d.p(d.t("Start "), d.ins("x" * size, BOB), d.dele("y" * size, ALICE))]
    return d.build()
