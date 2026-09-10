"""The four documents a customer receives, produced as PDFs from the pages they live on.

Until this existed every sales workflow ended at a data-entry form: the books knew what
the customer owed and there was no way to hand them anything that said so. Printing here
is producing the document's PDF and letting the browser print it, which is the same act a
later attach-to-record or mail command performs -- one renderer, one layout.

A route that answers 200 proves nothing about a document, so every check below reads the
text back out of the PDF and looks for the customer's name, the line amounts and the
total. The affordance is checked the same way: the link is found on the record page the
user is actually standing on, and the file is fetched from that link's own href rather
than from a path this file made up.
"""
import json
import re
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from bookflow.documents import model, render
from tests.test_row3_host import PASSWORD, WB, hosted  # noqa: F401
from tests.test_row5_browser_acceptance import CHROME, _Cdp, browser_site  # noqa: F401
from tests.test_row8_register_browser import _command

FROM, TO = "2026-06-01", "2026-06-30"
LINK = re.compile(r'<a[^>]+class="(?:primary-action|statement-print)"[^>]+href="([^"]+)"[^>]*>'
                  r'\s*Print / save PDF\s*</a>')


def browser(hosted) -> TestClient:  # noqa: F811
    client = TestClient(hosted.handle.app)
    assert client.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return client


def commands(hosted):  # noqa: F811
    """Everything a document needs, posted through the host exactly as an agent would."""
    def run(command, body, *, dry_run=False):
        path = f"/companies/{hosted.company_id}/commands/{command.replace(' ', '.')}"
        response = hosted.api.post(path + ("?dry_run=true" if dry_run else ""),
                                   json=body, headers=hosted.bearer)
        assert response.status_code == 200, response.text
        return response.json()
    return run


def seed(run):
    """One customer with an invoice, a receipt, an estimate and a part payment.

    Posted through whichever surface `run(command, input, dry_run=)` speaks, so the
    checks below and the phone-width witness are looking at the same books.
    """
    income = run("account create", {"name": "Print income", "type": "income"})["id"]
    bank = run("account create", {"name": "Print bank", "type": "bank"})["id"]
    code = next(row["id"] for row in run("sales-tax-code list", {})["items"] if not row["taxable"])
    labour = run("item create", {"name": "Site labour", "type": "service", "sales_enabled": True,
        "description": "Two hours on the riser", "income_account_id": income,
        "price": "120.00", "sales_tax_code_id": code})["id"]
    parts = run("item create", {"name": "Copper elbow", "type": "non_inventory_part", "sales_enabled": True,
        "description": "22mm elbow", "income_account_id": income, "price": "15.00",
        "sales_tax_code_id": code})["id"]
    customer = run("customer create", {"name": "Harbour Mills", "company_name": "Harbour Mills Ltd",
        "billing_address": {"line1": "8 Quay Street", "city": "Springfield",
                            "state": "IL", "postal_code": "62704"}})["id"]
    method = run("payment-method list", {})["items"][0]["id"]
    invoice = run("invoice post", {"number": "DOC-100", "date": "2026-06-03",
        "due_date": "2026-07-03", "customer": customer, "memo": "Riser repair, first visit",
        "lines": [{"item": labour, "quantity": "2"}, {"item": parts, "quantity": "4"}]})
    receipt = run("sales-receipt post", {"number": "DOC-200", "date": "2026-06-05",
        "customer": customer, "deposit_to": bank, "payment_method": method,
        "memo": "Paid at the door",
        "lines": [{"item": parts, "quantity": "2"}]})
    quote = {"date": "2026-06-08", "title": "Second riser", "customer": customer,
             "memo": "Valid for thirty days",
             "lines": [{"item": labour, "quantity": "5"}, {"item": parts, "quantity": "10"}]}
    preview = run("estimate create", quote, dry_run=True)
    estimate = run("estimate create", dict(quote, expected_facts_fingerprint=preview["facts_fingerprint"]))
    run("payment receive", {"customer": customer, "date": "2026-06-20", "amount": "100.00",
        "number": "RCT-900", "operation_key": "document-print-1", "payment_method": method,
        "applications": {"mode": "inline", "items": [
            {"invoice": invoice["id"], "expected_version": 1, "amount": "100.00"}]}})
    return dict(run=run, customer=customer, invoice=invoice, receipt=receipt,
                estimate=estimate, labour=labour, income=income)


@pytest.fixture
def books(hosted):  # noqa: F811
    return seed(commands(hosted))


def text_of(response) -> str:
    """Every page of the returned PDF as text, which is what a reader actually gets."""
    assert response.status_code == 200, response.text[:400]
    assert response.headers["content-type"].startswith("application/pdf"), response.headers
    reader = PdfReader(BytesIO(response.content))
    return "\n".join(page.extract_text() for page in reader.pages)


