"""The verified history watermark: re-prove only what was written since the last full proof.

`reconciliation_loading.load` proves an account's statement effect storage before any command
reads it: every stored effect version must equal what the adapters derive from the ledger, every
capture, claim, event and receipt must hold together (`reconciliation_storage_validation`). On five
years of books that is several seconds, and nearly all of it re-proves history that cannot have
changed. This module records, per account, the point a proof reached (`statement_watermarks`), and
tells a later load what it must prove again. design/performance.md ("Stage 2") is the account of it.

**What a watermark records.** The code version (a hash of every Bookflow source file),
the census (for each append-only table, its highest row number and row count), a fingerprint per
proved document of the rows that can change under it (its header, its bank-effect pointers, its
stored effect heads), the accounts the proof reached, the ledger setup the derivation reads (each
account's type and currency, the home currency), and a digest over all of that plus the database
schema. It is written only after a validation succeeds, in the same write transaction.

**What a later load proves.** Nothing changes for an account with no row, a row from other code
(`stale`), or a row whose digest no longer matches the file (`broken`: a row deleted from the
verified prefix, a schema change, a changed account type or currency): it is proved in full, as
before. Otherwise only the suffix is proved again, plus every older closure it touches:
- every document with a ledger or effect row written since, a changed fingerprint (a header
  updated in place, a moved head), or not proved before: derived again from the ledger and
  compared with every one of its stored versions, heads and transitions;
- every reconciliation row written since, and every row of a table whose rows can change;
- every capture or draft revision that gained a member, or whose movement group gained a version;
- every event whose transitions name a document derived again;
- the dated general-ledger equality of every account a re-derived document posts to, over the
  whole account (the verified documents contribute their stored current effects and their legs).
Any refusal of that scoped proof is only a reason to prove in full, which then decides: a scoped
proof never refuses books the full proof accepts.

**What it gives up.** A row of the verified prefix changed in place behind Bookflow (an edit to the
file, not a command) is not looked at again by a command; deleting one is (the census counts it).
`company verify`, and every backup and restore, prove the whole file.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import sqlalchemy as sa

FORMAT = 'statement-watermark/1'
TABLE = 'statement_watermarks'
_HERE = Path(__file__).resolve().parent
# Effect-storage tables that carry the document they belong to.
_DOCUMENT_TABLES = ('keys', 'effect_versions', 'effect_legs', 'effect_sources', 'commercial_versions',
                    'deposit_versions')
_code = None


def code_sources() -> list[Path]:
    """Every source whose bytes the derivation and the validation may depend on: the whole package.

    The derivation reaches well past the reconciliation modules (a deposit's stored effect is proved
    by the deposit models and validator, which import the sales, payment and custom-field models,
    and so on), and an import graph drawn by hand is the list that goes stale. So any change to any
    Bookflow source is a change of code version, and costs each account one full proof.
    """
    return sorted(_HERE.parent.rglob('*.py'))


def code_version() -> str:
    """SHA-256 of the derivation and validation code, once per process (as the catalog cache keys it)."""
    global _code
    if _code is None:
        from bookflow.hub.permission_catalog_cache import _bookflow_version
        digest = hashlib.sha256()

        def part(label, data: bytes):
            digest.update(label.encode() + b'\0' + str(len(data)).encode() + b'\0' + data)
        part('format', FORMAT.encode())
        base = _HERE.parent
        for path in code_sources():
            part('source:' + path.relative_to(base).as_posix(), path.read_bytes())
        part('bookflow', _bookflow_version().encode())
        part('python', ('%s|%s' % (sys.version, sys.implementation.cache_tag)).encode())
        _code = digest.hexdigest()
    return _code


def census_tables() -> tuple[str, ...]:
    """The append-only tables whose prefix a watermark counts: every reconciliation table outside
    MUTABLE, every ledger table the adapters read except the two updated in place (a document's
    header, the bank-effect pointers), and the audit events."""
    from bookflow.company import reconciliation_adapters as adapters
    from bookflow.company import schema as c
    from bookflow.company.reconciliation_schema import MUTABLE, PREFIX
    ledger = [n for n in adapters.TABLES if n not in ('transactions', 'bank_effect_current')]
    recon = [n for n in c.metadata.tables if n.startswith(PREFIX) and n not in MUTABLE]
    return tuple(sorted({*ledger, *recon, 'audit_events'}))


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=repr)


def _census(db) -> dict:
    out = {}
    for name in census_tables():
        count, top = db.conn.execute(sa.text(f'SELECT count(*), max(rowid) FROM "{name}"')).one()
        out[name] = [top or 0, count]
    return out


def _prefix_holds(db, census) -> bool:
    """Every counted prefix is still whole: the same row count up to the same highest row."""
    if set(census) != set(census_tables()):
        return False
    for name, (top, count) in census.items():
        found = db.conn.execute(sa.text(f'SELECT count(*), max(rowid) FROM "{name}" WHERE rowid <= :top'),
                                {'top': top}).one()
        if (found[0], found[1] or 0) != (count, top):
            return False
    return True


def _schema(db) -> str:
    rows = db.conn.execute(sa.text("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")).all()
    return hashlib.sha256(_canonical([list(r) for r in rows]).encode()).hexdigest()


def _setup(db) -> dict:
    """The ledger setup the derivation reads: each account's type and currency, the home currency."""
    from bookflow.company import schema as c
    accounts = db.conn.execute(sa.select(c.accounts.c.id, c.accounts.c.type, c.accounts.c.currency)).all()
    home = db.conn.execute(sa.select(c.company_info.c.home_currency)).scalars().all()
    return dict(accounts={r[0]: [r[1], r[2]] for r in accounts}, home=home)


