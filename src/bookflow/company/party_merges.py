"""Merging a duplicate customer or vendor into another by an audited alias (R117).

A merge records "B is merged into A" in ``party_merges`` and changes nothing posted. Every
read that groups or filters by customer or vendor resolves B to A through the two SQL
fragments here, so A/R and A/P, the aging reports, statements and balances show one entry.
B is made inactive, which hides it from pickers and refuses it for any new reference, and it
cannot be made active again while the merge stands. Undo ends the alias with its own stamp
and reason and makes B active again if the merge was what made it inactive.

Settlement needs nothing new. A customer's payment carries one component key per party and
already pays the invoices of the customer's whole family; a merged-away customer joins the
family of its survivor, so a payment received from A for B's old invoice is keyed to B,
credits B's receivable, and satisfies every existing trigger unchanged.

Merges are one level deep: a survivor is never itself merged away, and nothing is merged into
an entry that is merged away (the migration's insert trigger holds both). To fold a survivor
into a third entry, undo the merges into it first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bookflow.company import parties, schema
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError, require_reason
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched

KINDS = ("customer", "vendor")
_TABLES = {"customer": "customers", "vendor": "vendors"}


def survivor_sql(kind: str, expr: str) -> str:
    """SQL for the entry ``expr`` reads as: its survivor while a merge stands, else itself."""
    assert kind in KINDS
    return (f"coalesce((SELECT party_merge_.survivor_id FROM party_merges party_merge_ "
            f"WHERE party_merge_.party_kind='{kind}' AND party_merge_.merged_id={expr} "
            f"AND party_merge_.undone_at IS NULL), {expr})")


def merged_sql(kind: str) -> str:
    """SQL selecting the ids merged away while their merge stands."""
    assert kind in KINDS
    return f"SELECT merged_id FROM party_merges WHERE party_kind='{kind}' AND undone_at IS NULL"


def family_cte(name: str = "family", seed: str = "?") -> str:
    """A recursive CTE body ``name(id)``: a customer, its jobs, and every customer merged into
    any of them. A merged-away job is not counted under its old parent, only under its survivor,
    so no balance is counted twice."""
    return (f"{name}(id) AS (SELECT id FROM customers WHERE id={seed} "
            f"UNION SELECT child_.id FROM customers child_ JOIN {name} up_ ON child_.parent_id=up_.id "
            f"WHERE child_.id NOT IN ({merged_sql('customer')}) "
            f"UNION SELECT m_.merged_id FROM party_merges m_ JOIN {name} up_ ON m_.survivor_id=up_.id "
            f"WHERE m_.party_kind='customer' AND m_.undone_at IS NULL)")


def survivor(db, kind: str, party_id: str | None) -> str | None:
    if party_id is None:
        return None
    row = db.raw.execute(f"SELECT {survivor_sql(kind, ':party')}", {"party": party_id}).fetchone()
    return row[0]


def live_merge(db, kind: str, merged_id: str) -> dict[str, Any] | None:
    t = schema.party_merges
    row = db.conn.execute(sa.select(t).where(t.c.party_kind == kind, t.c.merged_id == merged_id,
                                             t.c.undone_at.is_(None))).mappings().first()
    return dict(row) if row else None


def merges(db, kind: str, *, party_id: str | None = None, include_undone: bool = False) -> list[dict[str, Any]]:
    t = schema.party_merges
    q = sa.select(t).where(t.c.party_kind == kind)
    if party_id is not None:
        q = q.where(sa.or_(t.c.merged_id == party_id, t.c.survivor_id == party_id))
    if not include_undone:
        q = q.where(t.c.undone_at.is_(None))
    return [dict(r) for r in db.conn.execute(q.order_by(t.c.created_at, t.c.id)).mappings()]


def _refuse(problem: str, **details: Any) -> BookflowError:
    return BookflowError("E_MERGE_REFUSED", message=f"Merge refused: {problem}.", details={"problem": problem, **details})


def require_person(s, kind: str) -> None:
    if s.actor is None or s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", details={"capability": kind, "required_role": "human"},
                            message=f"Only a person may merge or unmerge {kind}s; an agent cannot.")


# --- what moves -------------------------------------------------------------------------

def _documents(db, kind: str, party_id: str) -> dict[str, int]:
    """Documents whose current revision names the entry, counted by type."""
    rows = db.raw.execute("""SELECT t.type, count(*) FROM transactions t
        JOIN transaction_revisions r ON r.id=t.current_revision_id
        WHERE r.name_type=? AND r.name_id=? GROUP BY t.type ORDER BY t.type""", (kind, party_id)).fetchall()
    return {str(kind_): int(n) for kind_, n in rows}


def _currencies(db, kind: str, party_id: str) -> set[str]:
    rows = db.raw.execute("""SELECT DISTINCT r.currency FROM transactions t
        JOIN transaction_revisions r ON r.id=t.current_revision_id
        WHERE r.name_type=? AND r.name_id=?""", (kind, party_id)).fetchall()
    return {str(r[0]) for r in rows}


def _balance(db, kind: str, party_id: str) -> int:
    """Open A/R (customer) or A/P (vendor) of exactly this entry's own posted lines, unresolved."""
    from bookflow.company.customer_balances import register_functions
    register_functions(db)
    sign = "l.debit_minor_units-l.credit_minor_units" if kind == "customer" else "l.credit_minor_units-l.debit_minor_units"
    account = "accounts_receivable" if kind == "customer" else "accounts_payable"
    value = db.raw.execute(f"""SELECT bookflow_sum_int({sign}) FROM posting_lines l
        JOIN accounts a ON a.id=l.account_id
        WHERE l.name_type=? AND l.name_id=? AND a.type=?""", (kind, party_id, account)).fetchone()[0]
    return int(value or "0")


