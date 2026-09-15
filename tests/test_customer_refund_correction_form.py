"""The correction window of a customer refund: what it shows, and what saving it writes.

`customer-refund update` was examined on its own. This is the half a person touches, and the
danger here is not the command: it is a control that shows nothing where something is stored,
and a save that therefore writes -- or drops -- what nobody typed. So every value asserted
below is read off the page the workbench actually renders, and every save is the real form
POST of exactly the controls that page rendered, submitted the way a browser submits them.

The witness that matters most is the untouched save. A person who opens the correction, reads
it and closes it by pressing Save must leave the company database byte for byte what it was:
no revision, no reversal batch, no released and retaken credit. The edit test beside it is
what keeps that check honest -- the same POST with one field changed has to move the same
bytes, or the equality above would be proving nothing.
"""
from html.parser import HTMLParser
from pathlib import Path

from tests.payment_raw_evidence import database
from tests.test_credit_windows import _books, _browser
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

WB = {'X-Bookflow-Workbench': '1'}
FIRST = '18.00'
SECOND = '7.00'


class Controls(HTMLParser):
    """Every control a browser would submit from one rendered form, in submitted order.

    Rows inside `<template>` are the grid's blank row, which a browser does not submit
    until somebody presses Add entry, so they are skipped here for the same reason.
    """

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.values = {}
        self._templates = 0
        self._textarea = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'template':
            self._templates += 1
            return
        if self._templates or 'name' not in attrs:
            return
        if tag == 'input':
            if attrs.get('type') == 'checkbox' and attrs.get('checked') is None:
                return
            self.values[attrs['name']] = attrs.get('value') or ''
        elif tag == 'select':
            self.values.setdefault(attrs['name'], '')
        elif tag == 'textarea':
            self._textarea = attrs['name']
            self.values[attrs['name']] = ''

    def handle_endtag(self, tag):
        if tag == 'template' and self._templates:
            self._templates -= 1
        elif tag == 'textarea':
            self._textarea = None

    def handle_data(self, data):
        if self._textarea is not None:
            self.values[self._textarea] += data


def _location(hosted):
    return sorted(Path(hosted.root).rglob('company.db'))[0]


def _refunded(books, *, amounts=(FIRST,), explicit=False, **extra):
    """A credit per amount, all of them refunded in one document out of the same bank."""
    ok = books['ok']
    sources = []
    for amount in amounts:
        credit = ok('credit-memo.post', dict(
            date='2026-03-10', customer=books['customer'],
            lines=[dict(item=books['item'], quantity='1', unit_price=amount)]))
        sources.append(dict(credit_memo=credit['id'], **({'amount': amount} if explicit else {})))
    return ok('customer-refund.post', dict(
        date='2026-03-20', funding_account=books['bank'], method=books['method'],
        check_number='2041', reference='March refund', memo='Cheque 2041', sources=sources,
        **extra))


def _form(browser, company, refund_id):
    page = browser.get(f'/c/{company}/customer-refund/{refund_id}/update')
    assert page.status_code == 200, page.text[:400]
    return page.text, Controls(page.text).values


def _save(browser, company, refund_id, controls, reason=None):
    body = {**controls, 'action': 'submit'}
    if reason is not None:
        body['ctx:reason'] = reason
    saved = browser.post(f'/c/{company}/customer-refund/{refund_id}/update', data=body,
                         headers=WB, follow_redirects=False)
    assert saved.status_code == 303, (saved.status_code, saved.text[-2500:])
    return saved


def test_the_correction_window_shows_every_value_the_refund_stored(hosted):
    """Prefill parity, control by control, against what `customer-refund show` says is stored."""
    books = _books(hosted)
    company = hosted.company_id
    books['ok']('company.update', dict(use_classes=True))
    klass = books['ok']('class.create', dict(name='CW refund job'))['id']
    refund = _refunded(books, amounts=(FIRST, SECOND), explicit=True, class_id=klass)
    shown = books['ok']('customer-refund.show', dict(refund=refund['id']))
    profile = shown['revision']['profile']
    _, controls = _form(_browser(hosted), company, refund['id'])

    assert controls['f:date'] == shown['revision']['date'] == '2026-03-20'
    assert controls['f:number'] == shown['revision']['number']
    assert controls['f:memo'] == shown['revision']['memo'] == 'Cheque 2041'
    assert controls['f:reference'] == profile['reference'] == 'March refund'
    assert controls['f:check_number'] == profile['check_number'] == '2041'
    assert controls['f:funding_account'] == profile['funding_account']['id'] == books['bank']
    assert controls['f:method'] == profile['payment_method']['id'] == books['method']
    assert controls['f:customer'] == shown['customer_id'] == books['customer']
    assert controls['f:class_id'] == shown['revision']['lines'][0]['class_id'] == klass
    assert controls['f:expected_version'] == str(shown['version'])
    # The grid is the credits being paid out: one row each, in the document's own order.
    assert [(controls.get(f'c:sources:{index}:credit_memo'),
             controls.get(f'c:sources:{index}:amount')) for index in range(3)] == [
        (profile['sources'][0]['credit_memo_id'], FIRST),
        (profile['sources'][1]['credit_memo_id'], SECOND),
        (None, None)]

    # And the window is reached from the saved refund rather than typed as an address.
    detail = _browser(hosted).get(f'/c/{company}/customer-refund/{refund["id"]}')
    assert 'data-refund-correct' in detail.text
    assert f'/customer-refund/{refund["id"]}/update"' in detail.text
    assert 'There is no correction' not in detail.text


