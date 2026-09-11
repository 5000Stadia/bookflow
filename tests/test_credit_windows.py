"""Every credit page answers, and the apply surface carries the versions it needs.

The browser file beside this one drives real Chrome; this one is the part that does not need
a browser at all — that each of the nine credit routes renders, that the four nouns are filed
under the right navigation group rather than falling through to Hub, and that the apply and
unapply forms carry the credit's version and each invoice's version themselves, which is the
whole reason the surface exists.
"""
import re

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

INVOICE_DUE = 10000
APPLIED = 3000


def _browser(hosted) -> TestClient:
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def _books(hosted):
    company = hosted.company_id
    ok = lambda name, payload: hosted.ok(name, payload, company=company)
    income = ok("account.create", dict(name="CW income", type="income"))["id"]
    exempt = next(row["id"] for row in ok("sales-tax-code.list", {})["items"] if not row["taxable"])
    return dict(
        ok=ok, company=company, income=income,
        expense=ok("account.create", dict(name="CW parts", type="expense"))["id"],
        bank=ok("account.create", dict(name="CW checking", type="bank"))["id"],
        item=ok("item.create", dict(name="CW visit", type="service", sales_enabled=True,
                                    income_account_id=income, price="25.00",
                                    description="Site visit", sales_tax_code_id=exempt))["id"],
        customer=ok("customer.create", dict(name="CW homeowner"))["id"],
        vendor=ok("vendor.create", dict(name="CW supply"))["id"],
        method=next(row["id"] for row in ok("payment-method.list", {})["items"]
                    if row["kind"] == "check"))


def _documents(books):
    ok = books["ok"]
    invoice = ok("invoice.post", dict(date="2026-03-02", customer=books["customer"],
                                      due_date="2026-04-01",
                                      lines=[dict(item=books["item"], quantity="4",
                                                  unit_price="25.00")]))
    credit = ok("credit-memo.post", dict(date="2026-03-10", customer=books["customer"],
                                         lines=[dict(item=books["item"], quantity="1",
                                                     unit_price="30.00")]))
    spent = ok("credit-memo.post", dict(date="2026-03-12", customer=books["customer"],
                                        lines=[dict(item=books["item"], quantity="1",
                                                    unit_price="10.00")]))
    return dict(
        invoice=invoice, credit=credit,
        vendor_credit=ok("vendor-credit.post", dict(date="2026-03-11", vendor=books["vendor"],
                                                    supplier_reference="CN-7",
                                                    expenses=[dict(account=books["expense"],
                                                                   amount="15.00",
                                                                   memo="Two boxes back")])),
        refund=ok("customer-refund.post", dict(date="2026-03-20", funding_account=books["bank"],
                                               method=books["method"], check_number="2041",
                                               sources=[dict(credit_memo=spent["id"])])))


def test_the_four_credit_nouns_are_filed_under_their_own_group_not_hub(hosted):
    """`_grouped_nouns` falls through to Hub for a noun with no `ui_group`; these have one."""
    from bookflow.core import registry

    registry.load_all()
    groups = {"credit-memo": "Customers and sales", "customer-refund": "Customers and sales",
              "customer-credit": "Customers and sales", "vendor-credit": "Vendors and purchases"}
    for noun, group in groups.items():
        assert registry.noun_meta(noun).get("ui_group") == group, noun

    browser = _browser(hosted)
    customers = browser.get(f"/c/{hosted.company_id}/_group/customers")
    vendors = browser.get(f"/c/{hosted.company_id}/_group/vendors")
    company = browser.get(f"/c/{hosted.company_id}/_group/company")
    assert customers.status_code == vendors.status_code == company.status_code == 200
    for noun in ("credit-memo", "customer-refund", "customer-credit"):
        assert f"<h3>{noun}</h3>" in customers.text, noun
        assert f"<h3>{noun}</h3>" not in company.text, (noun, "still misfiled under Hub")
    assert "<h3>vendor-credit</h3>" in vendors.text
    assert "<h3>vendor-credit</h3>" not in company.text, "vendor credit is still misfiled under Hub"


