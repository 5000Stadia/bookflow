"""A publication check asks each distinct requirement once, however many sales it names.

Every 64 KiB frame of a page is checked afresh before release. A page naming hundreds of
sales (an invoice billed from long-running work) used to evaluate the same two or three
permission requirements once per sale on every frame, so its release time grew with history.
The evidence per sale is still read and checked; only the repeated requirement is not.
"""
import pytest

from bookflow.company import payment_authority as authority
from bookflow.core.errors import BookflowError


def facts(ids, *, linked=(), unresolved=()):
    return {i: dict(id=i, linked_work=i in linked, unresolved_target=i in unresolved) for i in ids}


@pytest.fixture
def asked(monkeypatch):
    calls = []
    monkeypatch.setattr(authority, 'require_resource', lambda s, capability, role: calls.append((capability, role)))
    return calls


def test_requirements_are_asked_once_in_first_need_order(monkeypatch, asked):
    ids = [f't{i}' for i in range(450)]  # more than two facts batches
    read = []
    def load(s, batch):
        read.append(len(batch))
        return facts(batch, linked=set(ids[1::2]))
    monkeypatch.setattr(authority, '_publication_transaction_facts', load)
    authority.authorize_publication_transactions(None, [(i, False) for i in ids] + [(ids[0], True)])
    assert read == [200, 200, 51]  # every named sale's evidence is still read
    assert asked == [('ledger.read', 'member'), ('customer-work', 'member'), ('ledger.post', 'standard')]


def test_unresolved_evidence_and_refused_requirements_still_refuse(monkeypatch, asked):
    monkeypatch.setattr(authority, '_publication_transaction_facts', lambda s, batch: facts(batch, unresolved={'t3'}))
    with pytest.raises(BookflowError) as exc:
        authority.authorize_publication_transactions(None, [(f't{i}', False) for i in range(5)])
    assert exc.value.details['reason'] == 'unresolved_payment_evidence'
    monkeypatch.setattr(authority, '_publication_transaction_facts', lambda s, batch: facts(batch))
    def refuse(s, capability, role):
        raise BookflowError('E_PERMISSION', details={'capability': capability})
    monkeypatch.setattr(authority, 'require_resource', refuse)
    with pytest.raises(BookflowError) as exc:
        authority.authorize_publication_transactions(None, [('t1', False)])
    assert exc.value.details['capability'] == 'ledger.read'


def test_linked_work_is_found_by_index_not_by_scanning_every_allocation(client):
    # co0064: asking whether a sale is linked to work reads that sale's allocations only,
    # however many allocations the company has made.
    import sqlite3
    import sqlalchemy as sa
    from sqlalchemy.dialects import sqlite
    from bookflow.company import schema as c
    from tests.test_row8_journal import database_path
    statement = sa.select(c.transactions.c.id, authority.work_link_predicate(c.transactions.c.id)).where(
        c.transactions.c.id.in_(['a', 'b']))
    sql = str(statement.compile(dialect=sqlite.dialect(), compile_kwargs={'literal_binds': True}))
    with sqlite3.connect(database_path(client)) as db:
        plan = [row[3] for row in db.execute('EXPLAIN QUERY PLAN ' + sql)]
    touching = [step for step in plan if 'work_billing_allocations' in step]
    assert len(touching) == 2 and all(step.startswith('SEARCH') and 'ix_work_billing_allocation_transaction' in step
                                      for step in touching), plan
