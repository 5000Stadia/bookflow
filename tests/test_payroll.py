"""Private calculator acceptance: independent cents oracles, authority and continuation."""
import json
import sqlite3
import subprocess
from copy import deepcopy
from pathlib import Path

import anyio
import pytest

import bookflow
from bookflow.core import registry
from bookflow.core.errors import BookflowError

COMMANDS = frozenset(('payroll setup', 'payroll settings', 'payroll draft', 'payroll show',
                      'payroll list', 'payroll prepare'))
SURFACES = ('python', 'cli', 'mcp')


@pytest.fixture
def payroll(tmp_path, monkeypatch):
    root = tmp_path / 'payroll-books'
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='Payroll fixture organization')
    cid = client.company.new(legal_name='Payroll fixture', home_currency='USD', timezone='UTC',
        organization='Payroll fixture organization', chart='general')['company_id']
    def run(command, raw=None, **context):
        if registry.get(command).is_write:
            context.setdefault('reason', 'Fictional payroll fixture')
        return client.run(command, raw or {}, company=cid, **context)
    for name, kind in [('Wage fixture', 'expense'), ('Employer expense fixture', 'expense'),
                       ('Net payable fixture', 'other_current_liability'),
                       ('Tax payable fixture', 'other_current_liability')]:
        run('account create', {'name': name, 'type': kind})
    employee = run('employee create', {'name': 'Paycheck fixture employee'})['id']
    folder = Path(run('company show')['path'])
    return type('Payroll', (), {'root': root, 'client': client, 'company': cid,
        'employee': employee, 'path': folder / 'company.db', 'run': staticmethod(run)})


def enable(co):
    assert co.run('payroll settings')['enabled'] is False
    return co.run('payroll setup', {'enabled': True, 'expected_version': 0, 'operation_key': 'enable',
        'wage_account': 'Wage fixture', 'employer_tax_account': 'Employer expense fixture',
        'net_pay_account': 'Net payable fixture'})


def p1(co, key='p1'):
    # Fictional, independently computed: 40 * $25 = $1000; 10% = $100, 5% = $50.
    # This increment accepts those fixed tax amounts; it ships no rates or table.
    return {'operation_key': key, 'employee': co.employee, 'period_start': '2026-10-01',
        'period_end': '2026-10-07', 'pay_date': '2026-10-09', 'taxes_confirmed': True,
        'earnings': [{'code': 'regular', 'quantity': '40', 'rate': '25'}],
        'items': [
            {'code': 'employee-tax', 'kind': 'employee_tax', 'amount_minor_units': 10000,
             'liability_account': 'Tax payable fixture', 'payee': 'Fictional agency',
             'source_note': 'Fictional P1: independently calculated 10 percent of gross'},
            {'code': 'employer-tax', 'kind': 'employer_tax', 'amount_minor_units': 5000,
             'liability_account': 'Tax payable fixture', 'payee': 'Fictional agency',
             'source_note': 'Fictional P1: independently calculated 5 percent of gross'}]}


def prepare(co, draft, key='calculate', **context):
    return co.run('payroll prepare', {'draft': draft['id'], 'expected_version': draft['version'],
                                     'operation_key': key}, **context)


def counts(co):
    with sqlite3.connect(co.path) as db:
        return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                     for table in ('transactions', 'posting_lines', 'payroll_drafts', 'payroll_draft_revisions'))


