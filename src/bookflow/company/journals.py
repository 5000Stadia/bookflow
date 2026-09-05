"""Domestic journal planning and persistence within dispatch's transaction."""
from __future__ import annotations

import json
import sqlalchemy as sa
from bookflow.company import schema as c, accounts, parties, list_service
from bookflow.company.journal_models import JournalLineInput, parse_domestic_amount, checked_sum
from bookflow.company.journal_outputs import (
    JournalOutput, JournalWriteOutput, JournalRevisionOutput, JournalBatchOutput,
    JournalSummaryOutput, JournalPageOutput, JournalHistoryOutput, JournalRevisionSummaryOutput,
)
from bookflow.core import audit, clock, versioning
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id, is_ulid
from bookflow.core.money import Money
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common

FACTS = ('original_minor_units', 'original_currency', 'rate_used', 'rate_source')
DIMENSIONS = ('account_id', 'account_snapshot', 'currency', 'name_type', 'name_id',
              'party_name', 'class_id', 'class_name', 'description', *FACTS)
TABLES = ('transaction_revisions', 'document_line_identities', 'document_lines',
          'posting_batches', 'posting_lines', 'posting_line_sources')
TYPES = ('transaction_revision', 'document_line_identity', 'document_line',
         'posting_batch', 'posting_line', 'posting_line_source')


def invalid(field, problem):
    return BookflowError('E_VALIDATION', details={'fields': [{'field': field, 'problem': problem}]})


def rows(s, table, *where, order=None):
    q = sa.select(table).where(*where)
    if order is not None:
        q = q.order_by(order)
    return [dict(r) for r in s.company.conn.execute(q).mappings()]


def resolve(s, selector):
    t = c.transactions
    key = selector.upper() if is_ulid(selector) else selector
    found = rows(s, t, t.c.id == key)
    if not found:
        found = rows(s, t, t.c.number == selector, t.c.type == 'journal_entry')
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'journal', 'selector': selector})
    return found[0]


def revision(s, header, number=None):
    t = c.transaction_revisions
    found = rows(s, t, t.c.transaction_id == header['id'],
                 t.c.id == header['current_revision_id'] if number is None else t.c.revision_number == number)
    if not found:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'transaction_revision'})
    return found[0]


def decoded(row):
    return {k: json.loads(v) if k.endswith('_snapshot') and isinstance(v, str) else v for k, v in row.items()}


def totals(debit, credit, currency, *, document=False):
    values = dict(debit_total=Money(debit, currency).to_dict(),
                  credit_total=Money(credit, currency).to_dict(),
                  debit_minor_units=debit, credit_minor_units=credit)
    if document:
        values['total'] = Money(debit, currency).to_dict()
    return values


def batch_output(s, batch, supplied=None):
    if supplied is None:
        # History summaries never load account snapshots or entered descriptions.
        t = c.posting_lines
        supplied = list(s.company.conn.execute(sa.select(
            t.c.debit_minor_units, t.c.credit_minor_units, t.c.currency,
        ).where(t.c.batch_id == batch['id'])).mappings())
    debit = checked_sum((l['debit_minor_units'] for l in supplied), 'debits')
    credit = checked_sum((l['credit_minor_units'] for l in supplied), 'credits')
    if not supplied:
        raise BookflowError('E_INTERNAL', message='A journal posting batch has no lines.')
    currency = supplied[0]['currency']
    return JournalBatchOutput(**batch, **totals(debit, credit, currency, document=True),
                              currency=currency, line_count=len(supplied))


