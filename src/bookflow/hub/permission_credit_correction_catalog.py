"""Explicit executable credit-correction delta over the accepted bill descriptor.

`customer-refund update`, `vendor-credit update` and `vendor-credit history`
are registered commands that arrived after the frozen ancestor was accepted, so
this is where a root accepts them. They do not belong in that ancestor: every
historical version is built by replace() over the one shared frozen base, and an
edit there rewrites descriptors that roots activated and stored long ago, which
leaves those roots unable to load their own permission state at all.
"""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_bill_deletion_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '3fb08d66917cf87c846efc9154474d6512e82d70'
ADDED_COMMANDS = (
    c.CommandDescriptor('customer-refund update','company','ledger.post','standard',(),True,None,False,False,None,None,None,None),
    c.CommandDescriptor('vendor-credit history','company','ledger.read','member',(),True,None,False,False,None,None,None,None),
    c.CommandDescriptor('vendor-credit update','company','ledger.post','standard',(),True,None,False,False,None,None,None,None),
)
# One action per added command, carrying the planner that still performs the
# remaining target-graph checks. The refund correction reached the ancestor with
# its action; the two vendor-credit verbs reached it without one, so the tip has
# been describing two commands it declared no company action for.
ADDED_ACTIONS = (
    c.CompanyAction('customer-refund update',(c.Requirement('ledger.post','standard'),),True,
        ('bookflow.commands.refund_cmds._write.<locals>.planner|src/bookflow/commands/refund_cmds.py',)),
    c.CompanyAction('vendor-credit history',(c.Requirement('ledger.read','member'),),True,
        ('bookflow.commands.vendor_credit_cmds._read.<locals>.planner|src/bookflow/commands/vendor_credit_cmds.py',)),
    c.CompanyAction('vendor-credit update',(c.Requirement('ledger.post','standard'),),True,
        ('bookflow.commands.vendor_credit_cmds._write.<locals>.planner|src/bookflow/commands/vendor_credit_cmds.py',)),
)
CATALOG = replace(previous.CATALOG, version=c.CREDIT_CORRECTION_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands,*ADDED_COMMANDS),key=lambda x:x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions,*ADDED_ACTIONS),key=lambda x:x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST),sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT,CATALOG,MANIFEST.standalone_names,digest)
