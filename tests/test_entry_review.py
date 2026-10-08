"""R163: an agent cannot quietly make the books balance.

The R164 study's demonstration, replayed: an agent posts debit Checking / credit Opening Balance
Equity with no memo, ticks it and finishes the reconciliation. Before R163 nothing warned. Now the
agent is warned at write time, the entry is on the owner's entries-to-review list with who posted
it and for whom, the owner can mark it reviewed (audited, the entry untouched), a back-dated entry
into the reconciled month moves a prior balance the drift report names, and a statement that will
not tie gets a sanctioned stop instead of a plug.
"""
from fastapi.testclient import TestClient

from bookflow.core.config import Config
from bookflow.core.ids import new_id
from tests.conftest import hosted_call, make_agent
from tests.test_row3_host import hosted  # noqa: F401  (fixture)

COMPANY_PATH = '/companies/{}/commands/{}'
# The coverage census's witness for the three commands R163 adds (tests/mcp_coverage.py).
COMMANDS = {'report entries-to-review', 'report prior-balances', 'review mark'}
SURFACES = ('http',)


class Caller:
    def __init__(self, hosted, secret, reason='R163 witness'):
        self.hosted, self.api = hosted, TestClient(hosted.handle.app)
        self.headers = {'Authorization': f'Bearer {secret}', 'X-Bookflow-Reason': reason}

    def call(self, name, body, **headers):
        from bookflow.core import registry
        registry.load_all()
        sent = {k: v for k, v in self.headers.items() if registry.get(name).is_write or k != 'X-Bookflow-Reason'}
        return self.api.post(COMPANY_PATH.format(self.hosted.company_id, name.replace(' ', '.')),
                             json=body, headers={**sent, **headers})

    def ok(self, name, body, **headers):
        r = self.call(name, body, **headers)
        assert r.status_code == 200, r.text
        return r.json()


def _setup(hosted):
    principal = Config.load(hosted.root / 'config.toml').user_table(hosted.login)['user_id']
    agent = make_agent(hosted_call(hosted), 'review-bot', principals=principal,
                       company=hosted.company_id, role='standard')
    secret = hosted.ok('token.issue', {'user': agent, 'principal': principal, 'label': 'review'})['secret']
    person = Caller(hosted, hosted.secret)
    bot = Caller(hosted, secret, reason='Opening balance')
    bank = person.ok('account create', {'name': 'Review Checking', 'type': 'bank'})['id']
    expense = person.ok('account create', {'name': 'Review supplies', 'type': 'expense'})['id']
    return person, bot, agent, bank, expense


def _journal(caller, debit, credit, amount, day, **headers):
    return caller.ok('journal post', {'date': day, 'lines': [
        {'account': debit, 'side': 'debit', 'amount': amount},
        {'account': credit, 'side': 'credit', 'amount': amount}]}, **headers)


def _review(caller, **extra):
    return caller.ok('report entries-to-review', {'as_of': '2026-12-31', 'limit': 200, **extra})


def _row(report, identity):
    return next((r for r in report['rows'] if r['transaction_id'] == identity), None)


def _reconcile(bot, bank, ending):
    opening = bot.ok('reconcile opening start', dict(
        operation_key=new_id(), account=bank, opening_date='2026-08-31', entered_balance='0.00',
        evidence=dict(format=1, statement_reference=None, entered_text='First statement'), references=[]))['draft']
    draft = bot.ok('reconcile start', dict(operation_key=new_id(), account=bank, statement_date='2026-09-30',
                                           ending_balance=ending, opening_draft_id=opening['id']))['draft']
    marked = bot.ok('reconcile mark', dict(operation_key=new_id(), draft=draft['id'], expected_version=1, all=True))['draft']
    preview = bot.ok('reconcile preview', dict(draft=draft['id'], expected_version=marked['version']))
    return draft, marked, preview


