"""Write Harbor Electric's files (see tests/fakeco.py): `render()` returns them as bytes by path."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from tests import fakeco as F
from tests import fakeco_data as D

CRLF = "\r\n"
STAMP = 1782950400  # the desktop product's TIMESTAMP column: seconds, the day the lists were exported
COMPANY = D.COMPANY["legal_name"]


# ---------------------------------------------------------------- small helpers

def _quote(cells) -> str:
    return ",".join(f'"{c}"' if c != "" else "" for c in cells)


def _csv(rows: list[list[str]]) -> str:
    return CRLF.join(_quote(r) if isinstance(r, list) else r for r in rows) + CRLF


def _long(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def _yn(flag: bool) -> str:
    return "Y" if flag else "N"


def _days_past(doc: F.Doc, as_of: date) -> int:
    ref = doc.due if doc.kind in ("invoice", "bill") else doc.date
    return (as_of - ref).days


def _bucket(doc: F.Doc, as_of: date) -> int:
    days = _days_past(doc, as_of)
    return 0 if days <= 0 else 1 if days <= 30 else 2 if days <= 60 else 3 if days <= 90 else 4


BUCKETS = ["current", "1-30", "31-60", "61-90", "over_90"]


# ---------------------------------------------------------------- the Lists IIF

_ACCNT = ["!ACCNT", "NAME", "REFNUM", "TIMESTAMP", "ACCNTTYPE", "OBAMOUNT", "DESC", "ACCNUM", "SCD", "BANKNUM", "EXTRA",
          "HIDDEN", "DELCOUNT", "USEID"]
_CUST = ["!CUST", "NAME", "REFNUM", "TIMESTAMP", "BADDR1", "BADDR2", "BADDR3", "BADDR4", "BADDR5", "SADDR1", "SADDR2",
         "SADDR3", "SADDR4", "SADDR5", "PHONE1", "PHONE2", "FAXNUM", "EMAIL", "NOTE", "CONT1", "CONT2", "CTYPE", "TERMS",
         "TAXABLE", "SALESTAXCODE", "LIMIT", "RESALENUM", "REP", "TAXITEM", "NOTEPAD", "SALUTATION", "COMPANYNAME",
         "FIRSTNAME", "MIDINIT", "LASTNAME", *[f"CUSTFLD{n}" for n in range(1, 16)], "JOBDESC", "JOBTYPE", "JOBSTATUS",
         "JOBSTART", "JOBPROJEND", "JOBEND", "HIDDEN", "DELCOUNT", "PRICELEVEL"]
_VEND = ["!VEND", "NAME", "REFNUM", "TIMESTAMP", "PRINTAS", "ADDR1", "ADDR2", "ADDR3", "ADDR4", "ADDR5", "VTYPE", "CONT1",
         "CONT2", "PHONE1", "PHONE2", "FAXNUM", "EMAIL", "NOTE", "TAXID", "LIMIT", "TERMS", "NOTEPAD", "SALUTATION",
         "COMPANYNAME", "FIRSTNAME", "MIDINIT", "LASTNAME", *[f"CUSTFLD{n}" for n in range(1, 16)], "1099", "HIDDEN",
         "DELCOUNT"]
_INVITEM = ["!INVITEM", "NAME", "REFNUM", "TIMESTAMP", "INVITEMTYPE", "DESC", "PURCHASEDESC", "ACCNT", "ASSETACCNT",
            "COGSACCNT", "QNTY", "QNTY", "PRICE", "COST", "TAXABLE", "SALESTAXCODE", "PAYMETH", "TAXVEND", "PREFVEND",
            "REORDERPOINT", "EXTRA", *[f"CUSTFLD{n}" for n in range(1, 6)], "DEP_TYPE", "ISPASSEDTHRU", "HIDDEN",
            "DELCOUNT"]
_TERMS = ["!TERMS", "NAME", "REFNUM", "TIMESTAMP", "DUEDAYS", "MINDAYS", "DISCPER", "DISCDAYS", "TERMSTYPE", "HIDDEN",
          "DELCOUNT"]
_EMP = ["!EMP", "NAME", "REFNUM", "TIMESTAMP", "INIT", "ADDR1", "ADDR2", "ADDR3", "ADDR4", "ADDR5", "SSNO", "PHONE1",
        "PHONE2", "FIRSTNAME", "MIDINIT", "LASTNAME", "HIDDEN", "DELCOUNT"]
_OTHER = ["!OTHERNAME", "NAME", "REFNUM", "TIMESTAMP", "BADDR1", "BADDR2", "BADDR3", "BADDR4", "BADDR5", "PHONE1",
          "PHONE2", "FAXNUM", "EMAIL", "CONT1", "HIDDEN", "DELCOUNT"]


def _iif_row(header: list[str], values: dict) -> str:
    cells, seen = [header[0][1:]], set()
    for column in header[1:]:
        if column in seen:  # the item list names QNTY twice
            cells.append("0")
            continue
        seen.add(column)
        cells.append(values.get(column, ""))
    assert len(cells) == len(header), header[0]
    return "\t".join(cells)


def _address(lines: list[str], width: int = 5) -> list[str]:
    lines = [line for line in lines if line]
    return (lines + [""] * width)[:width]


def lists_iif() -> bytes:
    out = ["!HDR\tPROD\tVER\tREL\tIIFVER\tDATE\tTIME\tACCNTNT\tACCNTNTSPLITTIME",
           f"HDR\tQuickBooks Pro\tVersion 34.0D\tRelease R5P\t1\t07/01/2026\t{STAMP}\tN\t0"]
    ref = iter(range(10, 10_000))
    out.append("\t".join(_ACCNT))
    for number, name, kind, desc, extra in D.ACCOUNTS:
        out.append(_iif_row(_ACCNT, dict(NAME=name, REFNUM=str(next(ref)), TIMESTAMP=str(STAMP - 86400 * 400),
                                         ACCNTTYPE=kind, OBAMOUNT="0.00", DESC=desc, ACCNUM=number, SCD="0",
                                         EXTRA=extra, HIDDEN="N", DELCOUNT="0", USEID="N")))
    for header, names in (("CTYPE", D.CUSTOMER_TYPES), ("VTYPE", D.VENDOR_TYPES)):
        out.append(f"!{header}\tNAME\tREFNUM\tTIMESTAMP\tHIDDEN")
        for name in names:
            out.append(f"{header}\t{name}\t{next(ref)}\t{STAMP - 86400 * 400}\tN")
    out.append("\t".join(_TERMS))
    for name, due, percent, discount_days in D.TERMS:
        out.append(_iif_row(_TERMS, dict(NAME=name, REFNUM=str(next(ref)), TIMESTAMP=str(STAMP - 86400 * 400),
                                         DUEDAYS=str(due), MINDAYS="0", DISCPER=percent or "0.0%",
                                         DISCDAYS=str(discount_days), TERMSTYPE="0", HIDDEN="N", DELCOUNT="0")))
    out.append("!PAYMETH\tNAME\tREFNUM\tTIMESTAMP\tHIDDEN")
    for name in D.PAYMENT_METHODS:
        out.append(f"PAYMETH\t{name}\t{next(ref)}\t{STAMP - 86400 * 400}\tN")
    out.append("\t".join(_CUST))
    for name, c in sorted(D.CUSTOMERS.items()):
        top = name.split(":")[0]
        parent = D.CUSTOMERS[top]
        who = c["company"] or f"{c['first']} {c['last']}".strip()
        bill = _address([who, *c["street"], c["city"]])
        values = dict(NAME=name, REFNUM=str(next(ref)), TIMESTAMP=str(STAMP - 86400 * 90),
                      **{f"BADDR{n + 1}": v for n, v in enumerate(bill)},
                      **{f"SADDR{n + 1}": v for n, v in enumerate(bill)},
                      PHONE1=c["phone"], EMAIL=c["email"] if ":" not in name else "", CONT1=c["contact"],
                      CTYPE=c["ctype"], TERMS=c["terms"], TAXABLE=_yn(c["taxable"]),
                      SALESTAXCODE="Tax" if c["taxable"] else "Non", LIMIT=c["limit"], RESALENUM=c["resale"],
                      TAXITEM=D.COMPANY["tax_item"] if parent["taxable"] else "",
                      COMPANYNAME=c["company"], FIRSTNAME=c["first"], LASTNAME=c["last"],
                      JOBDESC=c["job_desc"], JOBSTATUS=c["job_status"], HIDDEN=_yn(c["inactive"]), DELCOUNT="0")
        out.append(_iif_row(_CUST, values))
    out.append("\t".join(_VEND))
    for name, v in sorted(D.VENDORS.items()):
        printed = f"{v['first']} {v['last']}".strip() if v["first"] else name
        addr = _address([printed, *v["street"], v["city"]])
        out.append(_iif_row(_VEND, dict(NAME=name, REFNUM=str(next(ref)), TIMESTAMP=str(STAMP - 86400 * 120),
                                        PRINTAS=printed, **{f"ADDR{n + 1}": x for n, x in enumerate(addr)},
                                        VTYPE=v["vtype"], CONT1=v["contact"], PHONE1=v["phone"], EMAIL=v["email"],
                                        NOTE=v["account"], TAXID=v["taxid"], TERMS=v["terms"],
                                        COMPANYNAME="" if v["first"] else name, FIRSTNAME=v["first"],
                                        LASTNAME=v["last"], **{"1099": _yn(v["eligible_1099"])},
                                        HIDDEN=_yn(v["inactive"]), DELCOUNT="0")))
    out.append("\t".join(_EMP))
    for name, e in sorted(D.EMPLOYEES.items()):
        addr = _address([f"{e['first']} {e['last']}", *e["street"], e["city"]])
        out.append(_iif_row(_EMP, dict(NAME=name, REFNUM=str(next(ref)), TIMESTAMP=str(STAMP - 86400 * 300),
                                       **{f"ADDR{n + 1}": x for n, x in enumerate(addr)}, SSNO=e["ssn"],
                                       FIRSTNAME=e["first"], LASTNAME=e["last"], HIDDEN="N", DELCOUNT="0")))
    out.append("\t".join(_OTHER))
    for name, o in sorted(D.OTHER_NAMES.items()):
        addr = _address([D.COMPANY["owner"], *o["street"], o["city"]])
        out.append(_iif_row(_OTHER, dict(NAME=name, REFNUM=str(next(ref)), TIMESTAMP=str(STAMP - 86400 * 500),
                                         **{f"BADDR{n + 1}": x for n, x in enumerate(addr)}, PHONE1=o["phone"],
                                         HIDDEN="N", DELCOUNT="0")))
    out.append("\t".join(_INVITEM))
    for name, i in sorted(D.ITEMS.items(), key=lambda kv: (["SERV", "INVENTORY", "PART", "OTHC", "DISC", "SUBT",
                                                            "STAX", "GRP"].index(kv[1]["kind"]), kv[0])):
        stocked = i["kind"] == "INVENTORY"
        out.append(_iif_row(_INVITEM, dict(
            NAME=name, REFNUM=str(next(ref)), TIMESTAMP=str(STAMP - 86400 * 200), INVITEMTYPE=i["kind"],
            DESC=i["desc"], PURCHASEDESC=i["purchase_desc"], ACCNT=i["income"],
            ASSETACCNT=i["asset"] if stocked else "", COGSACCNT=i["cogs"] if stocked else i["expense"],
            PRICE=i["price"], COST=i["cost"], TAXABLE=_yn(i["taxable"]), SALESTAXCODE="Tax" if i["taxable"] else "Non",
            TAXVEND=i["agency"], PREFVEND=i["vendor"], REORDERPOINT=i["reorder"], ISPASSEDTHRU="N", HIDDEN="N",
            DELCOUNT="0")))
    return (CRLF.join(out) + CRLF).encode("cp1252")


# ---------------------------------------------------------------- the old books' reports

def _title(report: str, as_of: date, basis: bool = False) -> list:
    rows = [[COMPANY], [report]]
    if basis:
        rows.append(["Accrual Basis"])
    rows.append([f"As of {_long(as_of)}"])
    return rows


def trial_balance_csv(gl: dict, as_of: date) -> bytes:
    rows = _title("Trial Balance", as_of, basis=True)
    rows += [["", "", as_of.strftime("%b %d, %y")], ["", "", "Debit", "Credit"]]
    debits = credits = 0
    for name in F.ACCOUNT_ORDER:
        balance = gl.get(name, 0)
        if not balance:
            continue
        if balance > 0:
            rows.append([F.qb_account(name), "", F.qb_money(balance), ""])
            debits += balance
        else:
            rows.append([F.qb_account(name), "", "", F.qb_money(-balance)])
            credits -= balance
    assert debits == credits
    rows.append(["TOTAL", "", F.qb_money(debits), F.qb_money(credits)])
    return _csv(rows).encode("cp1252")


def _by_customer(docs: list[F.Doc]) -> dict:
    grouped: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for doc in docs:
        top, _, job = doc.name.partition(":")
        grouped[top][job].append(doc)
    for top, jobs in grouped.items():
        assert not ("" in jobs and len(jobs) > 1), f"{top} has both its own documents and jobs'"
    return grouped


def open_invoices_csv(ar: dict, as_of: date) -> bytes:
    rows = _title("Open Invoices", as_of)
    rows.append(["", "", "Type", "Date", "Num", "P. O. #", "Terms", "Due Date", "Class", "Aging", "Open Balance"])
    blank = [""] * 9
    docs = [d for d in ar.values() if d.open]
    total = 0

    def doc_row(doc: F.Doc):
        if doc.kind == "invoice":
            days = _days_past(doc, as_of)
            return ["", "", "Invoice", F.us(doc.date), doc.number, doc.po, doc.terms, F.us(doc.due), "",
                    str(days) if days > 0 else "", F.qb_money(doc.open)]
        return ["", "", "Credit Memo" if doc.kind == "credit_memo" else "Payment", F.us(doc.date), doc.number, "",
                "", F.us(doc.date), "", "", F.qb_money(doc.open)]

    for top, jobs in sorted(_by_customer(docs).items()):
        rows.append([top, *[""] * 10])
        top_total = 0
        for job, items in sorted(jobs.items()):
            items.sort(key=lambda d: (d.date, d.number))
            job_total = sum(d.open for d in items)
            if job:
                rows.append(["", job, *blank])
            rows += [doc_row(d) for d in items]
            if job:
                rows.append(["", f"Total {job}", *[""] * 8, F.qb_money(job_total)])
            top_total += job_total
        rows.append([f"Total {top}", *[""] * 9, F.qb_money(top_total)])
        total += top_total
    rows.append(["TOTAL", *[""] * 9, F.qb_money(total)])
    return _csv(rows).encode("cp1252")


def _aging_lines(docs: list[F.Doc], as_of: date, report: str) -> tuple[list, dict]:
    rows = _title(report, as_of)
    rows.append(["", "", "Current", "1 - 30", "31 - 60", "61 - 90", "> 90", "TOTAL"])
    grand = [0] * 5
    detail = {}
    for top, jobs in sorted(_by_customer(docs).items()):
        top_sums = [0] * 5
        has_jobs = "" not in jobs
        if has_jobs:
            rows.append([top, *[""] * 7])
        for job, items in sorted(jobs.items()):
            sums = [0] * 5
            for doc in items:
                sums[_bucket(doc, as_of)] += doc.open
            detail[f"{top}:{job}" if job else top] = sums
            cells = [F.qb_money(v) for v in sums] + [F.qb_money(sum(sums))]
            rows.append(["", job, *cells] if job else [top, "", *cells])
            top_sums = [a + b for a, b in zip(top_sums, sums)]
        if has_jobs:
            rows.append([f"Total {top}", "", *[F.qb_money(v) for v in top_sums], F.qb_money(sum(top_sums))])
        grand = [a + b for a, b in zip(grand, top_sums)]
    rows.append(["TOTAL", "", *[F.qb_money(v) for v in grand], F.qb_money(sum(grand))])
    return rows, detail


def ar_aging_csv(ar: dict, as_of: date) -> bytes:
    rows, _ = _aging_lines([d for d in ar.values() if d.open], as_of, "A/R Aging Summary")
    return _csv(rows).encode("cp1252")


def ap_aging_csv(ap: dict, as_of: date) -> bytes:
    rows, _ = _aging_lines([d for d in ap.values() if d.open], as_of, "A/P Aging Summary")
    return _csv(rows).encode("cp1252")


def unpaid_bills_csv(ap: dict, as_of: date) -> bytes:
    rows = _title("Unpaid Bills Detail", as_of)
    rows.append(["", "", "Type", "Date", "Num", "Due Date", "Aging", "Open Balance"])
    total = 0
    grouped = defaultdict(list)
    for doc in ap.values():
        if doc.open:
            grouped[doc.name].append(doc)
    for name, docs in sorted(grouped.items()):
        rows.append([name, *[""] * 7])
        for doc in sorted(docs, key=lambda d: (d.date, d.number)):
            if doc.kind == "bill":
                days = _days_past(doc, as_of)
                rows.append(["", "", "Bill", F.us(doc.date), doc.number, F.us(doc.due), str(days) if days > 0 else "",
                             F.qb_money(doc.open)])
            else:
                rows.append(["", "", "Credit", F.us(doc.date), doc.number, "", "", F.qb_money(doc.open)])
        subtotal = sum(d.open for d in docs)
        rows.append([f"Total {name}", *[""] * 6, F.qb_money(subtotal)])
        total += subtotal
    rows.append(["TOTAL", *[""] * 6, F.qb_money(total)])
    return _csv(rows).encode("cp1252")


def _percent(part: int, whole: int) -> str:
    return f"{(Decimal(part) * 100 / whole).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def inventory_valuation_csv(stock: dict, as_of: date) -> bytes:
    rows = _title("Inventory Valuation Summary", as_of)
    rows.append(["", "On Hand", "Avg Cost", "Asset Value", "% of Tot Asset", "Sales Price", "Retail Value",
                 "% of Tot Retail"])
    value_total = sum(v for _, v in stock.values())
    retail = {name: F.round_half_up(q * F.cents(F.item(name)["price"])) for name, (q, _) in stock.items()}
    retail_total = sum(retail.values())
    quantity_total = Decimal(0)
    for name in sorted(stock):
        quantity, value = stock[name]
        average = F.money(F.round_half_up(Decimal(value) / quantity)) if quantity else "0.00"
        rows.append([name, str(quantity), average, F.qb_money(value), _percent(value, value_total),
                     F.qb_money(F.cents(F.item(name)["price"])), F.qb_money(retail[name]),
                     _percent(retail[name], retail_total)])
        quantity_total += quantity
    rows.append(f'"TOTAL",{quantity_total},,"{F.qb_money(value_total)}","100.0%",,"{F.qb_money(retail_total)}","100.0%"')
    return _csv(rows).encode("cp1252")


def reconciliation_summary_csv(account: str, register: int) -> bytes:
    """The June reconciliation the old books finished, as the desktop product's Reconciliation Summary."""
    june = D.JUNE_RECONCILIATION[account]
    uncleared = [u for u in D.UNCLEARED if u[0] == account]
    card = account == "Visa Business Card"
    rows = [[COMPANY], ["Reconciliation Summary"], [f"{F.qb_account(account)}, Period Ending 06/30/2026"],
            ["", "", "", "Jun 30, 26"],
            ["Beginning Balance", "", "", F.qb_money(F.cents(june["beginning"]))]]
    out_label, in_label = (("Charges and Cash Advances", "Payments and Credits") if card
                           else ("Checks and Payments", "Deposits and Credits"))
    out_count, out_total = june["out"]
    in_count, in_total = june["in"]
    cleared = F.cents(out_total) + F.cents(in_total)
    rows += [["", "Cleared Transactions", "", ""],
             ["", "", f"{out_label} - {out_count} item{'' if out_count == 1 else 's'}", F.qb_money(F.cents(out_total))],
             ["", "", f"{in_label} - {in_count} item{'' if in_count == 1 else 's'}", F.qb_money(F.cents(in_total))],
             ["", "Total Cleared Transactions", "", F.qb_money(cleared)],
             ["Cleared Balance", "", "", F.qb_money(F.cents(june["beginning"]) + cleared)]]
    assert F.cents(june["beginning"]) + cleared == F.cents(D.JUNE_STATEMENTS[account])
    outs = [F.cents(u[6]) for u in uncleared if (F.cents(u[6]) > 0) == card]
    ins = [F.cents(u[6]) for u in uncleared if (F.cents(u[6]) > 0) != card]
    rows.append(["", "Uncleared Transactions", "", ""])
    if outs:
        rows.append(["", "", f"{out_label} - {len(outs)} item{'s' if len(outs) > 1 else ''}",
                     F.qb_money(sum(outs))])
    if ins:
        rows.append(["", "", f"{in_label} - {len(ins)} item{'s' if len(ins) > 1 else ''}", F.qb_money(sum(ins))])
    rows.append(["", "Total Uncleared Transactions", "", F.qb_money(sum(outs) + sum(ins))])
    rows.append(["Register Balance as of 06/30/2026", "", "", F.qb_money(register)])
    rows.append(["Ending Balance", "", "", F.qb_money(register)])
    return _csv(rows).encode("cp1252")


