"""Focused regression witnesses for the Row 3 workbench remediation."""

from __future__ import annotations

import html
import json
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from bookflow.adapters.workbench import forms as F
from bookflow.adapters.workbench import pages
from bookflow.core import registry
from bookflow.storage import migrate
from tests.conftest import make_actor
from tests.test_row3_host import (
    GHOST,
    OUTSIDER_PASSWORD,
    PASSWORD,
    WB,
    _hub_sql,
    hosted,
)


def _login(hosted) -> TestClient:
    client = TestClient(hosted.handle.app, follow_redirects=False)
    assert client.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    return client


def test_clear_wins_over_prefill_and_rendered_originals_are_omitted():
    registry.load_all()
    cmd = registry.get("company update")
    originals = {
        "phone": "555-0100",
        "email": "books@example.test",
        "report_basis": "accrual",
        "recent_activity_window_seconds": 60,
        "expected_version": 4,
    }
    rendered = {
        "f:phone": "555-0100",
        "f:email": "books@example.test",
        "f:report_basis": "accrual",
        "f:recent_activity_window_seconds": "60",
        "f:expected_version": "4",
        "action": "submit",
    }

    raw, headers, preview = F.translate(cmd, rendered, originals)
    assert raw == {} and headers == {} and preview is False

    rendered["clear:phone"] = "1"
    raw, _, _ = F.translate(cmd, rendered, originals)
    assert raw == {"phone": None}


