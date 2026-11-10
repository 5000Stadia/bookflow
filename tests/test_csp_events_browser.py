"""Local-profile Chrome checks for declarative handlers, including swapped fragments.

The fragment fixture isolates the actual template markup and installed scripts;
full-page budget, authentication and navigation checks use the local host.
"""
import json
import re
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jinja2
import pytest

from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command, _key, register_browser  # noqa: F401

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')
WORKBENCH = Path(__file__).parents[1] / 'src/bookflow/adapters/workbench'
TEMPLATES = WORKBENCH / 'templates'


@contextmanager
def fragment_server(pages):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path.startswith('/static/'):
                body = (WORKBENCH / self.path.lstrip('/')).read_bytes()
                status, media = 200, 'text/javascript'
            else:
                status, body = pages.get(self.path, (404, 'missing'))
                body, media = body.encode(), 'text/html'
            self.send_response(status)
            self.send_header('Content-Type', media)
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def shell(body):
    return '<!doctype html><html><head><script src="/static/csp-events.js"></script>' \
        '<script src="/static/htmx.min.js"></script></head><body>' + body + '</body></html>'


def test_every_validation_wrapper_swaps_400_and_409_after_real_clicks(tmp_path):
    """Every migrated section, actual markup, both status branches, after replacement."""
    pages = {}
    names = []
    for path in sorted(TEMPLATES.glob('*.html')):
        match = re.search(r'<section\b[^>]*\bdata-swap-validation-errors\b[^>]*>', path.read_text())
        if not match:
            continue
        names.append(path.stem)
        # Only budget's extra dynamic data-budget value needs rendering here.
        opening = jinja2.Template(match[0]).render(budget={'id': 'test-budget'})
        section_id = re.search(r'id="([^"]+)"', opening)[1]
        for status, next_status in ((200, 400), (400, 409), (409, 400)):
            body = opening + f'<p data-status="{status}">{status}</p><form hx-post="/{path.stem}/{next_status}" ' \
                f'hx-target="#{section_id}" hx-swap="outerHTML"><button>Submit</button></form></section>'
            pages[f'/{path.stem}/{status}'] = (status, shell(body) if status == 200 else body)
    assert len(names) >= 19
    with fragment_server(pages) as origin:
        b = _Cdp(tmp_path / 'wrappers-chrome')
        try:
            for name in names:
                b.navigate(f'{origin}/{name}/200')
                b.evaluate('window.sameDocument=true;window.settled=0;document.addEventListener("htmx:afterSettle",()=>{window.settled++})')
                for count, status in enumerate((400, 409, 400), 1):
                    b.evaluate('document.querySelector("button").click()')
                    b.wait_for(f'window.settled === {count} && !!document.querySelector("[data-status=\\"{status}\\"]")')
                    assert b.evaluate('window.sameDocument === true'), name
        finally:
            b.close()


def test_standalone_print_controls_load_their_handler(tmp_path):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), undefined=jinja2.ChainableUndefined)
    pages = {}
    for name in ('pick_list_print', 'fulfillment_pick_lists', 'pos_receipt'):
        pages['/' + name] = (200, env.get_template(name + '.html').render(
            static_urls={'csp-events.js': '/static/csp-events.js'}, pick={}, picks=[], groups=[],
            receipt={'sale': {'id': 'sale'}, 'width': 80, 'items': [], 'address': [], 'kind': 'sale'}))
    with fragment_server(pages) as origin:
        b = _Cdp(tmp_path / 'prints-chrome')
        try:
            for name in pages:
                b.navigate(origin + name)
                b.evaluate('window.printCalls=0;window.print=()=>{window.printCalls++}')
                b.evaluate('document.querySelector("[data-print-page]").click()')
                assert b.evaluate('window.printCalls') == 1, name
        finally:
            b.close()


