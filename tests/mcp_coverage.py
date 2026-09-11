"""Executable coverage ledger; pending rows are explicit, never counted as parity."""
from html.parser import HTMLParser

from bookflow.core import registry
from bookflow.core.publication_inventory import inventory
from bookflow.documentation.examples import _SUPPORTING_CREATE_INPUTS
from bookflow.documentation.introspection import model_fields
from bookflow.adapters.workbench import forms

# Reviewed payment base 15cb601 adds these command identities. They remain
# pending execution scenarios until actual cross-interface witnesses exist.
PAYMENT_COMMANDS = frozenset("""application history
application show
invoice settlement
payment apply
payment calculate
payment history
payment invoices
payment operation items
payment operation show
payment preview items
payment query
payment receive
payment selection clear
payment selection create
payment selection items
payment selection query
payment selection show
payment selection update
payment settlement
payment settlement changes
payment show
payment suggest
payment unapply
payment update
payment void""".splitlines())

BROWSING_COMMANDS = frozenset(('account query options', 'class query options', 'custom-field query children', 'custom-field query options', 'customer query options', 'customer-message query options', 'customer-type query options', 'employee query options', 'item query children', 'item query options', 'item-category query options', 'job-type query options', 'other-name query options', 'payment-method query options', 'price-level query children', 'price-level query options', 'sales-rep query options', 'sales-tax-code query options', 'ship-method query options', 'term query options', 'unit-of-measure query children', 'unit-of-measure query options', 'vendor query children', 'vendor query options', 'vendor-type query options'))

FROZEN_COMMANDS = BROWSING_COMMANDS | PAYMENT_COMMANDS | frozenset("""account activate
account create
account deactivate
account list
account query
account show
account update
activity
attachment add
attachment get
attachment link
attachment list
attachment unlink
audit list
audit show
audit tail
bill history
bill pay
bill payment apply
bill payment query
bill payment show
bill payment unapply
bill payment void
bill post
bill query
bill show
bill update
bill void
card-charge history
card-charge post
card-charge query
card-charge show
card-charge update
card-charge void
chart apply
chart list
chart show
check history
check post
check query
check show
check update
check void
class activate
class create
class deactivate
class list
class query
class show
class update
company attach
company compact
company detach
company list
company new
company rename
company show
company update
company use
credit-memo history
credit-memo post
credit-memo show
custom-field activate
custom-field create
custom-field deactivate
custom-field list
custom-field query
custom-field show
custom-field update
customer activate
customer create
customer deactivate
customer link-vendor
customer list
customer query
customer show
customer unlink-vendor
customer update
customer-message activate
customer-message create
customer-message deactivate
customer-message list
customer-message query
customer-message show
customer-message update
customer-type activate
customer-type create
customer-type deactivate
customer-type list
customer-type query
customer-type show
customer-type update
demo reset
deposit items
deposit post
deposit query
deposit show
deposit sources
deposit update
deposit void
directive add
directive deactivate
directive list
directive show
docs generate
employee activate
employee create
employee deactivate
employee list
employee query
employee show
employee update
estimate billing
estimate copy
estimate create
estimate history
estimate invoice
estimate query
estimate sales-receipt
estimate show
estimate update
estimate work-order
hub audit list
hub audit show
hub audit tail
init
invoice history
invoice post
invoice query
invoice show
invoice update
invoice void
item activate
item create
item deactivate
item list
item query
item show
item update
item-category activate
item-category create
item-category deactivate
item-category list
item-category query
item-category show
item-category update
job-type activate
job-type create
job-type deactivate
job-type list
job-type query
job-type show
job-type update
journal history
journal post
journal query
journal show
journal update
journal void
mcp
membership grant
membership list
membership revoke
note add
note edit
note list
note show
organization list
organization new
organization rename
organization show
other-name activate
other-name convert
other-name create
other-name deactivate
other-name list
other-name query
other-name show
other-name update
payment recovery abort
payment recovery apply
payment recovery begin
payment recovery compare
payment recovery compare-items
payment recovery items
payment recovery query
payment recovery replace
payment recovery seal
payment recovery show
payment recovery upload
payment-method activate
payment-method create
payment-method deactivate
payment-method list
payment-method query
payment-method show
payment-method update
presence clear
presence set
price-level activate
price-level create
price-level deactivate
price-level list
price-level query
price-level show
price-level update
profile apply
profile list
profile show
proposal copy
proposal create
proposal estimate
proposal history
proposal query
proposal show
proposal update
rate query
rate set
rate show
register calculate
register post
register query
register update
report ap-aging
report ar-aging
report balance-sheet
report general-ledger
report open-invoices
report profit-and-loss
report statement
report trial-balance
report unpaid-bills
sales-receipt history
sales-receipt post
sales-receipt query
sales-receipt show
sales-receipt update
sales-receipt void
sales-rep activate
sales-rep create
sales-rep deactivate
sales-rep list
sales-rep query
sales-rep show
sales-rep update
sales-tax liability
sales-tax pay
sales-tax payment query
sales-tax payment show
sales-tax payment void
sales-tax-code activate
sales-tax-code create
sales-tax-code deactivate
sales-tax-code list
sales-tax-code query
sales-tax-code show
sales-tax-code update
serve
ship-method activate
ship-method create
ship-method deactivate
ship-method list
ship-method query
ship-method show
ship-method update
term activate
term create
term deactivate
term list
term query
term show
term update
token issue
token list
token revoke
transfer history
transfer post
transfer query
transfer show
transfer update
transfer void
undo
unit-of-measure activate
unit-of-measure create
unit-of-measure deactivate
unit-of-measure list
unit-of-measure query
unit-of-measure show
unit-of-measure update
upgrade
user add
user list
user set-password
vendor activate
vendor create
vendor deactivate
vendor list
vendor query
vendor show
vendor update
vendor-type activate
vendor-type create
vendor-type deactivate
vendor-type list
vendor-type query
vendor-type show
vendor-type update
work-order billing
work-order complete
work-order copy
work-order create
work-order history
work-order invoice
work-order query
work-order sales-receipt
work-order show
work-order update
""".splitlines())
LISTS = {n + ' ' + v for n in _SUPPORTING_CREATE_INPUTS for v in ('create', 'show', 'list', 'query', 'update', 'activate', 'deactivate')}
FINANCIAL = {n + ' ' + v for n in ('journal', 'invoice', 'sales-receipt') for v in ('post', 'show', 'history', 'query', 'update', 'void')}


