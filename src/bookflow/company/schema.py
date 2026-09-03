"""Company database tables for row 1 (blueprint 4.1a, 9.1)."""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()


def _common() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("created_by", sa.String(26), nullable=False),
        sa.Column("created_via", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.Column("updated_by", sa.String(26), nullable=False),
        sa.Column("updated_via", sa.String(16), nullable=False),
    ]


def _address(prefix: str) -> list[sa.Column]:
    return [sa.Column(f"{prefix}_{f}", sa.String(200), nullable=True) for f in ("line1", "line2", "city", "state", "postal_code", "country")]


company_info = sa.Table(
    "company_info", metadata,
    *_common(),
    sa.Column("legal_name", sa.String(200), nullable=False),
    sa.Column("display_name", sa.String(200), nullable=False),
    sa.Column("tax_id_kind", sa.String(3), nullable=False, default="ein"),
    sa.Column("tax_id", sa.String(16), nullable=True),
    sa.Column("entity_type", sa.String(24), nullable=False, default="other"),
    sa.Column("income_tax_form", sa.String(24), nullable=False, default="other"),
    sa.Column("industry", sa.String(128), nullable=True),
    sa.Column("contact_name", sa.String(128), nullable=True),
    *_address("address"),
    *_address("legal_address"),
    *_address("ship_address"),
    sa.Column("phone", sa.String(64), nullable=True),
    sa.Column("fax", sa.String(64), nullable=True),
    sa.Column("email", sa.String(254), nullable=True),
    sa.Column("website", sa.String(254), nullable=True),
    sa.Column("fiscal_year_start_month", sa.Integer, nullable=False, default=1),
    sa.Column("tax_year_start_month", sa.Integer, nullable=False, default=1),
    sa.Column("report_basis", sa.String(8), nullable=False, default="accrual"),
    sa.Column("home_currency", sa.String(3), nullable=False),
    sa.Column("timezone", sa.String(64), nullable=False),
    sa.Column("closing_date", sa.String(10), nullable=True),
    sa.Column("recent_activity_window_seconds", sa.Integer, nullable=False, default=60),
    sa.Column("default_chart", sa.String(64), nullable=True),
)

principals = sa.Table(
    "principals", metadata,
    sa.Column("user_id", sa.String(26), primary_key=True),
    sa.Column("username", sa.String(64), nullable=False),
    sa.Column("display_name", sa.String(128), nullable=False),
    sa.Column("kind", sa.String(8), nullable=False),
    sa.Column("first_seen_at", sa.String(32), nullable=False),
    sa.Column("last_seen_at", sa.String(32), nullable=False),
)
