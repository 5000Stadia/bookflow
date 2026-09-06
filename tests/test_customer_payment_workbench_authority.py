"""New HTML paths retain the existing complete conditional-resource read gate."""
import pytest
from fastapi.testclient import TestClient

from bookflow import BookflowError
from bookflow.commands.host_cmds import start_serving
from bookflow.core.context import client_version
from bookflow.core.config import os_login
from bookflow.company import payment_authority
from tests.test_service_sales_lifecycle import sale, COMPANY
from tests.test_payment_receipts import method
from tests.test_work_billing_lifecycle import accepted, bill
from tests.test_row3_host import PASSWORD


def test_every_payment_workbench_direct_path_requires_historical_work_resource(client,sale,monkeypatch):
    invoice=bill(client,accepted(client,sale))
    paid=client.run('payment receive',dict(customer=sale['customer'],date='2026-06-02',amount='1',
        payment_method=method(client),operation_key='workbench-protected',applications=dict(mode='inline',items=[
            dict(invoice=invoice['id'],expected_version=1,amount='1')])),company=COMPANY)
    app=paid['effect']['applications'][0]['application_id']
    draft=client.run('payment selection create',dict(mode='existing_credit',payment=paid['id'],date='2026-06-02'),company=COMPANY)
    client.run('payment unapply',dict(payment=paid['id'],expected_version=1,operation_key='workbench-protected-unapply',
        applications=[dict(application_id=app,invoice_expected_version=2)]),reason='Release application, retain its authority history',company=COMPANY)
    company=client.company.show(company=COMPANY)['company_id']
    client.run('user set-password',dict(username=os_login(),password=PASSWORD))
    handle=start_serving(client.data_root,client_version(),bind='127.0.0.1:8765',secure_cookies=False,publish_descriptor=False)
    try:
        api=TestClient(handle.app)
        assert api.post('/login',json=dict(username=os_login(),password=PASSWORD)).status_code==200
        base='/c/'+company
        paths=[f'/receive-payments?payment={paid["id"]}',f'/receive-payments?selection={draft["id"]}',
            f'/payment/{paid["id"]}',f'/application/{app}',f'/application/{app}/history',f'/invoice/{invoice["id"]}/settlement']
        for path in paths:
            response=api.get(base+path);assert response.status_code==200,response.text
        # Existing symbolic resource guard permits an independently restricted
        # work resource even while company/ledger admission remains authorized.
        original=payment_authority.require_resource
        def deny_work(session,capability,role):
            if capability=='customer-work':raise BookflowError('E_PERMISSION',details={'capability':capability})
            return original(session,capability,role)
        monkeypatch.setattr(payment_authority,'require_resource',deny_work)
        for path in paths:
            response=api.get(base+path)
            assert response.status_code==403,(path,response.status_code,response.text)
            assert 'Sale witness customer' not in response.text
        response=api.post('/companies/'+company+'/commands/payment.operation.show',
            json=dict(operation_key='workbench-protected'),headers={'X-Bookflow-Workbench':'1'})
        assert response.status_code==403 and response.json()['code']=='E_PERMISSION'
    finally:
        handle.stop()
