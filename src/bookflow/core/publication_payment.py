"""Owned payment disclosure roots for pure hosted result publication.

Roots retain existing business identities, not pages or planner closures. Current
requirements come exclusively from the reviewed company payment authority helpers.
"""
from pydantic import BaseModel
from bookflow.core.errors import BookflowError
import sqlalchemy as sa

PAYMENT_COMMANDS = frozenset('''application history
application show
invoice settlement
payment apply
payment calculate
payment history
payment invoices
payment operation items
payment operation show
payment preview items
payment query
payment receive
payment selection clear
payment selection create
payment selection items
payment selection query
payment selection show
payment selection update
payment settlement
payment settlement changes
payment show
payment suggest
payment unapply
payment update
payment void'''.splitlines())
RELATED_COMMANDS = frozenset({'invoice show', 'invoice update', 'invoice history', 'invoice query',
                              'audit list', 'audit show', 'audit tail', 'activity',
                              'note add', 'note show', 'note edit', 'note list',
                              'attachment add', 'attachment get', 'attachment link',
                              'attachment unlink', 'attachment list'})


def captures(cmd):
    return cmd.name in PAYMENT_COMMANDS | RELATED_COMMANDS


def capture(cmd, inp, s, result, *, dry_run=False):
    from bookflow.company import schema as c, sales, payment_operations, payment_dependencies
    from bookflow.company import sales_defaults
    roots = set()
    write = (cmd.is_write and (cmd.name in PAYMENT_COMMANDS or cmd.name == 'invoice update')) or cmd.name == 'payment preview items'

    def add(kind, identifier):
        if identifier:
            roots.add((kind, identifier, write))

    def transaction(selector, kind, *, invoice_correction=False):
        if selector:
            root_kind = 'transaction'
            if kind == 'invoice' and (invoice_correction or cmd.name in {'invoice settlement', 'invoice update', 'payment settlement changes'}):
                root_kind = 'invoice_settlement'
            elif kind == 'payment' and cmd.name == 'payment history':
                root_kind = 'payment_history'
            add(root_kind, sales.resolve(s, selector, kind)['id'])

    def operation(key):
        if key:
            row = payment_operations.find(s, key)
            if row is not None:
                add('payment_operation', row['id'])

    def input_roots(model):
        # Traverse owning typed models only. A custom field called payment,
        # invoice or operation_key remains user data and cannot confer a root.
        if not isinstance(model, BaseModel):
            return
        for name in type(model).model_fields:
            value = getattr(model, name)
            if name in {'payment', 'invoice'} and isinstance(value, str):
                from bookflow.company.sales_models import InvoiceUpdateInput
                transaction(value, name, invoice_correction=isinstance(model, InvoiceUpdateInput))
            elif name == 'selection' and isinstance(value, str):
                add('payment_selection', value)
            elif name == 'application' and isinstance(value, str):
                add('application', value)
            elif name == 'operation_key':
                operation(value)
            elif name == 'guard' and isinstance(value, str):
                saved = payment_dependencies.decode(s, value)
                transaction(saved['owner_id'], saved['owner_type'])
            elif isinstance(value, BaseModel):
                input_roots(value)
            elif isinstance(value, list):
                for item in value:
                    input_roots(item)

    if cmd.name in PAYMENT_COMMANDS or cmd.name.startswith('invoice '):
        input_roots(inp)
    if cmd.name.startswith('payment selection '):
        if cmd.name == 'payment selection query':
            for row in result['items']:
                add('payment_selection', row['id'])
        elif not (dry_run and cmd.name == 'payment selection create'):
            add('payment_selection', result.get('id'))
    elif cmd.name in {'payment query', 'invoice query'}:
        for row in result['items']:
            add('transaction', row['id'])
    elif cmd.name in {'payment receive', 'payment apply', 'payment unapply', 'payment void', 'payment update', 'payment show'}:
        # A preview of a new receipt has no durable identity. Existing-payment
        # previews retain their input owner even if output hides prospective IDs.
        if not (dry_run and cmd.name == 'payment receive'):
            add('transaction', result.get('id'))
    if cmd.name in {'payment query', 'payment selection query', 'payment invoices', 'payment suggest'}:
        # These commands expose counts/aggregates after conditional filtering.
        # Retain the shared permission projection, not merely the returned page
        # and not an unbounded collection of every contributing record ID.
        roots.add(('work_access', work_access(s), False))
    if cmd.name in {'payment invoices', 'payment suggest'}:
        customer = getattr(inp, 'customer', None)
        if customer:
            add('payer', sales_defaults._row(s.company, 'customer', customer, active=False)['id'])
        elif getattr(inp, 'payment', None):
            header = sales.resolve(s, inp.payment, 'payment')
            payer = s.company.conn.execute(sa.select(c.payment_profiles.c.payer_id).where(
                c.payment_profiles.c.revision_id == header['current_revision_id'])).scalar_one()
            add('payer', payer)
    if cmd.name == 'activity' or cmd.noun in {'note', 'attachment'}:
        kind, identifier = getattr(inp, 'record_type', None), getattr(inp, 'record_id', None)
        if kind and identifier:
            if kind == 'audit_entry':
                event = s.company.conn.execute(sa.select(c.audit_entries.c.event_id).where(
                    c.audit_entries.c.id == identifier.upper())).scalar_one()
                add('audit_event', event)
            else:
                add(kind, identifier.upper())
        if cmd.name == 'attachment unlink':
            add('attachment_link', inp.link.upper())
        for kind in ('note', 'attachment', 'attachment_link'):
            selector = getattr(inp, kind, None)
            if selector:
                add(kind, selector.upper())
        if not dry_run:
            for kind in ('note', 'attachment'):
                if isinstance(result.get(kind), dict):
                    add(kind, result[kind]['id'])
    if cmd.name in {'audit list', 'audit tail', 'activity'}:
        for row in result['items']:
            add('audit_event', row['event_id'] if cmd.name == 'activity' else row['id'])
    elif cmd.name == 'audit show':
        add('audit_event', result['id'])
    return tuple(sorted(roots))


