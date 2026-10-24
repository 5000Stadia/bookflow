"""A deterministic large demo company with years of history, posted through the ordinary commands.

Every document goes through `dispatch.execute` in one held session (one root lock, one hub
open), so the books' rules hold exactly as for a person posting the same history. Names are
demo-style; amounts and dates come from `random.Random(seed)` and fixed calendar months, so a
rerun posts the same history (record ids are new each time).

The result is a template folder:

    <template>/root/            an ordinary data root
    <template>/manifest.json    the profile, counts, timings and the ids the bench needs

Templates are keyed by (profile, seed, company migration head); `template_name` gives the key.
"""
from __future__ import annotations

import json
import random
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

HISTORY_START = (2021, 10)  # first month of history
COMPANY = "Bench Trades Co"
ORG = "Bench Org"
GROUP_ORG = "Bench Group"
MEMBER = "bench-member"


@dataclass(frozen=True)
class Profile:
    name: str
    months: int
    customers: int
    items: int
    vendors: int
    invoices: int  # per month
    payments: int  # per month
    bills: int  # per month
    bill_payments: int  # per month
    deposits: int  # per month
    undeposited: int  # R180: payments left in Undeposited Funds for one big deposit
    group_companies: int  # R180: companies in the organisation for the membership grant


PROFILES = {
    # A small trade business over five years: ~13 documents a business day.
    "large": Profile("large", 60, 2000, 400, 150, 130, 110, 30, 25, 6, 1000, 200),
    "small": Profile("small", 6, 200, 40, 15, 13, 11, 3, 3, 2, 100, 20),
    # Seconds, for tests.
    "tiny": Profile("tiny", 2, 6, 3, 2, 3, 2, 1, 1, 1, 5, 2),
}


def template_name(profile: str, seed: int) -> str:
    from bookflow.storage.migrate import HEADS
    return f"bench-{profile}-s{seed}-{HEADS['company']}"


@contextmanager
def held_session(root: Path, client_name: str = "bookflow-perf"):
    """Yield call(name, raw, company=None) running each command in one locked, open session."""
    from bookflow.core import dispatch as d, registry
    from bookflow.core.config import Config, os_login
    from bookflow.core.context import ActorKind, Context, Interface
    from bookflow.core.ids import new_id
    from bookflow.core.locks import RootLock
    from bookflow.core.perms import private_umask
    from bookflow.core.session import Session

    registry.load_all()
    s = Session(data_root=root, os_login=os_login(), config=Config(root / "config.toml"))
    ctx = Context.new(Interface.system, client_name)
    with private_umask(), RootLock(root, "perf generate"):
        try:
            s.config = Config.load(root / "config.toml")
            d._open_hub(s, True, ctx)
            d._load_actor(s)
            ctx = ctx.model_copy(update={"actor_id": s.actor.id, "actor_kind": ActorKind(s.actor.kind)})
            d._migrate_hub(s, ctx)

            def call(name: str, raw: dict[str, Any], company: str | None = None) -> dict[str, Any]:
                cmd = registry.get(name)
                if cmd.is_write and s.company is not None and not s.company.writable:
                    s.close_company()
                return d.execute(cmd, raw, ctx.model_copy(update={"request_id": new_id()}), s,
                                 company_selector=company)
            yield call
        finally:
            d._close(s)


def _month(index: int) -> tuple[int, int]:
    y, m = HISTORY_START
    m += index
    return y + (m - 1) // 12, (m - 1) % 12 + 1


def _day(rng: random.Random, y: int, m: int, lo: int = 1, hi: int = 28) -> str:
    return date(y, m, rng.randint(lo, hi)).isoformat()


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


FIRST = ("Oak", "Pine", "River", "Hill", "Lake", "Elm", "Cedar", "Maple", "Stone", "Bay", "Ridge", "Park")
LAST = ("Apartments", "Dental", "Bakery", "Holdings", "Clinic", "Cafe", "Builders", "Motors", "Studio",
        "Farms", "Realty", "Market", "Salon", "Hardware", "Church", "School")
WORK = ("Service call", "Drain clearing", "Valve replacement", "Inspection", "Heater install", "Pipe repair",
        "Fixture install", "Leak detection", "Maintenance visit", "Backflow test")


