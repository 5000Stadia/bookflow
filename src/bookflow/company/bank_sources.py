"""Live bank source contract. No production source is registered.

Providers receive credentials in memory only, enforce the supplied timeout, and return
posted transactions in the statement parser's exact minor-unit shape. Provider failures
are deliberately reduced to fixed codes before they reach audit, logs or command output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from typing import Protocol

from bookflow.company.statement_files import Line

MAX_LINES = 500
FETCH_TIMEOUT = 10
MAX_ACCOUNTS = 1000


@dataclass(frozen=True)
class FeedConnection:
    settings: dict
    secret: str | None


@dataclass(frozen=True)
class CheckResult:
    ok: bool


@dataclass(frozen=True)
class RemoteAccount:
    id: str
    name: str
    last4: str
    kind: str
    currency: str


@dataclass(frozen=True)
class FeedPage:
    lines: tuple[Line, ...]
    next_cursor: str | None
    currency: str
    account_last4: str | None = None
    account_kind: str = 'bank'
    bank_number: str | None = None
    balance: int | None = None
    balance_date: str | None = None


class FeedFailure(Exception):
    """A provider refusal; only permanent determines whether the host disables it."""
    def __init__(self, *, permanent: bool = False):
        self.permanent = permanent
        super().__init__('bank source unavailable')


class FeedSource(Protocol):
    kind: str

    def validate_settings(self, settings: dict) -> dict:
        """Validate non-secret configuration; reject unknown fields and credential fields."""
        ...

    def check(self, conn: FeedConnection, *, timeout: int = FETCH_TIMEOUT) -> CheckResult: ...

    def accounts(self, conn: FeedConnection, *, limit: int = MAX_ACCOUNTS,
                 timeout: int = FETCH_TIMEOUT) -> list[RemoteAccount]: ...

    def fetch(self, conn: FeedConnection, remote_account: str, since_cursor: str | None,
              *, limit: int = MAX_LINES, timeout: int = FETCH_TIMEOUT) -> FeedPage:
        """Return at most limit posted lines; enforce timeout on every external operation."""
        ...


SOURCES: dict[str, FeedSource] = {}


def validate_page(page: FeedPage, *, limit: int = MAX_LINES) -> FeedPage:
    """Fail closed on malformed, pending, oversized or inexact provider output."""
    def text(value, size, optional=False):
        return (value is None and optional) or (type(value) is str and len(value) <= size)

    def day(value):
        return type(value) is str and date.fromisoformat(value).isoformat() == value

    def require(condition):
        if not condition:
            raise ValueError

    try:
        require(isinstance(page, FeedPage))
        require(type(page.lines) is tuple and len(page.lines) <= limit)
        require(text(page.next_cursor, 2048, True))
        require(text(page.currency, 3) and len(page.currency) == 3 and page.currency.isupper())
        require(page.account_kind in ('bank', 'card'))
        require(text(page.account_last4, 4, True))
        require(page.account_last4 is None or len(page.account_last4) == 4)
        require(text(page.bank_number, 64, True))
        require(page.balance is None or (type(page.balance) is int and abs(page.balance) < 2**63))
        require(page.balance_date is None or day(page.balance_date))
        for line in page.lines:
            require(isinstance(line, Line))
            require(text(line.fitid, 200) and line.fitid.strip())
            require(line.line_id == 'fitid:' + line.fitid)
            require(day(line.date))
            require(type(line.amount) is int and abs(line.amount) < 2**63)
            require(text(line.payee, 500) and text(line.memo, 2000) and text(line.number, 100))
            require(text(line.kind, 32) and line.kind.casefold() != 'pending')
    except (AssertionError, TypeError, ValueError, AttributeError):
        raise FeedFailure(permanent=True) from None
    return page


def serialize_page(page: FeedPage) -> str:
    validate_page(page)
    return json.dumps(asdict(page), sort_keys=True, separators=(',', ':'))


def page_from_json(value: str) -> FeedPage:
    try:
        body = json.loads(value)
        body['lines'] = tuple(Line(**line) for line in body['lines'])
        return validate_page(FeedPage(**body))
    except (TypeError, ValueError, KeyError):
        raise FeedFailure(permanent=True) from None


def fingerprint(page: FeedPage) -> str:
    return hashlib.sha256(serialize_page(page).encode()).hexdigest()
