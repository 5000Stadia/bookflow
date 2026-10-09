"""Exact customer AR and vendor AP nets over all immutable posting effects.

Callers own the company authorization and read snapshot. These are net ledger
balances, not invoice aging or application balances.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.ledger_reports import register_ledger_functions
from bookflow.core.exact import _require_i64
from bookflow.core.money import Money
from bookflow.company.party_merges import family_cte, survivor_sql


class _IntegerText(sa.TypeDecorator):
    """Bind integer filters as text so SQLite uses the integer collation."""
    impl = sa.String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(int(value))


def register_functions(db):
    register_ledger_functions(db)
    db.raw.create_collation(
        "bookflow_integer",
        lambda left, right: (int(left) > int(right)) - (int(left) < int(right)),
    )


def balance_expression(db=None):
    """Grouped lossless text projection with numeric SQL ordering/comparison.

    With no db, the caller must register_functions before executing the SQL.
    Never cast this aggregate to SQLite INTEGER: that silently clamps overflow.
    """
    if db is not None:
        register_functions(db)
    lines, accounts = schema.posting_lines, schema.accounts
    # Each posting leg has one nonnegative side, so this subtraction fits i64.
    # A merged-away customer's lines count to its survivor (party_merges.py).
    party = sa.literal_column(survivor_sql("customer", "posting_lines.name_id")).label("name_id")
    totals = sa.select(
        party,
        sa.func.bookflow_sum_int(lines.c.debit_minor_units - lines.c.credit_minor_units).label("net"),
    ).select_from(lines.join(accounts, accounts.c.id == lines.c.account_id)).where(
        lines.c.name_type == "customer", accounts.c.type == "accounts_receivable",
    ).group_by(party).cte("customer_ar_totals").prefix_with("MATERIALIZED")
    net = sa.select(totals.c.net).where(
        totals.c.name_id == schema.customers.c.id,
    ).correlate(schema.customers).scalar_subquery()
    return sa.type_coerce(sa.func.coalesce(net, "0").collate("bookflow_integer"), _IntegerText())


def own_balance(db, id: str) -> int:
    """Signed minor units for this exact customer/job; fail on i64 overflow."""
    value = db.conn.execute(sa.select(balance_expression(db)).where(
        schema.customers.c.id == id,
    )).scalar_one_or_none()
    return _require_i64(int(value or "0"), field="current_balance")


def family_balance(db, id: str) -> int:
    """Customer plus every descendant once, including inactive jobs."""
    return _require_i64(_family_net(db, id), field="family_balance")


def _family_net(db, id: str) -> int:
    """Unbounded intermediate for projections and nonblocking credit warnings."""
    register_functions(db)
    value = db.raw.execute(f"""
        WITH RECURSIVE {family_cte("family", ":id")}
        SELECT bookflow_sum_int(l.debit_minor_units - l.credit_minor_units)
        FROM posting_lines l JOIN accounts a ON a.id = l.account_id
        WHERE l.name_type = 'customer' AND a.type = 'accounts_receivable'
          AND l.name_id IN (SELECT id FROM family)
    """, {"id": id}).fetchone()[0]
    return int(value or "0")


def credit_warning(
    db, customer_id: str, new_total: int, *,
    old_customer_id: str | None = None, old_total: int = 0,
) -> str | None:
    """Warn for a proposed invoice using validated IDs and home minor units.

    Call before posting its effects. The nearest non-null limit owns the
    exposure family. An old invoice is removed only from that same family.
    Exposure arithmetic stays unbounded: exceeding a limit never blocks a sale.
    """
    from bookflow.company.list_service import hierarchy_ancestors

    customer = db.conn.execute(sa.select(schema.customers).where(
        schema.customers.c.id == customer_id,
    )).mappings().one()
    lineage = (customer, *hierarchy_ancestors(db, schema.customers, customer))
    owner = next((row for row in lineage if row["credit_limit_minor_units"] is not None), None)
    if owner is None:
        return None
    exposure = _family_net(db, owner["id"]) + new_total
    if old_customer_id is not None:
        same_family = db.raw.execute(f"""
            WITH RECURSIVE {family_cte("family", ":owner")}
            SELECT EXISTS(SELECT 1 FROM family WHERE id = :old)
        """, {"owner": owner["id"], "old": old_customer_id}).fetchone()[0]
        if same_family:
            exposure -= old_total
    limit = int(owner["credit_limit_minor_units"])
    if exposure <= limit:
        return None
    currency = db.conn.execute(sa.select(schema.company_info.c.home_currency)).scalar_one()
    return (f"Credit limit for {owner['full_name']}: proposed net AR exposure "
            f"{Money(exposure, currency)} exceeds the limit of {Money(limit, currency)}.")


def vendor_balance_expression(db=None):
    """What the company owes each vendor: net Accounts Payable over every posting effect.

    The same sum ``report vendor-balance-summary`` totals for the vendor (credits less debits
    on payable accounts), over every date as the customer balance is. Settlement never
    crosses vendors, so applications move nothing here. Same collation rules as
    ``balance_expression``.
    """
    if db is not None:
        register_functions(db)
    lines, accounts = schema.posting_lines, schema.accounts
    party = sa.literal_column(survivor_sql("vendor", "posting_lines.name_id")).label("name_id")
    totals = sa.select(
        party,
        sa.func.bookflow_sum_int(lines.c.credit_minor_units - lines.c.debit_minor_units).label("net"),
    ).select_from(lines.join(accounts, accounts.c.id == lines.c.account_id)).where(
        lines.c.name_type == "vendor", accounts.c.type == "accounts_payable",
    ).group_by(party).cte("vendor_ap_totals").prefix_with("MATERIALIZED")
    net = sa.select(totals.c.net).where(
        totals.c.name_id == schema.vendors.c.id,
    ).correlate(schema.vendors).scalar_subquery()
    return sa.type_coerce(sa.func.coalesce(net, "0").collate("bookflow_integer"), _IntegerText())


def vendor_balance(db, id: str) -> int:
    """Signed minor units the company owes this vendor; fail on i64 overflow."""
    value = db.conn.execute(sa.select(vendor_balance_expression(db)).where(
        schema.vendors.c.id == id,
    )).scalar_one_or_none()
    return _require_i64(int(value or "0"), field="open_balance")
