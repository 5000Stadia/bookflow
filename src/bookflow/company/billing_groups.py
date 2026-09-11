"""Billing groups: a named, reusable set of customers a batch of invoices is addressed to.

A group holds nothing commercial. It does not carry terms, a price level, a tax code or a
message, because those belong to the customers in it and are resolved for each of them when an
invoice is actually written. A group is a name and an ordered set of members, and the order is
the order a batch invoices in, so what a person sees on the preview is the order they built.

**What deleting a customer does.** Nothing here deletes a customer, because Bookflow has no
command that does: a customer is deactivated, and `undo` of its creation deactivates it too.
The rule this module makes explicit is the one that governs the act that would remove the row
for good, and the rule is **refuse**. `billing_group_members.customer_id` is a real foreign key,
so an orphan membership cannot exist in storage at all, and `undo._HARD_REFERENCES` names this
table against the `customer` record type so the refusal reads as a conflict naming the group
rather than as a foreign-key failure. Removing the customer from its groups first is one
command, and it is the person's decision rather than a silent shrinking of a set they built.

Deactivation is deliberately left alone. An inactive member stays in its groups, and the next
batch reports that customer as a failed row carrying `E_INACTIVE_REFERENCE`, which is visible
and retryable, where dropping it from the run would quietly stop sending an invoice somebody
is still expecting.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from bookflow.company import list_service, schema as c
from bookflow.company.lists import get_list_definition, normalize_display_name
from bookflow.core import clock
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id
from bookflow.core.registry import Applied, Plan, Touched
from bookflow.hub.users import common


def _invalid(field: str, problem: str) -> BookflowError:
    return BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": problem}]})


def resolve_customer(s, selector: Any, *, field: str = "customers") -> dict[str, Any]:
    """One customer or job, by id or by its canonical list name. Inactive rows resolve."""
    definition = get_list_definition("customer")
    try:
        return list_service.resolve_selector(s.company, c.customers, definition, selector)
    except BookflowError as exc:
        if exc.code == "E_RECORD_NOT_FOUND":
            exc.details.setdefault("field", field)
        raise


def resolve_group(s, selector: Any) -> dict[str, Any]:
    """One billing group, by id or by its exact name, ignoring case."""
    from bookflow.core.ids import is_ulid, normalize_ulid
    table = c.billing_groups
    trimmed = selector.strip() if isinstance(selector, str) else selector
    row = None
    if isinstance(trimmed, str) and is_ulid(trimmed):
        row = s.company.conn.execute(
            sa.select(table).where(table.c.id == normalize_ulid(trimmed))).mappings().first()
    if row is None and isinstance(trimmed, str) and trimmed:
        _, key = normalize_display_name(trimmed, field="billing_group")
        row = s.company.conn.execute(sa.select(table).where(table.c.name_key == key)).mappings().first()
    if row is None:
        raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": "billing_group", "selector": selector})
    return dict(row)


def members(s, group_id: str) -> list[dict[str, Any]]:
    """The group's members in placement order, each with its current customer label."""
    m, cu = c.billing_group_members, c.customers
    rows = s.company.conn.execute(
        sa.select(m, cu.c.full_name, cu.c.active)
        .join(cu, cu.c.id == m.c.customer_id)
        .where(m.c.group_id == group_id)
        .order_by(m.c.position, m.c.customer_id)).mappings().all()
    return [dict(row) for row in rows]


def _member_output(row: dict[str, Any]) -> dict[str, Any]:
    return dict(customer_id=row["customer_id"], customer_label=row["full_name"],
                active=bool(row["active"]), position=row["position"],
                created_at=row["created_at"], created_by=row["created_by"],
                created_via=row["created_via"])


def show_output(s, group: dict[str, Any]):
    from bookflow.company.batch_invoicing_models import BillingGroupShowOutput
    rows = members(s, group["id"])
    return BillingGroupShowOutput(
        **{key: group[key] for key in ("id", "version", "created_at", "created_by", "created_via",
                                       "updated_at", "updated_by", "updated_via", "name")},
        member_count=len(rows), members=[_member_output(row) for row in rows])


def _check_version(group: dict[str, Any], expected: int | None) -> None:
    if expected is not None and expected != group["version"]:
        raise BookflowError("E_VERSION_CONFLICT", details={
            "record_type": "billing_group", "record_id": group["id"],
            "current_version": group["version"], "expected_version": expected,
            "updated_at": group["updated_at"], "updated_by": group["updated_by"]})


def _unique_name(s, name: str, *, own: str | None = None) -> tuple[str, str]:
    display, key = normalize_display_name(name, field="name")
    query = sa.select(c.billing_groups.c.id).where(c.billing_groups.c.name_key == key)
    if own is not None:
        query = query.where(c.billing_groups.c.id != own)
    clash = s.company.conn.execute(query).first()
    if clash is not None:
        raise BookflowError("E_NAME_TAKEN", details={
            "record_type": "billing_group", "name": display, "record_id": clash[0]})
    return display, key


