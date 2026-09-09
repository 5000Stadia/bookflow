"""Closed public deposit detail field inventory (owning plan G7).

Every model reachable from the private reader outputs ``DepositShow`` and
``DepositItemPage`` appears here with its complete field set, and every field
carries exactly one disposition. ``conform`` enumerates the reachable closure at
runtime and requires exact equality in both directions: an unknown model, an
unknown field, a removed field, an unnamed public field or an unknown
disposition fails regardless of spelling.

Dispositions
------------
``disclosed``        The value reaches the public wire.
``reference_group``  The value reaches the wire only inside a governed reference
                     group, which is nulled as a whole when this reader is not
                     admitted to its list resource. Financial amounts on the
                     same row are never dropped with it.
``projected``        A nested private model that is never forwarded as an
                     object; its own fields carry the dispositions.
``derived``          The public value is computed from this field and audience
                     state; the private value itself is not copied.
``private``          Never leaves the reader. A reason is required.
"""
import typing

from pydantic import BaseModel

from bookflow.core.errors import BookflowError

DISPOSITIONS = ('disclosed', 'reference_group', 'projected', 'derived', 'private')


def _roots():
    from bookflow.company.deposit_read_models import DepositShow, DepositItemPage, DepositPage
    return (DepositShow, DepositItemPage, DepositPage)


def _public_roots():
    from bookflow.company.deposit_public_models import DepositDetail, DepositItemsPage, DepositQueryPage
    return (DepositDetail, DepositItemsPage, DepositQueryPage)


# Public fields with no private counterpart: wire constants, audience-derived
# availability, and continuation material minted in the public domain.
PUBLIC_CONSTRUCTED = {
    'DepositQueryPage.schema_version': 'Public query contract version.',
    'DepositQueryPage.company_id': 'Authenticated selected company.',
    'DepositQueryPage.currency': 'Selected company home currency.',
    'DepositQueryRow.received_from': 'Curated governed payer and additional-party references, from CashSource.profile and Additional.dimensions; no other profile fields.',

    'PartyReference.disclosed': 'Audience admission for the party list resource.',
    'ClassReference.group': 'Constant wire group name.',
    'ClassReference.disclosed': 'Audience admission for the class list resource.',
    'PaymentMethodReference.group': 'Constant wire group name.',
    'PaymentMethodReference.disclosed': 'Audience admission for the payment-method list resource.',
    'AccountReference.group': 'Constant wire group name.',
    'AccountReference.disclosed': 'Audience admission for the account list resource.',
    'SourceAccountReference.group': 'Constant wire group name.',
    'SourceAccountReference.disclosed': 'Audience admission for the account list resource.',
    'IssuerIdentity.group': 'Constant wire group name.',
    'IssuerIdentity.disclosed': 'Audience admission for the company resource.',
    'CustomFieldValue.group': 'Constant wire group name.',
    'CustomFieldValue.disclosed': 'Audience admission for the custom-field resource.',
    'AnnotationAccess.notes': 'Capability-derived availability, decided before any association read.',
    'AnnotationAccess.attachments': 'Capability-derived availability, decided before any association read.',
    'DepositDetail.annotations': 'Capability-derived annotation availability (owning plan G3).',
    'RevisionLink.selected': 'True for the revision this request selected.',
    'RevisionLink.current': 'True for the revision the document currently holds.',
    'SourceRow.row': 'Wire discriminator for the composition row family.',
    'AdditionalRow.row': 'Wire discriminator for the composition row family.',
    'AllocationRow.row': 'Wire discriminator for the composition row family.',
    'DepositItemsPage.schema_version': 'Public contract version, independent of the private reader.',
    'DepositItemsPage.company_id': 'The company this request selected.',
    'DepositItemsPage.fingerprint': 'Minted over the public projected page in the deposit-public domain.',
    'DepositItemsPage.next_cursor': 'Minted over the public projected page in the deposit-public domain.',
}

_G = 'reference_group'
_D = 'disclosed'
_P = 'private'
_R = 'derived'
_N = 'projected'

