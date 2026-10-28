# Agent guide: one safe HTTP write

This is the shortest end-to-end path from a fresh demo to a versioned, audited HTTP write. The [installed MCP guide](mcp-guide.md) uses the same authenticated host through a three-tool stdio launcher, including receipt files and recovery.

To install Bookflow for someone and connect to it as their agent, follow [agent install](agent-install.md) instead: `bookflow setup` creates your own identity working for the owner and writes your MCP configuration.

The bootstrap bearer below belongs to the human who issued it, so do not describe this token as an independent agent identity. An agent's own identity is made with `bookflow setup` or the `agent` commands (`agent create`, `agent assign`, `agent authorize`, then `token issue --principal`).

Some changes are a person's, whatever role an agent holds: the company's closing date (set, moved or cleared through `company update`) and anything that changes who can do what (memberships with their grants and denials, roles, agent assignment and authorization, token issue, users added, activated or deactivated, passwords, permission activation, backup schedules, `demo reset`), and merging or unmerging duplicate customers, vendors, accounts or items (`customer merge`, `vendor merge`, `account merge`, `item merge` and their `unmerge`). An agent asking for one gets `E_PERMISSION` with `details.reason` `people_only` and the rule it met; ask your principal to make the change. Do not make it yourself through a shell that runs as their login (`bookflow backup schedule` typed by you is recorded as their own act): give them the command and let them run it. When you find two entries that look like duplicates (two expense accounts for the same thing, an item entered twice), say so and name both; do not post to work around it. A merge leaves everything posted as it is and reads the duplicate as the other; a person previews it with `--dry-run` and can undo it. Reads, every other company setting and ordinary posting are unchanged. A directive you record yourself is your own note, made for your principal (`person_given` is false): citing it does not replace a reason. Only a directive a person recorded is their standing instruction.

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

## Moving a company in

To bring a company in from its old books (QuickBooks Desktop IIF list exports and report CSVs: trial
balance, open invoices, unpaid bills), use the `cutover` commands, not hand-made accounts, invoices and
journals. Old documents posted by hand keep their old dates and put old income into earlier periods.

1. `cutover plan`: reads the exports and shows every mapping, write and exception. Writes nothing.
2. `cutover apply`: makes the lists, posts each open document for its open balance and one opening
   journal at the cutover date. Every write carries the source reference `cutover:` plus its outside
   id, so a rerun makes only what is missing.
3. `cutover tie-out`: compares the trial balance and the aging with the old books; the Cutover
   Clearing account ties at 0.00.

The three take the same input. Attach each export once and pass only the attachment ids, so every
call reads the same files and nothing is retyped:

<!-- bookflow-example: illustrative -->
```json
{"command": "attachment add", "reason": "Old books export",
 "input": {"record_type": "company_info", "record_id": "<company_id from company show>",
           "original_filename": "trial_balance.csv", "media_type": "text/csv", "caption": "Old books export"},
 "transport": {"input_file": "/home/me/exports/trial_balance.csv"}}
{"command": "cutover plan", "input": {"as_of": "2026-09-30",
 "files": [{"attachment": "<accounts.iif id>"}, {"attachment": "<trial_balance.csv id>"}, "...one per export..."]}}
{"command": "cutover apply", "reason": "Move in from the old books", "input": "...the same as_of and files..."}
{"command": "cutover tie-out", "input": "...the same as_of and files, plus the A/R and A/P aging summaries..."}
```

Read the plan's `summary` and `blocking` (one line per blocking exception, kept even in a compacted
result) first, fix and plan again until `ready` is true, then apply. `clearing.parts` in the plan and
in apply show what each part posts to the clearing account, so the two reconcile. Files can be planned
in pieces; apply needs the trial balance with all of them. [Moving a company in](cutover.md) lists
the exports and each step, with the worked example in full. A long plan reads in pages: pass `limit`
(and the same input), and while `has_more` is true pass `next_cursor` back as `cursor`; the counts,
`blocking`, exceptions and checks always cover the whole plan.

Give the rest of the old books with the same call when the owner has them: each bank and card
account's Reconciliation Summary, the transactions that had not cleared (Transaction Detail, Cleared:
No), Undeposited Funds' QuickReport (Cleared: No) and the 1099 Summary for January 1 to the cutover.
Then each uncleared check, deposit and charge comes in on its own with its number and payee, the
first `reconcile start` on each account follows the old books' last reconciliation (name no opening),
`deposit post` picks the checks waiting in Undeposited Funds from `deposit sources`, and the year's
`report vendor-1099-summary` includes what was paid before the move-in. Never enter those items again
by hand: they are already in, and entering them twice is the mistake the tie-out would show.

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

## Finding a transaction

