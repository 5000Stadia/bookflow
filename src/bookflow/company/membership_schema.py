"""Membership plans and dated customer enrollments (R188 increment 1).

A plan names an existing price level; an enrollment says which customer may use that plan, from
which company-local date through which (both inclusive). Neither row carries money: a member price
is the plan's price level, resolved by the ordinary sales resolver.
"""
import sqlalchemy as sa

FLAG = 'membership_pricing_enabled'
STATUSES = ('active', 'suspended', 'ended')


def company_column(C):
    return C(FLAG, sa.Boolean, 'Optional membership pricing layer: an enrolled customer defaults to its plan price level '
             'on invoices dated inside the enrollment; off by default.', nullable=False, server_default=sa.text('0'))


def define_tables(metadata, C, T, common):
    plans = T('membership_plans', *common(),
        C('name', sa.String(200), 'Plan name as entered, such as Trade members.', nullable=False),
        C('name_key', sa.String(400), 'NFC-normalized, trimmed and case-folded name; unique within the company.', nullable=False),
        C('description', sa.String(500), 'What the plan is for; never printed on a sale.', nullable=True),
        C('price_level_id', sa.String(26), 'Existing price level a member is priced at; edits reach future drafts only.',
          sa.ForeignKey('price_levels.id', ondelete='RESTRICT'), nullable=False),
        C('active', sa.Boolean, 'Whether new enrollments may name the plan and new invoices may use it.', nullable=False),
        sa.UniqueConstraint('name_key', name='uq_membership_plan_name_key'),
        sa.CheckConstraint('length(trim(name)) > 0', name='ck_membership_plan_name'),
        sa.CheckConstraint('active IN (0,1)', name='ck_membership_plan_active'),
        description='Membership plans: a named, versioned link to one existing price level; no fee or renewal.')
    memberships = T('customer_memberships', *common(),
        C('customer_id', sa.String(26), 'Enrolled customer or job; inheritance by jobs is not implied.',
          sa.ForeignKey('customers.id', ondelete='RESTRICT'), nullable=False),
        C('plan_id', sa.String(26), 'Plan the customer may use.', sa.ForeignKey('membership_plans.id', ondelete='RESTRICT'), nullable=False),
        C('start_date', sa.String(10), 'First company-local transaction date, YYYY-MM-DD, the enrollment covers.', nullable=False),
        C('end_date', sa.String(10), 'Last company-local transaction date covered, inclusive; null for no end yet.', nullable=True),
        C('status', sa.String(10), 'active or ended; suspended is reserved for a later increment.', nullable=False),
        C('member_number', sa.String(40), 'Optional private member number; never printed by default.', nullable=True),
        C('member_number_key', sa.String(80), 'Case-folded member number; unique within the company.', nullable=True),
        sa.CheckConstraint('end_date IS NULL OR start_date <= end_date', name='ck_customer_membership_dates'),
        sa.CheckConstraint("status IN ('active','suspended','ended')", name='ck_customer_membership_status'),
        sa.CheckConstraint("status != 'ended' OR end_date IS NOT NULL", name='ck_customer_membership_ended'),
        sa.CheckConstraint('(member_number IS NULL) = (member_number_key IS NULL)', name='ck_customer_membership_number'),
        sa.Index('ix_customer_memberships_customer_interval', 'customer_id', 'start_date', 'end_date'),
        sa.Index('ix_customer_memberships_plan', 'plan_id', 'id'),
        sa.Index('ux_customer_memberships_member_number', 'member_number_key', unique=True,
                 sqlite_where=sa.text('member_number_key IS NOT NULL')),
        description='Dated customer enrollments in a membership plan; at most one effective per customer and date.')
    return {'membership_plans': plans, 'customer_memberships': memberships}
