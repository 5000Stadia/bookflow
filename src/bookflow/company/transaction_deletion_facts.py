"""Real-snapshot four-family evidence. No writes, policy provider or void producer."""
from contextlib import contextmanager
from dataclasses import replace
import sqlalchemy as sa
from bookflow.company import schema as c, document_effects, journals, billing_allocations
from bookflow.company import deposit_dependencies, payment_authority, reconciliation_adapters
from bookflow.company.deposit_dependency_history import execution_binding
from bookflow.company.transaction_deletion_models import DeleteFacts, StoredRow, DepositClaimBlocker, ApplicationsBlocker
from bookflow.core.errors import BookflowError
from bookflow.core.session import Session, Actor
from bookflow.hub import access, schema as h

FAMILIES = ('journal_entry', 'invoice', 'sales_receipt', 'payment')
OWNED = ('transaction_revisions', 'document_line_identities', 'document_lines',
    'posting_batches', 'posting_lines', 'posting_line_sources', 'sales_profiles',
    'sales_line_profiles', 'sales_tax_components', 'sales_tax_line_keys',
    'sales_tax_attributions', 'sales_tax_attribution_lines', 'payment_profiles',
    'payment_component_keys', 'payment_components', 'settlement_line_keys',
    'work_billing_allocations', 'deposit_profiles', 'deposit_row_keys', 'deposit_component_keys',
    'deposit_components', 'deposit_cash_cells', 'deposit_memberships', 'deposit_current_memberships')
EXTRA = ('transactions', 'applications', 'application_allocations', 'custom_field_values',
    'work_documents', 'work_revisions', 'work_lines', 'work_line_identities', 'audit_events', 'audit_entries', 'sequences', 'notes', 'attachment_links', 'attachments')
HISTORY = ('payment_operations','payment_operation_items','payment_selections',
    'payment_selection_revisions','payment_selection_items','payment_selection_recoveries',
    'payment_selection_recovery_chunks','payment_selection_recovery_items','payment_selection_recovery_active',
    'deposit_operations','deposit_operation_items','deposit_operation_targets','work_billing_conversions')
TABLES = frozenset((*OWNED, *EXTRA, *HISTORY))

def require(ok):
    if not ok:
        raise BookflowError('E_INTERNAL', message='Invalid owned deletion evidence.')

def row(table, values):
    require(table in TABLES)
    columns = tuple(c.metadata.tables[table].columns)
    require(set(values) == {col.name for col in columns})
    for col in columns:
        value = values[col.name]
        if value is None:
            require(col.nullable)
        elif isinstance(col.type, (sa.Integer, sa.BigInteger, sa.Boolean)):
            require(type(value) is int and -9223372036854775808 <= value <= 9223372036854775807)
            if isinstance(col.type, sa.Boolean): require(value in (0, 1))
        elif isinstance(col.type, sa.LargeBinary): require(type(value) is bytes)
        else: require(type(value) is str)
    return StoredRow(table=table, cells=tuple((col.name, values[col.name]) for col in columns))

def read(s, table, field, identifiers):
    require(table in TABLES and field in c.metadata.tables[table].c)
    ids = sorted(set(identifiers))
    result = []
    for offset in range(0, len(ids), 200):
        part = ids[offset:offset+200]
        names = ','.join('"'+col.name+'"' for col in c.metadata.tables[table].columns)
        sql = 'SELECT '+names+' FROM main."'+table+'" WHERE "'+field+'" IN ('+','.join('?' for _ in part)+')'
        result.extend(row(table, dict(value)) for value in s.company.conn.exec_driver_sql(sql, tuple(part)).mappings())
    return tuple(sorted(result, key=lambda item: repr(item.cells)))

def one(values):
    require(len(values) == 1)
    return values[0]

@contextmanager
def snapshot(s):
    """Borrow an existing snapshot or own a read transaction, never commit it."""
    require(type(s) is Session and s.company is not None and s.hub is not None)
    opened = []
    try:
        for db in (s.hub, s.company):
            if not db.raw.in_transaction:
                db.raw.execute('BEGIN')
                opened.append(db)
        yield
    finally:
        for db in reversed(opened):
            db.raw.execute('ROLLBACK')