def revision_output(s, rev, pending=None, *, summary_only=False):
    pending = pending or {}
    batches = rows(s, c.posting_batches, c.posting_batches.c.revision_id == rev['id'], order=c.posting_batches.c.id)
    batches += [b for b in pending.get('posting_batches', []) if b['revision_id'] == rev['id']]
    summaries = [batch_output(s, b, [l for l in pending['posting_lines'] if l['batch_id'] == b['id']]
                             if b in pending.get('posting_batches', []) else None) for b in batches]
    values = {k: v for k, v in rev.items() if not k.endswith('_snapshot')}
    values.update(totals(rev['total_minor_units'], rev['total_minor_units'], rev['currency'], document=True))
    if summary_only:
        line_count = s.company.conn.execute(sa.select(sa.func.count()).select_from(c.document_lines)
                                           .where(c.document_lines.c.revision_id == rev['id'])).scalar_one()
        return JournalRevisionSummaryOutput(**values, line_count=line_count, batches=summaries)
    lines = pending.get('document_lines')
    if lines is None:
        lines = rows(s, c.document_lines, c.document_lines.c.revision_id == rev['id'], order=c.document_lines.c.position)
    else:
        lines = [l for l in lines if l['revision_id'] == rev['id']]
    return JournalRevisionOutput(**values, line_count=len(lines), batches=summaries,
        issuer_snapshot=json.loads(rev['issuer_snapshot']), custom_fields_snapshot=json.loads(rev['custom_fields_snapshot']),
        lines=[dict(decoded(l), amount=Money(l['amount_minor_units'], l['currency']).to_dict()) for l in lines])


def summary(header, rev):
    return dict(header, date=rev['date'], memo=rev['memo'], currency=rev['currency'],
                total_minor_units=rev['total_minor_units'],
                **totals(rev['total_minor_units'], rev['total_minor_units'], rev['currency'], document=True))


def show(s, inp):
    h = resolve(s, inp.journal)
    r = revision(s, h, inp.revision_number)
    return JournalOutput(**summary(h, r), revision=revision_output(s, r))


def open_dates(s, dates):
    closing = s.company.conn.execute(sa.select(c.company_info.c.closing_date)).scalar_one()
    for date in dates:
        if closing and date <= closing:
            raise BookflowError('E_PERIOD_CLOSED', details={'date': date, 'closing_date': closing})


def version_meta(s, h, expected):
    writer = versioning.current_writer(s.company, 'transaction', h['id'], h)
    from bookflow.company.info import principal_names
    if writer:
        names = principal_names(s.company, {x for x in (writer.updated_by, writer.on_behalf_of) if x})
        writer.updated_by_name = names.get(writer.updated_by)
        writer.on_behalf_of_name = names.get(writer.on_behalf_of)
    def history(v):
        entries = versioning.history_from_entries(s.company, 'transaction', h['id'], v, audit.decode_snapshot)
        # Every version is the same indivisible financial aggregate.
        for e in entries:
            e.changed_columns = ['journal']
        return entries
    return versioning.check_update(current_version=h['version'], current_updated_at=h['updated_at'],
        current_writer=writer, changes={'journal'}, expected_version=expected, history_since=history,
        actor_id=s.actor.id, window_seconds=s.company_info_row.get('recent_activity_window_seconds', 60))


def active(row, kind):
    if not row['active']:
        raise BookflowError('E_INACTIVE_REFERENCE', details={'record_type': kind, 'record_id': row['id']})
    return row


def line_values(s, inp, currency, old=None, refresh=False):
    account = active(accounts.resolve_account(s.company, inp.account), 'account')
    if account['type'] == 'non_posting':
        raise invalid('account', 'must be a posting account')
    if account.get('currency') not in (None, currency):
        raise invalid('account', 'account currency must match the home currency for a domestic journal')
    amount = parse_domestic_amount(inp.amount, currency).minor_units
    party = active(parties.resolve_party(s.company, inp.name_type.replace('_', '-'), inp.name_id), inp.name_type) if inp.name_id else None
    required = {'accounts_receivable': 'customer', 'accounts_payable': 'vendor'}.get(account['type'])
    if required and inp.name_type != required:
        raise invalid('name_type', f'{account["type"]} requires {required}')
    klass = None
    if inp.class_id:
        from bookflow.company.lists import get_list_definition
        klass = active(list_service.resolve_selector(s.company, c.classes, get_list_definition('class'), inp.class_id), 'class')
    result = dict(account_id=account['id'], side=inp.side, amount_minor_units=amount, currency=currency,
        account_snapshot=json.dumps({k: account.get(k) for k in ('id', 'name', 'full_name', 'number', 'type')} | {'normal_balance': accounts.NORMAL_BALANCE[account['type']]}, sort_keys=True),
        name_type=inp.name_type, name_id=party['id'] if party else None,
        party_name=(party.get('full_name') or party.get('name') or party.get('display_name')) if party else None,
        class_id=klass['id'] if klass else None, class_name=(klass.get('full_name') or klass.get('name')) if klass else None,
        description=inp.description, **dict.fromkeys(FACTS))
    if old and not refresh:
        if result['account_id'] == old['account_id']:
            result['account_snapshot'] = old['account_snapshot']
        if (result['name_type'], result['name_id']) == (old['name_type'], old['name_id']):
            result['party_name'] = old['party_name']
        if result['class_id'] == old['class_id']:
            result['class_name'] = old['class_name']
    if old and all(result[k] == old[k] for k in ('account_id', 'side', 'amount_minor_units', 'currency')):
        result.update({k: old[k] for k in FACTS})
    return result


