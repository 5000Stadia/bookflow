"""Bounded SQL growth witness through real report and complete print reads."""
from collections import Counter
import re

import pytest

from bookflow.company import deposit_read_facts, deposit_report_print, deposit_reports
from bookflow.company.deposit_report_models import DepositDetailFilter, DepositDetailInput
from bookflow.core.publication import OSBinding
from tests.test_deposit_draft_financial import financial, run_private
from tests.test_deposit_lifecycle import additional_document
from tests.test_service_sales_lifecycle import sale


def test_report_and_print_batch_captured_financial_rows(client, sale, run_private, monkeypatch):
    # Real financial producers create components and allocated cash cells. Four
    # deposits stay below a supplier chunk boundary. Reusing parties/accounts
    # keeps the dataset small without fabricating any persisted financial facts.
    document = additional_document(client, sale, '10', cash='3')
    document['additional'] = [
        dict(document['additional'][0], memo=f'Cash row {n}') for n in range(3)
    ]
    posted_ids = set()
    tables = ('deposit_components', 'deposit_cash_cells')
    table_read = re.compile(
        r'\b(?:from|join)\s+(?:main\.)?["`\[]?('
        + '|'.join(tables) + r')\b', re.IGNORECASE,
    )
    period = dict(date_from='2026-06-01', date_to='2026-06-30')

    def measure(kind):
        def read(s, ctx):
            counts = Counter()

            def trace(statement):
                # Trace SQLite itself, including raw SQL as well as SQLAlchemy.
                # Count statements, not bound IDs, timing, or helper invocations.
                # Identity-only authority projections legitimately repeat per
                # deposit. Financial projections carry revision_id (or *);
                # include both bounded loads and whole-table history scans.
                projection = re.split(r'\bfrom\b', statement, maxsplit=1, flags=re.IGNORECASE)[0]
                if not re.search(r'\brevision_id\b|\*', projection, re.IGNORECASE):
                    return
                for table in set(table_read.findall(statement)):
                    counts[table.lower()] += 1

            binding = OSBinding.from_session(s)
            s.company.raw.set_trace_callback(trace)
            try:
                if kind == 'detail':
                    result = deposit_reports.detail(
                        s, DepositDetailInput(**period, limit=1), binding=binding,
                    )
                    assert len(result.items) == 1
                    assert bool(result.next_cursor) == (len(posted_ids) > 1)
                else:
                    result = deposit_report_print.print_data(
                        s, DepositDetailFilter(**period), binding=binding,
                    )
                    assert {r.movement.transaction_id for r in result.rows} == posted_ids
                    assert len(result.compositions) == len(posted_ids)
                    assert all(len(c.rows) == 3 and c.cash_allocations for c in result.compositions)
                assert result.totals.deposit_count == result.totals.row_count == len(posted_ids)
                assert result.totals.additional_count == 3 * len(posted_ids)
                assert result.totals.cell_count >= 3 * len(posted_ids)
                assert result.totals.composition.bank_total == 2700 * len(posted_ids)
            finally:
                s.company.raw.set_trace_callback(None)
            assert all(counts[t] > 0 for t in tables), counts
            return counts
        return run_private(read)

    observations = {}
    for n in range(4):
        result = financial(run_private, dict(operation_key=f'batch-witness-{n}', document=document))
        posted_ids.add(result.current.id)
        if n in (0, 3):
            observations[n + 1] = {kind: measure(kind) for kind in ('detail', 'print')}

    def assert_batched(small, large):
        assert large == small, f'captured-table SQL grew: {small} -> {large}'

    for kind in ('detail', 'print'):
        assert_batched(observations[1][kind], observations[4][kind])

    # Negative control: retain actual financial validation and output, but make
    # the supplier reload once per deposit. The same SQL witness must reject it.
    original = deposit_read_facts.load_complete

    def per_deposit(s, deposit_ids, *, binding):
        return tuple(
            value for identity in deposit_ids
            for value in original(s, [identity], binding=binding)
        )

    with monkeypatch.context() as patch:
        patch.setattr(deposit_read_facts, 'load_complete', per_deposit)
        for kind in ('detail', 'print'):
            regressed = measure(kind)
            with pytest.raises(AssertionError, match='captured-table SQL grew'):
                assert_batched(observations[1][kind], regressed)
            print(f'{kind}: one={dict(observations[1][kind])}, '
                  f'four={dict(observations[4][kind])}, per-deposit={dict(regressed)}')
