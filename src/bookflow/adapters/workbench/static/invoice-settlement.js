/* Retrieve the whole prospective recipe before allowing an invoice correction. */
(() => {
  const panel=document.getElementById('invoice-settlement-preview');if(!panel||panel.dataset.ready)return;panel.dataset.ready='true';
  const form=panel.closest('form'), save=form.querySelector('button[value="submit"]'),status=panel.querySelector('[data-settlement-status]'),target=panel.querySelector('[data-settlement-items]');
  const exact=window.BookflowExactJSON;
  const result=exact.parse(panel.querySelector('[data-settlement-result]').textContent);save.disabled=true;
  async function load() {
    if(result.idempotent_replay) {status.textContent='Original effect recovered. No new financial or header changes; current availability is separate.';save.disabled=false;return;}
    for(const descriptor of result.prospective_pages||[]) {
      let rows=[...result.effect[descriptor.kind]],cursor=descriptor.next_cursor;
      while(cursor) {
        const response=await fetch('/companies/'+panel.dataset.company+'/commands/payment.preview.items',{method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1','X-Bookflow-Client-Name':'bookflow-workbench'},
          body:exact.stringify({request:descriptor.request,kind:descriptor.kind,facts_fingerprint:result.facts_fingerprint,cursor})});
        const page=exact.parse(await response.text());if(!response.ok||page.facts_fingerprint!==result.facts_fingerprint)throw Error([page.code,page.message].filter(Boolean).join(' — ')||'Preview facts changed');
        rows.push(...page.items);cursor=page.next_cursor;
      }
      if(rows.length!==descriptor.total_count)throw Error('Incomplete proposed settlement changes');
      const group=document.createElement('details'),heading=document.createElement('summary');heading.textContent=descriptor.kind.replaceAll('_',' ')+': '+rows.length+' complete changes';group.append(heading);
      for(const row of rows) {
        const item=document.createElement('p');item.textContent=[row.kind,row.logical_kind,row.target_ordinal?'line '+row.target_ordinal:'',row.amount?.amount,row.amount?.currency,row.status].filter(Boolean).join(' · ');
        if(row.currency) {
          const fields=row.invoice_id?[['Gross','gross_minor_units'],['Applied','applied_minor_units'],['Due','due_minor_units']]:[['Received','received_minor_units'],['Effective received','effective_received_minor_units'],['Applied','applied_minor_units'],['Available credit','available_minor_units']];
          for(const [label,key] of fields) if(row[key]!==undefined) item.append(' · Proposed '+label+': '+exact.minor(row[key],row.currency)+' '+row.currency);
        }
        if(row.invoice_id||row.payment_id) {const a=document.createElement('a');a.href='/c/'+panel.dataset.company+'/'+(row.invoice_id?'invoice/':'payment/')+(row.invoice_id||row.payment_id);a.textContent=(row.invoice_id?'Invoice ':'Payment ')+(row.invoice_id||row.payment_id);item.append(' ',a);}
        group.append(item);
      }target.append(group);
    }
    status.textContent='Every proposed settlement change has been loaded. Review before saving.';save.disabled=false;panel.dataset.complete='true';
  }
  load().catch(err=>{status.textContent=err.message+'. Keep the draft and preview again; saving is unavailable.';});
})();
