"""Real offline binding continues with the explicitly activated catalog."""
import pytest
from bookflow.core import identity_admin_binding as producer
from bookflow.core.config import Config, os_login
from bookflow.hub import identity_admin as b, permission_catalog as c
from tests.test_permission_activation import legacy, writer, activate
from tests.payment_raw_evidence import database as snapshot


def test_ordinary_bound_admin_preview_after_activation(legacy):
    with writer(legacy) as db:
        activate(db)
    before = snapshot(legacy)
    config = Config(legacy.parent/'config.toml')
    config.set_user(os_login(),'H')
    config.save()
    intent = b.PutMembership('Q',c.ScopeKey('company','C'),b.Absent(),'standard',
                             ('transaction.check.delete',),('ledger.post',))
    with producer.offline_operation(legacy.parent,request_id='REQUEST',purpose='preview') as operation:
        with pytest.raises(b.AdministrationError) as caught:
            operation.preview(intent)
        assert caught.value.args == ('unavailable_target','scope')
    config.set_user(os_login(),'A')
    config.save()
    with producer.offline_operation(legacy.parent,request_id='REQUEST',purpose='preview') as operation:
        result = operation.preview(intent)
        assert result.visible.changed and result.visible.prospective
        assert result.pair.comparison.old.semantics == 'scoped_v1'
        assert result.pair.comparison.new.semantics == 'scoped_v1'
    assert snapshot(legacy) == before
