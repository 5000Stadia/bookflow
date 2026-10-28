"""R114 increment 1, the item picture (design/point-of-sale.md §6): set, replace, clear and fall back; a CLI picture
with no thumbnail; the bytes decide what is a picture (HEIC refused with its message); a chosen picture is never left
dangling; `item query` returns it."""
import struct
import zlib

import pytest

COMPANY = "Demo Plumbing Co"
ITEM = "Pipe Sealant"


def png(width=4, height=3, rgb=(200, 30, 30)):
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    chunk = lambda kind, data: struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(width=4, height=3):
    """A JPEG header: SOI, JFIF APP0 and a baseline frame header (SOF0) giving its size; enough for the size check."""
    sof = b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big") + b"\x03" + b"\x01\x11\x00" * 3
    return (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\x00" * 64
            + b"\xff\xd9")


JPEG = jpeg()
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64


@pytest.fixture
def files(tmp_path):
    out = {}
    for name, body in (("old.jpg", JPEG), ("new.png", png()), ("thumb.png", png(2, 2)), ("photo.heic", HEIC),
                       ("fake.png", b"%PDF-1.7\n" + b"x" * 100), ("other.png", png(5, 5, (0, 90, 200)))):
        (tmp_path / name).write_bytes(body)
        out[name] = str(tmp_path / name)
    return out


def show(cli):
    return cli.json("item", "show", ITEM, "--company", COMPANY)


def item_id(cli, name=ITEM):
    return cli.json("item", "show", name, "--company", COMPANY)["id"]


def test_set_replace_clear_and_fall_back(cli, files):
    assert show(cli)["picture"] is None
    # A photo attached before pictures existed shows with nothing written.
    old = cli.json("attachment", "add", "item", item_id(cli), files["old.jpg"], "--company", COMPANY)["attachment"]
    before = show(cli)
    assert before["picture"]["attachment_id"] == old["id"] and before["picture"]["chosen"] is False
    assert before["picture"]["url"].endswith(f"/picture/{old['id']}/{old['sha256']}")
    assert before["picture"]["url"].startswith("/c/")

    # A CLI picture with no thumbnail: linked to the item, chosen, its type from the bytes.
    out = cli.json("item", "picture", "set", ITEM, files["new.png"], "--company", COMPANY)
    new = out["picture"]
    assert new["chosen"] is True and new["media_type"] == "image/png" and out["thumbnail"] is None
    assert out["linked"] == [new["attachment_id"]]
    shown = show(cli)
    assert shown["picture"]["attachment_id"] == new["attachment_id"] and shown["thumbnail"] is None
    assert shown["version"] == before["version"] + 1
    linked = cli.json("attachment", "list", "item", item_id(cli), "--company", COMPANY)["items"]
    assert {link["attachment_id"] for link in linked} == {old["id"], new["attachment_id"]}

    # Replacing with an image already attached, with a thumbnail; a thumbnail is never the fallback picture. The
    # replaced picture is unlinked from the item (bytes and history kept), so it never comes back.
    thumb = cli.json("attachment", "add", "item", item_id(cli), files["thumb.png"], "--company", COMPANY)["attachment"]
    used = cli.json("item", "picture", "use", ITEM, old["id"], "--thumbnail-attachment", thumb["id"],
                    "--company", COMPANY)
    assert used["picture"]["attachment_id"] == old["id"] and used["picture"]["chosen"] is True
    assert used["thumbnail"]["attachment_id"] == thumb["id"] and used["unlinked"] == [new["attachment_id"]]
    other = cli.json("attachment", "add", "item", item_id(cli), files["other.png"], "--company", COMPANY)["attachment"]

    # A chosen picture cannot be unlinked out from under the item.
    link = next(l for l in cli.json("attachment", "list", "item", item_id(cli), "--company", COMPANY)["items"]
                if l["attachment_id"] == old["id"])
    error, _ = cli.error("attachment", "unlink", link["id"], "--expected-version", str(link["version"]),
                         "--company", COMPANY)
    assert error["code"] == "E_VALIDATION" and "item picture clear" in error["message"]

    # Clearing unlinks the picture and its thumbnail; the item falls back to its newest remaining image.
    cleared = cli.json("item", "picture", "clear", ITEM, "--company", COMPANY)
    assert set(cleared["unlinked"]) == {old["id"], thumb["id"]}
    after = show(cli)
    assert after["picture"]["attachment_id"] == other["id"] and after["picture"]["chosen"] is False
    assert after["thumbnail"] is None
    # Clearing again removes that one too; then nothing shows.
    cli.json("item", "picture", "clear", ITEM, "--company", COMPANY)
    assert show(cli)["picture"] is None


def test_only_real_images_and_heic_says_what_to_do(cli, files):
    error, _ = cli.error("item", "picture", "set", ITEM, files["photo.heic"], "--company", COMPANY)
    assert error["code"] == "E_VALIDATION" and "Most Compatible" in error["message"]
    error, _ = cli.error("item", "picture", "set", ITEM, files["fake.png"], "--company", COMPANY)
    assert error["code"] == "E_VALIDATION" and "JPEG, PNG or WebP" in error["message"]
    pdf = cli.json("attachment", "add", "item", item_id(cli), files["fake.png"], "--company", COMPANY)["attachment"]
    error, _ = cli.error("item", "picture", "use", ITEM, pdf["id"], "--company", COMPANY)
    assert error["code"] == "E_VALIDATION"
    # The fallback reads the stored type (here the name's guess); the workbench serves it only if the bytes are an
    # image (tests/test_item_pictures_browser.py), and a non-image file is never the fallback.
    assert show(cli)["picture"]["attachment_id"] == pdf["id"]
    doc = cli.json("attachment", "add", "item", item_id(cli), files["photo.heic"], "--media-type", "application/pdf",
                   "--company", COMPANY)["attachment"]
    assert show(cli)["picture"]["attachment_id"] == pdf["id"] != doc["id"]


def test_item_query_returns_the_picture(cli, files):
    cli.json("item", "picture", "set", ITEM, files["new.png"], "--company", COMPANY)
    other = cli.json("attachment", "add", "item", item_id(cli, "Brass Shutoff Valve"), files["other.png"], "--company", COMPANY)
    page = cli.json("item", "query", "--columns", '["full_name", "picture", "thumbnail"]', "--limit", "200",
                    "--company", COMPANY)
    rows = {row["label"]: row["values"] for row in page["items"]}
    assert rows[ITEM]["picture"]["chosen"] is True and rows[ITEM]["picture"]["url"].startswith("/c/")
    valve = rows["Brass Shutoff Valve"]["picture"]
    assert valve["attachment_id"] == other["attachment"]["id"] and valve["chosen"] is False
    assert rows["Brass Shutoff Valve"]["thumbnail"] is None
    assert sum(1 for row in rows.values() if row["picture"]) == 2
