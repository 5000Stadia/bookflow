"""Independent review gaps: retained claims, void recipes and continuation ownership."""
import hashlib
import hmac
import sqlite3
import pytest
from pydantic import ValidationError
from bookflow.company import deposit_dependency_history as history
from bookflow.company.deposit_dependency_models import InspectionRoot, PageInput
from bookflow.company.deposit_dependency_pages import changes_page
from bookflow.company.ledger_reports import _cursor_key
from bookflow.core.errors import BookflowError
from bookflow.core.publication import OSBinding
from tests.test_deposit_dependency_binding import observe, bound_people, _storage
from tests.test_deposit_lifecycle import driver, additional_document, replacement
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method
from tests.test_row8_journal import database_path
from tests.test_work_billing_lifecycle import accepted, bill


def _guard(s, target):
    binding = OSBinding.from_session(s)
    recipe, facts = history.capture(s, target, binding)
    assert not facts.unknown, facts.unknown
    return history.issue(s, recipe, facts, binding)


def test_removed_source_release_retains_history_and_work_authority(client, sale, driver, monkeypatch):
    work = accepted(client, sale)
    invoice = bill(client, work)
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02',
        amount='1.00', payment_method=method(client), operation_key='review-linked-payment',
        applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1, amount='1.00')])), company=COMPANY)
    document = additional_document(client, sale)
    document['sources'] = [dict(source_type='payment', source=payment['id'], expected_version=1)]
    posted = driver.run('post', dict(operation_key='review-claim', document=document))
    target = InspectionRoot(kind='deposit', id=posted.current.id)
    token = observe(client, monkeypatch, lambda s: _guard(s, target))
    update = replacement(posted, document)
    update['sources'] = []
    edited = driver.run('update', dict(operation_key='review-release', deposit=posted.current.id,
        expected_version=1, document=update), reason='Remove retained source')
    with sqlite3.connect(database_path(client)) as db:
        rows = db.execute('SELECT id,kind,reverses_membership_id FROM deposit_memberships WHERE source_transaction_id=? ORDER BY rowid', (payment['id'],)).fetchall()
        assert len(rows) == 2 and rows[0][1:] == ('claim', None) and rows[1][1:] == ('release', rows[0][0])
        assert db.execute('SELECT count(*) FROM deposit_current_memberships WHERE source_transaction_id=?', (payment['id'],)).fetchone() == (0,)
    before = driver.dump()
    def check(s):
        binding = OSBinding.from_session(s)
        result = history.compare(s, token, target, binding)
        assert not result.matches and not result.unknown_history
        assert {payment['id'], invoice['id']} <= set(result.baseline.transactions) & set(result.current.transactions)
        old = next(r for r in result.baseline.relations if r.kind == 'active_claim' and r.owner_id == payment['id'])
        current = next(r for r in result.current.relations if r.kind == 'active_claim' and r.owner_id == payment['id'])
        assert old.members == (rows[0][0],) and current.members == () and current.state == 'empty'
        assert {r.id for r in result.current.records if r.kind == 'deposit_membership'} == {r[0] for r in rows}
        assert any(r.kind == 'deposit_membership' and r.record_id == rows[1][0] and r.event_id == edited.effect.audit_event_id for r in result.changes)
        from bookflow.company import payment_authority
        real = payment_authority.require_resource
        def deny_work(session, resource, role):
            if resource == 'customer-work':
                raise BookflowError('E_PERMISSION')
            return real(session, resource, role)
        # Exercise actual retained-graph discovery under a denied existing resource;
        # the separate binding matrix covers actual credential transitions.
        with monkeypatch.context() as patch:
            patch.setattr(payment_authority, 'require_resource', deny_work)
            with pytest.raises(BookflowError) as caught:
                history.compare(s, token, target, binding)
            assert caught.value.code == 'E_PERMISSION'
    observe(client, monkeypatch, check)
    assert driver.dump() == before


