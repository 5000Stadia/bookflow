# Document delivery

This contract covers roadmap item R112: emailing the documents a customer receives, queued in an
outbox, tracked, and sent on a schedule, built whole except the sender. It builds on [printed
documents](printed-documents.md) (the four PDFs and the `render` callable) and on R111's overdue
notices and scheduler (`company/notices.py`, `company/scheduler.py`): `notices.py` already names
this item as the reader of its queue.

**Scope (the person, 2026-10-27):** "wire up our 112 without the final email connection, email
drafting and attached PDF but instead establish its build with the email integration itself to be
determined in the future but wired and ready to go otherwise." So this item builds drafting, templates,
the outbox and its states, tracking, batches, recurring sends and a transport seam. It builds no
sender. Until a person sets up a sending connection, **nothing leaves the machine**: messages wait in
the outbox, ready to send, and the owner can export one as a `.eml` file or send it from their own
mail program and mark it sent.

**What exists.** `documents.render(read, company_id, kind, identity)` returns the PDF bytes, file name
and title of an invoice, sales receipt, estimate or customer statement. Customers carry `email`,
`cc_email` and `preferred_delivery_method` (`none`, `email` or `mail`; a job inherits its parent's).
The `notice` queue holds R111's overdue notices with a `mark_sent` seam. The host's scheduler ticks
every company about once a minute (`scheduler.run_company`). The content-addressed attachment store
holds files. Nothing composes, queues or sends a message, and `printed-documents.md` says so.

Sources for the anchor: **[M]** the supplied Missing Manual (2016), printed page numbers; **[E]** the
anchor's Enterprise 24.0 in-depth guide, page numbers. The guides describe the screens, not the
mechanics behind them; where they are silent this document says so, and the rule given is Bookflow's.

## 1. The anchor's behaviour, and where Bookflow differs

**Matched.**

- **Five ways to send:** print now or later, email now or later, or print and email later; a form marked
  *Email Later* joins a queue [M 238–239]. Email now opens the mail program with fields filled and the form
  attached as a PDF, or a Send dialog for web mail [M 319]; "[form] and Attached Files" also attaches the
  files stored on the transaction [M 320]. Addresses, subject and text are editable first [M 320].
- **Send Forms** lists every unsent form; the user unchecks some, previews and edits a message, sends, or
  removes the unchecked from the queue [M 321, xvi].
- **Preferred Delivery Method** on the customer (E-mail, Mail, None) turns *Email Later* on automatically
  [M 69, 671]. Batch invoices end with a count to print and to email by that method [M 242]; statements
  filter by it [M 301] and email from Create Statements [M 304].
- **Templates** per form type (invoices, estimates, statements, receipts, overdue and almost-due notices
  and more): a name, a Default flag, a subject and a body with *Insert Field* merge fields; any
  administrator edits them [M 671–673].
- **Smaller features:** several contacts to choose from [E 67]; the PO number in the subject [E 69];
  invoices combined in one email [E 69]; an invoice status tracker [E 67]; automatic payment reminders
  [E 70] and automatic recurring statements reviewed before they go [E 71].

**Deliberate differences.**

- **A sending connection replaces the user's mail program.** The anchor hands the message to Outlook or
  webmail. Bookflow keeps it in an outbox and hands it to a *transport* when one exists; "opens in your
  mail program" is matched today by the `.eml` export (D2).
- **The outbox is a record.** The anchor's queue drops a form once sent; its guides describe no history.
  Bookflow keeps every message with its states, who did each step and what the transport said.
- **Sending never blocks work.** *Send Now* locks the program [M 321]; dispatch runs in the host (§4).
- **Any template at draft time.** The anchor offers several only with web mail [M 671].
- **A person's go.** Agents draft and release; a person approves what leaves (§7).
- **Recurring sends do not catch up** past a short window (§6): a statement three months late is not worth
  sending. **No open or click tracking** (§4).

**Outside this item:** the sender; inbound mail; payment links; HTML mail (D1); printing a `mail`-method
customer's batch (print templates, future); purchase-side documents and reports by email (the kinds table in
§3 is the one place to add them).

**Prior art,** from knowledge of four open-source systems, not re-checked against current releases:

- *Odoo:* `mail.template` per model; a compose wizard; each email a `mail.mail` row with states (outgoing,
  sent, exception, cancel), a failure reason and a cron sending in batches; opens by pixel, bounces to a
  catch-all inbox. *ERPNext:* Email Template (Jinja); an Email Queue row with status, retry count and
  send-after time flushed by the scheduler; Auto Repeat; Process Statement Of Accounts (customers, filters,
  frequency, template, sender in one record); read-receipt delivery status. *Invoice Ninja:* `$variable`
  templates, reminders by days after due, an invitation per contact with sent, viewed and opened dates,
  recurring invoices with remaining cycles and paused, provider webhooks. *Akaunting:* `{tag}` templates,
  a document `sent` status with a manual *Mark Sent*, recurring documents that email on creation, mail
  through the framework queue.
- *Adopted:* a row per email with explicit states, failure reason, retries and a flushing tick; selection
  plus frequency plus template in one saved schedule; remaining-cycle and paused states; a manual *Mark
  Sent* beside automatic sending; a default template per kind with a closed list of merge fields.
