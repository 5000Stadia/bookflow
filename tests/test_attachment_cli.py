"""Calling-machine binary paths, metadata output, and no-replace downloads."""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.transfer_paths import atomic_output

COMPANY = "Demo Plumbing Co"
PDF = b"%PDF-1.7\n" + bytes(range(256)) * 600 + b"\n%%EOF\n"


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "Receipt.pdf"
    path.write_bytes(PDF)
    return path


@pytest.fixture
def target(client):
    return client.customer.create(name="Attachment CLI target", company=COMPANY)["id"]


def upload(cli, target, path, *extra):
    return cli.json("attachment", "add", "customer", target, str(path),
                    "--company", COMPANY, *extra)


def test_add_pdf_get_exact_and_json_metadata(cli, target, pdf, tmp_path):
    added = upload(cli, target, pdf)
    attachment = added["attachment"]
    assert attachment["original_filename"] == pdf.name
    assert attachment["media_type"] == "application/pdf"
    assert attachment["size_bytes"] == len(PDF)
    assert attachment["sha256"] == hashlib.sha256(PDF).hexdigest()
    output = tmp_path / "download.pdf"
    metadata = cli.json("attachment", "get", attachment["id"], "--out", str(output), "--company", COMPANY)
    assert metadata == attachment
    assert output.read_bytes() == PDF
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".bookflow-transfer-*"))
    assert str(pdf) not in json.dumps(added)
    assert str(output) not in json.dumps(metadata)


def test_cli_explicit_filename_and_media_override(cli, target, pdf):
    attachment = upload(cli, target, pdf, "--original-filename", "Invoice.bin",
                        "--media-type", "application/octet-stream")["attachment"]
    assert attachment["original_filename"] == "Invoice.bin"
    assert attachment["media_type"] == "application/octet-stream"


def test_existing_output_preserved(cli, target, pdf, tmp_path):
    attachment = upload(cli, target, pdf)["attachment"]
    output = tmp_path / "existing.pdf"
    output.write_bytes(b"original destination")
    before = output.stat()
    error, code = cli.error("attachment", "get", attachment["id"], "--out", str(output), "--company", COMPANY)
    assert error["code"] == "E_IO" and code == 1
    assert output.read_bytes() == b"original destination"
    assert output.stat().st_ino == before.st_ino
    assert not list(tmp_path.glob(".bookflow-transfer-*"))


@pytest.mark.parametrize("directory", [False, True])
def test_wrong_input_path_is_stable_io(cli, target, tmp_path, directory):
    path = tmp_path / "bad.pdf"
    if directory:
        path.mkdir()
    error, code = cli.error("attachment", "add", "customer", target, str(path), "--company", COMPANY)
    assert error["code"] == "E_IO" and code == 1