def _setup_holds(stored, now) -> bool:
    """Every account proved with keeps its type and currency, and the home currency is the same.
    An account added since cannot reach a proved document: none of its legs names it."""
    return stored['home'] == now['home'] and all(now['accounts'].get(k) == v for k, v in stored['accounts'].items())


def _chunks(values, size=500):
    ordered = sorted(values)
    for offset in range(0, len(ordered), size):
        yield ordered[offset:offset + size]


def fingerprints(db, identifiers) -> dict:
    """Per document, a fingerprint of the rows that can change under it: its header, the
    bank-effect pointers of its keys, and the stored effect heads of its keys."""
    from bookflow.company import schema as c
    parts = {identity: [] for identity in identifiers}
    for chunk in _chunks(identifiers):
        for row in db.conn.execute(sa.select(c.transactions).where(c.transactions.c.id.in_(chunk))).mappings():
            parts[row['id']].append(('header', sorted(dict(row).items())))
        keys = c.bank_effect_keys
        for row in db.conn.execute(sa.select(keys.c.transaction_id, c.bank_effect_current)
                                   .join(keys, keys.c.id == c.bank_effect_current.c.key_id)
                                   .where(keys.c.transaction_id.in_(chunk))).mappings():
            parts[row['transaction_id']].append(('bank', sorted(dict(row).items())))
        rk, heads = c.reconciliation_keys, c.reconciliation_effect_heads
        for row in db.conn.execute(sa.select(rk.c.transaction_id, heads.c.key_id, heads.c.version_id)
                                   .join(rk, rk.c.id == heads.c.key_id).where(rk.c.transaction_id.in_(chunk))).mappings():
            parts[row['transaction_id']].append(('head', row['key_id'], row['version_id']))
    return {identity: hashlib.sha256(_canonical(sorted(found, key=_canonical)).encode()).hexdigest()[:32]
            for identity, found in parts.items()}


def _digest(code, census, documents, accounts, setup, schema) -> str:
    return hashlib.sha256(_canonical(dict(format=FORMAT, code=code, census=census, documents=documents,
                                          accounts=accounts, schema=schema, setup=setup)).encode()).hexdigest()