def uncleared_csv() -> bytes:
    rows = [[COMPANY], ["Uncleared Bank and Card Transactions"], ["As of June 30, 2026"],
            ["", "", "Type", "Date", "Num", "Name", "Memo", "Clr", "Amount", "Balance"]]
    total = 0
    for account in ("Checking", "Visa Business Card"):
        rows.append([F.qb_account(account), *[""] * 9])
        running = 0
        for acct, kind, when, number, name, memo, amount, _ in D.UNCLEARED:
            if acct != account:
                continue
            value = F.cents(amount) if account == "Checking" else -F.cents(amount)
            running += value
            rows.append(["", "", kind, F.us(F.day(when)), number, name, memo, "", F.qb_money(value), F.qb_money(running)])
        rows.append([f"Total {F.qb_account(account)}", *[""] * 7, F.qb_money(running), F.qb_money(running)])
        total += running
    rows.append(["TOTAL", *[""] * 7, F.qb_money(total), F.qb_money(total)])
    return _csv(rows).encode("cp1252")


def undeposited_csv() -> bytes:
    """The receipts still waiting in Undeposited Funds, as the desktop product's help has a person list them: the
    account's QuickReport, Dates All, filtered to Cleared No (a receipt is cleared there once it is deposited)."""
    account = F.qb_account("Undeposited Funds")
    rows = [[COMPANY], ["Account QuickReport"], ["All Transactions"],
            ["", "", "Type", "Date", "Num", "Name", "Memo", "Split", "Amount", "Balance"], [account, *[""] * 9]]
    running = 0
    for when, name, number, amount, _ in D.UNDEPOSITED:
        running += F.cents(amount)
        rows.append(["", "", "Payment", F.us(F.day(when)), number, name, "", F.qb_account("Accounts Receivable"),
                     F.qb_money(F.cents(amount)), F.qb_money(running)])
    rows.append([f"Total {account}", *[""] * 7, F.qb_money(running), F.qb_money(running)])
    rows.append(["TOTAL", *[""] * 7, F.qb_money(running), F.qb_money(running)])
    return _csv(rows).encode("cp1252")


