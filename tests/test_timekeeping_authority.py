"""R106: what an agent may do with people's time.

An agent records time and schedule entries for any employee, exactly as its principal could at the
standard role: a bookkeeper's agent typing up everyone's paper timesheets is ordinary work, audited as
the agent acting for its principal. Live clock in and out is the one exception: it records attendance
as it happens, so an agent may clock only its principal's own employee record, linked to the
principal's login by a person (`clock link`, a person's act). Clocking anybody else is refused, and so
is the link itself, and a link to a login outside the company.
"""
import pytest

from tests.conftest import hosted_call, make_agent
from tests.test_row3_host import hosted  # noqa: F401 - fixture

REASON = {"X-Bookflow-Reason": "timekeeping witness"}


@pytest.mark.timeout(600)
def test_an_agent_clocks_only_its_principal_and_cannot_link_logins(hosted):
    cid = hosted.company_id
    hosted.ok("user.add", {"username": "dave-d", "company": cid, "role": "owner", "password": "pw-dave-d-12345"},
              headers=REASON)
    agent = make_agent(hosted_call(hosted), "time-agent", principals="dave-d", company=cid, role="standard")
    secret = hosted.ok("token.issue", {"user": agent, "principal": "dave-d", "label": "time"}, headers=REASON)["secret"]
    as_agent = {"Authorization": "Bearer " + secret, **REASON}
    hosted.ok("employee.create", {"name": "Dave Brazer"}, company=cid, headers=REASON)
    job = {"customer": "Riverside Apartments", "item": "Mainline Clearing"}

    # Recording time after the fact is open to the agent for any employee, audited as the agent for its principal.
    other = hosted.ok("timesheet.save", {"employee": "Casey Worker", "week": "2026-10-20", "rows": [
        {**job, "hours": ["4", None, None, None, None, None, None]}]}, company=cid, headers=as_agent)
    assert other["employee_name"] == "Casey Worker" and other["week_total"] == "4"
    event = hosted.ok("audit.list", {"command": "timesheet save", "limit": 1}, company=cid)["items"][0]
    assert event["actor_kind"] == "agent" and event["on_behalf_of"], event
    hosted.ok("schedule.create", {"employee": "Casey Worker", "customer": "Riverside Apartments",
                                  "starts": "2026-10-21T08:00", "ends": "2026-10-21T10:00"}, company=cid, headers=as_agent)

    # Not linked: the agent cannot clock Dave, nor anyone else, nor name nobody.
    for body in ({"employee": "Dave Brazer", **job}, {"employee": "Casey Worker", **job}):
        r = hosted.call("clock.in", body, company=cid, headers=as_agent)
        assert r.status_code == 403 and r.json()["details"]["reason"] == "own_instruction_only", r.text
    r = hosted.call("clock.in", job, company=cid, headers=as_agent)
    assert r.status_code == 422 and r.json()["code"] == "E_VALIDATION", r.text

    # Linking a login to an employee is a person's act.
    r = hosted.call("clock.link", {"employee": "Dave Brazer", "user": "dave-d"}, company=cid, headers=as_agent)
    assert r.status_code == 403 and r.json()["details"]["reason"] == "people_only", r.text
    hosted.ok("user.add", {"username": "outsider-o", "password": "pw-outsider-o-12345"}, headers=REASON)
    r = hosted.call("clock.link", {"employee": "Dave Brazer", "user": "outsider-o"}, company=cid, headers=REASON)
    assert r.status_code == 422 and "not a member" in r.text, r.text
    linked = hosted.ok("clock.link", {"employee": "Dave Brazer", "user": "dave-d"}, company=cid, headers=REASON)
    assert linked["username"] == "dave-d" and linked["changed"]

    # Linked: the agent clocks its principal in with no employee named, and out again; never Casey.
    started = hosted.ok("clock.in", job, company=cid, headers=as_agent)
    assert started["employee_name"] == "Dave Brazer" and started["status"] == "open"
    r = hosted.call("clock.in", {"employee": "Casey Worker", **job}, company=cid, headers=as_agent)
    assert r.status_code == 403 and "timesheet save" in r.json()["message"], r.text
    status = hosted.ok("clock.status", {}, company=cid, headers={"Authorization": as_agent["Authorization"]})
    assert [c["employee_name"] for c in status["clocks"]] == ["Dave Brazer"]
    cancelled = hosted.ok("clock.cancel", {}, company=cid, headers=as_agent)
    assert cancelled["status"] == "cancelled"

    # "Log my 8 hours on the Riverside job yesterday": a timesheet row for the principal's own week.
    saved = hosted.ok("timesheet.save", {"week": "2026-10-20", "rows": [
        {**job, "hours": ["8", None, None, None, None, None, None]}]}, company=cid, headers=as_agent)
    assert saved["employee_name"] == "Dave Brazer" and saved["week_total"] == "8"
    # A person may still clock anyone, as the anchor lets anyone enter anyone's time.
    assert hosted.ok("clock.in", {"employee": "Casey Worker", **job}, company=cid, headers=REASON)["status"] == "open"
