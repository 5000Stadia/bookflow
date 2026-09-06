"""Ordinary access, retention and rendering checks against disposable roadmap notes."""
import importlib.util
import json
from pathlib import Path
import re
import secrets
import threading
import urllib.parse
from http.server import ThreadingHTTPServer
import httpx
import pytest

spec = importlib.util.spec_from_file_location('roadmap', Path(__file__).resolve().parents[1] / 'tools/roadmap.py')
roadmap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roadmap)

@pytest.fixture
def site(tmp_path):
    (tmp_path/'design/specs').mkdir(parents=True)
    intention = tmp_path/'design/intention.md'
    intention.write_text("# Sample — intention\n## What we're making\nTest\n## The spec list\n| # | What to build | Done |\n|---|---|---|\n| 22 | Payments | Correct totals |\n")
    (tmp_path/'notes').mkdir()
    (tmp_path/'notes/roadmap-status.json').write_text(json.dumps({'summary':'Test progress','updated_at':'2026-09-06T10:00:00+00:00','rows':{},'next':[],'later':[],'completed':[]}))
    key = tmp_path/'key'; key.write_text(secrets.token_hex(32)); key.chmod(0o600)
    state = roadmap.State(tmp_path,key,'http://127.0.0.1:0')
    server=ThreadingHTTPServer(('127.0.0.1',0),roadmap.handler(state))
    origin=f'http://127.0.0.1:{server.server_port}';state.origin=origin
    thread=threading.Thread(target=server.serve_forever);thread.start()
    with httpx.Client(base_url=origin,follow_redirects=False) as client:
        yield state,client,intention
    server.shutdown();thread.join();server.server_close()

def login(state,client):
    response=client.get('/access/'+state.key_file.read_text())
    assert response.status_code==303
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=Strict' in response.headers['set-cookie']
    page=client.get('/')
    assert page.status_code==200
    return re.search(r'name="csrf" value="([^"]+)"',page.text).group(1)

def submit(state,client,csrf,text='Useful detail',row='22'):
    return client.post('/note',headers={'Origin':state.origin},data={'row':row,'author':'K','text':text,'csrf':csrf})

def test_access_and_real_note_roundtrip(site):
    state,client,_=site
    assert client.get('/').status_code==401
    assert client.get('/access/'+('0'*64)).status_code==403
    csrf=login(state,client)
    assert submit(state,client,csrf,'<script>alert(1)</script>').status_code==303
    page=client.get('/')
    assert '&lt;script&gt;' in page.text and '<script>alert(1)</script>' not in page.text
    notes=roadmap.bridge.Project(state.root,state.comments).comments()
    assert len(notes)==1 and notes[0]['text']=='<script>alert(1)</script>'
    assert state.comments.stat().st_mode & 0o777 == 0o600

def test_missing_csrf_and_unknown_target_keep_draft(site):
    state,client,_=site
    csrf=login(state,client)
    assert submit(state,client,'','Keep me').status_code==403
    response=submit(state,client,csrf,'Keep this too','999')
    assert response.status_code==409 and 'Keep this too' in response.text
    assert not state.comments.read_text()

def test_closed_row_form_is_accepted_and_note_is_visible(site):
    state,client,intention=site
    csrf=login(state,client)
    intention.write_text(intention.read_text().replace('| 22 | Payments | Correct totals |\n',''))
    assert submit(state,client,csrf,'After closure').status_code==303
    page=client.get('/').text
    assert 'Archived module notes' in page and 'After closure' in page

def test_rotation_expiry_and_restart_preserve_unsaved_note(site):
    state,client,_=site
    csrf=login(state,client)
    state.key_file.write_text(secrets.token_hex(32))
    response=submit(state,client,csrf,'Rotation draft')
    assert response.status_code==401 and 'Rotation draft' in response.text
    csrf=login(state,client)
    for session in state.sessions.values():session['expires']=0
    assert submit(state,client,csrf,'Expiry draft').status_code==401
    csrf=login(state,client)
    state.sessions.clear()  # Exactly the empty session state of a new service process.
    assert submit(state,client,csrf,'Restart draft').status_code==401
    assert not state.comments.read_text()
    assert login(state,client)

@pytest.mark.parametrize('width',[1280,390])
def test_real_browser_form_saves_with_origin_and_fits_viewport(site,tmp_path,width):
    from tests.test_row5_browser_acceptance import _Cdp, CHROME
    if not CHROME.exists():pytest.skip('Chrome not installed')
    state,_,_=site
    browser=_Cdp(tmp_path/f'chrome-{width}')
    try:
        browser.viewport(width,900)
        browser.call('Page.navigate',{'url':state.origin+'/access/'+state.key_file.read_text()})
        browser.wait_for('!!document.querySelector("#now")')
        assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.evaluate('''(() => {const details=document.querySelector('#now .discussion');details.open=true;const form=details.querySelector('form');form.querySelector('[name=text]').value='A browser note';form.requestSubmit()})()''')
        browser.wait_for('document.body.textContent.includes("A browser note")')
        browser.evaluate('document.querySelector("#now .discussion").open=true')
        assert browser.evaluate('document.body.innerText.includes("A browser note")')
        notes=roadmap.bridge.Project(state.root,state.comments).comments()
        assert len(notes)==1 and notes[0]['text']=='A browser note'
    finally:
        browser.close()
