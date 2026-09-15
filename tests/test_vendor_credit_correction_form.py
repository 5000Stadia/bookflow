"""The correction window of a vendor credit: what it shows, and what saving it writes.

`vendor-credit update` was examined on its own. This is the half a person touches, and the
danger here is not the command: it is a control that shows nothing where something is stored,
and a save that therefore writes -- or drops -- what nobody typed. So every value asserted
below is read off the page the workbench actually renders, and every save is the real form
POST of exactly the controls that page rendered, submitted the way a browser submits them.

The witness that matters most is the untouched save. A person who opens the correction, reads
it and closes it by pressing Save must leave the company database byte for byte what it was:
no revision, no reversal batch, no released and retaken settlement. The edit test beside it is
what keeps that check honest -- the same POST with one field changed has to move the same
bytes, or the equality above would be proving nothing.
"""
from tests.payment_raw_evidence import database
from tests.test_credit_windows import _books, _browser
from tests.test_customer_refund_correction_form import Controls as _Controls, _location
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

WB = {'X-Bookflow-Workbench': '1'}


class Controls(_Controls):
    """The refund window's reader, plus the one kind of control that window has none of.

    A vendor credit's grid carries `class_mode`, which renders as a `<select>`; a browser
    submits the value of its selected option, and the shared reader records every select as
    empty because the refund form has no enumerated control in it. Reading the selection here
    is what makes the POSTs below the POST a browser would actually send -- without it the
    round trip would be proved against a submission no browser makes.
    """

    _select = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'option':
            if not self._templates and self._select is not None and 'selected' in values:
                self.values[self._select] = values.get('value') or ''
            return
        super().handle_starttag(tag, attrs)
        if tag == 'select' and not self._templates and 'name' in values:
            self._select = values['name']

    def handle_endtag(self, tag):
        super().handle_endtag(tag)
        if tag == 'select':
            self._select = None


FIRST = '15.00'
SECOND = '4.00'
THIRD = '6.00'


def _credit(books, *, rows=((FIRST, 'Two boxes back'),), **extra):
    return books['ok']('vendor-credit.post', dict(
        date='2026-03-11', vendor=books['vendor'], supplier_reference='CN-7',
        memo='Returned to the yard',
        expenses=[dict(account=books['expense'], amount=amount, memo=memo)
                  for amount, memo in rows], **extra))


def _form(browser, company, credit_id):
    page = browser.get(f'/c/{company}/vendor-credit/{credit_id}/update')
    assert page.status_code == 200, page.text[:400]
    return page.text, Controls(page.text).values


def _save(browser, company, credit_id, controls, reason=None):
    body = {**controls, 'action': 'submit'}
    if reason is not None:
        body['ctx:reason'] = reason
    saved = browser.post(f'/c/{company}/vendor-credit/{credit_id}/update', data=body,
                         headers=WB, follow_redirects=False)
    assert saved.status_code == 303, (saved.status_code, saved.text[-2500:])
    return saved


