"""Every accepted catalog version still reproduces the descriptor roots stored.

An activation writes the exact descriptor it accepted into the root. Every later
read rebuilds that descriptor from the module `permission_runtime.known_catalog`
names for the stored version, and `permission_snapshot._load_root` refuses the
root when the two differ by one field. The historical modules are a delta chain:
each one `replace()`s the descriptor below it, and the whole chain stands on the
single shared ancestor `permission_catalog.FROZEN_CATALOG`. A command added to
that ancestor therefore rewrites versions accepted long before it existed.

That is not hypothetical, and it is not rare. Three commands were added to the
frozen ancestor instead of to a delta; `payment-deletion-v1` went from 456
commands to 459, an installation activated at that version could no longer load
its own permission state, and every company-scoped command failed with
`catalog_mismatch` -- while `permission activate`, the one command that could
have moved the root forward, failed on the same read. Nothing in the product
repaired it. A third change made the same edit the same afternoon. These pins are
what an ancestor edit hits first, instead of a person's books.

Adding a tip means adding a line. Changing a line already here means a root that
stored that descriptor can no longer be read: rewrite the delta instead.
"""
import pytest

from bookflow.hub import permission_catalog as c, permission_runtime as runtime, permission_snapshot as s

# version -> (command count, descriptor_sha256), oldest first; the last is the tip.
ACCEPTED = {
    'purchase-delete-activation-preparation-v1': (448, 'a0823e6d9ae98e96c98e2b6e4e73042ecc8ecb7cf0e7d4d9b50e8febc7ed6979'),
    'purchase-permission-setup-v1': (451, '5d71ff2a0970f85e12b6fca0f84ff2736176565db44064b0668f84a3c132e2f2'),
    'purchase-deletion-v1': (453, 'bdcd036b653a165a65279386fc91a6ac8ead2dc45fa49e7791e943067c989f9e'),
    'sales-deletion-v1': (455, 'cedd4c0ae270c83bcbaf2756124adf8d866694c00a8e05c06733282fcc844943'),
    'payment-deletion-v1': (456, '676fb94480d9d413d0233e3f869a479020b1ee3ce20fc03feef02c96d33b4323'),
    'bill-deletion-v1': (457, '6e0a2035aee9edc6ba907f602d531334eadb81b0176a280d237ff840a0c9d487'),
    'credit-correction-v1': (460, '53d98dab0d040498fb0837c4de7cd60e30894436b44a2de25aa41a39c9d518d7'),
    'credit-memo-deletion-v1': (461, 'b3818cc259b81e4f7ef277d386cfc703d1cab8bbffb499e3025afec51af9bf5b'),
    'deposit-deletion-v1': (462, '449e98a8fa02bb9b5e3b805e70acbb70c0438df79b857a1e804526c2bf40c750'),
    'job-time-v1': (471, '0072e030fb0f0b63b11f41c99facfa400a127148080e1fa3a60cbeffa53d0d07'),
    'journal-deletion-v1': (472, '8bb60e36da0155cf3551b01e83e7ded46ee06fce8c2962876a69da66636c0b95'),
}

# The legacy bridge, read by a root that was never activated. Such a root stores
# no descriptor, so this value moving breaks nobody by itself -- but it is the
# ancestor every line above is built from, and it moves only when that ancestor
# is edited, which is exactly the event those lines cannot afford.
FROZEN_DESCRIPTOR = (442, 'c4b724e114772d9940ead6450a4bd64fd490cd1f45ca42092e647284e2fef123')

# What each delta contributes over the version below it. A command that entered
# through the shared ancestor instead leaves every one of these sets unchanged
# while every count above moves, which is how the message below tells the two
# mistakes apart.
CHAIN_ADDITIONS = {
    'purchase-delete-activation-preparation-v1': {'item-receipt history', 'item-receipt post', 'item-receipt query',
                                                  'item-receipt show', 'item-receipt update', 'item-receipt void'},
    'purchase-permission-setup-v1': {'membership effective', 'permission activate', 'permission show'},
    'purchase-deletion-v1': {'card-charge delete', 'check delete'},
    'sales-deletion-v1': {'invoice delete', 'sales-receipt delete'},
    'payment-deletion-v1': {'payment delete'},
    'bill-deletion-v1': {'bill delete'},
    'credit-correction-v1': {'customer-refund update', 'vendor-credit history', 'vendor-credit update'},
    'credit-memo-deletion-v1': {'credit-memo delete'},
    'deposit-deletion-v1': {'deposit delete'},
    'job-time-v1': {'time-activity billing', 'time-activity create', 'time-activity history',
                    'time-activity invoice', 'time-activity query', 'time-activity sales-receipt',
                    'time-activity show', 'time-activity update', 'time-activity void'},
    'journal-deletion-v1': {'journal delete'},
}

