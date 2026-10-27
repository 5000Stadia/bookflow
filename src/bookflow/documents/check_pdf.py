"""How a cheque goes onto preprinted check stock: fixed positions, an amount in words, alignment.

A sales form flows (``pdf.py``); a cheque cannot. Preprinted stock already carries the bank, the
account and routing numbers, the cheque number and the signature line, so what prints is only
the payment: the date, the payee, the amount in figures and in words, the address that shows
through the envelope window and the memo -- each at a fixed place on the paper. This module is
that place, as numbers a test can read back, and the one function that draws them.

**Styles.** The anchor's three: ``voucher`` is one cheque at the top of a letter sheet with two
identical stubs below it listing what the cheque paid; ``standard`` is three cheques to a sheet,
each the width of the page; ``wallet`` is three to a sheet with a stub at the left of each.

**Alignment** is the anchor's Fine Alignment: the whole form moves by a horizontal and a
vertical offset in hundredths of an inch. Positive horizontal moves right; positive vertical
moves up the page. Both are applied to every field, the same way, so a sample that prints
0.05 inch low is corrected by ``offset_y = 5``.

**A partial first sheet** (standard and wallet only) holds the cheques left on a sheet a previous
run did not use. The sheet goes into the feeder top edge first, the anchor's Portrait option, so
those cheques take the first slots from the top of the first page and every later page is full.

Nothing here reads the books. ``PrintedCheck`` holds the strings, already decided; ``layout``
says where each one goes; ``to_pdf`` draws them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas as pdfcanvas

from bookflow.documents.pdf import BODY, BOLD, INK, MUTED

PAGE_WIDTH, PAGE_HEIGHT = LETTER
INCH = 72.0
SLOT_HEIGHT = 3.5          # one standard or wallet cheque, in inches
SLOTS_PER_PAGE = 3
WALLET_STUB = 2.25         # the tear-off stub at the left of a wallet cheque, in inches
VOUCHER_STUB_TOPS = (3.67, 7.33)   # where each voucher stub starts, inches from the top of the sheet
STYLES = ('voucher', 'standard', 'wallet')

# Where each field sits on one cheque, in inches from the cheque's left edge and from its top.
# A standard and a voucher cheque share these; a wallet cheque is the same face, narrower,
# starting after its stub.
FACE = {
    'date': (6.55, 0.62),
    'payee': (0.95, 1.18),
    'amount': (6.85, 1.18),
    'amount_words': (0.45, 1.55),
    'address': (0.95, 1.95),
    'memo': (0.65, 2.95),
}
WALLET_FACE = {
    'date': (4.75, 0.62),
    'payee': (0.70, 1.18),
    'amount': (5.10, 1.18),
    'amount_words': (0.25, 1.55),
    'address': (0.70, 1.95),
    'memo': (0.45, 2.95),
}
WALLET_STUB_FIELDS = {'stub_number': (0.15, 0.45), 'stub_date': (0.15, 0.75), 'stub_payee': (0.15, 1.05),
                      'stub_amount': (0.15, 1.35), 'stub_memo': (0.15, 1.65)}


# ---------------------------------------------------------------- the amount in words

_ONES = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
         'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen',
         'nineteen')
_TENS = ('', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety')
_SCALES = ((10 ** 15, 'quadrillion'), (10 ** 12, 'trillion'), (10 ** 9, 'billion'),
           (10 ** 6, 'million'), (10 ** 3, 'thousand'))


def _below_thousand(value: int) -> str:
    words = []
    hundreds, rest = divmod(value, 100)
    if hundreds:
        words.append(f'{_ONES[hundreds]} hundred')
    if rest >= 20:
        tens, ones = divmod(rest, 10)
        words.append(_TENS[tens] + (f'-{_ONES[ones]}' if ones else ''))
    elif rest:
        words.append(_ONES[rest])
    return ' '.join(words)


def number_in_words(value: int) -> str:
    """A whole number written out the way a cheque writes dollars: ``one thousand two hundred thirty-four``."""
    if value < 0:
        raise ValueError('a cheque is never written for a negative amount')
    if value == 0:
        return 'zero'
    words = []
    for scale, name in _SCALES:
        count, value = divmod(value, scale)
        if count:
            words.append(f'{number_in_words(count)} {name}')
    if value:
        words.append(_below_thousand(value))
    return ' '.join(words)


def amount_in_words(minor_units: int, currency: str = 'USD') -> str:
    """The legal line of a cheque: whole units in words and the cents as a fraction of 100.

    Read from integer minor units, so nothing passes through a float: 0.05 is
    ``Zero and 05/100``, 1,000,000.00 is ``One million and 00/100``. A currency without cents
    is written without the fraction.
    """
    if isinstance(minor_units, bool) or not isinstance(minor_units, int):
        raise TypeError('an amount in words is read from integer minor units')
    from bookflow.core.money import minor_units_of as minor_unit_exponent
    exponent = minor_unit_exponent(currency)
    if exponent == 0:
        text = number_in_words(minor_units)
        return text[0].upper() + text[1:]
    units, cents = divmod(minor_units, 10 ** exponent)
    text = number_in_words(units)
    return f'{text[0].upper()}{text[1:]} and {cents:0{exponent}d}/{10 ** exponent}'


def amount_in_figures(minor_units: int, currency: str = 'USD') -> str:
    """The figure box: grouped digits, padded on the left so nothing can be written in front."""
    from bookflow.core.money import minor_units_of as minor_unit_exponent
    exponent = minor_unit_exponent(currency)
    units, cents = divmod(minor_units, 10 ** exponent) if exponent else (minor_units, 0)
    shown = f'{units:,}' + (f'.{cents:0{exponent}d}' if exponent else '')
    return '**' + shown


# ---------------------------------------------------------------- what is printed


@dataclass(frozen=True)
class StubLine:
    """One row of a voucher stub: a bill paid, or an expense or item line of a check."""
    cells: tuple[str, ...]


@dataclass(frozen=True)
class PrintedCheck:
    number: str
    date: str
    payee: str
    amount_minor_units: int
    currency: str
    memo: str = ''
    address: tuple[str, ...] = ()
    account_name: str = ''
    stub_heading: tuple[str, ...] = ()
    stub_lines: tuple[StubLine, ...] = ()
    stub_total: str = ''
    sample: bool = False

    @property
    def words(self) -> str:
        return amount_in_words(self.amount_minor_units, self.currency)

    @property
    def figures(self) -> str:
        return amount_in_figures(self.amount_minor_units, self.currency)


@dataclass(frozen=True)
class Alignment:
    offset_x_hundredths: int = 0
    offset_y_hundredths: int = 0


@dataclass(frozen=True)
class Placed:
    """One cheque's slot on one page."""
    page: int
    slot: int
    check: PrintedCheck


