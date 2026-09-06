/* Metadata-driven read controls. Decimal values remain strings throughout. */
(() => {
  const host = document.getElementById('browse-data');
  if (!host || document.getElementById('browse-form').dataset.ready) return;
  const data = JSON.parse(host.textContent), form = document.getElementById('browse-form');
  form.dataset.ready = '1';
  const $ = id => document.getElementById(id);
  let columns = data.columns.slice(), criteria = data.criteria.slice();
  let columnPage = data.options, filterPage = data.filters, selectedFilter = null;
  const columnMap = new Map([...data.options.items, ...columns].map(d => [d.key, d]));
  const filterMap = new Map(data.filters.items.map(d => [d.key, d]));
  const labels = {eq:'Equals',ne:'Does not equal',contains:'Contains',lt:'Less than',lte:'At most',gt:'Greater than',gte:'At least',is_missing:'Is missing',is_present:'Is present'};
  const message = text => { $('browse-error').textContent = text; };
  const node = (tag, text, props = {}) => Object.assign(document.createElement(tag), {textContent: text, ...props});
  function button(text, action) {const b = node('button', text, {type:'button'}); b.addEventListener('click', action); return b;}
  async function read(params, suffix = 'options') {
    if (suffix === 'options') params = {include_inactive: !$('metadata-active-only').checked, ...params};
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key,value]) => {if (value !== undefined && value !== null) query.set(key, Array.isArray(value) ? JSON.stringify(value) : String(value));});
    const response = await fetch(`${data.endpoint}/${suffix}?${query}`, {credentials:'same-origin', headers:{'Accept':'application/json'}});
    const value = await response.json();
    if (!response.ok) throw new Error(value.message || value.error?.message || 'The list changed or these choices are unavailable. Reload the page to restart.');
    return value;
  }
  function safe(action) {return async () => {message(''); try {await action();} catch (err) {message(err.message);}};}
  function option(select, d) {select.append(node('option', `${d.label}${d.active === false ? ' (inactive)' : ''}`, {value:d.key}));}
  function sync() {
    $('browse-columns').value = columns.map(d => d.key).join(',');
    $('browse-custom').value = JSON.stringify(criteria.filter(c => c.custom).map(c => c.custom));
    $('browse-legacy').replaceChildren(...criteria.filter(c => c.legacy).map(c => node('input', '', {type:'hidden',name:'filter',value:c.legacy})));
  }
  function drawColumns() {
    $('chosen-columns').replaceChildren();
    columns.forEach((d,i) => {
      const row = node('li',''); row.append(node('span',d.label));
      const up = button('Move up', () => {if(i){[columns[i-1],columns[i]]=[columns[i],columns[i-1]]; drawColumns(); $('chosen-columns').children[i-1].querySelector('button:not([hidden])').focus();}}); up.hidden = i === 0; up.setAttribute('aria-label',`Move ${d.label} up`);
      const down = button('Move down', () => {if(i < columns.length-1){[columns[i+1],columns[i]]=[columns[i],columns[i+1]];drawColumns();$('chosen-columns').children[i+1].querySelector('button:not([hidden])').focus();}}); down.hidden = i === columns.length-1; down.setAttribute('aria-label',`Move ${d.label} down`);
      const remove = button('Remove', () => {if(columns.length > 1){columns.splice(i,1);drawColumns();} else message('Keep at least one column.');}); remove.setAttribute('aria-label',`Remove ${d.label} column`);
      row.append(up,down,remove); $('chosen-columns').append(row);
    }); sync();
  }
  function drawCriteria() {
    $('active-criteria').replaceChildren();
    criteria.forEach((c,i) => {
      const row = node('div','',{className:'criterion'});
      const display = c.display !== undefined ? c.display : c.value;
      row.append(node('span',`${c.descriptor.label}${c.descriptor.active === false ? ' (inactive)' : ''} — ${labels[c.operator]}${display === undefined ? '' : ': ' + (display === true ? 'Yes' : display === false ? 'No' : display)}`));
      row.append(button('Remove filter', () => {criteria.splice(i,1);drawCriteria();})); $('active-criteria').append(row);
    }); sync();
  }
  function populate(select, page, map, append) {
    if (!append) select.replaceChildren(node('option','Choose a field',{value:''}));
    page.items.forEach(d => {map.set(d.key,d); if (![...select.options].some(o=>o.value===d.key)) option(select,d);});
  }
  let columnRequest = 0, filterRequest = 0;
  async function columnsPage(append) {
    const request = ++columnRequest;
    const page = await read({kind:'columns',query:$('column-search').value,cursor:append ? columnPage.next_cursor : null});
    if (request !== columnRequest) return;
    columnPage = page;
    populate($('available-columns'),columnPage,columnMap,append); $('more-columns').hidden=!columnPage.next_cursor;
  }
  async function filtersPage(append) {
    const request = ++filterRequest;
    const page = await read({kind:'filters',query:$('filter-search').value,cursor:append ? filterPage.next_cursor : null});
    if (request !== filterRequest) return;
    filterPage = page;
    populate($('available-filters'),filterPage,filterMap,append); $('more-filters').hidden=!filterPage.next_cursor;
  }
  let editorGeneration = 0;
  async function editor() {
    const generation = ++editorGeneration;
    $('filter-editor').replaceChildren();
    if (!selectedFilter || ['is_missing','is_present'].includes($('filter-operator').value)) return;
    const d = selectedFilter, label = node('label',d.kind === 'money' ? `Amount (${d.currency})` : 'Value');
    const discrete = d.kind === 'bool' || d.kind === 'choice' || (d.kind === 'reference' && d.reference_noun);
    const control = node(discrete ? 'select' : 'input','',{id:'filter-value'});
    if (discrete) control.append(node('option','Choose a value',{value:''}));
    if (d.kind === 'bool') {option(control,{key:'true',label:'Yes'});option(control,{key:'false',label:'No'});}
    else if (d.kind === 'choice' && !d.definition) d.choices.forEach(value => option(control,{key:value,label:value.replaceAll('_',' ')}));
    else if (!discrete) {control.type = d.kind === 'date' ? 'date' : 'text';if(['number','integer','money'].includes(d.kind)) control.inputMode='decimal';}
    label.append(control); $('filter-editor').append(label);
    if ((d.kind === 'choice' && d.definition) || (d.kind === 'reference' && d.reference_noun)) {
      const searchLabel=node('label','Find a value'), search=node('input','',{type:'search'});searchLabel.append(search);
      const find=button('Find values',safe(()=>load(false))), more=button('More values',safe(()=>load(true)));
      let page = null, valueRequest = 0;
      async function load(append) {
        const request = ++valueRequest;
        const next = await read(d.definition ? {kind:'choices',definition:d.definition,query:search.value,cursor:append ? page?.next_cursor : null} :
          {field:d.key,query:search.value,cursor:append ? page?.next_cursor : null}, d.definition ? 'options' : 'references');
        if (generation !== editorGeneration || request !== valueRequest) return;
        page=next; if(!append) control.replaceChildren(node('option','Choose a value',{value:''}));
        page.items.forEach(v => option(control,d.definition ? v : {key:v.id,label:v.label,active:v.active}));more.hidden=!page.next_cursor;
      }
      search.addEventListener('input', () => { more.hidden = true; });
      $('filter-editor').append(searchLabel,find,more); await load(false);
    }
  }
  $('available-filters').addEventListener('change',safe(async()=>{
    selectedFilter=filterMap.get($('available-filters').value); $('filter-operator').replaceChildren();
    (selectedFilter?.operators || []).forEach(op=>$('filter-operator').append(node('option',labels[op],{value:op})));
    await editor();
  }));
  $('filter-operator').addEventListener('change',safe(editor));
  $('add-filter').addEventListener('click',()=>{
    message('');const d=selectedFilter, operator=$('filter-operator').value, control=$('filter-value');
    if(!d) return message('Choose a filter field.');
    if(criteria.filter(c=>c.custom).length >=32 && d.definition) return message('Use at most 32 custom criteria.');
    const presence=['is_missing','is_present'].includes(operator);
    let value=control?.value;
    if(!presence && (value === undefined || (value === '' && d.kind !== 'text'))) return message('Choose or enter a value.');
    let display = control?.tagName === 'SELECT' ? control.selectedOptions[0].textContent : value;
    if (d.kind === 'money') {
      try {
        const exact = window.bookflowMath.evaluate(value,d.scale);
        if (exact.needsRounding) return message(`Use at most ${d.scale} decimal places; list filters do not round.`);
        const negative=exact.value.startsWith('-'), [whole,fraction='']=exact.value.replace('-','').split('.');
        value=(BigInt(whole+fraction.padEnd(d.scale,'0'))*(negative?-1n:1n)).toString();
        if (BigInt(value) < -9223372036854775808n || BigInt(value) > 9223372036854775807n) return message('Amount exceeds the supported range.');
        display=exact.value+' '+d.currency;
      } catch(err) {return message(err.message);}
    }
    if(d.definition){if(d.kind==='bool'&&!presence)value=value==='true';const custom={definition:d.definition,kind:presence?'presence':d.kind,operator};if(!presence)custom.value=value;criteria.push({descriptor:d,operator,value:presence?undefined:value,display:presence?undefined:display,custom});}
    else criteria.push({descriptor:d,operator,value,display,legacy:`${d.key}=${value}`});
    drawCriteria();
  });
  $('add-column').addEventListener('click',()=>{const d=columnMap.get($('available-columns').value);if(!d)return message('Choose a column.');if(columns.some(c=>c.key===d.key))return message('That column is already selected.');if(columns.length>=64)return message('Use at most 64 columns.');columns.push(d);drawColumns();});
  $('reset-columns').addEventListener('click',safe(async()=>{const page=await read({kind:'columns',keys:data.defaults,limit:200});page.items.forEach(d=>columnMap.set(d.key,d));columns=data.defaults.map(key=>columnMap.get(key));drawColumns();}));
  $('clear-filters').addEventListener('click',()=>{criteria=[];drawCriteria();});
  $('find-columns').addEventListener('click',safe(()=>columnsPage(false))); $('more-columns').addEventListener('click',safe(()=>columnsPage(true)));
  $('find-filters').addEventListener('click',safe(()=>filtersPage(false))); $('more-filters').addEventListener('click',safe(()=>filtersPage(true)));
  $('metadata-active-only').addEventListener('change',safe(async()=>{await columnsPage(false);await filtersPage(false);await editor();}));
  form.addEventListener('submit',sync);
  document.querySelectorAll('[data-sort-url]').forEach(button => button.addEventListener('click', () => { location.href=button.dataset.sortUrl; }));
  populate($('available-columns'),columnPage,columnMap,false); populate($('available-filters'),filterPage,filterMap,false);
  $('more-columns').hidden=!columnPage.next_cursor; $('more-filters').hidden=!filterPage.next_cursor; drawColumns(); drawCriteria();
})();
