/* Local calendar dates. Text fields remain the authoritative submitted values. */
(() => {
  'use strict';
  if (globalThis.BookflowDates) return;
  const iso = d => `${String(d.getFullYear()).padStart(4, '0')}-${String(d.getMonth()+1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  function local(y, m, d) { const value = new Date(2000, 0, 1, 12); value.setFullYear(y, m, d); return value; }
  function valid(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const [y,m,d] = value.split('-').map(Number);
    return y > 0 && iso(local(y,m-1,d)) === value;
  }
  function range(kind, today = new Date()) {
    const y=today.getFullYear(), m=today.getMonth(), d=today.getDate(), q=Math.floor(m/3)*3;
    const day=local(y,m,d);
    const pairs = {
      today:[day,day], yesterday:[local(y,m,d-1),local(y,m,d-1)],
      'this-month':[local(y,m,1),local(y,m+1,0)], 'last-month':[local(y,m-1,1),local(y,m,0)],
      'this-quarter':[local(y,q,1),local(y,q+3,0)], 'last-quarter':[local(y,q-3,1),local(y,q,0)],
      'this-year':[local(y,0,1),local(y+1,0,0)], 'last-year':[local(y-1,0,1),local(y,0,0)],
      'month-to-date':[local(y,m,1),day], 'quarter-to-date':[local(y,q,1),day], 'year-to-date':[local(y,0,1),day],
    };
    return kind === 'clear' ? ['', ''] : pairs[kind]?.map(iso) || null;
  }
  // Pure date arithmetic is also exercised independently of browser interaction.
  globalThis.BookflowDates = {range, valid};
  if (typeof document === 'undefined') return;
  const controls = new WeakMap();
  function label(input) {
    return input.getAttribute('aria-label') || input.labels?.[0]?.textContent.trim().split('\n')[0] || input.name || 'date';
  }
  function changed(input) {
    // Choosing an explicit value cancels only this field's existing clear flag.
    const clear = input.form?.elements.namedItem('clear:' + input.name.replace(/^f:/, ''));
    if (clear?.type === 'checkbox') clear.checked = false;
    input.dispatchEvent(new Event('input', {bubbles:true}));
    input.dispatchEvent(new Event('change', {bubbles:true}));
  }
  function enhance(input) {
    if (controls.has(input) || input.matches('[data-date-native]')) return;
    const existing = input.dataset.datePicker;
    let picker, button, tools;
    if (existing) {
      picker=document.getElementById(existing); button=document.getElementById(existing+'-open');
      tools=picker.closest('label');
    } else {
      tools=document.createElement('span'); tools.className='date-tools'; tools.dateInput=input;
      button=document.createElement('button'); button.type='button'; button.textContent='Calendar';
      picker=document.createElement('input'); picker.type='date'; picker.dataset.dateNative=''; picker.hidden=true;
      tools.append(button,picker); input.after(tools);
    }
    button.classList.add('date-calendar');
    button.setAttribute('aria-label', 'Open calendar for ' + label(input));
    picker.setAttribute('aria-label', 'Choose ' + label(input));
    // Auxiliary picker has no wire name and never owns form validity.
    picker.removeAttribute('name'); picker.required=false;
    const hide=()=>{ if(existing) tools.hidden=true; else picker.hidden=true; };
    function open() {
      if (!input.hasAttribute('data-date') || input.disabled || input.readOnly || input.closest('[hidden]')) return;
      picker.value=valid(input.value) ? input.value : '';
      picker.disabled=false;
      if(existing) tools.hidden=false; else picker.hidden=false;
      picker.focus();
      try { picker.showPicker?.(); } catch (_) { /* Keyboard-editable native fallback remains visible. */ }
    }
    button.addEventListener('click', open);
    input.addEventListener('keydown', e=>{if(e.altKey && e.key==='ArrowDown'){e.preventDefault();open();}});
    function sync() {
      if(!picker.value || input.value===picker.value) return; // Cancelling preserves typed attempts.
      input.value=picker.value; changed(input);
    }
    // Native keyboard selection can emit input before change (which may wait for blur).
    picker.addEventListener('input', sync);
    picker.addEventListener('change', ()=>{sync();hide();input.focus();});
    picker.addEventListener('keydown', e=>{if(e.key==='Escape'){hide();input.focus();}});
    controls.set(input,{tools,button,picker,existing});
    input.placeholder ||= 'YYYY-MM-DD';
  }
  const presets = [['custom','Custom'],['today','Today'],['yesterday','Yesterday'],
    ['this-month','This month'],['last-month','Last month'],
    ['this-quarter','This calendar quarter'],['last-quarter','Last calendar quarter'],
    ['this-year','This calendar year'],['last-year','Last calendar year'],
    ['month-to-date','Month to date'],['quarter-to-date','Calendar quarter to date'],['year-to-date','Calendar year to date']];
  const pairs=[['date_from','date_to','Date range'],['due_from','due_to','Due-date range'],['expected_from','expected_to','Expected-date range']];
  const paired=new WeakMap();
  function ranges(form) {
    for(const [from,to,title] of pairs) {
      // Exact known bound pairs, in the same form/collection row. Never date/due_date.
      for(const start of form.querySelectorAll('input[data-date]')) {
        const parts=start.name.split(/[:.]/); if(parts.at(-1)!==from) continue;
        const endName=start.name.slice(0,-from.length)+to;
        const end=Array.from(form.querySelectorAll('input[data-date]')).find(i=>i.name===endName);
        if(!end) continue;
        if(paired.get(start)?.end===end) {paired.get(start).select.disabled=start.disabled||end.disabled;continue;}
        paired.get(start)?.wrapper.remove();
        const wrapper=document.createElement('label'); wrapper.className='date-range-presets';
        wrapper.append(title+' · ');
        const select=document.createElement('select'); select.setAttribute('aria-label',title+' preset');
        for(const [value,text] of presets) select.add(new Option(text,value));
        const optional=i=>i.dataset.dateOptional ? i.dataset.dateOptional==='true' : !i.required;
        if(optional(start)&&optional(end)) select.add(new Option('Clear dates','clear'));
        wrapper.append(select); controls.get(start).tools.after(wrapper);
        let setting=false;
        select.addEventListener('change',()=>{
          const values=range(select.value); if(!values) return;
          setting=true; start.value=values[0];end.value=values[1];changed(start);changed(end);setting=false;
        });
        for(const input of [start,end]) input.addEventListener('input',()=>{if(!setting)select.value='custom';});
        paired.set(start,{end,wrapper,select});
      }
    }
  }
  function refresh() {
    // Custom-definition defaults can switch types while the existing input survives.
    document.querySelectorAll('[data-definition-default]').forEach(wrapper=>{
      const input=wrapper.querySelector('input:not([type=checkbox])');
      if(!input) return;
      const kind=input.form?.elements.namedItem('f:kind')?.value;
      if(kind==='date') input.dataset.date='';
      else if(input.hasAttribute('data-date')) {
        input.removeAttribute('data-date');
      }
    });
    document.querySelectorAll('.date-tools').forEach(tools=>{if(!tools.dateInput?.isConnected)tools.remove();else tools.hidden=!tools.dateInput.hasAttribute('data-date');});
    document.querySelectorAll('input[data-date],input[type=date]:not([data-date-native])').forEach(input=>{
      if(input.type==='date') {const value=input.getAttribute('value') || input.value;input.type='text';input.value=value;input.dataset.date='';}
      enhance(input);
      const owned=controls.get(input);if(owned)owned.button.disabled=input.disabled || input.readOnly;
    });
    document.querySelectorAll('form').forEach(ranges);
  }
  let scheduled=false;
  function schedule(){if(!scheduled){scheduled=true;queueMicrotask(()=>{scheduled=false;refresh();});}}
  document.addEventListener('change',schedule);
  document.addEventListener('htmx:load',schedule);
  new MutationObserver(schedule).observe(document.documentElement,{childList:true,subtree:true});
  refresh();
})();
