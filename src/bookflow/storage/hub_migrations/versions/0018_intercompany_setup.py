"""hub0018: staged intercompany setup; empty settings mean all layers off."""
from alembic import op
import secrets
revision = 'hub0018'
down_revision = 'hub0017'
branch_labels = depends_on = None
DDL = ['\nCREATE TABLE intercompany_relationships (\n\tid VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\tupdated_at VARCHAR(32) NOT NULL, \n\tupdated_by VARCHAR(26) NOT NULL, \n\tupdated_via VARCHAR(16) NOT NULL, \n\torganization_id VARCHAR(26) NOT NULL, \n\tcompany_a VARCHAR(26) NOT NULL, \n\tcompany_b VARCHAR(26) NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tmapping_a_digest VARCHAR(64) NOT NULL, \n\tmapping_b_digest VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_intercompany_pair UNIQUE (company_a, company_b), \n\tCONSTRAINT ck_intercompany_pair_order CHECK (company_a < company_b), \n\tCONSTRAINT ck_intercompany_enabled CHECK (enabled IN (0,1)), \n\tFOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(company_a) REFERENCES companies (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(company_b) REFERENCES companies (id) ON DELETE RESTRICT\n)\n\n', '\nCREATE TABLE intercompany_relationship_revisions (\n\trelationship_id VARCHAR(26) NOT NULL, \n\tversion INTEGER NOT NULL, \n\tenabled BOOLEAN NOT NULL, \n\tmapping_a_digest VARCHAR(64) NOT NULL, \n\tmapping_b_digest VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (relationship_id, version), \n\tFOREIGN KEY(relationship_id) REFERENCES intercompany_relationships (id) ON DELETE RESTRICT\n)\n\n', "CREATE TRIGGER intercompany_relationship_revisions_no_update BEFORE UPDATE ON intercompany_relationship_revisions BEGIN SELECT RAISE(ABORT, 'Configuration revisions are immutable'); END", "CREATE TRIGGER intercompany_relationship_revisions_no_delete BEFORE DELETE ON intercompany_relationship_revisions BEGIN SELECT RAISE(ABORT, 'Configuration revisions are immutable'); END"]
DDL.insert(0, "CREATE TABLE intercompany_identity (id INTEGER NOT NULL PRIMARY KEY, key VARCHAR(64) NOT NULL, CONSTRAINT ck_intercompany_identity CHECK (id = 1))")
NEW_TABLES = ('intercompany_identity', 'intercompany_relationships', 'intercompany_relationship_revisions')


def upgrade():
    db = op.get_bind()
    names = {row[0] for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(NEW_TABLES):
        raise RuntimeError('hub0018 reserved object exists')
    for statement in DDL:
        db.exec_driver_sql(statement)
    db.exec_driver_sql('INSERT INTO intercompany_identity (id,key) VALUES (1,?)', (secrets.token_hex(32),))


def downgrade():
    raise RuntimeError('Migrations are forward-only')