_ANCESTOR_EDIT = """
YOU HAVE EDITED THE FROZEN ANCESTOR. If you just added a command, a company action,
a capability or a default to `permission_catalog.py`, that is the mistake, and this
is what it does.

`FROZEN_COMMANDS`, `FROZEN_COMPANY_ACTIONS`, `FROZEN_CAPABILITIES` and
`FROZEN_DEFAULTS` are not the list of commands this build has. They are the single
shared ancestor that EVERY accepted catalog version is built from, each one a
`replace(previous.CATALOG, ...)` over it. Appending one line to them rewrites every
version in the chain, including versions real installations activated months ago and
stored a copy of. Such an installation then fails `catalog_mismatch` on
`permission_snapshot._load_root`, which means every company-scoped command stops --
`invoice query`, `audit list`, `bill query`, all of them -- and `permission activate`
cannot repair it, because it performs the same read before it can move the root
forward. There is no in-product remedy. That has already reached a deployment
rehearsal once.

What to do instead: revert your edit to `permission_catalog.py` and add your commands
in a NEW delta module. Copy `src/bookflow/hub/permission_credit_correction_catalog.py`
-- it is the smallest worked example, about forty lines. Give it its own
`*_POLICY_VERSION` constant in `permission_catalog.py`, add that constant to
`SCOPED_POLICY_VERSIONS`, register the module in
`permission_runtime.known_catalog()` and point `current_catalog()` at it. Then add one
line for your new tip to ACCEPTED in this file, and change no line already there.

`git diff src/bookflow/hub/permission_catalog.py` shows exactly what moved.
"""

_TIP_LINE = """
If instead you have added a legitimate new delta module and this is its version, add
one line to ACCEPTED in this file for it. Do not edit a line that is already there:
each one is a descriptor some root has stored, and changing it locks that root out.
"""


def _ancestor_moved():
    """Whether the shared ancestor itself differs from what the chain was built on."""
    return (len(c.FROZEN_CATALOG.commands),
            c.catalog_manifest(c.FROZEN_CATALOG, c.FROZEN_MANIFEST.standalone_names).descriptor_sha256) != FROZEN_DESCRIPTOR


def _deltas_intact():
    """Whether every delta still adds exactly its own commands over the one below."""
    below = {x.name for x in c.FROZEN_CATALOG.commands}
    for version, added in CHAIN_ADDITIONS.items():
        module = runtime.known_catalog(version)
        if module is None:
            return False
        above = {x.name for x in module.CATALOG.commands}
        if above - below != added or below - above:
            return False
        below = above
    return True


def _diagnosis(version, observed, pinned):
    lines = [
        '',
        'Accepted catalog version %r no longer reproduces the descriptor a root of that' % version,
        'version stored: it now has %d commands and sha %s,' % observed,
        'where the activation stored %d commands and sha %s.' % pinned,
    ]
    if _ancestor_moved():
        moved = len(c.FROZEN_CATALOG.commands) - FROZEN_DESCRIPTOR[0]
        lines.append('')
        lines.append('permission_catalog.FROZEN_CATALOG has %+d commands against the %d the whole'
                     % (moved, FROZEN_DESCRIPTOR[0]))
        lines.append('chain was built from, and %s.'
                     % ('every delta still adds exactly its own commands, so the new ones entered '
                        'through the shared ancestor' if _deltas_intact()
                        else 'a delta has moved as well'))
        lines.append(_ANCESTOR_EDIT)
    else:
        lines.append('')
        lines.append('The frozen ancestor is unchanged, so this version\'s own delta module moved.')
        lines.append('A delta that has been accepted is as frozen as the ancestor is.')
        lines.append(_TIP_LINE)
    return '\n'.join(lines)


@pytest.mark.parametrize('version', list(ACCEPTED))
def test_accepted_version_reproduces_the_descriptor_a_root_stored(version):
    pinned = ACCEPTED[version]
    module = runtime.known_catalog(version)
    assert module is not None, (
        '\npermission_runtime.known_catalog no longer resolves accepted version %r.\n'
        'Every version a root may have stored must stay selectable there: a root whose\n'
        'version is unrecognised falls back to the legacy bundle and fails to load.' % version)
    bundle = module.catalog_bundle()
    assert bundle.descriptor.version == version
    manifest = c.catalog_manifest(bundle.descriptor, bundle.exclusions)
    observed = (len(bundle.descriptor.commands), manifest.descriptor_sha256)
    assert observed == pinned, _diagnosis(version, observed, pinned)
    assert module.MANIFEST.descriptor_sha256 == pinned[1]


