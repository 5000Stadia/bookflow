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
    'customer-refund-history-v1': (473, '2cde0560b88c952260a309e5e2b22c242e0202a766ddfc6677b483ee1426c983'),
    'agent-administration-v1': (479, 'c15675d00ce1b01aa628eaa2424ee0d8e402957c5eb669da5f9f0384d55add25'),
    'everyday-reports-v1': (490, '4159308ce66339b8414fe62261e8510b460ce11be04ad5bf99c335cdf7836255'),
    'card-credit-v1': (496, 'b6a4609483758c7f1f4c876793aca985772510543741f6e7a0bd13130eb2b8c7'),
    'company-backup-v1': (498, '75c05cd2dc074a26bd218ab7e48d12c09a5dbeadefb46de0475ca4bb1028f070'),
    'identity-deactivation-v1': (502, '51f5128ce50d04fd36fdc9000c3879e404d02a862009906b121a590c1c85ced7'),
    'report-export-v1': (503, '585047459e889e7b48af181307386d6eb476454fd3de624b04c4916455652611'),
    'audit-visibility-v1': (503, '1a6e263186f929c26d8749f6ba1b6c1ec16706287b9db0b09affeb6442789433'),
    'statement-import-v1': (504, '43eb5b56cc903f203974cb0ddb0a5fa3958dcca1ed05e8029ae2b254fc4d46e3'),
    'backup-schedule-v1': (508, '43582adbc2b0fb3341c2af31766c7740a1d8f7c6ea495a79f5675485b4f477b7'),
    'entry-review-v1': (511, '50fd1552c34eb3ab3d6e73d129491a00bbff1efd2f721a1b94c37003b719a7c0'),
    'cutover-v1': (514, 'fbea65dcc4951605c67b4bc10692006c48f7f92b5ef0f161071d8933b48fb12c'),
    'uncategorized-v1': (515, 'dced5fb01946a8346701896d5c7e41def95b64a22d211f59adba53111828a03f'),
    'human-administration-v1': (515, '4299f7604a39a74f0e9310703eecf2014f4ec7f75ba7cbe8973d5729172727a4'),
    'sales-tax-adjustment-v1': (519, '03d9f30190f30e741521e98ac6044a6962e29d26f2a3be25f54f338c8174486f'),
    'restore-v1': (521, '0a94a8ee9ee2f15db64858e594ea131f2afeef36cb29f11e9509d84e19fa584c'),
    'party-merge-v1': (525, 'f42ffc267bd8651808375d39dfbfb7c2fa2613e5889151cd68dc6609b2174398'),
    'bounced-check-v1': (526, '0b6616e4040e1986f3b010f4f2d8c92276ba12d741c28b474539755fa65ed68e'),
    'cutover-rest-v1': (527, 'eec55b86544f824349e62bbdeb9c20a63c6873c4e6073f97ade1a3b0ebe9d084'),
    'attachment-text-v1': (528, 'b482f8ec802d32fa21f3793759791333dd0aefce88dc5d9dabc5d6a79743a284'),
    'transaction-find-v1': (529, 'aec5cc4879ba82b57cf1d22791abc6d1f6f01477724ed521efad854045cb8531'),
    'item-convert-v1': (530, 'baa8f52bccb7c70240df1fc3a0117d0b5d9b7555ee2c57abf5a05bbe8cce52e1'),
    'finance-charge-v1': (531, 'ec3bf5ce4476e78dcdd55e3f4b24a8c06c76c759e1e045e803107292e4cc4160'),
    'vendor-refund-v1': (532, '150e3626256eef7e05ebe0ca2a602a90d1646ca6ac5a9400625ce9b150f6df7d'),
    'budget-v1': (550, 'b635577d72c1463d55aa938880609d4fabf2ed5ec416f6ca98201fec91725615'),
    'customer-prepayment-v1': (558, '24bbfec464495318237c0b6d2e81538fdd267344af67d2698efbbe12302ff1f2'),
    'list-merge-v1': (562, '061fdabeacdb8b45e62d3d50554cb717fef704c75d8f5366bda50423e2045a4b'),
    'sales-order-v1': (570, '2d5b06b100b2f2c07809b723578b8b769f8d0dc9b2db3a18fc94014271f1a481'),
    'fixed-asset-v1': (580, 'ba2c43a29b9d4a3c5966c054b82f103ca93fa5cf3c5a297f2126244106ff6b94'),
    'assembly-build-v1': (587, '980e4483d64de9d252b152dd4d0b51e40c7c68735227c00a59c9ce09251bd10e'),
    'committed-stock-v1': (588, '7f632304aab96f6f20d91b08ee6cc8c34f77d3bb3066935390b155c0bf4bdba8'),
    'mileage-v1': (606, '357335f1d1d8fff71c0e0029deff5bea7be9186b9cfcd556d326c5a4b4898770'),
    'collections-v1': (608, 'a4a42a893faf487946db8c4536c8e470af9b2d398d7fff6aa6237e1eaf89d380'),
    'pricing-rules-v1': (617, '2223cf7fa5490ca8b42c102b2c41ae2c7c12a5c031c146c254a9856d7e105651'),
    'order-invoicing-v1': (619, '7452c4b9233d80fe00ac1d14aecb164e94c82035cebbc682c4e61171d1b1cc14'),
    'shortfall-po-v1': (621, 'ce572b26ff407657374ea07f8bc8833a4f2e19225f4d5fd52e956917b886dc51'),
    'partial-po-v1': (622, '0d545843f9a3705ce88d87928760dcceb645632178e41e9a4a4ccc5539632eb1'),
    'timesheets-v1': (637, '6ae1c2d2ca67063b62f849f5130ce721beded5ac7ac41977e406d799eb3b10a5'),
    'inventory-sites-v1': (650, 'f5445ed06cfd12459d81305a84717f78481ee7679c348910567a092d28801266'),
    'accountant-review-v1': (664, 'fdb82ff38e630d7667ff5570da3dfbda017bfeb7a705bcb6a190f5b77ac0cf2a'),
    'check-printing-v1': (673, 'ea6368e6f2957858e0013e2c53453b4b70981395cf0c1f316224bf55e7d6abed'),
    'barcodes-v1': (675, 'cc3a5ccc83813d3705e9e55753bbb1e1a52b983755fa0c9ced8305c84a4f5ccd'),
    'inventory-counts-v1': (685, 'cbf2b5f35092aa7691fde5b0abb64a0f7c72ea55cf02820051a2649d44d639f5'),
    'scheduling-v1': (696, '445b102fcbce9835b1edceafe75f8d5dc573d18f03c0db73c83085ad1ceb7568'),
    'performance-v1': (698, '4e60dbb727be31efe9804b77a51cf36648ec37efde2dae1b6e323739504ea6a4'),
    'serial-lot-v1': (708, '4b340c8c2715584586326a1ee9698cc097af5993ab61cf68b25644a859768d03'),
    'bins-v1': (723, '5b61fdbac14da296bfe5c87ba003b4acaf6fbec991d31b1675acb87a66209b91'),
    'landed-cost-v1': (729, '1031c961c6bd10b761157ec1c255240324aaa2237f06f96f168bf26b7c4f5350'),
    'fifo-v1': (733, '724f7e0e052f9255d45480af689cf2861870cac19a18c71783918af73b04bc88'),
    'verify-v1': (734, '1a850789e63fe9d8b8d995b67098d1eebe026405bea56e31a79d06ce7a487c96'),
    'agent-setup-v1': (735, '5102370f0ee860388d7452f9d004c51789a95c0ed5a93cefa19a38650e475162'),
    'shipments-v1': (743, '9fa6ebf4efaf4a043bf04870d0b7f5dbf30e97f655bf889b631247c43be9c284'),
    'setup-trial-v1': (745, '4e16e82de135a51dd6da3e05b717f128851ddb97e9001b35b3946db7379830de'),
    'shipments-returns-v1': (749, 'f2c9af3e35a8f1708cf051173e262624f264d266576a028990579e07d4323883'),
    'picking-v1': (755, '34c39c80a09a3d5ef8b10808cb187830a236f340946a45cbc9e2b9ca817e71af'),
    'delivery-v1': (771, '085b7a93194e439557234ed8d88a7d7bdfd39ed06f8b2cbdddb732daca07561b'),
    'packing-v1': (774, '10872774b49e7497c34eccde2882c74e49c36ead5d7d2ff765399dd31fae79c8'),
    'delivery-queue-v1': (785, '2e19e20f01b2cdc3110be9f381a9ec54fa4f7f0ba5ef6880b49189576076554c'),
    'import-v1': (797, '0db21ee9713630abf0d4a5d81c282ee608483cd6f16b8a2aa40e3a14712dfe4a'),
    'worksheet-v1': (799, 'c59d5c63f042353fd78d4e565561f9d0f4560ad8420d56b412915b90f23a4cf4'),
    'delivery-batch-v1': (806, 'e7d68fa7de0173bbe842091419c3deed98561bc4e07c92cf981e4759e59756b5'),
    'import-mapping-v1': (814, '66a2d6a6c90f23b6f547ec27aa823a1440b892c22a7bd0b50ce8c142c5dc7b5f'),
    'delivery-schedule-v1': (822, '66aed81b62dd65713f27e55f39cea8ba2aa393d8584b08013a4a0298ec50dd59'),
    'bank-feed-v1': (826, '37f3a79e1ad520b97153c7863f0d3111c821e75bd944b0193c727ff94b3e9a29'),
    'bank-feed-reverse-v1': (828, 'df70eed353fe2243e74c722b39a0e7a9b12f98a2e3bd1a9c03bfc382325328c6'),
    'import-documents-v1': (828, '6418e208309e3f01e38b4749e62895dad640e66deada78c513ed3bf7127cf13a'),
    'bank-line-v1': (834, 'bd11005cfa5a7630090a663bf20f7dd2bd8d7c4ee4a764cd7295a40e1f4f61e9'),
    'capabilities-v1': (834, 'f639c12bed3999fc17973aa0698198f2af7dc1c111932d1af9c4ac3e58e99c99'),
    'item-picture-v1': (838, 'fb1968ec34b88620255e739d05573fb009d3ac47036745294fd0a6f9c77ac8af'),
    'bulk-edit-v1': (845, 'c4664e87ef03c124012304d580259f65920644ed03c3b0c2cb535f8060e7fe6b'),
    'bank-line-details-v1': (847, 'c84a45585ddc7d4b5e7e1ce602517bfd25f3c76de7bba387ccc0064f8045f6e5'),
    'user-home-v1': (850, 'e3ca5ecf2483bcf9780cdd3e9300c5a9cb9ac74f02d2a9f29175bfe8f0c98b36'),
    'automation-v1': (853, '4df1e05f0db60c9ac166ce314398120e3065dfb07f205327c81f3d5e5348e7ed'),
    'opening-draft-v1': (862, '56a757a3260e92ca76ffafbf79f7cbcac99198a1c292278998588eef117d9aa2'),
    'pos-v1': (866, '3d55aae9746f7326fb83eab6d2a48e4d6d100ce35e48f6d845cbff2180dacaf2'),
    'bank-rule-v1': (874, '12b5f914d21302af037c6cc0b1aeab0ef6912d764015332d7734c12338ad8127'),
    'pos-sale-v1': (878, '1462bee09d7343f15fdc015fa86a114e30671e8d73fc2bab7f5e75444d0450cf'),
    'bank-reconcile-v1': (878, '8673bd998c7e077061301d0bfaea5af7b33cbd3eb773f89dd0e6fdcdbf026e42'),
    'bank-source-v1': (884, '9676dfedbb049b4743cdc1d56915a2d296f6eaf3cda9db30768d912a11d36949'),
    'pos-layout-v1': (893, '9f95dba850d8f730aac8e4fdfdd82f23de09109fcf54e7e9499ff9b22dd29916'),
    'pos-ticket-v1': (898, '6639dc50b25389df49db0c4bd6e45d637a6271a4c9f81c8abd579abfb0d7ff35'),
    'pos-ticket-takeover-v1': (899, 'bdcea7f8d98d6854d5ecc001b00e4b59f81288ef06af360288f8a2ce28e17bf3'),
    'pos-receipt-v1': (900, '00cfad8461346685e3de9485e8c900e23808206a65626cb72960aec0444a5a06'),
    'pos-refund-v1': (904, '469328cc54c3746bbb7e25fae4c5773e0123f935f5eba136a7a5349491097184'),
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
    'customer-refund-history-v1': {'customer-refund history'},
    'agent-administration-v1': {'agent assign', 'agent authorize', 'agent create', 'agent list', 'agent show',
                                'agent unassign'},
    'everyday-reports-v1': {'report customer-balance-detail', 'report customer-balance-summary', 'report deposit-detail', 'report open-purchase-orders', 'report purchases-by-item', 'report purchases-by-vendor', 'report reconciliation-discrepancy', 'report transaction-list-by-date', 'report vendor-1099-summary', 'report vendor-balance-detail', 'report vendor-balance-summary'},
    'card-credit-v1': {'card-credit history', 'card-credit post', 'card-credit query', 'card-credit show',
                       'card-credit update', 'card-credit void'},
    'company-backup-v1': {'company backup', 'company restore'},
    'identity-deactivation-v1': {'agent activate', 'agent deactivate', 'user activate', 'user deactivate'},
    'report-export-v1': {'report export'},
    'audit-visibility-v1': set(),
    'statement-import-v1': {'reconcile import'},
    'backup-schedule-v1': {'backup list', 'backup rehearse', 'backup schedule', 'backup verify'},
    'entry-review-v1': {'report entries-to-review', 'report prior-balances', 'review mark'},
    'cutover-v1': {'cutover apply', 'cutover plan', 'cutover tie-out'},
    'uncategorized-v1': {'account uncategorized'},
    'human-administration-v1': set(),
    'sales-tax-adjustment-v1': {'sales-tax adjust', 'sales-tax adjustment query', 'sales-tax adjustment show',
                                'sales-tax adjustment void'},
    'restore-v1': {'invoice restore', 'journal restore'},
    'party-merge-v1': {'customer merge', 'customer unmerge', 'vendor merge', 'vendor unmerge'},
    'bounced-check-v1': {'payment bounce'},
    'cutover-rest-v1': {'vendor 1099-opening'},
    'attachment-text-v1': {'attachment text'},
    'transaction-find-v1': {'transaction find'},
    'item-convert-v1': {'item convert'},
    'finance-charge-v1': {'finance-charge assess'},
    'vendor-refund-v1': {'vendor-credit refund'},
    'budget-v1': {'budget clear', 'budget create', 'budget delete', 'budget fill', 'budget list', 'budget rename',
                  'budget set', 'budget show', 'memorized-report create', 'memorized-report delete',
                  'memorized-report list', 'memorized-report run', 'memorized-report show', 'memorized-report update',
                  'report balance-sheet-comparison', 'report budget-overview', 'report budget-vs-actual',
                  'report profit-and-loss-comparison'},
    'customer-prepayment-v1': {'prepayment receive', 'prepayment attach', 'prepayment apply', 'prepayment unapply',
                               'prepayment refund', 'prepayment show', 'prepayment query', 'report customer-prepayments'},
    'list-merge-v1': {'account merge', 'account unmerge', 'item merge', 'item unmerge'},
    'sales-order-v1': {'estimate sales-order', 'sales-order copy', 'sales-order create', 'sales-order history',
                       'sales-order query', 'sales-order show', 'sales-order update', 'sales-order void'},
    'fixed-asset-v1': {'fixed-asset depreciate', 'fixed-asset dispose', 'fixed-asset list', 'fixed-asset schedule',
                       'fixed-asset setup', 'loan create', 'loan list', 'loan pay', 'loan show', 'loan update'},
    'assembly-build-v1': {'assembly build', 'assembly update', 'assembly finalize', 'assembly void', 'assembly delete', 'assembly show', 'assembly list'},
    'committed-stock-v1': {'report open-sales-orders'},
    'mileage-v1': {'mileage-rate delete', 'mileage-rate list', 'mileage-rate set', 'report mileage-by-job',
                   'report mileage-by-vehicle', 'trip billing', 'trip create', 'trip invoice', 'trip query',
                   'trip reimburse', 'trip sales-receipt', 'trip show', 'trip update', 'trip void', 'vehicle create',
                   'vehicle list', 'vehicle show', 'vehicle update'},
    'collections-v1': {'customer-credit transfer', 'customer-credit transfer-void'},
    'pricing-rules-v1': {'price-rule activate', 'price-rule create', 'price-rule deactivate', 'price-rule list',
                         'price-rule preferences', 'price-rule preview', 'price-rule set-preferences',
                         'price-rule show', 'price-rule update'},
    'order-invoicing-v1': {'sales-order billing', 'sales-order invoice'},
    'shortfall-po-v1': {'sales-order purchase-order', 'work-order purchase-order'},
    'partial-po-v1': {'purchase-order close-lines'},
    'timesheets-v1': {'clock cancel', 'clock in', 'clock link', 'clock out', 'clock status', 'schedule calendar',
                      'schedule cancel', 'schedule create', 'schedule show', 'schedule update', 'timesheet copy-last',
                      'timesheet preferences', 'timesheet save', 'timesheet set-preferences', 'timesheet show'},
    'inventory-sites-v1': {'inventory-site activate', 'inventory-site create', 'inventory-site deactivate',
                           'inventory-site list', 'inventory-site show', 'inventory-site update',
                           'inventory-transfer history', 'inventory-transfer post', 'inventory-transfer query',
                           'inventory-transfer show', 'inventory-transfer update', 'inventory-transfer void',
                           'report quantity-by-site'},
    'accountant-review-v1': {'accountant add', 'accountant list', 'accountant mark-entry', 'accountant reclassify',
                             'accountant remove', 'accountant reviewed-through', 'accountant-copy cancel',
                             'accountant-copy changes', 'accountant-copy export', 'accountant-copy import',
                             'accountant-copy preview', 'accountant-copy status', 'report accountant-changes',
                             'report accountant-review'},
    'check-printing-v1': {'check print', 'check print-alignment', 'check print-cancel', 'check print-confirm',
                          'check print-pdf', 'check print-queue', 'check print-run', 'check print-setup', 'check reprint'},
    'barcodes-v1': {'barcode generate', 'barcode lookup'},
    'inventory-counts-v1': {'inventory-count cancel', 'inventory-count create', 'inventory-count enter',
                            'inventory-count post', 'inventory-count query', 'inventory-count sheet',
                            'inventory-count show', 'inventory-count void', 'report inventory-valuation-detail',
                            'report physical-inventory-worksheet'},
    'scheduling-v1': {'todo create', 'todo update', 'todo complete', 'todo show', 'todo list', 'notice generate',
                      'notice list', 'notice show', 'notice mark-sent', 'notice dismiss', 'reminders'},
    'performance-v1': {'perf report', 'perf settings'},
    'serial-lot-v1': {'inventory assign', 'lot list', 'lot show', 'lot update', 'report lot-trace',
                      'report serial-lot-history', 'report serial-lot-stock', 'serial-number list',
                      'serial-number show', 'serial-number update'},
    'bins-v1': {'bin activate', 'bin create', 'bin deactivate', 'bin list', 'bin show', 'bin update',
                'cycle-class activate', 'cycle-class create', 'cycle-class deactivate', 'cycle-class list',
                'cycle-class show', 'cycle-class update', 'cycle-count classify', 'cycle-count due',
                'report items-by-bin'},
    'landed-cost-v1': {'landed-cost charges', 'landed-cost post', 'landed-cost query', 'landed-cost setup',
                       'landed-cost show', 'landed-cost void'},
    'fifo-v1': {'costing-method set', 'costing-method show', 'costing-method withdraw', 'report inventory-layers'},
    'verify-v1': {'company verify'},
    'agent-setup-v1': {'setup'},
    'shipments-v1': {'report shipped-not-invoiced', 'shipment history', 'shipment invoice', 'shipment post',
                     'shipment query', 'shipment show', 'shipment update', 'shipment void'},
    'setup-trial-v1': {'owner-link', 'reconcile discard'},
    'shipments-returns-v1': {'shipment return', 'shipment-return query', 'shipment-return show', 'shipment-return void'},
    'picking-v1': {'pick-list cancel', 'pick-list close', 'pick-list create', 'pick-list enter', 'pick-list query',
                   'pick-list show'},
    'delivery-v1': {'delivery cancel', 'delivery draft', 'delivery export', 'delivery history', 'delivery mark-sent',
                    'delivery query', 'delivery send', 'delivery show', 'delivery update', 'delivery-template create',
                    'delivery-template deactivate', 'delivery-template fields', 'delivery-template preview',
                    'delivery-template query', 'delivery-template show', 'delivery-template update'},
    'packing-v1': {'shipment cancel', 'shipment create', 'shipment pack'},
    'delivery-queue-v1': {'delivery approve', 'delivery retry', 'delivery run', 'delivery-connection add',
                          'delivery-connection check', 'delivery-connection disable', 'delivery-connection enable',
                          'delivery-connection query', 'delivery-connection remove', 'delivery-connection show',
                          'delivery-connection update'},
    'import-v1': {'batch query', 'batch show', 'import cancel', 'import errors', 'import kinds', 'import preview',
                  'import query', 'import reverse', 'import rows', 'import run', 'import show', 'import template'},
    'worksheet-v1': {'report order-fulfillment', 'report shipments'},
    'delivery-batch-v1': {'delivery batch', 'delivery batch-cancel', 'delivery batch-query', 'delivery batch-show', 'delivery flag',
                          'delivery queue', 'delivery unflag'},
    'import-mapping-v1': {'import check', 'import decide', 'import-mapping deactivate', 'import-mapping history',
                          'import-mapping match', 'import-mapping query', 'import-mapping save', 'import-mapping show'},
    'delivery-schedule-v1': {'delivery-schedule create', 'delivery-schedule live', 'delivery-schedule pause',
                             'delivery-schedule query', 'delivery-schedule resume', 'delivery-schedule run',
                             'delivery-schedule show', 'delivery-schedule update'},
    'bank-feed-v1': {'bank-feed import', 'bank-feed query', 'bank-feed show', 'bank-line query'},
    'bank-feed-reverse-v1': {'bank-feed move', 'bank-feed reverse'},
    'import-documents-v1': set(),
    'bank-line-v1': {'bank-line add', 'bank-line ignore', 'bank-line match', 'bank-line transfer', 'bank-line undo',
                     'bank-line unmatch'},
    'capabilities-v1': set(),  # no command: the people-only acts become capabilities
    'item-picture-v1': {'item picture clear', 'item picture get', 'item picture set', 'item picture use'},
    'bulk-edit-v1': {'bulk-edit apply', 'bulk-edit columns', 'bulk-edit grid', 'bulk-edit preview', 'bulk-edit query',
                     'bulk-edit reverse', 'bulk-edit show'},
    'bank-line-details-v1': {'bank-line deposit', 'bank-line pay-bills'},
    'user-home-v1': {'user home clear', 'user home set', 'user home show'},
    'automation-v1': {'automation list', 'automation pause', 'automation show'},
    'opening-draft-v1': {'opening-draft apply', 'opening-draft create', 'opening-draft discard', 'opening-draft plan',
                         'opening-draft query', 'opening-draft reverse', 'opening-draft show', 'opening-draft tie-out',
                         'opening-draft update'},
    'pos-v1': {'pos-register create', 'pos-register list', 'pos-register show', 'pos-register update'},
    'bank-rule-v1': {'bank-rule accept', 'bank-rule apply', 'bank-rule create', 'bank-rule deactivate',
                     'bank-rule decline', 'bank-rule query', 'bank-rule show', 'bank-rule update'},
    'pos-sale-v1': {'pos customer add', 'pos customer search', 'pos sale complete', 'pos sale preview'},
    'bank-reconcile-v1': set(),  # conditional inbox read on the existing reconcile import
    'bank-source-v1': {'bank-feed-connection add', 'bank-feed-connection enable', 'bank-feed-connection disable',
                       'bank-feed-connection check', 'bank-feed-connection query', 'bank-feed fetch'},
    'pos-layout-v1': {f'pos-box {verb}' for verb in ('list', 'create', 'update', 'move', 'remove', 'view',
                                                   'seed', 'picture set', 'picture get')},
    'pos-ticket-v1': {'pos ticket hold', 'pos ticket list', 'pos ticket resume', 'pos ticket discard', 'pos sale void'},
    'pos-ticket-takeover-v1': {'pos ticket take-over'},
    'pos-receipt-v1': {'pos receipt show'},
    'pos-refund-v1': {'pos refund', 'pos refund lines', 'pos refund show', 'pos refund receipt'},
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
