"""Display-only projected activity contract in real Chrome; no DB/auth claim."""
import base64
import json
from pathlib import Path
import shutil

import pytest

from bookflow.core.history_models import HistoryActivityItem
from tests.test_row5_browser_acceptance import CHROME, _Cdp

SCRIPT = Path(__file__).resolve().parents[1] / 'src/bookflow/adapters/workbench/static/annotations.js'
TOKEN = 'opaque.v2_A-z.9x_-unchanged'
TARGET = dict(record_type='customer', record_id='display-customer')


def item(key, **values):
    # Typed current schema, with nullable/private fields genuinely omitted on wire.
    defaults = dict(kind='audit', at='2026-09-08T01:02:03Z', event_id='event-' + key,
        entry_id='entry-' + key, record_type='customer', record_id=TARGET['record_id'],
        action='update', command=None, summary='Visible change ' + key,
        actor_id=None, actor_name=None, principal_id=None, principal_name=None,
        interface='http', version_before=None, version_after=None)
    return HistoryActivityItem(**(defaults | values)).model_dump(mode='json', exclude_none=True)


@pytest.mark.skipif(not CHROME.is_file(), reason='real Chrome is not installed')
@pytest.mark.parametrize('width', (1280, 390))
def test_projected_activity_attribution_and_opaque_pages(tmp_path, width):
    reason = 'Job done <img src=x onerror=window.unsafe=true> & paid'
    instruction = 'Post <script>window.unsafe=true</script> & invoice only when paid.'
    directive = dict(id='directive /?#&é', code='SI-7 <img src=x>', text=instruction)
    human = item('human', actor_id='human-id', actor_name='Human reader',
        explanation=dict(reason='Human explanation', directive_status='not_cited', directive=None))
    agent = item('agent', actor_id='agent-id', actor_name='Service agent', principal_id='principal-id',
        principal_name='Pat <img src=x onerror=window.unsafe=true>', interface='mcp',
        body='Visible <script>window.unsafe=true</script> body', text_truncated=True,
        explanation=dict(reason=reason, directive_status='available', directive=directive))
    hidden = item('hidden')
    id_only = item('id-only', actor_id='visible-agent-id', principal_id='visible-principal-id')
    unavailable = item('unavailable', explanation=dict(reason='Admitted reason without directive',
        directive_status='unavailable', directive=None))
    omitted = item('omitted', text_truncated=True, explanation=dict(reason=None,
        directive_status='available', directive=dict(id='omitted-directive', code='SI-8', text='')))
    # Adversarial extra response fields are NOT a valid ProjectedActivity shape.
    # They prove this display does not resurrect retired/private attribution or explanation.
    hidden.update(on_behalf_of='RAW-PRINCIPAL', on_behalf_of_name='RAW-NAME',
                  actor_kind='RAW-KIND', reason='RAW-REASON', source_ref='RAW-SOURCE',
                  explanation=None, directive_id='RAW-DIRECTIVE-ID',
                  directive_code='RAW-CODE', directive_text='RAW-INSTRUCTION',
                  directive=dict(id='RAW-NESTED-ID', code='RAW-NESTED-CODE', text='RAW-NESTED-TEXT'),
                  seq=987654321, high_water=987654322)
    unavailable['explanation']['directive'] = dict(id='RAW-UNAVAILABLE-ID',
        code='RAW-UNAVAILABLE-CODE', text='RAW-UNAVAILABLE-TEXT')
    omitted['directive_text'] = 'RAW-OMITTED-INSTRUCTION'
    pages = [dict(items=[human, agent], has_more=True, next_cursor=TOKEN),
             dict(items=[hidden, id_only, unavailable, omitted], has_more=False, next_cursor=None)]
    profile = tmp_path / 'chrome'
    browser = _Cdp(profile)
    try:
        browser.viewport(width, 850)
        config = dict(company='display-company', target=TARGET, writes=[], allowed={'activity': True})
        markup = '<section data-annotations="">' + ''.join(
            f'<section data-section="{kind}"><div data-items></div><p data-list-status role="status"></p>'
            '<button data-refresh>Refresh</button><button data-more hidden>More</button></section>'
            for kind in ('notes', 'files', 'activity')) + '</section>'
        browser.evaluate('document.body.innerHTML = ' + json.dumps(markup))
        # Neutral display fixture styling; this does not test workbench CSS/layout.
        browser.evaluate("document.body.style.cssText='background:white;color:black;font:16px sans-serif'")
        browser.evaluate('document.querySelector("[data-annotations]").dataset.annotations = ' + json.dumps(json.dumps(config)))
        browser.evaluate('window.pages = ' + json.dumps(pages))
        browser.evaluate('''window.requests=[]; window.errors=[];
          window.addEventListener('error',e=>errors.push(e.message));
          window.fetch=async (url,options)=>{
            if(url!=='/companies/display-company/commands/activity') throw Error('Unexpected request '+url);
            requests.push({url,method:options.method,input:JSON.parse(options.body)});
            return new Response(JSON.stringify(pages[requests.length===2?1:0]),{status:200,headers:{'Content-Type':'application/json'}});
          };''')
        browser.evaluate(SCRIPT.read_text())
        browser.wait_for("document.querySelector('[data-section=activity] [data-list-status]').textContent==='More entries available.'")
        text = browser.evaluate("[...document.querySelectorAll('[data-section=activity] .annotation-entry')].map(e=>e.innerText)")
        assert len(text) == 2
        assert 'Human reader' in text[0] and 'On behalf of' not in text[0]
        assert 'Human explanation' in text[0] and 'directive' not in text[0]
        assert 'Service agent' in text[1]
        assert 'On behalf of Pat <img src=x onerror=window.unsafe=true>' in text[1]
        assert 'Visible <script>window.unsafe=true</script> body' in text[1]
        assert 'Excerpt; full text is in audit event event-agent' in text[1]
        assert reason in text[1] and instruction in text[1] and directive['code'] in text[1]
        assert 'Instruction omitted' not in text[1]
        admitted_url = '/c/display-company/directive/directive%20%2F%3F%23%26%C3%A9'
        assert browser.evaluate("[...document.querySelectorAll('[data-section=activity] a')].map(a=>[a.textContent,a.getAttribute('href')])") == [
            [directive['code'], admitted_url]]
        browser.evaluate("document.querySelector('[data-section=activity] [data-more]').click()")
        browser.wait_for("document.querySelector('[data-section=activity] [data-list-status]').textContent==='All entries loaded.'")
        text = browser.evaluate("[...document.querySelectorAll('[data-section=activity] .annotation-entry')].map(e=>e.innerText)")
        assert len(text) == 6 and 'Human reader' in text[0] and 'Service agent' in text[1]
        assert 'Unknown actor' in text[2] and 'On behalf of' not in text[2]
        assert browser.evaluate("[...document.querySelectorAll('[data-section=activity] .annotation-entry')[2].querySelectorAll('p')].map(p=>p.textContent)") == [
            'Unknown actor · 2026-09-08T01:02:03Z', 'Visible change hidden']
        assert 'visible-agent-id' in text[3] and 'On behalf of visible-principal-id' in text[3]
        assert 'Admitted reason without directive' in text[4]
        assert 'Cited directive unavailable.' in text[4]
        assert 'SI-8' in text[5] and 'Instruction omitted from this excerpt.' in text[5]
        assert 'Read full instruction' in text[5]
        assert 'Excerpt; full text is in audit event event-omitted' in text[5]
        assert browser.evaluate("[...document.querySelectorAll('[data-section=activity] .annotation-entry')].map(e=>[...e.querySelectorAll('a')].map(a=>[a.textContent,a.getAttribute('href')]))") == [
            [], [[directive['code'], admitted_url]], [], [], [],
            [['SI-8', '/c/display-company/directive/omitted-directive'],
             ['Read full instruction', '/c/display-company/directive/omitted-directive']]]
        assert not any(word in '\n'.join(text) for word in ('RAW-', '987654321', '987654322', 'undefined', 'null'))
        assert browser.evaluate("document.querySelector('[data-section=activity] [data-more]').hidden")
        assert browser.evaluate('requests') == [
            dict(url='/companies/display-company/commands/activity', method='POST', input=TARGET | {'limit':20}),
            dict(url='/companies/display-company/commands/activity', method='POST', input=TARGET | {'limit':20, 'cursor':TOKEN})]
        assert not browser.evaluate("!!window.unsafe || !!document.querySelector('[data-section=activity] img,[data-section=activity] script')")
        assert browser.evaluate('errors') == []
        assert browser.evaluate('pages') == pages  # Display did not fabricate response fields.
        (tmp_path / 'rendered.json').write_text(json.dumps(dict(width=width, entries=text,
            requests=browser.evaluate('requests'), pages=pages), indent=2))
        (tmp_path / 'activity.png').write_bytes(base64.b64decode(browser.call('Page.captureScreenshot', {'format':'png'})['data']))
        browser.evaluate("document.querySelector('[data-section=activity] [data-refresh]').click()")
        browser.wait_for("requests.length===3 && document.querySelector('[data-section=activity] [data-list-status]').textContent==='More entries available.'")
        assert browser.evaluate('requests[2].input') == TARGET | {'limit':20}
        assert browser.evaluate("document.querySelectorAll('[data-section=activity] .annotation-entry').length") == 2
        assert browser.evaluate('errors') == []
    finally:
        browser.close()
        shutil.rmtree(profile)  # Only this closed disposable Chrome cache.
