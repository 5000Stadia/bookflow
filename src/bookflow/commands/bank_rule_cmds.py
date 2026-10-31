"""`bank-rule`: rules for the bank inbox, written or learned (R108 increment 4, design/bank-feeds.md §5).

The work lives in ``company/bank_rules.py``. Capability `bank-feed` (reads at member, writes at standard) with
`ledger.read` at member beside it, as the inbox's own commands. A rule proposes; only an auto-add or ignore rule made
live by a holder of `automate.bank-rule` acts on its own (`bank-rule update --live`, checked by
`automation.require_establish`).
"""
from bookflow.company import bank_rule_models as m
from bookflow.core.registry import Applied, Plan, command

_READ = (('ledger.read', 'member'),)
_ERRORS = ['E_VALIDATION', 'E_RECORD_NOT_FOUND', 'E_VERSION_CONFLICT']

_CREATE = (
    "Write a bank rule: the lines it fits (an account or every feed account, money in, out or any, and conditions "
    "that all hold: payee, payee_key, memo or number contains, starts_with or equals; amount equals or between) and "
    "what it proposes: categorise (payee, category account, class, memo), transfer (to an account) or ignore (with a "
    "reason). Rules are tried by priority (lower first; a written rule is 100, a learned one 1000), then a rule for "
    "the line's own account first, then specificity; the first that fits wins and the line shows it. A rule only "
    "proposes: auto_add (or an ignore rule) is a request that acts only once a holder of automate.bank-rule makes it "
    "live with `bank-rule update --live`. 'Always put Shell under Fuel' means create a proposal-only rule, "
    "then bank-rule apply --dry-run to show waiting lines it fits; apply to refresh their proposals. Report that "
    'it does not post unattended. Do not enable auto-add from that wording alone. Example: {"name": "Shell fuel", "account": "Checking", "direction": "out", '
    '"conditions": [{"field": "payee", "op": "contains", "value": "SHELL"}], "payee": "Shell", "category": "Fuel"}.')
_UPDATE = (
    "Change a bank rule, make an auto-add or ignore rule live, or pause one (live false). Making it live needs "
    "automate.bank-rule and one check of its exact details: --dry-run returns check (what it will do, the waiting "
    "lines it fits now, a fingerprint); pass the fingerprint as confirm. It then acts at each later import under your "
    "authority. A change to what a live rule does by anyone without automate.bank-rule returns it to draft; by a "
    "holder it stays live under them only with the new check confirmed. A learned rule you change is kept when its "
    'import is reversed. Example: {"rule": "Shell fuel", "live": true, "confirm": "<fingerprint>"}.')
_DEACTIVATE = ('Stop a bank rule fitting any line (status disabled); `bank-rule update --status active` turns it back '
               'on. Example: {"rule": "Shell fuel"}.')
_ACCEPT = ('Accept a suggested (learned) bank rule: it becomes active and proposes for its payee\'s lines. Example: '
           '{"rule": "01J..."}.')
_DECLINE = ("Decline a suggested (learned) bank rule: it is retired and its payee key is never suggested again for "
            'that account. Example: {"rule": "01J..."}.')
_SHOW = 'Show a bank rule: what it fits and does, its state, who made it live, and how many lines it touched.'
_QUERY = ("List bank rules in the order they are tried, one account's (with the rules for every account) or all; "
          '`--status suggested` lists the learned suggestions waiting for an answer.')
_APPLY = ("Read the rules over the waiting lines again: each line's rule proposal becomes the first rule that fits "
          "now (decided lines are never touched; nothing is added or ignored). With rule, only the lines that rule "
          'gains or loses. --dry-run lists what would change. Example: {"rule": "Shell fuel"}.')


def _output(s, result: dict) -> m.RuleOutput:
    from bookflow.company import bank_rules
    return m.RuleOutput(rule=bank_rules.out(s, result['row']), check=result.get('check'),
                        notes=tuple(result.get('notes', ())), summary=result['summary'] + '.',
                        next=result.get('next'))


def _rules():
    from bookflow.company import bank_rules
    return bank_rules


def plan_bank_rule_create(inp, ctx, s) -> Plan:
    return Plan(_output(s, _rules().create(s, ctx, inp, dry_run=True)[0]), {'input': inp})


def apply_bank_rule_create(plan, ctx, s) -> Applied:
    result, touched = _rules().create(s, ctx, plan.data['input'], dry_run=False)
    return Applied(_output(s, result), touched, result['summary'])


def plan_bank_rule_update(inp, ctx, s) -> Plan:
    return Plan(_output(s, _rules().update(s, ctx, inp, dry_run=True)[0]), {'input': inp})


def apply_bank_rule_update(plan, ctx, s) -> Applied:
    result, touched = _rules().update(s, ctx, plan.data['input'], dry_run=False)
    return Applied(_output(s, result), touched, result['summary'])


