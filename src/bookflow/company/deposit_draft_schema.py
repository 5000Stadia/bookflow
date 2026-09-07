"""Private durable deposit composition, independent of financial persistence."""
import sqlalchemy as sa

PREFIX = 'deposit_'
NAMES = ('drafts','draft_revisions','draft_row_keys','draft_sources','draft_additional',
         'selections','selection_revisions','selection_sources','draft_consumptions')


def define_tables(metadata, C, T, common):
    tables = {}
    def col(name, kind='id', nullable=False):
        return C(name, {'id':sa.String(26),'text':sa.Text,'int':sa.BigInteger}[kind],
                 'Deposit durable composition '+name.replace('_',' ')+'.', nullable=nullable)
    def ck(sql): return sa.CheckConstraint('COALESCE(('+sql+'),0)')
    def uq(*names): return sa.UniqueConstraint(*names)
    def fk(local, target, remote, deferred=False):
        return sa.ForeignKeyConstraint(local.split(), [target+'.'+n for n in remote.split()],
            deferrable=True if deferred else None, initially='DEFERRED' if deferred else None)
    def owner(local, target, remote, deferred=False): return fk(local,PREFIX+target,remote,deferred)
    def created(): return [col('created_at','text'),col('created_by'),col('created_via','text'),col('audit_event_id'),fk('audit_event_id','audit_events','id')]
    def table(name, columns, *constraints, pk='id'):
        value=T(PREFIX+name,*columns,sa.PrimaryKeyConstraint(*pk.split()),*constraints,
                description='Private immutable deposit composition '+name+'.')
        tables[value.name]=value
        return value
    table('drafts', [*common(),col('state','text'),col('current_revision_id'),col('audit_event_id'),
        col('edit_transaction_id',nullable=True),col('edit_type','text',True),col('baseline_version','int',True),col('baseline_revision_id',nullable=True),
        col('copy_transaction_id',nullable=True),col('copy_type','text',True),col('copy_version','int',True),col('copy_revision_id',nullable=True),
        col('consumed_revision_id',nullable=True),col('consumed_operation_id',nullable=True)],
        ck("typeof(version)='integer' AND version>0"), ck("state IN ('open','consumed','abandoned')"),
        ck("(edit_transaction_id IS NULL AND edit_type IS NULL AND baseline_version IS NULL AND baseline_revision_id IS NULL) OR (edit_transaction_id IS NOT NULL AND edit_type='deposit' AND typeof(baseline_version)='integer' AND baseline_version>0 AND baseline_revision_id IS NOT NULL)"),
        ck("(state='consumed' AND consumed_revision_id IS NOT NULL AND consumed_operation_id IS NOT NULL AND consumed_revision_id=current_revision_id) OR (state<>'consumed' AND consumed_revision_id IS NULL AND consumed_operation_id IS NULL)"),
        ck("(copy_transaction_id IS NULL AND copy_type IS NULL AND copy_version IS NULL AND copy_revision_id IS NULL) OR (edit_transaction_id IS NULL AND copy_transaction_id IS NOT NULL AND copy_type='deposit' AND typeof(copy_version)='integer' AND copy_version>0 AND copy_revision_id IS NOT NULL)"),
        fk('copy_transaction_id copy_type','transactions','id type'),owner('copy_transaction_id copy_revision_id','profiles','transaction_id revision_id'),
        fk('audit_event_id','audit_events','id'),fk('edit_transaction_id edit_type','transactions','id type'),
        owner('edit_transaction_id baseline_revision_id','profiles','transaction_id revision_id'),
        owner('id current_revision_id','draft_revisions','draft_id id',True),
        owner('id consumed_revision_id consumed_operation_id','draft_consumptions','draft_id revision_id operation_id',True))
    for kind, root in (('draft','drafts'),('selection','selections')):
        parent=kind+'_id'
        extra=[col('bank_account_id',nullable=True),col('cashback_account_id',nullable=True),
               fk('bank_account_id','accounts','id'),fk('cashback_account_id','accounts','id')] if kind=='draft' else [
               col('target_draft_id'),col('target_revision_id'),owner('target_draft_id target_revision_id','draft_revisions','draft_id id')]
        table(kind+'_revisions',[col('id'),col(parent),col('version','int'),col('previous_revision_id',nullable=True),
            col('snapshot','text'),col('manifest_hash','text'),col('high_water','int'),*extra,*created()],
            uq(parent,'id'),uq(parent,'version'),owner(parent,root,'id'),
            owner(parent+' previous_revision_id',kind+'_revisions',parent+' id'),
            ck("typeof(version)='integer' AND version>0 AND typeof(high_water)='integer' AND high_water>=0"),
            ck("(version=1 AND previous_revision_id IS NULL) OR (version>1 AND previous_revision_id IS NOT NULL)"),
            ck("json_valid(snapshot) AND json_type(snapshot)='object' AND length(manifest_hash)=64"))
    table('draft_row_keys',[col('id'),col('draft_id'),col('kind','text'),col('ordinal','int'),
        col('edit_transaction_id',nullable=True),col('original_row_id',nullable=True),*created()],
        uq('draft_id','id'),uq('draft_id','ordinal'),uq('draft_id','id','ordinal','kind'),owner('draft_id','drafts','id'),
        owner('edit_transaction_id original_row_id','row_keys','transaction_id id'),
        ck("kind IN ('source','additional') AND typeof(ordinal)='integer' AND ordinal>0"),
        ck('(edit_transaction_id IS NULL)=(original_row_id IS NULL)'))
    for kind in ('draft','selection'):
        parent=kind+'_id'
        key=[col('kind','text'),owner('draft_id row_id ordinal kind','draft_row_keys','draft_id id ordinal kind'),ck("kind='source'")] if kind=='draft' else []
        table(kind+'_sources',[col('revision_id'),col(parent),col('row_id'),col('ordinal','int'),*key,
            col('source_transaction_id'),col('source_type','text'),col('expected_header_version','int'),col('source_revision_id'),
            col('snapshot','text'),col('memo','text',True),col('memo_origin','text'),*created()],
            owner(parent+' revision_id',kind+'_revisions',parent+' id'),
            fk('source_transaction_id source_type','transactions','id type'),
            fk('source_transaction_id source_revision_id','transaction_revisions','transaction_id id'),
            uq('revision_id','source_transaction_id'),uq('revision_id','ordinal'),
            ck("source_type IN ('payment','sales_receipt') AND memo_origin IN ('source','entered')"),
            ck("typeof(ordinal)='integer' AND ordinal>0 AND typeof(expected_header_version)='integer' AND expected_header_version>0"),
            ck("json_valid(snapshot) AND json_type(snapshot)='object'"),pk='revision_id row_id')
    party_cols=[col(k+'_id',nullable=True) for k in ('customer','vendor','employee','other_name')]
    party_fks=[fk(k+'_id',t,'id') for k,t in (('customer','customers'),('vendor','vendors'),('employee','employees'),('other_name','other_names'))]
    branches=['(party_kind IS NULL AND '+ ' AND '.join(k+'_id IS NULL' for k in ('customer','vendor','employee','other_name'))+')']
    for selected in ('customer','vendor','employee','other_name'):
        branches.append("(party_kind='"+selected+"' AND "+' AND '.join(k+'_id IS '+('NOT NULL' if k==selected else 'NULL') for k in ('customer','vendor','employee','other_name'))+')')
    table('draft_additional',[col('revision_id'),col('draft_id'),col('row_id'),col('ordinal','int'),col('kind','text'),
        col('party_kind','text',True),*party_cols,col('account_id',nullable=True),col('class_id',nullable=True),col('payment_method_id',nullable=True),
        col('amount_minor_units','int',True),col('currency','text'),col('snapshot','text'),*created()],
        owner('draft_id revision_id','draft_revisions','draft_id id'),owner('draft_id row_id ordinal kind','draft_row_keys','draft_id id ordinal kind'),
        *party_fks,fk('account_id','accounts','id'),fk('class_id','classes','id'),fk('payment_method_id','payment_methods','id'),
        ck(' OR '.join(branches)),ck("kind='additional' AND typeof(ordinal)='integer' AND ordinal>0"),
        ck("amount_minor_units IS NULL OR typeof(amount_minor_units)='integer'"),ck("length(currency)=3 AND json_valid(snapshot) AND json_type(snapshot)='object'"),
        uq('revision_id','ordinal'),pk='revision_id row_id')
    table('selections',[*common(),col('state','text'),col('target_draft_id'),col('target_revision_id'),col('current_revision_id'),col('accepted_revision_id',nullable=True),col('audit_event_id')],
        ck("typeof(version)='integer' AND version>0 AND state IN ('open','accepted','abandoned')"),
        ck("(state='accepted' AND accepted_revision_id IS NOT NULL) OR (state<>'accepted' AND accepted_revision_id IS NULL)"),
        owner('target_draft_id target_revision_id','draft_revisions','draft_id id'),owner('target_draft_id accepted_revision_id','draft_revisions','draft_id id'),
        owner('id current_revision_id','selection_revisions','selection_id id',True),fk('audit_event_id','audit_events','id'))
    table('draft_consumptions',[col('operation_id'),col('draft_id'),col('revision_id'),col('manifest_hash','text'),*created()],
        uq('draft_id'),uq('draft_id','revision_id','operation_id'),fk('operation_id','deposit_operations','id'),
        owner('draft_id revision_id','draft_revisions','draft_id id'),ck('length(manifest_hash)=64'),pk='operation_id')
    for name, cols in {
        'drafts': [('state','id'),('edit_transaction_id',)],
        'draft_revisions':[('draft_id','version')], 'draft_row_keys':[('original_row_id',)],
        'draft_sources':[('revision_id','ordinal','row_id'),('source_transaction_id','draft_id')],
        'draft_additional':[('revision_id','ordinal','row_id')],
        'selections':[('target_draft_id',),('state','id')],
        'selection_revisions':[('selection_id','version')],
        'selection_sources':[('revision_id','ordinal','row_id'),('source_transaction_id','selection_id')],
    }.items():
        for fields in cols: sa.Index('ix_deposit_'+name+'_'+'_'.join(fields),*(tables[PREFIX+name].c[f] for f in fields))
    return tables


def guards():
    result=[]
    for name in NAMES:
        table=PREFIX+name
        events=('DELETE',) if name in ('drafts','selections') else ('UPDATE','DELETE')
        for event in events:
            result.append(f"CREATE TRIGGER {table}_no_{event.lower()} BEFORE {event} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable deposit composition'); END")
    for name in ('drafts','selections'):
        table=PREFIX+name
        frozen=['id','created_at','created_by','created_via']+(['edit_transaction_id','edit_type','baseline_version','baseline_revision_id','copy_transaction_id','copy_type','copy_version','copy_revision_id'] if name=='drafts' else ['target_draft_id','target_revision_id'])
        invalid=' OR '.join('NEW.'+f+' IS NOT OLD.'+f for f in frozen)
        invalid+=" OR OLD.state<>'open' OR NEW.version<>OLD.version+1"
        result.append(f"CREATE TRIGGER {table}_transition BEFORE UPDATE ON {table} WHEN {invalid} BEGIN SELECT RAISE(ABORT, 'invalid deposit composition transition'); END")
    return tuple(result)