def vendor_1099_csv() -> bytes:
    """The old books' 1099 Summary for the year so far (Vendors & Payables > 1099 Summary, January 1 to the cutover,
    thresholds ignored): one row per 1099 vendor, one column per 1099 box, and the total."""
    paid_bills = F.cents(D.CUTOVER_BALANCES["Subcontractors"]) - sum(
        F.cents(amount) for vendor, *_, amount in D.UNPAID_BILLS if vendor == "Delgado, Ray")
    assert F.cents(D.VENDOR_1099_PAID["Delgado, Ray"]) == paid_bills
    rows = [[COMPANY], ["1099 Summary"], [f"January through {F.CUTOVER:%B %Y}"],
            ["", "", "Box 1 Nonemployee Compensation", "TOTAL"]]
    total = 0
    for name, amount in sorted(D.VENDOR_1099_PAID.items()):
        rows.append([name, "", F.qb_money(F.cents(amount)), F.qb_money(F.cents(amount))])
        total += F.cents(amount)
    rows.append(["TOTAL", "", F.qb_money(total), F.qb_money(total)])
    return _csv(rows).encode("cp1252")


# ---------------------------------------------------------------- statements

_KIND_ORDER = {"deposit": 0, "transfer": 1, "interest": 2, "check": 3, "ach": 4, "returned": 5, "fee": 6, "charge": 3,
               "credit": 1, "payment": 0}