@dataclass
class Layout:
    style: str
    placed: list[Placed] = field(default_factory=list)

    @property
    def pages(self) -> int:
        return (self.placed[-1].page + 1) if self.placed else 0


def arrange(checks, style: str, checks_on_first_page: int = 3) -> Layout:
    """Which page and slot each cheque prints in, honouring a partial first sheet."""
    if style not in STYLES:
        raise ValueError(f'unknown check style {style!r}')
    layout = Layout(style)
    if style == 'voucher':
        layout.placed = [Placed(index, 0, check) for index, check in enumerate(checks)]
        return layout
    if not 1 <= checks_on_first_page <= SLOTS_PER_PAGE:
        raise ValueError('a sheet holds one to three cheques')
    page, slot, capacity = 0, 0, checks_on_first_page
    for check in checks:
        if slot == capacity:
            page, slot, capacity = page + 1, 0, SLOTS_PER_PAGE
        layout.placed.append(Placed(page, slot, check))
        slot += 1
    return layout


def positions(style: str, slot: int, alignment: Alignment = Alignment()) -> dict[str, tuple[float, float]]:
    """Every field's baseline, in PDF points from the bottom-left of the page.

    The one place a coordinate is computed, so what a test reads back is what was drawn.
    """
    dx = alignment.offset_x_hundredths * INCH / 100
    dy = alignment.offset_y_hundredths * INCH / 100
    top = 0.0 if style == 'voucher' else slot * SLOT_HEIGHT
    found = {}
    if style == 'wallet':
        face = {name: (WALLET_STUB + x, y) for name, (x, y) in WALLET_FACE.items()}
        face.update(WALLET_STUB_FIELDS)
    else:
        face = dict(FACE)
    for name, (x, y) in face.items():
        found[name] = (round(x * INCH + dx, 2), round(PAGE_HEIGHT - (top + y) * INCH + dy, 2))
    return found


