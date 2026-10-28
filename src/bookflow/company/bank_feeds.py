"""The bank inbox: statement files imported at any time, never duplicating an overlap (R108 increment 1,
design/bank-feeds.md §2, §3).

Nothing here is a second parser or matcher: a file is read by ``statement_files.parse``, each line keyed by its
``line_id`` (FITID, or a hash with an occurrence number), and matched by ``statement_files.match`` against
``reconcile_import_cmds.candidates``, exactly as ``reconcile import`` does; CSV mappings are the ones it saves.

``prepare`` derives everything an import will do and writes nothing (the preview); ``write`` re-derives it inside the
command's own write and records it in one transaction: the import, its new lines (each with an ``imported`` event), the
``seen_again`` events of lines a later file repeated with a moved date, what the account received, and the import's
**batch** (kind ``bank-import``, ordering ``strict``, scoped to the account) with one effect per row it made and per
match, so increment 2 reverses the whole import as one unit. An import posts nothing to the books.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, timedelta

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import batches, schema as c, statement_files as files
from bookflow.company import bank_feed_models as m
from bookflow.core.ids import new_id

BATCH_KIND = 'bank-import'
SOFT_DAYS = 3
CHUNK = 500
REFUSED_SHOWN = 50


def scope(account_id: str) -> str:
    return f'bank-account:{account_id}'


def _require_connect(s) -> None:
    """Going on past an account-number mismatch is a connection-level decision (design/bank-feeds.md §8)."""
    from bookflow.hub import access
    access.require_resource(s, 'bank-feed.connect', 'admin')


def _places(currency: str) -> int:
    from bookflow.core.money import CURRENCIES
    return CURRENCIES[currency][0]


def decimal(units: int, places: int) -> str:
    sign = '-' if units < 0 else ''
    units = abs(units)
    if not places:
        return sign + str(units)
    return f'{sign}{units // 10 ** places}.{units % 10 ** places:0{places}d}'


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


# ------------------------------------------------------------------ reading the file

def _problems(text: str, fmt: str, places: int, mapping: dict, first: BookflowError) -> list[dict]:
    """Every problem in a file the parser refused, not only the first: each row (or OFX transaction) is read on its
    own by the same parser, so the person corrects the file once."""
    found = []
    chosen = files.detect(text) if fmt == 'auto' else fmt
    try:
        if chosen in ('ofx', 'qfx'):
            tokens = files._ofx_tokens(text)
            for i, block in enumerate(files._ofx_blocks(tokens, 'STMTTRN')):
                where = f'STMTTRN[{i}]'
                if 'TRNAMT' not in block or 'DTPOSTED' not in block:
                    found.append({'field': 'content', 'problem': f'{where} has no TRNAMT or DTPOSTED'})
                    continue
                for check in (lambda: files.ofx_date(block['DTPOSTED'], where + '.DTPOSTED'),
                              lambda: files.amount(block['TRNAMT'], places, where + '.TRNAMT')):
                    try:
                        check()
                    except BookflowError as error:
                        found.extend(_fields(error))
        else:
            body = text.lstrip('﻿')
            try:
                dialect = csv.Sniffer().sniff(body[:4096], delimiters=',;\t|')
            except csv.Error:
                dialect = csv.excel
            rows = [r for r in csv.reader(io.StringIO(body), dialect) if any(cell.strip() for cell in r)]
            for n, row in enumerate(rows[1:], start=2):
                out = io.StringIO()
                writer = csv.writer(out, delimiter=dialect.delimiter, lineterminator='\n')
                writer.writerow(rows[0])
                writer.writerow(row)
                try:
                    files.parse_csv(out.getvalue(), places, mapping)
                except BookflowError as error:
                    found.extend({**f, 'field': f['field'].replace('row 2', f'row {n}', 1),
                                  'problem': f['problem']} for f in _fields(error))
    except BookflowError:
        pass
    return found or _fields(first)


def _fields(error: BookflowError) -> list[dict]:
    details = error.details or {}
    if details.get('fields'):
        return [dict(f) for f in details['fields']]
    if error.code == 'E_AMOUNT_PRECISION':
        return [{'field': details.get('field', 'content'),
                 'problem': f"more than {details.get('allowed_places')} decimal places; amounts are exact or refused"}]
    return [{'field': details.get('field', 'content'), 'problem': error.message or error.code}]


def read_file(text: str, fmt: str, places: int, mapping: dict):
    """The parsed statement, or one refusal listing every problem in the file (all or nothing)."""
    try:
        return files.parse(text, fmt, places, mapping)
    except BookflowError as error:
        if error.code not in ('E_VALIDATION', 'E_AMOUNT_PRECISION'):
            raise
        problems = _problems(text, fmt, places, mapping, error)
        raise BookflowError('E_VALIDATION', message=(
            f'The file was not imported: {len(problems)} problem{"s" if len(problems) != 1 else ""} '
            '(nothing is imported until every line reads). ' + '; '.join(
                f"{p['field']}: {p['problem']}" for p in problems[:5]) + ('...' if len(problems) > 5 else '')),
            details={'fields': problems[:REFUSED_SHOWN], 'total': len(problems)}) from None


def ofx_hint(text: str) -> dict | None:
    """The bank number, the last four characters of ACCTID and the statement kind an OFX file carries."""
    tokens = files._ofx_tokens(text)
    card = files._ofx_blocks(tokens, 'CCACCTFROM')
    bank = files._ofx_blocks(tokens, 'BANKACCTFROM')
    block = (card or bank or [None])[0]
    if block is None or not block.get('ACCTID'):
        return None
    return {'bank_number': (block.get('BANKID') or '').strip()[:64] or None,
            'account_last4': block['ACCTID'].strip()[-4:], 'kind': 'card' if card else 'bank'}


# ------------------------------------------------------------------ keys and dedupe

def _amount_hash(units: int) -> str:
    return hashlib.sha256(str(units).encode()).hexdigest()[:8]


def _live_lines(conn, account_id: str, keys) -> dict[str, dict]:
    found = {}
    for chunk in _chunks(keys):
        for row in conn.execute(sa.select(c.bank_lines).where(
                c.bank_lines.c.account_id == account_id, c.bank_lines.c.live == 1,
                c.bank_lines.c.line_key.in_(chunk))).mappings():
            found[row['line_key']] = dict(row)
    return found


def _live_by_fitid(conn, account_id: str, fitids) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for chunk in _chunks(fitids):
        for row in conn.execute(sa.select(c.bank_lines).where(
                c.bank_lines.c.account_id == account_id, c.bank_lines.c.live == 1,
                c.bank_lines.c.fitid.in_(chunk))).mappings():
            found.setdefault(row['fitid'], []).append(dict(row))
    return found


def _statement_keys(conn, account_id: str, keys) -> set[str]:
    found = set()
    for chunk in _chunks(keys):
        found.update(conn.execute(sa.select(c.statement_lines.c.line_id).where(
            c.statement_lines.c.account_id == account_id, c.statement_lines.c.line_id.in_(chunk))).scalars())
    return found


def _words(text: str) -> set[str]:
    return files._words(text)


def _similar(a: str, b: str) -> bool:
    """Payee texts sharing most words: more than half of the shorter one's."""
    x, y = _words(a), _words(b)
    if not x or not y:
        return False
    return 2 * len(x & y) > min(len(x), len(y))


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)