def test_the_study_scenario_warns_the_agent_and_lands_on_the_owners_list(hosted):
    person, bot, agent, bank, _ = _setup(hosted)
    plug = _journal(bot, bank, 'Opening Balance Equity', '5000.00', '2026-09-15')
    assert any('Opening Balance Equity' in w and 'entries-to-review' in w for w in plug['warnings']), plug['warnings']

    draft, marked, preview = _reconcile(bot, bank, '5000.00')
    assert preview['balanced'] and preview['next_step'] is None
    done = bot.ok('reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
                                           expected_facts_fingerprint=preview['expected_facts_fingerprint'],
                                           dependency_guard=preview['dependency_guard']))
    assert done['certificate_id']
    # The plug was there when the statement was finished, so no reviewed balance has moved since.
    assert person.ok('report prior-balances', {'as_of': '2026-12-31'})['totals']['changed_balances'] == 0

    row = _row(_review(person), plug['id'])
    assert row is not None
    assert row['flags'] == ['opening_balance_equity']
    assert row['actor_kind'] == 'agent' and row['posted_by_id'] == agent
    assert row['on_behalf_of'] and row['reason'] == 'Opening balance' and row['interface'] == 'http'
    assert row['account'] == 'Opening Balance Equity' and row['amount']['amount'] == '5000.00'
    assert not row['reviewed']

    # The owner marks it reviewed: audited, the entry untouched, off the list until it changes.
    before = person.ok('journal show', {'journal': plug['id']})
    refused = bot.call('review mark', {'transaction': plug['id']})
    assert refused.status_code != 200 and refused.json()['code'] == 'E_PERMISSION'
    mark = person.ok('review mark', {'transaction': plug['id'], 'note': 'Checked with the bank: real deposit'})
    assert mark['changed'] and mark['flags'] == row['flags']
    assert person.ok('journal show', {'journal': plug['id']})['version'] == before['version']
    assert _row(_review(person), plug['id']) is None
    shown = _row(_review(person, include_reviewed=True), plug['id'])
    assert shown['reviewed'] and shown['reviewed_at']
    events = person.ok('audit list', {'command': 'review mark', 'limit': 5})['items']
    assert len(events) == 1 and events[0]['actor_kind'] == 'human'
    assert not person.ok('review mark', {'transaction': plug['id'], 'note': 'Checked with the bank: real deposit'})['changed']


def test_a_plug_posted_and_cleared_inside_one_reconciliation_is_flagged_and_warned(hosted):
    """The gaming move: start, see the difference, post an entry that closes it, tick it, finish."""
    person, bot, agent, bank, expense = _setup(hosted)
    _journal(person, bank, expense, '100.00', '2026-09-10')
    draft, marked, preview = _reconcile(bot, bank, '200.00')
    assert preview['totals']['difference'] != 0
    plug = _journal(bot, bank, expense, '100.00', '2026-09-30')
    remarked = bot.ok('reconcile mark', dict(operation_key=new_id(), draft=draft['id'],
                                             expected_version=marked['version'], all=True))['draft']
    again = bot.ok('reconcile preview', dict(draft=draft['id'], expected_version=remarked['version']))
    assert again['balanced']
    done = bot.ok('reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=remarked['version'],
                                           expected_facts_fingerprint=again['expected_facts_fingerprint'],
                                           dependency_guard=again['dependency_guard']))
    assert any('after the reconciliation was started' in w for w in done['warnings']), done['warnings']
    row = _row(_review(person), plug['id'])
    assert row['flags'] == ['cleared_on_arrival', 'round_unexplained'] and row['posted_by_id'] == agent
    assert row['statement_date'] == '2026-09-30'


def test_a_person_cutover_entry_is_setup_and_is_not_flagged(hosted):
    person, bot, _, bank, _ = _setup(hosted)
    marker = 'cutover:books.qbw:opening:1'
    opening = _journal(person, bank, 'Opening Balance Equity', '1200.00', '2026-08-31',
                       **{'X-Bookflow-Source-Ref': marker})
    plain = _journal(person, bank, 'Opening Balance Equity', '30.00', '2026-08-31')
    tagged_by_agent = _journal(bot, bank, 'Opening Balance Equity', '40.00', '2026-08-31',
                               **{'X-Bookflow-Source-Ref': marker})
    report = _review(person)
    assert _row(report, opening['id']) is None
    assert _row(report, plain['id'])['flags'] == ['opening_balance_equity']
    # The marker is a caller-supplied header, so it exempts a person's move-in, not an agent's.
    assert _row(report, tagged_by_agent['id'])['flags'] == ['opening_balance_equity']