def actors(s, ctx, binding):
    actor, kind, principal, _ = execution_binding(s, binding)
    if principal != ctx.on_behalf_of:
        raise BookflowError('E_UNAUTHENTICATED')
    result = []
    for identity in (actor,) if principal is None else (actor, principal):
        value = s.hub.conn.execute(sa.select(h.users).where(h.users.c.id == identity, h.users.c.active.is_(True))).mappings().one_or_none()
        if value is None: raise BookflowError('E_UNAUTHENTICATED')
        # Same authenticated resource-only view as deposit history. Financial
        # reads/planners always receive the original real Session and database.
        view = replace(s, actor=Actor(**{key:value[key] for key in ('id','kind','username','display_name','hub_admin','timezone')}), memberships=[])
        access.load_memberships(view)
        result.append(view)
    return tuple(result), (actor, kind, principal)

def admit(s, ctx, family, binding):
    if family not in FAMILIES: raise BookflowError('E_VALIDATION')
    # Registry-backed require_resource also denies these capabilities by default.
    # This explicit gate guarantees ordering before binding/record disclosure.
    access.require_explicit_grant(s, 'transaction.'+family+'.delete')
    views, identity = actors(s, ctx, binding)
    for view in views:
        access.require_resource(view, 'transaction.'+family+'.delete', 'standard')
        access.require_resource(view, 'ledger.read', 'member')
    return views, identity

def verify_fks(s, rows):
    """Check every declared composite FK against actual stored owner rows."""
    for item in rows:
        values = item.values(); table = c.metadata.tables[item.table]
        if 'name_type' in values:
            from bookflow.company.parties import TABLES as parties
            kind, identity = values['name_type'], values['name_id']
            require((kind is None)==(identity is None))
            if identity is not None:
                require(kind.replace('_','-') in parties)
                party=parties[kind.replace('_','-')]
                require(s.company.conn.execute(sa.select(party.c.id).where(party.c.id==identity)).first() is not None)
        for constraint in table.foreign_key_constraints:
            elements = list(constraint.elements)
            if any(values[e.parent.name] is None for e in elements): continue
            target = elements[0].column.table
            predicate = [e.column == values[e.parent.name] for e in elements]
            require(s.company.conn.execute(sa.select(sa.literal(1)).select_from(target).where(*predicate).limit(1)).first() is not None)

def inverse_matches(original, inverse):
    """Full-value reversal bijection, independently of document_effects.reverse."""
    before = {r.values()['id']:r for r in original}
    require(len(before) == len(original) and len(original) == len(inverse))
    seen = set()
    for item in inverse:
        value=item.values()
        link = 'reversed_line_id' if item.table == 'posting_lines' else 'reversed_source_id'
        old_id=value[link]
        require(old_id in before and old_id not in seen)
        seen.add(old_id); old=before[old_id].values()
        changed={'id','created_at','created_by','created_via',link,
                 'batch_id' if item.table=='posting_lines' else 'posting_line_id'}
        if item.table=='posting_lines': changed |= {'debit_minor_units','credit_minor_units'}
        require({k:v for k,v in value.items() if k not in changed} == {k:v for k,v in old.items() if k not in changed})
        if item.table=='posting_lines':
            require((value['debit_minor_units'],value['credit_minor_units']) == (old['credit_minor_units'],old['debit_minor_units']))

