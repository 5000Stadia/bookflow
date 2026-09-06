/* Browser presentation and durable draft cooperation over the shared registry. */
(() => {
  const root = document.querySelector('#payment-workspace');
  if (!root || root.dataset.ready) return;
  root.dataset.ready = 'true';
  const exact = window.BookflowExactJSON;
  const config = exact.parse(document.querySelector('#payment-config').textContent);
  const $ = id => document.getElementById('payment-' + id);
  const el = (tag, text) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; return n; };
  const money = value => value?.amount !== undefined ? value.amount + ' ' + value.currency : '';
  // Formatting only. Integer amounts and allocation decisions come from commands.
  const units = (n, currency=config.currency) => {
    return exact.minor(n,currency);
  };
  const asMoney = (n, currency=config.currency) => ({minor_units:n, currency});
  const link = (text, href) => { const n = el('a', text); n.href = href; return n; };
  const storageKey = 'payment-request:' + config.company + ':' + config.actor;
  let mode = config.mode, payment = config.initial, draft = config.draft, selected = new Map(), candidates = [];
  let customer = null, nextCursor = null, preview = null, submitted = null, busy = false, customDefinitions = [], applicationRows = [];
  let selectedApplications = new Set();
  let key = 'WB-' + crypto.randomUUID();
  let customerPending = false, reviewedPayment = null;
  function resetDraftView() {
    draft=null;selected.clear();candidates=[];nextCursor=null;
    $('invoices').replaceChildren();$('more').hidden=true;$('balances').hidden=true;
    for(const id of ['totals','selection-status','amount-origin']) $(id).replaceChildren();
    const url=new URL(location.href);url.searchParams.delete('selection');history.replaceState(null,'',url);
  }
  function note(text) { $('message').textContent = text; }
  function button(text, fn) { const n=el('button',text); n.type='button'; n.addEventListener('click',()=>perform(fn)); return n; }
  function invalidate() { preview=null; $('save').disabled=true; $('save-new').disabled=true; $('preview-result').hidden=true; }
  function lockSubmitted(locked) {
    for(const control of $('form').querySelectorAll('input,select,textarea,button')) {
      if(locked) {if(control.dataset.beforePending===undefined) control.dataset.beforePending=String(control.disabled);control.disabled=true;}
      else if(control.dataset.beforePending!==undefined) {control.disabled=control.dataset.beforePending==='true';delete control.dataset.beforePending;}
    }
  }
  async function error(err) {
    const message=err.code==='E_REASON_REQUIRED'&&['update','unapply','void'].includes(mode)?
      'Enter a reason for this payment correction, unapply or void.':err.message;
    $('error').hidden=false; $('error').querySelector('p').textContent=[err.code,message].filter(Boolean).join(' — ');
    const area=$('error').querySelector('[data-comparisons]'); area.replaceChildren();
    for (const row of err.details?.changes || err.details?.settlement_changes || []) {
      area.append(el('p', `${row.actor_id || 'Unknown actor'} at ${row.at || 'unknown time'}: ${(row.fields || []).concat(row.settlement_fields || []).join(', ') || 'Fields unknown'}. Latest writer: ${row.latest_writer_id || 'unknown'}.`));
    }
    if (err.details?.fields) for (const field of err.details.fields) area.append(el('p', `${field.field}: ${field.problem}`));
    $('retry').hidden=!submitted; $('review').hidden=!!submitted; $('review').disabled=false;
    if (!submitted && (['E_VERSION_CONFLICT','E_PREVIEW_STALE'].includes(err.code) || err.details?.review?.command==='payment settlement changes')) {
      $('review').disabled=true;
      try {
        if(payment) {
          const current=await command('payment show',{payment:payment.id});
          reviewedPayment=current;
          area.append(el('p',`Saved receipt: ${money(payment.revision.total)}; current receipt: ${money(current.revision.total)}; your entered cash: ${$('amount').value} ${config.currency}.`));
          for(const [label,saved,value] of [
            ['Date',payment.revision.date,current.revision.date],['Memo',payment.revision.memo,current.revision.memo],
            ['Reference',payment.revision.reference,current.revision.reference],
            ['Method',payment.revision.profile.payment_method.label,current.revision.profile.payment_method.label],
            ['Destination',payment.revision.profile.deposit_account.full_name,current.revision.profile.deposit_account.full_name]])
            if(saved!==value) area.append(el('p',`${label}: saved ${saved??'blank'}; current ${value??'blank'}. Your form entry remains unchanged.`));
          const savedCustom=payment.revision.custom_fields_snapshot||{}, currentCustom=current.revision.custom_fields_snapshot||{};
          for(const id of new Set([...Object.keys(savedCustom),...Object.keys(currentCustom)])) {
            const before=savedCustom[id], after=currentCustom[id];
            if(exact.stringify(before)!==exact.stringify(after)) area.append(el('p',`${after?.name||before?.name||'Custom field '+id}: saved ${before===undefined?'omitted':exact.stringify(before.value)}; current ${after===undefined?'omitted':exact.stringify(after.value)}. Your form entry remains unchanged.`));
          }
          for(const [label,field] of [['Applied','applied_minor_units'],['Available credit','available_minor_units']])
            area.append(el('p',`${label}: saved ${units(payment.current[field])}; current ${units(current.current[field])}.`));
        }
        for(const row of selected.values()) {
          const current=await command('invoice settlement',{invoice:row.invoice_id});
          const comparison=el('p');comparison.append(link('Invoice '+row.invoice_id,`/c/${config.company}/invoice/${row.invoice_id}`),
            `: saved due ${units(row.due_minor_units)}; current due ${units(current.due_minor_units)}; retained payment ${row.amount_minor_units===null?'unresolved':units(row.amount_minor_units)} (${row.amount_origin}). Saved version ${row.expected_version}; current ${current.version}.`);
          area.append(comparison);
        }
        const guard=err.details?.review?.input?.guard || payment?.settlement_guard;
        if(guard) {
          const diagnostics=await pages('payment settlement changes',{guard});
          const group=el('details');group.open=true;group.append(el('summary',`${diagnostics.total_count} complete recorded changes since the saved baseline`));
          for(const row of diagnostics.items) group.append(el('p',`${row.record_id}: change event ${row.event_id || 'unknown'} by ${row.actor_id || 'unknown actor'} at ${row.at || 'unknown time'}; fields ${(row.fields||[]).concat(row.settlement_fields||[]).join(', ') || 'unknown'}. Latest writer: ${row.latest_writer_id || 'unknown'}; version ${row.baseline_version??'unknown'} → ${row.current_version??'unknown'}; ${row.age_seconds} seconds ago${row.on_behalf_of?' on behalf of '+row.on_behalf_of:''}.`));
          if(diagnostics.unknown_history) group.append(el('p','Some recorded history is unknown; review current facts before continuing.'));
          area.append(group);
        }
        $('review').disabled=false;
      } catch(readError) {area.append(el('p',`Current comparison could not be completed: ${readError.message || readError.code}. Keep the draft and review again.`));}
    }
  }
  async function perform(fn) {
    if (busy) return; busy=true; root.setAttribute('aria-busy','true');
    try { await fn(); } catch(err) { await error(err); } finally {busy=false;root.removeAttribute('aria-busy');}
  }
  async function command(name,input={},options={}) {
    const headers={'Content-Type':'application/json','X-Bookflow-Workbench':'1','X-Bookflow-Client-Name':'bookflow-workbench',
      'X-Bookflow-Context-Encoding':'percent-utf8'};
    if (options.reason !== undefined) headers['X-Bookflow-Reason']=encodeURIComponent(options.reason);
    let response;
    try { response=await fetch('/companies/'+encodeURIComponent(config.company)+'/commands/'+name.replaceAll(' ','.')+(options.preview?'?dry_run=true':''),
      {method:'POST',credentials:'same-origin',headers,body:exact.stringify(input)}); }
    catch (_) { throw {message:'Connection interrupted. Keep this draft and recover the exact submitted request before recording again.',ambiguous:true}; }
    let out;
    try {out=exact.parse(await response.text());} catch (_) {throw {message:'The server response could not be read. Recover the exact submitted request.',ambiguous:true};}
    if (!response.ok || out.code?.startsWith('E_')) throw out;
    if (response.status!==200) throw {message:'The operation has not confirmed completion. Recover the exact submitted request.',ambiguous:true};
    return out;
  }
  async function pages(name,input) {
    let items=[],cursor=null,first=null;
    do {const out=await command(name,{...input,...(cursor?{cursor}:{}),limit:200}); first ||= out;items.push(...out.items);cursor=out.next_cursor;} while(cursor);
    return {...first,items};
  }
  function context() {
    if (mode==='apply') return {mode:'existing_credit',payment:payment.id,date:$('date').value};
    if (!customer || customerPending) throw {message:'Choose a matching customer or job before preparing this payment.'};
    return {mode:'new_receipt',customer:customer.id,date:$('date').value,...($('ar').value?{ar_account:$('ar').value}:{})};
  }
  function selectionRef() {return {mode:'selection',selection:draft.id,expected_version:draft.version};}
  function saveDraftUrl() {
    const url=new URL(location.href);url.searchParams.set('selection',draft.id);history.replaceState(null,'',url);
  }
  async function reloadDraft() {
    if (!draft) return;
    draft=await command('payment selection show',{selection:draft.id});
    const out=await pages('payment selection items',{selection:draft.id,revision:draft.version});
    selected=new Map(out.items.map(row=>[row.invoice_id,row]));
    $('amount').value=draft.amount?.amount || ''; $('amount-origin').textContent='Amount source: '+draft.amount_origin.replaceAll('_',' ');
    $('selection-status').textContent=`Shared selection version ${draft.version}; ${draft.item_count} selected invoices. ${draft.state==='consumed'?'Already recorded.':''}`;
    const totals=$('totals');totals.replaceChildren(el('p','Selected invoice payments: '+units(draft.applied_minor_units)+' '+config.currency),
      el('p','Unapplied '+(mode==='apply'?'payment credit':'cash retained by '+(customer?.full_name||customer?.name||'payer'))+': '+(draft.unapplied_minor_units===null?'unresolved':units(draft.unapplied_minor_units)+' '+config.currency)));
    for (const problem of draft.problems) totals.append(el('p',problem));
    drawCandidates();
  }
  async function changeDraft(patch) {
    if (submitted) throw {message:'Recover the submitted request before changing this draft.'};
    invalidate();draft=await command('payment selection update',{selection:draft.id,expected_version:draft.version,...patch});await reloadDraft();
  }
  async function loadInvoices(cursor=null) {
    const out=await command('payment invoices',{...context(),limit:50,...(cursor?{cursor}:{})});
    candidates=out.items;nextCursor=out.next_cursor;$('more').hidden=!nextCursor;
    $('balances').hidden=false;$('payer-balance').textContent=money(out.payer_balance);$('family-balance').textContent=money(out.family_balance);
    for (const row of candidates) {
      if (!row.customer_label) { const party=row.customer_id===customer?.id?customer:await command('customer show',{customer:row.customer_id}); row.customer_label=party.full_name||party.name; }
    }
    drawCandidates();
  }
  function drawCandidates() {
    const body=$('invoices');body.replaceChildren();
    for (const row of candidates) {
      const chosen=selected.get(row.invoice_id), tr=el('tr');tr.dataset.invoice=row.invoice_id;
      const check=el('input');check.type='checkbox';check.checked=!!chosen;check.setAttribute('aria-label','Select invoice '+row.number);
      check.addEventListener('change',()=>perform(async()=>{
        if (!draft) await makeDraft();
        await changeDraft(check.checked?{set_items:[{invoice:row.invoice_id,expected_version:row.expected_version,amount_origin:'unresolved'}]}:{remove_invoices:[row.invoice_id]});
      }));
      const description=el('span');description.append(el('span',row.customer_label+' · '),link(row.number,`/c/${config.company}/invoice/${row.invoice_id}`));
      const input=el('input');input.type='text';input.inputMode='decimal';input.dataset.mathCurrency=config.currency;input.value=chosen?.amount_minor_units!=null?units(chosen.amount_minor_units):'';
      input.setAttribute('aria-label','Payment for invoice '+row.number);
      input.addEventListener('change',()=>perform(async()=>{
        if (!draft) await makeDraft();
        await changeDraft({set_items:[{invoice:row.invoice_id,expected_version:row.expected_version,
          ...(input.value?{amount:input.value,amount_origin:'entered'}:{amount_origin:'unresolved'})}]});
      }));
      const entry=el('span');entry.append(input,el('small',chosen?' '+chosen.amount_origin:' Not selected'));
      const cells=[check,el('span',row.date),description,el('span',units(row.original_gross_minor_units)),el('span',units(row.gross_minor_units)),el('span',units(row.applied_minor_units)),el('span',units(row.due_minor_units)),entry];
      const labels=['Select','Date','Job / invoice','Original','Current','Applied','Due','Payment'];
      cells.forEach((node,index)=>{const td=el('td');td.dataset.label=labels[index];td.append(node);tr.append(td);});body.append(tr);
    }
  }
  async function makeDraft() {
    const previous=[...selected.values()];
    const created=await command('payment selection create',{...context(),...($('amount').value?{amount:$('amount').value}:{}),
      ...(draft?{amount_origin:draft.amount_origin}:{}),label:'Workbench payment draft'});
    draft=created;saveDraftUrl();
    for (let offset=0;offset<previous.length;offset+=200) {
      const items=previous.slice(offset,offset+200).map(row=>({invoice:row.invoice_id,expected_version:row.expected_version,
        amount_origin:row.amount_origin,...(row.amount_minor_units!==null?{amount:asMoney(row.amount_minor_units)}:{})}));
      draft=await command('payment selection update',{selection:draft.id,expected_version:draft.version,set_items:items});
    }
    await reloadDraft();
  }
  async function calculate() {
    if (!draft) await makeDraft();
    const calculation=await pages('payment calculate',{...context(),applications:selectionRef(),amount_mode:'selection_total'});
    const items=calculation.items.map(row=>({invoice:row.invoice_id,expected_version:row.expected_version,amount_origin:row.amount_origin,
      ...(row.amount_minor_units!==null?{amount:asMoney(row.amount_minor_units)}:{})}));
    for(let i=0;i<items.length;i+=200) await changeDraft({set_items:items.slice(i,i+200)});
    await changeDraft({amount:calculation.amount,amount_origin:calculation.amount_origin});
    note('Calculation saved in the shared selection. Entered cash and manually fixed rows remain authoritative.');
  }
  async function autoApply() {
    if (!$('amount').value) throw {message:'Enter the actual cash amount before asking for matching/oldest suggestions.'};
    if (!draft) await makeDraft();
    const out=await pages('payment suggest',{...context(),amount:$('amount').value,strategy:'exact_then_oldest'});
    draft=await command('payment selection clear',{selection:draft.id,expected_version:draft.version});
    for(let i=0;i<out.items.length;i+=200) {
      const items=out.items.slice(i,i+200).map(row=>({invoice:row.invoice_id,expected_version:row.expected_version,amount:asMoney(row.amount_minor_units),amount_origin:'calculated'}));
      draft=await command('payment selection update',{selection:draft.id,expected_version:draft.version,set_items:items});
    }
    invalidate();await reloadDraft();note('Suggestions saved to the draft; no payment has been recorded.');
  }
  function customInput() {
    const values={},kinds={};
    for(const definition of customDefinitions) {
      const input=$('custom').querySelector(`[data-definition="${definition.id}"]`);
      const action=$('custom').querySelector(`[data-custom-action="${definition.id}"]`);
      if (!input || !action || action.value==='keep') continue;
      if(action.value==='set'&&definition.kind==='bool'&&!['true','false'].includes(input.value))
        throw {message:'Choose Yes or No for '+definition.name+' before setting its value.'};
      if(action.value!=='clear') kinds[definition.id]=definition.kind;
      values[definition.id]=action.value==='clear'?null:definition.kind==='bool'?input.value==='true':input.value;
    }
    return Object.keys(values).length?{custom_fields:values,expected_custom_field_kinds:kinds}:{};
  }
  async function intent() {
    if(mode==='receive'||mode==='apply') {
      if (!draft) await makeDraft();
      if (draft.state!=='open') throw {message:'This selection is already recorded. Open its operation or start a new payment.'};
    }
    const input={operation_key:key};
    if(mode==='receive') Object.assign(input,{customer:customer.id,date:$('date').value,amount:$('amount').value,applications:selectionRef()},customInput());
    else Object.assign(input,{payment:payment.id,expected_version:payment.version});
    if(mode==='apply') Object.assign(input,{date:$('date').value,applications:selectionRef()});
    if(mode==='unapply') input.applications=applicationRows.filter(row=>selectedApplications.has(row.application_id)).map(row=>({application_id:row.application_id,invoice_expected_version:row.invoice_version}));
    if(mode==='update') Object.assign(input,{date:$('date').value,amount:$('amount').value,settlement_guard:payment.settlement_guard},customInput());
    if(mode==='receive'||mode==='update') {
      for(const [field,id] of [['number','number'],['payment_method','method'],['deposit_to','destination'],['reference','reference'],['memo','memo']]) {
        if($(id).value) input[field]=$(id).value;
        else if(mode==='update'&&['reference','memo'].includes(field)) input[field]=null;
      }
      if(mode==='receive'&&$('ar').value) input.ar_account=$('ar').value;
    }
    const reason=$('reason').value;
    return {command:'payment '+mode,input,context:reason?{reason}:{}};
  }
  async function preparePreview() {
    if(submitted) throw {message:'Resolve the exact submitted request first.'};
    $('error').hidden=true;invalidate();
    lockSubmitted(true);
    try {
    const request=await intent(), out=await command(request.command,request.input,{preview:true,...request.context});
    const complete={};
    for(const descriptor of out.prospective_pages||[]) {
      let rows=[...out.effect[descriptor.kind]],cursor=descriptor.next_cursor;
      // Start a public 200-row delivery recipe when continuation is needed.
      // The original 50-row cursor remains bound to 50; never mix those recipes.
      if(cursor) {
        rows=[];cursor=null;
        do {const page=await command('payment preview items',{request:descriptor.request,kind:descriptor.kind,facts_fingerprint:out.facts_fingerprint,limit:200,...(cursor?{cursor}:{})});
          if(page.facts_fingerprint!==out.facts_fingerprint || page.total_count!==descriptor.total_count) throw {message:'Preview facts changed. Review again.'};
          rows.push(...page.items);cursor=page.next_cursor;note(`Loading ${descriptor.kind.replaceAll('_',' ')}: ${rows.length} of ${descriptor.total_count}.`);
        } while(cursor);
      }
      if(rows.length!==descriptor.total_count) throw {message:'Preview is incomplete. Do not save.'};complete[descriptor.kind]=rows;
    }
    preview={request:{...request,input:{...request.input,expected_facts_fingerprint:out.facts_fingerprint}},out,complete};
    drawPreview();
    } finally {lockSubmitted(false);}
    $('save').disabled=false;$('save-new').disabled=mode!=='receive';note('Complete proposed effects loaded. Review, then save.');
  }
  function drawPreview() {
    const area=$('preview-result');area.hidden=false;area.replaceChildren(el('h2',preview.out.idempotent_replay?'Original recorded effect — no new payment':'Proposed payment effect'));
    area.append(el('p',`Received ${units(preview.out.current.received_minor_units)}; applied ${units(preview.out.current.applied_minor_units)}; available ${units(preview.out.current.available_minor_units)} ${config.currency}.`));
    for(const [kind,rows] of Object.entries(preview.complete)) {
      const section=el('details');section.open=rows.length<=10;section.append(el('summary',kind.replaceAll('_',' ')+': '+rows.length+' complete changes'));
      const container=el('div');container.className='payment-effects';
      for(const row of rows) {
        const card=el('div');card.className='payment-effect';
        if(row.invoice_id) card.append(link('Invoice',`/c/${config.company}/invoice/${row.invoice_id}`),el('span',' · '));
        card.append(el('span',row.party_name||row.kind||row.status||'Document'),el('span',row.amount?' '+money(row.amount):row.received_minor_units!==undefined?' Received '+units(row.received_minor_units)+'; available '+units(row.available_minor_units):row.due_minor_units!==undefined?' Due '+units(row.due_minor_units):''));
        if(row.logical_kind) card.append(el('span',` · line ${row.target_ordinal}, ${row.logical_kind}`));container.append(card);
      }
      section.append(container);area.append(section);
    }
  }
  async function save(newAfter=false,retry=false) {
    if(!retry) {
      if(!preview) throw {message:'Preview these values before saving.'};
      submitted=preview.request;sessionStorage.setItem(storageKey,exact.stringify(submitted));
    }
    if(!submitted) throw {message:'No submitted request to recover.'};
    lockSubmitted(true);
    try {
      const out=await command(submitted.command,submitted.input,submitted.context);
      submitted=null;sessionStorage.removeItem(storageKey);lockSubmitted(false);invalidate();$('error').hidden=true;
      payment=await command('payment show',{payment:out.id});
      if(newAfter) {
        key='WB-'+crypto.randomUUID();resetDraftView();mode='receive';
        $('amount').value='';$('memo').value='';$('reference').value='';$('number').value='';
        const url=new URL(location.href);url.search='';history.replaceState(null,'',url);await drawCustom();await loadInvoices();
        note('Payment '+payment.number+' saved. New blank payment started; customer/date/method/destination retained.');
      } else {mode='show';await drawRecord(payment);note(out.idempotent_replay?'Recovered original payment. No new financial effect.':'Payment saved successfully.');}
    } catch(err) {
      if(err.code && !['E_INTERNAL','E_DB_BUSY','E_UNAUTHENTICATED'].includes(err.code)) {submitted=null;sessionStorage.removeItem(storageKey);lockSubmitted(false);}
      throw err;
    }
  }
  async function drawCustom() {
    const area=$('custom');area.replaceChildren();
    for(const definition of customDefinitions) {
      const snapshot=mode==='update'?payment.revision.custom_fields_snapshot[definition.id]:null;
      let input;
      if(['bool','choice'].includes(definition.kind)) {
        input=el('select');input.append(new Option('Choose a value',''));
        if(definition.kind==='bool') {input.append(new Option('Yes','true'),new Option('No','false'));}
        else for(const choice of definition.choices||[]) if(choice.active!==false) input.append(new Option(choice.value,choice.value));
      } else {input=el('input');input.type=definition.kind==='date'?'date':'text'; if(definition.kind==='number') {input.inputMode='decimal';input.dataset.mathScale='9';}}
      input.dataset.definition=definition.id;
      const value=snapshot?.value ?? definition.default ?? '';
      if(definition.kind==='choice'&&value!==''&&![...input.options].some(option=>option.value===String(value))) input.append(new Option(String(value)+' (captured)',String(value)));
      const action=el('select');action.dataset.customAction=definition.id;action.setAttribute('aria-label',definition.name+' action');
      action.append(new Option(mode==='update'?'Keep recorded value':'Use default / omit','keep'),new Option('Set value (text may be empty)','set'),new Option('Clear value','clear'));
      input.value=value===null?'':String(value);input.addEventListener('change',()=>{action.value='set';invalidate();});
      action.addEventListener('change',invalidate);
      const label=el('label',definition.name+(definition.required?' (required)':''));label.append(action,input);area.append(label);
    }
  }
  async function chooseCustomer(id) {
    invalidate();
    if((customer&&customer.id!==id)||(draft&&draft.context.customer_id!==id)) resetDraftView();
    customer=await command('customer show',{customer:id});$('customer').value=customer.full_name||customer.name;
    customerPending=false;
    $('customer-results').replaceChildren();
    const preferred=customer.payment_method?.id;
    if(preferred&&!$('method').value) $('method').value=preferred;
  }
  async function reviewCurrent() {
    invalidate();
    let reviewed=document.getElementById('payment-reviewed-comparisons');
    if(!reviewed) {reviewed=el('section');reviewed.id='payment-reviewed-comparisons';$('error').after(reviewed);}
    reviewed.replaceChildren(el('h3','Reviewed comparison — your entries are retained'), ...Array.from($('error').querySelector('[data-comparisons]').childNodes));
    $('error').hidden=true;
    if(payment) {
      const current=await command('payment show',{payment:payment.id});
      if(reviewedPayment && current.version!==reviewedPayment.version)
        throw {code:'E_VERSION_CONFLICT',message:'The receipt changed again while you were reviewing. Review the new comparison before adopting it.'};
      payment=current;reviewedPayment=null;
    }
    if(draft) {
      // Review is explicit: retain amounts/origins, adopt only current versions.
      draft=await command('payment selection show',{selection:draft.id});
      const old=await pages('payment selection items',{selection:draft.id,revision:draft.version});
      const updated=[];
      for(const row of old.items) {
        const current=await command('invoice show',{invoice:row.invoice_id});
        updated.push({invoice:row.invoice_id,expected_version:current.version,amount_origin:row.amount_origin,
          ...(row.amount_minor_units!==null?{amount:asMoney(row.amount_minor_units)}:{})});
      }
      if(mode==='apply') await changeDraft({adopt_funding_version:payment.version});
      for(let i=0;i<updated.length;i+=200) await changeDraft({set_items:updated.slice(i,i+200)});
      await reloadDraft();await loadInvoices();
    }
    if(mode==='unapply') await loadApplications();
    note('Current versions reviewed. Your entered amounts are retained; calculate suggestions explicitly if needed, then preview again.');
  }
  async function loadApplications() {
    const result=await pages('payment settlement',{payment:payment.id,kind:'applications'});applicationRows=result.items;
    const body=$('invoices');body.replaceChildren();
    for(const row of applicationRows) {
      const tr=el('tr');tr.dataset.application=row.application_id;
      const check=el('input');check.type='checkbox';check.setAttribute('aria-label','Unapply '+money(row.amount));check.checked=selectedApplications.has(row.application_id);
      check.addEventListener('change',()=>{invalidate();check.checked?selectedApplications.add(row.application_id):selectedApplications.delete(row.application_id);});
      for(const [label,node] of [['Select',check],['Date',el('span',row.effective_date)],['Invoice',link('Open invoice',`/c/${config.company}/invoice/${row.invoice_id}`)],['Recorded application',el('span',money(row.amount))]]) {const td=el('td');td.dataset.label=label;td.append(node);tr.append(td);}body.append(tr);
    }
    $('selection-status').textContent='Select whole recorded applications to reverse at their original dates. No ledger posting is created.';
  }
  async function drawRecord(record) {
    $('form').hidden=true;$('record').hidden=false;$('title').textContent='Payment '+record.number;
    const section=$('record'),r=record.revision,c=record.current;section.replaceChildren(el('h2','Internal payment receipt · revision '+r.revision_number),
      el('p',`${r.profile.payer.label} · ${r.date} · ${money(r.total)}`),el('p',`${r.profile.payment_method.label} · ${r.reference||'No reference'} · ${r.profile.deposit_account.full_name}`),el('p',r.memo||''),
      el('p',r.id===record.current_revision_id?'Latest recorded receipt facts.':'Historical receipt facts; actions use current settlement below.'),
      el('h3','Current settlement'),el('p',`${c.status}. Applied ${units(c.applied_minor_units)}; unapplied credit ${units(c.available_minor_units)} ${c.currency}.`));
    const actions=el('div');actions.className='payment-actions';
    for(const snapshot of Object.values(r.custom_fields_snapshot||{})) section.append(el('p',`${snapshot.name || snapshot.label || 'Custom field'}: ${snapshot.value===null?'Cleared':String(snapshot.value)}`));
    for(const verb of ['apply','update','unapply','void']) if(config.allowed.includes(verb)) actions.append(button(verb==='apply'?'Apply available credit':verb==='update'?'Correct receipt':verb==='unapply'?'Unapply recorded applications':'Void unapplied receipt',async()=>startMode(verb)));
    actions.append(button('Print internal receipt',()=>window.print()),button('History',async()=>showHistory()),link('Notes and attachments',`/c/${config.company}/payment/${record.id}`),link('New payment',`/c/${config.company}/receive-payments`));section.append(actions);
    const components=c.component_count>c.components.length?(await pages('payment settlement',{payment:record.id,kind:'components'})).items:c.components;
    for(const component of components) section.append(el('p',`${component.party_name}: owned credit ${units(component.available_minor_units)} ${component.currency}`));
  }
  async function showHistory() {
    const result=await pages('payment history',{payment:payment.id}),section=$('history');section.hidden=false;section.replaceChildren(el('h2','Recorded history'));
    for(const row of result.items) {
      const card=el('div');card.className='payment-effect';card.append(el('span',row.kind.replaceAll('_',' ')+' · audit '+row.audit_sequence+' '));
      if(row.revision) card.append(button('Receipt revision '+row.revision.revision_number,async()=>drawRecord(await command('payment show',{payment:payment.id,revision:row.revision.revision_number}))));
      if(row.operation_key) card.append(button('Original operation',async()=>recoverOperation(row.operation_key)));
      if(row.application) card.append(link('Application history',`/c/${config.company}/application/${row.application.id}/history`));
      section.append(card);
    }
  }
  async function recoverOperation(operationKey) {
    const result=await command('payment operation show',{operation_key:operationKey});
    const area=$('preview-result');area.hidden=false;area.replaceChildren(el('h2','Original recorded operation — read-only recovery'),
      el('p',result.request.command+' · '+operationKey),el('p','The original request and effect are retained. Current settlement is shown separately.'));
    const original=result.original,originalHeader=original.effect?.after_header,current=result.current;
    if(originalHeader) area.append(el('p',`Original recorded document ${originalHeader.number}: ${originalHeader.date} · ${money(originalHeader.amount)} · revision ${originalHeader.revision_number}.`));
    if(original.current?.applied_minor_units!==undefined) area.append(el('p',`At that operation: applied ${units(original.current.applied_minor_units)} ${config.currency}.`));
    area.append(el('p',current.received_minor_units!==undefined?
      `Current receipt ${units(current.received_minor_units)}; applied ${units(current.applied_minor_units)}; available ${units(current.available_minor_units)} ${current.currency}.`:
      `Current invoice ${current.status}; applied ${units(current.applied_minor_units)}; due ${units(current.due_minor_units)} ${current.currency}.`));
    area.append(el('p',`Recorded by ${result.execution.actor_id} via ${result.execution.interface}${result.execution.on_behalf_of?' on behalf of '+result.execution.on_behalf_of:''}.`),
      el('p',result.execution.reason?'Recorded reason: '+result.execution.reason:result.execution.directive_id?'Recorded under directive '+(result.execution.directive_code||result.execution.directive_id)+'.':'No explicit reason was supplied.'));
    const submittedFields=el('details');submittedFields.append(el('summary','Original submitted fields and context'),el('pre',exact.stringify(result.request)));area.append(submittedFields);
    for(const [kind,label] of [['source_components','source ownership'],['effect_applications','applications'],['allocations','allocation history'],['document_changes','document changes']]) {
      area.append(button('View original '+label,async()=>{
        const out=await pages('payment operation items',{operation_key:operationKey,kind});
        const group=el('details');group.open=true;group.append(el('summary',`Original ${label}: ${out.total_count} complete recorded items`));
        for(const row of out.items) {
          const card=el('p',`${row.party_name||row.kind||row.status||'Recorded document'}${row.amount?' · '+money(row.amount):row.received_minor_units!==undefined?' · received '+units(row.received_minor_units)+' · available '+units(row.available_minor_units):row.due_minor_units!==undefined?' · due '+units(row.due_minor_units):''}`);
          if(row.invoice_id) card.append(' · ',link('Invoice',`/c/${config.company}/invoice/${row.invoice_id}`));
          if(row.application_id) card.append(' · ',link('Application history',`/c/${config.company}/application/${row.application_id}/history`));
          group.append(card);
        }area.append(group);
      }));
    }
    area.append(button('Preview exact original request',async()=>{
      const request=result.request;
      const out=await command(request.command,request.input,{preview:true,...request.context});
      area.append(el('p',out.idempotent_replay?'Original operation recovered. No new payment or settlement change.':'A new effect was proposed; do not treat this as recovery.'));
    }));
  }
  async function startMode(verb,preserved=null) {
    mode=verb;invalidate();$('record').hidden=true;$('form').hidden=false;$('history').hidden=true;draft=preserved;selected.clear();key='WB-'+crypto.randomUUID();
    $('title').textContent={receive:'Receive customer payment',apply:'Apply existing payment credit',update:'Correct receipt',unapply:'Unapply recorded applications',void:'Void unapplied receipt'}[mode];
    const edit=['receive','update'].includes(mode);
    $('header').hidden=['unapply','void'].includes(mode);$('selection').hidden=['update','void'].includes(mode);
    $('reason-label').hidden=['receive','apply'].includes(mode);$('reason').required=!$('reason-label').hidden;$('save-new').hidden=mode!=='receive';
    for(const id of ['number','method','destination','memo','reference']) $(id).disabled=!edit;
    $('customer').disabled=mode!=='receive';$('find-customer').hidden=mode!=='receive';$('ar').disabled=mode!=='receive';
    $('date').value=payment?.revision.date||new Date().toISOString().slice(0,10);
    if(payment) {
      await chooseCustomer(payment.revision.profile.payer.id);
      for(const [id,fact] of [['method',payment.revision.profile.payment_method],['destination',payment.revision.profile.deposit_account]]) {
        if (![...$(id).options].some(option=>option.value===fact.id)) $(id).append(new Option((fact.label||fact.full_name)+' (captured)',fact.id));
      }
      for(const [id,value] of [['number',payment.revision.number],['amount',mode==='apply'?units(payment.current.available_minor_units):payment.revision.total.amount],['reference',payment.revision.reference||''],['memo',payment.revision.memo||''],['method',payment.revision.profile.payment_method.id],['destination',payment.revision.profile.deposit_account.id]]) $(id).value=value;
    }
    for(const id of ['auto','calculate','clear','refresh-draft']) $(id).hidden=['unapply','void','update'].includes(mode);
    if(preserved) {await chooseCustomer(preserved.context.customer_id);$('date').value=preserved.context.date;$('ar').value=preserved.context.ar_account_id;await reloadDraft();await loadInvoices();}
    else if(mode==='unapply') await loadApplications();
    else if(mode==='apply') {await makeDraft();await loadInvoices();}
    await drawCustom();
    if(mode==='void') note('Unapply every recorded application first. Voiding reverses receipt cash and AR at its original dates.');
  }
  async function initialize() {
    $('date').value=new Date().toISOString().slice(0,10);
    $('amount').dataset.mathCurrency=config.currency;
    const methods=await command('payment-method list',{}),accounts=await command('account list',{}),defs=await command('custom-field list',{filter:['target_type=payment']});
    for(const row of methods.items) $('method').append(new Option(row.name,row.id));
    for(const row of accounts.items) {
      if(row.type==='bank'||row.system_role==='undeposited_funds') $('destination').append(new Option(row.full_name||row.name,row.id));
      if(row.type==='accounts_receivable') $('ar').append(new Option(row.full_name||row.name,row.id));
    }
    customDefinitions=await Promise.all(defs.items.filter(row=>row.active).map(row=>command('custom-field show',{custom_field:row.id})));
    if(config.preferences.use_undeposited_funds_for_payments) $('destination').value=accounts.items.find(row=>row.system_role==='undeposited_funds')?.id||'';
    if(draft) await startMode(mode,draft);
    else if(payment&&mode==='show') await drawRecord(payment);
    else if(payment) await startMode(mode);
    else {
      $('reason-label').hidden=true;
      if(config.customer) await chooseCustomer(config.customer);
      let source=null;
      if(config.invoice) { source=await command('invoice show',{invoice:config.invoice}); await chooseCustomer(source.revision.profile.customer.id); }
      if(draft) {await chooseCustomer(draft.context.customer_id);$('date').value=draft.context.date;$('ar').value=draft.context.ar_account_id;await reloadDraft();await loadInvoices();}
      await drawCustom();
      if(source && !draft) { await makeDraft(); await loadInvoices(); await changeDraft({set_items:[{invoice:source.id,expected_version:source.version,amount_origin:'unresolved'}]}); }
    }
    if(!config.allowed.includes(mode) && mode!=='show') { $('form').hidden=true;note('Read-only access: payment changes require ledger posting permission.'); }
    const saved=sessionStorage.getItem(storageKey);
    if(saved) {submitted=exact.parse(saved);key=submitted.input.operation_key;lockSubmitted(true);error({message:'An earlier submitted payment has no confirmed result in this tab. Recover that exact request before recording another payment.'});}
    if(config.operation) await recoverOperation(config.operation);
    root.dataset.loaded='true';
  }
  $('form').addEventListener('submit',e=>e.preventDefault());
  $('find-customer').addEventListener('click',()=>perform(async()=>{
    const out=await command('customer query',{query:$('customer').value,limit:25});const results=$('customer-results');results.replaceChildren();
    for(const row of out.items) results.append(button(row.full_name||row.name,async()=>chooseCustomer(row.id)));
    if(!out.items.length) results.append(el('p','No matching customers.'));
  }));
  $('load').addEventListener('click',()=>perform(async()=>{invalidate();await makeDraft();await loadInvoices();if(!selected.size && draft.amount?.minor_units>0 && config.preferences.automatically_apply_payments) await autoApply();}));
  $('amount').addEventListener('change',()=>perform(async()=>{invalidate();if(draft) {await changeDraft({amount:$('amount').value||null});
    if(!selected.size && draft.amount?.minor_units>0 && config.preferences.automatically_apply_payments) await autoApply();}}));
  for(const event of ['input','change']) $('customer').addEventListener(event,()=>{
    customerPending=true;invalidate();$('balances').hidden=true;$('invoices').replaceChildren();
    note('Choose a matching customer. The previous preview cannot be saved; any prior shared selection remains in Saved selections.');
  });
  for(const id of ['number','method','destination','memo','reference','reason','date','ar']) $(id).addEventListener('change',invalidate);
  $('more').addEventListener('click',()=>perform(async()=>loadInvoices(nextCursor)));
  $('auto').addEventListener('click',()=>perform(autoApply));$('calculate').addEventListener('click',()=>perform(calculate));
  $('clear').addEventListener('click',()=>perform(async()=>{if(draft) {draft=await command('payment selection clear',{selection:draft.id,expected_version:draft.version});invalidate();await reloadDraft();note('Draft selections cleared. No recorded application changed.');}}));
  $('refresh-draft').addEventListener('click',()=>perform(async()=>{invalidate();await reloadDraft();await loadInvoices();}));
  $('preview').addEventListener('click',()=>perform(preparePreview));$('save').addEventListener('click',()=>perform(()=>save(false)));$('save-new').addEventListener('click',()=>perform(()=>save(true)));
  $('review').addEventListener('click',()=>perform(reviewCurrent));$('retry').addEventListener('click',()=>perform(()=>save(false,true)));
  perform(initialize);
})();
