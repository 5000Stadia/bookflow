"""Turn the people-only acts into grantable capabilities (R187 increment 1) without rewriting any accepted descriptor.

design/principal-directives.md §2 and §4: one question decides every act, *does this actor hold the capability?*,
asked the same way of a person and an agent on every interface. Each former people-only command now names a
capability. A person holds it by role exactly as they could run the command before (the defaults below repeat the
command's own threshold), so no person gains or loses anything; an agent holds it only when its principal grants it
(`membership grant --grants`), and then only while its principal holds it too (hub.capabilities is the gate).

Five acts stay tied to a human credential or to the root of trust (`STAYS_HUMAN`): their `admin:people-only:` keys
keep `human_only`. Every other human-only admin action, the closing date and the `admin:members:*`, `admin:users`
and `admin:agents` rule keys included, clears it. Clearing it can only add bits to an agent's authority signature,
and every capability below is new, so a person's signature gains atoms and loses none: a root replacing its catalog
with this one suspends no agent and revokes no token. No command, role default, company action or threshold that
already existed changes; only the authorization text of the commands that said "human" does.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_bank_feed_reverse_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '5de700e0f126c358e455a5b541af89a663e18630'
PEOPLE_ONLY_PREFIX = 'admin:people-only:'
CLOSING_DATE_ACTION = 'admin:company:closing-date'
CLOSING_DATE_CAPABILITY = 'company.closing-date'
# The five that no capability could make safe (§2 b).
STAYS_HUMAN = ('agent assign', 'agent authorize', 'permission activate', 'token issue', 'user set-password')
# capability -> (threshold a person holds it at, the commands it opens). Hub capabilities are held at hub_admin.
CAPABILITIES = {
    'party.merge': ('standard', ('customer merge', 'customer unmerge', 'vendor merge', 'vendor unmerge')),
    'list.merge': ('standard', ('account merge', 'account unmerge', 'item merge', 'item unmerge')),
    'restore.document': ('standard', ('invoice restore', 'journal restore')),
    'item.convert': ('standard', ('item convert',)),
    'check.print-confirm': ('standard', ('check print-cancel', 'check print-confirm', 'check reprint')),
    'clock.link': ('standard', ('clock link',)),
    'accountant.manage': ('admin', ('accountant add', 'accountant remove', 'accountant-copy cancel',
                                    'accountant-copy import')),
    'costing.method': ('admin', ('costing-method set', 'costing-method withdraw')),
    CLOSING_DATE_CAPABILITY: ('admin', ()),  # `company update` changing closing_date
    'membership.grant': ('admin', ('membership grant', 'membership revoke')),
    'backup.schedule': ('hub_admin', ('backup schedule',)),
    'demo.reset': ('hub_admin', ('demo reset',)),
    'user.manage': ('hub_admin', ('user activate', 'user add', 'user deactivate')),
    'agent.manage': ('hub_admin', ('agent activate', 'agent create', 'agent deactivate', 'agent unassign')),
    # §3 and §4: the consumers' acts that were people-only until now (R112).
    'delivery.send': ('standard', ('delivery approve',)),
    'delivery.mark-sent': ('standard', ('delivery mark-sent',)),
    'delivery.connect': ('admin', ('delivery-connection add', 'delivery-connection check', 'delivery-connection disable',
                                   'delivery-connection enable', 'delivery-connection remove',
                                   'delivery-connection update')),
    'automate.delivery': ('standard', ('delivery-schedule live',)),
}
COMMAND_CAPABILITY = {name: capability for capability, (_, names) in CAPABILITIES.items() for name in names}
_RANK = ('readonly', 'standard', 'admin', 'owner')
ADDED_CAPABILITIES = tuple(
    c.CapabilitySpec(name, (), ('hub_admin',)) if threshold == 'hub_admin' else c.CapabilitySpec(name, (threshold,), (threshold,))
    for name, (threshold, _) in sorted(CAPABILITIES.items()))
ADDED_DEFAULTS = tuple(
    c.DefaultEntry(role, c.Requirement(name, threshold))
    for name, (threshold, _) in sorted(CAPABILITIES.items())
    for role in ((*_RANK[_RANK.index(threshold):], 'hub_admin') if threshold != 'hub_admin' else ('hub_admin',)))
_ADMIN = 'installation administrator, or an agent its principal granted {}'
CHANGED_AUTHORIZATION = {
    'company update': 'company admin; changing the closing date needs company.closing-date',
    'demo reset': 'hub administrator, or an agent granted demo.reset; never while the demo organization holds a company the demo did not create; a root holding real books elsewhere needs force naming it',
    'user add': 'hub administrator, or an agent granted user.manage (no hub administrator, no password, no role above '
                'its own); the grant also needs administration of that scope',
    'user activate': _ADMIN.format('user.manage') + ', on an activated installation',
    'user deactivate': _ADMIN.format('user.manage') + ', on an activated installation',
    'agent create': _ADMIN.format('agent.manage') + ', on an activated installation',
    'agent activate': _ADMIN.format('agent.manage') + ', on an activated installation',
    'agent deactivate': _ADMIN.format('agent.manage') + ', on an activated installation',
    'agent unassign': _ADMIN.format('agent.manage') + ', on an activated installation',
    'membership grant': 'administrator of that company or organization (an agent: granted membership.grant, never its '
                        "own, a principal's or a hub administrator's membership, no role or capability above its own "
                        "or its principal's); owner to grant or move an owner; installation shortcut only before "
                        'permission activation',
    'membership revoke': 'administrator of that company or organization (an agent: granted membership.grant, never its '
                         "own, a principal's or a hub administrator's membership, nor one carrying a deny it could not "
                         'lift); owner to revoke an owner; installation shortcut only before permission activation',
}


def _cleared(action):
    if not action.human_only:
        return action
    if action.key.startswith(PEOPLE_ONLY_PREFIX) and action.key[len(PEOPLE_ONLY_PREFIX):] in STAYS_HUMAN:
        return action
    return replace(action, human_only=False)


# The agent's half of every company capability is admitted at one site (hub.capabilities -> company.capability_gate).
GATE_SOURCE = c.ResourceSource('bookflow.company.capability_gate.require_held',
    (('src/bookflow/company/capability_gate.py', 10),),
    tuple(c.Requirement(name, threshold) for name, (threshold, _) in sorted(CAPABILITIES.items())
          if threshold != 'hub_admin'))
CATALOG = replace(previous.CATALOG, version=c.CAPABILITIES_POLICY_VERSION,
    commands=tuple(replace(x, authorization_text=CHANGED_AUTHORIZATION[x.name]) if x.name in CHANGED_AUTHORIZATION else x
                   for x in previous.CATALOG.commands),
    capabilities=tuple(sorted((*previous.CATALOG.capabilities, *ADDED_CAPABILITIES), key=lambda x: x.name)),
    defaults=(*previous.CATALOG.defaults, *ADDED_DEFAULTS),
    admin_actions=tuple(_cleared(x) for x in previous.CATALOG.admin_actions),
    conditional_sources=tuple(sorted((*previous.CATALOG.conditional_sources, GATE_SOURCE), key=lambda x: x.owner)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
