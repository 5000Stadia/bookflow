"""Exact customer AR nets over all immutable posting effects.

Callers own the company authorization and read snapshot. These are net ledger
balances, not invoice aging or application balances.
"""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema
from bookflow.company.ledger_reports import register_ledger_functions
from bookflow.core.exact import _require_i64
from bookflow.core.money import Money


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
    totals = sa.select(
        lines.c.name_id,
        sa.func.bookflow_sum_int(lines.c.debit_minor_units - lines.c.credit_minor_units).label("net"),
    ).select_from(lines.join(accounts, accounts.c.id == lines.c.account_id)).where(
        lines.c.name_type == "customer", accounts.c.type == "accounts_receivable",
    ).group_by(lines.c.name_id).cte("customer_ar_totals").prefix_with("MATERIALIZED")
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
    value = db.raw.execute("""
        WITH RECURSIVE family(id) AS (
            SELECT id FROM customers WHERE id = :id
            UNION
            SELECT c.id FROM customers c JOIN family f ON c.parent_id = f.id
        )
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
        same_family = db.raw.execute("""
            WITH RECURSIVE family(id) AS (
                SELECT id FROM customers WHERE id = :owner
                UNION
                SELECT c.id FROM customers c JOIN family f ON c.parent_id = f.id
            )
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
