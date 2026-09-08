"""Recovery audit adapters explicitly cover their command and stored row owners."""
import importlib
import pytest
from bookflow.company import schema
from bookflow.hub import audit_projection_legacy as views

OWNERS = (('RecoveryAuditHeaderIntent', 'bookflow.company.payment_recovery_models.HeaderIntent'), ('RecoveryAuditBeginInput', 'bookflow.company.payment_recovery_models.BeginInput'), ('RecoveryAuditRecoveryEntry', 'bookflow.company.payment_recovery_models.RecoveryEntry'), ('RecoveryAuditUploadInput', 'bookflow.company.payment_recovery_models.UploadInput'), ('RecoveryAuditSealInput', 'bookflow.company.payment_recovery_models.SealInput'), ('RecoveryAuditApplyInput', 'bookflow.company.payment_recovery_models.ApplyInput'), ('RecoveryAuditAbortInput', 'bookflow.company.payment_recovery_models.AbortInput'), ('RecoveryAuditReplaceInput', 'bookflow.company.payment_recovery_models.ReplaceInput'), ('RecoveryAuditReceipt', 'bookflow.company.payment_recovery_outputs.Receipt'))

@pytest.mark.parametrize('view_name,owner_name',OWNERS)
def test_recovery_owned_fields(view_name,owner_name):
    module,name=owner_name.rsplit('.',1)
    owner=getattr(importlib.import_module(module),name)
    assert set(getattr(views,view_name).model_fields)-{'tag','projection_partial'}==set(owner.model_fields)

@pytest.mark.parametrize('view_name,table_name',(
    ('RecoveryHeaderView','payment_selection_recoveries'),
    ('RecoveryChunkView','payment_selection_recovery_chunks'),
    ('RecoveryItemView','payment_selection_recovery_items'),
    ('RecoveryActiveView','payment_selection_recovery_active'),
))
def test_recovery_captured_columns(view_name,table_name):
    assert set(getattr(views,view_name).model_fields)-{'tag','projection_partial'}==set(getattr(schema,table_name).c.keys())