FIELDS: dict[str, dict[str, tuple]] = {
    'deposit_read_models.DepositRow': {
        'selected': (_D, ('DepositQueryRow.selected',), ''),
        'current': (_D, ('DepositQueryRow.current',), ''),
        'totals': (_D, ('DepositQueryRow.totals',), ''),
        'counts': (_D, ('DepositQueryRow.counts',), ''),
    },
    'deposit_read_models.DepositPage': {
        'items': (_D, ('DepositQueryPage.items',), ''),
        'total_count': (_D, ('DepositQueryPage.total_count',), ''),
        'totals': (_D, ('DepositQueryPage.totals',), ''),
        'effective_bank_total': (_D, ('DepositQueryPage.effective_bank_total',), ''),
        'fingerprint': (_R, ('DepositQueryPage.fingerprint',), 'Reissued over complete public matching relation and semantic filters.'),
        'next_cursor': (_R, ('DepositQueryPage.next_cursor',), 'Reissued in public.query domain.'),
        'previous_cursor': (_R, ('DepositQueryPage.previous_cursor',), 'Reissued in public.query domain.'),
    },
    # ------------------------------------------------------------- show root
    'deposit_read_models.DepositShow': {
        'schema_version': (_R, ('DepositDetail.schema_version',), 'The public contract carries its own version.'),
        'company_id': (_D, ('DepositDetail.company_id',), ''),
        'currency': (_D, ('DepositDetail.currency',), ''),
        'selected': (_D, ('DepositDetail.selected',), ''),
        'selected_is_current': (_D, ('DepositDetail.selected_is_current',), ''),
        'current': (_D, ('DepositDetail.current',), ''),
        'current_observed_at': (_D, ('DepositDetail.current_observed_at',), ''),
        'totals': (_D, ('DepositDetail.totals',), ''),
        'counts': (_D, ('DepositDetail.counts',), ''),
        'fingerprints': (_P, (), 'Private-domain HMACs over captured composition, including undisclosed fields.'),
        'dated_state': (_D, ('DepositDetail.dated_state',), ''),
        'links': (_R, ('DepositDetail.revisions', 'DepositDetail.links'),
                  'Only admitted, actually callable destinations are emitted; operation, draft, '
                  'selection and bank-version links have no registered public command in this stage.'),
        'current_references': (_R, ('DepositDetail.current_references',),
                               'Reduced to the masters this bounded summary itself names, then to the '
                               'reference groups this audience is admitted to.'),
        'dependencies': (_R, ('DepositDetail.inspection',),
                         'The private inspection guard stays internal; only an audience-safe status is derived.'),
    },
    'deposit_read_models.Selected': {
        'pin': (_D, ('SelectedHeader.pin',), ''),
        'date': (_D, ('SelectedHeader.date',), ''),
        'number': (_D, ('SelectedHeader.number',), ''),
        'memo': (_D, ('SelectedHeader.memo',), ''),
        'issuer': (_G, ('SelectedHeader.issuer',), ''),
        'custom_fields': (_G, ('SelectedHeader.custom_fields',), ''),
        'deposit_to': (_G, ('SelectedHeader.deposit_to',), ''),
        'cash_back': (_D, ('SelectedHeader.cash_back',), ''),
    },
    'deposit_read_models.Pin': {
        'deposit': (_D, ('DepositPin.deposit_id', 'DepositDetail.deposit_id', 'DepositItemsPage.deposit_id'), ''),
        'revision_id': (_D, ('DepositPin.revision_id',), ''),
        'revision_number': (_D, ('DepositPin.revision_number', 'RevisionLink.revision_number'), ''),
    },
    'deposit_read_models.Issuer': {
        'id': (_G, ('IssuerIdentity.id',), ''),
        'display_name': (_G, ('IssuerIdentity.display_name',), ''),
        'legal_name': (_G, ('IssuerIdentity.legal_name',), ''),
        'home_currency': (_G, ('IssuerIdentity.home_currency',), ''),
        'address_line1': (_G, ('IssuerIdentity.address_line1',), ''),
        'address_line2': (_G, ('IssuerIdentity.address_line2',), ''),
        'address_city': (_G, ('IssuerIdentity.address_city',), ''),
        'address_state': (_G, ('IssuerIdentity.address_state',), ''),
        'address_postal_code': (_G, ('IssuerIdentity.address_postal_code',), ''),
        'address_country': (_G, ('IssuerIdentity.address_country',), ''),
        'legal_address_line1': (_G, ('IssuerIdentity.legal_address_line1',), ''),
        'legal_address_line2': (_G, ('IssuerIdentity.legal_address_line2',), ''),
        'legal_address_city': (_G, ('IssuerIdentity.legal_address_city',), ''),
        'legal_address_state': (_G, ('IssuerIdentity.legal_address_state',), ''),
        'legal_address_postal_code': (_G, ('IssuerIdentity.legal_address_postal_code',), ''),
        'legal_address_country': (_G, ('IssuerIdentity.legal_address_country',), ''),
        'ship_address_line1': (_G, ('IssuerIdentity.ship_address_line1',), ''),
        'ship_address_line2': (_G, ('IssuerIdentity.ship_address_line2',), ''),
        'ship_address_city': (_G, ('IssuerIdentity.ship_address_city',), ''),
        'ship_address_state': (_G, ('IssuerIdentity.ship_address_state',), ''),
        'ship_address_postal_code': (_G, ('IssuerIdentity.ship_address_postal_code',), ''),
        'ship_address_country': (_G, ('IssuerIdentity.ship_address_country',), ''),
    },
    'deposit_read_models.CustomValue': {
        'captured': (_N, (), 'The captured snapshot field carries its own dispositions.'),
        'captured_print_visibility': (_G, ('CustomFieldValue.print_visibility',), ''),
    },
    'journal_custom_fields.SnapshotField': {
        'definition_id': (_G, ('CustomFieldValue.definition_id',), ''),
        'value_id': (_P, (), 'Stored custom value row identity; a physical record id, not a business fact.'),
        'name': (_G, ('CustomFieldValue.name',), ''),
        'kind': (_G, ('CustomFieldValue.kind',), ''),
        'value': (_G, ('CustomFieldValue.value',), ''),
        'canonical_text': (_G, ('CustomFieldValue.canonical_text',), ''),
        'definition_version': (_G, ('CustomFieldValue.definition_version',), ''),
        'position': (_G, ('CustomFieldValue.position',), ''),
        'choice_id': (_G, ('CustomFieldValue.choice_id',), ''),
        'choice_label': (_G, ('CustomFieldValue.choice_label',), ''),
    },
    'deposit_models.Account': {
        'id': (_G, ('AccountReference.id',), ''),
        'name': (_G, ('AccountReference.name',), ''),
        'full_name': (_G, ('AccountReference.full_name',), ''),
        'number': (_G, ('AccountReference.number',), ''),
        'normal_balance': (_G, ('AccountReference.normal_balance',), ''),
        'type': (_G, ('AccountReference.account_type',), ''),
        'system_role': (_G, ('AccountReference.system_role',), ''),
        'active': (_G, ('AccountReference.active',), ''),
        'currency': (_G, ('AccountReference.currency',), ''),
    },
    'deposit_models.CashBack': {
        'account': (_G, ('CashBackLine.account',), ''),
        'units': (_D, ('CashBackLine.amount',), ''),
        'memo': (_D, ('CashBackLine.memo',), ''),
    },
    'deposit_lifecycle_models.DocumentState': {
        'id': (_D, ('CurrentState.deposit_id',), ''),
        'version': (_D, ('CurrentState.version',), ''),
        'revision_id': (_D, ('CurrentState.revision_id',), ''),
        'number': (_D, ('CurrentState.number',), ''),
        'status': (_D, ('CurrentState.status',), ''),
        'revision_date': (_D, ('CurrentState.revision_date',), ''),
        'currency': (_D, ('CurrentState.effective_bank_total',),
                     'Supplies the currency of every current-revision money field.'),
        'revision_posting_total': (_D, ('CurrentState.revision_posting_total',), ''),
        'revision_subtotal': (_D, ('CurrentState.revision_subtotal',), ''),
        'revision_bank_total': (_D, ('CurrentState.revision_bank_total',), ''),
        'revision_cash_back': (_D, ('CurrentState.revision_cash_back',), ''),
        'effective_bank_total': (_D, ('CurrentState.effective_bank_total',), ''),
        'active_source_ids': (_R, ('CurrentState.active_source_count',),
                              'Composition-sized: the bounded summary carries the count, and the '
                              'identities travel beside their rows on the sources item page.'),
    },
    'deposit_read_models.Totals': {
        'posting_total': (_D, ('DepositTotals.posting_total',), ''),
        'source_total': (_D, ('DepositTotals.source_total',), ''),
        'positive_additional_total': (_D, ('DepositTotals.positive_additional_total',), ''),
        'negative_additional_total': (_D, ('DepositTotals.negative_additional_total',), ''),
        'subtotal': (_D, ('DepositTotals.subtotal',), ''),
        'cash_back': (_D, ('DepositTotals.cash_back',), ''),
        'bank_total': (_D, ('DepositTotals.bank_total',), ''),
    },
    'deposit_models.SignedMoney': {
        'minor_units': (_D, ('Money.minor_units',), ''),
        'currency': (_D, ('Money.currency',), ''),
    },
    'deposit_read_models.Counts': {
        'sources': (_D, ('DepositCounts.sources',), ''),
        'additional': (_D, ('DepositCounts.additional',), ''),
        'cash_allocations': (_D, ('DepositCounts.cash_allocations',), ''),
        'components': (_D, ('DepositCounts.components',), ''),
    },
    'deposit_read_models.DatedState': {
        'as_of': (_D, ('DatedFinancialState.as_of',), ''),
        'basis': (_D, ('DatedFinancialState.basis',), ''),
        'knowledge_observed_at': (_D, ('DatedFinancialState.knowledge_observed_at',), ''),
        'cutoff_after_evaluation_date': (_D, ('DatedFinancialState.cutoff_after_evaluation_date',), ''),
        'financial_state': (_D, ('DatedFinancialState.financial_state',), ''),
        'bank_movement': (_D, ('DatedFinancialState.bank_movement',), ''),
        'source_membership_total': (_D, ('DatedFinancialState.source_membership_total',), ''),
    },
    'deposit_read_models.EvidenceLink': {
        'kind': (_R, ('AnnotationLink.kind',),
                 'Selects the destination family; only note and attachment associations are callable here.'),
        'id': (_R, ('AnnotationLink.id', 'RevisionLink.revision_id'), ''),
        'related_id': (_R, ('AnnotationLink.attachment_id',), ''),
        'active': (_R, ('AnnotationLink.active',), ''),
        'label': (_P, (), 'Carries the internal operation recovery key for operation links; attachment '
                          'captions are read through the attachment commands, never from this field.'),
    },
    'deposit_read_models.Navigation': {
        'current_type': (_G, ('CurrentReference.current_type',), ''),
        'kind': (_R, ('CurrentReference.group',), 'Mapped through the closed navigation kind to resource table.'),
        'id': (_G, ('CurrentReference.id',), ''),
        'label': (_G, ('CurrentReference.label',), ''),
        'active': (_G, ('CurrentReference.active',), ''),
        'version': (_G, ('CurrentReference.version',), ''),
        'available': (_G, ('CurrentReference.available',), ''),
    },
    'deposit_read_models.DependencySummary': {
        'purpose': (_D, ('InspectionSummary.purpose',), ''),
        'source_ids': (_R, ('InspectionSummary.source_count',),
                       'Composition-sized: the bounded summary carries the count, and the identities '
                       'travel beside their rows on the sources item page.'),
        'guard': (_P, (), 'BaselineRecipe token. Its read_digest and endpoint derive from readset anchors this '
                          'reader may not see, so publishing it or any hash of it is a disclosure side channel.'),
        'history': (_R, ('InspectionSummary.history',),
                    'Recomputed from unknown records this reader is admitted to; denied optional reference '
                    'history never changes the public status.'),
    },
    # ------------------------------------------------------------ items root
    'deposit_read_models.DepositItemPage': {
        'selected': (_D, ('DepositItemsPage.selected',), ''),
        'kind': (_D, ('DepositItemsPage.kind',), ''),
        'items': (_D, ('DepositItemsPage.items',), ''),
        'total_count': (_D, ('DepositItemsPage.total_count',), ''),
        'totals': (_D, ('DepositItemsPage.totals',), ''),
        'fingerprint': (_P, (), 'Private-domain HMAC over captured values including undisclosed fields.'),
        'next_cursor': (_P, (), 'Private-domain continuation; the public page mints its own over the '
                                'disclosed relation in a distinct deposit-public domain.'),
        'current': (_D, ('DepositItemsPage.current',), ''),
        'current_observed_at': (_D, ('DepositItemsPage.current_observed_at',), ''),
        'current_references': (_R, ('DepositItemsPage.current_references',),
                               'Reduced to the masters the emitted rows of this page name, then to the '
                               'reference groups this audience is admitted to.'),
    },
    'deposit_models.Additional': {
        'row_id': (_D, ('AdditionalRow.row_id',), ''),
        'ordinal': (_D, ('AdditionalRow.ordinal',), ''),
        'account': (_G, ('AdditionalRow.account',), ''),
        'units': (_D, ('AdditionalRow.amount',), 'Signed; negative additional rows stay represented.'),
        'dimensions': (_N, ('AdditionalRow.party', 'AdditionalRow.class_reference'),
                       'Party and class dimensions carry their own dispositions.'),
        'memo': (_D, ('AdditionalRow.memo',), ''),
        'check_number': (_D, ('AdditionalRow.check_number',), ''),
        'payment_method': (_G, ('AdditionalRow.payment_method',), ''),
    },
    'deposit_models.Dimensions': {
        'party_kind': (_R, ('PartyReference.group',), 'Closed party kind to list resource mapping.'),
        'party_id': (_G, ('PartyReference.id',), ''),
        'party_name': (_G, ('PartyReference.label',), ''),
        'class_id': (_G, ('ClassReference.id',), ''),
        'class_name': (_G, ('ClassReference.label',), ''),
    },
    'sales_facts.Reference': {
        'id': (_G, ('PaymentMethodReference.id', 'PartyReference.id'), ''),
        'label': (_G, ('PaymentMethodReference.label', 'PartyReference.label'), ''),
        'version': (_G, ('PaymentMethodReference.version', 'PartyReference.version'), ''),
    },
    'deposit_read_models.CellItem': {
        'id': (_P, (), 'Stored deposit_cash_cells row identity; a physical record id.'),
        'captured': (_N, (), 'The captured cell carries its own dispositions.'),
        'component_id': (_P, (), 'Stored deposit_components row identity; a physical record id.'),
        'bucket_row_id': (_P, (), 'Raw stored bucket row identity; the public row derives its additional '
                                  'business row identity from the captured bucket instead.'),
    },
    'deposit_models.Cell': {
        'row_id': (_D, ('AllocationRow.row_id',), ''),
        'component_ordinal': (_D, ('AllocationRow.component_ordinal',), ''),
        'bucket': (_R, ('AllocationRow.bucket', 'AllocationRow.additional_row_id'),
                   'Split into a closed bucket name and, for an additional bucket, its business row identity.'),
        'units': (_D, ('AllocationRow.amount',), ''),
    },
    'deposit_read_models.SourceItem': {
        'captured': (_N, (), 'The captured source row carries its own dispositions.'),
        'source_number': (_D, ('SourcePin.number',), ''),
        'from_account': (_G, ('SourceRow.from_account',), ''),
        'membership_ids': (_P, (), 'Deposit membership row identities; physical record ids.'),
        'current': (_N, ('SourceRow.current',), 'Current receipt facts carry their own dispositions.'),
    },
    'deposit_models.SourceRow': {
        'row_id': (_D, ('SourceRow.row_id',), ''),
        'ordinal': (_D, ('SourceRow.ordinal',), ''),
        'source': (_N, ('SourceRow.source',), 'The captured receipt carries its own dispositions.'),
        'occurrences': (_R, ('SourceRow.components',),
                        'Stable component ordinals the cash allocations refer to.'),
        'memo': (_D, ('SourceRow.memo',), ''),
        'memo_origin': (_D, ('SourceRow.memo_origin',), ''),
    },
    'deposit_models.CashSource': {
        'source_type': (_D, ('SourcePin.source_type',), ''),
        'transaction_id': (_D, ('SourcePin.transaction_id',), ''),
        'expected_header_version': (_D, ('SourcePin.expected_header_version',), ''),
        'revision_id': (_D, ('SourcePin.revision_id',), ''),
        'business_batch_id': (_P, (), 'Posting batch identity; a validation input, not a deposit row fact.'),
        'receipt_date': (_D, ('SourceRow.receipt_date',), ''),
        'currency': (_D, ('SourceRow.amount',), ''),
        'cash_minor_units': (_D, ('SourceRow.amount',), ''),
        'uf_account': (_G, ('SourceRow.from_account',),
                       'The same undeposited-funds account the reader validates across contributing '
                       'posting lines and discloses as the captured from-account.'),
        'source_memo': (_D, ('SourceRow.source_memo',), ''),
        'source_reference': (_D, ('SourceRow.check_reference',), ''),
        'profile': (_N, (), 'Never forwarded whole; only the payer and payment-method groups survive.'),
        'semantic_presence': (_P, (), 'Declared component keys; a validation input, not a public field.'),
        'components': (_R, ('SourceRow.allocation_parties', 'SourceRow.allocation_classes'),
                       'Only the distinct cash party and class dimensions are projected.'),
        'dependencies': (_P, (), 'Internal financial dependency identities retained for reconstruction.'),
    },
    'payment_outputs.PaymentProfileOutput': {
        'schema_version': (_P, (), 'Private capture format version.'),
        'payer': (_G, ('SourceRow.payer',), ''),
        'lineage': (_P, (), 'Prior payer identities; each needs its own admission and is not a deposit fact.'),
        'billing_address': (_P, (), 'Payer billing address; explicitly excluded by the owning plan.'),
        'ar_account': (_P, (), 'Receivable posting account of the receipt, not a deposit row fact.'),
        'deposit_account': (_P, (), 'Receipt-side preferred account, distinct from the captured from-account.'),
        'payment_method': (_G, ('SourceRow.payment_method',), ''),
        'preferences': (_P, (), 'Company payment preferences captured on the receipt.'),
    },
    'sales_facts.Account': {
        'id': (_G, ('SourceAccountReference.id',), ''),
        'name': (_G, ('SourceAccountReference.name',), ''),
        'full_name': (_G, ('SourceAccountReference.full_name',), ''),
        'number': (_G, ('SourceAccountReference.number',), ''),
        'type': (_G, ('SourceAccountReference.account_type',), ''),
        'normal_balance': (_G, ('SourceAccountReference.normal_balance',), ''),
    },
    'payment_outputs.PaymentPreferencesOutput': {
        'automatically_apply_payments': (_P, (), 'Company preference captured on the receipt.'),
        'automatically_calculate_payments': (_P, (), 'Company preference captured on the receipt.'),
        'use_undeposited_funds_for_payments': (_P, (), 'Company preference captured on the receipt.'),
    },
    'sales_facts.SalesProfile': {
        'schema_version': (_P, (), 'Private capture format version.'),
        'sales_tax_calculation': (_P, (), 'Receipt tax policy; not a deposit cash fact.'),
        'tax_policy_origin': (_P, (), 'Receipt tax policy provenance.'),
        'customer': (_G, ('SourceRow.payer',), 'The captured payer for a sales receipt row.'),
        'preferences': (_P, (), 'Company sales preferences captured on the receipt.'),
        'billing_address': (_P, (), 'Customer billing address; explicitly excluded by the owning plan.'),
        'shipping_address': (_P, (), 'Customer shipping address; explicitly excluded by the owning plan.'),
        'shipping_address_id': (_P, (), 'Customer address identity; explicitly excluded by the owning plan.'),
        'terms': (_P, (), 'Receipt payment terms; not a deposit row fact.'),
        'ship_date': (_P, (), 'Receipt shipping date; not a deposit row fact.'),
        'ship_method': (_P, (), 'Receipt ship method; not a deposit row fact.'),
        'sales_rep': (_P, (), 'Receipt sales rep; not a deposit row fact.'),
        'class_id': (_P, (), 'Receipt header class; the deposit discloses the cash allocation classes.'),
        'customer_tax_code': (_P, (), 'Receipt tax code; not a deposit row fact.'),
        'sales_tax_item': (_P, (), 'Receipt tax item; not a deposit row fact.'),
        'tax_rules': (_P, (), 'Receipt tax rules; not a deposit row fact.'),
        'price_level': (_P, (), 'Receipt price level; not a deposit row fact.'),
        'customer_message': (_P, (), 'Receipt customer message; not a deposit row fact.'),
        'customer_message_item': (_P, (), 'Receipt customer message list record; not a deposit row fact.'),
        'customer_purchase_order': (_P, (), 'Receipt purchase order; not a deposit row fact.'),
        'origins': (_P, (), 'Per-field default provenance of the receipt header.'),
        'control_account': (_P, (), 'Receipt control account; not a deposit row fact.'),
        'due_date': (_P, (), 'Receipt due date; not a deposit row fact.'),
        'discount_date': (_P, (), 'Receipt discount date; not a deposit row fact.'),
        'discount_available': (_P, (), 'Receipt discount availability; not a deposit row fact.'),
        'payment_method': (_G, ('SourceRow.payment_method',), ''),
        'payment_reference': (_P, (), 'The receipt-side reference; the disclosed deposit row value is the '
                                      'captured source reference.'),
    },
    'tax_policy.TaxOrigin': {
        'kind': (_P, (), 'Receipt tax policy provenance.'),
        'source_id': (_P, (), 'Receipt tax policy provenance.'),
    },
    'sales_facts.Customer': {
        'id': (_G, ('PartyReference.id',), ''),
        'label': (_G, ('PartyReference.label',), ''),
        'version': (_G, ('PartyReference.version',), ''),
        'company_name': (_P, (), 'Customer contact profile; not a deposit row fact.'),
        'salutation': (_P, (), 'Customer contact profile; not a deposit row fact.'),
        'first_name': (_P, (), 'Customer contact profile; not a deposit row fact.'),
        'middle_name': (_P, (), 'Customer contact profile; not a deposit row fact.'),
        'last_name': (_P, (), 'Customer contact profile; not a deposit row fact.'),
        'email': (_P, (), 'Customer contact profile; not a deposit row fact.'),
        'phone': (_P, (), 'Customer contact profile; not a deposit row fact.'),
        'resale_number': (_P, (), 'Customer tax profile; not a deposit row fact.'),
    },
    'sales_facts.Preferences': {
        'sales_tax_enabled': (_P, (), 'Company preference captured on the receipt.'),
        'sales_tax_liability_basis': (_P, (), 'Company preference captured on the receipt.'),
        'enable_price_levels': (_P, (), 'Company preference captured on the receipt.'),
        'use_classes': (_P, (), 'Company preference captured on the receipt.'),
        'prompt_for_class': (_P, (), 'Company preference captured on the receipt.'),
        'units_of_measure_mode': (_P, (), 'Company preference captured on the receipt.'),
    },
    'sales_models.Address': {
        'line1': (_P, (), 'Party address; explicitly excluded by the owning plan.'),
        'line2': (_P, (), 'Party address; explicitly excluded by the owning plan.'),
        'city': (_P, (), 'Party address; explicitly excluded by the owning plan.'),
        'state': (_P, (), 'Party address; explicitly excluded by the owning plan.'),
        'postal_code': (_P, (), 'Party address; explicitly excluded by the owning plan.'),
        'country': (_P, (), 'Party address; explicitly excluded by the owning plan.'),
    },
    'sales_facts.Term': {
        'id': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'label': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'version': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'kind': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'due_days': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'discount_days': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'due_day_of_month': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'due_next_month_if_within_days': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'discount_day_of_month': (_P, (), 'Receipt terms; not a deposit row fact.'),
        'discount_percent_millionths': (_P, (), 'Receipt terms; not a deposit row fact.'),
    },
    'sales_facts.TaxCode': {
        'id': (_P, (), 'Receipt tax code; not a deposit row fact.'),
        'label': (_P, (), 'Receipt tax code; not a deposit row fact.'),
        'version': (_P, (), 'Receipt tax code; not a deposit row fact.'),
        'taxable': (_P, (), 'Receipt tax code; not a deposit row fact.'),
    },
    'sales_facts.TaxRule': {
        'id': (_P, (), 'Receipt tax rule; not a deposit row fact.'),
        'label': (_P, (), 'Receipt tax rule; not a deposit row fact.'),
        'version': (_P, (), 'Receipt tax rule; not a deposit row fact.'),
        'rate_percent_millionths': (_P, (), 'Receipt tax rule; not a deposit row fact.'),
        'agency': (_P, (), 'Receipt tax agency; not a deposit row fact.'),
        'liability_account': (_P, (), 'Receipt tax liability account; not a deposit row fact.'),
    },
    'sales_facts.Origin': {
        'kind': (_P, (), 'Per-field default provenance of the receipt.'),
        'source_id': (_P, (), 'Per-field default provenance of the receipt.'),
    },
    'deposit_models.SemanticKey': {
        'kind': (_R, ('SourceComponent.kind',), ''),
        'identity': (_P, (), 'Internal component or document line identity behind the semantic key.'),
        'tax_item': (_P, (), 'Tax item identity; needs its own item admission and is not a deposit cash fact.'),
    },
    'deposit_models.CashComponent': {
        'key': (_N, (), 'The semantic key carries its own dispositions.'),
        'capacity': (_P, (), 'Component funding capacity; the disclosed split is the cash allocation amount.'),
        'document_line_id': (_P, (), 'Physical document line identity.'),
        'posting_line_id': (_P, (), 'Physical posting line identity.'),
        'posting_source_id': (_P, (), 'Physical posting source identity.'),
        'physical_component_id': (_P, (), 'Physical component identity.'),
        'cash': (_N, (), 'Cash party and class dimensions carry their own dispositions.'),
        'credit_owner_party': (_P, (), 'Credit ownership party; needs explicit business necessity and its own '
                                       'reference admission before any public exposure.'),
        'credit_owner_ar': (_P, (), 'Credit ownership receivable account; same disposition as the party.'),
        'sale_line': (_P, (), 'Whole sales line profile; explicitly excluded by the owning plan.'),
        'tax': (_P, (), 'Sales tax component snapshot; explicitly excluded by the owning plan.'),
    },
    'sales_facts.SalesLineProfile': {
        'schema_version': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'item': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'item_type': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'income_account': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'unit': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'class_id': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'tax_code': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'price_rule': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'standard_price_minor_units': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'cost_minor_units': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'price_basis_minor_units': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'origins': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'pricing_basis': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'net_amount_minor_units': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
        'allocation_proof': (_P, (), 'Sales line profile; explicitly excluded by the owning plan.'),
    },
    'sales_facts.Unit': {
        'id': (_P, (), 'Sales line unit; not a deposit cash fact.'),
        'label': (_P, (), 'Sales line unit; not a deposit cash fact.'),
        'version': (_P, (), 'Sales line unit; not a deposit cash fact.'),
        'set_id': (_P, (), 'Sales line unit; not a deposit cash fact.'),
        'abbreviation': (_P, (), 'Sales line unit; not a deposit cash fact.'),
        'factor_nanounits': (_P, (), 'Sales line unit; not a deposit cash fact.'),
    },
    'sales_facts.PriceRule': {
        'id': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'label': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'version': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'kind': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'currency': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'rounding_mode': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'increment_minor_units': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'offset_minor_units': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'percent_millionths': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'fixed_minor_units': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'adjustment_basis': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
        'matched': (_P, (), 'Sales line pricing rule; not a deposit cash fact.'),
    },
    'billing_facts.AllocationProof': {
        'source_document_id': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'source_revision_id': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'source_line_id': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'root_document_id': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'root_line_id': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'source_basis_hash': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'quoted_quantity_microunits': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'quoted_base_quantity_microunits': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'quoted_net_minor_units': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'denominator': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'spans': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
    },
    'billing_facts.AllocationSpan': {
        'start': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
        'end': (_P, (), 'Work billing allocation proof; not a deposit cash fact.'),
    },
    'billing_facts.TaxAllocationProof': {
        'source_document_id': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'source_revision_id': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'source_line_id': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'root_document_id': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'root_line_id': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'source_basis_hash': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'quoted_quantity_microunits': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'quoted_base_quantity_microunits': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'quoted_net_minor_units': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'denominator': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'spans': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
        'basis_version': (_P, (), 'Work billing tax allocation proof; not a deposit cash fact.'),
    },
    'sales_facts.SalesTaxComponent': {
        'schema_version': (_P, (), 'Sales tax component snapshot; not a deposit cash fact.'),
        'position': (_P, (), 'Sales tax component snapshot; not a deposit cash fact.'),
        'tax_item': (_P, (), 'Sales tax component snapshot; not a deposit cash fact.'),
        'agency': (_P, (), 'Sales tax component snapshot; not a deposit cash fact.'),
        'liability_account': (_P, (), 'Sales tax component snapshot; not a deposit cash fact.'),
    },
    'deposit_models.ComponentOccurrence': {
        'key': (_N, (), 'The semantic key carries its own dispositions.'),
        'ordinal': (_D, ('SourceComponent.ordinal',), ''),
        'present': (_D, ('SourceComponent.present',), ''),
    },
    'deposit_read_models.SourceCurrent': {
        'id': (_D, ('SourceCurrentState.transaction_id',), ''),
        'status': (_D, ('SourceCurrentState.status',), ''),
        'version': (_D, ('SourceCurrentState.version',), ''),
        'revision_id': (_D, ('SourceCurrentState.revision_id',), ''),
        'claim_id': (_P, (), 'Deposit membership row identity; a physical record id.'),
        'claimed_by': (_R, ('SourceCurrentState.claimed', 'SourceCurrentState.claimed_by_this_deposit'),
                       'Reduced to whether a claim exists and whether it is this deposit; a related '
                       'deposit is never identified.'),
        'payment_method_type': (_G, ('SourceCurrentState.payment_method_type',),
                                'Optional current classification, separate from the captured method.'),
    },
}


