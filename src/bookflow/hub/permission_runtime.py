"""Private current-policy bridge. No legacy activation or public access routing.

Visibility is governed by memberships, not installation administration. Record
and conditional source owners still perform their complete target graph checks.
"""
from collections import OrderedDict
from dataclasses import asdict, fields, replace
import hashlib
import json
import sqlite3
from threading import Lock

from . import permission_catalog as c, permission_policy as a, permission_snapshot as s
from .agent_authority import fail

# The commit at which call-site parity between the code and the inventory was last
# verified by union review. Not this module's own commit, which cannot name itself.
SOURCE_COMMIT = '3990636d2a2d3c683a0e806e6635a4a64c0075b0'


# The accepted descriptor now carries every conditional source at its current call
# sites, so this bridge holds no delta and CURRENT_SOURCES is the frozen inventory
# itself. The seam stays because the next drift between the code and the descriptor
# lands here first, ahead of the catalog transition that absorbs it. Nothing here may
# change a capability, threshold, default, action or availability; existing roots go
# through that separately admitted transition, never automatic adoption.
CURRENT_SOURCES = c.CONDITIONAL_RESOURCE_SOURCES
CURRENT_CATALOG = replace(c.FROZEN_CATALOG, conditional_sources=CURRENT_SOURCES)
CURRENT_MANIFEST = c.catalog_manifest(CURRENT_CATALOG, c.FROZEN_MANIFEST.standalone_names)


# The frozen ancestor, as every version in known_catalog() below was built on it.
# Each of those versions is replace(previous.CATALOG, ...) down a chain that ends at
# permission_catalog.FROZEN_CATALOG, and an activation stores the exact descriptor it
# accepted, so an edit to that ancestor rewrites descriptors installations already
# stored and those installations can no longer load their own permission state --
# every company-scoped command fails catalog_mismatch and `permission activate`
# cannot repair it, because it performs the same read first.
#
# This value lives here, and not beside the literal it checks, deliberately.
# permission_catalog.FROZEN_MANIFEST carries the same digest, but regenerating that
# literal to match is the second half of the mistake: a copy kept in that file would
# be regenerated along with it and would never fire. The sha compared below is
# computed from the ancestor as it stands now, not read from any literal.
ACCEPTED_ANCESTOR_SHA256 = 'c4b724e114772d9940ead6450a4bd64fd490cd1f45ca42092e647284e2fef123'
# CURRENT_CATALOG is the ancestor with CURRENT_SOURCES substituted, so while this
# bridge holds no delta it *is* the ancestor and its manifest, computed just above,
# is the ancestor's. Reuse it rather than hashing the same descriptor twice on every
# import; the day the bridge does hold a delta, the `is` fails and we hash.
_ANCESTOR_SHA256 = (CURRENT_MANIFEST.descriptor_sha256
                    if CURRENT_SOURCES is c.CONDITIONAL_RESOURCE_SOURCES else
                    c.catalog_manifest(c.FROZEN_CATALOG, c.FROZEN_MANIFEST.standalone_names).descriptor_sha256)
if _ANCESTOR_SHA256 != ACCEPTED_ANCESTOR_SHA256:
    raise RuntimeError(
        'The frozen permission catalog ancestor has been edited.\n'
        '\n'
        'permission_catalog.FROZEN_CATALOG now hashes to %s,\n'
        'where every accepted catalog version was built on %s.\n'
        '\n'
        'If you have just added a command, a company action, a capability or a default\n'
        'to FROZEN_COMMANDS or one of its neighbours in permission_catalog.py, that is\n'
        'the edit, and it is why this refuses to start. That ancestor is shared by every\n'
        'version in known_catalog() below, so adding to it rewrites descriptors that real\n'
        'installations activated and stored -- and such an installation then fails\n'
        'catalog_mismatch on every company-scoped command, with no in-product remedy,\n'
        'because `permission activate` performs the same read before it can move the root\n'
        'forward. Refusing to import is cheaper than shipping that.\n'
        '\n'
        'Put your commands in a new delta module instead: copy\n'
        'permission_credit_correction_catalog.py, give it its own *_POLICY_VERSION, add\n'
        'that constant to SCOPED_POLICY_VERSIONS, register the module in known_catalog()\n'
        'below, point current_catalog() at it, and add one line for the new tip to\n'
        'ACCEPTED in tests/test_permission_catalog_history.py.\n'
        '\n'
        '`git diff src/bookflow/hub/permission_catalog.py` shows exactly what moved. If\n'
        'you are deliberately retiring the ancestor, this constant is what you change,\n'
        'and every pinned version in that test moves with it.'
        % (_ANCESTOR_SHA256, ACCEPTED_ANCESTOR_SHA256))


def catalog_bundle() -> s.CatalogBundle:
    """Explicit executable-build bundle; no registry regeneration or root writes."""
    inventory = dict(manifest=asdict(CURRENT_MANIFEST),
                     sources=[asdict(x) for x in CURRENT_SOURCES])
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True,
                                      separators=(',', ':')).encode()).hexdigest()
    return s.CatalogBundle(SOURCE_COMMIT, CURRENT_CATALOG,
                          CURRENT_MANIFEST.standalone_names, digest)