def test_a_back_dated_entry_into_a_reconciled_month_warns_and_moves_a_prior_balance(hosted):
    person, bot, _, bank, expense = _setup(hosted)
    _journal(person, bank, expense, '500.00', '2026-09-10')     # an ordinary refund in September
    draft, marked, preview = _reconcile(person, bank, '500.00')
    person.ok('reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
                                       expected_facts_fingerprint=preview['expected_facts_fingerprint'],
                                       dependency_guard=preview['dependency_guard']))
    clean = person.ok('report prior-balances', {'as_of': '2026-12-31'})
    assert clean['totals']['changed_balances'] == 0 and clean['totals']['reviews'] >= 1

    late = _journal(bot, expense, bank, '300.00', '2026-09-20')
    assert any('already reconciled' in w and '2026-09-30' in w for w in late['warnings']), late['warnings']
    assert _row(_review(person), late['id'])['flags'] == ['reconciled_period']

    drift = person.ok('report prior-balances', {'as_of': '2026-12-31'})
    assert drift['totals']['changed_balances'] == 1 and drift['totals']['entries'] == 1
    balance, entry = drift['rows']
    assert balance['kind'] == 'balance' and balance['account_id'] == bank and balance['review_date'] == '2026-09-30'
    assert (balance['reviewed_balance']['amount'], balance['current_balance']['amount'],
            balance['change']['amount']) == ('500.00', '200.00', '-300.00')
    assert entry['kind'] == 'entry' and entry['transaction_id'] == late['id'] and entry['change']['amount'] == '-300.00'


def test_a_statement_that_will_not_tie_gets_a_sanctioned_stop(hosted):
    person, bot, agent, bank, expense = _setup(hosted)
    _journal(person, bank, expense, '100.00', '2026-09-10')
    draft, marked, preview = _reconcile(bot, bank, '150.00')
    assert not preview['balanced'] and 'leave this draft open' in preview['next_step']
    refused = bot.call('reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
                                                expected_facts_fingerprint=preview['expected_facts_fingerprint'],
                                                dependency_guard=preview['dependency_guard']))
    body = refused.json()
    assert body['code'] == 'E_RECONCILIATION_DIFFERENCE', body
    assert 'Never post' in body['details']['next'] and body['details']['difference'] == '50.00'
    assert 'never post an entry just to make it tie' in body['message']
    # The withdrawn plug input is refused as an unknown field, not silently ignored.
    plug = bot.call('reconcile finish', dict(operation_key=new_id(), draft=draft['id'], expected_version=marked['version'],
                                             expected_facts_fingerprint=preview['expected_facts_fingerprint'],
                                             dependency_guard=preview['dependency_guard'],
                                             adjustment=dict(date='2026-09-30', offset_account_id=expense, reason='plug')))
    assert plug.status_code != 200 and plug.json()['code'] == 'E_VALIDATION'

    # The stop: a note on the open draft, and the Overview's list of open reconciliations.
    bot.ok('note add', {'record_type': 'reconciliation_draft', 'record_id': draft['id'],
                        'body': 'Statement shows 150.00; ledger has 100.00. Missing a 50.00 deposit?'})
    left = _review(person)['open_reconciliations']
    found = next(r for r in left if r['draft_id'] == draft['id'])
    assert found['difference']['amount'] == '50.00' and found['started_by_kind'] == 'agent'
    assert found['started_by_id'] == agent and found['note'].startswith('Statement shows 150.00')

    from bookflow.core import registry
    registry.load_all()
    for name in ('reconcile finish', 'reconcile start'):
        assert 'Never post, change or tick an entry just to make the difference zero' in registry.get(name).description

    # The Overview shows the open reconciliation with its note; both reports have pages.
    from tests.test_row3_host import PASSWORD
    browser = TestClient(hosted.handle.app)
    assert browser.post('/login', json={'username': hosted.login, 'password': PASSWORD}).status_code == 200
    panel = browser.get(f'/c/{hosted.company_id}/_overview')
    assert panel.status_code == 200 and 'Entries to review' in panel.text
    assert 'Reconciliations left open' in panel.text and 'Missing a 50.00 deposit?' in panel.text
    for verb in ('entries-to-review', 'prior-balances'):
        page = browser.get(f'/c/{hosted.company_id}/report/{verb}?f:as_of=2026-12-31')
        assert page.status_code == 200, (verb, page.text[:300])