def load(s, ctx, intent, binding):
    views, identity = admit(s, ctx, intent.family, binding)
    found=read(s,'transactions','id',(intent.transaction_id,))
    if not found or found[0].values()['type'] != intent.family: raise BookflowError('E_RECORD_NOT_FOUND')
    header=one(found); hv=header.values()
    # R0's existing read-only closure includes historical applications, all
    # selection attempts/consumed operations and deposit operation participants.
    participants=reconciliation_adapters.authority(s,(intent.transaction_id,))
    for view in views:
        payment_authority.authorize(view, participants)
        if payment_authority.linked_work_required(s.company,participants):
            access.require_resource(view,'customer-work','standard')
    deposit_dependencies.reconciliation_status(s.company)
    require(hv['status'] in ('posted','voided'))
    from bookflow.company import sales, payment_dependencies
    version_owner=(journals.version_meta if intent.family=='journal_entry' else
        payment_dependencies.payment_version if intent.family=='payment' else sales._version)
    version_owner(s,hv,intent.expected_version)
    normalized_reason(ctx)
    if hv['version'] >= 9223372036854775807: raise BookflowError('E_VALUE_RANGE')
    blockers=[]
    from bookflow.company.payment_queries import active_applications
    applications=active_applications(s, **{intent.family:intent.transaction_id}) if intent.family in ('payment','invoice') else []
    if applications:
        blockers.append(ApplicationsBlocker(family=intent.family,transaction_id=intent.transaction_id,
            application_ids=tuple(sorted(r['id'] for r in applications))))
    claim=deposit_dependencies.active_claim(s,intent.transaction_id)
    if claim is not None:
        blockers.append(DepositClaimBlocker(source_id=intent.transaction_id,
            deposit_id=claim['transaction_id'],membership_id=claim['membership_id']))
    rows=list(read(s,'transactions','id',participants))
    for table in OWNED: rows.extend(read(s,table,'transaction_id',participants))
    apps={r.values()['id']:r for field in ('paying_transaction_id','paid_transaction_id') for r in read(s,'applications',field,participants)}
    rows.extend(apps.values())
    rows.extend(history(s, participants))
    allocations=read(s,'application_allocations','application_id',apps)
    rows.extend(allocations)
    # Full historical custom slots, not just currently active displayed values.
    rows.extend(r for r in read(s,'custom_field_values','record_id',participants) if r.values()['record_type'] in FAMILIES)
    owned=[r for r in rows if r.values().get('transaction_id')==intent.transaction_id]
    revision=one([r for r in owned if r.table=='transaction_revisions' and r.values()['id']==hv['current_revision_id']])
    batches=[r for r in owned if r.table=='posting_batches']
    business=one([r for r in batches if r.values()['revision_id']==hv['current_revision_id'] and r.values()['kind']!='reversal'])
    validate_history(owned, header)
    bv=business.values(); current_inverses=[r for r in batches if r.values()['reverses_batch_id']==bv['id']]
    prior=None
    if hv['status']=='posted': require(not current_inverses and hv['void_posting_batch_id'] is None)
    else:
        prior=one(current_inverses); pv=prior.values()
        require(pv['id']==hv['void_posting_batch_id'] and pv['kind']=='reversal' and pv['revision_id']==revision.values()['id'] and pv['effective_date']==bv['effective_date'])
        require(hv['voided_at']==pv['created_at'] and hv['voided_by']==pv['created_by'] and bool(hv['void_reason'].strip()))
    work_rows=[r for r in owned if r.table=='work_billing_allocations']
    for table, field, ids in (
        ('work_documents','id',{r.values()[key] for r in work_rows for key in ('source_document_id','root_document_id')}),
        ('work_revisions','id',{r.values()['source_revision_id'] for r in work_rows}),
        ('work_lines','id',{r.values()['source_line_id'] for r in work_rows})):
        rows.extend(read(s,table,field,ids))
    roots={r.values()['root_line_id'] for r in work_rows}
    roots.update(r.values()['line_id'] for r in rows if r.table=='work_lines')
    rows.extend(read(s,'work_line_identities','id',roots))
    work_ancestry(s,rows,work_rows)
    for item in work_rows:
        value=item.values(); proof=billing_allocations.read_proof(value)
        if proof is not None:
            require(value['source_basis_hash']==billing_allocations.basis(billing_allocations.captured_line(value),
                (value['root_document_id'],value['root_line_id']),billing_allocations.captured_policy(value)))
    events={r.values()['audit_event_id'] for r in rows if r.values().get('audit_event_id')}
    rows.extend(read(s,'audit_events','id',events))
    rows.extend(read(s,'audit_entries','event_id',events))
    # Immutable rows have original owning audit snapshots. Inverse checks alone
    # cannot detect a balanced corruption of the original financial evidence.
    audited_owned=owned+[r for r in rows if r.table in ('applications','application_allocations')
        and intent.transaction_id in {r.values().get(k) for k in ('paying_transaction_id','paid_transaction_id',
            'source_transaction_id','target_transaction_id')}]
    audit_evidence(rows, audited_owned, header, prior)
    rows.extend(preservation(s,intent,owned))

    unique={(r.table,r.cells):r for r in rows}
    ordered=tuple(sorted(unique.values(),key=lambda r:(r.table,repr(r.cells))))
    verify_fks(s,ordered)
    legs=[r for r in owned if r.table=='posting_lines' and r.values()['batch_id']==bv['id']]
    sources=[r for r in owned if r.table=='posting_line_sources' and r.values()['posting_line_id'] in {v.values()['id'] for v in legs}]
    require(legs and sum(r.values()['debit_minor_units'] for r in legs)==sum(r.values()['credit_minor_units'] for r in legs)==revision.values()['total_minor_units'])
    require(sorted(r.values()['line_no'] for r in legs)==list(range(1,len(legs)+1)))
    for source in sources:
        sv=source.values()
        require(sv['amount_minor_units']>0 and sv['revision_id']==revision.values()['id'] and sv['currency']==revision.values()['currency'])
    for leg in legs:
        lv=leg.values(); require(lv['currency']==revision.values()['currency'])
        require(lv['debit_minor_units']>=0 and lv['credit_minor_units']>=0 and (lv['debit_minor_units']>0)!=(lv['credit_minor_units']>0))
        require(sum(r.values()['amount_minor_units'] for r in sources if r.values()['posting_line_id']==lv['id'])==lv['debit_minor_units']+lv['credit_minor_units'])
    if prior:
        inverted=[r for r in owned if r.table=='posting_lines' and r.values()['batch_id']==prior.values()['id']]
        inverse_matches(legs,inverted)
        invsources=[r for r in owned if r.table=='posting_line_sources' and r.values()['posting_line_id'] in {v.values()['id'] for v in inverted}]
        inverse_matches(sources,invsources)
        linkage={r.values()['id']:r.values()['reversed_line_id'] for r in inverted}
        oldsources={r.values()['id']:r.values() for r in sources}
        for item in invsources: require(linkage[item.values()['posting_line_id']]==oldsources[item.values()['reversed_source_id']]['posting_line_id'])
    elif not blockers: journals.open_dates(s,[bv['effective_date']])
    required=[('transaction.'+intent.family+'.delete','standard'),('ledger.read','member')]
    if payment_authority.linked_work_required(s.company,participants): required.append(('customer-work','standard'))
    return DeleteFacts(company_id=s.company_row['id'],transaction_id=intent.transaction_id,family=intent.family,
        header=header,revision=revision,business_batch=business,prior_void_batch=prior,rows=ordered,participants=tuple(participants),
        work_allocation_ids=tuple(sorted(r.values()['id'] for r in work_rows)),
        current_work_allocation_ids=tuple(sorted(r.values()['id'] for r in work_rows if r.values()['revision_id']==hv['current_revision_id'])),
        required_resources=tuple(required),blockers=tuple(blockers)), identity


