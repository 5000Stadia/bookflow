"""R189 increment 2: who sold each POS line, asked at the till in real headless Chrome at 390 and 1440 px.

A tapped, a scanned and a looked-up group line each ask for their recipient before they join the cart; a group asks
once per enabled member; identical items with different people stay two lines, while the same item, price and person
combine; Cancel leaves the cart as it was. The sale posts the chosen people. At 1440 px a held ticket whose recipient
went inactive is asked again on resume before Charge is offered. Screenshots go to BF_PICTURE_SHOTS when it is set.
"""
import json

import pytest

from tests.test_item_pictures_browser import _fits
from tests.test_pos_sale_browser import SEALANT, _confirmed, _open_pos, _shot, _signed_in
from tests.test_row5_browser_acceptance import CHROME, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
WHY = {'X-Bookflow-Reason': 'Commission at the till test'}


def _setup(b, site):
    info = _command(b, site, 'company.show', {})
    _command(b, site, 'company.update', {'pos_enabled': True, 'commission_attribution_enabled': True,
                                         'expected_version': info['info_version']}, **WHY)
    people = {}
    for name, initials in (('Ada Till', 'AT'), ('Bo Till', 'BT')):
        other = _command(b, site, 'other-name.create', {'name': name}, **WHY)['id']
        people[name] = _command(b, site, 'sales-rep.create', {'name': name, 'initials': initials,
                                'name_type': 'other_name', 'name_id': other}, **WHY)['id']
    ids = {}
    for name in ('Brass Shutoff Valve', 'Pipe Sealant'):
        item = _command(b, site, 'item.show', {'item': name})
        _command(b, site, 'item.update', {'item': item['id'], 'expected_version': item['version'],
                                          'commission_enabled': True}, **WHY)
        ids[name] = item['id']
    coupling = _command(b, site, 'item.show', {'item': 'Copper Coupling'})['id']
    _command(b, site, 'item.create', {'name': 'Till Kit', 'type': 'group', 'description': 'Till kit',
        'print_members': True, 'members': [{'component_item_id': ids['Pipe Sealant'], 'quantity': '1'},
                                           {'component_item_id': coupling, 'quantity': '1'},
                                           {'component_item_id': ids['Brass Shutoff Valve'], 'quantity': '1'}]}, **WHY)
    return people, ids


def _lines(b):
    return b.evaluate('''window.bookflowPos.state().lines.map(l => [l.name, l.quantity,
        l.commission ? l.commission.name : null, (l.member_commissions || []).map(m => [m.member, m.name])])''')


def _asked(b, title):
    b.wait_for('document.querySelector("[data-pos-commission-dialog]").open', timeout=15)
    b.wait_for('!!document.querySelector("[data-pos-recipient]")', timeout=15)
    assert title in b.evaluate('document.querySelector("[data-pos-commission-title]").textContent')


def _choose(b, name, closes=True):
    b.evaluate(f'[...document.querySelectorAll("[data-pos-recipient]")].find(x => x.textContent === {json.dumps(name)}).click()')
    if closes:   # a group's next member is asked in the same dialog at once
        b.wait_for('!document.querySelector("[data-pos-commission-dialog]").open')


def _tap_valve(b):
    b.evaluate('''(() => { const box = document.querySelector('[data-pos-box-group="Plumbing Parts"]'); if (box) box.click(); })()''')
    b.wait_for('[...document.querySelectorAll("[data-pos-box-item]")].some(x => x.textContent.includes("Brass Shutoff Valve"))')
    b.evaluate('[...document.querySelectorAll("[data-pos-box-item]")].find(x => x.textContent.includes("Brass Shutoff Valve")).click()')


