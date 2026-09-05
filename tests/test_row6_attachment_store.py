"""Bounded, private and durable attachment byte lifecycle."""

import asyncio
import errno
import hashlib
import io
import os
from pathlib import Path
import stat

import pytest

from bookflow.company import attachment_store as store_api
from bookflow.core.errors import BookflowError


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "attachments"
    path.mkdir(mode=0o700)
    return path


def body_path(store, data):
    digest = hashlib.sha256(data).hexdigest()
    return store / digest[:2] / digest


def put(store, data):
    with store_api.stage(store, io.BytesIO(data), max(1, len(data))) as staged:
        return store_api.publish(store, staged)


def assert_error(code, operation, check=None):
    with pytest.raises(BookflowError) as caught:
        operation()
    assert caught.value.code == code
    if check is not None:
        assert caught.value.details == {"check": check}
    return caught.value


@pytest.mark.parametrize("data", [b"", b"abc", "é📄".encode(), b"a" * 140_000])
def test_exact_limit_bounded_requests_and_source_owned_by_caller(store, data):
    class Source(io.BytesIO):
        def read(self, size):
            assert 0 < size <= 65_536
            return super().read(size)

    source = Source(data)
    with store_api.stage(store, source, max(1, len(data))) as staged:
        assert staged.info.size_bytes == len(data)
        assert staged.info.sha256 == hashlib.sha256(data).hexdigest()
        result = store_api.publish(store, staged)
        assert result.newly_published
        assert not hasattr(result, "path")
    assert body_path(store, data).read_bytes() == data
    assert not source.closed
    assert not list(store.glob(".attachment-*"))


@pytest.mark.parametrize("data,limit", [(b"abcd", 3), ("é".encode(), 1),
                                         (b"a" * 200_000, 70_000)])
def test_over_limit_reads_only_one_extra_byte_and_cleans(store, data, limit):
    source = io.BytesIO(data)
    with pytest.raises(BookflowError) as caught:
        with store_api.stage(store, source, limit):
            pytest.fail("oversize resource yielded")
    assert caught.value.code == "E_VALUE_RANGE"
    assert source.tell() == limit + 1
    assert list(store.iterdir()) == []


@pytest.mark.parametrize("returned", [None, "text", bytearray(b"x"), b"x" * 65_537])
def test_malformed_stream_is_rejected_and_cleans(store, returned):
    class Source:
        def read(self, size):
            return returned

    with pytest.raises(BookflowError) as caught:
        with store_api.stage(store, Source(), 100_000):
            pytest.fail("malformed resource yielded")
    assert caught.value.code == "E_VALIDATION"
    assert list(store.iterdir()) == []


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 100_000_001])
def test_invalid_limit(limit):
    assert_error("E_VALIDATION", lambda: store_api.scan(io.BytesIO(), limit))


