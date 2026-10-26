"""R111 in the workbench: Reminders and my to-dos on home, the Reminders page, the to-do list with Done,
and the overdue notice queue with Print, Mark sent and Dismiss."""
from fastapi.testclient import TestClient

from tests.test_row3_host import PASSWORD, hosted  # noqa: F401

HEADERS = {"X-Bookflow-Workbench": "1", "X-Bookflow-Client-Name": "bookflow-workbench"}
AS_OF = "2099-06-15"


def test_reminders_to_dos_and_the_notice_queue(hosted):  # noqa: F811
    browser = TestClient(hosted.handle.app)
    assert browser.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id
    base = f"/companies/{company}/commands"

    def run(name, body):
        from bookflow.core import registry
        reason = {"X-Bookflow-Reason": "reminders witness"} if registry.get(name).is_write else {}
        answered = browser.post(f"{base}/{name.replace(' ', '.')}", json=body, headers={**HEADERS, **reason})
        assert answered.status_code == 200, answered.text[:600]
        return answered.json()

    customer = run("customer query", {"limit": 1})["items"][0]
    today = run("todo create", {"title": "Call about the balance", "priority": "high", "link_type": "customer",
                                "link": customer["id"]})
    later = run("todo create", {"title": "Order envelopes", "due_date": "2099-01-01"})

    home = browser.get(f"/c/{company}/_overview", headers=HEADERS)
    assert home.status_code == 200 and "Reminders" in home.text and f'data-my-todo="{today["id"]}"' in home.text
    assert f'data-my-todo="{later["id"]}"' not in home.text  # not due this week

    todos = browser.get(f"/c/{company}/todo", headers=HEADERS)
    assert todos.status_code == 200 and f'data-todo="{today["id"]}"' in todos.text and customer["full_name"] in todos.text
    done = browser.post(f"/c/{company}/todo/{today['id']}/_done", headers=HEADERS,
                        data={"back": f"/c/{company}/todo"}, follow_redirects=True)
    assert done.status_code == 200 and "To-do marked done." in done.text and f'data-todo="{today["id"]}"' not in done.text
    assert run("todo show", {"todo": today["id"]})["status"] == "done"

    run("company update", {"overdue_notice_days": 1, "overdue_notice_template": "letter"})
    made = run("notice generate", {"as_of": AS_OF})
    assert made["generated_count"] >= 2
    queue = browser.get(f"/c/{company}/notice", headers=HEADERS)
    first, second = made["generated"][0], made["generated"][1]
    assert queue.status_code == 200 and f'data-notice="{first["id"]}"' in queue.text and "data-ready-total" in queue.text
    pdf = browser.get(first["print_path"], headers=HEADERS)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    sent = browser.post(f"/c/{company}/notice/{first['id']}/_sent", headers=HEADERS,
                        data={"method": "email", "back": f"/c/{company}/notice"}, follow_redirects=True)
    assert sent.status_code == 200 and "Notice marked sent." in sent.text and f'data-notice="{first["id"]}"' not in sent.text
    dismissed = browser.post(f"/c/{company}/notice/{second['id']}/_dismiss", headers=HEADERS,
                             data={"note": "Paying Friday", "back": f"/c/{company}/notice"}, follow_redirects=True)
    assert dismissed.status_code == 200 and "Notice dismissed." in dismissed.text
    assert run("notice show", {"notice": second["id"]})["dismissed_reason"] == "Paying Friday"

    reminders = browser.get(f"/c/{company}/reminders?as_of={AS_OF}", headers=HEADERS)
    assert reminders.status_code == 200
    for key in ("memorized_due", "todos_due", "notices_ready", "overdue_invoices", "bills_to_pay"):
        assert f'data-reminder-section="{key}"' in reminders.text, key
    assert "Order envelopes" in reminders.text
    company_group = browser.get(f"/c/{company}/_group/company", headers=HEADERS).text
    assert f"/c/{company}/reminders" in company_group and f"/c/{company}/todo" in company_group


def _agent_template(hosted, name='Cleaner'):
    """An agent, acting for the installer, memorizes a monthly journal it wants entered automatically."""
    from bookflow.core.config import Config
    from tests.conftest import hosted_call, make_agent
    from tests.test_entry_review import Caller
    principal = Config.load(hosted.root / 'config.toml').user_table(hosted.login)['user_id']
    agent = make_agent(hosted_call(hosted), 'sched-bot', principals=principal, company=hosted.company_id, role='standard')
    secret = hosted.ok('token.issue', {'user': agent, 'principal': principal, 'label': 'sched'})['secret']
    person, bot = Caller(hosted, hosted.secret), Caller(hosted, secret, reason='Owner: post the monthly sweep')
    # A round month-end amount into a bank account with no memo, by an agent: the review's agent flag.
    bank = person.ok('account create', {'name': 'Sched Checking', 'type': 'bank'})['id']
    income = person.ok('account create', {'name': 'Sched sundry income', 'type': 'income'})['id']
    bot.ok('memorized create', {'name': name, 'command': 'journal post', 'frequency': 'monthly',
                                'start_date': '2099-01-31', 'mode': 'enter_automatically',
                                'payload': {'lines': [{'account': bank, 'side': 'debit', 'amount': '500.00'},
                                                      {'account': income, 'side': 'credit', 'amount': '500.00'}]}})
    return person, principal, agent


