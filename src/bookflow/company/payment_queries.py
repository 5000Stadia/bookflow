"""Exact current settlement capacity and authenticated read-only page recipes."""
import base64
import hashlib
import hmac
import json

import sqlalchemy as sa

from bookflow.company import schema as c, sales, document_effects as effects
from bookflow.company.ledger_schema import SETTLEABLE_RECEIVABLE_TYPES
from bookflow.company.payment_authority import authorize
from bookflow.core.errors import BookflowError
from bookflow.company.ledger_reports import _cursor_key


class _CrossJoin(sa.sql.selectable.Join):
    inherit_cache = True


from sqlalchemy.ext.compiler import compiles


@compiles(_CrossJoin, 'sqlite')
def _compile_cross_join(join, compiler, **kw):
    return (compiler.process(join.left, asfrom=True, **{k:v for k,v in kw.items() if k != 'asfrom'})
        + ' CROSS JOIN ' + compiler.process(join.right, asfrom=True, **{k:v for k,v in kw.items() if k != 'asfrom'})
        + ' ON ' + compiler.process(join.onclause, **kw))


def indexed_source(table, index_name, *names, expression=None):
    """Private fixed owned sources; callers never supply user identifiers."""
    assert index_name.startswith('ix_co17_') and any(i.name == index_name for i in table.indexes)
    columns = [table.c[name] for name in names]
    sql = 'SELECT ' + ', '.join(names)
    if expression is not None:
        from bookflow.company.read_indexes import PAYER_LABEL_SQL
        assert expression == 'payer_label'
        sql += ', ' + PAYER_LABEL_SQL + ' AS payer_label'
        columns.append(sa.column('payer_label', sa.Text()))
    sql += ' FROM ' + table.name + ' INDEXED BY ' + index_name
    return sa.text(sql).columns(*columns).subquery(table.name)


def cross_join(left, right, onclause):
    return _CrossJoin(left, right, onclause)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def active_applications(s, *, invoice=None, payment=None):
    app, inverse = c.applications, c.applications.alias('inverse')
    query = sa.select(app).where(app.c.kind == 'apply', ~sa.exists(sa.select(inverse.c.id).where(
        inverse.c.reverses_application_id == app.c.id)))
    if invoice:
        query = query.where(app.c.paid_transaction_id == invoice)
    if payment:
        query = query.where(app.c.paying_transaction_id == payment)
    return [dict(row) for row in s.company.conn.execute(query.order_by(app.c.effective_date, app.c.id)).mappings()]


def invoice_facts(s, selector, *, write=False):
    """Current settlement capacity of one receivable document a customer's money can settle.

    Named for the invoice because that is what nearly every caller is holding, but a statement
    charge is the same shape to everything downstream: a `sales_profiles` row naming the customer
    and the control account, commercial lines carrying their own posting attribution, and a gross
    the active applications subtract from. `SETTLEABLE_RECEIVABLE_TYPES` is the whole difference,
    which is why it is read here rather than written out.
    """
    header = sales.resolve(s, selector, SETTLEABLE_RECEIVABLE_TYPES)
    authorize(s, [header['id']], write=write)
    revision = effects.rows(s, c.transaction_revisions, c.transaction_revisions.c.id == header['current_revision_id'])[0]
    profile = sales.profile_row(s, revision)
    applications = active_applications(s, invoice=header['id'])
    applied = sum(row['amount_minor_units'] for row in applications)
    gross = revision['total_minor_units'] if header['status'] == 'posted' else 0
    return dict(header=header, revision=revision, profile=profile, applications=applications,
                gross=gross, applied=applied, due=gross - applied)


def invoice_current(s, selector):
    facts = invoice_facts(s, selector)
    header, revision = facts['header'], facts['revision']
    return _invoice_current_values(header, revision, facts['applied'])


def _invoice_current_values(header, revision, applied):
    gross = revision['total_minor_units'] if header['status'] == 'posted' else 0
    due = gross - applied
    return dict(invoice_id=header['id'], version=header['version'], revision_id=revision['id'],
                gross_minor_units=gross, applied_minor_units=applied, due_minor_units=due,
                currency=revision['currency'], status=('voided' if header['status'] == 'voided' else
                    'paid' if due == 0 else 'partial' if applied else 'unpaid'))


