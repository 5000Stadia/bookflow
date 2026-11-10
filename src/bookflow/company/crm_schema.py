"""Lead-owned contacts and identity; optional CRM layers remain off."""
import sqlalchemy as sa


def define_tables(c):
    C, T = c._column, c._table
    leads = T('crm_leads', *c._common(),
        C('display_name', sa.String(200), 'Lead display name; duplicate names are permitted.', nullable=False),
        C('name_key', sa.String(400), 'Normalized case-folded name used for exact selectors.', nullable=False),
        C('company_name', sa.String(200), 'Optional business name.', nullable=True),
        *c._person_identity(), *c._record_address('address', 'Lead postal address'),
        C('owner_id', sa.String(26), 'Responsible company member; ownership grants no authority.', nullable=True),
        C('source', sa.String(500), 'Where the enquiry came from.', nullable=True),
        C('rating', sa.String(8), 'unrated, cold, warm or hot.', nullable=False),
        C('summary', sa.Text, 'Short internal summary, separate from attributed notes.', nullable=True),
        C('status', sa.String(10), 'Lifecycle: open, closed or converted.', nullable=False),
        C('close_reason', sa.String(1000), 'Reason for closing; null while open.', nullable=True),
        C('archived', sa.Boolean, 'Hidden from ordinary lists without deleting history.', nullable=False),
        C('duplicate_of', sa.String(26), 'Explicit same-company duplicate lead; never an automatic merge.', sa.ForeignKey('crm_leads.id', ondelete='RESTRICT'), nullable=True),
        sa.CheckConstraint("length(trim(display_name)) > 0", name='ck_crm_lead_name'),
        sa.CheckConstraint("rating IN ('unrated','cold','warm','hot')", name='ck_crm_lead_rating'),
        sa.CheckConstraint("status IN ('open','closed','converted')", name='ck_crm_lead_status'),
        sa.CheckConstraint("status != 'closed' OR length(trim(close_reason)) > 0", name='ck_crm_lead_close'),
        sa.CheckConstraint('duplicate_of IS NULL OR duplicate_of != id', name='ck_crm_lead_duplicate'),
        sa.Index('ix_crm_leads_lifecycle_owner_name', 'status', 'archived', 'owner_id', 'name_key'),
        sa.Index('ix_crm_leads_name', 'name_key', 'id'),
        description='Prospects separate from accounting customers; no balances or ledger effects.')
    contacts = T('crm_lead_contacts', *c._owned_child('lead_id', 'crm_leads'), *c._contact_columns(),
        sa.CheckConstraint("role IN ('primary','alternate','additional')", name='ck_crm_lead_contacts_role'),
        sa.Index('ux_crm_lead_contacts_primary', 'lead_id', unique=True, sqlite_where=sa.text("active = 1 AND role = 'primary'")),
        sa.Index('ux_crm_lead_contacts_alternate', 'lead_id', unique=True, sqlite_where=sa.text("active = 1 AND role = 'alternate'")),
        description='Stable ordered contacts owned and versioned by a lead.')
    points = T('crm_lead_contact_points', *c._owned_child('contact_id', 'crm_lead_contacts'), *c._contact_point_columns(),
        description='Stable typed contact points owned through a lead contact.')
    return dict(crm_leads=leads, crm_lead_contacts=contacts, crm_lead_contact_points=points)