@pytest.mark.parametrize('version', list(ACCEPTED))
def test_stored_activation_blob_decodes_at_the_pinned_sha(version):
    # The exact read _load_root performs: the blob a root of this version stored,
    # read back under the sha stored beside it. decode_catalog refuses the pair
    # when the descriptor no longer hashes to that sha, which is how the root
    # locked itself out; reaching a value here means it would not.
    sha = ACCEPTED[version][1]
    stored = s.encode_catalog(runtime.known_catalog(version).CATALOG)
    try:
        decoded = s.decode_catalog(stored, version=version, sha256=sha)
    except s.SnapshotError:
        observed = (len(runtime.known_catalog(version).CATALOG.commands),
                    c.catalog_manifest(runtime.known_catalog(version).CATALOG).descriptor_sha256)
        raise AssertionError(
            'A root activated at %r would fail load_root with catalog_mismatch.%s'
            % (version, _diagnosis(version, observed, ACCEPTED[version]))) from None
    assert decoded.version == version
    assert c.catalog_manifest(decoded).descriptor_sha256 == sha
    with pytest.raises(s.SnapshotError):
        s.decode_catalog(stored, version=version, sha256='0' * 64)


def test_every_selectable_version_is_pinned_and_the_last_line_is_the_tip():
    assert set(c.SCOPED_POLICY_VERSIONS) == set(ACCEPTED), (
        '\nSCOPED_POLICY_VERSIONS and ACCEPTED must name the same versions. A new delta\n'
        'needs its constant in SCOPED_POLICY_VERSIONS (phase semantics), its module in\n'
        'permission_runtime.known_catalog(), and one new line in ACCEPTED here.' + _TIP_LINE)
    tip = list(ACCEPTED)[-1]
    assert runtime.current_catalog() is runtime.known_catalog(tip)
    assert runtime.current_catalog().CATALOG.version == tip
    assert runtime.known_catalog('unrecognized-future') is None


def test_the_product_pin_and_this_one_name_the_same_descriptors():
    """Two pins, in two files, deliberately.

    permission_runtime refuses to serve a version whose descriptor moved, so the
    mistake stops the product rather than only a test; this file fails in CI with the
    explanation of what was done and what to do instead. They are kept apart because a
    builder who hit the import refusal and edited that one constant to get past it
    would otherwise have silenced the whole check -- which is precisely how a digest
    kept beside the descriptor it guards stops working.
    """
    assert runtime.ACCEPTED_DESCRIPTOR_SHA256 == {version: sha for version, (_, sha) in ACCEPTED.items()}
    assert runtime.ACCEPTED_ANCESTOR_SHA256 == FROZEN_DESCRIPTOR[1]


def test_frozen_ancestor_and_the_legacy_bridge_hold_still():
    bundle = runtime.catalog_bundle()
    manifest = c.catalog_manifest(bundle.descriptor, bundle.exclusions)
    observed = (len(c.FROZEN_CATALOG.commands), manifest.descriptor_sha256)
    assert observed == FROZEN_DESCRIPTOR, (
        '\npermission_catalog.FROZEN_CATALOG now holds %d commands and hashes to %s,\n'
        'against the %d commands and %s every accepted version above was built from.%s'
        % (observed + FROZEN_DESCRIPTOR + (_ANCESTOR_EDIT,)))
    assert c.FROZEN_MANIFEST.descriptor_sha256 == FROZEN_DESCRIPTOR[1]


def test_each_delta_adds_exactly_its_own_commands_over_the_version_below():
    below = {x.name for x in c.FROZEN_CATALOG.commands}
    for version, added in CHAIN_ADDITIONS.items():
        above = {x.name for x in runtime.known_catalog(version).CATALOG.commands}
        assert above - below == added, (
            '\nDelta %r adds %s over the version below it, not %s.%s'
            % (version, sorted(above - below), sorted(added),
               _ANCESTOR_EDIT if _ancestor_moved() else _TIP_LINE))
        assert not below - above, (
            '\nDelta %r drops %s that the version below it accepted. A delta may add and\n'
            'may flip an availability the ancestor prepared; it may never remove.'
            % (version, sorted(below - above)))
        below = above


def test_no_delta_command_has_also_been_written_into_the_shared_ancestor():
    # The other half of the same mistake: a command declared in a delta and copied
    # into the ancestor reaches roots that never accepted it, through every version.
    ancestor = {x.name for x in c.FROZEN_CATALOG.commands}
    for version in ACCEPTED:
        module = runtime.known_catalog(version)
        leaked = sorted(ancestor & {x.name for x in getattr(module, 'ADDED_COMMANDS', ())})
        assert not leaked, (
            '\n%s is declared both in delta %r and in permission_catalog.FROZEN_COMMANDS.\n'
            'The ancestor copy reaches every root, including roots that never accepted this\n'
            'version. Delete the lines from FROZEN_COMMANDS; the delta is the only place\n'
            'these belong.%s' % (', '.join(repr(x) for x in leaked), version, _ANCESTOR_EDIT))