def _tick(hosted):
    from datetime import datetime, timezone
    return hosted.handle.host.schedule_now(now=datetime(2099, 1, 31, 18, tzinfo=timezone.utc))[hosted.company_id]


def test_E_an_agents_scheduled_entry_is_recorded_as_that_agents_write(hosted):  # noqa: F811
    """The scheduler replays an agent-authored template as the agent, for the person it acted for: review flags
    and the audit filters see it exactly as a direct agent write."""
    person, principal, agent = _agent_template(hosted)
    done = _tick(hosted)
    assert done['memorized']['entered'] == 1, done
    [occurrence] = person.ok('memorized show', {'memorized': 'Cleaner'})['occurrences']
    entry = occurrence['transaction_id']
    events = person.ok('audit list', {'actor': agent, 'principal': principal, 'command': 'journal post', 'limit': 10})['items']
    assert [e['actor_kind'] for e in events] == ['agent'] and events[0]['on_behalf_of'] == principal, events
    assert events[0]['interface'] == 'system' and events[0]['client_name'] == 'scheduler'
    review = person.ok('report entries-to-review', {'as_of': '2099-12-31', 'limit': 200})
    assert any(row['transaction_id'] == entry and row['actor_kind'] == 'agent' for row in review['rows'])


def _suspended_agents_template_is_blocked(hosted):
    import sqlite3
    person, principal, agent = _agent_template(hosted)
    with sqlite3.connect(hosted.root / 'hub.db') as hub:
        hub.execute("UPDATE agent_authority SET suspended_at = '2099-01-01T00:00:00Z', suspension_reason = 'probe' "
                    "WHERE agent_user_id = ?", (agent,))
    done = _tick(hosted)
    assert done['memorized']['entered'] == 0 and done['memorized']['blocked'] == 1, done
    [occurrence] = person.ok('memorized show', {'memorized': 'Cleaner'})['occurrences']
    assert occurrence['error_code'] == 'E_PERMISSION' and 'suspended' in occurrence['error_message']
    todo = next(t for t in person.ok('todo list', {'status': 'all'})['items']
                if t['source_ref'] == 'memorized-occurrence:' + occurrence['id'])
    assert todo['assignee_id'] == principal and todo['assignee_id'] != agent


def test_P_a_suspended_agents_template_is_blocked_under_the_current_policy(hosted):  # noqa: F811
    _suspended_agents_template_is_blocked(hosted)


def test_P_an_agent_unassigned_from_its_person_is_blocked(hosted):  # noqa: F811
    from tests.conftest import hosted_call
    person, principal, agent = _agent_template(hosted)
    hosted_call(hosted)('agent unassign', {'agent': agent, 'principal': principal})
    done = _tick(hosted)
    assert done['memorized']['entered'] == 0 and done['memorized']['blocked'] == 1, done


def test_M_a_member_denied_notes_and_customers_sees_neither_on_reminders_or_home(hosted):  # noqa: F811
    from tests.conftest import make_actor
    make_actor(hosted.root, 'mallory')
    hosted.ok('membership.grant', {'user': 'mallory', 'company': hosted.company_id, 'role': 'standard',
                                   'denies': ['note', 'customer'], 'expected_version': 0})
    hosted.ok('user.set-password', {'username': 'mallory', 'password': PASSWORD})
    owner = TestClient(hosted.handle.app)
    assert owner.post("/login", json={"username": hosted.login, "password": PASSWORD}).status_code == 200
    company = hosted.company_id
    for name, body in (('todo.create', {'title': 'HR: review salaries'}),
                       ('company.update', {'overdue_notice_days': 1, 'overdue_notice_template': 'letter'}),
                       ('notice.generate', {'as_of': AS_OF})):
        made = owner.post(f"/companies/{company}/commands/{name}", json=body, headers={**HEADERS, "X-Bookflow-Reason": "set up"})
        assert made.status_code == 200, made.text[:400]
    mallory = TestClient(hosted.handle.app)
    assert mallory.post("/login", json={"username": "mallory", "password": PASSWORD}).status_code == 200
    page = mallory.get(f"/c/{company}/reminders?as_of={AS_OF}", headers=HEADERS)
    assert page.status_code == 200 and 'data-reminder-section="overdue_invoices"' in page.text
    assert 'data-reminder-section="todos_due"' not in page.text and 'data-reminder-section="notices_ready"' not in page.text
    assert 'HR: review salaries' not in page.text and 'Dear ' not in page.text
    home = mallory.get(f"/c/{company}/_overview", headers=HEADERS)
    assert home.status_code == 200 and 'HR: review salaries' not in home.text
    assert 'data-reminder="todos_due"' not in home.text and 'data-reminder="notices_ready"' not in home.text
