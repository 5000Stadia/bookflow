"""R114 increment 1 review fixes (notes/reviews/pos-inc1-review.md), each failing before its fix.

Through the library: a replaced picture and its thumbnail never come back (set A, set B, Remove shows no picture, and a
former thumbnail linked again is still never the fallback); pictures over 12,000 px on a side or 50 megapixels are
refused from their headers (PNG, JPEG, WebP); `set` refuses the picture as its own thumbnail; a merge's survivor shows
the merged item's picture. Through the workbench: the upload and Remove run in the thread pool; an oversize upload is
refused before it is read; a reader of items without the attachment capability sees pictures and no other file
(`item picture get`); a list shows a placeholder, never the full picture, for a picture with no thumbnail; the
thumbnail's stored type comes from its bytes.
"""
import io
import sqlite3
import struct

import anyio
import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow.company import item_pictures as pics
from bookflow.core.transfer_protocol import encode_input
from tests.test_item_pictures import ITEM, jpeg, png
from tests.test_row3_host import PASSWORD, hosted  # noqa: F401 - fixture

COMPANY = "Demo Plumbing Co"
H = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}
REASON = {"X-Bookflow-Reason": "picture review"}


def _png_header(width, height):
    """A PNG whose header declares any size (the body is a token IDAT; only the header is read)."""
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + b"\0\0\0\0"
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + b"\0" * 64


def _webp(width, height, kind=b"VP8X"):
    if kind == b"VP8X":
        body = b"\0" * 4 + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    elif kind == b"VP8L":
        body = b"\x2f" + ((width - 1) | (height - 1) << 14).to_bytes(4, "little")
    else:
        body = b"\0\0\0\x9d\x01\x2a" + width.to_bytes(2, "little") + height.to_bytes(2, "little")
    return b"RIFF" + (len(body) + 12).to_bytes(4, "little") + b"WEBP" + kind + len(body).to_bytes(4, "little") + body


@pytest.fixture
def lib(root):
    client = bookflow.connect(data_root=str(root))
    company = client.company.list()["items"][0]["company_id"]

    def run(name, raw, body=None, reason=None):
        if body is None:
            return client.run(name, raw, company=company, reason=reason)
        with io.BytesIO(body) as stream:
            return client.run(name, raw, company=company, input_stream=stream)

    run.company = company
    return run


def _add(run, item, body, name="f.png", **extra):
    return run("attachment add", {"record_type": "item", "record_id": item, "original_filename": name,
                                  "media_type": "image/png", **extra}, body)["attachment"]


def _set(run, body, **extra):
    return run("item picture set", {"item": ITEM, "original_filename": "p.png", **extra}, body)


def test_set_a_set_b_then_remove_shows_no_picture_never_a_or_its_thumbnail(lib):
    item = lib("item show", {"item": ITEM})["id"]
    ta = _add(lib, item, png(2, 2, (1, 2, 3)))
    a = _set(lib, png(6, 6, (1, 2, 3)), thumbnail_attachment=ta["id"])["picture"]
    tb = _add(lib, item, png(3, 3, (9, 9, 9)))
    b = _set(lib, png(7, 7, (9, 9, 9)), thumbnail_attachment=tb["id"])
    assert set(b["unlinked"]) == {a["attachment_id"], ta["id"]}  # the replaced picture and its thumbnail leave
    cleared = lib("item picture clear", {"item": ITEM})
    assert set(cleared["unlinked"]) == {b["picture"]["attachment_id"], tb["id"]}
    assert lib("item show", {"item": ITEM})["picture"] is None
    # A file that was a thumbnail, linked to the item again, is still never the picture it falls back to.
    lib("attachment link", {"attachment": ta["id"], "record_type": "item", "record_id": item})
    assert lib("item show", {"item": ITEM})["picture"] is None
    # An ordinary image linked again is.
    lib("attachment link", {"attachment": a["attachment_id"], "record_type": "item", "record_id": item})
    assert lib("item show", {"item": ITEM})["picture"]["attachment_id"] == a["attachment_id"]


def test_dimensions_are_read_from_the_header_and_capped():
    assert pics.dimensions(png(5, 4), "image/png") == (5, 4)
    assert pics.dimensions(jpeg(640, 480), "image/jpeg") == (640, 480)
    exif = b"\xff\xd8\xff\xe1" + (2 + 60_000).to_bytes(2, "big") + b"\0" * 60_000
    assert pics.dimensions(exif + jpeg(30, 20)[2:], "image/jpeg") == (30, 20)  # a frame header after a long APP1
    for kind in (b"VP8X", b"VP8L", b"VP8 "):
        assert pics.dimensions(_webp(300, 200, kind), "image/webp") == (300, 200), kind
    assert pics.require_image(_png_header(12_000, 4_000), 100, field="picture", limit=10**9) == "image/png"
    for width, height in ((60_000, 60_000), (12_001, 10), (8_000, 8_000)):
        with pytest.raises(bookflow.BookflowError) as refused:
            pics.require_image(_png_header(width, height), 100, field="picture", limit=10**9)
        assert refused.value.code == "E_VALUE_RANGE" and "12,000 pixels" in refused.value.message
    with pytest.raises(bookflow.BookflowError) as unreadable:
        pics.require_image(b"\xff\xd8\xff\xe0\x00\x10" + b"\0" * 80, 100, field="picture", limit=10**9)
    assert unreadable.value.code == "E_VALIDATION"