# ------------------------------------------------------------------ preparing an import

def _account(s, selector: str) -> dict:
    from bookflow.company.accounts import resolve_account
    account = resolve_account(s.company, selector)
    if account['type'] not in ('bank', 'credit_card'):
        raise BookflowError('E_VALIDATION', message=(
            f"{account['name']} is not a bank or credit card account; statements import only into those."),
            details={'fields': [{'field': 'account', 'problem': 'not a bank or credit card account'}]})
    return account


def _full_name(s, account_id: str) -> str:
    row = s.company.conn.execute(sa.select(c.accounts).where(c.accounts.c.id == account_id)).mappings().first()
    return (row.get('full_name') or row['name']) if row is not None else account_id


def _mapping(s, account_id: str, inp) -> dict:
    from bookflow.commands.reconcile_import_cmds import CsvMapping
    from bookflow.company import statement_store as store
    saved = {}
    if inp.mapping_name:
        saved = store.saved_mapping(s.company.conn, account_id, inp.mapping_name)
        if saved is None:
            raise files.invalid('mapping_name', f'no CSV mapping is saved as {inp.mapping_name!r} for this account')
    given = inp.csv_mapping.model_dump()
    default = CsvMapping().model_dump()
    return {**default, **saved, **{k: v for k, v in given.items() if v != default[k]}}


