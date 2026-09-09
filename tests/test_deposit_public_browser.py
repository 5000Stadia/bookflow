"""The ordinary browser way into a deposit: the register link, the summary, the composition.

Every claim here is made against a page the running host actually served. The
register journey is driven by real Chrome at both widths, because a link built
by `register.js` and a layout that survives a 390px viewport are not things a
template can be asked about. Denials are real membership denies, so the refusal
and the redaction under test are the ones a member would actually meet.
"""
import json
import re
from html import escape, unescape
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bookflow.adapters.workbench import deposits as D
from tests import deposit_public_support as support
from tests.deposit_public_support import ABSENT
from tests.test_row3_host import live as live_server
from tests.test_row5_browser_acceptance import CHROME, _Cdp
from tests.test_service_sales_browser import _contained

# Quoted from the accepted disposition in notes/deposit-public-details-plan.md
# ("Focused acceptance20912643"). This module holds its own copy on purpose: a
# test that imported the constant could not tell a reworded page from the plan.
REFUSAL = ('Deposit details are unavailable. The record may no longer exist or your access may '
           'have changed. Return to the register or ask your company administrator for help.')

HOSTILE = '</script><script>alert(1)</script>'
FORBIDDEN_ACTIONS = ('Void', 'Modify', 'Delete', 'Unvoid', 'Edit', 'Correct')


@pytest.fixture(scope='module')
def world(public_deposit_world):
    """The produced company, served for this module and handed back with no host."""
    support.start(public_deposit_world, serving=True)
    try:
        yield public_deposit_world
    finally:
        support.stop(public_deposit_world)


@pytest.fixture(scope='module')
def site(world):
    """A real HTTP server in front of that host, for the browser journey."""
    yield from live_server.__wrapped__(SimpleNamespace(handle=SimpleNamespace(app=world['handle'].app)))


# ------------------------------------------------------------------ small helpers


def fetch(world, url, *, expect=200):
    client = TestClient(world['handle'].app)
    response = client.get(url, headers={'Authorization': 'Bearer ' + world['issued']['secret']})
    assert response.status_code == expect, (url, response.status_code, response.text[:600])
    return response.text


def detail_url(world, deposit, query=''):
    return f'/c/{world["cid"]}/deposit/{deposit}' + (('?' + query) if query else '')


def items_url(world, deposit, kind='sources', extra=''):
    return f'/c/{world["cid"]}/deposit/{deposit}/items?kind={kind}' + (('&' + extra) if extra else '')


def text(page):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', page))


def hrefs(page):
    return [unescape(found) for found in re.findall(r'href="([^"]+)"', page)]


def cards(page):
    """The per-row cards on an items page, in order."""
    return page.split('<section class="deposit-row-card"')[1:]


def all_pages(world, deposit):
    return [fetch(world, detail_url(world, deposit))] + [
        fetch(world, items_url(world, deposit, kind))
        for kind in ('sources', 'additional', 'cash_allocations')]


# --------------------------------------------------- the journey, in a real browser


