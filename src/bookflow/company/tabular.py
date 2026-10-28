"""The shared reader of tabular files for imports (R107, design/bulk-import.md §3).

Decoding, delimiters, money, dates and booleans, once. ``statement_files.py`` and ``cutover_sources.py`` keep their own
readers (their behaviour is relied on); ``tests/test_tabular.py`` feeds all three the same money and date strings and
asserts they agree.

- Text: strict UTF-8; a UTF-8 or UTF-16 byte order mark honoured and stripped; otherwise Windows-1252, reported as
  guessed. A byte that decodes in neither refuses the file; nothing becomes ``?``.
- Delimiter: comma, semicolon, tab or pipe, sniffed on the first 50 lines. Pasted cells from a spreadsheet are tab text.
- Money: currency symbols, spaces, thousands separators, ``(1,234.56)`` and ``1234.56-``; more decimals than the
  currency has is ``E_AMOUNT_PRECISION``, never rounded. ``1.234`` with no other clue in its column is ambiguous.
- Dates: ISO, ``YYYYMMDD``, slashed and dotted forms; the day and month order of a column is read from every value.
  Two-digit years up to the current year's are this century, later ones the last. Bare 5-digit spreadsheet serials
  are refused.
- Strings stay strings: leading zeros are kept and no cell is evaluated.
- Control characters inside cells (a NUL) are kept for the import to drop from text fields and report by position, or to
  refuse in numbers and references (Kabe 2026-10-27); a file that is not text at all (mostly control codes, a PDF, an
  image, an old .xls) is refused. An .xlsx workbook is read by ``xlsx.py``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date

from bookflow import BookflowError

DELIMITERS = (',', ';', '\t', '|')
DELIMITER_NAMES = {',': 'comma', ';': 'semicolon', '\t': 'tab', '|': 'pipe', 'xlsx': 'worksheet'}
SNIFF_LINES = 50
_CP1252_UNDEFINED = frozenset((0x81, 0x8D, 0x8F, 0x90, 0x9D))
TRUE_WORDS = frozenset(('yes', 'true', '1', 'y', 't', 'on'))
FALSE_WORDS = frozenset(('no', 'false', '0', 'n', 'f', 'off'))


class CellError(Exception):
    """A cell that cannot be read as its field's type: a stable ``code`` and a message a person reads."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Table:
    headers: list[str]
    rows: list[tuple[int, list[str]]]  # (file line the row starts on, cells)
    encoding: str
    encoding_guessed: bool
    delimiter: str
    sha256: str
    width_errors: dict[int, int] = field(default_factory=dict)  # row position -> cell count, where it differs
    sheet: str | None = None  # the worksheet an .xlsx file was read from


def decode(raw: bytes) -> tuple[str, str, bool]:
    """(text, encoding, guessed). A byte order mark decides; else strict UTF-8; else Windows-1252."""
    if raw.startswith(b'\xef\xbb\xbf'):
        return _strict(raw[3:], 'utf-8'), 'utf-8-sig', False
    if raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
        return _strict(raw, 'utf-16'), 'utf-16', False
    unmarked = _utf16_without_bom(raw)
    if unmarked is not None:
        return _strict(raw, unmarked), unmarked, True
    try:
        return raw.decode('utf-8'), 'utf-8', False
    except UnicodeDecodeError:
        pass
    if any(byte in _CP1252_UNDEFINED for byte in raw):
        bad = next(i for i, byte in enumerate(raw) if byte in _CP1252_UNDEFINED)
        raise BookflowError('E_VALIDATION', message=(
            f'The file is neither UTF-8 nor Windows-1252 (byte {bad + 1} decodes in neither); save it as UTF-8 CSV.'),
            details={'field': 'content', 'problem': 'encoding', 'byte_offset': bad})
    return raw.decode('cp1252'), 'cp1252', True


