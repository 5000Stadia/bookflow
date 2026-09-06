"""Immutable commercial facts owned by sales revisions and their ordered lines."""

import sqlalchemy as sa


def define_tables(metadata, column, table):
    C, T = column, table

    def identifier(name, description, target=None, *, primary_key=False, nullable=False):
        constraints = [sa.ForeignKey(target)] if target else []
        return C(name, sa.String(26), description, *constraints,
                 primary_key=primary_key, nullable=nullable)

    def created():
        return [C('created_at', sa.String(32), 'UTC time this commercial history was written.', nullable=False),
                identifier('created_by', 'Company principal that wrote this commercial history.'),
                C('created_via', sa.String(16), 'Interface that wrote this commercial history.', nullable=False)]

    def integer(name, description):
        return C(name, sa.BigInteger, description, nullable=False)

    def nonnegative(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} >= 0",
                                  name=f'ck_sales_{name}_nonnegative')

    def positive(name):
        return sa.CheckConstraint(f"typeof({name}) = 'integer' AND {name} > 0",
                                  name=f'ck_sales_{name}_positive')

    def snapshot(name, description):
        return C(name, sa.Text, description, nullable=False)

    def object_check(name):
        return sa.CheckConstraint(f"json_valid({name}) AND json_type({name}) = 'object'",
                                  name=f'ck_sales_{name}_object')

    sales_profiles = T('sales_profiles',
        identifier('revision_id', 'Immutable revision owning this one-to-one sales header.', primary_key=True),
        identifier('transaction_id', 'Stable sales document owning this revision.'),
        *created(),
        C('type', sa.String(32), 'Commercial type: invoice or sales_receipt.', nullable=False),
        identifier('customer_id', 'Customer or job captured for the sale.', 'customers.id'),
        identifier('control_account_id', 'Receivable account for an invoice or deposit account for a receipt.', 'accounts.id'),
        C('due_date', sa.String(10), 'Captured invoice due date; null for a sales receipt.', nullable=True),
        integer('subtotal_minor_units', 'Home-currency subtotal before tax.'),
        integer('tax_minor_units', 'Home-currency sum of captured component taxes.'),
        snapshot('profile_snapshot', 'Versioned typed JSON object of resolved header facts, rules and input origins.'),
        sa.UniqueConstraint('transaction_id', 'revision_id', name='uq_sales_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['transaction_revisions.transaction_id', 'transaction_revisions.id'], name='fk_sales_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'type'],
            ['transactions.id', 'transactions.type'], name='fk_sales_profile_type'),
        sa.CheckConstraint("(type = 'invoice' AND due_date IS NOT NULL) OR (type = 'sales_receipt' AND due_date IS NULL)",
                           name='ck_sales_profile_type_due'),
        nonnegative('subtotal_minor_units'), nonnegative('tax_minor_units'), object_check('profile_snapshot'),
        description='Immutable one-to-one sales revision headers, resolved customer facts and commercial totals.')

    sales_line_profiles = T('sales_line_profiles',
        identifier('document_line_id', 'Revision-local sale envelope owning this one-to-one profile.', primary_key=True),
        identifier('transaction_id', 'Stable document owning this commercial line.'),
        identifier('revision_id', 'Exact immutable sales revision owning this line.'),
        *created(),
        identifier('item_id', 'Captured sold item.', 'items.id'),
        C('quantity_microunits', sa.BigInteger, 'Exact selected-unit quantity in millionths; null only for an allocated fraction.', nullable=True),
        identifier('unit_id', 'Captured selected unit conversion; null without a unit.', 'unit_conversions.id', nullable=True),
        integer('unit_factor_nanounits', 'Positive captured base units per selected unit in billionths.'),
        C('base_quantity_microunits', sa.BigInteger, 'Exact base quantity in millionths; null only for an allocated fraction.', nullable=True),
        C('unit_price_minor_units', sa.BigInteger, 'Home-currency price per selected unit; null for amount pricing.', nullable=True),
        C('pricing_basis', sa.String(16), 'Authoritative unit, amount or allocated pricing mode.', nullable=False, server_default='unit'),
        integer('net_minor_units', 'Rounded home-currency extended line price before tax.'),
        integer('tax_minor_units', 'Home-currency sum of this line tax components.'),
        integer('gross_minor_units', 'Home-currency line net plus tax.'),
        snapshot('item_snapshot', 'Versioned typed JSON object of resolved item, account, unit, pricing, tax rules and origins.'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', name='uq_sales_line_profile_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id'],
            ['sales_profiles.transaction_id', 'sales_profiles.revision_id'], name='fk_sales_line_profile_revision'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['document_lines.transaction_id', 'document_lines.revision_id', 'document_lines.id'], name='fk_sales_line_profile_line'),
        positive('unit_factor_nanounits'),
        *(sa.CheckConstraint(f"(typeof({name}) = 'integer' AND {name} > 0) OR (pricing_basis = 'allocated' AND {name} IS NULL)", name=f'ck_sales_{name}_positive') for name in ('quantity_microunits', 'base_quantity_microunits')),
        sa.CheckConstraint("(pricing_basis = 'unit' AND typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0) OR (pricing_basis = 'amount' AND unit_price_minor_units IS NULL) OR (pricing_basis = 'allocated' AND (unit_price_minor_units IS NULL OR (typeof(unit_price_minor_units) = 'integer' AND unit_price_minor_units >= 0)))", name='ck_sales_pricing_basis'),
        nonnegative('net_minor_units'), nonnegative('tax_minor_units'),
        nonnegative('gross_minor_units'), object_check('item_snapshot'),
        description='Immutable one-to-one commercial item lines with exact quantities, prices and captured rules.')

    sales_tax_components = T('sales_tax_components',
        identifier('id', 'Stable ULID of this immutable tax component.', primary_key=True),
        identifier('transaction_id', 'Stable document owning this tax component.'),
        identifier('revision_id', 'Exact immutable sales revision owning this tax component.'),
        identifier('document_line_id', 'Exact revision-local commercial line taxed by this component.'),
        *created(),
        identifier('tax_item_id', 'Captured individual sales tax item.', 'items.id'),
        identifier('agency_id', 'Captured tax agency vendor.', 'vendors.id'),
        identifier('liability_account_id', 'Captured sales tax payable posting account.', 'accounts.id'),
        integer('rate_percent_millionths', 'Nonnegative tax percentage in millionths of one percent.'),
        integer('taxable_minor_units', 'Home-currency amount subject to this component.'),
        integer('tax_minor_units', 'Captured policy-attributed home-currency component tax; zero is retained.'),
        snapshot('component_snapshot', 'Versioned typed JSON object of captured tax item, agency and liability labels.'),
        sa.UniqueConstraint('transaction_id', 'revision_id', 'document_line_id', 'id', name='uq_sales_tax_component_owner'),
        sa.ForeignKeyConstraint(['transaction_id', 'revision_id', 'document_line_id'],
            ['sales_line_profiles.transaction_id', 'sales_line_profiles.revision_id', 'sales_line_profiles.document_line_id'],
            name='fk_sales_tax_component_line'),
        nonnegative('rate_percent_millionths'), nonnegative('taxable_minor_units'), nonnegative('tax_minor_units'),
        object_check('component_snapshot'),
        description='Immutable policy-attributed tax components, including legacy, zero-rate and rounding-to-zero facts.')

    return {name: value for name, value in locals().items() if isinstance(value, sa.Table)}
