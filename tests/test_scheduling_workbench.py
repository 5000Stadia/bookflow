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
