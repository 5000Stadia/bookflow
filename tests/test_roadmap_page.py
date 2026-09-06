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

@pytest.mark.parametrize('group',['next','later'])
def test_future_rendered_target_survives_manifest_removal(site,group):
    state,client,_=site
    path=state.root/'notes/roadmap-status.json'
    status=json.loads(path.read_text())
    status[group]=[{'row':'25','title':'Future module','summary':'Planned'}]
    path.write_text(json.dumps(status))
    csrf=login(state,client)
    assert 'name="row" value="25"' in client.get('/').text
    status[group]=[];path.write_text(json.dumps(status))
    assert submit(state,client,csrf,'Future note','25').status_code==303
    page=client.get('/').text
    assert 'Archived module notes' in page and 'Future note' in page

def test_unknown_target_can_be_corrected_without_retyping(site):
    state,client,_=site
    csrf=login(state,client)
    response=submit(state,client,csrf,'Keep <this> exactly','999')
    assert response.status_code==409
    assert '<select name="row" required>' in response.text
    assert '<code>999</code>' in response.text and 'value="K"' in response.text
    assert '<textarea name="text" required>Keep &lt;this&gt; exactly</textarea>' in response.text
    token=re.search(r'name="csrf" value="([^"]+)"',response.text).group(1)
    assert submit(state,client,token,'Keep <this> exactly','22').status_code==303
    notes=roadmap.bridge.Project(state.root,state.comments).comments()
    assert len(notes)==1 and notes[0]['row']=='22' and notes[0]['text']=='Keep <this> exactly'

def test_correction_form_offers_remain_valid_after_module_closes(site):
    state,client,intention=site
    csrf=login(state,client)
    intention.write_text(intention.read_text()+'| 25 | New module | Useful |\n')
    response=submit(state,client,csrf,'Keep correction draft','999')
    assert response.status_code==409 and '<option value="25">' in response.text
    intention.write_text(intention.read_text().replace('| 25 | New module | Useful |\n',''))
    token=re.search(r'name="csrf" value="([^"]+)"',response.text).group(1)
    assert submit(state,client,token,'Keep correction draft','25').status_code==303
    assert 'Keep correction draft' in client.get('/').text


def test_completed_followups_reenter_global_queue_and_keep_history(site):
    state,client,intention=site
    path=state.root/'notes/roadmap-status.json'
    status=json.loads(path.read_text())
    status['rows']={'22':{'title':'Full payment module','status':'Building'}}
    status['completed']=[{'row':'22','title':'Payment engine','summary':'Reviewed component'},
                         {'row':'21','title':'History','summary':'Reviewed history'}]
    path.write_text(json.dumps(status))
    project=roadmap.bridge.Project(state.root,state.comments)
    old=project.add_comment('21','K','Original history note','human')
    project.consume(old['id'],'bookflowcodex')
    for target in ('project','22','21','99'):
        project.add_comment(target,'K','Unread for '+target,'human')
    csrf=login(state,client)
    page=client.get('/').text
    queue=page.split('<section id="unread">')[1].split('</section>')[0]
    assert 'Notes to read (4)' in queue
    assert all('Unread for '+target in queue for target in ('project','22','21','99'))
    assert 'Original history note' not in queue
    assert 'Full payment module' in queue and 'Payment engine' not in queue
    assert '<span class="badge complete">Completed</span>' in page
    assert 'Shared notes with Full payment module.' in page
    assert 'Original history note' in page and 'Reviewed by bookflowcodex' in page
    assert len([n for n in project.comments() if not n['consumed']])==4
    for note in project.comments():
        if not note['consumed']:project.consume(note['id'],'bookflowcodex')
    assert 'Notes to read (0)' in client.get('/').text
    assert submit(state,client,csrf,'Correction after completion','21').status_code==303
    queue=client.get('/').text.split('<section id="unread">')[1].split('</section>')[0]
    assert 'Notes to read (1)' in queue and 'Correction after completion' in queue
    assert 'History' in queue
    status['completed'][1]['status']='Reopened'
    status['completed'][1]['summary']='Confirmed original-scope defect; fix pending review.'
    path.write_text(json.dumps(status))
    page=client.get('/').text
    assert '<span class="badge flight">Reopened</span>' in page
    assert 'Correction after completion' in page and 'Original history note' in page


@pytest.mark.parametrize('width',[1280,390])
def test_browser_completed_note_checkpoint_and_followup(site,tmp_path,width):
    from tests.test_row5_browser_acceptance import _Cdp, CHROME
    if not CHROME.exists():pytest.skip('Chrome not installed')
    state,_,_=site
    path=state.root/'notes/roadmap-status.json'
    status=json.loads(path.read_text())
    status['completed']=[{'row':'21','title':'Financial history','summary':'Reviewed and complete.'}]
    path.write_text(json.dumps(status))
    browser=_Cdp(tmp_path/f'completed-chrome-{width}')
    try:
        browser.viewport(width,900)
        browser.call('Page.navigate',{'url':state.origin+'/access/'+state.key_file.read_text()})
        browser.wait_for('!!document.querySelector("#done .badge.complete")')
        browser.evaluate('''(() => {const d=document.querySelector('#done .discussion');d.open=true;const f=d.querySelector('form');f.querySelector('[name=text]').value='Please revisit this completed work';f.requestSubmit()})()''')
        browser.wait_for('!!document.querySelector("#unread .unread-note")')
        assert browser.evaluate('document.querySelector("#unread").innerText.includes("Please revisit this completed work")')
        project=roadmap.bridge.Project(state.root,state.comments)
        note=project.comments()[0]
        project.consume(note['id'],'bookflowcodex')
        browser.call('Page.reload',{})
        browser.wait_for('document.querySelector("#unread")?.innerText.includes("Notes to read (0)")')
        browser.evaluate('''(() => {const d=document.querySelector('#done .discussion');d.open=true;const f=d.querySelector('form');f.querySelector('[name=text]').value='Another update after the last read';f.requestSubmit()})()''')
        browser.wait_for('document.querySelector("#unread")?.innerText.includes("Another update after the last read")')
        assert browser.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert len(project.comments())==2 and not project.comments()[1]['consumed']
        import base64
        screenshot=browser.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':True})
        folder=Path('.cache/roadmap-notes');folder.mkdir(parents=True,exist_ok=True)
        (folder/f'completed-notes-{width}.png').write_bytes(base64.b64decode(screenshot['data']))
    finally:
        browser.close()
