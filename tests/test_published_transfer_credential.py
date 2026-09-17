"""The published-transfer constructor must bind credentials before callback authorization."""
from types import SimpleNamespace

import pytest

from bookflow.core.errors import BookflowError
from bookflow.core.transfers import HostedTransfer
from bookflow.adapters.http.published_transfer import PublishedTransfer


@pytest.mark.parametrize('failure', [None, 'credential', 'callback'])
def test_session_handoff_binds_current_credential_and_preserves_rejections(monkeypatch, failure):
    calls = []
    hub = object()
    session = SimpleNamespace(hub=hub)
    rejection = BookflowError('E_UNAUTHENTICATED')

    class Credential:
        def revalidate(self, database):
            assert database is hub
            calls.append('validated')
            if failure == 'credential':
                raise rejection

    credential = Credential()

    def authorize(s):
        assert s.credential is credential
        calls.append('callback')
        if failure == 'callback':
            raise rejection

    def preparation(self, *args, authorize_session, **kwargs):
        # The real base constructor calls this before recover/prepare authorization.
        authorize_session(session)
        assert session.credential is credential
        calls.append('prepared')

    monkeypatch.setattr(HostedTransfer, '__init__', preparation)
    if failure:
        with pytest.raises(BookflowError) as error:
            PublishedTransfer(credential=credential, authorize_session=authorize)
        assert error.value is rejection
        assert 'prepared' not in calls
        if failure == 'credential':
            assert calls == ['validated']
            assert not hasattr(session, 'credential')
        else:
            assert calls == ['validated', 'callback']
    else:
        PublishedTransfer(credential=credential, authorize_session=authorize)
        assert calls == ['validated', 'callback', 'prepared']
