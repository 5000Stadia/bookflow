"""The text of an attachment a command was told to read, for commands that take a file by id."""
from __future__ import annotations

import sqlalchemy as sa

from bookflow.company import schema as c
from bookflow.core.errors import BookflowError


def attachment_text(s, selector: str, field: str) -> tuple[dict, str]:
    """The attachment's row and its body as text (UTF-8, else Windows-1252), verified against its digest.

    `field` names the input the id came from, so a missing attachment says where to look.
    """
    from bookflow.company import attachment_store as store
    from bookflow.company.cutover_sources import decode
    row = s.company.conn.execute(sa.select(c.attachments).where(c.attachments.c.id == selector)).mappings().first()
    if row is None:
        raise BookflowError("E_RECORD_NOT_FOUND", details={"record_type": "attachment", "selector": selector, "field": field})
    if row["collected_at"] is not None:
        raise BookflowError("E_IO", "Attachment body has been collected.", {"check": "collected_body", "attachment": selector})
    if s.company.conn.execute(sa.select(c.attachment_collection.c.id).limit(1)).first():
        raise BookflowError("E_DB_BUSY", "Attachment collection requires recovery.")
    folder = s.company.path.parent / "attachments"
    with store.open_verified(folder, store.BodyInfo(row["sha256"], row["size_bytes"])) as body:
        return dict(row), decode(body.read())
