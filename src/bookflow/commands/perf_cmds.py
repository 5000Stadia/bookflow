"""Performance tracking (R180): `perf report` and `perf settings` read and switch the installation's
timings store; `perf generate`, `perf bench` and `perf compare` are local operator tools.

The timings store is telemetry beside the hub, never part of the books (design/performance.md).
`perf settings` therefore writes no book or hub database and has no audit event: it flips one
flag in the store and an installation administrator is the only one who may.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.lazy import lazy
from bookflow.core.registry import Plan, command
from bookflow.core.session import Session

store = lazy("bookflow.perf.store")
co = lazy("bookflow.hub.companies")

Surface = Literal["cli", "python", "http", "mcp", "workbench", "system"]
_SINCE = re.compile(r"^(\d{1,4})([hd])$")


def _since(value: str, now: float | None = None) -> int:
    now = time.time() if now is None else now
    m = _SINCE.match(value)
    if m:
        return int(now - int(m[1]) * (3600 if m[2] == "h" else 86400))
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        raise BookflowError("E_VALIDATION", details={"fields": [
            {"field": "since", "problem": "use a date (2026-10-01), a date and time, or 24h / 7d"}]})
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp())


class PerfReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    since: str = Field("7d", max_length=40, description="Window start: a date, a date and time (UTC unless given), or 24h / 7d")
    command: str | None = Field(None, max_length=80, description="Only this command, e.g. `invoice post`")
    surface: Surface | None = Field(None, description="Only calls through this surface")
    limit: int = Field(50, ge=1, le=500, description="Most commands to list, slowest p95 first")
    slowest: int = Field(10, ge=0, le=100, description="How many of the slowest single calls to list")


class PerfCommandRow(BaseModel):
    command: str
    surface: str
    count: int
    errors: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    total_ms: float
    mean_sql: float = Field(description="Mean SQL statements per call")
    max_rows: int = Field(description="Longest list a call took in or gave back (lines, sources, items)")


class PerfCall(BaseModel):
    at: str
    command: str
    surface: str
    principal: str
    outcome: str
    wall_ms: float
    sql_count: int
    sql_ms: float
    rows_in: int | None
    rows_out: int | None
    size_class: str | None = Field(description="Company database size bucket")


class PerfReportOutput(BaseModel):
    since: str = Field(description="Window start, UTC")
    scope: Literal["installation", "visible companies"]
    recording: bool
    calls: int
    items: list[PerfCommandRow]
    slowest: list[PerfCall]


perf_report = command(
    "perf report", scope="hub", capability="hub",
    description="Report how long commands take: p50, p95 and max per command and surface over a window, "
                "and the slowest single calls. Times are inside the command (validation through the "
                "response), with SQL statement counts. Nothing about the books' content is recorded.",
    authorization="any signed-in person or agent; installation administrators see every company, "
                  "others only calls on companies they can see",
    input_model=PerfReportInput, output_model=PerfReportOutput, error_codes=["E_VALIDATION"])


@perf_report
def plan_perf_report(inp: PerfReportInput, ctx: Context, s: Session) -> Plan:
    from bookflow.core import timings
    since = _since(inp.since)
    timings.flush()
    companies = None
    if not s.is_hub_admin:
        companies = store.company_hashes(s.data_root, [r["id"] for r in co.list_visible(s)])
    out = store.report(s.data_root, since=since, command=inp.command, surface=inp.surface,
                       companies=companies, limit=inp.limit, slowest=inp.slowest)
    return Plan(preview=PerfReportOutput(
        since=datetime.fromtimestamp(since, timezone.utc).isoformat(timespec="seconds"),
        scope="installation" if companies is None else "visible companies",
        recording=store.enabled(s.data_root), calls=out["calls"],
        items=out["items"], slowest=out["slowest"]))


class PerfSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    recording: Literal["on", "off"] | None = Field(None, description="Turn recording on or off; omit to show the settings")


class PerfSettingsOutput(BaseModel):
    recording: bool
    environment_off: bool = Field(description="BOOKFLOW_PERF=off is set for this process")
    path: str
    bytes: int = Field(description="Store size on disk, with its write-ahead log")
    raw_rows: int
    daily_rows: int
    raw_days: int = Field(description="Raw calls are kept this many days")
    raw_cap: int = Field(description="and at most this many rows")
    daily_days: int = Field(description="Daily summaries are kept this many days")


perf_settings = command(
    "perf settings", scope="hub", capability="company", required_role="hub_admin",
    description="Show the timings store (size, rows, retention) or turn recording on or off. The store "
                "is telemetry kept beside the hub, never in the books; turning it off stops new rows "
                "within two seconds and keeps what is recorded.",
    input_model=PerfSettingsInput, output_model=PerfSettingsOutput)


@perf_settings
def plan_perf_settings(inp: PerfSettingsInput, ctx: Context, s: Session) -> Plan:
    from bookflow.core import timings
    if s.dry_run:
        raise BookflowError("E_USAGE", message="`perf settings` does not take --dry-run.")
    if inp.recording is not None:
        timings.flush()
        store.set_enabled(s.data_root, inp.recording == "on")
    info = store.info(s.data_root)
    return Plan(preview=PerfSettingsOutput(
        recording=info["enabled"], environment_off=timings.disabled_by_env(), path=s.rel_path(Path(info["path"])),
        **{k: info[k] for k in ("bytes", "raw_rows", "daily_rows", "raw_days", "raw_cap", "daily_days")}))


# ---------------------------------------------------------------- local operator tools

def _default_store() -> Path:
    from bookflow.storage.paths import resolve_data_root
    return resolve_data_root(None) / "telemetry" / "bench"


class PerfGenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    output: str = Field(min_length=1, description="Directory to hold templates; the template is a new folder in it")
    profile: Literal["large", "small", "tiny"] = Field("large", description="large: 5 years, ~18,000 documents; small: a tenth; tiny: seconds, for tests")
    seed: int = Field(180, ge=0, description="Random seed; the same seed and profile post the same history")


class PerfGenerateOutput(BaseModel):
    template: str
    reused: bool = Field(description="A template with this profile, seed and schema already existed")
    generation_seconds: float
    disk_bytes: int
    counts: dict[str, int]


def run_perf_generate(_cmd, inp: PerfGenerateInput, _ctx: Context) -> dict:
    from bookflow.perf import generate as gen
    target = Path(inp.output).expanduser() / gen.template_name(inp.profile, inp.seed)
    reused = (target / "manifest.json").exists()  # otherwise a killed run's checkpoint is resumed
    manifest = gen.load(target) if reused else gen.generate(target, inp.profile, inp.seed)
    return PerfGenerateOutput(template=str(target), reused=reused, generation_seconds=manifest["generation_seconds"],
                              disk_bytes=manifest["disk_bytes"], counts=manifest["counts"]).model_dump(mode="json")


class PerfBenchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    template: str = Field(min_length=1, description="A template folder from `perf generate`; the bench works on a copy")
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$", description="Name to store the run under")
    repeat: int = Field(5, ge=1, le=50, description="Timed repetitions per scenario (some heavy ones run fewer)")
    only: list[str] = Field(default_factory=list, description="Run only these scenarios (by name or command)")
    store: str | None = Field(None, description="Directory for stored runs; defaults to <data root>/telemetry/bench")
    work_dir: str | None = Field(None, description="Where the working copy goes; defaults to the system temporary directory")


class PerfScenario(BaseModel):
    scenario: str
    group: str
    command: str
    reps: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    sql_count: int | None
    rows: int | None


class PerfBenchOutput(BaseModel):
    name: str
    path: str
    seconds: float
    items: list[PerfScenario]


def run_perf_bench(_cmd, inp: PerfBenchInput, _ctx: Context) -> dict:
    from bookflow.perf import bench
    template = Path(inp.template).expanduser()
    if not (template / "manifest.json").exists():
        raise BookflowError("E_USAGE", message=f"{template} is not a `perf generate` template.")
    run = bench.run_bench(template, name=inp.name, repeat=inp.repeat, only=inp.only or None,
                          work_dir=Path(inp.work_dir).expanduser() if inp.work_dir else None)
    path = bench.save(run, Path(inp.store).expanduser() if inp.store else _default_store())
    return PerfBenchOutput(name=run["name"], path=str(path), seconds=run["seconds"], items=[
        PerfScenario(scenario=k, **{f: v[f] for f in ("group", "command", "reps", "p50_ms", "p95_ms", "max_ms",
                                                      "sql_count", "rows")})
        for k, v in run["scenarios"].items()]).model_dump(mode="json")


class PerfCompareInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    run: str = Field(min_length=1, description="A stored run's name, or a run file")
    baseline: str = Field(min_length=1, description="The baseline run's name, or a run file")
    threshold: float = Field(25.0, gt=0, le=1000, description="Flag a scenario whose p50 is slower by more than this percent")
    floor_ms: float = Field(20.0, ge=0, description="and by more than this many milliseconds")
    store: str | None = Field(None, description="Directory of stored runs; defaults to <data root>/telemetry/bench")


class PerfCompareRow(BaseModel):
    scenario: str
    baseline_p50_ms: float
    run_p50_ms: float
    change_ms: float
    change_pct: float | None
    baseline_sql: int | None
    run_sql: int | None
    regression: bool
    improvement: bool


class PerfCompareOutput(BaseModel):
    run: str
    baseline: str
    threshold_pct: float
    floor_ms: float
    regressions: int
    items: list[PerfCompareRow]


def run_perf_compare(_cmd, inp: PerfCompareInput, _ctx: Context) -> dict:
    from bookflow.perf import bench
    where = Path(inp.store).expanduser() if inp.store else _default_store()
    try:
        run, baseline = bench.load_run(inp.run, where), bench.load_run(inp.baseline, where)
    except (OSError, ValueError) as e:
        raise BookflowError("E_USAGE", message=f"cannot read a stored run: {e}")
    return PerfCompareOutput(**bench.compare(run, baseline, threshold=inp.threshold / 100,
                                             floor_ms=inp.floor_ms)).model_dump(mode="json")


def _never(inp, ctx, s) -> Plan:  # standalone runners own these commands
    raise NotImplementedError


perf_generate = command(
    "perf generate", scope="hub",
    description="Build the large bench company (five years of demo-style history posted through the ordinary "
                "commands) as a reusable template. Local operator tool: it never opens your books.",
    input_model=PerfGenerateInput, output_model=PerfGenerateOutput, bootstrap=True, local_only=True,
    standalone_runner=run_perf_generate)(_never)
perf_bench = command(
    "perf bench", scope="hub",
    description="Time the fixed scenario set (everyday reads and writes, the 1,000-row deposit and "
                "reconciliation, the organisation-wide membership grant) on a copy of a template, and store "
                "the run under a name. Local operator tool.",
    input_model=PerfBenchInput, output_model=PerfBenchOutput, bootstrap=True, local_only=True,
    standalone_runner=run_perf_bench)(_never)
perf_compare = command(
    "perf compare", scope="hub",
    description="Compare a stored bench run with a baseline and flag scenarios that got slower beyond a "
                "threshold. Local operator tool.",
    input_model=PerfCompareInput, output_model=PerfCompareOutput, bootstrap=True, local_only=True,
    standalone_runner=run_perf_compare)(_never)
