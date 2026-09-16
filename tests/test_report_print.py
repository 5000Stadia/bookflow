"""A printed report is the report, not the web page it was read on.

Three files in the whole workbench carried a print rule, against twenty-three reports, so
printing a profit and loss put the application's navigation bar, its section menu, its filter
form and its structured-data panels on the paper beside the figures -- and the accountant got
a screenshot of a browser rather than a statement.

One stylesheet now dresses every report for the printer, and it is keyed to the one thing every
report page has in common: the registry calls the command a report, so the page says it is one.
There is no per-report stylesheet, and none is wanted.

The evidence here is not that a file exists. Each rule is matched against the elements of a
report page that was actually rendered, so a rule that hides a thing which is not there, and a
thing on the page which no rule reaches, are both failures. Selector matching is done here
rather than in a browser because the rules are plain descendant selectors and a headless Chrome
is minutes of runtime to learn the same fact; what a browser would add is covered by the last
test, which proves the rules are not conditional on the width of anything.
"""
import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

SHEET = "/static/report-print.css"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
TOKEN = re.compile(r"^([a-zA-Z][\w-]*)?((?:[.#][\w-]+|\[[^\]]+\])*)$")
PIECE = re.compile(r"[.#][\w-]+|\[[^\]]+\]")


# --- reading the page and the stylesheet ---------------------------------------------------

