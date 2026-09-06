import concurrent.futures
import threading

import pytest
import sqlalchemy as sa

from bookflow.core.publication import PublicationPermit
from bookflow.hub import schema as h
from tests.test_row3_host import hosted


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


def test_self_revoke_receipt_once_then_authentication_fails(hosted):
    result = hosted.call("token.revoke", {"token": hosted.token})
    assert result.status_code == 200, result.text
    assert result.json()["changed"] is True
    assert hosted.call("company.list").status_code == 401