# Every accepted version's descriptor sha256, pinned here rather than in the module
# that builds it, for the reason ACCEPTED_ANCESTOR_SHA256 is pinned here: a digest kept
# beside the descriptor it guards gets regenerated along with the descriptor and never
# fires. Each sha below is compared against the manifest that module computes from its
# own CATALOG at import, so this cannot be satisfied by editing a literal.
#
# A root stores the exact descriptor it activated. An accepted delta is therefore as
# frozen as the ancestor is: the ancestor guard above does not cover these, and until
# this check existed only a test did. Verification runs inside known_catalog(), where
# the chain is imported anyway -- at module scope it would force that import on every
# command and cost roughly half a second, against nothing here.
ACCEPTED_DESCRIPTOR_SHA256 = {
    c.SCOPED_POLICY_VERSION: 'a0823e6d9ae98e96c98e2b6e4e73042ecc8ecb7cf0e7d4d9b50e8febc7ed6979',
    c.SETUP_POLICY_VERSION: '5d71ff2a0970f85e12b6fca0f84ff2736176565db44064b0668f84a3c132e2f2',
    c.DELETE_POLICY_VERSION: 'bdcd036b653a165a65279386fc91a6ac8ead2dc45fa49e7791e943067c989f9e',
    c.SALES_DELETE_POLICY_VERSION: 'cedd4c0ae270c83bcbaf2756124adf8d866694c00a8e05c06733282fcc844943',
    c.PAYMENT_DELETE_POLICY_VERSION: '676fb94480d9d413d0233e3f869a479020b1ee3ce20fc03feef02c96d33b4323',
    c.BILL_DELETE_POLICY_VERSION: '6e0a2035aee9edc6ba907f602d531334eadb81b0176a280d237ff840a0c9d487',
    c.CREDIT_CORRECTION_POLICY_VERSION: '53d98dab0d040498fb0837c4de7cd60e30894436b44a2de25aa41a39c9d518d7',
    c.CREDIT_DELETE_POLICY_VERSION: 'b3818cc259b81e4f7ef277d386cfc703d1cab8bbffb499e3025afec51af9bf5b',
    c.DEPOSIT_DELETE_POLICY_VERSION: '449e98a8fa02bb9b5e3b805e70acbb70c0438df79b857a1e804526c2bf40c750',
    c.JOB_TIME_POLICY_VERSION: '0072e030fb0f0b63b11f41c99facfa400a127148080e1fa3a60cbeffa53d0d07',
    c.JOURNAL_DELETE_POLICY_VERSION: '8bb60e36da0155cf3551b01e83e7ded46ee06fce8c2962876a69da66636c0b95',
    c.REFUND_HISTORY_POLICY_VERSION: '2cde0560b88c952260a309e5e2b22c242e0202a766ddfc6677b483ee1426c983',
    c.AGENT_ADMIN_POLICY_VERSION: 'c15675d00ce1b01aa628eaa2424ee0d8e402957c5eb669da5f9f0384d55add25',
    c.EVERYDAY_REPORTS_POLICY_VERSION: '4159308ce66339b8414fe62261e8510b460ce11be04ad5bf99c335cdf7836255',
    c.CARD_CREDIT_POLICY_VERSION: 'b6a4609483758c7f1f4c876793aca985772510543741f6e7a0bd13130eb2b8c7',
    c.BACKUP_POLICY_VERSION: '75c05cd2dc074a26bd218ab7e48d12c09a5dbeadefb46de0475ca4bb1028f070',
    c.DEACTIVATION_POLICY_VERSION: '51f5128ce50d04fd36fdc9000c3879e404d02a862009906b121a590c1c85ced7',
    c.REPORT_EXPORT_POLICY_VERSION: '585047459e889e7b48af181307386d6eb476454fd3de624b04c4916455652611',
    c.AUDIT_VISIBILITY_POLICY_VERSION: '1a6e263186f929c26d8749f6ba1b6c1ec16706287b9db0b09affeb6442789433',
    c.STATEMENT_IMPORT_POLICY_VERSION: '43eb5b56cc903f203974cb0ddb0a5fa3958dcca1ed05e8029ae2b254fc4d46e3',
    c.BACKUP_SCHEDULE_POLICY_VERSION: '43582adbc2b0fb3341c2af31766c7740a1d8f7c6ea495a79f5675485b4f477b7',
    c.ENTRY_REVIEW_POLICY_VERSION: '50fd1552c34eb3ab3d6e73d129491a00bbff1efd2f721a1b94c37003b719a7c0',
    c.CUTOVER_POLICY_VERSION: 'fbea65dcc4951605c67b4bc10692006c48f7f92b5ef0f161071d8933b48fb12c',
    c.UNCATEGORIZED_POLICY_VERSION: 'dced5fb01946a8346701896d5c7e41def95b64a22d211f59adba53111828a03f',
    c.HUMAN_ADMIN_POLICY_VERSION: '4299f7604a39a74f0e9310703eecf2014f4ec7f75ba7cbe8973d5729172727a4',
    c.SALES_TAX_ADJUSTMENT_POLICY_VERSION: '03d9f30190f30e741521e98ac6044a6962e29d26f2a3be25f54f338c8174486f',
    c.RESTORE_POLICY_VERSION: '0a94a8ee9ee2f15db64858e594ea131f2afeef36cb29f11e9509d84e19fa584c',
    c.PARTY_MERGE_POLICY_VERSION: 'f42ffc267bd8651808375d39dfbfb7c2fa2613e5889151cd68dc6609b2174398',
    c.BOUNCED_CHECK_POLICY_VERSION: '0b6616e4040e1986f3b010f4f2d8c92276ba12d741c28b474539755fa65ed68e',
    c.CUTOVER_REST_POLICY_VERSION: 'eec55b86544f824349e62bbdeb9c20a63c6873c4e6073f97ade1a3b0ebe9d084',
    c.ATTACHMENT_TEXT_POLICY_VERSION: 'b482f8ec802d32fa21f3793759791333dd0aefce88dc5d9dabc5d6a79743a284',
    c.TRANSACTION_FIND_POLICY_VERSION: 'aec5cc4879ba82b57cf1d22791abc6d1f6f01477724ed521efad854045cb8531',
    c.ITEM_CONVERT_POLICY_VERSION: 'baa8f52bccb7c70240df1fc3a0117d0b5d9b7555ee2c57abf5a05bbe8cce52e1',
    c.FINANCE_CHARGE_POLICY_VERSION: 'ec3bf5ce4476e78dcdd55e3f4b24a8c06c76c759e1e045e803107292e4cc4160',
    c.VENDOR_REFUND_POLICY_VERSION: '150e3626256eef7e05ebe0ca2a602a90d1646ca6ac5a9400625ce9b150f6df7d',
    c.BUDGET_POLICY_VERSION: 'b635577d72c1463d55aa938880609d4fabf2ed5ec416f6ca98201fec91725615',
    c.CUSTOMER_PREPAYMENT_POLICY_VERSION: '24bbfec464495318237c0b6d2e81538fdd267344af67d2698efbbe12302ff1f2',
    c.LIST_MERGE_POLICY_VERSION: '061fdabeacdb8b45e62d3d50554cb717fef704c75d8f5366bda50423e2045a4b',
    c.SALES_ORDER_POLICY_VERSION: '2d5b06b100b2f2c07809b723578b8b769f8d0dc9b2db3a18fc94014271f1a481',
    c.FIXED_ASSET_POLICY_VERSION: 'ba2c43a29b9d4a3c5966c054b82f103ca93fa5cf3c5a297f2126244106ff6b94',
    c.ASSEMBLY_BUILD_POLICY_VERSION: '980e4483d64de9d252b152dd4d0b51e40c7c68735227c00a59c9ce09251bd10e',
    c.COMMITTED_STOCK_POLICY_VERSION: '7f632304aab96f6f20d91b08ee6cc8c34f77d3bb3066935390b155c0bf4bdba8',
    c.MILEAGE_POLICY_VERSION: '357335f1d1d8fff71c0e0029deff5bea7be9186b9cfcd556d326c5a4b4898770',
    c.COLLECTIONS_POLICY_VERSION: 'a4a42a893faf487946db8c4536c8e470af9b2d398d7fff6aa6237e1eaf89d380',
    c.PRICING_RULES_POLICY_VERSION: '2223cf7fa5490ca8b42c102b2c41ae2c7c12a5c031c146c254a9856d7e105651',
    c.ORDER_INVOICING_POLICY_VERSION: '7452c4b9233d80fe00ac1d14aecb164e94c82035cebbc682c4e61171d1b1cc14',
    c.SHORTFALL_PO_POLICY_VERSION: 'ce572b26ff407657374ea07f8bc8833a4f2e19225f4d5fd52e956917b886dc51',
    c.PARTIAL_PO_POLICY_VERSION: '0d545843f9a3705ce88d87928760dcceb645632178e41e9a4a4ccc5539632eb1',
    c.TIMESHEETS_POLICY_VERSION: '6ae1c2d2ca67063b62f849f5130ce721beded5ac7ac41977e406d799eb3b10a5',
    c.INVENTORY_SITES_POLICY_VERSION: 'f5445ed06cfd12459d81305a84717f78481ee7679c348910567a092d28801266',
    c.ACCOUNTANT_REVIEW_POLICY_VERSION: 'fdb82ff38e630d7667ff5570da3dfbda017bfeb7a705bcb6a190f5b77ac0cf2a',
    c.CHECK_PRINTING_POLICY_VERSION: 'ea6368e6f2957858e0013e2c53453b4b70981395cf0c1f316224bf55e7d6abed',
    c.BARCODES_POLICY_VERSION: 'cc3a5ccc83813d3705e9e55753bbb1e1a52b983755fa0c9ced8305c84a4f5ccd',
    c.INVENTORY_COUNTS_POLICY_VERSION: 'cbf2b5f35092aa7691fde5b0abb64a0f7c72ea55cf02820051a2649d44d639f5',
    c.SCHEDULING_POLICY_VERSION: '445b102fcbce9835b1edceafe75f8d5dc573d18f03c0db73c83085ad1ceb7568',
    c.PERFORMANCE_POLICY_VERSION: '4e60dbb727be31efe9804b77a51cf36648ec37efde2dae1b6e323739504ea6a4',
    c.SERIAL_LOT_POLICY_VERSION: '4b340c8c2715584586326a1ee9698cc097af5993ab61cf68b25644a859768d03',
    c.BINS_POLICY_VERSION: '5b61fdbac14da296bfe5c87ba003b4acaf6fbec991d31b1675acb87a66209b91',
    c.LANDED_COST_POLICY_VERSION: '1031c961c6bd10b761157ec1c255240324aaa2237f06f96f168bf26b7c4f5350',
    c.FIFO_POLICY_VERSION: '724f7e0e052f9255d45480af689cf2861870cac19a18c71783918af73b04bc88',
    c.VERIFY_POLICY_VERSION: '1a850789e63fe9d8b8d995b67098d1eebe026405bea56e31a79d06ce7a487c96',
    c.AGENT_SETUP_POLICY_VERSION: '5102370f0ee860388d7452f9d004c51789a95c0ed5a93cefa19a38650e475162',
    c.SHIPMENTS_POLICY_VERSION: '9fa6ebf4efaf4a043bf04870d0b7f5dbf30e97f655bf889b631247c43be9c284',
    c.SETUP_TRIAL_POLICY_VERSION: '4e16e82de135a51dd6da3e05b717f128851ddb97e9001b35b3946db7379830de',
    c.SHIPMENT_RETURNS_POLICY_VERSION: 'f2c9af3e35a8f1708cf051173e262624f264d266576a028990579e07d4323883',
    c.PICKING_POLICY_VERSION: '34c39c80a09a3d5ef8b10808cb187830a236f340946a45cbc9e2b9ca817e71af',
    c.DELIVERY_POLICY_VERSION: '085b7a93194e439557234ed8d88a7d7bdfd39ed06f8b2cbdddb732daca07561b',
    c.PACKING_POLICY_VERSION: '10872774b49e7497c34eccde2882c74e49c36ead5d7d2ff765399dd31fae79c8',
    c.DELIVERY_QUEUE_POLICY_VERSION: '2e19e20f01b2cdc3110be9f381a9ec54fa4f7f0ba5ef6880b49189576076554c',
    c.IMPORT_POLICY_VERSION: '0db21ee9713630abf0d4a5d81c282ee608483cd6f16b8a2aa40e3a14712dfe4a',
    c.WORKSHEET_POLICY_VERSION: 'c59d5c63f042353fd78d4e565561f9d0f4560ad8420d56b412915b90f23a4cf4',
    c.DELIVERY_BATCH_POLICY_VERSION: 'e7d68fa7de0173bbe842091419c3deed98561bc4e07c92cf981e4759e59756b5',
    c.IMPORT_MAPPING_POLICY_VERSION: '66a2d6a6c90f23b6f547ec27aa823a1440b892c22a7bd0b50ce8c142c5dc7b5f',
    c.DELIVERY_SCHEDULE_POLICY_VERSION: '66aed81b62dd65713f27e55f39cea8ba2aa393d8584b08013a4a0298ec50dd59',
    c.BANK_FEED_POLICY_VERSION: '37f3a79e1ad520b97153c7863f0d3111c821e75bd944b0193c727ff94b3e9a29',
    c.IMPORT_DOCUMENTS_POLICY_VERSION: '6418e208309e3f01e38b4749e62895dad640e66deada78c513ed3bf7127cf13a',
    c.BANK_FEED_REVERSE_POLICY_VERSION: 'df70eed353fe2243e74c722b39a0e7a9b12f98a2e3bd1a9c03bfc382325328c6',
    c.BANK_LINE_POLICY_VERSION: 'bd11005cfa5a7630090a663bf20f7dd2bd8d7c4ee4a764cd7295a40e1f4f61e9',
    c.CAPABILITIES_POLICY_VERSION: 'f639c12bed3999fc17973aa0698198f2af7dc1c111932d1af9c4ac3e58e99c99',
    c.ITEM_PICTURE_POLICY_VERSION: 'fb1968ec34b88620255e739d05573fb009d3ac47036745294fd0a6f9c77ac8af',
    c.BULK_EDIT_POLICY_VERSION: 'c4664e87ef03c124012304d580259f65920644ed03c3b0c2cb535f8060e7fe6b',
    c.BANK_LINE_DETAILS_POLICY_VERSION: 'c84a45585ddc7d4b5e7e1ce602517bfd25f3c76de7bba387ccc0064f8045f6e5',
    c.USER_HOME_POLICY_VERSION: 'e3ca5ecf2483bcf9780cdd3e9300c5a9cb9ac74f02d2a9f29175bfe8f0c98b36',
    c.AUTOMATION_POLICY_VERSION: '4df1e05f0db60c9ac166ce314398120e3065dfb07f205327c81f3d5e5348e7ed',
    c.OPENING_DRAFT_POLICY_VERSION: '56a757a3260e92ca76ffafbf79f7cbcac99198a1c292278998588eef117d9aa2',
    c.POS_POLICY_VERSION: 'fbe539d7b76d6f661a16e1ef2cbb297e6cb534096d3bd13207586ad3c2283e88',
}
_VERIFIED_ACCEPTED = False


