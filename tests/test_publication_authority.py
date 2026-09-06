import concurrent.futures
import threading

import pytest
import sqlalchemy as sa

from bookflow.core.publication import PublicationPermit
from bookflow.hub import schema as h
from tests.test_row3_host import hosted


def test_publication_inventory_covers_registry_and_refuses_unknown_hub_projection():
    from types import SimpleNamespace
    from bookflow.core.publication_inventory import inventory, policy
    from bookflow.core import registry
    rows = inventory()
    assert {row["command"] for row in rows} == {cmd.name for cmd in registry.all_commands(include_standalone=True)}
    assert next(row for row in rows if row["command"] == "invoice update")["conditional_authority"]
    with pytest.raises(RuntimeError, match="lacks a publication dependency inventory"):
        policy(SimpleNamespace(name="new hub projection", scope="hub", local_only=False, standalone=False))


def test_real_publication_snapshot_is_accountable_and_rechecks_current_authority(hosted, monkeypatch):
    from bookflow.adapters.mcp.intents import retained_size
    original = PublicationPermit.check
    snapshots = []

    def restored(self, host, credential, **kwargs):
        state = self.retained()
        assert 0 < retained_size(state) < 1024 * 1024
        clone = PublicationPermit.from_retained(state)
        assert clone.cmd is self.cmd and clone.ctx == self.ctx and clone.inp == self.inp
        snapshots.append((state, host, credential))
        return original(clone, host, credential, **kwargs)

    observer = hosted.ok("token.issue", {"label": "snapshot observer"})["secret"]
    monkeypatch.setattr(PublicationPermit, "check", restored)
    assert hosted.call("company.show", {}, company=hosted.company_id).status_code == 200
    state, host, credential = snapshots[-1]
    assert hosted.call("token.revoke", {"token": hosted.token}, headers={"Authorization": "Bearer " + observer}).status_code == 200
    from bookflow.core.errors import BookflowError
    with pytest.raises(BookflowError) as caught:
        original(PublicationPermit.from_retained(state), host, credential, original_response=False)
    assert caught.value.code == "E_UNAUTHENTICATED"


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("loss", ["revoke", "downgrade"])
def test_publication_rechecks_after_execution(hosted, monkeypatch, write, loss):
    reached, release = threading.Event(), threading.Event()
    original = PublicationPermit.check
    target = "company update" if write else "company show"

    def delayed(self, *args, **kwargs):
        if self.cmd.name == target and not reached.is_set():
            reached.set()
            assert release.wait(10)
        return original(self, *args, **kwargs)

    observer = hosted.ok("token.issue", {"label": "publication-observer"})["secret"]
    before = hosted.info()["info"]["fax"]
    monkeypatch.setattr(PublicationPermit, "check", delayed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(hosted.call, "company.update" if write else "company.show",
                              {"fax": "committed-before-revocation"} if write else {}, company=hosted.company_id)
        try:
            assert reached.wait(10)
            if loss == "revoke":
                result = hosted.call("token.revoke", {"token": hosted.token}, headers={"Authorization": "Bearer " + observer})
                assert result.status_code == 200, result.text
            else:
                host = hosted.handle.host
                def downgrade():
                    db = host._hub
                    db.raw.execute("BEGIN IMMEDIATE")
                    actor = db.conn.execute(sa.select(h.api_tokens.c.user_id).where(h.api_tokens.c.id == hosted.token)).scalar_one()
                    db.conn.execute(h.users.update().where(h.users.c.id == actor).values(hub_admin=False))
                    db.raw.execute("COMMIT")
                host.submit(downgrade)
        finally:
            release.set()
        result = pending.result(10)
    assert result.status_code == (401 if loss == "revoke" else 403), result.text
    assert result.json()["details"]["outcome"] == "unknown"
    assert "committed-before-revocation" not in result.text
    current = hosted.call("company.show", {}, company=hosted.company_id, headers={"Authorization": "Bearer " + observer})
    assert current.status_code == 200, current.text
    assert current.json()["info"]["fax"] == ("committed-before-revocation" if write else before)
    assert hosted.handle.host._readers_attached == 0


def test_own_detach_receipt_is_publishable_without_reexecution(hosted):
    response = hosted.call("company.detach", {"company": hosted.company_id})
    assert response.status_code == 200, response.text
    assert response.json()["company_id"] == hosted.company_id
    assert all(item["company_id"] != hosted.company_id for item in hosted.ok("company.list")["items"])


@pytest.mark.parametrize("lowercase", [False, True])
def test_self_revoke_receipt_once_then_authentication_fails(hosted, lowercase):
    result = hosted.call("token.revoke", {"token": hosted.token.lower() if lowercase else hosted.token})
    assert result.status_code == 200, result.text
    assert result.json()["changed"] is True
    assert hosted.call("company.list").status_code == 401


def test_company_list_cannot_publish_detached_registration_with_unchanged_org_membership(hosted, monkeypatch):
    from tests.conftest import make_actor
    organization = hosted.company_list["items"][0]["organization_id"]
    user = make_actor(hosted.root, "publication-org-reader", org_role=(organization, "readonly"))
    secret = hosted.ok("token.issue", {"user": user, "label": "projection witness"})["secret"]
    reached, release = threading.Event(), threading.Event()
    original = PublicationPermit.check

    def delayed(self, host, credential, **kwargs):
        if self.cmd.name == "company list" and self.actor[0] == user and not reached.is_set():
            reached.set()
            assert release.wait(10)
        return original(self, host, credential, **kwargs)

    monkeypatch.setattr(PublicationPermit, "check", delayed)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pending = pool.submit(hosted.call, "company.list", {}, headers={"Authorization": "Bearer " + secret})
        try:
            assert reached.wait(10)
            assert hosted.call("company.detach", {"company": hosted.company_id}).status_code == 200
        finally:
            release.set()
        result = pending.result(10)
    assert result.status_code == 403
    assert result.json()["details"]["outcome"] == "unknown"
    assert hosted.company_id not in result.text
    assert hosted.call("company.list", {}, headers={"Authorization": "Bearer " + secret}).json()["items"] == []
