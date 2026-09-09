/* Receipt selection only: the generated form owns preview, save and retries. */
(() => {
  if (window.bookflowDepositPicker) return;
  window.bookflowDepositPicker = true;
  const node = (tag, text) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; return e; };
  function initialize() {
    const marker=document.querySelector('[data-deposit-company]');
    const form=marker?.closest('form');
    if(!form || form.dataset.depositReady)return;
    form.dataset.depositReady='true';
    const exact=window.BookflowExactJSON, field=name=>form.elements.namedItem(name);
    const mode=field('f:document.mode'), date=field('f:document.date');
    const sources=form.querySelector('[data-collection-path="document.sources"]');
    if(!sources)return;
    date.type='date';
    const advanced=node('details');advanced.append(node('summary','Advanced deposit options'));
    for(const name of ['f:operation_key','f:dependency_guard','f:document.mode','f:document.draft','f:document.expected_version']){
      const row=field(name)?.closest('.form-field');if(row){advanced.append(row);if(['f:operation_key','f:dependency_guard'].includes(name))row.hidden=true;}
    }
    for(const name of ['f:document.custom_fields','f:document.expected_custom_field_kinds']){const row=field(name)?.closest('.form-field');if(row)advanced.append(row);}
    for(const name of ['ctx:source_ref','ctx:directive','ctx:idempotency_key']){const row=field(name)?.closest('.row');if(row)advanced.append(row);}
    const extras=node('details');extras.append(node('summary','Other money and cash back (optional)'));
    for(const name of ['f:document.cash_back.account','f:document.cash_back.amount','f:document.cash_back.memo','collection:document.additional']){const row=field(name)?.closest('.form-field');if(row)extras.append(row);}
    form.insertBefore(extras,form.querySelector('.form-submit'));
    form.insertBefore(advanced,form.querySelector('.form-submit'));
    sources.querySelector('legend').textContent='Received payments';
    const clearSources=field('empty:document.sources');if(clearSources){clearSources.checked=false;clearSources.closest('label').hidden=true;}
    const bank=field('f:document.deposit_to');
    const bankSelect=node('select');bankSelect.setAttribute('aria-label','Deposit to bank');
    bankSelect.append(new Option('Choose a bank account',''));bank.type='hidden';bank.after(bankSelect);
    const error=node('p');error.setAttribute('role','alert');marker.after(error);
    async function command(name,input){
      const response=await fetch('/companies/'+encodeURIComponent(marker.dataset.depositCompany)+'/commands/'+name.replaceAll(' ','.'),{
        method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},body:exact.stringify(input)});
      const result=exact.parse(await response.text());
      if(!response.ok || result.code)throw new Error(result.message || result.code || 'Unable to load receipts.');
      return result;
    }
    const selected=new Map();
    const rows=sources.querySelector('[data-collection-items]');
    for(const item of rows.children){
      const get=suffix=>item.querySelector('[name$=":'+suffix+'"]')?.value;
      if(get('source'))selected.set(get('source'),{source:get('source'),source_type:get('source_type'),expected_version:get('expected_version'),memo_override:get('memo_override')});
    }
    rows.hidden=true;sources.querySelector('[data-collection-add]').hidden=true;
    const controls=node('div');controls.className='actions';
    const query=node('input');query.type='search';query.placeholder='Customer, reference or receipt';query.setAttribute('aria-label','Find receipts');
    const load=node('button','Find undeposited receipts');load.type='button';
    const more=node('button','More receipts');more.type='button';more.hidden=true;
    const status=node('p');status.setAttribute('role','status');
    const list=node('div');list.className='deposit-receipts';
    controls.append(query,load);sources.append(controls,status,list,more);
    let cursor=null,busy=false,filter=null;
    function sync(){
      rows.replaceChildren();let i=0;
      for(const r of selected.values()){
        const wrap=node('div');
        for(const key of ['source','source_type','expected_version','memo_override'])if(r[key]!==undefined && r[key]!==null){
          const input=node('input');input.type='hidden';input.name=`c:document.sources:${i}:${key}`;input.value=String(r[key]);wrap.append(input);
        }
        rows.append(wrap);i++;
      }
      status.textContent=`${selected.size} receipt(s) selected. Preview shows the exact bank total.`;
      rows.dispatchEvent(new Event('change',{bubbles:true}));
    }
    async function find(append=false){
      if(busy)return;busy=true;load.disabled=true;more.disabled=true;error.textContent='';
      try{
        if(!date.value)throw new Error('Enter the deposit date first.');
        if(!append){filter={date:date.value,...(query.value?{q:query.value}:{}),limit:50};cursor=null;list.replaceChildren();}
        const result=await command('deposit sources',{...filter,...(cursor?{cursor}:{})});
        for(const r of result.items){
          const label=node('label');label.className='workspace-card';label.style.display='block';
          const check=node('input');check.type='checkbox';check.checked=selected.has(r.source);check.disabled=!r.eligible;
          check.addEventListener('change',()=>{if(check.checked)selected.set(r.source,r);else selected.delete(r.source);sync();});
          label.append(check,document.createTextNode(` ${r.received_from} · ${r.number} · ${r.date}`),node('div',`${exact.minor(r.amount.minor_units,r.amount.currency)} ${r.amount.currency} · ${r.payment_method || ''} · ${r.reference || ''}`));list.append(label);
        }
        cursor=result.next_cursor;more.hidden=!cursor;
        status.textContent=`${selected.size} selected · ${result.total_count} matching receipt(s). Preview shows the exact bank total.`;
      }catch(e){error.textContent=e.message;}finally{busy=false;load.disabled=false;more.disabled=false;}
    }
    load.addEventListener('click',()=>find());more.addEventListener('click',()=>find(true));
    date.addEventListener('change',()=>{cursor=null;more.hidden=true;list.replaceChildren();status.textContent='Date changed. Find receipts again; existing selections are retained for preview.';});
    bankSelect.addEventListener('change',()=>{bank.value=bankSelect.value;bank.dispatchEvent(new Event('change',{bubbles:true}));});
    command('account list',{}).then(result=>{
      for(const row of result.items)if(row.type==='bank' && row.active!==false)bankSelect.append(new Option(row.full_name || row.name,row.id));
      bankSelect.value=bank.value;
      if(bank.value && !bankSelect.value)bankSelect.append(new Option(bank.value,bank.value,true,true));
    }).catch(e=>{error.textContent=e.message;bank.type='text';bankSelect.hidden=true;});
    status.textContent=`${selected.size} receipt(s) selected. Find receipts to review or change this selection.`;
  }
  document.addEventListener('DOMContentLoaded',initialize);document.addEventListener('htmx:afterSwap',initialize);initialize();
})();
