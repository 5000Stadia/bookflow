"""R112 increment 1: the same outbox through Python, the CLI, HTTP and MCP.

Each surface gets an identical copy of the demo. It lists the templates and their fields, adds one, makes it the
default, previews it and puts the original back; gives the demo customer an address; drafts an invoice email, edits,
reads, releases and exports it, marks it sent and reads its history; drafts a statement and cancels it; and lists the
outbox. Every surface must return the same subjects, recipients, states, attached file hash and history.
"""
import anyio
import pytest

from tests.mcp_matrix_support import Matrix

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('delivery draft', 'delivery update', 'delivery send', 'delivery cancel', 'delivery mark-sent',
                      'delivery export', 'delivery show', 'delivery query', 'delivery history',
                      'delivery-template create', 'delivery-template update', 'delivery-template deactivate',
                      'delivery-template show', 'delivery-template query', 'delivery-template fields',
                      'delivery-template preview',
                      # Increment 2: no transport is registered, so the queue is read, run and refused the same way.
                      'delivery approve', 'delivery retry', 'delivery run', 'delivery-connection add',
                      'delivery-connection update', 'delivery-connection enable', 'delivery-connection disable',
                      'delivery-connection remove', 'delivery-connection check', 'delivery-connection show',
                      'delivery-connection query'))


@pytest.mark.timeout(900)
def test_the_same_delivery_through_python_cli_http_and_mcp(root, tmp_path):
    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            results = {}
            for surface in SURFACES:
                call = lambda name, raw, **kw: matrix.call(surface, name, raw, **kw)  # noqa: E731
                invoice = await call('invoice show', {'invoice': 'DEMO-SALE-INV-ACTIVE'})
                await call('customer update', {'customer': invoice['customer_id'], 'email': 'ap@example.test'})
                fields = await call('delivery-template fields', {'kind': 'invoice'})
                made = await call('delivery-template create', {'kind': 'invoice', 'name': 'Brief',
                                                               'subject': 'Invoice {{document.number}}',
                                                               'body': 'Total {{document.total}}.'})
                await call('delivery-template update', {'template': made['id'], 'is_default': True})
                preview = await call('delivery-template preview', {'kind': 'invoice',
                                                                   'document': 'DEMO-SALE-INV-ACTIVE'})
                await call('delivery-template update', {'template': 'Invoice', 'kind': 'invoice', 'is_default': True})
                retired = await call('delivery-template deactivate', {'template': made['id']})
                shown_template = await call('delivery-template show', {'template': made['id']})
                templates = await call('delivery-template query', {'include_inactive': True})
                draft = await call('delivery draft', {'kind': 'invoice', 'document': 'DEMO-SALE-INV-ACTIVE'})
                edited = await call('delivery update', {'message': draft['number'], 'subject': 'Your invoice'})
                shown = await call('delivery show', {'message': draft['number']})
                released = await call('delivery send', {'messages': [draft['number']]})
                approved = await call('delivery approve', {'messages': [draft['number']]})
                retry = await call('delivery retry', {'message': draft['number']}, rejected=True)
                ran = await call('delivery run', {})
                connections = await call('delivery-connection query', {})
                added = await call('delivery-connection add', {'name': 'Office', 'kind': 'mcp-mail'}, rejected=True)
                missing = [await call(f'delivery-connection {verb}', {'connection': 'Office'}, rejected=True)
                           for verb in ('update', 'enable', 'disable', 'remove', 'check', 'show')]
                queue = (approved['count'], retry['code'], ran['claimed'], ran['connection'], connections['rows'],
                         connections['kinds_available'], added['code'], tuple(m['code'] for m in missing))
                exported = await call('delivery export', {'messages': [draft['number']]})
                sent = await call('delivery mark-sent', {'message': draft['number'], 'method': 'webmail'})
                history = await call('delivery history', {'message': draft['number']})
                statement = await call('delivery draft', {'kind': 'statement', 'customer': invoice['customer_id'],
                                                          'date_from': '2025-12-01', 'date_to': '2025-12-31'})
                cancelled = await call('delivery cancel', {'message': statement['number']})
                outbox = await call('delivery query', {'status': 'all'})
                results[surface] = (fields, preview, retired, shown_template, templates, edited, shown, released,
                                    exported, sent, history, cancelled, outbox, queue)
            return results
        finally:
            await matrix.close()

    results = anyio.run(witness)
    shapes = set()
    for surface, (fields, preview, retired, shown_template, templates, edited, shown, released, exported, sent,
                  history, cancelled, outbox, queue) in results.items():
        assert queue == (0, 'E_WORK_DEPENDENCY', 0, 'none', [], [], 'E_VALIDATION', ('E_RECORD_NOT_FOUND',) * 6), \
            (surface, queue)
        assert preview['subject'] == 'Invoice DEMO-SALE-INV-ACTIVE' and preview['template_name'] == 'Brief', surface
        assert retired['active'] is False and shown_template['name'] == 'Brief', surface
        assert edited['subject'] == shown['subject'] == 'Your invoice' and shown['to'] == ['ap@example.test'], surface
        assert released['messages'][0]['status'] == 'ready' and exported['exported'] == [shown['number']], surface
        assert sent['status_label'] == 'Sent by hand' and cancelled['status'] == 'cancelled', surface
        shapes.add((len(fields['fields']), len(templates['rows']), shown['number'], shown['documents'][0]['sha256'],
                    shown['body'], tuple(e['event'] for e in history['events']),
                    tuple((r['number'], r['status']) for r in outbox['rows'])))
    assert len(shapes) == 1, shapes
