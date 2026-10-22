"""R117 accounts and items: the twin-company check.

One script builds a small company twice: in the "merged" twin every use names the duplicate M and
M is then merged into S; in the "direct" twin every use names S from the start and M is only made
inactive. Every report command in the registry, on each basis it takes and filtered by either
name, must then read the same in both, and so must a fixed script run afterwards (edit an old
document naming M, void, pay, deposit, sell the item, enter a memorized transaction). The only
allowed difference is the as-posted snapshot of M's name on ledger rows, which is stripped.
Undoing the merge with nothing saved since reproduces the pre-merge results.
"""
import json
import re
import typing

import pytest

import bookflow
from bookflow.core import registry
from bookflow.core.errors import BookflowError

ORG = "Twin Books"
TWINS = {"merged": "Twin Merged LLC", "direct": "Twin Direct LLC"}
REASON = "twin check"
ACCOUNT_S, ACCOUNT_M = "Shop Supplies", "Shop Supply"
INCOME_S, INCOME_M = "Drain Income", "Drain Revenue"
ITEM_S, ITEM_M = "Drain Clearing", "Drain Clear"
BANK = "Twin Bank"
DATE_FROM, DATE_TO = "2026-01-01", "2026-12-31"

_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d[:\d.]*(Z|[+-]\d\d:?\d\d)?")
# The as-posted snapshot of a name on ledger and document rows, and paging and freshness tokens.
_SKIP_KEYS = re.compile(r"snapshot|cursor|fingerprint|generated_at|as_posted|freshness|token|watermark")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An empty root of its own: two small companies, no demo seed to build."""
    root = tmp_path / "root"
    monkeypatch.setenv("BOOKFLOW_DATA_ROOT", str(root))
    monkeypatch.delenv("BOOKFLOW_COMPANY", raising=False)
    c = bookflow.connect(data_root=str(root))
    c.init()
    c.run("organization new", {"name": ORG})
    return c


class Twin:
    def __init__(self, client, kind):
        self.client, self.kind, self.company = client, kind, TWINS[kind]
        self.ids = {}

    def run(self, name, data=None, **kw):
        command = registry.get(name)
        if command.is_write and "reason" not in kw:
            kw["reason"] = REASON
        if command.scope == "company":
            kw["company"] = self.company
        return self.client.run(name, data or {}, **kw)

    def use(self, s, m):
        """What this twin's script names: the duplicate in the merged twin, the survivor in the direct one."""
        return m if self.kind == "merged" else s


def _norm(value, company):
    if isinstance(value, dict):
        return {k: _norm(v, company) for k, v in value.items() if not _SKIP_KEYS.search(k)}
    if isinstance(value, list):
        items = [_norm(v, company) for v in value]
        return sorted(items, key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, str):
        value = value.replace(company, "<company>")
        return _STAMP.sub("<ts>", _ULID.sub("<id>", value))
    return value


def _build(client, kind):
    t = Twin(client, kind)
    t.run("company new", dict(legal_name=t.company, home_currency="USD", organization=ORG, timezone="UTC",
                              chart="general"))
    acct = lambda **kw: t.run("account create", kw)
    acct(name=BANK, type="bank")
    acct(name=ACCOUNT_S, type="expense")
    acct(name=ACCOUNT_M, type="expense")
    acct(name=INCOME_S, type="income")
    acct(name=INCOME_M, type="income")
    t.run("customer create", dict(name="Pat Customer"))
    vendor = t.run("vendor create", dict(name="Acme Supply", expense_accounts=[dict(account_id=_id(t, t.use(ACCOUNT_S, ACCOUNT_M)))]))
    t.ids["vendor"] = vendor["id"]
    codes = t.run("sales-tax-code list")["items"]
    tax_code = next(c["id"] for c in codes if not c.get("taxable"))
    t.run("item create", dict(name=ITEM_S, type="service", price="150.00", income_account_id=_id(t, t.use(INCOME_S, INCOME_M)),
                              description="Clear a drain", sales_tax_code_id=tax_code,
                              purchase_description="Drain clearing", cost="80.00",
                              expense_account_id=_id(t, t.use(ACCOUNT_S, ACCOUNT_M)), sales_enabled=True,
                              purchase_enabled=True))
    t.run("item create", dict(name=ITEM_M, type="service", price="150.00", income_account_id=_id(t, INCOME_S),
                              description="Clear a drain", sales_tax_code_id=tax_code))
    supplies = t.use(ACCOUNT_S, ACCOUNT_M)
    t.ids["bill"] = t.run("bill post", dict(vendor="Acme Supply", date="2026-03-02", memo="March parts",
                                            expenses=[dict(account=supplies, amount="100.00")]))["id"]
    t.ids["bill2"] = t.run("bill post", dict(vendor="Acme Supply", date="2026-03-03", memo="Second",
                                             expenses=[dict(account=supplies, amount="60.00")]))["id"]
    t.ids["check"] = t.run("check post", dict(account=BANK, pay_to=dict(name_type="vendor", name_id="Acme Supply"),
                                              date="2026-03-05", number="101", amount="40.00",
                                              expenses=[dict(account=supplies, amount="40.00")]))["id"]
    t.ids["journal"] = t.run("journal post", dict(date="2026-03-06", memo="Supplies from cash", lines=[
        dict(account=supplies, side="debit", amount="25.00"), dict(account=BANK, side="credit", amount="25.00")]))["id"]
    t.run("journal post", dict(date="2026-01-02", memo="Opening cash", lines=[
        dict(account=BANK, side="debit", amount="1000.00"), dict(account="Opening Balance Equity", side="credit", amount="1000.00")]))
    t.run("memorized create", dict(name="Monthly supplies", command="check post", payload=dict(
        account=BANK, pay_to=dict(name_type="vendor", name_id="Acme Supply"), amount="15.00",
        expenses=[dict(account=supplies, amount="15.00")])))
    t.ids["invoice"] = t.run("invoice post", dict(customer="Pat Customer", date="2026-03-10",
                                                  lines=[dict(item=ITEM_S, quantity="1", unit_price="200.00")]))["id"]
    return t


