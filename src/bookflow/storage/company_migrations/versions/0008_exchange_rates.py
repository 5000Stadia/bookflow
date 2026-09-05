"""Frozen manual exchange rates; revision co0008."""
from alembic import op

revision = 'co0008'
down_revision = 'co0007'
branch_labels = None
depends_on = None

DDL = "CREATE TABLE exchange_rates (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tdate VARCHAR(10) NOT NULL, \n\tfrom_currency VARCHAR(3) NOT NULL, \n\tto_currency VARCHAR(3) NOT NULL, \n\trate VARCHAR(31) NOT NULL, \n\tsource VARCHAR(16) NOT NULL, \n\tentered_by VARCHAR(26) NOT NULL, \n\tentered_at VARCHAR(32) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_exchange_rate_pair UNIQUE (date, from_currency, to_currency), \n\tCONSTRAINT ck_exchange_rate_version CHECK (typeof(version) = 'integer' AND version > 0), \n\tCONSTRAINT ck_exchange_rate_currencies CHECK (length(from_currency) = 3 AND from_currency NOT GLOB '*[^A-Z]*' AND length(to_currency) = 3 AND to_currency NOT GLOB '*[^A-Z]*'), \n\tCONSTRAINT ck_exchange_rate_foreign CHECK (from_currency <> to_currency), \n\tCONSTRAINT ck_exchange_rate_source CHECK (source = 'manual'), \n\tCONSTRAINT ck_exchange_rate_text CHECK (typeof(rate) = 'text' AND length(CAST(rate AS BLOB)) BETWEEN 1 AND 31), \n\tFOREIGN KEY(entered_by) REFERENCES principals (user_id)\n)"


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    raise NotImplementedError('Audited exchange rates cannot be downgraded')
