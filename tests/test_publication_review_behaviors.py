"""Real hosted publication predicates beyond registry classification (review F5)."""

import concurrent.futures
import threading

import pytest

from bookflow.core.publication import PublicationPermit
from bookflow.hub import schema as h
from tests.conftest import make_actor
from tests.test_row3_host import hosted
from tests.test_row7_credentials import writer


def member(hosted, role='admin'):
    actor = make_actor(hosted.root, 'publication-member', company_role=(hosted.company_id, role))
    secret = hosted.ok('token.issue', {'user': actor, 'label': 'Publication member'})['secret']
    return actor, {'Authorization': 'Bearer ' + secret}


def downgrade(hosted, actor, role):
    # Fixture authority edit is serialized by the actual shared writer.
    def change():
        with writer(hosted.root) as db:
            db.conn.execute(h.memberships.update().where(h.memberships.c.user_id == actor).values(role=role))
    hosted.handle.host.submit(change)


@pytest.mark.parametrize('protected_error', [False, True])
def test_member_role_change_suppresses_old_projection_and_protected_error(hosted, monkeypatch, protected_error):
    actor, headers = member(hosted)
    command = 'journal.post' if protected_error else 'company.show'
    raw = {'date': '2026-01-15', 'lines': [
        {'account': 'Checking', 'side': 'debit', 'amount': '125.00'},
        {'account': 'Service Income', 'side': 'credit', 'amount': '124.00'}]} if protected_error else {}
    control = hosted.call(command, raw, company=hosted.company_id, headers=headers)
    assert control.status_code == (400 if protected_error else 200)
    if protected_error:
        assert control.json()['code'] == 'E_UNBALANCED_ENTRY'
    else:
        assert control.json()['role'] == 'admin'
    before = hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id)
    reached, release = threading.Event(), threading.Event()
    original = PublicationPermit.check

    def barrier(self, *args, **kwargs):
        if self.cmd.name == command.replace('.', ' ') and self.actor[0] == actor and not reached.is_set():
            reached.set()
            assert release.wait(15)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(PublicationPermit, 'check', barrier)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pending = pool.submit(hosted.call, command, raw, company=hosted.company_id, headers=headers)
        try:
            assert reached.wait(15)
            downgrade(hosted, actor, 'readonly' if protected_error else 'standard')
        finally:
            release.set()
        denied = pending.result(15)
    assert denied.status_code == 403
    assert denied.json() == {'code': 'E_PERMISSION', 'message': 'The acting user may not run this command here.',
                             'details': {'stage': 'publication', 'outcome': 'unknown'}}
    assert hosted.ok('audit.list', {'limit': 200}, company=hosted.company_id) == before
    fresh = hosted.call('company.show', {}, company=hosted.company_id, headers=headers)
    assert fresh.status_code == 200 and fresh.json()['role'] == ('readonly' if protected_error else 'standard')


def test_delayed_workbench_receipt_checks_current_member_role(hosted):
    actor, headers = member(hosted)
    posted = hosted.api.post('/c/' + hosted.company_id + '/company/update', headers=headers,
        data={'f:fax': 'delayed-receipt-private', 'ctx:reason': 'Delayed browser receipt', 'action': 'submit'},
        follow_redirects=False)
    assert posted.status_code == 303, posted.text
    assert 'flash=' in posted.headers['location']
    downgrade(hosted, actor, 'standard')
    delayed = hosted.api.get(posted.headers['location'], headers=headers)
    assert delayed.status_code == 403
    assert delayed.json()['code'] == 'E_PERMISSION'
    assert 'delayed-receipt-private' not in delayed.text
    assert hosted.info()['info']['fax'] == 'delayed-receipt-private'
    assert hosted.ok('audit.list', {'command': 'company update'}, company=hosted.company_id)['items'][0]['reason'] == 'Delayed browser receipt'


def test_detach_preview_cannot_use_another_writers_detach_certificate(hosted, monkeypatch):
    reached, release = threading.Event(), threading.Event()
    original = PublicationPermit.check

    def barrier(self, *args, **kwargs):
        if self.cmd.name == 'company detach' and self.dry_run and not reached.is_set():
            reached.set()
            assert release.wait(15)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(PublicationPermit, 'check', barrier)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pending = pool.submit(hosted.api.post, '/commands/company.detach?dry_run=true',
                              json={'company': hosted.company_id}, headers=hosted.bearer)
        try:
            assert reached.wait(15)
            assert hosted.call('company.detach', {'company': hosted.company_id}).status_code == 200
        finally:
            release.set()
        result = pending.result(15)
    assert result.status_code == 403
    assert result.json()['code'] == 'E_PERMISSION'
    assert result.json()['details']['outcome'] == 'unknown'
    assert hosted.company_id not in result.text
