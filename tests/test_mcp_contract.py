import json
import os
import sys
from pathlib import Path

import anyio
import pytest

from bookflow.adapters.mcp.catalog import command_help, list_commands
from bookflow.adapters.mcp.envelopes import validate
from bookflow.adapters.mcp.files import Directories
from bookflow.adapters.mcp.launcher import host_origin
from bookflow.core.errors import BookflowError


def test_catalog_complete_and_cursor_bound():
    names = []
    page = list_commands(limit=17)
    while True:
        names += [row["name"] for row in page["commands"]]
        if page["next_cursor"] is None:
            break
        page = list_commands(limit=17, cursor=page["next_cursor"])
    assert names == sorted(set(names))
    assert {"token issue", "attachment add", "attachment get", "mcp"} <= set(names)
    with pytest.raises(BookflowError, match="Invalid input"):
        list_commands(cursor="junk")
    assert command_help("attachment add")["transfer"] == "input"
    assert command_help("token issue")["local_only"] is False


@pytest.mark.parametrize("raw", [
    {"command": "account list", "input": None},
    {"command": "account list", "input": {}, "dry_run": None},
    {"command": "account list", "input": {}, "dry_run": 0},
    {"command": "account list", "input": {}, "interface": "gui"},
    {"command": "account list", "input": {}, "input_ref": "x"},
    {"operation_ref": "x", "input_ref": "x", "action": "execute"},
    {"operation_ref": "x", "action": "status", "output_file": "/x"},
])
def test_invalid_envelopes_never_echo_values(raw):
    with pytest.raises(BookflowError) as caught:
        validate("bookflow_run", raw)
    assert caught.value.code in {"E_VALIDATION", "E_USAGE"}
    assert "input_value" not in json.dumps(caught.value.to_dict())


def test_false_and_nested_presence_preserved():
    raw = {"command": "customer update", "input": {"phone": None, "active": False, "tags": []}, "dry_run": False}
    assert validate("bookflow_run", raw).model_dump(exclude_unset=True) == raw


def test_company_fallback_is_calling_machine_read_only(tmp_path, monkeypatch):
    from bookflow.core.company_selection import company_selection
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    assert company_selection("company", login="worker") == (None, "none")
    (tmp_path / "config.toml").write_text('[users.worker]\nuser_id="selection-only"\ndefault_company="saved"\n')
    assert company_selection("company", login="worker") == ("saved", "default")
    monkeypatch.setenv("BOOKFLOW_COMPANY", "environment")
    assert company_selection("company", login="worker") == ("environment", "env")
    assert company_selection("company", "", login="worker") == ("", "option")
    assert company_selection("hub", login="worker") == (None, "none")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.toml"]


def test_shared_context_rejects_active_unsupported_but_accepts_null_false():
    from bookflow.core import registry
    from bookflow.core.context_options import normalize_options
    registry.load_all()
    cmd = registry.get("account list")
    assert normalize_options(cmd, dry_run=False, reason=None)["dry_run"] is False
    for option in ("reason", "directive", "source_ref", "idempotency_key"):
        with pytest.raises(BookflowError) as caught:
            normalize_options(cmd, **{option: ""})
        assert caught.value.code == "E_USAGE"


@pytest.mark.parametrize("value", ["http://example.com", "http://localhost:9", "https://user:secret@example.com", "https://example.com/?secret=x", "file:///etc/passwd", "https://example.com/path"])
def test_origin_rejects_ambient_or_secret_routes(value):
    with pytest.raises(BookflowError) as caught:
        host_origin(value)
    assert value not in str(caught.value)


def test_file_capabilities_no_escape_and_atomic_output(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    inbox.mkdir(mode=0o700)
    outbox.mkdir(mode=0o700)
    source = inbox / "receipt.pdf"
    source.write_bytes(b"receipt" * 10000)
    caps = Directories([str(inbox), str(outbox)])
    try:
        with caps.input(str(source)) as stream:
            assert stream.read() == b"receipt" * 10000
        outside = tmp_path / "outside"
        outside.write_text("private")
        (inbox / "link").symlink_to(outside)
        for path in [inbox / "link", inbox / ".." / "outside", outside]:
            with pytest.raises(BookflowError):
                with caps.input(str(path)):
                    pytest.fail("escaped")
        os.link(source, inbox / "hard")
        with pytest.raises(BookflowError):
            with caps.input(str(source)):
                pytest.fail("accepted hard link")
        target = outbox / "download.pdf"
        with pytest.raises(RuntimeError):
            with caps.output(str(target)) as stream:
                stream.write(b"partial")
                raise RuntimeError("interrupted")
        assert not list(outbox.iterdir())
        with caps.output(str(target)) as stream:
            stream.write(b"complete")
        assert target.read_bytes() == b"complete"
        with pytest.raises(BookflowError):
            with caps.output(str(target)):
                pytest.fail("overwritten")
    finally:
        caps.close()


def test_real_stdio_lists_three_tools_without_reachable_host(tmp_path):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def witness():
        env = {"BOOKFLOW_TOKEN": "not-a-real-credential", "BOOKFLOW_DATA_ROOT": str(tmp_path / "absent")}
        params = StdioServerParameters(command=os.environ.get("BOOKFLOW_MCP_TEST_BINARY", str(Path(sys.executable).with_name("bookflow"))),
                                      args=["mcp", "--url", "http://127.0.0.1:1"], env=env, cwd=str(tmp_path))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                assert {tool.name for tool in result.tools} == {"bookflow_list_commands", "bookflow_help", "bookflow_run"}
                rejected = await session.call_tool("bookflow_run", {"command": "account list", "input": {}, "dry_run": None})
                assert rejected.is_error
                assert rejected.structured_content["code"] == "E_VALIDATION"
                assert "not-a-real-credential" not in str(rejected)
        assert not (tmp_path / "absent").exists()

    anyio.run(witness)
