"""Audit projections expose compensating-event relationships."""


def test_company_audit_show_exposes_undo_chain(client):
    company = "Demo Plumbing Co"
    account = client.account.create(
        name="Audit undo relationship",
        type="expense",
        company=company,
    )
    original = client.audit.list(
        company=company,
        command="account create",
        record_type="account",
        record_id=account["id"],
    )["items"][0]
    assert client.audit.show(event=original["id"], company=company)[
        "undo_of_event_id"
    ] is None

    undone = client.run("undo", {"event_id": original["id"]}, company=company)
    shown = client.audit.show(event=undone["undo_event_id"], company=company)
    assert shown["undo_of_event_id"] == original["id"]