def _verify_accepted(known):
    """Refuse to serve any version whose stored descriptor this build no longer makes."""
    global _VERIFIED_ACCEPTED
    if _VERIFIED_ACCEPTED:
        return
    moved = sorted(version for version, module in known.items()
                   if version in ACCEPTED_DESCRIPTOR_SHA256
                   and module.MANIFEST.descriptor_sha256 != ACCEPTED_DESCRIPTOR_SHA256[version])
    unpinned = sorted(set(known) - set(ACCEPTED_DESCRIPTOR_SHA256))
    if moved:
        raise RuntimeError(
            'An accepted permission catalog version has been edited: %s.\n'
            '\n'
            'A root stores the exact descriptor it activated, and every later read rebuilds\n'
            'that descriptor from the module named here for the stored version. Changing an\n'
            'accepted delta therefore locks out every installation that activated it: each\n'
            'one fails catalog_mismatch on permission_snapshot._load_root, every\n'
            'company-scoped command stops, and `permission activate` cannot repair it\n'
            'because it performs the same read before it can move the root forward. An\n'
            'accepted delta is as frozen as permission_catalog.FROZEN_CATALOG is.\n'
            '\n'
            'If you are adding commands, they belong in a NEW delta on top -- copy\n'
            'permission_credit_correction_catalog.py, give it its own *_POLICY_VERSION, add\n'
            'that constant to SCOPED_POLICY_VERSIONS, register it in known_catalog() below,\n'
            'point current_catalog() at it, and pin it in ACCEPTED_DESCRIPTOR_SHA256 above\n'
            'and in ACCEPTED in tests/test_permission_catalog_history.py. Revert whatever\n'
            'changed in the module(s) named above.'
            % ', '.join(moved))
    if unpinned:
        raise RuntimeError(
            'Catalog version %s is selectable but has no pinned descriptor.\n'
            '\n'
            'Every version known_catalog() can hand back is a descriptor some root may have\n'
            'stored, so each one needs its sha256 in ACCEPTED_DESCRIPTOR_SHA256 above and a\n'
            'line in ACCEPTED in tests/test_permission_catalog_history.py. Add both; do not\n'
            'change a line that is already in either.'
            % ', '.join(unpinned))
    _VERIFIED_ACCEPTED = True


