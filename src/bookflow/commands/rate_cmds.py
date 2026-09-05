"""Manual dated-rate commands on the ordinary company command pipeline."""
from bookflow.company import rates
from bookflow.company.rate_models import RateSetInput, RateShowInput, RateQueryInput, RateOutput, RateWriteOutput, RatePageOutput
from bookflow.core.registry import Plan, command


@command('rate set', scope='company', description='Create or version-update a manual exact-date rate into the company home currency; zero version creates only.',
    input_model=RateSetInput, output_model=RateWriteOutput, writes={'company'}, required_role='standard', capability='ledger.post',
    accepts_idempotency_key=True, error_codes=['E_VERSION_CONFLICT', 'E_VALUE_RANGE'])
def rate_set(inp, ctx, s):
    return rates.prepare(s, ctx, inp)


rate_set.ledger = True
rate_set.applier(rates.apply)


@command('rate show', scope='company', description='Show a manual rate by stable id or exact date and original currency.',
    input_model=RateShowInput, output_model=RateOutput, required_role='member', capability='ledger.read', positional=['rate_id'], error_codes=['E_RECORD_NOT_FOUND'])
def rate_show(inp, ctx, s):
    return Plan(rates.show(s, inp))


@command('rate query', scope='company', description='Page manual rates in date, original currency and stable-id order; restart when company audit changes.',
    input_model=RateQueryInput, output_model=RatePageOutput, required_role='member', capability='ledger.read', error_codes=['E_QUERY_STALE'])
def rate_query(inp, ctx, s):
    return Plan(rates.page(s, ctx, inp))


RATE_COMMANDS = [rate_set, rate_show, rate_query]
