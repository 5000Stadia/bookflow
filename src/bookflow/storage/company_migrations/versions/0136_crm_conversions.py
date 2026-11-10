"""co0136: lead conversions and source-keyed lead imports; nothing posts and CRM switches stay as they are."""
from alembic import op
revision = 'co0136'
down_revision = 'co0135'
branch_labels = depends_on = None
DDL = ["\nCREATE TABLE crm_conversions (\n\tid VARCHAR(26) NOT NULL, \n\tlead_id VARCHAR(26) NOT NULL, \n\tcustomer_id VARCHAR(26) NOT NULL, \n\ttarget VARCHAR(8) NOT NULL, \n\tbatch_id VARCHAR(26) NOT NULL, \n\trequest_hash VARCHAR(64) NOT NULL, \n\tlead_version_before INTEGER NOT NULL, \n\tlead_version_after INTEGER NOT NULL, \n\tlead_status_before VARCHAR(10) NOT NULL, \n\tlead_close_reason_before VARCHAR(1000), \n\tcustomer_version_before INTEGER, \n\tcustomer_version_after INTEGER NOT NULL, \n\tlead_label VARCHAR(200) NOT NULL, \n\tcustomer_label VARCHAR(400) NOT NULL, \n\tstate VARCHAR(10) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\tcreated_by VARCHAR(26) NOT NULL, \n\tcreated_via VARCHAR(16) NOT NULL, \n\treversed_at VARCHAR(32), \n\treversed_by VARCHAR(26), \n\treversal_batch_id VARCHAR(26), \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_crm_conversion_target CHECK (target IN ('new','existing')), \n\tCONSTRAINT ck_crm_conversion_state CHECK (state IN ('active','reversed')), \n\tCONSTRAINT ck_crm_conversion_reversed CHECK ((state = 'reversed') = (reversed_at IS NOT NULL)), \n\tFOREIGN KEY(lead_id) REFERENCES crm_leads (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE RESTRICT, \n\tUNIQUE (batch_id), \n\tFOREIGN KEY(batch_id) REFERENCES batches (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(reversal_batch_id) REFERENCES batches (id) ON DELETE RESTRICT\n)\n\n", '\nCREATE TABLE crm_conversion_contacts (\n\tid VARCHAR(26) NOT NULL, \n\tconversion_id VARCHAR(26) NOT NULL, \n\tlead_contact_id VARCHAR(26) NOT NULL, \n\tcustomer_contact_id VARCHAR(26) NOT NULL, \n\taction VARCHAR(8) NOT NULL, \n\tfields TEXT NOT NULL, \n\t"before" TEXT, \n\tPRIMARY KEY (id), \n\tCONSTRAINT ck_crm_conversion_contact_action CHECK (action IN (\'created\',\'updated\')), \n\tFOREIGN KEY(conversion_id) REFERENCES crm_conversions (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(lead_contact_id) REFERENCES crm_lead_contacts (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(customer_contact_id) REFERENCES customer_contacts (id) ON DELETE RESTRICT\n)\n\n', '\nCREATE TABLE crm_lead_import_keys (\n\tid VARCHAR(26) NOT NULL, \n\tsource VARCHAR(200) NOT NULL, \n\tsource_key VARCHAR(200) NOT NULL, \n\tpayload_hash VARCHAR(64) NOT NULL, \n\tlead_id VARCHAR(26) NOT NULL, \n\tbatch_id VARCHAR(26) NOT NULL, \n\tcreated_at VARCHAR(32) NOT NULL, \n\treleased_by VARCHAR(26), \n\treleased_at VARCHAR(32), \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(lead_id) REFERENCES crm_leads (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(batch_id) REFERENCES batches (id) ON DELETE RESTRICT, \n\tFOREIGN KEY(released_by) REFERENCES batches (id) ON DELETE RESTRICT\n)\n\n', 'CREATE INDEX ix_crm_conversions_customer ON crm_conversions (customer_id, state, created_at)', "CREATE UNIQUE INDEX ux_crm_conversions_active_lead ON crm_conversions (lead_id) WHERE state = 'active'", 'CREATE INDEX ix_crm_conversion_contacts_conversion ON crm_conversion_contacts (conversion_id, lead_contact_id)', 'CREATE INDEX ix_crm_lead_import_keys_lead ON crm_lead_import_keys (lead_id)', 'CREATE UNIQUE INDEX ux_crm_lead_import_keys_live ON crm_lead_import_keys (source, source_key) WHERE released_by IS NULL']
NEW_TABLES = ('crm_conversions', 'crm_conversion_contacts', 'crm_lead_import_keys')


def upgrade():
    db = op.get_bind()
    names = {row[0] for row in db.exec_driver_sql('SELECT name FROM sqlite_schema')}
    if names & set(NEW_TABLES):
        raise RuntimeError('co0136 reserved object exists')
    for statement in DDL:
        db.exec_driver_sql(statement)


def downgrade():
    raise RuntimeError('Migrations are forward-only')
