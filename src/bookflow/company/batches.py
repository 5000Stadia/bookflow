"""Reversible batches: the shared ledger and its reversal (R107 increment 0, design/bulk-import.md §8a).

A **batch** is a multi-record import or modification (an import run, later a grid edit or a bank statement import). Its
write path records one **effect** per record it touched in the same transaction as the record's own write and audit
event (``dispatch._apply`` calls ``s.batch_recorder`` just before the commit), so an effect never exists without its
write or the reverse.

A batch is **reversed as one unit** through the ordinary inverse commands, under the caller's own grants:

- an ``updated`` effect sends its prior values back as an ordinary update carrying the version the batch left;
- a ``created`` list record is renamed out of the way (``<name> (reversed <batch>)``, freeing its name for the corrected
  file) and deactivated, so it leaves the lists and stays in history. Lists have no delete; when documents become
  importable their family's ``delete`` or ``void`` is their inverse.

**What blocks a reversal** is computed by a plan (``--dry-run``) and never by a parallel dependency engine: the batch's
ordering (``strict``: a later live batch in its scope); a record **edited again** since (its version is not the one the
batch left); a created record **in use** (any row the database's own foreign keys say refers to it, other than its own
detail rows and records the same batch made); and the inverse command's own refusal, permission included, from a dry run.
A blocked reversal changes nothing (``E_BATCH_BLOCKED`` names up to 50 blockers and the total). "Edited again" is
judged against the record's newest effect in any batch, reversals included, so batches unwind newest first; a record a
later live batch touched names that batch.

The plan is checked whole first; then each record's inverse steps run, each its own command, recorded as an effect of a
``reversal`` batch, up to ``limit`` records per call. A batch that is not finished stays ``reversing`` and the same
command resumes it from the recorded steps, so a reversal killed between slices repeats nothing.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any, Callable

import sqlalchemy as sa

from bookflow import BookflowError
from bookflow.company import schema as c
from bookflow.core.ids import new_id

OWNER = 'batch.apply'
DEFAULT_SLICE = 200
MAX_BLOCKERS = 50


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


# ------------------------------------------------------------------ adapters

@dataclass(frozen=True)
class Adapter:
    """How one batch kind reverses: its ordering and, per effect, the inverse commands (generic list records here)."""
    kind: str
    ordering: str = 'record'
    on_reversed: Callable[[Any, dict, str], None] | None = None  # (s, effect, reversal batch id), in the last step's tx
    on_finished: Callable[[Any, dict], None] | None = None  # (s, batch), in the transaction that marks it reversed
    label: str = ''
    # A kind whose effects are not list records plans and runs its own inverse steps (bank imports):
    # planner(s, ctx, batch, effects, done, probe, blockers) -> dict(steps, work, blockers, records, pending), where
    # steps[i] describes work[i] (extra trailing steps describe what on_finished does); runner(s, ctx, batch,
    # reversal_id, item) runs one work item and records its reversal effects; reader(s, batch) checks the caller may
    # read the batch. blocks: a reversal stopped by a blocker between slices is marked `blocked` (else it stays
    # `reversing`); the same command resumes either.
    planner: Callable | None = None
    runner: Callable | None = None
    reader: Callable[[Any, dict], None] | None = None
    blocks: bool = False


ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    ADAPTERS[adapter.kind] = adapter
    return adapter


def adapter_for(kind: str) -> Adapter:
    try:
        return ADAPTERS[kind]
    except KeyError:
        raise BookflowError('E_VALIDATION', message=f'A {kind} batch cannot be reversed here.',
                            details={'field': 'batch', 'kind': kind}) from None


@dataclass(frozen=True)
class ListRecord:
    noun: str
    table: sa.Table
    name_field: str  # the input field holding the record's own (leaf) name
    selector_field: str
    number_field: str | None = None  # a second unique field a reversed record releases (an account's number)

    @property
    def selector(self) -> str:
        """The input field its update and deactivate commands select the record by (`customer_type`)."""
        return self.noun.replace('-', '_')


@lru_cache(maxsize=None)
def list_record(record_type: str) -> ListRecord | None:
    from bookflow.company import lists
    for definition in lists.all_list_definitions():
        if definition.record_type == record_type:
            table = c.metadata.tables[definition.table]
            return ListRecord(definition.noun, table, 'name', definition.display_field,
                              'number' if definition.noun == 'account' else None)
    return None


def _record(record_type: str) -> ListRecord:
    found = list_record(record_type)
    if found is None:
        raise BookflowError('E_VALIDATION', message=f'No inverse is known for {record_type} records.',
                            details={'record_type': record_type})
    return found


# ------------------------------------------------------------------ the write path

def open_batch(s, ctx, *, kind: str, label: str, scope: str, ordering: str | None = None,
               source_type: str | None = None, source_id: str | None = None, reason: str | None = None) -> str:
    """Write a live batch row in its own transaction and return its id."""
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            batch_id = _insert_batch(s, ctx, kind=kind, label=label, scope=scope,
                                     ordering=ordering or adapter_for(kind).ordering, source_type=source_type,
                                     source_id=source_id, reason=reason)
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return batch_id


def _insert_batch(s, ctx, *, kind: str, label: str, scope: str, ordering: str, source_type: str | None = None,
                  source_id: str | None = None, reversal_of: str | None = None, reason: str | None = None) -> str:
    """Insert a live batch row inside the caller's open transaction."""
    batch_id = new_id()
    actor = s.actor
    s.company.conn.execute(sa.insert(c.batches).values(
        id=batch_id, kind=kind, scope=scope[:200], ordering=ordering, label=label[:200], state='live',
        source_type=source_type, source_id=source_id, reversal_of=reversal_of, actor_id=actor.id,
        actor_kind=actor.kind, via=str(getattr(ctx.interface, 'value', ctx.interface))[:16],
        on_behalf_of=_on_behalf(s, ctx), reason=reason if reason is not None else ctx.reason, created_at=now()))
    return batch_id