@pytest.mark.skipif(not CHROME.is_file(), reason='real Chrome is not installed')
@pytest.mark.parametrize('width', (1280, 390))
@pytest.mark.timeout(300)
def test_a_register_row_reaches_the_deposit_and_its_composition(world, site, tmp_path, width):
    """Register -> detail -> composition, followed, at a desktop and a phone width."""
    browser = _Cdp(tmp_path / f'chrome-{width}')
    try:
        browser.viewport(width, 850)
        browser.call('Network.enable')
        browser.call('Network.setExtraHTTPHeaders', {'headers': {
            'Authorization': 'Bearer ' + world['issued']['secret']}})
        register = (f'{site}/c/{world["cid"]}/account/{world["bank"]}/register'
                    '?date_from=2026-01-01&date_to=2026-12-31')
        browser.navigate(register)
        selector = json.dumps(f'#register-history a[href^="/c/{world["cid"]}/deposit/"]')
        browser.wait_for(f'document.querySelectorAll({selector}).length > 0', timeout=60)
        links = browser.evaluate(
            f'[...document.querySelectorAll({selector})].map(a => '
            '({text: a.textContent, href: a.getAttribute("href")}))')
        assert links, 'the register offered no deposit link'
        assert {link['text'] for link in links} == {'Deposit details'}, links
        target = next(link['href'] for link in links
                      if link['href'].startswith(f'/c/{world["cid"]}/deposit/{world["deposit"]}?'))
        assert 'revision_number=1' in target, target

        browser.navigate(site + target)
        browser.wait_for("document.querySelector('h1').textContent.startsWith('Deposit ')")
        body = browser.evaluate('document.body.innerText')
        assert 'Captured on this revision' in body and 'Current record' in body
        assert '63.00 USD' in body and 'Public detail memo' in body
        for word in FORBIDDEN_ACTIONS:
            assert word not in body, (word, width)
        _contained(browser, width)

        composition = browser.evaluate(
            "[...document.querySelectorAll('.deposit-kinds a')].map(a => a.getAttribute('href'))")
        assert len(composition) == 3 and all('revision_number=1' in url for url in composition)
        sources = next(url for url in composition if 'kind=sources' in url)
        browser.navigate(site + sources)
        browser.wait_for("document.body.innerText.includes('Current receipt')")
        assert browser.evaluate("document.querySelectorAll('.deposit-row-card').length") == 1
        body = browser.evaluate('document.body.innerText')
        assert '60.00 USD' in body and 'Sale witness customer' in body
        for word in FORBIDDEN_ACTIONS:
            assert word not in body, (word, width)
        _contained(browser, width)

        allocations = next(url for url in composition if 'kind=cash_allocations' in url)
        browser.navigate(site + allocations)
        browser.wait_for("document.querySelectorAll('.deposit-allocations tbody tr').length === 6")
        _contained(browser, width)
        # A wide uniform table scrolls inside its own region instead of pushing
        # the page sideways: that is what keeps 390px usable.
        assert browser.evaluate(
            "(() => {const wrap = document.querySelector('.table-wrap');"
            " return getComputedStyle(wrap).overflowX === 'auto'"
            " && wrap.getBoundingClientRect().right <= innerWidth + 1;})()")
    finally:
        browser.close()


# -------------------------------------------------------------------- the refusal


def test_one_refusal_serves_a_denied_deposit_and_an_absent_one(world):
    """Admission is all-or-nothing, so the denied page must not differ from the missing one."""
    with support.denying(world, ('customer-work',)):
        denied = fetch(world, detail_url(world, world['work']), expect=404)
    absent = fetch(world, detail_url(world, ABSENT), expect=404)
    for page in (denied, absent):
        assert REFUSAL in text(page)
    assert (denied.replace(world['work'], 'DEPOSIT-ID')
            == absent.replace(ABSENT, 'DEPOSIT-ID')), 'the two refusals are not the same page'
    for page in (denied, absent):
        readable = text(page).lower()
        for identifier in (world['work_payment'], world['customer'], world['deposit'],
                           world['bank'], world['simple']):
            assert identifier not in page, identifier
        for cause in ('e_record_not_found', 'permission', 'denied', 'membership',
                      'capability', 'forbidden', 'unauthorized', 'customer', 'payment'):
            assert cause not in readable, cause


def test_the_composition_route_refuses_the_same_way(world):
    """A denied composition page says exactly what the denied summary page says."""
    with support.denying(world, ('customer-work',)):
        denied = fetch(world, items_url(world, world['work'], 'sources'), expect=404)
    assert REFUSAL in text(denied)
    assert 'Your selection is kept: contributing receipts' in text(denied)
    assert world['work_payment'] not in denied


def test_the_refusal_leads_back_into_the_company_and_keeps_the_selection(world):
    """A link is an attempt to open a deposit, so the refusal has to be a way back."""
    with support.denying(world, ('customer-work',)):
        denied = fetch(world, detail_url(world, world['work'], 'revision_number=1'), expect=404)
    assert f'/c/{world["cid"]}/account' in hrefs(denied)
    assert f'/c/{world["cid"]}/' in hrefs(denied)
    assert detail_url(world, world['work'], 'revision_number=1') in hrefs(denied)
    assert 'Your selection is kept: revision 1' in text(denied)


# ------------------------------------------------------- selection across the links