def test_swapped_images_and_error_feedback_keep_their_behaviour(tmp_path):
    image_cases = {}
    for name in ('fulfillment', 'pick_list', 'shipment_pack', 'list_card', 'master_list', 'item_picture'):
        source = (TEMPLATES / (name + '.html')).read_text()
        match = re.search(r'<img\b[^>]*(?:data-hide-on-error|data-picture-fallback)[^>]*>', source)
        assert match, name
        tag = re.sub(r'src="[^"]*"', 'src="/missing-image"', match[0])
        tag = re.sub(r'loading="lazy"', '', tag)
        image_cases[name] = tag
    pos = (WORKBENCH / 'static/pos.js').read_text()
    assert 'data-pos-tile-fallback' in pos
    image_cases['pos'] = re.sub(r'src="[^"]*"', 'src="/missing-image"',
                               re.search(r'<img[^>]*data-pos-tile-fallback[^>]*>', pos)[0]).replace('loading="lazy"', '')
    images = ''.join(f'<div id="{name}">{tag}</div>' for name, tag in image_cases.items())
    overview = re.search(r'<div[^>]+data-overview-load[^>]*>', (TEMPLATES / 'home.html').read_text())[0]
    overview = re.sub(r'hx-get="[^"]*"', 'hx-get="/fail"', overview)
    overview = overview.replace('hx-trigger="load"', 'hx-trigger="click"').replace('<div ', '<div id="overview" ')
    delivery = (TEMPLATES / 'delivery_batch.html').read_text()
    batch = re.search(r'<div[^>]+data-batch-errors[^>]*>', delivery)[0]
    batch = jinja2.Template(batch).render(company_id='fixture', batch={'number': 'B-1', 'fingerprint': 'fixture'})
    batch = re.sub(r'hx-post="[^"]*"', 'hx-post="/fail"', batch)
    batch = batch.replace('hx-trigger="load delay:50ms"', 'hx-trigger="click"').replace('<div ', '<div id="batch" ')
    # Actual migrated wrapper markup; only the test endpoint and trigger are changed.
    # Real requests emit htmx responseError/sendError, never synthetic DOM events.
    body = '<button id="swap" hx-get="/images" hx-target="#images">Load pictures</button><div id="images"></div>' \
        + batch + 'Run batch</div><p data-batch-network-error hidden>Failed</p>' \
        + overview + '<p class="overview-loading">Loading</p></div>'
    with fragment_server({'/': (200, shell(body)), '/images': (200, images), '/fail': (500, 'failure')}) as origin:
        b = _Cdp(tmp_path / 'errors-chrome')
        try:
            b.navigate(origin + "/")
            b.evaluate('document.querySelector("#swap").click()')
            b.wait_for('!!document.querySelector("#item_picture [data-picture-none]")')
            for name in image_cases:
                if name == 'item_picture':
                    assert b.evaluate('document.querySelector("#item_picture").textContent') == 'No picture'
                elif name == 'pos':
                    b.wait_for('!!document.querySelector("#pos span.pos-tile")')
                    assert b.evaluate('document.querySelector("#pos").textContent') == ''
                else:
                    b.wait_for(f'document.querySelector("#{name} img")?.hidden === true')
            b.evaluate('document.querySelector("#batch").click()')
            b.wait_for('document.querySelector("[data-batch-network-error]").hidden === false')
            b.evaluate('document.querySelector("#overview").click()')
            b.wait_for('document.querySelector(".overview-loading").textContent === "Figures could not be loaded just now."')
            b.evaluate('document.querySelector("[data-batch-network-error]").hidden=true')
            b.call('Network.emulateNetworkConditions', {'offline': True, 'latency': 0, 'downloadThroughput': 0, 'uploadThroughput': 0})
            b.evaluate('document.querySelector("#batch").click()')
            b.wait_for('document.querySelector("[data-batch-network-error]").hidden === false')
        finally:
            b.close()


def test_local_budget_confirm_validation_and_report_history(register_browser):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    budget = _command(b, env.site, 'budget.create', {'name': 'Browser handler budget', 'fiscal_year': 2026})['budget']
    url = f'{base}/budget/{budget["id"]}'
    b.navigate(url)
    b.wait_for('!!document.querySelector("[data-budget-clear]")')
    b.evaluate('window.settled=0;document.addEventListener("htmx:afterSettle",()=>{window.settled++});window.requests=0;document.addEventListener("htmx:beforeRequest",()=>{window.requests++});'
               'window.confirm=()=>false;document.querySelector("[data-budget-clear]").click()')
    assert b.evaluate('window.requests') == 0
    assert _command(b, env.site, 'budget.show', {'budget': budget['id']})['version'] == budget['version']
    b.evaluate("""document.querySelector('[name="ctx:reason"]').value='Browser handler test';
        window.confirm=()=>true;document.querySelector('#budget-grid').dataset.original='1';
        document.querySelector('[data-budget-clear]').click()""")
    b.wait_for('window.settled === 1 && window.requests === 1 && !!document.querySelector("#budget-grid:not([data-original])")')
    # After a successful swap, the fresh form still swaps a rejected stale save.
    b.evaluate("""document.querySelector('[name=\"ctx:reason\"]').value='Browser handler test';
        document.querySelector('[name=version]').value='999999';
        document.querySelector('input[name^="m:"]').value='1.00';
        document.querySelector('[data-budget-save]').click()""")
    b.wait_for('document.querySelector("#budget-grid").textContent.includes("E_VERSION_CONFLICT")')
    b.navigate(f'{base}/report/profit-and-loss')
    report_url = b.evaluate('location.href')
    b.navigate(f'{base}/report/profit-and-loss/memorize')
    b.wait_for('!!document.querySelector("[data-history-back]")')
    b.evaluate('document.querySelector("[data-history-back]").click()')
    b.wait_for(f'location.href === {json.dumps(report_url)}')


