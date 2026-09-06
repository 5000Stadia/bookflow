"""Presence-sensitive adapter context options; no bookkeeping or actor authority."""

from bookflow.core.errors import BookflowError


def normalize_options(cmd, *, company=None, reason=None, source_ref=None,
                      directive=None, idempotency_key=None, dry_run=False):
    if type(dry_run) is not bool:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": "dry_run", "problem": "boolean required"}]})
    applicable = {
        "company": cmd.scope == "company", "reason": cmd.is_write,
        "source_ref": cmd.is_write, "directive": cmd.is_write and cmd.scope == "company",
        "idempotency_key": cmd.accepts_idempotency_key,
    }
    values = dict(company=company, reason=reason, source_ref=source_ref,
                  directive=directive, idempotency_key=idempotency_key)
    for name, value in values.items():
        if value is not None and not isinstance(value, str):
            raise BookflowError("E_VALIDATION", details={"fields": [{"field": name, "problem": "string or null required"}]})
        if value is not None and not applicable[name]:
            raise BookflowError("E_USAGE", details={"command": cmd.name, "argument": name})
    if dry_run and not cmd.is_write:
        raise BookflowError("E_USAGE", details={"command": cmd.name, "argument": "dry_run"})
    return {**values, "dry_run": dry_run}