def _utf16_without_bom(raw: bytes) -> str | None:
    """UTF-16 saved without a byte order mark: text in Latin script has a zero byte in every other position."""
    sample = raw[:4096 - 4096 % 2]
    if len(sample) < 4 or b'\x00' not in sample:
        return None
    even, odd = sample[0::2], sample[1::2]
    if odd.count(0) >= len(odd) * 0.4 and even.count(0) <= len(even) * 0.05:
        return 'utf-16-le'
    if even.count(0) >= len(even) * 0.4 and odd.count(0) <= len(odd) * 0.05:
        return 'utf-16-be'
    return None


def _strict(raw: bytes, encoding: str) -> str:
    try:
        text = raw.decode(encoding)
    except UnicodeDecodeError as exc:
        raise BookflowError('E_VALIDATION', message=f'The file says it is {encoding} but is not; save it again.',
                            details={'field': 'content', 'problem': 'encoding', 'byte_offset': exc.start}) from None
    return text[1:] if text.startswith('﻿') else text


def digest(raw: bytes | str) -> str:
    return hashlib.sha256(raw.encode('utf-8') if isinstance(raw, str) else raw).hexdigest()


def sniff_delimiter(text: str) -> str:
    """The delimiter giving the most rows of one width above one cell, over the first lines."""
    sample = '\n'.join(text.splitlines()[:SNIFF_LINES])
    best, best_score = ',', (-1, 0)
    for delimiter in DELIMITERS:
        try:
            widths = [len(row) for row in csv.reader(io.StringIO(sample), delimiter=delimiter) if any(c.strip() for c in row)]
        except csv.Error:
            continue
        if not widths:
            continue
        common = max(set(widths), key=widths.count)
        score = (widths.count(common) if common > 1 else 0, common)
        if score > best_score:
            best, best_score = delimiter, score
    return best


def read(raw: bytes | str, *, delimiter: str | None = None, header_row: bool = True, skip_rows: int = 0,
         sheet: str | None = None) -> Table:
    """Read a whole file. Blank rows are dropped; a row of another width than the header is noted, not dropped.

    An ``.xlsx`` workbook (a zip) is read by ``xlsx.py``, values only. A control character inside a cell (a NUL) is
    kept here for the import to drop and report; a file that is not text at all is refused.
    """
    if isinstance(raw, bytes):
        if raw.startswith(ZIP_SIGNATURE):
            from bookflow.company import xlsx
            return xlsx.read(raw, sheet=sheet, header_row=header_row, skip_rows=skip_rows)
        _refuse_binary(raw)
        text, encoding, guessed = decode(raw)
        sha = digest(raw)
    else:
        text, encoding, guessed = (raw[1:] if raw.startswith('\ufeff') else raw), 'text', False
        sha = digest(raw)
    if sheet is not None:
        raise BookflowError('E_VALIDATION', message='`sheet` names a worksheet of an .xlsx file; this file is text.',
                            details={'field': 'sheet'})
    _refuse_not_text(text)
    if delimiter is None:
        delimiter = sniff_delimiter(text)
    if delimiter not in DELIMITERS:
        raise BookflowError('E_VALIDATION', message='The delimiter must be a comma, semicolon, tab or pipe.',
                            details={'field': 'delimiter'})
    reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter)
    records: list[tuple[int, list[str]]] = []
    line = 1
    try:
        for cells in reader:
            start, line = line, reader.line_num + 1
            if any(cell.strip() for cell in cells):
                records.append((start, [unguard(cell) for cell in cells]))
    except csv.Error as exc:
        raise BookflowError('E_VALIDATION', message=f'The file cannot be read as delimited text near line {line}: {exc}',
                            details={'field': 'content', 'line': line}) from None
    records = records[skip_rows:]
    if not records:
        raise BookflowError('E_VALIDATION', message='The file has no rows.', details={'field': 'content'})
    if header_row:
        headers = [clean_text(cell)[0].strip() for cell in records[0][1]]
        body = records[1:]
    else:
        width = max(len(cells) for _, cells in records)
        headers = [f'Column {column_letter(i)}' for i in range(width)]
        body = records
    if header_row:
        if not any(headers):
            raise BookflowError('E_VALIDATION', message='The header row is empty.', details={'field': 'content'})
        seen = set()
        for header in headers:
            key = normalize_header(header)
            if key and key in seen:
                raise BookflowError('E_VALIDATION', message=f'The header {header!r} appears twice.',
                                    details={'field': 'content', 'header': header})
            seen.add(key)
    widths = {position: len(cells) for position, (_, cells) in enumerate(body, start=1) if len(cells) != len(headers)}
    return Table(headers, body, encoding, guessed, delimiter, sha, widths)