def test_a_huge_declared_picture_is_refused_by_set_and_use(lib):
    item = lib("item show", {"item": ITEM})["id"]
    with pytest.raises(bookflow.BookflowError) as refused:
        _set(lib, _png_header(60_000, 60_000))
    assert refused.value.code == "E_VALUE_RANGE"
    big = _add(lib, item, _png_header(60_000, 60_000))
    with pytest.raises(bookflow.BookflowError) as refused:
        lib("item picture use", {"item": ITEM, "attachment": big["id"]})
    assert refused.value.code == "E_VALUE_RANGE"


def test_set_refuses_the_picture_as_its_own_thumbnail(lib):
    item = lib("item show", {"item": ITEM})["id"]
    body = png(5, 5)
    same = _add(lib, item, body)
    with pytest.raises(bookflow.BookflowError) as refused:
        _set(lib, body, thumbnail_attachment=same["id"])
    assert refused.value.code == "E_VALIDATION" and "thumbnail" in refused.value.message


def test_a_merge_survivor_shows_the_merged_items_picture(lib):
    rows = lib("item query", {"columns": ["type"], "limit": 200})["items"]
    service = lib("item show", {"item": next(r["id"] for r in rows if r["values"]["type"] == "service")})
    for name in ("Picture Twin", "Picture Twin Old"):
        lib("item create", {"name": name, "type": "service", "price": "10.00",
                            "income_account_id": service["income_account_id"],
                            "sales_tax_code_id": service["sales_tax_code_id"]})
    old = lib("item show", {"item": "Picture Twin Old"})["id"]
    thumb = _add(lib, old, png(2, 2))
    chosen = lib("item picture set", {"item": old, "original_filename": "p.png", "thumbnail_attachment": thumb["id"]},
                 png(6, 6))
    lib("item merge", {"merged": "Picture Twin Old", "into": "Picture Twin"}, reason="Duplicate item")
    survivor = lib("item show", {"item": "Picture Twin"})
    assert survivor["picture"]["attachment_id"] == chosen["picture"]["attachment_id"]
    assert survivor["picture"]["chosen"] is False and survivor["thumbnail"]["attachment_id"] == thumb["id"]
    page = lib("item query", {"columns": ["picture", "thumbnail"], "ids": [survivor["id"]]})
    values = page["items"][0]["values"]
    assert values["picture"]["attachment_id"] == chosen["picture"]["attachment_id"]
    assert values["thumbnail"]["attachment_id"] == thumb["id"]
    # The merged item's picture changes only by undoing the merge.
    with pytest.raises(bookflow.BookflowError) as refused:
        lib("item picture clear", {"item": old})
    assert refused.value.code == "E_RECORD_IN_USE"


# --- the workbench -------------------------------------------------------------------------------------------------

def _browser(hosted, login=None, password=PASSWORD):
    client = TestClient(hosted.handle.app)
    assert client.post("/login", json={"username": login or hosted.login, "password": password}).status_code == 200
    return client


def _upload(hosted, raw, body):
    reply = hosted.api.post(f"/companies/{hosted.company_id}/transfers/item.picture.set", content=body, headers={
        **hosted.bearer, **REASON, "X-Bookflow-Input": encode_input(raw), "Content-Type": "application/octet-stream"})
    assert reply.status_code == 200, reply.text
    return reply.json()


def test_upload_and_remove_run_in_the_thread_pool(hosted, monkeypatch):
    from starlette.concurrency import run_in_threadpool as real
    from bookflow.adapters.workbench import item_picture_pages as pages
    calls = []

    async def spy(fn, *args, **kwargs):
        calls.append(fn.__name__)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(pages, "run_in_threadpool", spy, raising=False)
    br = _browser(hosted)
    item = hosted.ok("item.show", {"item": ITEM}, company=hosted.company_id)
    up = br.post(f"/c/{hosted.company_id}/item-picture/{item['id']}", headers=H,
                 files={"picture": ("p.png", png(8, 6), "image/png"), "thumbnail": ("t.png", png(4, 3), "image/png")})
    assert up.status_code == 200 and "Picture saved" in up.text, up.text[-500:]
    shown = hosted.ok("item.show", {"item": ITEM}, company=hosted.company_id)
    # The thumbnail's stored type comes from its bytes (a PNG here), not an assumed JPEG.
    assert shown["thumbnail"]["media_type"] == "image/png"
    gone = br.post(f"/c/{hosted.company_id}/item-picture/{item['id']}/clear", headers=H,
                   data={"expected_version": str(shown["version"])})
    assert gone.status_code == 200 and "Picture removed" in gone.text
    assert "upload_submit" in calls and "clear_submit" in calls, calls
    # A picture no larger than a thumbnail comes with the same bytes as both: it is kept once, with no thumbnail.
    item = hosted.ok("item.show", {"item": ITEM}, company=hosted.company_id)
    same = png(5, 4)
    up = br.post(f"/c/{hosted.company_id}/item-picture/{item['id']}", headers=H, data={
        "expected_version": str(item["version"])}, files={"picture": ("p.png", same, "image/png"),
                                                          "thumbnail": ("t.png", same, "image/png")})
    assert "Picture saved" in up.text, up.text[-500:]
    assert hosted.ok("item.show", {"item": ITEM}, company=hosted.company_id)["thumbnail"] is None


