"""What a public items cursor is bound to, and what it must survive.

The continuation domain is separate from the private one, and a token carries no
authority of its own: every page reauthorizes the whole connected closure, so a
capability lost between pages refuses the next page even when the rows the user
can see are unchanged. Everything here runs with no host at all, over the
offline dispatch path and the private execution bridge.
"""
import json

import pytest

import bookflow
from bookflow.company import deposit_public_reads as reads
from bookflow.company import deposit_read_models as m
from bookflow.company import deposit_read_pages as pages
from bookflow.company import deposit_queries as q
from bookflow.core.errors import BookflowError
from tests import deposit_public_support as support

FIXED = '2026-06-11T09:00:00+00:00'


@pytest.fixture(scope='module')
def world(public_deposit_world):
    support.stop(public_deposit_world)
    yield public_deposit_world
    support.stop(public_deposit_world)


def native(world, command, raw):
    return bookflow.connect(data_root=str(world['root'])).run(command, raw, company=world['cid'])


def page(world, deposit, kind, *, limit=1, cursor=None, revision=None):
    raw = {'deposit': deposit, 'kind': kind, 'page': {'limit': limit}}
    if cursor is not None:
        raw['page']['cursor'] = cursor
    if revision is not None:
        raw['revision_number'] = revision
    return native(world, 'deposit items', raw)


# ------------------------------------------------------------------- binding


def test_a_public_cursor_is_not_a_private_cursor_in_either_direction(world):
    first = page(world, world['deposit'], 'additional')
    assert first['next_cursor']
    with support.offline_reading(world) as (session, audience, binding):
        private = q.items(session, m.ItemsInput(deposit=world['deposit'], kind='additional',
                                                page=m.PageInput(limit=1)), binding=binding)
        assert private.next_cursor and private.next_cursor != first['next_cursor']
        # Neither domain verifies the other's token.
        with pytest.raises(BookflowError) as caught:
            pages.decode(session, binding, 'items', first['next_cursor'])
        assert caught.value.code == 'E_VALIDATION' and caught.value.details == {'field': 'cursor'}
        with pytest.raises(BookflowError) as caught:
            pages.decode(session, binding, reads.PURPOSE, private.next_cursor)
        assert caught.value.code == 'E_VALIDATION'
        # And the public domain really is the one the public pages use.
        assert pages.decode(session, binding, reads.PURPOSE, first['next_cursor'])['purpose'] \
            == reads.PURPOSE == 'public.items'
    with pytest.raises(BookflowError) as caught:
        page(world, world['deposit'], 'additional', cursor=private.next_cursor)
    assert caught.value.code == 'E_VALIDATION' and caught.value.details == {'field': 'cursor'}


def test_a_cursor_is_bound_to_its_collection_kind_and_its_deposit(world):
    sources = page(world, world['deposit'], 'sources')
    additional = page(world, world['deposit'], 'additional')
    assert additional['next_cursor']
    # The sources collection has one row, so it hands out no continuation at all.
    assert sources['next_cursor'] is None and sources['total_count'] == 1
    with pytest.raises(BookflowError) as caught:
        page(world, world['deposit'], 'sources', cursor=additional['next_cursor'])
    assert caught.value.code == 'E_QUERY_STALE'
    with pytest.raises(BookflowError) as caught:
        page(world, world['corrected'], 'additional', cursor=additional['next_cursor'])
    assert caught.value.code == 'E_QUERY_STALE'


def test_a_cursor_is_bound_to_its_selected_revision(world):
    """Two immutable revisions: a cursor over one is not a cursor over the other."""
    first = page(world, world['corrected'], 'additional', revision=1)
    second = page(world, world['corrected'], 'additional', revision=2)
    assert first['selected']['revision_number'] == 1 and second['selected']['revision_number'] == 2
    assert first['next_cursor'] and second['next_cursor']
    with pytest.raises(BookflowError) as caught:
        page(world, world['corrected'], 'additional', cursor=first['next_cursor'], revision=2)
    assert caught.value.code == 'E_VALIDATION' and caught.value.details == {'field': 'cursor'}
    # Without an explicit revision the cursor names its own, not the current one.
    resumed = page(world, world['corrected'], 'additional', cursor=first['next_cursor'])
    assert resumed['selected'] == first['selected'] and resumed['selected']['revision_number'] == 1


