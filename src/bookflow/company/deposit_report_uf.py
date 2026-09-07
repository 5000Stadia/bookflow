"""Exact UF equations over complete separately authorized owner streams.

No account discovery, catch-all permission handling, current eligibility lookup,
SQL history reconstruction or partial-population fallback belongs here.
"""
from bookflow.company.deposit_report_models import (
    DepositReportPeriod, UFBridge, UFEvent, UFAmounts,
)
from bookflow.core.errors import BookflowError
from bookflow.core.exact import _require_i64


def bridge(*, currency: str, account_id: str, period: DepositReportPeriod,
           receipts: tuple[UFEvent, ...], memberships: tuple[UFEvent, ...],
           ledger: tuple[UFEvent, ...]) -> UFBridge:
    """Pure calculation; input constructors and this result convey no authority.

    Release effective dates must already have been resolved and validated by
    the supplier through the claim's actual reversal batch, not source_date.
    """
    streams = (receipts, memberships, ledger)
    for stream in streams:
        if len({e.identity for e in stream}) != len(stream):
            raise BookflowError('E_DEPOSIT_SOURCE_INVALID')
    opening = tuple(sum(e.units for e in stream if e.effective_date < period.date_from) for stream in streams)
    closing = tuple(sum(e.units for e in stream if e.effective_date <= period.date_to) for stream in streams)
    def amounts(values):
        r, d, l = values
        values = dict(receipts=r, deposited=d, source_backed=r-d, ledger=l, unexplained=l-(r-d))
        return UFAmounts(**{key: _require_i64(value, field=key) for key, value in values.items()})
    return UFBridge(
        currency=currency, account_id=account_id, period=period,
        opening=amounts(opening), change=amounts(tuple(c-o for o, c in zip(opening, closing))),
        closing=amounts(closing),
        receipt_effect_count=sum(e.effective_date <= period.date_to for e in receipts),
        membership_effect_count=sum(e.effective_date <= period.date_to for e in memberships),
        ledger_line_count=sum(e.effective_date <= period.date_to for e in ledger),
    )
