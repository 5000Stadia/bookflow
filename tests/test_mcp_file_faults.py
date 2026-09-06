"""Descriptor-owned file delivery under mutation and ancestor replacement."""

import os

import pytest

from bookflow.adapters.mcp.files import Directories
from bookflow.core.errors import BookflowError


def test_mutated_source_is_not_a_sealed_upload(tmp_path):
    source = tmp_path / "receipt.pdf"
    source.write_bytes(b"receipt")
    directories = Directories([str(tmp_path)])
    try:
        with pytest.raises(BookflowError):
            with directories.input(str(source)) as stream:
                assert stream.read() == b"receipt"
                source.write_bytes(b"different receipt")
    finally:
        directories.close()


@pytest.mark.parametrize("replace_with_symlink", [False, True])
def test_output_ancestor_replacement_never_redirects_or_publishes(tmp_path, replace_with_symlink):
    approved = tmp_path / "approved"
    approved.mkdir(mode=0o700)
    parent = approved / "reports"
    parent.mkdir(mode=0o700)
    other = tmp_path / "outside"
    other.mkdir(mode=0o700)
    directories = Directories([str(approved)])
    try:
        with pytest.raises(BookflowError):
            with directories.output(str(parent / "result.json")) as stream:
                stream.write(b'{"complete":true}')
                parent.rename(approved / "moved")
                if replace_with_symlink:
                    parent.symlink_to(other, target_is_directory=True)
                else:
                    parent.mkdir(mode=0o700)
        assert not list(other.iterdir())
        assert not list((approved / "moved").iterdir())
        assert not (parent / "result.json").exists()
    finally:
        directories.close()


def test_nested_exchange_directory_cannot_be_writable_by_other_users(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o700)
    parent.chmod(0o777)
    (parent / "receipt").write_bytes(b"receipt")
    directories = Directories([str(tmp_path)])
    try:
        with pytest.raises(BookflowError) as caught:
            with directories.input(str(parent / "receipt")):
                pytest.fail("unsafe child opened")
        assert caught.value.code == "E_PERMISSION"
        with pytest.raises(BookflowError):
            with directories.output(str(parent / "report")):
                pytest.fail("unsafe child opened")
    finally:
        directories.close()


def test_directory_permission_change_before_publication_removes_owned_partial(tmp_path):
    parent = tmp_path / "reports"
    parent.mkdir(mode=0o700)
    directories = Directories([str(tmp_path)])
    try:
        with pytest.raises(BookflowError):
            with directories.output(str(parent / "result")) as stream:
                stream.write(b"private result")
                parent.chmod(0o777)
        assert not list(parent.iterdir())
    finally:
        directories.close()


def test_fifo_input_is_rejected_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "pipe"
    os.mkfifo(path)
    directories = Directories([str(tmp_path)])
    try:
        with pytest.raises(BookflowError) as caught:
            with directories.input(str(path)):
                pytest.fail("FIFO admitted")
        assert caught.value.code == "E_PERMISSION"
    finally:
        directories.close()