ZIP_SIGNATURE = b'PK\x03\x04'
_BINARY = ((b'\xd0\xcf\x11\xe0', 'an Excel 97-2003 (.xls) or other Office binary file; save it as .xlsx or CSV'),
           (b'%PDF', 'a PDF'), (b'\x89PNG', 'an image'), (b'\xff\xd8\xff', 'an image'), (b'GIF8', 'an image'),
           (b'PK', 'an archive'))
NOT_TEXT_SHARE = 0.10  # control characters (other than tab and line breaks) above this share: not a text file
KEPT_CONTROLS = frozenset('\t\n\r')


def _refuse_binary(raw: bytes) -> None:
    for signature, what in _BINARY:
        if raw.startswith(signature):
            raise BookflowError('E_VALIDATION', message=f'The file is {what}, not a table of text; save it as CSV.',
                                details={'field': 'content', 'problem': 'not_text'})


def _refuse_not_text(text: str) -> None:
    """A file whose characters are mostly control codes is binary data read as text, not a table with a stray NUL."""
    sample = text[:65536]
    controls = sum(1 for ch in sample if ch < ' ' and ch not in KEPT_CONTROLS) + sample.count('\x7f')
    if controls > 8 and controls > len(sample) * NOT_TEXT_SHARE:
        raise BookflowError('E_VALIDATION', message=(
            f'The file is not text ({controls} of its first {len(sample)} characters are control codes); save it as '
            'UTF-8 CSV.'), details={'field': 'content', 'problem': 'not_text'})


def unholdable(ch: str, multiline: bool) -> bool:
    """A character no text field holds: control codes (NUL, escape...) and lone surrogates; tab and line breaks only
    where the field takes long text. Accents, other scripts, symbols and emoji are all holdable."""
    if ch in KEPT_CONTROLS:
        return not multiline
    code = ord(ch)
    return code < 0x20 or 0x7f <= code < 0xa0 or 0xd800 <= code < 0xe000 or code in (0xfffe, 0xffff)


def clean_text(cell: str, multiline: bool = False) -> tuple[str, list[tuple[int, str]]]:
    """The cell without the characters a text field cannot hold, and each dropped one as (1-based position, char)."""
    if all(' ' <= ch < '\x7f' for ch in cell):  # the common case, at once
        return cell, []
    kept, dropped = [], []
    for position, ch in enumerate(cell, start=1):
        if unholdable(ch, multiline):
            dropped.append((position, ch))
        else:
            kept.append(ch)
    return (''.join(kept), dropped) if dropped else (cell, [])


def char_name(ch: str) -> str:
    import unicodedata
    names = {'\x00': 'NUL', '\x07': 'BEL', '\t': 'TAB', '\n': 'LINE FEED', '\r': 'CARRIAGE RETURN', '\x1b': 'ESCAPE', '\x7f': 'DELETE'}
    return names.get(ch) or unicodedata.name(ch, f'U+{ord(ch):04X}')


def column_letter(index: int) -> str:
    letters = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def normalize_header(text: str) -> str:
    return ' '.join((text or '').replace('﻿', '').strip().casefold().split())


# ------------------------------------------------------------------ money

_NUMBER = re.compile(r'^(\d*)(?:\.(\d*))?$')