def _id(t, account):
    return t.run("account show", dict(account=account))["id"]


def _after(t):
    """The fixed script run after the merge, the same in both twins."""
    bill = t.run("bill show", dict(bill=t.ids["bill"]))
    t.run("bill update", dict(bill=t.ids["bill"], expected_version=bill["version"], memo="March parts, corrected"))
    journal = t.run("journal show", dict(journal=t.ids["journal"]))
    t.run("journal update", dict(journal=t.ids["journal"], expected_version=journal["version"], memo="Supplies, cash"))
    check = t.run("check show", dict(check=t.ids["check"]))
    t.run("check void", dict(check=t.ids["check"], expected_version=check["version"]))
    t.run("bill pay", dict(date="2026-04-02", funding_account=BANK, method="Check", check_number="102",
                           bills=[dict(bill=t.ids["bill2"], amount="60.00")]))
    invoice = t.run("invoice show", dict(invoice=t.ids["invoice"]))
    t.run("invoice update", dict(invoice=t.ids["invoice"], expected_version=invoice["version"], memo="Drain, corrected"))
    invoice = t.run("invoice show", dict(invoice=t.ids["invoice"]))
    t.run("payment receive", dict(customer="Pat Customer", date="2026-04-03", amount="200.00", operation_key=f"{t.kind}-r1",
                                  payment_method="Check", reference="55", applications=dict(mode="inline", items=[
                                      dict(invoice=t.ids["invoice"], amount="200.00", expected_version=invoice["version"])])))
    sources = t.run("deposit sources", dict(date="2026-04-04", limit=50))
    rows = [dict(source_type=r["source_type"], source=r["source"], expected_version=r["expected_version"])
            for r in sources["items"]]
    t.run("deposit post", dict(operation_key=f"{t.kind}-d1", document=dict(mode="inline", deposit_to=BANK,
                                                                           date="2026-04-04", sources=rows)))
    t.run("invoice post", dict(customer="Pat Customer", date="2026-04-05",
                               lines=[dict(item=ITEM_S, quantity="2", unit_price="150.00")]))
    t.run("memorized enter", dict(memorized="Monthly supplies", date="2026-04-06"))


def _report_inputs(command, filters):
    """Every input set a report command is compared on: each basis it takes, and each account filter."""
    fields = command.input_model.model_fields
    base = {}
    for name in fields:
        if name in ("date_from",):
            base[name] = DATE_FROM
        elif name in ("date_to", "as_of"):
            base[name] = DATE_TO
        elif name == "limit":
            base[name] = 200
    if "report" in fields and fields["report"].is_required():
        return []
    variants = [dict(base)]
    if "basis" in fields:
        variants = [dict(base, basis=b) for b in _literals(fields["basis"].annotation) or ("accrual",)]
    extra = []
    for key, values in filters.items():
        if key in fields:
            for value in values:
                extra.extend(dict(v, **{key: value}) for v in variants)
    if "account" in fields and fields["account"].is_required():
        variants = [dict(v, account=BANK) for v in variants]
    return variants + extra


def _literals(annotation):
    """The string values a Literal (possibly Optional) annotation allows."""
    if isinstance(annotation, str):
        return [annotation]
    return [v for arg in typing.get_args(annotation) for v in _literals(arg)]


def _reports():
    registry.load_all()
    names = [c.name for c in registry.all_commands() if c.name.startswith("report ") and not c.is_write]
    names += ["register query", "transaction find", "account list", "item list"]
    return [registry.get(n) for n in sorted(set(names))]


def _read_all(t, survivor_label):
    """Every report, each input variant, normalized. Filters name the entry as ``survivor_label`` maps."""
    out = {}
    filters = {"account": survivor_label["account"], "accounts": [[x] for x in survivor_label["account"]],
               "item": survivor_label["item"]}
    for command in _reports():
        for inp in _report_inputs(command, filters):
            if command.name in ("account list", "item list"):
                inp = dict(include_inactive=True)
            key = f"{command.name} {json.dumps(inp, sort_keys=True)}"
            try:
                result = t.run(command.name, inp)
            except BookflowError as exc:
                result = {"error": exc.code}
            out[key] = _norm(result, t.company)
    return out