class _MembershipVisibility:
    def facts(self, root, union_scopes, union_subjects):
        # Reuse snapshot's exact raw/logical classifier (including both native
        # separators). It also checks that the observation covers the full root.
        observation = s._observe(root, union_scopes, union_subjects)
        live = {x.scope for x in observation.scopes if x.logical_present}
        users = {x.id: x for x in root.users}
        companies = dict(observation.live_companies)
        memberships = {(x.user_id, x.scope_type, x.scope_id)
                       for x in root.memberships if x.revoked_at is None}
        rows = []
        for who in union_subjects:
            user = users.get(who)
            for scope in union_scopes:
                visible = False
                if user is not None and user.active and scope in live:
                    if scope.kind == 'hub':
                        # Hub identity domain, not company-book access. A keeps
                        # separate human/admin action requirements in this domain.
                        visible = True
                    elif scope.kind == 'company':
                        visible = ((who, 'company', scope.id) in memberships or
                                   (who, 'organization', companies[scope.id]) in memberships)
                    elif scope.kind == 'future_company':
                        visible = (who, 'organization', scope.id) in memberships
                    else:
                        # Parent discovery through an exact company membership
                        # never creates an organization membership or role.
                        visible = ((who, 'organization', scope.id) in memberships or
                                   any(parent == scope.id and (who, 'company', child) in memberships
                                       for child, parent in companies.items()))
                rows.append(a.Visibility(who, scope, visible))
        return s.VisibilityFacts('explicit-membership-retirement-v1', tuple(rows))


VISIBILITY = _MembershipVisibility()


