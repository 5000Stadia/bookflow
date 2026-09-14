"""Public policy admission through the existing authenticated transaction producer."""
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from bookflow.core.identity_admin_binding import session_operation
from . import identity_admin as admin, permission_catalog as catalog


def activated(session):
    if session is None or session.hub is None:
        return False
    if not session.hub.raw.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='permission_state'").fetchone():
        return False
    row = session.hub.raw.execute('SELECT mode FROM main.permission_state WHERE id=1').fetchone()
    return row == ('policy_v1',)


def translate(error):
    category, field = error.args
    if category == 'conflict':
        raise BookflowError('E_VERSION_CONFLICT', details={'field':field}) from None
    if category in ('not_administrator', 'unavailable_target', 'protected_identity'):
        raise BookflowError('E_PERMISSION', details={'reason':category,'field':field}) from None
    raise BookflowError('E_VALIDATION', details={'fields':[{'field':field,'problem':category}]}) from None


def require(session, capability, required_role):
    ctx = getattr(session, '_permission_context', None)
    if ctx is None:
        ctx = Context.new(Interface.python, 'permission reader')
    try:
        with session_operation(session, ctx, purpose='preview') as operation:
            return operation.require_company(session.company_row['id'],
                catalog.Requirement(capability, required_role))
    except admin.AdministrationError as exc:
        translate(exc)