def _member_rows(s, group_id: str, selectors, *, at: str, actor: str, via: str, start: int):
    """Rows for new members, each carrying the customer it resolved to for the preview."""
    rows, seen = [], set()
    for offset, selector in enumerate(selectors or ()):
        customer = resolve_customer(s, selector)
        if customer["id"] in seen:
            raise _invalid("customers", "name a customer once; a customer cannot appear twice in one list")
        seen.add(customer["id"])
        rows.append(dict(id=new_id(), group_id=group_id, customer_id=customer["id"],
                         position=start + offset, created_at=at, created_by=actor, created_via=via,
                         full_name=customer["full_name"], active=customer["active"]))
    return rows


STORED = ("id", "group_id", "customer_id", "position", "created_at", "created_by", "created_via")


def _stored(rows):
    """Only the columns the table has; the resolved customer travels with the preview alone."""
    return [{key: row[key] for key in STORED} for row in rows]


# ---------------------------------------------------------------------------- create

def plan_create(inp, ctx, s) -> Plan:
    from bookflow.company.batch_invoicing_models import BillingGroupWriteOutput
    display, key = _unique_name(s, inp.name)
    at = clock.now_iso()
    group = dict(id=new_id(), **common(s.actor.id, ctx.interface.value, at), name=display, name_key=key)
    rows = _member_rows(s, group["id"], inp.customers, at=at, actor=s.actor.id,
                        via=ctx.interface.value, start=1)
    return Plan(BillingGroupWriteOutput(billing_group=_projected_output(group, rows)),
                dict(group=group, members=_stored(rows)))


def apply_create(plan: Plan, ctx, s) -> Applied:
    group, rows = plan.data["group"], plan.data["members"]
    s.company.conn.execute(c.billing_groups.insert().values(**group))
    if rows:
        s.company.conn.execute(c.billing_group_members.insert(), rows)
    touched = [Touched("billing_group", group["id"], "create", None, 1, group, db="company")]
    touched += [Touched("billing_group_member", row["id"], "create", None, 1, row, db="company")
                for row in rows]
    return Applied(plan.preview, touched, f'created billing group {group["name"]}')


# ---------------------------------------------------------------------------- rename

