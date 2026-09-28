"""`report export`: any paged report, whole, as the CSV file the workbench downloads.

The same text on every surface -- CLI, HTTP, MCP, Python and the workbench's own download
link -- because every one of them runs this command, and this command runs
`bookflow.documents.report_csv` over the report as the caller.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Plan, command
from bookflow.core.session import Session


class ReportExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    report: str = Field(description="The report to export, by its name: profit-and-loss, ar-aging, general-ledger, ... "
                                    "(`bookflow report --help` lists them)", min_length=1, max_length=64)
    filters: dict[str, Any] = Field(default_factory=dict, description=(
        "The report's own inputs, exactly as `report <name>` takes them (a JSON object, e.g. "
        "{\"date_from\": \"2026-01-01\", \"date_to\": \"2026-12-31\"}); cursor and limit are ignored, "
        "because the file is the whole report"))


class ReportExportOutput(BaseModel):
    report: str = Field(description="The report exported")
    title: str = Field(description="The report's name as a person reads it")
    filename: str = Field(description="A file name to save it under")
    media_type: str = Field(description="text/csv; charset=utf-8")
    rows: int = Field(description="Data rows in the file")
    truncated: bool = Field(description="True when the report was longer than the file carries; the preamble says so")
    content: str = Field(description=("The CSV text: a preamble (report, company, period, basis, currency, generation "
                                      "time, audit watermark, row count), a blank line, the header row, every row, then "
                                      "the whole-report totals. The workbench download is this text with a byte order mark"))


report_export = command(
    "report export", scope="company", capability="reports", required_role="member",
    description=("Export a whole report as CSV, every row across all pages, exactly as the workbench's download "
                 "produces it. The CLI prints the CSV itself; --json returns it in `content`."),
    input_model=ReportExportInput, output_model=ReportExportOutput, positional=["report"],
    error_codes=["E_VALIDATION", "E_QUERY_STALE"])


@report_export
def plan_report_export(inp: ReportExportInput, ctx: Context, s: Session) -> Plan:
    from bookflow.core import registry
    from bookflow.core.dispatch import run_in_session, validate_input
    from bookflow.documents import report_csv

    def read(name: str, raw: dict, company_id: str) -> dict:
        cmd = registry.get(name)
        try:
            inp_ = validate_input(cmd, raw)
        except BookflowError as err:
            if name != "company show" and err.code == "E_VALIDATION":
                fields = [{**f, "field": "filters." + str(f.get("field"))} for f in (err.details or {}).get("fields", [])]
                raise BookflowError("E_VALIDATION", message=err.message, details={**(err.details or {}), "fields": fields}) from None
            raise
        return run_in_session(cmd, inp_, ctx, s)

    exported = report_csv.export(read, s.company_row["id"], inp.report, dict(inp.filters))
    return Plan(ReportExportOutput(report=exported.report, title=exported.title, filename=exported.filename,
                                   media_type=exported.media_type, rows=exported.rows, truncated=exported.truncated,
                                   content=exported.content))
