"""Private reconciliation storage: tables, ownership and transition guards.

Every posting write reaches these tables through `reconciliation_materialization`; nothing
else writes them.
"""
import sqlalchemy as sa

from bookflow.company.reconciliation_models import (
    PRODUCER_ROLES, COMMERCIAL_PRODUCERS, DEPOSIT_PRODUCERS, FUNDING_PRODUCERS)

PREFIX = 'reconciliation_'


def _in(names):
    return '(' + ','.join(repr(name) for name in names) + ')'


# The producer/role and key-shape rules, written from the one declaration in
# `reconciliation_models` instead of retyped here.
PRODUCER_ROLE_RULE = ' OR '.join(
    "(producer=%r AND role IN %s)" % (producer, _in(roles)) for producer, roles in PRODUCER_ROLES.items())
KEY_SHAPE_RULE = (
    "(producer IN %s AND deposit_key_id IS NOT NULL AND commercial_line_id IS NULL) OR "
    "(producer IN %s AND deposit_key_id IS NULL AND commercial_line_id IS NULL) OR "
    "(producer IN %s AND deposit_key_id IS NULL AND commercial_line_id IS NOT NULL)"
    % (_in(DEPOSIT_PRODUCERS), _in(FUNDING_PRODUCERS), _in(COMMERCIAL_PRODUCERS)))
VERSION_SHAPE_RULE = (
    "(producer IN %s AND deposit_link_id=id AND commercial_link_id IS NULL) OR "
    "(producer IN %s AND deposit_link_id IS NULL AND commercial_link_id IS NULL) OR "
    "(producer IN %s AND commercial_link_id=id AND deposit_link_id IS NULL)"
    % (_in(DEPOSIT_PRODUCERS), _in(FUNDING_PRODUCERS), _in(COMMERCIAL_PRODUCERS)))
COMMANDS = tuple('reconcile '+name for name in (
    'opening start','start','draft update','mark','mark-all','accept-current','proposal set','proposal remove',
    'finish','opening finish','leave','resume','cancel','amendment start','amendment apply','undo',
    'attempt begin','attempt upload','attempt seal','attempt apply','attempt abort','report-preset create','report-preset update'))
MUTABLE = frozenset(PREFIX+n for n in ('accounts','effect_heads','active_certificates','current_members','drafts','proposals','attempts','attempt_active','report_presets'))