- *Rejected:* expression templates, since a template an agent can edit must not run code; pixels, view
  links and webhooks, which expose customer behaviour and an open port in a program whose rule is that
  nothing leaves the machine; deleting sent mail; sending inside the request, which hides failure;
  catch-all bounce inboxes.

## 2. The message, its states and its transitions

A **document** is the invoice, sales receipt, estimate, customer statement or overdue notice a customer
receives. A **message** is one email to a customer carrying one or more documents as PDFs. Both are
facts a person can inspect; a message never changes the books.

| State | Meaning |
|---|---|
| `draft` | Composed from a template; editable; nothing is waiting on it |
| `ready` | Released to the outbox. It waits for a person's go if it lacks one (§7), then for a sending connection and its next attempt time. With no connection it waits indefinitely, and that is correct |
| `sending` | A transport attempt is in flight under a lease |
| `sent` | Accepted by a transport (`sent_via` `connection`) or recorded by a person as sent from their own mail program (`manual`). Final |
| `held` | Needs a person: the document changed or was voided, no recipient, a template error, or an attempt whose outcome is unknown. Holds a `hold_reason` |
| `failed` | The transport refused permanently, or attempts ran out. Holds `failure_code` and the transport's text |
| `cancelled` | Set aside with a reason. Final |

| Act | From → to | What it records (event, audit, columns) |
|---|---|---|
| `delivery draft` | → `draft` | `drafted`: actor, interface, template, each source's kind, id and revision, warnings (empty merge fields) |
| `delivery update` | `draft`, `held` | `edited`: the fields changed; version bump (stale writers get E_VERSION_CONFLICT). `--refresh` re-freezes the documents as they are now (old and new revision and SHA-256 in the event), refills the subject and body the person did not write, and returns a `document_changed` hold to `draft` |
| `delivery send` | `draft` → `ready` | `released`: who, when; `go_by` and `go_at` when the actor is a person |
| `delivery approve` | `ready` | `approved`: `go_by`, `go_at`. People-only |
| dispatcher claims | `ready` → `sending` | `claimed`: attempt number, connection, lease expiry |
| transport accepts | `sending` → `sent` | `accepted`: provider reference, connection, actual From, `sent_at` |
| transport says try later | `sending` → `ready` | `attempt_failed`: code, text, `next_attempt_at` from the backoff |
| transport refuses, or last attempt fails | `sending` → `failed` | `attempt_failed`: code, text; `failure_code` |
| lease runs out, or the outcome is unknown | `sending` → `held` | `held`: `outcome_unknown` |
| precheck fails at claim | `ready` → `held` | `held`: `document_changed`, `document_voided`, `no_recipient`, `template_error` |
| `delivery retry` | `held`, `failed` → `ready` | `retried`: who. (Refreshing is `delivery update --refresh`, one verb; 2026-10-27) |
| `delivery cancel` | any but `sent` | `cancelled`: reason required |
| `delivery mark-sent` | `draft`, `ready`, `held`, `failed` → `sent` | `marked_sent`: who, `method` (`mail-program`, `webmail`, `other`), `sent_at` as told (not future). People-only |

Events (`delivery_events`) are append-only. A message's current state is its row; its history is the
events, so `delivery history` can answer who did what without trusting the row. A `sent` message is
never reopened; to send again, `delivery draft --again` makes a new message that names the earlier one
(`resend_of`). A transport may later add `delivered` or `bounced` events to a `sent` message; they
change no state (§4).

## 3. Drafting

**Kinds and identity.**

| Kind | A document is named by | Attached file (from `render`) | Default subject |
|---|---|---|---|
| `invoice` | the invoice, by number or id | `invoice-<number>.pdf` | `Invoice {{document.number}} from {{company.name}}` |
| `sales-receipt` | the receipt | `sales-receipt-<number>.pdf` | `Receipt {{document.number}} from {{company.name}}` |
| `estimate` | the estimate | `estimate-<number>.pdf` | `Estimate {{document.number}} from {{company.name}}` |
| `statement` | customer, `date_from`, `date_to` | `statement-<customer>-<date_to>.pdf` | `Statement from {{company.name}}` |
| `notice` | an overdue notice that is `ready` | the notice's statement or letter PDF | `Past due: {{customer.name}}` |

**Recipients.** To is the customer's `email`; a job without one uses its parent customer's. Cc is
`cc_email`. A company may set `delivery_copy_to`, added as Bcc to every message (default none). A
person may edit the lists in the draft; the anchor's several-contact choice [E 67] is the customer's
contact points of kind `main_email`, `additional_email` and `cc_email`, offered as choices in the
browser. Addresses are validated as syntax only. A draft with no To is allowed (it is editable) but
cannot be released: `send` is refused with E_VALIDATION naming `to`.

**Templates** (`delivery-template`): per kind, a name, a subject and a plain-text body. Exactly one
active default per kind; a company starts with one seeded default per kind. Several may exist, and the
chosen one is a `--template` input at draft time. The body and subject use a **closed list** of merge
fields and one conditional; there is no expression language:

