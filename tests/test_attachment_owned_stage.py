"""Incremental staging remains owned until retryable cleanup succeeds."""

import asyncio
import hashlib
import io
import os
from pathlib import Path

import pytest

from bookflow.company import attachment_store as api
from bookflow.core.errors import BookflowError
from bookflow.core.transfer_resources import TransferLease


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "attachments"
    path.mkdir(mode=0o700)
    return path


def lease_with_releases():
    released = []
    return TransferLease("principal", "company", released.append), released


def assert_error(code, operation):
    with pytest.raises(BookflowError) as caught:
        operation()
    assert caught.value.code == code
    return caught.value


@pytest.mark.parametrize("data", [b"", b"abc", "é📄".encode(), b"x" * 140_000])
def test_incremental_exact_limit_handoff_publication_and_dedup(store, data):
    for newly_published in (True, False):
        lease, released = lease_with_releases()
        owner = api.OwnedStage(store, max(1, len(data)), lease)
        for offset in range(0, len(data), api.CHUNK_SIZE):
            lease.check_io()
            owner.write(data[offset:offset + api.CHUNK_SIZE])
        owner.write(b"")
        staged = owner.complete()
        assert staged.info == api.BodyInfo(hashlib.sha256(data).hexdigest(), len(data))
        assert_error("E_IO", lambda: owner.write(b"x"))
        assert_error("E_IO", owner.complete)
        lease.handoff()
        lease.close()
        assert not staged._writer.closed and staged._path.exists()
        lease.check_start()
        assert api.publish(store, staged).newly_published is newly_published
        lease.finish()
        owner.close()
        lease.finish()
        assert released == [lease]
        assert not list(store.glob(".attachment-*"))
        assert (store / staged.info.sha256[:2] / staged.info.sha256).read_bytes() == data


@pytest.mark.parametrize("chunk,limit,code", [
    (None, 10, "E_VALIDATION"), ("x", 10, "E_VALIDATION"),
    (bytearray(b"x"), 10, "E_VALIDATION"),
    (b"x" * 65_537, 100_000, "E_VALIDATION"),
    (b"abcd", 3, "E_VALUE_RANGE"), ("é".encode(), 1, "E_VALUE_RANGE"),
])
def test_invalid_input_poisoned_until_lease_cleanup(store, chunk, limit, code):
    lease, released = lease_with_releases()
    owner = api.OwnedStage(store, limit, lease)
    assert_error(code, lambda: owner.write(chunk))
    assert_error("E_IO", owner.complete)
    lease.close()
    assert released == [lease] and list(store.iterdir()) == []


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, 100_000_001])
def test_invalid_limit_creates_nothing(store, limit):
    lease, released = lease_with_releases()
    assert_error("E_VALIDATION", lambda: api.OwnedStage(store, limit, lease))
    lease.close()
    assert released == [lease] and list(store.iterdir()) == []


@pytest.mark.parametrize("failure", ["directory", "mkstemp", "fdopen"])
def test_constructor_registers_before_initialization_failure(store, monkeypatch, failure):
    lease, released = lease_with_releases()
    registered = []
    add = lease.add_cleanup

    def register(callback):
        registered.append(callback)
        add(callback)

    def fail(*args, **kwargs):
        assert registered
        raise OSError("private path")

    monkeypatch.setattr(lease, "add_cleanup", register)
    target, name = {"directory": (api, "_directory"),
                    "mkstemp": (api.tempfile, "mkstemp"),
                    "fdopen": (api.os, "fdopen")}[failure]
    monkeypatch.setattr(target, name, fail)
    error = assert_error("E_IO", lambda: api.OwnedStage(store, 3, lease))
    assert "private path" not in str(error)
    lease.close()
    assert released == [lease] and list(store.iterdir()) == []


def test_failed_constructor_retains_raw_descriptor_and_path_for_retry(store, monkeypatch):
    lease, released = lease_with_releases()
    descriptors = []

    def fail_fdopen(fd, *args):
        descriptors.append(fd)
        raise OSError("fdopen")

    def fail_close(fd):
        raise OSError("close")

    with monkeypatch.context() as patch:
        patch.setattr(api.os, "fdopen", fail_fdopen)
        patch.setattr(api.os, "close", fail_close)
        assert_error("E_IO", lambda: api.OwnedStage(store, 3, lease))
        assert_error("E_IO", lease.close)
        assert lease.cleanup_pending and released == []
        assert len(list(store.iterdir())) == 1
        os.fstat(descriptors[0])
    lease.close()
    assert released == [lease] and list(store.iterdir()) == []
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize("after_effect", [False, True])
def test_close_failure_keeps_capacity_and_retries_before_unlink(store, monkeypatch, after_effect):
    lease, released = lease_with_releases()
    owner = api.OwnedStage(store, 3, lease)
    owner.write(b"abc")
    staged = owner.complete()
    writer = owner._writer

    class FlakyWriter:
        calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                if after_effect:
                    writer.close()
                raise OSError("close")
            writer.close()

    wrapper = FlakyWriter()
    owner._writer = wrapper
    assert_error("E_IO", lease.close)
    assert lease.cleanup_pending and released == [] and staged._path.exists()
    assert_error("E_IO", lambda: api.publish(store, staged))
    lease.close()
    assert wrapper.calls == 2 and writer.closed
    assert released == [lease] and list(store.iterdir()) == []


