"""company: audit tables, presence, idempotency keys, directives, sequences; baseline entry

Revision ID: co0002
Revises: co0001
"""

import json
import sqlalchemy as sa
from alembic import op

from bookflow.company.schema import audit_entries, audit_events, directives, idempotency_keys, presence, sequences

revision = "co0002"
down_revision = "co0001"


def upgrade() -> None:
    conn = op.get_bind()
    for table in (audit_events, audit_entries, presence, idempotency_keys, directives, sequences):
        table.create(conn, checkfirst=True)
    conn.execute(sa.text("INSERT INTO sequences (name, next_number) VALUES ('directive', 1)"))
    # the baseline entry for company_info is written by the migration caller, which knows the actor (blueprint 7)


def downgrade() -> None:
    raise NotImplementedError