def test_the_correction_window_shows_every_value_the_credit_stored(hosted):
    """Prefill parity, control by control, against what `vendor-credit show` says is stored."""
    books = _books(hosted)
    company = hosted.company_id
    books['ok']('company.update', dict(use_classes=True))
    klass = books['ok']('class.create', dict(name='CW credit job'))['id']
    credit = _credit(books, rows=((FIRST, 'Two boxes back'), (SECOND, 'One elbow back')),
                     class_id=klass)
    shown = books['ok']('vendor-credit.show', dict(credit=credit['id']))
    profile = shown['revision']['profile']
    _, controls = _form(_browser(hosted), company, credit['id'])

    assert controls['f:date'] == shown['revision']['date'] == '2026-03-11'
    assert controls['f:number'] == shown['revision']['number']
    assert controls['f:memo'] == shown['revision']['memo'] == 'Returned to the yard'
    assert controls['f:supplier_reference'] == profile['supplier_reference'] == 'CN-7'
    assert controls['f:vendor'] == shown['vendor_id'] == books['vendor']
    assert controls['f:ap_account'] == profile['ap_account']['id'] == shown['ap_account_id']
    assert controls['f:class_id'] == profile['class_id']['id'] == klass
    assert controls['f:expected_version'] == str(shown['version'])
    # The grid is the credited rows: one each, in the document's own order, with the identity
    # that makes a corrected row the same row rather than a replacement for it.
    lines = shown['revision']['expenses']
    assert [(controls.get(f'c:expenses:{index}:line_id'),
             controls.get(f'c:expenses:{index}:account'),
             controls.get(f'c:expenses:{index}:amount'),
             controls.get(f'c:expenses:{index}:memo')) for index in range(3)] == [
        (lines[0]['line_id'], books['expense'], FIRST, 'Two boxes back'),
        (lines[1]['line_id'], books['expense'], SECOND, 'One elbow back'),
        (None, None, None, None)]

    # And the window is reached from the saved credit rather than typed as an address.
    detail = _browser(hosted).get(f'/c/{company}/vendor-credit/{credit["id"]}')
    assert 'data-vendor-credit-correct' in detail.text
    assert f'/vendor-credit/{credit["id"]}/update"' in detail.text
    assert 'corrected rather than voided' in detail.text


def test_saving_the_correction_untouched_writes_nothing_at_all(hosted):
    """The whole point. Open the correction, change nothing, press Save: nothing may move."""
    books = _books(hosted)
    company = hosted.company_id
    credit = _credit(books, rows=((FIRST, 'Two boxes back'), (SECOND, 'One elbow back')))
    browser = _browser(hosted)
    _, controls = _form(browser, company, credit['id'])
    location = _location(hosted)

    before = database(location)
    _save(browser, company, credit['id'], controls)
    assert database(location) == before, 'an untouched save moved the company database'

    after = books['ok']('vendor-credit.show', dict(credit=credit['id']))
    assert after['version'] == credit['version']
    assert after['revision']['revision_number'] == 1
    assert after['revision']['id'] == credit['revision']['id']
    assert after['revision']['profile'] == credit['revision']['profile']
    assert len(after['revision']['source']['components']) == 2


def test_one_changed_field_moves_the_same_bytes_and_keeps_the_revision_it_replaced(hosted):
    """What keeps the equality above honest, and the prior revision a correction promises."""
    books = _books(hosted)
    company = hosted.company_id
    credit = _credit(books)
    browser = _browser(hosted)
    _, controls = _form(browser, company, credit['id'])
    location = _location(hosted)

    before = database(location)
    _save(browser, company, credit['id'], {**controls, 'f:memo': 'Returned to the depot'},
          reason='The yard was the wrong branch')
    assert database(location) != before

    after = books['ok']('vendor-credit.show', dict(credit=credit['id']))
    assert after['revision']['revision_number'] == 2
    assert after['revision']['memo'] == 'Returned to the depot'
    assert after['total_minor_units'] == credit['total_minor_units']
    previous = books['ok']('vendor-credit.show',
                           dict(credit=credit['id'], revision_number=1))
    assert previous['revision']['memo'] == 'Returned to the yard'
    assert previous['revision']['id'] == credit['revision']['id']


def test_a_payable_the_credit_defaulted_stays_defaulted(hosted):
    """The trap this projection exists to avoid.

    A credit posted without naming its payable takes the company's only active Accounts
    Payable account and captures that as a defaulted account. The correction form has to show
    the account -- a blank picker on a document that has one is exactly the defect this file
    exists for -- without turning it into a value somebody typed, because the captured origin
    is part of the profile the command compares a correction against. If naming it flipped the
    origin, an untouched save would become a reversal batch and a replacement batch for an
    edit nobody made.
    """
    books = _books(hosted)
    company = hosted.company_id
    credit = _credit(books)
    assert credit['revision']['profile']['origins']['ap_account']['kind'] == 'default'
    browser = _browser(hosted)
    _, controls = _form(browser, company, credit['id'])
    assert controls['f:ap_account'] == credit['ap_account_id'] != ''
    location = _location(hosted)

    before = database(location)
    _save(browser, company, credit['id'], controls)
    assert database(location) == before
    after = books['ok']('vendor-credit.show', dict(credit=credit['id']))
    assert after['revision']['profile']['origins']['ap_account']['kind'] == 'default'
    assert after['revision']['revision_number'] == 1


