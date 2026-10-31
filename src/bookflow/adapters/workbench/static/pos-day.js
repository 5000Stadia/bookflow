/* Day and drawer facts come from report pos-day; opening and closing post no accounting. */
(() => {
  const root = document.querySelector('[data-pos]');
  if (!root || window.bookflowPosDay) return;
  window.bookflowPosDay = true;
  const data = JSON.parse(document.getElementById('pos-data').textContent);
  const $ = selector => root.querySelector(selector);
  const node = (tag, text) => { const n=document.createElement(tag); if(text != null)n.textContent=text; return n; };
  const money = value => value ? `${value.amount} ${value.currency}` : '—';
  const register = () => window.bookflowPos.register();
  const pendingKey = `bookflow.pos.${data.company}.shift-pending`;
  let report=null, active=null, busy=false, pending=null, mode='open';
  try { pending=JSON.parse(localStorage.getItem(pendingKey)||'null'); } catch (_) {}
  function persist() { try { pending ? localStorage.setItem(pendingKey,JSON.stringify(pending)) : localStorage.removeItem(pendingKey); } catch (_) {} }
  async function command(name, body) {
    let response;
    try { response=await fetch(`/companies/${encodeURIComponent(data.company)}/commands/${name.replaceAll(' ','.')}`,{
      method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Bookflow-Workbench':'1'},body:JSON.stringify(body)}); }
    catch (_) { throw new Error('No confirmation from Bookflow. Retry this same count when connected.'); }
    let out; try { out=await response.json(); } catch (_) { throw new Error('No confirmation from Bookflow. Retry this same count.'); }
    if (!out || !response.ok || out.code) { const e=new Error(out?.message || 'The host did not confirm this action.'); e.definite=!!out?.code; throw e; }
    return out;
  }
  function controls() {
    $('[data-pos-shift-open]').hidden=!register().count_shifts || !!active;
    $('[data-pos-shift-close]').hidden=!active;
  }
  async function refresh() {
    try {
      const current=await command('report pos-day',{pos_register:register().id});
      if(current.pos_register.id!==register().id)return null;
      active=current.shifts.find(s=>s.status==='open') || null;
      controls();
      return current;
    } catch (e) { $('[data-pos-day-status]').textContent=e.message; return null; }
  }
  function detail(list,label,value) { const row=node('div');row.append(node('dt',label),node('dd',value));list.append(row); }
  function depositLink() {
    const link=$('[data-pos-day-deposit]'); if(!link || !report)return;
    const methods=[...$('[data-pos-deposit-methods]').querySelectorAll('input:checked')].map(n=>n.value);
    const query=new URLSearchParams({pos_register:report.pos_register.id,pos_date:report.date});
    if(report.shift)query.set('pos_shift',report.shift);
    for(const method of methods)query.append('pos_method',method);
    link.href=`/c/${encodeURIComponent(data.company)}/deposit/post?${query}`;
    link.hidden=!methods.length;
  }
  function draw(result) {
    report=result;
    $('[data-pos-day-date]').value=result.date;
    $('[data-pos-day-heading]').textContent=`${result.pos_register.name} · ${result.date}`;
    const body=$('[data-pos-day-results]');body.replaceChildren();
    const totals=node('dl');
    for(const [label,key] of [['Gross sales','gross_sales'],['Discounts','discounts'],['Tax collected','tax'],['Sales total','total_sales'],['Refunds','refunds_total'],['Refund tax','refund_tax'],['Voids','voids_total'],['Cash sales','cash_sales'],['Cash refunds','cash_refunds'],['Cash to deposit','cash_to_deposit'],['Expected in drawer','expected_drawer']])detail(totals,label,money(result[key]));
    detail(totals,'Sales / voids',`${result.sales_count} / ${result.voids_count}`);body.append(totals);
    body.append(node('h3','By payment method'));const tenders=node('ul');
    for(const t of result.by_tender)tenders.append(node('li',`${t.payment_method.name}: ${t.count} sale(s), ${money(t.amount)}; ${t.refund_count} refund(s), ${money(t.refund_amount)}`));
    body.append(tenders);
    body.append(node('h3','Shifts'));const shifts=node('ul');
    for(const s of result.shifts) {
      const li=node('li',`${s.status==='closed'?'Closed':'Open'} · Float ${money(s.float)} · Expected ${money(s.expected)}`);
      if(s.status==='closed')li.append(node('div',`Counted ${money(s.counted)} · Over / short ${money(s.over_short)}`));
      if(s.status==='closed' && data.can_deposit) { const button=node('button','Deposit this shift');button.type='button';button.addEventListener('click',()=>load({shift:s.id}));li.append(button); }
      shifts.append(li);
    }
    body.append(shifts);
    body.append(node('h3','Held tickets left'));const held=node('ul');
    for(const t of result.held_tickets)held.append(node('li',`${t.label || t.ticket || t.id}${t.older_than_seven_days ? ' · Older than seven days' : ''}`));
    if(!result.held_tickets.length)held.append(node('li','None'));body.append(held);
    const methods=$('[data-pos-deposit-methods]');
    if(methods) { methods.replaceChildren(node('legend','Payment methods to deposit')); for(const t of result.by_tender) {
      const label=node('label'),check=node('input');check.type='checkbox';check.value=t.payment_method.id;check.checked=t.payment_method.kind==='cash';check.addEventListener('change',depositLink);label.append(check,document.createTextNode(t.payment_method.name));methods.append(label);
    } depositLink(); }
  }
  async function load(extra={}) {
    $('[data-pos-day-status]').textContent='Loading…';
    try { const result=await command('report pos-day',{pos_register:register().id,...extra});if(result.pos_register.id!==register().id)return;draw(result);if(extra.shift) {report.shift=extra.shift;depositLink();} $('[data-pos-day-status]').textContent=''; }
    catch(e) { $('[data-pos-day-status]').textContent=e.message; }
  }
  async function openDay() { $('[data-pos-day-dialog]').showModal();await load();await refresh(); }
  function shiftDialog(next) {
    mode=pending?.command==='pos shift close'?'close':pending?'open':next;
    const closing=mode==='close';
    $('[data-pos-shift-title]').textContent=closing?'Close shift':'Open shift';
    $('[data-pos-shift-label]').textContent=closing?'Counted cash':'Opening float';
    $('[data-pos-shift-explanation]').textContent=closing?`Count all cash in the drawer, including the opening float. Expected ${money(active?.expected)}.`:'Enter the cash already in the drawer before sales begin.';
    $('[data-pos-shift-amount]').value=pending?(pending.body.counted ?? pending.body.float):'';
    $('[data-pos-shift-amount]').disabled=!!pending;
    $('[data-pos-shift-submit]').textContent=pending?'Retry this count':closing?'Close shift':'Open shift';
    $('[data-pos-shift-status]').textContent=pending?`The last count for ${pending.registerName || 'this register'} has no confirmed answer. Retry it before entering another.`:'';
    $('[data-pos-shift-dialog]').showModal();
  }
  $('[data-pos-day-open]').addEventListener('click',openDay);
  $('[data-pos-day-dismiss]').addEventListener('click',()=>$('[data-pos-day-dialog]').close());
  $('[data-pos-day-filter]').addEventListener('submit',e=>{e.preventDefault();load({date:$('[data-pos-day-date]').value});});
  $('[data-pos-shift-open]').addEventListener('click',async()=>{if(await refresh())shiftDialog('open');});
  $('[data-pos-shift-close]').addEventListener('click',async()=>{if(await refresh())shiftDialog('close');});
  $('[data-pos-shift-dismiss]').addEventListener('click',()=>$('[data-pos-shift-dialog]').close());
  $('[data-pos-shift-form]').addEventListener('submit',async e=>{
    e.preventDefault();if(busy)return;
    if(!pending) { const key=crypto.randomUUID ? crypto.randomUUID() : `shift-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      pending={registerName:register().name,command:`pos shift ${mode}`,body:mode==='close'?{shift:active?.id,counted:$('[data-pos-shift-amount]').value,operation_key:key}:{pos_register:register().id,float:$('[data-pos-shift-amount]').value,operation_key:key}};persist(); }
    busy=true;$('[data-pos-shift-submit]').disabled=true;$('[data-pos-shift-amount]').disabled=true;
    try { await command(pending.command,pending.body);pending=null;persist();$('[data-pos-shift-dialog]').close();await openDay(); }
    catch(err) { if(err.definite){pending=null;persist();} $('[data-pos-shift-status]').textContent=err.message;$('[data-pos-shift-amount]').disabled=!!pending;$('[data-pos-shift-submit]').textContent=pending?'Retry this count':mode==='close'?'Close shift':'Open shift'; }
    finally { busy=false;$('[data-pos-shift-submit]').disabled=false; }
  });
  $('[data-pos-register-select]')?.addEventListener('change',refresh);
  refresh().then(()=>{if(pending)shiftDialog('open');});
})();
