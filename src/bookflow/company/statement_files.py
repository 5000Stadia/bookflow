"""Bank statement files: parse CSV, OFX and QFX into exact lines, and match them to movements.

Pure: nothing here reads or writes a database. `reconcile import` hands it the file's text and
the account's reconcilable movements, and turns what comes back into draft marks.

Signs follow the bank's own view of the account, which is also OFX's: a positive amount puts
money into a bank account, and on a credit card a positive amount is a payment or credit that
lowers what is owed. A reconciliation counts a card the other way round (a charge raises the
statement balance), so the caller flips card amounts before matching.

Money is exact. Every amount becomes integer minor units of the account's currency; a figure
with more places than the currency has is refused rather than rounded.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field, replace
from datetime import date

from bookflow.core.errors import BookflowError

# How far apart a statement line and a book entry may be dated and still match outright, and how
# far for a suggestion. GnuCash's import matcher scores a date within 4 days as a match and gives
# up on one more than 14 days away; both defaults are taken from there.
MATCH_DAYS = 4
SUGGEST_DAYS = 14

FORMATS = ('ofx', 'qfx', 'csv')


def invalid(field_name, problem):
    return BookflowError('E_VALIDATION', message=problem,
                         details={'fields': [{'field': field_name, 'problem': problem}]})


@dataclass(frozen=True)
class Line:
    """One transaction as the bank printed it."""
    line_id: str
    date: str
    amount: int  # minor units, bank sign (see module docstring)
    payee: str = ''
    memo: str = ''
    number: str = ''
    fitid: str | None = None
    kind: str = ''


@dataclass(frozen=True)
class Statement:
    format: str
    lines: tuple[Line, ...]
    duplicates: tuple[Line, ...] = ()
    currency: str | None = None
    statement_date: str | None = None
    ending_balance: int | None = None  # minor units, bank sign
    start_date: str | None = None
    end_date: str | None = None


# ------------------------------------------------------------------ exact amounts and dates

_AMOUNT = re.compile(r'^([+-]?)(\d*)(?:[.,](\d*))?$')


def amount(text, places, field_name='amount'):
    """A decimal string as exact minor units; refuses floats' worth of places, never rounds."""
    raw = (text or '').strip().replace('$', '').replace(' ', '')
    negative = False
    if raw.startswith('(') and raw.endswith(')'):
        negative, raw = True, raw[1:-1]
    if raw.endswith('-'):
        negative, raw = not negative, raw[:-1]
    # A thousands separator only where a decimal point also appears after it.
    if raw.count(',') and raw.count('.') == 1 and raw.rfind(',') < raw.rfind('.'):
        raw = raw.replace(',', '')
    found = _AMOUNT.match(raw)
    if not found or not (found.group(2) or found.group(3)):
        raise invalid(field_name, f'not an amount: {text!r}')
    sign, whole, fraction = found.group(1), found.group(2) or '0', found.group(3) or ''
    if len(fraction) > places:
        if fraction[places:].strip('0'):
            raise BookflowError('E_AMOUNT_PRECISION', details={
                'field': field_name, 'allowed_places': places, 'given_places': len(fraction.rstrip('0'))})
        fraction = fraction[:places]
    units = int(whole) * 10 ** places + int((fraction + '0' * places)[:places] or '0')
    if sign == '-':
        negative = not negative
    return -units if negative else units


def ofx_date(text, field_name='date'):
    value = (text or '').strip()
    if len(value) < 8 or not value[:8].isdigit():
        raise invalid(field_name, f'not an OFX date: {text!r}')
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
    except ValueError as exc:
        raise invalid(field_name, f'not a calendar date: {text!r}') from exc


DATE_FORMATS = {'YYYY-MM-DD': ('%Y', '%m', '%d'), 'MM/DD/YYYY': ('%m', '%d', '%Y'),
                'DD/MM/YYYY': ('%d', '%m', '%Y'), 'MM/DD/YY': ('%m', '%d', '%y'),
                'YYYYMMDD': None}


