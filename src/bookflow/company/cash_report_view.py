"""Read-only SQL relations for a cash reporting projection.

No temporary tables or ledger writes. Existing report aggregations see their
ordinary immutable effects plus balanced cutoff adjustments. Original source
facts remain available for item, representative and dimension attribution.
"""
from datetime import date, timedelta
import json
import re


class CashReportView:
    def __init__(self, db):
        self.db = db
        self.raw = db.raw
        self._cache = {}
        self._columns = {
            table: [r[1] for r in self.raw.execute(f'PRAGMA table_info({table})')]
            for table in ('posting_lines', 'posting_batches', 'posting_line_sources')
        }

    def _record(self, table, identity):
        cursor = self.raw.execute(f'SELECT * FROM {table} WHERE id=?', (identity,))
        row = cursor.fetchone()
        if row is None:
            from bookflow.core.errors import BookflowError
            raise BookflowError('E_CASH_BASIS_EVIDENCE', 'A reporting source is missing.')
        return dict(zip(self._columns[table], row))

    def _relations(self, first, last):
        key = first, last
        if key in self._cache:
            return self._cache[key]
        from bookflow.company.cash_basis import cutoff_adjustments
        ending = cutoff_adjustments(self.db, last)
        before = (date.fromisoformat(first) - timedelta(days=1)).isoformat() if first > '0001-01-01' else None
        opening = cutoff_adjustments(self.db, before) if before else ()
        groups = [(last, 1, ending)]
        if opening:
            groups += [(before, 1, opening), (last, -1, opening)]
        tables = {name: [] for name in self._columns}
        serial = 0
        for effective_date, sign, adjustments in groups:
            for adjustment in adjustments:
                amount = sign * adjustment.amount_minor_units
                if not amount:
                    continue
                serial += 1
                identity = f'cash:{serial}'
                line = self._record('posting_lines', adjustment.posting_line_id)
                batch = self._record('posting_batches', adjustment.batch_id)
                source = self._record('posting_line_sources', adjustment.posting_source_id)
                line.update(id=identity, batch_id=identity, account_id=adjustment.account_id,
                            debit_minor_units=max(amount, 0), credit_minor_units=max(-amount, 0),
                            name_type=adjustment.name_type, name_id=adjustment.name_id,
                            class_id=adjustment.class_id, reversed_line_id=None)
                batch.update(id=identity, effective_date=effective_date)
                source.update(id=identity, posting_line_id=identity,
                              amount_minor_units=abs(amount), reversed_source_id=None)
                tables['posting_lines'].append(line)
                tables['posting_batches'].append(batch)
                tables['posting_line_sources'].append(source)
        bindings = {f'cash_{name}': json.dumps(rows, separators=(',', ':')) for name, rows in tables.items()}
        relations = {}
        for table, columns in self._columns.items():
            original = ','.join('"'+column+'"' for column in columns)
            virtual = ','.join("json_extract(value, '$."+column+"')" for column in columns)
            relations[table] = f'(SELECT {original} FROM main.{table} UNION ALL SELECT {virtual} FROM json_each(:cash_{table}))'
        self._cache[key] = relations, bindings
        return relations, bindings

    def execute(self, query, parameters=()):
        if not isinstance(parameters, dict) or 'date_to' not in parameters or not re.search(r'\bposting_(lines|batches|line_sources)\b', query):
            return self.raw.execute(query, parameters)
        relations, bindings = self._relations(parameters.get('date_from', '0001-01-01'), parameters['date_to'])
        # One substitution pass: injected main.table references must remain real.
        query = re.sub(r'\b(posting_lines|posting_batches|posting_line_sources)\b',
                       lambda match: relations[match.group()], query)
        # Quantity describes goods sold, not fractional currency recognition.
        # Projection adjustments change attributed money, never physical units.
        query = query.replace('CASE WHEN p.base_quantity_microunits IS NULL THEN NULL',
            "CASE WHEN s.id LIKE 'cash:%' THEN 0 WHEN p.base_quantity_microunits IS NULL THEN NULL")
        return self.raw.execute(query, {**parameters, **bindings})


def reporting_connection(db, basis):
    return CashReportView(db) if basis == 'cash' else db.raw
