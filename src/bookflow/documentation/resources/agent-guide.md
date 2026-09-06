# Agent guide: one safe HTTP write

This is the shortest end-to-end path from a fresh demo to a versioned, audited HTTP write. The [installed MCP guide](mcp-guide.md) uses the same authenticated host through a three-tool stdio launcher, including receipt files and recovery.

The bootstrap bearer belongs to the human who issued it. The current command registry has no public commands for creating agent identities or assigning principals, so do not describe this token as an independent agent identity.

Browser and programmatic clients operate on the same records. After another writer
corrects an invoice, read `invoice show` again and use its current `version` for the
next update. Retain existing line IDs, preview the intended correction and submit
its returned facts fingerprint. A stale `expected_version` returns
`E_VERSION_CONFLICT`; do not replace it blindly or recreate the invoice. Retry a
previous request with its original idempotency key and identical input, then read
the current record separately: a replay result describes the original operation.
The workbench records `interface=http` and `client_name=bookflow-workbench`; an HTTP
automation identifies its own client name. Audit actor and on-behalf-of fields
identify the authenticated writer and bound principal, independently of that name.

## Operator bootstrap

Install the `bookflow-core` distribution first, or run from a source-checkout root containing `pyproject.toml` and the committed `uv.lock`. The block automatically uses an installed `bookflow` command when one is on `PATH`. Otherwise it creates and synchronizes a dedicated environment inside the disposable trial directory, then uses `uv run --frozen --no-sync`; the checkout's lockfile and shared environment remain unchanged. Every later command uses the selected array, so the rest of the instructions are identical in both environments.

Run this as one Bash block on the host. It creates a specifically named temporary trial directory and data root, chooses a currently unused loopback port, issues and extracts a one-day token, starts a detached host, records its client environment and process id in an owner-only state file inside that directory, and waits for `GET /health` to return HTTP 200. It prints the state-file path, never the token. If an agent runner starts a fresh shell for each step, source that exact state file before running the executable journey or cleanup.

The port probe and host bind are separate operations. If the host reports `E_IO` because another process claimed the port between them, keep the initialized temporary root, choose another port, update `BOOKFLOW_BIND` and `BOOKFLOW_URL`, and retry only the host launch and state-file write.

<!-- bookflow-example: illustrative -->
```bash
set -euo pipefail

if command -v python3 >/dev/null 2>&1; then
    PYTHON=(python3)
else
    PYTHON=(python)
fi

umask 077
export BOOKFLOW_TRIAL_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/bookflow-agent-guide.XXXXXX")"
export BOOKFLOW_DATA_ROOT="$BOOKFLOW_TRIAL_ROOT/data"
export BOOKFLOW_STATE="$BOOKFLOW_TRIAL_ROOT/agent-guide.env"

if command -v bookflow >/dev/null 2>&1; then
    BOOKFLOW=(bookflow)
elif command -v uv >/dev/null 2>&1 && [ -f pyproject.toml ] && [ -f uv.lock ]; then
    BOOKFLOW_CHECKOUT="$PWD"
    export UV_PROJECT_ENVIRONMENT="$BOOKFLOW_TRIAL_ROOT/venv"
    uv sync --frozen --project "$BOOKFLOW_CHECKOUT" >/dev/null
    BOOKFLOW=(uv run --frozen --no-sync --project "$BOOKFLOW_CHECKOUT" bookflow)
else
    echo "Install bookflow-core, or run from a source-checkout root with uv and uv.lock." >&2
    exit 1
fi
"${BOOKFLOW[@]}" --help >/dev/null

BOOKFLOW_PORT="$("${PYTHON[@]}" - <<'PY'
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
export BOOKFLOW_TOKEN="$(printf '%s' "$TOKEN_DOCUMENT" | "${PYTHON[@]}" -c 'import json,sys; print(json.load(sys.stdin)["secret"])')"
nohup "${BOOKFLOW[@]}" serve --bind "$BOOKFLOW_BIND" </dev/null >"$BOOKFLOW_TRIAL_ROOT/host.out" 2>"$BOOKFLOW_TRIAL_ROOT/host.err" &
BOOKFLOW_HOST_PID=$!

{
    printf 'export BOOKFLOW_TRIAL_ROOT=%q\n' "$BOOKFLOW_TRIAL_ROOT"
    printf 'export BOOKFLOW_DATA_ROOT=%q\n' "$BOOKFLOW_DATA_ROOT"
    printf 'export BOOKFLOW_STATE=%q\n' "$BOOKFLOW_STATE"
    printf 'export BOOKFLOW_URL=%q\n' "$BOOKFLOW_URL"
    printf 'export BOOKFLOW_TOKEN=%q\n' "$BOOKFLOW_TOKEN"
    printf 'export BOOKFLOW_HOST_PID=%q\n' "$BOOKFLOW_HOST_PID"
    if [ -n "${UV_PROJECT_ENVIRONMENT:-}" ]; then
        printf 'export UV_PROJECT_ENVIRONMENT=%q\n' "$UV_PROJECT_ENVIRONMENT"
    fi
    declare -p PYTHON
    declare -p BOOKFLOW
} >"$BOOKFLOW_STATE"
chmod 600 "$BOOKFLOW_STATE"
printf 'BOOKFLOW_STATE=%s\n' "$BOOKFLOW_STATE" >&2

"${PYTHON[@]}" - <<'PY'
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

For a later shell on the same machine, source the exact `BOOKFLOW_STATE` path printed by bootstrap; the containing trial directory is mode 0700 and the state file is mode 0600. For an agent on another machine, transfer `BOOKFLOW_URL` and `BOOKFLOW_TOKEN` through the operator's normal secret channel instead. Never put the bearer token in the repository, command history, or logs. The token secret is returned only at issuance. Do not print `TOKEN_DOCUMENT` or `BOOKFLOW_TOKEN` after capture, and delete the temporary state file during cleanup.

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

The final line is a machine-readable receipt. The program has already verified that `version` is the company's resulting `info_version`, that `cursor` identifies the resumed audit event, and that `directive_code` is attached to each write. Retain those values if later work must continue from this run. Later verification can read the company with [`company show`](cli/company.md). To retrieve the event at the receipt cursor, call [`audit tail`](cli/audit.md) with `after` set to `cursor - 1` and `limit` set to 1, then require `items[0].seq == cursor`; `next_after` is the last sequence returned, while `high_water` is the lower-bound cursor used for that request. Use `audit show` with the returned event id when entry-level details are needed.

The journey expects exactly one fresh demo company. To rerun it from the beginning, stop the host, run `demo reset` against the same root, issue a new short-lived token, and restart the host. The fixed directive idempotency key is then safe because the reset created a fresh demo database.

## Trial cleanup

For the background host started above, send it SIGINT and wait for orderly shutdown:

<!-- bookflow-example: illustrative -->
```bash
# In a fresh shell, first run: source '/exact/BOOKFLOW_STATE/path/printed/by/bootstrap'
kill -INT "$BOOKFLOW_HOST_PID" 2>/dev/null || true
if ! wait "$BOOKFLOW_HOST_PID" 2>/dev/null; then
    for _ in {1..100}; do
        kill -0 "$BOOKFLOW_HOST_PID" 2>/dev/null || break
        sleep 0.05
    done
