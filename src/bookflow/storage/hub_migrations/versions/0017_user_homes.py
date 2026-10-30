"""Each person's Home page (R191, design/point-of-sale.md §8).

One new table, ``user_homes``: the company and workbench page a person lands on when they sign in or press Home.
A separate table rather than columns on ``users``, so setting a Home never moves a user's version, which the
permission audit reads as part of a person's authority. No existing table, column or row changes.
The DDL is literal: this revision is frozen.
"""
from alembic import op

revision = 'hub0017'
down_revision = 'hub0016'
branch_labels = None
depends_on = None
DDL = ('\nCREATE TABLE user_homes (\n\tuser_id VARCHAR(26) NOT NULL, \n\tcompany_id VARCHAR(26) NOT NULL, \n\tpath VARCHAR(400) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tset_at VARCHAR(32) NOT NULL, \n\tset_by VARCHAR(26) NOT NULL, \n\tset_via VARCHAR(16) NOT NULL, \n\tPRIMARY KEY (user_id), \n\tFOREIGN KEY(user_id) REFERENCES users (id)\n)\n\n',)
NEW_OBJECTS = ('user_homes', 'sqlite_autoindex_user_homes_1')


def upgrade():
    db = op.get_bind()
    names = {row[0].casefold() for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(NEW_OBJECTS):
        raise RuntimeError('hub0017 reserved object exists')
    for statement in DDL:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Hub migrations are forward-only')