@pytest.mark.parametrize('projection', ['membership', 'bank'])
def test_projection_disagreement_is_unknown_and_cannot_issue(client, sale, driver, monkeypatch, projection):
    payment = client.run('payment receive', dict(customer=sale['customer'], date='2026-06-02', amount='1.00',
        payment_method=method(client), operation_key='review-projection-payment'), company=COMPANY)
    document = additional_document(client, sale)
    document['sources'] = [dict(source_type='payment', source=payment['id'], expected_version=1)]
    posted = driver.run('post', dict(operation_key='review-projection-post', document=document))
    target = InspectionRoot(kind='deposit', id=posted.current.id)
    token = observe(client, monkeypatch, lambda s: _guard(s, target))
    before = driver.dump()
    with driver.session() as s:
        s.company.raw.execute('SAVEPOINT review_corruption')
        try:
            # Ordinary DML is guarded. Simulate an already-corrupt owned store
            # only inside this rollback-only savepoint; restore its trigger too.
            statement = ('DELETE FROM deposit_current_memberships WHERE source_transaction_id=?'
                         if projection == 'membership' else 'DELETE FROM bank_effect_current')
            parameters = (payment['id'],) if projection == 'membership' else ()
            with pytest.raises(sqlite3.IntegrityError):
                s.company.raw.execute(statement, parameters)
            if projection == 'membership':
                s.company.raw.execute('DROP TRIGGER deposit_current_claim_delete')
                s.company.raw.execute('DELETE FROM deposit_current_memberships WHERE source_transaction_id=?', (payment['id'],))
                expected = 'source_claims:' + payment['id']
            else:
                s.company.raw.execute('DROP TRIGGER bank_effect_current_delete')
                key = s.company.raw.execute('SELECT id FROM bank_effect_keys WHERE transaction_id=? ORDER BY id LIMIT 1', (posted.current.id,)).fetchone()[0]
                s.company.raw.execute('DELETE FROM bank_effect_current WHERE key_id=?', (key,))
                expected = 'bank_effect_current:' + key
            damaged = tuple(s.company.raw.iterdump())
            binding = OSBinding.from_session(s)
            result = history.compare(s, token, target, binding)
            assert not result.matches and result.unknown_history and expected in result.unknown_records
            recipe, facts = history.capture(s, target, binding)
            assert expected in facts.unknown
            with pytest.raises(BookflowError) as caught:
                history.issue(s, recipe, facts, binding)
            assert caught.value.code == 'E_PREVIEW_STALE' and caught.value.details['history'] == 'unknown_history'
            assert tuple(s.company.raw.iterdump()) == damaged
        finally:
            s.company.raw.execute('ROLLBACK TO review_corruption')
            s.company.raw.execute('RELEASE review_corruption')
    assert driver.dump() == before


def test_void_intent_roundtrip_and_legacy_digest_rejection(client, sale, driver, monkeypatch):
    posted = driver.run('post', dict(operation_key='review-void-post', document=additional_document(client, sale)))
    target = history.request(dict(command='deposit void', input=dict(operation_key='review-void', deposit=posted.current.id,
        expected_version=1), context=dict(reason='Cancel this deposit')))
    before = driver.dump()
    def check(s):
        binding = OSBinding.from_session(s)
        recipe, facts = history.capture(s, target, binding)
        assert not facts.unknown and facts.issuer is None and recipe.issuer_entry is None
        assert not any(r.kind in ('number_occupancy', 'automatic_number_effects', 'custom_definitions') for r in facts.relations)
        token = history.issue(s, recipe, facts, binding)
        result = history.compare(s, token, target, binding)
        assert result.matches and not result.unknown_history and result.baseline == result.current
        page = changes_page(s, token, target, PageInput(), binding)
        assert page.items == () and page.total_count == 0 and page.next_cursor is None
        old = hmac.new(_cursor_key(s.company), b'deposit-dependencies-v1\0' + b'0' * 64, hashlib.sha256).hexdigest()
        assert len(old) == 64
        with pytest.raises(BookflowError) as caught:
            history.compare(s, old, target, binding)
        assert caught.value.code == 'E_VALIDATION' and caught.value.details['reason'] == 'invalid_guard'
    observe(client, monkeypatch, check)
    assert driver.dump() == before


def test_page_bounds_and_cross_actor_cursor(root, client, sale, driver, monkeypatch, bound_people):
    assert PageInput().limit == 50
    for limit in (0, 201):
        with pytest.raises(ValidationError):
            PageInput(limit=limit)
    people = bound_people
    posted = driver.run('post', dict(operation_key='review-cursor-post', document=additional_document(client, sale)))
    target = InspectionRoot(kind='deposit', id=posted.current.id)
    first = observe(people['one'], monkeypatch, lambda s: _guard(s, target), people['company'])
    second = observe(people['two'], monkeypatch, lambda s: _guard(s, target), people['company'])
    client.run('company update', dict(closing_date='2025-01-01'), company=COMPANY)
    client.run('company update', dict(closing_date='2025-01-02'), company=COMPANY)
    page = observe(people['one'], monkeypatch, lambda s: changes_page(s, first, target, PageInput(limit=1), OSBinding.from_session(s)), people['company'])
    assert page.total_count == 2 and page.next_cursor
    before = _storage(root, database_path(client))
    def wrong_actor(s):
        # The second actor has its own valid baseline; only the first actor's
        # cursor is transplanted, so failure specifically covers cursor binding.
        with pytest.raises(BookflowError) as caught:
            changes_page(s, second, target, PageInput(limit=1, cursor=page.next_cursor), OSBinding.from_session(s))
        assert caught.value.code == 'E_VALIDATION' and caught.value.details['reason'] == 'invalid_guard'
    observe(people['two'], monkeypatch, wrong_actor, people['company'])
    assert _storage(root, database_path(client)) == before
