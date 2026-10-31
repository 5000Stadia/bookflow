"""Typed attachment metadata commands; binary resources are invocation-owned."""
from __future__ import annotations

import base64
import re
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from bookflow.commands.note_cmds import NoteTarget, _Cursor
from bookflow.core.context import Context
from bookflow.core.lazy import LazyModule
from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, Touched, TransferDescriptor, command

sa = LazyModule("sqlalchemy")
c = LazyModule("bookflow.company.schema")
records = LazyModule("bookflow.company.records")
store_api = LazyModule("bookflow.company.attachment_store")


class Caption(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    caption: str = Field("", max_length=2048, description="Association caption, at most 2048 UTF-8 bytes.")

    @field_validator("caption")
    @classmethod
    def caption_bytes(cls, value):
        if len(value.encode("utf-8")) > 2048:
            raise ValueError("caption exceeds 2048 UTF-8 bytes")
        return value


class AttachmentAddInput(NoteTarget, Caption):
    original_filename: str = Field(min_length=1, max_length=255, description="Basename only, at most 255 UTF-8 bytes.")
    media_type: str = Field("application/octet-stream", max_length=127, description="ASCII type/subtype media type.")

    @field_validator("original_filename")
    @classmethod
    def filename(cls, value):
        if value in (".", "..") or any(ch in "/\\" or unicodedata.category(ch) == "Cc" for ch in value) or len(value.encode("utf-8")) > 255:
            raise ValueError("filename must be a basename without controls, at most 255 UTF-8 bytes")
        return value

    @field_validator("media_type")
    @classmethod
    def media(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+/[A-Za-z0-9!#$%&'*+.^_`|~-]+", value):
            raise ValueError("media type must contain ASCII type/subtype tokens")
        return value


class AttachmentSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    attachment: str = Field(min_length=1, max_length=26, description="Stable attachment id.")


class AttachmentLinkInput(AttachmentSelector, NoteTarget, Caption):
    pass


class AttachmentUnlinkInput(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)
    link: str = Field(min_length=1, max_length=26, description="Stable link occurrence id.")
    expected_version: int = Field(strict=True, ge=1, description="Current link version; checked even for inactive links.")


class AttachmentListInput(NoteTarget):
    limit: int = Field(50, strict=True, ge=1, le=200)
    cursor: str | None = Field(None, max_length=2048)


class CommonOut(BaseModel):
    id: str
    version: int
    created_at: str
    created_by: str
    created_via: str
    updated_at: str
    updated_by: str
    updated_via: str


class AttachmentOut(CommonOut):
    sha256: str
    size_bytes: int
    original_filename: str
    media_type: str
    uploaded_by: str
    uploaded_at: str
    collected_at: str | None


class AttachmentLinkOut(CommonOut):
    attachment_id: str
    record_type: str
    record_id: str
    linked_by: str
    linked_by_name: str | None = None
    linked_at: str
    caption: str
    active: bool
    attachment: AttachmentOut


class AttachmentWriteOutput(WriteOutput):
    attachment: AttachmentOut
    link: AttachmentLinkOut
    idempotent_replay: bool = False


class AttachmentPage(BaseModel):
    items: list[AttachmentLinkOut]
    count: int
    has_more: bool
    next_cursor: str | None


def _out(s, row, model):
    from bookflow.core.session import localize
    return model(**{k: localize(s, v) if k in ("created_at", "updated_at", "uploaded_at", "linked_at", "collected_at") else v for k, v in row.items()})


def _link_names(s, links):
    from bookflow.company.info import principal_names
    names = principal_names(s.company, {link["linked_by"] for link in links})
    names.setdefault(s.actor.id, s.actor.display_name)
    return names


def _link_out(s, link, attachment, names=None):
    names = _link_names(s, [link]) if names is None else names
    return _out(s, dict(link, linked_by_name=names.get(link["linked_by"]),
                       attachment=_out(s, attachment, AttachmentOut)), AttachmentLinkOut)


# An import's file (R107): linked to the company under its own record type, captioned with the import kind, so it is
# never one of the company's ordinary attachments. Reading it is reading that kind's records (a vendor file is the
# vendors), so `attachment get`, `text` and `list` show it only to a caller who may read the kind. A newer file of the
# same kind from the same person replaces one whose import never ran, and any such file goes after IMPORT_FILE_DAYS;
# the file of a run that landed records stays, since a re-run of its source reads it.
IMPORT_FILE = "import_file"
IMPORT_FILE_DAYS = 7


def _resolve(s, record_type, record_id):
    return records.resolve(s, "company_info" if record_type == IMPORT_FILE else record_type, record_id)


def _import_kind(s, caption):
    """The import kind an import file's caption names, if the caller may read its records."""
    from bookflow.company import batches, import_kinds
    from bookflow.core.errors import BookflowError
    try:
        kind = import_kinds.kind(caption)
    except BookflowError:
        raise BookflowError("E_VALIDATION", "An import file's caption names its import kind (customer, vendor...).",
                            {"fields": [{"field": "caption", "problem": "not an import kind"}]}) from None
    batches.require_read(s, f"{kind.noun} show")
    return kind.name


def _readable_import_kinds(s):
    from bookflow.company import import_kinds
    from bookflow.core.errors import BookflowError
    out = set()
    for name in import_kinds.KINDS:
        try:
            out.add(_import_kind(s, name))
        except BookflowError:
            pass
    return out


def _guard_import_file(s, attachment_id):
    """An attachment that is only ever an import's file is read as its kind's records."""
    from bookflow.core.errors import BookflowError
    links = s.company.conn.execute(sa.select(c.attachment_links.c.record_type, c.attachment_links.c.caption).where(
        c.attachment_links.c.attachment_id == attachment_id)).all()
    if not links or any(kind != IMPORT_FILE for kind, _ in links):
        return
    refused = None
    for caption in sorted({caption for _, caption in links}):
        try:
            _import_kind(s, caption)
            return
        except BookflowError as error:
            refused = error
    raise refused


def _stale_import_files(s, keep_attachment, caption):
    """Import files to unlink as a new one is added: this person's earlier files of the same kind whose import never
    ran, and anyone's older than IMPORT_FILE_DAYS, except a file a run landed records from (or is running)."""
    from datetime import timedelta
    from bookflow.core.clock import now
    links = c.attachment_links
    cutoff = (now() - timedelta(days=IMPORT_FILE_DAYS)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    used = sa.exists(sa.select(c.import_runs.c.id).where(
        c.import_runs.c.attachment_id == links.c.attachment_id,
        sa.or_(c.import_runs.c.batch_id.is_not(None), c.import_runs.c.state.in_(("running", "stopped")))))
    return [dict(r) for r in s.company.conn.execute(sa.select(links).where(
        links.c.record_type == IMPORT_FILE, links.c.active.is_(True), links.c.attachment_id != keep_attachment,
        sa.or_(sa.and_(links.c.linked_by == s.actor.id, links.c.caption == caption), links.c.linked_at < cutoff),
        ~used)).mappings()]


def _get(s, kind, key):
    table = c.attachments if kind == "attachment" else c.attachment_links
    key = records.resolve(s, kind, key)
    return dict(s.company.conn.execute(sa.select(table).where(table.c.id == key)).mappings().one())


def _pending(s):
    from bookflow.core.errors import BookflowError
    if s.company.conn.execute(sa.select(c.attachment_collection.c.id).limit(1)).first():
        raise BookflowError("E_DB_BUSY", "Attachment collection requires recovery.")


def _store_limit(s):
    from bookflow.core.errors import BookflowError
    limit = s.company.conn.execute(sa.select(c.company_info.c.attachment_max_bytes)).scalar_one()
    if type(limit) is not int or not 1 <= limit <= 100000000:
        raise BookflowError("E_VALUE_RANGE", "Invalid company attachment byte limit.")
    return s.company.path.parent / "attachments", limit


def _available(s, row, *, verify=False):
    from bookflow.core.errors import BookflowError
    if row["collected_at"] is not None:
        raise BookflowError("E_IO", "Attachment body has been collected.", {"check": "collected_body"})
    if verify:
        with store_api.open_verified(_store_limit(s)[0], store_api.BodyInfo(row["sha256"], row["size_bytes"])):
            pass


def prepare_add(inp: AttachmentAddInput, ctx: Context, s):
    from bookflow.core.transfers import TransferPreparation
    _pending(s)
    _resolve(s, inp.record_type, inp.record_id)
    if inp.record_type == IMPORT_FILE:
        _import_kind(s, inp.caption)
    store, limit = _store_limit(s)
    return TransferPreparation(store, limit)


def prepare_get(inp: AttachmentSelector, ctx: Context, s):
    from bookflow.core.transfers import TransferPreparation
    _pending(s)
    row = _get(s, "attachment", inp.attachment)
    _guard_import_file(s, row["id"])
    _available(s, row)
    store, limit = _store_limit(s)
    return TransferPreparation(store, limit, store_api.BodyInfo(row["sha256"], row["size_bytes"]),
        {"original_filename": row["original_filename"], "media_type": row["media_type"]})


def _resource(s, preparation):
    from bookflow.core.errors import BookflowError
    resource = getattr(s, "transfer", None)
    if resource is None or resource.store != preparation.store or resource.info is None:
        raise BookflowError("E_VALIDATION", "A validated attachment transfer is required.")
    if preparation.info is None and resource.info.size_bytes > preparation.limit:
        raise BookflowError("E_VALUE_RANGE", "Attachment exceeds its byte limit.")
    if preparation.info is not None and resource.info != preparation.info:
        raise BookflowError("E_IO", "Attachment transfer metadata changed.")
    return resource


def _association(s, attachment, kind, key, caption, ctx):
    from bookflow.core.ids import new_id
    from bookflow.hub.users import common
    from bookflow.core.clock import now_iso
    row = s.company.conn.execute(sa.select(c.attachment_links).where(c.attachment_links.c.attachment_id == attachment["id"],
        c.attachment_links.c.record_type == kind, c.attachment_links.c.record_id == key, c.attachment_links.c.active.is_(True))).mappings().first()
    if row:
        return dict(row), False
    return dict(id=new_id(), **common(s.actor.id, ctx.interface.value), attachment_id=attachment["id"],
        record_type=kind, record_id=key, linked_by=s.actor.id, linked_at=now_iso(), caption=caption, active=True), True


def _write_preview(s, attachment, link):
    return AttachmentWriteOutput(attachment=_out(s, attachment, AttachmentOut), link=_link_out(s, link, attachment))


attachment_add = command("attachment add", scope="company", description="Upload verified bytes and link them to a company record.",
    input_model=AttachmentAddInput, output_model=AttachmentWriteOutput, writes={"company"}, required_role="standard",
    accepts_idempotency_key=True, positional=["record_type", "record_id"], transfer=TransferDescriptor("input", prepare_add), error_codes=["E_RECORD_NOT_FOUND", "E_IO", "E_VALUE_RANGE", "E_DB_BUSY"])


@attachment_add
def plan_add(inp: AttachmentAddInput, ctx: Context, s) -> Plan:
    from bookflow.core.ids import new_id
    from bookflow.hub.users import common
    from bookflow.core.clock import now_iso
    resource = _resource(s, prepare_add(inp, ctx, s))
    key = _resolve(s, inp.record_type, inp.record_id)
    found = s.company.conn.execute(sa.select(c.attachments).where(c.attachments.c.sha256 == resource.info.sha256)).mappings().first()
    attachment = dict(found) if found else dict(id=new_id(), **common(s.actor.id, ctx.interface.value),
        sha256=resource.info.sha256, size_bytes=resource.info.size_bytes, original_filename=inp.original_filename,
        media_type=inp.media_type, uploaded_by=s.actor.id, uploaded_at=now_iso(), collected_at=None)
    if attachment["size_bytes"] != resource.info.size_bytes:
        from bookflow.core.errors import BookflowError
        raise BookflowError("E_IO", "Attachment size differs from stored metadata.")
    restore = attachment["collected_at"] is not None
    if restore:
        attachment.update(collected_at=None, version=attachment["version"]+1, updated_at=now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    link, new_link = _association(s, attachment, inp.record_type, key, inp.caption, ctx)
    stale = []
    if inp.record_type == IMPORT_FILE:
        for row in _stale_import_files(s, attachment["id"], inp.caption):
            row.update(active=False, version=row["version"]+1, updated_at=now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
            stale.append(row)
    return Plan(_write_preview(s, attachment, link), dict(attachment=attachment, link=link, new_attachment=not found, restore=restore, new_link=new_link, stale=stale))


@attachment_add.applier
def apply_add(plan: Plan, ctx: Context, s) -> Applied:
    store_api.publish(s.transfer.store, s.transfer.staged)
    a = plan.data["attachment"]
    touched = []
    if plan.data["new_attachment"]:
        s.company.conn.execute(c.attachments.insert().values(**a))
        touched.append(Touched("attachment", a["id"], "create", None, 1, a, db="company"))
    elif plan.data["restore"]:
        s.company.conn.execute(c.attachments.update().where(c.attachments.c.id == a["id"]).values(**a))
        touched.append(Touched("attachment", a["id"], "update", a["version"]-1, a["version"], a, db="company"))
    for row in plan.data.get("stale", ()):  # an import file it replaces, or one past its days: unlinked, bytes kept until collected
        s.company.conn.execute(c.attachment_links.update().where(c.attachment_links.c.id == row["id"], c.attachment_links.c.active.is_(True)).values(**row))
        touched.append(Touched("attachment_link", row["id"], "update", row["version"]-1, row["version"], row, db="company"))
    return _apply_link(plan, s, touched)


def _apply_link(plan, s, touched):
    row = plan.data["link"]
    if plan.data["new_link"]:
        s.company.conn.execute(c.attachment_links.insert().values(**row))
        touched.append(Touched("attachment_link", row["id"], "create", None, 1, row, db="company"))
    return Applied(plan.preview, touched, "linked attachment" if touched else "no change")


attachment_link = command("attachment link", scope="company", description="Link an available attachment to another record.",
    input_model=AttachmentLinkInput, output_model=AttachmentWriteOutput, writes={"company"}, required_role="standard",
    accepts_idempotency_key=True, positional=["attachment", "record_type", "record_id"], error_codes=["E_RECORD_NOT_FOUND", "E_IO", "E_DB_BUSY"])


@attachment_link
def plan_link(inp: AttachmentLinkInput, ctx: Context, s) -> Plan:
    if inp.record_type == IMPORT_FILE:
        from bookflow.core.errors import BookflowError
        raise BookflowError("E_VALIDATION", "An import file is added by the import itself (`attachment add import_file`), never linked.",
                            {"fields": [{"field": "record_type", "problem": "an import file is added, not linked"}]})
    _pending(s)
    key = records.resolve(s, inp.record_type, inp.record_id)
    a = _get(s, "attachment", inp.attachment)
    _available(s, a, verify=True)
    link, new_link = _association(s, a, inp.record_type, key, inp.caption, ctx)
    return Plan(_write_preview(s, a, link), dict(link=link, new_link=new_link))


@attachment_link.applier
def apply_link(plan: Plan, ctx: Context, s) -> Applied:
    return _apply_link(plan, s, [])


attachment_unlink = command("attachment unlink", scope="company", description="Unlink an occurrence with its expected version, retaining history and bytes.",
    input_model=AttachmentUnlinkInput, output_model=AttachmentWriteOutput, writes={"company"}, required_role="standard",
    positional=["link"], error_codes=["E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_DB_BUSY", "E_VALIDATION"])


@attachment_unlink
def plan_unlink(inp: AttachmentUnlinkInput, ctx: Context, s) -> Plan:
    from bookflow.core.audit import decode_snapshot
    from bookflow.core.versioning import check_update, current_writer, history_from_entries
    from bookflow.core.clock import now_iso
    _pending(s)
    row = _get(s, "attachment_link", inp.link)
    writer = current_writer(s.company, "attachment_link", row["id"], row)
    if writer:
        from bookflow.company.info import principal_names
        names = principal_names(s.company, {v for v in (writer.updated_by, writer.on_behalf_of) if v})
        writer.updated_by_name = names.get(writer.updated_by)
        writer.on_behalf_of_name = names.get(writer.on_behalf_of)
    check_update(current_version=row["version"], current_updated_at=row["updated_at"], current_writer=writer,
        changes={"active"}, expected_version=inp.expected_version,
        history_since=lambda v: history_from_entries(s.company, "attachment_link", row["id"], v, decode_snapshot), actor_id=s.actor.id, window_seconds=0)
    changed = row["active"]
    if changed and row["record_type"] == "item":  # R114: a chosen item picture leaves through `item picture clear`
        from bookflow.company.item_pictures import referring_item
        owner = referring_item(s.company, row["attachment_id"], row["record_id"])
        if owner:
            from bookflow.core.errors import BookflowError
            raise BookflowError("E_VALIDATION", f"This file is the picture of item {owner['full_name']}; remove it "
                                "with `item picture clear` (Remove on the item page), which unlinks it too.",
                                {"fields": [{"field": "link", "problem": "the item's chosen picture"}]})
    if changed:
        row.update(active=False, version=row["version"]+1, updated_at=now_iso(), updated_by=s.actor.id, updated_via=ctx.interface.value)
    return Plan(_write_preview(s, _get(s, "attachment", row["attachment_id"]), row), dict(link=row, changed=changed))


@attachment_unlink.applier
def apply_unlink(plan: Plan, ctx: Context, s) -> Applied:
    row = plan.data["link"]
    if not plan.data["changed"]:
        return Applied(plan.preview, [], "no change")
    s.company.conn.execute(c.attachment_links.update().where(c.attachment_links.c.id == row["id"]).values(**row))
    return Applied(plan.preview, [Touched("attachment_link", row["id"], "update", row["version"]-1, row["version"], row, db="company")], "unlinked attachment")


attachment_get = command("attachment get", scope="company", description="Download verified attachment bytes with original metadata.",
    input_model=AttachmentSelector, output_model=AttachmentOut, required_role="member", positional=["attachment"],
    transfer=TransferDescriptor("output", prepare_get), error_codes=["E_RECORD_NOT_FOUND", "E_IO", "E_DB_BUSY", "E_VALUE_RANGE"])


@attachment_get
def plan_get(inp: AttachmentSelector, ctx: Context, s) -> Plan:
    _resource(s, prepare_get(inp, ctx, s))
    return Plan(_out(s, _get(s, "attachment", inp.attachment), AttachmentOut))


attachment_list = command("attachment list", scope="company", description="Page active file associations for a record, newest occurrence first.",
    input_model=AttachmentListInput, output_model=AttachmentPage, required_role="member", positional=["record_type", "record_id"], error_codes=["E_RECORD_NOT_FOUND"])


@attachment_list
def plan_list(inp: AttachmentListInput, ctx: Context, s) -> Plan:
    from bookflow.company.query import _invalid_cursor, permission_fingerprint
    from bookflow.core.ids import is_ulid
    key = _resolve(s, inp.record_type, inp.record_id)
    scope = dict(company=s.company_row["id"], record_type=inp.record_type, record_id=key, permissions=permission_fingerprint(s, ctx.on_behalf_of))
    query = sa.select(c.attachment_links).where(c.attachment_links.c.record_type == inp.record_type, c.attachment_links.c.record_id == key, c.attachment_links.c.active.is_(True))
    if inp.record_type == IMPORT_FILE:  # only the files of kinds the caller may read
        query = query.where(c.attachment_links.c.caption.in_(sorted(_readable_import_kinds(s))))
    if inp.cursor:
        try:
            encoded = inp.cursor.encode("ascii")
            previous = _Cursor.model_validate_json(base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
        except (ValueError, UnicodeError, ValidationError):
            raise _invalid_cursor() from None
        if previous.model_dump(exclude={"v", "before"}) != scope or not is_ulid(previous.before):
            raise _invalid_cursor()
        query = query.where(c.attachment_links.c.id < previous.before)
    rows = [dict(r) for r in s.company.conn.execute(query.order_by(c.attachment_links.c.id.desc()).limit(inp.limit+1)).mappings()]
    more = len(rows) > inp.limit
    rows = rows[:inp.limit]
    attachments = {r["id"]: dict(r) for r in s.company.conn.execute(sa.select(c.attachments).where(c.attachments.c.id.in_({r["attachment_id"] for r in rows}))).mappings()} if rows else {}
    cursor = base64.urlsafe_b64encode(_Cursor(**scope, before=rows[-1]["id"]).model_dump_json().encode()).decode().rstrip("=") if more else None
    names = _link_names(s, rows)
    return Plan(AttachmentPage(items=[_link_out(s, r, attachments[r["attachment_id"]], names) for r in rows], count=len(rows), has_more=more, next_cursor=cursor))


# What `attachment text` reads whole: a statement's summary box, a letter, an export. Anything larger
# is downloaded with `attachment get`; nothing is cut short.
TEXT_LIMIT = 65536
# Keep bank-download format suffixes literal so attachment text detection stays compatible.
TEXT_EXTENSIONS = frozenset(('.txt', '.text', '.csv', '.tsv', '.ofx', '.qfx', '.qbo', '.iif', '.json', '.xml', '.md', '.htm', '.html'))
TEXT_MEDIA = frozenset(('application/json', 'application/xml', 'application/csv', 'application/x-ofx', 'application/vnd.intu.qfx',
                        'application/x-qfx', 'application/x-iif'))


class AttachmentTextOut(AttachmentOut):
    text: str = Field(description='The whole file as text.')
    encoding: str = Field(description='utf-8 (a byte order mark dropped), else windows-1252, as the desktop product writes.')
    line_count: int


def _textual(row):
    import os
    media = row["media_type"].lower()
    return (media.startswith("text/") or media in TEXT_MEDIA
            or os.path.splitext(row["original_filename"].lower())[1] in TEXT_EXTENSIONS)


attachment_text = command("attachment text", scope="company",
    description=("Read a small text attachment (a statement's summary box, a letter, a CSV or OFX file; at most 64 KiB) "
                 "as text in the result, with its original metadata. Over MCP this is how a text file already "
                 "attached is read; `attachment get` downloads any file's bytes."),
    input_model=AttachmentSelector, output_model=AttachmentTextOut, required_role="member", positional=["attachment"],
    error_codes=["E_RECORD_NOT_FOUND", "E_IO", "E_DB_BUSY", "E_VALUE_RANGE", "E_VALIDATION"])


@attachment_text
def plan_text(inp: AttachmentSelector, ctx: Context, s) -> Plan:
    from bookflow.core.errors import BookflowError
    _pending(s)
    row = _get(s, "attachment", inp.attachment)
    _guard_import_file(s, row["id"])
    _available(s, row)
    if not _textual(row):
        raise BookflowError("E_VALIDATION", f"{row['original_filename']} ({row['media_type']}) is not a text file; download it with `attachment get`.",
                            {"fields": [{"field": "attachment", "problem": "not a text file"}]})
    if row["size_bytes"] > TEXT_LIMIT:
        raise BookflowError("E_VALUE_RANGE", f"{row['original_filename']} is {row['size_bytes']} bytes, more than the {TEXT_LIMIT} `attachment text` reads; download it with `attachment get`.",
                            {"field": "attachment", "size_bytes": row["size_bytes"], "limit": TEXT_LIMIT})
    with store_api.open_verified(_store_limit(s)[0], store_api.BodyInfo(row["sha256"], row["size_bytes"])) as body:
        raw = body.read(TEXT_LIMIT + 1)
    if b"\x00" in raw:
        raise BookflowError("E_VALIDATION", f"{row['original_filename']} holds binary data, not text; download it with `attachment get`.",
                            {"fields": [{"field": "attachment", "problem": "not a text file"}]})
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text, encoding = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = raw.decode("cp1252", errors="replace"), "windows-1252"
    out = _out(s, row, AttachmentOut)
    return Plan(AttachmentTextOut(**out.model_dump(), text=text, encoding=encoding, line_count=len(text.splitlines())))
