"""Merging a duplicate account or item into another by an audited alias (R117, second part).

The same alias as customers and vendors (``party_merges.py``): a merge records "M is merged into
S" in ``list_merges`` and changes nothing posted, and every read that groups or filters by account
or item resolves M to S through ``party_merges.survivor_sql``. Accounts and items are also named
by editable list rows (an item's income account, a vendor's expense accounts, a group's members,
a price level's rows), and a save naming an inactive entry is refused, so the one audited merge
also re-points each of those rows to S and records it in ``list_merge_references``. Where S
already has its own row under a unique key, S's row is kept and M's ended, or the merge is
refused where the two differ in a way that matters (a per-item price, a component quantity).
A saved document revised later resolves M to S as it saves (``resolve_saved``).

Undo stamps the merge, puts every recorded reference back that still holds what the merge wrote,
and makes M active again. Ids that an unfinished settlement, reconciliation or inventory cost
chain runs through stay on M; those are what refuse a merge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.party_merges import members_sql, require_person, survivor_sql  # noqa: F401
from bookflow.core import audit, clock
from bookflow.core.errors import BookflowError, require_reason
from bookflow.core.ids import new_id
from bookflow.core.registry import Touched

KINDS = ("account", "item")
_TABLES = {"account": "accounts", "item": "items"}

# Account fields on an item, in the order they are re-pointed.
ITEM_ACCOUNT_FIELDS = ("income_account_id", "expense_account_id", "cogs_account_id", "asset_account_id",
                       "deposit_account_id", "liability_account_id", "accumulated_depreciation_account_id",
                       "depreciation_expense_account_id", "gain_loss_account_id")


def _refuse(problem: str, **details: Any) -> BookflowError:
    return BookflowError("E_MERGE_REFUSED", message=f"Merge refused: {problem}.", details={"problem": problem, **details})


def live_merge(db, kind: str, merged_id: str) -> dict[str, Any] | None:
    t = schema.list_merges
    row = db.conn.execute(sa.select(t).where(t.c.list_kind == kind, t.c.merged_id == merged_id,
                                             t.c.undone_at.is_(None))).mappings().first()
    return dict(row) if row else None


def merges(db, kind: str, *, entry_id: str | None = None, include_undone: bool = False) -> list[dict[str, Any]]:
    t = schema.list_merges
    q = sa.select(t).where(t.c.list_kind == kind)
    if entry_id is not None:
        q = q.where(sa.or_(t.c.merged_id == entry_id, t.c.survivor_id == entry_id))
    if not include_undone:
        q = q.where(t.c.undone_at.is_(None))
    return [dict(r) for r in db.conn.execute(q.order_by(t.c.created_at, t.c.id)).mappings()]


def _merged_into(db, kind: str, survivor_id: str) -> list[str]:
    return [r[0] for r in db.raw.execute(
        "SELECT merged_id FROM list_merges WHERE list_kind=? AND survivor_id=? AND undone_at IS NULL",
        (kind, survivor_id))]


def survivor_id(db, kind: str, entry_id: str | None) -> str | None:
    if entry_id is None:
        return None
    row = db.raw.execute(f"SELECT {survivor_sql(kind, '?')}", (entry_id, entry_id)).fetchone()
    return row[0]


def resolve_saved(db, kind: str, row: dict[str, Any]) -> dict[str, Any]:
    """The row a save uses for ``row``: its survivor's row while ``row`` is merged away, else itself.
    A document naming M (an old invoice being corrected, a memorized entry) saves with S."""
    if row.get("active") or not row.get("id"):
        return row
    merge = live_merge(db, kind, str(row["id"]))
    if merge is None:
        return row
    table = schema.accounts if kind == "account" else schema.items
    found = db.conn.execute(sa.select(table).where(table.c.id == merge["survivor_id"])).mappings().first()
    return dict(found) if found is not None else row


def _live_map(db, kind: str) -> dict[str, dict[str, Any]]:
    table = _TABLES[kind]
    try:
        rows = db.raw.execute(f"SELECT m.merged_id, a.* FROM list_merges m JOIN {table} a ON a.id=m.survivor_id "
                              "WHERE m.list_kind=? AND m.undone_at IS NULL", (kind,))
    except Exception as exc:  # a partial schema built by hand for a narrow check has no merges
        if "no such table" in str(exc):
            return {}
        raise
    names = [d[0] for d in rows.description]
    return {r[0]: dict(zip(names[1:], r[1:])) for r in rows.fetchall()}


def survive(db, model):
    """``model`` (captured line facts being saved again) with every merged-away account or item
    in it replaced by its survivor (R117): an account fact is any object carrying ``normal_balance``,
    an item fact the object under ``item``. The model itself when nothing in it was merged."""
    accounts, items = _live_map(db, "account"), _live_map(db, "item")
    if not accounts and not items:
        return model
    data, changed = model.model_dump(), [False]

    def walk(value, key=None):
        if isinstance(value, dict):
            if "normal_balance" in value and value.get("id") in accounts:
                row = accounts[value["id"]]
                value.update({k: row[k] for k in ("id", "name", "full_name", "number", "type") if k in value})
                changed[0] = True
            elif key == "item" and value.get("id") in items:
                row = items[value["id"]]
                value.update({k: row[k] for k in ("id", "name", "full_name", "version") if k in value})
                if "label" in value:
                    value["label"] = row["full_name"]
                changed[0] = True
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)

    walk(data)
    return type(model).model_validate(data) if changed[0] else model


def member_ids(db, kind: str, entry_id: str) -> list[str]:
    """Every id that reads as ``entry_id``: itself and each entry merged into it."""
    return [entry_id, *_merged_into(db, kind, entry_id)]


def refuse_while_merged(db, kind: str, entry_id: str, verb: str) -> None:
    """An entry merged away changes only by undoing its merge (as `customer activate` refuses)."""
    if not db.conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='list_merges'").first():
        return
    merge = live_merge(db, kind, entry_id)
    if merge is not None:
        raise BookflowError("E_RECORD_IN_USE", message=f"This {kind} is merged into another; undo the merge "
                            f"(`{kind} unmerge`) before you {verb} it.",
                            details={"record_type": kind, "record_id": entry_id, "survivor_id": merge["survivor_id"],
                                     "problem": f"a merged {kind} can be changed only by undoing its merge"})


def is_merged_away(db, kind: str, entry_id: str | None) -> bool:
    return bool(entry_id) and live_merge(db, kind, str(entry_id)) is not None


# --- editable references ----------------------------------------------------------------

@dataclass(frozen=True)
class Reference:
    """One editable list row naming the merged entry, and what the merge does with it."""
    table: str
    record_id: str
    column: str
    action: str  # repoint | end
    owner_kind: str  # the versioned list record whose version the merge advances
    owner_id: str
    label: str  # what a person reads in the preview
    conflict: str | None = None  # why S's own row is kept and this one ended
    before: str | None = None  # what the column holds now; the merged entry unless given
    after: str | None = None  # what a repoint writes; the survivor unless given
    explicit: bool = False  # before and after are given, not the merged entry and survivor


def _owner_name(db, owner_kind: str, owner_id: str) -> str:
    table = {"item": "items", "account": "accounts", "vendor": "vendors", "price_level": "price_levels"}.get(owner_kind)
    if table is None:
        return "the company's preferences"
    row = db.raw.execute(f"SELECT {'full_name' if table in ('items', 'accounts') else 'name'} FROM {table} WHERE id=?",
                         (owner_id,)).fetchone()
    return str(row[0]) if row else owner_id


def _account_references(db, merged: str, survivor: str) -> list[Reference]:
    refs: list[Reference] = []
    for field in ITEM_ACCOUNT_FIELDS:
        for (item_id,) in db.raw.execute(f"SELECT id FROM items WHERE {field}=? ORDER BY full_name_key, id", (merged,)):
            refs.append(Reference("items", item_id, field, "repoint", "item", item_id,
                                  f"item {_owner_name(db, 'item', item_id)}: {field.removesuffix('_id').replace('_', ' ')}"))
    for (account_id,) in db.raw.execute(
            "SELECT id FROM accounts WHERE reimbursable_income_account_id=? ORDER BY full_name_key, id", (merged,)):
        refs.append(Reference("accounts", account_id, "reimbursable_income_account_id", "repoint", "account", account_id,
                              f"account {_owner_name(db, 'account', account_id)}: reimbursable income account"))
    for field in ("customer_discount_account_id", "vendor_discount_account_id"):
        for (info_id,) in db.raw.execute(f"SELECT id FROM company_info WHERE {field}=?", (merged,)):
            refs.append(Reference("company_info", info_id, field, "repoint", "company_info", info_id,
                                  f"company preferences: {field.removesuffix('_account_id').replace('_', ' ')} account"))
    for row_id, vendor_id in db.raw.execute(
            "SELECT e.id, e.vendor_id FROM vendor_expense_accounts e JOIN vendors v ON v.id=e.vendor_id "
            "WHERE e.account_id=? AND e.active=1 ORDER BY v.name_key, e.id", (merged,)):
        label = f"vendor {_owner_name(db, 'vendor', vendor_id)}: expense account"
        has_own = db.raw.execute("SELECT 1 FROM vendor_expense_accounts WHERE vendor_id=? AND account_id=? AND active=1",
                                 (vendor_id, survivor)).fetchone()
        if has_own:
            refs.append(Reference("vendor_expense_accounts", row_id, "account_id", "end", "vendor", vendor_id, label,
                                  "the vendor already lists the survivor; its own row is kept"))
        else:
            refs.append(Reference("vendor_expense_accounts", row_id, "account_id", "repoint", "vendor", vendor_id, label))
    return refs


def _item_references(db, merged: str, survivor: str) -> list[Reference]:
    refs: list[Reference] = []
    for (box_id,) in db.raw.execute("SELECT id FROM pos_boxes WHERE item_id=? ORDER BY id", (merged,)):
        refs.append(Reference("pos_boxes", box_id, "item_id", "repoint", "pos_box", box_id,
                              f"POS box {box_id}: item"))
    # Group and assembly members naming M.
    from bookflow.company.items import _graph_reaches
    from bookflow.core.exact import format_quantity_micro_units as qty_text
    names = {i: _owner_name(db, "item", i) for i in (merged, survivor)}
    for row_id, owner, qty, unit in db.raw.execute(
            "SELECT id, owner_item_id, quantity_microunits, unit_id FROM item_members "
            "WHERE component_item_id=? AND active=1 ORDER BY owner_item_id, position, id", (merged,)):
        kit = _owner_name(db, 'item', owner)
        label = f"item {kit}: member"
        if owner == survivor:
            raise _refuse("the item to merge into lists this item as one of its own members", owner_item_id=owner)
        if _graph_reaches(db, survivor, owner):
            # The owner would list the survivor, which already contains the owner: a member cycle.
            raise _refuse(f"{kit} lists this item, and the item to merge into already contains {kit}; "
                          "the merge would make a group contain itself", owner_item_id=owner)
        own = db.raw.execute("SELECT id, quantity_microunits, unit_id FROM item_members WHERE owner_item_id=? "
                             "AND component_item_id=? AND active=1", (owner, survivor)).fetchone()
        if own is None:
            refs.append(Reference("item_members", row_id, "component_item_id", "repoint", "item", owner, label))
        elif own[2] != unit:
            raise _refuse(f"{kit} lists both items in different units; make them agree or remove one first",
                          owner_item_id=owner)
        else:
            # The same thing listed twice: S's row keeps the sum, M's row ends.
            total = int(own[1] or 0) + int(qty or 0)
            plain = (f"{kit}: {names[survivor]} ×{qty_text(own[1] or 0)} and {names[merged]} ×{qty_text(qty or 0)} "
                     f"become {names[survivor]} ×{qty_text(total)}")
            refs.append(Reference("item_members", own[0], "quantity_microunits", "repoint", "item", owner, plain,
                                  before=str(own[1]), after=str(total), explicit=True))
            refs.append(Reference("item_members", row_id, "component_item_id", "end", "item", owner, label,
                                  "summed into the survivor's own row"))
    # Price-level rows naming M.
    for row_id, level, price, cur, pct, basis, frac in db.raw.execute(
            "SELECT id, price_level_id, price_minor_units, price_currency, percent_millionths, adjustment_basis, "
            "price_fraction "
            "FROM price_level_items WHERE item_id=? AND active=1 ORDER BY price_level_id, position, id", (merged,)):
        label = f"price level {_owner_name(db, 'price_level', level)}: per-item price"
        own = db.raw.execute("SELECT price_minor_units, price_currency, percent_millionths, adjustment_basis, price_fraction "
                             "FROM price_level_items WHERE price_level_id=? AND item_id=? AND active=1",
                             (level, survivor)).fetchone()
        if own is None:
            refs.append(Reference("price_level_items", row_id, "item_id", "repoint", "price_level", level, label))
        elif tuple(own) != (price, cur, pct, basis, frac):
            raise _refuse(f"price level {_owner_name(db, 'price_level', level)} gives the two items different prices; "
                          "make them agree or remove one first", price_level_id=level)
        else:
            refs.append(Reference("price_level_items", row_id, "item_id", "end", "price_level", level, label,
                                  "it already prices the survivor the same; its own row is kept"))
    # M's own vendor profiles move to S unless S already has that vendor or that preferred rank.
    for row_id, vendor_id, rank in db.raw.execute(
            "SELECT id, vendor_id, preferred_rank FROM item_vendor_profiles WHERE item_id=? AND active=1 "
            "ORDER BY position, id", (merged,)):
        label = f"vendor profile for {_owner_name(db, 'vendor', vendor_id)}"
        if db.raw.execute("SELECT 1 FROM item_vendor_profiles WHERE item_id=? AND vendor_id=? AND active=1",
                          (survivor, vendor_id)).fetchone():
            refs.append(Reference("item_vendor_profiles", row_id, "item_id", "end", "item", merged, label,
                                  "the survivor already has a profile for this vendor; its own is kept"))
        elif rank is not None and db.raw.execute(
                "SELECT 1 FROM item_vendor_profiles WHERE item_id=? AND preferred_rank=? AND active=1",
                (survivor, rank)).fetchone():
            refs.append(Reference("item_vendor_profiles", row_id, "item_id", "end", "item", merged, label,
                                  f"the survivor already has a preferred vendor ranked {rank}; its own is kept"))
        else:
            refs.append(Reference("item_vendor_profiles", row_id, "item_id", "repoint", "item", merged, label))
            if rank == 1:
                # The item's own preferred vendor is its first-ranked profile: it moves with it.
                refs.append(Reference("items", merged, "preferred_vendor_id", "repoint", "item", merged,
                                      "preferred vendor of the merged item", before=vendor_id, after=None, explicit=True))
                if db.raw.execute("SELECT preferred_vendor_id FROM items WHERE id=?", (survivor,)).fetchone()[0] is None:
                    refs.append(Reference("items", survivor, "preferred_vendor_id", "repoint", "item", survivor,
                                          f"preferred vendor: {_owner_name(db, 'vendor', vendor_id)}",
                                          before=None, after=vendor_id, explicit=True))
    return refs


# --- what moves -------------------------------------------------------------------------

def _account_balance(db, account_id: str, account_type: str) -> int:
    from bookflow.company.accounts import NORMAL_BALANCE
    from bookflow.company.customer_balances import register_functions
    register_functions(db)
    value = db.raw.execute("SELECT bookflow_sum_int(debit_minor_units-credit_minor_units) FROM posting_lines "
                           "WHERE account_id=?", (account_id,)).fetchone()[0]
    net = int(value or "0")
    return -net if NORMAL_BALANCE.get(account_type) == "credit" else net


def _account_documents(db, account_id: str) -> dict[str, dict[str, int]]:
    rows = db.raw.execute("""SELECT t.type, count(DISTINCT l.transaction_id), count(*) FROM posting_lines l
        JOIN transactions t ON t.id=l.transaction_id WHERE l.account_id=? GROUP BY t.type ORDER BY t.type""",
                          (account_id,)).fetchall()
    return {str(kind): {"documents": int(docs), "postings": int(lines)} for kind, docs, lines in rows}


# Item lines a document holds, current revisions only: every entered-line table keyed by its
# document line that carries an item.
_ITEM_LINE_TABLES = ("sales_line_profiles", "credit_line_profiles", "purchase_item_lines",
                     "vendor_credit_item_lines", "money_out_item_lines")


def _item_documents(db, item_id: str) -> dict[str, dict[str, int]]:
    present = {r[0] for r in db.raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    lines = " UNION ALL ".join(f"SELECT document_line_id, item_id FROM {t}" for t in _ITEM_LINE_TABLES if t in present)
    rows = db.raw.execute(f"""SELECT t.type, count(DISTINCT t.id), count(*) FROM ({lines}) x
        JOIN document_lines d ON d.id=x.document_line_id JOIN transactions t ON t.current_revision_id=d.revision_id
        WHERE x.item_id=? GROUP BY t.type ORDER BY t.type""", (item_id,)).fetchall()
    counts = {str(kind): {"documents": int(docs), "postings": int(n)} for kind, docs, n in rows}
    # Unposted documents (estimates, sales and purchase orders) are counted beside them.
    for kind, sql in (("purchase_order", "SELECT count(DISTINCT o.id), count(*) FROM purchase_order_lines l "
                                         "JOIN purchase_orders o ON o.current_revision_id=l.revision_id WHERE l.item_id=?"),):
        if "purchase_order_lines" in present:
            try:
                docs, n = db.raw.execute(sql, (item_id,)).fetchone()
            except Exception as exc:  # a shape this build does not have
                if "no such column" not in str(exc):
                    raise
                docs = n = 0
            if docs:
                counts[kind] = {"documents": int(docs), "postings": int(n)}
    return dict(sorted(counts.items()))


def _home(db) -> str:
    return str(db.conn.execute(sa.select(schema.company_info.c.home_currency)).scalar_one())


@dataclass(frozen=True)
class MergePlan:
    kind: str
    merged: dict[str, Any]
    survivor: dict[str, Any]
    existing: dict[str, Any] | None
    documents: dict[str, dict[str, int]]
    merged_balance: int | None
    survivor_balance: int | None
    references: tuple[Reference, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        return self.existing is None


def _resolve(db, kind: str, selector: str) -> dict[str, Any]:
    if kind == "account":
        from bookflow.company.accounts import resolve_account
        return dict(resolve_account(db, selector))
    from bookflow.company.items import resolve_item
    return dict(resolve_item(db, selector))


def _exists(db, sql: str, params: tuple) -> bool:
    try:
        return db.raw.execute(sql, params).fetchone() is not None
    except Exception as exc:  # a table this company's schema does not have yet
        if "no such table" in str(exc):
            return False
        raise


def _account_refusals(db, merged: dict[str, Any], target: dict[str, Any], ids: dict[str, str]) -> None:
    m, s = merged["id"], target["id"]
    if merged["type"] != target["type"]:
        raise _refuse("the two accounts are of different types", merged_type=merged["type"],
                      survivor_type=target["type"], **ids)
    home = _home(db)
    if (merged.get("currency") or home) != (target.get("currency") or home):
        raise _refuse("the two accounts are in different currencies", **ids)
    if merged.get("system_role") or target.get("system_role"):
        raise _refuse("an account the books use for a fixed purpose (a system account) is never merged", **ids)
    from bookflow.company.uncategorized import SEED_KEYS
    if merged.get("seed_key") in SEED_KEYS:
        raise _refuse("an Uncategorized account is never merged away; merge the other account into it instead", **ids)
    if _exists(db, "SELECT 1 FROM accounts WHERE parent_id=?", (m,)):
        raise _refuse("this account has subaccounts of its own; move or merge them first", **ids)
    posted = _exists(db, "SELECT 1 FROM posting_lines WHERE account_id=?", (m,))
    if merged["type"] in ("bank", "credit_card") and (
            posted or _exists(db, "SELECT 1 FROM check_instruments WHERE account_id=?", (m,))
            or _exists(db, "SELECT 1 FROM statement_imports WHERE account_id=?", (m,))
            or _exists(db, "SELECT 1 FROM reconciliation_accounts WHERE account_id=?", (m,))):
        raise _refuse("a bank or credit card account with postings is never merged: a reconciliation clears each "
                      "line on its own account, so its lines could never clear under the other", **ids)
    if merged["type"] in ("accounts_receivable", "accounts_payable") and posted:
        raise _refuse("an A/R or A/P account with postings is never merged: each payment and credit is settled "
                      "against the account it was posted to", **ids)
    for side in (m, s):
        if (_exists(db, "SELECT 1 FROM items WHERE type IN ('sales_tax_item','sales_tax_group') AND liability_account_id=?", (side,))
                or _exists(db, "SELECT 1 FROM sales_tax_components WHERE liability_account_id=?", (side,))
                or _exists(db, "SELECT 1 FROM credit_tax_components WHERE liability_account_id=?", (side,))):
            raise _refuse("a sales tax item uses one of these accounts as its liability account", account_id=side, **ids)
        if _exists(db, "SELECT 1 FROM inventory_movements WHERE asset_account_id=?", (side,)):
            raise _refuse("inventory movements use one of these accounts as their asset account", account_id=side, **ids)


def _item_refusals(db, merged: dict[str, Any], target: dict[str, Any], ids: dict[str, str]) -> None:
    m, s = merged["id"], target["id"]
    if merged["type"] != target["type"]:
        raise _refuse("the two items are of different types", merged_type=merged["type"],
                      survivor_type=target["type"], **ids)
    if merged["type"] in ("sales_tax_item", "sales_tax_group"):
        raise _refuse("sales tax items and groups are never merged: each tax collected and paid is attributed to "
                      "its own item", **ids)
    if merged["type"] == "fixed_asset":
        raise _refuse("fixed assets are never merged", **ids)
    if merged["type"] == "group":
        raise _refuse("groups are never merged; edit the group's members instead", **ids)
    if _exists(db, "SELECT 1 FROM items WHERE parent_id=?", (m,)):
        raise _refuse("this item has sub-items of its own; move or merge them first", **ids)
    if merged["type"] in ("inventory_part", "inventory_assembly"):
        for side in (m, s):
            if (_exists(db, "SELECT 1 FROM inventory_movements WHERE item_id=?", (side,))
                    or _exists(db, "SELECT 1 FROM item_receipt_lines WHERE item_id=?", (side,))):
                raise _refuse("an inventory item with stock movements is never merged: its average cost would be "
                              "re-run over both histories; move its quantity into the other with a stock transfer",
                              item_id=side, **ids)
    if merged.get("price_currency") and target.get("price_currency") and merged["price_currency"] != target["price_currency"]:
        raise _refuse("the two items are priced in different currencies", **ids)


def plan_merge(db, kind: str, merged_selector: str, survivor_selector: str) -> MergePlan:
    if kind not in KINDS:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "kind", "problem": "is not account or item"}]})
    merged = _resolve(db, kind, merged_selector)
    target = _resolve(db, kind, survivor_selector)
    ids = dict(merged_id=merged["id"], survivor_id=target["id"])
    if merged["id"] == target["id"]:
        raise _refuse(f"an {kind} cannot be merged into itself", **ids)
    if kind == "account":
        from bookflow.company.pos_layer import refuse_if_used
        refuse_if_used(db, "account", merged["id"], "merged away")
    existing = live_merge(db, kind, merged["id"])
    if existing is not None:
        if existing["survivor_id"] == target["id"]:
            return _plan(db, kind, merged, target, existing, ())
        raise _refuse(f"this {kind} is already merged into another one; undo that merge first",
                      current_survivor_id=existing["survivor_id"], **ids)
    if live_merge(db, kind, target["id"]) is not None:
        raise _refuse(f"the {kind} to merge into is itself merged away; merge into its survivor instead", **ids)
    if _merged_into(db, kind, merged["id"]):
        raise _refuse(f"other {kind}s are merged into this one; undo those merges, then merge each into the survivor", **ids)
    if not target.get("active"):
        raise _refuse(f"the {kind} to merge into is inactive; make it active first", **ids)
    (_account_refusals if kind == "account" else _item_refusals)(db, merged, target, ids)
    refs = (_account_references if kind == "account" else _item_references)(db, merged["id"], target["id"])
    return _plan(db, kind, merged, target, None, tuple(refs))


def _plan(db, kind, merged, target, existing, refs) -> MergePlan:
    if kind == "account":
        return MergePlan(kind, merged, target, existing, _account_documents(db, merged["id"]),
                         _account_balance(db, merged["id"], merged["type"]),
                         sum(_account_balance(db, x, target["type"]) for x in (target["id"], *_merged_into(db, kind, target["id"]))),
                         refs)
    return MergePlan(kind, merged, target, existing, _item_documents(db, merged["id"]), None, None, refs)


def _notes(db, plan: MergePlan) -> list[str]:
    notes = []
    closing = db.raw.execute("SELECT closing_date FROM company_info").fetchone()
    if closing and closing[0]:
        notes.append(f"Reports of periods closed through {closing[0]} will also show the merged {plan.kind}'s "
                     f"entries under {plan.survivor['full_name']}; nothing posted in them changes.")
    else:
        notes.append(f"Reports of every period, earlier ones included, will show the merged {plan.kind}'s entries "
                     f"under {plan.survivor['full_name']}; nothing posted changes.")
    if any(r.conflict for r in plan.references):
        notes.append("Where the survivor already has its own row, its row is kept; see each reference's conflict.")
    return notes


def preview(db, plan: MergePlan) -> dict[str, Any]:
    from bookflow.core.money import Money
    currency = plan.survivor.get("currency") or _home(db) if plan.kind == "account" else _home(db)
    money = (lambda v: None if v is None else Money(v, currency).to_dict())
    after = None if plan.survivor_balance is None else plan.survivor_balance + (plan.merged_balance if plan.changed else 0)
    return dict(
        merge_id=plan.existing["id"] if plan.existing else None, changed=plan.changed,
        merged_id=plan.merged["id"], merged_name=plan.merged["full_name"],
        survivor_id=plan.survivor["id"], survivor_name=plan.survivor["full_name"],
        documents=[dict(type=k, count=v["documents"], postings=v["postings"]) for k, v in plan.documents.items()],
        document_count=sum(v["documents"] for v in plan.documents.values()),
        merged_balance=money(plan.merged_balance), survivor_balance=money(plan.survivor_balance),
        survivor_balance_after=money(after),
        notes=_notes(db, plan),
        references=[dict(table=r.table, record_id=r.record_id, field=r.column, action=r.action,
                         owner_type=r.owner_kind, owner_id=r.owner_id, label=r.label, conflict=r.conflict)
                    for r in plan.references],
    )


# --- writing ----------------------------------------------------------------------------

_OWNER_TABLES = {"account": schema.accounts, "item": schema.items, "vendor": schema.vendors,
                 "price_level": schema.price_levels, "company_info": schema.company_info, "pos_box": schema.pos_boxes}


def _snapshot(db, owner_kind: str, owner_id: str) -> dict[str, Any]:
    from bookflow.company import undo
    handler = undo.HANDLERS.get(owner_kind, "update") if owner_kind != "company_info" else None
    if handler is not None:
        return dict(handler.snapshotter(db.conn, owner_id))
    table = _OWNER_TABLES[owner_kind]
    return dict(db.conn.execute(sa.select(table).where(table.c.id == owner_id)).mappings().one())


def _typed(column: str, value):
    """A stored reference value as its column holds it: quantities are integers."""
    return int(value) if value is not None and column.endswith("_microunits") else value


def _write(s, ctx, *, kind: str, entry_id: str, entry_active: bool, steps: list[tuple[str, str, str, Any]],
           owners: list[tuple[str, str]], action: str, at: str) -> list[Touched]:
    """Apply ``steps`` (table, record id, column, value) and advance each owner's version once,
    the merged entry's own row included, returning one audit entry per owner."""
    db = s.company
    order = list(dict.fromkeys([(kind, entry_id), *owners]))
    before = {o: (_snapshot(db, *o), int(db.raw.execute(f"SELECT version FROM {_OWNER_TABLES[o[0]].name} WHERE id=?",
                                                         (o[1],)).fetchone()[0])) for o in order}
    for table, record_id, column, value in steps:
        db.conn.execute(sa.text(f"UPDATE {table} SET {column}=:value WHERE id=:id"), {"value": value, "id": record_id})
    meta = dict(updated_at=at, updated_by=s.actor.id, updated_via=ctx.interface.value)
    for owner_kind, owner_id in order:
        t = _OWNER_TABLES[owner_kind]
        values = dict(meta, version=t.c.version + 1)
        if (owner_kind, owner_id) == (kind, entry_id):
            values["active"] = entry_active
        db.conn.execute(t.update().where(t.c.id == owner_id).values(**values))
    touched = []
    for o in order:
        snap, version = before[o]
        touched.append(Touched(o[0], o[1], action, version, version + 1, _snapshot(db, *o), snap, db="company"))
    if any(owner_kind == "pos_box" for owner_kind, _ in order):
        t = schema.company_info
        info = dict(db.conn.execute(sa.select(t.c.id, t.c.pos_layout_version,
                                             t.c.pos_home_view_style)).mappings().one())
        after = dict(info, pos_layout_version=info['pos_layout_version'] + 1)
        db.conn.execute(t.update().where(t.c.id == info['id']).values(
            pos_layout_version=after['pos_layout_version']))
        touched.append(Touched('pos_layout', info['id'], action, info['pos_layout_version'],
                               after['pos_layout_version'], after, info, db='company'))
    return touched


