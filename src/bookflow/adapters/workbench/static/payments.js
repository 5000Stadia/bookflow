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
  let destinationOverride = !config.preferences.use_undeposited_funds_for_payments, defaultDestination = null;
  let key = 'WB-' + crypto.randomUUID();
  let customerPending = false, reviewedPayment = null, draftAttempt = null, reviewedDraft = null, activeAction = null, reviewEpoch = 0;
  const busyStatus=el('p');busyStatus.id='payment-busy-status';busyStatus.setAttribute('role','status');busyStatus.hidden=true;root.prepend(busyStatus);
  function resetDraftView() {
    draft=null;draftAttempt=null;reviewedDraft=null;selected.clear();candidates=[];nextCursor=null;
    recoveryView=null;recoveryConfirmed=null;document.getElementById('payment-recovery-panel')?.remove();
    $('invoices').replaceChildren();$('more').hidden=true;$('balances').hidden=true;
    for(const id of ['totals','selection-status','amount-origin']) $(id).replaceChildren();
    const url=new URL(location.href);url.searchParams.delete('selection');url.searchParams.delete('recovery');history.replaceState(null,'',url);
  }
  function note(text) { $('message').textContent = text; }
  function button(text, fn) { const n=el('button',text); n.type='button'; n.addEventListener('click',()=>perform(fn)); return n; }
  function invalidate() { reviewEpoch++;recoveryConfirmed=null;document.querySelectorAll('[data-recovery-confirm]').forEach(button=>{button.disabled=true;});preview=null; $('save').disabled=true; $('save-new').disabled=true; $('preview-result').hidden=true; }
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
    if (!submitted && (['E_VERSION_CONFLICT','E_PREVIEW_STALE','E_QUERY_STALE','E_RECOVERY_PENDING'].includes(err.code) || err.details?.review?.command==='payment settlement changes')) {
      $('review').disabled=true;
      try {
        if(draft) {
          const current=await command('payment selection show',{selection:draft.id});
          const currentRows=(await pages('payment selection items',{selection:current.id,revision:current.version})).items;
          reviewedDraft={header:current,rows:currentRows};
          area.append(el('p','Recovery keeps this shared selection and reviews your entire attempted edit before recording can resume.'));
          const baseline=draftAttempt?.baseline||draft, baselineRows=draftAttempt?.rows||[...selected.values()];
          const attempted=new Map(baselineRows.map(row=>[row.invoice_id,{...row}]));
          const touched=new Set();
          for(const patch of draftAttempt?.patches||[]) {
            for(const id of patch.remove_invoices||[]) {touched.add(id);attempted.delete(id);}
            for(const row of patch.set_items||[]) {touched.add(row.invoice);attempted.set(row.invoice,{...attempted.get(row.invoice),...row,invoice_id:row.invoice,
              attemptedAmount:Object.hasOwn(row,'amount')?(typeof row.amount==='string'?row.amount:row.amount===null?'unresolved':units(row.amount.minor_units)):'unresolved'});}
          }
          const amountPatch=[...(draftAttempt?.patches||[])].reverse().find(p=>Object.hasOwn(p,'amount'));
          area.append(el('p',`Shared draft cash: saved ${money(baseline.amount)||'unresolved'}; current ${money(current.amount)||'unresolved'}; your attempted cash: ${amountPatch?draftAttempt.cashText+' '+config.currency:'unchanged by this edit'}. Saved version ${baseline.version}; current ${current.version}.`));
          const oldRows=new Map(baselineRows.map(row=>[row.invoice_id,row])), nowRows=new Map(currentRows.map(row=>[row.invoice_id,row]));
          const describe=row=>!row?'not selected':`${row.attemptedAmount??(row.amount_minor_units===null?'unresolved':units(row.amount_minor_units))} (${row.amount_origin})`;
          for(const id of new Set([...oldRows.keys(),...nowRows.keys(),...attempted.keys()])) {
            const comparison=el('p');comparison.append(link('Invoice '+id,`/c/${config.company}/invoice/${id}`),
              `: saved selection ${describe(oldRows.get(id))}; current selection ${describe(nowRows.get(id))}; your attempted selection ${touched.has(id)?describe(attempted.get(id)):'unchanged by this edit'}.`);area.append(comparison);
          }
        }
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
  // Pause native editing while reads/actions can replace form controls. Inert
  // does not overwrite business disabled state or the submitted-request lock.
  function pauseEditors(paused) {
    for(const control of root.querySelectorAll('input,select,textarea')) control.inert=paused;
  }
  new MutationObserver(()=>{if(busy) pauseEditors(true);}).observe(root,{childList:true,subtree:true});
  function drawDestination() {
    const useDefault=mode==='receive'&&!destinationOverride;
    $('destination-label').hidden=useDefault;
    $('destination').required=['receive','update'].includes(mode)&&!useDefault;
    $('destination-default').hidden=!useDefault;
    $('destination-reset').hidden=mode!=='receive'||!destinationOverride||!config.preferences.use_undeposited_funds_for_payments;
  }
  async function perform(fn) {
    if (busy) {busyStatus.textContent='Finishing the current action. Your next action is queued.';await activeAction;return perform(fn);}
    let done;activeAction=new Promise(resolve=>{done=resolve;});busy=true; pauseEditors(true);root.setAttribute('aria-busy','true');
    busyStatus.hidden=false;busyStatus.textContent='Working on this payment. Editing is paused until this action finishes.';
    try { await fn(); } catch(err) { await error(err); } finally {busy=false;pauseEditors(false);root.removeAttribute('aria-busy');busyStatus.hidden=true;done();}
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
    if (!customer || customerPending || $('customer').value!==(customer.full_name||customer.name)) throw {message:'Choose a matching customer or job before preparing this payment.'};
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
      el('p',(mode==='apply'?'Unallocated draft amount':'Unapplied cash retained by '+(customer?.full_name||customer?.name||'payer'))+': '+(draft.unapplied_minor_units===null?'unresolved':units(draft.unapplied_minor_units)+' '+config.currency)));
    if(mode==='apply') totals.append(el('p','Current available payment credit: '+units(payment.current.available_minor_units)+' '+config.currency));
    for (const problem of draft.problems) totals.append(el('p',problem));
    if(draft.current_lifecycle?.state?.startsWith('recovery_')) $('selection-status').append(' Recovery pending: '+draft.current_lifecycle.received_entry_count+' of '+draft.current_lifecycle.declared_entry_count+' attempted edits shared.');
    if(draft.current_lifecycle?.consumed_operation) totals.append(button('Original recorded operation',()=>recoverOperation(draft.current_lifecycle.consumed_operation.operation_key)));
    drawCandidates();
  }
  async function changeDraft(patch) {
    if (submitted) throw {message:'Recover the submitted request before changing this draft.'};
    context();invalidate();
    try {
      if(draftAttempt) throw {code:'E_VERSION_CONFLICT',message:'Review the retained edits against the current shared draft before saving them.'};
      draft=await command('payment selection update',{selection:draft.id,expected_version:draft.version,...patch});await reloadDraft();
    }
    catch(err) {
      if(['E_VERSION_CONFLICT','E_PREVIEW_STALE','E_QUERY_STALE','E_RECOVERY_PENDING'].includes(err.code)) {
        draftAttempt ||= {baseline:structuredClone(draft),rows:structuredClone([...selected.values()]),patches:[]};
        draftAttempt.patches.push(structuredClone(patch));
        if(Object.hasOwn(patch,'amount')) draftAttempt.cashText=$('amount').value;
      }
      throw err;
    }
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
      // A statement charge is settled exactly like an invoice and arrives in the same rows, so
      // it is named and linked as what it is rather than described to the reader as an invoice.
      const kind=row.document_type==='statement_charge'?'statement charge':'invoice';
      const noun=row.document_type==='statement_charge'?'statement-charge':'invoice';
      const check=el('input');check.type='checkbox';check.checked=!!chosen;check.setAttribute('aria-label','Select '+kind+' '+row.number);
      check.addEventListener('change',()=>{const checked=check.checked;return perform(async()=>{
        if (!draft) await makeDraft();
        await changeDraft(checked?{set_items:[{invoice:row.invoice_id,expected_version:row.expected_version,amount_origin:'unresolved'}]}:{remove_invoices:[row.invoice_id]});
      });});
      const description=el('span');description.append(el('span',row.customer_label+' · '),link(row.number,`/c/${config.company}/${noun}/${row.invoice_id}`));
      const input=el('input');input.type='text';input.inputMode='decimal';input.dataset.mathCurrency=config.currency;input.value=chosen?.amount_minor_units!=null?units(chosen.amount_minor_units):'';
      input.setAttribute('aria-label','Payment for '+kind+' '+row.number);
      input.addEventListener('change',()=>{const value=input.value;return perform(async()=>{
        if (!draft) await makeDraft();
        await changeDraft({set_items:[{invoice:row.invoice_id,expected_version:row.expected_version,
          ...(value?{amount:value,amount_origin:'entered'}:{amount_origin:'unresolved'})}]});
      });});
      const entry=el('span');entry.append(input,el('small',chosen?' '+chosen.amount_origin:' Not selected'));
      const cells=[check,el('span',row.date),description,el('span',units(row.original_gross_minor_units)),el('span',units(row.gross_minor_units)),el('span',units(row.applied_minor_units)),el('span',units(row.due_minor_units)),entry];
      const labels=['Select','Date','Job / document','Original','Current','Applied','Due','Payment'];
      cells.forEach((node,index)=>{const td=el('td');td.dataset.label=labels[index];td.append(node);tr.append(td);});body.append(tr);
    }
  }
  async function makeDraft() {
    if(draftAttempt) throw {code:'E_VERSION_CONFLICT',message:'Review the retained edits before replacing this shared draft.'};
    if(draft) {
      if(draft.state==='consumed') throw {code:'E_SELECTION_CONSUMED',message:'This selection is already recorded. Open its original operation or explicitly start a blank new payment.'};
      await reloadDraft();return;
    }
    draft=await command('payment selection create',{...context(),...($('amount').value?{amount:$('amount').value}:{}),label:'Workbench payment draft'});
    saveDraftUrl();await reloadDraft();
  }
  async function completeDraftChanges(patches) {
    const baseline=structuredClone(draft),rows=structuredClone([...selected.values()]);
    try {for(const patch of patches) await changeDraft(patch);}
    catch(err) {
      if(['E_VERSION_CONFLICT','E_PREVIEW_STALE','E_QUERY_STALE','E_RECOVERY_PENDING'].includes(err.code))
        draftAttempt={baseline,rows,patches:structuredClone(patches),cashText:$('amount').value};
      throw err;
    }
  }
  async function calculate() {
    if (!draft) await makeDraft();
    const calculation=await pages('payment calculate',{...context(),applications:selectionRef(),amount_mode:'selection_total'});
    const items=calculation.items.map(row=>({invoice:row.invoice_id,expected_version:row.expected_version,amount_origin:row.amount_origin,
      ...(row.amount_minor_units!==null?{amount:asMoney(row.amount_minor_units)}:{})}));
    const patches=[];for(let i=0;i<items.length;i+=200) patches.push({set_items:items.slice(i,i+200)});
    patches.push({amount:calculation.amount,amount_origin:calculation.amount_origin});
    await completeDraftChanges(patches);
    note('Calculation saved in the shared selection. Entered cash and manually fixed rows remain authoritative.');
  }
  async function autoApply() {
    if(draftAttempt) throw {code:'E_VERSION_CONFLICT',message:'Review the retained edits before replacing them with suggestions.'};
    if (!$('amount').value) throw {message:'Enter the actual cash amount before asking for matching/oldest suggestions.'};
    if (!draft) await makeDraft();
    const out=await pages('payment suggest',{...context(),amount:$('amount').value,strategy:'exact_then_oldest'});
    const patches=[];
    const removals=[...selected.keys()];
    for(let i=0;i<removals.length;i+=200) patches.push({remove_invoices:removals.slice(i,i+200)});
    for(let i=0;i<out.items.length;i+=200) {
      const items=out.items.slice(i,i+200).map(row=>({invoice:row.invoice_id,expected_version:row.expected_version,amount:asMoney(row.amount_minor_units),amount_origin:'calculated'}));
      patches.push({set_items:items});
    }
    await completeDraftChanges(patches);
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
      context();
      if(draftAttempt) throw {message:'Review the rejected shared-draft edits before previewing this payment.'};
      if (!draft) await makeDraft();
      if (draft.current_lifecycle?.state?.startsWith('recovery_')) throw {code:'E_RECOVERY_PENDING',message:'Finish or explicitly discard the shared recovery before recording.'};
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
        if(field==='deposit_to'&&mode==='receive'&&!destinationOverride) continue;
        if(field==='deposit_to'&&mode==='receive'&&!$(id).value) throw {message:'Choose a bank or Undeposited Funds destination before previewing.'};
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
    if(['receive','update'].includes(mode)) area.append(el('p',`Deposit to: ${Object.hasOwn(preview.request.input,'deposit_to')?$('destination').selectedOptions[0]?.textContent:(defaultDestination?.full_name||defaultDestination?.name||'Unresolved')} · ${Object.hasOwn(preview.request.input,'deposit_to')?'explicit choice':'company default (Undeposited Funds)'}.`));
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
    if(draftAttempt&&customer?.id!==id) throw {code:'E_VERSION_CONFLICT',message:'Review your retained shared-draft edits before changing its customer. Choose the original customer to continue that review.'};
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
      await shareRecovery();
      return;
    }
    if(mode==='unapply') await loadApplications();
    note('Current versions reviewed. Your entered amounts are retained; calculate suggestions explicitly if needed, then preview again.');
  }
  // The complete intent is committed locally before the first begin/replace request.
  // Outbox state assists transport; only public server receipts establish sharing.
  const recoveryScope='payment-recovery:'+config.company+':'+config.actor+':';
  let recoveryView=null, recoveryConfirmed=null;
  async function outbox(action,key,value) {
    const db=await new Promise((resolve,reject)=>{
      const request=indexedDB.open('bookflow-payment-recovery-v1',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('attempts');
      request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
    });
    try {return await new Promise((resolve,reject)=>{
      const tx=db.transaction('attempts',action==='get'||action==='all'?'readonly':'readwrite');
      const store=tx.objectStore('attempts');
      const request=action==='get'?store.get(key):action==='all'?store.getAll():store.put(exact.stringify(value),key);
      let result;request.onsuccess=()=>{result=request.result;};
      tx.oncomplete=()=>resolve(action==='get'?(result?exact.parse(result):null):action==='all'?result.map(exact.parse):result);
      tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
    });} finally {db.close();}
  }
  function canonical(value) {
    if(value===null||typeof value!=='object') return exact.stringify(value);
    if(Array.isArray(value)) return '['+value.map(canonical).join(',')+']';
    return '{'+Object.keys(value).filter(key=>value[key]!==undefined).sort((a,b)=>{const x=Array.from(a,c=>c.codePointAt(0)),y=Array.from(b,c=>c.codePointAt(0));for(let i=0;i<Math.min(x.length,y.length);i++) if(x[i]!==y[i]) return x[i]-y[i];return x.length-y.length;}).map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
  }
  async function digest(value) {
    const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value)));
    return [...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,'0')).join('');
  }
  function exactUnits(value) {
    if(value&&typeof value==='object') return value.minor_units;
    const scale=JSON.parse(document.querySelector('#math-currencies').textContent)[config.currency]??2;
    const match=/^(\d+)(?:\.(\d*))?$/.exec(String(value).trim());
    if(!match||(match[2]||'').length>scale) throw {message:'Enter exact money before sharing recovery.'};
    return BigInt(match[1])*10n**BigInt(scale)+BigInt((match[2]||'').padEnd(scale,'0')||'0');
  }
  async function persistAttempt(attempt) {
    try {await outbox('put',attempt.storageKey,attempt);}
    catch (_) {throw {message:attempt.recovery_id?'This browser could not save its latest acknowledgement. Recovery is shared and remains blocked; resolve the original recovery before continuing.':'The complete attempted edits could not be saved in this browser. This new attempt has not been shared; keep this tab open and retry.'};}
  }
  function recoveryPanel() {
    let panel=document.getElementById('payment-recovery-panel');
    if(!panel) {panel=el('section');panel.id='payment-recovery-panel';$('form').before(panel);}
    panel.style.overflowWrap='anywhere';panel.replaceChildren(el('h2','Recover this shared payment selection'));
    return panel;
  }
  async function shareRecovery() {
    const current=await command('payment selection show',{selection:draft.id});
    if(current.state!=='open') throw {code:'E_SELECTION_CONSUMED',message:'This selection is already recorded. Recover its original operation.'};
    if(reviewedDraft&&current.version!==reviewedDraft.header.version) throw {code:'E_VERSION_CONFLICT',message:'The shared draft changed again while you reviewed. Read its new values before sharing recovery.'};
    const local=draftAttempt?.baseline||draft;
    const changes=new Map();let header={action:'keep'};
    for(const patch of draftAttempt?.patches||[]) {
      for(const id of patch.remove_invoices||[]) {
        const old=(draftAttempt?.rows||[...selected.values()]).find(row=>row.invoice_id===id);
        changes.set(id,{invoice_id:id,observed_invoice_version:old?.expected_version||1,action:'remove'});
      }
      for(const row of patch.set_items||[]) {
        const common={invoice_id:row.invoice,observed_invoice_version:row.expected_version};
        const origin=row.amount_origin||(row.amount!==undefined&&row.amount!==null?'entered':'unresolved');
        if(origin==='calculated') changes.set(row.invoice,{...common,action:'calculate',...(row.amount!=null?{attempted_calculated_minor_units:exactUnits(row.amount)}:{})});
        else changes.set(row.invoice,{...common,action:'set',amount_origin:origin,currency:config.currency,amount_minor_units:origin==='unresolved'?null:exactUnits(row.amount)});
      }
      if(Object.hasOwn(patch,'amount')||Object.hasOwn(patch,'amount_origin')) {
        const origin=patch.amount_origin||(patch.amount!=null?'entered':current.context.automatically_calculate?'selection_total':'unresolved');
        header=origin==='entered'?{action:'set',amount_origin:origin,amount_minor_units:exactUnits(patch.amount??current.amount),currency:config.currency}:{action:'set',amount_origin:origin};
      }
    }
    const generation=crypto.randomUUID(),entries=[...changes.values()].sort((a,b)=>a.invoice_id<b.invoice_id?-1:a.invoice_id>b.invoice_id?1:0);
    const manifest={domain:'bookflow.payment.recovery.intent',format:1,selection:current.id,local_baseline_revision:local.revision_id,
      anchor_revision:current.revision_id,attempt_generation:generation,header_intent:header,entries};
    const begin={recovery_key:'WB-REC-'+generation,selection:current.id,expected_version:current.version,local_baseline_revision:local.revision_id,
      attempt_generation:generation,declared_entry_count:entries.length,intent_hash:await digest(manifest),header_intent:header};
    const attempt={scope:recoveryScope+current.id,storageKey:recoveryScope+current.id+':'+generation,manifest,begin,entries,actions:{},done:false};
    const active=current.current_lifecycle;
    if(active?.recovery_id) {
      if(!window.confirm(`Replace the ENTIRE prior attempt (${active.received_entry_count} of ${active.declared_entry_count} edits shared) with these ${entries.length} complete edits? All prior edits absent from this replacement are discarded, including any missing edits.`)) return;
      attempt.replacement={recovery_id:active.recovery_id,expected_recovery_version:active.recovery_version,replacement:begin};
    }
    await persistAttempt(attempt);
    note('Complete attempted edits saved in this browser; not yet shared.');
    await resumeRecovery(attempt);
  }
  async function resumeRecovery(attempt) {
    invalidate();recoveryConfirmed=null;
    if(!attempt.recovery_id) {
      let shared;
      try {shared=await command('payment recovery show',{recovery_key:attempt.begin.recovery_key});}
      catch(err) {if(err.code!=='E_RECORD_NOT_FOUND') throw err;}
      if(!shared) {
        const result=await command('payment recovery '+(attempt.replacement?'replace':'begin'),attempt.replacement||attempt.begin);
        attempt.recovery_id=result.original_receipt.replacement_recovery_id||result.original_receipt.recovery_id;
      } else attempt.recovery_id=shared.id;
      await persistAttempt(attempt);
    }
    let state=await command('payment recovery show',{recovery_id:attempt.recovery_id});
    if(state.state==='uploading') {
      for(let offset=0;offset<attempt.entries.length;offset+=200) {
        await command('payment recovery upload',{recovery_id:state.id,chunk_index:offset/200,entries:attempt.entries.slice(offset,offset+200)});
      }
      state=await command('payment recovery show',{recovery_id:state.id});
      attempt.actions.seal ||= {recovery_id:state.id,expected_recovery_version:state.version};
      await persistAttempt(attempt);
      await command('payment recovery seal',attempt.actions.seal);
    }
    await showRecovery(attempt.recovery_id,attempt);
  }
  async function discoverRecovery() {
    if(!draft) return;
    const attempts=(await outbox('all')).filter(row=>row.scope===recoveryScope+draft.id&&!row.done);
    const active=draft.current_lifecycle;
    if(active?.recovery_id) return showRecovery(active.recovery_id,attempts.find(row=>row.recovery_id===active.recovery_id||row.begin.attempt_generation===active.attempt_generation));
    if(attempts.length) {
      const panel=recoveryPanel();panel.append(el('p','This browser holds attempted edits with an unresolved sharing acknowledgement.'));
      for(const attempt of attempts) panel.append(button('Resolve sharing and resume',()=>resumeRecovery(attempt)));
    }
  }
  async function receivedRecoveryEvidence(panel,state,kind,title,render) {
    const section=el('section');section.dataset.recoveryEvidence=kind;
    const heading=el('h4',title),rows=el('div'),progress=el('p');let cursor=null,received=0;
    const more=button('Next '+title.toLowerCase(),load);more.dataset.recoveryMore=kind;
    section.append(heading,progress,rows,more);panel.append(section);
    async function load() {
      const page=await command('payment recovery items',{recovery_id:state.id,kind,limit:50,...(cursor?{cursor}:{})});
      for(const row of page.items) rows.append(render(row));
      received+=page.items.length;cursor=page.next_cursor;more.hidden=!cursor;
      progress.textContent=`Showing ${received} of ${page.total_count}. These are acknowledged server records.`;
    }
    await load();
  }
  async function showRecovery(identifier,attempt=null) {
    recoveryConfirmed=null;invalidate();
    const epoch=reviewEpoch;
    const state=await command('payment recovery show',{recovery_id:identifier});recoveryView=state;
    const panel=recoveryPanel();
    const url=new URL(location.href);url.searchParams.set('selection',state.selection_id);url.searchParams.set('recovery',identifier);history.replaceState(null,'',url);
    panel.append(el('p',`${state.state}: ${state.received_entry_count} of ${state.declared_entry_count} attempted edits shared. Saved selection version ${state.anchor_selection_version}.`));
    if(['applied','aborted','superseded'].includes(state.state)) {
      panel.append(el('p',state.state==='applied'?'The complete recovery was applied to this selection. Preview the payment before recording.':state.state==='aborted'?'The entire attempt was discarded. Its evidence remains available.':'This attempt was replaced; inspect the current recovery.'));
      if(attempt) {attempt.done=true;await persistAttempt(attempt);}
      draftAttempt=null;reviewedDraft=null;await reloadDraft();
      if(draft.current_lifecycle?.recovery_id) panel.append(button('Open current recovery',()=>showRecovery(draft.current_lifecycle.recovery_id)));
      return;
    }
    if(state.state==='uploading') {
      panel.append(el('p',`${state.missing_chunk_count} parts are still missing. Recording remains blocked, including after closing this tab.`));
      await receivedRecoveryEvidence(panel,state,'missing_ranges','Missing ranges',row=>{
        const first=BigInt(row.first_chunk_index),last=BigInt(row.last_chunk_index),end=(last+1n)*200n,declared=BigInt(state.declared_entry_count);
        return el('p',`Parts ${first+1n}–${last+1n}: missing attempted edits ${first*200n+1n}–${end<declared?end:declared}. Their values have not been received by the server.`);
      });
      await receivedRecoveryEvidence(panel,state,'entries','Received edits',row=>{
        const item=el('details');item.dataset.recoveryEntry=row.invoice_id;
        item.append(el('summary',`Edit ${row.entry_index}: invoice ${row.invoice_id}`));
        const action=row.action==='remove'?'Remove from selection':row.action==='calculate'?'Request a new calculation from current facts':`${row.amount_origin==='unresolved'?'Unresolved amount':units(row.amount_minor_units,row.currency)+' '+row.currency} (${row.amount_origin})`;
        item.append(el('p',action),el('p',`Submitted observed invoice version: ${row.observed_invoice_version}. This is the attempt's version claim.`));
        if(row.retained_calculation_revision_id) item.append(el('p','Restore saved calculation from revision '+row.retained_calculation_revision_id));
        if(row.attempted_calculated_minor_units!==null) item.append(el('p','Previous local calculation display (not authoritative): '+units(row.attempted_calculated_minor_units)));
        return item;
      });
      await receivedRecoveryEvidence(panel,state,'chunks','Chunk acknowledgements',row=>{
        const receipt=exact.parse(row.receipt_snapshot),item=el('details');
        item.append(el('summary','Part '+(BigInt(row.chunk_index)+1n)+' acknowledged'));
        item.append(el('p',`${receipt.actor_id||'Unknown actor'} at ${receipt.recorded_at||'unknown time'}; ${receipt.received_entry_count} of ${receipt.declared_entry_count} edits received at this original acknowledgement.`));
        return item;
      });
      if(attempt) panel.append(button('Resume complete saved attempt',()=>resumeRecovery(attempt)));
      else panel.append(el('p','The remaining edits are not stored in this browser. Resume from the original browser, or explicitly discard the entire attempt.'));
      panel.append(button('Reload shared recovery',()=>showRecovery(identifier,attempt)));
    } else {
      const request={recovery_id:identifier,attempt_generation:state.attempt_generation,intent_hash:state.intent_hash};
      const comparison=await command('payment recovery compare',request);
      const changes=await pages('payment recovery compare-items',{...request,facts_fingerprint:comparison.facts_fingerprint,kind:'changes'});
      const problems=await pages('payment recovery compare-items',{...request,facts_fingerprint:comparison.facts_fingerprint,kind:'problems'});
      const latest=await command('payment recovery show',{recovery_id:identifier});
      if(epoch!==reviewEpoch||latest.version!==state.version||latest.state!=='sealed'||changes.total_count!==comparison.change_count||problems.total_count!==comparison.problem_count)
        throw {code:'E_QUERY_STALE',message:'Recovery changed during review. Reload the complete comparison.'};
      const showAmount=value=>value===null?'unresolved':units(value);
      panel.append(el('p',`Proposed cash: ${showAmount(comparison.amount_minor_units)} ${comparison.currency}; selected: ${showAmount(comparison.selected_minor_units)}; unapplied: ${showAmount(comparison.unapplied_minor_units)}.`));
      const hc=comparison.header_comparison;
      panel.append(el('p',`Cash: your saved value ${showAmount(hc.local_baseline.amount_minor_units)} (${hc.local_baseline.amount_origin}); current saved value ${showAmount(hc.anchor.amount_minor_units)} (${hc.anchor.amount_origin}); requested ${hc.attempted.action==='keep'?'keep the saved mode':hc.attempted.amount_origin}; proposed ${showAmount(hc.proposed.amount_minor_units)}${hc.derived?' calculated from the final selected rows':''}.`));
      for(const row of changes.items) {
        const details=el('details');details.append(el('summary','Invoice '+row.invoice_id));
        const body=el('div');
        const describe=value=>!value?'not selected':`${showAmount(value.amount_minor_units)} (${value.amount_origin})`;
        if(row.history_event_id) body.append(el('p',`${row.actor_id||'Unknown actor'} at ${row.at}: ${row.changed_fields?.join(', ')||'Fields unknown'}; version ${row.version_before} → ${row.version_after}.`));
        else {
          body.append(el('p','Your saved selection: '+describe(row.local_baseline)),el('p','Current saved selection: '+describe(row.anchor)),
            el('p','Attempted edit: '+(!row.attempted?'unchanged':row.attempted.action==='remove'?'remove':row.attempted.action==='calculate'?'calculate from current due'+(row.attempted.attempted_calculated_minor_units!=null?' (previous display '+showAmount(row.attempted.attempted_calculated_minor_units)+')':''):(row.attempted.retained_calculation_revision_id?'restore historical calculation from saved revision '+row.attempted.retained_calculation_revision_id+': ':'')+describe(row.attempted))),
            el('p','Proposed selection: '+describe(row.proposed)),el('p',row.current?`Current invoice version ${row.current.version}; due ${showAmount(row.current.due)}; ${row.current.status}.`:'Current invoice facts unavailable.'));
        }
        details.append(body);panel.append(details);
      }
      for(const row of problems.items) panel.append(el('p',row.code));
      recoveryConfirmed={recovery_id:identifier,expected_recovery_version:state.version,attempt_generation:state.attempt_generation,
        intent_hash:state.intent_hash,expected_selection_version:state.anchor_selection_version,expected_facts_fingerprint:comparison.facts_fingerprint};
      if(!comparison.hard_blocker_count) {const confirmButton=button('Confirm complete recovery',async()=>{
        const confirmed=recoveryConfirmed;if(!confirmed) throw {message:'Read the complete comparison again.'};
        if(attempt) {attempt.actions.apply=confirmed;await persistAttempt(attempt);}
        await command('payment recovery apply',confirmed);await showRecovery(identifier,attempt);await loadInvoices();
      });confirmButton.dataset.recoveryConfirm='true';panel.append(confirmButton);}
      panel.append(button('Reload complete comparison',()=>showRecovery(identifier,attempt)));
    }
    if(draftAttempt) panel.append(button('Replace with my complete attempted edits',()=>shareRecovery()));
    panel.append(button('Discard entire attempt',async()=>{
      if(!window.confirm(`Discard ALL ${state.declared_entry_count} attempted edits, including missing edits, and restore saved selection version ${state.anchor_selection_version}?`)) return;
      const input={recovery_id:identifier,expected_recovery_version:state.version,disposition:'discard_entire_attempt'};
      if(attempt) {attempt.actions.abort=input;await persistAttempt(attempt);}
      await command('payment recovery abort',input);await showRecovery(identifier,attempt);await loadInvoices();
    }));
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
    $('balances').hidden=true;$('payer-balance').replaceChildren();$('family-balance').replaceChildren();
    mode=verb;invalidate();$('record').hidden=true;$('form').hidden=false;$('history').hidden=true;draft=preserved;selected.clear();key='WB-'+crypto.randomUUID();
    $('title').textContent={receive:'Receive customer payment',apply:'Apply existing payment credit',update:'Correct receipt',unapply:'Unapply recorded applications',void:'Void unapplied receipt'}[mode];
    $('amount-label').textContent=mode==='apply'?'Amount to allocate':'Amount received';
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
    drawDestination();await drawCustom();
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
    defaultDestination=accounts.items.find(row=>row.system_role==='undeposited_funds');
    if(config.preferences.use_undeposited_funds_for_payments) $('destination').value=defaultDestination?.id||'';
    drawDestination();
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
    if(draft) await discoverRecovery();
    root.dataset.loaded='true';
  }
  $('form').addEventListener('submit',e=>e.preventDefault());
  $('find-customer').addEventListener('click',()=>perform(async()=>{
    const out=await command('customer query',{query:$('customer').value,limit:25});const results=$('customer-results');results.replaceChildren();
    for(const row of out.items) results.append(button(row.full_name||row.name,async()=>chooseCustomer(row.id)));
    if(!out.items.length) results.append(el('p','No matching customers.'));
  }));
  $('load').addEventListener('click',()=>perform(async()=>{invalidate();await makeDraft();await loadInvoices();if(!selected.size && draft.amount?.minor_units>0 && config.preferences.automatically_apply_payments) await autoApply();}));
  $('amount').addEventListener('change',()=>{const value=$('amount').value;return perform(async()=>{
    $('amount').value=value;
    if(draft&&!draftAttempt&&draft.amount_origin==='entered'&&value===draft.amount?.amount) return;
    invalidate();if(draft) {await changeDraft({amount:value||null});
    if(!selected.size && draft.amount?.minor_units>0 && config.preferences.automatically_apply_payments) await autoApply();}});});
  $('destination-override').addEventListener('click',()=>perform(()=>{destinationOverride=true;invalidate();drawDestination();}));
  $('destination-reset').addEventListener('click',()=>perform(()=>{destinationOverride=false;invalidate();drawDestination();}));
  for(const event of ['input','change']) $('customer').addEventListener(event,()=>{
    customerPending=true;invalidate();$('balances').hidden=true;$('invoices').replaceChildren();
    note('Choose a matching customer. The previous preview cannot be saved; any prior shared selection remains in Saved selections.');
  });
  for(const id of ['number','method','destination','memo','reference','reason','date','ar']) $(id).addEventListener('change',invalidate);
  $('more').addEventListener('click',()=>perform(async()=>loadInvoices(nextCursor)));
  $('auto').addEventListener('click',()=>perform(autoApply));$('calculate').addEventListener('click',()=>perform(calculate));
  $('clear').addEventListener('click',()=>perform(async()=>{if(draft) {context();if(draftAttempt) throw {code:'E_VERSION_CONFLICT',message:'Review the retained edits before clearing this shared draft.'};draft=await command('payment selection clear',{selection:draft.id,expected_version:draft.version});invalidate();await reloadDraft();note('Draft selections cleared. No recorded application changed.');}}));
  $('refresh-draft').addEventListener('click',()=>perform(async()=>{context();if(draftAttempt) throw {code:'E_VERSION_CONFLICT',message:'Review the retained edits before refreshing this shared draft.'};invalidate();await reloadDraft();await loadInvoices();}));
  $('preview').addEventListener('click',()=>perform(preparePreview));$('save').addEventListener('click',()=>perform(()=>save(false)));$('save-new').addEventListener('click',()=>perform(()=>save(true)));
  $('review').addEventListener('click',()=>perform(reviewCurrent));$('retry').addEventListener('click',()=>perform(()=>save(false,true)));
  perform(initialize);
})();