def test_local_select_changes_login_feedback_and_logout(register_browser):
    env, b = register_browser, register_browser.browser
    base = f'{env.site.base_url}/c/{env.site.company_id}'
    for route, selector in (('/bulk-edit?kind=item', '[name=kind]'),
                            ('/print-checks', '[name=account]')):
        b.navigate(base + route)
        b.wait_for(f'!!document.querySelector({json.dumps(selector)})')
        choice, key = b.evaluate(f"""(() => {{const e=document.querySelector({json.dumps(selector)});
            const next=e.selectedIndex+1<e.options.length?e.selectedIndex+1:e.selectedIndex-1;
            return [e.options[next]?.value,next>e.selectedIndex?'ArrowDown':'ArrowUp'];}})()""")
        assert choice is not None, route
        b.evaluate(f'document.querySelector({json.dumps(selector)}).focus()')
        _key(b, key)
        _key(b, 'Enter')
        b.wait_for(f'new URL(location.href).searchParams.get({json.dumps("kind" if "bulk" in route else "account")}) === {json.dumps(choice)}')
    b.wait_for('document.readyState === "complete" && !!document.querySelector("details.account-menu")')
    b.evaluate('document.querySelector("details.account-menu").open=true;document.querySelector("[data-logout] button").click()')
    b.wait_for('location.pathname === "/login" && !!document.querySelector("#err")')
    b.evaluate(f'document.querySelector("[name=username]").value={json.dumps(env.site.login)};'
               'document.querySelector("[name=password]").value="wrong-password";'
               'document.querySelector("form[hx-post=\\"/login\\"] button").click()')
    b.wait_for('document.querySelector("#err").textContent.includes("did not match")')
    b.evaluate('window.cleared=false;document.addEventListener("htmx:beforeRequest",()=>{window.cleared=document.querySelector("#err").textContent===""});')
    b.call('Network.emulateNetworkConditions', {'offline': True, 'latency': 0, 'downloadThroughput': 0, 'uploadThroughput': 0})
    b.evaluate('document.querySelector("form[hx-post=\\"/login\\"] button").click()')
    b.wait_for('document.querySelector("#err").textContent.includes("could not be reached")')
    assert b.evaluate('window.cleared')
    b.call('Network.emulateNetworkConditions', {'offline': False, 'latency': 0, 'downloadThroughput': -1, 'uploadThroughput': -1})


def test_local_setup_feedback_clears_and_displays_server_error_on_click(browser_site, tmp_path):
    import os
    from bookflow.hub import setup_links
    from tests.test_setup_link_browser import _fresh_owner

    root = Path(os.environ['BOOKFLOW_DATA_ROOT'])
    secret, _ = setup_links.create(root, _fresh_owner(root, browser_site.login))
    b = _Cdp(tmp_path / 'setup-feedback-chrome')
    try:
        b.navigate(f'{browser_site.base_url}/setup/{secret}')
        b.evaluate("""document.querySelector('#password').value='browser-setup-password';
            document.querySelector('#again').value='different-setup-password';
            document.querySelector('form[data-auth-feedback=setup] button').click()""")
        b.wait_for('document.querySelector("#err").textContent.includes("do not match")')
        b.evaluate('window.cleared=false;document.addEventListener("htmx:beforeRequest",()=>{window.cleared=document.querySelector("#err").textContent===""});')
        b.call('Network.emulateNetworkConditions', {'offline': True, 'latency': 0, 'downloadThroughput': 0, 'uploadThroughput': 0})
        b.evaluate('document.querySelector("form[data-auth-feedback=setup] button").click()')
        b.wait_for('document.querySelector("#err").textContent.includes("could not be reached")')
        assert b.evaluate('window.cleared')
        assert setup_links.peek(root, secret) is not None
    finally:
        b.close()


def test_history_cancel_without_previous_page_stays_put(tmp_path):
    source = (TEMPLATES / 'memorize_report.html').read_text()
    link = re.search(r'<a[^>]+data-history-back[^>]*>.*?</a>', source)[0]
    link = jinja2.Template(link).render(company_id='fixture')
    with fragment_server({'/': (200, shell(link))}) as origin:
        b = _Cdp(tmp_path / 'history-chrome')
        try:
            b.navigate(origin + '/')
            b.call('Page.resetNavigationHistory')
            assert b.evaluate('history.length') == 1
            b.evaluate('window.cancelPrevented=false;document.addEventListener("click",e=>{window.cancelPrevented=e.defaultPrevented});document.querySelector("[data-history-back]").click()')
            assert b.evaluate('window.cancelPrevented')
            assert b.evaluate('location.pathname') == '/'
        finally:
            b.close()