def _on_behalf(s, ctx) -> str | None:
    value = getattr(ctx, 'on_behalf_of', None) or getattr(s.actor, 'owner_user_id', None)
    return value if isinstance(value, str) and len(value) == 26 else None


def last_event_id(s) -> str | None:
    """The newest company audit event: inside a command's transaction, the one that command just wrote."""
    return s.company.conn.execute(sa.text('SELECT id FROM audit_events ORDER BY seq DESC LIMIT 1')).scalar()


def record_effect(s, batch_id: str, *, record_type: str, record_id: str, action: str, version_after: int | None,
                  before: dict | None = None, event_id: str | None = None) -> int:
    """Append one effect inside the caller's open transaction; returns its seq."""
    seq = (s.company.conn.execute(sa.select(sa.func.max(c.batch_effects.c.seq))
                                  .where(c.batch_effects.c.batch_id == batch_id)).scalar() or 0) + 1
    s.company.conn.execute(sa.insert(c.batch_effects).values(
        batch_id=batch_id, seq=seq, record_type=record_type, record_id=record_id, action=action,
        version_after=version_after, before=None if before is None else json.dumps(before, sort_keys=True),
        event_id=event_id, created_at=now()))
    return seq


def touched_effects(applied, record_types: set[str]) -> list[tuple[str, str, str, int | None]]:
    """(record_type, record_id, action, version_after) for the records a command created or updated."""
    out = []
    for t in applied.touched:
        if t.db != 'company' or t.record_type not in record_types:
            continue
        if t.action == 'create':
            out.append((t.record_type, t.record_id, 'created', t.version_after))
        elif t.action in ('update', 'activate', 'deactivate'):
            out.append((t.record_type, t.record_id, 'updated', t.version_after))
    return out


def run_nested(s, ctx, name: str, payload: dict, *, source_ref: str, dry_run: bool = False,
               recorder: Callable | None = None, recovery: bool = True) -> dict:
    """Run one registry command inside this session, as `cutover` nests its steps: the caller's own authority,
    validation and audit, with ``source_ref`` naming where the write came from. ``recovery=False`` skips a command's
    permanent-recovery lookup (a replay by operation key), which closes and reopens the session's company: a nested
    step that carries no operation key has nothing to replay."""
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session, validate_input
    command = registry.get(name)
    nested = ctx.model_copy(update={'idempotency_key': None, 'source_ref': source_ref[:512]})
    saved = (s.company_touched, s.hub_touched, list(s.warnings), s.dry_run, getattr(s, 'batch_recorder', None))
    s.batch_recorder = recorder
    try:
        return run_in_session(command, validate_input(command, payload), nested, s, dry_run=dry_run,
                              _recovery_checked=not recovery)
    finally:
        s.company_touched, s.hub_touched, s.warnings, s.dry_run, s.batch_recorder = saved
        if s.company is not None and s.company.write_transaction:
            s.company.raw.rollback()


# ------------------------------------------------------------------ reading

def _batch(s, batch_id: str) -> dict:
    row = s.company.conn.execute(sa.select(c.batches).where(c.batches.c.id == batch_id)).mappings().first()
    if row is None:
        raise BookflowError('E_RECORD_NOT_FOUND', details={'record_type': 'batch', 'selector': batch_id})
    return dict(row)


def effects(s, batch_id: str) -> list[dict]:
    rows = s.company.conn.execute(sa.select(c.batch_effects).where(c.batch_effects.c.batch_id == batch_id)
                                  .order_by(c.batch_effects.c.seq)).mappings().all()
    return [dict(row) for row in rows]


def counts(s, batch_id: str) -> dict[str, int]:
    rows = s.company.conn.execute(sa.select(c.batch_effects.c.action, sa.func.count())
                                  .where(c.batch_effects.c.batch_id == batch_id)
                                  .group_by(c.batch_effects.c.action)).all()
    return {action: int(n) for action, n in rows}


def describe(s, batch: dict, *, with_plan: bool = False, ctx=None) -> dict:
    out = {k: batch[k] for k in ('id', 'kind', 'scope', 'ordering', 'label', 'state', 'source_type', 'source_id',
                                 'reversal_of', 'actor_id', 'actor_kind', 'via', 'on_behalf_of', 'reason',
                                 'created_at', 'reversed_by', 'reversed_at', 'reversal_reason', 'reversal_event_id')}
    out['counts'] = counts(s, batch['id'])
    if with_plan:
        if batch['state'] in ('live', 'reversing', 'blocked') and batch['reversal_of'] is None \
                and batch['kind'] in ADAPTERS:
            try:
                found = plan(s, ctx, batch['id'], probe=False)
            except BookflowError as error:
                if error.code != 'E_PERMISSION':
                    raise
                # The records are not the caller's to read: no names, values or blockers from them.
                out['reversible'], out['first_blocker'] = False, {'code': 'E_PERMISSION', 'message': (
                    'You cannot read every kind of record this batch touched.'), 'details': error.details}
                return out
            out['reversible'] = not found['blockers']
            out['first_blocker'] = found['blockers'][0] if found['blockers'] else None
        else:
            out['reversible'], out['first_blocker'] = False, None
    return out


def show(s, ctx, batch_id: str) -> dict:
    batch = _batch(s, batch_id)
    reader = ADAPTERS[batch['kind']].reader if batch['kind'] in ADAPTERS else None
    (reader or require_batch_read)(s, batch)
    out = describe(s, batch, with_plan=True, ctx=ctx)
    out['effects'] = [_effect_out(e) for e in effects(s, batch_id)[:200]]
    return out


