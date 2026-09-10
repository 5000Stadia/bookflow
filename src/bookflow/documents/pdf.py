"""How a printed document looks on paper. One module, so every document matches.

The page is laid out the way a bookkeeper expects a sales form: the company block at the
top left, the document title and number at the top right, the addressed blocks under
them, a boxed bar of the header fields, the lines in a table, the totals stacked at the
right, then the message and memo. Nothing here reads the books or formats an amount; it
draws the strings `model.PrintedDocument` already holds.

Pagination is the reason this uses flowables rather than a fixed grid. A line item is
never split across a page boundary, the line table repeats its column headings on every
continuation page, and every page carries the document's own identity and a page count,
so a stapled two-page invoice is still readable when the pages come apart.
"""
from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

from bookflow.documents.model import PrintedDocument

PAGE = LETTER
MARGIN = 0.5 * inch
BODY = "Helvetica"
BOLD = "Helvetica-Bold"
INK = colors.HexColor("#12202f")
MUTED = colors.HexColor("#4a5b6d")
RULE = colors.HexColor("#9fb0c2")
BAND = colors.HexColor("#eef3f8")

TEXT = ParagraphStyle("text", fontName=BODY, fontSize=9, leading=11.5, textColor=INK)
SMALL = ParagraphStyle("small", parent=TEXT, fontSize=8, leading=10, textColor=MUTED)
CELL = ParagraphStyle("cell", parent=TEXT, fontSize=8.5, leading=10.5)
CELL_RIGHT = ParagraphStyle("cellright", parent=CELL, alignment=2)
HEADING = ParagraphStyle("heading", parent=TEXT, fontName=BOLD, fontSize=8,
                         leading=10, textColor=MUTED)
NAME = ParagraphStyle("name", parent=TEXT, fontName=BOLD, fontSize=11, leading=13)
TITLE = ParagraphStyle("title", parent=TEXT, fontName=BOLD, fontSize=22, leading=24,
                       alignment=2)
ALERT = ParagraphStyle("alert", parent=TEXT, fontName=BOLD, fontSize=10, leading=13,
                       textColor=colors.HexColor("#8a1c1c"))

WIDTH = PAGE[0] - 2 * MARGIN


def _p(text, style=TEXT):
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def _plain(width, rows, styles, **kwargs):
    table = Table(rows, colWidths=width, **kwargs)
    table.setStyle(TableStyle(styles))
    return table


def _letterhead(document: PrintedDocument):
    """Who is sending this, and what it is — the two things read first."""
    left = [_p(document.issuer.name or "", NAME)]
    left += [_p(line, TEXT) for line in document.issuer.lines]
    left += [_p(line, SMALL) for line in document.contact]
    right = [_p(document.title.upper(), TITLE)]
    if document.number:
        right.append(_p(f"No. {document.number}", ParagraphStyle(
            "num", parent=TEXT, fontName=BOLD, fontSize=11, leading=14, alignment=2)))
    return _plain([WIDTH * 0.58, WIDTH * 0.42], [[left, right]], [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ])


def _parties(document: PrintedDocument):
    shown = [party for party in document.parties if not party.empty]
    if not shown:
        return None
    cells = []
    for party in shown:
        block = [_p(party.heading.upper(), HEADING)]
        if party.name:
            block.append(_p(party.name, ParagraphStyle("who", parent=TEXT, fontName=BOLD)))
        block += [_p(line, TEXT) for line in party.lines]
        cells.append(block)
    width = WIDTH / max(len(cells), 2)
    # A single addressed block is half the page wide and belongs at the left margin,
    # not centred in the frame, which is what a table does when left to itself.
    return _plain([width] * len(cells), [cells], [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ], hAlign="LEFT")


def _facts(document: PrintedDocument):
    """The boxed bar of header fields — date, terms, due date and the rest."""
    if not document.facts:
        return None
    labels = [_p(label.upper(), HEADING) for label, _ in document.facts]
    values = [_p(value, CELL) for _, value in document.facts]
    width = WIDTH / len(document.facts)
    return _plain([width] * len(document.facts), [labels, values], [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])


