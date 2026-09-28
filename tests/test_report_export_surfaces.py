"""R145: a report's CSV is the same file on every surface.

The workbench's download, `report export` over the CLI, HTTP and MCP, the Python client and the
CLI's `report <name> --csv` all produce one text for the same report and filters: the
whole report, every page, with its preamble and whole-report totals. Checked for a long,
paged report (the general ledger) and a short one (the trial balance), on identical copies of
the demo company.
"""
import csv
import io
import subprocess

import anyio
import bookflow
import pytest
from fastapi.testclient import TestClient

from tests import provenance
from tests.conftest import BIN
from tests.mcp_matrix_support import Matrix
from tests.test_row3_host import PASSWORD

SURFACES = ('python', 'cli', 'http', 'mcp')
COMMANDS = frozenset(('report export',))
REPORTS = {
    "general-ledger": {"date_from": "2000-01-01", "date_to": "2099-12-31"},
    "trial-balance": {"date_to": "2026-12-31"},
}


def comparable(text):
    """The file without the one line that differs by the instant it was generated."""
    lines = text.split("\r\n")
    generated = [line for line in lines if line.startswith("Generated,")]
    assert len(generated) == 1, lines[:10]
    return [line for line in lines if not line.startswith("Generated,")]


def data_rows(text):
    lines = list(csv.reader(io.StringIO(text)))
    blank = lines.index([])
    rows = []
    for line in lines[blank + 2:]:
        if not line:
            break
        rows.append(line)
    return rows


@pytest.mark.timeout(900)
def test_report_csv_is_the_same_file_on_every_surface(root, tmp_path):
    from bookflow.core.config import os_login
    seed = bookflow.connect(data_root=str(root))
    login = os_login()
    seed.run("user set-password", {"username": login, "password": PASSWORD})

    async def witness():
        matrix = Matrix()
        try:
            await matrix.open(root, tmp_path)
            files = {}
            for verb, filters in REPORTS.items():
                for surface in matrix.documents:
                    document = await matrix.call(surface, "report export", {"report": verb, "filters": filters})
                    assert document["media_type"] == "text/csv; charset=utf-8" and document["report"] == verb
                    files[(verb, surface)] = document
            # The MCP result is the command's own document, carried as text content.
            reply = await matrix.mcp.call_tool("bookflow_run", {"command": "report export", "company": matrix.company,
                                                                "input": {"report": "trial-balance",
                                                                          "filters": REPORTS["trial-balance"]}})
            assert not reply.is_error and reply.content[0].type == "text"
            assert "Report,Trial balance" in reply.structured_content["content"]
            assert "Trial balance" in reply.content[0].text

            browser = TestClient(matrix.hosts["http"].handle.app)
            assert browser.post("/login", json={"username": login, "password": PASSWORD}).status_code == 200
            for verb, filters in REPORTS.items():
                python = files[(verb, "python")]
                for surface in ("cli", "http", "mcp"):
                    other = files[(verb, surface)]
                    assert comparable(other["content"]) == comparable(python["content"]), (verb, surface)
                    assert (other["rows"], other["truncated"], other["filename"]) == (
                        python["rows"], python["truncated"], python["filename"])
                download = browser.get(f"/c/{matrix.company}/report/{verb}/export.csv",
                                       params={"f:" + key: value for key, value in filters.items()})
                assert download.status_code == 200 and download.headers["content-type"].startswith("text/csv")
                body = download.content.decode("utf-8")
                assert body.startswith("﻿"), "the download keeps its byte order mark for spreadsheets"
                assert comparable(body[1:]) == comparable(python["content"]), verb
                assert f'filename="{python["filename"]}"' in download.headers["content-disposition"]

                # The CLI's own spelling on the report command prints the same text, raw.
                flags = [part for key, value in filters.items() for part in ("--" + key.replace("_", "-"), value)]
                printed = subprocess.run([str(BIN), "report", verb, *flags, "--csv", "--company", matrix.company],
                                         capture_output=True, timeout=provenance.CHILD_SECONDS,
                                         env=provenance.child_env(BOOKFLOW_DATA_ROOT=str(matrix.roots["cli"])))
                assert printed.returncode == 0, printed.stderr
                assert comparable(printed.stdout.decode("utf-8")) == comparable(python["content"]), verb

            ledger = files[("general-ledger", "python")]
            assert ledger["rows"] > 200 and not ledger["truncated"], "the long report crosses its page size"
            assert len(data_rows(ledger["content"])) == ledger["rows"]
        finally:
            await matrix.close()

    anyio.run(witness)


def test_export_refuses_a_name_that_is_not_a_report_and_names_bad_filters(client):
    for report, problem in (("export", "not an exportable report"), ("nonsense", "not an exportable report")):
        with pytest.raises(bookflow.BookflowError) as caught:
            client.run("report export", {"report": report}, company="Demo Plumbing Co")
        assert caught.value.code == "E_VALIDATION" and problem in caught.value.details["fields"][0]["problem"]
    with pytest.raises(bookflow.BookflowError) as caught:
        client.run("report export", {"report": "trial-balance", "filters": {"bogus": 1}}, company="Demo Plumbing Co")
    fields = {f["field"] for f in caught.value.details["fields"]}
    assert "filters.bogus" in fields and "filters.date_to" in fields