def work_access(s):
    from bookflow.company import payment_authority
    try:
        payment_authority.require_resource(s, 'customer-work', 'member')
    except BookflowError as exc:
        if exc.code != 'E_PERMISSION':
            raise
        return False
    return True


def check(s, roots):
    from bookflow.company import payment_authority
    for kind, identifier, write in roots:
        if kind == 'work_access':
            if work_access(s) != identifier:
                raise BookflowError('E_PERMISSION', details={'reason': 'payment_projection_changed'})
        elif kind == 'audit_event':
            payment_authority.authorize_event(s, identifier)
        else:
            try:
                ids = payment_authority.disclosure_transactions(s.company, kind, identifier)
            except (ValueError, TypeError, KeyError, RecursionError):
                raise BookflowError('E_IO', details={'stage': 'publication', 'outcome': 'unknown',
                                                    'reason': 'invalid_authority_evidence'}) from None
            # Every returned dependency must resolve in this selected company.
            # Missing/foreign historical references cannot be treated as an
            # ordinary ledger-only graph merely because no billing edge exists.
            from bookflow.company import schema as c
            identifiers = sorted(ids)
            for offset in range(0, len(identifiers), 500):
                chunk = identifiers[offset:offset + 500]
                count = s.company.conn.execute(sa.select(sa.func.count()).select_from(c.transactions).where(
                    c.transactions.c.id.in_(chunk))).scalar_one()
                if count != len(chunk):
                    raise BookflowError('E_PERMISSION', details={'reason': 'unresolved_payment_evidence'})
            if ids or kind in payment_authority.PAYMENT_TARGETS or kind in {'payer', 'payment_history', 'invoice_settlement'}:
                # Annotation writes disclose the target under the core's read
                # policy; writing a note does not grant/require ledger posting.
                payment_authority.authorize(s, ids, write=write)