def number_format(values: list[str]) -> tuple[str, str] | None:
    """(decimal, thousands) read from a whole column, or None when ``1.234``-style values leave it open.

    A comma after the last point, or a point before a comma, settles it; two separators of one kind are thousands; a
    separator followed by other than three digits is a decimal mark.
    """
    decided: tuple[str, str] | None = None
    open_question = False
    for raw in values:
        text = _bare(raw)
        if not text or not re.fullmatch(r'[0-9.,]+', text):
            continue  # junk is the check's to refuse; it says nothing about the column's format
        points, commas = text.count('.'), text.count(',')
        guess = None
        if points and commas:
            guess = ('.', ',') if text.rfind('.') > text.rfind(',') else (',', '.')
        elif points > 1:
            guess = (',', '.')
        elif commas > 1:
            guess = ('.', ',')
        elif points == 1:
            guess = ('.', ',') if len(text.split('.')[1]) != 3 else None
        elif commas == 1:
            guess = (',', '.') if len(text.split(',')[1]) != 3 else None
        if guess is None:
            open_question = open_question or bool(points or commas)
            continue
        if decided is None:
            decided = guess
    if decided is None and open_question:
        return None
    return decided or ('.', ',')


def _bare(text: str) -> str:
    value = (text or '').strip()
    for strip in ('$', '€', '£', '¥', ' ', ' ', ' ', "'"):
        value = value.replace(strip, '')
    value = re.sub(r'^[A-Z]{3}', '', value)
    value = re.sub(r'[A-Z]{3}$', '', value)
    return value.strip('()+-')


def parse_money(text: str, places: int = 2, *, decimal: str = '.', thousands: str = ',') -> int | None:
    """Exact minor units; blank is None. ``(1,234.56)``, ``1234.56-``, ``-$5``, ``1 234,56`` with ``decimal=','``."""
    value = (text or '').strip()
    for strip in ('$', '€', '£', '¥', ' ', ' ', ' '):
        value = value.replace(strip, '')
    value = re.sub(r'^[A-Za-z]{3}|[A-Za-z]{3}$', '', value)
    if not value:
        return None
    negative = False
    if value.startswith('(') and value.endswith(')'):
        negative, value = True, value[1:-1]
    if value.endswith('-'):
        negative, value = not negative, value[:-1]
    if value.startswith('-'):
        negative, value = not negative, value[1:]
    elif value.startswith('+'):
        value = value[1:]
    if thousands:
        value = value.replace(thousands, '')
    if decimal != '.':
        if '.' in value:
            raise CellError('bad_value', f'not an amount: {text!r}')
        value = value.replace(decimal, '.')
    found = _NUMBER.match(value)
    if not found or not (found.group(1) or found.group(2)):
        raise CellError('bad_value', f'not an amount: {text!r}')
    whole, fraction = found.group(1) or '0', found.group(2) or ''
    if len(fraction) > places:
        if fraction[places:].strip('0'):
            raise CellError('E_AMOUNT_PRECISION', f'{text!r} has more than {places} decimal places')
        fraction = fraction[:places]
    minor = int(whole) * 10 ** places + int(fraction.ljust(places, '0') or '0')
    return -minor if negative else minor


def money_text(minor: int, places: int = 2) -> str:
    sign = '-' if minor < 0 else ''
    whole, part = divmod(abs(minor), 10 ** places)
    return f'{sign}{whole}.{part:0{places}d}' if places else f'{sign}{whole}'


def parse_decimal(text: str, *, decimal: str = '.', thousands: str = ',') -> str | None:
    """A plain decimal number as canonical text (quantities, percents), never rounded; blank is None."""
    value = (text or '').strip().replace('%', '').replace(' ', '')
    if not value:
        return None
    negative = value.startswith('-')
    value = value.lstrip('+-')
    if thousands:
        value = value.replace(thousands, '')
    if decimal != '.':
        value = value.replace(decimal, '.')
    if not _NUMBER.match(value) or value in ('', '.'):
        raise CellError('bad_value', f'not a number: {text!r}')
    return ('-' if negative else '') + value