def statement_lines(books: F.Books, account: str, first: date, last: date) -> list[F.Move]:
    lines = [m for m in books.moves if m.account == account and m.cleared and first <= m.cleared <= last]
    order = {id(m): n for n, m in enumerate(books.moves)}
    return sorted(lines, key=lambda m: (m.cleared, _KIND_ORDER.get(m.kind, 9), order[id(m)]))


def _fitid(m: F.Move, n: int) -> str:
    return f"{m.cleared:%Y%m%d}{n:04d}"


def checking_ofx(books: F.Books, first: date, last: date, beginning: int) -> tuple[bytes, int]:
    lines = statement_lines(books, "Checking", first, last)
    ending = beginning + sum(m.amount for m in lines)
    out = ["OFXHEADER:100", "DATA:OFXSGML", "VERSION:102", "SECURITY:NONE", "ENCODING:USASCII", "CHARSET:1252",
           "COMPRESSION:NONE", "OLDFILEUID:NONE", "NEWFILEUID:NONE", "",
           "<OFX>", "<SIGNONMSGSRSV1><SONRS>", "<STATUS><CODE>0", "<SEVERITY>INFO", "</STATUS>",
           f"<DTSERVER>{last + timedelta(days=1):%Y%m%d}063000", "<LANGUAGE>ENG",
           "<FI><ORG>Cedar Prairie Bank", "<FID>7104", "</FI>", "</SONRS></SIGNONMSGSRSV1>",
           "<BANKMSGSRSV1><STMTTRNRS>", "<TRNUID>1", "<STATUS><CODE>0", "<SEVERITY>INFO", "</STATUS>", "<STMTRS>",
           "<CURDEF>USD", "<BANKACCTFROM><BANKID>071923284", "<ACCTID>000044714471", "<ACCTTYPE>CHECKING",
           "</BANKACCTFROM>", "<BANKTRANLIST>", f"<DTSTART>{first:%Y%m%d}", f"<DTEND>{last:%Y%m%d}"]
    counter = defaultdict(int)
    for m in lines:
        counter[m.cleared] += 1
        out += ["<STMTTRN>", f"<TRNTYPE>{m.trntype or ('CREDIT' if m.amount > 0 else 'DEBIT')}",
                f"<DTPOSTED>{m.cleared:%Y%m%d}120000", f"<TRNAMT>{F.money(m.amount)}",
                f"<FITID>{_fitid(m, counter[m.cleared])}"]
        if m.trntype == "CHECK":
            out.append(f"<CHECKNUM>{m.number}")
        out.append(f"<NAME>{m.desc[:32]}")
        if m.memo:
            out.append(f"<MEMO>{m.memo}")
        out.append("</STMTTRN>")
    out += ["</BANKTRANLIST>", f"<LEDGERBAL><BALAMT>{F.money(ending)}", f"<DTASOF>{last:%Y%m%d}", "</LEDGERBAL>",
            f"<AVAILBAL><BALAMT>{F.money(ending)}", f"<DTASOF>{last:%Y%m%d}", "</AVAILBAL>", "</STMTRS>",
            "</STMTTRNRS></BANKMSGSRSV1>", "</OFX>"]
    return (CRLF.join(out) + CRLF).encode("ascii"), ending


