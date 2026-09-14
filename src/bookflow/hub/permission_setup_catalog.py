"""Explicit public setup descriptor delta over the accepted phase1 inventory."""
from dataclasses import asdict, replace
import hashlib
import json
from . import permission_activation_catalog as previous, permission_catalog as c
from .permission_snapshot import CatalogBundle

SOURCE_COMMIT = '4db21bc4d808c25288f10709ae4c66b918b75baa'
ADDED_COMMANDS = (
    c.CommandDescriptor('membership effective','hub','membership','authenticated',(),True,None,False,False,None,'bookflow.commands.permission_cmds.authorize_effective|src/bookflow/commands/permission_cmds.py',None,None),
    c.CommandDescriptor('permission activate','hub','user','hub_admin',(),True,None,False,False,None,None,None,None),
    c.CommandDescriptor('permission show','hub','user','hub_admin',(),True,None,False,False,None,None,None,None),
)
CHANGED_AUTHORIZATION = {'membership grant': 'human administrator of that company or organization; owner to grant or move an owner; installation shortcut only before permission activation', 'membership list': 'the memberships you administer: installation-wide only in legacy mode; those of a company or organization you administer, and always your own', 'membership revoke': 'human administrator of that company or organization; owner to revoke an owner; installation shortcut only before permission activation', 'user list': 'the principals you administer: installation-wide only in legacy mode; the members of a company or organization you administer, and always yourself'}
CATALOG = replace(previous.CATALOG,version=c.SETUP_POLICY_VERSION,
    commands=tuple(sorted((*(replace(x,authorization_text=CHANGED_AUTHORIZATION[x.name]) if x.name in CHANGED_AUTHORIZATION else x for x in previous.CATALOG.commands),*ADDED_COMMANDS),key=lambda x:x.name)))
MANIFEST = c.catalog_manifest(CATALOG,previous.MANIFEST.standalone_names)


def catalog_bundle():
    inventory = dict(manifest=asdict(MANIFEST),sources=[asdict(x) for x in CATALOG.conditional_sources])
    digest = hashlib.sha256(json.dumps(inventory,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return CatalogBundle(SOURCE_COMMIT,CATALOG,MANIFEST.standalone_names,digest)