def prepare(s, ctx, inp) -> dict:
    """What importing this file would do, derived afresh; writes nothing."""
    from bookflow.commands import reconcile_cmds as rc
    from bookflow.commands.reconcile_import_cmds import candidates
    conn = s.company.conn
    account = _account(s, inp.account)
    account_id = account['id']
    currency = account['currency']
    card = account['type'] == 'credit_card'
    places = _places(currency)
    mapping = _mapping(s, account_id, inp)
    if inp.attachment is not None:
        from bookflow.company.attachment_text import attachment_text
        text = attachment_text(s, inp.attachment, 'attachment')[1]
    else:
        text = inp.content
    parsed = read_file(text, inp.format, places, mapping)
    if inp.save_mapping and parsed.format != 'csv':
        raise files.invalid('save_mapping', 'only a CSV file has a column mapping to save')
    if parsed.currency and parsed.currency.upper() != currency:
        raise files.invalid('content', f'the file is in {parsed.currency}; this account is in {currency}')
    if not parsed.lines:
        raise files.invalid('content', 'the file has no transactions')
    sha = hashlib.sha256(text.encode()).hexdigest()
    warnings: list[str] = []

    # The whole file: this exact file came in already.
    same_file = conn.execute(sa.select(c.bank_feed_imports.c.id, c.bank_feed_imports.c.created_at).join(
        c.batches, c.batches.c.id == c.bank_feed_imports.c.batch_id).where(
        c.bank_feed_imports.c.account_id == account_id, c.bank_feed_imports.c.file_sha256 == sha,
        c.batches.c.state != 'reversed').order_by(c.bank_feed_imports.c.created_at)).mappings().first()
    if same_file is not None:
        if not inp.again:
            raise BookflowError('E_ALREADY_IMPORTED', message=(
                f"This exact file came into {_full_name(s, account_id)} on {same_file['created_at'][:10]} (import "
                f"{same_file['id']}). Its lines are already in the inbox; pass again to import it anyway."),
                details={'import': same_file['id'], 'imported_at': same_file['created_at'], 'field': 'again'})
        warnings.append(f"this exact file came in on {same_file['created_at'][:10]} (import {same_file['id']})")

    check = _account_check(s, account, parsed, text, inp)

    # Keys: the parser's ids. A FITID is the same line while its amount is the same (a pending line the bank re-dated
    # when it posted); a FITID the bank reused for another amount is kept apart (`!<amount hash>`), never dropped.
    rows = []  # dicts: line, key, outcome, warnings (and held: the live line it repeats)
    ordered = sorted((*parsed.lines, *parsed.duplicates), key=lambda v: (v.date, v.line_id, v.amount))
    by_fitid = _live_by_fitid(conn, account_id, {v.fitid for v in ordered if v.fitid})
    live = _live_lines(conn, account_id, {v.line_id for v in ordered if not v.fitid})
    taken = {row['line_key'] for held in by_fitid.values() for row in held}
    in_file: dict[tuple[str, int], dict] = {}
    for line in ordered:
        r = {'line': line, 'key': line.line_id, 'outcome': 'new', 'warnings': []}
        rows.append(r)
        if not line.fitid:
            if line.line_id in live:
                r['outcome'], r['held'] = 'already_in', live[line.line_id]
            continue
        if (line.fitid, line.amount) in in_file:
            r['outcome'] = 'repeated_in_file'
            continue
        in_file[(line.fitid, line.amount)] = r
        held = by_fitid.get(line.fitid, [])
        same = next((row for row in held if row['amount'] == line.amount), None)
        if same is not None:
            r['key'], r['outcome'], r['held'] = same['line_key'], 'already_in', same
            continue
        if held or any(f == line.fitid and a != line.amount for f, a in in_file):
            r['warnings'] = ['fitid_reused']
        if r['key'] in taken:
            r['key'] = f"{line.line_id}!{_amount_hash(line.amount)}"
        taken.add(r['key'])
    in_statement = _statement_keys(conn, account_id, {r['key'] for r in rows if r['outcome'] == 'new'})
    for r in rows:
        if r['outcome'] == 'new' and r['key'] in in_statement:
            r['outcome'] = 'in_statement'
    new = [r for r in rows if r['outcome'] == 'new']

    # Soft duplicates: a new hash line that probably repeats a live line whose text the bank cleaned up.
    file_keys = {r['key'] for r in rows}
    claimed = set()
    hash_new = [r for r in new if r['key'].startswith('hash:')]
    if hash_new:
        lo = min(r['line'].date for r in hash_new)
        hi = max(r['line'].date for r in hash_new)
        lo = (date.fromisoformat(lo) - timedelta(days=SOFT_DAYS)).isoformat()
        hi = (date.fromisoformat(hi) + timedelta(days=SOFT_DAYS)).isoformat()
        nearby = [dict(v) for v in conn.execute(sa.select(
            c.bank_lines.c.id, c.bank_lines.c.line_key, c.bank_lines.c.date, c.bank_lines.c.amount,
            c.bank_lines.c.payee, c.bank_lines.c.memo).where(
            c.bank_lines.c.account_id == account_id, c.bank_lines.c.live == 1,
            c.bank_lines.c.date >= lo, c.bank_lines.c.date <= hi)).mappings()
            if v['line_key'] not in file_keys]
        by_amount: dict[int, list[dict]] = {}
        for v in nearby:
            by_amount.setdefault(v['amount'], []).append(v)
        for r in hash_new:
            line = r['line']
            fits = [v for v in by_amount.get(line.amount, ()) if v['id'] not in claimed
                    and _days(v['date'], line.date) <= SOFT_DAYS
                    and _similar(v['payee'] or v['memo'], line.payee or line.memo)]
            if fits:
                fits.sort(key=lambda v: (_days(v['date'], line.date), v['id']))
                r['duplicate_of'] = fits[0]['id']
                claimed.add(fits[0]['id'])
                r['warnings'] = [*r['warnings'], 'possible_duplicate']

    # Matching: the shared matcher, in reconciliation sign, against entries no live inbox line is linked to yet.
    sign = -1 if card else 1
    results = []
    if new:
        snapshot = rc._loaded(s, account_id)
        cutoff = (date.fromisoformat(max(r['line'].date for r in new)) + timedelta(days=inp.suggest_days)).isoformat()
        linked = set(conn.execute(sa.select(c.bank_lines.c.movement).where(
            c.bank_lines.c.account_id == account_id, c.bank_lines.c.live == 1,
            c.bank_lines.c.movement.is_not(None))).scalars())
        population = [v for v in candidates(snapshot, account_id, cutoff, None, conn)
                      if v.ref[0].model_dump_json() not in linked]
        lines = [files.Line(**{**r['line'].__dict__, 'amount': sign * r['line'].amount}) for r in new]
        results = files.match(lines, population, match_days=inp.match_days, suggest_days=inp.suggest_days)
    for r, result in zip(new, results):
        r['result'] = result
        if result.status == 'reconciled':
            r['state'], r['entry'] = 'settled', result.match
        elif result.status == 'matched' and inp.auto_match:
            r['state'], r['entry'] = 'matched', result.match
        else:
            r['state'], r['entry'] = 'waiting', None
            if result.status == 'matched':
                r['suggest'] = [result.match]
        r['reason'] = result.reason
    for r in new:
        r.setdefault('state', 'waiting')
        r.setdefault('entry', None)
        r.setdefault('reason', '')

    overlap = _overlap(s, account_id, [r['key'] for r in rows if r['outcome'] != 'repeated_in_file'])
    for o in overlap:
        if o['percent'] >= 50:
            warnings.append(f"{o['lines']} of these lines ({o['percent']}%) are already in {o['account']}: "
                            'check this is the right account')
    reused = sum(1 for r in new if 'fitid_reused' in r['warnings'])
    if reused:
        warnings.append(f'{reused} line(s) carry a FITID the bank already used for another amount: kept, not dropped')
    soft = sum(1 for r in new if r.get('duplicate_of'))
    if soft:
        warnings.append(f'{soft} new line(s) look like lines already in the inbox with the text changed '
                        '(possible_duplicate): ignore each as a duplicate, or keep it')
    count = lambda outcome: sum(1 for r in rows if r['outcome'] == outcome)
    counts = dict(lines_in_file=len(rows), new=len(new), already_in=count('already_in'),
                  in_statement=count('in_statement'), repeated_in_file=count('repeated_in_file'),
                  soft_duplicates=soft, fitid_reused=reused,
                  matched=sum(1 for r in new if r['state'] == 'matched'),
                  settled=sum(1 for r in new if r['state'] == 'settled'),
                  waiting=sum(1 for r in new if r['state'] == 'waiting'))
    total = sum(r['line'].amount for r in new)
    dates = [r['line'].date for r in rows]
    already = counts['already_in'] + counts['in_statement']
    summary = f"{len(rows)} lines: {len(new)} new, {already} already in"
    if counts['repeated_in_file']:
        summary += f", {counts['repeated_in_file']} repeated in the file"
    return dict(account=account, account_id=account_id, currency=currency, places=places, card=card, text=text,
                sha=sha, parsed=parsed, mapping=mapping, rows=rows, new=new, counts=counts, total=total,
                date_from=min(dates), date_to=max(dates), summary=summary, warnings=warnings, check=check,
                overlap=overlap, input=inp)