def test_revision_dated_and_kind_survive_every_link(world):
    corrected, dated = world['corrected'], 'as_of=2026-06-30'
    page = fetch(world, detail_url(world, corrected, 'revision_number=1&' + dated))
    assert 'You are reading captured revision 1' in text(page)
    assert 'The current record is at version 2' in text(page)
    composition = [url for url in hrefs(page) if '/items?' in url]
    assert len(composition) == 3
    assert all('revision_number=1' in url and dated in url for url in composition), composition
    revisions = [url for url in hrefs(page) if '/deposit/' in url and 'revision_number=' in url
                 and '/items' not in url]
    assert sorted(revisions) == sorted(detail_url(world, corrected, f'revision_number={n}&' + dated)
                                       for n in (1, 2))
    assert 'Bank effect as of 2026-06-30' in text(page)

    # The revision the reader picked is the revision the composition shows.
    additional = next(url for url in composition if 'kind=additional' in url)
    rows = fetch(world, additional)
    assert '12.00 USD' in rows and '19.00 USD' not in rows
    assert 'Captured revision 1' in text(rows)
    assert detail_url(world, corrected, 'revision_number=1&' + dated) in hrefs(rows)

    current = fetch(world, items_url(world, corrected, 'additional', 'revision_number=2'))
    assert '19.00 USD' in current and '12.00 USD' not in current


def test_a_continuation_cursor_survives_the_next_page_link(world, monkeypatch):
    """A real cursor from a real page, followed through a real link."""
    monkeypatch.setattr(D, 'PAGE_LIMIT', 2)
    first = fetch(world, items_url(world, world['deposit'], 'cash_allocations',
                                   'revision_number=1'))
    assert '2 of 6 rows on this page' in text(first)
    following = [url for url in hrefs(first) if 'cursor=' in url]
    assert len(following) == 1, following
    assert 'kind=cash_allocations' in following[0] and 'revision_number=1' in following[0]
    second = fetch(world, following[0])
    assert '2 of 6 rows on this page' in text(second)

    def rows(page):
        return re.findall(r'<td class="deposit-id">([^<]+)</td>\s*<td>(\d+)</td>\s*<td>([^<]+)</td>',
                          page)
    assert rows(second) and rows(second) != rows(first)
    assert any('cursor=' in url for url in hrefs(second))


def test_a_dead_cursor_keeps_the_reader_selection(world):
    stale = fetch(world, items_url(world, world['deposit'], 'additional',
                                   'revision_number=1&cursor=not-a-cursor'), expect=400)
    assert 'Your selection is kept: revision 1, additional cash rows, a later page' in text(stale)
    assert items_url(world, world['deposit'], 'additional', 'revision_number=1') in hrefs(stale)


# ------------------------------------------------------------------- redaction


def test_a_denied_reference_group_reads_as_unavailable_never_blank(world):
    open_rows = fetch(world, items_url(world, world['deposit'], 'additional'))
    assert 'Public detail class' in open_rows
    open_detail = fetch(world, detail_url(world, world['deposit']))
    assert 'Captured default' in open_detail and 'Public detail label' in open_detail

    with support.denying(world, ('class', 'custom-field')):
        rows = fetch(world, items_url(world, world['deposit'], 'additional'))
        detail = fetch(world, detail_url(world, world['deposit']))

    assert 'Public detail class' not in rows
    assert 'class="deposit-redacted">Not available to you<' in rows
    with_class, without_class = cards(rows)
    assert 'Not available to you' in with_class
    # The row that never carried a class still reads as absent, so a redaction
    # is never mistaken for an empty field and an empty field never claims to
    # be a redaction.
    assert 'Not available to you' not in without_class
    assert '<b>Class (captured)</b><span>—</span>' in without_class

    assert 'Captured default' not in detail and 'Public detail label' not in detail
    assert 'class="deposit-redacted">Not available to you<' in detail
    assert 'No custom values were captured' not in text(detail)


# ----------------------------------------------------------------- what is shown


def test_every_amount_reaches_the_page_and_negative_and_zero_are_labelled(world):
    page = fetch(world, detail_url(world, world['deposit']))
    for amount in ('60.00 USD', '10.00 USD', '-2.00 USD', '5.00 USD',
                   '68.00 USD', '70.00 USD', '63.00 USD'):
        assert amount in page, amount
    assert 'deposit-negative">-2.00 USD' in page
    assert 'reduces the bank effect' in text(page)
    assert 'minor_units' not in page, 'a raw wire amount reached the page'

    voided = fetch(world, detail_url(world, world['voided']))
    assert 'deposit-zero">0.00 USD' in voided and 'no effect' in text(voided)
    assert 'voided' in text(voided)
    # A voided deposit still shows what its revision captured, beside the
    # current status, and the two are separately labelled.
    assert '7.00 USD' in voided
    assert 'Captured on this revision' in voided and 'Current record' in voided


