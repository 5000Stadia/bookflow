"""R106: the timekeeping commands through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. It sets the work week and rounding, saves a week of
Casey Worker's time on Riverside Apartments (8 and 7.5 hours), shows it, copies it into the next week,
clocks Casey in two hours ago and out an hour later (rounded to 15 minutes: 1 hour), cancels a second
clock, reads the clock status, unlinks (a no-op) Casey's login, schedules Casey twice so the second
warns of the overlap, moves and cancels one, and reads the calendar by employee and by job. Every
surface must return the same figures.
"""
from datetime import datetime, timedelta, timezone

import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset((
    'clock cancel', 'clock in', 'clock link', 'clock out', 'clock status', 'schedule calendar', 'schedule cancel',
    'schedule create', 'schedule show', 'schedule update', 'timesheet copy-last', 'timesheet preferences',
    'timesheet save', 'timesheet set-preferences', 'timesheet show'))
WHO, JOB, ITEM = 'Casey Worker', 'Riverside Apartments', 'Mainline Clearing'


def _z(moment):
    return moment.isoformat(timespec='seconds').replace('+00:00', 'Z')


@pytest.mark.timeout(900)
def test_the_same_timekeeping_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                await call('timesheet set-preferences', {'week_start': 'monday', 'clock_rounding': '15'})
                prefs = await call('timesheet preferences', {})
                row = {'customer': JOB, 'item': ITEM, 'notes': 'Main line', 'billable': True,
                       'hours': ['8', '7.5', None, None, None, None, None]}
                saved = await call('timesheet save', {'employee': WHO, 'week': '2026-01-12', 'rows': [row]})
                shown = await call('timesheet show', {'employee': WHO, 'week': '2026-01-14'})
                copied = await call('timesheet copy-last', {'employee': WHO, 'week': '2026-01-19'})
                now = datetime.now(timezone.utc).replace(microsecond=0)
                await call('clock in', {'employee': WHO, 'customer': JOB, 'item': ITEM, 'at': _z(now - timedelta(hours=2))})
                out = await call('clock out', {'employee': WHO, 'end': _z(now - timedelta(hours=1))})
                await call('clock in', {'employee': WHO, 'customer': JOB, 'item': ITEM})
                status = await call('clock status', {})
                cancelled = await call('clock cancel', {'employee': WHO})
                link = await call('clock link', {'employee': WHO})
                first = await call('schedule create', {'employee': WHO, 'customer': JOB, 'starts': '2026-01-13T08:00',
                                                       'ends': '2026-01-13T12:00', 'notes': 'Water heater'})
                second = await call('schedule create', {'employee': WHO, 'customer': JOB, 'starts': '2026-01-13T11:00',
                                                        'ends': '2026-01-13T13:00'})
                moved = await call('schedule update', {'entry': second['id'], 'expected_version': 1,
                                                       'starts': '2026-01-13T12:00'})
                await call('schedule cancel', {'entry': first['id'], 'expected_version': 1})
                one = await call('schedule show', {'entry': moved['id']})
                by_person = await call('schedule calendar', {'employee': WHO, 'view': 'day', 'date': '2026-01-13'})
                by_job = await call('schedule calendar', {'customer': JOB, 'view': 'week', 'date': '2026-01-13'})
                results[surface] = (prefs, saved, shown, copied, out, status, cancelled, link, second, moved, one,
                                    by_person, by_job)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (prefs, saved, shown, copied, out, status, cancelled, link, second, moved, one, by_person,
                  by_job) in results.items():
        assert prefs['clock_rounding'] == '15' and prefs['week_start'] == 'monday', surface
        assert saved['changed'] and saved['week_total'] == '15.5' == shown['week_total'], surface
        assert [r['notes'] for r in copied['rows']] == ['Main line'] and copied['week_total'] == '0', surface
        assert sum(float(r['hours']) for r in out['recorded_hours']) == 1.0 and out['status'] == 'closed', surface
        assert [c['employee_name'] for c in status['clocks']] == [WHO] and cancelled['status'] == 'cancelled', surface
        assert link['changed'] is False, surface
        assert len(second['conflicts']) == 1 and moved['conflicts'] == [] and one['starts'] == '2026-01-13T12:00', surface
        assert [i['starts'] for i in by_person['days'][0]['items']] == ['2026-01-13T12:00'], surface
        assert by_job['date_from'] == '2026-01-12' and len(by_job['days']) == 7, surface
        shapes.add(repr((saved['day_totals'], shown['rows'][0]['hours'], copied['copied_from'],
                         [d['date'] for d in by_job['days'] if d['items']])))
    assert len(shapes) == 1