def test_a_row_deliberately_left_unclassified_is_not_silently_reclassified(hosted):
    """A credit carrying a class with one row held out of it: the row's own origin, kept.

    Supplying the grid replaces it outright, and the form submits the whole grid the moment
    one cell in it differs. A baseline that reopened the held-out row as `inherit` would take
    the credit's class on the next save, on a row nobody touched.
    """
    books = _books(hosted)
    company = hosted.company_id
    books['ok']('company.update', dict(use_classes=True))
    klass = books['ok']('class.create', dict(name='CW held out'))['id']
    other = books['ok']('class.create', dict(name='CW its own'))['id']
    credit = books['ok']('vendor-credit.post', dict(
        date='2026-03-11', vendor=books['vendor'], class_id=klass, memo='Mixed',
        expenses=[dict(account=books['expense'], amount=FIRST, memo='Inherits'),
                  dict(account=books['expense'], amount=SECOND, memo='Held out',
                       class_mode='none'),
                  dict(account=books['expense'], amount=THIRD, memo='Its own',
                       class_id=other)]))
    lines = credit['revision']['expenses']
    assert [line['class_id'] for line in lines] == [klass, None, other]
    browser = _browser(hosted)
    _, controls = _form(browser, company, credit['id'])
    # Each row says what it said on the way in, not what it happens to have resolved to: the
    # first took the credit's class, the second was deliberately held out of it, the third
    # named its own.
    assert controls['c:expenses:0:class_mode'] == 'inherit'
    assert controls['c:expenses:1:class_mode'] == 'none'
    assert (controls['c:expenses:2:class_mode'], controls['c:expenses:2:class_id']) == ('value', other)
    location = _location(hosted)

    before = database(location)
    _save(browser, company, credit['id'], controls)
    assert database(location) == before

    # And a correction that moves one cell of the grid leaves the other rows exactly as they were.
    _save(browser, company, credit['id'], {**controls, 'c:expenses:0:amount': '16.00'},
          reason='A sixteenth box came back')
    after = books['ok']('vendor-credit.show', dict(credit=credit['id']))
    corrected = after['revision']['expenses']
    assert [(row['amount']['amount'], row['class_id'], row['memo']) for row in corrected] == [
        ('16.00', klass, 'Inherits'), (SECOND, None, 'Held out'), (THIRD, other, 'Its own')]
    assert [row['line_id'] for row in corrected] == [line['line_id'] for line in lines]
    origins = [row['line_snapshot']['origins']['class_id']['kind'] for row in corrected]
    assert origins == ['default', 'default', 'explicit']


def test_the_history_page_lists_every_revision_and_links_back_into_each(hosted):
    books = _books(hosted)
    company = hosted.company_id
    credit = _credit(books)
    browser = _browser(hosted)
    _, controls = _form(browser, company, credit['id'])
    _save(browser, company, credit['id'], {**controls, 'f:memo': 'Corrected memo'},
          reason='Wrong memo')

    detail = browser.get(f'/c/{company}/vendor-credit/{credit["id"]}')
    assert f'/vendor-credit/{credit["id"]}/history' in detail.text

    page = browser.get(f'/c/{company}/vendor-credit/{credit["id"]}/history')
    assert page.status_code == 200, page.text[:400]
    assert f'Revision history for {credit["number"]}' in page.text
    assert 'Returned to the yard' in page.text and 'Corrected memo' in page.text
    assert '?revision_number=1' in page.text and '?revision_number=2' in page.text
    assert 'replacement' in page.text and 'reversal' in page.text
