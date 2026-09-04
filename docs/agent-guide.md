# Agent guide: one safe HTTP write

This is the shortest end-to-end path from a fresh demo to a versioned, audited write. Bookflow currently exposes programmatic remote work over bearer-authenticated HTTP; it does not expose an MCP endpoint.

The bootstrap bearer belongs to the human who issued it. The current command registry has no public commands for creating agent identities or assigning principals, so do not describe this token as an independent agent identity.

## Operator bootstrap

Install the `bookflow-core` distribution first. Use `BOOKFLOW=(bookflow)` for an installed distribution. In a source checkout, use `BOOKFLOW=(uv run bookflow)` instead. Every command below uses that array, so the rest of the instructions are identical in both environments.

Run these commands on the host. They create an isolated temporary data root, choose a currently unused loopback port, issue and extract a one-day token, start the host in the background, and wait for `GET /health` to return HTTP 200. The port probe and host bind are separate operations; if the host reports `E_IO` because another process claimed the port between them, run the block again with a fresh temporary root.

<!-- bookflow-example: illustrative -->
```sh
BOOKFLOW=(bookflow)
# In a source checkout, use this line instead:
# BOOKFLOW=(uv run bookflow)
"${BOOKFLOW[@]}" --help >/dev/null

export BOOKFLOW_DATA_ROOT="$(mktemp -d)"
BOOKFLOW_PORT="$(python - <<'PY'
import socket
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    print(probe.getsockname()[1])
PY
)"
export BOOKFLOW_BIND="127.0.0.1:$BOOKFLOW_PORT"
export BOOKFLOW_URL="http://$BOOKFLOW_BIND"
"${BOOKFLOW[@]}" init --json
"${BOOKFLOW[@]}" demo reset --json
TOKEN_DOCUMENT="$("${BOOKFLOW[@]}" token issue --label agent-guide --days 1 --json)"
export BOOKFLOW_TOKEN="$(printf '%s' "$TOKEN_DOCUMENT" | python -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
"${BOOKFLOW[@]}" serve --bind "$BOOKFLOW_BIND" >"$BOOKFLOW_DATA_ROOT/host.out" 2>"$BOOKFLOW_DATA_ROOT/host.err" &
BOOKFLOW_HOST_PID=$!

python - <<'PY'
import os
import time
from urllib.error import URLError
from urllib.request import urlopen

url = os.environ["BOOKFLOW_URL"] + "/health"
for _ in range(100):
    try:
        with urlopen(url, timeout=0.2) as response:
            if response.status == 200:
                break
    except URLError:
        pass
    time.sleep(0.05)
else:
    raise SystemExit("Bookflow host did not become ready")
PY
```

For an agent in another process or on another machine, transfer `BOOKFLOW_URL` and `BOOKFLOW_TOKEN` through the operator's normal secret channel instead of writing the bearer token into the repository, command history, or logs. The token secret is returned only at issuance. Do not print `TOKEN_DOCUMENT` or `BOOKFLOW_TOKEN` after capture.

## Executable journey

Set `BOOKFLOW_URL` and `BOOKFLOW_TOKEN` in the agent's environment, then run this exact standard-library-only program. It discovers every identifier, version, and cursor from the service. The assertions are part of the example: if an invariant fails, the program stops instead of silently continuing.

The request headers follow the [command contract](concepts.md): `X-Bookflow-Client-Name` labels the caller in audit, `X-Bookflow-Directive` cites the standing instruction authorizing these writes, and `Idempotency-Key` makes retried directive creation return the original result instead of creating a duplicate.