def csv_date(text, style, field_name='date'):
    value = (text or '').strip()
    try:
        if style == 'YYYYMMDD' or (style == 'auto' and re.fullmatch(r'\d{8}', value)):
            return ofx_date(value, field_name)
        if style == 'auto':
            if re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', value):
                style = 'YYYY-MM-DD'
            elif re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', value):
                style = 'MM/DD/YYYY'  # the anchor's own convention; name DD/MM/YYYY to override
            elif re.fullmatch(r'\d{1,2}/\d{1,2}/\d{2}', value):
                style = 'MM/DD/YY'
            else:
                raise ValueError
        parts = [int(p) for p in re.split(r'[-/.]', value)]
        order = DATE_FORMATS[style]
        found = dict(zip(order, parts))
        year = found.get('%Y') or (2000 + found['%y'] if found['%y'] < 70 else 1900 + found['%y'])
        return date(year, found['%m'], found['%d']).isoformat()
    except (ValueError, KeyError, TypeError) as exc:
        raise invalid(field_name, f'not a date in {style} form: {text!r}') from exc


def line_id(fitid, values, occurrence):
    """FITID when the bank gave one; otherwise a stable hash of what the line says.

    `occurrence` keeps two genuinely identical lines (two 4.50 coffees the same day) apart while
    still giving the same file the same ids every time it is read.
    """
    if fitid:
        return 'fitid:' + fitid
    text = '\x1f'.join(str(v) for v in (*values, occurrence))
    return 'hash:' + hashlib.sha256(text.encode()).hexdigest()[:24]


def _identified(lines):
    """Give each line its id and set aside a FITID the file already used."""
    kept, duplicates, seen, occurrences = [], [], set(), {}
    for line in lines:
        values = (line.date, line.amount, line.payee, line.memo, line.number)
        occurrences[values] = occurrences.get(values, 0) + 1
        identity = line_id(line.fitid, values, occurrences[values])
        value = replace(line, line_id=identity)
        if identity in seen:
            duplicates.append(value)
        else:
            seen.add(identity)
            kept.append(value)
    return tuple(kept), tuple(duplicates)


# ------------------------------------------------------------------ OFX and QFX

_TAG = re.compile(r'<(/?)([A-Za-z0-9.]+)\s*>([^<]*)')


def _ofx_tokens(content):
    """OFX 1.x is SGML whose leaf elements have no closing tag; OFX 2 is XML. One reader serves
    both: every element is an opening tag followed by its text, and an aggregate is closed by an
    explicit end tag in both versions."""
    start = content.upper().find('<OFX>')
    if start < 0:
        raise invalid('content', 'no <OFX> element; this is not an OFX or QFX file')
    body = re.sub(r'<\?.*?\?>|<!--.*?-->', '', content[start:], flags=re.S)
    return [(close == '/', name.upper(), text.strip()) for close, name, text in _TAG.findall(body)]


def _ofx_blocks(tokens, name):
    """Each `name` aggregate as a dict of the leaf values directly or indirectly inside it."""
    blocks, current, depth = [], None, 0
    for closing, tag, text in tokens:
        if tag == name and not closing:
            current, depth = {}, 1
            continue
        if current is None:
            continue
        if tag == name and closing:
            blocks.append(current)
            current = None
            continue
        if not closing and text:
            current.setdefault(tag, _unescape(text))
    return blocks


def _unescape(text):
    return (text.replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')
            .replace('&apos;', "'").replace('&nbsp;', ' ').replace('&amp;', '&'))


