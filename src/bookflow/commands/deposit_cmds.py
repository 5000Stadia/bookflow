"""Deposits: bank the receipts sitting in Undeposited Funds.

Write verbs translate the owned private deposit aggregate
(`company/deposit_lifecycle.py` plans it, `company/deposit_persistence.py` writes
it in one transaction). Read verbs use the sealed public reader execution owner.
No accounting decision is taken in this module.
"""
from bookflow.core.registry import command, Plan, Applied
from bookflow.company import deposit_lifecycle as lifecycle, deposit_persistence as persistence
from bookflow.company import deposit_outputs as outputs, deposit_source_queries as source_queries
from bookflow.company.deposit_draft_models import SourceQuery
from bookflow.company.deposit_lifecycle_models import LifecycleOutput, PostInput, UpdateInput, VoidInput
from bookflow.company.deposit_outputs import DepositSourcesOutput, DepositWriteOutput


DESCRIPTIONS = {
    'post': ('Bank selected undeposited customer payments and sales receipts into one bank account as one '
             'deposit, with any other money entered beside them and an optional cash-back line; credits '
             'Undeposited Funds for each receipt and debits the bank for the net total. Discover the '
             'receipts, their ids and their expected versions with `deposit sources`.'),
    'update': ('Correct a posted deposit by complete replacement: the old batch is reversed at its own date, '
               'a new revision and its claims are recorded at the new date, and receipts left out of the '
               'replacement return to Undeposited Funds.'),
    'void': ('Void a deposit with an exact reversal at its original date; the receipts it banked stay posted '
             'and become available to deposit again.'),
}

ERRORS = ['E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_PREVIEW_STALE', 'E_PERIOD_CLOSED', 'E_INACTIVE_REFERENCE',
          'E_DUPLICATE_NUMBER', 'E_AMOUNT_PRECISION', 'E_VALUE_RANGE', 'E_REASON_REQUIRED', 'E_SCHEMA_BEHIND',
          'E_DEPOSIT_SOURCE_INELIGIBLE', 'E_DEPOSIT_SOURCE_CLAIMED', 'E_DEPOSIT_SOURCE_INVALID',
          'E_DEPOSIT_DATE_BEFORE_SOURCE', 'E_DEPOSIT_TOTAL', 'E_DEPOSIT_OPERATION_KEY_REUSED',
          'E_DEPOSIT_DRAFT_STATE', 'E_DEPOSIT_DEPENDENCY']


def _binding(s):
    """The authenticated producer: the host's credential, or this login's OS binding."""
    return getattr(s, 'credential', None)


def _financial(verb, model):
    def planner(inp, ctx, s):
        prepared = lifecycle.prepare(s, ctx, inp, verb, binding=_binding(s))
        if isinstance(prepared, LifecycleOutput):
            # An exact permanent replay: the original effect, never a second write.
            return Plan(outputs.write_output(prepared), data={'recovered': True})
        return Plan(outputs.write_output(persistence.preview(s, ctx, prepared), dry_run=s.dry_run),
                    data={'prepared': prepared})

    def apply(plan, ctx, s):
        if plan.data.get('recovered'):
            return Applied(plan.preview, [], 'recovered deposit operation')
        # The aggregate re-resolves, revalidates and audits itself inside this
        # transaction; dispatch must not write a second event over its own.
        output = persistence.execute(s, ctx, plan.data['prepared'])
        return Applied(outputs.write_output(output), [], 'deposit ' + verb, audited=True)

    cmd = command('deposit ' + verb, scope='company', description=DESCRIPTIONS[verb],
        input_model=model, output_model=DepositWriteOutput, writes={'company'}, required_role='standard',
        capability='ledger.post', accepts_idempotency_key=True,
        positional=[] if verb == 'post' else ['deposit'],
        error_codes=ERRORS)(planner)
    cmd.ledger = True
    cmd.applier(apply)
    return cmd


deposit_post = _financial('post', PostInput)
deposit_update = _financial('update', UpdateInput)
deposit_void = _financial('void', VoidInput)


@command('deposit sources', scope='company',
    description=('List the customer payments and sales receipts sitting in Undeposited Funds that a deposit '
                 'dated `date` could bank, with each receipt id, expected version and exact amount, plus the '
                 'authorized count and subtotal of the whole filter.'),
    input_model=SourceQuery, output_model=DepositSourcesOutput, required_role='member', capability='ledger.read',
    error_codes=['E_RECORD_NOT_FOUND', 'E_QUERY_STALE', 'E_VALIDATION', 'E_SCHEMA_BEHIND',
                 'E_DEPOSIT_SOURCE_INVALID'])
def deposit_sources(inp, ctx, s):
    return Plan(outputs.sources_page(s, source_queries.query(s, inp, ctx=ctx, binding=_binding(s))))


from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.registry import command, Plan
from bookflow.core.session import Session
from bookflow.company.deposit_read_models import ShowInput, ItemsInput
from bookflow.company.deposit_public_models import DepositDetail, DepositItemsPage

_ERRORS = ['E_RECORD_NOT_FOUND', 'E_VALIDATION', 'E_PERMISSION', 'E_UNAUTHENTICATED',
           'E_DEPOSIT_SOURCE_INVALID']


@command('deposit show', scope='company',
         description='Show one deposit: its selected revision header, exact totals and counts, current status, '
                     'optional dated bank effect and the business references this member may see.',
         input_model=ShowInput, output_model=DepositDetail,
         required_role='member', capability='ledger.read', positional=['deposit'],
         error_codes=_ERRORS)
def deposit_show(inp: ShowInput, ctx: Context, s: Session) -> Plan:
    raise BookflowError('E_INTERNAL', message='Deposit details require authenticated reader execution')


@command('deposit items', scope='company',
         description='Page one deposit revision\'s composition: contributing receipts, additional cash rows or '
                     'cash allocations, keeping the selected revision across pages.',
         input_model=ItemsInput, output_model=DepositItemsPage,
         required_role='member', capability='ledger.read', positional=['deposit'],
         error_codes=[*_ERRORS, 'E_QUERY_STALE'])
def deposit_items(inp: ItemsInput, ctx: Context, s: Session) -> Plan:
    raise BookflowError('E_INTERNAL', message='Deposit details require authenticated reader execution')


DEPOSIT_COMMANDS = [deposit_show, deposit_items]