def existing_input(l):
    return JournalLineInput(line_id=l['line_id'], account=l['account_id'], side=l['side'],
        amount={'minor_units': l['amount_minor_units'], 'currency': l['currency']},
        name_type=l['name_type'], name_id=l['name_id'], class_id=l['class_id'], description=l['description'])


def allocate(s, explicit, own=None):
    t = c.transactions
    def occupied(n):
        q = sa.select(t.c.id).where(t.c.type == 'journal_entry', t.c.number == n)
        if own:
            q = q.where(t.c.id != own)
        return s.company.conn.execute(q).first() is not None
    if explicit is not None:
        if occupied(explicit):
            raise BookflowError('E_DUPLICATE_NUMBER', details={'number': explicit, 'type': 'journal_entry'})
        return explicit, None
    sequence = rows(s, c.sequences, c.sequences.c.name == 'journal_entry')
    n, prefix = (sequence[0]['next_number'], sequence[0]['prefix']) if sequence else (1, '')
    while occupied(f'{prefix}{n}'):
        n += 1
    if n >= 9223372036854775807:
        raise BookflowError('E_VALUE_RANGE', details={'field': 'next_number'})
    return f'{prefix}{n}', dict(name='journal_entry', next_number=n + 1, prefix=prefix)


def prepare(s, ctx, inp, operation):
    old_h = resolve(s, inp.journal) if operation != 'post' else None
    old_r = revision(s, old_h) if old_h else None
    meta = version_meta(s, old_h, inp.expected_version) if old_h else None
    warnings = [w] if meta and (w := list_service.blind_write_warning(meta)) else []
    if operation == 'void' and (not ctx.reason or not ctx.reason.strip()):
        raise BookflowError('E_REASON_REQUIRED')
    if operation == 'void' and len(ctx.reason.strip()) > 140:
        raise invalid('reason', 'must be at most 140 characters')
    if operation == 'update' and old_h['status'] == 'voided':
        raise invalid('journal', 'a voided journal cannot be updated')
    if operation == 'void' and old_h['status'] == 'voided':
        return Plan(JournalWriteOutput(**summary(old_h, old_r), revision=revision_output(s, old_r), changed=False, warnings=warnings), {'input': inp, 'operation': operation, 'changed': False})
    date = old_r['date'] if operation == 'void' else (inp.date or old_r['date'] if old_r else inp.date)
    open_dates(s, [date] + ([old_r['date']] if old_r else []))
    created = lambda: dict(id=new_id(), created_at=at, created_by=s.actor.id, created_via=ctx.interface.value)
    at = clock.now_iso()
    company_info = dict(s.company.conn.execute(sa.select(c.company_info)).mappings().one())
    event = new_id()
    pending = {k: [] for k in TABLES}
    h = dict(old_h) if old_h else dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), type='journal_entry', status='posted',
        voided_at=None, voided_by=None, void_reason=None, void_posting_batch_id=None)
    sequence = None
    old_lines = rows(s, c.document_lines, c.document_lines.c.revision_id == old_r['id'], order=c.document_lines.c.position) if old_r else []
    current_batch = rows(s, c.posting_batches, c.posting_batches.c.revision_id == old_r['id'], c.posting_batches.c.kind != 'reversal')[0] if old_r else None
    if operation != 'void':
        number, sequence = allocate(s, inp.number if inp.number is not None else (old_h['number'] if old_h else None), h['id'])
        currency = old_r['currency'] if old_r else company_info['home_currency']
        entered = inp.lines if inp.lines is not None else [existing_input(l) for l in old_lines]
        prior = {l['line_id']: l for l in old_lines}
        seen, values = set(), []
        for line in entered:
            key = line.line_id.upper() if line.line_id and is_ulid(line.line_id) else line.line_id
            if key is not None and (key not in prior or key in seen):
                raise invalid('line_id', 'must be a unique current line identity owned by this journal; retired identities cannot return')
            seen.add(key) if key else None
            old = prior.get(key)
            values.append((key, line_values(s, line, currency, old, getattr(inp, 'refresh_defaults', False))))
        debit = checked_sum((v['amount_minor_units'] for _, v in values if v['side'] == 'debit'), 'debits')
        credit = checked_sum((v['amount_minor_units'] for _, v in values if v['side'] == 'credit'), 'credits')
        if debit != credit:
            raise BookflowError('E_UNBALANCED_ENTRY', details={'debit_minor_units': debit, 'credit_minor_units': credit, 'currency': currency})
        memo = inp.memo if 'memo' in inp.model_fields_set or not old_r else old_r['memo']
        issuer = json.dumps({k: v for k, v in company_info.items() if k in ('id', 'legal_name', 'display_name', 'home_currency') or k.startswith(('address_', 'legal_address_', 'ship_address_'))}, sort_keys=True)
        if old_r and not inp.refresh_defaults:
            issuer = old_r['issuer_snapshot']
        unchanged = old_r and (date, number, memo, issuer) == (old_r['date'], old_r['number'], old_r['memo'], old_r['issuer_snapshot']) and len(values) == len(old_lines) and all(
            key == old['line_id'] and all(value[k] == old[k] for k in value) for (key, value), old in zip(values, old_lines))
        if unchanged:
            return Plan(JournalWriteOutput(**summary(old_h, old_r), revision=revision_output(s, old_r), changed=False, warnings=warnings), {'input': inp, 'operation': operation, 'changed': False})
        r = dict(**created(), transaction_id=h['id'], revision_number=old_r['revision_number'] + 1 if old_r else 1,
            supersedes_revision_id=old_r['id'] if old_r else None, date=date, number=number, name_type=None, name_id=None,
            memo=memo, total_minor_units=debit, currency=currency, issuer_snapshot=issuer, custom_fields_snapshot='{}', audit_event_id=event)
        pending['transaction_revisions'].append(r)
        h.update(number=number, current_revision_id=r['id'])
        for position, (key, value) in enumerate(values, 1):
            if key is None:
                ident = dict(**created(), transaction_id=h['id'])
                pending['document_line_identities'].append(ident)
                key = ident['id']
            pending['document_lines'].append(dict(**created(), transaction_id=h['id'], revision_id=r['id'], line_id=key, position=position, kind='journal', **value))
    else:
        r = old_r
    if old_h:
        h.update(version=old_h['version'] + 1, updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
        inverse = dict(**created(), transaction_id=h['id'], revision_id=old_r['id'], kind='reversal',
            effective_date=current_batch['effective_date'], reverses_batch_id=current_batch['id'], replaces_batch_id=None, audit_event_id=event)
        pending['posting_batches'].append(inverse)
        old_legs = rows(s, c.posting_lines, c.posting_lines.c.batch_id == current_batch['id'], order=c.posting_lines.c.line_no)
        for leg in old_legs:
            new = dict(leg, **created(), batch_id=inverse['id'], debit_minor_units=leg['credit_minor_units'], credit_minor_units=leg['debit_minor_units'], reversed_line_id=leg['id'])
            pending['posting_lines'].append(new)
            for source in rows(s, c.posting_line_sources, c.posting_line_sources.c.posting_line_id == leg['id']):
                pending['posting_line_sources'].append(dict(source, **created(), posting_line_id=new['id'], reversed_source_id=source['id']))
        if operation == 'void':
            h.update(status='voided', voided_at=at, voided_by=s.actor.id, void_reason=ctx.reason.strip(), void_posting_batch_id=inverse['id'])
    if operation != 'void':
        batch = dict(**created(), transaction_id=h['id'], revision_id=r['id'], kind='replacement' if old_h else 'original',
            effective_date=r['date'], reverses_batch_id=None, replaces_batch_id=current_batch['id'] if current_batch else None, audit_event_id=event)
        pending['posting_batches'].append(batch)
        for line in pending['document_lines']:
            leg = dict(**created(), transaction_id=h['id'], batch_id=batch['id'], line_no=line['position'],
                **{k: line[k] for k in DIMENSIONS}, debit_minor_units=line['amount_minor_units'] if line['side'] == 'debit' else 0,
                credit_minor_units=line['amount_minor_units'] if line['side'] == 'credit' else 0, reversed_line_id=None)
            pending['posting_lines'].append(leg)
            pending['posting_line_sources'].append(dict(**created(), transaction_id=h['id'], posting_line_id=leg['id'],
                revision_id=r['id'], document_line_id=line['id'], amount_minor_units=line['amount_minor_units'], currency=line['currency'], reversed_source_id=None))
    validate_pending_aggregate(s, h, pending)
    view_pending = pending if operation != 'void' else {k: v for k, v in pending.items() if k != 'document_lines'}
    output = JournalWriteOutput(**summary(h, r), revision=revision_output(s, r, view_pending), warnings=warnings,
        changed_fields=['journal'] if old_h else [])
    return Plan(output, dict(input=inp, operation=operation, changed=True, header=h, before=old_h,
                            pending=pending, sequence=sequence, event=event))


def validate_pending_aggregate(s, header, pending):
    """Independently verify generated accounting effects before any audit or row write.

    Stored reversal targets are checked too: copying corrupt attribution must never
    turn it into an apparently valid correction. This validator does not resolve
    active masters, because exact reversals retain inactive historical references.
    """
    from collections import defaultdict

    currency = s.company.conn.execute(sa.select(c.company_info.c.home_currency)).scalar_one()
    document_id = header['id']

    def require(condition, problem):
        if not condition:
            raise BookflowError('E_INTERNAL', message='Invalid journal aggregate: ' + problem)

    def index(values):
        result = {value['id']: value for value in values}
        require(len(result) == len(values), 'duplicate generated identity')
        require(all(value['transaction_id'] == document_id for value in values), 'cross-document ownership')
        return result

    indexed = {table: index(pending[table]) for table in TABLES}
    batches = indexed['posting_batches']
    legs = indexed['posting_lines']
    sources = indexed['posting_line_sources']
    require(bool(batches), 'missing accounting effects')
    require(all(l['batch_id'] in batches for l in legs.values()), 'orphan posting line')
    require(all(x['posting_line_id'] in legs for x in sources.values()), 'orphan source')
    sources_by_line = defaultdict(list)
    legs_by_batch = defaultdict(list)
    for source in sources.values():
        sources_by_line[source['posting_line_id']].append(source)
    for leg in legs.values():
        legs_by_batch[leg['batch_id']].append(leg)
    documents = dict(indexed['document_lines'])

    def source_document(source):
        key = source['document_line_id']
        if key not in documents:
            found = rows(s, c.document_lines, c.document_lines.c.id == key,
                         c.document_lines.c.transaction_id == document_id)
            require(len(found) == 1, 'missing source document line')
            documents[key] = found[0]
        doc = documents[key]
        require(doc['transaction_id'] == source['transaction_id'] == document_id
                and doc['revision_id'] == source['revision_id']
                and doc['currency'] == source['currency'] == currency, 'source ownership or currency differs')
        return doc

    def check_effect(batch, own_legs, own_sources):
        require(batch['transaction_id'] == document_id, 'batch belongs to another document')
        require(2 <= len(own_legs) <= 200, 'batch must have two through 200 posting lines')
        require(sorted(l['line_no'] for l in own_legs) == list(range(1, len(own_legs) + 1)), 'invalid posting positions')
        debit = credit = 0
        for leg in own_legs:
            require(leg['transaction_id'] == document_id and leg['currency'] == currency, 'posting ownership or home currency differs')
            d = checked_sum([leg['debit_minor_units']], 'debit_minor_units')
            cr = checked_sum([leg['credit_minor_units']], 'credit_minor_units')
            require((d > 0 and cr == 0) or (cr > 0 and d == 0), 'posting line must have one positive side')
            attributed = own_sources.get(leg['id'], [])
            require(bool(attributed), 'posting line has no source')
            for source in attributed:
                require(source['posting_line_id'] == leg['id'], 'source belongs to another posting line')
                amount = checked_sum([source['amount_minor_units']], 'source.amount_minor_units')
                require(amount > 0, 'source amount must be positive')
                source_document(source)
            require(checked_sum((x['amount_minor_units'] for x in attributed), 'source_total') == d + cr,
                    'source allocations do not equal posting amount')
            debit += d
            credit += cr
        checked_sum([debit], 'batch.debit_total')
        checked_sum([credit], 'batch.credit_total')
        if debit != credit:
            raise BookflowError('E_UNBALANCED_ENTRY', details={
                'debit_minor_units': debit, 'credit_minor_units': credit, 'currency': currency})
        return debit

    for batch in batches.values():
        own_legs = legs_by_batch[batch['id']]
        total = check_effect(batch, own_legs, sources_by_line)
        if batch['kind'] != 'reversal':
            require(batch['kind'] in ('original', 'replacement'), 'unknown business batch kind')
            rev = indexed['transaction_revisions'].get(batch['revision_id'])
            require(rev is not None, 'business batch has no new revision')
            require(rev['currency'] == currency and rev['total_minor_units'] == total
                    and rev['date'] == batch['effective_date'], 'revision total, currency or date differs')
            entered = [l for l in indexed['document_lines'].values() if l['revision_id'] == rev['id']]
            require(len(entered) == len(own_legs), 'entered and posting line counts differ')
            used = []
            for leg in own_legs:
                attributed = sources_by_line[leg['id']]
                require(len(attributed) == 1, 'domestic business posting must have exactly one entered source')
                source = attributed[0]
                doc = source_document(source)
                require(doc['id'] in indexed['document_lines'] and doc['revision_id'] == rev['id'], 'business source is not in the new revision')
                require(source['reversed_source_id'] is None and leg['reversed_line_id'] is None, 'business posting has reversal links')
                require(doc['side'] in ('debit', 'credit') and doc['amount_minor_units'] > 0, 'invalid entered side or amount')
                require(doc['amount_minor_units'] == leg['debit_minor_units'] + leg['credit_minor_units']
                        and (doc['side'] == 'debit') == (leg['debit_minor_units'] > 0)
                        and doc['position'] == leg['line_no']
                        and all(doc[k] == leg[k] for k in DIMENSIONS), 'posting differs from entered line')
                used.append(doc['id'])
            require(len(set(used)) == len(entered), 'entered-source mapping is not a bijection')
            continue

        targets = rows(s, c.posting_batches, c.posting_batches.c.id == batch['reverses_batch_id'],
                       c.posting_batches.c.transaction_id == document_id)
        require(len(targets) == 1, 'missing reversal target')
        target = targets[0]
        require(target['kind'] in ('original', 'replacement')
                and batch['effective_date'] == target['effective_date']
                and batch['revision_id'] == target['revision_id'], 'reversal changes target kind, date or revision')
        old_legs = rows(s, c.posting_lines, c.posting_lines.c.batch_id == target['id'])
        old_by_id = index(old_legs)
        old_sources = rows(s, c.posting_line_sources,
                           c.posting_line_sources.c.posting_line_id.in_(list(old_by_id)))
        old_source_ids = index(old_sources)
        old_by_line = defaultdict(list)
        for source in old_sources:
            old_by_line[source['posting_line_id']].append(source)
        check_effect(target, old_legs, old_by_line)
        reversed_legs, reversed_sources = [], []
        for leg in own_legs:
            original = old_by_id.get(leg['reversed_line_id'])
            require(original is not None, 'reversal line has no original')
            require(leg['debit_minor_units'] == original['credit_minor_units']
                    and leg['credit_minor_units'] == original['debit_minor_units']
                    and leg['line_no'] == original['line_no']
                    and all(leg[k] == original[k] for k in DIMENSIONS), 'reversal line is not an exact inverse')
            reversed_legs.append(original['id'])
            for source in sources_by_line[leg['id']]:
                old_source = old_source_ids.get(source['reversed_source_id'])
                require(old_source is not None and old_source['posting_line_id'] == original['id'], 'reversal source has no corresponding original')
                require(all(source[k] == old_source[k] for k in (
                    'transaction_id', 'revision_id', 'document_line_id', 'amount_minor_units', 'currency',
                )), 'reversal changed source attribution')
                reversed_sources.append(old_source['id'])
        require(len(reversed_legs) == len(set(reversed_legs)) == len(old_by_id)
                and set(reversed_legs) == set(old_by_id), 'reversal lines are not a bijection')
        require(len(reversed_sources) == len(set(reversed_sources)) == len(old_source_ids)
                and set(reversed_sources) == set(old_source_ids), 'reversal sources are not a bijection')


def apply(plan, ctx, s):
    # Rebuild under BEGIN IMMEDIATE: references, dates, versions and allocation are decisive here.
    fresh = prepare(s, ctx, plan.data['input'], plan.data['operation'])
    return persist_prepared(fresh, ctx, s, command_name='journal ' + plan.data['operation'])


def persist_prepared(fresh, ctx, s, *, command_name):
    """Persist a writer-validated aggregate under its outer command's single audit event."""
    if not fresh.data['changed']:
        return Applied(fresh.preview, [], 'no change')
    d = fresh.data
    h, old, pending = d['header'], d['before'], d['pending']
    validate_pending_aggregate(s, h, pending)
    touched = [Touched('transaction', h['id'], 'update' if old else 'create', old['version'] if old else None,
                       h['version'], h, old, db='company')]
    for table, kind in zip(TABLES, TYPES):
        touched.extend(Touched(kind, row['id'], 'create', None, 1, decoded(row), db='company') for row in pending[table])
    summary_text = f"{d['operation']} journal {h['number']}"
    audit.write_event_to(s.company, ctx, command_name, summary_text, touched,
        actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=getattr(s, 'directive_code', None), event_id=d['event'])
    if old:
        s.company.conn.execute(c.transactions.update().where(c.transactions.c.id == h['id']).values(**h))
    else:
        s.company.conn.execute(c.transactions.insert().values(**h))
    for table in TABLES:
        if pending[table]:
            s.company.conn.execute(getattr(c, table).insert(), pending[table])
    if d['sequence']:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(c.sequences).values(**d['sequence'])
        s.company.conn.execute(stmt.on_conflict_do_update(index_elements=['name'], set_=d['sequence']))
    return Applied(fresh.preview, touched, summary_text, audited=True)


def page(s, ctx, inp, history=False):
    from bookflow.company.query import page_state, continuation
    # page_state's generic model_dump fingerprint binds every journal filter and limit.
    class Contract:
        cursor = inp.cursor
        query = getattr(inp, 'query', None)

        def model_dump(self, **kwargs):
            return inp.model_dump(**kwargs)

    state = page_state(s, 'journal history' if history else 'journal query', Contract(), ctx.on_behalf_of)
    if history:
        h = resolve(s, inp.journal)
        t = c.transaction_revisions
        q = sa.select(*(column for column in t.c if not column.name.endswith('_snapshot'))).where(t.c.transaction_id == h['id']).order_by(t.c.revision_number)
    else:
        t, r = c.transactions, c.transaction_revisions
        q = sa.select(t).join(r, r.c.id == t.c.current_revision_id)
        if inp.status:
            q = q.where(t.c.status == inp.status)
        if inp.date_from:
            q = q.where(r.c.date >= inp.date_from)
        if inp.date_to:
            q = q.where(r.c.date <= inp.date_to)
        if inp.query:
            q = q.where(sa.or_(t.c.number.contains(inp.query, autoescape=True), r.c.memo.contains(inp.query, autoescape=True)))
        q = q.order_by(r.c.date, t.c.id)
    found = [dict(r) for r in s.company.conn.execute(q.offset(state.offset).limit(inp.limit + 1)).mappings()]
    more, found = len(found) > inp.limit, found[:inp.limit]
    shared = dict(count=len(found), has_more=more, next_cursor=continuation(state, len(found), more), audit_watermark=state.sequence)
    if history:
        return JournalHistoryOutput(**{k: h[k] for k in ('id', 'version', 'current_revision_id', 'number', 'status')},
            items=[revision_output(s, r, summary_only=True) for r in found], **shared)
    return JournalPageOutput(items=[JournalSummaryOutput(**summary(h, revision(s, h))) for h in found], **shared)
