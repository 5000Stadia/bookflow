"""Transfer documentation follows registry metadata and executable adapter syntax."""

from dataclasses import replace
import io
import json
from pathlib import Path
import re
import shlex
import sys

import pytest

from bookflow.core import registry
from bookflow.documentation import generate as generation
from bookflow.documentation.examples import EXAMPLES

NEW_COMMANDS = (
    "attachment add", "attachment link", "attachment unlink", "attachment list",
    "attachment get", "activity", "company compact",
)


@pytest.mark.parametrize("name", NEW_COMMANDS)
def test_new_cli_examples_parse_to_the_documented_input(name, monkeypatch, tmp_path, capsys):
    from bookflow.adapters.cli.app import main
    from bookflow.core import dispatch

    monkeypatch.chdir(tmp_path)
    (tmp_path / "receipt.pdf").write_bytes(b"%PDF-1.7\nexample\n")
    calls = []

    def execute(cmd, raw, ctx, **kwargs):
        calls.append(cmd.name)
        assert cmd.name == name
        actual = cmd.input_model.model_validate(raw).model_dump()
        expected = cmd.input_model.model_validate(EXAMPLES[name].input).model_dump()
        assert actual == expected
        if name == "activity":
            assert raw["kinds"] == ["note", "attachment"]
        if name == "attachment link":
            assert cmd.positional == ["attachment", "record_type", "record_id"]
            assert raw["record_type"] == "customer"
        assert kwargs["company_selector"] == "Demo Plumbing Co"
        assert not {"path", "out", "input_stream", "output_stream"} & raw.keys()
        if cmd.transfer is not None:
            if cmd.transfer.direction == "input":
                assert kwargs["input_stream"].read(100) == b"%PDF-1.7\nexample\n"
            else:
                assert not (tmp_path / "downloaded-receipt.pdf").exists()
                kwargs["output_stream"].write(b"verified body")
        if name == "company compact":
            assert kwargs["dry_run"] is True
        return {"example_parsed": True}

    monkeypatch.setattr(dispatch, "run", execute)
    monkeypatch.setattr(sys, "argv", shlex.split(EXAMPLES[name].invocation))
    main()
    assert calls == [name]
    assert json.loads(capsys.readouterr().out) == {"example_parsed": True}
    if name == "attachment get":
        assert (tmp_path / "downloaded-receipt.pdf").read_bytes() == b"verified body"


def test_transfer_routes_options_and_headers_follow_descriptor():
    registry.load_all()
    for name, direction, stream, cli_option in (
        ("attachment add", "input", "input_stream", "`PATH`"),
        ("attachment get", "output", "output_stream", "`--out PATH`"),
    ):
        cmd = registry.get(name)
        # Renaming proves the renderer uses the descriptor, not a command-name list.
        projected = replace(cmd, name=f"example {direction}")
        assert generation._http_route(projected) == f"`POST /companies/{{company_id}}/transfers/example.{direction}`"
        assert cli_option in dict(generation._command_options(projected))
        assert f"`{stream}=`" in "\n".join(generation._transfer_section(projected))
        headers = {row[0]: row[2] for row in generation._http_headers(projected)}
        assert "8,192" in headers["`X-Bookflow-Input`"]
        assert "6,144" in headers["`X-Bookflow-Input`"]
        assert ("`Content-Type`" in headers) == (direction == "input")
        ordinary = replace(projected, transfer=None)
        assert "/commands/example." in generation._http_route(ordinary)
        assert generation._transfer_section(ordinary) == []
        assert cli_option not in dict(generation._command_options(ordinary))
        assert "`X-Bookflow-Input`" not in {row[0] for row in generation._http_headers(ordinary)}