def test_captured_values_and_current_values_are_told_apart(world):
    page = fetch(world, detail_url(world, world['corrected'], 'revision_number=1'))
    readable = text(page)
    assert 'Captured on this revision' in readable and 'Current record' in readable
    assert 'Exactly what this immutable revision recorded' in readable
    assert 'Current records this summary names' in readable
    assert 'Before correction' in readable and 'After correction' not in readable

    rows = fetch(world, items_url(world, world['deposit'], 'sources'))
    readable = text(rows)
    for label in ('Payer (captured)', 'From account (captured)', 'Payment method (captured)',
                  'Current receipt', 'Current status', 'Current records this page names'):
        assert label in readable, label


def test_the_summary_names_the_composition_without_fetching_it(world, monkeypatch):
    """One page, one command: no automatic composition read and no per-row fan-out."""
    from bookflow.adapters.http import execution
    seen = []
    original = execution.run_hosted

    def watched(host, cmd, *args, **kwargs):
        seen.append(cmd.name)
        return original(host, cmd, *args, **kwargs)
    monkeypatch.setattr(execution, 'run_hosted', watched)

    page = fetch(world, detail_url(world, world['deposit']))
    assert seen == ['deposit show'], seen
    assert '1 contributing receipts · 2 additional cash rows · 6 cash allocations' in text(page)
    seen.clear()
    fetch(world, items_url(world, world['deposit'], 'sources'))
    assert seen == ['deposit items'], seen


def test_no_modify_or_void_action_appears_on_any_deposit_page(world):
    pages = all_pages(world, world['deposit'])
    with support.denying(world, ('customer-work',)):
        pages.append(fetch(world, detail_url(world, world['work']), expect=404))
    for page in pages:
        readable = text(page)
        for word in FORBIDDEN_ACTIONS:
            assert word not in readable, word
        for verb in ('/void', '/update', '/delete', '/copy', '/unapply'):
            assert verb not in page, verb
        assert 'method="post"' not in page.split('<main>')[1]


def test_the_pages_stay_inside_this_company(world):
    for page in all_pages(world, world['deposit']):
        for url in hrefs(page):
            assert (url.startswith(f'/c/{world["cid"]}/') or url.startswith('/static/')
                    or url in ('/', '/companies', '/hub/', '/hub/audit', '/login')), url


# ------------------------------------------------------------------- escaping


def test_captured_text_is_escaped_before_it_reaches_a_page(world, monkeypatch):
    """Real pages, real stack; only the field value is made hostile.

    The produced company is shared with the other public-deposit modules, so the
    hostile value is injected at the projection boundary rather than written
    into the fixture.
    """
    original = D._decorate

    def hostile(value):
        out = original(value)
        if isinstance(out, dict) and isinstance(out.get('selected'), dict):
            if 'memo' in out['selected']:
                out['selected']['memo'] = HOSTILE
                out['selected']['custom_fields'][0]['name'] = HOSTILE
            for row in out.get('items') or ():
                row['memo'] = HOSTILE
        return out
    monkeypatch.setattr(D, '_decorate', hostile)
    for url in (detail_url(world, world['deposit']),
                items_url(world, world['deposit'], 'additional')):
        page = fetch(world, url)
        assert HOSTILE not in page, url
        assert escape(HOSTILE) in page, url
        assert '<script>alert' not in page, url


# --------------------------------------------------------------- layout material


def test_the_deposit_layout_carries_no_width_a_phone_cannot_show():
    """What can honestly be read from the stylesheet; Chrome checks the rest."""
    css = (Path(D.__file__).parent / 'static' / 'deposit.css').read_text()
    # The only pixel width in the sheet is the breakpoint itself: every box,
    # track and column is sized in rem, percent or fr.
    assert re.findall(r'\bwidth\s*:\s*(\d+)px', css) == ['700']
    assert '@media (max-width:700px)' in css
    assert 'minmax(min(100%,26rem),1fr)' in css, 'the two-column grid must be able to collapse'
    templates = Path(D.__file__).parent / 'templates'
    for name in ('deposit_detail.html', 'deposit_items.html'):
        markup = (templates / name).read_text()
        assert markup.count('<table') == markup.count('class="table-wrap"'), name


def test_the_served_pages_carry_the_responsive_shell(world):
    for page in all_pages(world, world['deposit']):
        assert 'name="viewport" content="width=device-width, initial-scale=1"' in page
        assert '/static/deposit.css' in page
        assert 'style="width:' not in page and 'style="min-width:' not in page