_BANK_TYPE = {"DEP": "DEPOSIT", "CHECK": "CHECK_PAID", "DEBIT": "ACH_DEBIT", "XFER": "ACCT_XFER", "PAYMENT": "LOAN_PMT",
              "FEE": "FEE_TRANSACTION", "SRVCHG": "FEE_TRANSACTION", "INT": "MISC_CREDIT"}


def _cell(text: str) -> str:
    return f'"{text}"' if ("," in text or '"' in text) else text


def bank_csv(books: F.Books, account: str, first: date, last: date, beginning: int) -> tuple[bytes, int]:
    """The bank's CSV download: newest first, a running balance, checks and deposit slips numbered."""
    lines = statement_lines(books, account, first, last)
    balance, rows = beginning, []
    for m in lines:
        balance += m.amount
        details = "CHECK" if m.kind == "check" else "DSLIP" if m.kind == "deposit" else (
            "CREDIT" if m.amount > 0 else "DEBIT")
        kind = "MISC_DEBIT" if m.kind == "returned" else "ACCT_XFER" if m.kind == "transfer" else _BANK_TYPE[m.trntype]
        text = m.desc + (" " + m.memo if m.memo else "")
        if m.kind == "check":
            text = f"CHECK {m.number}"
        rows.append([details, F.us(m.cleared), text, F.money(m.amount), kind, F.money(balance),
                     m.number if m.kind == "check" else ""])
    out = ["Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #"]
    for row in reversed(rows):
        out.append(",".join(_cell(c) for c in row))
    return (CRLF.join(out) + CRLF).encode("ascii"), balance


def card_summary(books: F.Books, first: date, last: date, beginning: int) -> bytes:
    """The statement's summary box, which a card's CSV download leaves out."""
    lines = statement_lines(books, "Visa Business Card", first, last)
    payments = sum(m.amount for m in lines if m.kind == "payment")
    credits = sum(m.amount for m in lines if m.kind == "credit")
    purchases = sum(m.amount for m in lines if m.kind == "charge")
    ending = beginning + payments + credits + purchases
    due = F.next_business_day(last + timedelta(days=25))
    out = ["Cedar Prairie Bank Visa Business, account ending 9012", "Harbor Electric LLC",
           f"Statement closing date {F.us(last)}", "",
           f"Previous balance        {F.qb_money(beginning):>12}",
           f"Payments                {F.qb_money(payments):>12}",
           f"Other credits           {F.qb_money(credits):>12}",
           f"Purchases               {F.qb_money(purchases):>12}",
           f"New balance             {F.qb_money(ending):>12}", "",
           f"Payment due date {F.us(due)}. Transactions are in the CSV download for this period."]
    return (CRLF.join(out) + CRLF).encode("ascii")


