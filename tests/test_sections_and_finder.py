"""Sections open on their records and tasks; the command finder reaches every command by name (R61)."""
from fastapi.testclient import TestClient

from bookflow.adapters.workbench import home, naming, pages
from bookflow.core import registry

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401


def _browser(hosted) -> TestClient:
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return browser


def test_a_section_opens_on_its_records_one_new_menu_and_its_tasks(hosted):
    company = hosted.company_id
    hosted.ok("customer.create", {"name": "AAA Section Customer"}, company=company)
    page = _browser(hosted).get(f"/c/{company}/_group/customers").text
    # The records first: a search that lands on the customer list, and the first customers.
    assert f'action="/c/{company}/customer"' in page and 'name="query"' in page
    assert "AAA Section Customer" in page
    assert page.index('class="overview-card section-records"') < page.index('id="tasks-title"')
    # One "+ New" menu with the create actions.
    assert page.count("data-new-menu") == 1
    for href in ("/invoice/post", "/estimate/create", "/sales-receipt/post", "/receive-payments",
                 "/credit-memo/post", "/customer/create"):
        assert f'href="/c/{company}{href}"' in page, href
    # No wall of generated command buttons: record-level and profile-list commands live elsewhere.
    assert 'class="noun-row"' not in page
    for href in ("/customer/link-vendor", "/customer/unlink-vendor", "/customer-type/create", "/job-type"):
        assert f'href="/c/{company}{href}"' not in page, href


def test_every_menu_section_is_a_page_and_every_link_on_it_opens(hosted):
    browser = _browser(hosted)
    company = hosted.company_id
    home_page = browser.get(f"/c/{company}/").text
    opened: set[str] = set()
    for entry in home.MENU:
        assert f'href="/c/{company}/_group/{entry.slug}"' in home_page, entry.slug
        page = browser.get(f"/c/{company}/_group/{entry.slug}")
        assert page.status_code == 200, entry.slug
        assert f"<h1>{entry.label}</h1>" in page.text, entry.slug
        section = home.SECTION_BY_SLUG.get(entry.slug)
        if section is None:
            # Reports and Audit keep their grouped list of pages.
            assert '<div class="noun-row">' in page.text, entry.slug
            continue
        links = home.resolve_section(company, section)
        assert links["new"] or links["tasks"] or links["groups"], (entry.slug, "an empty section is not a destination")
        for link in (*links["new"], *links["lists"], *links["tasks"], *(l for _, ls in links["groups"] for l in ls)):
            assert f'href="{link["href"]}"' in page.text, (entry.slug, link)
            opened.add(link["href"])
    for href in sorted(opened):
        response = browser.get(href)
        assert response.status_code == 200, (href, response.status_code)
    assert browser.get(f"/c/{company}/_group/nope").status_code == 400


def test_settings_holds_the_profile_lists_grouped_like_the_anchor(hosted):
    company = hosted.company_id
    page = _browser(hosted).get(f"/c/{company}/_group/settings").text
    assert "<h2 id=\"group-1\">Customer and vendor profile lists</h2>" in page
    profile = page[page.index("Customer and vendor profile lists"):page.index("Items and prices")]
    for noun in ("sales-rep", "customer-type", "vendor-type", "job-type", "term", "customer-message",
                 "payment-method", "ship-method"):
        assert f'href="/c/{company}/{noun}"' in profile, noun
    for noun in ("price-level", "unit-of-measure", "class", "sales-tax-code", "custom-field"):
        assert f'href="/c/{company}/{noun}"' in page, noun
    assert f'href="/c/{company}/_all"' in page


def test_the_customer_page_offers_linking_to_a_vendor(hosted):
    company = hosted.company_id
    customer = hosted.ok("customer.create", {"name": "Linkable Customer"}, company=company)
    browser = _browser(hosted)
    page = browser.get(f"/c/{company}/customer/{customer['id']}").text
    href = f'/c/{company}/customer/{customer["id"]}/link-vendor'
    assert f'<a href="{href}">Link to a vendor</a>' in page
    assert "unlink-vendor" not in page  # nothing is linked yet
    form = browser.get(href)
    assert form.status_code == 200 and 'name="f:vendor"' in form.text


# Nouns whose commands act on another record and are offered on that record's page.
REACHED_FROM = {"customer-credit": "credit-memo"}


def test_the_finder_index_reaches_every_routed_command(hosted):
    """Every routed company command is in the finder by its page, or is a record-level action whose
    records' list is in the finder (the record page offers it), or is listed in All commands."""
    company = hosted.company_id
    browser = _browser(hosted)
    response = browser.get(f"/c/{company}/_finder")
    assert response.status_code == 200
    # What a reader may open changes with their role; a stale copy must never be reused.
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    hrefs = {item["href"] for item in items}
    assert all(item["label"] and item["kind"] in ("section", "task", "page") for item in items)
    registry.load_all()
    # Labels are page titles, never a command's own name.
    assert not [item["label"] for item in items if registry.get(item["label"]) is not None]
    all_page = browser.get(f"/c/{company}/_all").text
    missing = []
    for command in registry.routed_commands():
        if command.scope != "company":
            continue
        owner = command.noun.removesuffix(" query")
        if owner != command.noun:
            # A list's own search controls (options, children) work inside that list's page.
            if f"/c/{company}/{owner.replace(' ', '-')}" not in hrefs:
                missing.append(command.name)
            continue
        if command.noun in REACHED_FROM:
            # Applied from another record's page: that record's list must be findable.
            if f"/c/{company}/{REACHED_FROM[command.noun]}" not in hrefs:
                missing.append(command.name)
            continue
        base = f"/c/{company}/{command.noun.replace(' ', '-')}"
        if not command.verb or command.verb in ("list", "query"):
            target = base
        elif command.verb == "show" and not pages._record_selector(command, command.noun):
            target = base + "/self"
        elif command.version_source or command.verb == "show":
            # Record-level: reached from a record, which is reached from its list or its own form.
            if base in hrefs or f"{base}/show" in hrefs or f'href="{base}' in all_page:
                continue
            missing.append(command.name)
            continue
        else:
            target = f"{base}/{command.verb}"
        if target not in hrefs:
            missing.append(command.name)
    assert not missing, "\n".join(sorted(missing))
    # The finder's footer and Settings both lead to the full catalogue.
    assert f"/c/{company}/_all" in hrefs


def test_the_finder_offers_only_what_the_reader_may_run():
    registry.load_all()
    rows = [(noun, [c for c in pages._verbs(noun, "company") if not c.is_write]) for noun in pages._nouns("company")]
    grouped = pages._grouped_nouns(rows, company=True)
    items = home.finder_index("c1", grouped, permits=lambda command: not command.is_write,
                              heading=naming.heading, plural=naming.list_heading,
                              selector=pages._record_selector)
    hrefs = {item["href"] for item in items}
    assert "/c/c1/invoice" in hrefs and "/c/c1/report/profit-and-loss" in hrefs
    for href in ("/c/c1/invoice/post", "/c/c1/customer/create", "/c/c1/customer/link-vendor", "/c/c1/receive-payments"):
        assert href not in hrefs, href