def audit_evidence(rows, owned, header, prior):
    from bookflow.core.audit import decode_snapshot
    entries=[r.values() for r in rows if r.table=='audit_entries']
    from bookflow.company import sales, payments
    inventory=(*sales.TABLE_KINDS,*payments.TABLE_KINDS,('work_billing_allocations','work_billing_allocation','id'))
    kinds={table:kind for table,kind,key in inventory}
    keys={table:key for table,kind,key in inventory}
    hv=header.values();root=hv['id']
    current=one([e for e in entries if e['record_type']=='transaction' and e['record_id']==root
        and e['version_after']==hv['version']])
    require(decode_snapshot(current['after'])==hv)
    current_revision=one([r.values() for r in owned if r.table=='transaction_revisions'
        and r.values()['id']==hv['current_revision_id']])
    require(current_revision['number']==hv['number'])
    known={(kinds[r.table],r.values()[keys[r.table]]) for r in owned if r.table in kinds}
    for entry in entries:
        if entry['action']=='create' and entry['record_type'] in kinds.values():
            captured=decode_snapshot(entry['after'])
            if captured and captured.get('transaction_id')==root:
                require((entry['record_type'],entry['record_id']) in known)
    for r in owned:
        if r.table not in kinds: continue
        value=r.values()
        witnesses=[e for e in entries if e['record_type']==kinds[r.table] and e['record_id']==value[keys[r.table]] and e['action']=='create']
        entry=one(witnesses)
        captured=decode_snapshot(entry['after'])
        require(type(captured) is dict)
        if r.table=='posting_line_sources':
            # Pre-co14/co20 snapshots intentionally lack nullable components.
            for key in ('payment_component_id','deposit_component_id'): captured.setdefault(key,None)
        if r.table=='work_billing_allocations' and 'allocation_version' not in captured:
            # co0015 legacy allocations retain the pre-interval audit shape.
            require(value['allocation_version']==1)
            captured.update(allocation_version=1,source_basis_hash=None,denominator_hex=None,spans_json=None)
        expected=document_effects.decoded(value)
        require(captured==expected)
        if value.get('audit_event_id'): require(entry['event_id']==value['audit_event_id'])
    if prior:
        pv=prior.values(); hv=header.values()
        event=one([r.values() for r in rows if r.table=='audit_events' and r.values()['id']==pv['audit_event_id']])
        require(event['actor_id']==hv['voided_by'] and event['reason']==hv['void_reason'])
        entry=one([e for e in entries if e['event_id']==event['id'] and e['record_type']=='transaction' and e['record_id']==hv['id']])
        after=decode_snapshot(entry['after']); before=decode_snapshot(entry['before'])
        require(type(after) is dict and type(before) is dict)
        require(before['status']=='posted' and after['status']=='voided')
        require(all(after[k]==hv[k] for k in ('id','current_revision_id','void_posting_batch_id','voided_at','voided_by','void_reason')))


