"""Public deposit detail preparation and its bounded retained request.

The two registered deposit read commands carry no plan-time implementation. Like
the projected history family they are prepared here, under the actual
authenticated reader, and executed by the publication owner in
``publication_deposit``. Nothing in this module reads deposit facts: it settles
context, activation, the selected company and the strict input, and freezes them
into a value the release check can reproduce without the original session.

Dispatch consults ``COMMANDS`` on every offline command, so this module stays
import-cheap: the wire models, the permission audience and SQLAlchemy are all
imported inside the functions that need them.
"""
from dataclasses import dataclass

from bookflow.core.errors import BookflowError

# Only public deposit detail reads use this path. Existing writes and sources
# keep their ordinary execution path; deposit query remains unregistered.
COMMANDS = frozenset({'deposit show', 'deposit items'})


def input_model(command):
    """The strict input each public deposit command owns."""
    from bookflow.company.deposit_read_models import ItemsInput, ShowInput
    return {'deposit show': ShowInput, 'deposit items': ItemsInput}.get(command)


def resolve_company(reader, audience, selector, source):
    """Resolve the selector against companies this audience can actually see.

    Mirrors the history family: an invisible company is not found rather than
    denied, and a visible company still has to admit the read requirement.
    """
    import sqlalchemy as sa
    from bookflow.core.dispatch import _resolve_visible_company
    from bookflow.hub import schema as h
    if selector is None:
        raise BookflowError('E_COMPANY_NOT_FOUND', message=(
            'No company selected; give --company, set BOOKFLOW_COMPANY, or run '
            '`bookflow company use <company>`.'), details={'source': 'none'})
    visible = [scope.id for scope in audience.comparison.scopes
               if scope.kind == 'company' and audience.visible(scope)]
    row = _resolve_visible_company(reader.session, selector, source,
                                   h.companies.c.id.in_(visible) if visible else sa.false())
    audience.require(row['id'])
    audience.validate()
    return row['id']


@dataclass(frozen=True)
class DepositRequest:
    """Closed, reproducible request: no session, reader, binding or callback."""

    command: str
    company: str
    input: object

    @classmethod
    def capture(cls, command, company, value):
        model = input_model(command)
        if model is None or type(value) is not model or type(company) is not str or not company:
            raise TypeError('closed deposit request required')
        # Round trip through the strict model: model_copy is not validation, and
        # the retained value must be exactly what the wire contract accepts.
        return cls(command, company, model.model_validate_json(value.model_dump_json(exclude_unset=True)))

    def __deepcopy__(self, memo):
        # Every leaf is a frozen typed value; the retained receipt cache copies
        # permits, and copying this request must not clone a live object graph.
        return self


def prepare(reader, audience, cmd, raw, ctx, selector, source, dry_run=False):
    """Settle context, activation, company and strict input under the reader."""
    from bookflow.company import deposit_public_authority as pa
    from bookflow.core.context import CONTEXT_FIELD_NAMES
    from bookflow.core.context_options import normalize_options
    from bookflow.core.dispatch import validate_context, validate_input
    from bookflow.hub.access import require_command_activation
    if cmd.name not in COMMANDS or cmd.is_write or dry_run:
        raise BookflowError('E_VALIDATION')
    if type(audience) is not pa.DepositAudience or audience.reader is not reader:
        raise BookflowError('E_UNAUTHENTICATED')
    normalize_options(cmd, company=selector, reason=ctx.reason, source_ref=ctx.source_ref,
                      directive=ctx.directive_id, idempotency_key=ctx.idempotency_key, dry_run=dry_run)
    validate_context(ctx)
    identity = reader.authenticate()
    if ctx.on_behalf_of is not None and ctx.on_behalf_of != identity.principal:
        raise BookflowError('E_UNAUTHENTICATED')
    require_command_activation(reader.session, cmd)
    company = resolve_company(reader, audience, selector, source)
    fields = set(raw) & CONTEXT_FIELD_NAMES
    if fields:
        raise BookflowError('E_CONTEXT_IN_INPUT', details={'fields': sorted(fields)})
    return DepositRequest.capture(cmd.name, company, validate_input(cmd, raw))
