"""`item picture set`, `use` and `clear` (R114 increment 1, design/point-of-sale.md §6).

The picture is an ordinary attachment linked to the item, so it is in the item's files, keeps its history and is
copied with the company. Setting one uploads (as `attachment add` does) or names an attachment, links it to the item
when it is not yet, and records it on the item as an item update, with the item's version and audit entry. Only a
real JPEG, PNG or WebP is a picture: the bytes decide, never the name or the declared type.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookflow.commands import attachment_cmds as files
from bookflow.core.context import Context
from bookflow.core.lazy import LazyModule
from bookflow.core.models import WriteOutput
from bookflow.core.registry import Applied, Plan, Touched, TransferDescriptor, command

sa = LazyModule("sqlalchemy")
c = LazyModule("bookflow.company.schema")
pics = LazyModule("bookflow.company.item_pictures")
items = LazyModule("bookflow.company.items")
store_api = LazyModule("bookflow.company.attachment_store")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", defer_build=True)


class _Target(_Strict):
    item: str = Field(min_length=1, max_length=400, description="Item id or full name.")
    expected_version: int | None = Field(None, strict=True, ge=1, description="The item's current version.")


class _Thumbnail(_Strict):
    thumbnail_attachment: str | None = Field(None, min_length=1, max_length=26, description=(
        "A small copy of the picture already uploaded (at most 1,000,000 bytes), linked to the item if it is not."))


class PictureSetInput(_Target, _Thumbnail):
    original_filename: str = Field(min_length=1, max_length=255, description="Basename only.")
    media_type: str = Field("application/octet-stream", max_length=127,
                            description="Ignored for the stored type: the bytes decide JPEG, PNG or WebP.")
    caption: str = Field("Item picture", max_length=2048, description="Caption on the item's link to the file.")

    @field_validator("original_filename")
    @classmethod
    def filename(cls, value):
        import unicodedata
        if value in (".", "..") or any(ch in "/\\" or unicodedata.category(ch) == "Cc" for ch in value) \
                or len(value.encode("utf-8")) > 255:
            raise ValueError("filename must be a basename without controls, at most 255 UTF-8 bytes")
        return value


class PictureUseInput(_Target, _Thumbnail):
    attachment: str = Field(min_length=1, max_length=26, description="An image attachment; linked to the item if it is not.")


class PictureClearInput(_Target):
    keep_file: bool = Field(False, description=(
        "Keep the picture linked to the item as an ordinary file. Then, as the newest image, it is still shown."))


class PictureWriteOutput(WriteOutput):
    item: str
    full_name: str
    version: int
    picture: dict | None = Field(description="What the item now shows (see `item show`).")
    thumbnail: dict | None
    linked: list[str] = Field(default_factory=list, description="Attachment ids newly linked to the item.")
    unlinked: list[str] = Field(default_factory=list, description="Attachment ids unlinked from the item.")


def _error(message, field, problem, code="E_VALIDATION"):
    from bookflow.core.errors import BookflowError
    return BookflowError(code, message, {"fields": [{"field": field, "problem": problem}]})


def _head(s, row, limit, field):
    """Check a stored attachment is a usable picture, from its verified bytes."""
    files._available(s, row)
    with store_api.open_verified(files._store_limit(s)[0], store_api.BodyInfo(row["sha256"], row["size_bytes"])) as body:
        head = body.read(64)
    return pics.require_image(head, row["size_bytes"], field=field, limit=limit)


def _linked(s, ctx, item_id, attachment, caption):
    """The item's active link to the attachment, planned when there is none."""
    link, new = files._association(s, attachment, pics.RECORD_TYPE, item_id, caption, ctx)
    return link, new


def _thumbnail(s, ctx, item_id, key):
    if key is None:
        return None, None
    row = files._get(s, "attachment", key)
    _head(s, row, pics.THUMBNAIL_MAX_BYTES, "thumbnail_attachment")
    return row, _linked(s, ctx, item_id, row, "Item picture thumbnail")


def _current(s, selector):
    return items.resolve_item(s.company, selector)


def _mutation(s, ctx, current, values, expected_version):
    """The item update that records the picture: its own version and audit entry."""
    from bookflow.commands.item_cmds import _version_meta
    from bookflow.company import custom_fields, list_service
    from bookflow.core.clock import now_iso
    from bookflow.company.list_merges import refuse_while_merged
    refuse_while_merged(s.company, "item", current["id"], "edit")
    changed = {k for k, v in values.items() if current.get(k) != v}
    _version_meta(s, current, {"picture"} if changed else set(), expected_version)
    if not changed:
        return None
    members, vendors = items._members(s.company, current["id"]), items._vendors(s.company, current["id"])
    custom = tuple(custom_fields.read_owner_values(s.company, record_type=items.ITEM_RECORD_TYPE, record_id=current["id"]))
    after = {**current, **values, **list_service.update_metadata(current, s.actor.id, ctx.interface.value, at=now_iso())}
    return items.ItemMutation("update", current, after, members, members, vendors, vendors, custom, custom, ("picture",))


