"""Document delivery, increment 1: messages and the outbox (co0105, R112; design/document-delivery.md §2, §3, §8).

A **message** is one email to a customer carrying one or more documents as PDFs. It is composed from a
**template**, its attachments are rendered and frozen when it is drafted, and it waits in the outbox. Nothing here
sends: this increment builds no transport, so a message is drafted, edited, released (``ready``), exported as a
``.eml`` file, marked sent by a person, or cancelled.

- ``delivery_templates``: a subject and a plain-text body per kind, with a closed list of merge fields. Exactly one
  active default per kind (a partial unique index); a company starts with one seeded default per kind.
- ``delivery_messages``: the message and its current state. Never deleted (a guard).
- ``delivery_message_documents``: each attached file: the source document, its revision when drafted, the file
  name, size and SHA-256 of the frozen bytes. Fixed, except that a draft or held message's file may be refreshed
  (``delivery update --refresh``; the event keeps the old revision and hash).
- ``delivery_files``: immutable, content-addressed. A file no standing message carries (only cancelled messages that
  were never exported) is pruned; the hash stays on the message's rows and in its history. The frozen bytes, one row per SHA-256, so identical files share
  one. (The attachment store's bodies live behind upload transfers and collection; a frozen PDF is the company's own
  output, so it lives in the company file, travels with its backups, and is never collected.)
- ``delivery_events``: append-only. Every state change and every act on a message, with who and through what.
"""
import sqlalchemy as sa

KINDS = ('invoice', 'sales-receipt', 'estimate', 'statement', 'notice')
STATUSES = ('draft', 'ready', 'sending', 'sent', 'held', 'failed', 'cancelled')
LIVE = ('draft', 'ready', 'sending', 'held', 'failed')
HOLD_REASONS = ('document_changed', 'document_voided', 'no_recipient', 'template_error', 'outcome_unknown')
SENT_VIA = ('connection', 'manual')
SENT_METHODS = ('mail-program', 'webmail', 'other')
ROLES = ('document', 'attached-file')
EVENTS = ('drafted', 'edited', 'released', 'approved', 'claimed', 'accepted', 'attempt_failed', 'held', 'retried',
          'cancelled', 'marked_sent', 'exported', 'delivered', 'bounced')
SEQUENCE = 'delivery'
FIRST_NUMBER = 1001
PREFIX = 'M-'


