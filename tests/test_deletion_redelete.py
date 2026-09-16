"""A second delete says the document is already deleted, not that its version is stale.

Deleting a record twice is what a person does when they are not sure the first one took,
and what an agent does when it retries. The answer has to end the attempt. A stale-version
error does not: the version is stale *because* the delete happened, so re-reading it and
trying again earns the same error for ever. Every family answers the same way here, and the
three cases that could be confused with each other are kept apart -- a replay of the same
request still succeeds, and a record that is merely out of date still gets its version
conflict.
"""
import pytest

from bookflow.core.errors import BookflowError
from tests.test_bill_item_lines import books, _bill
from tests.test_purchase_deletion import enable, location
from tests.payment_raw_evidence import database

# Every family with shipped retained-deletion storage, by the noun a person types.
FAMILIES = ('check', 'card-charge', 'invoice', 'sales-receipt', 'payment', 'bill')

ALREADY = 'already deleted and cannot be deleted again'


def enter(books, noun):
    """One posted document of this family, entered the way its own tests enter one."""
    run = books['run']
    if noun in ('check', 'card-charge'):
        account = books['bank'] if noun == 'check' else books['client'].account.create(
            company=books['company'], name='Redelete card', type='credit_card')['id']
        return run(noun + ' post', dict(account=account, date='2017-03-03', amount='40.00',
            expenses=[dict(account=books['freight'], amount='40.00')]), reason='Pay the carrier')
    if noun in ('invoice', 'sales-receipt'):
        from tests.test_sales_deletion import sale
        return sale(books, noun)[1]
    if noun == 'payment':
        from tests.test_payment_deletion import receive
        return receive(books)
    return run('bill post', _bill(books), reason='Enter the bill')


def version_of(books, noun, identity):
    return books['run'](noun + ' show', {noun.replace('-', '_'): identity})['version']


def void_raw(noun, identity, version):
    """A void of this family, with the permanent key the receipt family requires."""
    raw = {noun.replace('-', '_'): identity, 'expected_version': version}
    if noun == 'payment':
        raw['operation_key'] = 'prior-void'
    return raw


@pytest.mark.parametrize('noun', FAMILIES)
def test_a_second_delete_says_the_document_is_gone_rather_than_that_the_version_moved(books, noun):
    run = books['run']
    selector = noun.replace('-', '_')
    post = enter(books, noun)
    entered = version_of(books, noun, post['id'])
    path = location(books)
    enable(books, noun)
    raw = {selector: post['id'], 'expected_version': entered, 'operation_key': 'first-delete'}
    deleted = run(noun + ' delete', raw, reason='Remove the duplicate')
    assert deleted['status'] == 'deleted'
    after = database(path)

    # The same key carrying the same request is a replay, and it still succeeds unchanged.
    # The new guard must not swallow it: a retry that already worked has not failed.
    replay = run(noun + ' delete', raw, reason='Remove the duplicate')
    assert replay['idempotent_replay'] and not replay['changed']
    assert replay['id'] == deleted['id'] and replay['version'] == deleted['version']
    assert database(path) == after

    # A fresh key is a new operation, and it is told what actually happened -- both at the
    # version the caller was holding when it asked, and at the version the retained record
    # now carries, because a person who re-reads the record and tries again must not be
    # sent round the same loop.
    for attempt, version in (('held', entered), ('reread', deleted['version'])):
        with pytest.raises(BookflowError) as refused:
            run(noun + ' delete', {selector: post['id'], 'expected_version': version,
                                   'operation_key': 'fresh-' + attempt}, reason='Try it again')
        assert refused.value.code == 'E_VALIDATION', (noun, attempt, refused.value.code)
        problem = refused.value.details['fields'][0]['problem']
        assert ALREADY in problem, (noun, attempt, problem)
        assert database(path) == after, (noun, attempt)


@pytest.mark.parametrize('noun', FAMILIES)
def test_a_stale_version_on_a_record_that_is_still_there_is_still_a_version_conflict(books, noun):
    """The guard above must not have swallowed the ordinary concurrency answer."""
    run = books['run']
    post = enter(books, noun)
    stale = version_of(books, noun, post['id'])
    # Voided, not deleted: the record is still there and its version has simply moved on.
    run(noun + ' void', void_raw(noun, post['id'], stale), reason='Prior cancellation')
    path = location(books)
    enable(books, noun)
    before = database(path)
    with pytest.raises(BookflowError) as conflict:
        run(noun + ' delete', {noun.replace('-', '_'): post['id'], 'expected_version': stale,
                               'operation_key': 'stale-but-live'}, reason='Delete at a stale version')
    assert conflict.value.code == 'E_VERSION_CONFLICT', (noun, conflict.value.code)
    assert database(path) == before
