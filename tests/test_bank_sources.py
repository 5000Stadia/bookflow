"""Fake-only conformance witness and rejection tests for the live-feed boundary."""
from dataclasses import replace
from decimal import Decimal
import subprocess
import sys

import pytest

from bookflow.company.bank_sources import (
    CheckResult, FeedConnection, FeedFailure, FeedPage, MAX_LINES,
    RemoteAccount, fingerprint, page_from_json, serialize_page, validate_page,
)
from bookflow.company.statement_files import Line
from tests.bankfeed_source_contract import BankSourceContract, SourceCase


FIRST = Line('fitid:posted-1', '2026-10-01', -425, 'Coffee', fitid='posted-1', kind='DEBIT')
SECOND = Line('fitid:posted-2', '2026-10-02', 10000, 'Receipt', fitid='posted-2', kind='CREDIT')
PENDING = Line('fitid:pending-1', '2026-10-03', -600, 'Pending coffee', fitid='pending-1', kind='pending')


def page(lines=(FIRST,), cursor='next'):
    return FeedPage(lines, cursor, 'USD', account_last4='5678', balance=9575, balance_date='2026-10-02')


class FakeSource:
    kind = 'fake'

    def validate_settings(self, settings):
        if settings:
            raise ValueError('fake accepts no settings')
        return {}

    def check(self, conn, *, timeout=10):
        return CheckResult(conn.secret == 'test-only-secret')

    def accounts(self, conn, *, limit=1000, timeout=10):
        return [RemoteAccount('remote-1', 'Test account', '5678', 'bank', 'USD')]

    def fetch(self, conn, remote_account, since_cursor, *, limit=MAX_LINES, timeout=10):
        assert remote_account == 'remote-1' and limit > 0 and timeout > 0
        if since_cursor == 'failure':
            raise FeedFailure(permanent=True)
        rows, cursor = {
            None: ((FIRST, PENDING), 'next'),
            'next': ((SECOND,), 'empty'),
            'empty': ((), 'empty'),
            'changed': ((replace(FIRST, amount=-500),), 'empty'),
        }[since_cursor]
        return page(tuple(row for row in rows if row.kind != 'pending')[:limit], cursor)


@pytest.fixture
def source_case():
    return SourceCase(FakeSource(), FeedConnection({}, 'test-only-secret'), 'remote-1',
                      'posted-1', -425, 'posted-2', 'pending-1', 'changed', 'failure')


class TestFakeSource(BankSourceContract):
    pass


def test_production_registry_is_empty_in_fresh_process():
    result = subprocess.run([sys.executable, '-c',
                             'from bookflow.core import registry; registry.load_all(); '
                             'import bookflow.core.host; '
                             'from bookflow.company.bank_sources import SOURCES; assert SOURCES == {}'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('changes', [
    {'amount': 1.0}, {'amount': True}, {'amount': Decimal('1')}, {'amount': 2**63},
    {'amount': -(2**63)}, {'fitid': None}, {'fitid': ''}, {'fitid': ' '},
    {'fitid': 'x' * 201}, {'line_id': 'fitid:other'}, {'date': '2026-02-30'},
    {'date': '20261001'}, {'date': None}, {'payee': 'x' * 501}, {'memo': 'x' * 2001},
    {'number': 'x' * 101}, {'kind': 'PENDING'}, {'kind': None},
])
def test_malformed_line_refused_without_returning_data(changes):
    with pytest.raises(FeedFailure) as error:
        validate_page(page((replace(FIRST, **changes),)))
    assert error.value.permanent is True
    assert str(error.value) == 'bank source unavailable'


@pytest.mark.parametrize('changes', [
    {'lines': [FIRST]}, {'lines': (object(),)}, {'lines': (FIRST,) * (MAX_LINES + 1)},
    {'next_cursor': 'x' * 2049}, {'next_cursor': 123}, {'currency': 'usd'},
    {'currency': 'US'}, {'account_kind': 'savings'}, {'account_last4': '12345'},
    {'account_last4': '123'}, {'bank_number': 'x' * 65}, {'balance': 1.0},
    {'balance': True}, {'balance': 2**63}, {'balance_date': '2026-02-30'},
])
def test_malformed_page_refused(changes):
    with pytest.raises(FeedFailure) as error:
        validate_page(replace(page(), **changes))
    assert error.value.permanent is True


def test_page_bound_can_be_narrowed():
    with pytest.raises(FeedFailure):
        validate_page(page((FIRST, SECOND)), limit=1)
    assert validate_page(page(()), limit=0).lines == ()


def test_page_roundtrip_and_preview_fingerprint():
    original = page((FIRST, SECOND))
    assert page_from_json(serialize_page(original)) == original
    assert fingerprint(original) == fingerprint(page_from_json(serialize_page(original)))
    assert fingerprint(original) != fingerprint(replace(original, next_cursor='later'))
    assert fingerprint(original) != fingerprint(replace(original, lines=(replace(FIRST, amount=-426), SECOND)))


@pytest.mark.parametrize('value', ['not json', '{}', '[]', '{"lines": [{"amount": 1.2}]}'])
def test_invalid_staged_page_refused(value):
    with pytest.raises(FeedFailure) as error:
        page_from_json(value)
    assert error.value.permanent is True


def test_optimized_process_still_rejects_malformed_provider_page():
    program = '''
from bookflow.company.bank_sources import FeedFailure, FeedPage, validate_page
from bookflow.company.statement_files import Line
line = Line('fitid:posted', '2026-10-01', 1.5, fitid='posted')
try:
    validate_page(FeedPage((line,), None, 'USD'))
except FeedFailure as error:
    if not error.permanent:
        raise SystemExit('malformed page was not a permanent refusal')
else:
    raise SystemExit('optimized Python accepted an inexact amount')
'''
    result = subprocess.run([sys.executable, '-O', '-c', program], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