def parse_ofx(content, places, fmt='ofx'):
    tokens = _ofx_tokens(content)
    leaves = {}
    for closing, tag, text in tokens:
        if not closing and text:
            leaves.setdefault(tag, _unescape(text))
    lines = []
    for i, block in enumerate(_ofx_blocks(tokens, 'STMTTRN')):
        where = f'STMTTRN[{i}]'
        if 'TRNAMT' not in block or 'DTPOSTED' not in block:
            raise invalid('content', f'{where} has no TRNAMT or DTPOSTED')
        name = block.get('NAME', '') or block.get('PAYEE', '')
        lines.append(Line(line_id='', date=ofx_date(block['DTPOSTED'], where + '.DTPOSTED'),
                          amount=amount(block['TRNAMT'], places, where + '.TRNAMT'),
                          payee=name.strip(), memo=block.get('MEMO', '').strip(),
                          number=(block.get('CHECKNUM') or block.get('REFNUM') or '').strip(),
                          fitid=(block.get('FITID') or '').strip() or None,
                          kind=block.get('TRNTYPE', '').strip()))
    kept, duplicates = _identified(lines)
    balance = _ofx_blocks(tokens, 'LEDGERBAL')
    ending = statement_date = None
    if balance and 'BALAMT' in balance[0]:
        ending = amount(balance[0]['BALAMT'], places, 'LEDGERBAL.BALAMT')
        if 'DTASOF' in balance[0]:
            statement_date = ofx_date(balance[0]['DTASOF'], 'LEDGERBAL.DTASOF')
    listing = _ofx_blocks(tokens, 'BANKTRANLIST')
    start = ofx_date(listing[0]['DTSTART']) if listing and 'DTSTART' in listing[0] else None
    end = ofx_date(listing[0]['DTEND']) if listing and 'DTEND' in listing[0] else None
    return Statement(format=fmt, lines=kept, duplicates=duplicates, currency=leaves.get('CURDEF'),
                     statement_date=statement_date or end, ending_balance=ending,
                     start_date=start, end_date=end)


# ------------------------------------------------------------------ CSV

# Column names a bank download commonly uses, tried when no mapping names them.
HEADER_GUESSES = {
    'date': ('date', 'posted date', 'posting date', 'transaction date', 'trans date', 'post date'),
    'amount': ('amount', 'transaction amount', 'amt'),
    'debit': ('debit', 'withdrawal', 'withdrawals', 'money out', 'debit amount', 'payment'),
    'credit': ('credit', 'deposit', 'deposits', 'money in', 'credit amount'),
    'payee': ('payee', 'description', 'name', 'merchant', 'details'),
    'memo': ('memo', 'notes', 'note', 'reference'),
    'number': ('check number', 'check', 'check no', 'check #', 'check no.', 'num', 'number', 'chk'),
    'fitid': ('fitid', 'transaction id', 'id', 'reference number', 'ref'),
    'balance': ('balance', 'running balance', 'ledger balance'),
}


def guess_mapping(header):
    """The column for each field, by the names banks use, or '' where none fits."""
    lowered = {h.strip().casefold(): h for h in header}
    found = {}
    for name, guesses in HEADER_GUESSES.items():
        found[name] = next((lowered[g] for g in guesses if g in lowered), '')
    if found['payee'] and found['payee'] == found['memo']:
        found['memo'] = ''
    return found


def parse_csv(content, places, mapping):
    """`mapping` names a header for date and for amount (or debit/credit), plus optional payee,
    memo, number, fitid and balance; `date_format` and `invert` shape the values."""
    text = content.lstrip('﻿')
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in r)]
    if not rows:
        raise invalid('content', 'the CSV file is empty')
    header = [h.strip() for h in rows[0]]
    given = {k: v for k, v in (mapping or {}).items() if k not in ('date_format', 'invert')}
    columns = guess_mapping(header)
    columns.update({k: v for k, v in given.items() if v})
    style = (mapping or {}).get('date_format') or 'auto'
    invert = bool((mapping or {}).get('invert'))
    index = {h: i for i, h in enumerate(header)}
    for name, column in columns.items():
        if column and column not in index:
            raise invalid('csv_mapping.' + name, f'no column named {column!r}; the header is {header}')
    if not columns['date']:
        raise invalid('csv_mapping.date', f'name the date column; the header is {header}')
    if not columns['amount'] and not (columns['debit'] or columns['credit']):
        raise invalid('csv_mapping.amount', f'name an amount column, or debit and credit columns; the header is {header}')

    def cell(row, name):
        column = columns.get(name)
        if not column:
            return ''
        position = index[column]
        return row[position].strip() if position < len(row) else ''

    lines, balances = [], []
    for n, row in enumerate(rows[1:], start=2):
        where = f'row {n}'
        when = csv_date(cell(row, 'date'), style, f'{where} date')
        if columns['amount']:
            value = amount(cell(row, 'amount'), places, f'{where} amount')
        else:
            out, into = cell(row, 'debit'), cell(row, 'credit')
            value = ((amount(into, places, f'{where} credit') if into else 0)
                     - (abs(amount(out, places, f'{where} debit')) if out else 0))
        if invert:
            value = -value
        if cell(row, 'balance'):
            balances.append((when, n, amount(cell(row, 'balance'), places, f'{where} balance')))
        lines.append(Line(line_id='', date=when, amount=value, payee=cell(row, 'payee'),
                          memo=cell(row, 'memo'), number=cell(row, 'number'),
                          fitid=cell(row, 'fitid') or None))
    kept, duplicates = _identified(lines)
    dates = [v.date for v in kept]
    ending = None
    if balances:
        # The running balance on the latest-dated row; on a tie the file's own order decides,
        # since a bank lists a day's lines in the order they posted, newest first or last.
        latest = max(b[0] for b in balances)
        same = [b for b in balances if b[0] == latest]
        first_row, last_row = min(same, key=lambda b: b[1]), max(same, key=lambda b: b[1])
        newest_first = len(balances) > 1 and balances[0][0] > balances[-1][0]
        ending = (first_row if newest_first else last_row)[2]
    return Statement(format='csv', lines=kept, duplicates=duplicates,
                     statement_date=max(dates) if dates else None, ending_balance=ending,
                     start_date=min(dates) if dates else None, end_date=max(dates) if dates else None)