@pytest.mark.parametrize("after_effect", [False, True])
def test_unlink_retry_after_completed_input_preserves_body_and_other_stage(store, monkeypatch, after_effect):
    lease, released = lease_with_releases()
    owner = api.OwnedStage(store, 3, lease)
    staged = owner.read_from(io.BytesIO(b"abc"))
    api.publish(store, staged)
    unrelated = store / ".attachment-unrelated.tmp"
    unrelated.write_bytes(b"keep")
    unlink = Path.unlink
    calls = []

    def flaky(path, **kwargs):
        assert path == staged._path
        calls.append(path)
        if len(calls) == 1:
            if after_effect:
                unlink(path, **kwargs)
            raise OSError("unlink")
        return unlink(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky)
    lease.handoff()
    assert_error("E_IO", lease.finish)
    assert lease.cleanup_pending and released == []
    lease.close()
    assert len(calls) == 1
    lease.finish()
    assert len(calls) == 2 and released == [lease]
    assert unrelated.read_bytes() == b"keep"
    assert (store / staged.info.sha256[:2] / staged.info.sha256).read_bytes() == b"abc"


@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
def test_scan_failure_and_failed_unlink_remain_retryable(store, monkeypatch, failure):
    lease, released = lease_with_releases()
    owner = api.OwnedStage(store, 3, lease)

    class Source:
        def read(self, size):
            owner.write(b"a")
            raise failure("source")

    with pytest.raises(BookflowError if failure is OSError else failure):
        owner.read_from(Source())
    assert_error("E_IO", owner.complete)

    def fail(*args, **kwargs):
        raise OSError("unlink")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        assert_error("E_IO", lease.close)
        assert lease.cleanup_pending and released == []
    lease.close()
    assert released == [lease] and list(store.iterdir()) == []


@pytest.mark.parametrize("data", [b"", b"x" * 70_000, b"x" * 80_000])
def test_read_from_bounds_and_exact_eof(store, data):
    lease, _ = lease_with_releases()
    owner = api.OwnedStage(store, 70_000, lease)

    class Source(io.BytesIO):
        def read(self, size):
            assert 0 < size <= 65_536
            return super().read(size)

    source = Source(data)
    if len(data) > 70_000:
        assert_error("E_VALUE_RANGE", lambda: owner.read_from(source))
        assert source.tell() == 70_001
    else:
        assert owner.read_from(source).info.size_bytes == len(data)
    lease.close()
    assert not source.closed and list(store.iterdir()) == []


def test_cancel_during_eof_read_cannot_complete(store):
    lease, _ = lease_with_releases()
    owner = api.OwnedStage(store, 3, lease)

    class Source:
        def read(self, size):
            lease.cancel()
            return b""

    assert_error("E_IO", lambda: owner.read_from(Source()))
    assert_error("E_IO", owner.complete)
    lease.close()
    assert list(store.iterdir()) == []


def test_complete_failure_retains_cleanup(store, monkeypatch):
    lease, released = lease_with_releases()
    owner = api.OwnedStage(store, 3, lease)

    def fail(*args):
        raise OSError("fstat")

    with monkeypatch.context() as patch:
        patch.setattr(api.os, "fstat", fail)
        assert_error("E_IO", owner.complete)
    lease.close()
    assert released == [lease] and list(store.iterdir()) == []


@pytest.mark.parametrize("result", [None, 1, OSError("write")])
def test_failed_or_short_write_cannot_be_completed(store, result):
    lease, released = lease_with_releases()
    owner = api.OwnedStage(store, 3, lease)
    writer = owner._writer

    class BrokenWriter:
        def write(self, data):
            if isinstance(result, OSError):
                raise result
            return result

        def close(self):
            writer.close()

    owner._writer = BrokenWriter()
    assert_error("E_IO", lambda: owner.write(b"abc"))
    assert_error("E_IO", owner.complete)
    lease.close()
    assert writer.closed and released == [lease]
    assert list(store.iterdir()) == []


def published(store, data):
    lease, _ = lease_with_releases()
    owner = api.OwnedStage(store, max(1, len(data)), lease)
    staged = owner.read_from(io.BytesIO(data))
    api.publish(store, staged)
    lease.close()
    return staged.info, store / staged.info.sha256[:2] / staged.info.sha256


