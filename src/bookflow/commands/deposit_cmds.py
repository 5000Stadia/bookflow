"""Registered public deposit detail contracts; execution requires an authenticated reader.

Only `deposit show` and `deposit items` are registered here. Deposit query, its
register/search/totals surface and every deposit write stay unregistered. Like
the projected history family, these commands carry no plan-time implementation:
the trusted execution owner runs them under an authenticated reader that also
supplies the revalidatable binding the financial owners require.

That owner is `core.publication_deposit`, reached through `core.deposit_offline`
from `core.dispatch` and through the sibling branch in
`adapters.http.execution`, which together cover every registered route. The
refusals below are therefore unreachable guards, not the behaviour a caller
sees; `tests/test_deposit_public_execution.py` witnesses that no route reaches
them.
"""
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
