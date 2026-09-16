"""Current executable delta and strict immutable descriptor reuse."""
from dataclasses import replace
import pytest
from bookflow.core import registry
from bookflow.hub import permission_catalog as c, permission_setup_catalog as build
from bookflow.hub import permission_activation_catalog as previous, permission_runtime as runtime
from tests.test_permission_catalog import owner, R
from tests.test_permission_activation_catalog import test_every_resource_call_site_has_explicit_owner_disposition


def test_full_current_setup_descriptor_and_publication_inventory():
    registry.load_all()
    expected=[]
    for cmd in registry.all_commands(include_standalone=True):
        if cmd.standalone:
            continue
        expected.append(c.CommandDescriptor(cmd.name,cmd.scope,cmd.capability,cmd.required_role or 'authenticated',
            tuple(sorted((R(*x) for x in cmd.resource_requirements),key=lambda x:(x.capability,c.THRESHOLDS.index(x.threshold)))),
            True,cmd.feature,cmd.local_only,cmd.bootstrap,cmd.authorization,owner(cmd.authorize_input),
            owner(cmd.permanent_recovery),owner(cmd.transfer.prepare) if cmd.transfer else None))
    # Registry parity is the tip's. `build` is a frozen historical delta -- roots have
    # stored its descriptor, so it cannot grow to match today's registry, and naming
    # it here told a builder who added a command to edit an accepted delta. The
    # assertion below is the one this file owns: what the setup delta itself adds.
    assert runtime.current_catalog().CATALOG.commands==tuple(sorted(expected,key=lambda x:x.name))
    assert {x.name for x in build.CATALOG.commands}-{x.name for x in previous.CATALOG.commands}=={
        'permission show','permission activate','membership effective'}
    assert set(build.CHANGED_AUTHORIZATION)=={'membership grant','membership revoke','membership list','user list'}
    assert build.CATALOG.company_actions==previous.CATALOG.company_actions
    assert build.CATALOG.defaults==previous.CATALOG.defaults
    assert build.CATALOG.conditional_sources==previous.CURRENT_SOURCES
    from bookflow.core.publication_inventory import inventory, HUB
    rows={x['command']:x for x in inventory()}
    for name in ('permission show','permission activate','membership effective'):
        assert rows[name]['policy']==HUB[name]
    assert rows['membership effective']['conditional_authority']=='bookflow.commands.permission_cmds.authorize_effective'
    assert not any(x.available for x in build.CATALOG.company_actions if x.key.startswith('contract:delete:'))


def test_normalization_cache_preserves_types_and_changed_descriptor_validation():
    good=build.CATALOG
    assert c._normal_catalog(good)==c._normal_catalog(replace(good))
    # Dataclass equality considers True == 1; reuse must still reject the integer.
    wrong=replace(good,commands=(replace(good.commands[0],available=1),*good.commands[1:]))
    with pytest.raises(c.PolicyInputError):c._normal_catalog(wrong)
    with pytest.raises(c.PolicyInputError):c._normal_catalog(replace(good,defaults=list(good.defaults)))
    changed=replace(good,company_actions=tuple(replace(x,available=True) if x.key=='contract:delete:check' else x for x in good.company_actions))
    assert c.catalog_manifest(changed).descriptor_sha256 != c.catalog_manifest(good).descriptor_sha256


def test_decoded_catalog_reuse_requires_exact_bytes_version_and_digest():
    import json
    from bookflow.hub import permission_snapshot as snap
    raw=snap.encode_catalog(build.CATALOG)
    opts=dict(version=build.CATALOG.version,sha256=build.MANIFEST.descriptor_sha256)
    assert snap.decode_catalog(raw,**opts)==c._normal_catalog(build.CATALOG)
    assert snap.decode_catalog(raw,**opts)==c._normal_catalog(build.CATALOG)
    with pytest.raises(snap.SnapshotError):snap.decode_catalog(raw,**dict(opts,version='wrong'))
    with pytest.raises(snap.SnapshotError):snap.decode_catalog(raw,**dict(opts,sha256='0'*64))
    altered=json.loads(raw);altered['commands'][0]['available']=1
    with pytest.raises(snap.SnapshotError):snap.decode_catalog(json.dumps(altered),**opts)
    altered=json.loads(raw);altered['commands'][0]['available']=False
    with pytest.raises(snap.SnapshotError):snap.decode_catalog(json.dumps(altered),**opts)
