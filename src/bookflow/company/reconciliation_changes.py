"""What correcting or voiding a reconciled transaction does to the reconciliation that cleared it.

A finished reconciliation -- a certificate, or the opening balance an account adopted -- claims
the exact version of every statement movement it cleared. Correcting or voiding one of those
transactions afterwards is allowed, as it is in the anchor: the ledger moves, the claim keeps
the version it cleared, and the reconciliation stops tying to its statement by exactly what the
change moved. Nothing here refuses or posts anything. It says so, plainly, on the preview and in
the saved result, and it gives the reconciliation discrepancy report the same arithmetic.

**What a reconciliation's cleared balance is now.** For a certificate: every movement it counted
as cleared (`opening_covered`, `prior_cleared`, `selected`), each at its current version, counted
when that version is still live, still on the reconciled account and still dated on or before
the statement date. For an opening: its covered movements the same way, at the opening date.
The statement's ending balance (the opening's adopted balance) is what that sum was when it was
certified; the difference between the two is what a later change did to it.

**Which reconciliation a change is reported against.** The one whose claim holds the movement:
the statement it was reconciled on, or the opening balance that adopted it. Later statements of
the same account inherit the same difference through their beginning balance; the discrepancy
report shows each of them.

**No difference, no warning.** A change that leaves every reconciled figure where it was -- a
memo, a payee, a number, a class, a date still on or before the statement date -- does not move
a reconciliation, and the anchor does not warn about it either.
"""
from __future__ import annotations

import json
from collections import defaultdict

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.errors import BookflowError
from bookflow.core.money import Money

# What a certificate counted as cleared; `outstanding` is what it did not.
CLEARED = ('opening_covered', 'prior_cleared', 'selected')
VERSION_FIELDS = ('active', 'account_id', 'account_type', 'effective_date', 'signed_debit')


def amount(version, account_id, cutoff):
    """What one movement version adds to a reconciliation of `account_id` at `cutoff`.

    The statement's own sign: a bank balance goes up with a debit, a card balance -- what is
    owed -- with a credit. A version that is voided, on another account or dated after the
    statement is no part of it.
    """
    if (version is None or not version['active'] or version['account_id'] != account_id
            or version['effective_date'] > cutoff):
        return 0
    return -version['signed_debit'] if version['account_type'] == 'credit_card' else version['signed_debit']


def _versions(s, identifiers):
    table = c.reconciliation_effect_versions
    found, ordered = {}, sorted(i for i in set(identifiers) if i)
    for offset in range(0, len(ordered), 200):
        for row in s.company.conn.execute(sa.select(table.c.id, *(table.c[f] for f in VERSION_FIELDS)).where(
                table.c.id.in_(ordered[offset:offset + 200]))).mappings():
            found[row['id']] = dict(row)
    return found


def _held(s, *, transactions=(), keys=()):
    """Live claims on these documents' (or these keys') statement movements.

    Only a finished reconciliation claims anything: a draft's marks are not claims, so a
    movement ticked on an unfinished reconciliation is not held here and keeps the draft's own
    rules.
    """
    k, m, claims, heads = (c.reconciliation_keys, c.reconciliation_current_members,
                           c.reconciliation_claims, c.reconciliation_effect_heads)
    ordered, column = (sorted(set(transactions)), k.c.transaction_id) if transactions else (sorted(set(keys)), k.c.id)
    found = []
    for offset in range(0, len(ordered), 200):
        found.extend(dict(row) for row in s.company.conn.execute(
            sa.select(k.c.id.label('key_id'), k.c.producer, k.c.transaction_id, k.c.role,
                      k.c.commercial_line_id, k.c.deposit_key_id,
                      claims.c.account_id, claims.c.certificate_id, claims.c.opening_id,
                      claims.c.version_id.label('claimed_version_id'),
                      heads.c.version_id.label('head_version_id'))
            .join(m, m.c.key_id == k.c.id).join(claims, claims.c.id == m.c.claim_id)
            .outerjoin(heads, heads.c.key_id == k.c.id)
            .where(column.in_(ordered[offset:offset + 200]))).mappings())
    return found