def _account_check(s, account: dict, parsed, text: str, inp) -> dict:
    conn = s.company.conn
    account_id = account['id']
    known = conn.execute(sa.select(c.bank_feed_accounts).where(
        c.bank_feed_accounts.c.account_id == account_id)).mappings().first()
    last = conn.execute(sa.select(sa.func.max(c.bank_feed_imports.c.created_at)).where(
        c.bank_feed_imports.c.account_id == account_id)).scalar()
    out = dict(account_id=account_id, account=_full_name(s, account_id), type=account['type'], last_import_at=last,
               known_bank_number=known['bank_number'] if known else None,
               known_account_last4=known['account_last4'] if known else None,
               known_kind=known['kind'] if known else None)
    if parsed.format == 'csv':
        return {**out, 'status': 'no_id', 'hint': None}
    hint = ofx_hint(text)
    if hint is None:
        return {**out, 'status': 'no_id', 'hint': None}
    out.update(file_bank_number=hint['bank_number'], file_account_last4=hint['account_last4'], file_kind=hint['kind'])
    problems = []
    expected_kind = 'card' if account['type'] == 'credit_card' else 'bank'
    if hint['kind'] != expected_kind:
        problems.append(f"the file is a {'card' if hint['kind'] == 'card' else 'bank'} statement and "
                        f"{out['account']} is a {'credit card' if expected_kind == 'card' else 'bank'} account")
    if known is not None and known['account_last4'] and (
            known['account_last4'] != hint['account_last4']
            or (known['bank_number'] or None) != hint['bank_number']):
        problems.append(f"the file is for account ...{hint['account_last4']}"
                        + (f" at bank {hint['bank_number']}" if hint['bank_number'] else '')
                        + f"; {out['account']} last received ...{known['account_last4']}")
    elif (known is None or not known['account_last4']) and account.get('institution_account_last4') and (
            account['institution_account_last4'] != hint['account_last4']):
        # Nothing received yet: the account's own record of its number at the bank is the hint.
        out['known_account_last4'] = account['institution_account_last4']
        problems.append(f"the file is for account ...{hint['account_last4']}; {out['account']} is recorded as "
                        f"...{account['institution_account_last4']} at the bank")
    holder = conn.execute(sa.select(c.bank_feed_accounts.c.account_id).where(
        c.bank_feed_accounts.c.account_id != account_id,
        c.bank_feed_accounts.c.account_last4 == hint['account_last4'],
        sa.func.coalesce(c.bank_feed_accounts.c.bank_number, '') == (hint['bank_number'] or ''))).scalar()
    if holder is not None:
        problems.append(f"account ...{hint['account_last4']} last came into {_full_name(s, holder)}")
    if not problems:
        status = 'first' if known is None or not known['account_last4'] else 'same'
        return {**out, 'status': status, 'hint': hint}
    if not inp.confirm_account:
        raise BookflowError('E_ACCOUNT_MISMATCH', message=(
            'This file may be for another account: ' + '; '.join(problems) + '. Import it to the right account, or '
            'pass confirm_account (bank-feed.connect) to go on.'), details={
            'account': account_id, 'account_name': out['account'], 'file': hint,
            'known': {'bank_number': out['known_bank_number'], 'account_last4': out['known_account_last4'],
                      'kind': out['known_kind']},
            'received_by': holder, 'received_by_name': _full_name(s, holder) if holder else None,
            'problems': problems, 'field': 'confirm_account'})
    _require_connect(s)
    return {**out, 'status': 'confirmed', 'hint': hint}


