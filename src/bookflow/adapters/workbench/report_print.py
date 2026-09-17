"""Complete filtered report views using the shared export traversal and report layouts."""
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request

from bookflow.core import registry
from bookflow.core.errors import BookflowError
from . import report_export as Export
from . import naming as Naming

SEGMENT = 'print-all'


def print_url(company_id, verb, inputs):
    url = urlsplit(Export.export_url(company_id, verb, inputs))
    return urlunsplit(url._replace(path=url.path.removesuffix(Export.SEGMENT) + SEGMENT))


def return_url(company_id, verb, inputs):
    url = urlsplit(Export.export_url(company_id, verb, inputs))
    return urlunsplit(url._replace(path=url.path.removesuffix('/' + Export.SEGMENT)))


def complete(read, company_id, cmd, raw):
    rows, first, truncated = Export.read_all(read, company_id, cmd, raw)
    if truncated:
        raise BookflowError('E_VALUE_RANGE', message=(
            f'This report exceeds the printable limit of {len(rows)} rows. '
            'Narrow the dates or filters and print again. No partial report was produced.'))
    return {**first, 'rows': rows, 'count': len(rows), 'next_cursor': None}


def install(app, *, run, page_error, form_page):
    @app.get('/c/{company_id}/report/{verb}/' + SEGMENT, name='report-print-all')
    def full_report(company_id: str, verb: str, request: Request):
        cmd = registry.get(f'report {verb}')
        if not Export.is_report(cmd):
            return page_error(request, BookflowError('E_USAGE', message=f'unknown report {verb}'), company_id=company_id)
        raw = {}
        try:
            raw = Export.inputs_from(cmd, request.query_params)
            result = complete(lambda name, inputs, company: run(request, name, inputs, company), company_id, cmd, raw)
            # The successful report call already validated these same inputs.
            inputs = cmd.input_model.model_validate(raw).model_dump(mode='json')
            filters = [(key, Naming.column_label(key), Export._readable(value))
                       for key, value in inputs.items() if key not in {'cursor', 'limit'} and value is not None]
            response = form_page(request, company_id, 'report', verb, None, result=result,
                report_input=inputs, report_full={'filters': filters,
                    'return_url': return_url(company_id, verb, inputs)})
        except BookflowError as error:
            response = page_error(request, error, company_id=company_id,
                                  restart_url=return_url(company_id, verb, raw))
        response.headers['Cache-Control'] = 'no-store'
        return response