def test_p1_preview_save_reopen_and_no_ledger_effect(payroll):
    co = payroll
    enable(co)
    before = counts(co)
    draft = co.run('payroll draft', p1(co))
    preview = prepare(co, draft, dry_run=True)
    assert counts(co)[2:] == (1, 1)
    result = prepare(co, draft)
    assert result['calculation'] == preview['calculation']
    calc = result['calculation']
    assert {k: calc[k] for k in ('gross_minor_units', 'employee_tax_minor_units',
        'employee_deduction_minor_units', 'employer_cost_minor_units', 'net_minor_units',
        'total_expense_minor_units', 'debit_minor_units', 'credit_minor_units')} == {
            'gross_minor_units': 100000, 'employee_tax_minor_units': 10000,
            'employee_deduction_minor_units': 0, 'employer_cost_minor_units': 5000,
            'net_minor_units': 90000, 'total_expense_minor_units': 105000,
            'debit_minor_units': 105000, 'credit_minor_units': 105000}
    assert calc['ledger_effect'] is False
    assert [(row['code'], row['amount_minor_units']) for row in calc['trace']] == [
        ('regular', 100000), ('employee-tax', 10000), ('employer-tax', 5000)]
    assert co.run('payroll show', {'draft': draft['id']}) == result
    assert co.run('payroll list')['rows'] == [result]
    assert counts(co)[:2] == before[:2]
    with sqlite3.connect(co.path) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'payroll_rate%'").fetchall()


def test_exact_line_cents_zero_net_and_negative_refusal(payroll):
    co = payroll
    enable(co)
    raw = p1(co, 'cents')
    # Each 0.001 * $5 = half a cent rounds up independently: total TWO cents, not one.
    raw['earnings'] = [{'code': str(i), 'quantity': '0.001', 'rate': '5'} for i in range(2)]
    raw['items'] = []
    cents = prepare(co, co.run('payroll draft', raw), 'cents-preview')['calculation']
    assert cents['gross_minor_units'] == cents['net_minor_units'] == 2
    assert [row['exact_minor_units'] for row in cents['trace']] == ['0.500', '0.500']
    raw = p1(co, 'zero')
    raw['earnings'] = [{'code': 'fixed', 'amount_minor_units': 1000}]
    raw['items'] = [dict(raw['items'][0], code='deduction', kind='employee_deduction', amount_minor_units=1000)]
    assert prepare(co, co.run('payroll draft', raw), 'zero-preview')['calculation']['net_minor_units'] == 0
    raw = deepcopy(raw)
    raw['operation_key'] = 'negative'
    raw['items'][0]['amount_minor_units'] = 1100
    negative = co.run('payroll draft', raw)
    before = counts(co)
    with pytest.raises(BookflowError) as caught:
        prepare(co, negative, 'negative-preview')
    assert caught.value.code == 'E_VALIDATION'
    assert '1100' not in json.dumps(caught.value.to_dict())
    assert counts(co) == before
    assert co.run('payroll show', {'draft': negative['id']})['calculation'] is None


def test_stale_draft_permanent_retry_and_edit_invalidates_calculation(payroll):
    co = payroll
    enable(co)
    raw = p1(co)
    original = co.run('payroll draft', raw)
    assert co.run('payroll draft', raw) == original
    with pytest.raises(BookflowError) as caught:
        co.run('payroll draft', dict(raw, pay_date='2026-10-10'))
    assert caught.value.code == 'E_IDEMPOTENCY_MISMATCH'
    ready = prepare(co, original)
    with pytest.raises(BookflowError) as caught:
        prepare(co, original, 'stale-prepare')
    assert caught.value.code == 'E_VERSION_CONFLICT'
    update = dict(raw, draft=ready['id'], expected_version=ready['version'], operation_key='edit',
                  earnings=[{'code': 'fixed', 'amount_minor_units': 100001}])
    edited = co.run('payroll draft', update)
    assert edited['version'] == ready['version'] + 1
    assert edited['state'] == 'draft' and edited['calculation'] is None
    with pytest.raises(BookflowError) as caught:
        co.run('payroll draft', dict(update, operation_key='stale-update'))
    assert caught.value.code == 'E_VERSION_CONFLICT'
    assert co.run('payroll draft', raw) == original  # Permanent receipt, not a second draft.
    assert counts(co)[2] == 1