def test_dry_scan_has_no_filesystem_effect(store, monkeypatch):
    before = list(store.iterdir())

    def forbidden(*args, **kwargs):
        pytest.fail("dry scan touched the filesystem")

    monkeypatch.setattr(store_api.tempfile, "mkstemp", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    info = store_api.scan(io.BytesIO(b"abc"), 3)
    assert info.sha256 == hashlib.sha256(b"abc").hexdigest()
    assert info.size_bytes == 3
    assert list(store.iterdir()) == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX private permission bits")
def test_private_modes_and_unique_temporaries(store):
    with store_api.stage(store, io.BytesIO(b"a"), 1) as first:
        with store_api.stage(store, io.BytesIO(b"b"), 1) as second:
            assert first._path != second._path
            assert stat.S_IMODE(first._path.stat().st_mode) == 0o600
            result = store_api.publish(store, first)
            body = store / result.sha256[:2] / result.sha256
            assert stat.S_IMODE(body.stat().st_mode) == 0o600
            assert stat.S_IMODE(body.parent.stat().st_mode) == 0o700
        assert first._path.exists()
    assert not first._path.exists()
    assert not second._path.exists()


def test_dedup_verifies_and_syncs_existing_file(store, monkeypatch):
    first = put(store, b"abc")
    inode = body_path(store, b"abc").stat().st_ino
    synced_files = []
    synced_dirs = []
    fsync = os.fsync

    def track(fd):
        synced_files.append(os.fstat(fd).st_ino)
        fsync(fd)

    monkeypatch.setattr(store_api.os, "fsync", track)
    monkeypatch.setattr(store_api, "sync_directory", synced_dirs.append)
    second = put(store, b"abc")
    assert first.newly_published and not second.newly_published
    assert body_path(store, b"abc").stat().st_ino == inode
    assert inode in synced_files
    assert synced_dirs == [body_path(store, b"abc").parent, store]


@pytest.mark.parametrize("corrupt", [b"abd", b"", b"longer"])
def test_existing_corruption_is_not_replaced_or_removed(store, corrupt):
    put(store, b"abc")
    body = body_path(store, b"abc")
    body.write_bytes(corrupt)
    assert_error("E_IO", lambda: put(store, b"abc"), "body_content")
    assert body.read_bytes() == corrupt
    assert not list(store.glob(".attachment-*"))


@pytest.mark.parametrize("tamper", [b"abd", b"", b"longer"])
def test_staged_tampering_is_verified_before_publication(store, tamper):
    with store_api.stage(store, io.BytesIO(b"abc"), 3) as staged:
        staged._writer.flush()
        staged._path.write_bytes(tamper)
        assert_error("E_IO", lambda: store_api.publish(store, staged), "staged_content")
        assert staged._writer.closed
    assert list(store.iterdir()) == []


@pytest.mark.parametrize("entry", ["store", "shard", "body"])
@pytest.mark.skipif(os.name != "posix", reason="Symlink creation requires platform privileges")
def test_symlink_entries_refused(store, tmp_path, entry):
    data = b"abc"
    target = tmp_path / "target"
    if entry == "store":
        target.mkdir(mode=0o700)
        store.rmdir()
        store.symlink_to(target, target_is_directory=True)
    elif entry == "shard":
        target.mkdir(mode=0o700)
        body_path(store, data).parent.symlink_to(target, target_is_directory=True)
    else:
        target.write_bytes(data)
        body_path(store, data).parent.mkdir(mode=0o700)
        body_path(store, data).symlink_to(target)
    assert_error("E_IO", lambda: put(store, data))
    assert target.is_dir() and list(target.iterdir()) == [] or target.read_bytes() == data


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_body_refused_without_opening(store, kind):
    if kind == "fifo" and (os.name != "posix" or not hasattr(os, "mkfifo")):
        pytest.skip("POSIX named pipe witness")
    body = body_path(store, b"abc")
    body.parent.mkdir(mode=0o700)
    if kind == "directory":
        body.mkdir()
    else:
        os.mkfifo(body, 0o600)
    assert_error("E_IO", lambda: put(store, b"abc"), "body_type")
    assert body.exists()


@pytest.mark.parametrize("entry", ["store", "shard"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX directory ownership and mode")
def test_nonprivate_directory_refused_without_chmod(store, entry):
    path = store if entry == "store" else body_path(store, b"abc").parent
    if entry == "shard":
        path.mkdir(mode=0o700)
    path.chmod(0o755)
    assert_error("E_IO", lambda: put(store, b"abc"), "directory_private")
    assert stat.S_IMODE(path.stat().st_mode) == 0o755


@pytest.mark.parametrize("failure", ["link", "fsync"])
def test_publication_os_failure_cleans_and_never_falls_back(store, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise OSError(errno.ENOTSUP, "private path must not leak")

    monkeypatch.setattr(store_api.os, failure, fail)
    error = assert_error("E_IO", lambda: put(store, b"abc"), "publication")
    assert "private path" not in str(error)
    assert not body_path(store, b"abc").exists()
    assert not list(store.glob(".attachment-*"))


@pytest.mark.parametrize("fail_at", [1, 2])
def test_failed_directory_sync_leaves_body_and_consumes_writer_retry_syncs(store, monkeypatch, fail_at):
    original = store_api.sync_directory
    calls = []

    def fail(path):
        calls.append(path)
        if len(calls) == fail_at:
            raise OSError("sync failed")
        original(path)

    monkeypatch.setattr(store_api, "sync_directory", fail)
    with store_api.stage(store, io.BytesIO(b"abc"), 3) as staged:
        assert_error("E_IO", lambda: store_api.publish(store, staged), "publication")
        assert staged._writer.closed
        assert_error("E_IO", lambda: store_api.publish(store, staged), "staged_resource")
        with pytest.raises(ValueError):
            staged._writer.write(b"corruption")
        assert body_path(store, b"abc").read_bytes() == b"abc"
    monkeypatch.setattr(store_api, "sync_directory", original)
    assert not put(store, b"abc").newly_published
    assert body_path(store, b"abc").read_bytes() == b"abc"


@pytest.mark.parametrize("during_scan", [True, False])
def test_cancellation_cleans_only_owned_temporary(store, during_scan):
    unrelated = store / ".attachment-unrelated.tmp"
    unrelated.write_bytes(b"keep")

    class Source:
        def read(self, size):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        with store_api.stage(store, Source() if during_scan else io.BytesIO(b"abc"), 3):
            raise asyncio.CancelledError()
    assert list(store.iterdir()) == [unrelated]
    assert unrelated.read_bytes() == b"keep"


@pytest.mark.parametrize("winner", [b"abc", b"bad"])
def test_no_replace_race_verifies_winner(store, monkeypatch, winner):
    link = os.link

    def race(src, dst, **kwargs):
        Path(dst).write_bytes(winner)
        return link(src, dst, **kwargs)

    monkeypatch.setattr(store_api.os, "link", race)
    if winner == b"abc":
        assert not put(store, b"abc").newly_published
    else:
        assert_error("E_IO", lambda: put(store, b"abc"), "body_content")
    assert body_path(store, b"abc").read_bytes() == winner


def test_consumed_closed_and_wrong_store_resources_refused(store, tmp_path):
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    with store_api.stage(store, io.BytesIO(b"abc"), 3) as staged:
        assert_error("E_IO", lambda: store_api.publish(other, staged), "staged_resource")
        store_api.publish(store, staged)
        assert_error("E_IO", lambda: store_api.publish(store, staged), "staged_resource")
    assert_error("E_IO", lambda: store_api.publish(store, staged), "staged_resource")
    assert_error("E_IO", lambda: store_api.publish(store, Path("arbitrary")), "staged_resource")
    with store_api.stage(store, io.BytesIO(b"abc"), 3) as closed:
        closed._writer.close()
        assert_error("E_IO", lambda: store_api.publish(store, closed), "staged_resource")
    assert list(other.iterdir()) == []


def test_external_and_replaced_staging_paths_refused(store, tmp_path):
    external = tmp_path / ".attachment-external.tmp"
    external.write_bytes(b"abc")
    with external.open("r+b") as writer:
        assert_error("E_IO", lambda: store_api.StagedAttachment(
            store, external, writer, store_api.scan(io.BytesIO(b"abc"), 3)),
            "staged_resource")
    with store_api.stage(store, io.BytesIO(b"abc"), 3) as staged:
        original = staged._path
        staged._path = external
        assert_error("E_IO", lambda: store_api.publish(store, staged), "staged_resource")
        staged._path = original
        staged._writer.flush()
        original.unlink()
        original.write_bytes(b"abc")
        assert_error("E_IO", lambda: store_api.publish(store, staged), "staged_identity")
    assert external.read_bytes() == b"abc"
    assert not body_path(store, b"abc").exists()


@pytest.mark.parametrize("staged_body", [True, False])
@pytest.mark.skipif(os.name != "posix", reason="POSIX sparse-file witness")
def test_sparse_corruption_rejected_before_any_verification_read(store, monkeypatch, staged_body):
    original_verify = store_api._verify

    class NoRead:
        def __init__(self, reader):
            self.reader = reader

        def fileno(self):
            return self.reader.fileno()

        def seek(self, *args):
            pytest.fail("size mismatch must reject before reading")

    def check(reader, info, label):
        if label == ("staged_content" if staged_body else "body_content"):
            reader = NoRead(reader)
        original_verify(reader, info, label)

    if not staged_body:
        put(store, b"abc")
        with body_path(store, b"abc").open("r+b") as writer:
            writer.truncate(1 << 40)
    monkeypatch.setattr(store_api, "_verify", check)
    with store_api.stage(store, io.BytesIO(b"abc"), 3) as staged:
        if staged_body:
            staged._writer.flush()
            staged._writer.truncate(1 << 40)
        assert_error("E_IO", lambda: store_api.publish(store, staged),
                     "staged_content" if staged_body else "body_content")


@pytest.mark.parametrize("exceptional", [True, False])
def test_cleanup_failure_observable_without_masking_original(store, monkeypatch, exceptional):
    unlink = Path.unlink

    def fail(path, **kwargs):
        if path.name.startswith(".attachment-"):
            raise OSError("cleanup failed")
        return unlink(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        with pytest.raises(asyncio.CancelledError if exceptional else BookflowError) as caught:
            with store_api.stage(store, io.BytesIO(b"abc"), 3):
                if exceptional:
                    raise asyncio.CancelledError()
        if not exceptional:
            assert caught.value.details == {"check": "stage_cleanup"}
    for path in store.glob(".attachment-*"):
        path.unlink()


def test_verification_growth_reads_only_expected_size_plus_one(store):
    with store_api.stage(store, io.BytesIO(b"abc"), 3) as staged:
        staged._writer.flush()

        class GrowingReader:
            requests = []

            def fileno(self):
                return staged._writer.fileno()

            def seek(self, offset):
                pass

            def read(self, size):
                self.requests.append(size)
                return b"x" * size

        reader = GrowingReader()
        assert_error("E_IO", lambda: store_api._verify(reader, staged.info, "staged_content"),
                     "staged_content")
        assert reader.requests == [4]


def test_link_sees_closed_synced_writer_and_cancel_preserves_body(store, monkeypatch):
    link = os.link
    fsync = os.fsync
    synced = []

    def track(fd):
        synced.append(os.fstat(fd).st_ino)
        fsync(fd)

    monkeypatch.setattr(store_api.os, "fsync", track)
    with pytest.raises(asyncio.CancelledError):
        with store_api.stage(store, io.BytesIO(b"abc"), 3) as staged:
            def cancel_after_link(src, dst, **kwargs):
                assert staged._writer.closed
                assert Path(src).stat().st_ino in synced
                link(src, dst, **kwargs)
                raise asyncio.CancelledError()

            monkeypatch.setattr(store_api.os, "link", cancel_after_link)
            store_api.publish(store, staged)
    assert body_path(store, b"abc").read_bytes() == b"abc"
    assert not list(store.glob(".attachment-*"))


def test_source_io_failure_is_safe_and_cleans(store):
    class Source:
        def read(self, size):
            raise OSError("sensitive source path")

    with pytest.raises(BookflowError) as caught:
        with store_api.stage(store, Source(), 3):
            pytest.fail("failed stream yielded")
    assert caught.value.code == "E_IO"
    assert "sensitive" not in str(caught.value)
    assert list(store.iterdir()) == []
    assert_error("E_IO", lambda: store_api.scan(Source(), 3), "stream_read")