- `{{company.name|phone|email|website}}`, `{{sender.name}}` (the person drafting, or the company)
- `{{customer.name|first_name|last_name|account_number}}`
- `{{document.kind|number|date|due_date|total|balance_due|po_number|terms}}`
- `{{statement.date_from|date_to|balance|overdue}}`, `{{notice.overdue|oldest_due_date|days_overdue|letter}}`
- `{{if field}} … {{end}}` shows its text only when the field has a value (so a subject can carry
  "PO {{document.po_number}}" only when there is one [E 69]); no nesting.

Saving a template with an unknown field is refused, naming it (`delivery-template fields` lists them
by kind). A field with no value at draft time becomes empty text and a draft warning. Amounts are the
strings the document's commands already return; a template never recomputes one.

**Attachments are frozen at draft.** The draft renders each document's PDF through `documents.render`
as the drafting principal, stores the bytes in the attachment store (content-addressed, so identical
files share one), and records per file the source kind, id and revision, file name, size and SHA-256
(`delivery_message_documents`). What the owner sees in the outbox is what will be attached. With
`--with-attached-files`, files linked to the document in the attachment store are attached too [M 320],
recorded the same way. A full attachment store refuses the draft with the store's own error.

**Staleness.** At claim and at export, a document message checks that each source is not voided and
still says what the frozen PDF says: same revision, and the document drawn now hashes to the frozen
SHA-256; if not it becomes `held` (`document_changed` or `document_voided`). *(Amended 2026-10-27: the
revision alone missed a payment, which changes the balance an invoice prints without a new revision;
drawing costs about what a draft does, 0.13-0.27 s per document.)* `delivery update --refresh`
re-renders from the document as it is now and re-applies the template; edits the person made are kept and
listed. Statements and notices are dated readings and are never stale.

**Duplicates and again.** Drafting is idempotent per `(kind, customer, sources and revisions,
recipients)`: a second draft returns the first message while it is live. A message for a source already
`sent` is refused with E_WORK_DEPENDENCY naming it; `--again` makes a new one. A command's own
idempotency key behaves as everywhere else.

**Several documents, one email.** `--combine` puts one customer's documents in one message with one
PDF each [E 69]. Default: one message per document, as the anchor [M 321].

## 4. Delivery, tracking and the transport seam

**What the seam is.** `bookflow/delivery/transport.py` defines a small protocol and a registry:

```python
class Transport(Protocol):
    kind: str                        # "mcp-mail", "smtp"...: a string, no schema change per kind
    def check(self, conn) -> CheckResult                      # can it connect with its secret?
    def send(self, message: OutgoingMessage, attempt_key: str) -> TransportResult
    def events(self, conn, refs: list[str]) -> list[TransportEvent]   # optional: delivered, bounced
```

`OutgoingMessage` carries From, To, Cc, Bcc, Subject, text body, a fixed `Message-ID` chosen at draft,
and the attachments as bytes (an adapter whose server wants file paths stages them in a private
directory and removes them after, since mail servers commonly take attachments by path).
`TransportResult` is `accepted(provider_ref, actual_from)`, `temporary(code, text)`,
`permanent(code, text)` or `unknown(text)`. `attempt_key` is `<message id>:<attempt number>`, so an
adapter or its server can recognise a replay. A kind is registered in code; **this build registers
none**, so the registry is empty and `delivery-connection add` refuses an unknown kind, listing the
available ones (none). The tests register a fake transport; a real one is a later item that implements
the protocol, registers its kind and passes the shared conformance suite
(`tests/delivery_transport_contract.py`, built in increment 2: accepted, temporary then accepted,
permanent, unknown, replay of an `attempt_key`, attachments intact byte for byte).

**Connections** (`delivery_connections`, set up by a person only): a name, a `kind`, non-secret
`settings` (JSON the kind validates: server command pinned to a version, From address), a `secret_ref`,
`enabled`, `agent_go` and an optional `daily_limit`. The row never holds a secret: `secret_ref` names an
entry in the operating system's keyring (or an environment variable the host reads), never the secret.
A company file copied or restored on another machine arrives with every connection disabled and
`enabled_host` cleared, so a restored backup full of `ready` messages cannot send a second copy of
anything. `delivery-connection enable` on a machine binds it there.

**Dispatch** is the host's, never a command's, and never holds the writer during a network call: one
write claims up to a small chunk of messages (`ready`, a go present or `agent_go` on the connection,
`next_attempt_at` due, under the daily limit), the transport is called outside the writer, and one
write records each outcome. The tick that finds no enabled connection does one `EXISTS` and returns;
it costs nothing in a company that never sends. `delivery run` does one dispatch pass on demand
(`--dry-run` lists what it would claim) and is how tests and an owner see it work.