def test_private_reads_colleague_and_general_employee_audit_leakage(payroll):
    from tests.conftest import as_user, make_actor
    from bookflow.core.audit import decode_snapshot
    co = payroll
    enable(co)
    raw = p1(co)
    raw['earnings'][0]['rate'] = '25.000017'
    raw['items'][0]['source_note'] = 'PRIVATE-PAYROLL-SOURCE-73519'
    draft = co.run('payroll draft', raw, reason='PRIVATE-PAYROLL-REASON-73519')
    result = prepare(co, draft)
    uid = make_actor(co.root, 'payroll-reader')
    co.client.run('membership grant', {'user': uid, 'company': co.company, 'role': 'readonly',
        'expected_version': 0, 'grants': ['payroll.read']})
    colleague = as_user(co.root, 'payroll-reader')
    assert colleague.run('payroll show', {'draft': draft['id']}, company=co.company) == result
    uid = make_actor(co.root, 'ordinary-employee-reader')
    co.client.run('membership grant', {'user': uid, 'company': co.company, 'role': 'standard', 'expected_version': 0})
    ordinary = as_user(co.root, 'ordinary-employee-reader')
    for command, body in [('payroll show', {'draft': draft['id']}), ('payroll list', {}), ('payroll settings', {})]:
        with pytest.raises(BookflowError) as caught:
            ordinary.run(command, body, company=co.company)
        assert caught.value.code == 'E_PERMISSION'
        assert '73519' not in json.dumps(caught.value.to_dict())
    for command, body in [('employee show', {'employee': co.employee}), ('employee list', {}),
                          ('employee query', {'limit': 20})]:
        document = ordinary.run(command, body, company=co.company)
        text = json.dumps(document)
        assert '73519' not in text and '25.000017' not in text
        assert 'earnings' not in text and 'calculation' not in text
    with sqlite3.connect(co.path) as db:
        public = db.execute("SELECT summary, reason, source_ref, idempotency_key FROM audit_events WHERE command LIKE 'payroll %'").fetchall()
        snapshots = db.execute("SELECT e.before,e.after FROM audit_entries e JOIN audit_events a ON a.id=e.event_id WHERE a.command LIKE 'payroll %'").fetchall()
        text = json.dumps([public, [[decode_snapshot(v) for v in row] for row in snapshots]])
        assert '73519' not in text and '25.000017' not in text and '100000' not in text
        assert not db.execute("SELECT output FROM idempotency_keys WHERE command LIKE 'payroll %'").fetchall()


def test_disabled_stops_new_work_but_keeps_history(payroll):
    co = payroll
    setting = enable(co)
    draft = co.run('payroll draft', p1(co))
    co.run('payroll setup', {'enabled': False, 'expected_version': setting['version'], 'operation_key': 'disable'})
    assert co.run('payroll show', {'draft': draft['id']}) == draft
    assert co.run('payroll list')['rows'] == [draft]
    for command, body in [('payroll draft', p1(co, 'off')),
        ('payroll prepare', {'draft': draft['id'], 'expected_version': draft['version'], 'operation_key': 'off-prepare'})]:
        with pytest.raises(BookflowError) as caught:
            co.run(command, body)
        assert caught.value.code == 'E_FEATURE_DISABLED'