@pytest.mark.parametrize("data", [b"", b"abc", b"x" * 140_000])
def test_open_verified_returns_held_readonly_stream_rewound_and_lease_owned(store, data):
    info, path = published(store, data)
    lease, released = lease_with_releases()
    reader = api.open_verified(store, info)
    lease.add_cleanup(reader.close)
    assert not reader.closed and reader.tell() == 0
    assert not reader.writable()
    if os.name == "posix":
        with pytest.raises(OSError):
            os.write(reader.fileno(), b"x")
        path.unlink()
        path.write_bytes(b"replacement")
    assert reader.read() == data
    lease.close()
    assert reader.closed and released == [lease]


@pytest.mark.parametrize("bad", [
    api.BodyInfo("../" + "a" * 61, 0), api.BodyInfo("A" * 64, 0),
    api.BodyInfo("a" * 63, 0), api.BodyInfo("a" * 64, -1),
    api.BodyInfo("a" * 64, True), api.BodyInfo("a" * 64, 100_000_001),
    api.BodyInfo("a" * 64, 1.5), None,
])
def test_open_verified_rejects_invalid_metadata_before_filesystem(store, monkeypatch, bad):
    def forbidden(*args):
        pytest.fail("invalid metadata reached filesystem")

    monkeypatch.setattr(api, "_directory", forbidden)
    assert_error("E_VALIDATION", lambda: api.open_verified(store, bad))


@pytest.mark.parametrize("corrupt", [b"abd", b"", b"abcd"])
def test_open_verified_closes_on_content_failure(store, monkeypatch, corrupt):
    info, path = published(store, b"abc")
    path.write_bytes(corrupt)
    readers = []
    fdopen = os.fdopen

    def track(*args):
        reader = fdopen(*args)
        readers.append(reader)
        return reader

    monkeypatch.setattr(api.os, "fdopen", track)
    error = assert_error("E_IO", lambda: api.open_verified(store, info))
    assert error.details == {"check": "body_content"}
    assert len(readers) == 1 and readers[0].closed
    assert path.read_bytes() == corrupt


@pytest.mark.parametrize("entry", ["store", "shard", "body"])
def test_open_verified_rejects_symlinks(store, tmp_path, entry):
    info, path = published(store, b"abc")
    selected = {"store": store, "shard": path.parent, "body": path}[entry]
    target = tmp_path / "moved"
    selected.rename(target)
    selected.symlink_to(target, target_is_directory=entry != "body")
    assert_error("E_IO", lambda: api.open_verified(store, info))


@pytest.mark.parametrize("entry", ["store", "shard"])
@pytest.mark.skipif(os.name != "posix", reason="POSIX private directory modes")
def test_open_verified_rejects_nonprivate_directories(store, entry):
    info, path = published(store, b"abc")
    (store if entry == "store" else path.parent).chmod(0o755)
    assert_error("E_IO", lambda: api.open_verified(store, info))


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_open_verified_rejects_absent_or_nonregular_body(store, kind):
    info, path = published(store, b"abc")
    path.unlink()
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO unavailable")
        os.mkfifo(path, 0o600)
    assert_error("E_IO", lambda: api.open_verified(store, info))


@pytest.mark.skipif(os.name != "posix", reason="POSIX O_NOFOLLOW")
def test_open_verified_does_not_follow_body_replaced_with_symlink(store, tmp_path, monkeypatch):
    info, path = published(store, b"abc")
    target = tmp_path / "target"
    target.write_bytes(b"abc")
    open_fd = os.open

    def race(name, flags):
        assert flags & os.O_NOFOLLOW
        path.unlink()
        path.symlink_to(target)
        return open_fd(name, flags)

    monkeypatch.setattr(api.os, "open", race)
    assert_error("E_IO", lambda: api.open_verified(store, info))


def test_open_verified_hash_reads_are_bounded_and_rewound(store, monkeypatch):
    data = b"x" * 140_000
    info, _ = published(store, data)
    fdopen = os.fdopen
    requests = []

    class Reader:
        def __init__(self, held):
            self.held = held

        def __getattr__(self, name):
            return getattr(self.held, name)

        def read(self, size):
            requests.append(size)
            assert 0 < size <= 65_536
            return self.held.read(size)

    monkeypatch.setattr(api.os, "fdopen", lambda *args: Reader(fdopen(*args)))
    reader = api.open_verified(store, info)
    assert reader.tell() == 0 and sum(requests) <= len(data) + 2
    reader.close()


def test_open_verified_rejects_size_before_hash_read(store, monkeypatch):
    info, path = published(store, b"abc")
    with path.open("r+b") as writer:
        writer.truncate(1 << 30)
    fdopen = os.fdopen
    held = []

    class Reader:
        def __init__(self, reader):
            self.reader = reader

        def __getattr__(self, name):
            return getattr(self.reader, name)

        def seek(self, *args):
            pytest.fail("size mismatch reached hash scan")

    def track(*args):
        reader = Reader(fdopen(*args))
        held.append(reader)
        return reader

    monkeypatch.setattr(api.os, "fdopen", track)
    assert_error("E_IO", lambda: api.open_verified(store, info))
    assert held[0].closed