<!-- bookflow-example: executable -->
```python
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


base_url = os.environ["BOOKFLOW_URL"].rstrip("/")
token = os.environ["BOOKFLOW_TOKEN"]
client_name = "agent-guide"
base_headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Bookflow-Client-Name": client_name,
}


def post(path, body, *, expected=200, headers=None):
    request = Request(
        base_url + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={**base_headers, **(headers or {})},
    )
    try:
        with urlopen(request, timeout=10) as response:
            status = response.status
            payload = json.loads(response.read())
    except HTTPError as error:
        status = error.code
        payload = json.loads(error.read())
    assert status == expected, (status, payload)
    return payload


def command(name, body, *, company_id=None, expected=200, headers=None):
    if company_id is None:
        path = f"/commands/{name}"
    else:
        path = f"/companies/{company_id}/commands/{name}"
    return post(path, body, expected=expected, headers=headers)


def next_sse_event(company_id, after):
    query = urlencode({"command": "company update"})
    request = Request(
        f"{base_url}/companies/{company_id}/events?{query}",
        headers={
            "Authorization": f"Bearer {token}",
            "Last-Event-ID": str(after),
            "X-Bookflow-Client-Name": client_name,
        },
    )
    with urlopen(request, timeout=10) as response:
        assert response.status == 200
        event_id = None
        event_name = None
        event_data = None
        while True:
            raw = response.readline()
            assert raw, "the event feed ended before an event arrived"
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("id: "):
                event_id = int(line[4:])
            elif line.startswith("event: "):
                event_name = line[7:]
            elif line.startswith("data: "):
                event_data = json.loads(line[6:])
            elif not line and event_data is not None:
                return {"id": event_id, "event": event_name, "data": event_data}


companies = command("company.list", {})
demos = [item for item in companies["items"] if item["is_demo"]]
assert len(demos) == 1
company_id = demos[0]["company_id"]

directive = command(
    "directive.add",
    {"text": "Preserve concurrent changes and cite this instruction."},
    company_id=company_id,
    headers={"Idempotency-Key": "agent-guide-directive-v1"},
)["directive"]
directive_code = directive["code"]
issuer_id = directive["recorded_by"]
assert directive["given_by"] == issuer_id
directive_header = {"X-Bookflow-Directive": directive_code}

before = command("company.show", {}, company_id=company_id)
stale_version = before["info_version"]
start_cursor = command("audit.tail", {}, company_id=company_id)["high_water"]
assert isinstance(start_cursor, int)

first_phone = "555-0181"
second_phone = "555-0182"
first = command(
    "company.update",
    {"expected_version": stale_version, "phone": first_phone},
    company_id=company_id,
    headers=directive_header,
)
assert first["version"] == stale_version + 1

conflict = command(
    "company.update",
    {"expected_version": stale_version, "phone": second_phone},
    company_id=company_id,
    expected=409,
    headers=directive_header,
)
assert conflict["code"] == "E_VERSION_CONFLICT"
assert "phone" in conflict["details"]["changed_fields"]
after_conflict = command("company.show", {}, company_id=company_id)
assert after_conflict["info"]["phone"] == first_phone
assert after_conflict["info_version"] == first["version"]

retried = command(
    "company.update",
    {"expected_version": after_conflict["info_version"], "phone": second_phone},
    company_id=company_id,
    headers=directive_header,
)
after_retry = command("company.show", {}, company_id=company_id)
assert after_retry["info"]["phone"] == second_phone
assert after_retry["info_version"] == retried["version"]

audit = command(
    "audit.list",
    {
        "command": "company update",
        "record_type": "company_info",
        "record_id": company_id,
        "limit": 20,
    },
    company_id=company_id,
)
our_events = [
    event
    for event in audit["items"]
    if event["client_name"] == client_name
    and event["directive_code"] == directive_code
]
assert len(our_events) == 2
assert all(event["command"] == "company update" for event in our_events)
assert all(event["interface"] == "http" for event in our_events)
assert all(event["actor_kind"] == "human" for event in our_events)
assert all(event["actor_id"] == issuer_id and event["actor_name"] for event in our_events)
assert all(event["directive_id"] == directive["id"] for event in our_events)
actor_ids = {event["actor_id"] for event in our_events}
assert len(actor_ids) == 1

polled = command(
    "audit.tail",
    {"after": start_cursor, "command": "company update", "limit": 20},
    company_id=company_id,
)
polled_events = [
    event
    for event in polled["items"]
    if event["client_name"] == client_name
    and event["directive_code"] == directive_code
]
assert len(polled_events) == 2
resume_cursor = polled["next_after"]
assert isinstance(resume_cursor, int) and resume_cursor > start_cursor

third = command(
    "company.update",
    {"expected_version": after_retry["info_version"], "fax": "555-0183"},
    company_id=company_id,
    headers=directive_header,
)
assert third["version"] == after_retry["info_version"] + 1

resumed = next_sse_event(company_id, resume_cursor)
event = resumed["data"]
assert resumed["event"] == "audit"
assert resumed["id"] == event["seq"] and resumed["id"] > resume_cursor
assert event["command"] == "company update"
assert event["interface"] == "http"
assert event["client_name"] == client_name
assert event["directive_code"] == directive_code
assert event["directive_id"] == directive["id"]
assert event["actor_kind"] == "human"
assert event["actor_id"] in actor_ids

print(json.dumps({
    "company_id": company_id,
    "directive_code": directive_code,
    "version": third["version"],
    "cursor": resumed["id"],
}))
```

The final line is a machine-readable receipt. The program has already verified that `version` is the company's resulting `info_version`, that `cursor` identifies the resumed audit event, and that `directive_code` is attached to each write. Retain those values if later work must continue from this run. Later verification can read the company with [`company show`](cli/company.md) and request events after the receipt cursor with [`audit tail`](cli/audit.md).

The journey expects exactly one fresh demo company. To rerun it from the beginning, stop the host, run `demo reset` against the same root, issue a new short-lived token, and restart the host. The fixed directive idempotency key is then safe because the reset created a fresh demo database.

## Trial cleanup

For the background host started above, send it SIGINT and wait for orderly shutdown:

<!-- bookflow-example: illustrative -->
```sh
kill -INT "$BOOKFLOW_HOST_PID"
wait "$BOOKFLOW_HOST_PID"
unset BOOKFLOW_TOKEN TOKEN_DOCUMENT
```

Confirm that the host has stopped before removing anything. If this was an isolated documentation trial, remove only the exact temporary data root created by `mktemp`; never remove a normal Bookflow data root. Cleanup is intentionally not a recursive copy-paste command because the data-root path must be inspected by the operator first. If the data root is retained, revoke the short-lived guide token with [`token revoke`](cli/token.md) when it is no longer needed.
