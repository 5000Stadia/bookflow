"""A report as the CSV file a spreadsheet opens: the one renderer every surface uses.

An accountant asks for the profit and loss as a file; a bank wants the balance sheet. The
workbench's download, `report export` on the CLI and over MCP, and the Python client all come
here, so the same report with the same filters is the same text everywhere.

Three things decide its shape.

*It is the report, not a page of it.* ``read_all`` reads through the report command's own
continuation -- ``PAGE`` rows at a time, following ``next_cursor`` up to ``PAGES`` times -- so
the file carries the rows past the first page. Where the ceiling is reached the preamble says
so in words, because a truncated ledger that does not admit it is worse than a long one.

*No report is named here.* Which reports exist, what columns each one has and how each cell is
written are all read off the registry and off the command's own output model: any command the
registry calls a paged ``report`` exports, its columns are its row model in declaration order,
and a report declared tomorrow exports the day it is declared.

*It runs through ``read``*, a callable ``read(command_name, input, company_id)`` that runs a
registered command as whoever is asking, exactly as ``bookflow.documents.render`` does, so the
acting principal's permissions and company isolation apply exactly as on the report itself.
"""
from __future__ import annotations

import csv
import io
import re
import types
import typing
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from bookflow.core import registry
from bookflow.core.errors import BookflowError

Read = Callable[[str, dict, str], dict]


def _naming():
    """The words a person reads for a report and its columns (the workbench's own naming)."""
    from bookflow.adapters.workbench import naming
    return naming


# One read of the report command, at its own declared maximum, and how many of them the
# file will follow: the same 200 x 50 the customer statement's PDF already follows.
PAGE = 200
PAGES = 50
# How many times the whole traversal is taken again from the first row when a write
# lands in the middle of it. Each attempt is one consistent reading or none.
ATTEMPTS = 3
MEDIA_TYPE = "text/csv; charset=utf-8"
# The CLI's spelling of `report export` on every exportable report, and its generated docs.
CSV_HELP = ("Print the whole report as CSV (every row, all pages) instead of this page as a table; "
               "the same file `report export` and the workbench download produce")
UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
# A cell a spreadsheet would execute rather than read. Only text cells are guarded;
# a money, count or flag cell is typed by the report's own model and never reaches this.
FORMULA = ("=", "+", "-", "@", "\t", "\r")
NUMERIC = re.compile(r"^[-+]?[0-9][0-9,]*(\.[0-9]+)?$")


def is_report(cmd) -> bool:
    """Whether this command is a paged report a page of rows can be read out of.

    Asked of the registry rather than of a list: the four facts below are what the
    export actually relies on, so a command that has them can be exported and a command
    that loses one stops being offered instead of failing halfway through a file.
    """
    return bool(cmd is not None and cmd.noun == "report" and not cmd.is_write
                and not cmd.local_only and cmd.scope == "company"
                and "cursor" in cmd.input_model.model_fields
                and "limit" in cmd.input_model.model_fields
                and "rows" in cmd.output_model.model_fields
                and "metadata" in cmd.output_model.model_fields)


def reports() -> list[str]:
    """The verb of every exportable report, as the registry has them."""
    registry.load_all("report")
    return sorted(cmd.verb for cmd in registry.all_commands() if is_report(cmd))


