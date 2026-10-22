"""Price rules and the pricing-rule preference (R110).

Two new tables: ``price_rules`` (conditions, an adjustment and rounding that price matching sales lines)
and ``price_rule_preferences`` (the company's one choice of how a line picks between its price level and
the rules that match: the lowest price, or the first rule by priority). Additive: nothing existing
changes. A line priced by a rule captures the rule in its own line facts, so no saved document reads
these tables. DDL below is frozen: this migration never imports current application metadata.
"""
from alembic import op

revision = 'co0086'
down_revision = 'co0083'
branch_labels = depends_on = None
DDL = (
    "\nCREATE TABLE price_rules (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(200) NOT NULL, \n\tname_key VARCHAR(400) NOT NULL, \n\tdescription VARCHAR(500), \n\tactive BOOLEAN NOT NULL, \n\tpriority INTEGER NOT NULL, \n\tdate_from VARCHAR(10), \n\tdate_to VARCHAR(10), \n\tmin_quantity_microunits BIGINT, \n\tconditions TEXT NOT NULL, \n\tadjustment_kind VARCHAR(8) NOT NULL, \n\tdirection VARCHAR(8) NOT NULL, \n\tbasis VARCHAR(12) NOT NULL, \n\tvalue BIGINT, \n\tbreaks TEXT, \n\trounding VARCHAR(8) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_price_rule_name_key UNIQUE (name_key), \n\tCONSTRAINT ck_price_rule_name_nonblank CHECK (length(trim(name)) > 0), \n\tCONSTRAINT ck_price_rule_active CHECK (active IN (0,1)), \n\tCONSTRAINT ck_price_rule_priority CHECK (priority >= 1), \n\tCONSTRAINT ck_price_rule_dates CHECK (date_from IS NULL OR date_to IS NULL OR date_from <= date_to), \n\tCONSTRAINT ck_price_rule_min_quantity CHECK (min_quantity_microunits IS NULL OR min_quantity_microunits > 0), \n\tCONSTRAINT ck_price_rule_kind CHECK (adjustment_kind IN ('percent','amount')), \n\tCONSTRAINT ck_price_rule_direction CHECK (direction IN ('down','up')), \n\tCONSTRAINT ck_price_rule_basis CHECK (basis IN ('sales_price','cost')), \n\tCONSTRAINT ck_price_rule_rounding CHECK (rounding IN ('cent','x.99','x.95','dollar')), \n\tCONSTRAINT ck_price_rule_value CHECK ((value IS NULL) != (breaks IS NULL)), \n\tCONSTRAINT ck_price_rule_value_sign CHECK (value IS NULL OR value >= 0)\n)\n\n",
    "\nCREATE TABLE price_rule_preferences (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tsingleton INTEGER NOT NULL, \n\tconflict_policy VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_price_rule_preferences_singleton UNIQUE (singleton), \n\tCONSTRAINT ck_price_rule_preferences_singleton CHECK (singleton = 1), \n\tCONSTRAINT ck_price_rule_preferences_policy CHECK (conflict_policy IN ('lowest_price','priority'))\n)\n\n",
)
NEW_OBJECTS = ('price_rules', 'price_rule_preferences')


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(NEW_OBJECTS):
        raise RuntimeError('co0086 reserved object exists')
    for statement in DDL:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