def test_flash_expiry_and_foreign_session_do_not_disclose_or_consume(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(pages.time, "monotonic", lambda: now[0])
    store = pages._FlashStore()

    key = store.put("owner-session", {"result": {"secret": "only-owner"}})
    assert store.take(key, "foreign-session") is None
    assert store.take(key, "owner-session") == {"result": {"secret": "only-owner"}}
    assert store.take(key, "owner-session") is None

    expired = store.put("owner-session", {"result": {"secret": "too-late"}})
    now[0] += pages.FLASH_TTL_SECONDS
    assert store.take(expired, "owner-session") is None


def test_company_forms_authorize_before_rendering(hosted, root):
    hosted.ok("organization.new", {"name": "Hidden Org"})
    hidden = hosted.ok(
        "company.new",
        {"legal_name": "Hidden Books LLC", "home_currency": "USD", "organization": "Hidden Org", "timezone": "UTC"},
    )["company_id"]
    make_actor(root, "form-reader", company_role=(hosted.company_id, "readonly"))
    issued = hosted.ok("token.issue", {"user": "form-reader", "label": "form-reader"})
    headers = {"Authorization": f"Bearer {issued['secret']}"}
    client = TestClient(hosted.handle.app)

    hidden_page = client.get(f"/c/{hidden}/directive/add", headers=headers)
    ghost_page = client.get(f"/c/{GHOST}/directive/add", headers=headers)
    assert hidden_page.status_code == ghost_page.status_code == 404
    assert hidden_page.text == ghost_page.text
    assert "Hidden Books" not in hidden_page.text


def test_record_actions_and_presence_follow_the_effective_role(hosted, root):
    directive = hosted.ok("directive.list", {}, company=hosted.company_id)["items"][0]
    record_url = f"/c/{hosted.company_id}/directive/{directive['code']}"
    write_url = f"/c/{hosted.company_id}/directive/{directive['code']}/deactivate"
    hosted.ok(
        "presence.set",
        {"record_type": "directive", "record_id": directive["id"]},
        company=hosted.company_id,
    )

    admin = _login(hosted)
    admin_page = admin.get(record_url)
    assert admin_page.status_code == 200
    assert f'href="{write_url}"' in admin_page.text
    assert "/presence/set/directive/" in admin_page.text
    assert "Editing now:" in admin_page.text

    make_actor(root, "readonly-web", company_role=(hosted.company_id, "readonly"))
    issued = hosted.ok("token.issue", {"user": "readonly-web", "label": "readonly-web"})
    readonly_page = TestClient(hosted.handle.app).get(
        record_url, headers={"Authorization": f"Bearer {issued['secret']}"}
    )
    assert readonly_page.status_code == 200
    assert f'href="{write_url}"' not in readonly_page.text
    assert "/presence/set/directive/" not in readonly_page.text
    assert "/presence/clear/directive/" not in readonly_page.text
    assert "Editing now:" not in readonly_page.text
    readonly_list = TestClient(hosted.handle.app).get(
        f"/c/{hosted.company_id}/directive", headers={"Authorization": f"Bearer {issued['secret']}"}
    )
    assert f'href="/c/{hosted.company_id}/directive/add"' not in readonly_list.text
    readonly_form = TestClient(hosted.handle.app).get(
        f"/c/{hosted.company_id}/directive/add", headers={"Authorization": f"Bearer {issued['secret']}"}
    )
    assert readonly_form.status_code == 403 and "E_PERMISSION" in readonly_form.text

    make_actor(root, "hub-reader", hub_admin=True, company_role=(hosted.company_id, "readonly"))
    issued = hosted.ok("token.issue", {"user": "hub-reader", "label": "hub-reader"})
    hub_admin_page = TestClient(hosted.handle.app).get(
        record_url, headers={"Authorization": f"Bearer {issued['secret']}"}
    )
    assert hub_admin_page.status_code == 200
    assert f'href="{write_url}"' in hub_admin_page.text
    assert "/presence/set/directive/" in hub_admin_page.text


def test_hub_actions_and_direct_forms_follow_hub_role(hosted):
    outsider = TestClient(hosted.handle.app)
    assert outsider.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    page = outsider.get("/hub/")
    assert page.status_code == 200
    assert 'href="/hub/organization/new"' not in page.text
    direct = outsider.get("/hub/organization/new")
    assert direct.status_code == 403 and "E_PERMISSION" in direct.text
    assert "capability" in direct.text and "required_role" in direct.text


def test_success_uses_session_bound_one_time_post_redirect_get(hosted, monkeypatch):
    owner = _login(hosted)
    before = hosted.info()
    response = owner.post(
        f"/c/{hosted.company_id}/company/self/update",
        headers=WB,
        data={
            "originals": json.dumps({**before["info"], "expected_version": before["info_version"]}, default=str),
            "f:website": "https://prg.example.test",
            "action": "submit",
        },
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert urlsplit(location).path == f"/c/{hosted.company_id}/company/self"
    flash = parse_qs(urlsplit(location).query).get("flash", [])
    assert len(flash) == 1 and len(flash[0]) >= 20
    assert "prg.example.test" not in location

    first = owner.get(location)
    assert first.status_code == 200 and "Result" in first.text and "prg.example.test" in first.text
    second = owner.get(location)
    assert second.status_code == 200 and "prg.example.test" in second.text  # the record itself remains updated
    assert "Result — company update: done" not in second.text

    created = owner.post(
        f"/c/{hosted.company_id}/directive/add",
        headers=WB,
        data={"originals": "{}", "f:text": "Keep the receipt image.", "action": "submit"},
    )
    assert created.status_code == 303
    assert urlsplit(created.headers["location"]).path.startswith(f"/c/{hosted.company_id}/directive/")
    assert owner.get(created.headers["location"]).status_code == 200

    issued = owner.post(
        "/hub/token/issue",
        headers=WB,
        data={"originals": "{}", "f:label": "one-time-secret", "action": "submit"},
    )
    assert issued.status_code == 303
    secret_location = issued.headers["location"]
    assert "secret" not in secret_location and "token" in urlsplit(secret_location).path

    foreign = TestClient(hosted.handle.app, follow_redirects=False)
    assert foreign.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    foreign_page = html.unescape(foreign.get(secret_location).text)
    assert '"secret":' not in foreign_page and "Result — token issue: done" not in foreign_page

    shown_response = owner.get(secret_location)
    shown_once = html.unescape(shown_response.text)
    assert shown_response.headers["cache-control"] == "no-store"
    assert '"secret":' in shown_once
    consumed = html.unescape(owner.get(secret_location).text)
    assert '"secret":' not in consumed and "Result — token issue: done" not in consumed

    monkeypatch.setattr(pages, "FLASH_TTL_SECONDS", 0.0)
    expired = owner.post(
        "/hub/token/issue",
        headers=WB,
        data={"originals": "{}", "f:label": "expired-secret", "action": "submit"},
    )
    expired_page = html.unescape(owner.get(expired.headers["location"]).text)
    assert expired_page and '"secret":' not in expired_page and "Result — token issue: done" not in expired_page


def test_canonical_audit_pages_validate_filters(hosted):
    client = _login(hosted)
    company_event = hosted.ok("audit.list", {"limit": 1}, company=hosted.company_id)["items"][0]["id"]
    hub_event = hosted.ok("hub.audit.list", {"limit": 1})["items"][0]["id"]

    for url in (
        f"/c/{hosted.company_id}/audit",
        f"/c/{hosted.company_id}/audit/{company_event}",
        "/hub/audit",
        f"/hub/audit/{hub_event}",
    ):
        response = client.get(url)
        assert response.status_code == 200, (url, response.status_code, response.text[:300])

    for url in (f"/c/{hosted.company_id}/audit?limit=not-an-int", "/hub/audit?before=not-an-int"):
        response = client.get(url)
        assert response.status_code == 422
        assert "E_VALIDATION" in response.text

    assert f'href="/c/{hosted.company_id}/audit/{company_event}"' in client.get(f"/c/{hosted.company_id}/audit").text
    assert f'href="/hub/audit/{hub_event}"' in client.get("/hub/audit").text


def test_picker_exposes_schema_revision_state_and_upgrade_link(hosted, root):
    client = _login(hosted)
    current = client.get("/companies")
    assert current.status_code == 200
    assert migrate.HEADS["company"] in current.text and "current" in current.text

    _hub_sql(root, "UPDATE companies SET schema_revision = 'co0001' WHERE id = ?", (hosted.company_id,))
    behind = client.get("/companies")
    assert behind.status_code == 200
    assert "co0001" in behind.text and "behind" in behind.text
    assert 'href="/hub/upgrade"' in behind.text


def test_login_uses_available_htmx_and_points_to_self_service(hosted):
    page = TestClient(hosted.handle.app).get("/login")
    assert page.status_code == 200
    assert 'hx-ext="json-enc"' not in page.text
    assert "event.detail.successful){window.location" not in page.text
    assert 'name="next" value="/"' in page.text
    assert 'href="/hub/user/set-password"' in page.text


def test_successful_htmx_login_redirects_to_a_safe_visible_destination(hosted):
    client = TestClient(hosted.handle.app, follow_redirects=False)
    destination = f"/c/{hosted.company_id}/directive"
    response = client.post(
        "/login",
        data={"username": hosted.login, "password": PASSWORD, "next": destination},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == destination
    assert response.cookies.get("bookflow_session")

    for destination in ("//outside.example/path", "/\\outside.example/path", "https://outside.example/path"):
        unsafe = TestClient(hosted.handle.app, follow_redirects=False).post(
            "/login",
            data={"username": hosted.login, "password": PASSWORD, "next": destination},
            headers={"HX-Request": "true"},
        )
        assert unsafe.status_code == 200 and unsafe.headers["HX-Redirect"] == "/"

    ordinary_api = TestClient(hosted.handle.app).post(
        "/login", json={"username": hosted.login, "password": PASSWORD}
    )
    assert ordinary_api.status_code == 200 and "HX-Redirect" not in ordinary_api.headers