def detect(content):
    head = content.lstrip('﻿ \r\n\t')[:2048].upper()
    if head.startswith('OFXHEADER') or '<OFX>' in head or head.startswith('<?XML'):
        return 'ofx'
    return 'csv'


def parse(content, fmt, places, mapping=None):
    chosen = detect(content) if fmt == 'auto' else fmt
    if chosen in ('ofx', 'qfx'):
        if fmt == 'auto':
            # Quicken's QFX is OFX with Intuit's <INTU.*> sign-on tags.
            chosen = 'qfx' if '<INTU.' in content.upper() else 'ofx'
        return parse_ofx(content, places, fmt=chosen)
    return parse_csv(content, places, mapping)


# ------------------------------------------------------------------ matching


@dataclass(frozen=True)
class Candidate:
    """One whole movement the account could clear, in reconciliation sign."""
    ref: object  # whatever the caller needs back (movement key and fingerprint)
    date: str
    amount: int
    number: str = ''
    payees: tuple[str, ...] = ()
    memo: str = ''
    selected: bool = False  # already ticked on the draft
    reconciled: bool = False  # cleared by a certified statement
    eligible: bool = True  # dated on or before the statement date


@dataclass
class Result:
    line: Line
    status: str = 'unmatched'  # matched, suggested, unmatched, reconciled
    match: Candidate | None = None
    suggestions: list = field(default_factory=list)
    reason: str = ''


def _days(a, b):
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


def _digits(text):
    text = (text or '').strip().lstrip('#').lstrip('0')
    return text if text.isdigit() else ''


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]+', (text or '').casefold()) if len(w) > 2}


def _rank(line, candidate):
    """Lower is better: a matching check number first, then the nearer date, then shared words."""
    number = _digits(line.number) and _digits(line.number) == _digits(candidate.number)
    shared = len(_words(line.payee + ' ' + line.memo) & _words(' '.join(candidate.payees) + ' ' + candidate.memo))
    return (0 if number else 1, _days(line.date, candidate.date), -shared)


