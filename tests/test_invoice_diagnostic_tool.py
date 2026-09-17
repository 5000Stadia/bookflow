"""Diagnostic ownership, attribution and restoration—not product latency assertions."""
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from types import SimpleNamespace

import pytest

from tools import profile_invoice as diagnostic


def test_copy_guard_refuses_live_aliases_descriptors_and_outputs_inside_data(tmp_path):
    root = tmp_path / 'copy'
    root.mkdir()
    (root / 'hub.db').touch()
    output = tmp_path / 'report.json'
    assert diagnostic.validate_copy(root, output, True) == (root, output)
    for ack, target in ((False, output), (True, root / 'report.json')):
        with pytest.raises(ValueError):
            diagnostic.validate_copy(root, target, ack)
    for target in (diagnostic.Path('/tmp/bfdemo3/hub.db'), diagnostic.Path.home() / '.bookflow' / 'output.json'):
        with pytest.raises(ValueError):
            diagnostic.validate_copy(root, target, True)
    for name in ('host.json', 'root.lock'):
        (root / name).touch()
        with pytest.raises(ValueError):
            diagnostic.validate_copy(root, output, True)
        (root / name).unlink()
    alias = tmp_path / 'live-alias'
    alias.symlink_to('/tmp/bfdemo3', target_is_directory=True)
    with pytest.raises(ValueError):
        diagnostic.validate_copy(alias, output, True)
    (root / 'external').symlink_to(tmp_path / 'elsewhere')
    with pytest.raises(ValueError):
        diagnostic.validate_copy(root, output, True)


def test_invoice_command_names_cannot_be_profiled_as_record_pages():
    company, invoice = '01M22MZ2WN9WCS9Q8GP5AXSA06', '01M239EF6R2MPXGKNXMS30Q9KX'
    assert diagnostic.valid_identifiers(company, invoice)
    for wrong in ('post', 'query', 'show', '../invoice', 'self'):
        assert not diagnostic.valid_identifiers(company, wrong)


def test_stage_attribution_crosses_copied_context_and_excludes_unmeasured_calls():
    timings = diagnostic.Timings()
    inner = timings.wrap(lambda: 7, 'inner')
    outer = timings.wrap(inner, 'outer')
    assert outer() == 7 and timings.report() == []
    token = diagnostic.ACTIVE.set('invoice-test')
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert worker.submit(copy_context().run, outer).result() == 7
    finally:
        diagnostic.ACTIVE.reset(token)
    rows = {row['stage']: row for row in timings.report()}
    assert rows['outer']['calls'] == rows['inner']['calls'] == 1
    assert rows['outer']['inclusive_seconds'] >= rows['inner']['inclusive_seconds']
    assert rows['outer']['exclusive_of_measured_children_seconds'] == pytest.approx(
        rows['outer']['inclusive_seconds'] - rows['inner']['inclusive_seconds'])
    assert diagnostic.STACK.get() == ()


def test_stage_installation_restores_closures_and_modules_on_failure():
    from bookflow.hub import permission_runtime
    original_observe = permission_runtime.observe_current
    def run(request, name):
        return name
    original_run = run
    def endpoint():
        return run(None, 'invoice show')
    route = SimpleNamespace(dependant=SimpleNamespace(call=endpoint))
    token = diagnostic.ACTIVE.set('exception-test')
    timings = diagnostic.Timings()
    try:
        with pytest.raises(RuntimeError):
            with ExitStack() as stack:
                diagnostic.install_stages(stack, route, timings)
                assert route.dependant.call() == 'invoice show'
                assert permission_runtime.observe_current is not original_observe
                raise RuntimeError('test cleanup')
    finally:
        diagnostic.ACTIVE.reset(token)
    assert route.dependant.call is endpoint
    assert dict(diagnostic.closure_cells(endpoint))['run'].cell_contents is original_run
    assert permission_runtime.observe_current is original_observe
    assert {r['stage'] for r in timings.report()} == {'endpoint.worker', 'command.invoice show'}
