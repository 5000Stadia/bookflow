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


def test_intermediate_replacement_with_original_final_parent_is_rejected(tmp_path):
    a, old = tmp_path / "a", tmp_path / "old"
    a.mkdir(mode=0o700)
    b = a / "b"
    b.mkdir(mode=0o700)
    caps = Directories([str(tmp_path)])
    try:
        with pytest.raises(BookflowError):
            with caps.output(str(b / "result")) as stream:
                stream.write(b"verified")
                a.rename(old)
                a.mkdir(mode=0o700)
                (old / "b").rename(b)
        assert not list(b.iterdir())
    finally:
        caps.close()


def test_temporary_replacement_at_link_cannot_be_reported_complete(tmp_path, monkeypatch):
    caps = Directories([str(tmp_path)])
    original_link = os.link

    def replace_then_link(source, destination, **kwargs):
        os.unlink(source, dir_fd=kwargs["src_dir_fd"])
        fd = os.open(source, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600,
                     dir_fd=kwargs["src_dir_fd"])
        try:
            os.write(fd, b"replacement")
        finally:
            os.close(fd)
        return original_link(source, destination, **kwargs)

    monkeypatch.setattr(os, "link", replace_then_link)
    try:
        with pytest.raises(BookflowError) as caught:
            with caps.output(str(tmp_path / "result")) as stream:
                stream.write(b"verified")
        assert caught.value.code == "E_IO"
    finally:
        caps.close()


@pytest.mark.parametrize("ancestor", [False, True])
def test_symlink_denial_has_public_permission_classification(tmp_path, ancestor):
    source = tmp_path / "source"
    source.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(tmp_path if ancestor else source)
    caps = Directories([str(tmp_path)])
    try:
        with pytest.raises(BookflowError) as caught:
            with caps.input(str(link / "source" if ancestor else link)):
                pytest.fail("symlink admitted")
        assert caught.value.code == "E_PERMISSION"
        assert caught.value.details == {"stage": "local_file", "reason": "unsafe_file"}
    finally:
        caps.close()