def match(lines, candidates, *, match_days=MATCH_DAYS, suggest_days=SUGGEST_DAYS):
    """Pair each line with at most one movement, and each movement with at most one line.

    A pair needs the exact amount. Check numbers that both sides carry must agree; when they
    agree the pair matches across the whole suggestion window, since a check clears when the
    payee banks it. Otherwise a pair matches within `match_days` and is offered as a suggestion
    out to `suggest_days`. Movements already cleared by a certified statement can still pair, so
    a line imported again after its statement was finished reads as reconciled instead of as
    something to enter.

    A line with two equally good movements is never decided by a coin toss: it is a suggestion
    listing both. Ties are broken only by the rank above.
    """
    results = [Result(line) for line in lines]
    pairs = []
    for i, result in enumerate(results):
        for j, candidate in enumerate(candidates):
            if candidate.amount != result.line.amount:
                continue
            days = _days(result.line.date, candidate.date)
            ours, theirs = _digits(result.line.number), _digits(candidate.number)
            if ours and theirs and ours != theirs:
                continue
            if days > suggest_days:
                continue
            same_check = bool(ours) and ours == theirs
            strong = (same_check or days <= match_days) and candidate.eligible
            pairs.append((_rank(result.line, candidate), i, j, strong))
    pairs.sort(key=lambda p: (not p[3], p[0], p[1], p[2]))
    taken_line, taken_movement = set(), set()
    by_line = {}
    for rank, i, j, strong in pairs:
        by_line.setdefault(i, []).append((rank, j, strong))
    for rank, i, j, strong in pairs:
        if not strong or i in taken_line or j in taken_movement:
            continue
        rivals = [r for r in by_line[i] if r[2] and r[1] not in taken_movement and r[0] == rank]
        if len(rivals) > 1:
            continue  # two movements equally good for this line: leave it to a person
        # Another line that ranks this movement just as well makes it a coin toss too.
        contenders = [p for p in pairs if p[2] == j and p[3] and p[1] not in taken_line and p[0] == rank]
        if len(contenders) > 1:
            continue
        taken_line.add(i)
        taken_movement.add(j)
        candidate = candidates[j]
        result = results[i]
        result.match = candidate
        result.status = 'reconciled' if candidate.reconciled else 'matched'
        result.reason = ('already cleared by a certified statement' if candidate.reconciled
                         else 'same amount and check number' if rank[0] == 0
                         else 'same amount, same day' if rank[1] == 0
                         else f'same amount, {rank[1]} day{"s" if rank[1] > 1 else ""} apart')
    for i, result in enumerate(results):
        if result.match is not None:
            continue
        # A same-amount entry inside the suggestion window that a person already ticked is their
        # answer to this line's suggestion, and the line is matched to it.
        ticked = [j for _, j, _ in by_line.get(i, []) if j not in taken_movement
                  and candidates[j].selected and candidates[j].eligible]
        if len(ticked) == 1:
            taken_movement.add(ticked[0])
            result.match, result.status = candidates[ticked[0]], 'matched'
            result.reason = (f'same amount, {_days(result.line.date, result.match.date)} days apart; '
                             'ticked by a person')
            continue
        options = [candidates[j] for _, j, _ in sorted(by_line.get(i, []), key=lambda r: r[0])
                   if j not in taken_movement]
        if options:
            result.status = 'suggested'
            result.suggestions = options[:5]
            result.reason = ('more than one entry fits equally well; pick one'
                             if len(options) > 1 else
                             'same amount, but dated after the statement date' if not options[0].eligible
                             else f'same amount, {_days(result.line.date, options[0].date)} days apart'
                             + (', and already cleared by a certified statement' if options[0].reconciled else ''))
        else:
            result.reason = 'no entry in the books has this amount near this date; enter it, then import again'
    return results



def cleared_without_line(lines, ticked, *, suggest_days=SUGGEST_DAYS):
    """Ticked movements no statement line accounts for.

    This is the one check of truth rather than consistency: a reconciliation can tie because an
    invented entry was ticked to make it tie, and only the bank's own lines can tell. A ticked
    movement is supported by a line of the same amount dated within `suggest_days` whose check
    number, when both carry one, agrees; each line supports one movement, nearest first.
    Flagged, never refused -- a person may know why (a bank that omits a line, a file cut short).
    """
    pairs = []
    for i, line in enumerate(lines):
        for j, candidate in enumerate(ticked):
            if candidate.amount != line.amount:
                continue
            days = _days(line.date, candidate.date)
            ours, theirs = _digits(line.number), _digits(candidate.number)
            if days > suggest_days or (ours and theirs and ours != theirs):
                continue
            pairs.append(((0 if ours and ours == theirs else 1, days), i, j))
    used_lines, supported = set(), set()
    for _, i, j in sorted(pairs):
        if i in used_lines or j in supported:
            continue
        used_lines.add(i)
        supported.add(j)
    return [c for j, c in enumerate(ticked) if j not in supported]