def _stub(c, check: PrintedCheck, top_inches: float, alignment: Alignment):
    """One voucher stub: who, when, which cheque, then what it paid, then the total."""
    dx = alignment.offset_x_hundredths * INCH / 100
    dy = alignment.offset_y_hundredths * INCH / 100
    x0 = 0.5 * INCH + dx
    y = PAGE_HEIGHT - (top_inches + 0.35) * INCH + dy
    c.setFont(BOLD, 9)
    c.setFillColor(INK)
    c.drawString(x0, y, check.payee)
    c.drawRightString(PAGE_WIDTH - 0.5 * INCH + dx, y, f'{check.date}    No. {check.number}')
    y -= 0.25 * INCH
    if check.stub_heading:
        c.setFont(BOLD, 8)
        c.setFillColor(MUTED)
        _row(c, check.stub_heading, x0, y)
        y -= 0.2 * INCH
    c.setFont(BODY, 8.5)
    c.setFillColor(INK)
    shown = check.stub_lines[:10]
    for line in shown:
        _row(c, line.cells, x0, y)
        y -= 0.18 * INCH
    if len(check.stub_lines) > len(shown):
        c.drawString(x0, y, f'... and {len(check.stub_lines) - len(shown)} more')
        y -= 0.18 * INCH
    c.setFont(BOLD, 9)
    y -= 0.08 * INCH
    if check.account_name:
        c.drawString(x0, y, check.account_name)
    c.drawRightString(PAGE_WIDTH - 0.5 * INCH + dx, y, f'Total {check.stub_total or check.figures.lstrip("*")}')


def _row(c, cells, x0, y):
    """Stub columns: the first two left-aligned, every later one right-aligned at fixed stops."""
    stops = (0.0, 1.25, 4.6, 5.65, 6.7, 7.5)
    for index, cell in enumerate(cells[:6]):
        if index < 2:
            c.drawString(x0 + stops[index] * INCH, y, str(cell)[:60])
        else:
            c.drawRightString(x0 + stops[index] * INCH, y, str(cell))


def _face(c, check: PrintedCheck, style: str, slot: int, alignment: Alignment):
    spot = positions(style, slot, alignment)
    c.setFillColor(INK)
    c.setFont(BODY, 10)
    c.drawString(*spot['date'], check.date)
    c.setFont(BOLD, 10)
    c.drawString(*spot['payee'], check.payee)
    c.drawString(*spot['amount'], check.figures)
    c.setFont(BODY, 10)
    c.drawString(*spot['amount_words'], (check.words + ' ').ljust(86, '*'))
    x, y = spot['address']
    for index, line in enumerate((check.payee, *check.address)[:5]):
        c.drawString(x, y - index * 12, line)
    if check.memo:
        c.drawString(*spot['memo'], check.memo[:60])
    if style == 'wallet':
        c.setFont(BODY, 8)
        c.drawString(*spot['stub_number'], f'No. {check.number}')
        c.drawString(*spot['stub_date'], check.date)
        c.drawString(*spot['stub_payee'], check.payee[:28])
        c.drawString(*spot['stub_amount'], check.figures)
        if check.memo:
            c.drawString(*spot['stub_memo'], check.memo[:28])
    if check.sample:
        c.setFont(BOLD, 14)
        c.setFillColor(MUTED)
        x, y = spot['amount_words']
        c.drawString(x + 2.2 * INCH, y - 0.5 * INCH, 'ALIGNMENT SAMPLE - NOT A CHECK')


def to_pdf(checks, style: str, *, alignment: Alignment = Alignment(), checks_on_first_page: int = 3,
           title: str = 'Checks', compress: bool = True) -> bytes:
    """Draw the cheques of one print run, in order, onto letter sheets of the given style."""
    layout = arrange(list(checks), style, checks_on_first_page)
    buffer = BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=LETTER, pageCompression=1 if compress else 0,
                          invariant=1)  # the same run prints the same bytes: no clock or random id inside
    c.setTitle(title)
    c.setAuthor('Bookflow')
    page = 0
    for placed in layout.placed:
        while placed.page > page:
            c.showPage()
            page += 1
        _face(c, placed.check, style, placed.slot, alignment)
        if style == 'voucher':
            for top in VOUCHER_STUB_TOPS:
                _stub(c, placed.check, top, alignment)
    c.showPage()
    c.save()
    return buffer.getvalue()