def _fail(reason, detail):
    raise BookflowError('E_INTERNAL', message='deposit public manifest: ' + reason,
                        details={'reason': reason, 'detail': sorted(detail) if not isinstance(detail, str) else detail})


def _leaves(annotation, found):
    if typing.get_origin(annotation) is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found.add(annotation)
        return
    for argument in typing.get_args(annotation):
        _leaves(argument, found)


def reachable(roots):
    """Every pydantic model reachable from these outputs, unions and nesting included."""
    seen = {}
    pending = list(roots)
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen[model] = True
        for field in model.model_fields.values():
            nested = set()
            _leaves(field.annotation, nested)
            pending.extend(sorted(nested, key=lambda m: (m.__module__, m.__name__)))
    return seen


def _name(model):
    return model.__module__.rsplit('.', 1)[-1] + '.' + model.__name__


def conform(*, roots=None, public_roots=None, fields=None, constructed=None):
    """Exact two-way closure between the private reader graph and this manifest."""
    fields = FIELDS if fields is None else fields
    constructed = PUBLIC_CONSTRUCTED if constructed is None else constructed
    models = reachable(_roots() if roots is None else roots)
    names = {_name(model): model for model in models}
    if len(names) != len(models):
        _fail('ambiguous model name', [m.__name__ for m in models])
    if set(names) != set(fields):
        _fail('model closure differs from the manifest',
              set(names) ^ set(fields))
    for name, model in sorted(names.items()):
        declared = fields[name]
        if set(declared) != set(model.model_fields):
            _fail('field set differs for ' + name, set(declared) ^ set(model.model_fields))
        for field, entry in declared.items():
            if type(entry) is not tuple or len(entry) != 3:
                _fail('malformed entry', name + '.' + field)
            disposition, targets, note = entry
            if disposition not in DISPOSITIONS:
                _fail('unknown disposition', name + '.' + field)
            if type(targets) is not tuple or any(type(t) is not str for t in targets):
                _fail('malformed targets', name + '.' + field)
            if disposition == 'private':
                if targets or not note:
                    _fail('a private field needs a reason and no target', name + '.' + field)
            elif disposition != 'projected' and not targets:
                _fail('a disclosed field needs a public target', name + '.' + field)

    # Public side: every wire field is produced by a named private field, a
    # declared construction, or is a container of individually named members.
    public = reachable(_public_roots() if public_roots is None else public_roots)
    wire = set()
    for model in public:
        if model.model_config.get('extra') != 'forbid':
            _fail('public model must forbid extra keys', model.__name__)
        wire.update(model.__name__ + '.' + field for field in model.model_fields)
    named = {target for declared in fields.values() for entry in declared.values() for target in entry[1]}
    unknown = named - wire
    if unknown:
        _fail('manifest names a public field that does not exist', unknown)
    unnamed = wire - named - set(constructed)
    if unnamed:
        _fail('public field with no declared source', unnamed)
    stale = set(constructed) - wire
    if stale:
        _fail('declared construction for a public field that does not exist', stale)
    return True


def disposition(model_name, field):
    return FIELDS[model_name][field][0]
