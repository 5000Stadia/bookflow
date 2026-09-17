"""Opt-in invoice diagnostics. Runs only on an explicitly acknowledged disposable copy."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import ExitStack
from contextvars import ContextVar
import cProfile
import functools
import inspect
import json
from pathlib import Path
import re
import secrets
import statistics
import threading
import time

ACTIVE = ContextVar('invoice_diagnostic_request', default=None)
STACK = ContextVar('invoice_diagnostic_stack', default=())
SOURCE = Path(__file__).resolve().parents[1] / 'src'


def validate_copy(root, output, acknowledged):
    root, output = root.resolve(), output.resolve()
    protected = [Path('/tmp/bfdemo3'), Path.home() / '.bookflow', Path.home() / '.bookflow-row5-checkpoint']
    if not acknowledged or not (root / 'hub.db').is_file():
        raise ValueError('An existing disposable copy and --disposable-copy are required')
    if any(root == p.resolve() or root.is_relative_to(p.resolve()) or p.resolve().is_relative_to(root) for p in protected):
        raise ValueError('Refusing protected or live storage')
    if any(output == p.resolve() or output.is_relative_to(p.resolve()) for p in protected):
        raise ValueError('Refusing output into protected or live storage')
    if output.is_relative_to(root):
        raise ValueError('Output must be outside the copied storage')
    # A descriptor may redirect operations to another root. Require it removed, even if stale.
    if any((root / name).exists() for name in ('host.json', 'root.lock')):
        raise ValueError('Copy must have no host.json or root.lock')
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Copy must contain no symlinks')
    return root, output


class Timings:
    def __init__(self):
        self.rows = defaultdict(lambda: [0, 0.0, 0.0])
        self.lock = threading.Lock()

    def wrap(self, function, label):
        @functools.wraps(function)
        def measured(*args, **kwargs):
            if ACTIVE.get() is None:
                return function(*args, **kwargs)
            name = label(*args, **kwargs) if callable(label) else label
            parents = STACK.get()
            frame = [0.0]
            token = STACK.set((*parents, frame))
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - start
                STACK.reset(token)
                if parents:
                    parents[-1][0] += elapsed
                with self.lock:
                    row = self.rows[name]
                    row[0] += 1
                    row[1] += elapsed
                    row[2] += max(0.0, elapsed - frame[0])
        return measured

    def report(self):
        return [{'stage': k, 'calls': v[0], 'inclusive_seconds': v[1],
                 'exclusive_of_measured_children_seconds': v[2]}
                for k, v in sorted(self.rows.items(), key=lambda pair: -pair[1][1])]


def closure_cells(function, seen=None):
    seen = set() if seen is None else seen
    for name, cell in zip(function.__code__.co_freevars, function.__closure__ or ()):
        if id(cell) in seen:
            continue
        seen.add(id(cell))
        yield name, cell
        value = cell.cell_contents
        if inspect.isfunction(value):
            yield from closure_cells(value, seen)


def install_stages(stack, route, timings):
    from bookflow.hub import permission_runtime as runtime, permission_snapshot as snapshot
    from bookflow.core.publication import PublicationPermit
    from jinja2 import Template

    def patch(owner, name, label):
        original = getattr(owner, name)
        setattr(owner, name, timings.wrap(original, label))
        stack.callback(setattr, owner, name, original)

    for owner, name, label in (
        (runtime, 'observe_current', 'permission.observe_current'),
        (snapshot, '_load_root', 'permission.load_root'),
        (snapshot, '_validated_root', 'permission.validate_root'),
        (PublicationPermit, 'check', 'response.publication_check'),
        (Template, 'render', 'template.render'),
    ):
        patch(owner, name, label)
    cells = list(closure_cells(route.dependant.call))
    for name, cell in cells:
        if name not in ('run', 'render', 'record_page') or not inspect.isfunction(cell.cell_contents):
            continue
        original = cell.cell_contents
        label = (lambda request, name, *a, **k: 'command.' + name) if name == 'run' else 'page.' + name
        cell.cell_contents = timings.wrap(original, label)
        stack.callback(setattr, cell, 'cell_contents', original)
    patch(route.dependant, 'call', 'endpoint.worker')


def source_name(filename):
    try:
        return str(Path(filename).resolve().relative_to(SOURCE.parent))
    except (ValueError, OSError):
        return None


def function_report(profiler):
    rows = []
    for entry in profiler.getstats():
        code = entry.code
        if isinstance(code, str):
            continue
        filename = source_name(code.co_filename)
        if filename is not None:
            rows.append(dict(file=filename, line=code.co_firstlineno, function=code.co_name,
                             calls=entry.callcount, own_seconds=entry.inlinetime,
                             cumulative_seconds=entry.totaltime))
    return sorted(rows, key=lambda r: -r['cumulative_seconds'])[:40]


def diagnose(root, company, invoice, samples, lines=False):
    import bookflow
    from bookflow.commands.host_cmds import start_serving
    from bookflow.core.context import client_version
    from bookflow.core.config import os_login
    from fastapi.testclient import TestClient
    from bookflow.hub import permission_snapshot as snapshot, permission_runtime as runtime
    line_profiler = None
    if lines:
        from line_profiler import LineProfiler  # optional; never a runtime dependency
        line_profiler = LineProfiler(runtime.observe_current, snapshot._load_root, snapshot._validated_root)
    client = bookflow.connect(data_root=str(root))
    company_path = Path(client.company.show(company=company)['path']).resolve()
    if not company_path.is_relative_to(root):
        raise ValueError('Company path leaves disposable copy')
    password = secrets.token_urlsafe(24)
    client.run('user set-password', {'username': os_login(), 'password': password})
    host = start_serving(root, client_version(), bind='127.0.0.1:0', secure_cookies=False, publish_descriptor=False)
    try:
        with TestClient(host.app) as browser:
            assert browser.post('/login', json={'username': os_login(), 'password': password}).status_code == 200
            url = f'/c/{company}/invoice/{invoice}'
            def request(label):
                token = ACTIVE.set(label)
                start = time.perf_counter()
                try:
                    response = browser.get(url, follow_redirects=False)
                    elapsed = time.perf_counter() - start
                    if response.status_code != 200:
                        raise ValueError('Invoice diagnostic request did not return HTTP 200')
                    return {'request_id': label, 'seconds': elapsed, 'status': response.status_code,
                            'response_bytes': len(response.content)}
                finally:
                    ACTIVE.reset(token)
            baseline = [request('baseline-' + str(i + 1)) for i in range(samples)]
            route = next(r for r in host.app.routes if getattr(r, 'path', None) == '/c/{company_id}/{noun}/{record_id}')
            timings = Timings()
            with ExitStack() as stack:
                install_stages(stack, route, timings)
                staged = request('stages')
            profiler = cProfile.Profile()
            original = route.dependant.call
            @functools.wraps(original)
            def profiled(*args, **kwargs):
                return profiler.runcall(original, *args, **kwargs)
            with ExitStack() as stack:
                stack.callback(setattr, route.dependant, 'call', original)
                route.dependant.call = profiled
                function_run = request('worker-functions')
            result = {'transport': 'in-process ASGI TestClient; not network or browser rendering',
                      'baseline': baseline, 'baseline_median_seconds': statistics.median(r['seconds'] for r in baseline),
                      'stage_request': staged, 'stages': timings.report(),
                      'function_profile_request': function_run, 'worker_functions': function_report(profiler),
                      'notes': ['Startup/login excluded. First baseline read is followed by warmed reads.',
                                'Instrumented requests include profiler overhead; compare with baseline.',
                                'Inclusive stages overlap. Exclusive values subtract only measured children, not all callees.',
                                'Worker function/line profiles exclude middleware and later publication checks.',
                                'Stage instrumentation includes publication checks outside the endpoint.',
                                'Unattributed request time may include dispatch, scheduling and response handling; it is not a measured queue delay.']}
            if line_profiler is not None:
                with ExitStack() as stack:
                    stack.callback(setattr, route.dependant, 'call', original)
                    route.dependant.call = line_profiler(original)
                    result['line_profile_request'] = request('worker-lines')
                stats = line_profiler.get_stats()
                result['worker_lines'] = sorted([
                    {'file': source_name(filename), 'function': name, 'line': lineno,
                     'hits': hits, 'seconds': ticks * stats.unit}
                    for (filename, _, name), values in stats.timings.items()
                    if source_name(filename) is not None
                    for lineno, hits, ticks in values], key=lambda r: -r['seconds'])[:60]
            return result
    finally:
        host.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--disposable-copy', action='store_true')
    parser.add_argument('--company', required=True)
    parser.add_argument('--invoice', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--lines', action='store_true')
    args = parser.parse_args()
    try:
        if not 1 <= args.samples <= 10 or not valid_identifiers(args.company, args.invoice):
            raise ValueError('Use 1–10 samples and actual company/invoice ULIDs, not command names')
        root, output = validate_copy(args.data_root, args.output, args.disposable_copy)
        result = diagnose(root, args.company, args.invoice, args.samples, args.lines)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + '\n')
        print(f'Baseline median: {result["baseline_median_seconds"]:.3f}s; report: {output}')
        for row in result['stages']:
            print(f'{row["inclusive_seconds"]:8.3f}s  {row["calls"]:4d} calls  {row["stage"]}')
    except Exception as error:
        # Do not echo request bodies, credentials or data-bearing exception messages.
        parser.exit(1, f'Diagnostic failed ({type(error).__name__}); check copy, identifiers and optional dependencies.\n')


def valid_identifiers(company, invoice):
    return all(re.fullmatch(r'[0-7][0-9A-HJKMNP-TV-Z]{25}', x) for x in (company, invoice))


if __name__ == '__main__':
    main()