def _overlap(s, account_id: str, keys: list[str]) -> list[dict]:
    if not keys:
        return []
    found: dict[str, int] = {}
    for chunk in _chunks(set(keys)):
        for other, n in s.company.conn.execute(sa.select(c.bank_lines.c.account_id, sa.func.count()).where(
                c.bank_lines.c.account_id != account_id, c.bank_lines.c.live == 1,
                c.bank_lines.c.line_key.in_(chunk)).group_by(c.bank_lines.c.account_id)).all():
            found[other] = found.get(other, 0) + int(n)
    return sorted(({'account_id': other, 'account': _full_name(s, other), 'lines': n,
                    'percent': n * 100 // len(keys)} for other, n in found.items()),
                  key=lambda o: (-o['lines'], o['account']))


# ------------------------------------------------------------------ output

def _entry_id(candidate) -> str:
    return candidate.ref[0].transaction_id


def output(prepared: dict, *, import_id=None, batch_id=None, line_ids=None, dry_run=False) -> m.ImportOutput:
    inp, places = prepared['input'], prepared['places']
    line_ids = line_ids or {}
    out = []
    for r in prepared['rows'][:inp.limit]:
        line = r['line']
        result = r.get('result')
        options = r.get('suggest') or (result.suggestions if result is not None and result.status == 'suggested'
                                       else [])
        out.append(m.Line(
            id=line_ids.get(id(r)), line_key=r['key'], fitid=line.fitid, date=line.date, amount=line.amount,
            amount_decimal=decimal(line.amount, places), payee=line.payee, memo=line.memo, number=line.number,
            outcome=r['outcome'], state=r.get('state') if r['outcome'] == 'new' else None,
            reason=(r.get('reason') or '') if r['outcome'] == 'new' else {
                'already_in': 'already in the inbox' + (f" (since {r['held']['date']})" if r.get('held') else ''),
                'in_statement': "already in a reconciliation's statement",
                'repeated_in_file': 'the file repeats this line; read once'}[r['outcome']],
            entry_id=_entry_id(r['entry']) if r.get('entry') is not None else None,
            suggestions=tuple(m.Suggestion(entry_id=_entry_id(v), date=v.date, amount=v.amount, number=v.number,
                                           payees=v.payees) for v in options[:5]),
            possible_duplicate_of=r.get('duplicate_of'), warnings=tuple(r['warnings'])))
    counts = prepared['counts']
    if dry_run:
        step = ('Dry run: nothing was written. Run the same call without --dry-run to import '
                f"{counts['new']} new line(s); then `bank-line query --state waiting` lists what needs sorting.")
    elif import_id is None:
        step = 'Nothing new to import: every line of this file is already in.'
    else:
        step = (f"Imported {counts['new']} line(s) into the inbox ({counts['matched']} matched, {counts['settled']} "
                f"settled, {counts['waiting']} waiting). `bank-line query --state waiting --import-id {import_id}` "
                f"lists what needs sorting; `bank-feed show {import_id}` shows the import.")
    check = {k: v for k, v in prepared['check'].items() if k != 'hint'}
    return m.ImportOutput(
        import_id=import_id, batch_id=batch_id, account_id=prepared['account_id'], currency=prepared['currency'],
        format=prepared['parsed'].format, file_sha256=prepared['sha'], date_from=prepared['date_from'],
        date_to=prepared['date_to'], sum=prepared['total'], sum_decimal=decimal(prepared['total'], places),
        counts=m.ImportCounts(**counts), summary=prepared['summary'], account_check=m.AccountCheck(**check),
        overlap_elsewhere=tuple(m.Overlap(**o) for o in prepared['overlap']), warnings=tuple(prepared['warnings']),
        lines=tuple(out), has_more_lines=len(prepared['rows']) > inp.limit, next=step)


# ------------------------------------------------------------------ writing an import

def write(s, ctx, prepared: dict) -> tuple[m.ImportOutput, bool]:
    """Record the import in the command's own transaction; returns the output and whether anything was written."""
    from bookflow.company import statement_store as store
    from bookflow.core import clock
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    conn = s.company.conn
    inp = prepared['input']
    account_id = prepared['account_id']
    new = prepared['new']
    moved = [r for r in prepared['rows'] if r['outcome'] == 'already_in'
             and r['held']['date'] != r['line'].date]
    saving = bool(inp.save_mapping) and store.saved_mapping(conn, account_id, inp.save_mapping) != prepared['mapping']
    if not new and not saving and not moved:
        return output(prepared), False
    actor = s.actor
    at = clock.now_iso()
    via = str(getattr(ctx.interface, 'value', ctx.interface))[:16]
    import_id = new_id() if new else None
    touched = []
    if import_id:
        touched.append(Touched('bank_feed_import', import_id, 'create', None, 1,
                               {'account_id': account_id, 'lines_new': len(new)}, db='company'))
    if saving:
        touched.append(Touched('statement_csv_mapping', account_id, 'create', None, 1, {'name': inp.save_mapping},
                               db='company'))
    summary = (f"imported a {prepared['parsed'].format} statement into the bank inbox: {prepared['summary']}"
               if import_id else f"read a statement: {prepared['summary']}")
    event = write_event_to(s.company, ctx, 'bank-feed import', summary, touched,
                           actor_id=actor.id if actor else None, actor_kind=actor.kind if actor else None)
    line_ids: dict[int, str] = {}
    batch_id = None
    if import_id:
        batch_id = batches._insert_batch(
            s, ctx, kind=BATCH_KIND, label=f"Bank import: {prepared['summary']}"[:200], scope=scope(account_id),
            ordering='strict', source_type='bank_feed_import', source_id=import_id)
        conn.execute(c.bank_feed_imports.insert().values(
            id=import_id, batch_id=batch_id, account_id=account_id, format=prepared['parsed'].format,
            file_sha256=prepared['sha'], attachment_id=inp.attachment, mapping_name=inp.mapping_name or None,
            date_from=prepared['date_from'], date_to=prepared['date_to'], lines_in_file=len(prepared['rows']),
            lines_new=len(new), counts=json.dumps(prepared['counts'], sort_keys=True),
            warnings=json.dumps(prepared['warnings']), moved_from=None, reason=ctx.reason,
            created_by=actor.id, created_via=via, on_behalf_of=batches._on_behalf(s, ctx), created_at=at,
            audit_event_id=event))
        line_rows, event_rows = [], []
        for n, r in enumerate(new):
            line, entry = r['line'], r['entry']
            line_id = new_id()
            line_ids[id(r)] = line_id
            line_rows.append(dict(
                id=line_id, import_id=import_id, ordinal=n, account_id=account_id, line_key=r['key'],
                fitid=line.fitid, date=line.date, amount=line.amount, payee=line.payee, memo=line.memo,
                number=line.number[:64], kind=(line.kind or '')[:32], state=r['state'], live=1, version=1,
                entry_id=_entry_id(entry) if entry is not None else None,
                movement=entry.ref[0].model_dump_json() if entry is not None else None,
                link_how='auto' if entry is not None else None, match_reason=(r['reason'] or None) and r['reason'][:500],
                possible_duplicate_of=r.get('duplicate_of'), warnings=json.dumps(r['warnings'])))
            event_rows.append(dict(
                id=new_id(), line_id=line_id, kind='imported', import_id=import_id, batch_id=batch_id,
                details=json.dumps({'state': r['state'], 'reason': r['reason'], 'warnings': r['warnings'],
                                    'entry': line_rows[-1]['entry_id'],
                                    'possible_duplicate_of': r.get('duplicate_of')}, sort_keys=True),
                created_by=actor.id, created_via=via, created_at=at, audit_event_id=event))
        for chunk in _chunks(line_rows):
            conn.execute(c.bank_lines.insert(), chunk)
        effects = [dict(record_type='bank_feed_import', record_id=import_id, action='created', version_after=None)]
        effects += [dict(record_type='bank_line', record_id=v['id'], action='created', version_after=1)
                    for v in line_rows]
        effects += [dict(record_type='transaction', record_id=v['entry_id'], action='linked', version_after=None)
                    for v in line_rows if v['entry_id'] is not None]
        for chunk in _chunks([dict(batch_id=batch_id, seq=n, before=None, event_id=event, created_at=at, **e)
                              for n, e in enumerate(effects, start=1)]):
            conn.execute(c.batch_effects.insert(), chunk)
        hint = prepared['check'].get('hint')
        values = dict(last_import_id=import_id, updated_at=at)
        if hint is not None and prepared['check']['status'] in ('first', 'confirmed'):
            values.update(bank_number=hint['bank_number'], account_last4=hint['account_last4'], kind=hint['kind'])
        known = conn.execute(sa.select(c.bank_feed_accounts.c.version).where(
            c.bank_feed_accounts.c.account_id == account_id)).scalar()
        if known is None:
            conn.execute(c.bank_feed_accounts.insert().values(account_id=account_id, version=1, **values))
        else:
            conn.execute(c.bank_feed_accounts.update().where(c.bank_feed_accounts.c.account_id == account_id)
                         .values(version=known + 1, **values))
    else:
        event_rows = []
    # A line the bank re-dated (a pending item that posted) is the same line: its date drift is noted on it.
    for r in moved:
        event_rows.append(dict(
            id=new_id(), line_id=r['held']['id'], kind='seen_again', import_id=import_id, batch_id=batch_id,
            details=json.dumps({'date_was': r['held']['date'], 'date_in_file': r['line'].date,
                                'file_sha256': prepared['sha']}, sort_keys=True),
            created_by=actor.id, created_via=via, created_at=at, audit_event_id=event))
    for chunk in _chunks(event_rows):
        conn.execute(c.bank_line_events.insert(), chunk)
    if saving:
        store.save_mapping(conn, account_id=account_id, name=inp.save_mapping, mapping=prepared['mapping'],
                           created_at=at, created_by=actor.id if actor else None, audit_event_id=event)
    return output(prepared, import_id=import_id, batch_id=batch_id, line_ids=line_ids), True


# ------------------------------------------------------------------ reading the inbox

def _import_row(s, row: dict) -> dict:
    state = s.company.conn.execute(sa.select(c.batches.c.state).where(c.batches.c.id == row['batch_id'])).scalar()
    return dict(id=row['id'], batch_id=row['batch_id'], batch_state=state, account_id=row['account_id'],
                account=_full_name(s, row['account_id']), format=row['format'], file_sha256=row['file_sha256'],
                attachment_id=row['attachment_id'], mapping_name=row['mapping_name'], date_from=row['date_from'],
                date_to=row['date_to'], lines_in_file=row['lines_in_file'], lines_new=row['lines_new'],
                counts=json.loads(row['counts']), warnings=tuple(json.loads(row['warnings'])),
                moved_from=row['moved_from'], reason=row['reason'], created_by=row['created_by'],
                created_via=row['created_via'], on_behalf_of=row['on_behalf_of'], created_at=row['created_at'])


def _line_out(row: dict, places: int) -> dict:
    return dict(id=row['id'], import_id=row['import_id'], account_id=row['account_id'], line_key=row['line_key'],
                fitid=row['fitid'], date=row['date'], amount=row['amount'],
                amount_decimal=decimal(row['amount'], places), payee=row['payee'], memo=row['memo'],
                number=row['number'], kind=row['kind'], state=row['state'], live=bool(row['live']),
                entry_id=row['entry_id'], link_how=row['link_how'], reason=row['match_reason'],
                possible_duplicate_of=row['possible_duplicate_of'], warnings=tuple(json.loads(row['warnings'])),
                version=row['version'])


def _offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not re.fullmatch(r'\d{1,9}', cursor):
        raise files.invalid('cursor', 'not a cursor from an earlier page of this query; start again without one')
    return int(cursor)


def _currency_places(s, account_id: str) -> int:
    currency = s.company.conn.execute(sa.select(c.accounts.c.currency).where(c.accounts.c.id == account_id)).scalar()
    return _places(currency)


def show(s, import_id: str, *, state=None, limit=100, cursor=None) -> dict:
    row = s.company.conn.execute(sa.select(c.bank_feed_imports).where(
        c.bank_feed_imports.c.id == import_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'bank_feed_import', 'selector': import_id})
    places = _currency_places(s, row['account_id'])
    by_state = {k: int(n) for k, n in s.company.conn.execute(
        sa.select(c.bank_lines.c.state, sa.func.count()).where(c.bank_lines.c.import_id == import_id)
        .group_by(c.bank_lines.c.state)).all()}
    q = sa.select(c.bank_lines).where(c.bank_lines.c.import_id == import_id)
    if state:
        q = q.where(c.bank_lines.c.state == state)
    start = _offset(cursor)
    found = s.company.conn.execute(q.order_by(c.bank_lines.c.ordinal).offset(start).limit(limit + 1)).mappings().all()
    return dict(bank_import=_import_row(s, dict(row)), by_state=by_state,
                lines=tuple(_line_out(dict(v), places) for v in found[:limit]),
                next_cursor=str(start + limit) if len(found) > limit else None)


def query(s, *, account=None, include_reversed=False, limit=50, cursor=None) -> dict:
    q = sa.select(c.bank_feed_imports).join(c.batches, c.batches.c.id == c.bank_feed_imports.c.batch_id)
    if account is not None:
        q = q.where(c.bank_feed_imports.c.account_id == _account(s, account)['id'])
    if not include_reversed:
        q = q.where(c.batches.c.state != 'reversed')
    start = _offset(cursor)
    found = s.company.conn.execute(q.order_by(c.bank_feed_imports.c.created_at.desc(), c.bank_feed_imports.c.id.desc())
                                   .offset(start).limit(limit + 1)).mappings().all()
    return dict(imports=tuple(_import_row(s, dict(v)) for v in found[:limit]),
                next_cursor=str(start + limit) if len(found) > limit else None)


def line_query(s, inp) -> dict:
    t = c.bank_lines
    filters = []
    account_id = None
    if inp.account is not None:
        account_id = _account(s, inp.account)['id']
        filters.append(t.c.account_id == account_id)
    if inp.import_id is not None:
        filters.append(t.c.import_id == inp.import_id)
    if not inp.include_reversed:
        filters.append(t.c.live == 1)
    if inp.date_from:
        filters.append(t.c.date >= inp.date_from)
    if inp.date_to:
        filters.append(t.c.date <= inp.date_to)
    if inp.text:
        for word in inp.text.split():
            like = '%' + word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            filters.append(sa.or_(t.c.payee.ilike(like, escape='\\'), t.c.memo.ilike(like, escape='\\'),
                                  t.c.number.ilike(like, escape='\\')))
    if inp.amount is not None:
        if account_id is None:
            raise files.invalid('amount', 'name the account to filter by amount (its currency fixes the places)')
        filters.append(t.c.amount == files.amount(inp.amount, _currency_places(s, account_id), 'amount'))
    counts = {k: int(n) for k, n in s.company.conn.execute(
        sa.select(t.c.state, sa.func.count()).where(*filters).group_by(t.c.state)).all()}
    if inp.state:
        filters.append(t.c.state == inp.state)
    start = _offset(inp.cursor)
    found = s.company.conn.execute(sa.select(t).where(*filters).order_by(t.c.date, t.c.import_id, t.c.ordinal)
                                   .offset(start).limit(inp.limit + 1)).mappings().all()
    places = {}
    lines = []
    for v in found[:inp.limit]:
        if v['account_id'] not in places:
            places[v['account_id']] = _currency_places(s, v['account_id'])
        lines.append(_line_out(dict(v), places[v['account_id']]))
    return dict(lines=tuple(lines), counts=counts, next_cursor=str(start + inp.limit) if len(found) > inp.limit
                else None)
