"""The company half of hub.capabilities: one declared `require_resource` site (catalog capabilities-v1).

An agent's capability is admitted like any company requirement -- its own floor and denies, its principal's, the
binding and suspension (permission_runtime.require_company) -- after hub.capabilities found its explicit grant.
"""
from bookflow.hub import access


def require_held(s, capability: str, threshold: str) -> None:
    access.require_resource(s, capability, threshold)
