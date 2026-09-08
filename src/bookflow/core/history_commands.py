"""Public history command preparation under the actual authenticated reader."""
import sqlalchemy as sa
from pydantic import ValidationError
from bookflow.core.errors import BookflowError
from bookflow.core.history_inputs import INPUTS, command_request
from bookflow.hub.audit_projection import make_audience
from bookflow.hub import schema as h

COMMANDS = {
    **{f"{prefix}audit {mode}": mode for prefix in ("", "hub ") for mode in ("list", "show", "tail")},
    "activity": "activity",
}


def resolve_company(reader, selector, source, *, capability="audit"):
    from bookflow.core.dispatch import _resolve_visible_company
    if selector is None:
        raise BookflowError("E_COMPANY_NOT_FOUND", message="No company selected; give --company, set BOOKFLOW_COMPANY, or run `bookflow company use <company>`.", details={"source": "none"})
    audience = make_audience(reader, read_capability=capability)
    visible = [scope.id for scope in audience.comparison.scopes
               if scope.kind == "company" and audience.visible(scope)]
    row = _resolve_visible_company(reader.session, selector, source,
                                   h.companies.c.id.in_(visible) if visible else sa.false())
    audience.require(row["id"], ((capability, "member"),))
    audience.validate()
    return row["id"]


def prepare(reader, cmd, raw, ctx, selector, source, dry_run=False):
    from bookflow.core.context import CONTEXT_FIELD_NAMES
    from bookflow.core.context_options import normalize_options
    from bookflow.core.dispatch import validate_context, _validation_error
    from bookflow.hub.access import require_command_activation
    mode = COMMANDS.get(cmd.name)
    if mode is None or cmd.is_write:
        raise BookflowError("E_VALIDATION")
    normalize_options(cmd, company=selector, reason=ctx.reason,
                      source_ref=ctx.source_ref, directive=ctx.directive_id,
                      idempotency_key=ctx.idempotency_key, dry_run=dry_run)
    validate_context(ctx)
    identity = reader.authenticate()
    if ctx.on_behalf_of is not None and ctx.on_behalf_of != identity.principal:
        raise BookflowError("E_UNAUTHENTICATED")
    require_command_activation(reader.session, cmd)
    company = None
    if cmd.scope == "company":
        company = resolve_company(reader, selector, source,
                                  capability="activity" if mode == "activity" else "audit")
    fields = set(raw) & CONTEXT_FIELD_NAMES
    if fields:
        raise BookflowError("E_CONTEXT_IN_INPUT", details={"fields": sorted(fields)})
    try:
        validated = INPUTS[mode].model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(exc) from None
    return command_request(mode, validated, company=company)
