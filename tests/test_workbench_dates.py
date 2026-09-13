"""Declared date controls and inclusive local-calendar presets."""
from datetime import date, datetime
from pathlib import Path
import json
import subprocess

from pydantic import BaseModel, Field
from bookflow.adapters.workbench.forms import leaves, collection_schema
from bookflow.company.sales_models import InvoicePostInput
from bookflow.company.ledger_reports import GeneralLedgerInput, TrialBalanceInput
from bookflow.company.summary_reports import SalesByCustomerInput
from bookflow.company.journal_models import _Date
from bookflow.company.rate_models import RateSetInput
from bookflow.company.inventory_models import InventoryAdjustInput


def test_real_string_validators_and_nested_dates_have_calendar_metadata():
    for model, fields in [(InvoicePostInput, {'date', 'due_date', 'ship_date'}),
                          (GeneralLedgerInput, {'date_from','date_to'}),
                          (SalesByCustomerInput, {'date_from','date_to'}),
                          (TrialBalanceInput, {'date_to'}),
                          (RateSetInput, {'date'}), (InventoryAdjustInput, {'date'})]:
        found={leaf['path'] for leaf in leaves(model) if leaf['date']}
        assert fields <= found, (model, found)

    class Entry(BaseModel):
        day: date
        captured: _Date | None = None
        formatted: str = Field(json_schema_extra={'format':'date'})
        date_label: str
        instant: datetime

    controls=collection_schema(list[list[Entry]])['item']['collection']['item']['fields']
    assert {f['name'] for f in controls if f['date']} == {'day','captured','formatted'}


def test_local_calendar_presets_cover_rollovers_leap_days_and_timezones():
    script=Path('src/bookflow/adapters/workbench/static/dates.js').read_text()
    checks=[
        ('2028-03-01','yesterday',['2028-02-29','2028-02-29']),
        ('2027-03-01','last-month',['2027-02-01','2027-02-28']),
        ('2028-02-12','this-month',['2028-02-01','2028-02-29']),
        ('2026-01-01','last-month',['2025-12-01','2025-12-31']),
        ('2026-01-01','last-quarter',['2025-10-01','2025-12-31']),
        ('2026-04-01','last-quarter',['2026-01-01','2026-03-31']),
        ('2026-12-31','this-quarter',['2026-10-01','2026-12-31']),
        ('2026-01-01','last-year',['2025-01-01','2025-12-31']),
        ('2028-02-29','this-year',['2028-01-01','2028-12-31']),
        ('2028-02-29','month-to-date',['2028-02-01','2028-02-29']),
        ('2026-05-02','quarter-to-date',['2026-04-01','2026-05-02']),
        ('2026-05-02','year-to-date',['2026-01-01','2026-05-02']),
        ('2026-05-02','today',['2026-05-02','2026-05-02']),
        ('2026-05-02','clear',['','']),
        ('2026-05-02','custom',None),
    ]
    source=script+'\n'+f'''
const assert=require('node:assert/strict');
for(const tz of ['America/Los_Angeles','Pacific/Kiritimati','UTC']) {{
 process.env.TZ=tz;
 for(const [day,kind,want] of {json.dumps(checks)}) {{
  const [y,m,d]=day.split('-').map(Number);
  for(const hour of [0,23]) assert.deepEqual(BookflowDates.range(kind,new Date(y,m-1,d,hour,59)),want);
 }}
}}
assert.equal(BookflowDates.valid('2028-02-29'),true);
for(const bad of ['2027-02-29','2026-13-01','2026-02-30','not a date','2026-1-01']) assert.equal(BookflowDates.valid(bad),false);
'''
    subprocess.run(['node','-e',source],check=True,capture_output=True,text=True)