def define_tables(metadata, C, T, common):
    def quoted(values):
        return ', '.join(f"'{value}'" for value in values)

    def ident(name, description, target=None, *, nullable=False, primary_key=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints, nullable=nullable, primary_key=primary_key)

    def text(name, description, size=None, *, nullable=True):
        return C(name, sa.String(size) if size else sa.Text, description, nullable=nullable)

    def created(what):
        return (C('created_at', sa.String(32), f'UTC time this {what} was recorded.', nullable=False),
                C('created_by', sa.String(26), f'Company principal that recorded this {what}.', nullable=False),
                C('created_via', sa.String(16), f'Interface that recorded this {what}.', nullable=False))

    templates = T('delivery_templates', *common(),
        text('kind', 'Kind of document this template is for: ' + ', '.join(KINDS) + '.', 16, nullable=False),
        text('name', 'Template name as entered.', 100, nullable=False),
        text('name_key', 'Case-folded name, unique within its kind.', 100, nullable=False),
        text('subject', 'Subject line, with merge fields.', nullable=False),
        text('body', 'Plain-text body, with merge fields.', nullable=False),
        C('is_default', sa.Boolean, 'The default for its kind; exactly one active default per kind.', nullable=False),
        C('active', sa.Boolean, 'Whether this template can be chosen for a new draft.', nullable=False),
        sa.CheckConstraint(f'kind IN ({quoted(KINDS)})', name='ck_delivery_template_kind'),
        sa.CheckConstraint('length(trim(name)) BETWEEN 1 AND 100', name='ck_delivery_template_name'),
        sa.CheckConstraint('length(subject) BETWEEN 1 AND 500 AND length(body) <= 20000',
                           name='ck_delivery_template_text'),
        sa.CheckConstraint('active = 1 OR is_default = 0', name='ck_delivery_template_default_active'),
        sa.UniqueConstraint('kind', 'name_key', name='uq_delivery_template_name'),
        description='Email templates per document kind: a subject and a plain-text body with merge fields.')
    sa.Index('uq_delivery_template_default', templates.c.kind, unique=True,
             sqlite_where=sa.text('is_default = 1 AND active = 1'))

    files = T('delivery_files',
        C('sha256', sa.String(64), 'Lowercase SHA-256 of the bytes.', primary_key=True),
        C('size_bytes', sa.BigInteger, 'Size of the bytes.', nullable=False),
        text('media_type', 'Media type of the bytes.', 127, nullable=False),
        C('content', sa.LargeBinary, 'The frozen bytes.', nullable=False),
        C('created_at', sa.String(32), 'UTC time these bytes were first frozen.', nullable=False),
        sa.CheckConstraint("length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'", name='ck_delivery_file_sha256'),
        sa.CheckConstraint('size_bytes = length(content)', name='ck_delivery_file_size'),
        description='Frozen attachment bytes, content-addressed: identical files share one row. Immutable; '
                    'pruned once no standing message carries them.')

    messages = T('delivery_messages', *common(),
        text('number', 'Message number from the delivery sequence (M-1001, M-1002, ...).', 64, nullable=False),
        text('kind', 'Kind of document it carries: ' + ', '.join(KINDS) + '.', 16, nullable=False),
        ident('customer_id', 'Customer (or job) the message is to.', 'customers.id'),
        text('status', 'draft, ready, sending, sent, held, failed or cancelled.', 10, nullable=False),
        text('hold_reason', 'Why it is held: ' + ', '.join(HOLD_REASONS) + '; null unless held.', 32),
        text('from_name', 'From display name.', 200),
        text('from_address', 'From address; null until a sending connection supplies one.', 254),
        text('reply_to', 'Reply-To address; null for none.', 254),
        text('to_addresses', 'To addresses, a JSON array.', nullable=False),
        text('cc_addresses', 'Cc addresses, a JSON array.', nullable=False),
        text('bcc_addresses', 'Bcc addresses, a JSON array.', nullable=False),
        text('subject', 'Subject as it will be sent.', nullable=False),
        text('body', 'Plain-text body as it will be sent.', nullable=False),
        text('message_id', 'The fixed RFC 5322 Message-ID chosen at draft.', 200, nullable=False),
        ident('template_id', 'Template it was composed from.', 'delivery_templates.id', nullable=True),
        text('dedupe_key', 'Kind, customer, sources and revisions, recipients: one live or sent message per key.',
             200, nullable=False),
        ident('resend_of', 'The earlier message this one sends again (--again).', 'delivery_messages.id',
              nullable=True),
        ident('released_by', 'Principal who released it to the outbox.', nullable=True),
        text('released_at', 'When it was released.', 32),
        ident('go_by', "Person whose go it carries (a person's release or approval).", nullable=True),
        text('go_at', 'When the go was given.', 32),
        C('attempts', sa.Integer, 'Transport attempts made.', nullable=False, server_default='0'),
        text('next_attempt_at', 'When the next attempt is due; null for now.', 32),
        text('lease_until', 'When an attempt in flight lapses.', 32),
        text('sent_at', 'When it was sent (as accepted, or as the person says).', 32),
        text('sent_via', 'connection or manual.', 12),
        text('sent_method', 'For a hand-sent message: mail-program, webmail or other.', 16),
        text('connection_id', 'Sending connection used (a later increment).', 26),
        text('provider_ref', "The transport's reference for the accepted message.", 200),
        text('failure_code', 'Why it failed.', 64),
        text('failure_text', "The transport's text.", 2000),
        text('cancelled_reason', 'Why it was cancelled.', 500),
        sa.CheckConstraint(f'kind IN ({quoted(KINDS)})', name='ck_delivery_message_kind'),
        sa.CheckConstraint(f'status IN ({quoted(STATUSES)})', name='ck_delivery_message_status'),
        sa.CheckConstraint(f"(status = 'held') = (hold_reason IS NOT NULL) AND "
                           f"(hold_reason IS NULL OR hold_reason IN ({quoted(HOLD_REASONS)}))",
                           name='ck_delivery_message_hold'),
        sa.CheckConstraint(f"(status = 'sent') = (sent_at IS NOT NULL AND sent_via IS NOT NULL) AND "
                           f"(sent_via IS NULL OR sent_via IN ({quoted(SENT_VIA)})) AND "
                           f"(sent_method IS NULL OR sent_method IN ({quoted(SENT_METHODS)}))",
                           name='ck_delivery_message_sent'),
        sa.CheckConstraint("(status = 'cancelled') = (cancelled_reason IS NOT NULL)",
                           name='ck_delivery_message_cancelled'),
        sa.CheckConstraint("json_valid(to_addresses) AND json_type(to_addresses) = 'array' AND "
                           "json_valid(cc_addresses) AND json_type(cc_addresses) = 'array' AND "
                           "json_valid(bcc_addresses) AND json_type(bcc_addresses) = 'array'",
                           name='ck_delivery_message_addresses'),
        sa.CheckConstraint("typeof(attempts) = 'integer' AND attempts >= 0", name='ck_delivery_message_attempts'),
        sa.UniqueConstraint('number', name='uq_delivery_message_number'),
        sa.UniqueConstraint('message_id', name='uq_delivery_message_message_id'),
        description='Outbox messages: one email to a customer with its documents attached, and its current state.')
    sa.Index('uq_delivery_message_dedupe', messages.c.dedupe_key, unique=True,
             sqlite_where=sa.text("status <> 'cancelled'"))
    sa.Index('ix_delivery_messages_status', messages.c.status, messages.c.id)
    sa.Index('ix_delivery_messages_customer', messages.c.customer_id, messages.c.id)

    documents = T('delivery_message_documents',
        ident('id', 'Stable ULID of this attached file.', primary_key=True),
        ident('message_id', 'Message the file is attached to.', 'delivery_messages.id'),
        C('position', sa.Integer, 'One-based order of the attachment.', nullable=False),
        text('role', 'document (the rendered PDF) or attached-file (a file linked to the document).', 16,
             nullable=False),
        text('source_kind', 'Kind of the source document.', 16, nullable=False),
        ident('source_id', 'Source document id; for a statement, the customer.'),
        ident('source_revision', 'Source revision when drafted; null for a statement or notice.', nullable=True),
        text('statement_from', 'A statement source: first day of the period.', 10),
        text('statement_to', 'A statement source: last day of the period.', 10),
        text('file_name', 'File name as attached.', 255, nullable=False),
        text('media_type', 'Media type as attached.', 127, nullable=False),
        C('size_bytes', sa.BigInteger, 'Size of the frozen bytes.', nullable=False),
        C('sha256', sa.String(64), 'SHA-256 of the frozen bytes (delivery_files; pruned once no standing message '
          'carries them, the hash kept here).', nullable=False),
        ident('attachment_id', 'Attachment-store file, for an attached file.', nullable=True),
        *created('attached file'),
        sa.CheckConstraint(f'role IN ({quoted(ROLES)})', name='ck_delivery_document_role'),
        sa.CheckConstraint(f'source_kind IN ({quoted(KINDS)})', name='ck_delivery_document_source_kind'),
        sa.CheckConstraint('position > 0', name='ck_delivery_document_position'),
        sa.UniqueConstraint('message_id', 'position', name='uq_delivery_document_position'),
        description='Files attached to a message, frozen when it was drafted: source, revision, name, size, hash.')
    sa.Index('ix_delivery_documents_source', documents.c.source_kind, documents.c.source_id)

    events = T('delivery_events',
        ident('id', 'Stable ULID of this event.', primary_key=True),
        ident('message_id', 'Message the event belongs to.', 'delivery_messages.id'),
        C('ordinal', sa.Integer, "One-based order within the message's history.", nullable=False),
        text('event', 'What happened: ' + ', '.join(EVENTS) + '.', 16, nullable=False),
        text('status', 'Message status after the event.', 10, nullable=False),
        text('details', 'What the event records, a JSON object.', nullable=False),
        text('at', 'UTC time of the event.', 32, nullable=False),
        ident('actor_id', 'Principal who acted.'),
        text('actor_kind', 'human, agent or system.', 8, nullable=False),
        text('via', 'Interface the act came through.', 16, nullable=False),
        sa.CheckConstraint(f'event IN ({quoted(EVENTS)})', name='ck_delivery_event_event'),
        sa.CheckConstraint(f'status IN ({quoted(STATUSES)})', name='ck_delivery_event_status'),
        sa.CheckConstraint("json_valid(details) AND json_type(details) = 'object'", name='ck_delivery_event_details'),
        sa.CheckConstraint('ordinal > 0', name='ck_delivery_event_ordinal'),
        sa.UniqueConstraint('message_id', 'ordinal', name='uq_delivery_event_ordinal'),
        description="Append-only message history: every state change and act, with who and through what.")
    return dict(delivery_templates=templates, delivery_files=files, delivery_messages=messages,
                delivery_message_documents=documents, delivery_events=events)