def _labels(names):
    return {"account": names, "item": [ITEM_S]}


BOTH = [ACCOUNT_S, ACCOUNT_M, INCOME_S, INCOME_M]


@pytest.mark.timeout(900)
def test_merged_and_direct_twins_read_the_same(client):
    merged, direct = _build(client, "merged"), _build(client, "direct")
    before = _read_all(merged, _labels(BOTH))

    preview = merged.run("account merge", dict(merged=ACCOUNT_M, into=ACCOUNT_S), dry_run=True)
    assert preview["merge_id"] is None and preview["changed"]
    assert preview["merged_balance"]["minor_units"] == 22500 and preview["survivor_balance"]["minor_units"] == 0
    assert preview["survivor_balance_after"]["minor_units"] == 22500
    assert {(d["type"], d["count"]) for d in preview["documents"]} == {("bill", 2), ("journal_entry", 2)}
    assert {(r["owner_type"], r["field"], r["action"]) for r in preview["references"]} == {
        ("item", "expense_account_id", "repoint"), ("vendor", "account_id", "repoint")}
    done = merged.run("account merge", dict(merged=ACCOUNT_M, into=ACCOUNT_S))
    assert done["changed"] and done["merge_id"]
    again = merged.run("account merge", dict(merged=ACCOUNT_M, into=ACCOUNT_S))
    assert not again["changed"] and again["merge_id"] == done["merge_id"]
    direct.run("account deactivate", dict(account=ACCOUNT_M))

    # Re-pointed references: the item and the vendor default now name S.
    s_id = _id(merged, ACCOUNT_S)
    assert merged.run("item show", dict(item=ITEM_S))["expense_account_id"] == s_id
    assert [a["account_id"] for a in merged.run("vendor show", dict(vendor="Acme Supply"))["expense_accounts"]] == [s_id]

    # Undo with nothing saved since reproduces the pre-merge reads, references included; then merge again.
    undone = merged.run("account unmerge", dict(merged=ACCOUNT_M))
    assert undone["changed"] and undone["reactivated"] and undone["restored"] == 2
    differ = _diff(_strip_versions(_read_all(merged, _labels(BOTH))), _strip_versions(before))
    assert not differ, "\n".join(differ[:30])
    merged.run("account merge", dict(merged=ACCOUNT_M, into=ACCOUNT_S))
    income = merged.run("account merge", dict(merged=INCOME_M, into=INCOME_S))
    assert income["merged_balance"]["minor_units"] == 20000
    assert {(r["owner_type"], r["field"]) for r in income["references"]} == {("item", "income_account_id")}
    direct.run("account deactivate", dict(account=INCOME_M))

    _compare(merged, direct)
    _after(merged)
    _after(direct)
    _compare(merged, direct)


def _diff(a, b, path=""):
    """Paths where two normalized reads differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k, "<missing>"), b.get(k, "<missing>"), f"{path}/{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        left = [json.dumps(x, sort_keys=True) for x in a]
        right = [json.dumps(x, sort_keys=True) for x in b]
        only_a = [x for x in left if x not in right]
        only_b = [x for x in right if x not in left]
        if len(only_a) == len(only_b):
            return [d for x, y in zip(only_a, only_b) for d in _diff(json.loads(x), json.loads(y), f"{path}[~]")]
        return [f"{path}[+] {x[:300]}" for x in only_a] + [f"{path}[-] {x[:300]}" for x in only_b]
    return [] if a == b else [f"{path}: {json.dumps(a)[:200]} != {json.dumps(b)[:200]}"]


def _strip_versions(reads):
    return json.loads(re.sub(r'"(version|updated_at|updated_by|updated_via)": [^,}]+', '"\\1": 0', json.dumps(reads)))


def _compare(merged, direct):
    # In the merged twin the filters name M or S and must read as S; the direct twin names S.
    m = _strip_versions(_read_all(merged, _labels(BOTH)))
    d = _strip_versions(_read_all(direct, _labels([ACCOUNT_S, INCOME_S])))
    import os
    if os.environ.get("TWIN_DUMP"):
        with open(os.environ["TWIN_DUMP"], "w") as fh:
            json.dump({"merged": m, "direct": d}, fh, indent=1, sort_keys=True)
    failed = sorted(k for k, v in m.items() if isinstance(v, dict) and set(v) == {"error"})
    assert len(failed) <= len(m) // 5, failed  # a report that refuses in both twins proves nothing
    missing = []
    for key, value in m.items():
        twin_key = key.replace(json.dumps(ACCOUNT_M), json.dumps(ACCOUNT_S)).replace(json.dumps(INCOME_M), json.dumps(INCOME_S))
        missing += _diff(value, d.get(twin_key, "<missing>"), key)
    assert not missing, "\n".join(missing[:60]) + f"\n... {len(missing)} differences over {len(m)} reads"
    return len(m)