def _declared(annotation):
    """The declared type with ``| None`` stripped, or None where there is no single one."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        named = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        return named[0] if len(named) == 1 else None
    return annotation


def _is_model(annotation) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _is_money(annotation) -> bool:
    """A money value, recognised by what it carries rather than by its class name."""
    return _is_model(annotation) and {"amount", "currency", "minor_units"} <= set(annotation.model_fields)


def _kind(annotation) -> str:
    """How one declared field of a row is written into cells."""
    declared = _declared(annotation)
    if declared is None:
        return "scalar"
    if _is_money(declared):
        return "money"
    if typing.get_origin(declared) is list:
        args = typing.get_args(declared)
        return "spread" if args and _is_money(_declared(args[0])) else "list"
    if _is_model(declared):
        return "model"
    if declared is dict or typing.get_origin(declared) is dict:
        return "mapping"
    return "scalar"


def row_model(cmd):
    """The model one row of this report is, or None where the report declares none."""
    args = typing.get_args(cmd.output_model.model_fields["rows"].annotation)
    return args[0] if args and _is_model(args[0]) else None


def columns(cmd, result: dict) -> list[tuple[str, str, str]]:
    """One entry per printed column: the row field it reads, how it is written, its heading.

    The order is the row model's own declaration order, which is the order the report's
    author wrote the row in. A report whose rows carry one money value per column of the
    result -- the profit and loss read across jobs or classes -- spreads that one field
    into the columns the result itself names, so the file has a column per job rather
    than one cell holding all of them.
    """
    model = row_model(cmd)
    if model is None:
        return []
    spread = [str(column.get("label") or "") for column in (result.get("columns") or [])]
    found: list[tuple[str, str, str]] = []
    for name, field in model.model_fields.items():
        kind = _kind(field.annotation)
        if kind == "spread" and spread:
            found += [(name, f"spread:{index}", label) for index, label in enumerate(spread)]
        else:
            found.append((name, "list" if kind == "spread" else kind, _naming().column_label(name)))
    # Two fields can read as the same heading -- an amount and the same amount in
    # millionths, a parent's ID and a parent's name -- and two columns headed alike in a
    # spreadsheet is a column a person cannot identify. Where that happens the field's
    # own name is spelled out instead, which is the one thing that is always distinct.
    seen = [heading for _, _, heading in found]
    return [(name, kind, _naming().words(name) if seen.count(heading) > 1 else heading)
            for name, kind, heading in found]


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if value is True or value is False:
        return "yes" if value else "no"
    return str(value)


def _readable(value: Any) -> str:
    """A nested value written so a person reads it in one cell."""
    if isinstance(value, dict):
        if {"amount", "currency", "minor_units"} <= set(value):
            return _plain(value["amount"])
        return " · ".join(f"{_naming().words(key)}: {_readable(item)}"
                          for key, item in value.items() if item not in (None, "", [], {}))
    if isinstance(value, (list, tuple)):
        return " | ".join(_readable(item) for item in value if item not in (None, "", [], {}))
    return _plain(value)


def _guard(text: str) -> str:
    """Neutralise a cell a spreadsheet would run as a formula.

    A memo or a customer name is typed by whoever entered it, and a spreadsheet treats a
    leading ``=`` as a program. A number is left exactly as the report wrote it, so a
    negative amount is still a negative amount.
    """
    if text[:1] in FORMULA and not NUMERIC.fullmatch(text):
        return "'" + text
    return text


def cell(row: dict, name: str, kind: str) -> str:
    value = row.get(name)
    if kind == "money":
        return _plain((value or {}).get("amount"))
    if kind.startswith("spread:"):
        index = int(kind.split(":", 1)[1])
        found = value[index] if isinstance(value, list) and index < len(value) else None
        return _plain((found or {}).get("amount"))
    if kind in ("list", "model", "mapping"):
        return _guard(_readable(value))
    return _guard(_plain(value))


def _totals(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """The report's whole-report totals, flattened to label and figure."""
    if not isinstance(value, dict):
        return [(prefix, _readable(value))] if prefix else []
    if {"amount", "currency", "minor_units"} <= set(value):
        return [(prefix, _plain(value["amount"]))]
    found: list[tuple[str, str]] = []
    for key, item in value.items():
        label = _naming().column_label(key)
        found += _totals(item, f"{prefix} · {label}" if prefix else label)
    return found