def test_saving_the_correction_untouched_writes_nothing_at_all(hosted):
    """The whole point. Open the correction, change nothing, press Save: nothing may move."""
    books = _books(hosted)
    company = hosted.company_id
    refund = _refunded(books, amounts=(FIRST, SECOND), explicit=True)
    browser = _browser(hosted)
    _, controls = _form(browser, company, refund['id'])
    location = _location(hosted)

    before = database(location)
    _save(browser, company, refund['id'], controls)
    assert database(location) == before, 'an untouched save moved the company database'

    after = books['ok']('customer-refund.show', dict(refund=refund['id']))
    assert after['version'] == refund['version']
    assert after['revision']['revision_number'] == 1
    assert after['revision']['id'] == refund['revision']['id']
    assert after['revision']['profile'] == refund['revision']['profile']
    assert len(after['consumptions']) == len(refund['consumptions']) == 2


def test_one_changed_field_moves_the_same_bytes_and_keeps_the_revision_it_replaced(hosted):
    """What keeps the equality above honest, and the prior revision a correction promises."""
    books = _books(hosted)
    company = hosted.company_id
    refund = _refunded(books, explicit=True)
    browser = _browser(hosted)
    _, controls = _form(browser, company, refund['id'])
    location = _location(hosted)

    before = database(location)
    _save(browser, company, refund['id'], {**controls, 'f:memo': 'Cheque 2042'},
          reason='Wrong cheque number written down')
    assert database(location) != before

    after = books['ok']('customer-refund.show', dict(refund=refund['id']))
    assert after['revision']['revision_number'] == 2
    assert after['revision']['memo'] == 'Cheque 2042'
    assert after['total_minor_units'] == refund['total_minor_units']
    previous = books['ok']('customer-refund.show',
                           dict(refund=refund['id'], revision_number=1))
    assert previous['revision']['memo'] == 'Cheque 2041'
    assert previous['revision']['id'] == refund['revision']['id']


def test_a_source_amount_the_refund_defaulted_stays_defaulted(hosted):
    """The trap this projection exists to avoid.

    A refund posted with no amount pays out everything its credit is worth and captures that
    as a defaulted amount. Putting a figure in that cell would submit an explicit amount, flip
    the captured origin and turn an untouched save into a real correction -- a reversal batch
    and a replacement batch for an edit nobody made.
    """
    books = _books(hosted)
    company = hosted.company_id
    refund = _refunded(books)
    assert refund['revision']['profile']['origins']['amount']['kind'] == 'default'
    browser = _browser(hosted)
    _, controls = _form(browser, company, refund['id'])
    assert controls['c:sources:0:amount'] == ''
    assert controls['c:sources:0:credit_memo'] == \
        refund['revision']['profile']['sources'][0]['credit_memo_id']
    location = _location(hosted)

    before = database(location)
    _save(browser, company, refund['id'], controls)
    assert database(location) == before
    after = books['ok']('customer-refund.show', dict(refund=refund['id']))
    assert after['revision']['profile']['origins']['amount']['kind'] == 'default'
    assert after['revision']['revision_number'] == 1


def test_correcting_one_credit_of_two_leaves_the_other_exactly_as_it_was(hosted):
    """`sources` replaces the whole list, so the untouched row has to be in the form already."""
    books = _books(hosted)
    company = hosted.company_id
    refund = _refunded(books, amounts=(FIRST, SECOND), explicit=True)
    first, second = refund['revision']['profile']['sources']
    browser = _browser(hosted)
    _, controls = _form(browser, company, refund['id'])

    _save(browser, company, refund['id'], {**controls, 'c:sources:0:amount': '12.00'},
          reason='Only twelve of the first credit came back')
    after = books['ok']('customer-refund.show', dict(refund=refund['id']))
    assert [(row['credit_memo_id'], row['amount_minor_units'])
            for row in after['revision']['profile']['sources']] == [
        (first['credit_memo_id'], 1200), (second['credit_memo_id'], 700)]
    assert after['total_minor_units'] == 1900
    # The credit whose row nobody touched is worth what it was; the corrected one got the
    # difference back rather than being released and forgotten.
    worth = lambda credit_id: books['ok'](
        'credit-memo.show', dict(credit_memo=credit_id))['source_current']
    assert worth(first['credit_memo_id'])['available_minor_units'] == 1800 - 1200
    assert worth(second['credit_memo_id'])['available_minor_units'] == 0