# ------------------------------------------------------------------ dates

_DATE_PARTS = re.compile(r'^(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})$')


def date_order(values: list[str]) -> str | None:
    """``MDY`` or ``DMY`` for a slashed or dotted column, ``YMD`` for ISO; None when every value is ambiguous.

    A first part over 12 means day first; a second part over 12 means month first.
    """
    orders = set()
    ambiguous = False
    for raw in values:
        found = _DATE_PARTS.match((raw or '').strip())
        if not found:
            continue
        first, second = int(found.group(1)), int(found.group(2))
        if len(found.group(1)) == 4:
            orders.add('YMD')
        elif first > 12:
            orders.add('DMY')
        elif second > 12:
            orders.add('MDY')
        else:
            ambiguous = True
    if len(orders - {'YMD'}) > 1:
        raise CellError('bad_value', 'the column mixes day-first and month-first dates')
    slashed = orders - {'YMD'}
    if slashed:
        return slashed.pop()
    if ambiguous:
        return None
    return 'YMD' if orders else 'MDY'


def parse_date(text: str, order: str | None = 'MDY', *, current_year: int | None = None) -> str | None:
    """ISO date text; blank is None. ``order`` is the column's day and month order (``MDY``, ``DMY``, ``YMD``)."""
    value = (text or '').strip()
    if not value:
        return None
    current_year = current_year or date.today().year
    try:
        if re.fullmatch(r'\d{5}', value):
            raise CellError('bad_value', f'{value!r} looks like a spreadsheet date serial; export dates as text')
        if re.fullmatch(r'\d{8}', value):
            return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
        found = _DATE_PARTS.match(value)
        if not found:
            raise CellError('bad_value', f'not a date: {text!r}')
        a, b, c = found.group(1), found.group(2), found.group(3)
        if len(a) == 4:
            year, month, day = int(a), int(b), int(c)
        else:
            if order is None:
                raise CellError('ambiguous_date', f'{text!r} could be day-first or month-first; set date_order')
            first, second = int(a), int(b)
            month, day = (first, second) if order == 'MDY' else (second, first)
            year = _year(c, current_year)
        return date(year, month, day).isoformat()
    except ValueError:
        raise CellError('bad_value', f'not a calendar date: {text!r}') from None


def _year(text: str, current_year: int) -> int:
    year = int(text)
    if len(text) <= 2:
        century = current_year - current_year % 100
        year += century if year <= current_year % 100 else century - 100
    return year


def parse_bool(text: str) -> bool | None:
    value = (text or '').strip().casefold()
    if not value:
        return None
    if value in TRUE_WORDS:
        return True
    if value in FALSE_WORDS:
        return False
    raise CellError('bad_value', f'not yes or no: {text!r}')


FORMULA = ('=', '+', '-', '@', '\t', '\r')
# Digits and the marks numbers, phones and dates are written with: nothing a spreadsheet could run.
NUMERIC_LIKE = re.compile(r'[-+]?[0-9(][0-9 ().,/:+-]*')


def spreadsheet_safe(cell: str) -> str:
    """A cell written to a file a spreadsheet opens: text starting ``=``, ``+``, ``-`` or ``@`` gets an apostrophe,
    as the report CSV guards it; a number, phone or date (``-5``, ``+1 415 555 0100``) is left as written."""
    text = '' if cell is None else str(cell)
    return "'" + text if text[:1] in FORMULA and not NUMERIC_LIKE.fullmatch(text) else text


def unguard(cell: str) -> str:
    """The reader's half of ``spreadsheet_safe``: a refused-rows file imports again with its own values."""
    return cell[1:] if cell[:1] == "'" and cell[1:2] in FORMULA and cell[1:2] != '' else cell


def write_csv(headers: list[str], rows: list[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\r\n')
    writer.writerow([spreadsheet_safe(h) for h in headers])
    for row in rows:
        writer.writerow([spreadsheet_safe(cell) for cell in row])
    return out.getvalue()