def plan_rename(inp, ctx, s) -> Plan:
    from bookflow.company.batch_invoicing_models import BillingGroupWriteOutput
    group = resolve_group(s, inp.billing_group)
    _check_version(group, inp.expected_version)
    display, key = _unique_name(s, inp.name, own=group["id"])
    changed = display != group["name"]
    new = dict(group)
    if changed:
        new.update(name=display, name_key=key, version=group["version"] + 1,
                   updated_at=clock.now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(BillingGroupWriteOutput(billing_group=show_output(s, new), changed=changed),
                dict(before=group, group=new, changed=changed))


def apply_rename(plan: Plan, ctx, s) -> Applied:
    if not plan.data["changed"]:
        return Applied(plan.preview, [], "no change")
    group, before = plan.data["group"], plan.data["before"]
    s.company.conn.execute(c.billing_groups.update().where(c.billing_groups.c.id == group["id"]).values(**group))
    return Applied(plan.preview, [Touched("billing_group", group["id"], "update", before["version"],
                                          group["version"], group, before, db="company")],
                   f'renamed billing group to {group["name"]}')


# ---------------------------------------------------------------------------- delete

def plan_delete(inp, ctx, s) -> Plan:
    from bookflow.company.batch_invoicing_models import BillingGroupWriteOutput
    group = resolve_group(s, inp.billing_group)
    _check_version(group, inp.expected_version)
    shown = show_output(s, group)
    return Plan(BillingGroupWriteOutput(billing_group=shown, deleted=True),
                dict(group=group, members=members(s, group["id"])))


def apply_delete(plan: Plan, ctx, s) -> Applied:
    group, rows = plan.data["group"], plan.data["members"]
    s.company.conn.execute(c.billing_group_members.delete().where(
        c.billing_group_members.c.group_id == group["id"]))
    s.company.conn.execute(c.billing_groups.delete().where(c.billing_groups.c.id == group["id"]))
    touched = [Touched("billing_group_member", row["id"], "delete", 1, None, None, before,
                       db="company") for row, before in zip(rows, _stored(rows))]
    touched.append(Touched("billing_group", group["id"], "delete", group["version"], None, None,
                           group, db="company"))
    return Applied(plan.preview, touched, f'deleted billing group {group["name"]}')


# ---------------------------------------------------------------------------- membership

def plan_add(inp, ctx, s) -> Plan:
    from bookflow.company.batch_invoicing_models import BillingGroupWriteOutput
    group = resolve_group(s, inp.billing_group)
    current = members(s, group["id"])
    held = {row["customer_id"] for row in current}
    at = clock.now_iso()
    start = max((row["position"] for row in current), default=0) + 1
    rows, position = [], start
    for selector in inp.customers:
        customer = resolve_customer(s, selector)
        if customer["id"] in held:
            continue
        held.add(customer["id"])
        rows.append(dict(id=new_id(), group_id=group["id"], customer_id=customer["id"],
                         position=position, created_at=at, created_by=s.actor.id,
                         created_via=ctx.interface.value, full_name=customer["full_name"],
                         active=customer["active"]))
        position += 1
    return Plan(BillingGroupWriteOutput(billing_group=_projected_output(group, current + rows),
                                        changed=bool(rows)),
                dict(group=group, members=_stored(rows), changed=bool(rows)))


def _projected_output(group: dict[str, Any], rows: list[dict[str, Any]]):
    from bookflow.company.batch_invoicing_models import BillingGroupShowOutput
    ordered = sorted(rows, key=lambda row: (row["position"], row["customer_id"]))
    return BillingGroupShowOutput(
        **{key: group[key] for key in ("id", "version", "created_at", "created_by", "created_via",
                                       "updated_at", "updated_by", "updated_via", "name")},
        member_count=len(ordered), members=[_member_output(row) for row in ordered])


def apply_add(plan: Plan, ctx, s) -> Applied:
    if not plan.data["changed"]:
        return Applied(plan.preview, [], "no change")
    group, rows = plan.data["group"], plan.data["members"]
    s.company.conn.execute(c.billing_group_members.insert(), rows)
    return Applied(plan.preview, [Touched("billing_group_member", row["id"], "create", None, 1,
                                          row, db="company") for row in rows],
                   f'added {len(rows)} to billing group {group["name"]}')


def plan_remove(inp, ctx, s) -> Plan:
    from bookflow.company.batch_invoicing_models import BillingGroupWriteOutput
    group = resolve_group(s, inp.billing_group)
    current = members(s, group["id"])
    held = {row["customer_id"]: row for row in current}
    removing = []
    for selector in inp.customers:
        customer = resolve_customer(s, selector)
        row = held.get(customer["id"])
        if row is not None and row not in removing:
            removing.append(row)
    remaining = [row for row in current if row not in removing]
    return Plan(BillingGroupWriteOutput(billing_group=_projected_output(group, remaining),
                                        changed=bool(removing)),
                dict(group=group, members=removing, changed=bool(removing)))


def apply_remove(plan: Plan, ctx, s) -> Applied:
    if not plan.data["changed"]:
        return Applied(plan.preview, [], "no change")
    group, rows = plan.data["group"], plan.data["members"]
    s.company.conn.execute(c.billing_group_members.delete().where(
        c.billing_group_members.c.group_id == group["id"],
        c.billing_group_members.c.customer_id.in_([row["customer_id"] for row in rows])))
    touched = [Touched("billing_group_member", row["id"], "delete", 1, None, None, before,
                       db="company") for row, before in zip(rows, _stored(rows))]
    return Applied(plan.preview, touched, f'removed {len(rows)} from billing group {group["name"]}')


# ---------------------------------------------------------------------------- reads

def show(s, inp) -> Any:
    return show_output(s, resolve_group(s, inp.billing_group))


def page(s, inp) -> Any:
    from bookflow.company.batch_invoicing_models import BillingGroupOutput, BillingGroupPageOutput
    table, member = c.billing_groups, c.billing_group_members
    counts = sa.select(member.c.group_id, sa.func.count().label("member_count")).group_by(
        member.c.group_id).subquery()
    query = sa.select(table, sa.func.coalesce(counts.c.member_count, 0).label("member_count")).join(
        counts, counts.c.group_id == table.c.id, isouter=True)
    if inp.query:
        _, key = normalize_display_name(inp.query, field="query")
        query = query.where(table.c.name_key.like("%" + key.replace("%", r"\%").replace("_", r"\_") + "%", escape="\\"))
    if inp.cursor:
        # The page is in name order, so the continuation is the (name, id) pair of the last row
        # read back from its id rather than a second copy of the name carried through the client.
        previous = s.company.conn.execute(
            sa.select(table.c.name_key).where(table.c.id == inp.cursor)).scalar_one_or_none()
        if previous is None:
            raise BookflowError("E_LIST_FILTER", details={"problem": "the continuation names a group that is gone; start again", "field": "cursor"})
        query = query.where(sa.tuple_(table.c.name_key, table.c.id) > sa.tuple_(previous, inp.cursor))
    rows = [dict(row) for row in s.company.conn.execute(
        query.order_by(table.c.name_key, table.c.id).limit(inp.limit + 1)).mappings()]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    return BillingGroupPageOutput(
        items=[BillingGroupOutput(**{key: row[key] for key in (
            "id", "version", "created_at", "created_by", "created_via", "updated_at", "updated_by",
            "updated_via", "name")}, member_count=row["member_count"]) for row in rows],
        count=len(rows), next_cursor=rows[-1]["id"] if more and rows else None)
