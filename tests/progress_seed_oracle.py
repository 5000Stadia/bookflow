"""Complete financial-row inventory for the bounded progress seed block."""
from collections import Counter
import sqlite3


TABLES = ('transactions', 'transaction_revisions', 'posting_batches',
          'posting_lines', 'posting_line_sources')


def snapshot(database):
    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        return {table: {row['id']: dict(row) for row in db.execute(f'SELECT * FROM "{table}"')}
                for table in TABLES}


def assert_progress_inventory(database, before, after, expected, tax):
    """`expected` declares (exact number, type, date, revision nets) from business facts.

    Read stored IDs only to resolve relationships. Amounts and allowed multiplicities
    come from the caller's independent arithmetic, never from command output.
    """
    added = {}
    for table in TABLES:
        assert before[table], (table, 'empty preservation baseline')
        for identity, row in before[table].items():
            assert after[table].get(identity) == row, (table, identity, 'old row changed or deleted')
        added[table] = {identity: row for identity, row in after[table].items()
                        if identity not in before[table]}

    transactions = added['transactions']
    revisions = added['transaction_revisions']
    batches = added['posting_batches']
    lines = added['posting_lines']
    sources = added['posting_line_sources']
    assert len(transactions) == 8
    assert len(revisions) == 9  # eight originals plus INV-4 correction; void adds no revision
    assert len(batches) == 18  # nine business batches, each reversed exactly once
    assert Counter((r['number'], r['type'], r['status']) for r in transactions.values()) == Counter(
        (number, kind, 'voided') for number, kind, _date, _nets in expected)
    docs = {row['number']: row for row in transactions.values()}
    revision_keys = {identity: (transactions[row['transaction_id']]['number'], row['revision_number'])
                     for identity, row in revisions.items()}
    assert Counter(revision_keys.values()) == Counter(
        (number, version) for number, _kind, _date, nets in expected
        for version in range(1, len(nets) + 1))
    revision_ids = {key: identity for identity, key in revision_keys.items()}
    batch_keys = {identity: (*revision_keys[row['revision_id']], row['kind'])
                  for identity, row in batches.items()}
    assert Counter(batch_keys.values()) == Counter(
        (number, version, kind) for number, _type, _date, nets in expected
        for version in range(1, len(nets) + 1)
        for kind in ('original' if version == 1 else 'replacement', 'reversal'))
    batch_ids = {key: identity for identity, key in batch_keys.items()}

    with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        accounts = {row['id']: row['name'] for row in db.execute('SELECT id, name FROM accounts')}
        envelopes = {row['id']: dict(row) for row in db.execute('SELECT * FROM document_lines')}
        components = {row['id']: dict(row) for row in db.execute('SELECT * FROM sales_tax_components')}

    expected_lines, expected_sources = [], []
    for number, document_type, date, versions in expected:
        document = docs[number]
        assert document['current_revision_id'] == revision_ids[number, len(versions)]
        assert document['void_posting_batch_id'] == batch_ids[number, len(versions), 'reversal']
        assert document['version'] == len(versions) + 1
        for version, nets in enumerate(versions, 1):
            revision_id = revision_ids[number, version]
            revision = revisions[revision_id]
            assert (revision['number'], revision['date'], revision['currency'], revision['total_minor_units']) == (
                number, date, 'USD', sum(net + tax(net) for net in nets))
            assert revision['supersedes_revision_id'] == (revision_ids[number, version - 1] if version > 1 else None)
            business_kind = 'original' if version == 1 else 'replacement'
            business_id = batch_ids[number, version, business_kind]
            for kind in (business_kind, 'reversal'):
                batch = batches[batch_ids[number, version, kind]]
                assert (batch['transaction_id'], batch['effective_date']) == (document['id'], date)
                assert batch['reverses_batch_id'] == (business_id if kind == 'reversal' else None)
                assert batch['replaces_batch_id'] == (
                    batch_ids[number, version - 1, 'original'] if kind == 'replacement' else None)
                # Each service line owns a control debit, income credit and tax credit.
                # Control has separate net/tax sources; the two credits each have one.
                for position, net in enumerate(nets, 1):
                    levy = tax(net)
                    control = 'Checking' if document_type == 'sales_receipt' else 'Accounts Receivable'
                    for offset, (account, debit, credit, allocations) in enumerate((
                        (control, net + levy, 0, [('net', net), ('tax', levy)]),
                        ('Service Income', 0, net, [('net', net)]),
                        ('Sales Tax Payable', 0, levy, [('tax', levy)]),
                    ), 1):
                        line_no = 3 * (position - 1) + offset
                        if kind == 'reversal':
                            debit, credit = credit, debit
                        key = (number, version, kind, line_no)
                        expected_lines.append((*key, account, debit, credit, 'USD'))
                        expected_sources.extend((*key, position, category, amount, 'USD')
                                                for category, amount in allocations)

    actual_lines, actual_sources = [], []
    for row in lines.values():
        batch = batches[row['batch_id']]
        assert row['transaction_id'] == batch['transaction_id']
        key = (*batch_keys[batch['id']], row['line_no'])
        actual_lines.append((*key, accounts[row['account_id']], row['debit_minor_units'],
                             row['credit_minor_units'], row['currency']))
        if batch['kind'] == 'reversal':
            original = lines[row['reversed_line_id']]
            assert original['batch_id'] == batch['reverses_batch_id']
            assert (original['line_no'], original['account_id'], original['debit_minor_units'],
                    original['credit_minor_units']) == (
                row['line_no'], row['account_id'], row['credit_minor_units'], row['debit_minor_units'])
        else:
            assert row['reversed_line_id'] is None
    assert Counter(actual_lines) == Counter(expected_lines)

    for row in sources.values():
        line = lines[row['posting_line_id']]
        batch = batches[line['batch_id']]
        envelope = envelopes[row['document_line_id']]
        assert row['transaction_id'] == line['transaction_id'] == envelope['transaction_id']
        assert row['revision_id'] == batch['revision_id'] == envelope['revision_id']
        assert row['payment_component_id'] is None and row['deposit_component_id'] is None
        category = 'net'
        if row['tax_component_id'] is not None:
            category = 'tax'
            component = components[row['tax_component_id']]
            assert component['document_line_id'] == envelope['id']
            assert component['revision_id'] == row['revision_id']
        key = (*batch_keys[batch['id']], line['line_no'])
        actual_sources.append((*key, envelope['position'], category, row['amount_minor_units'], row['currency']))
        if batch['kind'] == 'reversal':
            original = sources[row['reversed_source_id']]
            assert original['posting_line_id'] == line['reversed_line_id']
            for field in ('transaction_id', 'revision_id', 'document_line_id', 'tax_component_id',
                          'amount_minor_units', 'currency'):
                assert original[field] == row[field], (field, row['id'])
        else:
            assert row['reversed_source_id'] is None
    assert Counter(actual_sources) == Counter(expected_sources)
    # Bijections also reject two inverse rows pointing at the same original.
    for rows, field in ((lines, 'reversed_line_id'), (sources, 'reversed_source_id')):
        originals = {r['id'] for r in rows.values() if r[field] is None}
        assert Counter(r[field] for r in rows.values() if r[field] is not None) == Counter(originals)
