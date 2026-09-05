"""Case-insensitive usernames preserve credential and account boundaries."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import bookflow
from bookflow import BookflowError
from bookflow.adapters.http import auth
from bookflow.hub import users
from bookflow.storage.engine import open_database
from tests.conftest import make_actor
from tests.test_row3_host import OUTSIDER_PASSWORD, PASSWORD, WB, hosted  # noqa: F401


@pytest.mark.parametrize("supplied", ["outsider", "OUTSIDER", "OuTsIdEr"])
@pytest.mark.parametrize("encoding", ["json", "data"])
def test_login_username_case_preserves_identity(hosted, supplied, encoding):
    browser = TestClient(hosted.handle.app)
    response = browser.post("/login", **{encoding: {
        "username": supplied, "password": OUTSIDER_PASSWORD,
    }})
    assert response.status_code == 200
    assert response.json()["user_id"] == hosted.outsider_id
    assert response.json()["username"] == "outsider"
    assert response.cookies.get("bookflow_session")
    wrong = browser.post("/login", json={
        "username": supplied, "password": OUTSIDER_PASSWORD.upper(),
    })
    assert wrong.status_code == 401 and wrong.json()["code"] == "E_LOGIN_FAILED"
    assert not wrong.cookies


@pytest.mark.parametrize(("stored", "supplied"), [("Straße", "STRASSE"), ("Élodie", "E\u0301LODIE")])
def test_existing_unicode_username_uses_casefold(hosted, stored, supplied):
    uid = make_actor(hosted.root, stored)
    hosted.ok("user.set-password", {"username": supplied, "password": PASSWORD})
    response = hosted.api.post("/login", json={"username": supplied, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["user_id"] == uid and response.json()["username"] == stored


def test_self_service_case_variants_do_not_grant_other_accounts(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": "OUTSIDER", "password": OUTSIDER_PASSWORD}).status_code == 200
    changed = browser.post("/commands/user.set-password", json={
        "username": "OuTsIdEr", "password": PASSWORD,
    }, headers=WB)
    assert changed.status_code == 200 and changed.json()["user_id"] == hosted.outsider_id
    issued = browser.post("/commands/token.issue", json={"user": "OUTSIDER", "label": "mine"}, headers=WB)
    assert issued.status_code == 200 and issued.json()["user_id"] == hosted.outsider_id
    for route, fields in (("user.set-password", {"password": PASSWORD}), ("token.issue", {"label": "other"})):
        selector = "username" if route == "user.set-password" else "user"
        responses = [browser.post(f"/commands/{route}", json={**fields, selector: target}, headers=WB)
                     for target in (hosted.login.upper(), "MISSING-PERSON")]
        assert responses[0].status_code == responses[1].status_code == 403
        assert responses[0].content == responses[1].content


def test_case_collision_fails_login_and_self_service_closed(hosted):
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": "outsider", "password": OUTSIDER_PASSWORD}).status_code == 200
    make_actor(hosted.root, "OUTSIDER")
    for spelling in ("outsider", "OUTSIDER", "OuTsIdEr"):
        response = TestClient(hosted.handle.app).post("/login", json={"username": spelling, "password": OUTSIDER_PASSWORD})
        assert response.status_code == 401 and response.json()["code"] == "E_LOGIN_FAILED"
        assert not response.cookies
    for route, fields in (("user.set-password", {"username": "OUTSIDER", "password": PASSWORD}),
                          ("token.issue", {"user": "OUTSIDER", "label": "ambiguous"})):
        response = browser.post(f"/commands/{route}", json=fields, headers=WB)
        assert response.status_code == 403
    # Stable IDs still resolve the original user for authenticated self-service.
    assert browser.post("/commands/user.set-password", json={
        "username": hosted.outsider_id, "password": PASSWORD,
    }, headers=WB).status_code == 200


def test_collision_during_password_verification_cannot_issue_session(hosted, monkeypatch):
    original = auth.verify_password

    def introduce_collision(stored, supplied):
        result = original(stored, supplied)
        make_actor(hosted.root, "OUTSIDER")
        return result

    monkeypatch.setattr(auth, "verify_password", introduce_collision)
    response = hosted.api.post("/login", json={"username": "Outsider", "password": OUTSIDER_PASSWORD})
    assert response.status_code == 401 and response.json()["code"] == "E_LOGIN_FAILED"
    assert not response.cookies


def test_inactive_user_cannot_log_in_with_case_variant(hosted):
    with open_database(hosted.root / "hub.db", writable=True) as db:
        db.raw.execute("UPDATE users SET active = 0 WHERE id = ?", (hosted.outsider_id,))
    response = hosted.api.post("/login", json={"username": "OUTSIDER", "password": OUTSIDER_PASSWORD})
    assert response.status_code == 401 and not response.cookies


def test_create_human_rejects_case_collision(hosted):
    with open_database(hosted.root / "hub.db", writable=True) as db:
        with pytest.raises(BookflowError) as error:
            users.create_human(SimpleNamespace(hub=db), username="OUTSIDER", display_name="Other",
                               created_by=hosted.outsider_id, via="python", hub_admin=False)
        assert error.value.code == "E_VALIDATION"


def test_init_case_variant_preserves_original_spelling(tmp_path):
    client = bookflow.connect(data_root=str(tmp_path / "init"))
    first = client.init(username="Alice")
    again = client.init(username="ALICE")
    assert not again["created"]
    assert again["user_id"] == first["user_id"] and again["username"] == "Alice"


def test_login_inputs_support_phone_and_password_managers(hosted):
    page = hosted.api.get("/login").text
    assert 'autocomplete="username"' in page and 'autocapitalize="none"' in page
    assert 'autocomplete="current-password"' in page
