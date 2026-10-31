"""Bank rules and learning (R108 increment 4) on automation-v1, without rewriting any accepted descriptor.

`bank-rule create`, `update`, `deactivate`, `accept`, `decline` and `apply` are standard writes, and `bank-rule show`
and `query` member reads, under the existing capability `bank-feed`, with `ledger.read` at `member` beside each, as
the inbox's own commands are. Making an auto-add or ignore rule live asks `automate.bank-rule` (registered by
automation-v1) through `company.automation.require_establish`; the entries a live rule adds run each family's own
posting command under its establisher. `automation show` and `pause` check a bank rule against `bank-rule show` and
`update`, so the automation kind source's declared requirements grow by `bank-feed` at member and standard. No
capability, role default or threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_automation_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '06c29f99e0f2ea31a76b3e83877467cab5022c8b'
_OWNER = 'bookflow.commands.bank_rule_cmds.{}|src/bookflow/commands/bank_rule_cmds.py'
_LEDGER = (c.Requirement('ledger.read', 'member'),)
# (name, threshold, planner)
_ADDED = (
    ('bank-rule accept', 'standard', 'plan_bank_rule_accept'),
    ('bank-rule apply', 'standard', 'plan_bank_rule_apply'),
    ('bank-rule create', 'standard', 'plan_bank_rule_create'),
    ('bank-rule deactivate', 'standard', 'plan_bank_rule_deactivate'),
    ('bank-rule decline', 'standard', 'plan_bank_rule_decline'),
    ('bank-rule query', 'member', 'plan_bank_rule_query'),
    ('bank-rule show', 'member', 'plan_bank_rule_show'),
    ('bank-rule update', 'standard', 'plan_bank_rule_update'),
)
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', 'bank-feed', threshold, _LEDGER, True, None, False, False, None, None, None,
                        None)
    for name, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, tuple(sorted((*_LEDGER, c.Requirement('bank-feed', threshold)),
                                       key=lambda r: r.capability)), True, (_OWNER.format(planner),))
    for name, threshold, planner in _ADDED)
KIND_SOURCE = replace(previous.KIND_SOURCE, requirements=tuple(sorted(
    {*previous.KIND_SOURCE.requirements, c.Requirement('bank-feed', 'member'), c.Requirement('bank-feed', 'standard')},
    key=lambda r: (r.capability, r.threshold))))
CATALOG = replace(previous.CATALOG, version=c.BANK_RULE_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    conditional_sources=tuple(sorted(
        (*(x for x in previous.CATALOG.conditional_sources if x.owner != KIND_SOURCE.owner), KIND_SOURCE),
        key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
