"""Add the credit card credit without rewriting any accepted permission descriptor.

A card credit is a refund onto a company card -- a returned part, a vendor's credit to the
card -- and its six commands are admitted exactly as a card charge's are: entering, correcting
and voiding one at `ledger.post` standard, reading one at `ledger.read` member, each with one
company action carrying the planner that runs it. No capability, role default, admin action or
existing threshold changes, so a root replacing its catalog with this one suspends no agent and
revokes no token, and a root activated at the previous catalog admits these commands through
the tip without re-activation.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_everyday_reports_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '285bf7621ed3bb5254212fe0723235e504604a54'
# Verb -> (capability, threshold, the check_cmds factory whose planner runs it).
VERBS = {
    'history': ('ledger.read', 'member', '_read'),
    'post': ('ledger.post', 'standard', '_write'),
    'query': ('ledger.read', 'member', '_read'),
    'show': ('ledger.read', 'member', '_read'),
    'update': ('ledger.post', 'standard', '_write'),
    'void': ('ledger.post', 'standard', '_write'),
}
ADDED_COMMANDS = tuple(
    c.CommandDescriptor('card-credit ' + verb, 'company', capability, threshold, (), True, None, False, False,
                        None, None, None, None)
    for verb, (capability, threshold, _) in sorted(VERBS.items()))
ADDED_ACTIONS = tuple(
    c.CompanyAction('card-credit ' + verb, (c.Requirement(capability, threshold),), True,
                    (f'bookflow.commands.check_cmds.{factory}.<locals>.planner|src/bookflow/commands/check_cmds.py',))
    for verb, (capability, threshold, factory) in sorted(VERBS.items()))
CATALOG = replace(previous.CATALOG, version=c.CARD_CREDIT_POLICY_VERSION,
    commands=tuple(sorted((*previous.CATALOG.commands, *ADDED_COMMANDS), key=lambda x: x.name)),
    company_actions=tuple(sorted((*previous.CATALOG.company_actions, *ADDED_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