def define_tables(metadata, C, T, common):
    """Explicit relational ownership; all new indexes belong to these tables."""
    made = {}
    def col(name, kind='id', null=False):
        typ = sa.BigInteger if kind in ('int','positive','count','bool') else sa.Text
        info={'kind':kind}
        value=C(name,typ(),name.replace('_',' ')+'.',nullable=null)
        value.info.update(info)
        return value
    def ids(*names):return [col(n.rstrip('?'),null=n.endswith('?')) for n in names]
    def num(name, kind='positive', null=False):return col(name,kind,null)
    def txt(name,null=False):return col(name,'text',null)
    def obj(name):return col(name,'json')
    def ck(expr):return sa.CheckConstraint('COALESCE(('+expr+'),0)')
    def enum(name,values):return ck(name+' IN ('+','.join(repr(v) for v in values.split('|'))+')')
    def uq(*cols):return sa.UniqueConstraint(*cols)
    def fk(cols,target,remote=None,defer=False):
        if isinstance(cols,str):cols=cols.split()
        if remote is None:remote=cols
        elif isinstance(remote,str):remote=remote.split()
        return sa.ForeignKeyConstraint(cols,[target+'.'+r for r in remote],deferrable=True if defer else None,initially='DEFERRED' if defer else None)
    def own(cols,target,remote=None,defer=False):return fk(cols,PREFIX+target,remote,defer)
    def created():return [txt('created_at'),*ids('created_by'),txt('created_via'),*ids('audit_event_id'),fk('audit_event_id','audit_events','id')]
    def table(name,columns,*constraints,pk='id'):
        name=PREFIX+name
        constraints += tuple(c for c in columns if not isinstance(c, sa.Column))
        columns = [c for c in columns if isinstance(c, sa.Column)]
        for c in columns:
            k=c.info['kind'];n=c.name
            if k in ('int','positive','count','bool'):
                rule=f"typeof({n})='integer'"
                if k=='positive':rule+=f' AND {n}>0'
                if k=='count':rule+=f' AND {n}>=0'
                if k=='bool':rule+=f' AND {n} IN (0,1)'
            elif k=='json':rule=f"CASE WHEN json_valid({n}) THEN json_type({n})='object' ELSE 0 END"
            elif k=='id':rule=f"typeof({n})='text' AND length({n})=26 AND {n} NOT GLOB '*[^0-9A-HJKMNP-TV-Z]*'"
            else:rule=f"typeof({n})='text'"
            if c.nullable:rule=f'{n} IS NULL OR ({rule})'
            constraints+= (ck(rule),)
        t=T(name,*columns,sa.PrimaryKeyConstraint(*pk.split()),*constraints,description='Private nonactivating reconciliation '+name[len(PREFIX):].replace('_',' ')+'.')
        made[name]=t;return t
    table('keys',ids('id')+[txt('producer'),*ids('transaction_id'),txt('role'),*ids('commercial_line_id?','deposit_key_id?')],
        uq('id','transaction_id','producer'),fk('transaction_id producer','transactions','id type'),
        fk('transaction_id commercial_line_id','document_line_identities','transaction_id id'),fk('transaction_id deposit_key_id','bank_effect_keys','transaction_id id'),
        ck(PRODUCER_ROLE_RULE), ck(KEY_SHAPE_RULE))
    sa.Index('uq_reconciliation_commercial_key',made[PREFIX+'keys'].c.producer,made[PREFIX+'keys'].c.transaction_id,made[PREFIX+'keys'].c.role,made[PREFIX+'keys'].c.commercial_line_id,unique=True,sqlite_where=sa.text('commercial_line_id IS NOT NULL'))
    sa.Index('uq_reconciliation_deposit_key',made[PREFIX+'keys'].c.deposit_key_id,unique=True,sqlite_where=sa.text('deposit_key_id IS NOT NULL'))
    # A funding document is its own component, so the pair the commercial index guards does not
    # exist for it; what must be unique is the document and the role it funds.
    sa.Index('uq_reconciliation_funding_key',made[PREFIX+'keys'].c.producer,made[PREFIX+'keys'].c.transaction_id,made[PREFIX+'keys'].c.role,unique=True,sqlite_where=sa.text('commercial_line_id IS NULL AND deposit_key_id IS NULL'))
    table('effect_versions',ids('id','key_id','transaction_id')+[txt('producer'),txt('source_version'),*ids('revision_id','business_batch_id','transition_batch_id?','source_audit_event_id','account_id'),txt('account_type'),txt('currency'),col('effective_date','date'),num('signed_debit','int'),num('active','bool'),obj('movement_snapshot'),obj('display_snapshot'),obj('provenance_snapshot'),num('format_version'),*ids('commercial_link_id?','deposit_link_id?')],
        uq('key_id','source_version'),uq('key_id','id'),uq('id','key_id','account_id'),uq('id','transaction_id'),uq('id','transaction_id','producer'),
        own('key_id transaction_id producer','keys','id transaction_id producer'),fk('transaction_id revision_id','transaction_revisions','transaction_id id'),
        fk('transaction_id business_batch_id','posting_batches','transaction_id id'),fk('transaction_id transition_batch_id','posting_batches','transaction_id id'),
        fk('source_audit_event_id','audit_events','id'),fk('account_id','accounts','id'),enum('account_type','bank|credit_card'),ck('format_version=1'),
        ck('(active=1 AND signed_debit<>0) OR (active=0 AND signed_debit=0)'),
        ck(VERSION_SHAPE_RULE),
        own('commercial_link_id','commercial_versions','id',True),own('deposit_link_id','deposit_versions','id',True))
    table('commercial_versions',ids('id','transaction_id')+[txt('producer'),*ids('line_id')],
        enum('producer','|'.join(COMMERCIAL_PRODUCERS)),own('id transaction_id producer','effect_versions','id transaction_id producer',True),fk('transaction_id line_id','document_line_identities','transaction_id id'))
    table('deposit_versions',ids('id','transaction_id')+[txt('producer'),*ids('bank_key_id','bank_version_id')],
        ck("producer='deposit'"),own('id transaction_id producer','effect_versions','id transaction_id producer',True),
        fk('transaction_id bank_key_id','bank_effect_keys','transaction_id id'),fk('bank_key_id bank_version_id','bank_effect_versions','key_id id'),uq('bank_version_id'))
    for suffix,field,target in [('legs','posting_line_id','posting_lines'),('sources','source_id','posting_line_sources')]:
        table('effect_'+suffix,ids('version_id','transaction_id',field),own('version_id transaction_id','effect_versions','id transaction_id'),fk(['transaction_id',field],target,['transaction_id','id']),uq(field),pk='version_id '+field)
    table('effect_heads',ids('key_id','version_id'),own('key_id version_id','effect_versions','key_id id'),pk='key_id')
    table('accounts',ids('account_id')+[txt('currency'),txt('convention'),num('version'),*ids('opening_id?','head_certificate_id?','last_event_id?')],
        fk('account_id','accounts','id'),enum('convention','bank|card_debt'),own('opening_id account_id','openings','id account_id',True),own('head_certificate_id account_id','certificates','id account_id',True),own('last_event_id','events','id',True),pk='account_id')
    table('openings',ids('id','account_id')+[num('generation'),col('opening_date','date'),num('balance','int'),txt('currency'),*ids('predecessor_opening_id?','origin_draft_revision_id'),obj('evidence_snapshot'),obj('authorized_source_snapshot'),*created()],
        uq('id','account_id'),uq('account_id','generation'),fk('account_id','accounts','id'),own('predecessor_opening_id account_id','openings','id account_id'),own('origin_draft_revision_id account_id','draft_revisions','id account_id'))
    table('opening_members',ids('opening_id','account_id','key_id','version_id')+[txt('classification'),num('ordinal','count')],
        own('opening_id account_id','openings','id account_id'),own('version_id key_id account_id','effect_versions','id key_id account_id'),enum('classification','covered|outstanding'),uq('opening_id','ordinal'),pk='opening_id key_id')
    table('certificates',ids('id','account_id')+[num('generation'),col('statement_date','date'),*ids('opening_id','previous_certificate_id?','supersedes_certificate_id?','origin_draft_revision_id'),
        *[num(n,'int') for n in ('beginning_balance','ending_balance','selected_sum','original_difference','final_difference','positive_sum','negative_sum')],num('positive_count','count'),num('negative_count','count'),txt('currency'),txt('convention'),obj('captured_source_snapshot'),obj('issuer_snapshot'),*created()],
        uq('id','account_id'),uq('account_id','generation'),fk('account_id','accounts','id'),own('opening_id account_id','openings','id account_id'),own('previous_certificate_id account_id','certificates','id account_id'),own('supersedes_certificate_id account_id','certificates','id account_id'),own('origin_draft_revision_id account_id','draft_revisions','id account_id'),ck('final_difference=0 AND positive_sum>=0 AND negative_sum<=0'),enum('convention','bank|card_debt'))
    table('certificate_members',ids('certificate_id','account_id','key_id','version_id')+[txt('classification'),num('eligible_at_cutoff','bool'),num('ordinal','count')],
        own('certificate_id account_id','certificates','id account_id'),own('version_id key_id account_id','effect_versions','id key_id account_id'),enum('classification','opening_covered|prior_cleared|selected|outstanding'),uq('certificate_id','ordinal'),pk='certificate_id key_id')
    table('active_certificates',ids('account_id')+[col('statement_date','date'),*ids('certificate_id')],own('certificate_id account_id','certificates','id account_id'),uq('certificate_id'),pk='account_id statement_date')
    table('claims',ids('id','account_id','key_id','version_id','opening_id?','certificate_id?','event_id'),
        own('version_id key_id account_id','effect_versions','id key_id account_id'),own('opening_id account_id','openings','id account_id'),own('certificate_id account_id','certificates','id account_id'),own('event_id','events','id'),uq('id','key_id'),ck('(opening_id IS NOT NULL)+(certificate_id IS NOT NULL)=1'))
    table('releases',ids('id','claim_id','event_id'),own('claim_id','claims','id'),own('event_id','events','id'),uq('claim_id'))
    table('current_members',ids('key_id','claim_id'),own('claim_id key_id','claims','id key_id'),uq('claim_id'),pk='key_id')
    table('events',ids('id','operation_id','audit_event_id','actor_id','principal_id?')+[txt('interface'),txt('recorded_at'),txt('reason',True),txt('kind'),num('schema_version')],
        uq('operation_id'),uq('audit_event_id'),own('operation_id','operations','id',True),fk('audit_event_id','audit_events','id'),ck('schema_version=1'),
        enum('kind','draft_change|opening_certify|finish|amend|invalidate|undo|proposal_change|bulk_stage|bulk_terminal|report_preset_change'))
    table('event_accounts',ids('event_id','account_id')+[num('before_chain_version','count'),num('after_chain_version'),*ids('before_opening_id?','after_opening_id?','before_head_id?','after_head_id?')],
        own('event_id','events','id'),fk('account_id','accounts','id'),
        *[own(n+' account_id',target,'id account_id',True) for n,target in [('before_opening_id','openings'),('after_opening_id','openings'),('before_head_id','certificates'),('after_head_id','certificates')]],
        uq('account_id','after_chain_version'),ck('after_chain_version=before_chain_version+1'),pk='event_id account_id')
    table('event_effects',ids('event_id','key_id','old_version_id?','new_version_id','source_audit_event_id'),
        own('event_id','events','id'),own('key_id old_version_id','effect_versions','key_id id'),own('key_id new_version_id','effect_versions','key_id id'),fk('source_audit_event_id','audit_events','id'),pk='event_id key_id')
    table('drafts',ids('id','account_id')+[txt('kind'),num('version'),*ids('current_revision_id'),txt('state'),*ids('terminal_operation_id?'),*created()],
        uq('id','account_id'),fk('account_id','accounts','id'),own('current_revision_id id account_id','draft_revisions','id draft_id account_id',True),own('terminal_operation_id','operations','id',True),enum('kind','opening|statement|amendment'),enum('state','open|consumed|canceled'),ck("(state='open' AND terminal_operation_id IS NULL) OR (state<>'open' AND terminal_operation_id IS NOT NULL)"))
    table('draft_revisions',ids('id','draft_id','account_id')+[num('revision_number'),*ids('previous_revision_id?'),obj('header_snapshot'),num('base_chain_version','count'),*ids('base_opening_id?','base_head_id?','repair_of_opening_id?','repair_of_certificate_id?'),*created()],
        uq('draft_id','id'),uq('id','account_id'),uq('id','draft_id','account_id'),uq('draft_id','revision_number'),uq('draft_id','audit_event_id'),own('draft_id account_id','drafts','id account_id',True),own('draft_id previous_revision_id','draft_revisions','draft_id id'),
        *[own(n+' account_id',target,'id account_id') for n,target in [('base_opening_id','openings'),('base_head_id','certificates'),('repair_of_opening_id','openings'),('repair_of_certificate_id','certificates')]])
    table('draft_members',ids('revision_id','draft_id','account_id','key_id','version_id')+[txt('action'),num('ordinal','count')],
        own('revision_id draft_id account_id','draft_revisions','id draft_id account_id'),own('version_id key_id account_id','effect_versions','id key_id account_id'),enum('action','mark|covered|outstanding'),uq('revision_id','ordinal'),pk='revision_id key_id')
    table('proposals',ids('id','draft_id')+[txt('role'),*ids('current_revision_id'),num('version')],
        uq('id','draft_id'),own('draft_id','drafts','id'),enum('role','charge|earned_credit|force_adjustment'),own('id current_revision_id','proposal_revisions','proposal_id id',True))
    table('proposal_revisions',ids('id','proposal_id','draft_id')+[num('revision_number'),*ids('previous_revision_id?','offset_account_id','class_id?'),col('date','date'),num('amount_minor_units','int'),txt('currency'),txt('memo',True),txt('number',True),txt('reason',True),obj('input_snapshot'),obj('expected_account_facts'),*created()],
        uq('proposal_id','id'),uq('proposal_id','revision_number'),uq('proposal_id','audit_event_id'),uq('id','proposal_id','draft_id'),own('proposal_id draft_id','proposals','id draft_id',True),own('proposal_id previous_revision_id','proposal_revisions','proposal_id id'),fk('offset_account_id','accounts','id'),fk('class_id','classes','id'),ck('amount_minor_units<>0'))
    table('proposal_consumptions',ids('proposal_id','proposal_revision_id','operation_id','journal_transaction_id','journal_revision_id')+[txt('journal_type')],
        own('proposal_id proposal_revision_id','proposal_revisions','proposal_id id'),own('operation_id','operations','id'),fk('journal_transaction_id journal_type','transactions','id type'),fk('journal_transaction_id journal_revision_id','transaction_revisions','transaction_id id'),ck("journal_type='journal_entry'"),uq('journal_transaction_id'),pk='proposal_id')
    table('draft_proposals',ids('draft_revision_id','draft_id','proposal_id','proposal_revision_id'),own('draft_id draft_revision_id','draft_revisions','draft_id id'),own('proposal_revision_id proposal_id draft_id','proposal_revisions','id proposal_id draft_id'),pk='draft_revision_id proposal_id')
    table('operations',ids('id')+[txt('operation_key'),txt('command'),num('request_schema_version'),obj('original_request_snapshot'),txt('canonical_intent_hash'),num('effect_schema_version'),obj('original_effect_snapshot'),*created()],
        uq('operation_key'),enum('command','|'.join(COMMANDS)),ck('request_schema_version=1 AND effect_schema_version=1'),ck('length(operation_key) BETWEEN 1 AND 128'),ck("length(canonical_intent_hash)=64 AND canonical_intent_hash NOT GLOB '*[^0-9a-f]*'"))
    table('operation_items',ids('operation_id')+[txt('kind'),num('ordinal','count'),obj('facts_snapshot')],own('operation_id','operations','id'),enum('kind','request|effects|targets|generated'),pk='operation_id kind ordinal')
    for kind,target,field in [('transactions','transactions','transaction_id'),('accounts','accounts','account_id'),('drafts',PREFIX+'drafts','draft_id'),('openings',PREFIX+'openings','opening_id'),('certificates',PREFIX+'certificates','certificate_id')]:
        table('operation_'+kind,ids('operation_id',field),own('operation_id','operations','id'),fk(field,target,'id' if kind!='accounts' else 'id'),pk='operation_id '+field)
    table('attempts',ids('id','draft_id','base_revision_id')+[num('version'),txt('state'),num('declared_count','count'),txt('intent_hash'),txt('attempt_generation'),*created()],
        uq('draft_id','id'),own('draft_id base_revision_id','draft_revisions','draft_id id'),enum('state','uploading|sealed|applied|aborted|superseded'),ck("length(intent_hash)=64 AND intent_hash NOT GLOB '*[^0-9a-f]*'"))
    table('attempt_chunks',ids('attempt_id')+[num('chunk_index','count'),txt('request_hash'),obj('original_chunk'),obj('receipt')],own('attempt_id','attempts','id'),ck("length(request_hash)=64 AND request_hash NOT GLOB '*[^0-9a-f]*'"),pk='attempt_id chunk_index')
    pointers=[num(n+'_ordinal','count',True) for n in ('member','seed','certificate','proposal')]
    table('attempt_items',ids('attempt_id')+[num('ordinal','count'),txt('kind'),obj('payload'),*pointers],own('attempt_id','attempts','id'),enum('kind','member|seed|certificate|proposal'),
        ck(' + '.join('('+n+'_ordinal IS NOT NULL)' for n in ('member','seed','certificate','proposal'))+'=1'),
        *[ck(f"(kind='{n}' AND {n}_ordinal=ordinal) OR (kind<>'{n}' AND {n}_ordinal IS NULL)") for n in ('member','seed','certificate','proposal')],
        *[own('attempt_id '+n+'_ordinal','attempt_'+n+'s','attempt_id ordinal',True) for n in ('member','seed','certificate','proposal')],pk='attempt_id ordinal')
    for name,columns,constraints in [
        ('member',ids('draft_id','account_id','key_id','saved_version_id','current_version_id?')+[txt('action')],[own('draft_id account_id','drafts','id account_id'),own('saved_version_id key_id account_id','effect_versions','id key_id account_id'),own('current_version_id key_id account_id','effect_versions','id key_id account_id'),enum('action','mark|unmark|covered|outstanding|accept_current')]),
        ('seed',ids('account_id','opening_id?','certificate_id?')+[txt('kind'),col('date','date',True)],[fk('account_id','accounts','id'),own('opening_id account_id','openings','id account_id'),own('certificate_id account_id','certificates','id account_id'),enum('kind','opening|statement|insert'),ck("(kind='opening' AND opening_id IS NOT NULL AND certificate_id IS NULL) OR (kind='statement' AND certificate_id IS NOT NULL AND opening_id IS NULL) OR (kind='insert' AND opening_id IS NULL AND certificate_id IS NULL AND date IS NOT NULL)")]),
        ('certificate',ids('account_id','certificate_id?','predecessor_id?','replacement_draft_revision_id?')+[txt('mode')],[fk('account_id','accounts','id'),own('certificate_id account_id','certificates','id account_id'),own('predecessor_id account_id','certificates','id account_id'),own('replacement_draft_revision_id account_id','draft_revisions','id account_id'),enum('mode','replace|invalidate|insert'),ck("(mode='invalidate' AND certificate_id IS NOT NULL AND replacement_draft_revision_id IS NULL) OR (mode='replace' AND certificate_id IS NOT NULL AND replacement_draft_revision_id IS NOT NULL) OR (mode='insert' AND certificate_id IS NULL AND replacement_draft_revision_id IS NOT NULL)")]),
        ('proposal',ids('proposal_id','proposal_revision_id','draft_id'),[own('proposal_revision_id proposal_id draft_id','proposal_revisions','id proposal_id draft_id')]),
    ]:
        table('attempt_'+name+'s',ids('attempt_id')+[num('ordinal','count'),*columns],own('attempt_id ordinal','attempt_items','attempt_id ordinal',True),*constraints,pk='attempt_id ordinal')
    table('attempt_active',ids('draft_id','attempt_id'),own('draft_id attempt_id','attempts','draft_id id'),uq('attempt_id'),pk='draft_id')
    table('opening_evidence',ids('opening_id')+[num('ordinal','count'),txt('kind'),*ids('transaction_id','attachment_id?','attachment_link_id?'),obj('captured_evidence')],own('opening_id','openings','id'),fk('transaction_id','transactions','id'),fk('attachment_id','attachments','id'),fk('attachment_link_id','attachment_links','id'),
        enum('kind','transaction|transaction_attachment'),ck("(kind='transaction' AND transaction_id IS NOT NULL AND attachment_id IS NULL AND attachment_link_id IS NULL) OR (kind='transaction_attachment' AND transaction_id IS NOT NULL AND attachment_id IS NOT NULL AND attachment_link_id IS NOT NULL)"),pk='opening_id ordinal')
    table('report_presets',ids('id')+[num('version'),txt('name'),*ids('account_id?'),obj('parameters_snapshot'),*created()],fk('account_id','accounts','id'),own('id version','report_preset_revisions','preset_id version',True))
    table('report_preset_revisions',ids('preset_id')+[num('version'),obj('parameters_snapshot'),*ids('audit_event_id')],uq('preset_id','audit_event_id'),own('preset_id','report_presets','id',True),fk('audit_event_id','audit_events','id'),pk='preset_id version')
    # Fixed, deterministically named per-column FK access paths, only on new tables.
    for name,t in made.items():
        for field in sorted({fk.parent.name for fk in t.foreign_keys}):
            if not any(list(i.columns)[0].name==field for i in t.indexes) and not (len(t.primary_key.columns) and list(t.primary_key.columns)[0].name==field):
                sa.Index('ix_'+name+'_'+field,t.c[field])
    return made


