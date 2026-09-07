"""Bounded final review corrections on the accepted coordinate union."""
import importlib

import pytest

from bookflow import BookflowError
from bookflow.company import payment_authority
from bookflow.company import transaction_deletion_facts as facts
from bookflow.company.transaction_deletion import prepare_delete, require_ready
from bookflow.company.transaction_deletion_models import BlockedDelete
from bookflow.company.transaction_deletion_validation import validate_delete
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_row8_journal import journal_accounts
from tests.test_transaction_deletion_preparation import session_call, raw
from tests.test_transaction_deletion_review import claimed, grant, intent, journal


def test_closed_period_claim_returns_blocker_but_unblocked_still_checks_date(
    client, claimed, journal, grant, monkeypatch,
):
    payment, deposit, _ = claimed
    client.run('company update', {'closing_date': '2026-02-01'}, company=COMPANY)

    def check(s, ctx):
        before = raw(s)
        request = intent(payment, 'payment')
        blocked = prepare_delete(s, ctx, request)
        assert isinstance(blocked, BlockedDelete)
        assert len(blocked.facts.blockers) == 1
        blocker = blocked.facts.blockers[0]
        assert (blocker.kind, blocker.source_id, blocker.deposit_id) == (
            'deposit_claim', payment['id'], deposit.current.id,
        )
        assert validate_delete(s, ctx, blocked) == blocked
        with pytest.raises(BookflowError) as caught:
            require_ready(blocked)
        assert caught.value.code == 'E_DEPOSIT_DEPENDENCY'
        assert caught.value.details['deposit'] == deposit.current.id
        for version, reason, code in (
            (payment['version'] - 1, ' ', 'E_VERSION_CONFLICT'),
            (payment['version'], ' ', 'E_REASON_REQUIRED'),
        ):
            with pytest.raises(BookflowError) as caught:
                prepare_delete(s, ctx.model_copy(update={'reason': reason}),
                    request.model_copy(update={'expected_version': version}))
            assert caught.value.code == code
        with pytest.raises(BookflowError) as caught:
            prepare_delete(s, ctx, intent(journal))
        assert caught.value.code == 'E_PERIOD_CLOSED'
        assert raw(s) == before

    session_call(client, monkeypatch, check)


def test_preservation_inventory_is_literal_and_independent_of_shared_map(monkeypatch):
    expected = (
        ('transaction_revisions', 'transaction_revision', 'id'),
        ('document_line_identities', 'document_line_identity', 'id'),
        ('document_lines', 'document_line', 'id'),
        ('posting_batches', 'posting_batch', 'id'),
        ('posting_lines', 'posting_line', 'id'),
        ('posting_line_sources', 'posting_line_source', 'id'),
        ('payment_profiles', 'payment_profile', 'revision_id'),
        ('payment_component_keys', 'payment_component_key', 'id'),
        ('payment_components', 'payment_component', 'id'),
        ('settlement_line_keys', 'settlement_line_key', 'id'),
    )
    assert facts.PRESERVATION_TARGETS == expected
    try:
        with monkeypatch.context() as patch:
            # An existing owned table newly exposed by the shared annotation map
            # must not change Delete's separately reviewed preservation scope.
            patch.setitem(payment_authority.PAYMENT_TARGETS,
                'sales_profile', ('sales_profiles', 'revision_id'))
            importlib.reload(facts)
            assert facts.PRESERVATION_TARGETS == expected
    finally:
        importlib.reload(facts)
