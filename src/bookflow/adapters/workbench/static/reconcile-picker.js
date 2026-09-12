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
  const key = () => 'WB-finish-' + Array.from(crypto.getRandomValues(new Uint8Array(18)),
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
    controls.append(query,load);
    const list=node('div');list.className='reconcile-list';list.setAttribute('data-reconcile-list','');
    const actions=node('div');actions.className='reconcile-actions';
    const finish=node('button','Finish and certify');finish.type='button';finish.disabled=true;
    finish.setAttribute('data-reconcile-finish','');
    actions.append(finish);
    const certificate=node('div');certificate.hidden=true;certificate.className='reconcile-certificate';
    entries.before(summary);entries.after(controls,list,actions,certificate);
    entries.hidden=true;

    const rows=entries.querySelector('[data-collection-items]');
    const chosen=new Map();
    let currency='', server=null, busy=false;
    // A disabled button says "not yet"; a button that looks ready and does nothing when pressed
    // says the page is broken. So everything that makes the page unready goes through here.
    function working(value){
      busy=value;load.disabled=value;
      finish.disabled=value || !server || !server.balanced;
    }

    async function command(name,input){
      const response=await fetch('/companies/'+encodeURIComponent(company)+'/commands/'+name.replaceAll(' ','.'),{
        method:'POST',credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},
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
          currency=page.currency;
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

    finish.addEventListener('click',async()=>{
      if(busy||!server)return;working(true);error.textContent='';
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
        certificate.hidden=false;
        certificate.replaceChildren(node('h2','Statement certified'),
          node('p','Certificate '+done.certificate_id+' · cleared balance '
               +money(done.totals.cleared_balance,currency)+' '+currency));
        list.replaceChildren();controls.hidden=true;
        status.textContent='This statement is reconciled.';
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
