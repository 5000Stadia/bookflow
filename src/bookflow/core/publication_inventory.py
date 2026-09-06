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
    "upgrade": "returned_company_registrations_and_shared_may_write",
}


def policy(cmd):
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
    return [{"command": cmd.name, "policy": policy(cmd), "scope": cmd.scope,
             "credential": "original_secret_current_binding",
             "actor_and_memberships": "current_exact_authority",
             "required_role": cmd.required_role,
             "resources": cmd.resource_requirements,
             "conditional_authority": None if cmd.authorize_input is None else cmd.authorize_input.__module__ + "." + cmd.authorize_input.__name__,
             "transfer_authority": None if cmd.transfer is None else "registered_transfer_prepare",
             "additional": "original_event_role" if cmd.name == "undo" else "bound_principal" if cmd.name == "directive add" else None}
            for cmd in registry.all_commands(include_standalone=True)]