def card_csv(books: F.Books, first: date, last: date, beginning: int) -> tuple[bytes, int]:
    lines = statement_lines(books, "Visa Business Card", first, last)
    owed, rows = beginning, []
    for m in lines:
        owed += m.amount
        kind = {"charge": "Sale", "credit": "Return", "payment": "Payment"}[m.kind]
        rows.append([F.us(m.card_date), F.us(m.cleared), m.desc, m.category, kind, F.money(-m.amount), ""])
    out = ["Transaction Date,Post Date,Description,Category,Type,Amount,Memo"]
    for row in reversed(rows):
        out.append(",".join(_cell(c) for c in row))
    return (CRLF.join(out) + CRLF).encode("ascii"), owed


# ---------------------------------------------------------------- the paperwork

def _short(d: date) -> str:
    return f"{d:%a} {d.month}/{d.day}"


def _amt(c: int) -> str:
    return "$" + F.qb_money(c)


def _lines_text(lines: list[dict], tax: str, taxable: str, total: str, indent: str = "    ") -> list[str]:
    out = []
    for line in lines:
        name = line["item"]
        if line["quantity"] is None:
            out.append(f"{indent}{name} ({line['rate']}) {F.qb_money(F.cents(line['amount']))}  {line['desc']}")
            continue
        quantity = Decimal(line["quantity"])
        shown = f"{quantity.normalize():f}" + (" hr" if F.item(name)["kind"] == "SERV" and name.endswith(("Labor", "Hour"))
                                              else "")
        what = f"{name} {shown} @ {line['rate']}" if (quantity != 1 or F.item(name)["kind"] in ("SERV", "INVENTORY")) \
            else name
        note = f"  {line['desc']}" if line["desc"] != F.item(name)["desc"] else ""
        out.append(f"{indent}{what} = {F.qb_money(F.cents(line['amount']))}{note}")
    if F.cents(tax):
        out.append(f"{indent}Sales tax 8.75% on {F.qb_money(F.cents(taxable))} = {F.qb_money(F.cents(tax))}")
    out.append(f"{indent}Total {F.qb_money(F.cents(total))}")
    return out


def _sentence(ev: dict, events: dict) -> list[str]:
    kind, when = ev["kind"], F.day(ev["date"])
    note = ev.get("note", "")
    if kind == "invoice":
        info = F.customer(ev["customer"])
        head = f"- Invoice {ev['number']}, {_short(when)}, {ev['customer']} ({ev['terms']}"
        head += f", due {F.day(ev['due']).month}/{F.day(ev['due']).day})" if ev["terms"] != "Due on receipt" else ")"
        if ev.get("po"):
            head += f", their P.O. {ev['po']}"
        if ev.get("new_customer"):
            c = info
            head += f"  [new customer: {c['first']} {c['last']}, {', '.join(c['street'])}, {c['city']}, {c['phone']}]"
        if not info["taxable"]:
            head += "  (no sales tax: " + ("exempt, " + info["resale"] if info["resale"] else "contractor work") + ")"
        return [head, *_lines_text(ev["lines"], ev["tax"], ev["taxable"], ev["total"]), f"    {note}"]
    if kind == "sales_receipt":
        return [f"- Cash sale {ev['number']}, {_short(when)}, {ev['customer']}, paid {ev['method'].lower()}",
                *_lines_text(ev["lines"], ev["tax"], ev["taxable"], ev["total"]), f"    {note}"]
    if kind == "credit_memo":
        return [f"- Credit memo {ev['number']}, {_short(when)}, {ev['customer']} (against invoice {ev['source_invoice']})",
                *_lines_text(ev["lines"], ev["tax"], ev["taxable"], ev["total"]), f"    {note}"]
    if kind == "payment":
        paid = []
        for a in ev["applied"]:
            text = a["invoice"]
            if F.cents(a["discount"]):
                text += f" (less their discount {F.qb_money(F.cents(a['discount']))})"
            paid.append(text)
        how = "cash" if ev["method"] == "Cash" else f"check #{ev['ref']}"
        return [f"- {_short(when)}: {ev['customer']}, {how}, {_amt(F.cents(ev['amount']))} for "
                + ", ".join(paid) + f". {note}"]
    if kind == "deposit":
        parts = []
        for item_ in ev["items"]:
            source = item_["source"]
            if source.startswith("uf:"):
                name = source[3:]
                number = next(n for _, who, n, _, _ in D.UNDEPOSITED if who == name)
                parts.append(f"{name} #{number} {F.qb_money(F.cents(item_['amount']))}")
            else:
                src = events[source]
                label = "cash sale " + src["number"] if src["kind"] == "sales_receipt" else (
                    f"{src['customer']} " + ("cash" if src.get("method") == "Cash" else f"#{src['ref']}"))
                parts.append(f"{label} {F.qb_money(F.cents(item_['amount']))}")
        return [f"- {_short(when)}: deposited {', '.join(parts)}. Deposit total {_amt(F.cents(ev['total']))}. {note}"]
    if kind in ("bill", "vendor_credit"):
        label = "bill" if kind == "bill" else "credit memo"
        head = f"- {ev['vendor']} {label} {ev['number']}, dated {when.month}/{when.day}"
        if kind == "bill":
            due = F.day(ev["due"])
            head += f", {ev['terms']}, due {due.month}/{due.day}"
        head += f": {_amt(F.cents(ev['total']))}"
        body = []
        for line in ev["lines"]:
            job = f"  (job: {line['job']})" if line.get("job") else ""
            if "item" in line:
                body.append(f"    {line['item']} {Decimal(line['quantity']).normalize():f} @ {line['cost']} = "
                            f"{F.qb_money(F.cents(line['amount']))}{job}")
            else:
                body.append(f"    {line['memo']}: {F.qb_money(F.cents(line['amount']))}{job}")
        return [head, *body, f"    {note}"]
    if kind == "bill_payment":
        bills = []
        for b in ev["bills"]:
            text = f"{b['bill']} {F.qb_money(F.cents(b['amount']))}"
            if F.cents(b["discount"]):
                text += f" (took the {F.qb_money(F.cents(b['discount']))} discount)"
            bills.append(text)
        how = f"check {ev['number']}" if ev["method"] == "Check" else "online/autopay"
        return [f"- {_short(when)}: paid {ev['vendor']} {_amt(F.cents(ev['total']))} by {how}: {', '.join(bills)}. {note}"]
    if kind == "check":
        total = F.cents(ev["total"])
        detail = ev["lines"][0][2] if len(ev["lines"]) == 1 else ""
        how = f"check {ev['number']}" if ev["number"] != "ACH" else "came out of checking"
        if ev["number"] == "ACH":
            return [f"- {_short(when)}: {ev['payee']} {_amt(total)} {how}" + (f" ({detail})" if detail else "")
                    + f". {note}"]
        return [f"- {_short(when)}: {how} to {ev['payee']} {_amt(total)} ({detail}). {note}"]
    if kind in ("card_charge", "card_credit"):
        what = "credit" if kind == "card_credit" else ""
        payee = ev.get("payee") or ev["desc"].title()
        account, amount, memo = ev["lines"][0]
        return [f"- {_short(when)}: {payee} {what + ' ' if what else ''}{_amt(F.cents(amount))} - {memo}. {note}"]
    if kind == "transfer":
        return [f"- {_short(when)}: {note} ({_amt(F.cents(ev['amount']))}, {ev['from_account']} to {ev['to_account']})"]
    if kind == "sales_tax_payment":
        return [f"- {_short(when)}: {note}"]
    if kind == "customer_refund":
        return [f"- {_short(when)}: check {ev['number']} to {ev['customer']} {_amt(F.cents(ev['amount']))}. {note}"]
    if kind == "payroll":
        out = [f"- Paywell payroll for pay date {F.day(ev['pay_date']).month}/{F.day(ev['pay_date']).day}, "
               f"taken from checking {_short(when)}: gross wages {F.qb_money(F.cents(ev['gross']))}, employee taxes "
               f"withheld {F.qb_money(F.cents(ev['employee_taxes']))}, employer taxes "
               f"{F.qb_money(F.cents(ev['employer_taxes']))}. Two debits: net pay {F.qb_money(F.cents(ev['net']))} "
               f"and taxes {F.qb_money(F.cents(ev['tax_debit']))}."]
        for row in ev["employees"]:
            first, last = row["employee"].split(", ")[1], row["employee"].split(", ")[0]
            hours = f"{row['regular']} hrs" + (f" + {row['overtime']} OT" if row["overtime"] != "0" else "")
            out.append(f"    {first} {last}: {hours}, gross {F.qb_money(F.cents(row['gross']))}, "
                       f"net {F.qb_money(F.cents(row['net']))}")
        if note:
            out.append(f"    {note}")
        return out
    if kind in ("bounced_check", "credit_apply", "vendor_credit_apply", "inventory_adjust", "write_off"):
        return [f"- {_short(when)}: {note}"]
    raise KeyError(kind)