def company_info_columns(C):
    return (
        C('delivery_reply_to', sa.String(254), 'Email: Reply-To on every message; null for none.', nullable=True),
        C('delivery_copy_to', sa.String(254), 'Email: Bcc on every message (a copy to the company); null for none.',
          nullable=True),
        C('delivery_auto_flag_from', sa.String(10), "Email: documents dated on or after this day are flagged to email "
          "when the customer prefers email (document delivery's queue); null is off.", nullable=True),
    )


TABLES = ('delivery_events', 'delivery_message_documents', 'delivery_messages', 'delivery_files',
          'delivery_templates')


def define_queue_tables(metadata, C, T, common):
    """Increment 2 (co0107): the sending connections a person sets up. A row never holds a secret."""
    connections = T('delivery_connections', *common(),
        C('name', sa.String(100), 'Connection name as entered.', nullable=False),
        C('name_key', sa.String(100), 'Case-folded name, unique among connections not removed.', nullable=False),
        C('kind', sa.String(64), 'Transport kind (mcp-mail, smtp, ...): free text, checked against the kinds this '
          'build registers when the connection is added.', nullable=False),
        C('settings', sa.Text, "Non-secret settings, a JSON object the kind validates (server command pinned to a "
          "version, From address).", nullable=False),
        C('secret_ref', sa.String(200), "Where the secret is: env:NAME or keyring:service/name. Never the secret.",
          nullable=True),
        C('enabled', sa.Boolean, 'Whether the dispatcher may send through it.', nullable=False),
        C('enabled_host', sa.String(64), 'The machine (and company file) it was enabled on; a copy or restore '
          'clears it, and a connection sends only where it was enabled.', nullable=True),
        C('daily_limit', sa.Integer, 'Most attempts in any 24 hours; null for no limit.', nullable=True),
        C('last_check_at', sa.String(32), 'When it was last checked.', nullable=True),
        C('last_check_ok', sa.Boolean, 'Whether the last check could connect.', nullable=True),
        C('last_check_text', sa.String(500), 'What the last check said.', nullable=True),
        C('removed_at', sa.String(32), 'When it was removed; a removed connection is kept for the history.',
          nullable=True),
        sa.CheckConstraint('length(trim(name)) BETWEEN 1 AND 100', name='ck_delivery_connection_name'),
        sa.CheckConstraint('length(kind) BETWEEN 1 AND 64', name='ck_delivery_connection_kind'),
        sa.CheckConstraint("json_valid(settings) AND json_type(settings) = 'object'",
                           name='ck_delivery_connection_settings'),
        sa.CheckConstraint("secret_ref IS NULL OR secret_ref GLOB 'env:?*' OR secret_ref GLOB 'keyring:?*/?*'",
                           name='ck_delivery_connection_secret_ref'),
        sa.CheckConstraint('(enabled = 1) = (enabled_host IS NOT NULL) AND (removed_at IS NULL OR enabled = 0)',
                           name='ck_delivery_connection_enabled'),
        sa.CheckConstraint('daily_limit IS NULL OR daily_limit > 0', name='ck_delivery_connection_limit'),
        description='Sending connections: where mail goes and under whose keys (held in the keyring or the '
                    'environment, never here). None exist until a person sets one up.')
    sa.Index('uq_delivery_connection_name', connections.c.name_key, unique=True,
             sqlite_where=sa.text('removed_at IS NULL'))
    return dict(delivery_connections=connections)


