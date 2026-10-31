"""Reusable bank-source conformance tests, independent of an adapter's transport.

A future adapter subclasses ``BankSourceContract`` and supplies ``source_case``:
a sandbox source seeded with two posted transactions, a pending transaction, a page
reusing the first posted ID with a changed amount, and a permanent-failure cursor.
No production account or credentials belong in this fixture. Import-path replay
and reversal are integration tests; these checks exercise the provider boundary.
"""
from dataclasses import dataclass

import pytest

from bookflow.company.bank_sources import (
    FeedConnection, FeedFailure, FeedSource, fingerprint, validate_page,
)


@dataclass(frozen=True)
class SourceCase:
    source: FeedSource
    connection: FeedConnection
    remote_account: str
    first_id: str
    first_amount: int
    next_id: str
    pending_id: str
    changed_cursor: str
    failed_cursor: str


class BankSourceContract:
    def test_check_and_account_discovery(self, source_case):
        case = source_case
        assert case.source.check(case.connection, timeout=1).ok is True
        accounts = case.source.accounts(case.connection, limit=1000, timeout=1)
        assert len(accounts) <= 1000
        account = next(a for a in accounts if a.id == case.remote_account)
        assert account.name and len(account.last4) == 4
        assert account.kind in ('bank', 'card')
        assert len(account.currency) == 3 and account.currency.isupper()

    def test_first_next_empty_and_replay(self, source_case):
        case = source_case
        fetch = lambda cursor: validate_page(case.source.fetch(
            case.connection, case.remote_account, cursor, limit=2, timeout=1), limit=2)
        first = fetch(None)
        assert [(line.fitid, line.amount) for line in first.lines] == [(case.first_id, case.first_amount)]
        assert first.next_cursor is not None
        following = fetch(first.next_cursor)
        assert [line.fitid for line in following.lines] == [case.next_id]
        assert following.next_cursor not in (None, first.next_cursor)
        empty = fetch(following.next_cursor)
        assert empty.lines == ()
        assert fingerprint(fetch(None)) == fingerprint(first)
        assert fingerprint(fetch(first.next_cursor)) == fingerprint(following)
        assert case.pending_id not in {line.fitid for page in (first, following, empty) for line in page.lines}

    def test_changed_amount_id_is_not_suppressed(self, source_case):
        case = source_case
        changed = validate_page(case.source.fetch(
            case.connection, case.remote_account, case.changed_cursor, limit=2, timeout=1))
        same_id = [line for line in changed.lines if line.fitid == case.first_id]
        assert len(same_id) == 1 and same_id[0].amount != case.first_amount

    def test_permanent_failure_is_typed(self, source_case):
        case = source_case
        with pytest.raises(FeedFailure) as error:
            case.source.fetch(case.connection, case.remote_account, case.failed_cursor, limit=2, timeout=1)
        assert error.value.permanent is True

    def test_requested_page_bound(self, source_case):
        case = source_case
        page = case.source.fetch(case.connection, case.remote_account, None, limit=1, timeout=1)
        assert validate_page(page, limit=1) is page