def read_all(read, company_id: str, cmd, raw: dict) -> tuple[list[dict], dict, bool]:
    """Every row of the report, followed through its own continuation.

    Returns the rows, the first page's result -- which carries the metadata, the totals
    and the columns the whole report is described by -- and whether the ceiling cut it
    short. The same shape, and the same honesty about the cut, as the statement PDF.

    A report refuses its own continuation when the books move underneath it, and that
    refusal is right: a file whose first half is one state and second half another is
    worth nothing to an accountant. So a write landing mid-traversal is not paged
    through and is not papered over -- everything read so far is thrown away and the
    whole report is taken again from its first row, which is exactly what the refusal
    asks for. The file is one state of the books either way; the preamble names which,
    by the audit watermark of the reading it came from.
    """
    for attempt in range(ATTEMPTS):
        try:
            first = read(cmd.name, {**raw, "limit": PAGE}, company_id)
            rows = list(first["rows"])
            cursor, pages = first.get("next_cursor"), 0
            while cursor and pages < PAGES:
                page = read(cmd.name, {**raw, "limit": PAGE, "cursor": cursor}, company_id)
                rows += page["rows"]
                cursor, pages = page.get("next_cursor"), pages + 1
            return rows, first, bool(cursor)
        except BookflowError as err:
            if err.code != "E_QUERY_STALE" or attempt == ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")


def preamble(title: str, company: str, result: dict, rows: int, truncated: bool) -> list[list[str]]:
    """What the file is, above the table: the same identification the printed page carries."""
    metadata = result.get("metadata") or {}
    period = metadata.get("period") or {}
    lines = [["Report", title], ["Company", company]]
    if period.get("date_from"):
        lines.append(["Period", f"{period['date_from']} to {period.get('date_to', '')}"])
    else:
        lines.append(["As of", _plain(period.get("date_to"))])
    lines += [["Basis", _plain(metadata.get("basis")).capitalize()],
              ["Currency", _plain(metadata.get("currency"))],
              ["Generated", _plain(metadata.get("generation_time"))],
              ["Audit watermark", _plain(metadata.get("audit_watermark"))],
              ["Rows", str(rows)]]
    if truncated:
        lines.append(["Truncated", f"Only the first {rows} rows of this report are in this file. "
                                   "Narrow the dates or the filters and export again to see the rest."])
    return lines


def document(cmd, title: str, company: str, rows: list[dict], result: dict, truncated: bool) -> str:
    """The whole CSV: what the report is, its rows, and its whole-report totals."""
    plan = columns(cmd, result)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    for line in preamble(title, company, result, len(rows), truncated):
        writer.writerow(line)
    writer.writerow([])
    writer.writerow([heading for _, _, heading in plan])
    for row in rows:
        writer.writerow([cell(row, name, kind) for name, kind, _ in plan])
    totals = _totals(result.get("totals"))
    if totals:
        writer.writerow([])
        writer.writerow(["Whole-report totals — all pages"])
        for label, figure in totals:
            writer.writerow([label, figure])
    return buffer.getvalue()


def filename(verb: str, result: dict) -> str:
    period = (result.get("metadata") or {}).get("period") or {}
    stem = "-".join(UNSAFE.sub("-", str(part)).strip("-")
                    for part in (verb, period.get("date_to")) if part)
    return f"{stem or 'report'}.csv"


@dataclass(frozen=True)
class ReportCsv:
    """One report, whole, as CSV text, and the name to save it under."""

    report: str
    title: str
    filename: str
    media_type: str
    rows: int
    truncated: bool
    content: str


def export(read: Read, company_id: str, verb: str, raw: dict) -> ReportCsv:
    """The whole report ``verb`` with filters ``raw``, as the CSV a spreadsheet opens.

    ``raw`` is the report's own input; its continuation and page size are dropped, because
    the file is the whole report from its first row. The text carries no byte order mark:
    a download adds one for spreadsheets, and a terminal or an agent reads plain UTF-8.
    """
    cmd = registry.get(f"report {verb}")
    if not is_report(cmd):
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "report", "problem": f"not an exportable report; choose one of: {', '.join(reports())}"}]})
    raw = {key: value for key, value in (raw or {}).items() if key not in ("cursor", "limit")}
    company = read("company show", {}, company_id)
    rows, result, truncated = read_all(read, company_id, cmd, raw)
    title = _naming().heading("report", verb)
    label = str(company.get("display_name") or company.get("legal_name") or "")
    return ReportCsv(report=verb, title=title, filename=filename(verb, result), media_type=MEDIA_TYPE,
                     rows=len(rows), truncated=truncated, content=document(cmd, title, label, rows, result, truncated))
