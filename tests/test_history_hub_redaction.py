"""Hub completeness observes reader masking and producer-owned vocabulary."""
from dataclasses import fields
from types import SimpleNamespace
import pytest
from bookflow.core.errors import BookflowError
from bookflow.hub import audit_projection as p, identity_admin as admin
from bookflow.hub import permission_snapshot as snapshot, permission_admin_audit as producer
from bookflow.hub.agent_authority import TokenRow


COMMON=dict(id='record',version=1,created_at='2026-09-08T00:00:00Z',created_by='actor',created_via='cli',
    updated_at='2026-09-08T00:00:00Z',updated_by='actor',updated_via='cli')
REGISTRY=dict(COMMON,display_name='Company',name_key='company',path='organizations/o/c',pending_path=None,is_demo=False)


def audience(*,admin=False,parent=True):
    return SimpleNamespace(global_admin=lambda:admin,identity=SimpleNamespace(actor='record'),
        subjects=lambda:('record',),identity_visible=lambda identity:identity is not None and (admin or identity=='record'),
        visible=lambda scope:parent or scope.kind=='company',administrator=lambda scope:False)


def test_registry_parent_redaction_marks_admin_view_without_serializing_signal():
    row=dict(REGISTRY,organization_id='org',legal_name='Company',home_currency='USD',schema_revision='0024')
    reader=audience(admin=True,parent=False)
    reader.visible=lambda scope:scope.kind=='company'
    view=p._hub_view(reader,'company','record',row)
    assert view.organization_id is None and view.reader_redacted
    assert 'reader_redacted' not in view.model_dump()
    complete=p._hub_view(audience(admin=True),'company','record',row)
    assert complete.organization_id=='org' and not complete.reader_redacted
    with pytest.raises(BookflowError):p._hub_view(reader,'company','record',dict(row,reader_redacted=False))


def test_self_user_and_token_identity_masking_use_same_signal():
    user=dict(COMMON,kind='human',username='reader',display_name='Reader',owner_user_id=None,
        password_hash=None,hub_admin=False,timezone='UTC',active=True)
    view=p._hub_view(audience(),'user','record',user)
    assert view.reader_redacted and view.kind is None
    assert not p._hub_view(audience(admin=True),'user','record',user).reader_redacted
    token=dict(COMMON,user_id='record',on_behalf_of='hidden',kind='bearer',label=None,
        expires_at=None,last_used_at=None,revoked_at=None,authority_epoch=None)
    view=p._hub_view(audience(),'api_token','record',token)
    assert view.reader_redacted and view.on_behalf_of is None
    assert not p._hub_view(audience(admin=True),'api_token','record',token).reader_redacted


def test_membership_identity_redaction_and_scope_collapse():
    row=dict(id='membership',user_id='record',scope_type='company',scope_id='company',role='owner',
        grants=None,denies=None,granted_by='other',granted_at='2026-09-08T00:00:00Z',revoked_at=None)
    reader=audience()
    view=p._hub_view(reader,'membership','membership',row)
    assert view.granted_by is None and view.reader_redacted
    reader.visible=lambda scope:False
    assert p._hub_view(reader,'membership','membership',row) is None


def test_supported_producers_are_hub_owned_not_company_repair_commands():
    from bookflow.core import registry
    registry.load_all()
    private={'permission '+kind for kind in admin.AUDIT_KINDS.values()}
    assert private=={
        'permission membership put','permission membership revoke','permission user active',
        'permission assignments set','permission agent authorize','permission catalog replace'}
    assert p.HUB_EXPLANATION_COMMANDS==private|{'organization new','organization rename'}
    for command in p.HUB_EXPLANATION_COMMANDS-private:
        assert registry.get(command).scope=='hub'
    assert not {'company new','company rename','company use','invoice post'} & p.HUB_EXPLANATION_COMMANDS


# Every supported capture field is explicitly classified. Adding *any* field,
# even one whose name does not advertise a reference, requires a disclosure review.
# Identity metadata in the omitted set is universally stripped or global-admin
# only; it is not a scope grant. Membership/registry scope references are live
# visibility decisions. Assignment identities rely on the WHOLE co-effect event.
BASE='id version created_at created_by created_via updated_at updated_by updated_via'
CONTRACTS={
 p.RegistryCaptured:(BASE+' display_name name_key path pending_path is_demo', 'id created_by updated_by'),
 p.CompanyRegistryCaptured:(BASE+' display_name name_key path pending_path is_demo organization_id legal_name home_currency schema_revision','id organization_id created_by updated_by'),
 p.LegacyUserCaptured:(BASE+' kind username display_name owner_user_id password_hash hub_admin timezone active','id owner_user_id created_by updated_by'),
 p.LegacyTokenCaptured:(BASE+' user_id on_behalf_of kind label expires_at last_used_at revoked_at authority_epoch','id user_id on_behalf_of created_by updated_by'),
 p.LegacyMembershipCaptured:('id user_id scope_type scope_id role grants denies granted_by granted_at revoked_at version updated_at updated_by updated_via','id user_id scope_id granted_by updated_by'),
 snapshot.MembershipRow:('id user_id scope_type scope_id role grants denies granted_by granted_at revoked_at version updated_at updated_by updated_via','id user_id scope_id granted_by updated_by'),
 snapshot.AssignmentRow:('agent_user_id principal_user_id assigned_by assigned_at revoked_at','agent_user_id principal_user_id assigned_by'),
 snapshot.AuthorityRow:('agent_user_id epoch suspended_at suspension_reason version updated_at updated_by updated_via authorized_at authorized_by permitted_use_at fresh_context_ack_at fresh_context_required','agent_user_id updated_by authorized_by'),
 producer.UserAuditRow:(BASE+' kind active hub_admin owner_user_id','id owner_user_id created_by updated_by'),
 TokenRow:(BASE+' user_id on_behalf_of kind expires_at revoked_at authority_epoch','id user_id on_behalf_of created_by updated_by'),
 producer.StateRow:('id generation mode catalog_version catalog_sha256 catalog_json updated_at updated_by updated_via','updated_by'),
}


@pytest.mark.parametrize('model',tuple(CONTRACTS))
def test_capture_field_inventory_requires_new_reference_review(model):
    names,references=CONTRACTS[model]
    actual=set(model.model_fields) if hasattr(model,'model_fields') else {x.name for x in fields(model)}
    assert actual==set(names.split())
    assert set(references.split())<=actual