def _effect_out(e: dict) -> dict:
    return {'seq': e['seq'], 'record_type': e['record_type'], 'record_id': e['record_id'], 'action': e['action'],
            'version_after': e['version_after'], 'before': json.loads(e['before']) if e['before'] else None,
            'event_id': e['event_id']}


def query(s, ctx, *, kind=None, state=None, scope=None, limit=50, cursor=None) -> dict:
    q = sa.select(c.batches).order_by(c.batches.c.created_at.desc(), c.batches.c.id.desc())
    if kind:
        q = q.where(c.batches.c.kind == kind)
    if state:
        q = q.where(c.batches.c.state == state)
    if scope:
        q = q.where(c.batches.c.scope == scope)
    if cursor:
        q = q.where(c.batches.c.id < cursor)
    rows = [dict(r) for r in s.company.conn.execute(q.limit(limit + 1)).mappings().all()]
    more = len(rows) > limit
    rows = rows[:limit]
    return {'items': [describe(s, r, with_plan=True, ctx=ctx) for r in rows],
            'next_cursor': rows[-1]['id'] if more and rows else None}


# ------------------------------------------------------------------ references the database does not declare

PARTIES = ('customers', 'vendors', 'employees', 'other_names')
# Columns naming a record without a foreign key (one column may name several lists), checked as uses beside the
# foreign keys: ``None`` is any list. `tests/test_batches.py` scans the company schema and fails on an unkeyed
# ``*_id`` column that is in neither this nor NOT_REFERENCES.
SOFT_REFERENCES: dict[tuple[str, str], tuple[str, ...] | None] = {
    ('document_lines', 'name_id'): PARTIES,
    ('posting_lines', 'name_id'): PARTIES,
    ('transaction_revisions', 'name_id'): PARTIES,
    ('memorized_references', 'target_id'): None,
    ('notes', 'record_id'): None,
    ('attachment_links', 'record_id'): None,
    ('todos', 'link_id'): ('customers', 'vendors'),
    ('sales_reps', 'name_id'): ('employees', 'vendors', 'other_names'),
    ('other_names', 'converted_to_id'): ('customers', 'vendors', 'employees'),
    ('vendors', 'billing_rate_level_id'): None,
    ('deposit_component_keys', 'tax_item_id'): ('items',),
    ('application_allocations', 'tax_item_id'): ('items',),
    ('accountant_adjustments', 'from_account_id'): ('accounts',),
    ('accountant_adjustments', 'to_account_id'): ('accounts',),
    ('party_merges', 'merged_id'): PARTIES,
    ('party_merges', 'survivor_id'): PARTIES,
    ('list_merges', 'merged_id'): None,
    ('list_merges', 'survivor_id'): None,
    ('list_merge_references', 'record_id'): None,
    ('list_merge_references', 'owner_id'): None,
    ('delivery_message_documents', 'source_id'): ('customers',),  # a statement's customer
}
_PEOPLE = 'a person or principal of the hub, never a list record'
_INTERNAL = "a document's own row or line, never a list record"
_LEDGER = "the batch and import ledger's own history"
NOT_REFERENCES: dict[tuple[str, str], str] = {
    ('company_info', 'tax_id'): 'a tax registration number, not a record id',
    ('custom_field_values', 'record_id'): ("the record's own custom-field values (its detail); they change only "
                                           "through the record's own update, which bumps its version"),
    ('principals', 'user_id'): _PEOPLE, ('presence', 'user_id'): _PEOPLE,
    ('presence', 'record_id'): 'who is looking at a record now; ephemeral',
    ('audit_entries', 'record_id'): 'the audit trail: history, never a use',
    ('audit_events', 'actor_id'): _PEOPLE, ('audit_events', 'session_id'): 'the audit trail',
    ('audit_events', 'request_id'): 'the audit trail', ('audit_events', 'directive_id'): 'the audit trail',
    ('idempotency_keys', 'actor_id'): _PEOPLE, ('idempotency_keys', 'request_id'): 'a retry key',
    ('notes', 'author_id'): _PEOPLE, ('todos', 'assignee_id'): _PEOPLE,
    ('report_cursor_keys', 'key_id'): 'a report paging cursor',
    ('reconciliation_events', 'actor_id'): _PEOPLE, ('reconciliation_events', 'principal_id'): _PEOPLE,
    ('purchase_deletions', 'principal_id'): _PEOPLE, ('sales_deletions', 'principal_id'): _PEOPLE,
    ('payment_deletions', 'principal_id'): _PEOPLE, ('bill_deletions', 'principal_id'): _PEOPLE,
    ('credit_deletions', 'principal_id'): _PEOPLE, ('deposit_deletions', 'principal_id'): _PEOPLE,
    ('journal_deletions', 'principal_id'): _PEOPLE,
    ('timekeeping_people', 'user_id'): _PEOPLE, ('accountant_designations', 'user_id'): _PEOPLE,
    ('accountant_copies', 'accountant_user_id'): _PEOPLE, ('accountant_imports', 'accountant_user_id'): _PEOPLE,
    ('accountant_adjustments', 'accountant_user_id'): _PEOPLE,
    ('accountant_review_marks', 'accountant_user_id'): _PEOPLE,
    ('accountant_copies', 'origin_company_id'): 'a company, not a record in it',
    ('accountant_imports', 'result_id'): 'an accountant import result', ('accountant_adjustments', 'copy_id'): _INTERNAL,
    ('accountant_adjustments', 'source_transaction_id'): _INTERNAL, ('accountant_review_marks', 'copy_id'): _INTERNAL,
    ('scheduled_revision_authority', 'revision_id'): _INTERNAL,
    ('scheduled_revision_authority', 'agent_id'): _PEOPLE, ('scheduled_revision_authority', 'principal_id'): _PEOPLE,
    ('scheduled_group_authority', 'group_id'): _INTERNAL, ('scheduled_group_authority', 'principal_id'): _PEOPLE,
    ('scheduled_holds', 'occurrence_id'): _INTERNAL,
    ('deposit_selection_sources', 'row_id'): _INTERNAL, ('credit_tax_components', 'source_tax_component_id'): _INTERNAL,
    ('invoice_batches', 'billing_group_id'): 'a billing group, which no import or batch makes',
    ('memorized_occurrences', 'transaction_id'): _INTERNAL, ('statement_effect_pending', 'transaction_id'): _INTERNAL,
    ('statement_lines', 'line_id'): _INTERNAL, ('prepayment_draws', 'line_id'): _INTERNAL,
    ('assembly_builds', 'build_id'): _INTERNAL, ('purchase_coverage_links', 'purchase_order_line_id'): _INTERNAL,
    ('inventory_transfer_lines', 'line_id'): _INTERNAL,
    ('landed_cost_allocation_lines', 'charge_line_id'): _INTERNAL,
    ('shipment_lines', 'root_document_id'): _INTERNAL, ('shipment_lines', 'root_line_id'): _INTERNAL,
    ('inventory_placements', 'source_line_id'): _INTERNAL, ('pick_list_lines', 'order_line_id'): _INTERNAL,
    ('pick_list_lines', 'root_document_id'): _INTERNAL, ('pick_list_lines', 'root_line_id'): _INTERNAL,
    ('batches', 'source_id'): _LEDGER, ('batches', 'actor_id'): _PEOPLE, ('batch_effects', 'record_id'): _LEDGER,
    ('import_runs', 'attachment_id'): _LEDGER, ('import_rows', 'record_id'): _LEDGER,
    ('import_keys', 'record_id'): _LEDGER,
    ('delivery_events', 'actor_id'): _PEOPLE,
    ('delivery_message_documents', 'attachment_id'): 'a stored file, never a list record',
    ('delivery_messages', 'connection_id'): 'a delivery connection, which no import or batch makes',
    ('delivery_messages', 'message_id'): 'an e-mail Message-ID (text), not a record id',
    ('delivery_flags', 'source_id'): 'an invoice, sales receipt or estimate flagged Email later, which no import or '
                                     'batch makes',
    ('delivery_batches', 'schedule_run_id'): 'a schedule run (delivery increment 4), which no import or batch makes',
    ('shipment_draft_lines', 'root_document_id'): _INTERNAL, ('shipment_draft_lines', 'root_line_id'): _INTERNAL,
    ('shipment_package_contents', 'revision_id'): _INTERNAL, ('shipment_packages', 'revision_id'): _INTERNAL,
    ('shipment_packages', 'shipment_id'): _INTERNAL,
}