class Scope:
    """What a load proves again on top of a holding watermark (see the module docstring)."""

    def __init__(self, documents, written, current):
        self.documents = frozenset(documents)  # derived again from the ledger
        self.written = written                 # reconciliation table -> primary keys written since
        self.current = current                 # every document's fingerprint now


def read(db, account_id):
    from bookflow.company import schema as c
    t = c.statement_watermarks
    row = db.conn.execute(sa.select(t).where(t.c.account_id == account_id)).mappings().first()
    return dict(row) if row is not None else None


def state(db, account_id, accounts, identifiers):
    """`(reason, scope)`: reason 'holds' with the Scope to prove, or 'missing', 'stale' or 'broken'
    with None (prove in full). Any doubt is 'broken'."""
    row = read(db, account_id)
    if row is None:
        return 'missing', None
    if row['code_version'] != code_version():
        return 'stale', None
    try:
        census = json.loads(row['census'])
        documents = json.loads(row['documents'])
        reached = json.loads(row['accounts'])
        setup = json.loads(row['setup'])
        if row['digest'] != _digest(row['code_version'], census, documents, reached, setup, _schema(db)):
            return 'broken', None
        if (not _prefix_holds(db, census) or not _setup_holds(setup, _setup(db)) or sorted(accounts) != reached
                or not set(documents) <= set(identifiers)):
            return 'broken', None
        return 'holds', _scope(db, census, documents, identifiers)
    except (ValueError, TypeError, KeyError, AttributeError):
        return 'broken', None


def _scope(db, census, documents, identifiers):
    from bookflow.company import reconciliation_adapters as adapters
    from bookflow.company import schema as c
    from bookflow.company.reconciliation_schema import PREFIX
    identifiers = set(identifiers)
    current = fingerprints(db, identifiers)
    derive = {i for i in identifiers if documents.get(i) != current[i]}
    ledger = [n for n in adapters.TABLES if n not in ('transactions', 'bank_effect_current')]
    for name in ledger:
        found = db.conn.execute(sa.text(f'SELECT DISTINCT transaction_id FROM "{name}" WHERE rowid > :top'),
                                {'top': census[name][0]}).scalars()
        derive.update(i for i in found if i in identifiers)
    written = {}
    for name in census:
        if not name.startswith(PREFIX):
            continue
        table = c.metadata.tables[name]
        columns = [col.name for col in table.primary_key.columns]
        rows = db.conn.execute(sa.select(*(table.c[col] for col in columns)).where(
            sa.literal_column('rowid') > census[name][0])).all()
        written[name.removeprefix(PREFIX)] = {tuple(r) for r in rows}
    for short in _DOCUMENT_TABLES:
        table = c.metadata.tables[PREFIX + short]
        derive.update(db.conn.execute(sa.select(sa.distinct(table.c.transaction_id)).where(
            sa.literal_column('rowid') > census[PREFIX + short][0])).scalars())
    return Scope(derive, written, current)


def verified(db, rows, identifiers, scope):
    """What the documents outside the scope stand for (`reconciliation_adapters.Verified`)."""
    from bookflow.company import reconciliation_adapters as adapters
    from bookflow.company import schema as c
    rest = set(identifiers) - scope
    keys = {k['id'] for k in rows['keys'] if k['transaction_id'] in rest}
    versions = {v['id']: v for v in rows['effect_versions']}
    current = tuple(sorted((v['id'], v['account_id'], v['active'], v['effective_date'], v['signed_debit'])
                           for v in (versions[h['version_id']] for h in rows['effect_heads'] if h['key_id'] in keys)))
    legs = []
    lines, batches = c.posting_lines, c.posting_batches
    for chunk in _chunks(rest):
        legs.extend((r[0], r[1], r[2] - r[3]) for r in db.conn.execute(
            sa.select(lines.c.account_id, batches.c.effective_date, lines.c.debit_minor_units, lines.c.credit_minor_units)
            .join(batches, batches.c.id == lines.c.batch_id).where(lines.c.transaction_id.in_(chunk))))
    return adapters.Verified(current=current, legs=tuple(sorted(legs)))


