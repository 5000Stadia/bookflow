"""Held carts share working state, never postings, with versioned lifecycle boundaries."""
from concurrent.futures import ThreadPoolExecutor
import pytest
import bookflow
from tests.test_pos_sale import Company, _reads


@pytest.fixture
def co(root):
    co = Company(root)
    info = co.run('company show')
    co.run('company update', {'pos_enabled': True, 'expected_version': info['info_version']})
    return co


def hold(co, ticket='held-cart', **kw):
    return co.run('pos ticket hold', dict(ticket=ticket, lines=[{'item': 'Pipe Sealant', 'quantity': '2'}], **kw))


def change(co, verb, ticket):
    return co.run('pos ticket ' + verb, {'ticket': ticket['id'], 'expected_version': ticket['version']})


def complete(co, ticket, **kw):
    cart = dict(pos_register=ticket['pos_register']['id'], customer=ticket['customer']['id'] if ticket['customer'] else None,
                lines=ticket['lines'])
    total = co.run('pos sale preview', cart)['total']['amount']
    return co.run('pos sale complete', dict(cart, ticket=ticket['id'], ticket_version=ticket['version'],
        expected_total=total, tender={'payment_method': 'Cash'}, **kw))


def facts(co):
    return _reads(co)


def test_hold_resume_discard_do_not_post_and_survive_another_client(co, root):
    before = facts(co)
    trial = co.run('pos ticket hold', {'ticket': 'trial', 'lines': [{'item': 'Pipe Sealant'}]}, dry_run=True)
    assert trial['status'] == 'held'
    assert co.run('pos ticket list')['items'] == []
    ticket = hold(co, label='Waiting for Casey')
    assert ticket['version'] == 1 and ticket['customer'] is None
    assert ticket['clerk_name'] and ticket['clerk_id']
    assert ticket['lines'][0]['item'] == co.run('item show', {'item': 'Pipe Sealant'})['id']
    other = Company(root)
    assert other.run('pos ticket list')['items'] == [ticket]
    resumed = change(other, 'resume', ticket)
    assert resumed['status'] == 'open' and resumed['version'] == 2
    assert other.run('pos ticket list')['count'] == 0
    discarded = change(other, 'discard', resumed)
    assert discarded['status'] == 'discarded' and discarded['version'] == 3
    assert facts(co) == before
    activity = co.run('audit list', {'record_type': 'pos_ticket', 'record_id': ticket['id']})
    assert len(activity['items']) == 3
    assert {e['command'] for e in activity['items']} == {'pos ticket hold', 'pos ticket resume', 'pos ticket discard'}


def test_off_refuses_naming_held_ticket_and_discard_unblocks(co):
    ticket = hold(co, label='Casey collecting lunch')
    info = co.run('company show')
    error = co.fails('company update', {'pos_enabled': False, 'expected_version': info['info_version']})
    assert error.code == 'E_WORK_DEPENDENCY'
    assert ticket['label'] in error.message and error.details['ticket'] == ticket['id']
    change(co, 'discard', ticket)
    co.run('company update', {'pos_enabled': False, 'expected_version': info['info_version']})
    for verb, body in [('list', {}), ('resume', {'ticket': ticket['id'], 'expected_version': 2}),
                       ('discard', {'ticket': ticket['id'], 'expected_version': 2}),
                       ('hold', {'ticket': 'new', 'lines': [{'item': 'Pipe Sealant'}]})]:
        assert co.fails('pos ticket ' + verb, body).code == 'E_FEATURE_DISABLED'