# ------------------------------------------------------------------ who may read a batch

def require_read(s, command_name: str) -> None:
    """The caller may run ``command_name`` (a kind's ``show``): holding ``import`` reveals nothing it would not."""
    from bookflow.core import registry
    from bookflow.hub import access
    cmd = registry.get(command_name)
    access.require_resource(s, cmd.capability, cmd.required_role or 'authenticated')
    for capability, required_role in cmd.resource_requirements:
        access.require_resource(s, capability, required_role)


def require_batch_read(s, batch: dict) -> None:
    """The kind's own read permission for every kind of record the batch touched."""
    types = s.company.conn.execute(sa.select(c.batch_effects.c.record_type).where(
        c.batch_effects.c.batch_id == batch['id']).distinct()).scalars().all()
    for record_type in sorted(types):
        rec = list_record(record_type)
        if rec is not None:
            require_read(s, f'{rec.noun} show')


# ------------------------------------------------------------------ planning a reversal

def _done_steps(s, batch: dict) -> dict[tuple[str, str], list[dict]]:
    """Inverse steps already run for each record, from the reversal batch's own effects."""
    if not batch['reversed_by']:
        return {}
    done: dict[tuple[str, str], list[dict]] = {}
    for e in effects(s, batch['reversed_by']):
        done.setdefault((e['record_type'], e['record_id']), []).append(e)
    return done


def _groups(all_effects: list[dict]) -> list[dict]:
    """One entry per record, in the order the batch first touched it. The first touch decides the inverse (a record
    the batch made is retired however often it then changed it); the prior values are each field's value before the
    batch's first change to it, so the record returns to its state before the batch."""
    groups: dict[tuple[str, str], dict] = {}
    for e in all_effects:
        key = (e['record_type'], e['record_id'])
        group = groups.get(key)
        if group is None:
            group = groups[key] = {'key': key, 'seq': e['seq'], 'record_type': key[0], 'record_id': key[1],
                                   'action': e['action'], 'before': {}, 'batch_id': e['batch_id']}
        for name, value in (json.loads(e['before']) if e['before'] else {}).items():
            group['before'].setdefault(name, value)
    return list(groups.values())