def _full_known():
    """Import and verify every accepted version: the full check (see known_catalog)."""
    from . import permission_activation_catalog, permission_setup_catalog
    from . import permission_deletion_catalog, permission_sales_deletion_catalog
    from . import permission_payment_deletion_catalog, permission_bill_deletion_catalog
    from . import permission_credit_correction_catalog
    from . import permission_credit_deletion_catalog, permission_deposit_deletion_catalog
    from . import permission_job_time_catalog
    from . import permission_journal_deletion_catalog, permission_refund_history_catalog
    from . import permission_agent_catalog, permission_everyday_reports_catalog, permission_card_credit_catalog
    from . import permission_backup_catalog
    from . import permission_deactivation_catalog, permission_report_export_catalog
    from . import permission_audit_visibility_catalog, permission_statement_import_catalog
    from . import permission_backup_schedule_catalog
    from . import permission_entry_review_catalog
    from . import permission_cutover_catalog
    from . import permission_uncategorized_catalog
    from . import permission_human_admin_catalog
    from . import permission_sales_tax_adjustment_catalog
    from . import permission_restore_catalog
    from . import permission_party_merge_catalog
    from . import permission_bounced_check_catalog
    from . import permission_cutover_rest_catalog
    from . import permission_attachment_text_catalog
    from . import permission_transaction_find_catalog
    from . import permission_item_convert_catalog
    from . import permission_finance_charge_catalog
    from . import permission_vendor_refund_catalog
    from . import permission_budget_catalog
    from . import permission_customer_prepayment_catalog
    from . import permission_list_merge_catalog
    from . import permission_sales_order_catalog
    from . import permission_fixed_asset_catalog
    from . import permission_assembly_build_catalog
    from . import permission_committed_stock_catalog
    from . import permission_mileage_catalog
    from . import permission_collections_catalog
    from . import permission_pricing_rules_catalog
    from . import permission_order_invoicing_catalog
    from . import permission_shortfall_po_catalog
    from . import permission_partial_po_catalog
    from . import permission_timesheets_catalog
    from . import permission_inventory_sites_catalog
    from . import permission_accountant_review_catalog
    from . import permission_check_printing_catalog
    from . import permission_barcodes_catalog
    from . import permission_inventory_counts_catalog
    from . import permission_scheduling_catalog
    from . import permission_performance_catalog
    from . import permission_serial_lot_catalog
    from . import permission_bins_catalog
    from . import permission_landed_cost_catalog
    from . import permission_fifo_catalog
    from . import permission_verify_catalog
    from . import permission_agent_setup_catalog
    from . import permission_shipments_catalog
    from . import permission_setup_trial_catalog
    from . import permission_shipment_returns_catalog
    from . import permission_picking_catalog
    from . import permission_delivery_catalog
    from . import permission_packing_catalog
    from . import permission_delivery_queue_catalog
    from . import permission_import_catalog
    from . import permission_worksheet_catalog
    from . import permission_delivery_batch_catalog
    from . import permission_import_mapping_catalog
    from . import permission_delivery_schedule_catalog
    from . import permission_bank_feed_catalog
    from . import permission_import_documents_catalog
    from . import permission_bank_feed_reverse_catalog
    from . import permission_bank_line_catalog
    from . import permission_capabilities_catalog
    from . import permission_item_picture_catalog
    from . import permission_bulk_edit_catalog
    from . import permission_bank_line_details_catalog
    from . import permission_user_home_catalog
    from . import permission_automation_catalog
    from . import permission_opening_draft_catalog
    from . import permission_pos_catalog
    known = {
        c.SCOPED_POLICY_VERSION: permission_activation_catalog,
        c.SETUP_POLICY_VERSION: permission_setup_catalog,
        c.DELETE_POLICY_VERSION: permission_deletion_catalog,
        c.SALES_DELETE_POLICY_VERSION: permission_sales_deletion_catalog,
        c.PAYMENT_DELETE_POLICY_VERSION: permission_payment_deletion_catalog,
        c.BILL_DELETE_POLICY_VERSION: permission_bill_deletion_catalog,
        c.CREDIT_CORRECTION_POLICY_VERSION: permission_credit_correction_catalog,
        c.CREDIT_DELETE_POLICY_VERSION: permission_credit_deletion_catalog,
        c.DEPOSIT_DELETE_POLICY_VERSION: permission_deposit_deletion_catalog,
        c.JOB_TIME_POLICY_VERSION: permission_job_time_catalog,
        c.JOURNAL_DELETE_POLICY_VERSION: permission_journal_deletion_catalog,
        c.REFUND_HISTORY_POLICY_VERSION: permission_refund_history_catalog,
        c.AGENT_ADMIN_POLICY_VERSION: permission_agent_catalog,
        c.EVERYDAY_REPORTS_POLICY_VERSION: permission_everyday_reports_catalog,
        c.CARD_CREDIT_POLICY_VERSION: permission_card_credit_catalog,
        c.BACKUP_POLICY_VERSION: permission_backup_catalog,
        c.DEACTIVATION_POLICY_VERSION: permission_deactivation_catalog,
        c.REPORT_EXPORT_POLICY_VERSION: permission_report_export_catalog,
        c.AUDIT_VISIBILITY_POLICY_VERSION: permission_audit_visibility_catalog,
        c.STATEMENT_IMPORT_POLICY_VERSION: permission_statement_import_catalog,
        c.BACKUP_SCHEDULE_POLICY_VERSION: permission_backup_schedule_catalog,
        c.ENTRY_REVIEW_POLICY_VERSION: permission_entry_review_catalog,
        c.CUTOVER_POLICY_VERSION: permission_cutover_catalog,
        c.UNCATEGORIZED_POLICY_VERSION: permission_uncategorized_catalog,
        c.HUMAN_ADMIN_POLICY_VERSION: permission_human_admin_catalog,
        c.SALES_TAX_ADJUSTMENT_POLICY_VERSION: permission_sales_tax_adjustment_catalog,
        c.RESTORE_POLICY_VERSION: permission_restore_catalog,
        c.PARTY_MERGE_POLICY_VERSION: permission_party_merge_catalog,
        c.BOUNCED_CHECK_POLICY_VERSION: permission_bounced_check_catalog,
        c.CUTOVER_REST_POLICY_VERSION: permission_cutover_rest_catalog,
        c.ATTACHMENT_TEXT_POLICY_VERSION: permission_attachment_text_catalog,
        c.TRANSACTION_FIND_POLICY_VERSION: permission_transaction_find_catalog,
        c.ITEM_CONVERT_POLICY_VERSION: permission_item_convert_catalog,
        c.FINANCE_CHARGE_POLICY_VERSION: permission_finance_charge_catalog,
        c.VENDOR_REFUND_POLICY_VERSION: permission_vendor_refund_catalog,
        c.BUDGET_POLICY_VERSION: permission_budget_catalog,
        c.CUSTOMER_PREPAYMENT_POLICY_VERSION: permission_customer_prepayment_catalog,
        c.LIST_MERGE_POLICY_VERSION: permission_list_merge_catalog,
        c.SALES_ORDER_POLICY_VERSION: permission_sales_order_catalog,
        c.FIXED_ASSET_POLICY_VERSION: permission_fixed_asset_catalog,
        c.ASSEMBLY_BUILD_POLICY_VERSION: permission_assembly_build_catalog,
        c.COMMITTED_STOCK_POLICY_VERSION: permission_committed_stock_catalog,
        c.MILEAGE_POLICY_VERSION: permission_mileage_catalog,
        c.COLLECTIONS_POLICY_VERSION: permission_collections_catalog,
        c.PRICING_RULES_POLICY_VERSION: permission_pricing_rules_catalog,
        c.ORDER_INVOICING_POLICY_VERSION: permission_order_invoicing_catalog,
        c.SHORTFALL_PO_POLICY_VERSION: permission_shortfall_po_catalog,
        c.PARTIAL_PO_POLICY_VERSION: permission_partial_po_catalog,
        c.TIMESHEETS_POLICY_VERSION: permission_timesheets_catalog,
        c.INVENTORY_SITES_POLICY_VERSION: permission_inventory_sites_catalog,
        c.ACCOUNTANT_REVIEW_POLICY_VERSION: permission_accountant_review_catalog,
        c.CHECK_PRINTING_POLICY_VERSION: permission_check_printing_catalog,
        c.BARCODES_POLICY_VERSION: permission_barcodes_catalog,
        c.INVENTORY_COUNTS_POLICY_VERSION: permission_inventory_counts_catalog,
        c.SCHEDULING_POLICY_VERSION: permission_scheduling_catalog,
        c.PERFORMANCE_POLICY_VERSION: permission_performance_catalog,
        c.SERIAL_LOT_POLICY_VERSION: permission_serial_lot_catalog,
        c.BINS_POLICY_VERSION: permission_bins_catalog,
        c.LANDED_COST_POLICY_VERSION: permission_landed_cost_catalog,
        c.FIFO_POLICY_VERSION: permission_fifo_catalog,
        c.VERIFY_POLICY_VERSION: permission_verify_catalog,
        c.AGENT_SETUP_POLICY_VERSION: permission_agent_setup_catalog,
        c.SHIPMENTS_POLICY_VERSION: permission_shipments_catalog,
        c.SETUP_TRIAL_POLICY_VERSION: permission_setup_trial_catalog,
        c.SHIPMENT_RETURNS_POLICY_VERSION: permission_shipment_returns_catalog,
        c.PICKING_POLICY_VERSION: permission_picking_catalog,
        c.DELIVERY_POLICY_VERSION: permission_delivery_catalog,
        c.PACKING_POLICY_VERSION: permission_packing_catalog,
        c.DELIVERY_QUEUE_POLICY_VERSION: permission_delivery_queue_catalog,
        c.IMPORT_POLICY_VERSION: permission_import_catalog,
        c.WORKSHEET_POLICY_VERSION: permission_worksheet_catalog,
        c.DELIVERY_BATCH_POLICY_VERSION: permission_delivery_batch_catalog,
        c.IMPORT_MAPPING_POLICY_VERSION: permission_import_mapping_catalog,
        c.DELIVERY_SCHEDULE_POLICY_VERSION: permission_delivery_schedule_catalog,
        c.BANK_FEED_POLICY_VERSION: permission_bank_feed_catalog,
        c.IMPORT_DOCUMENTS_POLICY_VERSION: permission_import_documents_catalog,
        c.BANK_FEED_REVERSE_POLICY_VERSION: permission_bank_feed_reverse_catalog,
        c.BANK_LINE_POLICY_VERSION: permission_bank_line_catalog,
        c.CAPABILITIES_POLICY_VERSION: permission_capabilities_catalog,
        c.ITEM_PICTURE_POLICY_VERSION: permission_item_picture_catalog,
        c.BULK_EDIT_POLICY_VERSION: permission_bulk_edit_catalog,
        c.BANK_LINE_DETAILS_POLICY_VERSION: permission_bank_line_details_catalog,
        c.USER_HOME_POLICY_VERSION: permission_user_home_catalog,
        c.AUTOMATION_POLICY_VERSION: permission_automation_catalog,
        c.OPENING_DRAFT_POLICY_VERSION: permission_opening_draft_catalog,
        c.POS_POLICY_VERSION: permission_pos_catalog,
    }
    # Each module hashed its own descriptor when it was imported just above, so this is
    # a handful of string comparisons, once per process, and no descriptor is hashed for
    # it. See ACCEPTED_DESCRIPTOR_SHA256 for why it is not done at module scope.
    _verify_accepted(known)
    return known