def _lines(document: PrintedDocument):
    """The line table. `repeatRows` reprints the headings on every continuation page and
    reportlab splits between rows only, so no line item is ever cut in half."""
    columns = document.columns
    total_weight = sum(column.weight for column in columns) or 1
    widths = [WIDTH * column.weight / total_weight for column in columns]
    header = [_p(column.label.upper(), HEADING) for column in columns]
    body = []
    for row in document.rows:
        cells = []
        for column, value in zip(columns, row):
            if column.wrap:
                cells.append(_p(value, CELL))
            else:
                cells.append(_p(value, CELL_RIGHT if column.align == "right" else CELL))
        body.append(cells)
    if not body:
        body = [[_p("No lines on this document.", SMALL)] + [""] * (len(columns) - 1)]
    styles = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#d5dfe9")),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    table = Table([header] + body, colWidths=widths, repeatRows=1, splitByRow=1)
    table.setStyle(TableStyle(styles))
    return table


def _totals(document: PrintedDocument):
    if not document.totals:
        return None
    rows, styles = [], [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for index, total in enumerate(document.totals):
        style = ParagraphStyle("t", parent=TEXT, fontName=BOLD if total.emphasis else BODY,
                               fontSize=10.5 if total.emphasis else 9)
        rows.append([_p(total.label, style), _p(total.value, ParagraphStyle(
            "tv", parent=style, alignment=2))])
        if total.emphasis:
            styles += [("LINEABOVE", (0, index), (-1, index), 0.6, RULE),
                       ("BACKGROUND", (0, index), (-1, index), BAND)]
    block = _plain([WIDTH * 0.22, WIDTH * 0.13], rows, styles)
    return _plain([WIDTH * 0.65, WIDTH * 0.35], [["", block]], [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ])


def _grid(grid):
    labels = [_p(label.upper(), HEADING) for label, _ in grid.cells]
    values = [_p(value, CELL_RIGHT) for _, value in grid.cells]
    width = WIDTH / max(len(grid.cells), 1)
    table = _plain([width] * len(grid.cells), [labels, values], [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 1), (-1, 1), "RIGHT"),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])
    return KeepTogether([_p(grid.heading, HEADING), Spacer(1, 2), table])


def story(document: PrintedDocument) -> list:
    """The document as an ordered list of things to place on the page."""
    flow = [_letterhead(document), Spacer(1, 10)]
    for alert in document.alerts:
        flow += [_p(alert, ALERT), Spacer(1, 4)]
    if document.subject:
        flow += [_p(document.subject, ParagraphStyle("subject", parent=TEXT, fontName=BOLD,
                                                     fontSize=12, leading=15)), Spacer(1, 6)]
    parties = _parties(document)
    if parties is not None:
        flow += [parties, Spacer(1, 8)]
    facts = _facts(document)
    if facts is not None:
        flow += [facts, Spacer(1, 10)]
    flow += [_lines(document), Spacer(1, 8)]
    totals = _totals(document)
    if totals is not None:
        flow += [totals, Spacer(1, 10)]
    for grid in document.grids:
        flow += [_grid(grid), Spacer(1, 10)]
    for note in document.notes:
        block = ([_p(note.heading.upper(), HEADING)] if note.heading else []) + [_p(note.body, TEXT)]
        flow += [KeepTogether(block), Spacer(1, 6)]
    return flow


class _Numbered(pdfcanvas.Canvas):
    """Two passes over the same pages, so page one can say how many pages there are."""

    footer = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        pages = self._saved
        for state in pages:
            self.__dict__.update(state)
            self._stamp(len(pages))
            super().showPage()
        super().save()

    def _stamp(self, total):
        self.setFont(BODY, 7.5)
        self.setFillColor(MUTED)
        self.drawString(MARGIN, MARGIN * 0.6, self.footer)
        count = f"Page {self._pageNumber} of {total}"
        self.drawRightString(PAGE[0] - MARGIN, MARGIN * 0.6, count)
        self.setStrokeColor(RULE)
        self.line(MARGIN, MARGIN * 0.6 + 10, PAGE[0] - MARGIN, MARGIN * 0.6 + 10)


def to_pdf(document: PrintedDocument) -> bytes:
    """Draw one described document and return the PDF bytes."""
    buffer = BytesIO()
    template = SimpleDocTemplate(
        buffer, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN + 14,
        title=document.name or document.title,
        author=document.issuer.name or "", subject=document.title,
        creator="Bookflow", lang="en",
    )
    stamped = type("_Stamped", (_Numbered,), {"footer": document.footer})
    template.build(story(document), canvasmaker=stamped)
    return buffer.getvalue()
