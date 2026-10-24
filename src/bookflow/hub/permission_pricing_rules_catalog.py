"""Add price rules (R110) without rewriting any accepted descriptor.

Nine company commands, all on the existing `price-level` capability, as price levels are: writing a rule
or the conflict preference is `standard`, reading rules, the preference and a price preview is `member`.
Nothing here is reserved to a person; no capability, role default or existing threshold changes, so a
root replacing its catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_collections_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'd9a5fc50b874c6e9290d0dea7bfbaaefe2e80efa'
_OWNER = 'bookflow.commands.pricing_rule_cmds.{}|src/bookflow/commands/pricing_rule_cmds.py'
# (name, threshold, planner)
_ADDED = (
    ('price-rule activate', 'standard', 'plan_activate'),
    ('price-rule create', 'standard', 'plan_create'),
    ('price-rule deactivate', 'standard', 'plan_deactivate'),
    ('price-rule list', 'member', 'plan_list'),
    ('price-rule preferences', 'member', 'plan_preferences'),
    ('price-rule preview', 'member', 'plan_preview'),
    ('price-rule set-preferences', 'standard', 'plan_set_preferences'),
    ('price-rule show', 'member', 'plan_show'),
    ('price-rule update', 'standard', 'plan_update'),
)

ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'price-level', threshold, (), True, None, False, False, None, None, None, None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement('price-level', threshold),), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
CATALOG = replace(previous.CATALOG, version=c.PRICING_RULES_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
