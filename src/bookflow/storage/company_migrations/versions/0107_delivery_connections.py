"""Document delivery, increment 2: the queue and the transport seam (R112, design/document-delivery.md §4, §8).

One new table, ``delivery_connections``: a sending connection a person sets up -- a name, a ``kind`` (free text, no
CHECK list: a new kind needs no schema change), non-secret JSON ``settings``, a ``secret_ref`` naming where the secret
is (``env:NAME`` or ``keyring:service/name``; never the secret), ``enabled`` with the machine it was enabled on, and an
optional ``daily_limit``. Connections are removed, never deleted (a guard). This build registers no transport, so no
company gains a row from this migration and nothing can send. Moves no stored row. DDL below is frozen: this migration
never imports current application metadata.
"""
from alembic import op

revision = 'co0107'
down_revision = 'co0106'
branch_labels = depends_on = None

DDL = ("\nCREATE TABLE delivery_connections (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\tname VARCHAR(100) NOT NULL, \n\tname_key VARCHAR(100) NOT NULL, \n\tkind VARCHAR(64) NOT NULL, \n\tsettings TEXT NOT NULL, \n\tsecret_ref VARCHAR(200), \n\tenabled BOOLEAN NOT NULL, \n\tenabled_host VARCHAR(64), \n\tdaily_limit INTEGER, \n\tlast_check_at VARCHAR(32), \n\tlast_check_ok BOOLEAN, \n\tlast_check_text VARCHAR(500), \n\tremoved_at VARCHAR(32), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_delivery_connection_name CHECK (length(trim(name)) BETWEEN 1 AND 100), \n\tCONSTRAINT ck_delivery_connection_kind CHECK (length(kind) BETWEEN 1 AND 64), \n\tCONSTRAINT ck_delivery_connection_settings CHECK (json_valid(settings) AND json_type(settings) = 'object'), \n\tCONSTRAINT ck_delivery_connection_secret_ref CHECK (secret_ref IS NULL OR secret_ref GLOB 'env:?*' OR secret_ref GLOB 'keyring:?*/?*'), \n\tCONSTRAINT ck_delivery_connection_enabled CHECK ((enabled = 1) = (enabled_host IS NOT NULL) AND (removed_at IS NULL OR enabled = 0)), \n\tCONSTRAINT ck_delivery_connection_limit CHECK (daily_limit IS NULL OR daily_limit > 0)\n)\n\n",)
INDEXES = ('CREATE UNIQUE INDEX uq_delivery_connection_name ON delivery_connections (name_key) WHERE removed_at IS NULL',)
GUARDS = ("CREATE TRIGGER delivery_connections_no_delete BEFORE DELETE ON delivery_connections BEGIN SELECT RAISE(ABORT, 'connections are removed, never deleted'); END",)
TABLES = ('delivery_connections',)
# What a test rewinding a head file drops: the guards, then the table.
TRIGGERS = tuple(statement.split()[2] for statement in GUARDS)


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & (set(TABLES) | set(TRIGGERS)):
        raise RuntimeError('co0106 reserved object exists')
    for statement in DDL + INDEXES + GUARDS:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Company migrations are forward-only')