def _owner(s, row):
    """The reconciliation holding a claim: kind, identity, account, cutoff and reconciled balance."""
    if row['certificate_id']:
        t = c.reconciliation_certificates
        found = s.company.conn.execute(sa.select(t).where(t.c.id == row['certificate_id'])).mappings().one()
        return dict(kind='statement', id=found['id'], account_id=found['account_id'],
                    cutoff=found['statement_date'], reconciled=found['ending_balance'], currency=found['currency'])
    t = c.reconciliation_openings
    found = s.company.conn.execute(sa.select(t).where(t.c.id == row['opening_id'])).mappings().one()
    return dict(kind='opening', id=found['id'], account_id=found['account_id'],
                cutoff=found['opening_date'], reconciled=found['balance'], currency=found['currency'])


def cleared(s, owner):
    """A reconciliation's cleared balance as its movements stand now."""
    v, heads = c.reconciliation_effect_versions, c.reconciliation_effect_heads
    if owner['kind'] == 'statement':
        members = c.reconciliation_certificate_members
        where = sa.and_(members.c.certificate_id == owner['id'], members.c.classification.in_(CLEARED))
    else:
        members = c.reconciliation_opening_members
        where = sa.and_(members.c.opening_id == owner['id'], members.c.classification == 'covered')
    rows = s.company.conn.execute(
        sa.select(*(v.c[f] for f in VERSION_FIELDS)).select_from(members)
        .join(heads, heads.c.key_id == members.c.key_id).join(v, v.c.id == heads.c.version_id)
        .where(where)).mappings()
    return sum(amount(row, owner['account_id'], owner['cutoff']) for row in rows)


def _money(minor_units, currency):
    return Money(minor_units, currency).amount


def _account_label(s, account_id):
    row = s.company.conn.execute(sa.select(c.accounts.c.full_name).where(c.accounts.c.id == account_id)).first()
    return row[0] if row else account_id


def _sentence(s, owner, delta, now, *, saved):
    """One warning, in the words a bookkeeper would use."""
    currency = owner['currency']
    account = _account_label(s, owner['account_id'])
    if owner['kind'] == 'statement':
        where = f"This transaction was reconciled on the {account} statement dated {owner['cutoff']}."
        against, noun = "the statement's ending balance", 'reconciliation'
    else:
        where = (f"This transaction is part of the opening balance adopted for {account} "
                 f"on {owner['cutoff']}.")
        against, noun = 'the adopted opening balance', 'opening balance'
    off = now - owner['reconciled']
    if off == 0:
        verb = 'brings' if saved else 'will bring'
        return (f"{where} This change {verb} that {noun} back to tie: its cleared balance "
                f"{'is' if saved else 'will be'} {_money(now, currency)} {currency}, matching {against}.")
    left = 'This change left' if saved else 'Saving this change will leave'
    becomes = 'is now' if saved else 'becomes'
    return (f"{where} {left} that {noun} off by {_money(abs(off), currency)} {currency}: its "
            f"cleared balance {becomes} {_money(now, currency)} against {against} of "
            f"{_money(owner['reconciled'], currency)}, until the reconciliation is re-done. "
            f"The reconciliation discrepancy report shows the change.")


def _warn(s, changes, *, saved):
    """Warnings for (held row, version before, version after) triples, one per reconciliation moved."""
    deltas, owners = defaultdict(int), {}
    for row, before, after in changes:
        key = row['certificate_id'] or row['opening_id']
        if key not in owners:
            owners[key] = _owner(s, row)
        owner = owners[key]
        deltas[key] += (amount(after, owner['account_id'], owner['cutoff'])
                        - amount(before, owner['account_id'], owner['cutoff']))
    found = []
    for key in sorted(owners, key=lambda k: (owners[k]['cutoff'], k)):
        delta = deltas[key]
        if delta == 0:
            continue
        owner = owners[key]
        now = cleared(s, owner) + (0 if saved else delta)
        found.append(_sentence(s, owner, delta, now, saved=saved))
    return found