def generate(target: Path, profile: str = "large", seed: int = 180,
             progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Build a template folder at `target` (which must not exist). Returns its manifest."""
    import bookflow

    p = PROFILES[profile]
    say = progress or (lambda _m: None)
    target = Path(target)
    if target.exists():
        raise FileExistsError(str(target))
    root = target / "root"
    target.mkdir(parents=True)
    rng = random.Random(seed)
    started = time.perf_counter()
    phases: dict[str, float] = {}
    counts: dict[str, int] = {}

    def phase(name: str, t0: float) -> None:
        phases[name] = round(time.perf_counter() - t0, 2)
        say(f"{name}: {phases[name]}s")

    def count(kind: str, n: int = 1) -> None:
        counts[kind] = counts.get(kind, 0) + n

    t0 = time.perf_counter()
    c = bookflow.connect(data_root=str(root))
    c.init()
    c.organization.new(name=ORG)
    c.company.new(legal_name=COMPANY, home_currency="USD", organization=ORG, chart="service")
    phase("setup", t0)

    ids: dict[str, Any] = {}
    with held_session(root) as call:
        co = COMPANY

        def run(name, raw):
            return call(name, raw, co)

        accounts = run("account list", {})["items"]
        ids["company"] = call("company show", {}, co)["company_id"]
        by_type: dict[str, list] = {}
        for a in accounts:
            by_type.setdefault(a["type"], []).append(a)
        income = by_type["income"][0]["id"]
        expenses = [a["id"] for a in by_type.get("expense", [])][:6] or [run("account create", {"name": "Supplies", "type": "expense"})["id"]]
        bank = (by_type.get("bank") or [None])[0]
        checking = bank["id"] if bank else run("account create", {"name": "Checking", "type": "bank"})["id"]
        uf = next(a["id"] for a in accounts if a.get("system_role") == "undeposited_funds"
                  or a["name"] == "Undeposited Funds")
        non = next(x["id"] for x in run("sales-tax-code list", {})["items"] if not x.get("taxable"))
        methods = run("payment-method list", {})["items"]
        method = next((x["id"] for x in methods if x.get("kind") == "check"), None) or methods[0]["id"]
        ids.update(checking=checking, undeposited=uf, income=income, expense=expenses[0], payment_method=method)

        t0 = time.perf_counter()
        items = []
        for i in range(p.items):
            work = WORK[i % len(WORK)]
            price = rng.choice((45, 65, 85, 95, 125, 145, 185, 240, 320)) * 100 + rng.choice((0, 0, 50))
            items.append((run("item create", {
                "name": f"{work} {i:04d}", "type": "service", "description": work, "sales_enabled": True,
                "price": _money(price), "income_account_id": income, "sales_tax_code_id": non})["id"], price))
        count("items", p.items)
        customers = [run("customer create", {"name": f"{rng.choice(FIRST)} {rng.choice(LAST)} {i:05d}"})["id"]
                     for i in range(p.customers)]
        count("customers", p.customers)
        vendors = [run("vendor create", {"name": f"{rng.choice(FIRST)} Supply {i:04d}"})["id"]
                   for i in range(p.vendors)]
        count("vendors", p.vendors)
        phase("lists", t0)
        ids.update(item=items[0][0], customer=customers[0], vendor=vendors[0])

        t0 = time.perf_counter()
        open_invoices: list[dict] = []  # oldest first
        open_bills: list[dict] = []
        number = {"inv": 0, "bill": 0, "pay": 0, "dep": 0}
        post_times: list[float] = []
        for month in range(p.months):
            y, m = _month(month)
            for _ in range(p.invoices):
                lines = [{"item": it, "quantity": str(rng.randint(1, 3))}
                         for it, _price in rng.sample(items, rng.randint(1, min(4, len(items))))]
                number["inv"] += 1
                a = time.perf_counter()
                out = run("invoice post", {"date": _day(rng, y, m), "number": f"INV-{number['inv']:06d}",
                                           "customer": rng.choice(customers), "lines": lines})
                post_times.append(time.perf_counter() - a)
                open_invoices.append({"id": out["id"], "version": out["version"],
                                      "customer": out["customer_id"], "total": out["total"]})
                count("invoices"); count("invoice_lines", len(lines))
            paid = []
            # Customers pay the oldest invoices, most of them in full, a month or two later.
            for inv in open_invoices[:p.payments] if month > 0 else []:
                number["pay"] += 1
                out = run("payment receive", {
                    "customer": inv["customer"], "date": _day(rng, y, m), "amount": inv["total"],
                    "number": f"CHK-{number['pay']:06d}", "payment_method": method, "deposit_to": uf,
                    "operation_key": f"gen-pay-{number['pay']}",
                    "applications": {"mode": "inline", "items": [
                        {"invoice": inv["id"], "expected_version": inv["version"], "amount": inv["total"]}]}})
                paid.append({"source_type": "payment", "source": out["id"], "expected_version": out["version"]})
                count("payments")
            del open_invoices[:len(paid)]
            for k in range(p.deposits):
                group = paid[k::p.deposits]
                if not group:
                    continue
                number["dep"] += 1
                run("deposit post", {"operation_key": f"gen-dep-{number['dep']}", "document": {
                    "mode": "inline", "deposit_to": checking, "date": date(y, m, 28).isoformat(),
                    "number": f"DEP-{number['dep']:05d}", "sources": group}})
                count("deposits"); count("deposit_lines", len(group))
            for _ in range(p.bills):
                number["bill"] += 1
                amount = rng.randint(4000, 250000)
                out = run("bill post", {"date": _day(rng, y, m, 1, 20), "number": f"B-{number['bill']:06d}",
                                        "vendor": rng.choice(vendors),
                                        "expenses": [{"account": rng.choice(expenses), "amount": _money(amount)}]})
                open_bills.append({"bill": out["id"]})
                count("bills")
            for bill in open_bills[:p.bill_payments]:
                run("bill pay", {"date": date(y, m, 27).isoformat(), "bills": [bill],
                                 "funding_account": checking, "method": "check"})
                count("bill_payments")
            del open_bills[:p.bill_payments]
            if month % 6 == 5 or month == p.months - 1:
                say(f"month {month + 1}/{p.months} {y}-{m:02d}: {sum(counts.get(k, 0) for k in ('invoices', 'payments', 'deposits', 'bills', 'bill_payments'))} documents, "
                    f"{time.perf_counter() - started:.0f}s")
        phase("history", t0)
        post_times.sort()
        invoice_post = {"count": len(post_times), "first_tenth_ms": round(1000 * sum(post_times[:max(1, len(post_times) // 10)]) / max(1, len(post_times) // 10), 1)}

        # R180: payments left in Undeposited Funds, for one deposit of all of them.
        t0 = time.perf_counter()
        y, m = _month(p.months - 1)
        last_day = date(y, m, 28).isoformat()
        customer = run("customer create", {"name": "Busy Month Customer"})["id"]
        for n in range(p.undeposited):
            run("payment receive", {"customer": customer, "date": last_day, "amount": f"{10 + n % 97}.{n % 100:02d}",
                                    "number": f"UND-{n:05d}", "payment_method": method, "deposit_to": uf,
                                    "operation_key": f"gen-und-{n}", "applications": {"mode": "inline", "items": []}})
        count("undeposited_payments", p.undeposited)
        ids.update(busy_customer=customer, last_day=last_day)
        phase("undeposited", t0)

        # R180: one organisation of many companies, for the organisation-wide membership grant.
        t0 = time.perf_counter()
        call("organization new", {"name": GROUP_ORG})
        for n in range(p.group_companies):
            call("company new", {"legal_name": f"Bench Branch {n:03d}", "home_currency": "USD",
                                 "organization": GROUP_ORG, "chart": "none"})
        count("group_companies", p.group_companies)
        call("user add", {"username": MEMBER, "display_name": "Bench Member", "password": "bench-member-pw-180"})
        phase("group", t0)

    _checkpoint(root)
    manifest = {
        "profile": asdict(p), "seed": seed, "template": target.name, "counts": counts, "ids": ids,
        "organization": ORG, "group_organization": GROUP_ORG, "member": MEMBER, "company_name": COMPANY,
        "generation_seconds": round(time.perf_counter() - started, 1), "phases": phases,
        "invoice_post_early_ms": invoice_post["first_tenth_ms"],
        "disk_bytes": disk_bytes(root), "history": [f"{HISTORY_START[0]}-{HISTORY_START[1]:02d}", ids["last_day"]],
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    say(f"done: {manifest['generation_seconds']}s, {manifest['disk_bytes'] / 1e6:.1f} MB")
    return manifest


def _checkpoint(root: Path) -> None:
    import sqlite3
    for path in root.rglob("*.db"):
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            db.close()


def disk_bytes(root: Path) -> int:
    return sum(f.stat().st_size for f in Path(root).rglob("*") if f.is_file())


def load(template: Path) -> dict[str, Any]:
    return json.loads((Path(template) / "manifest.json").read_text())


def copy_root(template: Path, target: Path) -> Path:
    """A private working copy of a template's data root (paths inside are relative)."""
    shutil.copytree(Path(template) / "root", target)
    return target
