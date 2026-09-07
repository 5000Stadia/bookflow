"""Test-only invocation/ASGI attribution; never infer phases from SQL text."""
from collections import Counter
from contextvars import ContextVar
from contextlib import contextmanager
import sqlalchemy as sa

_current = ContextVar('query_cost_trace', default=None)
_phase = ContextVar('query_cost_phase', default='request')


class QueryTrace:
    def __init__(self, monkeypatch):
        from bookflow.adapters.http import execution
        from bookflow.core.publication import PublicationPermit
        original_execute, original_check = execution.execute, PublicationPermit.check

        def execute(*args, **kwargs):
            with self.phase('execution'):
                return original_execute(*args, **kwargs)

        def check(*args, **kwargs):
            with self.phase('check'):
                return original_check(*args, **kwargs)

        monkeypatch.setattr(execution, 'execute', execute)
        monkeypatch.setattr(PublicationPermit, 'check', check)
        self.receipts = []

    @contextmanager
    def phase(self, kind):
        trace = _current.get()
        if trace is None:
            yield; return
        index = len(trace['phases'])
        trace['phases'].append({'kind': kind, 'sql': []})
        token = _phase.set(index)
        try:
            yield
        finally:
            _phase.reset(token)

    def app(self, app):
        async def observed(scope, receive, send):
            async def record(message):
                trace = _current.get()
                if trace is not None and message['type'].startswith('http.response.'):
                    trace['frames'].append({'type': message['type'], 'bytes': len(message.get('body', b'')),
                                            'more_body': message.get('more_body', False)})
                await send(message)
            await app(scope, receive, record)
        return observed

    def run(self, call):
        trace = {'raw_sql': [], 'request_sql': [], 'phases': [], 'frames': []}
        token = _current.set(trace)
        def capture(connection, cursor, statement, parameters, context, many):
            active = _current.get()
            if active is not trace:
                return
            trace['raw_sql'].append(statement)
            phase = _phase.get()
            (trace['request_sql'] if phase == 'request' else trace['phases'][phase]['sql']).append(statement)
        sa.event.listen(sa.engine.Engine, 'before_cursor_execute', capture)
        try:
            value = call()
        finally:
            sa.event.remove(sa.engine.Engine, 'before_cursor_execute', capture)
            _current.reset(token)
            self.receipts.append(trace)
        return value, trace


def assert_bounded(*traces):
    """Raw exact phase multisets, complete observed frames and no missing check."""
    baseline = None
    for trace in traces:
        executions = [p['sql'] for p in trace['phases'] if p['kind'] == 'execution']
        checks = [p['sql'] for p in trace['phases'] if p['kind'] == 'check']
        assert len(executions) == 1, 'exactly one dispatch execution'
        frames = trace['frames']
        assert frames and frames[0]['type'] == 'http.response.start'
        assert sum(f['type'] == 'http.response.start' for f in frames) == 1
        bodies = frames[1:]
        assert bodies and all(f['type'] == 'http.response.body' and f['bytes'] <= 65536 for f in bodies)
        assert all(f['more_body'] for f in bodies[:-1]) and not bodies[-1]['more_body']
        assert len(bodies) == max(1, (sum(f['bytes'] for f in bodies) + 65535)//65536)
        assert len(checks) == 1 + len(frames), 'missing or extra release check'
        assert checks[0], 'check performed no current SQL validation'
        assert all(Counter(c) == Counter(checks[0]) for c in checks), 'different check work'
        raw = Counter(trace['request_sql'])
        for p in trace['phases']:
            raw.update(p['sql'])
        assert raw == Counter(trace['raw_sql']), 'unattributed or duplicated SQL'
        contract = (Counter(trace['request_sql']), Counter(executions[0]), Counter(checks[0]))
        if baseline is None:
            baseline = contract
        else:
            assert contract == baseline, 'page-size dependent work inside phase'