def affordance(page_text: str) -> str:
    """The print destination the page itself declares, taken from the page, not invented."""
    found = LINK.search(page_text)
    assert found, "this page offers the reader no way to print the document"
    return found.group(1).replace("&amp;", "&")


# ------------------------------------------------------------------ the four documents

def test_an_invoice_prints_what_the_customer_owes(books, hosted):  # noqa: F811
    reader = browser(hosted)
    page = reader.get(f"/c/{hosted.company_id}/invoice/{books['invoice']['id']}", headers=WB)
    assert page.status_code == 200
    href = affordance(page.text)
    assert href == f"/c/{hosted.company_id}/invoice/{books['invoice']['id']}/print"

    printed = reader.get(href, headers=WB, follow_redirects=False)
    assert 'filename="invoice-DOC-100.pdf"' in printed.headers["content-disposition"]
    body = text_of(printed)
    assert "Demo Plumbing Co" in body and "100 Main St" in body
    assert "Harbour Mills" in body and "8 Quay Street" in body
    assert "INVOICE" in body and "DOC-100" in body
    assert "2026-06-03" in body and "2026-07-03" in body
    # Both line items, priced as the books priced them, and the arithmetic they add to.
    assert "Site labour" in body and "Two hours on the riser" in body
    assert "Copper elbow" in body and "240.00" in body and "60.00" in body
    assert "300.00" in body
    # What is still owed after the part payment, which is the reason to send this at all.
    assert "Payments and credits" in body and "100.00" in body
    assert "Balance due" in body and "200.00" in body
    assert "Riser repair, first visit" in body
    assert "Page 1 of 1" in body


def test_a_sales_receipt_prints_as_a_paid_document(books, hosted):  # noqa: F811
    reader = browser(hosted)
    page = reader.get(f"/c/{hosted.company_id}/sales-receipt/{books['receipt']['id']}", headers=WB)
    href = affordance(page.text)
    assert href == f"/c/{hosted.company_id}/sales-receipt/{books['receipt']['id']}/print"

    body = text_of(reader.get(href, headers=WB))
    assert "SALES RECEIPT" in body and "DOC-200" in body
    assert "Harbour Mills" in body
    assert "Copper elbow" in body and "30.00" in body
    assert "Total" in body
    # A receipt is settled by definition; it must not offer a balance to chase.
    assert "Balance due" not in body


def test_an_estimate_prints_as_a_quote_with_no_amount_owed(books, hosted):  # noqa: F811
    reader = browser(hosted)
    page = reader.get(f"/c/{hosted.company_id}/estimate/{books['estimate']['id']}", headers=WB)
    href = affordance(page.text)
    assert href == f"/c/{hosted.company_id}/estimate/{books['estimate']['id']}/print"

    body = text_of(reader.get(href, headers=WB))
    assert "ESTIMATE" in body and "Second riser" in body
    assert "Harbour Mills" in body and "8 Quay Street" in body
    assert "Site labour" in body and "600.00" in body
    assert "Copper elbow" in body and "150.00" in body
    assert "750.00" in body
    assert "Valid for thirty days" in body
    assert "Balance due" not in body


def test_a_customer_statement_prints_the_account_it_shows(books, hosted):  # noqa: F811
    reader = browser(hosted)
    page = reader.post(f"/c/{hosted.company_id}/report/statement",
                       data={"f:date_from": FROM, "f:date_to": TO, "f:limit": "200"}, headers=WB)
    assert page.status_code == 200, page.text[:400]
    href = affordance(page.text)
    assert href.startswith(f"/c/{hosted.company_id}/report/statement/print?")
    assert f"date_from={FROM}" in href and f"date_to={TO}" in href

    printed = reader.get(href, headers=WB)
    assert "statement-Harbour-Mills" in printed.headers["content-disposition"]
    body = text_of(printed)
    assert "STATEMENT" in body and "Demo Plumbing Co" in body
    assert "Harbour Mills" in body and "8 Quay Street" in body
    # The account as the customer reads it: what was charged, what was paid, what is left.
    assert "Balance forward" in body
    assert "DOC-100" in body and "300.00" in body
    assert "RCT-900" in body and "-100.00" in body
    assert "Balance due" in body and "200.00" in body
    # The aging box the anchor prints under a statement.
    for heading in ("CURRENT", "1-30", "31-60", "61-90", "OVER 90", "TOTAL"):
        assert heading in body, heading


# ------------------------------------------------------------------ pagination and refusal

