from pathlib import Path

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.fs import _linux_fs_type, check_local, fs_type_of

MOUNTINFO = """\
22 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw
40 22 0:35 / /mnt/share rw - nfs4 server:/export rw
41 22 0:36 / /mnt/with\\040space rw - cifs //srv/s rw
42 22 0:37 / /tmp rw - tmpfs tmpfs rw
"""


def test_longest_prefix_and_unescape():
    assert _linux_fs_type(Path("/home/k/x"), MOUNTINFO) == "ext4"
    assert _linux_fs_type(Path("/mnt/share/acme"), MOUNTINFO) == "nfs4"
    assert _linux_fs_type(Path("/mnt/with space/acme"), MOUNTINFO) == "cifs"
    assert _linux_fs_type(Path("/mnt/sharex"), MOUNTINFO) == "ext4"
    assert _linux_fs_type(Path("/tmp/a"), MOUNTINFO) == "tmpfs"


def test_check_local_codes():
    assert check_local(Path("/x"), fs_type="ext4") == "ext4"
    with pytest.raises(BookflowError) as e:
        check_local(Path("/x"), fs_type="nfs4")
    assert e.value.code == "E_NETWORK_SHARE"
    with pytest.raises(BookflowError) as e:
        check_local(Path("/x"), fs_type="fuse.sshfs")
    assert e.value.code == "E_NETWORK_SHARE"
    with pytest.raises(BookflowError) as e:
        check_local(Path("/x"), fs_type=None) if False else check_local(Path("/nonexistent-root-xyz"), fs_type="")
    assert e.value.code == "E_NETWORK_SHARE"


def test_real_tmp_is_local(tmp_path):
    t = fs_type_of(tmp_path)
    assert t is not None
    check_local(tmp_path / "not-yet-created")