def _merged_into(db, kind: str, survivor_id: str) -> list[str]:
    return [r[0] for r in db.raw.execute(
        "SELECT merged_id FROM party_merges WHERE party_kind=? AND survivor_id=? AND undone_at IS NULL",
        (kind, survivor_id))]


def _combined_balance(db, kind: str, party_id: str) -> int:
    return sum(_balance(db, kind, x) for x in (party_id, *_merged_into(db, kind, party_id)))


def _home(db) -> str:
    return str(db.conn.execute(sa.select(schema.company_info.c.home_currency)).scalar_one())


@dataclass(frozen=True)
class MergePlan:
    kind: str
    merged: dict[str, Any]
    survivor: dict[str, Any]
    existing: dict[str, Any] | None
    documents: dict[str, int]
    merged_balance: int
    survivor_balance: int

    @property
    def changed(self) -> bool:
        return self.existing is None


def plan_merge(db, kind: str, merged_selector: str, survivor_selector: str) -> MergePlan:
    if kind not in KINDS:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "kind", "problem": "is not customer or vendor"}]})
    merged = dict(parties.resolve_party(db, kind, merged_selector))
    target = dict(parties.resolve_party(db, kind, survivor_selector))
    ids = dict(merged_id=merged["id"], survivor_id=target["id"])
    if merged["id"] == target["id"]:
        raise _refuse(f"a {kind} cannot be merged into itself", **ids)
    existing = live_merge(db, kind, merged["id"])
    if existing is not None:
        if existing["survivor_id"] == target["id"]:
            return MergePlan(kind, merged, target, existing, _documents(db, kind, merged["id"]),
                             _balance(db, kind, merged["id"]), _combined_balance(db, kind, target["id"]))
        raise _refuse(f"this {kind} is already merged into another one; undo that merge first",
                      current_survivor_id=existing["survivor_id"], **ids)
    if live_merge(db, kind, target["id"]) is not None:
        raise _refuse(f"the {kind} to merge into is itself merged away; merge into its survivor instead", **ids)
    if _merged_into(db, kind, merged["id"]):
        raise _refuse(f"other {kind}s are merged into this one; undo those merges, then merge each into the survivor", **ids)
    if kind == "customer":
        merged_job, target_job = merged.get("parent_id") is not None, target.get("parent_id") is not None
        if merged_job and not target_job:
            raise _refuse("a job cannot be merged into a customer that is not a job", **ids)
        if target_job and not merged_job:
            raise _refuse("a customer cannot be merged into a job", **ids)
        lineage = db.raw.execute("""WITH RECURSIVE up(id, parent_id) AS (
              SELECT id, parent_id FROM customers WHERE id=?
              UNION SELECT c.id, c.parent_id FROM customers c JOIN up ON c.id=up.parent_id) SELECT id FROM up""",
            (target["id"],)).fetchall()
        if merged["id"] in {r[0] for r in lineage}:
            raise _refuse("a customer cannot be merged into one of its own jobs", **ids)
        if db.raw.execute("SELECT 1 FROM customers WHERE parent_id=? LIMIT 1", (merged["id"],)).fetchone():
            raise _refuse("this customer has jobs of its own; move or merge its jobs first", **ids)
    if kind == "vendor" and _balance(db, kind, merged["id"]) != 0:
        # A bill payment never crosses vendors, so the merged vendor's open bills could not be
        # paid from the survivor; settle them first.
        raise _refuse("this vendor has an open payable balance; pay or settle its bills and credits first", **ids)
    link = db.raw.execute(f"SELECT 1 FROM customer_vendor_links WHERE {kind}_id=? AND active=1", (merged["id"],)).fetchone()
    if link:
        raise _refuse(f"this {kind} is linked to a {'vendor' if kind == 'customer' else 'customer'}; unlink it first", **ids)
    mine, theirs = _currencies(db, kind, merged["id"]), _currencies(db, kind, target["id"]) or {_home(db)}
    if mine - theirs:
        raise _refuse(f"this {kind} has documents in a currency the other has never used",
                      currencies=sorted(mine - theirs), **ids)
    # The merged entry is made inactive; anything that refuses that refuses the merge too.
    parties.plan_party_active_change(db, kind, merged["id"], False, actor_id="", via="")
    return MergePlan(kind, merged, target, None, _documents(db, kind, merged["id"]),
                     _balance(db, kind, merged["id"]), _combined_balance(db, kind, target["id"]))


