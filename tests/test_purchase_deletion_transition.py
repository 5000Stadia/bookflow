"""Explicit setup-to-Delete catalog activation keeps existing memberships exact."""
from pathlib import Path
import sqlite3
import pytest
from bookflow.core.errors import BookflowError
from bookflow.hub import permission_deposit_deletion_catalog as current, permission_setup_catalog as previous
from tests.test_bill_item_lines import books
from tests.test_purchase_deletion import location
from tests.payment_raw_evidence import database


def test_existing_setup_requires_explicit_delete_catalog_transition(books,monkeypatch):
    client=books['client'];company=books['company']
    post=books['run']('check post',dict(account=books['bank'],date='2017-01-01',amount='1',
        expenses=[dict(account=books['freight'],amount='1')]),reason='Existing purchase')
    with monkeypatch.context() as historical:
        # Exact accepted phase2 descriptor through its existing public activation owner.
        historical.setattr(current,'CATALOG',previous.CATALOG)
        historical.setattr(current,'MANIFEST',previous.MANIFEST)
        historical.setattr(current,'catalog_bundle',previous.catalog_bundle)
        state=client.permission.show()
        client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
        member=next(x for x in client.membership.list(company=company)['items'] if x['scope_type']=='company' and x['scope_id']==company)
        client.membership.grant(user=member['user_id'],company=company,role=member['role'],expected_version=member['version'],
            grants=['transaction.check.delete'],denies=['ledger.post'])
    path=location(books);root=Path(client.data_root)
    with sqlite3.connect(root/'hub.db') as db:
        members=db.execute('SELECT * FROM memberships ORDER BY id').fetchall()
        assert db.execute('SELECT catalog_version FROM permission_state').fetchone()==(previous.CATALOG.version,)
    before=database(path)
    with pytest.raises(BookflowError) as unavailable:
        books['run']('check delete',dict(check=post['id'],expected_version=1),reason='Not activated yet')
    assert unavailable.value.code=='E_PERMISSION'
    assert database(path)==before
    state=client.permission.show()
    preview=client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'],dry_run=True)
    assert preview['changed'] and preview['generation']==state['generation']+1
    client.permission.activate(expected_generation=state['generation'],expected_catalog_sha256=state['catalog_sha256'])
    with sqlite3.connect(root/'hub.db') as db:
        assert db.execute('SELECT * FROM memberships ORDER BY id').fetchall()==members
        assert db.execute('SELECT catalog_version FROM permission_state').fetchone()==(current.CATALOG.version,)
    deleted=books['run']('check delete',dict(check=post['id'],expected_version=1),reason='Explicitly activated deletion')
    assert deleted['status']=='deleted'