class _CachedLayer:
    """One accepted version read back from the sealed cache: what known_catalog's callers use
    of a layer module (CATALOG, MANIFEST, SOURCE_COMMIT, catalog_bundle()), nothing more."""
    __slots__ = ('CATALOG', 'MANIFEST', 'SOURCE_COMMIT', '_bundle')

    def __init__(self, catalog, manifest, source_commit, bundle):
        self.CATALOG, self.MANIFEST, self.SOURCE_COMMIT, self._bundle = catalog, manifest, source_commit, bundle

    def catalog_bundle(self):
        return self._bundle


class _CachedChain:
    """The verified chain as a sealed file recorded it. Each version is decoded on first use
    and must still validate and hash to its pin; any failure is a fallback, never a pass."""
    def __init__(self, sealed):
        self._sealed, self._layers = sealed, {}

    def get(self, version):
        if version in self._layers:
            return self._layers[version]
        if version not in self._sealed.index:
            return None
        with _known_lock:
            if version not in self._layers:
                self._layers[version] = self._decode(version)
            return self._layers[version]

    def _decode(self, version):
        entry = self._sealed.index[version]
        catalog = s._decode(s._parse(self._sealed.blob(version), 'catalog'), c.Catalog, 'catalog')
        standalone = s._decode(entry['standalone'], tuple[str, ...], 'standalone')
        manifest = c.catalog_manifest(catalog, standalone)
        exclusions = s._decode(entry['bundle_exclusions'], tuple[str, ...], 'exclusions')
        if (catalog.version != version or manifest.descriptor_sha256 != ACCEPTED_DESCRIPTOR_SHA256.get(version)
                or manifest.standalone_names != standalone or type(entry['source_commit']) is not str
                or type(entry['bundle_source_commit']) is not str or type(entry['bundle_digest']) is not str):
            raise ValueError('cached version %r does not reproduce its pin' % version)
        bundle = s.CatalogBundle(entry['bundle_source_commit'], catalog, exclusions, entry['bundle_digest'])
        return _CachedLayer(catalog, manifest, entry['source_commit'], bundle)