@pytest.mark.parametrize('width', [390, 1440])
def test_tap_scan_and_group_ask_who_sold_each_line_at_the_till(browser_site, tmp_path, width):
    site = browser_site
    b = _signed_in(tmp_path, site, width, 844 if width == 390 else 900)
    try:
        people, ids = _setup(b, site)
        _open_pos(b, site)
        # By tap: the recipient is asked before the line joins the cart.
        _tap_valve(b)
        _asked(b, 'Who sold Brass Shutoff Valve?')
        assert _lines(b) == []
        if width == 390:   # full height on a phone, every choice a 44 px target
            assert b.evaluate('Math.round(document.querySelector("[data-pos-commission-dialog]").getBoundingClientRect().width)') == 390
        assert b.evaluate('[...document.querySelectorAll("[data-pos-recipient], [data-pos-commission-cancel]")].every(x => x.getBoundingClientRect().height >= 44)')
        _shot(b, width, 'pos-commission-picker')
        _choose(b, 'Ada Till')
        # The same item for someone else stays its own line; again for Ada combines with hers.
        _tap_valve(b)
        _asked(b, 'Brass Shutoff Valve')
        _choose(b, 'Bo Till')
        _tap_valve(b)
        _asked(b, 'Brass Shutoff Valve')
        _choose(b, 'Ada Till')
        assert _lines(b) == [['Brass Shutoff Valve', '2', 'Ada Till', []], ['Brass Shutoff Valve', '1', 'Bo Till', []]]
        # Cancel adds nothing.
        _tap_valve(b)
        _asked(b, 'Brass Shutoff Valve')
        b.evaluate('document.querySelector("[data-pos-commission-cancel]").click()')
        b.wait_for('!document.querySelector("[data-pos-commission-dialog]").open')
        assert len(_lines(b)) == 2
        # By scan: a keyboard-mode scanner's code and Enter.
        b.evaluate(f"""(() => {{ const i = document.querySelector('[data-scan-input]'); i.focus(); i.value = '{SEALANT}';
          i.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); }})()""")
        _asked(b, 'Who sold Pipe Sealant?')
        _choose(b, 'Bo Till')
        # A group: once per enabled member (the coupling is not enabled), and Cancel on the second drops the group.
        for finish in (False, True):
            b.evaluate("""(() => { const i = document.querySelector('[data-scan-input]'); i.value = 'Till Kit';
              i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
            b.wait_for('[...document.querySelectorAll("[data-pos-result]")].some(x => x.textContent.includes("Till Kit"))')
            b.evaluate('[...document.querySelectorAll("[data-pos-result]")].find(x => x.textContent.includes("Till Kit")).click()')
            _asked(b, 'Who sold Pipe Sealant in Till Kit?')
            _choose(b, 'Ada Till', closes=False)
            _asked(b, 'Who sold Brass Shutoff Valve in Till Kit?')
            if finish:
                _choose(b, 'Bo Till')
            else:
                b.evaluate('document.querySelector("[data-pos-commission-cancel]").click()')
                b.wait_for('!document.querySelector("[data-pos-commission-dialog]").open')
                assert len(_lines(b)) == 3
        assert _lines(b)[-1] == ['Till Kit', '1', None, [[1, 'Ada Till'], [3, 'Bo Till']]]
        labels = b.evaluate('[...document.querySelectorAll("[data-pos-line-commission-label]")].map(x => x.textContent)')
        assert labels == ['Commission: Ada Till', 'Commission: Bo Till', 'Commission: Bo Till', 'Commission: Ada Till, Bo Till']
        total = _confirmed(b)
        _fits(b)
        _shot(b, width, 'pos-commission-cart')
        b.evaluate('document.querySelector("[data-pos-charge]").click()')
        b.wait_for('document.querySelector("[data-pos-tender]").open')
        b.evaluate('document.querySelector("[data-pos-quick-amount]").click()')
        b.evaluate('document.querySelector("[data-pos-complete]").click()')
        b.wait_for('!document.querySelector("[data-pos-done]").hidden', timeout=20)
        number = b.evaluate('document.querySelector("[data-pos-done-number]").textContent')
        receipt = _command(b, site, 'sales-receipt.show', {'sales_receipt': number})
        assert receipt['total']['amount'] == total
        named = [(line['item_snapshot']['item']['label'], line['item_snapshot']['commission']['recipients'][0]['recipient_label'])
                 for line in receipt['revision']['lines'] if line['item_snapshot'].get('commission')]
        assert named == [('Brass Shutoff Valve', 'Ada Till'), ('Brass Shutoff Valve', 'Bo Till'), ('Pipe Sealant', 'Bo Till'),
                         ('Pipe Sealant', 'Ada Till'), ('Brass Shutoff Valve', 'Bo Till')]
        assert [line['quantity'] for line in receipt['revision']['lines']][:2] == ['2', '1']
        if width == 1440:
            _held_ticket_is_asked_again(b, site, people)
    finally:
        b.close()


def _held_ticket_is_asked_again(b, site, people):
    b.evaluate('document.querySelector("[data-pos-new-sale]").click()')
    _tap_valve(b)
    _asked(b, 'Brass Shutoff Valve')
    _choose(b, 'Ada Till')
    _confirmed(b)
    b.evaluate('document.querySelector("[data-pos-hold]").click()')
    b.evaluate('document.querySelector("[data-pos-hold-label]").value = "Ada back soon"')
    b.evaluate('document.querySelector("[data-pos-hold-form]").requestSubmit()')
    b.wait_for('window.bookflowPos.state().lines.length === 0', timeout=20)
    rep = _command(b, site, 'sales-rep.show', {'sales_rep': people['Ada Till']})
    _command(b, site, 'sales-rep.deactivate', {'sales_rep': rep['id'], 'expected_version': rep['version']}, **WHY)
    _open_pos(b, site)
    b.evaluate('document.querySelector("[data-pos-held]").click()')
    b.wait_for('!!document.querySelector("[data-pos-resume]")', timeout=20)
    b.evaluate('document.querySelector("[data-pos-resume]").click()')
    _asked(b, 'Who sold Brass Shutoff Valve?')
    assert b.evaluate('document.querySelector("[data-pos-charge]").disabled')
    assert not b.evaluate('[...document.querySelectorAll("[data-pos-recipient]")].some(x => x.textContent === "Ada Till")')
    _shot(b, 1440, 'pos-commission-resumed')
    _choose(b, 'Bo Till')
    assert _lines(b) == [['Brass Shutoff Valve', '1', 'Bo Till', []]]
    _confirmed(b)
    b.wait_for('!document.querySelector("[data-pos-charge]").disabled')