def advance(db, account_id, accounts, documents):
    """Record the proof just made, inside the write transaction it was made in."""
    from bookflow.company import schema as c
    from bookflow.core import clock
    census = _census(db)
    reached = sorted(accounts)
    code = code_version()
    seq = db.conn.execute(sa.text('SELECT max(seq) FROM audit_events')).scalar()
    setup = json.loads(_canonical(_setup(db)))
    row = dict(account_id=account_id, code_version=code, through_seq=seq, census=_canonical(census),
               documents=_canonical(documents), accounts=_canonical(reached), setup=_canonical(setup),
               digest=_digest(code, census, documents, reached, setup, _schema(db)), verified_at=clock.now_iso())
    t = c.statement_watermarks
    if db.conn.execute(sa.select(t.c.account_id).where(t.c.account_id == account_id)).first() is None:
        db.conn.execute(t.insert().values(**row))
    else:
        db.conn.execute(t.update().where(t.c.account_id == account_id).values(**row))


def statement_accounts(db) -> list[str]:
    """Every account whose statement effect storage there is to prove: each bank and card
    account, and any other account statement effects or a reconciliation name."""
    from bookflow.company import schema as c
    found = set(db.conn.execute(sa.select(c.accounts.c.id).where(c.accounts.c.type.in_(('bank', 'credit_card')))).scalars())
    found.update(db.conn.execute(sa.select(sa.distinct(c.reconciliation_effect_versions.c.account_id))).scalars())
    found.update(db.conn.execute(sa.select(c.reconciliation_accounts.c.account_id)).scalars())
    return sorted(found)


def verify(db, account_id=None) -> list[dict]:
    """Prove each account's statement effect storage in full, whatever its watermark says.

    The proof every reconciliation command made before watermarks existed: every stored effect
    version re-derived from the ledger and compared, field by field, with every capture, claim,
    event, receipt and attempt checked against the rows it stored. It reads; it writes nothing
    (a watermark moves only inside a reconciliation write). Each result is `proved`,
    `unsupported` (the account holds a document the statement adapters cannot represent, or is in
    a foreign currency: nothing is stored to prove), `pending` (posted documents still owe their
    statement effects to the next command) or `failed`, with the check that refused.
    This is an integrity check, so it asks no record-level permission; its callers gate it.
    """
    from bookflow.company import reconciliation_loading as loading
    from bookflow.company import reconciliation_materialization as materialization
    from bookflow.company import reconciliation_preparation as preparation
    results = []
    for account in ([account_id] if account_id is not None else statement_accounts(db)):
        out = dict(account_id=account, outcome='proved', check=None, documents=0, effect_versions=0,
                   certificates=0, watermark=None)
        try:
            materialization.assert_materialized(db)
            accounts, identifiers, rows = loading.gather(db, account)
            out.update(documents=len(identifiers), effect_versions=len(rows['effect_versions']),
                       certificates=len(rows['certificates']))
            try:
                out['watermark'] = state(db, account, accounts, identifiers)[0]
            except Exception:
                out['watermark'] = 'broken'
            loading.prove_whole(db, rows, identifiers)
        except preparation.ReconciliationError as exc:
            reason = (exc.details or {}).get('reason') or exc.rule
            if reason == 'E_RECONCILIATION_UNSUPPORTED':
                out.update(outcome='unsupported', check='unsupported_document')
            elif (exc.details or {}).get('check') == 'effects_pending':
                # Posted documents whose statement effects the next command materializes (an
                # upgrade's backfill not yet finished): not damage, and nothing to prove yet.
                out.update(outcome='pending', check='effects_pending')
            else:
                out.update(outcome='failed', check=(exc.details or {}).get('check') or reason)
        results.append(out)
    return results
