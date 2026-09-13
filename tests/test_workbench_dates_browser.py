"""Calendar affordances and local presets in real desktop/phone forms."""
import base64
import json
from pathlib import Path

import pytest
from tests.test_row5_browser_acceptance import CHROME, browser_site
from tests.test_row8_register_browser import register_browser, _key, _command

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason='Chrome unavailable')


def click(b, selector):
    b.evaluate(f'document.querySelector({json.dumps(selector)}).scrollIntoView({{block:"center"}})')
    rect=b.evaluate(f'(()=>{{var r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}};}})()')
    if b.evaluate('innerWidth <= 390'):
        b.call('Emulation.setTouchEmulationEnabled', {'enabled':True})
        b.call('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[rect]})
        b.call('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
        return
    b.call('Input.dispatchMouseEvent', {'type':'mousePressed','button':'left','clickCount':1,**rect})
    b.call('Input.dispatchMouseEvent', {'type':'mouseReleased','button':'left','clickCount':1,**rect})


def screenshot(b, width, name):
    destination=Path('notes/date-screenshots');destination.mkdir(parents=True,exist_ok=True)
    height=b.evaluate('Math.max(innerHeight,document.documentElement.scrollHeight)')
    (destination/f'{name}-{width}.png').write_bytes(base64.b64decode(b.call('Page.captureScreenshot',{
        'format':'png','captureBeyondViewport':True,
        'clip':{'x':0,'y':0,'width':width,'height':height,'scale':1}})['data']))


@pytest.mark.parametrize('width',[1280,390])
@pytest.mark.timeout(180)
def test_calendars_presets_invalid_attempts_and_dynamic_controls(register_browser,width):
    env=register_browser;b=env.browser;b.viewport(width,900)
    b.wait_for("!!document.querySelector('#register-period .date-range-presets')")
    assert b.evaluate("document.querySelectorAll('#register-calendar-open').length") == 1
    # Actual pointer activation delegates to the real native API, preserving invalid text.
    b.evaluate("""window.calendarCalls=0;window.calendarErrors=[];
      var nativePicker=HTMLInputElement.prototype.showPicker;
      HTMLInputElement.prototype.showPicker=function(){window.calendarCalls++;try{return nativePicker.call(this);}catch(e){window.calendarErrors.push(e.name);throw e;}};
      document.querySelector('#register-date').value='2026-02-no';""")
    click(b,'#register-calendar-open')
    assert b.evaluate('window.calendarCalls') == 1
    assert b.evaluate('window.calendarErrors') == []
    assert b.evaluate("document.querySelector('#register-date').value") == '2026-02-no'
    _key(b,'Escape')
    assert b.evaluate("document.querySelector('#register-date').value") == '2026-02-no'
    assert not b.evaluate("!!document.querySelector('#register-period option[value=clear]')")
    screenshot(b,width,'register-calendar')

    base=f'{env.site.base_url}/c/{env.site.company_id}'
    b.navigate(base+'/report/general-ledger')
    b.wait_for("!!document.querySelector('.date-range-presets')")
    # Choice fills the real report controls without submitting. Entered custom bounds reset it.
    b.evaluate("""window.submits=0;document.querySelector('form[data-generated-form]').addEventListener('submit',()=>window.submits++);
      var s=document.querySelector('.date-range-presets select');s.value='last-quarter';s.dispatchEvent(new Event('change',{bubbles:true}));""")
    assert b.evaluate("[document.querySelector('[name=\"f:date_from\"]').value,document.querySelector('[name=\"f:date_to\"]').value]") == b.evaluate("BookflowDates.range('last-quarter')")
    assert b.evaluate('window.submits') == 0
    assert not b.evaluate("!!document.querySelector('.date-range-presets option[value=clear]')")
    screenshot(b,width,'report-presets')
    # A failed real HTMX submission retains the exact invalid text and gets one new widget.
    b.evaluate("""document.querySelector('[name="f:date_from"]').value='2026-02-no';
      window.oldDateForm=document.querySelector('form[data-generated-form]');oldDateForm.requestSubmit();""")
    b.wait_for("!window.oldDateForm.isConnected && !!document.querySelector('.date-calendar')")
    assert b.evaluate("document.querySelector('[name=\"f:date_from\"]').value") == '2026-02-no'
    assert b.evaluate("document.querySelectorAll('[name=\"f:date_from\"] + .date-tools').length") == 1
    assert b.evaluate("document.querySelector('.date-range-presets select').value") == 'custom'
    screenshot(b,width,'retained-invalid-date')

    b.navigate(base+'/invoice/post')
    b.wait_for("!!document.querySelector('[name=\"f:date\"] + .date-tools')")
    assert b.evaluate("document.querySelector('[name=\"f:due_date\"]').hasAttribute('data-date')")
    assert b.evaluate("document.querySelectorAll('.date-range-presets').length") == 0
    # Keyboard access opens the auxiliary calendar; picking updates the authoritative wire value.
    b.evaluate("document.querySelector('[name=\"f:date\"]').focus()")
    b.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'ArrowDown','code':'ArrowDown','modifiers':1})
    b.wait_for("document.activeElement.matches('[data-date-native]')")
    _key(b,'Escape')
    b.evaluate("document.querySelector('[name=\"f:date\"]').value='2028-02-28'")
    click(b,'[name="f:date"] + .date-tools .date-calendar')
    screenshot(b,width,'native-date-control')
    _key(b,'Escape')
    # CDP targets the page, not the browser-owned popup grid. Check its change
    # contract explicitly; native opening is exercised with real pointer/touch above.
    b.evaluate("""var p=document.querySelector('[name="f:date"] + .date-tools input');p.value='2028-02-29';p.dispatchEvent(new Event('change',{bubbles:true}));""")
    assert b.evaluate("document.querySelector('[name=\"f:date\"]').value") == '2028-02-29'
    # Unsupported showPicker keeps a visible, keyboard-editable native fallback.
    b.evaluate("window.savedPicker=HTMLInputElement.prototype.showPicker;HTMLInputElement.prototype.showPicker=undefined")
    click(b,'[name="f:date"] + .date-tools .date-calendar')
    assert b.evaluate("document.activeElement.matches('[data-date-native]') && !document.activeElement.hidden")
    _key(b,'Escape')
    b.evaluate("HTMLInputElement.prototype.showPicker=window.savedPicker")
    screenshot(b,width,'invoice-calendar')

    # Real list forms have separate main and due-date pairs, and optional Clear dates.
    b.navigate(base+'/bill')
    b.wait_for("document.querySelectorAll('.date-range-presets').length === 2")
    b.evaluate("""document.querySelector('[name=due_from]').value='2020-01-01';
      var s=document.querySelector('[aria-label="Date range preset"]');s.value='today';s.dispatchEvent(new Event('change'));""")
    assert b.evaluate("document.querySelector('[name=due_from]').value") == '2020-01-01'
    b.evaluate("""var s=document.querySelector('[aria-label="Due-date range preset"]');s.value='clear';s.dispatchEvent(new Event('change'));""")
    assert b.evaluate("[document.querySelector('[name=due_from]').value,document.querySelector('[name=due_to]').value]") == ['','']
    screenshot(b,width,'separate-list-ranges')

    definition=_command(b,env.site,'custom-field.create',{'name':'Calendar witness','kind':'date','scopes':['customer']})
    b.navigate(base+'/customer/create')
    selector=f'[name="cf:{definition["id"]}"]'
    b.wait_for(f'!!document.querySelector({json.dumps(selector+" + .date-tools")})')
    b.evaluate(f'''var i=document.querySelector({json.dumps(selector)});i.value='not-a-date';i.dispatchEvent(new Event('input',{{bubbles:true}}));''')
    assert b.evaluate(f'document.querySelector({json.dumps("[name=\"cf-state:"+definition["id"]+"\"]")}).value') == 'set'
    # Collection insertion/removal uses the same initializer (typed nested metadata is separately checked).
    b.evaluate("""var f=document.querySelector('form[data-generated-form]');
      f.insertAdjacentHTML('beforeend','<fieldset id="date-row"><label>Nested date<input name="c:rows:0:day" data-date value="bad attempt"></label></fieldset>');""")
    b.wait_for("!!document.querySelector('#date-row .date-calendar')")
    b.evaluate("document.dispatchEvent(new Event('htmx:load'));document.dispatchEvent(new Event('htmx:load'))")
    assert b.evaluate("document.querySelectorAll('#date-row .date-calendar').length") == 1
    assert b.evaluate("document.querySelector('#date-row input[data-date]').value") == 'bad attempt'
    assert b.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    screenshot(b,width,'custom-and-dynamic-date')


@pytest.mark.timeout(120)
def test_native_calendar_keyboard_selection_updates_text_on_trusted_input(register_browser):
    """No screenshot/viewport resize between opening the popup and choosing a day."""
    b=register_browser.browser
    evidence=[]
    for width in (1280,390):
        b.viewport(width,900)
        b.evaluate("""window.nativeDateEvents=[];
          document.querySelector('#register-date').value='2028-02-28';
          for(var kind of ['input','change']) document.querySelector('#register-calendar').addEventListener(kind,e=>window.nativeDateEvents.push({type:e.type,trusted:e.isTrusted,value:e.target.value}));""")
        click(b,'#register-calendar-open')
        _key(b,'ArrowRight')
        _key(b,'Enter')
        state=b.evaluate("({value:document.querySelector('#register-date').value,picker:document.querySelector('#register-calendar').value,events:window.nativeDateEvents,active:document.activeElement.id})")
        evidence.append({'width':width,**state})
        Path('notes/native-selection-evidence.json').write_text(json.dumps(evidence,indent=2))
        assert state['value']=='2028-02-29',state
        assert any(e['trusted'] and e['value']=='2028-02-29' for e in state['events']),state
        screenshot(b,width,'native-selected-leap-day')
