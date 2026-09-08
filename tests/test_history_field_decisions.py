"""Field-disclosure memo stays inside one authenticated projection audience."""
from contextlib import contextmanager

import pytest

from bookflow.core import identity_admin_binding as binding
from bookflow.core.context import client_version
from bookflow.core.errors import BookflowError
from bookflow.core.host import Host
from bookflow.hub import audit_projection as projection, audit_projection_legacy as legacy
from bookflow.hub import identity_admin as admin
from bookflow.hub.permission_catalog import ScopeKey
from tests.test_permission_runtime import path
from tests.permission_admin_support import binding as token_binding
from tests.test_audit_projection_publication import apply


@pytest.fixture
def hosted(path):
    host=Host(path.parent,version=client_version());host.start()
    try:yield host,path
    finally:host.stop()


@contextmanager
def audience(hosted,who='A'):
    host,path=hosted
    admitted = (admin.TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')
                if who == 'GP-live' else token_binding(path,who))
    with binding.hosted_reader(host,admitted,request_id='REQUEST') as reader:
        yield projection.make_audience(reader)


def count_require(monkeypatch, value):
    calls=[];original=value.require
    def counted(company, requirements):
        calls.append((company,requirements))
        return original(company,requirements)
    monkeypatch.setattr(value,'require',counted)
    return calls


def test_field_decision_keys_and_main_gate_stays_fresh(hosted,monkeypatch):
    with audience(hosted) as value:
        calls=count_require(monkeypatch,value)
        for _ in range(3):assert projection._kind_allowed(value,'C','customer')
        assert projection._kind_allowed(value,'C','vendor')
        assert projection._kind_allowed(value,'D','customer')
        for _ in range(3):assert not projection._kind_allowed(value,'E','customer')
        assert len(calls)==4
        value.require('C',legacy.entry_requirement('customer'))
        value.require('C',legacy.entry_requirement('customer'))
        assert len(calls)==6


def test_new_audience_recomputes_after_real_permission_loss(hosted,monkeypatch):
    with audience(hosted) as value:
        assert projection._kind_allowed(value,'C','customer')
    host,path=hosted
    apply(host,path,admin.PutMembership('A',ScopeKey('organization','O'),admin.Version(1),'admin',denies=('customer',)),'W')
    with audience(hosted) as value:
        calls=count_require(monkeypatch,value)
        for _ in range(2):assert not projection._kind_allowed(value,'C','customer')
        assert projection._kind_allowed(value,'C','vendor')
        assert len(calls)==2


@pytest.mark.parametrize('who', ('R','GP-live'))
def test_same_capability_different_thresholds_remain_distinct(hosted,monkeypatch,who):
    original=legacy.entry_requirement
    monkeypatch.setattr(legacy,'entry_requirement',lambda kind: (('attachment','member' if kind=='customer' else 'admin'),) if kind in ('customer','vendor') else original(kind))
    with audience(hosted,who) as value:
        assert projection._kind_allowed(value,'C','customer')
        assert not projection._kind_allowed(value,'C','vendor')
        assert projection._kind_allowed(value,'C','customer')


def test_unexpected_errors_are_not_retained(hosted,monkeypatch):
    with audience(hosted) as value:
        calls=[]
        def fail(*args):
            calls.append(args)
            raise BookflowError('E_IO')
        monkeypatch.setattr(value,'require',fail)
        for _ in range(2):
            with pytest.raises(BookflowError,match='E_IO'):
                projection._kind_allowed(value,'C','customer')
        assert len(calls)==2