def plan_bank_rule_deactivate(inp, ctx, s) -> Plan:
    return Plan(_output(s, _rules().set_status(s, ctx, inp, 'deactivate', dry_run=True)[0]), {'input': inp})


def apply_bank_rule_deactivate(plan, ctx, s) -> Applied:
    result, touched = _rules().set_status(s, ctx, plan.data['input'], 'deactivate', dry_run=False)
    return Applied(_output(s, result), touched, result['summary'])


def plan_bank_rule_accept(inp, ctx, s) -> Plan:
    return Plan(_output(s, _rules().set_status(s, ctx, inp, 'accept', dry_run=True)[0]), {'input': inp})


def apply_bank_rule_accept(plan, ctx, s) -> Applied:
    result, touched = _rules().set_status(s, ctx, plan.data['input'], 'accept', dry_run=False)
    return Applied(_output(s, result), touched, result['summary'])


def plan_bank_rule_decline(inp, ctx, s) -> Plan:
    return Plan(_output(s, _rules().set_status(s, ctx, inp, 'decline', dry_run=True)[0]), {'input': inp})


def apply_bank_rule_decline(plan, ctx, s) -> Applied:
    result, touched = _rules().set_status(s, ctx, plan.data['input'], 'decline', dry_run=False)
    return Applied(_output(s, result), touched, result['summary'])


def plan_bank_rule_apply(inp, ctx, s) -> Plan:
    return Plan(m.ApplyOutput(**_rules().apply(s, ctx, inp, dry_run=True)[0]), {'input': inp})


def apply_bank_rule_apply(plan, ctx, s) -> Applied:
    result, touched = _rules().apply(s, ctx, plan.data['input'], dry_run=False)
    return Applied(m.ApplyOutput(**result), touched, result['summary'])


def plan_bank_rule_show(inp, ctx, s) -> Plan:
    rules = _rules()
    row = rules.get(s, inp.rule)
    return Plan(m.RuleOutput(rule=rules.out(s, row), check=rules.check(s, row) if rules.is_automation(row) else None,
                             summary=f"bank rule {row['name']}."))


def plan_bank_rule_query(inp, ctx, s) -> Plan:
    return Plan(m.QueryOutput(**_rules().query(s, inp)))


def _write(name, description, inp, out, planner, applier, *, errors=_ERRORS, positional=('rule',)):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  writes={'company'}, required_role='standard', capability='bank-feed', positional=list(positional),
                  accepts_idempotency_key=True, error_codes=list(errors))(planner)
    cmd.applier(applier)
    cmd.resource_requirements = _READ
    return cmd


def _read(name, description, inp, out, planner, *, positional=()):
    cmd = command(name, scope='company', description=description, input_model=inp, output_model=out,
                  required_role='member', capability='bank-feed', positional=list(positional),
                  error_codes=['E_VALIDATION', 'E_RECORD_NOT_FOUND'])(planner)
    cmd.resource_requirements = _READ
    return cmd


bank_rule_create = _write('bank-rule create', _CREATE, m.CreateInput, m.RuleOutput, plan_bank_rule_create,
                          apply_bank_rule_create, errors=[*_ERRORS, 'E_NAME_TAKEN', 'E_AMOUNT_PRECISION'],
                          positional=())
bank_rule_update = _write('bank-rule update', _UPDATE, m.UpdateInput, m.RuleOutput, plan_bank_rule_update,
                          apply_bank_rule_update,
                          errors=[*_ERRORS, 'E_NAME_TAKEN', 'E_AMOUNT_PRECISION', 'E_PERMISSION', 'E_DB_BUSY'])
bank_rule_deactivate = _write('bank-rule deactivate', _DEACTIVATE, m.RuleSelector, m.RuleOutput,
                              plan_bank_rule_deactivate, apply_bank_rule_deactivate)
bank_rule_accept = _write('bank-rule accept', _ACCEPT, m.RuleSelector, m.RuleOutput, plan_bank_rule_accept,
                          apply_bank_rule_accept)
bank_rule_decline = _write('bank-rule decline', _DECLINE, m.RuleSelector, m.RuleOutput, plan_bank_rule_decline,
                           apply_bank_rule_decline)
bank_rule_apply = _write('bank-rule apply', _APPLY, m.ApplyInput, m.ApplyOutput, plan_bank_rule_apply,
                         apply_bank_rule_apply, positional=())
bank_rule_show = _read('bank-rule show', _SHOW, m.RuleSelector, m.RuleOutput, plan_bank_rule_show,
                       positional=('rule',))
bank_rule_query = _read('bank-rule query', _QUERY, m.QueryInput, m.QueryOutput, plan_bank_rule_query)
