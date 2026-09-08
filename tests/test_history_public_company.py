"""Registered company activity resolves a name and retains opaque continuation."""
from bookflow.core import registry
from bookflow.core.context import Context, client_version
from bookflow.core.config import os_login
from bookflow.core.host import Host
from bookflow.core.publication import OSBinding
from bookflow.adapters.http.execution import run_hosted
from tests.test_audit_projection_activity import world, customer, note, edited


def test_registered_company_activity_name_and_continuation(world,customer,edited):
    registry.load_all()
    ctx=Context.new('http','Company history')
    host=Host(world['root'],version=client_version());host.start()
    try:
        cred=OSBinding.capture(host,os_login())
        cmd=registry.get('activity')
        raw={'record_type':'customer','record_id':customer['id'],'limit':1}
        first=run_hosted(host,cmd,raw,ctx,cred,'Demo Plumbing Co','option',False)
        assert first['projection_version']==2 and first['count']==1 and first['next_cursor']
        second=run_hosted(host,cmd,{**raw,'cursor':first['next_cursor']},ctx,cred,'Demo Plumbing Co','option',False)
        assert second['items'][0]['entry_id'] != first['items'][0]['entry_id']
        assert 'high_water' not in first and 'seq' not in first['items'][0]
        second.check()
        assert host._readers_attached==0
    finally:host.stop()