def test_generated_transfer_contract_separates_bytes_from_json():
    tree = generation.render_tree()
    page = tree["cli/attachment.md"].decode()
    add = page.split("## `attachment add`", 1)[1].split("\n## `", 1)[0]
    get = page.split("## `attachment get`", 1)[1].split("\n## `", 1)[0]
    for section, direction in ((add, "input"), (get, "output")):
        assert f"| External binary body | {direction} |" in section
        assert "Send the input object as JSON." not in section
        assert "Paths and stream objects are never command JSON fields" in section
        assert "| `X-Bookflow-Input` | required |" in section
    assert "CLI defaults to PATH basename" in add
    assert "CLI guesses MIME type" in add
    assert "?dry_run=true" in add
    assert "publish atomically" in get.lower()
    assert "never overwrite" in get
    for header in ("Content-Disposition", "Content-Length", "Cache-Control", "nosniff", "X-Bookflow-SHA256", "X-Bookflow-Output"):
        assert header in get
    assert "complete typed command output as unpadded base64url UTF-8 JSON" in get
    assert "6,144 decoded bytes and 8,192 encoded bytes" in get
    assert "Python sink can contain partial bytes" in get
    assert "| External binary body | none |" in page
    assert "attachment_max_bytes" in tree["cli/company.md"].decode()
    for table in ("attachments", "attachment_links", "attachment_collection"):
        assert f"schema/company/{table}.md" in tree
    guide = tree["transfers.md"].decode()
    assert "`X-Bookflow-Output` contains the complete typed command output" in guide
    assert "6,144 decoded bytes and 8,192 encoded bytes" in guide
    assert "--kinds '[\"note\",\"attachment\"]'" in guide
    assert "without decoding" not in guide
    assert "**and a successful final response**" in guide
    assert "Body EOF alone is insufficient" in guide
    assert "blocked caller-owned operation cannot be forcibly interrupted" in guide


def test_python_file_examples_execute_on_demo(client, tmp_path, monkeypatch):
    import bookflow

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bookflow, "connect", lambda: client)
    body = b"%PDF-1.7\nDocumentation file example\n%%EOF\n"
    (tmp_path / "receipt.pdf").write_bytes(body)
    guide = generation._resource("transfers.md").decode()
    example = re.findall(r"```python\n(.*?)\n```", guide, re.S)[0]
    namespace = {}
    exec(compile(example, "transfers.md", "exec"), namespace)
    assert (tmp_path / "python-receipt.pdf").read_bytes() == body
    assert namespace["metadata"]["size_bytes"] == len(body)
    readme = Path(__file__).resolve().parents[1] / "README.md"
    example = re.findall(r"```python\n(.*?)\n```", readme.read_text(), re.S)[0]
    exec(compile(example, "README.md", "exec"), namespace)
    assert namespace["result"]["attachment"]["sha256"] == namespace["metadata"]["sha256"]


def test_checked_in_documentation_is_fresh():
    output = Path(__file__).resolve().parents[1] / "docs"
    generation.generate_docs(str(output), True)


def test_http_example_encodes_metadata_and_sends_bounded_raw_chunks(monkeypatch, tmp_path):
    from urllib import request
    from bookflow.core.transfer_protocol import decode_input
    from bookflow.documentation.examples import ID

    monkeypatch.chdir(tmp_path)
    body = bytes(range(256)) * 600
    (tmp_path / "receipt.pdf").write_bytes(body)
    calls = []

    def receive(req, timeout):
        calls.append(req)
        assert req.full_url == f"http://127.0.0.1/companies/{ID}/transfers/attachment.add"
        assert req.method == "POST" and timeout == 30
        assert req.get_header("Content-type") == "application/octet-stream"
        fields = decode_input(req.get_header("X-bookflow-input"))
        assert fields == EXAMPLES["attachment add"].input
        chunks = list(req.data)
        assert all(0 < len(chunk) <= 65_536 for chunk in chunks)
        assert b"".join(chunks) == body
        return io.BytesIO(b'{"attachment": {"id": "example"}}')

    monkeypatch.setattr(request, "urlopen", receive)
    example = re.findall(r"```python\n(.*?)\n```", generation._resource("transfers.md").decode(), re.S)[1]
    namespace = {"company_id": ID, "customer_id": ID, "base_url": "http://127.0.0.1", "token": "example"}
    exec(compile(example, "transfers.md HTTP", "exec"), namespace)
    assert len(calls) == 1
    assert namespace["added"] == {"attachment": {"id": "example"}}