def test_strict_versions_block_stale_resume_rehold_discard_and_completion(co):
    ticket = hold(co)
    # Completing while held must not bypass the hand-off.
    with pytest.raises(bookflow.BookflowError) as error:
        complete(co, ticket)
    assert error.value.code == 'E_WORK_DEPENDENCY'
    resumed = change(co, 'resume', ticket)
    for verb in ('resume', 'discard'):
        assert co.fails('pos ticket ' + verb, {'ticket': ticket['id'], 'expected_version': 1}).code == 'E_VERSION_CONFLICT'
    assert co.fails('pos ticket hold', {'ticket': ticket['id'], 'lines': ticket['lines']}).code == 'E_VERSION_CONFLICT'
    reheld = co.run('pos ticket hold', {'ticket': ticket['id'], 'lines': ticket['lines'], 'expected_version': 2})
    assert reheld['version'] == 3
    with pytest.raises(bookflow.BookflowError) as stale:
        complete(co, resumed)
    assert stale.value.code == 'E_VERSION_CONFLICT'
    assert stale.value.details['updated_by'] and stale.value.details['changed_fields']
    resumed = change(co, 'resume', reheld)
    sale = complete(co, resumed)
    assert sale['ticket'] == ticket['id']
    assert complete(co, resumed)['id'] == sale['id']  # permanent sale retry still wins after completed transition
    assert co.fails('pos ticket hold', {'ticket': ticket['id'], 'lines': ticket['lines'], 'expected_version': 4}).code == 'E_WORK_DEPENDENCY'
    assert co.fails('pos ticket discard', {'ticket': ticket['id'], 'expected_version': 5}).code == 'E_WORK_DEPENDENCY'


def test_newest_first_and_original_register_and_identity(co):
    first = hold(co, 'first')
    second = hold(co, 'second')
    assert [t['id'] for t in co.run('pos ticket list')['items']] == ['second', 'first']
    assert 'Pipe Sealant' in first['label']
    resumed = change(co, 'resume', first)
    item = co.run('item show', {'item': 'Pipe Sealant'})
    co.run('item update', {'item': item['id'], 'name': 'Renamed Sealant', 'expected_version': item['version'], 'price': '99.00'})
    preview = co.run('pos sale preview', {'lines': resumed['lines']})
    assert preview['lines'][0]['item_name'] == 'Renamed Sealant'
    assert preview['lines'][0]['unit_price']['amount'] == '99.00'
    co.run('pos-register create', {'name': 'Second'})
    assert co.fails('pos ticket hold', dict(ticket=first['id'], expected_version=2, pos_register='Second',
        lines=first['lines'])).code == 'E_VALIDATION'
    assert co.fails('pos sale complete', dict(ticket=first['id'], ticket_version=2, pos_register='Second',
        lines=first['lines'], expected_total=preview['total']['amount'], tender={'payment_method': 'Cash'})).code == 'E_VALIDATION'


def test_claim_race_has_one_winner(co, root):
    ticket = hold(co)
    def resume(_):
        other = Company(root)
        try:
            return change(other, 'resume', ticket)
        except bookflow.BookflowError as error:
            return error.code
    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(resume, [1, 2]))
    assert sum(isinstance(a, dict) for a in answers) == 1
    assert next(a for a in answers if isinstance(a, str)) in ('E_VERSION_CONFLICT', 'E_DB_BUSY')


def test_lost_hold_resume_discard_answers_replay_without_reapplying(co):
    held = co.run('pos ticket hold', {'ticket': 'retry', 'lines': [{'item': 'Pipe Sealant'}]}, idempotency_key='hold-retry')
    resumed = co.run('pos ticket resume', {'ticket': 'retry', 'expected_version': 1}, idempotency_key='resume-retry')
    repeated = co.run('pos ticket resume', {'ticket': 'retry', 'expected_version': 1}, idempotency_key='resume-retry')
    assert repeated['version'] == resumed['version'] == 2
    co.run('pos ticket hold', {'ticket': 'retry', 'lines': [{'item': 'Pipe Sealant'}]}, idempotency_key='hold-retry')
    assert co.run('pos ticket list')['items'] == []
    discarded = co.run('pos ticket discard', {'ticket': 'retry', 'expected_version': 2}, idempotency_key='discard-retry')
    repeated = co.run('pos ticket discard', {'ticket': 'retry', 'expected_version': 2}, idempotency_key='discard-retry')
    assert repeated['version'] == discarded['version'] == 3
    assert held['version'] == 1
