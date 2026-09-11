"""The availability contract for billing groups and batch invoicing.

Registration is not callability. These tests navigate to the two declared destinations the way
a person does -- from the home window's own tiles -- and then drive the batch form through its
Preview button, because a preview that nobody can reach is not a preview.

The journey uses the demo company, where "Riverside Apartments" carries the "Preferred Customer"
price level and the other customers do not. One group, one line, one preview: the table has to
show two different amounts, which is the whole per-customer resolution claim rendered in HTML.
"""

import re

from tests.test_home_window import _browser, _main, navigate_witness  # noqa: F401
from tests.test_row3_host import WB, hosted  # noqa: F401

from bookflow.adapters.workbench import home

ROW = re.compile(r'<tr data-customer="[^"]*" data-status="(?P<status>[^"]*)">(?P<body>.*?)</tr>', re.S)
AMOUNT = re.compile(r'<td data-amount>(?P<amount>[^<]*)</td>')
LABEL = re.compile(r'<td data-customer-label>(?P<label>.*?)</td>', re.S)


def _rows(html: str) -> list[tuple[str, str, str]]:
    found = []
    for match in ROW.finditer(html):
        label = LABEL.search(match.group("body"))
        amount = AMOUNT.search(match.group("body"))
        assert label and amount, match.group("body")[:200]
        found.append((re.sub(r"<[^>]+>", "", label.group("label")).strip(),
                      amount.group("amount").strip(), match.group("status")))
    return found


def test_both_tiles_land_on_a_usable_page(hosted):
    """The third condition of the availability contract, performed rather than asserted."""
    browser = _browser(hosted)
    resolved = home.resolve(hosted.company_id)
    for step_id in ("billing-groups", "batch-invoice"):
        item = home.find(resolved, step_id)
        assert item is not None, step_id
        assert item.live, (step_id, item.reason)
        navigate_witness(browser, item, step_id)

    # The tiles are on the home window itself, as links rather than as inert planned tiles.
    page = browser.get(f"/c/{hosted.company_id}/").text
    assert f'href="/c/{hosted.company_id}/billing-group"' in page
    assert f'href="/c/{hosted.company_id}/batch-invoice/post"' in page


def _prepare_company(hosted):
    """Two company settings the demo does not carry, both of them real preconditions.

    A batch has no tax item and no price level of its own on purpose: both are facts of the
    company and of the customer, never of the run. So the journey sets the company default tax
    item and switches price levels on, which is what a person would do, rather than reaching
    around either of them through the batch.
    """
    items = hosted.ok("item.query", {"limit": 200, "projection": "summary"},
                      company=hosted.company_id)["items"]
    tax_item = next(row["id"] for row in items if row["type"] == "sales_tax_item" and row["active"])
    hosted.ok("company.update", {"default_sales_tax_item_id": tax_item,
                                 "enable_price_levels": True},
              company=hosted.company_id,
              headers={"X-Bookflow-Reason": "Set the default tax item and switch price levels on"})


def test_a_group_is_built_and_previewed_in_the_browser_with_a_row_per_customer(hosted):
    browser = _browser(hosted)
    company = hosted.company_id
    _prepare_company(hosted)

    created = browser.post(f"/c/{company}/billing-group/create", headers=WB, follow_redirects=False,
                           data={"originals": "{}", "f:name": "Browser retainers",
                                 "action": "submit"})
    assert created.status_code == 303, _main(created.text)[:1500]
    landing = re.search(r'/billing-group/([0-9A-Z]{26})', created.headers["location"])
    assert landing, created.headers["location"]
    group = landing.group(1)

    listed = browser.get(f"/c/{company}/billing-group")
    assert listed.status_code == 200 and "Browser retainers" in listed.text
    assert f'/billing-group/{group}' in listed.text

    # The group editor: the page the create form landed a person on, with membership on it.
    record = browser.get(f"/c/{company}/billing-group/{group}")
    assert record.status_code == 200 and "Browser retainers" in record.text
    assert f'/billing-group/{group}/add' in record.text

    add_form = browser.get(f"/c/{company}/billing-group/{group}/add")
    assert add_form.status_code == 200
    assert "Add customers to this billing group" in add_form.text
    assert f'value="{group}"' in add_form.text, "the group a person opened this from is pinned"

    added = browser.post(f"/c/{company}/billing-group/{group}/add", headers=WB,
                         follow_redirects=False, data={
                             "originals": "{}", "collection:customers": "1",
                             "c:customers:0:value": "Riverside Apartments",
                             "c:customers:1:value": "Commercial Example Customer",
                             "action": "submit"})
    assert added.status_code == 303, _main(added.text)[:1500]
    members = browser.get(f"/c/{company}/billing-group/{group}").text
    assert "Riverside Apartments" in members and "Commercial Example Customer" in members

    # And the batch window, previewed rather than posted.
    form = browser.get(f"/c/{company}/batch-invoice/post")
    assert form.status_code == 200 and "New batch of invoices" in form.text

    preview = browser.post(f"/c/{company}/batch-invoice/post", headers=WB, data={
        "originals": "{}", "f:date": "2026-09-01", "f:billing_group": group,
        "collection:lines": "1", "c:lines:0:item": "Mainline Clearing",
        "c:lines:0:quantity": "1", "action": "preview"})
    assert preview.status_code == 200, preview.text[:600]
    body = _main(preview.text)
    assert 'class="error"' not in body, body[:600]
    assert "What this batch will create" in body
    rows = _rows(body)
    assert [label for label, _amount, _status in rows] == [
        "Riverside Apartments", "Commercial Example Customer"]
    assert {status for _label, _amount, status in rows} == {"will_create"}
    amounts = {label: amount for label, amount, _status in rows}
    # The price level belongs to Riverside alone, so the same line is not the same money, and
    # the preview shows the level it resolved next to the amount it produced.
    assert amounts["Riverside Apartments"] != amounts["Commercial Example Customer"], amounts
    assert all(amount for amount in amounts.values())
    riverside = ROW.search(body).group("body")
    assert "Preferred Customer" in riverside, riverside
    # Nothing was written: the preview says so, no row claims a created invoice, and no row
    # links to one, because no number and no transaction exists yet to link to.
    assert "Nothing is written yet" in body
    assert 'data-status="created"' not in body
    assert f"/c/{company}/invoice/" not in body
