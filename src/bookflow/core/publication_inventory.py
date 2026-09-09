"""Current registered publication dependencies; unknown hub projections fail review.

Company commands use selected registration, current role/resources and their shared
authorize_input predicate. Granular grants are a Row7 dependency, not inferred here.
"""

HUB = {
    "chart list": "static_product_metadata", "chart show": "static_product_metadata",
    "profile list": "static_product_metadata", "profile show": "static_product_metadata",
    "company list": "returned_company_registrations",
    "organization list": "returned_organization_visibility",
    "organization show": "returned_organization_visibility",
    "hub audit list": "visible_audit_records", "hub audit show": "visible_audit_records",
    "hub audit tail": "visible_audit_records",
    "company new": "resolved_organization_and_own_lifecycle_certificate",
    "company attach": "hub_admin_and_own_lifecycle_certificate",
    "company detach": "hub_admin_and_own_detach_certificate",
    "demo reset": "hub_admin_and_ordered_own_lifecycle_certificates",
    "organization new": "current_hub_admin", "organization rename": "current_hub_admin",
    "token issue": "resolved_target_principal_and_epoch",
    "token list": "resolved_target_and_actor_projection",
    "token revoke": "resolved_target_and_exact_own_revocation_certificate",
    "user set-password": "resolved_human_target_and_current_self_or_admin",
    "user add": "current_hub_admin_and_resolved_grant_scope",
    "membership grant": "resolved_target_user_and_current_scope_administration",
    "membership revoke": "resolved_target_user_and_current_scope_administration",
    "user list": "returned_listing_audience_and_current_scope_administration",
    "membership list": "returned_listing_audience_and_current_scope_administration",
    "upgrade": "returned_company_registrations_and_shared_may_write",
}


def policy(cmd):
    from bookflow.core.publication_payment import PAYMENT_COMMANDS
    planner = getattr(cmd, 'plan', None)
    if getattr(planner, '__module__', None) in {'bookflow.commands.payment_cmds', 'bookflow.commands.payment_recovery_cmds'} and cmd.name not in PAYMENT_COMMANDS:
        raise RuntimeError('Registered payment command lacks a publication dependency inventory: ' + cmd.name)
    if cmd.name in {'deposit show', 'deposit items'}:
        return 'reader_bound_public_detail_proof'
    if cmd.local_only or cmd.standalone:
        return "local_only"
    if cmd.scope == "company":
        return "selected_company_and_current_resources"
    if cmd.name not in HUB:
        raise RuntimeError("Registered hub command lacks a publication dependency inventory: " + cmd.name)
    return HUB[cmd.name]


def inventory():
    from bookflow.core import registry
    registry.load_all()
    from bookflow.core.publication_payment import captures
    return [{"command": cmd.name, "policy": policy(cmd), "scope": cmd.scope,
             "credential": "original_secret_current_binding",
             "actor_and_memberships": "current_exact_authority",
             "required_role": cmd.required_role,
             "resources": cmd.resource_requirements,
             "conditional_authority": None if cmd.authorize_input is None else cmd.authorize_input.__module__ + "." + cmd.authorize_input.__name__,
             "payment_authority": "owned_roots_current_shared_payment_requirements" if captures(cmd) else None,
             "transfer_authority": None if cmd.transfer is None else "registered_transfer_prepare",
             "additional": "original_event_role" if cmd.name == "undo" else "bound_principal" if cmd.name == "directive add" else None}
            for cmd in registry.all_commands(include_standalone=True)]