Asked to find, look up or open a document ("the $412.50 check to Acme", "everything we billed Riverside
in March", "invoice 1043"), call `transaction find` first. One read searches every kind of document --
invoices, sales receipts, credit memos, statement charges, customer payments and refunds, estimates,
proposals, work orders, purchase orders, item receipts, bills, bill payments, vendor credits, checks,
card charges and credits, deposits, transfers, journal entries, sales tax payments and adjustments and
inventory adjustments -- by any mix of:

- `types`: e.g. `["check"]`; omit for every type.
- `name`: a customer, job, vendor, employee or other name, by id or full name. Part of a name finds every
  party whose name contains it (`names` in the result says which). A customer brings its jobs, and a name
  merged into another is found under the one it was merged into.
- `number`: the document's number or a check's own number, exact.
- `amount` (exact, e.g. `"412.50"`) or `amount_from` / `amount_to`, on the document total.
- `date_from` / `date_to`, `memo` (text the memo contains) and `account` (full name or id).

Each row has the date, type, number, name, account, memo, amount and `status` (open or paid for an
invoice, statement charge or bill; open or closed for an order, estimate, proposal or work order; void;
otherwise posted), with `id` and `show_command`: open the document with `<show_command> <id>`. Voided
documents are included unless `include_voided` is false; deleted ones never are. A type you may not read
is left out and listed in `withheld_types`. Results are sorted by date, then number; follow `next_cursor`
for more.

## When the account is unclear: Ask My Accountant

Every company has two accounts for entries whose right account is not yet known:
**Uncategorized Expense (Ask My Accountant)** for spending and **Uncategorized Income**
for money received. The standard profile adds them to each new company; `profile apply
standard` adds them to an older one.

When you are unsure which account an expense or receipt belongs to, do not guess. Post
it to the matching Uncategorized account and put what is unclear in the memo, in words
the person can answer ("Hardware store, no itemised receipt: tools or job supplies?").
A guessed account looks finished and is never questioned; an Uncategorized one is asked
about. `account uncategorized` lists what is waiting, oldest first, with each account's
count and amount, and the Overview shows the same. When the person answers, move the
entry by updating its own document (`check update`, `bill update`, `journal update`
and so on) to the right account; it then leaves the list.

## A customer's check comes back

When the statement shows a returned deposited item, do not void or delete the receipt and do not
re-enter anything by hand: `payment bounce` records the return in one step. Preview with `dry_run`
(it shows every document and a `facts_fingerprint`), then write it with the reason.

<!-- bookflow-example: illustrative -->
```json
{"command": "payment bounce", "reason": "Statement 07-09: returned deposited item, check 1182, NSF",
 "input": {"payment": "<receipt id from payment query>", "expected_version": 2, "date": "2026-07-09",
           "operation_key": "bounce-1182",
           "bank_fee_amount": "12.00", "bank_fee_account": "Bank Service Charges",
           "customer_fee_amount": "35.00"}}
```

The invoices the receipt paid reopen; the returned amount and the bank's fee leave the bank as two
lines on that date; the customer fee becomes an open invoice. With only `customer_fee_amount`, as
the anchor does, the fee is billed through the Returned Check Charge item to the Returned Check
Charges income account, both set up on first use; name an Other Charge or service item
(`customer_fee_item`) or the income account an item posts to (`customer_fee_account`) to bill it
elsewhere. Leave out the `bank_fee_*` or `customer_fee_*` fields when there is none. To undo it, void the returned-check refund
(`customer-refund void`); there is no un-bounce command. A receipt that was never deposited did not
bounce: unapply and void it.

## A customer pays a deposit

A deposit, down payment or retainer is a customer prepayment: money paid before the work bills,
held on the Customer Prepayments liability (set up on first use) and never income. Take it with
`prepayment receive`, naming the accepted estimate, work order or sales order it is for (as
`"sales-order 1003"`; or the customer when it is for no document yet), so the invoice made from
that document uses it by itself. Do not post it with `sales-receipt post` or `payment receive`: those
make it a sale or a receivable credit, and both commands say so when the customer holds money.

<!-- bookflow-example: illustrative -->
```json
{"command": "prepayment receive",
 "input": {"document": "work-order 1002", "amount": "200.00", "payment_method": "Check", "reference": "4471"}}
```

The receipt goes to Undeposited Funds and is banked with `deposit post` like any receipt. When the
work bills, `estimate invoice` or `work-order invoice` uses what is held by itself (oldest receipt
first; `apply_prepayment: false` leaves it held) and reports `prepayment_applied` and
`balance_due`. For any other invoice, `prepayment apply --invoice N` with the document, customer
or receipts to draw from. `prepayment show --customer` lists what is held and every draw. To
undo a draw, `prepayment unapply <credit memo>` (a reason is required; a draw inside a closed
period cannot be undone). To pay money back, `prepayment refund`. To keep part as a fee, invoice
the fee and `prepayment apply` to it, then refund the rest: held money becomes income only through
an invoice. A document holding money cannot be voided or cancelled until the money is used,
refunded or moved with `prepayment attach`. `report customer-prepayments` ties to the account on
any date.

## Writing off a bad debt

A customer who will never pay is written off with a receipt for nothing, as the anchor does: the
open balance is taken as a discount to an expense account. The customer may be inactive; do not
reactivate it. Sales tax is not reduced. A reason is required and the owner sees an agent's write-off
on `report entries-to-review`, so say why in it.

<!-- bookflow-example: illustrative -->
```json
{"command": "payment receive", "reason": "Bauer Builders closed; owner approved the write-off",
 "input": {"customer": "Bauer Builders", "date": "2026-07-31", "amount": "0.00", "operation_key": "wo-bauer",
           "discounts": [{"invoice": "2388", "amount": "442.50", "expected_version": 1}],
           "discount_account": "Bad Debt"}}
```

`discount_account` is required (an expense account such as Bad Debt; a discount account such as Sales
Discounts for a charge that is waived, not a bad debt); `applications` stays empty. To reverse it, `payment
unapply` then `payment void` the receipt.

## Acme paid 480 of a 500 invoice; write off the 20

A short payment is an underpayment, and the anchor asks whether to leave it or write it off. To write
it off, record the receipt for the cash with `underpayment` `"write_off"` and the account it goes to:
the invoice is paid in full, 480.00 to the bank and 20.00 to Bad Debt, in one receipt.

<!-- bookflow-example: illustrative -->
```json
{"command": "payment receive", "reason": "Acme short-paid invoice 1043 by 20.00; owner agreed to write it off",
 "input": {"customer": "Acme", "date": "2026-07-15", "amount": "480.00", "operation_key": "acme-1043",
           "payment_method": "Check", "reference": "5521",
           "applications": {"mode": "inline", "items": [{"invoice": "1043", "expected_version": 1, "amount": "480.00"}]},
           "underpayment": "write_off", "write_off_account": "Bad Debt"}}
```

`write_off_class` puts a class on the write-off; an early-payment discount on the same invoice goes to
`discount_account` as usual. Unapplying the invoice later takes the write-off back with it. Leave `underpayment` out and the 20.00 stays owed;
the result's `summary.underpayment.next` is then the 0.00 receipt that writes it off later. Cash
beyond the invoices stays as the customer's credit, and `summary.overpayment.next` is the
`customer-refund post` that sends it back. An agent's write-off is listed on `report entries-to-review`.

## Use the credit on job A for job B

A credit (an open credit memo or a receipt's unapplied money) belongs to the job it was written for.
To use it on another job of the same customer, move it with `customer-credit transfer`, and name the
other job's invoices in `apply_to` to use it at once (leave `amount` out of a row to pay what it owes).

<!-- bookflow-example: illustrative -->
```json
{"command": "customer-credit transfer", "reason": "Acme asked to use the Kitchen overpayment on the Bath job",
 "input": {"from_customer": "Acme:Kitchen", "to_customer": "Acme:Bath", "amount": "120.00", "date": "2026-07-15",
           "operation_key": "acme-kitchen-bath", "apply_to": [{"invoice": "1088", "expected_version": 1}]}}
```

It charges Kitchen 120.00 on an invoice that Kitchen's own credit pays (oldest credit first, or the
`credit_memo` or `payment` named) and gives Bath a credit memo for 120.00, both through the Credit
Transfer item and the Credit Transfer Clearing account (set up on first use; it nets to zero), as the
anchor's guides do it with a clearing account. It works between the parent customer and a job too, never
between two customers (pay one back with `customer-refund post` instead). `payment invoices` lists a
job's credit memos as `available_credits`; `payment query` with `has_available_credit` lists receipts
holding money. The job the credit leaves may be inactive. Never void or edit the charge invoice or the
credit memo yourself; to undo it, `customer-credit transfer-void <transfer_id>`: what the moved credit paid is
owed again and both documents are voided at the transfer's date.

## Assess this month's finance charges

Late charges follow the company's policy (`company update --finance-charge-annual-rate 18
--finance-charge-minimum 2.00 --finance-charge-account-id "Finance Charge Income"`, plus grace days,
from due date or invoice date, and charging on overdue charges); an agent does not set the policy.
Apply payments and credits first: a customer with `has_unapplied_credits` is charged on the full
overdue balance. Preview with `dry_run`, read the rows, then assess with the preview's fingerprint,
usually dated the last day of the month, before printing statements.

<!-- bookflow-example: illustrative -->
```json
{"command": "finance-charge assess", "reason": "Month-end finance charges, owner's standing policy",
 "input": {"date": "2026-07-31", "expected_facts_fingerprint": "<facts_fingerprint from the dry run>"}}
```

Leaving out `customers` assesses every proposed row; to leave a customer out or change an amount, list
the ones to charge (`"customers": [{"customer": "Acme"}, {"customer": "Birch", "amount": "1.50"}]`).
Each charge is a statement charge dated that day; assessing the same date again charges nobody twice.
To take one back, `statement-charge void` it.

## Log mileage: "42 miles to the Smith job, billable"

A trip is a vehicle, the trip end date (and start date, if it ran over several days), and either both
odometer readings or `total_miles`. Billable miles name the customer:job and the mileage item (a service
or other-charge item; its price is the price per mile). Recording a trip posts nothing.

<!-- bookflow-example: illustrative -->
```json
{"command": "trip create", "reason": "Mileage log",
 "input": {"vehicle": "Service van", "trip_end_date": "2026-10-20", "total_miles": "42",
           "billable": true, "customer": "Smith", "item": "Mileage", "notes": "Site visit"}}
```

Bill it with `trip invoice` (the trip ID, its `version` as `expected_version`, a `conversion_key` and a
date): the miles are billed exactly once, and `report unbilled-costs` lists billable trips not yet billed.
A billed trip cannot be voided or have its miles changed until its invoice is voided. To pay a driver back,
`trip reimburse` writes one check for chosen trips at the mileage rate in effect on each trip's end date
(`mileage-rate set` keeps rates by date); a trip is paid back once while that check stands. The deduction
for taxes is `report mileage-by-vehicle`; what each job was charged is `report mileage-by-job`.

## Log hours: "log my 8 hours on the Smith job yesterday"

Hours worked are time entries, and a person's week is a timesheet: one row per customer:job, service
item, class, billable and notes, seven daily hours. `timesheet save` takes the whole week and writes one
time entry per row per day with hours, so read the week first (`timesheet show`) and save it back with
the new hours added; saving the same sheet twice changes nothing. "My" is the person you act for: leave
`employee` out and their linked employee is used (a person links it once with `clock link`). You may
record time and schedule entries for any employee, as the person you act for could -- typing up
everyone's paper timesheets is ordinary work; name the employee.

<!-- bookflow-example: illustrative -->
```json
{"command": "timesheet save", "reason": "Kabe: log my 8 hours on Smith yesterday",
 "input": {"week": "2026-10-20",
           "rows": [{"customer": "Smith", "item": "Labor", "billable": true, "notes": "Rough-in",
                     "hours": [null, "8", null, null, null, null, null]}]}}
```

The hours list starts on the company's first work day (`timesheet preferences`; Monday by default).
Hours already billed stay billed: a billed cell can take more hours (they become a new, unbilled entry),
but lowering it below what was billed is refused with `E_WORK_DEPENDENCY` until the invoice is voided or
edited. Bill time as always with `time-activity invoice`; `report unbilled-costs` lists it.

Clocking: `clock in` and `clock out` record attendance as it happens (rounded per the preference, split
at midnight). That is the one exception: you may clock in or out only the person you act for (their login
linked to their employee record), on their instruction; clocking anyone else is refused -- record their
hours with `timesheet save` instead. A clock left on past the maximum shift is closed only with the `end` time the person stopped.

## Who is where: "what's Dave doing tomorrow?"

`schedule calendar` with `employee` and `view: "day"` (or `week`, `month`) and the date lists Dave's
schedule entries and the work orders he is assigned that day; `customer` instead shows one job's calendar.
`schedule create` books someone on a job or work order from a local start to an end
(`YYYY-MM-DDTHH:MM`); a double-booking still saves, with a warning naming the clash, so tell the person.

<!-- bookflow-example: illustrative -->
```json
{"command": "schedule calendar", "input": {"employee": "Dave Brazer", "view": "day", "date": "2026-10-25"}}
```

## A service item

`item create` with `type: service`, `sales_enabled: true`, a `name` and an `income_account_id` is enough
for a service sold per job: a missing `price` is 0.00 (type the rate on each invoice line), a missing
`description` is the item's name, and on books that have not turned sales tax on a missing
`sales_tax_code_id` is the non-taxable code. Anything you give is kept.

## A debit-card purchase or an online payment from the bank

A payment that leaves the bank account with no cheque behind it (a debit-card purchase, an online bill
payment, an automatic withdrawal) is `check post` with `no_number: true`: the accounting is a check's,
it takes no number from the chequebook, never enters the print queue, and stays unnumbered when
corrected. Do not type a word such as `DEBIT` as the check number: a number is unique on its account,
so the second one is refused with `E_DUPLICATE_NUMBER`.

<!-- bookflow-example: illustrative -->
```json
{"command": "check post", "input": {"account": "Checking", "no_number": true, "date": "2026-10-20", "amount": "62.40", "expenses": [{"account": "Vehicle Expense", "amount": "62.40"}]}, "reason": "Fuel, debit card"}
```

## Printing checks: "print this week's checks"

Write the checks with `to_be_printed: true` (on `check post`, or by check from a bank account on
`bill pay`, `sales-tax pay` or `customer-refund post`) and leave `number` / `check_number` out: they wait in the bank account's print queue with no number
(`check print-queue`). `check print` gives them numbers in order from `first_number`, the number on
the first check loaded in the printer, and `check print-pdf --run` returns the PDF to print. A number
already used on the account is refused, never skipped.

<!-- bookflow-example: illustrative -->
```json
{"command": "check print", "reason": "Print this week's checks",
 "input": {"account": "Checking", "first_number": "1001", "style": "voucher"}}
```

Then ask the person "Did the checks print OK?" and stop: only they know what came out of the printer,
so `check print-confirm` (all fine, or `failed_from` the first bad number), `check print-cancel` and
`check reprint` are a person's act and refused to an agent. Misprinted checks go back to the queue for
new numbers and their numbers are recorded as spoiled, which `report missing-checks` shows. A printed
check's amount, payee and bank account cannot be changed: void it and write a new one.

## Special prices: "give Acme 10% off pipe fittings through December"

A price rule changes the price a sales line defaults to when its conditions hold: items or
`item_categories`, `customers` (a customer covers its jobs) or `customer_types`, `classes`, `sales_reps`,
a `date_from`/`date_to` window on the transaction date, and `min_quantity`. Its adjustment is a percent or
amount (`kind`), `down` or `up`, from the item's `sales_price` or `cost`, rounded to the `cent`, `x.99`,
`x.95` or `dollar`. Quantity discounts are `breaks`: `[{"min_quantity": "10", "value": "5"}, {"min_quantity":
"50", "value": "10"}]` is 5% off from 10 and 10% off from 50.

<!-- bookflow-example: illustrative -->
```json
{"command": "price-rule create", "reason": "Acme year-end deal",
 "input": {"name": "Acme fittings 10% off", "customers": ["Acme"], "item_categories": ["Pipe fittings"],
           "value": "10", "date_to": "2026-12-31"}}
```

Before quoting, `price-rule preview` (item, customer, quantity, date) says what a line would cost and
why ("price from rule Acme fittings 10% off: ..."). When a price level and rules both price a line, the
company preference picks the lowest price (the default) or the first rule by `priority`
(`price-rule set-preferences`). A line priced by a rule carries it in `item_snapshot.pricing_rule`; a typed
`unit_price` overrides it. Saved documents never change when a rule does; end a deal early with
`price-rule deactivate`.

## Handing the books to the outside accountant

An agent can prepare the accountant's hand-off but never accepts an import: designating an external
accountant (`accountant add`), cancelling a copy and `accountant-copy import` are a person's steps.

Before a review, read `report accountant-review` (as_of the period end): entries to review, what waits in
the Uncategorized accounts, bank and card accounts not reconciled through the date, open receivables,
payables and undeposited funds, and the accountant's reviewed-through mark. Tell the owner what is open.

When the owner asks for an accountant's copy (for the designated external accountant; the copy's changes come
back as theirs), `accountant-copy export` with the dividing date writes an
archive and a small manifest into the company folder's exports/; Bookflow sends it nowhere, so tell the
owner the file name to hand over. While it is out, a posting dated on or before the dividing date, an
account edit or a list merge is refused (E_PERIOD_CLOSED with `reason` accountant_copy_out, or E_PERMISSION):
date the work after the dividing date, or wait. `accountant-copy status` says whether a copy is out.

<!-- bookflow-example: illustrative -->
```json
{"command": "accountant-copy export", "reason": "Q3 review by our accountant", "input": {"through": "2026-09-30"}}
```

When the accountant's change file arrives, `accountant-copy preview` (its full path) lists each change as
new, imported or refused, and why; show that to the owner and let them import. Each imported change is
posted as the accountant, and `report accountant-changes` lists everything an accountant changed.

## "Why is it slow?"

Every command's time is recorded beside the hub (never in the books, and never what was typed):
`perf report` (`since` 24h or 7d, optionally one `command` or `surface`) gives p50, p95 and max per
command and the slowest single calls, each with its SQL statement count. Quote those figures rather
than guessing; an installation administrator can turn recording off with `perf settings`.

## How are we doing against budget?

A budget is a named plan for one fiscal year: an amount per account per month, entered positive for
income and expense alike, and it never posts. For "how are we doing against budget this quarter?",
find the budget (`budget list --fiscal-year 2026`) and read budget vs actual for the quarter's dates on
the company's basis; each period has Actual, Budget, $ Over Budget and % of Budget, then a Total group,
and `totals.net_income` answers the headline question.

<!-- bookflow-example: illustrative -->
```json
{"command": "report budget-vs-actual",
 "input": {"budget": "Plan 2026", "date_from": "2026-07-01", "date_to": "2026-09-30", "columns": "month"}}
```

Actual is `report profit-and-loss` for the same dates, account for account; a month only partly in the
dates has its budget prorated by days. Report the accounts furthest over or under in money, not just
percent: % of Budget is blank where nothing was budgeted. A customer or class budget compares only the
customers or classes it budgets (name one with `customer` or `class_id`). For "compared with last year",
use `report profit-and-loss-comparison --compare previous_year` (or `previous_period`), and
`report balance-sheet-comparison` for balances.

Building a budget is a person's plan, so an agent enters amounts only when asked: `budget create
--from-actuals` copies last year month by month, `budget fill` sets a row from a starting amount with a
monthly change (`change` or `change_percent`, compounding) or copies a month across, `budget set` sets
months by hand, `budget clear` empties a row or the whole budget. To keep a report someone runs
often, `memorized-report create` with a `date_range` such as `this_fiscal_quarter`, and
`memorized-report run` it: the range is worked out again on every run.

## Depreciation and loan payments

A fixed asset is a `fixed_asset` item; its book depreciation terms come from `fixed-asset setup`
(straight line, or declining balance at 200 or 150 percent switching to straight line, salvage, life
in months, month or year periods). For "post this month's depreciation", preview with a dry run, then
post through the month end:

<!-- bookflow-example: illustrative -->
```json
{"command": "fixed-asset depreciate", "input": {"through": "2026-11-30"}}
```

It posts only periods not yet posted (debit depreciation expense, credit accumulated depreciation),
so running it twice is harmless; `fixed-asset schedule` shows each period posted or pending. A month
already closed is refused: post it with `entry: combined`, dated in an open period, if the person
agrees. Selling or scrapping an asset is `fixed-asset dispose` after depreciation through that date.

For "record the truck loan payment", `loan show` names the next payment and its split, and
`loan pay` writes the check:

<!-- bookflow-example: illustrative -->
```json
{"command": "loan pay", "input": {"loan": "Truck loan"}}
```

Principal goes to the loan's liability account, interest to its interest account and escrow to its
escrow account, from the schedule. Extra principal (`extra_principal`) keeps the payment and shortens
the loan. `loan show` reports whether the liability account ties to the schedule's balance; if it does
not, the money borrowed or a payment was recorded outside `loan pay`: say so rather than adjusting.
Tax depreciation (MACRS) is not computed here.

## Customer-work vocabulary

Use the same business nouns in conversation and commands:

| Request | Operation |
|---|---|
| Write a proposal or statement of work | `proposal create` with customer, title and scope |
| Make an estimate from that proposal | `proposal estimate` with the source version and a permanent conversion key |
| Record the customer's accepted estimate | `estimate update` with `status="accepted"` and a fresh decision note |
| Make a work order from that estimate | `estimate work-order` with the accepted source version and a permanent conversion key |
| Take a customer's order for goods (stocked parts included) | `sales-order create` with customer, date and lines; the title defaults to the customer |
| Turn the accepted estimate into the customer's order | `estimate sales-order` with the accepted source version and a permanent conversion key |
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

## Taking a customer's order

"Take an order for 10 widgets for Acme" is `sales-order create --customer Acme --date ...
--lines '[{"item":"Widget","quantity":"10"}]'`. An order posts nothing and moves no stock; the
invoice made from it does. Its show output gives each line `order.ordered`, `invoiced`,
`cancelled` and `open` (what is still to ship, the backorder), and the order's `order.progress`
(open, partly_invoiced, invoiced, closed, voided) with its exact open amount.

- The customer no longer wants part of a line: `sales-order update` with that line's
  `cancelled_quantity`, or `close: true` to cancel whatever is still open on it. Cancelling back
  to `0` reopens it. Only what is not yet invoiced can be cancelled.
- Nothing more will ship: `sales-order update --status closed` with a reason; `--status open`
  with a reason brings back what was open. A wrong order is `sales-order void` with a reason; it
  is refused while an invoice consumes any line, so void or remove that invoice first.
- An accepted estimate becomes either the order (`estimate sales-order`) or a work order
  (`estimate work-order`), never both, and its billing then goes there. Keep the conversion key
  for retries: the same key returns the same order; a different request under it is refused.
- Estimates, proposals, work orders and orders may carry stocked parts. A stocked line is billed
  by quantity, never by a percent or an amount, and the invoice made from it issues the stock.
- `company update --sales-orders-enabled false` stops new orders; existing ones stay readable.

## Billing what shipped on an order

"Bill Acme for what shipped today" is `sales-order invoice <number> --expected-version <v>
--conversion-key <new key> --date <today>`. With no selection it bills what can ship: each
stocked line up to what is available to this order (on hand less what other orders and work
orders commit), and the order's other open lines with it. If nothing can ship it refuses and lists
each line's open quantity and availability: choose `--all-open true` (bill every open quantity
whatever the stock), `line_ids` or `selections` (a quantity per line, naming the line or its
item). The invoice is taxed at its own date's rates; `order_tax` shows the difference from the
order's quote. Money held on the order or its estimate is applied unless `apply_prepayment` is
false. A retry with the same key returns the same invoice.

Voiding, deleting or updating away an order-made invoice line reopens that quantity on the order
by default; pass `release_to_order cancel` to cancel it on the order instead. The preview says
which. A credit memo never changes the order. `invoice post`, `sales-receipt post`, `payment
receive` and every other `<kind> invoice` (estimate, work order, time) warn when the customer has
an open order and give the command to use instead.

Before typing an invoice for a customer, check `report open-sales-orders --customer <name>`: if
the goods are on an open order, bill the order with `sales-order invoice` rather than retyping its
lines, or the order stays open, its stock stays committed and its deposit is not used. Each
order-made invoice line shows the order's ordered, previously invoiced and backordered quantities
(`order` on the line in `invoice show`, and on the printed invoice).

## Ordering what an order is short

`sales-order purchase-order <number> --date <d>` (or `work-order purchase-order`) writes one
purchase order for what the document is short: its open stocked quantity not on hand, not already
on order for it and not needed first by other open documents. Lines default to those whose
preferred vendor is the vendor; with several vendors it refuses and lists each vendor with its
lines, so run it once per vendor with `--vendor`. Running it again before anything changes orders
nothing. Explicit `lines` (item or line_id, quantity) order any amount, spares included. Each
document line shows the purchase-order lines ordered for it and what is still to arrive; each
purchase order shows what it covers. Amending a purchase order down, receiving it or voiding it
brings the shortfall back by exactly that.

## What is available to sell

`report stock-status` and `item show` give each stocked item its quantity on hand, committed (the
open quantity on open sales orders and work orders, a complete but uninvoiced work order included),
available (on hand less committed) and on order, with the next expected delivery. The company
preference `available_deducts_sales_orders` (on by default) decides whether available deducts
committed. An order or work-order preview gives each stocked line its on hand, committed elsewhere,
on order and available to it, and warns when it ends short; an ordinary invoice or sales receipt
that takes committed stock warns and names the documents. Neither refuses. `report
open-sales-orders` (`--by customer` or `--by item`, `--short-only`) lists what customers have
ordered and not yet received.

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
## Choose master-list columns and filters

Use `<noun> query options` with `kind` of `columns`, `filters` or `sorts`. Discovery returns a bounded page, complete `default_columns`, and `next_cursor`; follow every needed page. To find a custom choice, use the same command with `kind: "choices"` and its stable `definition` ID. Choice results use stable option IDs as keys, with current labels and activity. `query` searches labels; `keys` retrieves an explicitly selected bounded set. Inactive definitions and choices remain discoverable by default. Metadata reads use current company authority and never change records.

For example, `bookflow customer query --columns '["full_name","email"]' --company "Demo Plumbing Co" --json` returns stable row identity plus `values`, ordered `columns` descriptors, page-size `count`, and a separate `matching_total`. Omitting `columns` retains the previous summary/reference output shape. Reference projections do not accept selected columns. Public catalogs expand declared aliases and stored fields; they do not expose arbitrary database columns. Monetary values contain exact decimal strings and currency; do not convert them through floating point.

Unit prices and costs (an item's price, cost and vendor purchase cost, a per-item price-level price, and every line's `unit_price`, `unit_cost`, `rate` or `estimated_unit_cost`) may carry up to three places beyond the currency's minor unit: five decimal places in USD, such as fuel at `3.459` or a screw at `0.0125`. More is refused with `E_AMOUNT_PRECISION` (`allowed_places`). The line amount is quantity times the exact price, rounded once to the cent, half to even: 12.5 x 3.459 = 43.24. In output, a price's `amount` is exact text and its `minor_units` is only the nearest cent, so read the `amount` (stored rows add a `<prefix>_fraction` column, thousandths of a minor unit, null when the price is whole). Line amounts and totals are always whole minor units.

Pass typed custom criteria through `custom_filters`, for example `[{"definition":"<definition-id>","kind":"bool","operator":"eq","value":false}]`. Text supports `eq`, `ne`, `contains`; number/date also support `lt`, `lte`, `gt`, `gte`; bool/choice support `eq`, `ne`. Numbers are canonical decimal strings with at most nine fractional digits and signed-64-bit nano-unit range. Dates are canonical ISO dates. Choice values are stable choice IDs belonging to the definition, not labels. Text equality is literal and case-sensitive; containment uses normalized Unicode search. A retired choice never acquires matches from a later option reusing its label. Existing in-use choice rename/retirement rules remain unchanged.

For absence, use `{"definition":"<definition-id>","kind":"presence","operator":"is_missing"}` or `is_present`, with no value. Every criterion combines by AND with ordinary filters. `ne` excludes missing values. Empty text, false and zero are present values. Cleared values are absent; values of inactive definitions remain readable, searchable, selectable and filterable without being reactivated.

A collection-valued selected column returns an exact count and `query children` coordinates (`record`, `column`). Use that command's continuation to read all public child rows, at most 200 per page; never interpret a count as a complete embedded collection. Full legacy `show` and `list` retain their existing contracts. The browser presents readable collection tables and continuation links.

Keep page `count` distinct from `matching_total`. A cursor is bound to the current company, authority, criteria and selected column order. On `E_QUERY_STALE`, discard accumulated pages, remove the cursor and repeat the same controls. A changed query/projection cannot reuse a continuation. Browser sorting/filtering/column controls persist in the URL and do not write preferences or financial data. Named balance filters accept amounts in the displayed currency; existing `field=value` balance filters retain their integer-minor-unit wire meaning.

Python callers may use `client.run("customer query options", {"kind":"columns"}, company=...)` and `client.run("customer query", {"columns":["full_name","email"]}, company=...)`; HTTP and MCP use the same registered command names and JSON input. Registry discovery is not itself evidence of a completed actual-MCP usability trial.

For bounded current reference resolution, master `query` accepts optional `ids` (1–64
stable record IDs). Combine it with `projection: "reference"` and
`include_inactive: true` to retrieve current labels and activity, without full record
collections. It intersects every other criterion and remains subject to ordinary
company authority and cursor checks. Omitted `ids` preserves ordinary browsing.

## Importing invoices, bills or payments from a file

"Load these 40 open invoices from the spreadsheet" is `import check --kind invoice` on the file (every
problem at once, by column and row), then `import preview`, then `import run` until done. Rows sharing an
invoice number are one invoice, its lines in file order; a payment file is applied to invoices by their
numbers. An unknown customer is named, never made, unless the owner asked for new customers to be created
(`create_missing`, and never one that looks like an existing name). A plainly named import just runs. An
import that sets how the business works from then on (a saved mapping next month's file will use, customers
or lists it creates, an opening) gets one check of its exact details, from the preview, with the owner
before `import run`. `import reverse --dry-run`, then `import reverse`, undoes a whole run; it is refused,
naming the payment, while a payment is applied to one of its invoices.

## Reading a bank statement from a file

When the owner says the statement is in your input folder, attach the file once and import it by its
attachment id; `reconcile import` itself takes no `transport.input_file`, and pasted text works too
(`content`). The attachment is the same one you would give `cutover`:

<!-- bookflow-example: illustrative -->
```json
{"command": "attachment add", "reason": "July checking statement",
 "input": {"record_type": "company_info", "record_id": "<company_id from company show>",
           "original_filename": "checking-2026-07.ofx", "media_type": "application/x-ofx"},
 "transport": {"input_file": "/home/me/input/checking-2026-07.ofx"}}
{"command": "reconcile import", "reason": "Reconcile July checking",
 "input": {"account": "Checking", "attachment": "<attachment id from the reply>", "start": true}}
```

Leave out `start` to read the statement without opening a reconciliation. Each line comes back matched,
suggested, unmatched (enter it, then import again) or already reconciled. Import again after entering
what the statement shows; nothing already true is repeated. Then `reconcile preview` and
`reconcile finish`.

A long statement can be read in pages: pass `limit`, and while `has_more` is true pass `next_cursor`
back as `cursor` with the same file. The counts, the ticking and `cleared_without_line` always cover
the whole file, whichever page you read.

A statement's summary box (its closing date and new balance) often comes as a separate text file. It is
not a statement file and `reconcile import` refuses it. Attach it the same way and read it with
`attachment text <attachment id>`, which returns a small text file's content (at most 64 KiB); take
`statement_date` and `ending_balance` from what it says, never from the books.

## Books that start in the middle of a statement

When the books begin part-way through a bank statement (the opening balance entered as a journal on the
start date), reconcile from zero: `reconcile opening start` dated the day before the account's first
movement with `entered_balance` 0.00, then `reconcile start` for the statement and `reconcile mark` with
`all`, which ticks the opening-balance entry with the rest; the statement's beginning balance is then
not compared with anything the bank printed. That is the recommended route. The other route, an
opening dated on the start date with the opening balance as `entered_balance`, works only once the
opening-balance entry is marked `covered` on the opening draft (`reconcile mark` on the opening with
`entries`, action `covered`); until then the opening is not proven (`E_RECONCILIATION_OPENING_UNPROVEN`).
`reconcile mark --all` on the statement then leaves that entry out, as the opening's.

## A draft whose ticked entry changed

If an entry ticked on an open draft is corrected afterwards, `reconcile preview` refuses with
`E_RECONCILIATION_SELECTION_STALE` and lists what changed (`details.stale`). Take the ticks back with
`reconcile mark` and `all: true, all_action: "unmark"` (or untick just that entry), then tick again and
preview. A draft nobody will finish is dropped with `reconcile discard <draft id>` and a reason: it is
canceled, kept with its history, and leaves the Overview and the owner's entries-to-review.

## A reconciliation that will not tie

Never force a statement to tie. `reconcile finish` with `adjustment` (a journal to
Reconciliation Discrepancies for whatever is left) is for a person only and refuses an
agent with `E_PERMISSION`. When the difference will not reach zero, leave the draft open
and tell the owner the account, the statement date and the remaining difference from
`reconcile preview`.

## An item that should have been stocked

`item convert ITEM --to inventory` turns a non-inventory part or other charge into an inventory
part. It is one-way and a person's act: an agent is refused with `E_PERMISSION`, so tell the
owner which item and why, and suggest `--dry-run` to see the accounts it will carry. Nothing
posted changes; stock starts at 0 on the conversion date and is counted with `inventory adjust`.
After a conversion, no stock line for that item may be dated before the conversion date.

## We built 5 water heater kits today

A finished item made from parts is an inventory assembly; record the work with `assembly build`.
Preview it first: the dry run lists each component with what one kit needs, what is on hand that
day and what pending builds hold, and `max_buildable`, the most the parts allow.

<!-- bookflow-example: illustrative -->
```json
{"command": "assembly build", "reason": "Owner: we built 5 water heater kits today",
 "input": {"item": "Water Heater Kit", "quantity": "5", "date": "2026-07-14"}}
```

The parts leave stock at their average cost and the kits arrive at the sum; a service or other
non-stock part on the bill of materials adds its purchase cost. A component that is itself an
assembly is taken from stock, never built for you: build it first. More than the parts allow is
refused with the shortages and `max_buildable`; if the owner wants the build held until parts
arrive, send `"pending": true` (nothing posts, the parts are reserved) and later `assembly finalize
BUILD`. `assembly list --status pending` is the pending builds report. Correct a build with
`assembly update` (quantity, date, memo); `assembly void` or `assembly delete` takes it back. Never
use `inventory adjust` to fake a build: it would not move the parts' cost into the kit.

## Move 5 widgets from the van to the warehouse

Stock lives at inventory sites: a warehouse, a service van, a consignment location. Every company starts
with one, Main, the default; `inventory-site list` names them. Moving stock between sites is
`inventory-transfer post`, never `transfer post` (that moves money) and never `inventory adjust`:

<!-- bookflow-example: illustrative -->
```json
{"command": "inventory-transfer post", "reason": "Unloaded 5 widgets from the van",
 "input": {"from_site": "Van 1", "to_site": "Warehouse", "lines": [{"item": "Widget", "quantity": "5"}]}}
```

A transfer posts nothing: an item has one average cost company-wide, so value and the general ledger
do not move. The result gives each site's quantity after it and warns, naming the other sites, when the
van goes below zero. Every stock line (`invoice`, `sales-receipt`, `credit-memo`, `bill`, `check`,
`card-charge`, `vendor-credit`, `item-receipt`, `inventory adjust`, `assembly build`, sales-order,
work-order and purchase-order lines) takes `site`; a document may name one `site` for all its lines.
Leave it out and the line uses the default site; a return goes back where its sale took from, and a
receipt against a purchase-order line arrives where that line said. `report quantity-by-site` and
`report stock-status --site` read stock by site.
## What needs doing today?

Start with `reminders`: one heading per kind of work with its count and total -- memorized
transactions due (reminder slots, and missed automatic ones the company left for a person),
memorized entries that need a person (blocked: a closed period, an inactive account, an owner who
lost the right to post), to-dos due, overdue notices ready to send, overdue invoices and bills to
pay. Then `todo list --mine`: the to-dos assigned to you, soonest first.

<!-- bookflow-example: illustrative -->
```json
{"command": "todo list", "input": {"mine": true, "due_by": "2026-11-03"}}
```

Do each one, then `todo complete TODO`. A to-do whose `source_ref` begins `memorized-occurrence:`
was assigned by the scheduler because an automatic entry failed: its notes carry the error; fix
the cause and `memorized retry OCCURRENCE`, or `memorized skip OCCURRENCE` if the owner says it
should not be entered. Leave work for a person with `todo create --assignee USERNAME` and a
reason, linked to the customer, vendor, document or memorized transaction it is about.

The host enters automatic memorized transactions and prepares overdue notices by itself while
`bookflow serve` runs: do not run `memorized process` just because it is a new day. Overdue notices
are never sent by Bookflow. `notice list` shows the ones ready, each with `print_path` (its PDF)
and the letter text; sending one is the owner's decision. Once a person (or a delivery step the
owner set up) has sent it, `notice mark-sent NOTICE --method email`; if the owner says not to,
`notice dismiss NOTICE --note "why"`. A dismissed notice is not prepared again until the next
notice period.

## When to ask, and when to just do it

A clear task runs: "post that invoice to John and email it to him" names the acts, so post it and draft and
send the email with no "are you sure?". Missing details get a question, never a guess: "send the invoice when
the guys are done" does not say which job, which invoice or when "done" is, so ask for those and do nothing
yet. A broad change that sets how the business works from then on (a batch to every customer, a schedule
turned on, new terms for everyone) gets one check of its exact details: run it with `dry_run`, say what it
will do in plain words (who, how many, what, from when) and run it on the person's yes. One check, of the
details; never a second "are you sure". Ask too when you would be inferring a damaging act nobody named.

## Emailing a document

No email leaves this machine until the owner sets up a sending connection (`delivery-connection`); until
then messages wait in the outbox, ready, and `delivery run` reports "no sending connection". For "email
Riverside their invoice DEMO-PIPE-INV-1": `delivery draft --kind invoice --document DEMO-PIPE-INV-1`, read
back the returned To, Subject and body (change them with `delivery update` if asked), then `delivery send`
on the message. Tell the person whether it is waiting for their go (you lack `delivery.send`:
`waiting_for_go` is true and `delivery approve` is theirs) and that it will not go out until a sending
connection exists. A customer with no address drafts with a warning and no To: ask the person for the
address and add it with `delivery update --to`, never invent one. A statement takes `customer`, `date_from` and `date_to`.

"What is waiting to go out?": `delivery query --status ready` (and `--status draft` for unreleased drafts).
To send one by hand meanwhile, `delivery export` returns a `.eml` file (a zip for several) with the PDF
attached, to open in a mail program. "I emailed it myself" is recorded by `delivery mark-sent MESSAGE
--method mail-program`, which needs `delivery.mark-sent`: without it, name the message and ask the person
to mark it sent. The outbox then shows "Sent by hand", never "delivered".

## Emailing customers in a batch

Nothing leaves this machine until the owner sets up a sending connection; messages wait in the outbox.
For "send statements to everyone who owes us", run `delivery batch --kind statement --period 2026-09
--balance-over 0 --method email --dry-run` and show the person exactly who would get what and who is
skipped and why (no address, prefers mail, already in the outbox or sent) before running it without
`--dry-run`: a batch is a broad change, so the person confirms its exact details once, from that
preview. Run it with the preview's `fingerprint`, so it does exactly what was shown: if the set changed
since, it is refused, and you preview again. If the period, kind or customers are not given, ask; never
guess them. A batch drafts 10 messages per call and returns `next`; call `delivery batch` with it until
`state` is `done`. An agent's batch with `send` waits for a person's go; `delivery approve --batch`
approves only messages still exactly as the batch drafted them, and names the rest. `delivery
batch-query` lists unfinished batches; `delivery batch-cancel B-1001 --reason "..."` cancels a whole
batch, naming any message that was already released or exported.

## Recurring email schedules

"Email statements to everyone with a balance at each month end": `delivery-schedule create --name
"Month-end statements" --kind statement --period month-to-date --balance-over 0 --frequency monthly
--start-date 2026-11-30 --mode send`. Ask for anything missing (which documents, how often, the first
day); never guess. The host runs each slot once; after it was off, only the latest missed slot runs, and
only within `catch_up_days`. A `send` schedule you write stays a draft: its runs leave messages waiting for
a go and raise one to-do. Making it live (`delivery-schedule live`) is your principal's act (it needs
`automate.delivery`): run it with `--dry-run`, show them the check (selection, cadence, first run, who it
would reach today), and they turn it on with its fingerprint as `confirm`. Your later change to what or
when it sends returns it to draft. `delivery-schedule pause` stops it; `show` lists its runs.

## A scanned barcode

Pass a scanned code as `item`: every `item` field resolves an id, then a name, then a barcode, so
`{"item": "0012345678905", "quantity": "2"}` on an invoice line bills the item carrying that code, with
no other syntax. A code two active items share is refused naming both; name the item instead. For
anything else about a code (what it is, where it is, which line fields to use) call `barcode lookup
--code`; an unknown code is not an error, and the answer says how to assign it (`item update <item>
--barcode <code>`). `barcode generate` gives items without a code an EAN-13 of the company's own
(prefix 2); existing codes never change, and a code already on another active item is refused.

## I counted the van: 12 widgets and 3 elbows

A stocktake is a count document, never a hand-worked `inventory adjust`: the count keeps what was
counted, and the difference from the books is worked out for you at the count's place in its day.

<!-- bookflow-example: illustrative -->
```json
{"command": "inventory-count create", "reason": "Van count", "input": {"site": "Van 1"}}
{"command": "inventory-count enter", "reason": "Counted the van",
 "input": {"count": "C-1", "entries": [{"item": "Widget", "quantity": "12"}, {"item": "Elbow", "quantity": "3"}]}}
{"command": "inventory-count post", "reason": "Van count", "dry_run": true,
 "input": {"count": "C-1", "expected_version": 1, "adjustment_account": "Inventory Shrinkage"}}
```

`create` lists what the books hold at the site (name `items` or a `category` to count fewer). `enter`
appends; a person scanning and you typing can count the same count at once, and an item not on the
sheet adds a found line. Uncounted lines are no part of the count, so enter 0 for a place that is
empty. The dry run of `post` shows each item's difference and its value at the average and gives a
`preview_fingerprint`; post again with it. Less posts as an issue, more as found stock at the average.
Paperwork for that day entered later -- the jobs you bill at night -- needs nothing more: it lands
before the count, which is derived again, so the van stays as counted and only the true shrinkage
posts. A sale that really happened after the count says `"count_position": "after"`. A count that was
wrong is voided (`inventory-count void`), never adjusted around. `inventory adjust` takes `lines`
(1-200, each with `quantity_change` or `new_quantity`) for many items at once; an increase with no
value is valued at the running average. `report physical-inventory-worksheet` prints a blank sheet
and `report inventory-valuation-detail` shows each movement's effect on quantity and value.

## I counted bin A3, and what should we count this week?

A site that tracks bins (`inventory-site update <site> --tracks-bins true`) locates its stock to bins up to
three levels deep (`bin create --name 3 --parent A` makes A:3). Every stock line, transfer line, count entry,
`inventory adjust` line and `inventory assign` takes `bin` (a transfer line `from_bin` and `to_bin`); a line
naming none goes to the item's default bin at the site (`item update --site-settings
'[{"site":"Warehouse","default_bin":"A2"}]'`), a correction keeps the bin its stock stood in, and a return goes
back to its sale's bin. Moving stock between two bins of one site is an `inventory-transfer post` with the same
`from_site` and `to_site`: it posts nothing. A scanned bin label is a `bin` anywhere a bin is named, and
`barcode lookup` answers `kind` `bin`.

<!-- bookflow-example: illustrative -->
```json
{"command": "inventory-count create", "reason": "Count bin A3", "input": {"site": "Warehouse", "bins": ["A3"]}}
{"command": "inventory-count enter", "reason": "Counted bin A3",
 "input": {"count": "C-2", "entries": [{"item": "Widget", "quantity": "12"}, {"code": "0012345678905", "quantity": "3"}]}}
{"command": "cycle-count due", "reason": "What to count this week", "input": {"site": "Warehouse"}}
{"command": "inventory-count create", "reason": "This week's cycle count", "input": {"site": "Warehouse", "cycle": true, "limit": 25}}
```

A bins count lists exactly what the bins hold (and the bins inside them); entries naming no bin count in the
one bin named. Each item's cycle class (A every 30 days, B 90, C 365, stored on first use; `cycle-class`
changes them) makes it due at a site once that many days have passed since its last recorded count there, or
when it was never counted there. `cycle-count due` lists the due items, most overdue first; a `cycle` count
takes the next ones, and recording it moves them on. `cycle-count classify --dry-run` proposes classes from
the cost of goods each item issued (the items making 80% are A, the next 15% B), and running it sets them.
`report items-by-bin` shows each bin's items, and the bins adding up to the site.

## Which customer got water heater WH-1?

An item with `tracking` `serial` carries a serial number per unit; one with `lot` carries lot numbers
and their expiry. Every stock line (and a transfer line, a count entry, `inventory adjust` and
`assembly build`) takes `serial_numbers` (one per unit, up to the quantity) or `lot` (with `lot_expiry`
when the lot is new). Name only numbers you were given: a unit whose number you do not know is left
out and stays unassigned, and a person or you number it later with `inventory assign`, either units on
hand or the units one document line moved:

<!-- bookflow-example: illustrative -->
```json
{"command": "report serial-lot-history", "reason": "Warranty call", "input": {"number": "WH-1"}}
{"command": "inventory assign", "reason": "Serial read off the job sheet",
 "input": {"item": "Water Heater", "document": "invoice 1001", "serial_numbers": ["WH-7"]}}
```

The history lists every move of the number in order with its document and customer or vendor; its last
sale row answers the question, and `serial-number show` says where it is now. A number already in stock
is refused, and one that has left is refused naming the document that took it; a number never on file
is accepted with a warning and shows -1 until its receipt. A return names no numbers when its sale took
them all: they come back. An outgoing sealant line that names no lot leaves unassigned, and the result
names the earliest-expiring lot there; name it as `lot` to take it. `report serial-lot-stock` lists
numbers in stock (with `expiring_by`), and `report lot-trace` answers a recall: every vendor and
customer of a lot, and the builds it went into. A scanned serial or lot label works in `barcode lookup`.

## The freight bill for last week's delivery came in at $90; spread it over what came in

Freight, duty and broker's fees that belong in the cost of stock go to the **Landed Cost Clearing** account
(`landed-cost setup` makes it once). Enter the freight bill as an ordinary bill with an expense line on that
account; a vendor credit for freight on it is a freight credit. Then find the charge and spread it over the
receipts the stock came on -- item receipts, or the bills, checks and card charges whose item lines brought it in:

<!-- bookflow-example: illustrative -->
```json
{"command": "landed-cost charges", "input": {}}
{"command": "landed-cost post", "reason": "Freight for last week's delivery", "dry_run": true,
 "input": {"charges": [{"document": "FRT-88"}], "targets": [{"document": "IR-1001"}, {"document": "IR-1002"}],
           "basis": "quantity"}}
```

`basis` is `quantity` (the default), `value` (each line's own cost, before any landed cost), `weight` or
`volume` (the items' `unit_weight` or `unit_volume`, set with `item update`), or `amounts` (each target's
`amount`, summing exactly to the charges; the only basis that may also send a share to an expense account with
`account`). The dry run shows each target's share and how much of it stays in stock and how much was issued
before the allocation date. Stock sold before then gets a forward true-up to cost of goods sold dated at the
allocation; nothing earlier moves, and a date in a closed period moves to the first open date. Allocate part of
a charge with `amount`; `landed-cost void` takes an allocation back. A freight bill, or a receipt, an allocation
names cannot be voided or reduced until that allocation is voided (E_WORK_DEPENDENCY says which).

## Costing method: average or FIFO

Stock is costed by weighted average unless a person switched the company to FIFO. `costing-method show` says
which method is in force and lists every switch; `report inventory-layers` lists each item's cost layers on a
date (under FIFO a sale takes the oldest layer first; under average each item is one row). Changing the method
is a person's act, on the accountant's advice: `costing-method set` and `costing-method withdraw` refuse an
agent (E_PERMISSION). When asked to switch, say so and leave it to your principal. Everything else -- sales,
receipts, returns, counts, freight -- works the same under either method; replay costs it.

## Ship Acme's order now, bill it later

A warehouse that ships before it bills records the shipment on its own. `shipment post` takes the order and ships,
on the ship date (default today), what it still has to ship up to what is available to it; name `lines` (an order
line's `order_line` or an `item`, a `quantity`, and `bin`, `lot` or `serial_numbers` where they matter) to ship
less or past what is on hand. The units leave stock at the ship date and their cost waits in **Shipped Not
Invoiced**, an asset, until an invoice bills them; nothing reaches income or receivables. What is not shipped stays
on the order to ship (its backorder); no backorder document is made.

<!-- bookflow-example: illustrative -->
```json
{"command": "shipment post", "reason": "Shipped Acme's order", "dry_run": true, "input": {"sales_order": "1001"}}
{"command": "shipment invoice", "reason": "Bill last week's shipment", "input": {"shipment": "1004", "date": "2026-12-12"}}
```

Bill shipped goods with `shipment invoice <number>`, or `sales-order invoice` with no selection (it bills shipped
units first): that invoice moves no stock and moves their cost to cost of goods sold, so profit and loss reads as if
the invoice had shipped them. **Never retype shipped goods on `invoice post`**: that issues them from stock a second
time. `invoice post`, `sales-order invoice` and `payment receive` warn when the customer has shipments not yet
invoiced and give the command. An invoice may not be dated before the ship date. `sales-order show` gives each line
`shipped`, `shipped_not_invoiced` and `to_ship`; committed stock is what is still to ship. `shipment update` corrects
date, lines, places or tracking (carrier and tracking numbers are text only); `shipment void` puts the units back,
and both are refused below what an invoice billed (void or correct that invoice first; its cost goes back to
Shipped Not Invoiced). `report shipped-not-invoiced` lists what waits and always equals the account.

**Goods sent back.** Units a shipment took that no invoice has billed come back with `shipment return <shipment>`:
name the `lines` (`shipment_line`, its position as `shipment show` lists it, and a `quantity` in the base unit; serial
numbers or a lot the shipment took), the `date`, and `reopen` (default true: they are to ship again; false cancels
them on the order). They go back to the bin they left, worth their share of the shipment line's cost, out of Shipped
Not Invoiced. Units an invoice billed come back on a credit memo naming the invoice line (`credit-memo post` with
`source_invoice` and `source_line`), as any return: the order stays invoiced. `shipment-return void` undoes a return.
While a return stands the shipment's stock is not restated or voided; to take units off a shipment, record a return.

<!-- bookflow-example: illustrative -->
```json
{"command": "shipment return", "reason": "Acme sent one back before we billed it", "input": {"shipment": "1004", "lines": [{"shipment_line": "1", "quantity": "1"}]}}
```

**Picking first (optional).** Pick lists are a layer on shipping that a company turns on (`company update
--pick-and-pack-enabled true`, an admin's setting); while it is off every `pick-list` command answers
E_FEATURE_DISABLED and you ship straight from the order. On, `pick-list create` takes `orders` (one list can cover
several) or `ready: true`, and lists each order line's units by bin in pick order, the earliest-expiring lot
suggested; what the site cannot give is a short line. `pick-list enter` records what was picked (a `line`, an `item`
or a scanned `code`, with `bin`, `lot`, `serial_number` and `quantity`); `shipment post --pick-list <list>` ships what
was picked for one order (name `sales_order` when the list covers several) from the bins and lots picked, and what was
not picked stays to ship. `pick-list close` ends picking; `pick-list cancel` releases the list.

<!-- bookflow-example: illustrative -->
```json
{"command": "pick-list create", "reason": "Pick everything that can go out today", "input": {"site": "Warehouse", "ready": true}}
```

**Packing (optional) and shipping facts.** With the same company setting on, `shipment create` makes a draft (from
the order, its `lines` or a `pick_list`) that moves nothing; `shipment pack <n>` boxes a draft or a shipped shipment
and posts nothing: `--unit WH-1001 --box 2` puts that unit (a serial number, lot or item code) in box 2, box 0 takes
it out, or `packages` gives the whole packing; a box weighs its contents (item `unit_weight`) plus tare, or its
measured `weight`. `shipment post --shipment <draft>` ships it with its boxes. Whatever the setting: tracking numbers,
`freight_amount` (and `freight_item`, billed once by the shipment's first invoice), `shipment post --invoice` (the shipment and
its invoice in one transaction) and the printed `packing-slip` (no prices).

<!-- bookflow-example: illustrative -->
```json
{"command": "shipment pack", "reason": "WH-1001 went in box 2", "input": {"shipment": "1004", "unit": "WH-1001", "box": 2}}
```

**What can ship.** `report order-fulfillment` is the fulfillment worksheet: each open order's status (`to_pick`,
`picking`, `picked`, `packed`, `partly_shipped`, `shipped`), what it has to ship and what `shipment post` would ship
now, by line and site; `--status to_pick,picking` answers "what's left to pick?", `--can-ship`, `--complete-only` and
`--newly-shippable <date>` narrow it. `report shipments` lists shipments with boxes, weights and tracking numbers.

<!-- bookflow-example: illustrative -->
```json
{"command": "report order-fulfillment", "input": {"status": "to_pick,picking"}}
```

## Stock sent back to a vendor

Enter it as one `vendor-credit post` with `items`: one row per thing sent back, each with
`item`, `quantity` and either `unit_cost` (what the vendor credits for one) or `amount` (the whole
line), plus the bill's optional `customer`, `billable` and class. A row may name the receipt line it goes
back against as `receipt_line` (the `line_id` from `bill show` or `item-receipt show`): under FIFO the units
leave that receipt's cost layer first; under average it is only recorded. Do not take the stock out with
`inventory adjust` into a clearing account and then credit the clearing account: that costs the
units at the average, not at what the vendor credited, and leaves two documents where one credit
note arrived. `expenses` rows (an account and an amount) stay for things that are not items,
freight or a restocking fee, and the two tabs can be used together.

A stocked item credits Inventory Asset the credited amount and takes the quantity off the shelf
on the credit's date. The credit decides what the units were worth, so the average cost of what
stays moves; a credit of 4 at 42.10 against an average of 41.55 leaves the rest costing a little
less. Any other item credits the expense account its purchase side names. Applying the credit
to bills and paying are unchanged (`vendor-credit apply`).

Only stock on hand on the credit's date can go back. A refusal names the item, the date and
`items`: send back no more than is there, or, when you are returning all of it, credit exactly
what it is worth (the refusal prints that figure). `vendor-credit update` with `items` replaces
that grid, and one left out keeps its captured rows and the stock they moved; `vendor-credit
void` puts the stock back at the value it left at. A return dated before later sales recosts
those sales with their own dated entries, as a backdated bill does.

## The supplier billed part of an order

"The supplier billed us for 4 of the 10 on PO 1007": read the order with `purchase-order show
1007`; its `line_tracking` gives each line's position, ordered, received, billed and open
quantity. Enter the bill against the order with only that part:
`bill post` with `purchase_order` 1007, the bill `date` and `order_lines` [{`line`: "1",
`quantity`: "4"}] (a line is its position or its `line_id`; leave `quantity` out to take all
that is open; give `unit_cost` when the vendor charged a different price; an account line takes
`amount`). The bill is exactly those lines and it receives what it bills. The order stays
`partly_received` with 6 open, the open purchase orders report shows the rest, and a later bill
takes the 6. Asking for more than is open is refused with what remains. Goods that came in
before the bill go through `item-receipt post` against the order, then `bill post` with
`receipts`. To correct a bill entered this way, void it (its 4 return to the order) and enter it
again. When the rest is never coming, `purchase-order close-lines 1007 --lines '["1"]'` closes the line
(`reopen` undoes it); the order closes when every line is received or closed.

## A deposit of a whole month of checks

One deposit holds up to 2,000 rows (receipts plus other money). List what is waiting with
`deposit sources` page by page (`limit` 200, then `cursor`), and post them in one `deposit post`.
Read a large deposit back with `deposit show` for its totals and counts and `deposit items` for
its rows, 200 per page. A deposit of 1,000 checks takes a minute or two to post or void, because
every receipt's history is checked; run it once and wait for the answer rather than retrying.
Known limit: reconciling a bank account that holds such a deposit is slow -- its time grows with
the square of the rows (about 20 minutes for 1,000) -- so expect a long `reconcile mark` and
`reconcile finish`, and prefer several smaller deposits when the bank lists them separately.

## A vendor sends a refund check

"Acme sent us a $120 refund check for our return" means a vendor credit is open and the vendor
paid it in cash instead. Find the credit (`vendor-credit query --vendor Acme`, its
`settlement_current.unapplied` is what is free), then one `vendor-credit refund <credit>
--deposit-to <bank> --date <date> --amount 120.00` with `--payment-method Check --check-number`
when you have them. Leave `amount` out to refund everything free; a smaller amount leaves the rest
of the credit open. It banks one deposit (debit the bank, credit Accounts Payable for the vendor)
and settles the credit against it, so the vendor's balance, the A/P aging, the deposit detail and
the bank reconciliation all agree. Do not deposit the check to an expense or income account: that
leaves the credit standing as money the vendor still owes. No credit entered yet? Post the
`vendor-credit` first (the return itself), then refund it. To undo, void the deposit; the credit
reopens. `vendor-credit unapply` with nothing named leaves a refund standing; name it in
`--deposits` only when the refund itself was wrong and the deposit stays. A refund already deposited with a line from Accounts Payable naming the vendor is settled
with `vendor-credit apply <credit> --deposits '[{"deposit":"<deposit>"}]'`.

## Deleting duplicate documents

Use `invoice delete`, `sales-receipt delete`, `check delete`, `card-charge delete`,
`payment delete` or `bill delete` with the exact observed version,
a reason and a stable operation key. Preview first. The company must explicitly
activate the current permission catalog, and the authenticated actor and bound
principal need Standard plus the exact family Delete grant and ledger.read;
ledger.post is not required for cancellation. Company Users & permissions exposes
these grants. No role receives them by default.

Deletion retains captured facts, numbering and history, reverses the document through
its stock/accounting owner and hides it from ordinary lists/register presentation.
Read retained facts with `include_deleted=true` on show/query/history. A zero-price
sale can still have stock/COGS to reverse. Deleting a bill also releases the received
lines it claimed; a purchase order it consumed stays consumed, exactly as it does on
void. Dependencies and closed affected dates
refuse; resolve supported payment/deposit/credit/reconciliation dependencies explicitly,
never silently cascade. A bill still answered by a bill payment or a vendor credit
refuses and names the settlement; unapply it first. Retry the original operation key
only under current authority. There is no Restore command. `design/transaction-deletion.md`
lists every posted family and, for each one without a delete command, the reason.

## What reconciliation proves, and `company verify`

Each reconciliation command proves what was written since the account's verified watermark,
and every older reconciliation that touches, against the ledger; a doubt means it proves the
whole account. It does not look again at an older record changed outside Bookflow (the company
file edited directly). `company verify` proves the whole of every bank and card account (or
`--account` one) and reports `proved`, `unsupported`, `pending` or `failed` with the check that
refused; it only reads, and an agent may run it. A backup runs the same proof and refuses books
that fail it (`E_BACKUP_INVALID`, check `statement_proof`). If an account fails, stop working on
it and tell the owner what `company verify` reported: nothing you post fixes it.

## When a reconciliation will not tie

Never post, change or tick an entry just to make a reconciliation's difference zero. If a
statement will not tie, stop: leave the draft open, add a note to it saying what you checked
(`note add reconciliation_draft <draft id> --body "..."`), and tell the owner. The open draft,
its difference and your note appear on the owner's Overview. `reconcile finish` has no
adjustment input; a statement is certified only when its ticked movements tie.

The owner's `report entries-to-review` lists, from any command and any actor, entries dated
into a reconciled statement period or a closed period but entered later, entries an agent
posted after a reconciliation was started and then cleared on it, entries touching Opening
Balance Equity outside the move-in, an agent's round, unexplained month-end amounts into a
bank account, and a customer balance an agent wrote off, or a charge it waived, with a receipt of 0.00 (`write_off`). Your write's result carries a warning when it lands there. Nothing is refused;
the owner reviews it (`review mark`, a person's step). `report prior-balances` shows any
reconciled or closed balance that has moved since, with the entries that moved it.
