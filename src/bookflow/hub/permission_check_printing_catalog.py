"""Add Print Checks without rewriting any accepted descriptor (chained on partial-po-v1).

R152. `check print`, `check print-setup` and the three people-only acts are `ledger.post`
`standard`, as `check post` is; `check print-queue`, `check print-run`, `check print-pdf` and
`check print-alignment` are reads at `ledger.read` `member`, as `check show` is. Confirming a print
run, cancelling one and reprinting a check say what happened to paper in a printer, so each carries
a people-only admin action: an agent may queue checks and prepare a run, never answer "Did checks
print OK?". No capability, role default or existing threshold changes, so a root replacing its
catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_partial_po_catalog as previous, permission_catalog as c
from . import permission_human_admin_catalog as human_admin
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '908099d969fe9b4f1b0f1bb284965df9150939ad'
_OWNER = 'bookflow.commands.check_print_cmds.{}|src/bookflow/commands/check_print_cmds.py'
_ADDED = (
    ('check print', 'ledger.post', 'standard', 'plan_print'),
    ('check print-alignment', 'ledger.read', 'member', 'plan_print_alignment'),
    ('check print-cancel', 'ledger.post', 'standard', 'plan_print_cancel'),
    ('check print-confirm', 'ledger.post', 'standard', 'plan_print_confirm'),
    ('check print-pdf', 'ledger.read', 'member', 'plan_print_pdf'),
    ('check print-queue', 'ledger.read', 'member', 'plan_print_queue'),
    ('check print-run', 'ledger.read', 'member', 'plan_print_run'),
    ('check print-setup', 'ledger.post', 'standard', 'plan_print_setup'),
    ('check reprint', 'ledger.post', 'standard', 'plan_reprint'),
)
PEOPLE_ONLY = ('check print-cancel', 'check print-confirm', 'check reprint')
ADDED_COMMANDS = tuple(
    c.CommandDescriptor(name, 'company', capability, threshold, (), True, None, False, False, None, None, None, None)
    for name, capability, threshold, _ in _ADDED)
ADDED_ACTIONS = tuple(
    c.CompanyAction(name, (c.Requirement(capability, threshold),), True, (_OWNER.format(planner),))
    for name, capability, threshold, planner in _ADDED)
ADDED_ADMIN_ACTIONS = tuple(c.AdminAction(human_admin.PEOPLE_ONLY_PREFIX + name, 'company', 'standard', True, True)
                            for name in PEOPLE_ONLY)
CATALOG = replace(previous.CATALOG, version=c.CHECK_PRINTING_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