class _Document(HTMLParser):
    """Every element of a page, each with the ancestors it sits inside."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.nodes = [], []

    def _node(self, tag, attrs):
        node = {"tag": tag, "attrs": {k: (v or "") for k, v in attrs},
                "ancestors": list(self.stack)}
        self.nodes.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._node(tag, attrs)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._node(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                return


def elements(markup):
    parser = _Document()
    parser.feed(markup)
    return parser.nodes


def _simple(node, token):
    """Whether one element answers to one compound selector: tag, classes, ids, attributes."""
    found = TOKEN.match(token)
    assert found, f"this matcher does not understand the selector piece {token!r}"
    tag, rest = found.group(1), found.group(2) or ""
    if tag and node["tag"] != tag:
        return False
    classes = set(node["attrs"].get("class", "").split())
    for piece in PIECE.findall(rest):
        if piece.startswith("."):
            if piece[1:] not in classes:
                return False
        elif piece.startswith("#"):
            if node["attrs"].get("id") != piece[1:]:
                return False
        else:
            name, _, value = piece[1:-1].partition("=")
            if name not in node["attrs"]:
                return False
            if value and node["attrs"][name] != value.strip("\"'"):
                return False
    return True


def matches(node, selector):
    """Whether an element answers to a descendant selector, as a browser would read it."""
    parts = selector.split()
    if not _simple(node, parts[-1]):
        return False
    index = 0
    for part in parts[:-1]:
        while index < len(node["ancestors"]) and not _simple(node["ancestors"][index], part):
            index += 1
        if index == len(node["ancestors"]):
            return False
        index += 1
    return True


def _uncommented(css):
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def _block(css, at_rule):
    start = css.index(at_rule)
    open_brace = css.index("{", start)
    depth, cursor = 1, open_brace + 1
    while depth:
        depth += {"{": 1, "}": -1}.get(css[cursor], 0)
        cursor += 1
    return css[open_brace + 1:cursor - 1]


def rules(block):
    """Every ordinary rule of a block, as (selector group, declarations). At-rules are skipped."""
    found, cursor = [], 0
    while True:
        open_brace = block.find("{", cursor)
        if open_brace < 0:
            return found
        selector = block[cursor:open_brace].strip()
        depth, end = 1, open_brace + 1
        while depth:
            depth += {"{": 1, "}": -1}.get(block[end], 0)
            end += 1
        if not selector.startswith("@"):
            found.append((selector, block[open_brace + 1:end - 1]))
        cursor = end


def _declared(body, name):
    return re.search(rf"(?:^|;)\s*{name}\s*:\s*([^;!]+)", body)


def hidden_selectors(print_block):
    """Every selector the print rules switch off."""
    off = []
    for selector, body in rules(print_block):
        value = _declared(body, "display")
        if value and value.group(1).strip() == "none":
            off += [part.strip() for part in selector.split(",")]
    return off


# --- the page these rules are written against ----------------------------------------------

@pytest.fixture
def printed(hosted):  # noqa: F811
    """One report as a person leaves it on the screen before pressing Print, and the sheet."""
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    page = browser.post(f"/c/{hosted.company_id}/report/general-ledger",
                        data={"f:date_from": "2000-01-01", "f:date_to": "2099-12-31", "action": "submit"},
                        headers={"X-Bookflow-Workbench": "1"})
    assert page.status_code == 200, page.text[:400]
    sheet = browser.get(SHEET)
    assert sheet.status_code == 200, "the shared report print stylesheet is not served"
    return page, sheet, browser


def test_the_report_page_loads_one_shared_sheet_that_only_print_can_see(printed):
    """One file, loaded like any other, whose every rule but one is scoped to paper.

    The exception is the rule that keeps the printed identification block off the screen,
    and it is the only way this sheet is allowed to change what a reader sees in a window.
    """
    page, sheet, _ = printed
    assert re.search(r'<link rel="stylesheet" href="[^"]*report-print\.css[^"]*"', page.text), \
        "the report page does not load the print stylesheet"
    assert 'data-report-page="1"' in page.text, "the page does not declare itself a report"
    assert sheet.headers["content-type"].startswith("text/css")
    css = _uncommented(sheet.text)
    outside = rules(css.split("@media")[0])
    assert outside, "nothing keeps the printed identification block off the screen"
    for selector, body in outside:
        assert selector.strip() == ".report-print-heading", \
            f"loading this sheet changes how the screen looks: {selector!r}"
        assert _declared(body, "display").group(1).strip() == "none", body


def test_the_furniture_a_person_used_to_reach_the_report_is_not_printed(printed):
    """Every one of these is on the page, and every one is switched off for paper.

    Both halves matter: a rule for something that is not there hides nothing, and a thing on
    the page that no rule reaches is what lands on the paper.
    """
    page, sheet, _ = printed
    nodes = elements(page.text)
    off = hidden_selectors(_block(_uncommented(sheet.text), "@media print"))
    furniture = {
        "the application header bar": "header",
        "the section navigation": "nav.group-menu",
        "the breadcrumb": "nav.crumbs",
        "the filter form": "form[data-generated-form]",
        "the next-page button": "form#statement-next-page",
        "the structured-data panel": "details.report-structured",
        "the download link": "p.report-tools",
    }
    for description, selector in furniture.items():
        present = [node for node in nodes if matches(node, selector)]
        assert present, f"{description} is not on the report page; this rule guards nothing"
        for node in present:
            assert any(matches(node, rule) for rule in off), \
                f"{description} ({selector}) would be printed with the report"


def test_the_report_itself_and_what_identifies_it_are_kept(printed):
    """The figures, their column headings, the whole-report totals, and the block naming
    the company, the report and the period. None of these may be caught by a hiding rule."""
    page, sheet, _ = printed
    nodes = elements(page.text)
    printing = _block(_uncommented(sheet.text), "@media print")
    off = hidden_selectors(printing)
    kept = {
        "the report heading": "h1",
        "the identification block": "div.report-print-heading",
        "the company and period line": "div.report-print-heading p",
        "the ledger table": "table#report-lines",
        "its column headings": "table#report-lines thead th",
        "its rows": "table#report-lines tbody tr",
        "its figures": "table#report-lines tbody td",
        "the whole-report totals": "dl.report-totals",
    }
    for description, selector in kept.items():
        present = [node for node in nodes if matches(node, selector)]
        assert present, f"{description} is not on the report page"
        for node in present:
            caught = [rule for rule in off if matches(node, rule)]
            assert not caught, f"{description} would be missing from the printed report: {caught}"
    assert len([n for n in nodes if matches(n, "table#report-lines tbody tr")]) >= 40, \
        "this report is too short for the printed-page rules to be worth asserting"


def test_what_paper_needs_and_a_screen_does_not_is_switched_on(printed):
    """The identification block prints and never shows on screen, and a report longer than a
    sheet repeats its column headings rather than leaving later sheets unlabelled."""
    page, sheet, _ = printed
    css = _uncommented(sheet.text)
    printing = _block(css, "@media print")

    outside = [body for selector, body in rules(css.split("@media")[0])
               if ".report-print-heading" in selector]
    assert outside and any(_declared(body, "display").group(1).strip() == "none"
                           for body in outside if _declared(body, "display")), \
        "the printed identification block would also appear on the screen"

    shown = [(selector, body) for selector, body in rules(printing)
             if ".report-print-heading" in selector and _declared(body, "display")]
    assert any(_declared(body, "display").group(1).strip() == "block" for _, body in shown), \
        "the identification block is never switched on for paper"

    heads = [body for selector, body in rules(printing) if selector.strip().endswith("thead")]
    assert any(_declared(body, "display")
               and _declared(body, "display").group(1).strip() == "table-header-group"
               for body in heads), "column headings would not repeat on later printed sheets"

    scrollers = [body for selector, body in rules(printing)
                 if "table-wrap" in selector or "report-lines-wrap" in selector]
    assert any(_declared(body, "overflow") and "visible" in _declared(body, "overflow").group(1)
               for body in scrollers), "a wide report would be cut off at the edge of a viewport"


def test_the_print_rules_do_not_depend_on_the_width_of_anything(printed):
    """Asserted here rather than in a browser: at a desktop width, at a phone width and at any
    paper width these are the same rules, because none of them is conditional on a width.

    The phone-card layout that turns a report table into stacked cards is scoped to `screen`
    in the sheet that owns it, so it cannot reach paper either -- which is what makes a printed
    report a table whatever window it was printed from."""
    page, sheet, browser = printed
    css = _uncommented(sheet.text)
    for condition in re.findall(r"@media([^{]*)\{", css):
        assert "width" not in condition, f"a print rule is conditional on a width: {condition!r}"

    detail = browser.get("/static/document-detail.css")
    assert detail.status_code == 200
    for condition in re.findall(r"@media([^{]*)\{", _uncommented(detail.text)):
        if "width" in condition:
            assert "screen" in condition, \
                f"a width-conditional rule could reach paper and restack the table: {condition!r}"


def test_a_page_that_is_not_a_report_is_left_alone(hosted):  # noqa: F811
    """Every rule is scoped to the report flag, so one shared stylesheet cannot change how
    anything else in this workbench prints."""
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    page = browser.get(f"/c/{hosted.company_id}/customer")
    assert page.status_code == 200
    assert 'data-report-page' not in page.text, "a list page is claiming to be a report"
    css = _uncommented(browser.get(SHEET).text)
    for selector, _ in rules(_block(css, "@media print")):
        for part in selector.split(","):
            assert part.strip().startswith("[data-report-page]"), \
                f"an unscoped print rule would reach every page: {part!r}"