def _preview(s, owner, linked=(), unlinked=(), extra=None):
    """What the item will show: the chosen picture, else its newest linked image (counting links this plan adds)."""
    picture, thumbnail = pics.pictures(s.company, [owner]).get(owner["id"], (None, None))
    company = s.company_row["id"]
    if owner.get("picture_attachment_id"):
        rows = {r["id"]: r for r in s.company.conn.execute(sa.select(c.attachments).where(c.attachments.c.id.in_(
            [owner["picture_attachment_id"], owner.get("picture_thumbnail_attachment_id") or ""]))).mappings()}
        rows.update(extra or {})
        picture = pics._out(rows[owner["picture_attachment_id"]], True)
        thumb = owner.get("picture_thumbnail_attachment_id")
        thumbnail = pics._out(rows[thumb], True) if thumb in rows else None
    elif picture and picture["attachment_id"] in unlinked:
        picture = None  # a removed fallback: the next one shows once it is gone
    return PictureWriteOutput(item=owner["id"], full_name=owner["full_name"], version=owner["version"],
                              picture=pics.absolute(company, picture), thumbnail=pics.absolute(company, thumbnail),
                              linked=list(linked), unlinked=list(unlinked))


def _apply(plan: Plan, ctx: Context, s, touched) -> Applied:
    for link in plan.data.get("links", ()):
        s.company.conn.execute(c.attachment_links.insert().values(**link))
        touched.append(Touched("attachment_link", link["id"], "create", None, 1, link, db="company"))
    for link in plan.data.get("unlinks", ()):
        s.company.conn.execute(c.attachment_links.update().where(c.attachment_links.c.id == link["id"]).values(**link))
        touched.append(Touched("attachment_link", link["id"], "update", link["version"] - 1, link["version"], link, db="company"))
    mutation = plan.data.get("mutation")
    if mutation is not None:
        items.persist_item_mutation(s.company, mutation)
        from bookflow.commands.item_cmds import _touched
        touched.append(_touched(mutation))
    return Applied(plan.preview, touched, plan.data["summary"] if touched else "no change")


def prepare_set(inp: PictureSetInput, ctx: Context, s):
    from bookflow.core.transfers import TransferPreparation
    files._pending(s)
    _current(s, inp.item)
    store, limit = files._store_limit(s)
    return TransferPreparation(store, min(limit, pics.PICTURE_MAX_BYTES))


item_picture_set = command("item picture set", scope="company", capability="item", required_role="standard",
    description="Upload a JPEG, PNG or WebP as the item's picture: linked to the item as a file, then chosen.",
    input_model=PictureSetInput, output_model=PictureWriteOutput, writes={"company"}, accepts_idempotency_key=True,
    positional=["item"], transfer=TransferDescriptor("input", prepare_set),
    error_codes=["E_VALIDATION", "E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_IO", "E_VALUE_RANGE", "E_DB_BUSY"])


@item_picture_set
def plan_set(inp: PictureSetInput, ctx: Context, s) -> Plan:
    from bookflow.core.clock import now_iso
    from bookflow.core.ids import new_id
    from bookflow.hub.users import common
    resource = files._resource(s, prepare_set(inp, ctx, s))
    kind = pics.require_image(resource.head, resource.info.size_bytes, field="picture", limit=pics.PICTURE_MAX_BYTES)
    current = _current(s, inp.item)
    found = s.company.conn.execute(sa.select(c.attachments).where(c.attachments.c.sha256 == resource.info.sha256)).mappings().first()
    attachment = dict(found) if found else dict(id=new_id(), **common(s.actor.id, ctx.interface.value),
        sha256=resource.info.sha256, size_bytes=resource.info.size_bytes, original_filename=inp.original_filename,
        media_type=kind, uploaded_by=s.actor.id, uploaded_at=now_iso(), collected_at=None)
    restore = attachment["collected_at"] is not None
    if restore:
        attachment.update(collected_at=None, version=attachment["version"] + 1, updated_at=now_iso(),
                          updated_by=s.actor.id, updated_via=ctx.interface.value)
    link, new_link = _linked(s, ctx, current["id"], attachment, inp.caption)
    thumb, thumb_link = _thumbnail(s, ctx, current["id"], inp.thumbnail_attachment)
    values = {"picture_attachment_id": attachment["id"], "picture_thumbnail_attachment_id": thumb and thumb["id"]}
    mutation = _mutation(s, ctx, current, values, inp.expected_version)
    links = [l for l, new in (((link, new_link),) + ((thumb_link,) if thumb_link else ())) if new]
    owner = mutation.after if mutation else current
    preview = _preview(s, owner, linked=[l["attachment_id"] for l in links], extra={attachment["id"]: attachment})
    return Plan(preview, dict(attachment=attachment, new_attachment=not found, restore=restore, links=links,
                              mutation=mutation, summary=f"set the picture of item {current['full_name']}"))


