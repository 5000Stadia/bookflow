"""Build assemblies (R103): consume the components, receive the finished assembly at their cost.

Seven verbs, the anchor's Build Assemblies window and its pending builds: build (or mark pending), edit,
finalize a pending build, void, delete, show one, and list them -- `assembly list --status pending` is
the Pending Builds report. The bookkeeping lives in ``company/assembly_builds.py``.
"""
from bookflow.company import assembly_builds
from bookflow.company.assembly_build_models import (
    AssemblyBuildFinalizeInput, AssemblyBuildInput, AssemblyBuildListInput, AssemblyBuildListOutput,
    AssemblyBuildOutput, AssemblyBuildSelectInput, AssemblyBuildUpdateInput, AssemblyBuildVoidInput,
)
from bookflow.core.registry import Plan, command

_BUILD = (
    'Build an inventory assembly from its bill of materials. Each component is consumed -- its quantity per '
    'assembly times `quantity` -- at its weighted-average cost on `date`, and the assembly is received at the sum; '
    'a non-stock component (service, non-inventory part, other charge) adds its purchase cost and credits its '
    'expense account, as the anchor does. A component that is itself an assembly is consumed from stock, never '
    'built automatically. Dry-run is the Build Assemblies window: each component with the quantity needed, on '
    'hand on the date and reserved by pending builds, and `max_buildable`, the most the components on hand allow. '
    'Building more is refused naming the shortages, unless `pending` is set: a pending build posts nothing, '
    'reserves its components and is built later with `assembly finalize`. The build posts as a journal entry the '
    'inventory ledger owns: debit the assembly\'s inventory asset account, credit each component\'s; where they '
    'share one account (the usual case) the general ledger does not move (`posts_to_ledger` false). Stock status, '
    'inventory valuation and the balance sheet agree on every date. A backdated build recosts later sales of its '
    'components and of the assembly by dated corrections, and a later backdated purchase of a component recosts '
    'the build and the assembly with it. A date in a closed period is refused.'
)
_UPDATE = (
    'Edit a build: its quantity, date or memo. A posted build is restated -- the old consumption and receipt are '
    'reversed exactly and the build is posted again at the average costs now, and later documents are recosted '
    'by dated corrections. A pending build stays pending. More than the components allow is refused; a closed '
    'period, old date or new, is refused.'
)
_FINALIZE = (
    'Build a pending build now that its components are on hand, on its own date or on `date`, from the bill of '
    'materials it reserved. Refused naming the shortages while they are still short.'
)
_VOID = (
    'Void a posted build with a required reason: the components go back on the shelf and the assembly comes off, '
    'both at exactly what the build posted, and later documents are recosted by dated corrections. A pending '
    'build posted nothing; delete it instead. A closed period is refused.'
)
_DELETE = (
    'Delete a build with a required reason. A pending build is removed and its components released; a posted '
    'build is voided first, exactly as `assembly void` does. The build drops off `assembly list`; its history '
    'stays readable with `assembly show` and `--status deleted`.'
)
_SHOW = 'Show one build: the assembly, quantity, date, each component with what it cost, the assembly cost, and every revision.'
_LIST = ('List builds, newest first: posted, pending, voided, or with `--status deleted` the deleted ones. '
         '`--status pending` is the Pending Builds report, and `reserved` totals the components pending builds hold.')

_WRITE_ERRORS = ['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION', 'E_VALUE_RANGE',
                 'E_PERIOD_CLOSED', 'E_VERSION_CONFLICT', 'E_REASON_REQUIRED']


def _planner(operation):
    def planner(inp, ctx, s):
        return assembly_builds.prepare(s, ctx, inp, operation)
    planner.__name__ = planner.__qualname__ = f'plan_assembly_{operation}'
    return planner


plan_assembly_build = command(
    'assembly build', scope='company', description=_BUILD, input_model=AssemblyBuildInput,
    output_model=AssemblyBuildOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, error_codes=['E_RECORD_NOT_FOUND', 'E_INACTIVE_REFERENCE', 'E_VALIDATION',
                                               'E_VALUE_RANGE', 'E_PERIOD_CLOSED'])(_planner('build'))
plan_assembly_update = command(
    'assembly update', scope='company', description=_UPDATE, input_model=AssemblyBuildUpdateInput,
    output_model=AssemblyBuildOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, positional=['build'], error_codes=_WRITE_ERRORS[:-1])(_planner('update'))
plan_assembly_finalize = command(
    'assembly finalize', scope='company', description=_FINALIZE, input_model=AssemblyBuildFinalizeInput,
    output_model=AssemblyBuildOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, positional=['build'], error_codes=_WRITE_ERRORS[:-1])(_planner('finalize'))
plan_assembly_void = command(
    'assembly void', scope='company', description=_VOID, input_model=AssemblyBuildVoidInput,
    output_model=AssemblyBuildOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, positional=['build'], error_codes=_WRITE_ERRORS)(_planner('void'))
plan_assembly_delete = command(
    'assembly delete', scope='company', description=_DELETE, input_model=AssemblyBuildVoidInput,
    output_model=AssemblyBuildOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, positional=['build'], error_codes=_WRITE_ERRORS)(_planner('delete'))


@command('assembly show', scope='company', description=_SHOW, input_model=AssemblyBuildSelectInput,
         output_model=AssemblyBuildOutput, required_role='member', capability='ledger.read',
         positional=['build'], error_codes=['E_RECORD_NOT_FOUND'])
def plan_assembly_show(inp, ctx, s):
    return Plan(assembly_builds.show(s, inp))


@command('assembly list', scope='company', description=_LIST, input_model=AssemblyBuildListInput,
         output_model=AssemblyBuildListOutput, required_role='member', capability='ledger.read',
         error_codes=['E_RECORD_NOT_FOUND', 'E_VALIDATION'])
def plan_assembly_list(inp, ctx, s):
    return Plan(assembly_builds.page(s, inp))


for _write in (plan_assembly_build, plan_assembly_update, plan_assembly_finalize, plan_assembly_void,
               plan_assembly_delete):
    _write.ledger = True
    _write.applier(assembly_builds.apply)