def invoice_currents(s, headers, revisions):
    """Current settlement for one already-selected sales page, with full authority."""
    if not headers:
        return {}
    ids = [header['id'] for header in headers]
    authorize(s, ids)
    app, inverse = c.applications, c.applications.alias('page_inverse')
    amounts = {identifier: 0 for identifier in ids}
    # Python integers preserve the single-record projection's exact arithmetic;
    # the query is restricted to page identities, not all company applications.
    for identifier, amount in s.company.conn.execute(sa.select(app.c.paid_transaction_id,
            app.c.amount_minor_units).where(app.c.paid_transaction_id.in_(ids), app.c.kind == 'apply',
            ~sa.exists(sa.select(inverse.c.id).where(inverse.c.reverses_application_id == app.c.id)))):
        amounts[identifier] += amount
    return {header['id']: _invoice_current_values(header, revisions[header['current_revision_id']], amounts[header['id']])
        for header in headers}


def payer_balances(s, customer_id):
    """CP02: actual payer/family net AR, authorized over the contributing graph."""
    from bookflow.company import customer_balances
    from bookflow.core.money import Money
    from bookflow.core.exact import _require_i64
    from bookflow.company.payment_authority import authorize_query
    family = sa.select(c.customers.c.id).where(c.customers.c.id == customer_id).cte('balance_family', recursive=True)
    family = family.union(sa.select(c.customers.c.id).join(family, c.customers.c.parent_id == family.c.id))
    transactions = sa.select(c.posting_lines.c.transaction_id).join(c.accounts,
        c.accounts.c.id == c.posting_lines.c.account_id).where(c.accounts.c.type == 'accounts_receivable',
        c.posting_lines.c.name_type == 'customer', c.posting_lines.c.name_id.in_(sa.select(family.c.id))).distinct()
    authorize_query(s, transactions)
    currency = s.company_info_row['home_currency']
    # One lossless posting scan supplies both CP02 projections. Reuse the owning
    # arbitrary-intermediate aggregate and checked Money boundary, never SQLite
    # SUM/REAL or a stored running balance.
    customer_balances.register_functions(s.company)
    # Group losslessly; do not reject a party intermediate before cancellation.
    party_nets = {party: int(amount or '0') for party, amount in s.company.raw.execute("""
        WITH RECURSIVE balance_family(id) AS (
            SELECT id FROM customers WHERE id=? UNION
            SELECT child.id FROM customers AS child JOIN balance_family AS family ON child.parent_id=family.id)
        SELECT name_id,bookflow_sum_int(debit_minor_units-credit_minor_units)
        FROM posting_lines INDEXED BY ix_co17_posting_party_ar
        WHERE name_type='customer'
            AND account_id IN (SELECT id FROM accounts WHERE type='accounts_receivable')
            AND name_id IN (SELECT id FROM balance_family)
        GROUP BY name_id
        """, (customer_id,)).fetchall()}
    payer, family_net = party_nets.get(customer_id, 0), sum(party_nets.values())
    return dict(customer_id=customer_id, payer_balance=Money(_require_i64(int(payer or '0'), field='current_balance'), currency).to_dict(),
        family_balance=Money(_require_i64(int(family_net or '0'), field='family_balance'), currency).to_dict())


def payment_facts(s, selector, *, write=False):
    header = sales.resolve(s, selector, 'payment')
    authorize(s, [header['id']], write=write)
    revision = effects.rows(s, c.transaction_revisions, c.transaction_revisions.c.id == header['current_revision_id'])[0]
    profile = effects.rows(s, c.payment_profiles, c.payment_profiles.c.revision_id == revision['id'])[0]
    components = effects.rows(s, c.payment_components, c.payment_components.c.revision_id == revision['id'])
    keys = {row['id']: row for row in effects.rows(s, c.payment_component_keys,
            c.payment_component_keys.c.transaction_id == header['id'])}
    applications = active_applications(s, payment=header['id'])
    available = {key: 0 for key in keys}
    if header['status'] == 'posted':
        available.update({row['component_key_id']: row['amount_minor_units'] for row in components})
    for app in applications:
        available[app['source_component_key_id']] -= app['amount_minor_units']
    return dict(header=header, revision=revision, profile=profile, components=components,
                keys=keys, applications=applications, available=available)