def test_cli_and_mcp(payroll, tmp_path):
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    from tests import provenance
    from tests.test_row3_host import Hosted, live
    co = payroll
    enable(co)
    raw = p1(co)
    args = ['--company', co.company, '--reason', 'Fictional payroll fixture', 'payroll', 'draft',
        '--operation-key', raw['operation_key'], '--employee', co.employee,
        '--period-start', raw['period_start'], '--period-end', raw['period_end'], '--pay-date', raw['pay_date'],
        '--taxes-confirmed', '--earnings', json.dumps(raw['earnings']), '--items', json.dumps(raw['items']), '--json']
    result = subprocess.run(['/home/k/Projects/Bookflow/.venv/bin/python', '-c',
        'from bookflow.bootstrap import main; main()', *args], capture_output=True, text=True, timeout=20,
        env=provenance.child_env(BOOKFLOW_DATA_ROOT=str(co.root)))
    assert result.returncode == 0, result.stderr
    draft = json.loads(result.stdout)
    token = co.client.token.issue(label='Payroll continuation')
    handle = start_serving(co.root, client_version(), bind='127.0.0.1:8765', secure_cookies=False)
    hosted = Hosted(handle, co.root, '', co.company, token, '', {})
    server = live.__wrapped__(hosted)
    url = next(server)
    async def witness():
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        params = StdioServerParameters(command=provenance.launcher(), args=['mcp', '--url', url],
            env=provenance.child_env(BOOKFLOW_TOKEN=token['secret'], BOOKFLOW_COMPANY=co.company,
                                    BOOKFLOW_DATA_ROOT=str(tmp_path / 'absent')))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.discover()
                reply = await session.call_tool('bookflow_run', {'command': 'payroll prepare', 'company': co.company,
                    'reason': 'Continue saved paycheck', 'input': {'draft': draft['id'],
                    'expected_version': draft['version'], 'operation_key': 'mcp-prepare'}})
                assert not reply.is_error, reply.structured_content
                assert reply.structured_content['id'] == draft['id']
                assert reply.structured_content['calculation']['net_minor_units'] == 90000
    try:
        anyio.run(witness)
    finally:
        server.close()
        handle.stop()
    assert co.run('payroll show', {'draft': draft['id']})['state'] == 'prepared'


def test_independent_calculator_oracles_and_float_refusal():
    from pydantic import ValidationError
    from bookflow.company.payroll import calculate
    from bookflow.company.payroll_models import DraftInput
    base = {'operation_key': 'pure', 'employee': 'fixture', 'period_start': '2026-10-01',
            'period_end': '2026-10-07', 'taxes_confirmed': True,
            'earnings': [{'code': 'regular', 'quantity': '40', 'rate': '25'}],
            'items': [{'code': 'employee', 'kind': 'employee_tax', 'amount_minor_units': 10000,
                       'liability_account': 'fixture', 'payee': 'fixture', 'source_note': 'fictional'},
                      {'code': 'employer', 'kind': 'employer_tax', 'amount_minor_units': 5000,
                       'liability_account': 'fixture', 'payee': 'fixture', 'source_note': 'fictional'}]}
    result = calculate(DraftInput(**base))
    assert (result.gross_minor_units, result.net_minor_units, result.employer_cost_minor_units,
            result.debit_minor_units, result.credit_minor_units) == (100000, 90000, 5000, 105000, 105000)
    cents = dict(base, earnings=[{'code': 'a', 'quantity': '0.001', 'rate': '5'},
                                {'code': 'b', 'quantity': '0.001', 'rate': '5'}], items=[])
    assert calculate(DraftInput(**cents)).net_minor_units == 2
    thirds = dict(base, earnings=[{'code': 'fraction', 'quantity': '0.3333', 'rate': '30'}], items=[])
    assert calculate(DraftInput(**thirds)).net_minor_units == 1000
    deduction = dict(base['items'][0], kind='employee_deduction', amount_minor_units=1000)
    zero = dict(base, earnings=[{'code': 'a', 'amount_minor_units': 1000}], items=[deduction])
    assert calculate(DraftInput(**zero)).net_minor_units == 0
    with pytest.raises(BookflowError) as caught:
        calculate(DraftInput(**dict(zero, items=[dict(deduction, amount_minor_units=1100)])))
    assert caught.value.code == 'E_VALIDATION'
    assert '1100' not in json.dumps(caught.value.to_dict())
    for earning in [{'code': 'a', 'amount_minor_units': 1.0}, {'code': 'a', 'quantity': 0.1, 'rate': '10'}]:
        with pytest.raises(ValidationError):
            DraftInput(**dict(base, earnings=[earning]))


