"""Translate public setup into the existing versioned administration owner."""
import json
from bookflow.core.identity_admin_binding import session_operation
from bookflow.core.session import now_iso
from . import identity_admin as b, permission_runtime as runtime, permission_catalog as c
from .permission_admin_audit import AuditContext
from .permission_access import translate


def audit_context(ctx, command=None):
    return AuditContext(now_iso(),ctx.interface.value,ctx.client_name,ctx.client_version,
        ctx.client_host,ctx.session_id,ctx.request_id,ctx.reason,ctx.idempotency_key,
        ctx.directive_id,source_ref=ctx.source_ref,command=command)


def edit(session, ctx, intent, *, preview=False, catalog=None):
    try:
        with session_operation(session,ctx,purpose='preview' if preview else 'apply') as operation:
            binding = operation._binding('preview' if preview else 'apply')
            bundle = catalog or runtime.catalog_for_root(session.hub)
            if preview:
                return b.preview_edit(session.hub,binding=binding,intent=intent,catalog=bundle,
                    visibility=runtime.VISIBILITY,request_id=ctx.request_id)
            return b.apply_edit(session.hub,binding=binding,intent=intent,catalog=bundle,
                visibility=runtime.VISIBILITY,audit=audit_context(ctx,getattr(session,'_permission_command',None))).private
    except b.AdministrationError as exc:
        translate(exc)


def membership_intent(user, scope, existing, role, grants=None, denies=None, expected_version=None):
    # Zero is the public explicit-absence precondition. Omission preserves the
    # old role-only API; the GUI always supplies the observed version or zero.
    version = (existing['version'] if existing else 0) if expected_version is None else expected_version
    expected = b.Absent() if version == 0 else b.Version(version)
    def values(value, field):
        return tuple(json.loads(existing.get(field) or '[]')) if value is None and existing else tuple(value or ())
    return b.PutMembership(user['id'],c.ScopeKey(scope.scope_type,scope.scope_id),expected,role,
        values(grants,'grants'),values(denies,'denies'))
