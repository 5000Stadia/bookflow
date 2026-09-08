"""Owned payment schemas cannot grow past their declared historical adapters."""
from bookflow.company import payment_models as inputs,payment_outputs as outputs,schema
from bookflow.hub import audit_projection_legacy as views


def declared(model):
    return set(model.model_fields)-{'tag','projection_partial'}


def test_payment_operation_capture_covers_stored_header():
    assert declared(views.PaymentOperationView)==set(schema.payment_operations.c.keys())


def test_payment_operation_intent_covers_every_owned_input_field():
    for owner,view in (
        (inputs.PaymentReceiveInput,views.ReceiveIntent),
        (inputs.PaymentApplyInput,views.ApplyIntent),
        (inputs.PaymentUnapplyInput,views.UnapplyIntent),
        (inputs.PaymentUpdateInput,views.UpdateIntent),
        (inputs.PaymentVoidInput,views.VoidIntent),
    ):
        assert declared(view)==set(owner.model_fields)-{'operation_key'},owner.__name__


def test_payment_operation_result_covers_every_owned_output_field():
    for owner,view in (
        (outputs.PaymentWriteOutput,views.PaymentWriteResultView),
        (outputs.PaymentEffectOutput,views.PaymentEffectResultView),
        (outputs.PaymentCurrentOutput,views.PaymentCurrentResultView),
        (outputs.PaymentComponentOutput,views.SourceComponentResultView),
        (outputs.PaymentApplicationOutput,views.ApplicationResultView),
        (outputs.PaymentAllocationOutput,views.AllocationResultView),
        (outputs.InvoiceSettlementOutput,views.InvoiceSettlementResultView),
        (outputs.PaymentEffectHeader,views.PaymentEffectHeaderView),
        (outputs.PaymentEffectCounts,views.PaymentEffectCountsView),
    ):
        assert declared(view)==set(owner.model_fields),owner.__name__
