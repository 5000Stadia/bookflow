import multiprocessing as mp
import os

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.locks import RootLock


def _hold(root, seconds, started):
    with RootLock(root, "holder"):
        started.set()
        import time
        time.sleep(seconds)


def test_second_process_gets_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("BOOKFLOW_LOCK_TIMEOUT", "0.3")
    started = mp.Event()
    p = mp.Process(target=_hold, args=(tmp_path, 2.0, started))
    p.start()
    try:
        assert started.wait(5)
        with pytest.raises(BookflowError) as e:
            with RootLock(tmp_path, "second"):
                pass
        assert e.value.code == "E_DB_BUSY"
        assert e.value.details["holder"]["command"] == "holder"
        assert e.value.details["holder"]["pid"] == str(p.pid)
    finally:
        p.terminate()
        p.join()


def test_lock_released_and_content_cleared(tmp_path):
    with RootLock(tmp_path, "one") as lk:
        assert lk.holder()["pid"] == str(os.getpid())
    assert (tmp_path / "root.lock").read_text() == ""
    with RootLock(tmp_path, "two"):
        pass