QUEUE_TABLES = ('delivery_connections',)
GUARDED = (
    ('delivery_events', ('UPDATE', 'DELETE'), 'delivery events are append-only'),
    ('delivery_message_documents', ('UPDATE', 'DELETE'), 'attached files are frozen (a refresh of a draft or held '
     "message's file excepted)"),
    ('delivery_files', ('UPDATE', 'DELETE'), 'frozen files are immutable (deleted only once no standing message '
     'carries them)'),
    ('delivery_messages', ('DELETE',), 'messages are never deleted'),
    ('delivery_templates', ('DELETE',), 'templates are deactivated, never deleted'),
    ('delivery_connections', ('DELETE',), 'connections are removed, never deleted'),
)


BATCH_STATES = ('drafting', 'done', 'cancelled')
FLAG_KINDS = ('invoice', 'sales-receipt', 'estimate')


def define_batch_tables(metadata, C, T, common, messages):
    """Increment 3 (co0108): *Email later* flags, batches, and the batch a message was drafted in."""
    flags = T('delivery_flags', *common(),
        C('source_kind', sa.String(16), 'Kind of the document: ' + ', '.join(FLAG_KINDS) + '.', nullable=False),
        C('source_id', sa.String(26), 'The invoice, sales receipt (transactions) or estimate (work_documents).',
          nullable=False),
        C('wanted', sa.Boolean, "True for the anchor's Email later; false for an explicit opt-out, which wins over "
          "the company's auto-flag date.", nullable=False),
        sa.CheckConstraint("source_kind IN ('invoice', 'sales-receipt', 'estimate')", name='ck_delivery_flag_kind'),
        sa.UniqueConstraint('source_kind', 'source_id', name='uq_delivery_flag_source'),
        description="Email later: one row per document a person flagged to email, or opted out of the company's "
                    'auto-flag date.')
    batches = T('delivery_batches', *common(),
        C('number', sa.String(64), 'Batch number from the delivery-batch sequence (B-1001, ...).', nullable=False),
        C('kind', sa.String(16), 'Kind of document the batch drafts.', nullable=False),
        C('selection', sa.Text, 'The selection as asked (queued, unsent, statements, ready notices; filters), a '
          'JSON object.', nullable=False),
        C('targets', sa.Text, 'The messages to draft, fixed when the batch began: a JSON array of customers and '
          'their documents.', nullable=False),
        C('skipped', sa.Text, 'Who was left out and why (no address, prefers mail, already in the outbox or sent), '
          'a JSON array.', nullable=False),
        C('planned', sa.Integer, 'Messages to draft.', nullable=False),
        C('position', sa.Integer, 'Targets drafted or skipped so far; the next chunk starts here.', nullable=False),
        C('send', sa.Boolean, 'Release each message as it is drafted.', nullable=False),
        C('state', sa.String(10), 'drafting, done or cancelled.', nullable=False),
        C('schedule_run_id', sa.String(26), 'The schedule run that started it (increment 4); null for a person or '
          'agent.', nullable=True),
        C('cancelled_reason', sa.String(500), 'Why it was cancelled.', nullable=True),
        C('go_by', sa.String(26), "The person whose go the batch's releases carry, decided once when it began (a "
          'person who asked to send); null otherwise, so a later continue never adds a go.', nullable=True),
        C('fingerprint', sa.String(64), 'SHA-256 of the exact set when it began (documents, versions, recipients); '
          'a run or continue carrying another is refused.', nullable=False),
        C('drafted', sa.Text, 'Each message as the batch left it (version, recipients hash, content hash), a JSON '
          'object by message id: approve and send by batch act only on messages still so.', nullable=False),
        sa.CheckConstraint("state IN ('drafting', 'done', 'cancelled')", name='ck_delivery_batch_state'),
        sa.CheckConstraint("(state = 'cancelled') = (cancelled_reason IS NOT NULL)", name='ck_delivery_batch_cancelled'),
        sa.CheckConstraint("json_valid(selection) AND json_type(selection) = 'object' AND json_valid(targets) AND "
                           "json_type(targets) = 'array' AND json_valid(skipped) AND json_type(skipped) = 'array' AND "
                           "json_valid(drafted) AND json_type(drafted) = 'object'",
                           name='ck_delivery_batch_json'),
        sa.CheckConstraint('planned >= 0 AND position BETWEEN 0 AND planned', name='ck_delivery_batch_position'),
        sa.UniqueConstraint('number', name='uq_delivery_batch_number'),
        description='Batches of drafted messages: what was asked, the messages planned, who was skipped and why, '
                    'and how far drafting has gone. Cancelled as one unit.')
    messages.append_column(C('batch_id', sa.String(26), 'The batch that drafted it; null for a single draft.',
                             sa.ForeignKey('delivery_batches.id'), nullable=True))
    sa.Index('ix_delivery_messages_batch', messages.c.batch_id)
    return dict(delivery_flags=flags, delivery_batches=batches)


BATCH_TABLES = ('delivery_flags', 'delivery_batches')