def guards(names):
    """Only new-table triggers; the ledger's own tables keep theirs."""
    result = []
    def trigger(table, suffix, event, invalid):
        result.append(f"CREATE TRIGGER reconciliation_{table}_{suffix} BEFORE {event} ON reconciliation_{table} WHEN {invalid} BEGIN SELECT RAISE(ABORT, 'invalid reconciliation storage transition'); END")
    for name in names:
        short = name.removeprefix(PREFIX)
        if name not in MUTABLE:
            for verb in ('UPDATE', 'DELETE'):
                trigger(short, 'no_'+verb.lower(), verb, '1')
        elif short not in ('active_certificates', 'current_members', 'attempt_active'):
            trigger(short, 'no_delete', 'DELETE', '1')
    trigger('event_effects','causality','INSERT',"EXISTS (SELECT 1 FROM reconciliation_effect_versions v WHERE v.id=NEW.new_version_id AND v.source_audit_event_id<>NEW.source_audit_event_id) OR EXISTS (SELECT 1 FROM reconciliation_events e WHERE e.id=NEW.event_id AND e.audit_event_id<>NEW.source_audit_event_id)")
    trigger('keys','deposit_role','INSERT',"NEW.producer='deposit' AND NOT EXISTS (SELECT 1 FROM bank_effect_keys k WHERE k.id=NEW.deposit_key_id AND k.transaction_id=NEW.transaction_id AND k.role=NEW.role)")
    head = "NOT EXISTS (SELECT 1 FROM reconciliation_effect_versions v JOIN reconciliation_keys k ON k.id=v.key_id JOIN transactions t ON t.id=v.transaction_id WHERE v.id=NEW.version_id AND v.key_id=NEW.key_id AND ((v.producer='deposit' AND EXISTS (SELECT 1 FROM bank_effect_current b WHERE b.key_id=k.deposit_key_id AND b.version_id=v.source_version)) OR (v.producer<>'deposit' AND t.current_revision_id=v.revision_id AND ((t.status='voided' AND v.transition_batch_id=t.void_posting_batch_id) OR (t.status<>'voided' AND v.transition_batch_id IS NULL)))))"
    trigger('effect_heads','current','INSERT',head)
    trigger('effect_heads','current_update','UPDATE','NEW.key_id<>OLD.key_id OR '+head)
    trigger('commercial_versions','owner','INSERT',"NOT EXISTS (SELECT 1 FROM reconciliation_effect_versions v JOIN reconciliation_keys k ON k.id=v.key_id WHERE v.id=NEW.id AND k.commercial_line_id=NEW.line_id AND v.producer=NEW.producer AND v.source_version=COALESCE(v.transition_batch_id,v.business_batch_id)||':'||k.role||':'||k.commercial_line_id)")
    trigger('deposit_versions','owner','INSERT',"NOT EXISTS (SELECT 1 FROM reconciliation_effect_versions v JOIN reconciliation_keys k ON k.id=v.key_id JOIN bank_effect_versions b ON b.id=NEW.bank_version_id JOIN bank_effect_keys bk ON bk.id=b.key_id WHERE v.id=NEW.id AND k.deposit_key_id=NEW.bank_key_id AND bk.role=k.role AND v.source_version=b.id AND v.revision_id=b.revision_id AND v.business_batch_id=b.batch_id AND v.account_id=b.account_id AND v.signed_debit=b.signed_debit AND v.active=b.active AND v.currency=b.currency AND v.effective_date=b.effective_date AND v.source_audit_event_id=b.audit_event_id)")
    # A funding version has no subtype row to own it, so the version itself carries the proof
    # that its source identity names the document and the role, exactly as the commercial owner
    # trigger proves the line.
    trigger('effect_versions','funding_anchor','INSERT',"NEW.producer IN "+_in(FUNDING_PRODUCERS)+" AND NOT EXISTS (SELECT 1 FROM reconciliation_keys k WHERE k.id=NEW.key_id AND k.commercial_line_id IS NULL AND k.deposit_key_id IS NULL AND NEW.source_version=COALESCE(NEW.transition_batch_id,NEW.business_batch_id)||':'||k.role||':'||NEW.transaction_id)")
    trigger('effect_versions','business_anchor','INSERT',"NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.business_batch_id AND b.transaction_id=NEW.transaction_id AND b.revision_id=NEW.revision_id AND b.kind IN ('original','replacement')) OR (NEW.transition_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM posting_batches b WHERE b.id=NEW.transition_batch_id AND b.transaction_id=NEW.transaction_id AND b.kind='reversal' AND b.audit_event_id=NEW.source_audit_event_id))")
    trigger('effect_legs','owner','INSERT',"NOT EXISTS (SELECT 1 FROM reconciliation_effect_versions v JOIN posting_lines l ON l.id=NEW.posting_line_id WHERE v.id=NEW.version_id AND v.active=1 AND v.business_batch_id=l.batch_id AND v.transaction_id=l.transaction_id AND v.account_id=l.account_id AND v.currency=l.currency)")
    trigger('effect_sources','owner','INSERT',"NOT EXISTS (SELECT 1 FROM posting_line_sources s JOIN reconciliation_effect_legs l ON l.posting_line_id=s.posting_line_id AND l.version_id=NEW.version_id WHERE s.id=NEW.source_id AND s.transaction_id=NEW.transaction_id)")
    trigger('opening_evidence','attachment_owner','INSERT',"NEW.attachment_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM attachment_links l WHERE l.id=NEW.attachment_link_id AND l.attachment_id=NEW.attachment_id AND l.record_type='transaction' AND l.record_id=NEW.transaction_id)")
    trigger('claims','member','INSERT',"NOT EXISTS (SELECT 1 FROM reconciliation_opening_members m WHERE m.opening_id=NEW.opening_id AND m.key_id=NEW.key_id AND m.version_id=NEW.version_id AND m.classification='covered') AND NOT EXISTS (SELECT 1 FROM reconciliation_certificate_members m WHERE m.certificate_id=NEW.certificate_id AND m.key_id=NEW.key_id AND m.version_id=NEW.version_id AND m.classification='selected')")
    owner = "EXISTS (SELECT 1 FROM reconciliation_accounts a WHERE a.account_id=c.account_id AND a.opening_id=c.opening_id) OR EXISTS (SELECT 1 FROM reconciliation_active_certificates a WHERE a.account_id=c.account_id AND a.certificate_id=c.certificate_id)"
    trigger('current_members','claim','INSERT',f"NOT EXISTS (SELECT 1 FROM reconciliation_claims c WHERE c.id=NEW.claim_id AND c.key_id=NEW.key_id AND NOT EXISTS (SELECT 1 FROM reconciliation_releases r WHERE r.claim_id=c.id) AND ({owner}))")
    trigger('current_members','release','DELETE',"NOT EXISTS (SELECT 1 FROM reconciliation_releases r WHERE r.claim_id=OLD.claim_id)")
    trigger('current_members','no_update','UPDATE','1')
    for short in ('drafts','proposals','report_presets'):
        revision = {'drafts':('draft_revisions','draft_id','revision_number'), 'proposals':('proposal_revisions','proposal_id','revision_number'), 'report_presets':('report_preset_revisions','preset_id','version')}[short]
        rt, parent, version = revision
        predicate = f"NOT EXISTS (SELECT 1 FROM reconciliation_{rt} r JOIN reconciliation_events e ON e.audit_event_id=r.audit_event_id WHERE r.{parent}=NEW.id AND r.{version}=NEW.version"
        if short != 'report_presets': predicate += ' AND r.id=NEW.current_revision_id'
        predicate += ')'
        trigger(short,'revision','INSERT',predicate)
        trigger(short,'revision_update','UPDATE',f"NEW.id<>OLD.id OR NEW.version<>OLD.version+1 OR {predicate}")
    trigger('drafts','lifecycle','UPDATE',"OLD.state<>'open' OR NEW.account_id<>OLD.account_id OR NEW.kind<>OLD.kind")
    trigger('proposals','consumed','UPDATE',"NEW.draft_id<>OLD.draft_id OR NEW.role<>OLD.role OR EXISTS (SELECT 1 FROM reconciliation_proposal_consumptions c WHERE c.proposal_id=OLD.id)")
    trigger('proposal_revisions','amount','INSERT',"EXISTS (SELECT 1 FROM reconciliation_proposals p WHERE p.id=NEW.proposal_id AND p.role<>'force_adjustment' AND NEW.amount_minor_units<=0)")
    # Chain movement has an immutable before/after event; writer supplies the event first.
    event_after = "e.account_id=NEW.account_id AND e.after_chain_version=NEW.version AND e.after_opening_id IS NEW.opening_id AND e.after_head_id IS NEW.head_certificate_id AND e.event_id=NEW.last_event_id"
    trigger('accounts','event','INSERT',f"NOT EXISTS (SELECT 1 FROM reconciliation_event_accounts e WHERE {event_after} AND e.before_chain_version=0 AND e.before_opening_id IS NULL AND e.before_head_id IS NULL)")
    trigger('accounts','event_update','UPDATE',f"NEW.account_id<>OLD.account_id OR NEW.currency<>OLD.currency OR NEW.convention<>OLD.convention OR NEW.version<>OLD.version+1 OR NOT EXISTS (SELECT 1 FROM reconciliation_event_accounts e WHERE {event_after} AND e.before_chain_version=OLD.version AND e.before_opening_id IS OLD.opening_id AND e.before_head_id IS OLD.head_certificate_id)")
    trigger('active_certificates','no_update','UPDATE','1')
    trigger('active_certificates','insert_owner','INSERT',"NOT EXISTS (SELECT 1 FROM reconciliation_certificates c WHERE c.id=NEW.certificate_id AND c.account_id=NEW.account_id AND c.statement_date=NEW.statement_date)")
    trigger('active_certificates','release','DELETE',"EXISTS (SELECT 1 FROM reconciliation_claims c WHERE c.certificate_id=OLD.certificate_id AND NOT EXISTS (SELECT 1 FROM reconciliation_releases r WHERE r.claim_id=c.id)) OR NOT EXISTS (SELECT 1 FROM reconciliation_event_accounts e JOIN reconciliation_events v ON v.id=e.event_id WHERE e.account_id=OLD.account_id AND v.kind IN ('amend','invalidate','undo'))")
    attempt_event = "NOT EXISTS (SELECT 1 FROM reconciliation_events e JOIN reconciliation_operations o ON o.id=e.operation_id JOIN reconciliation_operation_drafts d ON d.operation_id=o.id WHERE e.audit_event_id=NEW.audit_event_id AND d.draft_id=NEW.draft_id AND ((NEW.state IN ('uploading','sealed') AND e.kind='bulk_stage') OR (NEW.state IN ('applied','aborted','superseded') AND e.kind='bulk_terminal')))"
    trigger('attempts','event','INSERT',attempt_event)
    trigger('attempts','event_update','UPDATE','NEW.audit_event_id=OLD.audit_event_id OR '+attempt_event)
    trigger('attempts','initial','INSERT',"NEW.state<>'uploading' OR NEW.version<>1")
    trigger('attempts','transition','UPDATE',"NEW.id<>OLD.id OR NEW.draft_id<>OLD.draft_id OR NEW.base_revision_id<>OLD.base_revision_id OR NEW.declared_count<>OLD.declared_count OR NEW.intent_hash<>OLD.intent_hash OR NEW.attempt_generation<>OLD.attempt_generation OR NEW.version<>OLD.version+1 OR NOT ((OLD.state='uploading' AND NEW.state IN ('uploading','sealed','aborted','superseded')) OR (OLD.state='sealed' AND NEW.state IN ('applied','aborted','superseded')))")
    for short in ('attempt_chunks','attempt_items','attempt_members','attempt_seeds','attempt_certificates','attempt_proposals'):
        trigger(short,'upload_state','INSERT',"NOT EXISTS (SELECT 1 FROM reconciliation_attempts a WHERE a.id=NEW.attempt_id AND a.state='uploading')")
    trigger('attempt_active','state','INSERT',"NOT EXISTS (SELECT 1 FROM reconciliation_attempts a WHERE a.id=NEW.attempt_id AND a.draft_id=NEW.draft_id AND a.state IN ('uploading','sealed'))")
    trigger('attempt_active','no_update','UPDATE','1')
    trigger('attempt_active','terminal','DELETE',"NOT EXISTS (SELECT 1 FROM reconciliation_attempts a WHERE a.id=OLD.attempt_id AND a.state IN ('applied','aborted','superseded'))")
    immutable = {
        'drafts':('id','account_id','kind','created_at','created_by','created_via','audit_event_id'),
        'proposals':('id','draft_id','role'),
        'attempts':('id','draft_id','base_revision_id','declared_count','intent_hash','attempt_generation','created_at','created_by','created_via'),
        'report_presets':('id','created_at','created_by','created_via','audit_event_id'),
    }
    for short, fields in immutable.items():
        trigger(short,'immutable_fields','UPDATE',' OR '.join('NEW.'+f+' IS NOT OLD.'+f for f in fields))
    return result
