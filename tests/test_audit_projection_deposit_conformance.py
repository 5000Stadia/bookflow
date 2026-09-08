"""Private capture conformance; not public audit adapter acceptance."""
import importlib
import pytest
from bookflow.hub import audit_projection_legacy as views
OWNERS = (('DepositAuditCashBack', 'bookflow.company.deposit_draft_models.CashBack'), ('DepositAuditCustomCapture', 'bookflow.company.deposit_draft_models.CustomCapture'), ('DepositAuditHeader', 'bookflow.company.deposit_draft_models.Header'), ('DepositAuditSource', 'bookflow.company.deposit_draft_models.Source'), ('DepositAuditParty', 'bookflow.company.deposit_models.Party'), ('DepositAuditAdditional', 'bookflow.company.deposit_draft_models.Additional'), ('DepositAuditSummary', 'bookflow.company.deposit_draft_models.Summary'), ('DepositAuditManifest', 'bookflow.company.deposit_draft_models.Manifest'), ('DepositAuditSignedMoney', 'bookflow.company.deposit_models.SignedMoney'), ('DepositAuditCashBackInput', 'bookflow.company.deposit_models.CashBackInput'), ('DepositAuditSourceInput', 'bookflow.company.deposit_models.SourceInput'), ('DepositAuditAdditionalInput', 'bookflow.company.deposit_models.AdditionalInput'), ('DepositAuditInlineDocument', 'bookflow.company.deposit_models.InlineDocument'), ('DepositAuditDraftDocument', 'bookflow.company.deposit_models.DraftDocument'), ('DepositAuditPostInput', 'bookflow.company.deposit_lifecycle_models.PostInput'), ('DepositAuditReplacementDocument', 'bookflow.company.deposit_models.ReplacementDocument'), ('DepositAuditUpdateInput', 'bookflow.company.deposit_lifecycle_models.UpdateInput'), ('DepositAuditVoidInput', 'bookflow.company.deposit_lifecycle_models.VoidInput'), ('DepositAuditConsumedDraftState', 'bookflow.company.deposit_lifecycle_models.ConsumedDraftState'), ('DepositAuditDraftRowIdentity', 'bookflow.company.deposit_lifecycle_models.DraftRowIdentity'), ('DepositAuditDraftConsumptionReceipt', 'bookflow.company.deposit_lifecycle_models.DraftConsumptionReceipt'), ('DepositAuditDocumentState', 'bookflow.company.deposit_lifecycle_models.DocumentState'), ('DepositAuditMembershipChange', 'bookflow.company.deposit_lifecycle_models.MembershipChange'), ('DepositAuditHeaderChange', 'bookflow.company.deposit_lifecycle_models.HeaderChange'), ('DepositAuditBankEffect', 'bookflow.company.bank_effects.BankEffect'), ('DepositAuditLifecycleEffect', 'bookflow.company.deposit_lifecycle_models.LifecycleEffect'), ('DepositAuditLifecycleOutput', 'bookflow.company.deposit_lifecycle_models.LifecycleOutput'))
@pytest.mark.parametrize('view_name,owner_name',OWNERS)
def test_deposit_capture_fields(view_name,owner_name):
    module,name=owner_name.rsplit('.',1)
    owner=getattr(importlib.import_module(module),name)
    view=getattr(views,view_name)
    expected=set(owner.model_fields)
    if name in ('PostInput','UpdateInput','VoidInput'):expected.remove('operation_key')
    assert set(view.model_fields)-{'projection_partial'}==expected
    for key,field in owner.model_fields.items():
        if key in expected:assert view.model_fields[key].alias==field.alias


def test_deposit_reference_routes_have_serializable_null_fields():
    from pydantic import TypeAdapter
    owners={getattr(views,name) for name,_ in OWNERS}
    routes=set(views._OBJECT_REFERENCE_KINDS)|set(views._OBJECT_FIELD_REQUIREMENTS)
    for owner,groups in views._REFERENCE_GROUPS.items():
        routes.update((owner,key) for fields,_ in groups for key in fields)
    for owner,fields in views._POLYMORPHIC_PARTIES.items():
        routes.update((owner,key) for key in fields)
    for owner,key in routes:
        if owner in owners:
            assert key in owner.model_fields,(owner.__name__,key)
            assert TypeAdapter(owner.model_fields[key].annotation).validate_python(None) is None
