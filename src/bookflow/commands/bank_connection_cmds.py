"""Connection commands authorize host work; no command opens a network connection."""
from bookflow.company import bank_connections as service, bank_connection_models as m
from bookflow.core.registry import command, Plan


def plan_add(inp, ctx, s):
    return service.prepare_add(s, ctx, inp)


def plan_enable(inp, ctx, s):
    return service.prepare_change(s, ctx, inp, 'enable')


def plan_disable(inp, ctx, s):
    return service.prepare_change(s, ctx, inp, 'disable')


def plan_check(inp, ctx, s):
    return service.prepare_change(s, ctx, inp, 'check')


def plan_fetch(inp, ctx, s):
    return service.prepare_fetch(s, ctx, inp)


def plan_query(inp, ctx, s):
    return Plan(service.query(s, inp))


def _write(name, description, model, planner):
    cmd = command(name, scope='company', description=description, input_model=model, output_model=m.ConnectionOutput,
        writes={'company'}, required_role='admin', capability='bank-feed.connect', accepts_idempotency_key=True,
        error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT', 'E_PERMISSION',
                     *(['E_ACCOUNT_MISMATCH'] if name == 'bank-feed fetch' else [])])(planner)
    cmd.resource_requirements = (('bank-feed', 'member'), ('ledger.read', 'member'))
    cmd.plan_in_write = True
    cmd.applier(service.apply)
    return cmd


add = _write('bank-feed-connection add', 'Add a disabled per-account feed connection with non-secret settings and an OS keyring secret_ref. No providers are registered in this build; unknown kinds refuse. Example: {"name":"Checking feed","kind":"unavailable","account":"Checking","remote_account":"checking"}.', m.AddInput, plan_add)
enable = _write('bank-feed-connection enable', 'Enable fetching on this host. The first page requires confirm equal to the staged preview fingerprint. A restored connection is disabled. Example: {"connection":"Checking feed"}.', m.EnableInput, plan_enable)
disable = _write('bank-feed-connection disable', 'Disable fetching and cancel a pending check. Example: {"connection":"Checking feed"}.', m.Selector, plan_disable)
check = _write('bank-feed-connection check', 'Request a bounded provider check and first-page preview on the host. The host may contact the configured provider; this command makes no network call. Example: {"connection":"Checking feed"}.', m.Selector, plan_check)
fetch = _write('bank-feed fetch', 'With dry_run, show the staged first page without contacting a provider. With confirm equal to its fingerprint, approve first backfill and enable bounded host fetching. No provider is wired in this build. Example: {"connection":"Checking feed"}.', m.EnableInput, plan_fetch)
query = command('bank-feed-connection query', scope='company', description='List account connections and available provider kinds (none in this build); never read a secret or contact a provider.', input_model=m.QueryInput, output_model=m.QueryOutput,
    required_role='member', capability='bank-feed', error_codes=[])(plan_query)
query.resource_requirements = (('ledger.read', 'member'),)