def test_every_credit_route_renders_a_usable_page(hosted):
    books = _books(hosted)
    made = _documents(books)
    company = hosted.company_id
    browser = _browser(hosted)
    pages = [
        (f"/c/{company}/credit-memo", "Credit memos"),
        (f"/c/{company}/customer-refund", "Customer refunds"),
        (f"/c/{company}/vendor-credit", "Vendor credits"),
        (f"/c/{company}/credit-memo/{made['credit']['id']}", made["credit"]["number"]),
        (f"/c/{company}/customer-refund/{made['refund']['id']}", made["refund"]["number"]),
        (f"/c/{company}/vendor-credit/{made['vendor_credit']['id']}", "CN-7"),
        (f"/c/{company}/credit-memo/post", "New credit memo"),
        (f"/c/{company}/customer-refund/post", "New customer refund"),
        (f"/c/{company}/vendor-credit/post", "New vendor credit"),
        (f"/c/{company}/credit-memo/{made['credit']['id']}/apply", "apply to invoices"),
        (f"/c/{company}/credit-memo/{made['credit']['id']}/history",
         f"Revision history for {made['credit']['number']}"),
        # Both seeded openings: a return from the invoice, a refund from the credit.
        (f"/c/{company}/credit-memo/post?invoice={made['invoice']['id']}",
         "Returning lines of invoice"),
        (f"/c/{company}/customer-refund/post?credit_memo={made['credit']['id']}", "Paying back"),
        (f"/c/{company}/invoice/{made['invoice']['id']}", "Credit or return this invoice"),
    ]
    for url, needle in pages:
        page = browser.get(url, follow_redirects=False)
        assert page.status_code == 200, (url, page.status_code, page.text[:400])
        assert needle in page.text, (url, needle, page.text[:400])

    # The saved documents show their own figures, not a schema dump.
    credit = browser.get(f"/c/{company}/credit-memo/{made['credit']['id']}").text
    assert "Still available 30.00 USD" in credit
    vendor_credit = browser.get(f"/c/{company}/vendor-credit/{made['vendor_credit']['id']}").text
    assert "Still free 15.00 USD" in vendor_credit
    refund = browser.get(f"/c/{company}/customer-refund/{made['refund']['id']}").text
    assert "2041" in refund and "10.00" in refund

    # The document arrows step between credit memos the way they do between bills.
    assert "Find a credit memo" in credit
    assert "Find a vendor credit" in vendor_credit


def test_the_seeded_return_names_the_source_invoice_and_its_line(hosted):
    books = _books(hosted)
    made = _documents(books)
    line_id = made["invoice"]["revision"]["lines"][0]["line_id"]
    page = _browser(hosted).get(
        f"/c/{hosted.company_id}/credit-memo/post?invoice={made['invoice']['id']}")
    assert page.status_code == 200
    assert f'value="{made["invoice"]["id"]}"' in page.text
    assert f'value="{line_id}"' in page.text
    # The seeded rows are attempted controls, not originals: an original is a comparison
    # baseline and a value equal to it is never submitted, which would drop every row.
    originals = re.search(r'name="originals" value=\'([^\']*)\'',
                          page.text.replace("&#39;", "'")).group(1)
    assert '"lines"' not in originals, originals


def test_the_grids_own_empty_row_panel_does_not_refuse_a_returned_line():
    """The defect this translation exists for, reproduced from the controls the grid renders.

    Every row panel renders its `use_defaults` collection with its own marker, so an untouched
    row posts `use_defaults: []`. A returned line refuses any mention of `use_defaults`, so
    without the translation no return could ever be saved from the browser at all.
    """
    from bookflow.adapters.workbench import credits as Credits, forms as F
    from bookflow.core import registry

    registry.load_all()
    command = registry.get("credit-memo post")
    posted = {
        "action": "preview", "f:customer": "CUST", "f:date": "2026-04-05",
        "collection:lines": "1", "collection:lines:0:use_defaults": "1",
        "c:lines:0:line_id": "", "c:lines:0:source_invoice": "INV",
        "c:lines:0:source_line": "LINE", "c:lines:0:quantity": "1",
        "c:lines:0:item": "", "c:lines:0:description": "", "c:lines:0:unit": "",
        "c:lines:0:unit_price": "", "c:lines:0:class_id": "", "c:lines:0:tax_code": "",
        "c:lines:0:net_amount": "",
    }
    raw, _, _ = F.translate(command, posted, None)
    assert raw["lines"][0]["use_defaults"] == [], "the grid stopped submitting the empty panel"
    with pytest.raises(ValidationError):
        command.input_model.model_validate(raw)

    command.input_model.model_validate(Credits.untouched_return_defaults(raw))
    # A default a person actually chose is left alone, and the command still refuses it.
    chosen, _, _ = F.translate(command, {**posted, "c:lines:0:use_defaults:0:value": "unit_price"},
                               None)
    assert Credits.untouched_return_defaults(chosen)["lines"][0]["use_defaults"] == ["unit_price"]
    with pytest.raises(ValidationError):
        command.input_model.model_validate(chosen)