def page(s, noun, inp, items, *, facts=None):
    """Bound page delivery, not receipt size; bind complete relevant facts.

The recipe excludes unrelated draft audit writes. Current authorization must be
applied before this function, on EVERY page, including all graph members.
"""
    contract = inp.model_dump(mode='json', exclude={'cursor'})
    fp = digest([s.company_row['id'], noun, contract, facts if facts is not None else items])
    offset = 0
    domain = b'bookflow.payment.page.v1\0'
    key = _cursor_key(s.company)
    if inp.cursor:
        try:
            payload, signature = inp.cursor.split('.')
            raw = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
            mac = base64.urlsafe_b64decode(signature + '=' * (-len(signature) % 4))
            if not hmac.compare_digest(mac, hmac.digest(key, domain + raw, 'sha256')):
                raise ValueError()
            saved = json.loads(raw)
            if not isinstance(saved, dict) or set(saved) != {'v', 'fp', 'offset'} or type(saved['v']) is not int or saved['v'] != 1:
                raise ValueError()
            if saved['fp'] != fp:
                raise BookflowError('E_QUERY_STALE')
            offset = saved['offset']
            if type(offset) is not int or not 0 <= offset <= len(items):
                raise ValueError()
        except BookflowError:
            raise
        except (ValueError, TypeError, KeyError):
            raise BookflowError('E_VALIDATION', details={'fields': [{'field': 'cursor', 'problem': 'invalid payment continuation'}]}) from None
    selected = items[offset:offset + inp.limit]
    next_cursor = None
    if offset + len(selected) < len(items):
        raw = canonical(dict(v=1, fp=fp, offset=offset + len(selected))).encode()
        encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
        next_cursor = encode(raw) + '.' + encode(hmac.digest(key, domain + raw, 'sha256'))
    return dict(items=selected, total_count=len(items), next_cursor=next_cursor, facts_fingerprint=fp)


def sql_page(s, noun, inp, statement, *, facts=None, known_count=None, count_with_page=False):
    """SQL delivery; ordinary queries pin audit, preparation pins relevant facts."""
    if facts is None:
        facts = s.company.conn.execute(sa.select(sa.func.coalesce(sa.func.max(c.audit_events.c.seq), 0))).scalar_one()
    fp = digest([s.company_row['id'], noun, inp.model_dump(mode='json', exclude={'cursor'}), facts])
    domain, key = b'bookflow.payment.query.v1\0', _cursor_key(s.company)
    offset = 0
    if inp.cursor:
        try:
            body, signature = inp.cursor.split('.')
            raw = base64.b64decode(body + '=' * (-len(body) % 4), altchars=b'-_', validate=True)
            mac = base64.b64decode(signature + '=' * (-len(signature) % 4), altchars=b'-_', validate=True)
            if not hmac.compare_digest(mac, hmac.digest(key, domain + raw, 'sha256')):
                raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {'v', 'fp', 'offset'} or value['v'] != 1 or type(value['offset']) is not int or value['offset'] < 0:
                raise ValueError()
            if value['fp'] != fp:
                raise BookflowError('E_QUERY_STALE')
            offset = value['offset']
        except (ValueError, TypeError, KeyError):
            raise BookflowError('E_VALIDATION', details={'field': 'cursor'}) from None
    count_statement = sa.select(sa.func.count()).select_from(statement.order_by(None).subquery())
    if count_with_page and known_count is None:
        # Expensive text/capacity predicates are evaluated once, before paging.
        # Keep the ordinary two-query path for cheap/indexed queries, where a
        # window would unnecessarily materialize the complete selected relation.
        projection = statement.add_columns(sa.func.count().over().label('__page_total'))
        rows = [dict(row) for row in s.company.conn.execute(projection.offset(offset).limit(inp.limit)).mappings()]
        count = rows[0]['__page_total'] if rows else (0 if offset == 0 else s.company.conn.execute(count_statement).scalar_one())
        for row in rows:
            del row['__page_total']
    else:
        count = known_count if known_count is not None else s.company.conn.execute(count_statement).scalar_one()
        rows = [dict(row) for row in s.company.conn.execute(statement.offset(offset).limit(inp.limit)).mappings()]
    cursor = None
    if offset + len(rows) < count:
        raw = canonical(dict(v=1, fp=fp, offset=offset + len(rows))).encode()
        encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
        cursor = encode(raw) + '.' + encode(hmac.digest(key, domain + raw, 'sha256'))
    return dict(items=rows, total_count=count, next_cursor=cursor, facts_fingerprint=fp)