def history(s, participants):
    result=[]; scope=set(participants)
    for table,kind,children in (
        ('payment_operations','payment_operation',('payment_operation_items',)),
        ('deposit_operations','deposit_operation',('deposit_operation_items','deposit_operation_targets')),
        ('payment_selections','payment_selection',('payment_selection_revisions','payment_selection_items',
            'payment_selection_recoveries','payment_selection_recovery_chunks','payment_selection_recovery_items',
            'payment_selection_recovery_active'))):
        t=c.metadata.tables[table]
        identifiers=[identifier for identifier in s.company.conn.execute(sa.select(t.c.id)).scalars()
            if payment_authority.record_transactions(s.company,kind,identifier) & scope]
        result.extend(read(s,table,'id',identifiers))
        for child in children:
            result.extend(read(s,child,'selection_id' if table=='payment_selections' else 'operation_id',identifiers))
    result.extend(read(s,'work_billing_conversions','destination_transaction_id',participants))
    return result


def validate_history(owned, header):
    from datetime import date
    revisions=sorted((r.values() for r in owned if r.table=='transaction_revisions'),key=lambda r:r['revision_number'])
    require(revisions and revisions[-1]['id']==header.values()['current_revision_id'])
    require([r['revision_number'] for r in revisions]==list(range(1,len(revisions)+1)))
    batches=[r.values() for r in owned if r.table=='posting_batches']
    previous=None
    for rev in revisions:
        require(rev['supersedes_revision_id']==previous)
        require(date.fromisoformat(rev['date']).isoformat()==rev['date'])
        business=one([b for b in batches if b['revision_id']==rev['id'] and b['kind']!='reversal'])
        require(business['effective_date']==rev['date'])
        require(business['kind']==('original' if previous is None else 'replacement'))
        cancellations=[b for b in batches if b['reverses_batch_id']==business['id']]
        if rev is not revisions[-1] or header.values()['status']=='voided':
            cancellation=one(cancellations)
            require(cancellation['revision_id']==rev['id'] and cancellation['effective_date']==business['effective_date'])
            old=[r for r in owned if r.table=='posting_lines' and r.values()['batch_id']==business['id']]
            new=[r for r in owned if r.table=='posting_lines' and r.values()['batch_id']==cancellation['id']]
            inverse_matches(old,new)
        else: require(not cancellations)
        previous=rev['id']