_known = None  # dict of verified modules, or a _CachedChain; decided once per process
_known_lock = Lock()


def _cache_key():
    from . import permission_catalog_cache as cache
    return cache.compute_key(ACCEPTED_DESCRIPTOR_SHA256, ACCEPTED_ANCESTOR_SHA256)


def _json_fragment(value, memo):
    """JSON for a descriptor value, as asdict() would give it. The layers share most of their
    descriptor objects, so each one is encoded once (keyed by identity while all are alive)."""
    kind = type(value)
    if kind is tuple or kind in _DESCRIPTOR_FIELDS:
        found = memo.get(id(value))
        if found is not None and found[0] is value:
            return found[1]
        if kind is tuple:
            text = '[' + ','.join(_json_fragment(x, memo) for x in value) + ']'
        else:
            text = '{' + ','.join(json.dumps(name) + ':' + _json_fragment(getattr(value, name), memo)
                                  for name in _DESCRIPTOR_FIELDS[kind]) + '}'
        memo[id(value)] = (value, text)
        return text
    if kind in (str, int, bool, type(None)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    raise TypeError('not a descriptor value')


_DESCRIPTOR_FIELDS = {kind: tuple(sorted(f.name for f in fields(kind))) for kind in (
    c.Catalog, c.CommandDescriptor, c.CapabilitySpec, c.DefaultEntry, c.CompanyAction,
    c.AdminAction, c.ResourceSource, c.Requirement)}


def _cache_entries(known):
    entries, memo = {}, {}
    for version, module in known.items():
        bundle = module.catalog_bundle()
        if bundle.descriptor != module.CATALOG:
            return None  # a layer whose bundle is not its CATALOG: never cached
        entries[version] = dict(
            catalog=_json_fragment(module.CATALOG, memo),
            meta=dict(standalone=list(module.MANIFEST.standalone_names), source_commit=module.SOURCE_COMMIT,
                      bundle_source_commit=bundle.source_commit, bundle_exclusions=list(bundle.exclusions),
                      bundle_digest=bundle.source_inventory_digest))
    return entries


def verify_full():
    """Run the full check in this process, whatever the cache holds, and refresh the cache.

    `permission show` and `permission activate` call this: they are the self-check."""
    global _known
    with _known_lock:
        known = _full_known()
        fresh, _known = type(_known) is not dict, known
    if fresh:
        _write_cache(known)  # once per process: a warm server's self-checks never rewrite it
    return known


def _write_cache(known):
    from . import permission_catalog_cache as cache
    if cache.cache_dir() is None:
        return
    try:
        entries = _cache_entries(known)
        if entries is not None:
            cache.store(_cache_key(), entries)
    except Exception:
        pass  # the cache is an optimisation; a failed write costs the next process time only


def _chain():
    global _known
    known = _known
    if known is not None:
        return known
    from . import permission_catalog_cache as cache
    with _known_lock:
        if _known is None and cache.cache_dir() is not None and not cache.forced_full():
            try:
                sealed = cache.load(_cache_key())
            except Exception:
                sealed = None
            if sealed is not None and set(sealed.index) == set(ACCEPTED_DESCRIPTOR_SHA256):
                _known = _CachedChain(sealed)
        if _known is not None:
            return _known
    return verify_full()


def known_catalog(version):
    """Accepted explicit policy versions; never substitute the current build.

    The first call in a process either verifies the whole chain (_full_known) or, when this
    exact code version already did so and sealed the result under the data root, reads that
    back (permission_catalog_cache). Any doubt about the sealed copy means the full check.
    """
    known = _chain()
    if type(known) is dict:
        return _full_known().get(version)
    try:
        return known.get(version)
    except Exception:
        return verify_full().get(version)


def current_catalog():
    """Executable descriptor owner; this accessor does not activate a root."""
    return known_catalog(c.POS_POLICY_VERSION)


def catalog_for_root(tx):
    """Select only a recognized explicitly stored build; legacy stays frozen."""
    state = tx.raw.execute('SELECT mode,catalog_version FROM main.permission_state WHERE id=1').fetchone()
    selected = known_catalog(state[1]) if state[0] == 'policy_v1' else None
    return selected.catalog_bundle() if selected is not None else catalog_bundle()


def observe_current(tx) -> s.ObservedPair:
    """Fresh complete observation of this root against itself, on the caller's transaction.

    This is a read, not a policy transition: it needs the catalog in force, not a
    stored activation copy of it. A root that has never been activated has no
    such copy and there is no command that would make one, so demanding
    policy_v1 here would leave every install unable to observe its own
    memberships and so unable to read its own audit trail. Administration keeps
    the strict requirement through assemble_pair.
    """
    bundle = catalog_for_root(tx)
    try:
        root = s.load_root(tx, catalog=bundle)
        return s.observe_pair(root, root, old_catalog=bundle, new_catalog=bundle,
                              visibility=VISIBILITY, activated=False)
    except s.SnapshotError as exc:
        from .identity_admin import _translate_snapshot
        _translate_snapshot(exc)


# Cross-transaction reuse (R74). A complete observation is a pure function of the
# permission input tables, the schema and this build's catalog code. Database triggers
# (hub0014) redraw authority_generation.token in the same transaction as any write to
# those tables, from any connection or process. So an observation built from a
# transaction that read token T describes exactly the facts of every later transaction
# that reads T again: the check is one SELECT on the caller's own transaction, made
# fresh for every use, and nothing reused ever outlives a token change. Credential,
# token expiry, binding and publication checks stay outside this cache.
AUTHORITY_TABLES = ('users', 'organizations', 'companies', 'memberships', 'agent_principals',
                    'agent_authority', 'role_capabilities', 'permission_state')
AUTHORITY_TRIGGERS = {
    f'authority_generation_{table}_{event.lower()}': (table,
        f'CREATE TRIGGER authority_generation_{table}_{event.lower()} AFTER {event} ON {table} '
        'BEGIN UPDATE authority_generation SET generation = generation + 1, '
        'token = lower(hex(randomblob(16))) WHERE id = 1; END')
    for table in AUTHORITY_TABLES for event in ('INSERT', 'UPDATE', 'DELETE')}
_STAMP_SQL = ('SELECT g.token, g.generation, (SELECT schema_version FROM pragma_schema_version), '
              "(SELECT group_concat(version_num, ',') FROM main.alembic_version) "
              'FROM main.authority_generation AS g WHERE g.id = 1')
_REUSE_LIMIT = 8
_reuse = OrderedDict()  # key -> (observation, {decision key: ExecutionResult})
_reuse_lock = Lock()


def authority_stamp(tx):
    """The authority generation as the caller's open transaction sees it; None when unprovable.

    The schema version is part of it because DDL can drop a trigger; the alembic head
    because the observation depends on it. Outside a transaction two statements can see
    two different commits, so there is nothing to compare.
    """
    if getattr(tx, '_closed', True) or not tx.raw.in_transaction:
        return None
    try:
        row = tx.raw.execute(_STAMP_SQL).fetchone()
    except sqlite3.DatabaseError:
        return None  # a hub before hub0014, or no longer readable: always observe fresh
    if row is None or type(row[0]) is not str or len(row[0]) != 32 or type(row[2]) is not int:
        return None
    return tuple(row)


def _triggers_intact(tx):
    rows = tx.raw.execute("SELECT name,tbl_name,sql FROM main.sqlite_master WHERE type='trigger' "
                          "AND name LIKE 'authority\\_generation\\_%' ESCAPE '\\'").fetchall()
    return {name: (table, sql) for name, table, sql in rows} == AUTHORITY_TRIGGERS


def forget_reused_observations():
    """Drop every reused observation. Never required for correctness; tests and tools only."""
    with _reuse_lock:
        _reuse.clear()
        _decisions.clear()


# Decisions are a pure function of an observation and their arguments, so each reused
# observation carries its own; they live and die with it and are reached only through
# an observation the caller's transaction has just proved current.
_decisions = {}  # id(observation) -> (observation, {key: ExecutionResult})


def _decision_memo(observed):
    with _reuse_lock:
        entry = _decisions.get(id(observed))
        return entry[1] if entry is not None and entry[0] is observed else None


def _reuse_key(tx, stamp):
    # The build's own inputs are part of the key, so a replaced catalog, visibility or
    # loader (tests monkeypatch them) can never be answered from an older build's facts.
    return (str(tx.path), stamp, id(catalog_for_root), id(known_catalog), id(VISIBILITY),
            id(observe_current), id(s.load_root), id(s.observe_pair))


def _stamped_observation(tx):
    stamp = authority_stamp(tx)
    if stamp is None:
        return observe_current(tx)
    key = _reuse_key(tx, stamp)
    with _reuse_lock:
        observed = _reuse.get(key)
        if observed is not None:
            _reuse.move_to_end(key)
            return observed
    observed = observe_current(tx)
    # Store only what this same transaction proved: the token is unchanged across the
    # read (no own write intervened) and every trigger that keeps it honest is present.
    if authority_stamp(tx) == stamp and _triggers_intact(tx):
        with _reuse_lock:
            _reuse[key] = observed
            _reuse.move_to_end(key)
            _decisions[id(observed)] = (observed, {})
            while len(_reuse) > _REUSE_LIMIT:
                _, dropped = _reuse.popitem(last=False)
                if _decisions.get(id(dropped), (None,))[0] is dropped and not any(
                        x is dropped for x in _reuse.values()):
                    del _decisions[id(dropped)]
    return observed


def _operation_observation(tx):
    """Reuse facts only while the exact tracked database snapshot is unchanged.

    Administrative callers keep using observe_current for independent evidence.
    Authentication and policy evaluation are deliberately outside this cache.
    Across transactions, reuse is admitted only by _stamped_observation's fresh check.
    """
    key = tx.authority_snapshot_key()
    cached = getattr(tx, '_permission_observation', None)
    if key is not None and cached is not None and cached[0] == key:
        return cached[1]
    tx._permission_observation = None
    observed = _stamped_observation(tx)
    if key is not None and tx.authority_snapshot_key() == key:
        tx._permission_observation = (key, observed)
    return observed


def require_company(tx, *, actor: str, principal: str | None, company: str,
                    requirement: c.Requirement) -> a.ExecutionResult:
    """Current actor/human intersection; not a credential or graph certificate.

    The binding producer authenticates immediately before calling this function.
    No stored output of this function can authorize a later transaction: a policy
    result is memoized only on an observation this transaction has just proved current
    through its own authority token read, and is exactly what recomputing it would give.
    """
    observed = _operation_observation(tx)
    scope = c.ScopeKey('company', company)
    comparison = observed.snapshot.comparison
    if actor not in comparison.subjects or scope not in comparison.scopes:
        fail('unavailable_target', 'scope')
    if principal is not None and principal not in comparison.subjects:
        fail('not_administrator', 'binding')
    memo = _decision_memo(observed)
    decision = (actor, principal, scope, requirement)
    result = memo.get(decision) if memo is not None else None
    if result is None:
        try:
            result = a.execution(comparison, phase='old', actor=actor, bound_human=principal,
                                 scope=scope, requirement=requirement)
        except c.PolicyInputError:
            fail('invalid_input', 'requirement')
        if memo is not None:
            with _reuse_lock:
                memo[decision] = result
    if not result.intersection_admitted:
        fail('unavailable_target', 'scope')
    return result
