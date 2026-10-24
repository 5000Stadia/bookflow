"""R121 in the browser: the Accountant review page, reached from Company. The owner makes an accountant's
copy through the page, sees the lock, the copy (opened as a separate company) posts an adjusting entry and
writes its change file through its own page, and the owner previews the file and imports the ticked
change; the checklist shows throughout."""
import html
import re

from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}


def _text(page):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(page)))


def test_accountant_review_page_exports_previews_and_imports(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200

    def run(name, body, company=hosted.company_id):
        reason = {} if name.split()[-1] in ("show", "list", "status") else {"X-Bookflow-Reason": "accountant witness"}
        prefix = f"/companies/{company}" if company else ""
        answered = browser.post(f"{prefix}/commands/{name.replace(' ', '.')}", json=body, headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    office = run("account create", {"name": "AW Office", "type": "expense"})["id"]
    repairs = run("account create", {"name": "AW Repairs", "type": "expense"})["id"]
    bank = run("account create", {"name": "AW Bank", "type": "bank"})["id"]
    run("journal post", {"date": "2026-09-15", "memo": "Office", "lines": [
        {"account": office, "side": "debit", "amount": "300.00"}, {"account": bank, "side": "credit", "amount": "300.00"}]})

    run("user add", {"username": "aw-cpa", "company": hosted.company_id, "role": "standard", "password": "pw-aw-cpa-123456"}, company=None)
    run("accountant add", {"user": "aw-cpa", "through": "2026-09-30"})
    page = f"/c/{hosted.company_id}/accountant"
    assert page in browser.get(f"/c/{hosted.company_id}/_group/company", headers=HEADERS).text
    empty = browser.get(page, headers=HEADERS)
    assert empty.status_code == 200 and "data-no-copy" in empty.text and "data-review-checklist" in empty.text

    def post(url, **form):
        answered = browser.post(url, data={"ctx:reason": "accountant witness", **form}, headers=HEADERS)
        assert answered.status_code == 200, _text(answered.text)[:800]
        return answered.text

    made = _text(post(page, action="export", through="2026-09-30"))
    assert "Made the accountant's copy" in made and "on or before 2026-09-30" in made and "Cancel the copy" in made
    status = run("accountant-copy status", {})
    archive = next(hosted.root.rglob(status["current"]["file_name"]))
    copy = run("company restore", {"archive": str(archive), "as_copy": True, "name": "AW Copy"}, company=None)["company_id"]

    copy_page = f"/c/{copy}/accountant"
    assert 'data-copy-side="accountant"' in browser.get(copy_page, headers=HEADERS).text
    run("journal post", {"date": "2026-09-30", "memo": "Accrue repairs", "lines": [
        {"account": repairs, "side": "debit", "amount": "40.00"}, {"account": bank, "side": "credit", "amount": "40.00"}]},
        company=copy)
    wrote = _text(post(copy_page, action="changes"))
    assert "Wrote the change file" in wrote
    changes = next(hosted.root.rglob("*accountant changes*.json"))

    previewed = post(page, action="preview", file=str(changes))
    assert "data-change-list" in previewed and "1 new" in _text(previewed)
    key = re.search(r'name="change:([^"]+)"', previewed).group(1)
    imported = _text(post(page, action="import", file=str(changes), **{f"change:{key}": "on"}))
    assert "Imported 1 of the accountant's changes" in imported and "data-no-copy" not in imported
    assert run("accountant-copy status", {})["current"] is None