def test_an_oversize_upload_is_refused_before_it_is_read(hosted):
    br = _browser(hosted)
    item = hosted.ok("item.show", {"item": ITEM}, company=hosted.company_id)["id"]
    path = f"/c/{hosted.company_id}/item-picture/{item}"
    # A declared length over the limit is refused without reading.
    refused = br.post(path, headers={**H, "Content-Type": "multipart/form-data; boundary=x"},
                      content=b"x" * (pics.PICTURE_MAX_BYTES + pics.THUMBNAIL_MAX_BYTES + 128 * 1024))
    assert refused.status_code == 200 and "at most 20 MB" in refused.text
    # A streamed body with no length is read only up to the limit.
    cookie = "; ".join(f"{k}={v}" for k, v in br.cookies.items())
    chunks, sent, replies = 60, [0], []

    async def receive():
        sent[0] += 1
        return {"type": "http.request", "body": b"x" * (1 << 20), "more_body": sent[0] < chunks}

    async def send(message):
        replies.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
             "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode()), (b"x-bookflow-workbench", b"1"),
                         (b"content-type", b"multipart/form-data; boundary=x")]}
    anyio.run(hosted.handle.app, scope, receive, send)
    body = b"".join(m.get("body", b"") for m in replies if m["type"] == "http.response.body")
    assert b"at most 20 MB" in body and sent[0] < chunks, (sent[0], body[-300:])


def _membership_version(hosted, user):
    with sqlite3.connect((hosted.root / "hub.db").as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT version FROM memberships WHERE user_id=? AND scope_type='company' AND scope_id=?",
                         (user, hosted.company_id)).fetchone()
    return row[0] if row else 0


def test_a_reader_of_items_without_attachments_sees_pictures_and_no_other_file(hosted):
    cid = hosted.company_id
    item = hosted.ok("item.show", {"item": ITEM}, company=cid)["id"]
    thumb = hosted.api.post(f"/companies/{cid}/transfers/attachment.add", content=png(3, 2), headers={
        **hosted.bearer, **REASON, "Content-Type": "application/octet-stream", "X-Bookflow-Input": encode_input({
            "record_type": "item", "record_id": item, "original_filename": "t.png", "media_type": "image/png"})})
    thumb = thumb.json()["attachment"]
    other = hosted.api.post(f"/companies/{cid}/transfers/attachment.add", content=png(9, 9, (5, 5, 5)), headers={
        **hosted.bearer, **REASON, "Content-Type": "application/octet-stream", "X-Bookflow-Input": encode_input({
            "record_type": "customer", "record_id": hosted.ok("customer.list", {}, company=cid)["items"][0]["id"],
            "original_filename": "receipt.png", "media_type": "image/png"})}).json()["attachment"]
    done = _upload(hosted, {"item": item, "original_filename": "p.png", "thumbnail_attachment": thumb["id"]}, png(8, 8))
    clerk = hosted.ok("user.add", {"username": "pos-clerk", "password": "pw-pos-clerk-12345", "company": cid,
                                   "role": "readonly"}, headers=REASON)["user_id"]
    denied = hosted.call("membership.grant", {"user": clerk, "company": cid, "role": "readonly", "grants": [],
                                              "denies": ["attachment"],
                                              "expected_version": _membership_version(hosted, clerk)}, headers=REASON)
    assert denied.status_code == 200, denied.text
    br = _browser(hosted, "pos-clerk", "pw-pos-clerk-12345")
    for shown in (done["picture"], done["thumbnail"]):
        got = br.get(shown["url"])
        assert got.status_code == 200 and got.headers["content-type"] == "image/png", (got.status_code, shown)
        assert got.headers["content-length"] == str(shown["size_bytes"]) and "immutable" in got.headers["cache-control"]
    # Not a picture of any item: still needs the attachment capability.
    assert br.get(f"/c/{cid}/picture/{other['id']}/{other['sha256']}").status_code == 404
    assert br.post(f"/companies/{cid}/commands/attachment.list", headers=H,
                   json={"record_type": "item", "record_id": item}).status_code == 403


def test_a_list_shows_a_placeholder_never_the_full_picture(hosted):
    cid = hosted.company_id
    item = hosted.ok("item.show", {"item": ITEM}, company=cid)["id"]
    done = _upload(hosted, {"item": item, "original_filename": "p.png"}, png(8, 8))
    assert done["thumbnail"] is None
    page = _browser(hosted).get(f"/c/{cid}/item")
    assert page.status_code == 200
    assert done["picture"]["url"] not in page.text and "data:image/svg+xml" in page.text
