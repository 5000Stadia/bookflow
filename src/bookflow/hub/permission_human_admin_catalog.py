"""Keep the closing date and who-can-do-what in a person's hands, without rewriting any accepted descriptor.

The desktop product keeps the closing date and user setup with its Admin, a person. This delta
records the same rule as human-only administrative actions, which the catalog already knows how to
say (`AdminAction.human_only`):

* `admin:company:closing-date` -- setting, moving or clearing a company's closing date. `company
  update` stays at company `admin` for every other field; only a change to `closing_date` asks
  for a person. Its authorization text says so.
* `admin:people-only:<command>` -- one per command that changes who can do what: memberships with
  their grants and denials (a role is a membership's), agents and their principals and
  authorization, tokens, accounts, and the activation that enrols administrators. Every write at
  the `user`, `membership` and `token` capabilities in the catalog below is here except `token
  revoke`, which stays open so an agent can always revoke its own token; and `backup schedule`,
  which chooses where a company's books are copied. Reads stay as they were.

`hub.people_only` reads these keys from the tip; the command list lives here and nowhere else.
No command, capability, role default, company action or threshold changes. The new actions are
admitted only to people, so no agent's own authority signature moves and a root replacing its
catalog with this one suspends no agent and revokes no token.
"""
from dataclasses import asdict, replace
import hashlib
import json

from . import permission_backup_schedule_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = 'ac3de3f1318b96dd4ea4a35bfaf6795201954550'
CLOSING_DATE_ACTION = 'admin:company:closing-date'
PEOPLE_ONLY_PREFIX = 'admin:people-only:'
PEOPLE_ONLY_COMMANDS = (
    'agent activate', 'agent assign', 'agent authorize', 'agent create', 'agent deactivate', 'agent unassign',
    'membership grant', 'membership revoke', 'permission activate', 'token issue',
    'user activate', 'user add', 'user deactivate', 'user set-password',
)
_THRESHOLD = {x.name: x.threshold for x in previous.CATALOG.commands}
# `backup schedule` (backup-schedule-v1, below) already refuses anyone but a person in its own
# code; listing it here makes the catalog say so too.
PEOPLE_ONLY_COMMANDS += tuple(name for name in ('backup schedule',) if name in _THRESHOLD)
CHANGED_AUTHORIZATION = {
    'company update': 'company admin; changing the closing date needs a person, never an agent',
}
ADDED_ADMIN_ACTIONS = (
    c.AdminAction(CLOSING_DATE_ACTION, 'company', 'admin', True, True),
    *(c.AdminAction(PEOPLE_ONLY_PREFIX + name, 'hub', _THRESHOLD[name], True, True) for name in PEOPLE_ONLY_COMMANDS),
)
CATALOG = replace(previous.CATALOG, version=c.HUMAN_ADMIN_POLICY_VERSION,
    commands=tuple(replace(x, authorization_text=CHANGED_AUTHORIZATION[x.name]) if x.name in CHANGED_AUTHORIZATION else x
                   for x in previous.CATALOG.commands),
    admin_actions=tuple(sorted((*previous.CATALOG.admin_actions, *ADDED_ADMIN_ACTIONS), key=lambda x: x.key)))
MANIFEST = c.catalog_manifest(CATALOG, previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST), sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT, CATALOG, MANIFEST.standalone_names, digest)
