"""Content-sized thermal receipts, with one Code 128 representation for HTML and PDF."""
from io import BytesIO
from decimal import Decimal
from pathlib import Path
from functools import lru_cache
import re
from xml.sax.saxutils import escape
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, Spacer


def barcode(number):
    # Code 128 cannot represent Unicode. Never silently drop characters from an identity.
    if not number.isascii():
        return None
    return createBarcodeDrawing('Code128', value=number, barHeight=14 * mm, barWidth=0.25 * mm, humanReadable=False)


def barcode_svg(number):
    drawing = barcode(number)
    return drawing.asString('svg') if drawing else ''


@lru_cache(maxsize=1)
def receipt_fonts():
    pdfmetrics.registerFont(TTFont('ReceiptSans', str(Path(__file__).parent / 'fonts' / 'DejaVuSans.ttf')))
    pdfmetrics.registerFontFamily('ReceiptSans', normal='ReceiptSans', bold='ReceiptSans', italic='ReceiptSans', boldItalic='ReceiptSans')
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))


def pdf(receipt):
    receipt_fonts()
    width, margin = receipt['width'] * mm, 3 * mm
    available = width - 2 * margin
    style = ParagraphStyle('receipt', fontName='ReceiptSans', fontSize=9, leading=12, splitLongWords=True)
    rows = []
    def line(text, bold=False):
        safe = escape(str(text)).replace('\n', '<br/>')
        safe = re.sub(r'[\u2e80-\u9fff\uf900-\ufaff]+', lambda m: '<font name="STSong-Light">' + m.group() + '</font>', safe)
        rows.append(Paragraph('<b>' + safe + '</b>' if bold else safe, style))
    line(receipt['company_name'], True)
    for address in receipt['address']:
        line(address)
    if receipt['phone']: line(receipt['phone'])
    if receipt['reprint']: line('Reprint', True)
    if receipt['status'] == 'voided': line('VOID', True)
    sale = receipt['sale']
    line(sale['created_at'])
    line(sale['pos_register']['name'] + ' · ' + receipt['clerk'])
    line('Receipt ' + receipt['number'])
    if receipt['customer']: line(receipt['customer'])
    rows.append(Spacer(1, 6))
    for item in receipt['lines']:
        line(item['name'])
        line(f"{item['quantity'] or ''} × {item['price'] or ''}     {item['amount']}")
        rows.append(Spacer(1, 4))
    line('Subtotal ' + receipt['subtotal'])
    if Decimal(receipt['discounts']): line('Discounts ' + receipt['discounts'])
    for tax in receipt['taxes']: line(f"{tax['name']} {tax['rate']}%   {tax['amount']}")
    line('TOTAL ' + receipt['total'] + ' ' + sale['total']['currency'], True)
    for tender in sale['tenders']:
        line(tender['payment_method']['name'] + ' ' + tender['amount']['amount'])
    line('Change due ' + sale['change']['amount'])
    if receipt['message']: line(receipt['message'])
    sizes = [flow.wrap(available, 1000000) for flow in rows]
    drawing = barcode(receipt['number'])
    scale = min(1, available / drawing.width) if drawing else 1
    barheight = drawing.height * scale if drawing else 0
    height = 2 * margin + sum(h for w, h in sizes) + barheight + 10
    target = BytesIO()
    canvas = Canvas(target, pagesize=(width, height))
    canvas.setTitle('Receipt ' + receipt['number'])
    y = height - margin
    for flow, (_, h) in zip(rows, sizes):
        y -= h
        flow.drawOn(canvas, margin, y)
    if drawing:
        canvas.saveState()
        canvas.translate(margin + (available - drawing.width * scale) / 2, margin)
        canvas.scale(scale, scale)
        drawing.drawOn(canvas, 0, 0)
        canvas.restoreState()
    canvas.showPage()
    canvas.save()
    return target.getvalue()