def test_private_storage_revisions_retry_and_stale_snapshot(monkeypatch, tmp_path):
    """Exercise persisted draft/calculation and audit without the full company fixture startup."""
    from types import SimpleNamespace
    import sqlalchemy as sa
    from bookflow.company import schema as c, payroll as service, payroll_models as m
    from bookflow.core.context import Context, Interface
    from bookflow.core.audit import decode_snapshot
    engine = sa.create_engine('sqlite://')
    with engine.connect() as connection:
        for table in (c.payroll_settings, c.payroll_drafts, c.payroll_draft_revisions,
                      c.payroll_operations, c.audit_events, c.audit_entries):
            table.create(connection)
        connection.exec_driver_sql('CREATE TABLE company_info (home_currency TEXT)')
        connection.exec_driver_sql("INSERT INTO company_info VALUES ('USD')")
        session = SimpleNamespace(company=SimpleNamespace(conn=connection, path=tmp_path / 'company.db'),
            company_row={'id': 'fixture-company'}, actor=SimpleNamespace(id='fixture-actor', kind='human'))
        monkeypatch.setattr(service, 'account', lambda s, selector, types, field: selector)
        monkeypatch.setattr(service, 'employee', lambda s, selector: selector)
        ctx = Context.new(Interface.python, 'fixture', reason='PRIVATE-73519', source_ref='PRIVATE-73519')
        setup = m.SetupInput(enabled=True, expected_version=0, operation_key='setup', wage_account='wages',
                            employer_tax_account='expense', net_pay_account='net')
        service.apply(service.prepare_setup(setup, ctx, session), ctx, session)
        raw = p1(SimpleNamespace(employee='fixture-employee'))
        inp = m.DraftInput(**raw)
        draft = service.apply(service.prepare_draft(inp, ctx, session), ctx, session).output
        prepared = m.PrepareInput(draft=draft.id, expected_version=1, operation_key='prepare')
        preview = service.prepare_calculation(prepared, ctx, session)
        assert service.output(service.row(session, draft.id)).calculation is None
        saved = service.apply(preview, ctx, session).output
        assert saved.calculation.net_minor_units == 90000
        assert service.output(service.row(session, draft.id)) == saved
        assert service.apply(service.prepare_draft(inp, ctx, session), ctx, session).output == draft
        assert connection.execute(sa.select(sa.func.count()).select_from(c.payroll_draft_revisions)).scalar_one() == 2
        with pytest.raises(BookflowError) as caught:
            service.prepare_calculation(prepared.model_copy(update={'operation_key': 'new'}), ctx, session)
        assert caught.value.code == 'E_VERSION_CONFLICT'
        edited = inp.model_copy(update={'draft': draft.id, 'expected_version': 2, 'operation_key': 'edit'})
        new = service.apply(service.prepare_draft(edited, ctx, session), ctx, session).output
        assert new.calculation is None and new.version == 3
        public = [dict(r) for r in connection.execute(sa.select(c.audit_events)).mappings()]
        snapshots = [decode_snapshot(v) for v in connection.execute(sa.select(c.audit_entries.c.after)).scalars()]
        assert '73519' not in json.dumps(public)
        assert snapshots == [{'version': 1}, {'version': 1}, {'version': 2}, {'version': 3}]


def test_payroll_migration_matches_metadata_and_retains_history():
    import importlib
    import sqlalchemy as sa
    from bookflow.company import schema as c
    from tests.test_docs_schema_metadata import _metadata_shape, _database_shape
    migration = importlib.import_module('bookflow.storage.company_migrations.versions.0129_payroll_drafts')
    engine = sa.create_engine('sqlite://')
    with engine.connect() as connection:
        for statement in migration.DDL + migration.TRIGGERS:
            connection.exec_driver_sql(statement)
        expected = {k: v for k, v in _metadata_shape(c.metadata).items() if k in migration.TABLES}
        assert _database_shape(sa.inspect(connection), connection) == expected
        connection.exec_driver_sql("INSERT INTO payroll_operations VALUES ('key','actor','payroll draft','hash','{}','private')")
        for sql in ("DELETE FROM payroll_operations", "UPDATE payroll_operations SET output='changed'"):
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(sql)