def _latest(s, keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Each record's newest effect in any batch, reversals included: the version the books should hold now."""
    by_type: dict[str, list[str]] = {}
    for record_type, record_id in keys:
        by_type.setdefault(record_type, []).append(record_id)
    out: dict[tuple[str, str], dict] = {}
    for record_type, ids in by_type.items():
        for start in range(0, len(ids), 500):
            rows = s.company.raw.execute(
                'SELECT e.record_id, e.batch_id, e.version_after, b.reversal_of, b.label, b.state '
                'FROM batch_effects e JOIN batches b ON b.id = e.batch_id WHERE e.rowid IN ('
                'SELECT max(rowid) FROM batch_effects WHERE record_type = ? '
                'AND record_id IN (SELECT value FROM json_each(?)) GROUP BY record_id)',
                (record_type, json.dumps(ids[start:start + 500]))).fetchall()
            for record_id, batch_id, version, reversal_of, label, state in rows:
                out[(record_type, record_id)] = {'batch_id': batch_id, 'version_after': version,
                                                 'reversal_of': reversal_of, 'label': label, 'state': state}
    return out


def _current(s, record_type: str, ids: list[str]) -> dict[str, dict]:
    rec = _record(record_type)
    t = rec.table
    cols = [t.c.id, t.c.version, t.c.updated_by, t.c.updated_at, t.c.active, t.c[rec.name_field]]
    out = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for row in s.company.conn.execute(sa.select(*cols).where(t.c.id.in_(chunk))).mappings():
            out[row['id']] = dict(row)
    return out


_FK_CACHE: dict[str, list[tuple[str, str]]] = {}


def _referrers(s, table: str) -> list[tuple[str, str]]:
    """(table, column) pairs that refer to ``table``: its foreign keys, less the record's own detail rows, and the
    declared references without one (SOFT_REFERENCES)."""
    if table not in _FK_CACHE:
        singular = table[:-3] + 'y' if table.endswith('ies') else table.rstrip('s')
        found = []
        names = [r[0] for r in s.company.raw.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        for name in names:
            for fk in s.company.raw.execute(f"PRAGMA foreign_key_list('{name}')").fetchall():
                if fk[2] != table:
                    continue
                own = name.startswith(singular + '_') and fk[3] in (singular + '_id', 'owner_' + singular + '_id')
                if not own:
                    found.append((name, fk[3]))
        found += [(name, column) for (name, column), targets in SOFT_REFERENCES.items()
                  if (targets is None or table in targets) and name in names]
        _FK_CACHE[table] = sorted(set(found))
    return _FK_CACHE[table]


def _in_use(s, record_type: str, ids: list[str], made_here: set[str]) -> dict[str, dict]:
    """For each created record, what refers to it elsewhere (foreign keys and the declared references without one):
    per table, the first referring row."""
    rec = _record(record_type)
    found: dict[str, dict] = {}
    if not ids:
        return found
    payload = json.dumps(ids)
    for table, column in _referrers(s, rec.table.name):
        meta = c.metadata.tables.get(table)
        key = 'id' if meta is not None and 'id' in meta.c else 'rowid'
        document = 'transaction_id' if meta is not None and 'transaction_id' in meta.c else 'NULL'
        sql = (f'SELECT "{column}", {key}, {document} FROM "{table}" '
               f'WHERE "{column}" IN (SELECT value FROM json_each(?))')
        for target, referrer, transaction in s.company.raw.execute(sql, (payload,)).fetchall():
            if referrer in made_here:  # a record the same batch made (a customer using a type it made too)
                continue
            uses = found.setdefault(target, {'tables': [], 'rows': [], 'documents': []})
            if transaction and len(uses['documents']) < 3:
                kind, number = s.company.raw.execute('SELECT type, number FROM transactions WHERE id = ?',
                                                     (transaction,)).fetchone() or ('transaction', None)
                text = f"{kind.replace('_', ' ')} {number or transaction}"
                if text not in uses['documents']:
                    uses['documents'].append(text)
            if table not in uses['tables']:
                uses['tables'].append(table)
                uses['rows'].append({'table': table, 'column': column, 'id': referrer, 'transaction_id': transaction})
    return found


def _steps_for(group: dict, rec: ListRecord, version: int) -> list[tuple[str, dict]]:
    """The inverse commands for one record, in order, given its version when the first one runs."""
    if group['action'] == 'updated':
        return [(f'{rec.noun} update', {rec.selector: group['record_id'], 'expected_version': version,
                                        **group['before']})]
    if group['action'] == 'created':
        return [('rename', {}), (f'{rec.noun} deactivate', {rec.selector: group['record_id']})]
    raise BookflowError('E_VALIDATION', message=f"No inverse for a {group['action']} effect.",
                        details={'action': group['action']})


def released_name(name: str, batch_id: str) -> str:
    suffix = f' (reversed {batch_id[-6:].lower()})'
    return (name or '')[:200 - len(suffix)].rstrip() + suffix


def _blocker(effect: dict, rec: ListRecord | None, code: str, message: str, *, clears: str | None = None,
             **extra) -> dict:
    out = {'seq': effect.get('seq'), 'record_type': effect.get('record_type'), 'record_id': effect.get('record_id'),
           'code': code, 'message': message, 'clears': clears}
    out.update(extra)
    return out


def _fingerprint(steps_out: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        [(x['seq'], x['record_id'], x['version'], x['steps']) for x in steps_out]).encode()).hexdigest()


def plan(s, ctx, batch_id: str, *, probe: bool = True) -> dict:
    """What reversing this batch would do and what blocks it. ``probe`` dry-runs every inverse command too."""
    batch = _batch(s, batch_id)
    adapter = adapter_for(batch['kind'])
    blockers: list[dict] = []
    if batch['reversal_of'] is not None:
        raise BookflowError('E_VALIDATION', message='A reversal is not itself reversed; run the corrected import.',
                            details={'field': 'batch', 'batch': batch_id})
    if batch['state'] in ('reversed', 'reversed_partly'):
        raise BookflowError('E_VALIDATION', message=f'Batch {batch_id} is already {batch["state"]}.',
                            details={'field': 'batch', 'state': batch['state']})
    (adapter.reader or require_batch_read)(s, batch)
    if adapter.ordering == 'strict':
        later = s.company.conn.execute(sa.text(
            "SELECT id, label, created_at FROM batches WHERE scope = :scope AND reversal_of IS NULL "
            "AND state IN ('live', 'reversing', 'blocked') AND rowid > (SELECT rowid FROM batches WHERE id = :id) "
            "ORDER BY rowid DESC"), {'scope': batch['scope'], 'id': batch_id}).mappings().all()
        for row in later:
            blockers.append({'seq': None, 'record_type': 'batch', 'record_id': row['id'], 'code': 'later_batch',
                             'message': f"A later batch in {batch['scope']} is still live: {row['label']}.",
                             'clears': f"reverse batch {row['id']} first", 'at': row['created_at']})
    if adapter.planner is not None:
        found = adapter.planner(s, ctx, batch, effects(s, batch_id), _done_steps(s, batch), probe, blockers)
        return {'batch': batch, 'fingerprint': _fingerprint(found['steps']), **found}
    groups = _groups(effects(s, batch_id))
    done = _done_steps(s, batch)
    made = {g['key'] for g in groups if g['action'] == 'created'}
    pending = []
    for g in reversed(groups):
        steps_done = done.get(g['key'], [])
        if len(steps_done) >= (1 if g['action'] == 'updated' else 2):
            continue
        pending.append((g, _record(g['record_type']), steps_done))
    latest = _latest(s, [g['key'] for g, _, _ in pending])
    by_type: dict[str, list[str]] = {}
    for g, _, _ in pending:
        by_type.setdefault(g['record_type'], []).append(g['record_id'])
    current = {t: _current(s, t, ids) for t, ids in by_type.items()}
    in_use = {t: _in_use(s, t, [g['record_id'] for g, _, _ in pending if g['record_type'] == t
                                and g['action'] == 'created'], {i for _, i in made})
              for t in by_type}
    steps_out, work = [], []
    for g, rec, steps_done in pending:
        row = current[g['record_type']].get(g['record_id'])
        if row is None:
            blockers.append(_blocker(g, rec, 'E_RECORD_NOT_FOUND', f"{rec.noun} {g['record_id']} no longer exists."))
            continue
        label = f"{rec.noun} {row[rec.name_field]}"
        last = latest.get(g['key'])
        if last is not None and last['batch_id'] not in (batch_id, batch['reversed_by']) \
                and last['reversal_of'] is None:
            blockers.append(_blocker(g, rec, 'later_batch',
                                     f"{label} was changed since by a later batch, {last['label']} ({last['state']}).",
                                     clears=f"reverse batch {last['batch_id']} first", batch=last['batch_id']))
            continue
        expected = last['version_after'] if last is not None else None
        if expected is not None and row['version'] != expected:
            blockers.append(_blocker(g, rec, 'edited_again',
                                     f"{label} was changed since the batch (version {row['version']}, the batch left "
                                     f"{expected}), by {row['updated_by']} at {row['updated_at']}.",
                                     clears=f"`{rec.noun} history {g['record_id']}` shows the change; undo it or keep "
                                            "the batch", changed_by=row['updated_by'], changed_at=row['updated_at']))
            continue
        use = in_use[g['record_type']].get(g['record_id']) if g['action'] == 'created' else None
        if use is not None:
            blockers.append(_blocker(g, rec, 'in_use',
                                     f"{label} is in use: " + (', '.join(use['documents']) if use['documents'] else
                                                               'rows of ' + ', '.join(use['tables'])) + ' refer to it.',
                                     clears=f"void, delete or move what uses {label} first", used_by=use['rows']))
            continue
        steps = _steps_for(g, rec, row['version'])[len(steps_done):]
        steps_out.append({'seq': g['seq'], 'record_type': g['record_type'], 'record_id': g['record_id'],
                          'name': row[rec.name_field], 'action': g['action'], 'version': row['version'],
                          'steps': [name if name != 'rename' else f'{rec.noun} update' for name, _ in steps]})
        work.append((g, rec, row, steps, len(steps_done)))
        if probe:
            refusal = _probe(s, ctx, batch, g, rec, row, steps, made)
            if refusal is not None:
                blockers.append(refusal)
    return {'batch': batch, 'steps': steps_out, 'blockers': blockers, 'fingerprint': _fingerprint(steps_out),
            'work': work, 'records': len(groups), 'pending': len(pending)}


def _payload(s, rec: ListRecord, batch: dict, row: dict, name: str, payload: dict, version: int | None) -> dict:
    if name == 'rename':
        out = {rec.selector: row['id'], 'expected_version': version,
               rec.name_field: released_name(row[rec.name_field], batch['id'])}
        if rec.number_field:
            out[rec.number_field] = None
        if rec.noun == 'sales-rep':
            out['initials'] = _free_initials(s, row['id'])
        return out
    return dict(payload, expected_version=version)


def _free_initials(s, record_id: str) -> str:
    """Initials are unique among inactive reps too and always set: a retired rep takes five characters of its own
    id that no rep uses, freeing its initials for the corrected file."""
    for end in range(len(record_id), 4, -1):
        candidate = record_id[end - 5:end]
        if not s.company.conn.execute(sa.text('SELECT 1 FROM sales_reps WHERE initials_key = :k AND id != :id'),
                                      {'k': candidate.casefold(), 'id': record_id}).first():
            return candidate
    return record_id[-5:]


def _probe(s, ctx, batch, g, rec, row, steps, made) -> dict | None:
    """Dry-run the inverse commands still to run: their own refusals, permission included, name the blocker. A
    deactivate refused only for children this same batch made (and reverses first) is not a blocker."""
    for name, payload in steps:
        command = f'{rec.noun} update' if name == 'rename' else name
        body = _payload(s, rec, batch, row, name, payload, row['version'])
        if name.endswith(' deactivate'):
            body.pop('expected_version', None)
        try:
            run_nested(s, ctx, command, body, source_ref=f"reverse:{batch['id']}", dry_run=True)
        except BookflowError as error:
            if error.code == 'E_ACTIVE_DEPENDENTS' and _only_batch_children(s, rec, row, g, made):
                continue
            return _blocker(g, rec, error.code, f"{rec.noun} {row[rec.name_field]}: {error.message}",
                            clears=_clears_for(error, rec), details=error.details)
    return None


def _only_batch_children(s, rec, row, g, made) -> bool:
    if 'path' not in rec.table.c:
        return False
    path = s.company.conn.execute(sa.select(rec.table.c.path).where(rec.table.c.id == row['id'])).scalar()
    children = s.company.conn.execute(sa.select(rec.table.c.id).where(
        rec.table.c.path > path, rec.table.c.path < path[:-1] + '0', rec.table.c.active.is_(True))).scalars().all()
    return bool(children) and all((g['record_type'], child) in made for child in children)


def _clears_for(error: BookflowError, rec: ListRecord) -> str:
    if error.code == 'E_PERMISSION':
        return 'ask your principal or an admin for the grant the details name'
    if error.code == 'E_ACTIVE_DEPENDENTS':
        return f'deactivate or move what sits under this {rec.noun} first'
    if error.code == 'E_VERSION_CONFLICT':
        return f'the {rec.noun} changed meanwhile; run the reversal again'
    return f'see the details; `{rec.noun} show` reads the record'


def _progress(found: dict) -> str:
    if found['batch']['state'] not in ('reversing', 'blocked'):
        return ''
    done = found['records'] - found['pending']
    return f"The reversal is part way ({done} of {found['records']} records reversed) and cannot go on. "


def refuse(found: dict) -> BookflowError:
    blockers = found['blockers']
    first = blockers[0]
    return BookflowError('E_BATCH_BLOCKED', message=(
        _progress(found) + f"The batch cannot be reversed: {len(blockers)} blocker{'s' if len(blockers) != 1 else ''}, "
        f"first {first['message']} Nothing was changed."),
        details={'batch': found['batch']['id'], 'blockers': blockers[:MAX_BLOCKERS], 'total': len(blockers),
                 'state': found['batch']['state'], 'records': found['records'],
                 'reversed': found['records'] - found['pending']})


# ------------------------------------------------------------------ reversing

def reverse(s, ctx, batch_id: str, *, reason: str | None, dry_run: bool = False, limit: int = DEFAULT_SLICE,
            fingerprint: str | None = None, interrupt: Callable[[int], None] | None = None,
            then: Callable[[], Any] | None = None) -> dict:
    """Reverse a batch (or its next slice). ``interrupt`` is a test hook called after each record; ``then`` runs
    inside the transaction that marks the batch reversed (a bank import's move re-imports there), and its result is
    returned as ``then``.

    The whole plan is probed (every inverse command dry-run) once: by a dry run whose fingerprint the real call
    carries, else by the first slice. A later slice re-checks only what can have changed since: each record's
    version against its newest effect, what uses a created record, and later batches. An inverse command refused
    while running stops the slice there with the record named; what ran stays reversed and the state `reversing`."""
    batch = _batch(s, batch_id)
    adapter = adapter_for(batch['kind'])
    starting = batch['state'] not in ('reversing', 'blocked')
    found = plan(s, ctx, batch_id, probe=dry_run or (starting and fingerprint is None))
    if found['blockers']:
        if not dry_run and not starting and adapter.blocks:
            _set_state(s, batch_id, 'blocked')
            found['batch'] = _batch(s, batch_id)
        raise refuse(found)
    if starting and fingerprint is not None and fingerprint != found['fingerprint']:
        raise BookflowError('E_VERSION_CONFLICT', message='The batch changed since that dry run; run it again.',
                            details={'field': 'fingerprint', 'current': found['fingerprint']})
    if dry_run:
        return _result(s, found, state=batch['state'], reversed_now=0, dry_run=True)
    if batch['state'] == 'blocked':
        _set_state(s, batch_id, 'reversing')
    reversal_id = batch['reversed_by'] or _begin(s, ctx, batch, reason)
    batch = _batch(s, batch_id)
    work = found['work'][:max(1, limit)]
    reversed_now = 0
    for item in work if adapter.runner is not None else ():
        try:
            adapter.runner(s, ctx, batch, reversal_id, item)
        except BookflowError as error:
            stopped = _stopped_item(found, item, error, reversed_now)
            if adapter.blocks:
                _set_state(s, batch_id, 'blocked')
                stopped.details['state'] = 'blocked'
            raise stopped from None
        reversed_now += 1
        if interrupt is not None:
            interrupt(reversed_now)
    for g, rec, row, steps, offset in work if adapter.runner is None else ():
        total = 1 if g['action'] == 'updated' else 2
        version = row['version']
        for i, (name, payload) in enumerate(steps):
            index = offset + i
            command = f'{rec.noun} update' if name == 'rename' else name
            try:
                output = run_nested(s, ctx, command, _payload(s, rec, batch, row, name, payload, version),
                                    source_ref=f'reverse:{batch_id}',
                                    recorder=_reversal_recorder(reversal_id, g, index,
                                                                adapter if index == total - 1 else None))
            except BookflowError as error:
                raise _stopped(found, g, rec, row, error, reversed_now) from None
            version = output.get('version') if isinstance(output, dict) else None
        reversed_now += 1
        if interrupt is not None:
            interrupt(reversed_now)
    if len(found['work']) > len(work):
        rest = found['steps'][len(work):]
        return _result(s, dict(found, steps=rest, fingerprint=_fingerprint(rest)), state='reversing',
                       reversed_now=reversed_now)
    event_id, then_out = _finish(s, ctx, batch_id, reversal_id, batch, then)
    final = _batch(s, batch_id)
    return {'batch': describe(s, final), 'state': 'reversed', 'reversed_now': reversed_now, 'remaining': 0,
            'blockers': [], 'steps': [], 'fingerprint': None, 'reversal_batch': reversal_id, 'event_id': event_id,
            'dry_run': False, 'then': then_out}


def _stopped(found: dict, g: dict, rec: ListRecord, row: dict, error: BookflowError, reversed_now: int):
    done = found['records'] - found['pending'] + reversed_now
    blocker = _blocker(g, rec, error.code, f"{rec.noun} {row[rec.name_field]}: {error.message}",
                       clears=_clears_for(error, rec), details=error.details)
    return BookflowError('E_BATCH_BLOCKED', message=(
        f"The reversal stopped part way ({done} of {found['records']} records reversed): {blocker['message']} "
        "The rest is unchanged; once that is cleared, the same command goes on from there."),
        details={'batch': found['batch']['id'], 'blockers': [blocker], 'total': 1, 'state': 'reversing',
                 'records': found['records'], 'reversed': done})


def _stopped_item(found: dict, item: dict, error: BookflowError, reversed_now: int):
    """An adapter's own inverse step refused while running (``item`` carries its record and label)."""
    done = found['records'] - found['pending'] + reversed_now
    blocker = {'seq': item.get('seq'), 'record_type': item.get('record_type'), 'record_id': item.get('record_id'),
               'code': item.get('code_for', {}).get(error.code, error.code),
               'message': f"{item.get('label', item.get('record_id'))}: {error.message}",
               'clears': item.get('clears_for', {}).get(error.code), 'details': error.details}
    return BookflowError('E_BATCH_BLOCKED', message=(
        f"The reversal stopped part way ({done} of {found['records']} records reversed): {blocker['message']} "
        "The rest is unchanged; once that is cleared, the same command goes on from there."),
        details={'batch': found['batch']['id'], 'blockers': [blocker], 'total': 1, 'state': 'reversing',
                 'records': found['records'], 'reversed': done})


def _set_state(s, batch_id: str, state: str) -> None:
    """Move a batch between `reversing` and `blocked` in its own transaction."""
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            s.company.conn.execute(sa.update(c.batches).where(
                c.batches.c.id == batch_id, c.batches.c.state.in_(('reversing', 'blocked'))).values(state=state))
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise


def _result(s, found: dict, *, state: str, reversed_now: int, dry_run: bool = False) -> dict:
    batch = _batch(s, found['batch']['id'])
    return {'batch': describe(s, batch), 'state': state, 'reversed_now': reversed_now,
            'remaining': len(found['steps']), 'blockers': found['blockers'][:MAX_BLOCKERS],
            'steps': found['steps'][:MAX_BLOCKERS], 'fingerprint': found['fingerprint'],
            'reversal_batch': batch['reversed_by'], 'event_id': None, 'dry_run': dry_run}


def _reversal_recorder(reversal_id: str, group: dict, index: int, adapter: Adapter | None):
    def record(s, cmd, applied, output):
        version = output.get('version') if isinstance(output, dict) else None
        record_effect(s, reversal_id, record_type=group['record_type'], record_id=group['record_id'],
                      action='reversed', version_after=version,
                      before={'of_seq': group['seq'], 'step': index, 'command': cmd.name},
                      event_id=last_event_id(s))
        if adapter is not None and adapter.on_reversed is not None:
            adapter.on_reversed(s, group, reversal_id)
    return record


def _begin(s, ctx, batch: dict, reason: str | None) -> str:
    """Open the reversal batch and mark the batch `reversing` in one transaction; refused if another reversal of it
    began since this call read it."""
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            state, reversed_by = s.company.conn.execute(sa.select(c.batches.c.state, c.batches.c.reversed_by).where(
                c.batches.c.id == batch['id'])).one()
            if state != 'live' or reversed_by is not None:
                raise BookflowError('E_VERSION_CONFLICT', message=(
                    f"Another reversal of batch {batch['id']} began meanwhile (now {state}); run the command again "
                    'to see it or go on with it.'), details={'field': 'batch', 'state': state,
                                                            'reversal_batch': reversed_by})
            reversal_id = _insert_batch(s, ctx, kind='reversal', label=f"Reversal of {batch['label']}"[:200],
                                        scope=batch['scope'], ordering='record', reversal_of=batch['id'],
                                        reason=reason)
            s.company.conn.execute(sa.update(c.batches).where(c.batches.c.id == batch['id']).values(
                state='reversing', reversed_by=reversal_id, reversed_at=now(), reversal_reason=reason))
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return reversal_id


def _finish(s, ctx, batch_id: str, reversal_id: str, batch: dict, then: Callable[[], Any] | None = None):
    from bookflow.core.audit import write_event_to
    from bookflow.core.registry import Touched
    n = counts(s, batch_id)
    summary = f"reversed batch {batch['label']}: {sum(n.values())} record{'s' if sum(n.values()) != 1 else ''}"
    with s.commits.operation(OWNER, s.company):
        s.company.raw.execute('BEGIN IMMEDIATE')
        try:
            event_id = write_event_to(s.company, ctx, 'batch reverse', summary,
                                      [Touched('batch', batch_id, 'update', None, None, {'state': 'reversed'},
                                               {'state': batch['state']}, db='company')],
                                      actor_id=s.actor.id, actor_kind=s.actor.kind, directive_code=s.directive_code)
            s.company.conn.execute(sa.update(c.batches).where(c.batches.c.id == batch_id).values(
                state='reversed', reversal_event_id=event_id))
            finished = adapter_for(batch['kind']).on_finished
            if finished is not None:
                finished(s, batch)
            then_out = then() if then is not None else None
            s.commits.commit(s.company, OWNER)
        except BaseException:
            if s.company.write_transaction:
                s.company.raw.rollback()
            raise
    return event_id, then_out