SECTIONS = [
    ("INVOICES AND SALES", ("invoice", "sales_receipt", "credit_memo")),
    ("MONEY IN", ("payment",)),
    ("DEPOSITS", ("deposit",)),
    ("BILLS THAT CAME IN", ("bill", "vendor_credit")),
    ("PAID (checks, online and autopay)", ("bill_payment", "check", "customer_refund", "sales_tax_payment", "transfer")),
    ("VISA CARD", ("card_charge", "card_credit")),
    ("PAYROLL", ("payroll",)),
    ("OTHER", ("bounced_check", "credit_apply", "vendor_credit_apply", "inventory_adjust", "write_off")),
]


def paperwork(events: list[dict], title: str, intro: list[str], everything: list[dict]) -> bytes:
    by_id = {ev["id"]: ev for ev in everything}
    out = [title, "=" * len(title), *intro, ""]
    for heading, kinds in SECTIONS:
        chosen = [ev for ev in events if ev["kind"] in kinds]
        if not chosen:
            continue
        out.append(heading)
        for ev in chosen:
            out += _sentence(ev, by_id)
        out.append("")
    return ("\n".join(line.rstrip() for line in out).rstrip() + "\n").encode("utf-8")


# ---------------------------------------------------------------- the answer key

def key(snapshot: F.Snapshot, books: F.Books, previous: F.Snapshot | None) -> dict:
    as_of = snapshot.as_of
    gl = snapshot.gl
    tb = [dict(number=F.ACCOUNT_NUMBERS[name], account=name, balance=F.money(gl[name]))
          for name in F.ACCOUNT_ORDER if gl.get(name)]
    debits = sum(v for v in gl.values() if v > 0)

    def aging(docs: dict) -> dict:
        open_docs = [d for d in docs.values() if d.open]
        _, detail = _aging_lines(open_docs, as_of, "")
        rows = [dict(name=name, **{b: F.money(v) for b, v in zip(BUCKETS, sums)}, total=F.money(sum(sums)))
                for name, sums in sorted(detail.items())]
        total = [sum(s[n] for s in detail.values()) for n in range(5)]
        return dict(rows=rows, **{b: F.money(v) for b, v in zip(BUCKETS, total)}, total=F.money(sum(total)),
                    documents=[dict(kind=d.kind, number=d.number, name=d.name, date=d.date.isoformat(),
                                    due=d.due.isoformat() if d.due else None, open=F.money(d.open))
                               for d in sorted(open_docs, key=lambda d: (d.name, d.date, d.number))])

    recs = {}
    for account in ("Checking", "Savings", "Visa Business Card"):
        card = account == "Visa Business Card"
        book = -gl.get(account, 0) if card else gl.get(account, 0)
        first = (previous.as_of + timedelta(days=1)) if previous else None
        cleared = [m for m in books.moves if m.account == account and m.cleared and first and first <= m.cleared <= as_of]
        outstanding = [m for m in books.moves if m.account == account and m.date <= as_of
                       and (m.cleared is None or m.cleared > as_of)]
        beginning = F.cents(D.JUNE_STATEMENTS[account]) + sum(
            m.amount for m in books.moves if m.account == account and m.cleared and F.CUTOVER < m.cleared < (first or as_of))
        ending = beginning + sum(m.amount for m in cleared) if first else F.cents(D.JUNE_STATEMENTS[account])
        assert ending + sum(m.amount for m in outstanding) == book, (account, as_of)
        row = lambda m: dict(date=m.date.isoformat(), posted=m.cleared.isoformat() if m.cleared else None,
                             amount=F.money(m.amount), kind=m.kind, number=m.number, name=m.name,
                             description=m.desc, event=m.event)
        recs[account] = dict(statement_date=as_of.isoformat(), beginning_balance=F.money(beginning) if first else None,
                             ending_balance=F.money(ending), cleared=[row(m) for m in cleared] if first else None,
                             outstanding=[row(m) for m in outstanding], book_balance=F.money(book),
                             sign="amounts are what is owed: a charge is positive" if card else
                             "amounts are money in: a deposit is positive")
    return dict(
        company=COMPANY, as_of=as_of.isoformat(), conventions=F.CONVENTIONS,
        trial_balance=dict(rows=tb, debits=F.money(debits), credits=F.money(debits)),
        receivables=aging(snapshot.ar), payables=aging(snapshot.ap),
        sales_tax=[dict(agency=F.AGENCY, owed=F.money(snapshot.tax))],
        inventory=dict(rows=[dict(item=name, on_hand=f"{q.normalize():f}", value=F.money(v))
                             for name, (q, v) in sorted(snapshot.stock.items())],
                       value=F.money(sum(v for _, v in snapshot.stock.values()))),
        reconciliations=recs,
    )