def _has_tables(s):
    return 'reconciliation_current_members' in c.metadata.tables and s.company is not None


def saved(s, observed):
    """After a write: what the movements it moved did to the reconciliations holding them.

    `observed` maps each key whose head the write moved to the head it had before; the stored
    heads are already the new ones.
    """
    if not observed or not _has_tables(s):
        return []
    held = _held(s, keys=observed)
    if not held:
        return []
    versions = _versions(s, [observed[r['key_id']] for r in held] + [r['head_version_id'] for r in held])
    return _warn(s, [(row, versions.get(observed[row['key_id']]), versions.get(row['head_version_id']))
                     for row in held], saved=True)


def _targets(plan):
    """(what to project, the documents it changes) for a write's plan, or (None, empty)."""
    data = getattr(plan, 'data', None)
    if not isinstance(data, dict):
        return None, set()
    if data.get('prospective') is not None:
        # A check, card charge, transfer or register entry is a journal underneath, and keeps
        # the journal plan it was built from for exactly this.
        return _targets(data['prospective'])
    prepared = data.get('prepared')
    if prepared is not None and hasattr(prepared, 'input_json'):
        identity = json.loads(prepared.input_json).get('deposit')
        return prepared, ({identity} if identity else set())
    return plan, {row['id'] for row in (data.get('header'), data.get('before'))
                  if isinstance(row, dict) and row.get('id')}


def _component(row):
    from bookflow.company.reconciliation_materialization import _stored_component
    return _stored_component(row)


def preview(s, ctx, cmd_name, plan):
    """Before a write: what saving it would do to the reconciliations holding its movements.

    A void takes every movement of the document it voids to nothing. A correction is projected
    by the statement adapters from the exact plan the save would write; a plan they cannot
    project still warns, naming the reconciliation, without a figure.
    """
    if not _has_tables(s):
        return []
    projected, targets = _targets(plan)
    if not targets:
        return []
    held = _held(s, transactions=targets)
    if not held:
        return []
    versions = _versions(s, [r['head_version_id'] for r in held])
    if cmd_name.endswith(' void'):
        return _warn(s, [(row, versions.get(row['head_version_id']), None) for row in held], saved=False)
    from bookflow.company import reconciliation_adapters as adapters
    from bookflow.company.reconciliation_models import ChangedEffects
    try:
        changes = adapters.prepare_prospective(s, ctx, projected).changes
    except (adapters.Unsupported, adapters.Corrupt, BookflowError, TypeError):
        changes = None
    if not isinstance(changes, ChangedEffects):
        return _unprojected(s, held)
    before = {(v.ref.producer, v.ref.transaction_id, v.ref.role, v.ref.component_id) for v in changes.before}
    after = {(v.ref.producer, v.ref.transaction_id, v.ref.role, v.ref.component_id):
             {f: getattr(v, f) for f in VERSION_FIELDS} for v in changes.after}
    moved = []
    for row in held:
        component = _component(row)
        if component in after:
            moved.append((row, versions.get(row['head_version_id']), after[component]))
        elif component in before:
            moved.append((row, versions.get(row['head_version_id']), None))
    return _warn(s, moved, saved=False)


def _unprojected(s, held):
    found, seen = [], set()
    for row in held:
        key = row['certificate_id'] or row['opening_id']
        if key in seen:
            continue
        seen.add(key)
        owner = _owner(s, row)
        account = _account_label(s, owner['account_id'])
        what = (f"the {account} statement dated {owner['cutoff']}" if owner['kind'] == 'statement'
                else f"the opening balance adopted for {account} on {owner['cutoff']}")
        found.append(f"This transaction was reconciled on {what}. If this change moves its amount, "
                     f"account or date, that reconciliation will no longer tie until it is re-done; "
                     f"the saved result and the reconciliation discrepancy report will say by how much.")
    return found


def with_warnings(output, lines):
    """The same write output with these warnings added after its own, once each."""
    if not lines or output is None or 'warnings' not in type(output).model_fields:
        return output
    current = list(output.warnings or [])
    return output.model_copy(update={'warnings': current + [line for line in lines if line not in current]})
