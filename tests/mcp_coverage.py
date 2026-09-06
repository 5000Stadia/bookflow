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

FROZEN_COMMANDS = PAYMENT_COMMANDS | frozenset("""account activate
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
chart apply
chart list
chart show
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
report balance-sheet
report general-ledger
report profit-and-loss
report trial-balance
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
undo
unit-of-measure activate
unit-of-measure create
unit-of-measure deactivate
unit-of-measure list
unit-of-measure query
unit-of-measure show
unit-of-measure update
upgrade
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


def execution_map():
    from tests.test_mcp_registry_register import COMMANDS as REGISTER_COMMANDS
    from tests.test_mcp_registry_credentials import COMMANDS as CREDENTIAL_COMMANDS
    from tests.test_mcp_registry_presence import COMMANDS as PRESENCE_COMMANDS
    from tests.test_mcp_registry_work import FAMILIES as WORK_FAMILIES
    from tests.test_mcp_registry_work_billing import FAMILIES as BILLING_FAMILIES
    from tests.test_mcp_registry_hub_reads import FAMILIES as HUB_FAMILIES
    from tests.test_mcp_registry_supporting import FAMILIES
    from tests.test_mcp_registry_payment_preparation import FAMILIES as PAYMENT_FAMILIES
    from tests.test_mcp_registry_payments import COMMANDS as PAYMENT_FINANCIAL
    registry.load_all()
    commands = registry.all_commands(include_standalone=True)
    assert {c.name for c in commands} == FROZEN_COMMANDS, 'New or removed command needs a deliberate coverage disposition'
    permissions = {row['command']: row for row in inventory()}
    result = []
    for cmd in commands:
        witness = ('tests/test_mcp_registry_lists.py::test_each_list_lifecycle_valid_rejected_and_preview_parity' if cmd.name in LISTS else
                   'tests/test_mcp_registry_financial.py::test_financial_lifecycle_full_documents_and_ledger_parity' if cmd.name in FINANCIAL else
                   'tests/test_mcp_registry_supporting.py::test_supporting_family_full_documents_and_rejections' if any(cmd.name in names for names in FAMILIES.values()) else
                   'tests/test_mcp_registry_payment_preparation.py::test_payment_preparation_four_surface_documents_context_and_rejections' if any(cmd.name in names for names in PAYMENT_FAMILIES.values()) else
                   'tests/test_mcp_registry_payments.py::test_payment_financial_lifecycle_full_documents_and_exact_ledger' if cmd.name in PAYMENT_FINANCIAL else
                   'tests/test_mcp_registry_hub_reads.py::test_hub_read_full_documents_and_scope_boundaries' if any(cmd.name in names for names in HUB_FAMILIES.values()) else
                   'tests/test_mcp_registry_work.py::test_nonposting_work_lifecycle_full_documents_and_lineage' if any(cmd.name in names for names in WORK_FAMILIES.values()) else
                   'tests/test_mcp_registry_work_billing.py::test_work_billing_full_documents_retries_and_exact_batches' if any(cmd.name in names for names in BILLING_FAMILIES.values()) else
                   'tests/test_mcp_registry_register.py::test_register_calculate_post_correct_query_and_rejection_parity' if cmd.name in REGISTER_COMMANDS else
                   'tests/test_mcp_registry_credentials.py::test_credentials_full_documents_owned_hashes_and_rejected_state' if cmd.name in CREDENTIAL_COMMANDS else
                   'tests/test_mcp_registry_presence.py::test_advisory_presence_exact_documents_and_no_business_mutation' if cmd.name in PRESENCE_COMMANDS else None)
        mode = ('standalone_protocol' if cmd.protocol_stdout else 'standalone_local' if cmd.standalone else
                'local_lifecycle' if cmd.local_only else 'binary_' + cmd.transfer.direction if cmd.transfer else
                'advisory' if cmd.kind == 'advisory' else 'finite_poll_with_local_follow' if cmd.streams else 'routed_json')
        result.append({'command': cmd.name, 'mode': mode, 'scope': cmd.scope, 'kind': cmd.kind,
            'preview': cmd.is_write, 'idempotency_key': cmd.accepts_idempotency_key,
            'clearable': cmd.clearable, 'execution_witness': witness,
            'coverage': 'four_surface_scenario' if witness else 'pending_four_surface_or_local_lifecycle',
            'publication': permissions[cmd.name]})
    return result


class Controls(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.named = {}
        self.feed(page)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag in {'input', 'select', 'textarea'} and 'name' in attributes:
            self.named.setdefault(attributes['name'], []).append({'tag': tag, **attributes})


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
                              'branches': value[branch], 'browser_acceptance': 'pending'})
                for index, child in enumerate(value[branch]):
                    visit(child, path + '/' + branch + '/' + str(index), references)
        for name, child in value.get('properties', {}).items():
            visit(child, path + '/' + name, references)
        if 'items' in value:
            visit(value['items'], path + '/[]', references)
    visit(schema, '')
    return found


def workbench_row(cmd, url, page_text):
    """Actual page controls plus schema paths; interaction acceptance is separate."""
    described = forms.describe_fields(cmd.noun, cmd.verb, cmd.input_model, None, None)
    controls = Controls(page_text).named
    paths = []
    typed_witness = 'tests/test_mcp_workbench_typed_default_browser.py::test_generated_boolean_default_is_a_boolean_on_preview_and_save'
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
        witness = typed_witness if cmd.name in {'custom-field create', 'custom-field update'} and field.path in {'default', 'scopes', 'name'} else None
        paths.append({'path': field.path, 'control_family': control, 'schema_type': field.type,
            'required': field.required, 'nullable': field.nullable,
            'visible_when': leaf.get('visible_when'), 'choices': leaf.get('choices'),
            'rendered_children': [{'path': row['path'], 'visibility_cases': row.get('visibility_cases'),
                                   'kind': row['kind'], 'choices': row.get('choices')} for row in children],
            'actual_tags': [{'tag': node['tag'], 'type': node.get('type')} for node in actual],
            'browser_witness': witness,
            'browser_acceptance': 'representative_typed_default_case' if witness else 'pending_complete_family_mapping'})
    return {'command': cmd.name, 'url': url, 'input_paths': paths,
            'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
            'form_witness': 'tests/test_row3_host.py::test_every_routed_command_has_a_form_with_one_control_per_input_leaf',
            'context': {'encoding': 'ctx: fields to shared headers; action to dry_run',
                        'fields': [name for name in ('reason', 'source_ref', 'directive', 'idempotency_key') if 'ctx:' + name in controls]},
            'output_policy_source': 'src/bookflow/adapters/workbench/templates/form.html',
            'error_policy_source': 'src/bookflow/adapters/workbench/pages.py:submit and error.html',
            'success_policy_source': 'src/bookflow/adapters/workbench/pages.py:_success_target',
            'output_and_success_interaction': 'pending_complete_family_mapping',
            'specialized_annotation_witness': 'tests/test_row6_workbench.py::test_real_browser_notes_files_conflicts_drafts_and_narrow_keyboard' if cmd.noun in {'note', 'attachment', 'activity'} else None}


def local_workbench_boundaries():
    return [{'command': cmd.name, 'url': None, 'boundary': 'local-only: no routed workbench command',
             'input_paths': [{'path': field.path, 'control_family': 'local_invocation', 'schema_type': field.type,
                              'browser_acceptance': 'not_applicable_local_only'}
                             for field in model_fields(cmd.input_model, leaves_only=True)],
             'schema_variants': schema_variants(cmd.input_model.model_json_schema()),
             'local_lifecycle_coverage': 'pending_complete_matrix'}
            for cmd in registry.all_commands(include_standalone=True) if cmd.local_only or cmd.standalone]


def workbench_family_map(rows):
    """Group every rendered path, retaining explicit representative-test limits.

    A linked test is a witness location, not a claim that every path or every
    variant has been exercised in a browser. Run evidence is recorded separately.
    """
    typed = 'tests/test_mcp_workbench_typed_default_browser.py::test_generated_boolean_default_is_a_boolean_on_preview_and_save'
    payment = 'tests/test_mcp_payment_form_browser.py::test_saved_selection_control_previews_real_receipt'
    sale = 'tests/test_service_sales_browser.py::test_generated_sale_preview_correct_history_and_void'
    policies = {
        'text': (typed, 'custom-field name; ordinary Unicode and error preservation remain per-command checks'),
        'number': (payment, 'saved-selection expected_version; integer and decimal models keep their own constraints'),
        'choice': (typed, 'definition kind and scopes choices'),
        'reference_combobox': (sale, 'customer, account and sale-line item lookup stores exact IDs'),
        'bool': (None, 'explicit generated boolean/nullable/omitted browser witness still required'),
        'collection': (typed, 'primitive scopes collection; structured sale lines have separate sale witness'),
        'typed_definition_default': (typed, 'false/true definition default preview and save'),
        'runtime_custom_fields': ('tests/test_row8_custom_field_browser.py::test_generated_preview_error_retains_attempts_after_inventory_changes', 'generated custom-field values and rejected stale inventory retain attempted values'),
        'billing_selection': ('tests/test_work_billing_browser.py::test_generic_billing_card_selects_source_before_preview', 'source selection and billing preview; selection variants remain separately covered'),
        'discriminated_model': (payment, 'saved-selection branch; all inactive and nested branches require their individual witnesses'),
        'json': (None, 'payment custom-field object editor requires actual browser encoding witness'),
        'statement_continuation': ('tests/test_financial_statements_browser.py::test_statements_from_navigation_paging_and_current_ledger', 'statement paging and current-books drill-down'),
        'secret': (None, 'owned password form requires actual browser secret-output policy witness'),
        'local_invocation': (None, 'no workbench route; local lifecycle and MCP rejection are distinct contracts'),
    }
    grouped = {name: {'family': name, 'paths': [], 'representative_witness': witness,
                      'limits': limits, 'status': 'representative_test_mapped' if witness else 'pending_browser_or_local_witness'}
               for name, (witness, limits) in policies.items()}
    for row in rows:
        for field in row['input_paths']:
            family = field['control_family']
            assert family in grouped, (row['command'], field['path'], family)
            grouped[family]['paths'].append({'command': row['command'], 'path': field['path']})
    return list(grouped.values())
