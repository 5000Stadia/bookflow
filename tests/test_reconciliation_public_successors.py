"""A user can finish successive statements without losing history or version checks."""
import copy

import pytest
from bookflow import BookflowError
from bookflow.company import reconciliation_loading as loading
from tests.test_reconciliation_commands import (
    reconciled, driver, account, journal, pair, run, new_id, prepared,
)


def test_second_statement_keeps_first_capture_and_clears_only_new_money(reconciled, client, driver):
    bank = reconciled['bank']
    first = reconciled['done']
    with driver.session() as s:
        before = loading.load(s, bank)
    old_cert = copy.deepcopy(before.by('certificates')[first['certificate_id']])
    old_claims = copy.deepcopy(before.rows['claims'])
    equity = account(client, 'February equity', 'equity')
    journal(client, pair(bank, equity, '3.00'), date='2026-02-10')
    draft = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-02-28',
        ending_balance='15.50', opening_id=first['opening_id']))['draft']
    page = run(client, 'reconcile candidates', dict(draft=draft['id'], limit=200))
    entries = [dict(movement=row['movement'], group_fingerprint=row['group_fingerprint'],
                    action='mark') for row in page['items'] if row['eligible'] and not row['claimed']]
    assert len(entries) == 1
    marked = run(client, 'reconcile mark', dict(operation_key=new_id(), draft=draft['id'],
                    expected_version=1, entries=entries))['draft']
    request = dict(operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
                   **prepared(client, draft['id'], marked['version'])[0])
    retry_key = new_id()
    done = run(client, 'reconcile finish', request, idempotency_key=retry_key)
    with driver.session() as s:
        before_retry = loading.load(s, bank).rows
    replay = run(client, 'reconcile finish', request, idempotency_key=retry_key)
    assert replay.pop('idempotent_replay') is True
    assert replay == done
    with driver.session() as s:
        assert loading.load(s, bank).rows == before_retry
    assert done['totals']['beginning_balance'] == 1250
    assert done['totals']['selected_sum'] == 300
    assert done['totals']['ending_balance'] == 1550
    with driver.session() as s:
        after = loading.load(s, bank)
    assert after.by('certificates')[first['certificate_id']] == old_cert
    cert = after.by('certificates')[done['certificate_id']]
    assert (cert['generation'], cert['previous_certificate_id']) == (2, first['certificate_id'])
    state = next(row for row in after.rows['accounts'] if row['account_id'] == bank)
    assert (state['version'], state['head_certificate_id']) == (2, done['certificate_id'])
    assert all(row in after.rows['claims'] for row in old_claims)
    members = [row for row in after.rows['certificate_members']
               if row['certificate_id'] == done['certificate_id']]
    assert sorted(row['classification'] for row in members) == ['prior_cleared', 'prior_cleared', 'selected']


def test_finish_rejects_stale_version_without_changing_the_account(reconciled, client, driver):
    bank = reconciled['bank']
    draft = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=bank, statement_date='2026-02-28',
        ending_balance='12.50', opening_id=reconciled['done']['opening_id']))['draft']
    guards = prepared(client, draft['id'], draft['version'])[0]
    with driver.session() as s:
        before = loading.load(s, bank).rows
    with pytest.raises(BookflowError) as caught:
        run(client, 'reconcile finish', dict(operation_key=new_id(), draft=draft['id'],
                    expected_version=999, **guards))
    assert caught.value.code == 'E_VERSION_CONFLICT'
    with driver.session() as s:
        assert loading.load(s, bank).rows == before


def test_empty_credit_card_statement_uses_card_convention(client, driver):
    card = account(client, 'Unused company card', 'credit_card')
    opening = run(client, 'reconcile opening start', dict(
        operation_key=new_id(), account=card, opening_date='2026-01-01', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='New unused card'),
        references=[]))['draft']
    draft = run(client, 'reconcile start', dict(
        operation_key=new_id(), account=card, statement_date='2026-01-31',
        ending_balance='0.00', opening_draft_id=opening['id']))['draft']
    done = run(client, 'reconcile finish', dict(operation_key=new_id(), draft=draft['id'],
                expected_version=1, **prepared(client, draft['id'], 1)[0]))
    with driver.session() as s:
        snapshot = loading.load(s, card)
    assert snapshot.by('certificates')[done['certificate_id']]['convention'] == 'card_debt'
    assert snapshot.rows['accounts'][0]['convention'] == 'card_debt'
    assert snapshot.rows['certificate_members'] == []