# ---------------------------------------------------------------- everything

def render() -> dict[str, bytes]:
    books, snapshots = F.build()
    cut = snapshots[0]
    files: dict[str, bytes] = {}
    old = "handed-over/old-books/"
    files[old + "lists.iif"] = lists_iif()
    files[old + "trial_balance.csv"] = trial_balance_csv(cut.gl, F.CUTOVER)
    files[old + "open_invoices.csv"] = open_invoices_csv(cut.ar, F.CUTOVER)
    files[old + "unpaid_bills.csv"] = unpaid_bills_csv(cut.ap, F.CUTOVER)
    files[old + "ar_aging.csv"] = ar_aging_csv(cut.ar, F.CUTOVER)
    files[old + "ap_aging.csv"] = ap_aging_csv(cut.ap, F.CUTOVER)
    files[old + "inventory_valuation.csv"] = inventory_valuation_csv(cut.stock, F.CUTOVER)
    files[old + "reconciliation_summary_checking_2026-06.csv"] = reconciliation_summary_csv("Checking", cut.gl["Checking"])
    files[old + "reconciliation_summary_savings_2026-06.csv"] = reconciliation_summary_csv("Savings", cut.gl["Savings"])
    files[old + "reconciliation_summary_visa_2026-06.csv"] = reconciliation_summary_csv(
        "Visa Business Card", -cut.gl["Visa Business Card"])
    files[old + "uncleared_2026-06-30.csv"] = uncleared_csv()
    files[old + "undeposited_funds_2026-06-30.csv"] = undeposited_csv()
    files[old + "vendor_1099_summary_2026-06.csv"] = vendor_1099_csv()

    beginning = {k: F.cents(v) for k, v in D.JUNE_STATEMENTS.items()}
    for month_end in F.MONTH_ENDS:
        first = month_end.replace(day=1)
        folder = f"handed-over/{month_end:%Y-%m}/"
        ofx, ending = checking_ofx(books, first, month_end, beginning["Checking"])
        files[folder + f"checking-{month_end:%Y-%m}.ofx"] = ofx
        csv_bytes, csv_ending = bank_csv(books, "Checking", first, month_end, beginning["Checking"])
        assert csv_ending == ending
        files[folder + f"checking-{month_end:%Y-%m}.csv"] = csv_bytes
        savings, savings_end = bank_csv(books, "Savings", first, month_end, beginning["Savings"])
        files[folder + f"savings-{month_end:%Y-%m}.csv"] = savings
        card, card_end = card_csv(books, first, month_end, beginning["Visa Business Card"])
        files[folder + f"visa-{month_end:%Y-%m}.csv"] = card
        files[folder + f"visa-{month_end:%Y-%m}-summary.txt"] = card_summary(books, first, month_end,
                                                                            beginning["Visa Business Card"])
        beginning = {"Checking": ending, "Savings": savings_end, "Visa Business Card": card_end}
        month_events = [ev for ev in books.events if first <= F.day(ev["date"]) <= month_end
                        and ev["kind"] not in ("bank_charge", "bank_interest")]
        if month_end.month == 7:
            weeks = [(date(2026, 7, 1), date(2026, 7, 5)), (date(2026, 7, 6), date(2026, 7, 12)),
                     (date(2026, 7, 13), date(2026, 7, 19)), (date(2026, 7, 20), date(2026, 7, 26)),
                     (date(2026, 7, 27), date(2026, 7, 31))]
            for n, (start, end) in enumerate(weeks, start=1):
                chosen = [ev for ev in month_events if start <= F.day(ev["date"]) <= end]
                intro = [f"From Mike. Everything from {start:%B} {start.day} to {end:%B} {end.day}: invoices, checks, "
                         "bills, card receipts. Card receipts are on the Visa unless I say otherwise."]
                if n == 1:
                    intro += ["", "We stopped using QuickBooks on June 30. The exports and the June reconciliation "
                              "reports are in the old-books folder. The June statements ended at: checking "
                              f"{_amt(F.cents(D.JUNE_STATEMENTS['Checking']))}, savings "
                              f"{_amt(F.cents(D.JUNE_STATEMENTS['Savings']))}, Visa "
                              f"{_amt(F.cents(D.JUNE_STATEMENTS['Visa Business Card']))}."]
                files[folder + f"paperwork-2026-07-week-{n}.txt"] = paperwork(
                    chosen, f"Harbor Electric paperwork, week {n}: {start:%b} {start.day} - {end:%b} {end.day}, 2026",
                    intro, books.events)
        else:
            files[folder + f"paperwork-{month_end:%Y-%m}.txt"] = paperwork(
                month_events, f"Harbor Electric paperwork, {month_end:%B %Y}",
                ["From Mike. The whole month in one go this time, shorter. Same as always: card receipts are on "
                 "the Visa."], books.events)

    files["answer/events.json"] = (json.dumps(books.events, indent=1, default=str) + "\n").encode("utf-8")
    previous = None
    for snapshot in snapshots:
        files[f"answer/key-{snapshot.as_of.isoformat()}.json"] = (
            json.dumps(key(snapshot, books, previous), indent=1) + "\n").encode("utf-8")
        previous = snapshot
    return files


def main(argv: list[str]) -> None:
    out = Path(argv[0]) if argv else F.OUT
    files = render()
    for path, data in files.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(f"wrote {len(files)} files under {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
