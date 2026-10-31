"""A command may also own read subcommands without losing its CLI write entry."""
from typer.testing import CliRunner

from bookflow.adapters.cli.app import build_app


def test_refund_full_cli_preserves_write_and_read_commands(monkeypatch, tmp_path):
    from bookflow.core import dispatch
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(tmp_path))
    seen = []

    def run(cmd, raw, ctx, **kwargs):
        seen.append((cmd.name, raw))
        return {'command': cmd.name}

    monkeypatch.setattr(dispatch, 'run', run)
    app = build_app(full=True)
    runner = CliRunner()
    help_result = runner.invoke(app, ['pos', 'refund', '--help'], terminal_width=240, color=False)
    assert help_result.exit_code == 0, help_result.output
    assert '--operation-key' in help_result.output and '--lines' in help_result.output
    assert all(verb in help_result.output for verb in ('show', 'lines', 'receipt'))
    targeted_help = runner.invoke(build_app('pos refund'), ['pos', 'refund', '--help'],
                                  terminal_width=240, color=False)
    assert targeted_help.exit_code == 0, targeted_help.output
    assert '--operation-key' in targeted_help.output and '\nCommands:' in targeted_help.output
    write = runner.invoke(app, ['pos', 'refund', '--operation-key', 'cli-refund', '--lines',
                               '[{"item":"service","quantity":"1"}]', '--json'])
    assert write.exit_code == 0, (write.output, write.exception)
    assert seen == [('pos refund', {'operation_key': 'cli-refund', 'lines': [{'item': 'service', 'quantity': '1'}]})]
    read = runner.invoke(app, ['pos', 'refund', 'lines', '--sale', 'SR-1', '--json'])
    assert read.exit_code == 0, (read.output, read.exception)
    assert seen[-1] == ('pos refund lines', {'sale': 'SR-1'})
    assert len(seen) == 2  # The parent's write callback never executes for a read subcommand.


def test_refund_python_namespace_is_callable_and_keeps_its_reads(monkeypatch):
    from bookflow.client import Client
    seen = []

    def run(self, name, raw, **options):
        seen.append((name, raw, options))
        return {'command': name}

    monkeypatch.setattr(Client, 'run', run)
    client = Client()
    client.pos.refund(operation_key='python-return', lines=[{'item': 'Service'}],
                      company='Shop', dry_run=True, reason='Inspect return')
    client.pos.refund.lines(sale='SR-1', company='Shop')
    client.pos.refund.show(refund='python-return', company='Shop')
    client.pos.refund.receipt(sale='python-return', reprint=True, company='Shop')
    assert [row[0] for row in seen] == ['pos refund', 'pos refund lines', 'pos refund show', 'pos refund receipt']
    assert seen[0][1] == {'operation_key': 'python-return', 'lines': [{'item': 'Service'}]}
    assert seen[0][2]['company'] == 'Shop' and seen[0][2]['dry_run'] is True
    assert seen[0][2]['reason'] == 'Inspect return'
    assert seen[-1][1] == {'sale': 'python-return', 'reprint': True}
