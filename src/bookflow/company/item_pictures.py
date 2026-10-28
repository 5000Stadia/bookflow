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
# Pixels, read from the header: a picture larger than this on a side, or in all, is refused, since every browser that
# shows it decodes it whole (a phone tab runs out of memory on a small file that declares 60000x60000).
MAX_SIDE = 12_000
MAX_PIXELS = 50_000_000
# How far into the file the header is read: a JPEG's size (its SOF segment) follows its EXIF and colour profile.
HEAD_BYTES = 256 * 1024  # as bookflow.core.transfers.HEAD_BYTES keeps of an upload
# Every thumbnail's link to its item carries this caption, and a file ever linked to an item under it is never the
# picture an item falls back to, even after it is unlinked and linked again.
THUMBNAIL_CAPTION = "Item picture thumbnail"
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


_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def dimensions(head: bytes, kind: str) -> tuple[int, int] | None:
    """(width, height) from the header of a JPEG (its SOF segment), PNG (IHDR) or WebP (VP8, VP8L or VP8X), or None."""
    if kind == "image/png":
        if len(head) >= 24 and head[12:16] == b"IHDR":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        return None
    if kind == "image/webp":
        chunk = head[12:16]
        if chunk == b"VP8X" and len(head) >= 30:
            return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
        if chunk == b"VP8L" and len(head) >= 25 and head[20] == 0x2F:
            bits = int.from_bytes(head[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8 " and len(head) >= 30 and head[23:26] == b"\x9d\x01\x2a":
            return int.from_bytes(head[26:28], "little") & 0x3FFF, int.from_bytes(head[28:30], "little") & 0x3FFF
        return None
    at = 2  # after SOI: markers, each with a two-byte length, until a frame header
    while at + 4 <= len(head):
        if head[at] != 0xFF:
            return None
        marker = head[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            at += 2
            continue
        if marker in (0xD9, 0xDA):
            return None
        if marker in _JPEG_SOF:
            if at + 9 > len(head):
                return None
            return int.from_bytes(head[at + 7:at + 9], "big"), int.from_bytes(head[at + 5:at + 7], "big")
        at += 2 + int.from_bytes(head[at + 2:at + 4], "big")
    return None


def require_image(head: bytes, size: int, *, field: str, limit: int) -> str:
    """The picture's real type, or the refusal a person can act on. Its size in pixels is read from the header: one
    that cannot be read, or is over 12,000 px on a side or 50 megapixels in all, is refused."""
    kind = sniff(head)
    if kind is None:
        heif = head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS
        raise BookflowError("E_VALIDATION", HEIC_MESSAGE if heif else NOT_IMAGE_MESSAGE, {"fields": [
            {"field": field, "problem": "HEIC is not accepted" if heif else "not a JPEG, PNG or WebP image"}]})
    if size > limit:
        raise BookflowError("E_VALUE_RANGE", f"A {field.replace('_', ' ')} may be at most {limit:,} bytes.",
                            {"fields": [{"field": field, "problem": f"larger than {limit} bytes"}]})
    size_px = dimensions(head, kind)
    if size_px is None or 0 in size_px:
        raise BookflowError("E_VALIDATION", "This picture's size could not be read; save it again as JPEG or PNG.",
                            {"fields": [{"field": field, "problem": "width and height not readable"}]})
    width, height = size_px
    if max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
        raise BookflowError("E_VALUE_RANGE", f"This picture is {width:,} x {height:,} pixels; a picture may be at "
                            f"most {MAX_SIDE:,} pixels on a side and {MAX_PIXELS // 1_000_000} megapixels in all.",
                            {"fields": [{"field": field, "problem": f"{width}x{height} pixels is too large"}]})
    return kind


def url(attachment_id: str, sha256: str) -> str:
    """Relative to the company's workbench root, ``/c/<company>/``; commands that know the company complete it."""
    return f"picture/{attachment_id}/{sha256}"


def absolute(company_id: str, value: dict | None) -> dict | None:
    if value and not str(value.get("url", "")).startswith("/"):
        value = {**value, "url": f"/c/{company_id}/{value['url']}"}
    return value


def _thumbnails():
    """Every file that is or ever was a thumbnail: some item's current one, or linked to an item as one."""
    other = schema.items.alias("thumbnail_owners")  # never correlated with the item being shown
    was = schema.attachment_links.alias("thumbnail_links")
    return sa.union(
        sa.select(other.c.picture_thumbnail_attachment_id).where(other.c.picture_thumbnail_attachment_id.is_not(None)),
        sa.select(was.c.attachment_id).where(was.c.record_type == RECORD_TYPE, was.c.caption == THUMBNAIL_CAPTION))


def _newest_image(table):
    """The attachment id of an item's newest linked, uncollected image (by stored type), as a scalar subquery."""
    links, files = schema.attachment_links, schema.attachments
    return (sa.select(links.c.attachment_id).join(files, files.c.id == links.c.attachment_id).where(
        links.c.record_type == RECORD_TYPE, links.c.record_id == table.c.id, links.c.active.is_(True),
        files.c.collected_at.is_(None), files.c.media_type.in_(IMAGE_TYPES),
        links.c.attachment_id.not_in(_thumbnails()))
        .order_by(links.c.linked_at.desc(), links.c.id.desc()).limit(1).correlate(table).scalar_subquery())


def _inherited(table, column="picture_attachment_id"):
    """The chosen picture (or its thumbnail) of the newest item merged into this one, as a scalar subquery."""
    merged, merges = schema.items.alias("merged_items"), schema.list_merges
    return (sa.select(merged.c[column]).join(merges, merges.c.merged_id == merged.c.id).where(
        merges.c.list_kind == RECORD_TYPE, merges.c.survivor_id == table.c.id, merges.c.undone_at.is_(None),
        merged.c.picture_attachment_id.is_not(None))
        .order_by(merges.c.created_at.desc(), merges.c.id.desc()).limit(1).correlate(table).scalar_subquery())


def effective_expression(table: sa.Table = schema.items):
    """The picture shown for each item row: the chosen one, else its newest linked image, else the chosen picture
    of an item merged into it (the survivor of a merge inherits the merged item's picture when it has none)."""
    return sa.func.coalesce(table.c.picture_attachment_id, _newest_image(table), _inherited(table))


def thumbnail_expression(table: sa.Table = schema.items):
    """The thumbnail shown beside the picture: the chosen picture's, else the inherited picture's."""
    return sa.case((table.c.picture_attachment_id.is_not(None), table.c.picture_thumbnail_attachment_id),
                   (_newest_image(table).is_(None), _inherited(table, "picture_thumbnail_attachment_id")))


def query_column(table: sa.Table, which: str):
    """`item query`'s picture or thumbnail column: the attachment's facts as a JSON object, or null."""
    files = schema.attachments.alias(f"{which}_file")
    target = effective_expression(table) if which == "picture" else thumbnail_expression(table)
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
    shown = {row[0]: (row[1], row[2]) for row in db.conn.execute(sa.select(
        items.c.id, effective_expression(), thumbnail_expression()).where(items.c.id.in_(ids)))}
    wanted = {v for pair in shown.values() for v in pair if v}
    rows = {r["id"]: r for r in db.conn.execute(sa.select(schema.attachments).where(
        schema.attachments.c.id.in_(sorted(wanted)))).mappings()} if wanted else {}
    out = {}
    for owner in owners:
        key = str(owner["id"])
        chosen_id = owner.get("picture_attachment_id")
        picture_id, thumb_id = shown.get(key, (None, None))
        picture_id = chosen_id or picture_id
        if chosen_id:
            thumb_id = owner.get("picture_thumbnail_attachment_id")
        picture = _out(rows[picture_id], bool(chosen_id)) if picture_id in rows else None
        out[key] = (picture, _out(rows[thumb_id], True) if picture and thumb_id in rows else None)
    return out


def shown_by(db, attachment_id: str) -> str | None:
    """The id of an item that shows this attachment as its picture or thumbnail (chosen, fallen back to, or inherited
    through a merge), or None. `item picture get` serves only such a file to a reader of items."""
    items, links = schema.items, schema.attachment_links
    direct = db.conn.execute(sa.select(items.c.id).where(sa.or_(
        items.c.picture_attachment_id == attachment_id, items.c.picture_thumbnail_attachment_id == attachment_id))
        .limit(1)).scalar()
    if direct:
        return direct
    owners = sa.select(links.c.record_id).where(links.c.attachment_id == attachment_id,
                                                links.c.record_type == RECORD_TYPE, links.c.active.is_(True))
    return db.conn.execute(sa.select(items.c.id).where(items.c.id.in_(owners),
                                                       effective_expression() == attachment_id).limit(1)).scalar()


def referring_item(db, attachment_id: str, item_id: str | None = None) -> dict | None:
    """The item whose chosen picture or thumbnail this attachment is (on ``item_id`` when given)."""
    items = schema.items
    query = sa.select(items.c.id, items.c.full_name).where(sa.or_(
        items.c.picture_attachment_id == attachment_id, items.c.picture_thumbnail_attachment_id == attachment_id))
    if item_id is not None:
        query = query.where(items.c.id == item_id)
    row = db.conn.execute(query.limit(1)).mappings().first()
    return dict(row) if row else None
