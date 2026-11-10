"""Payroll's own accounts and ledger entries, refused by name to every writer that is not payroll.

Payroll posts through the shared journal and check writers inside `writing()`. Outside it, the
three accounts payroll settings name while payroll is on (wages, employer expense, net pay) -- and any
account a posted paycheck captured for those roles -- refuse journal, register, check, import and memorized-entry
lines, transfers and merges; and a transaction payroll owns refuses journal, register and check
edits, voids and deletes. Item liability accounts stay open: the design lets an ordinary entry touch
them and shows it later as an unattributed difference (increment 3).

Writers that post without the journal line path (bills, sales documents, deposits) meet the same rule
in the `payroll_reserved_posting` trigger on `posting_lines`: a new, unreversed leg on a reserved
account is refused unless its transaction carries a payroll ledger link, which payroll writes before
its legs. Reversal legs of older entries are not refused, so history from before setup can still be voided.
"""
from contextlib import contextmanager
from contextvars import ContextVar

import sqlalchemy as sa

from bookflow.core.errors import BookflowError

_WRITING = ContextVar('payroll_writing', default=False)
_ROLES = ('wage_account', 'employer_tax_account', 'net_pay_account')
_CAPTURED = ('wage_account_id', 'employer_account_id', 'net_pay_account_id')


@contextmanager
def writing():
    token = _WRITING.set(True)
    try:
        yield
    finally:
        _WRITING.reset(token)


def active():
    return _WRITING.get()


def _table(conn, name):
    return conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).first() is not None


def reserved(conn, identifiers):
    """Which of these account ids payroll owns."""
    from bookflow.company import schema as c
    ids = [i for i in identifiers if i]
    if not ids or not _table(conn, 'payroll_settings'):
        return set()
    found = set()
    # Settings name accounts only while the layer is on; a posted paycheck's accounts stay reserved.
    row = conn.execute(sa.select(*(c.payroll_settings.c[r] for r in _ROLES)).where(
        c.payroll_settings.c.enabled.is_(True))).first()
    if row:
        found |= {value for value in row if value in ids}
    if _table(conn, 'payroll_paychecks'):
        p = c.payroll_paychecks
        for column in _CAPTURED:
            found |= set(conn.execute(sa.select(p.c[column]).where(p.c[column].in_(ids)).distinct()).scalars())
    return found


RESERVED_MESSAGE = 'Account is reserved for payroll'  # the payroll_reserved_posting trigger's text
_WRITERS = ('only `payroll post`, `payroll pay-net`, `payroll void`, `payroll void-payment` and `batch reverse` '
            'write to it.')


def reserved_refusal(name=None):
    what = f'"{name}" is a payroll account' if name else 'One of these accounts is a payroll account'
    return BookflowError('E_SYSTEM_RECORD', message=f'{what}: {_WRITERS}',
        details={'fields': [{'field': 'account', 'problem': 'reserved for payroll'}], 'owner': 'payroll'})


def require_unreserved_account(db, *identifiers):
    if active():
        return
    held = reserved(db.conn, identifiers)
    if held:
        from bookflow.company import schema as c
        raise reserved_refusal(db.conn.execute(sa.select(c.accounts.c.name).where(
            c.accounts.c.id == sorted(held)[0])).scalar())


def owned(conn, transaction_id):
    from bookflow.company import schema as c
    if not transaction_id or not _table(conn, 'payroll_ledger_links'):
        return None
    return conn.execute(sa.select(c.payroll_ledger_links.c.role).where(
        c.payroll_ledger_links.c.transaction_id == transaction_id).order_by(c.payroll_ledger_links.c.role).limit(1)).scalar()


def refusal(conn, transaction_id):
    """The refusal a non-payroll writer earns on a payroll-owned transaction, or None."""
    if active():
        return None
    role = owned(conn, transaction_id)
    if role is None:
        return None
    what = 'a paycheck' if role.startswith('paycheck') else "a paycheck's net-pay check"
    use = ('`payroll void` or `batch reverse`' if role.startswith('paycheck')
           else '`payroll void-payment` or `batch reverse`')
    return BookflowError('E_SYSTEM_RECORD', message=(
        f'This entry is {what}, owned by payroll; its components and year-to-date facts move with it. Use {use}.'),
        details={'fields': [{'field': 'journal', 'problem': 'owned by payroll'}], 'owner': 'payroll',
                 'transaction_id': transaction_id})


def require_unowned(s, transaction_id):
    error = refusal(s.company.conn, transaction_id)
    if error is not None:
        raise error
