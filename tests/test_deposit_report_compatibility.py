"""Pure report/financial representation compatibility, not public GL evidence.

The public GL/register repair is independently frozen at 1ab44f7; real command
and statement compatibility remains a supplier/integration checkpoint here.
"""
from bookflow.company.deposit_models import Effect
from bookflow.company.deposit_report_models import DepositDetailInput
from bookflow.company.deposit_report_print import selected_compositions
from bookflow.company.deposit_reports import aggregate
from tests.test_deposit_reports import world


def test_complete_projection_does_not_page_or_rewrite_financial_effects():
    facts = world()
    before = tuple(r.effect.model_dump_json() for r in facts[0].revisions)
    results = []
    for size in (1,25,200):
        inp = DepositDetailInput(date_from='2026-06-01',date_to='2026-06-30',page_size=size)
        result = aggregate(facts,inp,currency='USD',destination_id=None)
        results.append(result.model_dump())
        captured = selected_compositions(result,facts)
        assert all(type(r.effect) is Effect for r in captured)
        assert [r.effect.bank_total for r in captured] == [17200,17200,19200]
        assert len(result.rows) == 3
    assert results[0] == results[1] == results[2]
    assert tuple(r.effect.model_dump_json() for r in facts[0].revisions) == before