def apply_merge(s, ctx, plan: MergePlan, command: str) -> tuple[str, list[Touched]]:
    """Re-point every editable reference, make M inactive, write the merge, its references and one
    audit event."""
    require_reason(ctx.reason)
    db, kind = s.company, plan.kind
    at, event, merge_id = clock.now_iso(), new_id(), new_id()
    survivor = plan.survivor["id"]
    steps = [(r.table, r.record_id, r.column if r.action == "repoint" else "active",
              _typed(r.column, r.after if r.explicit else survivor) if r.action == "repoint" else 0)
             for r in plan.references]
    touched = _write(s, ctx, kind=kind, entry_id=plan.merged["id"], entry_active=False, steps=steps,
                     owners=[(r.owner_kind, r.owner_id) for r in plan.references], action="merge", at=at)
    row = dict(id=merge_id, list_kind=kind, merged_id=plan.merged["id"], survivor_id=survivor,
               deactivated_merged=bool(plan.merged.get("active")), reason=ctx.reason.strip(), created_at=at,
               created_by=s.actor.id, created_via=ctx.interface.value, audit_event_id=event)
    touched.append(Touched("list_merge", merge_id, "create", None, 1, dict(row), db="company"))
    audit.write_event_to(db, ctx, command, f"Merged {kind} {plan.merged['full_name']} into {plan.survivor['full_name']}",
                         touched, actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    db.conn.execute(schema.list_merges.insert().values(**row))
    for position, r in enumerate(plan.references, start=1):
        db.conn.execute(schema.list_merge_references.insert().values(
            id=new_id(), merge_id=merge_id, position=position, table_name=r.table, record_id=r.record_id,
            column_name=r.column, action=r.action, before_value=r.before if r.explicit else plan.merged["id"],
            after_value=(r.after if r.explicit else survivor) if r.action == "repoint" else None,
            owner_kind=r.owner_kind, owner_id=r.owner_id))
    return merge_id, touched


@dataclass(frozen=True)
class UnmergePlan:
    kind: str
    merged: dict[str, Any]
    merge: dict[str, Any] | None
    last: dict[str, Any] | None
    restores: tuple[dict[str, Any], ...] = ()
    kept: tuple[dict[str, Any], ...] = ()


def _owner_unchanged(db, merge: dict[str, Any], ref: dict[str, Any]) -> bool:
    """Whether the list record owning a re-pointed row is still at the version the merge wrote."""
    written = db.raw.execute("SELECT version_after FROM audit_entries WHERE event_id=? AND record_type=? AND record_id=?",
                             (merge["audit_event_id"], ref["owner_kind"], ref["owner_id"])).fetchone()
    table = _OWNER_TABLES[ref["owner_kind"]].name
    now = db.raw.execute(f"SELECT version FROM {table} WHERE id=?", (ref["owner_id"],)).fetchone()
    return written is not None and now is not None and int(written[0]) == int(now[0])


def plan_unmerge(db, kind: str, merged_selector: str) -> UnmergePlan:
    merged = _resolve(db, kind, merged_selector)
    merge = live_merge(db, kind, merged["id"])
    if merge is None:
        last = next(iter(reversed(merges(db, kind, entry_id=merged["id"], include_undone=True))), None)
        if last is None or last["merged_id"] != merged["id"]:
            raise _refuse(f"this {kind} is not merged into another one", merged_id=merged["id"])
        return UnmergePlan(kind, merged, None, last)
    if kind == "item" and (merge["deactivated_merged"] or merged.get("active")) and merged.get("barcode"):
        from bookflow.company import items  # R104: unmerging may not bring back a code another item now holds
        try:
            items.assert_barcode_available(db, merged["barcode"], exclude_id=merged["id"], reactivating=merged["full_name"])
        except BookflowError as exc:  # unmerge refuses with its own declared code, naming the holder
            raise _refuse(exc.details["fields"][0]["problem"], merged_id=merged["id"], item_id=exc.details["item_id"],
                          item=exc.details["item"]) from None
    restores, kept = [], []
    for ref in db.conn.execute(sa.select(schema.list_merge_references).where(
            schema.list_merge_references.c.merge_id == merge["id"]).order_by(
            schema.list_merge_references.c.position.desc())).mappings():
        ref = dict(ref)
        current = db.raw.execute(f"SELECT {ref['column_name']}, active FROM {ref['table_name']} WHERE id=?",
                                 (ref["record_id"],)).fetchone() if ref["table_name"] not in ("items", "accounts", "company_info", "pos_boxes") \
            else (db.raw.execute(f"SELECT {ref['column_name']}, 1 FROM {ref['table_name']} WHERE id=?",
                                 (ref["record_id"],)).fetchone())
        child = ref["table_name"] not in ("items", "accounts", "company_info")
        if current is None:
            kept.append(ref)
        elif child and not _owner_unchanged(db, merge, ref):
            # The owner's list was saved again since (a kit rewritten): its rows stay as the person left them.
            kept.append(ref)
        elif ref["action"] == "repoint" and (None if current[0] is None else str(current[0])) == ref["after_value"]:
            restores.append(ref)
        elif ref["action"] == "end" and not current[1]:
            restores.append(ref)
        else:
            kept.append(ref)  # changed since the merge; left as the person last saved it
    return UnmergePlan(kind, merged, merge, None, tuple(restores), tuple(kept))


def apply_unmerge(s, ctx, plan: UnmergePlan, command: str) -> list[Touched]:
    require_reason(ctx.reason)
    db, kind, merge = s.company, plan.kind, plan.merge
    at, event = clock.now_iso(), new_id()
    stamp = dict(undone_at=at, undone_by=s.actor.id, undone_via=ctx.interface.value,
                 undo_reason=ctx.reason.strip(), undo_audit_event_id=event)
    t = schema.list_merges
    steps = [(r["table_name"], r["record_id"], r["column_name"] if r["action"] == "repoint" else "active",
              _typed(r["column_name"], r["before_value"]) if r["action"] == "repoint" else 1) for r in plan.restores]
    entry_active = bool(merge["deactivated_merged"]) or bool(plan.merged.get("active"))
    touched = _write(s, ctx, kind=kind, entry_id=merge["merged_id"], entry_active=entry_active, steps=steps,
                     owners=[(r["owner_kind"], r["owner_id"]) for r in plan.restores], action="unmerge", at=at)
    touched.append(Touched("list_merge", merge["id"], "unmerge", 1, 2, {**merge, **stamp}, dict(merge), db="company"))
    survivor_name = db.raw.execute(f"SELECT full_name FROM {_TABLES[kind]} WHERE id=?", (merge["survivor_id"],)).fetchone()[0]
    audit.write_event_to(db, ctx, command, f"Undid merge of {kind} {plan.merged['full_name']} into {survivor_name}",
                         touched, actor_id=s.actor.id, actor_kind=s.actor.kind, event_id=event)
    db.conn.execute(t.update().where(t.c.id == merge["id"], t.c.undone_at.is_(None)).values(**stamp))
    return touched
