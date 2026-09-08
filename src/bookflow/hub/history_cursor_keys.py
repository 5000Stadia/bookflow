"""Read-only access to the hub-owned history continuation key."""
from bookflow.core.errors import BookflowError


def read(db):
    row = db.raw.execute('SELECT key_material FROM main.history_cursor_keys WHERE key_id=1').fetchone()
    if row is None or type(row[0]) is not bytes or len(row[0]) != 32:
        raise BookflowError('E_INTERNAL', message='History continuation key is unavailable')
    return row[0]