def work_ancestry(s, rows, allocations):
    """Capture exact historical ancestry, including intermediate source owners."""
    while True:
        identities={r.values()['id']:r.values() for r in rows if r.table=='work_line_identities'}
        lines={r.values()['id']:r.values() for r in rows if r.table=='work_lines'}
        missing_lines={v['source_line_id'] for v in identities.values() if v['source_line_id']} - set(lines)
        missing_keys=({v['line_id'] for v in lines.values()} | {v['root_line_id'] for v in identities.values()}) - set(identities)
        if not missing_lines and not missing_keys: break
        added=(*read(s,'work_lines','id',missing_lines),*read(s,'work_line_identities','id',missing_keys))
        require(len(added)==len(missing_lines)+len(missing_keys));rows.extend(added)
    documents={v['document_id'] for v in identities.values()}
    documents.update(v['root_document_id'] for v in identities.values())
    rows.extend(read(s,'work_documents','id',documents))
    rows.extend(read(s,'work_revisions','id',{v['revision_id'] for v in lines.values()}))
    from bookflow.company.work_tax_facts import read_line
    for allocation in allocations:
        value=allocation.values();line=lines[value['source_line_id']]
        require(billing_allocations.captured_line(value)==read_line(line['facts_snapshot']))
        cursor=identities[line['line_id']];seen=set()
        while True:
            require(cursor['id'] not in seen);seen.add(cursor['id'])
            require((cursor['root_document_id'],cursor['root_line_id'])==(value['root_document_id'],value['root_line_id']))
            if cursor['source_line_id'] is None:
                require((cursor['document_id'],cursor['id'])==(value['root_document_id'],value['root_line_id']))
                break
            cursor=identities[lines[cursor['source_line_id']]['line_id']]


def normalized_reason(ctx):
    reason=(ctx.reason or '').strip()
    if not reason: raise BookflowError('E_REASON_REQUIRED')
    if len(reason)>140:
        raise BookflowError('E_VALIDATION',details={'fields':[{'field':'reason','problem':'must be at most 140 characters'}]})
    return reason


# Closed direct annotation inventory from company.records. No unrelated family
# counters, global annotation state, annotation-of-annotation traversal, body
# filesystem or collection-operation claim is made by this preservation slice.
PRESERVATION_TARGETS=(('transaction_revisions','transaction_revision','id'),
    ('document_line_identities','document_line_identity','id'),('document_lines','document_line','id'),
    ('posting_batches','posting_batch','id'),('posting_lines','posting_line','id'),
    ('posting_line_sources','posting_line_source','id'),
    ('payment_profiles','payment_profile','revision_id'),
    ('payment_component_keys','payment_component_key','id'),
    ('payment_components','payment_component','id'),
    ('settlement_line_keys','settlement_line_key','id'))

def preservation(s,intent,owned):
    result=list(read(s,'sequences','name',(intent.family,)))
    targets={('transaction',intent.transaction_id)}
    for table,kind,key in PRESERVATION_TARGETS:
        targets.update((kind,r.values()[key]) for r in owned if r.table==table)
    for table in ('notes','attachment_links'):
        result.extend(r for r in read(s,table,'record_id',{identity for _,identity in targets})
            if (r.values()['record_type'],r.values()['record_id']) in targets)
    result.extend(read(s,'attachments','id',{r.values()['attachment_id'] for r in result if r.table=='attachment_links'}))
    return result
