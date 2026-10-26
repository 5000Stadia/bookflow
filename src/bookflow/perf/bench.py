"""The bench: a fixed scenario set timed on a fresh copy of a generated company, stored as a named run.

Every timed call goes through the Python client, the full offline path a CLI call takes (lock,
hub open, actor, execute) minus interpreter start. Each scenario runs one untimed warm-up where
it can, then `repeat` timed repetitions. The statement count of each call comes from the copy's
own timings store (core/timings.py), so it is the same instrument users have.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from bookflow.perf import generate as gen
from bookflow.perf.store import percentile

DEFAULT_REPEAT = 5


@dataclass(frozen=True)
class Scenario:
    name: str
    group: str  # read, write, hard, admin
    command: str
    repeat: int | None = None  # None: the bench's --repeat
    warm: bool = True


SCENARIOS = (
    Scenario("home", "read", "company show"),
    Scenario("register", "read", "register query"),
    Scenario("customer list", "read", "customer list"),
    Scenario("item list", "read", "item list"),
    Scenario("A/R aging", "read", "report ar-aging"),
    Scenario("profit and loss, 5 years", "read", "report profit-and-loss"),
    Scenario("balance sheet", "read", "report balance-sheet"),
    Scenario("invoice post", "write", "invoice post", warm=False),
    Scenario("bill post", "write", "bill post", warm=False),
    Scenario("payment receive", "write", "payment receive", warm=False),
    Scenario("deposit post, 20 lines", "write", "deposit post", warm=False),
    Scenario("deposit sources, first page", "hard", "deposit sources"),
    Scenario("deposit post, all undeposited", "hard", "deposit post", repeat=3, warm=False),
    Scenario("deposit void, all undeposited", "hard", "deposit void", repeat=3, warm=False),
    Scenario("reconcile start", "hard", "reconcile start", repeat=1, warm=False),
    Scenario("reconcile candidates, first page", "hard", "reconcile candidates"),
    Scenario("reconcile mark all", "hard", "reconcile mark", warm=False),
    Scenario("reconcile preview", "hard", "reconcile preview"),
    Scenario("membership grant, organisation", "admin", "membership grant", repeat=3, warm=False),
    Scenario("membership revoke, organisation", "admin", "membership revoke", repeat=3, warm=False),
)


class _Bench:
    def __init__(self, root: Path, manifest: dict, say: Callable[[str], None]):
        import bookflow
        from bookflow.core import timings
        self.root, self.m, self.say, self.timings = root, manifest, say, timings
        self.ids = manifest["ids"]
        self.client = bookflow.connect(data_root=str(root))
        self.company = manifest["company_name"]
        self.serial = 0
        self.state: dict[str, Any] = {}

    def key(self, prefix: str) -> str:
        self.serial += 1
        return f"bench-{prefix}-{self.serial}"

    def run(self, command: str, body: dict, *, hub: bool = False, dry_run: bool = False) -> dict:
        from bookflow.core import registry
        reason = "Performance bench" if registry.get(command).is_write else None
        return self.client.run(command, body, company=None if hub else self.company, reason=reason,
                               dry_run=dry_run)

    def timed(self, command: str, body: dict, *, hub: bool = False) -> tuple[float, dict, dict | None]:
        start = time.perf_counter()
        out = self.run(command, body, hub=hub)
        ms = (time.perf_counter() - start) * 1000
        return ms, out, self._last_row(command)

    def _last_row(self, command: str) -> dict | None:
        import sqlite3
        from bookflow.perf import store
        self.timings.flush()
        p = store.path(self.root)
        if not p.exists():
            return None
        db = sqlite3.connect(p)
        try:
            row = db.execute("SELECT wall_ms, sql_count, sql_ms, rows_in, rows_out FROM calls WHERE command=? "
                             "ORDER BY rowid DESC LIMIT 1", (command,)).fetchone()
        finally:
            db.close()
        return dict(zip(("wall_ms", "sql_count", "sql_ms", "rows_in", "rows_out"), row)) if row else None

    # ------------------------------------------------------------ scenario bodies

    def body(self, s: Scenario, rep: int) -> tuple[dict, bool]:
        """The input for one call of scenario `s` (rep -1 is the warm-up), and whether it is hub scope."""
        i, day = self.ids, self.ids["last_day"]
        start = self.m["history"][0] + "-01"
        name = s.name
        if name == "home":
            return {}, False
        if name == "register":
            return {"account": i["checking"], "limit": 50, "direction": "desc"}, False
        if name in ("customer list", "item list"):
            return {}, False
        if name == "A/R aging":
            return {"as_of": day}, False
        if name == "profit and loss, 5 years":
            return {"date_from": start, "date_to": day}, False
        if name == "balance sheet":
            return {"date_to": day}, False
        if name == "invoice post":
            return {"date": day, "number": self.key("inv"), "customer": i["customer"],
                    "lines": [{"item": i["item"], "quantity": "2"}, {"item": i["item"], "quantity": "1"}]}, False
        if name == "bill post":
            return {"date": day, "number": self.key("bill"), "vendor": i["vendor"],
                    "expenses": [{"account": i["expense"], "amount": "412.50"}]}, False
        if name == "payment receive":
            return self._payment(day), False
        if name == "deposit post, 20 lines":
            sources = []
            for _ in range(20):
                out = self.run("payment receive", self._payment(day))
                sources.append({"source_type": "payment", "source": out["id"], "expected_version": out["version"]})
            return self._deposit(sources, day), False
        if name == "deposit sources, first page":
            return {"date": day, "limit": 200}, False
        if name == "deposit post, all undeposited":
            return self._deposit(self._all_sources(day), day), False
        if name == "deposit void, all undeposited":
            dep = self.state.pop("big_deposit")
            void = {"deposit": dep["id"], "expected_version": dep["version"], "operation_key": self.key("void")}
            preview = self.run("deposit void", void, dry_run=True)  # the guard a person's preview shows
            return {**void, "dependency_guard": preview["dependency_guard"]}, False
        if name == "reconcile start":
            # The account was never reconciled: open it before its first movement (nothing to tick),
            # so the statement reconciliation sees every deposit and bill payment uncleared.
            opening = self.run("reconcile opening start", {
                "operation_key": self.key("open"), "account": i["checking"], "opening_date": start,
                "entered_balance": "0.00", "references": [],
                "evidence": {"format": 1, "statement_reference": None, "entered_text": "Bench opening"}})["draft"]
            return {"operation_key": self.key("rec"), "account": i["checking"], "statement_date": day,
                    "ending_balance": "0.00", "opening_draft_id": opening["id"]}, False
        if name == "reconcile candidates, first page":
            return {"draft": self.state["draft"]["id"], "limit": 200}, False
        if name == "reconcile mark all":
            draft = self.state["draft"]
            return {"draft": draft["id"], "operation_key": self.key("mark"), "expected_version": draft["version"],
                    "all": True, "all_action": "mark" if rep % 2 == 0 else "unmark"}, False
        if name == "reconcile preview":
            draft = self.state["draft"]
            return {"draft": draft["id"], "expected_version": draft["version"]}, False
        if name == "membership grant, organisation":
            return {"user": self.m["member"], "organization": self.m["group_organization"], "role": "standard"}, True
        if name == "membership revoke, organisation":
            return {"user": self.m["member"], "organization": self.m["group_organization"]}, True
        raise KeyError(name)

    def _payment(self, day: str) -> dict:
        return {"customer": self.ids["busy_customer"], "date": day, "amount": "37.25", "number": self.key("chk"),
                "payment_method": self.ids["payment_method"], "deposit_to": self.ids["undeposited"],
                "operation_key": self.key("pay"), "applications": {"mode": "inline", "items": []}}

    def _deposit(self, sources: list, day: str) -> dict:
        return {"operation_key": self.key("dep"), "document": {
            "mode": "inline", "deposit_to": self.ids["checking"], "date": day, "number": self.key("D"),
            "sources": sources}}

    def _all_sources(self, day: str) -> list:
        rows, cursor = [], None
        while True:
            page = self.run("deposit sources", {"date": day, "limit": 200, **({"cursor": cursor} if cursor else {})})
            rows += [{k: v[k] for k in ("source", "source_type", "expected_version")} for v in page["items"]]
            cursor = page.get("next_cursor")
            if not cursor:
                return rows

    def after(self, s: Scenario, rep: int, out: dict) -> None:
        """Carry what the next call needs (the deposit to void, the draft and its version)."""
        if s.name == "deposit post, all undeposited":
            self.state["big_deposit"] = out["deposit"]
            self.state["big_lines"] = len(out.get("receipts") or [])
        elif s.name == "deposit void, all undeposited" and rep + 1 < self._reps(s):
            pass
        elif s.name == "reconcile start":
            self.state["draft"] = out["draft"]
        elif s.name == "reconcile mark all":
            self.state["draft"] = out.get("draft") or self.state["draft"]
        elif s.name == "reconcile preview":
            self.state["preview_totals"] = out.get("totals")

    def _reps(self, s: Scenario) -> int:
        return s.repeat if s.repeat is not None else self.repeat


def _interleaved(selected: list[Scenario]) -> list[list[Scenario]]:
    """Run post/void (and grant/revoke) pairs alternately, so each post has sources to take."""
    by = {s.name: s for s in selected}
    order, done = [], set()
    pairs = {"deposit post, all undeposited": "deposit void, all undeposited",
             "membership grant, organisation": "membership revoke, organisation"}
    for s in selected:
        if s.name in done:
            continue
        partner = pairs.get(s.name)
        if partner and partner in by:
            order.append([s, by[partner]])
            done.update((s.name, partner))
        elif s.name in pairs.values() and any(k in by for k, v in pairs.items() if v == s.name):
            continue
        else:
            order.append([s])
            done.add(s.name)
    return order


def run_bench(template: Path, *, name: str, repeat: int = DEFAULT_REPEAT, only: list[str] | None = None,
              work_dir: Path | None = None, say: Callable[[str], None] | None = None) -> dict:
    """Copy the template's root, run the scenarios, delete the copy, and return the run document."""
    say = say or (lambda _m: None)
    template = Path(template)
    manifest = gen.load(template)
    base = Path(tempfile.mkdtemp(prefix="bookflow-bench-", dir=str(work_dir) if work_dir else None))
    root = base / "root"
    started = time.perf_counter()
    results: dict[str, Any] = {}
    previous_root = os.environ.get("BOOKFLOW_DATA_ROOT")
    previous_perf = os.environ.pop("BOOKFLOW_PERF", None)
    try:
        gen.copy_root(template, root)
        os.environ["BOOKFLOW_DATA_ROOT"] = str(root)
        b = _Bench(root, manifest, say)
        b.repeat = repeat
        selected = [s for s in SCENARIOS if not only or s.name in only or s.command in only]
        for group in _interleaved(selected):
            reps = max(b._reps(s) for s in group)
            samples: dict[str, list] = {s.name: [] for s in group}
            for s in group:
                if s.warm:
                    body, hub = b.body(s, -1)
                    b.after(s, -1, b.run(s.command, body, hub=hub))
            for rep in range(reps):
                for s in group:
                    if rep >= b._reps(s):
                        continue
                    body, hub = b.body(s, rep)
                    ms, out, row = b.timed(s.command, body, hub=hub)
                    b.after(s, rep, out)
                    samples[s.name].append((ms, row))
            for s in group:
                rows = samples[s.name]
                walls = [r[0] for r in rows]
                inner = [r[1] for r in rows if r[1]]
                results[s.name] = {
                    "group": s.group, "command": s.command, "reps": len(walls),
                    "ms": [round(w, 1) for w in walls],
                    "p50_ms": round(percentile(walls, 0.5), 1), "p95_ms": round(percentile(walls, 0.95), 1),
                    "min_ms": round(min(walls), 1), "max_ms": round(max(walls), 1),
                    "execute_p50_ms": round(percentile([r["wall_ms"] for r in inner], 0.5), 1) if inner else None,
                    "sql_count": inner[-1]["sql_count"] if inner else None,
                    "sql_ms": round(percentile([r["sql_ms"] for r in inner], 0.5), 1) if inner else None,
                    "rows": max([(r["rows_in"] or 0) for r in inner] + [(r["rows_out"] or 0) for r in inner]) if inner else None,
                    "load_1m": _load(),
                }
                say(f"{s.name}: p50 {results[s.name]['p50_ms']} ms, {results[s.name]['sql_count']} statements, load {results[s.name]['load_1m']}")
    finally:
        if previous_root is None:
            os.environ.pop("BOOKFLOW_DATA_ROOT", None)
        else:
            os.environ["BOOKFLOW_DATA_ROOT"] = previous_root
        if previous_perf is not None:
            os.environ["BOOKFLOW_PERF"] = previous_perf
        shutil.rmtree(base, ignore_errors=True)
    from bookflow.core.context import client_version
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = None
    return {
        "name": name, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bookflow": client_version(), "template": manifest["template"], "profile": manifest["profile"]["name"],
        "counts": manifest["counts"], "machine": {"cpus": os.cpu_count(), "load_1m": load,
                                                  "python": platform.python_version()},
        "repeat": repeat, "seconds": round(time.perf_counter() - started, 1), "scenarios": results,
    }