plan_set.resource_requirements = (("attachment", "standard"),)


@item_picture_set.applier
def apply_set(plan: Plan, ctx: Context, s) -> Applied:
    store_api.publish(s.transfer.store, s.transfer.staged)
    a = plan.data["attachment"]
    touched = []
    if plan.data["new_attachment"]:
        s.company.conn.execute(c.attachments.insert().values(**a))
        touched.append(Touched("attachment", a["id"], "create", None, 1, a, db="company"))
    elif plan.data["restore"]:
        s.company.conn.execute(c.attachments.update().where(c.attachments.c.id == a["id"]).values(**a))
        touched.append(Touched("attachment", a["id"], "update", a["version"] - 1, a["version"], a, db="company"))
    return _apply(plan, ctx, s, touched)


item_picture_use = command("item picture use", scope="company", capability="item", required_role="standard",
    description="Choose an image attachment as the item's picture, linking it to the item if it is not.",
    input_model=PictureUseInput, output_model=PictureWriteOutput, writes={"company"}, accepts_idempotency_key=True,
    positional=["item", "attachment"],
    error_codes=["E_VALIDATION", "E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_IO", "E_VALUE_RANGE", "E_DB_BUSY"])


@item_picture_use
def plan_use(inp: PictureUseInput, ctx: Context, s) -> Plan:
    files._pending(s)
    current = _current(s, inp.item)
    attachment = files._get(s, "attachment", inp.attachment)
    _head(s, attachment, pics.PICTURE_MAX_BYTES, "attachment")
    link, new_link = _linked(s, ctx, current["id"], attachment, "Item picture")
    thumb, thumb_link = _thumbnail(s, ctx, current["id"], inp.thumbnail_attachment)
    if thumb and thumb["id"] == attachment["id"]:
        raise _error("A thumbnail is a smaller copy, not the picture itself.", "thumbnail_attachment", "same as the picture")
    values = {"picture_attachment_id": attachment["id"], "picture_thumbnail_attachment_id": thumb and thumb["id"]}
    mutation = _mutation(s, ctx, current, values, inp.expected_version)
    links = [l for l, new in (((link, new_link),) + ((thumb_link,) if thumb_link else ())) if new]
    owner = mutation.after if mutation else current
    return Plan(_preview(s, owner, linked=[l["attachment_id"] for l in links]),
                dict(links=links, mutation=mutation, summary=f"set the picture of item {current['full_name']}"))


plan_use.resource_requirements = (("attachment", "standard"),)


@item_picture_use.applier
def apply_use(plan: Plan, ctx: Context, s) -> Applied:
    return _apply(plan, ctx, s, [])


item_picture_clear = command("item picture clear", scope="company", capability="item", required_role="standard",
    description="Remove the item's picture: unlinks it and its thumbnail from the item (bytes and history kept); "
                "the item then shows its newest remaining image.",
    input_model=PictureClearInput, output_model=PictureWriteOutput, writes={"company"}, accepts_idempotency_key=True,
    positional=["item"], error_codes=["E_RECORD_NOT_FOUND", "E_VERSION_CONFLICT", "E_DB_BUSY"])


@item_picture_clear
def plan_clear(inp: PictureClearInput, ctx: Context, s) -> Plan:
    from bookflow.core.clock import now_iso
    files._pending(s)
    current = _current(s, inp.item)
    shown, _thumb = pics.pictures(s.company, [current]).get(current["id"], (None, None))
    gone = {current.get("picture_thumbnail_attachment_id")} - {None}
    if not inp.keep_file:
        gone |= {current.get("picture_attachment_id") or (shown and shown["attachment_id"])} - {None}
    unlinks = []
    if gone:
        rows = s.company.conn.execute(sa.select(c.attachment_links).where(
            c.attachment_links.c.record_type == pics.RECORD_TYPE, c.attachment_links.c.record_id == current["id"],
            c.attachment_links.c.active.is_(True), c.attachment_links.c.attachment_id.in_(sorted(gone)))).mappings()
        for row in rows:
            row = dict(row)
            row.update(active=False, version=row["version"] + 1, updated_at=now_iso(), updated_by=s.actor.id,
                       updated_via=ctx.interface.value)
            unlinks.append(row)
    values = {"picture_attachment_id": None, "picture_thumbnail_attachment_id": None}
    mutation = _mutation(s, ctx, current, values, inp.expected_version)
    owner = mutation.after if mutation else current
    preview = _preview(s, owner, unlinked=[l["attachment_id"] for l in unlinks])
    return Plan(preview, dict(unlinks=unlinks, mutation=mutation, summary=f"removed the picture of item {current['full_name']}"))


plan_clear.resource_requirements = (("attachment", "standard"),)


@item_picture_clear.applier
def apply_clear(plan: Plan, ctx: Context, s) -> Applied:
    return _apply(plan, ctx, s, [])
