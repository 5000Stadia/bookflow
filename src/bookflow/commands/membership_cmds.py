"""Membership plans and dated customer enrollments (R188 increments 1 and 2).

The work lives in ``company/memberships.py``; an invoice or sales receipt (a POS sale is one) asks it for
the sold-to customer's effective enrollment through one hook in ``sales_defaults.resolve_header``. Plans
need promotion.manage, enrollments and job inheritance promotion.assign with ordinary customer write
rights, reads promotion.read with customer read rights. Enrolling many customers is one reversible batch.
"""
from bookflow.company import memberships as ms
from bookflow.company import membership_models as m
from bookflow.core.registry import Plan, command

_WRITE_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_FEATURE_DISABLED', 'E_NAME_TAKEN',
                 'E_VERSION_CONFLICT']
_LAYER = (' The membership pricing layer must be on (`company update --membership-pricing-enabled true`);'
          ' off, the command is refused with E_FEATURE_DISABLED and prices resolve as they always have.')


def _write(name, description, inp, out, planner, applier, *, capability, errors, positional=(), version=None,
           resources=()):
    cmd = command(name, scope='company', description=description + _LAYER, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability=capability, accepts_idempotency_key=True,
                  positional=list(positional), version_source=version, error_codes=errors)(planner)
    if resources:
        cmd.resource_requirements = resources
    cmd.applier(applier)
    return cmd


def _read(name, description, inp, out, planner, *, errors, positional=(), resources=()):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out, writes=set(),
                  required_role='standard', capability='promotion.read', positional=list(positional),
                  error_codes=errors)(planner)
    if resources:
        cmd.resource_requirements = resources
    return cmd


def plan_create(inp, ctx, s) -> Plan:
    return ms.plan_create(s, ctx, inp)


def plan_update(inp, ctx, s) -> Plan:
    return ms.plan_update(s, ctx, inp)


def plan_show(inp, ctx, s) -> Plan:
    return Plan(ms.plan_show(s, inp))


def plan_list(inp, ctx, s) -> Plan:
    return Plan(ms.plan_list(s, inp))


def plan_set(inp, ctx, s) -> Plan:
    return ms.membership_set(s, ctx, inp)


def plan_end(inp, ctx, s) -> Plan:
    return ms.membership_end(s, ctx, inp)


def plan_membership_show(inp, ctx, s) -> Plan:
    return Plan(ms.membership_show(s, inp))


_PLAN_VERSION = ('membership-plan show', 'plan', 'version')

membership_plan_create = _write(
    'membership-plan create', 'Define a membership plan: a name and one existing price level that enrolled customers are'
    ' priced at on invoices dated inside their enrollment. No fee, renewal or payment is implied.'
    ' Example: "Trade members" at price level "Member Price".',
    m.PlanCreateInput, m.PlanWriteOutput, plan_create, ms.apply_plan, capability='promotion.manage',
    errors=_WRITE_ERRORS)
membership_plan_update = _write(
    'membership-plan update', 'Rename a membership plan, point it at another price level, or deactivate it, with its'
    ' expected version. New invoices use the change; saved invoices keep the level they captured.',
    m.PlanUpdateInput, m.PlanWriteOutput, plan_update, ms.apply_plan, capability='promotion.manage',
    errors=_WRITE_ERRORS, positional=['plan'], version=_PLAN_VERSION)
membership_plan_show = _read('membership-plan show', 'Show a membership plan, its price level and how many enrollments name it.',
                             m.PlanSelectInput, m.PlanOutput, plan_show, errors=['E_RECORD_NOT_FOUND'], positional=['plan'])
membership_plan_list = _read('membership-plan list', 'The membership plans in name order (active only unless include_inactive),'
                             ' and whether the membership pricing layer is on.',
                             m.PlanListInput, m.PlanPage, plan_list, errors=['E_VALIDATION'])
customer_membership_set = _write(
    'customer membership set', 'Enroll a customer (or a job, by itself) in a membership plan from start_date through'
    ' end_date, both inclusive company-local dates (omit end_date for no end yet); give membership and'
    ' expected_version to change an existing enrollment instead. An invoice dated inside the interval defaults to the'
    " plan's price level in place of the customer's ordinary one; an explicit price level or unit price still wins, and"
    ' price rules compete as usual. Overlapping enrollments are refused with E_MEMBERSHIP_OVERLAP. The customer'
    " record's own price level and saved invoices never change.",
    m.MembershipSetInput, m.MembershipWriteOutput, plan_set, ms.apply_membership, capability='promotion.assign',
    errors=[*_WRITE_ERRORS, 'E_MEMBERSHIP_OVERLAP'], positional=['customer'], resources=(('customer', 'standard'),))
customer_membership_end = _write(
    'customer membership end', 'End an enrollment on end_date (inclusive), with its expected version. Invoices dated'
    ' after it price at the ordinary level; invoices already saved keep their prices.',
    m.MembershipEndInput, m.MembershipWriteOutput, plan_end, ms.apply_membership, capability='promotion.assign',
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_FEATURE_DISABLED', 'E_VERSION_CONFLICT'],
    positional=['membership'], resources=(('customer', 'standard'),))
customer_membership_show = _read(
    'customer membership show', "A customer's enrollment history, newest first, and the enrollment covering as_of"
    ' (today by default) when the layer is on. Member numbers are shown only here.',
    m.MembershipShowInput, m.CustomerMembershipOutput, plan_membership_show, errors=['E_RECORD_NOT_FOUND'],
    positional=['customer'], resources=(('customer', 'member'),))


# ---------------------------------------------------------------------------- increment 2 (membership-sales-v1)

def plan_inherit(inp, ctx, s) -> Plan:
    return ms.inheritance_set(s, ctx, inp)


def plan_members(inp, ctx, s) -> Plan:
    return Plan(ms.members_list(s, inp))


def plan_enroll(inp, ctx, s) -> Plan:
    return ms.enroll_many(s, ctx, inp)


customer_membership_inherit = _write(
    'customer membership inherit', "Say whether a job inherits its nearest ancestor's membership (true, the default for"
    ' every job) or uses only its own enrollment (false). A job with its own enrollment covering the sale date always'
    ' uses it. Sales already saved keep the membership they captured.',
    m.InheritanceSetInput, m.InheritanceOutput, plan_inherit, ms.apply_inheritance, capability='promotion.assign',
    errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_FEATURE_DISABLED', 'E_VERSION_CONFLICT'],
    positional=['customer'], resources=(('customer', 'standard'),))
customer_membership_list = _read(
    'customer membership list', 'The customers a membership prices on as_of (today by default), in name order: those'
    ' enrolled and, unless include_inherited is false, the jobs that inherit from them, each with whose enrollment it'
    ' is. Filter by plan. Member numbers are not listed. Empty while the layer is off.',
    m.MemberListInput, m.MemberPage, plan_members, errors=['E_VALIDATION', 'E_RECORD_NOT_FOUND'],
    resources=(('customer', 'member'),))
customer_membership_enroll = _write(
    'customer membership enroll', 'Enroll several customers (up to 200, each once) in one plan for the same dates, all'
    ' or none, as one reversible batch. Preview it with --dry-run first. A mistaken run is undone as a unit with'
    ' `batch reverse <batch>`, which refuses, naming each, while an enrollment was changed since or a saved sale'
    ' captured it; ending an enrollment is the separate, prospective act.',
    m.EnrollManyInput, m.EnrollManyOutput, plan_enroll, ms.apply_enroll_many, capability='promotion.assign',
    errors=[*_WRITE_ERRORS, 'E_MEMBERSHIP_OVERLAP'], resources=(('customer', 'standard'),))