LOCAL_VALID_WITNESSES = {
    'init': ['tests/test_mcp_local_bootstrap.py::test_installed_cli_bootstrap_preview_create_reopen_and_conflict',
             'tests/test_row1_flow.py::test_init_edges'],
    'company use': ['tests/test_row1_flow.py::test_company_selection',
                    'tests/test_row3_host.py::test_a_forwarded_company_use_writes_the_callers_login_table'],
    'docs generate': ['tests/test_docs_command.py::test_docs_generate_cli_needs_no_initialized_data_root'],
    'mcp': ['tests/test_mcp_hosted.py::test_real_stdio_discovery_help_and_attributed_host_write',
            'tests/test_mcp_installed_guide.py::test_literal_installed_mcp_guide'],
    'serve': ['tests/test_row3_host.py::test_the_host_migrates_and_records_the_descriptor',
              'tests/test_row3_host.py::test_sigint_wakes_a_live_stream_and_cleans_the_host'],
}


def execution_map():
    from tests.test_mcp_registry_demo_upgrade import COMMANDS as DEMO_UPGRADE_COMMANDS
    from tests.test_mcp_local_boundary import COMMANDS as LOCAL_COMMANDS
    from tests.test_mcp_registry_undo import COMMANDS as UNDO_COMMANDS
    from tests.test_mcp_registry_rollout import COMMANDS as ROLLOUT_COMMANDS
    from tests.test_mcp_registry_company_maintenance import COMMANDS as MAINTENANCE_COMMANDS
    from tests.test_mcp_registry_compact import COMMANDS as COMPACT_COMMANDS
    from tests.test_mcp_registry_audit_reads import COMMANDS as AUDIT_COMMANDS
    from tests.test_mcp_registry_attachments import COMMANDS as ATTACHMENT_COMMANDS
    from tests.test_mcp_registry_register import COMMANDS as REGISTER_COMMANDS
    from tests.test_mcp_registry_credentials import COMMANDS as CREDENTIAL_COMMANDS
    from tests.test_mcp_registry_presence import COMMANDS as PRESENCE_COMMANDS
    from tests.test_mcp_registry_work import FAMILIES as WORK_FAMILIES
    from tests.test_mcp_registry_work_billing import FAMILIES as BILLING_FAMILIES
    from tests.test_mcp_registry_hub_reads import FAMILIES as HUB_FAMILIES
    from tests.test_mcp_registry_supporting import FAMILIES
    from tests.test_mcp_registry_payment_preparation import FAMILIES as PAYMENT_FAMILIES
    from tests.test_mcp_registry_payments import COMMANDS as PAYMENT_FINANCIAL
    from tests.test_mcp_registry_deposits import COMMANDS as DEPOSIT_COMMANDS
    from tests.test_mcp_registry_identity import COMMANDS as IDENTITY_COMMANDS
    from tests.test_money_out_documents import COMMANDS as MONEY_OUT_COMMANDS
    from tests.test_bill_entry import COMMANDS as BILL_COMMANDS
    from tests.test_bill_payment import COMMANDS as BILL_PAYMENT_COMMANDS
    from tests.test_credit_memo import COMMANDS as CREDIT_MEMO_COMMANDS
    from tests.test_sales_tax_remittance import COMMANDS as SALES_TAX_COMMANDS
    from tests.test_transfer_funds import COMMANDS as TRANSFER_COMMANDS
    from tests.test_payment_recovery_interfaces import COMMANDS as RECOVERY_COMMANDS
    registry.load_all()
    commands = registry.all_commands(include_standalone=True)
    assert {c.name for c in commands} == FROZEN_COMMANDS, 'New or removed command needs a deliberate coverage disposition'
    permissions = {row['command']: row for row in inventory()}
    result = []
    for cmd in commands:
        witness = ('tests/test_mcp_registry_browsing.py::test_list_browsing_four_interfaces' if cmd.name in BROWSING_COMMANDS else
                   'tests/test_mcp_registry_lists.py::test_each_list_lifecycle_valid_rejected_and_preview_parity' if cmd.name in LISTS else
                   'tests/test_mcp_registry_financial.py::test_financial_lifecycle_full_documents_and_ledger_parity' if cmd.name in FINANCIAL else
                   'tests/test_mcp_registry_supporting.py::test_supporting_family_full_documents_and_rejections' if any(cmd.name in names for names in FAMILIES.values()) else
                   'tests/test_mcp_registry_payment_preparation.py::test_payment_preparation_four_surface_documents_context_and_rejections' if any(cmd.name in names for names in PAYMENT_FAMILIES.values()) else
                   'tests/test_mcp_registry_payments.py::test_payment_financial_lifecycle_full_documents_and_exact_ledger' if cmd.name in PAYMENT_FINANCIAL else
                   'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger' if cmd.name in DEPOSIT_COMMANDS else
                   'tests/test_mcp_registry_identity.py::test_identity_lifecycle_full_documents_owned_password_and_rejected_state' if cmd.name in IDENTITY_COMMANDS else
                   'tests/test_money_out_documents.py::test_the_same_check_and_card_charge_through_python_cli_http_and_mcp' if cmd.name in MONEY_OUT_COMMANDS else
                   'tests/test_bill_entry.py::test_the_same_bill_through_python_cli_http_and_mcp' if cmd.name in BILL_COMMANDS else
                   'tests/test_bill_payment.py::test_the_same_bill_payment_through_python_cli_http_and_mcp' if cmd.name in BILL_PAYMENT_COMMANDS else
                   'tests/test_credit_memo.py::test_the_same_credit_memo_through_python_cli_http_and_mcp' if cmd.name in CREDIT_MEMO_COMMANDS else
                   'tests/test_sales_tax_remittance.py::test_the_same_sales_tax_remittance_through_python_cli_http_and_mcp' if cmd.name in SALES_TAX_COMMANDS else
                   'tests/test_transfer_funds.py::test_the_same_transfer_through_python_cli_http_and_mcp' if cmd.name in TRANSFER_COMMANDS else
                   'tests/test_payment_recovery_interfaces.py::test_complete_recovery_contract_on_all_four_interfaces' if cmd.name in RECOVERY_COMMANDS else
                   'tests/test_mcp_registry_hub_reads.py::test_hub_read_full_documents_and_scope_boundaries' if any(cmd.name in names for names in HUB_FAMILIES.values()) else
                   'tests/test_mcp_registry_work.py::test_nonposting_work_lifecycle_full_documents_and_lineage' if any(cmd.name in names for names in WORK_FAMILIES.values()) else
                   'tests/test_mcp_registry_work_billing.py::test_work_billing_full_documents_retries_and_exact_batches' if any(cmd.name in names for names in BILLING_FAMILIES.values()) else
                   'tests/test_mcp_registry_register.py::test_register_calculate_post_correct_query_and_rejection_parity' if cmd.name in REGISTER_COMMANDS else
                   'tests/test_mcp_registry_credentials.py::test_credentials_full_documents_owned_hashes_and_rejected_state' if cmd.name in CREDENTIAL_COMMANDS else
                   'tests/test_mcp_registry_presence.py::test_advisory_presence_exact_documents_and_no_business_mutation' if cmd.name in PRESENCE_COMMANDS else
                   'tests/test_mcp_registry_audit_reads.py::test_audit_activity_full_documents_historical_attribution_and_rejections' if cmd.name in AUDIT_COMMANDS else
                   'tests/test_mcp_registry_attachments.py::test_registered_binary_and_link_lifecycle_complete_parity' if cmd.name in ATTACHMENT_COMMANDS else
                   'tests/test_mcp_registry_compact.py::test_compaction_preview_collection_replay_and_rejection_parity' if cmd.name in COMPACT_COMMANDS else
                   'tests/test_mcp_registry_company_maintenance.py::test_company_maintenance_valid_preview_rejections_and_owned_move' if cmd.name in MAINTENANCE_COMMANDS else
                   'tests/test_mcp_registry_rollout.py::test_rollout_chart_profile_detach_reattach_full_documents' if cmd.name in ROLLOUT_COMMANDS else
                   'tests/test_mcp_registry_undo.py::test_undo_preview_compensation_replay_and_rejected_state' if cmd.name in UNDO_COMMANDS else
                   'tests/test_mcp_registry_demo_upgrade.py::test_owned_demo_replacement_and_current_schema_upgrade' if cmd.name in DEMO_UPGRADE_COMMANDS else
                   'tests/test_mcp_local_boundary.py::test_installed_local_boundaries_are_explicit_and_do_not_execute' if cmd.name in LOCAL_COMMANDS else None)
        mode = ('standalone_protocol' if cmd.protocol_stdout else 'standalone_local' if cmd.standalone else
                'local_lifecycle' if cmd.local_only else 'binary_' + cmd.transfer.direction if cmd.transfer else
                'advisory' if cmd.kind == 'advisory' else 'finite_poll_with_local_follow' if cmd.streams else 'routed_json')
        result.append({'command': cmd.name, 'mode': mode, 'scope': cmd.scope, 'kind': cmd.kind,
            'preview': cmd.is_write, 'idempotency_key': cmd.accepts_idempotency_key,
            'clearable': cmd.clearable, 'execution_witness': witness,
            'coverage': 'local_lifecycle_scenario' if cmd.name in LOCAL_COMMANDS else 'four_surface_scenario' if witness else 'pending_four_surface_or_local_lifecycle',
            'local_valid_witnesses': LOCAL_VALID_WITNESSES.get(cmd.name, []),
            'publication': permissions[cmd.name]})
    return result