def test_the_apply_form_carries_the_credit_version_and_each_invoice_version(hosted):
    books = _books(hosted)
    made = _documents(books)
    company = hosted.company_id
    credit, invoice = made["credit"], made["invoice"]
    browser = _browser(hosted)

    page = browser.get(f"/c/{company}/credit-memo/{credit['id']}/apply")
    assert page.status_code == 200
    assert f'name="select:{invoice["id"]}"' in page.text
    assert f'name="version:{invoice["id"]}" value="{invoice["version"]}"' in page.text, \
        "the page must carry the invoice's version rather than make a person find it"

    posted = browser.post(
        f"/c/{company}/credit-memo/{credit['id']}/apply",
        data={f"select:{invoice['id']}": "1", f"version:{invoice['id']}": str(invoice["version"]),
              f"amount:{invoice['id']}": "30.00", "reason": "Goodwill applied"},
        headers={"X-Bookflow-Workbench": "1", "HX-Request": "true"})
    assert posted.status_code == 200 and posted.headers.get("HX-Redirect") == \
        f"/c/{company}/credit-memo/{credit['id']}", (posted.status_code, posted.text[:400])
    settled = hosted.ok("invoice.show", {"invoice": invoice["id"]}, company=company)
    assert settled["settlement_current"]["due_minor_units"] == INVOICE_DUE - APPLIED
    assert hosted.ok("credit-memo.show", {"credit_memo": credit["id"]},
                     company=company)["source_current"]["available_minor_units"] == 0

    # And the same page now carries the application and the invoice's new version, so taking
    # it back off asks for nothing either.
    page = browser.get(f"/c/{company}/credit-memo/{credit['id']}/apply")
    application = re.search(r'name="unapply:([^"]+)"', page.text)
    assert application, page.text[page.text.find("Already applied"):][:600]
    carried = re.search(r'name="invoice-version:' + re.escape(application.group(1))
                        + r'" value="(\d+)"', page.text)
    assert carried and int(carried.group(1)) == settled["version"]
    off = browser.post(
        f"/c/{company}/credit-memo/{credit['id']}/unapply",
        data={f"unapply:{application.group(1)}": "1",
              f"invoice-version:{application.group(1)}": carried.group(1),
              "reason": "Taken back off"},
        headers={"X-Bookflow-Workbench": "1", "HX-Request": "true"})
    assert off.status_code == 200 and off.headers.get("HX-Redirect"), off.text[:400]
    assert hosted.ok("invoice.show", {"invoice": invoice["id"]},
                     company=company)["settlement_current"]["due_minor_units"] == INVOICE_DUE


def test_a_stale_invoice_version_is_refused_on_the_page_rather_than_reconciled(hosted):
    """The page carries versions so nobody hunts for them; it never invents a fresh one."""
    books = _books(hosted)
    made = _documents(books)
    company = hosted.company_id
    credit, invoice = made["credit"], made["invoice"]
    browser = _browser(hosted)
    refused = browser.post(
        f"/c/{company}/credit-memo/{credit['id']}/apply",
        data={f"select:{invoice['id']}": "1", f"version:{invoice['id']}": "999",
              f"amount:{invoice['id']}": "30.00"},
        headers={"X-Bookflow-Workbench": "1", "HX-Request": "true"})
    assert refused.status_code == 200 and not refused.headers.get("HX-Redirect")
    assert "E_VERSION_CONFLICT" in refused.text, refused.text[:600]
    assert hosted.ok("invoice.show", {"invoice": invoice["id"]},
                     company=company)["settlement_current"]["due_minor_units"] == INVOICE_DUE

    # Nothing ticked is a refusal too, and it says so rather than writing an empty apply.
    empty = browser.post(f"/c/{company}/credit-memo/{credit['id']}/apply", data={},
                         headers={"X-Bookflow-Workbench": "1", "HX-Request": "true"})
    assert empty.status_code == 200 and not empty.headers.get("HX-Redirect")
    assert "Tick at least one invoice" in empty.text