def test_dry_run_leaves_attachment_store_and_links_unchanged(cli, target, pdf, root):
    def stores():
        return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
                for store in root.rglob("attachments") if store.is_dir()
                for p in [store, *store.rglob("*")]}

    before = stores()
    preview = upload(cli, target, pdf, "--dry-run")
    assert preview["dry_run"] is True
    assert preview["attachment"]["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert stores() == before
    listed = cli.json("attachment", "list", "customer", target, "--company", COMPANY)
    assert listed["count"] == 0


def test_help_and_required_external_paths(cli):
    add_help = cli.run("attachment", "add", "--help").stdout
    assert "PATH" in add_help and "--original-filename" in add_help
    assert "basename" in add_help and "MIME" in add_help
    get_help = cli.run("attachment", "get", "--help").stdout
    assert "--out" in get_help and "PATH" in get_help and "atomically" in get_help
    for args in (("attachment", "get", "example"), ("attachment", "add", "customer", "example")):
        error, code = cli.error(*args, "--company", COMPANY)
        assert error["code"] == "E_USAGE" and code == 2


@pytest.mark.parametrize("failure", [BookflowError("E_IO"), OSError("backend disconnected"), KeyboardInterrupt()])
def test_cli_failed_download_cleans_private_output(monkeypatch, tmp_path, capsys, failure):
    from bookflow.adapters.cli.app import main
    from bookflow.core import dispatch

    output = tmp_path / "failed.pdf"

    def failed(cmd, raw, ctx, **kwargs):
        assert raw == {"attachment": "example"}
        stream = kwargs["output_stream"]
        assert not output.exists()
        assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600
        stream.write(PDF[:100])
        raise failure

    monkeypatch.setattr(dispatch, "run", failed)
    monkeypatch.setattr(sys, "argv", ["bookflow", "attachment", "get", "example", "--out", str(output),
                                     "--company", COMPANY, "--data-root", str(tmp_path / "isolated"), "--json"])
    if isinstance(failure, KeyboardInterrupt):
        try:
            main()
        except SystemExit:
            pass
    else:
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1
    captured = capsys.readouterr()
    if not isinstance(failure, KeyboardInterrupt):
        assert json.loads(captured.err)["code"] == "E_IO"
    assert not output.exists()
    assert not list(tmp_path.glob(".bookflow-transfer-*"))


def test_input_stream_is_external_and_read_by_dispatch(monkeypatch, tmp_path, capsys):
    from bookflow.adapters.cli.app import main
    from bookflow.core import dispatch

    path = tmp_path / "body.unknown-bookflow-type"
    path.write_bytes(PDF)
    original_open = Path.open
    reads = []

    class Bounded:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            assert 0 < size <= 65536
            reads.append(size)
            return self.stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

    def opened(p, *args, **kwargs):
        stream = original_open(p, *args, **kwargs)
        return Bounded(stream) if p == path else stream

    def receive(cmd, raw, ctx, **kwargs):
        assert not reads
        assert raw == {"record_type": "customer", "record_id": "example",
                       "original_filename": path.name, "media_type": "application/octet-stream"}
        body = bytearray()
        while chunk := kwargs["input_stream"].read(65536):
            body.extend(chunk)
        assert body == PDF
        return {"metadata": True}

    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(dispatch, "run", receive)
    monkeypatch.setattr(sys, "argv", ["bookflow", "attachment", "add", "customer", "example", str(path),
                                     "--company", COMPANY, "--data-root", str(tmp_path / "isolated"), "--json"])
    main()
    assert json.loads(capsys.readouterr().out) == {"metadata": True}
    assert len(reads) > 2


def test_output_race_never_replaces_winner(tmp_path):
    output = tmp_path / "raced.pdf"
    with pytest.raises(FileExistsError):
        with atomic_output(output) as stream:
            stream.write(PDF)
            output.write_bytes(b"race winner")
    assert output.read_bytes() == b"race winner"
    assert not list(tmp_path.glob(".bookflow-transfer-*"))


def test_existing_symlink_and_unrelated_temp_are_preserved(tmp_path):
    output = tmp_path / "existing.pdf"
    output.symlink_to(tmp_path / "absent")
    unrelated = tmp_path / ".bookflow-transfer-unrelated"
    unrelated.write_bytes(b"another invocation")
    with pytest.raises(FileExistsError):
        with atomic_output(output):
            pytest.fail("an existing symlink must be rejected before dispatch")
    assert output.is_symlink()
    assert unrelated.read_bytes() == b"another invocation"


def test_output_waits_for_completion_and_syncs_before_link(monkeypatch, tmp_path):
    from bookflow.core import transfer_paths

    output = tmp_path / "verified.pdf"
    original_sync, original_link = os.fsync, os.link
    operations = []

    def sync(fd):
        operations.append("directory_sync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_sync")
        original_sync(fd)

    def link(*args, **kwargs):
        assert stream.closed
        assert not output.exists()
        operations.append("link")
        original_link(*args, **kwargs)

    monkeypatch.setattr(transfer_paths.os, "fsync", sync)
    monkeypatch.setattr(transfer_paths.os, "link", link)
    with atomic_output(output) as stream:
        stream.write(PDF)
        assert not output.exists()
        assert list(tmp_path.glob(".bookflow-transfer-*"))
    assert operations == ["file_sync", "link", "directory_sync"]
    assert output.read_bytes() == PDF


@pytest.mark.parametrize("failure_at", ["link", "file_sync", "directory_sync"])
def test_publication_failure_cleans_own_files(monkeypatch, tmp_path, failure_at):
    from bookflow.core import transfer_paths

    output = tmp_path / "failed.pdf"
    original_fsync = os.fsync

    def sync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if directory == (failure_at == "directory_sync"):
            raise OSError("sync failed")
        original_fsync(fd)

    def link(*args, **kwargs):
        raise OSError("hard links unavailable")

    if failure_at == "link":
        monkeypatch.setattr(transfer_paths.os, "link", link)
    else:
        monkeypatch.setattr(transfer_paths.os, "fsync", sync)
    with pytest.raises(OSError):
        with atomic_output(output) as stream:
            stream.write(PDF)
    assert not output.exists()
    assert not list(tmp_path.glob(".bookflow-transfer-*"))


def test_link_positional_order_and_activity_kinds(cli, target, pdf):
    added = upload(cli, target, pdf)
    account = cli.json("account", "create", "--name", "Attachment destination", "--type", "income", "--company", COMPANY)
    linked = cli.json("attachment", "link", added["attachment"]["id"], "account", account["id"], "--company", COMPANY)
    assert linked["link"]["record_id"] == account["id"]
    feed = cli.json("activity", "account", account["id"], "--kinds", '["attachment"]', "--company", COMPANY)
    assert len(feed["items"]) == 1 and feed["items"][0]["kind"] == "attachment"