def test_a_cursor_survives_the_current_revision_moving_under_it(world):
    """The pin is the selected revision; making another one current does not stale it."""
    party = dict(kind='customer', id=world['customer'])
    document = dict(mode='inline', deposit_to=world['bank'], date='2026-06-10', memo='Pin survival',
                    additional=[dict(received_from=party, from_account=world['income'],
                                     amount=f'{index + 1}.00', memo=f'Pin row {index + 1}')
                                for index in range(3)])
    posted = support.post(world['client'], dict(operation_key='public-pin-post', document=document))
    deposit = posted.current.id
    first = page(world, deposit, 'additional', limit=1)
    assert first['selected']['revision_number'] == 1 and first['next_cursor']
    changed = support.replacement(posted, document)
    changed['memo'] = 'Pin survival corrected'
    changed['additional'][2]['amount'] = '30.00'
    support.update(world['client'], dict(deposit=deposit, expected_version=posted.current.version,
                                         operation_key='public-pin-update', document=changed))
    current = native(world, 'deposit show', {'deposit': deposit})
    # An unqualified show follows the current revision; the cursor does not.
    assert current['current']['version'] == 2 and current['selected_is_current']
    assert current['selected']['pin']['revision_number'] == 2
    resumed = page(world, deposit, 'additional', limit=1, cursor=first['next_cursor'])
    assert resumed['selected'] == first['selected'] == {'deposit_id': deposit,
                                                        'revision_id': first['selected']['revision_id'],
                                                        'revision_number': 1}
    assert resumed['items'][0]['memo'] == 'Pin row 2'
    assert resumed['fingerprint'] == first['fingerprint']
    # The current state on the page is the current one, not the pinned one.
    assert resumed['current']['version'] == 2
    # And the new revision pages independently, with its changed row.
    latest = page(world, deposit, 'additional', limit=3, revision=2)
    assert [row['amount']['minor_units'] for row in latest['items']] == [100, 200, 3000]


def test_the_signing_material_is_the_company_key_and_has_no_rotation(world):
    """G1, as behaviour: the token is company-keyed, unversioned and unrotatable."""
    from bookflow.core import registry
    import base64
    first = page(world, world['deposit'], 'additional')
    raw = base64.urlsafe_b64decode(first['next_cursor'] + '=' * (-len(first['next_cursor']) % 4))
    body = json.loads(raw[:-32])
    # No key id travels in the token, so there is nothing a verifier could
    # select between: one company key verifies it or nothing does.
    assert set(body) == {'v', 'purpose', 'fp', 'position'}
    registry.load_all()
    assert not [name for name in registry.REGISTRY
                if 'rotate' in name or 'cursor-key' in name or 'rotation' in name]
    with support.offline_reading(world) as (session, audience, binding):
        from bookflow.company.ledger_reports import _cursor_key
        key, scope = pages._scope(session, binding, reads.PURPOSE)
        assert key == _cursor_key(session.company) and len(key) == 32
        assert reads.PURPOSE.encode() in scope and session.company_row['id'].encode() in scope
        assert binding.user_id.encode() in scope


# --------------------------------------------- reauthorization on every page


def test_every_page_reauthorizes_the_whole_closure_including_off_page_sources(world):
    """A capability lost between pages refuses the next page, not just the first."""
    party = dict(kind='customer', id=world['customer'])
    payment = world['client'].run('payment receive', dict(
        customer=world['customer'], date='2026-06-02', amount='2.00',
        payment_method=world['method'], operation_key='public-offpage-pay'),
        company=support.COMPANY)
    from tests.test_work_billing_lifecycle import accepted, bill
    invoice = bill(world['client'], accepted(world['client'], dict(customer=world['customer'],
                                                                  item='Sale witness service')),
                   key='public-offpage-work')
    linked = world['client'].run('payment receive', dict(
        customer=world['customer'], date='2026-06-02', amount='1.00',
        payment_method=world['method'], operation_key='public-offpage-work-pay',
        applications=dict(mode='inline', items=[dict(invoice=invoice['id'], expected_version=1,
                                                     amount='1.00')])), company=support.COMPANY)
    posted = support.post(world['client'], dict(operation_key='public-offpage-post', document=dict(
        mode='inline', deposit_to=world['bank'], date='2026-06-10', memo='Off-page closure',
        sources=[dict(source_type='payment', source=payment['id'], expected_version=1),
                 dict(source_type='payment', source=linked['id'], expected_version=1)],
        additional=[dict(received_from=party, from_account=world['income'],
                         amount='4.00', memo='Off-page cash')])))
    deposit = posted.current.id
    first = page(world, deposit, 'sources', limit=1)
    assert first['total_count'] == 2 and first['next_cursor']
    # The work-linked receipt is on page two; the deny still refuses page one.
    assert first['items'][0]['source']['transaction_id'] == payment['id']
    with support.denied_offline(world, ('customer-work',)):
        for cursor in (None, first['next_cursor']):
            with pytest.raises(BookflowError) as caught:
                page(world, deposit, 'sources', limit=1, cursor=cursor)
            assert caught.value.code == 'E_RECORD_NOT_FOUND' and not caught.value.details
        with pytest.raises(BookflowError) as caught:
            native(world, 'deposit show', {'deposit': deposit})
        assert caught.value.code == 'E_RECORD_NOT_FOUND'
    resumed = page(world, deposit, 'sources', limit=1, cursor=first['next_cursor'])
    assert resumed['items'][0]['source']['transaction_id'] == linked['id']


