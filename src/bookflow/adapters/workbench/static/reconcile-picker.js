/* Ticking a statement. The generated form still owns save and retry; this owns the list a
   person ticks, the running difference while they tick it, and the finish once it is zero.

   `reconcile mark` wants a movement key and a group fingerprint per entry, which nothing a
   person can type produces -- they come from `reconcile candidates`, and this is what carries
   them from the one to the other. The running difference is arithmetic on the integer minor
   units the candidates already carry, so it answers on every tick; the authority is still
   `reconcile preview`, which is what the finish is gated on. */
(() => {
  if (window.bookflowReconcilePicker) return;
  window.bookflowReconcilePicker = true;
  const node = (tag, text) => { const e=document.createElement(tag); if(text!==undefined)e.textContent=text; return e; };
  const money = (units, currency) => window.BookflowExactJSON.minor(units, currency);
  // `crypto.randomUUID` needs a secure context and the workbench is ordinarily served over
  // plain HTTP on a machine's own address, where it is simply not defined.
  const key = (what='finish') => 'WB-' + what + '-' + Array.from(crypto.getRandomValues(new Uint8Array(18)),
    b => b.toString(16).padStart(2, '0')).join('');

  function initialize() {
    const marker=document.querySelector('[data-reconcile-company]');
    const form=marker?.closest('form');
    if(!form || form.dataset.reconcileReady)return;
    form.dataset.reconcileReady='true';
    const exact=window.BookflowExactJSON, field=name=>form.elements.namedItem(name);
    const company=marker.dataset.reconcileCompany;
    const entries=form.querySelector('[data-collection-path="entries"]');
    const draftField=field('f:draft'), versionField=field('f:expected_version');
    if(!entries || !draftField)return;

    // The three fields that identify the draft are not questions for a person, but they are
    // still what the form submits, so they are folded away rather than taken out.
    const advanced=node('details');advanced.append(node('summary','Which draft this is'));
    for(const name of ['f:operation_key','f:draft','f:expected_version']){
      const row=field(name)?.closest('.form-field');
      if(row){advanced.append(row);if(name==='f:operation_key')row.hidden=true;}
    }
    form.insertBefore(advanced,form.querySelector('.form-submit')||form.lastElementChild);
    const error=node('p');error.setAttribute('role','alert');marker.after(error);
    const summary=node('section');summary.className='reconcile-summary';
    summary.setAttribute('aria-label','Reconciliation totals');
    const figures=node('dl');figures.className='reconcile-figures';
    const cells={};
    for(const [key,label] of [['cleared','Cleared balance'],['statement','Statement ending balance'],
                              ['ticked','Ticked'],['difference','Difference']]){
      const wrap=node('div');wrap.append(node('dt',label));
      const value=node('dd','—');if(key==='difference')value.className='reconcile-difference';
      cells[key]=value;wrap.append(value);figures.append(wrap);
    }
    summary.append(node('h2','Where this statement stands'),figures);
    const status=node('p');status.setAttribute('role','status');summary.append(status);
    const controls=node('div');controls.className='reconcile-controls';
    const query=node('input');query.type='search';query.placeholder='Number, payee or memo';
    query.setAttribute('aria-label','Find movements');
    const load=node('button','Show what can be cleared');load.type='button';
    load.setAttribute('data-reconcile-load','');
    // "Mark all" ticks everything listed in one saved step (`reconcile mark` with `all`), the way
    // QuickBooks' button does; "Clear all marks" is its undo. Both are previewable on the CLI.
    const markAll=node('button','Mark all');markAll.type='button';markAll.setAttribute('data-reconcile-mark-all','');
    const clearAll=node('button','Clear all marks');clearAll.type='button';clearAll.setAttribute('data-reconcile-clear-all','');
    controls.append(query,load,markAll,clearAll);
    const list=node('div');list.className='reconcile-list';list.setAttribute('data-reconcile-list','');
    const actions=node('div');actions.className='reconcile-actions';
    const finish=node('button','Finish and certify');finish.type='button';finish.disabled=true;
    finish.setAttribute('data-reconcile-finish','');
    // A statement that genuinely will not tie: QuickBooks' "Enter Adjustment". Offered only once
    // the saved marks still leave a difference and nothing unsaved is pending, so the amount it
    // names is the one the finish will post. Agents are refused by the command itself.
    const adjust=node('section');adjust.className='reconcile-adjust';adjust.hidden=true;
    adjust.setAttribute('data-reconcile-adjust','');adjust.setAttribute('aria-label','Finish with adjustment');
    const adjustText=node('p');adjustText.setAttribute('data-reconcile-adjust-text','');
    const reasonLabel=node('label','Reason for the adjustment ');
    const reason=node('input');reason.type='text';reason.maxLength=500;
    reason.setAttribute('data-reconcile-adjust-reason','');reasonLabel.append(reason);
    const adjustFinish=node('button','Finish with adjustment');adjustFinish.type='button';adjustFinish.disabled=true;
    adjustFinish.setAttribute('data-reconcile-adjust-finish','');
    adjust.append(node('h3','The statement will not tie?'),adjustText,reasonLabel,adjustFinish);
    actions.append(finish,adjust);
    const certificate=node('div');certificate.hidden=true;certificate.className='reconcile-certificate';
    entries.before(summary);entries.after(controls,list,actions,certificate);
    entries.hidden=true;

    const rows=entries.querySelector('[data-collection-items]');
    const chosen=new Map();
    let currency='', server=null, busy=false, finished=false, cutoff='';
    const ADJUSTMENT_ACCOUNT='Reconciliation Discrepancies';
    // A disabled button says "not yet"; a button that looks ready and does nothing when pressed
    // says the page is broken. So everything that makes the page unready goes through here.
    function working(value){
      busy=value;load.disabled=value || finished;markAll.disabled=clearAll.disabled=value || finished;
      finish.disabled=value || finished || !server || !server.balanced;
      adjustFinish.disabled=value || finished || !server || server.balanced || adjust.hidden || !reason.value.trim();
    }

    async function command(name,input,headers={}){
      const response=await fetch('/companies/'+encodeURIComponent(company)+'/commands/'+name.replaceAll(' ','.'),{
        method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1',...headers},
        body:exact.stringify(input)});
      const result=exact.parse(await response.text());
      if(!response.ok || result.code)throw new Error(result.message || result.code || 'Unable to read this reconciliation.');
      return result;
    }

    function sync(){
      rows.replaceChildren();let i=0;
      for(const row of chosen.values()){
        const wrap=node('div');
        for(const [key,value] of [['movement',exact.stringify(row.movement)],
                                  ['group_fingerprint',row.group_fingerprint],['action','mark']]){
          const input=node('input');input.type='hidden';
          input.name='c:entries:'+i+':'+key;
          input.value=value;wrap.append(input);
        }
        rows.append(wrap);i++;
      }
      const ticked=[...chosen.values()].reduce((total,row)=>total+Number(row.amount),0);
      cells.ticked.textContent=chosen.size+' movement(s) · '+money(ticked,currency)+' '+currency;
      if(server){
        const cleared=Number(server.totals.beginning_balance)+ticked;
        const difference=Number(server.totals.ending_balance)-cleared;
        cells.cleared.textContent=money(cleared,currency)+' '+currency;
        cells.difference.textContent=money(difference,currency)+' '+currency;
        cells.difference.dataset.balanced=String(difference===0);
        status.textContent=difference===0
          ? 'The difference is zero. Save your marks, then finish.'
          : 'Keep ticking until the difference is zero.';
        // Offered on the saved marks only: an unsaved tick would change what it posts.
        const saved=Number(server.totals.difference);
        adjust.hidden=finished || saved===0 || difference!==saved;
        if(!adjust.hidden)adjustText.textContent='If you have looked and it still will not tie, finish with an '
          +'adjustment: one journal for '+money(saved,currency)+' '+currency
          +(cutoff?' dated '+cutoff:'')+', posted to '+ADJUSTMENT_ACCOUNT+', and the statement is certified.';
        working(busy);
      }
      rows.dispatchEvent(new Event('change',{bubbles:true}));
    }

    async function refresh(){
      if(!draftField.value)return;
      server=await command('reconcile preview',{draft:draftField.value,
        expected_version:Number(versionField.value||1)});
      currency=server.currency;
      cells.statement.textContent=money(server.totals.ending_balance,currency)+' '+currency;
      working(busy);
      if(server.balanced)status.textContent='Saved marks balance this statement. Finish to certify it.';
      sync();
    }

    async function show(){
      if(busy)return;working(true);error.textContent='';
      try{
        if(!draftField.value)throw new Error('Open this page from the statement you started.');
        list.replaceChildren();
        let cursor=null,seen=0;
        do{
          const page=await command('reconcile candidates',{draft:draftField.value,limit:200,
            ...(cursor?{cursor}:{}),
            ...(query.value?{filters:{number:query.value,hide_after_date:true,sort:'date',descending:false}}:{})});
          currency=page.currency;cutoff=page.cutoff||cutoff;
          for(const row of page.items){
            const label=node('label');label.className='reconcile-movement';
            label.dataset.stale=String(row.stale);label.dataset.claimed=String(row.claimed);
            const check=node('input');check.type='checkbox';
            check.checked=chosen.has(row.group_fingerprint)||row.selected;
            check.disabled=row.stale||row.claimed||!row.eligible;
            if(check.checked)chosen.set(row.group_fingerprint,row);
            check.addEventListener('change',()=>{
              if(check.checked)chosen.set(row.group_fingerprint,row);else chosen.delete(row.group_fingerprint);
              sync();});
            const facts=node('div');facts.className='reconcile-facts';
            for(const part of [row.date,row.number,(row.payees||[]).join(', '),row.memo||''])
              if(part)facts.append(node('span',part));
            const body=node('div');
            body.append(node('div',money(row.amount,currency)+' '+currency),facts);
            body.firstChild.className='reconcile-amount reconcile-who';
            if(row.stale)facts.append(node('span','changed since you last looked'));
            if(row.claimed)facts.append(node('span','already cleared by another statement'));
            label.append(check,body);list.append(label);seen++;
          }
          cursor=page.next_cursor;
        }while(cursor);
        status.textContent=seen+' movement(s) to review.';
        sync();
      }catch(e){error.textContent=e.message;}finally{working(false);}
    }

    async function everything(action){
      if(busy||finished)return;working(true);error.textContent='';
      try{
        if(!draftField.value)throw new Error('Open this page from the statement you started.');
        const done=await command('reconcile mark',{operation_key:key('mark-all'),draft:draftField.value,
          expected_version:Number(versionField.value||1),all:true,all_action:action,
          ...(query.value?{filters:{number:query.value}}:{})},
          {'X-Bookflow-Reason':action==='mark'?'Mark all on the reconciliation':'Clear all marks on the reconciliation'});
        // The write moved the draft on; the form's own save must start from this version, and the
        // list must be read again so the boxes show what is now saved.
        versionField.value=String(done.draft.version);chosen.clear();
        await refresh();
      }catch(e){error.textContent=e.message;working(false);return;}
      working(false);await show();
    }
    markAll.addEventListener('click',()=>everything('mark'));
    clearAll.addEventListener('click',()=>everything('unmark'));

    finish.addEventListener('click',async()=>{
      if(busy||finished||!server)return;working(true);error.textContent='';
      try{
        const fresh=await command('reconcile preview',{draft:draftField.value,
          expected_version:Number(versionField.value||1)});
        if(!fresh.balanced)throw new Error('The saved marks do not balance this statement yet.');
        // Its own key: the form's belongs to the save that put the marks in, and reusing it is
        // refused as a replayed operation rather than accepted as a different one.
        const done=await command('reconcile finish',{
          operation_key:key(),draft:draftField.value,
          expected_version:fresh.version,
          expected_facts_fingerprint:fresh.expected_facts_fingerprint,
          dependency_guard:fresh.dependency_guard});
        finished=true;
        finish.textContent='Statement certified';
        certificate.hidden=false;
        certificate.replaceChildren(node('h2','Statement certified'),
          node('p','Certificate '+done.certificate_id+' · cleared balance '
               +money(done.totals.cleared_balance,currency)+' '+currency));
        list.replaceChildren();controls.hidden=true;
        status.textContent='This statement is reconciled.';
      }catch(e){error.textContent=e.message;}finally{working(false);}
    });

    reason.addEventListener('input',()=>working(busy));
    adjustFinish.addEventListener('click',async()=>{
      if(busy||finished||!server||!reason.value.trim())return;working(true);error.textContent='';
      try{
        const fresh=await command('reconcile preview',{draft:draftField.value,
          expected_version:Number(versionField.value||1)});
        if(fresh.balanced)throw new Error('The saved marks balance this statement now; finish it without an adjustment.');
        const done=await command('reconcile finish',{
          operation_key:key('adjust'),draft:draftField.value,
          expected_version:fresh.version,
          expected_facts_fingerprint:fresh.expected_facts_fingerprint,
          dependency_guard:fresh.dependency_guard,
          adjustment:{reason:reason.value.trim()}},
          {'X-Bookflow-Reason':'Finish the statement with a reconciliation adjustment'});
        finished=true;adjust.hidden=true;
        finish.textContent='Statement certified';
        const made=done.adjustment;
        certificate.hidden=false;
        certificate.replaceChildren(node('h2','Statement certified with an adjustment'),
          node('p','Journal '+(made.number||made.journal_id)+' posted '+made.amount_decimal+' '+currency
               +' to '+made.account_name+' on '+made.date+'.'),
          node('p','Certificate '+done.certificate_id+' · cleared balance '
               +money(done.totals.cleared_balance,currency)+' '+currency));
        list.replaceChildren();controls.hidden=true;
        status.textContent='This statement is reconciled, with an adjustment.';
      }catch(e){error.textContent=e.message;}finally{working(false);}
    });

    load.addEventListener('click',()=>show());
    query.addEventListener('change',()=>show());
    refresh().then(()=>show()).catch(e=>{error.textContent=e.message;});
  }
  document.addEventListener('DOMContentLoaded',initialize);
  document.addEventListener('htmx:afterSwap',initialize);
  initialize();
})();
