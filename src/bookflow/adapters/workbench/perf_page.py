"""The performance page: `perf report` as a filter form above its table (R180).

At ``/hub/performance``. The filters are plain query parameters (a GET form, so a view is a link
that can be kept), and every figure comes from the command.
"""

from bookflow.core.errors import BookflowError

FIELDS = ('since', 'command', 'surface')
SURFACES = ('cli', 'python', 'http', 'mcp', 'workbench', 'system')


def inputs(params) -> tuple[dict, dict]:
    form = {name: (params.get(name) or '').strip() for name in FIELDS}
    form['since'] = form['since'] or '7d'
    raw = {name: value for name, value in form.items() if value}
    if raw.get('surface') not in SURFACES:
        raw.pop('surface', None)
    return raw, form


def install(app, *, host, run, render, page_error):
    from fastapi import Request
    from fastapi.responses import HTMLResponse
    from bookflow.core.permission_package import read_endpoint

    @app.get('/hub/performance', response_class=HTMLResponse)
    @read_endpoint(host)
    def performance_page(request: Request):
        """How long commands take: p50, p95 and max per command, and the slowest calls."""
        raw, form = inputs(request.query_params)
        report, error = None, None
        try:
            report = run(request, 'perf report', raw, None)
        except BookflowError as refused:
            error = refused.to_dict()
        return render('perf.html', request, company=None, form=form, report=report, error=error,
                      surfaces=SURFACES)