**Retries and idempotency.** `temporary` retries after 5 minutes, 30 minutes and 2 hours, then fails,
four attempts in all (change it if a real provider says better, and say why). `permanent` fails at
once. **An unknown outcome is never retried automatically:** a timeout after the bytes may have left, a
crash between claim and record, or a lease (10 minutes) that expires. The message goes to `held`
(`outcome_unknown`) because a duplicate email to a customer is worse than a late one; the person
checks their sent mail (or the transport's `events`, where it can answer) and chooses `mark-sent` or
`retry`. Every attempt writes one event, so a message's attempts are countable and ordered.

**What "track delivery" can honestly mean.**

- *Before a sender:* the lifecycle Bookflow itself knows: drafted, edited, released, approved, exported,
  marked sent (by whom, how, when), cancelled, with who and through which interface. The outbox shows
  the document's business state beside it, as the anchor's status tracker does [E 67]: sent 9 days ago,
  still unpaid. **`sent` here means the owner says so.** It is shown as "Sent by hand" and never as
  delivered.
- *With a connection:* `accepted` (the server took it, with its reference), `failed` with the
  provider's reason, and, if the transport implements `events`, `delivered` and `bounced`, polled on
  the dispatch tick (no inbound port). A bounce raises one to-do for the person who drafted it, using
  R111's to-dos. The message projection shows `delivery`: `unknown`, `accepted`, `delivered` or
  `bounced`, derived from the newest event and never stored as a counter.
- *Never:* "opened" or "read". Bookflow adds no pixel and no tracked link.

**The `.eml` export** is included. `delivery export <message>` returns one RFC 5322 file
(`message/rfc822`): To, Cc, Subject, the text body, the PDFs attached, the fixed `Message-ID`, and
`X-Unsent: 1` so mail programs that honour it open it as an unsent message. It changes no state and
adds an `exported` event; the owner sends from their mail program and records it with `mark-sent`.
Several messages export as one zip of `.eml` files. Support is best effort across mail programs and is
tested by parsing the file with the standard library, not by opening a program.

## 5. The queue, the customer's method and batches

**To be emailed** is derived, never narrated. A document is to be emailed when it is not cancelled,
voided or already in a live or `sent` message, and either (a) a person marked it *Email later*
(`delivery flag`, the anchor's checkbox [M 239]), or (b) the company turned on **auto-flag** from a date
(`company update --delivery-auto-flag-from`; null is off) and the customer's effective
`preferred_delivery_method` is `email` and the document is dated on or after that date [M 671]. An
explicit `delivery unflag` wins over (b). The date keeps turning the preference on from flagging the
company's whole history. `delivery queue` lists these documents with the method, address and any
problem (no address); `--method mail` lists the customers the owner prints for, and does nothing else.

**Batches** (`delivery batch`) draft many messages in one act. Selections:

- `--queued`: every to-be-emailed document of a kind;
- `--kind invoice --unsent [--date-from --date-to --customer-type --customer]`: documents with no live
  or sent message;
- `--kind statement --period <month|date range> --balance-over N [--method email --customer-type]`: one
  statement per customer with a balance, the anchor's Create Statements filters [M 301];
- `--kind notice --ready`: R111's ready overdue notices. Marking such a message sent calls
  `notices.mark_sent` with method `email`; `notice mark-sent` is refused while a live message exists
  for that notice, naming it.

A batch is previewable (`--dry-run` lists who gets what and who is skipped, with reasons: no address,
method `mail`, already sent). It returns a `batch_id` and drafts in chunks per call (the scheduler's
fair-share rule: no batch holds the writer for its whole length; the chunk size is set from the
measurement in increment 3), returning `next`; the browser loops with progress. A batch makes **drafts**,
or with `--send` releases them too. Every message names its batch, so *Send forms* [M 321] is the
outbox filtered to one batch: deselect, edit, release the rest.

## 6. Recurring sends

A **delivery schedule** (`delivery-schedule`) is a saved batch with a frequency: a name, a kind, a
selection (the §5 filters, plus statement periods `previous-month`, `month-to-date`, `since-last-run`),
a template, `combine`, a frequency, start date, optional stop date or remaining runs (the memorized
schedule's own date arithmetic, reused: a month-end start stays at month end), `mode` `draft` or
`send`, and `active`. Examples: statements to every customer with a balance on the last day of each
month [E 71]; every day, invoices still to be emailed; weekly, ready overdue notices.

- **A run is the host's work** on the scheduler tick, claimed first: `(schedule, slot date)` is unique,
  so a second tick, a restart or `delivery-schedule run` the same day finds the run already there.
  A run drafts in chunks over the following ticks and ends `done` with counts.
- **No catch-up beyond the latest slot.** A host that was off runs only the most recent missed slot,
  and only if it is no more than `catch_up_days` old (default 7); older slots are recorded `skipped`
  and shown in the outbox, never run. (Memorized entries owe every missed slot because they are the
  books; a missed email is not.)
- **`send` mode needs a person's standing go.** A schedule has `go_by`, empty at creation. Only
  `delivery-schedule authorize` (people-only) sets it, and any change to its selection, template,
  combine, mode or frequency by someone other than `go_by` clears it, because the content changed
  under the authority (R111's lesson). A run in `send` mode with `go_by` set creates messages that
  carry that go; without it, the messages are `ready` and wait for approval, and the run raises one
  to-do for a company administrator, not one per message. A `draft` schedule needs no authorization.
- **Recorded as R111 records.** The messages' audit actor is the schedule's last editor, interface
  `system`, client `scheduler`, reason "On schedule: <name> for <slot>".
- With no connection, a `send` schedule still runs: it fills the outbox with messages `ready`, which is
  the point of building the item before its sender.

## 7. Agents and permissions

A new capability `delivery` (reads at `member`, writes at `standard`, like other lists). Drafting also
needs the authority that prints the document: `ledger.read` for invoices, receipts, statements and
notices, `customer-work` read for estimates. A permission layer per increment is chained on the tip
(`delivery-v1`, `delivery-queue-v1`, `delivery-batch-v1`, `delivery-schedule-v1`); none changes an
existing capability or role default.

| Acts | Who |
|---|---|
| `delivery-template` writes; `delivery draft`, `update`, `send`, `export`, `retry`, `cancel`, `batch`, `flag`, `unflag`; `delivery-schedule` writes except `authorize` | standard, `delivery` (an agent may do all of it) |
| `delivery show`, `query`, `history`, `queue`, `run --dry-run`; `delivery-template show`, `query`, `fields`, `preview`; `delivery-schedule show`, `query` | member |
| `delivery approve`; `delivery mark-sent`; `delivery-schedule authorize`; every `delivery-connection` write and `check` | **people-only** |
| `company update` delivery preferences (from-name, reply-to, copy-to, auto-flag date) | admin, as other company preferences |

The line is "what leaves the machine, and what happened outside it":

- **Drafting and releasing are an agent's.** The agent can compose every email of the month and put them
  in the outbox.
- **The go is a person's.** A message leaves only if a person released it or approved it, or the person
  who set up the connection chose `agent_go` for it (a standing instruction on the connection, with a
  `daily_limit`). Without that, an agent's release leaves messages `ready`, shown "Waiting for your
  go", and `delivery approve` (one id, a batch, or "all waiting") is the person's tap.
- **`mark-sent` and `delivery-connection` are people-only,** on the model of `check print-confirm`: what
  happened in the owner's mail program is known to the owner, and where mail goes and under whose keys
  is the owner's decision. An agent asking for one gets E_PERMISSION `people_only` and tells its
  principal.
- **Isolation.** A principal who cannot read a customer's documents cannot draft, list or see their
  messages, and a message never discloses another company's data. The outbox and `delivery query`
  respect the same visibility as the records they cite.

**An agent's first time.** "Email Acme invoice 1007": `delivery draft invoice 1007`; the agent reads the
returned To, Subject and body, edits with `delivery update` if asked, runs `delivery send`, and tells the
person it is waiting for their go and that nothing will leave until a sending connection exists.
"Send statements to everyone who owes us": `delivery batch --kind statement --balance-over 0 --method
email --dry-run`, then the same without it; report the counts and the skipped. "What is waiting?":
`delivery query --status ready`. "I emailed 1007 myself": it cannot record that; it names the message
and asks the person to mark it sent.

## 8. Surfaces

### Storage and migrations

One company migration per increment, numbered after main's head when it merges (written here +1 to +4
after co0104); each keeps every existing row, index and trigger and extends the demo seed with any
oracle move stated. Tables follow the common columns (id, version, created and updated attribution).

| Increment | Tables and columns |
|---|---|
| 1 Messages | `delivery_templates` (kind, name, `name_key`, subject, body, `is_default` with one active default per kind, active); `delivery_messages` (number from the `delivery` sequence starting 1001; kind; customer; `status`; `hold_reason`; From name and address, reply-to, To, Cc, Bcc as JSON arrays; subject; body; fixed `message_id`; `template_id`; `dedupe_key`, unique among non-cancelled; `resend_of`; `released_by`, `released_at`, `go_by`, `go_at`; `attempts`, `next_attempt_at`, `lease_until`; `sent_at`, `sent_via`, `sent_method`, `connection_id`, `provider_ref`; `failure_code`, `failure_text`; `cancelled_reason`); `delivery_message_documents` (message, position, role `document` or `attached-file`, source kind, source id, source revision, `statement_from`, `statement_to`, file name, media type, size, SHA-256, attachment id); `delivery_events` (append-only: guards refuse update and delete); `company_info` gains `delivery_reply_to`, `delivery_copy_to`, `delivery_auto_flag_from`; a hub migration registers the `delivery` capability, as hub0007 and hub0008 did for notes and attachments |
| 2 Queue and seam | `delivery_connections` (name, `name_key`, `kind` as free text with no CHECK, `settings` JSON, `secret_ref`, `enabled`, `enabled_host`, `agent_go`, `daily_limit`, last check result); the restore and copy path clears `enabled` |
| 3 Batches | `delivery_flags` (source kind and id, `wanted` true for *Email later*, false for an explicit opt-out; one row per source); `delivery_batches` (selection JSON, counts, state, schedule run); `delivery_messages.batch_id` |
| 4 Schedules | `delivery_schedules` (the fields of §6, `go_by`, `go_principal`, `go_epoch` as R111's group authority records them); `delivery_schedule_runs` (unique `(schedule_id, slot_date)`, state, counts, skipped reason) |

Every state change writes its event in the same transaction as the row. No guard counts rows. The
message and event tables are never deleted from.

### Commands

| Command | Does |
|---|---|
| `delivery-template create`, `update`, `deactivate`, `show`, `query`, `fields`, `preview` | §3; `preview` renders a template against a named document without drafting |
| `delivery draft` | One message for a document (`kind` + identity), `--template`, `--to --cc --subject --body`, `--combine`, `--with-attached-files`, `--again` |
| `delivery update [--refresh]`, `cancel`, `retry` | §2 |
| `delivery send` | Release drafts (one, several, or a batch) to `ready` |
| `delivery approve` | Give a person's go to `ready` messages (ids, a batch, or `--all-waiting`) |
| `delivery mark-sent` | Record a hand-sent message: `--method`, `--sent-at` |
| `delivery export` | `.eml`, or a zip of several |
| `delivery show`, `query`, `history` | A message; the outbox with filters (status, kind, customer, batch, date, `delivery`); its events |
| `delivery queue`, `flag`, `unflag` | §5 |
| `delivery batch` | §5 |
| `delivery run` | One dispatch pass; with no connection it reports "0 claimed, no sending connection" |
| `delivery-connection add`, `update`, `enable`, `disable`, `remove`, `check`, `show`, `query` | §4; `add` with no registered kind is refused naming the kinds available |
| `delivery-schedule create`, `update`, `authorize`, `pause`, `resume`, `run`, `show`, `query` | §6 |
| `company update` | gains the three delivery preferences |

All are registry commands, so the CLI, HTTP, MCP (`bookflow_run`) and the browser reach them
identically, each with a dry run, idempotency on writes and a worked example in its help. Refusals use
existing codes: E_VALIDATION (bad address, unknown field, nothing to release), E_WORK_DEPENDENCY with
`details.status` (a state that does not allow the act), E_VERSION_CONFLICT, E_PERMISSION `people_only`.

### Browser

- **Email… on the record page** of an invoice, sales receipt and estimate, beside *Print / save PDF*, and
  on each statement row, opens a compose panel (htmx): To, Cc, Subject and body from the default template
  with a template picker, the PDF with a preview link, and *Save draft*, *Send*, *Export .eml*. Forms gain
  the anchor's *Email later* checkbox [M 239]. A person's *Send* is release and go in one tap. With no
  connection the panel says: "No sending connection is set up. This will wait in the outbox. Export it to
  send it yourself."
- **The Outbox** (Customers menu): tabs *Waiting for your go*, *Ready*, *Sent*, *Needs attention*,
  *Drafts*, *Cancelled*; filters by kind, customer, batch and date; each row shows customer, document, To,
  subject, state, who and when, and the document's business state (unpaid, paid). Checkboxes feed Approve,
  Send, Export, Mark sent and Cancel. A header line says whether a connection exists and what that means.
- **Batch:** *Email statements…* (statement report) and *Email unsent invoices…* (invoice list) open a
  preview with a checkbox per customer, the skipped with reasons, and a progress bar while drafting.
- **Company settings > Email and sending:** from-name, reply-to, copy-to, auto-flag date; Templates;
  Schedules (with Authorize); Connections (a person): "None. Nothing leaves this machine." The customer
  page shows the preferred method and latest messages.
- Every screen works at 1440 and 390 pixels; forms are htmx and tested by a real Chrome submit. The
  outbox count is offered to R184's home flow.

## 9. Privacy

Nothing leaves the machine without both a connection a person set up and a person's go. This build
registers no transport; the only outward act is a `.eml` file a person downloads. A test makes every
socket and subprocess call fail and runs a batch, a schedule run, an approval and a dispatch tick with no
connection. Secrets live only in the keyring or environment; no backup, copy or `delivery show` holds one.
Bodies and PDFs are financial data in the company file and its attachment store, and a copy carries them,
which is why a copy arrives with connections disabled. No message carries remote content or tracked links.

## 10. Measures and acceptance criteria

*Measures, taken and recorded in increments 1, 3 and 4:* draft time for one of each kind and for a
batch of 200 invoices and 200 statements on the large template; the writer time per chunk (which sets
the chunk size); the idle scheduler tick with no schedule and no connection (one statement).

1. **Nothing leaves.** With no connection, draft, export, release, approval, batch, schedule run and
   dispatch tick open no socket and start no process (§9's test); the messages end `ready`.
2. **The email is right.** For each of the five kinds the draft's To, Cc, Subject and body match template
   and customer, and the attached bytes equal `documents.render`'s (same SHA-256 and file name).
3. **Nothing sends twice.** A repeated draft, batch, schedule slot or tick, and a crash between claim and
   record, leave one message and at most one send with the fake transport; an unknown outcome ends `held`.
4. **A person's go holds.** An agent-released message is not sent until a person approves it or the
   connection's `agent_go` is on; an agent cannot approve, mark sent or touch a connection.
5. **Honest tracking.** A hand-sent message reads "Sent by hand", never delivered; delivered or bounced
   appear only after a transport event; nothing claims "opened".
6. **A restored copy is inert.** A restored or copied company has every connection disabled until a person
   enables one on that machine.
7. **Ready for a sender.** The conformance suite passes against the fake; a real transport needs no schema
   or command change, only an adapter, a registered kind and its keys.
8. **The owner's task.** A fresh agent with only Bookflow's help and MCP, given §7's prompts in the demo
   company, does each correctly first time; the owner then exports one `.eml`, sends it by hand and marks it
   sent, and the outbox, notice queue and audit list agree.
9. **Both surfaces.** Email panel, Outbox, batch preview, templates and settings work at 1440 and 390
   pixels with a real submit and show the same states as the CLI.
10. **The docs hold.** Generated docs are current, `printed-documents.md`'s "Not built here" is updated,
    and the demo company carries templates and messages in several states and no connection.

## 11. Build increments

Each increment is one migration, one permission layer and one merge, with its docs and demo changes.
Sizes are the builder's own times: S 30–60 min, M 1½–2½ h, L 4–6 h.

1. **Messages and the outbox (L).** Templates and merge fields, the five kinds, drafting with frozen
   PDFs and staleness, `update`, `cancel`, `export`, `mark-sent`, `show`, `query`, `history`, the Email
   panel, the Outbox page, the settings page. `send` exists but only for `draft → ready`. *Done when* a
   person drafts, edits, exports and marks sent an invoice, receipt, estimate, statement and notice, the
   attached bytes equal `render`'s, and the outbox shows each message's history. *Tests:* templates
   (unknown field refused, the conditional, empty field warning); a draft per kind against `render`;
   dedupe and `--again`; stale and voided sources; `.eml` parsed with the standard library; people-only
   `mark-sent`; the notice hand-off; a real Chrome submit of the panel; draft times recorded.
2. **The queue and the seam (M).** `delivery_connections`, the protocol and registry, the dispatcher and
   `delivery run`, claim and record outside the writer, retries, unknown outcome, `approve` and the go
   rule, events from a transport, copy and restore disabling connections, the conformance suite with the
   fake. *Done when* criteria 3 to 7 pass with the fake transport and an empty registry in production.
   *Tests:* the whole state table row by row; backoff; lease expiry and a crash between claim and record;
   `agent_go` and `daily_limit`; the socket and subprocess blocker over a full pass; a restore test;
   the idle-tick measurement.
3. **Flags, the customer's method and batches (M).** `delivery_flags`, auto-flag date, `queue`, `flag`,
   `unflag`, `batch` in chunks with dry run, `--combine`, notices as a source, the batch preview and
   *Email later* in the browser. *Done when* every selection in §5 drafts the right messages on the demo
   and on 1,000 documents, skips are named, and a rerun duplicates nothing. *Tests:* each selection
   against hand-worked sets; preference inheritance for a job; auto-flag date; chunk and resume; batch
   and chunk times recorded.
4. **Recurring schedules (M).** `delivery_schedules` and runs, the scheduler block, slot claims, the
   catch-up rule, `authorize` and its clearing, the to-do for approval, the Schedules page. *Done when*
   a monthly statement schedule and a daily queued-invoice schedule run unattended on a test clock,
   once per slot, and an agent-edited schedule loses its go. *Tests:* a month-end start across February;
   restart mid-run; a missed slot inside and outside the window; authority cleared on edit by another
   principal; the scheduler's idle tick unchanged for a company with no schedules.
5. **Close (S).** Agent guide and `docs/` sections, README, `printed-documents.md`, the demo seed, the
   schema and command census, and the closing trial of criterion 8. *Done when* criteria 8 and 10 pass.

## 12. Open questions

Each default stands if nobody decides. D1 to D3 are the person's to decide; the rest are the builder's.

- **D1. HTML mail.** The anchor has formatted mail and a plain-text option [M 671]. *Default:* plain text
  only; the schema does not block an HTML part later.
- **D2. The first real sender:** which kind (a mail MCP server, SMTP with an app password, another) and who
  sets it up. *Default:* none in this item; a later item adds one kind against the conformance suite.
- **D3. Who may approve and mark sent.** *Default:* as §7, both people-only, with `agent_go` the person's
  way to let an agent's releases leave. Relax `mark-sent` for agents if hand-sent records prove tedious.
- 1. **Reminders before the due date** [E 70] and reminder series. *Default:* not built; overdue notices
  plus a `notice` schedule cover after-due reminders.
- 2. **Per-recipient status.** *Default:* one status per message.
- 3. **Retention.** *Default:* messages, events and frozen PDFs are kept with the company; no purge.
- 4. **A `mail`-method customer.** *Default:* listed by `delivery queue --method mail`; printing is print
  templates' batch printing.
- 5. **`since-last-run` period.** *Default:* from the day after the last run's `date_to`; the first run uses
  the previous month.
- 6. **Message numbers.** *Default:* a sequence from 1001. **Bcc to self:** off; `delivery_copy_to` enables.
- 7. **Inbound replies and bounces by mailbox.** *Default:* not built; bounces arrive only as a transport's
  `events`.

## As built: increment 1 (2026-10-27)

**What exists.** co0105 (`delivery_templates`, `delivery_files`, `delivery_messages`, `delivery_message_documents`,
`delivery_events`; `company_info.delivery_reply_to`, `delivery_copy_to`, `delivery_auto_flag_from`), one seeded
default template per kind, and sixteen commands under the `delivery-v1` layer (on `picking-v1`): `delivery draft`,
`update`, `send` (draft to ready only), `cancel`, `mark-sent` (people-only), `export`, `show`, `query`, `history`, and
`delivery-template create`, `update`, `deactivate`, `show`, `query`, `fields`, `preview`. `company update` gains
reply-to and copy-to. The browser has the Email panel (Email… beside Print on invoices, sales receipts, estimates,
statement rows and ready overdue notices), the Outbox (Customers menu), a message page with its frozen PDF and
history, and Email and sending settings (addresses, templates, "Connections: none"). Code:
`company/delivery*.py`, `commands/delivery_cmds.py`, `adapters/workbench/delivery_pages.py`.

**Decisions taken in the build, each reversible:**

- **No new capability, no hub migration.** The brief allowed one migration, so messages are admitted as R111's
  overdue notices are: `customer` at `member` (reads) and `standard` (writes, export included: it holds and writes
  events, so it needs what drafting needs; amended 2026-10-27), and every `delivery` command
  also needs `ledger.read` and `customer-work` at `member`, so a principal who cannot read an invoice or an estimate
  cannot see it in the outbox. Drafting and previewing read the document as the principal, so its own read is
  checked too. No capability or role default changes. A `delivery` capability (§7) can be added later by its own hub
  migration if a company needs to grant email separately from customers.
- **Frozen bytes live in `delivery_files`, in the company file,** content-addressed (one row per SHA-256), not in the
  attachment store: that store's bodies arrive through upload transfers and are subject to collection, while a frozen
  PDF is the company's own output that must never be collected. It travels with backups. `delivery_message_documents`
  keeps `attachment_id` for linked files (`--with-attached-files`, later).
- **PDFs are drawn invariant** (`documents/pdf.py`): no creation clock, no random file id, so the same document gives
  the same bytes and "the attached bytes equal `render`'s" is checkable by hash. `render` was split into `build` and
  `drawn`; drafting describes the document once, fills the template from that description and draws it.
- **One live message per document.** Beyond §3's dedupe key: a second, different draft for a document already in the
  outbox is refused (E_WORK_DEPENDENCY naming it), not added beside it. `--again` makes one more, naming the newest sent
  message or else the original; asking again returns that same message.
- **Staleness is derived.** Every read compares revisions and voids; release, export and `delivery show` also draw
  the document now and compare its SHA-256 with the frozen PDF's (2026-10-27: a payment changed an invoice's balance
  due without a new revision, and the frozen PDF still read 120.00 owed). The outbox list keeps the cheap check.
  Release and export hold a stale message (`document_changed`, `document_voided`) instead of acting; exporting a
  message already held for the same reason holds nothing more. `delivery update --refresh` re-freezes the documents
  into the same message (number and history kept; the `edited` event lists the old and new revision and SHA-256, the
  subject and body refilled, and those kept because the person wrote them) and returns a `document_changed` hold to
  `draft`. It is the one refresh verb: increment 2's `retry` no longer takes `--refresh`. A voided document is not
  refreshed; that message is cancelled. The refusal of a second draft for a document whose message is stale names
  `--refresh`. To allow it, `delivery_message_documents` may be rewritten while its message is a draft or held (the
  guard checks the message's standing state and the fixed columns).
- **Frozen files are pruned on cancel** when no standing message carries them: a cancelled message that was never
  exported gives its PDF up unless another live, sent or exported message has the same bytes. The rows and the
  history keep the SHA-256; the message page says the file was removed. The guard on `delivery_files` refuses any
  other delete, and `delivery_message_documents.sha256` no longer has a foreign key so the hash outlives the bytes.
- **The notice hand-off:** marking a notice's message sent calls `notices.mark_sent(..., 'email')` in the same write;
  `notice mark-sent` *and* `notice dismiss` are refused while a live message carries the notice.
- Message numbers are `M-1001`, `M-1002`, ...; Message-IDs are `<id@bookflow.invalid>`. `sender.name` is the drafting
  person, or the company name for an agent. `statement.overdue` is the statement's aging total less its current
  column. The `.eml` uses CRLF line ends (RFC 5322) and `X-Unsent: 1`; several messages export as a zip.
- In the browser every form posts through htmx, Export .eml too (cookie-authenticated writes need the header only
  htmx sends; 2026-10-27): the answering page carries the file as a data link that `workflow.js` clicks once, and a
  held message's page says why instead. The Email panel passes "send it again" through, and offers Refresh PDF when
  its message is stale; the message page offers it too. `delivery show --include-files` returns the frozen bytes the
  message page links to (none for a pruned file).

**Not in this increment:** `approve`, `retry`, connections, dispatch (increment 2); `--combine`,
`--with-attached-files`, flags, the auto-flag setting in `company update` (its column exists) and batches
(increment 3); schedules (increment 4); the contact-point picker and demo messages (increment 5).

**Measured:** draft time per kind through the HTTP host, demo copy, after a warm-up: invoice 0.25 s, sales receipt
0.14 s, estimate 0.12 s, statement 0.15 s, notice 0.15 s (`tests/test_delivery.py`, printed with `-s`).

**Tests:** `tests/test_delivery.py` (a draft per kind against `render`, templates, dedupe and `--again`, stale and
voided sources, `.eml` parsed with the standard library, people-only `mark-sent` and an agent's release waiting for a
go, the notice hand-off, the workbench through the test client), `tests/test_delivery_seeded.py` (five seeds of thirty
random acts), `tests/test_delivery_surfaces.py` (Python, CLI, HTTP and MCP agree), `tests/test_delivery_browser.py`
(a real Chrome submit of the Email panel at 1440 and 390, and Export .eml clicked in the Outbox and the Email panel
with the downloaded file parsed). Review fixes (2026-10-27) are tested in `tests/test_delivery.py`: payment
staleness and refresh, the draft refusal naming refresh, held export idempotent, blank template names, export's
role, pruning on cancel, and the panel's again, export and hold answers.