fi
kill -0 "$BOOKFLOW_HOST_PID" 2>/dev/null && { echo "host did not stop" >&2; exit 1; }
"${PYTHON[@]}" - <<'PY'
import os
from pathlib import Path
Path(os.environ["BOOKFLOW_STATE"]).unlink(missing_ok=True)
PY
unset BOOKFLOW_TOKEN TOKEN_DOCUMENT
```

Confirm that the host has stopped before removing anything. If this was an isolated documentation trial, remove only the exact `BOOKFLOW_TRIAL_ROOT` created by `mktemp`; it contains the isolated data root and, for a source run, the isolated environment. Never remove a normal Bookflow data root or a source checkout. Cleanup is intentionally not a recursive copy-paste command because the trial-root path must be inspected by the operator first. If the data root is retained, revoke the short-lived guide token with [`token revoke`](cli/token.md) when it is no longer needed.

## Customer-work vocabulary

Use the same business nouns in conversation and commands:

| Request | Operation |
|---|---|
| Write a proposal or statement of work | `proposal create` with customer, title and scope |
| Make an estimate from that proposal | `proposal estimate` with the source version and a permanent conversion key |
| Record the customer's accepted estimate | `estimate update` with `status="accepted"` and a fresh decision note |
| Make a work order from that estimate | `estimate work-order` with the accepted source version and a permanent conversion key |
| Mark the work complete | `work-order complete` with actual start/end; preview fills remaining completed quantities |
| Find the customer's work and previous versions | Each noun's `query`, `show` and `history` |

Preview writes and show the resolved scope, prices, cost visibility and destination.
Use the returned fingerprint when the authorized action must match that preview.
An operational conversion preserves its source; keep its conversion key for retries.
Inspect the returned current destination instead of creating a second document.
`estimate copy` normally means another alternative; independent new scope must be
selected explicitly. No create/copy/complete operation sends anything.

`invoice post` creates an independent service invoice. `estimate invoice` and
`work-order invoice` bill the linked source; their `sales-receipt` counterparts
record genuinely received payment for a new sale. Use `payment receive` for new
cash against existing invoices, and `payment apply` for already recorded available
credit. Shared selection drafts preserve entered/calculated amount origins across
interfaces; read [customer payment workflows](customer-payment-workflows.md) before
preparing a remittance or correcting an applied document. Delivery remains staged.
Never report a document sent when only its record was
created, or improvise an external send from a create-only request.

## Bill work in installments

Read `estimate billing` or `work-order billing` first. It identifies the current
billing owner, stable source-line IDs, previous invoices/receipts and remaining
scope. Bill the work order when the estimate has one. Use the current source
version, financial date and a new permanent `conversion_key` for each intended bill.
Keep that same key and original input when retrying the same bill.

The following are alternative additions to the ordinary conversion input. Replace
`LINE_ID` with a stable `lines[].line_id` from the billing read. Percentages mean
additional percentages of the original scope, never percentages of what remains.

| Request | Input addition |
|---|---|
| Invoice all remaining work | Omit `line_ids`, `selections` and `percent` |
| Invoice the remainder of selected lines | `"line_ids":["LINE_ID"]` |
| Invoice25% of the original scope | `"percent":"25"` |
| Invoice a selected quantity | `"selections":[{"line_id":"LINE_ID","quantity":"0.25"}]` |
| Invoice an exact net amount before tax | `"selections":[{"line_id":"LINE_ID","net_amount":"40.00"}]` |
| Invoice a line-specific percentage | `"selections":[{"line_id":"LINE_ID","percent":"25"}]` |
| Rebill one exact released installment | `"selections":[{"line_id":"LINE_ID","rebill_allocation_id":"ALLOCATION_ID"}]` |

`ALLOCATION_ID` is `revision.billing_sources[].id` from the earlier bill's detailed
revision. It must match the current source basis and be entirely released. A fresh
quantity/amount request chooses earliest free scope; an exact rebill selects the
referenced installment. Replaying a voided bill's original key returns that voided
bill and does not create a rebill.

Preview with `--dry-run`, inspect the exact gross and use its
`expected_facts_fingerprint` when posting. For a paid receipt also supply
`deposit_to`, a resolved `payment_method`, and `amount_received` equal to gross.
Changed consumption returns `E_PREVIEW_STALE` with the latest attributed billing
changes, even when the work source's version did not change.

Tax is calculated on each bill's allocated net at the captured component rates.
The total of installment taxes can differ from the quote by rounding. Remaining
tax is a forecast for billing the remaining net together, not quoted tax minus
actual billed tax. Billing does not establish operational completion.

Allocated quantities may be exact fractions: a one-microunit quote billed40c of
its100c net has quantity `"1/2500000"`, `quantity_microunits:null`, and
`quantity_fraction:{"numerator":"1","denominator":"2500000"}`. Read the quoted
quantity/rate separately. Never round such a fraction into an ordinary editable
quantity. Positive scope that rounds to zero net can accompany other charged lines;
uncharged physical scope can remain after all chargeable work has been billed.

To correct an invoice, retain an allocated line using only its sale `line_id` and
matching `item`. Remove the entire line to release its allocation. Add ordinary
independent lines for extra charges; they consume no quoted scope. The browser's
"Add an unlinked line" action opens this separate, previewed correcting write.
For a receipt with any linked-work history, a correction that changes gross must
include `amount_received` equal to the new gross, for example `"1.04"` when keeping
a four-cent installment and adding a one-dollar exempt line. This remains required
after linked lines are removed. Same-gross and metadata/no-op edits can omit it;
any supplied received total is checked. Ordinary receipt corrections keep their
existing omission behavior.

New conversion previews/results include `billing_progress` for every current
source line: `previous`, `current`, `cumulative`, and `remaining` each expose exact
quantity/fraction, original-scope percentage/fraction and net/tax/gross minor units.
Previous and cumulative tax use actual installments; remaining tax forecasts a
single bill of remaining net. These values describe that conversion's posting
boundary and exclude independent extra lines. A committed-key replay returns the
current saved sale with an empty progress list; use the billing read for current
source consumption.
Source conversion cannot exceed100% of a quoted line. If fragmentation produces
`E_VALUE_RANGE`, use its `recommended_net_amount` on that source line and continue
with remaining work; never silently drop spans to make a request fit.

### Captured work tax and complete remaining forecasts

Proposal, estimate and work-order create/update accept `sales_tax_calculation`.
Use `line_component_half_even`, `line_combined_half_up`, or
`invoice_combined_half_up`; omitted updates preserve the captured policy and origin.
`use_defaults=["sales_tax_calculation"]` explicitly selects the current company
setting. Copy/conversion carries the source policy. Details expose the captured
origin, rules and exact cells; tax ordinals are separate from payment ordinals.

Billing inspection and progress expose `forecast_basis=all_remaining_together`,
`can_bill_together`, eligibility reasons, prospective line ordinals and exact tax
attribution. A false eligibility value labels a hypothetical total: it does not
promise an executable bill. Above 200 spans per line, use the exact recommended
net amount; above 2000 spans per conversion, select fewer complete lines. Inspect
a fresh forecast after each installment. Actual installments round independently.

Rebilling preserves exact released scope and net. Invoice rounding preserves total
tax for the same scope and rates, but reversing destination order can move cents
between lines or agencies. For net5/net10 with A10%/Z5%, invoice rounding charges
2 cents: A2/Z0 with net5 first, A1/Z1 with net10 first. Line rounding charges3 cents,
A2/Z1 in either order. Preview the new attribution; old bills remain exact history.
A linked-work receipt correction needs `amount_received` when gross changes, even
after its last linked line was removed. Metadata and same-gross edits may omit it;
any supplied confirmation must equal the complete newly calculated gross.
