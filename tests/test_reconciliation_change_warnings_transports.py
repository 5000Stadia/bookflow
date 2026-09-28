"""The reconciled-transaction warning over actual in-process Python, CLI, HTTP and MCP.

The June books of `test_reconciliation_change_warnings`: Checking reconciled to a 910.00
statement dated 2026-06-30, with a 250.00 check on it. Correcting the check to 275.00 leaves the
reconciliation off by 25.00, its cleared balance 885.00. Every surface previews that correction
and then saves it, and every surface must say the same two sentences and show the same report.
"""
import asyncio
from pathlib import Path

import pytest

from tests.mcp_matrix_support import Matrix
from tests.test_deposit_command import COMPANY, books  # noqa: F401
from tests.test_reconciliation_change_warnings import WHERE, june  # noqa: F401

SURFACES = ('python', 'cli', 'http', 'mcp')

PREVIEW = (WHERE + ' Saving this change will leave that reconciliation off by 25.00 USD: its cleared balance'
           " becomes 885.00 against the statement's ending balance of 910.00, until the reconciliation is"
           ' re-done. The reconciliation discrepancy report shows the change.')
SAVED = (WHERE + ' This change left that reconciliation off by 25.00 USD: its cleared balance is now'
         " 885.00 against the statement's ending balance of 910.00, until the reconciliation is"
         ' re-done. The reconciliation discrepancy report shows the change.')


@pytest.mark.timeout(1200)
def test_every_surface_says_the_same_thing_about_the_same_correction(june, tmp_path):
    pytest.importorskip('mcp')
    client = june['client']
    shown = client.run('check show', dict(check=june['check']['id']), company=COMPANY)
    expense = next(row for row in shown['revision']['lines'] if row['side'] == 'debit')
    body = dict(check=june['check']['id'], expected_version=shown['version'], amount='275.00',
                expenses=[dict(line_id=expense['line_id'], account=expense['account_id'], amount='275.00')])
    report = dict(account=june['bank'], as_of='2026-12-31')

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(Path(client.data_root), tmp_path / 'surfaces')
            said = {}
            for surface in SURFACES:
                preview = await matrix.call(surface, 'check update', body, dry_run=True)
                saved = await matrix.call(surface, 'check update', body)
                figures = await matrix.call(surface, 'report reconciliation-discrepancy', report)
                said[surface] = (preview['warnings'], saved['warnings'],
                                 [(row['kind'], row['reconciled']['minor_units'], row['current']['minor_units'],
                                   row['difference']['minor_units'], row['type_of_change'])
                                  for row in figures['rows']])
            return said
        finally:
            await matrix.close()

    said = asyncio.run(witness())
    for surface in SURFACES:
        assert said[surface] == ([PREVIEW], [SAVED], [
            ('reconciliation', 0, 0, 0, None),
            ('reconciliation', 91000, 88500, -2500, None),
            ('change', -25000, -27500, -2500, 'amount')]), surface