def test_a_long_document_repeats_its_headings_and_never_splits_a_line(books, hosted):  # noqa: F811
    """A stapled invoice must read on page two: headings repeat and no row is cut in half."""
    run = books["run"]
    long_invoice = run("invoice post", {"number": "DOC-300", "date": "2026-06-09",
        "due_date": "2026-07-09", "customer": books["customer"],
        "lines": [{"item": books["labour"], "quantity": str(index + 1)} for index in range(60)]})
    reader = browser(hosted)
    printed = reader.get(f"/c/{hosted.company_id}/invoice/{long_invoice['id']}/print", headers=WB)
    assert printed.status_code == 200
    pages = [page.extract_text() for page in PdfReader(BytesIO(printed.content)).pages]
    assert len(pages) > 1, "sixty lines fitted on one page; this check proves nothing"
    carrying = [number for number, page in enumerate(pages, start=1) if "Site labour" in page]
    assert len(carrying) > 1, "every line landed on one page; this check proves nothing"
    for number, page in enumerate(pages, start=1):
        if number in carrying:
            assert "AMOUNT" in page, f"page {number} carries lines without column headings"
        assert f"Page {number} of {len(pages)}" in page
    # A line item is one row of facts. Sixty descriptions, each printed once and whole,
    # is what "never split across a page" looks like from the reader's side.
    joined = "\n".join(pages)
    assert joined.count("Site labour") == 60
    assert joined.count("Two hours on the riser") == 60


def test_printing_refuses_what_it_cannot_identify(books, hosted):  # noqa: F811
    reader = browser(hosted)
    missing = reader.get(f"/c/{hosted.company_id}/invoice/01ARZ3NDEKTSV4RRFFQ69G5FAV/print", headers=WB)
    assert missing.status_code == 404 and "application/pdf" not in missing.headers["content-type"]
    bare = reader.get(f"/c/{hosted.company_id}/report/statement/print", headers=WB)
    assert bare.status_code == 422 and "application/pdf" not in bare.headers["content-type"]
    assert "customer" in bare.text


def test_the_renderer_is_callable_without_a_request(books, hosted):  # noqa: F811
    """The seam a later attach or mail command uses: a read callable in, bytes out."""
    run = books["run"]
    rendered = render(lambda name, raw, company: run(name, raw), hosted.company_id,
                      "invoice", {"document": books["invoice"]["id"]})
    assert rendered.media_type == "application/pdf"
    assert rendered.filename == "invoice-DOC-100.pdf"
    assert rendered.content.startswith(b"%PDF-")
    assert "DOC-100" in rendered.title
    assert "Harbour Mills" in "\n".join(
        page.extract_text() for page in PdfReader(BytesIO(rendered.content)).pages)
    # The description a route draws from is the same one the bytes were drawn from.
    described = model.build(lambda name, raw, company: run(name, raw), hosted.company_id,
                            "invoice", {"document": books["invoice"]["id"]})
    assert described.title == "Invoice" and described.number == "DOC-100"
    assert [total.label for total in described.totals] == [
        "Subtotal", "Sales tax", "Total", "Payments and credits", "Balance due"]


# ------------------------------------------------------------------ on a real phone

@pytest.mark.timeout(300)
@pytest.mark.skipif(not CHROME.exists(), reason="Chrome is unavailable")
def test_the_print_affordance_fits_a_phone(browser_site, tmp_path):  # noqa: F811
    """The user reviews this product on a phone, and a control that pushes the page
    sideways is how the last invoice layout was rejected. So the measurement is the
    document's own box against its own content at 390px, with the link on the page."""
    site = browser_site
    chrome = _Cdp(tmp_path / "print-chrome")
    try:
        chrome.navigate(site.base_url + "/login")
        chrome.evaluate("""(() => {document.querySelector('[name=username]').value=%s;
            document.querySelector('[name=password]').value=%s;
            document.querySelector('form[hx-post="/login"]').requestSubmit();})()"""
            % (json.dumps(site.login), json.dumps(PASSWORD)))
        chrome.wait_for("!!document.querySelector('.nav-group')")

        def run(command, body, *, dry_run=False):
            name = command.replace(" ", ".") + ("?dry_run=true" if dry_run else "")
            return _command(chrome, site, name, body)

        made = seed(run)
        chrome.viewport(390, 844)
        for noun, key in (("invoice", "invoice"), ("sales-receipt", "receipt"),
                          ("estimate", "estimate")):
            chrome.navigate(f"{site.base_url}/c/{site.company_id}/{noun}/{made[key]['id']}")
            chrome.wait_for("!!document.querySelector('.document-print a')")
            link = chrome.evaluate("""(() => {const a = document.querySelector('.document-print a');
                const box = a.getBoundingClientRect();
                return {text: a.textContent.trim(), href: a.getAttribute('href'),
                        right: box.right, width: box.width, viewport: innerWidth,
                        page: document.documentElement.scrollWidth,
                        client: document.documentElement.clientWidth};})()""")
            assert link["text"] == "Print / save PDF", (noun, link)
            assert link["href"].endswith(f"/{noun}/{made[key]['id']}/print"), (noun, link)
            assert link["width"] > 0 and link["right"] <= link["viewport"] + 1, (noun, link)
            assert link["page"] == link["client"], (noun, "the page scrolls sideways", link)
    finally:
        chrome.close()
