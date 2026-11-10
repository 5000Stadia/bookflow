"""Local-only reservation admission; never resolves or identifies a counterpart."""
import sqlalchemy as sa
from bookflow.core.errors import BookflowError
from bookflow.storage.migrate import FeatureRevision, feature_admission


def require_unreserved(db, *identifiers):
    from bookflow.company import schema as c
    from types import SimpleNamespace
    def check():
        t = c.intercompany_mappings
        if db.conn.execute(sa.select(t.c.id).where(sa.or_(t.c.due_from_id.in_(identifiers),
                t.c.due_to_id.in_(identifiers))).limit(1)).first():
            raise BookflowError('E_SYSTEM_RECORD', message='Account is reserved for an owned workflow.')
    view = SimpleNamespace(raw=db.conn.connection.driver_connection)
    resolver = feature_admission(view, FeatureRevision('company', 'co0135'), resolver=check)
    if resolver is not None:
        resolver()