# ------------------------------------------- hidden changes and stale signals


def test_a_hidden_only_reference_change_never_stales_a_page(world):
    """Full payload comparison at one controlled instant, not just the cursor."""
    with support.denied_offline(world, ('class',)):
        with support.offline_reading(world) as (session, audience, binding):
            before = reads.items(session, m.ItemsInput(
                deposit=world['deposit'], kind='additional', page=m.PageInput(limit=1)),
                audience=audience, at=FIXED)
            before_detail = reads.show(session, m.ShowInput(deposit=world['deposit']),
                                       audience=audience, at=FIXED)
            before_guard = q.show(session, m.ShowInput(deposit=world['deposit']),
                                  binding=binding).dependencies.guard
        support.start(world, serving=True)
        support.set_denies(world, ())
        support.api(world)('class.update', {'class': world['grouping'],
                                            'expected_version': world.setdefault('class_version', 1),
                                            'name': 'Continuation renamed class'},
                           company=world['cid'],
                           headers={'X-Bookflow-Reason': 'Continuation rename'})
        world['class_version'] += 1
        support.set_denies(world, ('class',))
        support.stop(world)
        with support.offline_reading(world) as (session, audience, binding):
            after = reads.items(session, m.ItemsInput(
                deposit=world['deposit'], kind='additional', page=m.PageInput(limit=1)),
                audience=audience, at=FIXED)
            after_detail = reads.show(session, m.ShowInput(deposit=world['deposit']),
                                      audience=audience, at=FIXED)
            after_guard = q.show(session, m.ShowInput(deposit=world['deposit']),
                                 binding=binding).dependencies.guard
            # The cursor issued before the hidden change still continues.
            second = reads.items(session, m.ItemsInput(
                deposit=world['deposit'], kind='additional',
                page=m.PageInput(limit=1, cursor=before.next_cursor)), audience=audience, at=FIXED)
    assert before_guard and after_guard and before_guard != after_guard
    # Everything, byte for byte, at the same observation instant.
    assert after.model_dump(mode='json') == before.model_dump(mode='json')
    assert after_detail.model_dump(mode='json') == before_detail.model_dump(mode='json')
    assert second.items and second.items[0].row_id != before.items[0].row_id


def test_a_disclosed_row_change_does_stale_an_outstanding_page(world):
    """The other half: the fingerprint is over the disclosed relation."""
    party = dict(kind='customer', id=world['customer'])
    document = dict(mode='inline', deposit_to=world['bank'], date='2026-06-10', memo='Stale witness',
                    additional=[dict(received_from=party, from_account=world['income'],
                                     amount='5.00', memo='Stale row one'),
                                dict(received_from=party, from_account=world['income'],
                                     amount='6.00', memo='Stale row two')])
    posted = support.post(world['client'], dict(operation_key='public-stale-post', document=document))
    deposit = posted.current.id
    first = page(world, deposit, 'additional', limit=1)
    assert first['next_cursor']
    changed = support.replacement(posted, document)
    changed['additional'][1]['memo'] = 'Stale row two edited'
    support.update(world['client'], dict(deposit=deposit, expected_version=posted.current.version,
                                         operation_key='public-stale-update', document=changed))
    # The pinned revision is immutable, so its own cursor still resumes.
    resumed = page(world, deposit, 'additional', limit=1, cursor=first['next_cursor'])
    assert resumed['items'][0]['memo'] == 'Stale row two'
    # A cursor cannot be carried onto the revision that actually changed.
    with pytest.raises(BookflowError) as caught:
        page(world, deposit, 'additional', limit=1, cursor=first['next_cursor'], revision=2)
    assert caught.value.code == 'E_VALIDATION' and caught.value.details == {'field': 'cursor'}