def _load():
    try:
        return round(os.getloadavg()[0], 1)
    except OSError:
        return None


# ---------------------------------------------------------------- stored runs and comparison

def save(run: dict, store_dir: Path) -> Path:
    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = store_dir / f"{run['name']}.json"
    target.write_text(json.dumps(run, indent=2, sort_keys=True))
    return target


def load_run(reference: str, store_dir: Path) -> dict:
    p = Path(reference)
    if not p.suffix:
        p = Path(store_dir) / f"{reference}.json"
    return json.loads(p.read_text())


def compare(run: dict, baseline: dict, *, threshold: float = 0.25, floor_ms: float = 20.0) -> dict:
    """Each scenario in both: the p50 change, and a regression flag when it is slower by more than
    `threshold` (a fraction) and by more than `floor_ms`."""
    rows = []
    for name, now in run["scenarios"].items():
        was = baseline["scenarios"].get(name)
        if was is None:
            continue
        delta = now["p50_ms"] - was["p50_ms"]
        ratio = (now["p50_ms"] / was["p50_ms"]) if was["p50_ms"] else None
        rows.append({"scenario": name, "baseline_p50_ms": was["p50_ms"], "run_p50_ms": now["p50_ms"],
                     "change_ms": round(delta, 1), "change_pct": round((ratio - 1) * 100, 1) if ratio else None,
                     "baseline_sql": was.get("sql_count"), "run_sql": now.get("sql_count"),
                     "regression": bool(ratio and ratio > 1 + threshold and delta > floor_ms),
                     "improvement": bool(ratio and ratio < 1 / (1 + threshold) and -delta > floor_ms)})
    return {"run": run["name"], "baseline": baseline["name"], "threshold_pct": round(threshold * 100, 1),
            "run_load_1m": (run.get("machine") or {}).get("load_1m"),
            "baseline_load_1m": (baseline.get("machine") or {}).get("load_1m"),
            "floor_ms": floor_ms, "items": rows, "regressions": sum(r["regression"] for r in rows)}
