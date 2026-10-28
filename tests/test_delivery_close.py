"""R112 increment 5, the close: criterion 10's demo (templates, messages in several states, a schedule and no
connection) and the closing trial's fix (an owner's words, email and every month, find the delivery commands)."""
from bookflow.adapters.mcp.catalog import command_help, list_commands

# The demo's own outbox (seed.toml, R112 increment 5): M-1001 sent by hand, M-1002 a draft statement. Tests that
# claim something about every message they made leave these out.
DEMO_OUTBOX = frozenset({'M-1001', 'M-1002'})


def own(rows):
    """The rows a test made: every outbox row but the demo's own."""
    return [r for r in rows if r['number'] not in DEMO_OUTBOX]


def test_the_demo_carries_messages_in_several_states_a_template_edit_a_schedule_and_no_connection(client):
    company = client.company.list()['items'][0]['company_id']

    def run(name, raw):
        return client.run(name, raw, company=company)

    outbox = run('delivery query', {'status': 'all'})
    assert outbox['connection'] == 'none'
    states = {(m['number'], m['kind'], m['status'], m['status_label']) for m in outbox['rows']}
    assert states == {('M-1001', 'invoice', 'sent', 'Sent by hand'), ('M-1002', 'statement', 'draft', 'Draft')}
    sent = next(m for m in outbox['rows'] if m['status'] == 'sent')
    assert [e['event'] for e in run('delivery history', {'message': sent['number']})['events']
            if e['event'] in ('exported', 'marked_sent')] == ['exported', 'marked_sent']
    statement = next(t for t in run('delivery-template query', {})['rows'] if t['kind'] == 'statement')
    assert 'We accept checks and bank transfers.' in statement['body'] and statement['version'] > 1
    [schedule] = run('delivery-schedule query', {})['rows']
    assert (schedule['name'], schedule['mode'], schedule['active']) == ('Daily invoice emails', 'draft', False)
    assert run('delivery-connection query', {})['rows'] == []


def test_owner_words_find_the_delivery_commands():
    for words, wanted in (('email', 'delivery draft'), ('outbox', 'delivery query'),
                          ('recurring', 'delivery-schedule create'), ('every month', 'delivery-schedule create')):
        names, cursor = [], None
        while True:
            page = list_commands(prefix=words, cursor=cursor)
            names += [c['name'] for c in page['commands']]
            cursor = page['next_cursor']
            if cursor is None:
                break
        assert wanted in names, words
    assert 'delivery-schedule create' in command_help('delivery batch')['documentation']