def preview(db, plan: MergePlan) -> dict[str, Any]:
    from bookflow.core.money import Money
    currency = _home(db)
    after = plan.survivor_balance + (plan.merged_balance if plan.changed else 0)
    label = lambda row: row.get("full_name") or row["name"]
    return dict(
        merge_id=plan.existing["id"] if plan.existing else None, changed=plan.changed,
        merged_id=plan.merged["id"], merged_name=label(plan.merged),
        survivor_id=plan.survivor["id"], survivor_name=label(plan.survivor),
        documents=[dict(type=k, count=v) for k, v in plan.documents.items()],
        document_count=sum(plan.documents.values()),
        merged_balance=Money(plan.merged_balance, currency).to_dict(),
        survivor_balance=Money(plan.survivor_balance, currency).to_dict(),
        survivor_balance_after=Money(after, currency).to_dict(),
    )


def _touched(kind, mutation, action):
    return Touched(kind, str(mutation.after["id"]), action, int(mutation.before["version"]),
                   int(mutation.after["version"]), dict(mutation.after_snapshot), dict(mutation.before_snapshot), db="company")


def apply_merge(s, ctx, plan: MergePlan, command: str) -> tuple[str, list[Touched]]:
    """Write the merge row, make the merged entry inactive, and write one audit event."""
    require_reason(ctx.reason)
    db, kind = s.company, plan.kind
    at, event, merge_id = clock.now_iso(), new_id(), new_id()
    change = parties.plan_party_active_change(db, kind, plan.merged["id"], False,
                                              actor_id=s.actor.id, via=ctx.interface.value, at=at)
    row = dict(id=merge_id, party_kind=kind, merged_id=plan.merged["id"], survivor_id=plan.survivor["id"],
               deactivated_merged=change.changed, reason=ctx.reason.strip(), created_at=at, created_by=s.actor.id,
               created_via=ctx.interface.value, audit_event_id=event)
    touched = [_touched(kind, m, "merge") for m in change.mutations]
    touched.append(Touched("party_merge", merge_id, "create", None, 1, dict(row), db="company"))
    audit.write_event_to(db, ctx, command, f"Merged {kind} {plan.merged['name']} into {plan.survivor['name']}",
                         touched, actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    db.conn.execute(schema.party_merges.insert().values(**row))
    parties.persist_party_active_change(db, change)
    return merge_id, touched


@dataclass(frozen=True)
class UnmergePlan:
    kind: str
    merged: dict[str, Any]
    merge: dict[str, Any] | None
    last: dict[str, Any] | None


def plan_unmerge(db, kind: str, merged_selector: str) -> UnmergePlan:
    merged = dict(parties.resolve_party(db, kind, merged_selector))
    merge = live_merge(db, kind, merged["id"])
    if merge is None:
        last = next(iter(reversed(merges(db, kind, party_id=merged["id"], include_undone=True))), None)
        if last is None or last["merged_id"] != merged["id"]:
            raise _refuse(f"this {kind} is not merged into another one", merged_id=merged["id"])
        return UnmergePlan(kind, merged, None, last)
    return UnmergePlan(kind, merged, merge, None)


def apply_unmerge(s, ctx, plan: UnmergePlan, command: str) -> list[Touched]:
    require_reason(ctx.reason)
    db, kind, merge = s.company, plan.kind, plan.merge
    at, event = clock.now_iso(), new_id()
    stamp = dict(undone_at=at, undone_by=s.actor.id, undone_via=ctx.interface.value,
                 undo_reason=ctx.reason.strip(), undo_audit_event_id=event)
    t = schema.party_merges
    touched = [Touched("party_merge", merge["id"], "unmerge", 1, 2, {**merge, **stamp}, dict(merge), db="company")]
    change = None
    if merge["deactivated_merged"]:
        change = parties.plan_party_active_change(db, kind, merge["merged_id"], True, actor_id=s.actor.id,
                                                  via=ctx.interface.value, at=at, unmerging=True)
        touched.extend(_touched(kind, m, "unmerge") for m in change.mutations)
    survivor_name = db.raw.execute(f"SELECT name FROM {_TABLES[kind]} WHERE id=?", (merge["survivor_id"],)).fetchone()[0]
    audit.write_event_to(db, ctx, command, f"Undid merge of {kind} {plan.merged['name']} into {survivor_name}",
                         touched, actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    db.conn.execute(t.update().where(t.c.id == merge["id"], t.c.undone_at.is_(None)).values(**stamp))
    if change is not None:
        parties.persist_party_active_change(db, change)
    return touched
