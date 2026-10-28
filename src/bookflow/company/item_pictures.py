"""The item picture (R114 increment 1, design/point-of-sale.md §6): one fact on the item, seen everywhere.

An item's picture is an ordinary attachment linked to the item. ``items.picture_attachment_id`` names the one chosen;
with none chosen, the item shows its newest linked image, read here when shown, so pictures attached before this
existed appear with nothing written. ``items.picture_thumbnail_attachment_id`` is a small copy the browser made, also
linked to the item. Only JPEG, PNG and WebP are pictures, and the bytes decide that, never a name or a declared type.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from bookflow.company import schema
from bookflow.core.errors import BookflowError

RECORD_TYPE = "item"
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
PICTURE_MAX_BYTES = 20_000_000
THUMBNAIL_MAX_BYTES = 1_000_000
HEIC_MESSAGE = ("HEIC pictures cannot be shown in every browser: choose Most Compatible in the phone's camera "
                "settings, or share the photo as JPEG.")
NOT_IMAGE_MESSAGE = "A picture must be a JPEG, PNG or WebP image."
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif", b"avis"}


class ItemPictureOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attachment_id: str
    sha256: str
    media_type: str = Field(description="The stored media type of the attachment.")
    size_bytes: int
    chosen: bool = Field(description="True when set with `item picture set` or `use`; false when it is the item's "
                                     "newest linked image, shown because none was chosen.")
    url: str = Field(description="Where the workbench serves it, content-addressed and cached for good.")


def sniff(head: bytes) -> str | None:
    """The image type the first bytes prove, or None."""
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def require_image(head: bytes, size: int, *, field: str, limit: int) -> str:
    """The picture's real type, or the refusal a person can act on."""
    kind = sniff(head)
    if kind is None:
        heif = head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS
        raise BookflowError("E_VALIDATION", HEIC_MESSAGE if heif else NOT_IMAGE_MESSAGE, {"fields": [
            {"field": field, "problem": "HEIC is not accepted" if heif else "not a JPEG, PNG or WebP image"}]})
    if size > limit:
        raise BookflowError("E_VALUE_RANGE", f"A {field.replace('_', ' ')} may be at most {limit:,} bytes.",
                            {"fields": [{"field": field, "problem": f"larger than {limit} bytes"}]})
    return kind


def url(attachment_id: str, sha256: str) -> str:
    """Relative to the company's workbench root, ``/c/<company>/``; commands that know the company complete it."""
    return f"picture/{attachment_id}/{sha256}"


def absolute(company_id: str, value: dict | None) -> dict | None:
    if value and not str(value.get("url", "")).startswith("/"):
        value = {**value, "url": f"/c/{company_id}/{value['url']}"}
    return value


def _newest_image(table):
    """The attachment id of an item's newest linked, uncollected image (by stored type), as a scalar subquery."""
    links, files = schema.attachment_links, schema.attachments
    other = schema.items.alias("thumbnail_owners")  # never correlated with the item being shown
    thumbnails = sa.select(other.c.picture_thumbnail_attachment_id).where(
        other.c.picture_thumbnail_attachment_id.is_not(None))
    return (sa.select(links.c.attachment_id).join(files, files.c.id == links.c.attachment_id).where(
        links.c.record_type == RECORD_TYPE, links.c.record_id == table.c.id, links.c.active.is_(True),
        files.c.collected_at.is_(None), files.c.media_type.in_(IMAGE_TYPES),
        links.c.attachment_id.not_in(thumbnails))
        .order_by(links.c.linked_at.desc(), links.c.id.desc()).limit(1).correlate(table).scalar_subquery())


def effective_expression(table: sa.Table = schema.items):
    """The picture shown for each item row: the chosen one, else its newest linked image."""
    return sa.func.coalesce(table.c.picture_attachment_id, _newest_image(table))


def query_column(table: sa.Table, which: str):
    """`item query`'s picture or thumbnail column: the attachment's facts as a JSON object, or null."""
    files = schema.attachments.alias(f"{which}_file")
    target = (effective_expression(table) if which == "picture" else
              sa.case((table.c.picture_attachment_id.is_not(None), table.c.picture_thumbnail_attachment_id)))
    return sa.select(sa.func.json_object("attachment_id", files.c.id, "sha256", files.c.sha256, "media_type",
                                         files.c.media_type, "size_bytes", files.c.size_bytes)).where(
        files.c.id == target).correlate(table).scalar_subquery()


def decode_query(company_id: str, value: str | None, chosen: bool, which: str) -> dict | None:
    import json
    if not value:
        return None
    row = json.loads(value)
    return absolute(company_id, {**row, "chosen": bool(chosen) or which == "thumbnail",
                                 "url": url(row["attachment_id"], row["sha256"])})


def _out(row: Mapping[str, Any], chosen: bool) -> dict:
    return {"attachment_id": row["id"], "sha256": row["sha256"], "media_type": row["media_type"],
            "size_bytes": row["size_bytes"], "chosen": chosen, "url": url(row["id"], row["sha256"])}


def pictures(db, owners: Iterable[Mapping[str, Any]]) -> dict[str, tuple[dict | None, dict | None]]:
    """Per item id, its (picture, thumbnail) outputs; the thumbnail only beside a chosen picture."""
    owners = list(owners)
    if not owners:
        return {}
    ids = [str(o["id"]) for o in owners]
    items = schema.items
    shown = dict(db.conn.execute(sa.select(items.c.id, effective_expression()).where(items.c.id.in_(ids))).all())
    wanted = {v for v in shown.values() if v} | {o.get("picture_thumbnail_attachment_id") for o in owners} - {None}
    rows = {r["id"]: r for r in db.conn.execute(sa.select(schema.attachments).where(
        schema.attachments.c.id.in_(sorted(wanted)))).mappings()} if wanted else {}
    out = {}
    for owner in owners:
        key = str(owner["id"])
        chosen_id = owner.get("picture_attachment_id")
        picture_id = chosen_id or shown.get(key)
        picture = _out(rows[picture_id], bool(chosen_id)) if picture_id in rows else None
        thumb_id = owner.get("picture_thumbnail_attachment_id") if chosen_id else None
        out[key] = (picture, _out(rows[thumb_id], True) if thumb_id in rows else None)
    return out


def referring_item(db, attachment_id: str, item_id: str | None = None) -> dict | None:
    """The item whose chosen picture or thumbnail this attachment is (on ``item_id`` when given)."""
    items = schema.items
    query = sa.select(items.c.id, items.c.full_name).where(sa.or_(
        items.c.picture_attachment_id == attachment_id, items.c.picture_thumbnail_attachment_id == attachment_id))
    if item_id is not None:
        query = query.where(items.c.id == item_id)
    row = db.conn.execute(query.limit(1)).mappings().first()
    return dict(row) if row else None