class Controls(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.named = {}
        self.ids = {}
        self.feed(page)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if 'id' in attributes:
            self.ids.setdefault(attributes['id'], []).append({'tag': tag, **attributes})
        if tag in {'input', 'select', 'textarea'} and 'name' in attributes:
            self.named.setdefault(attributes['name'], []).append({'tag': tag, **attributes})


def variant_policies():
    """Finite structural cases, with outstanding behavior separate from node count.

    These references locate executable evidence. They are not stored test results
    and never turn an unexecuted or uncovered branch into browser acceptance.
    """
    nested = 'tests/test_mcp_nested_gui_browser.py::test_nested_collection_model_object_null_and_full_error'
    parent = 'tests/test_mcp_nested_gui_browser.py::test_nullable_parent_object_clear_is_distinct_from_omission_and_child_patch'
    boolean = 'tests/test_mcp_workbench_control_browser.py::test_generated_boolean_false_omission_and_null_encoding'
    number = 'tests/test_mcp_rate_gui_browser.py::test_rate_exact_integer_decimal_conflict_destination_noop_and_replay'
    custom = 'tests/test_row8_custom_field_browser.py::test_keyboard_all_kinds_48_fields_splits_restore_clear'
    money = 'tests/test_numeric_entry_browser.py::test_generated_sales_math_preview_and_save'
    payment = 'tests/test_mcp_payment_form_browser.py::test_saved_selection_control_previews_real_receipt'
    return {
        ('anyOf', ('Address', 'null')): ([parent, 'tests/test_mcp_address_payee_browser.py::test_sales_address_default_explicit_null_and_return_to_current_default'], []),
        ('anyOf', ('AddressInput', 'null')): ([parent, 'tests/test_mcp_address_payee_browser.py::test_party_address_create_patch_clear_and_omission'], []),
        ('anyOf', ('RegisterParty', 'null')): ([nested, parent, 'tests/test_mcp_address_payee_browser.py::test_top_level_register_payee_prefill_null_replacement_and_journal_destination'], []),
        ('anyOf', ('CheckParty', 'null')): ([
            'tests/test_money_out_browser.py::test_the_check_window_posts_the_check_the_command_posts',
            'tests/test_money_out_browser.py::test_the_expenses_grid_has_nothing_to_scroll_sideways_at_phone_width'],
            ['the document payee is driven at /pay_to on both nouns — a vendor chosen on the check window, '
             'and the picker left at its opening value at 390px on check and card charge alike; the per-line '
             '/expenses/[]/party alternative has no column in the Expenses grid and no browser case at all']),
        ('anyOf', ('MoneyInput', 'string')): (['tests/test_mcp_nested_gui_browser.py::test_nested_collection_model_object_null_and_full_error', 'tests/test_mcp_money_gui_browser.py::test_structured_integer_money_object_exact_bytes_and_core_rejection'], []),
        ('anyOf', ('MoneyInput', 'null', 'string')): ([
            'tests/test_mcp_money_gui_browser.py::test_structured_integer_money_object_exact_bytes_and_core_rejection',
            'tests/test_bill_payment.py::test_the_same_bill_payment_through_python_cli_http_and_mcp'],
            ['the omitted branch of a bill-payment amount means "everything still open on that bill"; '
             'there is no Pay Bills form, so no browser case reaches it and the four-surface witness '
             'is the only executable evidence']),
        ('anyOf', ('SalesMoneyInput', 'null', 'string')): ([money, 'tests/test_mcp_money_gui_browser.py::test_top_level_money_object_nullable_branch_and_complete_calculation', 'tests/test_mcp_sales_money_origin_browser.py::test_sales_line_money_object_null_rejection_and_current_default_origin'], []),
        ('anyOf', ('SignedMoney', 'string')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['the deposit witnesses enter signed decimal strings; the structured signed-money object has no dedicated browser case yet']),
        ('anyOf', ('SalesMoneyInput', 'string')): ([money, 'tests/test_mcp_money_gui_browser.py::test_top_level_money_object_nullable_branch_and_complete_calculation'], []),
        ('anyOf', ('any', 'null')): ([custom, 'tests/test_mcp_definition_kinds_browser.py::test_definition_all_kinds_default_empty_null_omission_and_choice_visibility', 'tests/test_mcp_any_value_browser.py::test_any_money_top_level_and_nested_json_values_and_unchanged_text'], []),
        ('anyOf', ('array', 'null')): ([custom, 'tests/test_mcp_nested_gui_browser.py::test_optional_nested_collection_order_empty_null_and_omission'], []),
        ('anyOf', ('boolean', 'null')): ([boolean], []),
        ('anyOf', ('integer', 'null')): ([number, 'tests/test_mcp_nested_gui_browser.py::test_optional_nested_collection_order_empty_null_and_omission'], []),
        ('anyOf', ('null', 'object')): ([custom, 'tests/test_mcp_payment_form_browser.py::test_generated_payment_json_object_error_preview_and_saved_false'], []),
        ('anyOf', ('null', 'string')): ([boolean], []),
        ('oneOf', ('BoolCriterion', 'ChoiceCriterion', 'DateCriterion', 'NumberCriterion', 'PresenceCriterion', 'TextCriterion')): ([
            'tests/test_list_browsing_browser.py::test_named_columns_filters_and_readable_collections',
            'tests/test_list_browsing_browser.py::test_reorder_reset_zero_missing_paging_and_stale_restart',
            'tests/test_list_browsing_text_date_browser.py::test_text_date_named_criteria_exact_wire_results_and_retained_identity'],
            ['Dedicated list chooser witnesses cover representative Boolean/number/choice/presence/text/date cases; untested operators, malformed-date interactions and all generated-form criterion branches remain open.']),
        ('anyOf', ('CashBackInput', 'null')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['the deposit correction witness supplies a cash-back line and the post witness omits it; explicit-null clearing has no dedicated browser case yet']),
        ('oneOf', ('DraftDocument', 'InlineDocument')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['durable deposit drafts and their selection dialog are not registered yet, so only the inline branch has a form']),
        ('oneOf', ('DraftDocument', 'ReplacementDocument')): ([
            'tests/test_mcp_registry_deposits.py::test_deposit_lifecycle_full_documents_and_exact_ledger'],
            ['durable deposit drafts and their selection dialog are not registered yet, so only the inline branch has a form']),
        ('oneOf', ('InlineApplications', 'SelectionReference')): ([payment, 'tests/test_mcp_nested_payment_request_browser.py::test_all_six_nested_preview_request_branches_exact_input_results_and_inactive_controls'], []),
        ('oneOf', ('InlineCalculation', 'SelectionReference')): ([payment, 'tests/test_mcp_calculation_variant_browser.py::test_calculation_inline_null_origin_rejections_and_saved_selection'], ['parent-owned payment-selection workspace/navigation and combined-base acceptance']),
        ('oneOf', ('ApplyPreviewRequest', 'InvoiceUpdatePreviewRequest', 'ReceivePreviewRequest', 'UnapplyPreviewRequest', 'UpdatePreviewRequest', 'VoidPreviewRequest')): (['tests/test_mcp_nested_payment_request_browser.py::test_all_six_nested_preview_request_branches_exact_input_results_and_inactive_controls'], []),
    }


def variant_key(variant):
    return (variant['combination'], tuple(sorted(
        branch.get('$ref', branch.get('type', 'any')).split('/')[-1]
        for branch in variant['branches'])))


def workbench_variant_map(rows):
    policies = variant_policies()
    grouped = {}
    for row in rows:
        if row['url'] is None:
            continue
        for variant in row['schema_variants']:
            key = variant_key(variant)
            assert key in policies, (row['command'], variant['path'], key)
            witnesses, remaining = policies[key]
            group = grouped.setdefault(key, {'combination': key[0], 'branches': key[1],
                'browser_witnesses': witnesses, 'remaining_material_cases': remaining,
                'status': 'material_cases_open' if remaining else 'shared_representation_witnesses_linked',
                'paths': []})
            group['paths'].append({'command': row['command'], 'path': variant['path'],
                                  'nested_alternative': '/oneOf/' in variant['path'],
                                  'surface': row.get('surface', 'generated_form'),
                                  'representation_limit': row.get('schema_variant_limits', 'representative schema witnesses; not individual path acceptance')})
    return list(grouped.values())


def schema_variants(schema):
    found = []
    def visit(value, path, references=()):
        if not isinstance(value, dict):
            return
        if '$ref' in value:
            ref = value['$ref']
            if ref in references:
                return
            target = schema
            for part in ref[2:].split('/'):
                target = target[part.replace('~1', '/').replace('~0', '~')]
            visit(target, path, (*references, ref))
        for branch in ('oneOf', 'anyOf'):
            if branch in value:
                found.append({'path': path, 'combination': branch, 'discriminator': value.get('discriminator'),
                              'branches': value[branch], 'browser_acceptance': 'see_material_variant_map_and_override_requirements'})
                for index, child in enumerate(value[branch]):
                    visit(child, path + '/' + branch + '/' + str(index), references)
        for name, child in value.get('properties', {}).items():
            visit(child, path + '/' + name, references)
        if 'items' in value:
            visit(value['items'], path + '/[]', references)
    visit(schema, '')
    return found


PAYMENT_WORKSPACE_WITNESSES = {
    'receive': 'tests/test_mcp_payment_form_browser.py::test_saved_selection_control_previews_real_receipt',
    'apply': 'tests/test_customer_payment_browser.py::test_receive_shared_origins_preview_unapply_reuse',
    'update': 'tests/test_customer_payment_browser.py::test_correct_receipt_and_applied_invoice_with_customs',
    'unapply': 'tests/test_customer_payment_browser.py::test_receive_shared_origins_preview_unapply_reuse',
    'void': 'tests/test_customer_payment_browser.py::test_lost_response_exact_recovery_locks_draft_and_voids_without_duplicate',
}


def payment_workspace_row(cmd, url, page_text, config):
    """Inventory actual workspace controls/state; registry alternatives stay explicit."""
    assert cmd.noun == 'payment' and cmd.verb in PAYMENT_WORKSPACE_WITNESSES
    assert config['mode'] == cmd.verb
    controls = Controls(page_text).ids
    # Closed mapping: a newly registered input path must acquire an explicit
    # workspace representation or unsupported-alternative disposition.
    mapping = {name: ('control', 'payment-' + identity, 'visible input') for name, identity in {
        'customer': 'customer', 'date': 'date', 'amount': 'amount',
        'payment_method': 'method', 'ar_account': 'ar', 'deposit_to': 'destination',
        'number': 'number', 'reference': 'reference', 'memo': 'memo',
    }.items()}
    mapping.update({
        'payment': ('captured_state', 'payment-config', 'initial.id'),
        'expected_version': ('captured_state', 'payment-config', 'initial.version'),
        'settlement_guard': ('captured_state', 'payment-config', 'initial.settlement_guard'),
        'operation_key': ('generated_state', 'payment-save', 'one retained operation key per intent'),
        'expected_facts_fingerprint': ('preview_state', 'payment-preview', 'complete preview response fingerprint retained for save'),
        'custom_fields': ('runtime_controls', 'payment-custom', 'definition-specific Keep/Set/Clear values'),
        'expected_custom_field_kinds': ('runtime_state', 'payment-custom', 'captured kinds for explicitly set values'),
        'applications.mode': ('selected_branch', 'payment-invoices', 'constant selection; no inline request from workspace'),
        'applications.selection': ('shared_state', 'payment-invoices', 'durable draft.id'),
        'applications.expected_version': ('shared_state', 'payment-invoices', 'durable draft.version'),
        'applications[].application_id': ('selected_rows', 'payment-invoices', 'selected recorded application identity'),
        'applications[].invoice_expected_version': ('captured_state', 'payment-invoices', 'selected application invoice version'),
    })
    for path in ('applications.items[].invoice', 'applications.items[].expected_version',
                 'applications.items[].amount', 'invoice_versions[].invoice', 'invoice_versions[].expected_version'):
        mapping[path] = ('registry_alternative_not_emitted', None,
                         'workspace uses durable selection or signed settlement guard; no direct input for this alternative')
    paths = []
    for field in model_fields(cmd.input_model, leaves_only=True):
        assert field.path in mapping, (cmd.name, field.path)
        representation, identity, meaning = mapping[field.path]
        if identity:
            assert len(controls.get(identity, [])) == 1, (cmd.name, field.path, identity)
        paths.append({'path': field.path, 'control_family': 'payment_workspace',
                      'schema_type': field.type, 'required': field.required, 'nullable': field.nullable,
                      'representation': representation, 'control_id': identity, 'meaning': meaning,
                      'actual_tags': controls.get(identity, []) if identity else [],
                      'browser_witness': PAYMENT_WORKSPACE_WITNESSES[cmd.verb],
                      'browser_acceptance': 'workspace_mode_linked_not_individual_schema_alternative_acceptance'})
    return {'command': cmd.name, 'url': url, 'surface': 'payment_workspace', 'mode': config['mode'],
            'input_paths': paths, 'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'schema_variant_limits': 'Registry alternatives are inventoried, not all emitted by this workspace; amounts use exact decimal text, applications use shared selections, update uses signed settlement guard.',
            'form_witness': 'tests/test_row3_host.py::test_every_routed_command_has_a_form_with_one_control_per_input_leaf',
            'context': {'encoding': 'workspace command JSON; reason header from payment-reason', 'fields': ['reason']},
            'output_policy_source': 'src/bookflow/adapters/workbench/static/payments.js:drawPreview/drawRecord',
            'error_policy_source': 'src/bookflow/adapters/workbench/static/payments.js',
            'success_policy_source': 'src/bookflow/adapters/workbench/static/payments.js:save',
            'browser_witness': PAYMENT_WORKSPACE_WITNESSES[cmd.verb]}


def command_variant_witnesses(name):
    """Authored browser branches, not acceptance of every related schema path."""
    groups = [
        ({'customer create', 'customer update'}, 'test_mcp_address_payee_browser', 'test_party_address_create_patch_clear_and_omission'),
        ({'register post', 'register update'}, 'test_mcp_address_payee_browser', 'test_top_level_register_payee_prefill_null_replacement_and_journal_destination'),
        ({'invoice post', 'invoice update'}, 'test_mcp_address_payee_browser', 'test_sales_address_default_explicit_null_and_return_to_current_default'),
        ({'invoice post', 'invoice update'}, 'test_mcp_sales_money_origin_browser', 'test_sales_line_money_object_null_rejection_and_current_default_origin'),
        ({'custom-field update'}, 'test_mcp_definition_kinds_browser', 'test_definition_all_kinds_default_empty_null_omission_and_choice_visibility'),
        ({'payment calculate'}, 'test_mcp_calculation_variant_browser', 'test_calculation_inline_null_origin_rejections_and_saved_selection'),
        ({'customer create', 'customer update', 'item create'}, 'test_mcp_any_value_browser', 'test_any_money_top_level_and_nested_json_values_and_unchanged_text'),
        ({'term create', 'term update', 'account create', 'item create', 'price-level create', 'price-level update'}, 'test_mcp_visibility_browser', 'test_command_visibility_families_exclude_old_controls_and_save_to_exact_record'),
        ({'organization new', 'organization rename', 'company new', 'company detach', 'company attach', 'chart list', 'demo reset'}, 'test_mcp_hub_destination_browser', 'test_hub_organization_and_company_new_attach_destinations_with_complete_receipts'),
    ]
    return ['tests/' + module + '.py::' + test for commands, module, test in groups if name in commands]


def workbench_row(cmd, url, page_text):
    """Actual page controls plus schema paths; interaction acceptance is separate."""
    described = forms.describe_fields(cmd.noun, cmd.verb, cmd.input_model, None, None)
    controls = Controls(page_text).named
    paths = []
    policies = workbench_family_policies()
    for field in model_fields(cmd.input_model, leaves_only=True):
        leaf = next((row for row in described if field.path == row['path'] or field.path.startswith(row['path'] + '[]')), None)
        children = [row for row in described if row['path'].startswith(field.path + '.')]
        if leaf is None and children:
            # Introspection reports a model union as one schema leaf; the
            # renderer supplies each discriminated branch as real controls.
            leaf = {'kind': 'discriminated_model', 'path': field.path}
        assert leaf is not None, (cmd.name, field.path)
        control = leaf['kind']
        actual = controls.get('f:' + field.path, [])
        if any('data-ref-value' in node for node in actual):
            control = 'reference_combobox'
        if field.path.startswith('custom_fields') or field.path.startswith('custom_field_kinds'):
            control = 'runtime_custom_fields' if 'name="cf:' in page_text else leaf['kind']
        elif cmd.noun in ('estimate', 'work-order') and cmd.verb in ('invoice', 'sales-receipt') and field.path.split('[')[0] in ('line_ids', 'selections', 'percent'):
            control = 'billing_selection'
        elif field.path == 'cursor' and cmd.name in ('report balance-sheet', 'report profit-and-loss'):
            control = 'statement_continuation'
        elif leaf.get('definition_default'):
            control = 'typed_definition_default'
        witness, limits = policies[control]
        paths.append({'path': field.path, 'control_family': control, 'schema_type': field.type,
            'required': field.required, 'nullable': field.nullable,
            'visible_when': leaf.get('visible_when'), 'choices': leaf.get('choices'),
            'rendered_children': [{'path': row['path'], 'visibility_cases': row.get('visibility_cases'),
                                   'kind': row['kind'], 'choices': row.get('choices')} for row in children],
            'actual_tags': [{'tag': node['tag'], 'type': node.get('type'), 'structured_alternative': node.get('data-math-structured')} for node in actual],
            'browser_witness': witness,
            'browser_acceptance': 'representative_family_mapped_not_individual_path_acceptance',
            'representative_limits': limits})
    return {'command': cmd.name, 'url': url, 'input_paths': paths,
            'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'command_variant_witnesses': command_variant_witnesses(cmd.name),
            'command_variant_limits': 'Authored cases at 1280/390; references are not stored results or exhaustive command acceptance.',
            'form_witness': 'tests/test_row3_host.py::test_every_routed_command_has_a_form_with_one_control_per_input_leaf',
            'context': {'encoding': 'ctx: fields to shared headers; action to dry_run',
                        'fields': [name for name in ('reason', 'source_ref', 'directive', 'idempotency_key') if 'ctx:' + name in controls]},
            'output_policy_source': 'src/bookflow/adapters/workbench/templates/form.html',
            'error_policy_source': 'src/bookflow/adapters/workbench/pages.py:submit and error.html',
            'success_policy_source': 'src/bookflow/adapters/workbench/pages.py:_success_target',
            'output_and_success_interaction': {
                'scope': 'representative shared renderer behavior; not every command success target',
                'preview_save_error': 'tests/test_mcp_workbench_control_browser.py::test_generated_boolean_false_omission_and_null_encoding',
                'read_completion_label': 'tests/test_mcp_money_gui_browser.py::test_top_level_money_object_nullable_branch_and_complete_calculation',
                'secret_output': 'tests/test_mcp_workbench_control_browser.py::test_generated_password_error_preview_and_save_never_echo_secret',
                'sale_detail_history': 'tests/test_service_sales_browser.py::test_generated_sale_preview_correct_history_and_void',
                'file_handoff': 'tests/test_mcp_file_gui_browser.py::test_installed_mcp_file_browser_and_agent_continuation',
                'nested_object_and_company_fallback_receipt': 'tests/test_mcp_nested_gui_browser.py::test_nested_collection_model_object_null_and_full_error',
                'own_detach_redirect_receipt': 'tests/test_mcp_detach_gui_browser.py::test_generated_own_detach_preview_redirect_and_full_receipt' if cmd.name == 'company detach' else None,
                'rate_destination_and_flags': 'tests/test_mcp_rate_gui_browser.py::test_rate_exact_integer_decimal_conflict_destination_noop_and_replay' if cmd.name == 'rate set' else None},
            'specialized_annotation_witness': 'tests/test_row6_workbench.py::test_real_browser_notes_files_conflicts_drafts_and_narrow_keyboard' if cmd.noun in {'note', 'attachment', 'activity'} else None}


def local_workbench_boundaries():
    return [{'command': cmd.name, 'url': None, 'boundary': 'local-only: no routed workbench command',
             'input_paths': [{'path': field.path, 'control_family': 'local_invocation', 'schema_type': field.type,
                              'browser_acceptance': 'not_applicable_local_only'}
                             for field in model_fields(cmd.input_model, leaves_only=True)],
             'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'command_variant_witnesses': command_variant_witnesses(cmd.name),
            'command_variant_limits': 'Authored cases at 1280/390; references are not stored results or exhaustive command acceptance.',
             'local_lifecycle_coverage': 'local_lifecycle_scenario',
             'local_execution_witnesses': LOCAL_VALID_WITNESSES[cmd.name],
             'hosted_rejection_witness': 'tests/test_mcp_local_boundary.py::test_installed_local_boundaries_are_explicit_and_do_not_execute'}
            for cmd in registry.all_commands(include_standalone=True) if cmd.local_only or cmd.standalone]


def workbench_family_policies():
    """Representative witnesses, with the semantics each actually exercises."""
    typed = 'tests/test_mcp_workbench_typed_default_browser.py::test_generated_boolean_default_is_a_boolean_on_preview_and_save'
    payment = 'tests/test_mcp_payment_form_browser.py::test_saved_selection_control_previews_real_receipt'
    sale = 'tests/test_service_sales_browser.py::test_generated_sale_preview_correct_history_and_void'
    return {
        'text': (typed, 'custom-field name; ordinary Unicode and error preservation remain per-command checks'),
        'number': ('tests/test_mcp_rate_gui_browser.py::test_rate_exact_integer_decimal_conflict_destination_noop_and_replay', 'generated exact rate entry; captured workspace versions are separate'),
        'choice': (typed, 'definition kind and scopes choices'),
        'reference_combobox': (sale, 'customer, account and sale-line item lookup stores exact IDs'),
        'bool': ('tests/test_mcp_workbench_control_browser.py::test_generated_boolean_false_omission_and_null_encoding', 'actual false, omitted value, rejected nonnullable clear, nullable fax clear; URL encoding, preview and persistence'),
        'collection': (typed, 'primitive scopes collection; structured sale lines have separate sale witness'),
        'typed_definition_default': (typed, 'false/true definition default preview and save'),
        'runtime_custom_fields': ('tests/test_row8_custom_field_browser.py::test_generated_preview_error_retains_attempts_after_inventory_changes', 'generated custom-field values and rejected stale inventory retain attempted values'),
        'billing_selection': ('tests/test_work_billing_browser.py::test_generic_billing_card_selects_source_before_preview', 'source selection and billing preview; selection variants remain separately covered'),
        'discriminated_model': ('tests/test_mcp_nested_payment_request_browser.py::test_all_six_nested_preview_request_branches_exact_input_results_and_inactive_controls', 'served generated nested preview request branches; dedicated payment workspace is separate'),
        'json': ('tests/test_mcp_payment_form_browser.py::test_generated_payment_json_object_error_preview_and_saved_false', 'served generated payment-preview object textarea: malformed JSON retained and false encoded; dedicated workspace separately previews/saves false with exact fingerprint'),
        'statement_continuation': ('tests/test_financial_statements_browser.py::test_statements_from_navigation_paging_and_current_ledger', 'statement paging and current-books drill-down'),
        'secret': ('tests/test_mcp_workbench_control_browser.py::test_generated_password_error_preview_and_save_never_echo_secret', 'owned password form error and preview never echo secret, save changes verified hash; other secret commands retain core permissions'),
        'local_invocation': ('tests/test_mcp_local_boundary.py::test_installed_local_boundaries_are_explicit_and_do_not_execute', 'no workbench route; execution_map links actual per-command local lifecycle witnesses separately from installed MCP/HTTP rejection'),
    }


def workbench_family_map(rows):
    """Map every rendered path to a representative, not individual-path acceptance.

    Actual run evidence is separate. Payment workspace integration and fresh
    blind-agent acceptance are not implied by this shared-control inventory.
    """
    policies = workbench_family_policies()
    grouped = {name: {'family': name, 'paths': [], 'representative_witness': witness,
                      'limits': limits, 'status': 'representative_test_mapped' if witness else 'pending_browser_or_local_witness'}
               for name, (witness, limits) in policies.items()}
    grouped['payment_workspace'] = {'family': 'payment_workspace', 'paths': [],
        'representative_witnesses': PAYMENT_WORKSPACE_WITNESSES,
        'limits': 'Mode-specific controls and captured state, not generated controls or every registry alternative.',
        'status': 'workspace_mode_witnesses_linked'}
    for row in rows:
        for field in row['input_paths']:
            family = field['control_family']
            assert family in grouped, (row['command'], field['path'], family)
            grouped[family]['paths'].append({'command': row['command'], 'path': field['path']})
    return list(grouped.values())
