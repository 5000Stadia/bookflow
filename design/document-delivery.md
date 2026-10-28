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

> **Changed 2026-10-27 (R187, reshaped the same day).** Builds to [one permission model](principal-directives.md). Replaces
> the earlier note that gated the go on the principal's `approve` or a confirmed directive. **A message leaves when an actor
> holding the capability `delivery.send` releases or approves it**, human or agent alike; an actor without it can draft and
> request a release, and the message waits. `delivery.mark-sent` and `delivery.connect` are capabilities too. A `send` schedule
> is an **automation** that an actor holding `automate.delivery` makes live and that then runs under that actor's authority.
> No directive go, `go_directive_id`, `automation establish` or agent policy; `agent_go` stays unbuilt. D3 changed.

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
- **The go is a capability.** Anyone may draft; what leaves was released or approved by an actor holding `delivery.send`
  (§7). An agent holds it only if its principal granted it.
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
| `ready` | Released to the outbox. It waits for a go if it lacks one (§7), then for a sending connection and its next attempt time. With no connection it waits indefinitely, and that is correct |
| `sending` | A transport attempt is in flight under a lease |
| `sent` | Accepted by a transport (`sent_via` `connection`) or recorded by a person as sent from their own mail program (`manual`). Final |
| `held` | Needs a person: the document changed or was voided, no recipient, a template error, or an attempt whose outcome is unknown. Holds a `hold_reason` |
| `failed` | The transport refused permanently, or attempts ran out. Holds `failure_code` and the transport's text |
| `cancelled` | Set aside with a reason. Final |

| Act | From → to | What it records (event, audit, columns) |
|---|---|---|
| `delivery draft` | → `draft` | `drafted`: actor, interface, template, each source's kind, id and revision, warnings (empty merge fields) |
| `delivery update` | `draft`, `held` | `edited`: the fields changed; version bump (stale writers get E_VERSION_CONFLICT). `--refresh` re-freezes the documents as they are now (old and new revision and SHA-256 in the event), refills the subject and body the person did not write, and returns a `document_changed` hold to `draft` |
| `delivery send` | `draft` → `ready` | `released`: who, when; `go_by` and `go_at` when the actor holds `delivery.send` (an actor without it leaves the message `ready` with no go, a request) |
| `delivery approve` | `ready` | `approved`: `go_by`, `go_at`. Needs `delivery.send` |
| dispatcher claims | `ready` → `sending` | `claimed`: attempt number, connection, lease expiry |
| transport accepts | `sending` → `sent` | `accepted`: provider reference, connection, actual From, `sent_at` |
| transport says try later | `sending` → `ready` | `attempt_failed`: code, text, `next_attempt_at` from the backoff |
| transport refuses, or last attempt fails | `sending` → `failed` | `attempt_failed`: code, text; `failure_code` |
| lease runs out, or the outcome is unknown | `sending` → `held` | `held`: `outcome_unknown` |
| precheck fails at claim | `ready` → `held` | `held`: `document_changed`, `document_voided`, `no_recipient`, `template_error` |
| `delivery retry` | `held`, `failed` → `ready` | `retried`: who. (Refreshing is `delivery update --refresh`, one verb; 2026-10-27) |
| `delivery cancel` | any but `sent` | `cancelled`: reason required |
| `delivery mark-sent` | `draft`, `ready`, `held`, `failed` → `sent` | `marked_sent`: who, `method` (`mail-program`, `webmail`, `other`), `sent_at` as told (not future). Needs `delivery.mark-sent` |

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
`enabled` and an optional `daily_limit`. The row never holds a secret: `secret_ref` names an
entry in the operating system's keyring (or an environment variable the host reads), never the secret.
A company file copied or restored on another machine arrives with every connection disabled and
`enabled_host` cleared, so a restored backup full of `ready` messages cannot send a second copy of
anything. `delivery-connection enable` on a machine binds it there.

**Dispatch** is the host's, never a command's, and never holds the writer during a network call: one
write claims up to a small chunk of messages (`ready`, a go present, `next_attempt_at` due, under the daily limit; a
`go_by` counts only while that actor is active and still holds `delivery.send`, otherwise the message goes back to
waiting for a go), the transport is called outside the writer, and one
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
fair-share rule: no batch holds the writer for its whole length; 10 messages per call, measured in
increment 3 at about 0.9 s of writer time for invoices and 2 s for statements), returning `next`; the browser loops with progress. A batch makes **drafts**,
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
- **`send` mode is an automation** ([principal directives](principal-directives.md) §3). An actor who may write schedules,
  an agent included, creates and edits the schedule as `draft`. An actor holding **`automate.delivery`** turns `send` mode on
  (`delivery-schedule update --mode send`), seeing who would get mail now. The schedule then runs under that actor's authority
  (`established_by`): it sends only while it is live, its establisher is active, and the establisher still holds `automate.delivery`
  and `delivery.send`; otherwise it is `paused` with the reason. A change to its selection, template, combine, mode or frequency
  by an actor without `automate.delivery` returns it to `draft`; by a holder it stays live under the editor (R111's lesson
  that content changes under the authority). A run of a live schedule creates messages with `go_by` the establisher; a paused or
  draft `send` schedule still runs as `draft` would, leaving the messages `ready` and waiting for a go, and raises one to-do,
  not one per message.
- **Recorded as R111 records.** The messages' audit actor is the schedule's last editor, interface
  `system`, client `scheduler`, reason "On schedule: <name> for <slot>".
- With no connection, a `send` schedule still runs: it fills the outbox with messages `ready`, which is
  the point of building the item before its sender.

## 7. Agents and permissions

A new capability `delivery` (reads at `member`, writes at `standard`, like other lists), and four grantable capabilities
([principal directives](principal-directives.md) §4): `delivery.send`, `delivery.mark-sent` (humans at standard), `delivery.connect` (humans
at admin) and `automate.delivery`. Agents hold none of the four until their principal grants them. Drafting also
needs the authority that prints the document: `ledger.read` for invoices, receipts, statements and
notices, `customer-work` read for estimates. A permission layer per increment is chained on the tip
(`delivery-v1`, `delivery-queue-v1`, `delivery-batch-v1`, `delivery-schedule-v1`); none changes an
existing capability or role default.

| Acts | Who |
|---|---|
| `delivery-template` writes; `delivery draft`, `update`, `export`, `retry`, `cancel`, `batch`, `flag`, `unflag`; `delivery-schedule` writes (a `send` schedule is `draft` until made live) | standard, `delivery` (an agent may do all of it) |
| `delivery send` (release with a go) and `delivery approve` | standard, `delivery` plus **`delivery.send`**; without it, `delivery send` leaves the message `ready` and waiting |
| `delivery show`, `query`, `history`, `queue`, `run --dry-run`; `delivery-template show`, `query`, `fields`, `preview`; `delivery-schedule show`, `query` | member |
| `delivery mark-sent`; making a `send` schedule live; every `delivery-connection` write and `check` | standard plus **`delivery.mark-sent`**; standard plus **`automate.delivery`**; admin plus **`delivery.connect`** |
| `company update` delivery preferences (from-name, reply-to, copy-to, auto-flag date) | admin, as other company preferences |

The line is "what leaves the machine, and what happened outside it":

- **Drafting is anyone's; the go is a capability.** An agent can compose every email of the month and put them in the
  outbox. A message leaves only if an actor holding `delivery.send` released or approved it, still holding it when the
  dispatcher claims the message. An agent that holds `delivery.send` (a principal's standing grant such as "send each invoice to
  its customer when I tell you the job is done") releases directly. Without it, an agent's release leaves messages
  `ready`, shown "Waiting for a go", and `delivery approve` (one id, a batch, or "all waiting") is a holder's tap. The daily limit
  on a connection still caps what leaves.
- **`mark-sent` and `delivery-connection` are capabilities, not defaults for agents,** on the model of `check print-confirm`:
  what happened in the owner's mail program is known to the owner, and where mail goes and under whose keys is the
  owner's decision. An agent asking without the capability gets E_PERMISSION naming it and tells its principal.
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
| 2 Queue and seam | `delivery_connections` (name, `name_key`, `kind` as free text with no CHECK, `settings` JSON, `secret_ref`, `enabled`, `enabled_host`, `daily_limit`, last check result); the restore and copy path clears `enabled` |
| 3 Batches | `delivery_flags` (source kind and id, `wanted` true for *Email later*, false for an explicit opt-out; one row per source); `delivery_batches` (selection JSON, counts, state, schedule run); `delivery_messages.batch_id` |
| 4 Schedules | `delivery_schedules` (the fields of §6 and `state` `draft|live|paused`, `established_by`, `established_at`, `paused_reason`); `delivery_schedule_runs` (unique `(schedule_id, slot_date)`, state, counts, skipped reason) |

Every state change writes its event in the same transaction as the row. No guard counts rows. The
message and event tables are never deleted from.

### Commands

| Command | Does |
|---|---|
| `delivery-template create`, `update`, `deactivate`, `show`, `query`, `fields`, `preview` | §3; `preview` renders a template against a named document without drafting |
| `delivery draft` | One message for a document (`kind` + identity), `--template`, `--to --cc --subject --body`, `--combine`, `--with-attached-files`, `--again` |
| `delivery update [--refresh]`, `cancel`, `retry` | §2 |
| `delivery send` | Release drafts (one, several, or a batch) to `ready` |
| `delivery approve` | Give a go (needs `delivery.send`) to `ready` messages (ids, a batch, or `--all-waiting`) |
| `delivery mark-sent` | Record a hand-sent message: `--method`, `--sent-at` |
| `delivery export` | `.eml`, or a zip of several |
| `delivery show`, `query`, `history` | A message; the outbox with filters (status, kind, customer, batch, date, `delivery`); its events |
| `delivery queue`, `flag`, `unflag` | §5 |
| `delivery batch` | §5 |
| `delivery run` | One dispatch pass; with no connection it reports "0 claimed, no sending connection" |
| `delivery-connection add`, `update`, `enable`, `disable`, `remove`, `check`, `show`, `query` | §4; `add` with no registered kind is refused naming the kinds available |
| `delivery-schedule create`, `update`, `pause`, `resume`, `run`, `show`, `query` (turning `send` mode on needs `automate.delivery`) | §6 |
| `company update` | gains the three delivery preferences |

All are registry commands, so the CLI, HTTP, MCP (`bookflow_run`) and the browser reach them
identically, each with a dry run, idempotency on writes and a worked example in its help. Refusals use
existing codes: E_VALIDATION (bad address, unknown field, nothing to release), E_WORK_DEPENDENCY with
`details.status` (a state that does not allow the act), E_VERSION_CONFLICT, E_PERMISSION (`principal_only`, with `people_only` kept).

### Browser

- **Email… on the record page** of an invoice, sales receipt and estimate, beside *Print / save PDF*, and
  on each statement row, opens a compose panel (htmx): To, Cc, Subject and body from the default template
  with a template picker, the PDF with a preview link, and *Save draft*, *Send*, *Export .eml*. Forms gain
  the anchor's *Email later* checkbox [M 239]. A holder's *Send* is release and go in one tap. With no
  connection the panel says: "No sending connection is set up. This will wait in the outbox. Export it to
  send it yourself."
- **The Outbox** (Customers menu): tabs *Waiting for your go*, *Ready*, *Sent*, *Needs attention*,
  *Drafts*, *Cancelled*; filters by kind, customer, batch and date; each row shows customer, document, To,
  subject, state, who and when, and the document's business state (unpaid, paid). Checkboxes feed Approve,
  Send, Export, Mark sent and Cancel. A header line says whether a connection exists and what that means.
- **Batch:** *Email statements…* (statement report) and *Email unsent invoices…* (invoice list) open a
  preview with a checkbox per customer, the skipped with reasons, and a progress bar while drafting.
- **Company settings > Email and sending:** from-name, reply-to, copy-to, auto-flag date; Templates;
  Schedules (a `send` schedule shows *Turn on* to holders of `automate.delivery`); Connections (holders of `delivery.connect`): "None. Nothing leaves this machine." The customer
  page shows the preferred method and latest messages.
- Every screen works at 1440 and 390 pixels; forms are htmx and tested by a real Chrome submit. The
  outbox count is offered to R184's home flow.

## 9. Privacy

Nothing leaves the machine without both a connection set up by a holder of `delivery.connect` and a go from a holder of `delivery.send`. This build
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
4. **The go holds.** A message released by an actor without `delivery.send` is not sent until a holder approves it; a go whose
   giver has lost `delivery.send` by the claim returns the message to waiting; an actor without the capabilities cannot approve,
   mark sent, touch a connection or turn a schedule's `send` on, and the refusal names the capability.
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
   dedupe and `--again`; stale and voided sources; `.eml` parsed with the standard library; `mark-sent` needing its capability; the notice hand-off; a real Chrome submit of the panel; draft times recorded.
2. **The queue and the seam (M).** `delivery_connections`, the protocol and registry, the dispatcher and
   `delivery run`, claim and record outside the writer, retries, unknown outcome, `approve` and the go
   rule, events from a transport, copy and restore disabling connections, the conformance suite with the
   fake. *Done when* criteria 3 to 7 pass with the fake transport and an empty registry in production.
   *Tests:* the whole state table row by row; backoff; lease expiry and a crash between claim and record;
   the go (holder, non-holder, a holder who lost the capability before the claim) and `daily_limit`; the socket and subprocess blocker over a full pass; a restore test;
   the idle-tick measurement.
3. **Flags, the customer's method and batches (M).** `delivery_flags`, auto-flag date, `queue`, `flag`,
   `unflag`, `batch` in chunks with dry run, `--combine`, notices as a source, the batch preview and
   *Email later* in the browser. *Done when* every selection in §5 drafts the right messages on the demo
   and on 1,000 documents, skips are named, and a rerun duplicates nothing. *Tests:* each selection
   against hand-worked sets; preference inheritance for a job; auto-flag date; chunk and resume; batch
   and chunk times recorded.
4. **Recurring schedules (M).** `delivery_schedules` and runs, the scheduler block, slot claims, the
   catch-up rule, making `send` live through the §3 check of principal-directives (R187 increments 1 and 2 land first), the to-do for
   approval, the Schedules page. *Done when* a monthly statement schedule and a daily queued-invoice schedule run
   unattended on a test clock, once per slot, a `send` schedule an agent drafted sends nothing until a holder of `automate.delivery` makes it live, and an edit by a non-holder returns it to draft. *Tests:* a month-end start across February;
   restart mid-run; a missed slot inside and outside the window; draft on an edit by a non-holder, pause on the establisher losing a capability or being deactivated; the scheduler's idle tick unchanged for a company with no schedules.
5. **Close (S).** Agent guide and `docs/` sections, README, `printed-documents.md`, the demo seed, the
   schema and command census, and the closing trial of criterion 8. *Done when* criteria 8 and 10 pass.

## 12. Open questions

Each default stands if nobody decides. D1 to D3 are the person's to decide; the rest are the builder's.

- **D1. HTML mail.** The anchor has formatted mail and a plain-text option [M 671]. *Default:* plain text
  only; the schema does not block an HTML part later.
- **D2. The first real sender:** which kind (a mail MCP server, SMTP with an app password, another) and who
  sets it up. *Default:* none in this item; a later item adds one kind against the conformance suite.
- **D3. Who may approve and mark sent. Reshaped by Kabe's R187 direction (2026-10-27).** *Default:* capabilities
  (`delivery.send`, `delivery.mark-sent`), held by humans at standard and by agents only when their principal grants
  them; there is no connection flag (`agent_go` is dropped). Grant `delivery.mark-sent` to an agent if hand-sent records prove tedious.
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

## As built: increment 2 (2026-10-27)

**What exists.** co0107 (`delivery_connections`, removed and never deleted), the seam in
`bookflow/delivery/transport.py` (`Transport`, `OutgoingMessage`, `TransportResult`, `TransportEvent`, `TransportKind`, the
registry, `resolve_secret`), the dispatcher in `company/delivery_dispatch.py`, connections in
`company/delivery_connections.py`, and eleven commands under the `delivery-queue-v1` layer (on `delivery-v1`): `delivery
approve`, `retry`, `run`, and `delivery-connection add`, `update`, `enable`, `disable`, `remove`, `check`, `show`,
`query`. **The registry is empty in production**: `add` refuses every kind, naming the kinds available (none), and a
test asserts no module registers one. The browser gains Approve (a person, on a message waiting for a go, and in the
Outbox), Retry (a failed message or an unknown outcome), the failure and hold explained on the message page, and the
connections listed in Email and sending.

**Decisions taken in the build, each reversible:**

- **Interim permissions, marked for R187.** `approve` and every connection write and `check` are people-only admin
  actions (approve at `customer` standard, connections at `company` admin; reads at `company` member), as `mark-sent`
  is. A go counts at the claim while its giver is an active person with a standing membership of the company or its
  organization at standard or above whose `denies` do not name `customer`, and, where permissions are activated,
  while the policy in force still admits them for `customer` at standard; an installation administrator counts only
  through such a membership. `delivery send` and `retry` by a person give the go; an agent's leave the message
  waiting.
- **A go is for the exact content and recipients it saw.** Holding a message (at release, export, the claim, an
  unknown outcome or a lease that ran out), editing it, refreshing it and an agent's release each clear `go_by` and
  `go_at`, and the event records `go_withdrawn` (whose go ended). A changed document therefore always needs a new go
  after its refresh. Each place carries a comment naming `delivery.send` or
  `delivery.connect`. No hub migration.
- **A withdrawn go is an `edited` event** (`changed: ['go']`, `go_withdrawn`, why): the event list is fixed by co0105's
  CHECK, and the message stays `ready`, waiting for a new go.
- **Three steps, no transaction open across a send.** `decide` reads, `claim` writes in one write (lease 10 minutes, one
  more attempt, the `claimed` event with `attempt_key` `<id>:<n>`), the transports are called with nothing open, and
  `record` writes every outcome in one write. Every update is guarded by the version read, so a message changed
  meanwhile is left for the next pass. `delivery run` owns its transactions as `memorized process` does, and reopens
  the commit barrier (`CommitHooks.reopen`) once its claim is committed, so readers go on while the transports talk;
  through the host it still occupies the writer thread for its chunk's calls (at most 10), which a person asked for.
  On the host, the scheduler tick claims on the writer and hands the calls to the host's own `bookflow-delivery`
  thread (never the writer or the timer thread), which submits the record as one more writer job; the timer does not
  wait for it, and a tick claims nothing more for any company while that thread is still busy.
- **Each claim is read again just before its send** (on the writer, through the host): it is sent only while still
  `sending` under this attempt and connection with at least one bounded send's time (`SEND_TIMEOUT`) left on its
  lease. A claim that fails the check is skipped: nothing leaves, and a still-live one goes back to `ready` with its go
  (an `attempt_failed` event, code `not_sent`, not counted as a failure).
- **Every transport call is bounded:** `send` by `transport.SEND_TIMEOUT` (120 s; an answer that does not come is an
  unknown outcome, held), `open`, `check` and `events` by `CALL_TIMEOUT` (30 s), each on its own daemon thread that is
  abandoned if it hangs. Host shutdown waits `delivery_stop_seconds` (10 s) for the delivery thread, then records what
  it could not: a send in flight is held `outcome_unknown` ("the host stopped before the transport answered"), a claim
  never handed over goes back to ready.
- **Retries:** 5 min, 30 min, 2 h, then `failed`: four attempts counted since the last `retry` (the `retried` event
  keeps `attempts_before`; `attempts` never resets, so attempt keys never repeat). An unknown outcome, a transport that
  raises, and a lease that runs out are held `outcome_unknown`; a late answer after the lease is kept in the history
  (`late: true`) and moves nothing, except a late `accepted` for a message now `held` or `ready` (retried, not yet
  claimed), which resolves it to `sent`. `retry` refuses once any `accepted` is on record for the message (its next
  step is `mark-sent`), so a late acceptance can never be duplicated. A transport that cannot be opened is a temporary
  failure (nothing left). The backoff counts from the time of the record (on the host, the tick's time plus the
  send's duration), not the claim.
- **The claim precheck** draws each document as the go's giver where the host allows acting for someone (its writer
  thread); an in-process `delivery run` reads as the actor running it, who holds the same reads.
- **The daily limit** counts `claimed` events through the connection in the last 24 hours (a rolling day).
- **Copies are inert three times over.** `company restore` and `company attach` clear every connection (`enabled`,
  `enabled_host`); a connection sends only where `enabled_host` matches this installation: a fingerprint of a random
  install id (`<data root>/install-id`, created once, never inside a company file), the host name, the data folder and
  the company id, so a hand copy onto a replacement machine with the same name and path does not match; and the next
  pass (`delivery run` or the tick) disables any connection enabled elsewhere (`connection disabled`). Backups keep
  `secret_ref` (a name, never a secret) and the restore side disables every connection, as §4 asks ("arrives with
  every connection disabled").
- **Tracking** polls a transport that defines `events` for messages it accepted in the last 14 days until one bounces
  (a relay's `delivered` is often followed by the far end's bounce; at most 50 refs, every 15 minutes on the tick);
  `delivered` and `bounced` are events, `delivery` on the message is derived from the newest, and a bounce raises one
  to-do (`delivery-bounce:<id>`) for the person who drafted it (or who gave the go). A tick with only tracking to ask
  takes no write (and so no commit barrier) unless an answer came back.
- **A transport's own words are redacted and capped** (`transport.clean_text`): addresses, `password=`/`token:`-style
  pairs, `Bearer` values and long token-like strings are replaced, then the text is cut at 500 characters, before it
  lands in `failure_text`, `last_check_text`, an event, the audit, a backup or a member's view. `secret_ref` must match
  `env:NAME` or `keyring:service/name` exactly (no trailing newline).
- **The conformance suite** (`tests/delivery_transport_contract.py`) runs through the dispatcher against a company: a
  harness scripts the far side (`plan`) and reports what arrived (`delivered`, `deliveries_of`). The fake
  (`tests/delivery_fake_transport.py`) is its first harness; a real adapter's wraps a local test server.
- `delivery-connection check` calls the transport when applied (a person's explicit act, bounded by `CALL_TIMEOUT`);
  `--dry-run` asks the transport nothing.

**Not in this increment:** a sender; flags, batches (increment 3); schedules (increment 4).

**Measured:** the idle scheduler tick for a company with no connection and nothing in flight, through the host
(median of 25): 9.56 ms with the delivery hook, 9.43 ms without; the hook's one statement on the writer, 44.5 µs
(`tests/test_delivery_queue.py`, printed with `-s`). Within the run-to-run noise: nothing measurable.

**Tests:** `tests/test_delivery_queue.py` (the empty production registry; connections never holding a secret; the
conformance suite against the fake; backoff; a crash between claim and record and the lease; unknown outcomes and a
raise; the claim precheck and retry pointing at refresh; the daily limit; tracking and the bounce to-do; a restored
copy disabled; a full pass with sockets and processes blocked; the go for a person, an agent and a person deactivated
before the claim, through the host's tick; the idle-tick measurement; and the review fixes: a go ended by the hold,
the refresh and an agent's release (nothing sends), a giver denied `customer` or an installation administrator
without a membership, the commit barrier open during a host `delivery run`'s send, a claim skipped when its lease ran
short or a concurrent pass held it, a late acceptance resolved to sent and a retry refused after one, a hung send
bounded in process and on the host's delivery thread with checkpoints going on and shutdown holding it, a connection
from another installation and an attached file disabled, tracking after delivered, the backoff from the answer, a
poll-only tick without the barrier, a dry-run check and redacted text), `tests/test_delivery_seeded.py` (the queue,
five seeds of 30 steps: 20 messages reached the fake server, none twice; 20 crashes between send and record, 4 leases expired, 6 held unknown outcomes, 4 failed, 11 retry waits, 11 sent), and `tests/test_delivery_surfaces.py` (the new commands on all four surfaces).

## As built: increment 3 (2026-10-27)

**What exists.** co0109 (on R107's co0108; `delivery_flags`, `delivery_batches`, `delivery_messages.batch_id` with its index), seven
commands under the `delivery-batch-v1` layer (on `delivery-queue-v1`): `delivery flag`, `unflag`, `queue`, `batch`,
`batch-show`, `batch-query`, `batch-cancel`; `delivery draft` gains `combine` with `documents`; `delivery send` and `approve` take a
`batch`; `delivery query` filters by `batch` and a message shows its batch; `company update` gains
`delivery_auto_flag_from`. Code: `company/delivery_batches.py`, `company/delivery_batch_models.py`. The browser has the
*Email later* checkbox beside Email… on invoices, sales receipts and estimates (an htmx fragment that posts on change),
the batch page (*Email the queue…*, *Email unsent invoices…*, *Email statements…* from the Outbox: a preview with a
checkbox per message, the skipped with why, then a progress bar that loops chunk by chunk, Send the drafts and Cancel
the batch), the Outbox's batch filter, and the auto-flag date in Email and sending.

**Decisions taken in the build, each reversible:**

- **Permissions** follow increments 1 and 2: `customer` at `standard` to flag, unflag, batch and cancel a batch, at
  `member` for `queue` and `batch-show`, each with `ledger.read` and `customer-work` at `member`. A batch's `send`
  releases as `delivery send` does: a person's release is their go, an agent's waits (comment naming `delivery.send`
  for R187). No people-only action is added: drafting is anyone's.
- **To be emailed is derived** (§5) from the books, the flags, the date and the messages on every read; the customer's
  method is effective through the job's parents, and an explicit `unflag` wins over the date. A flagged document goes
  even to a customer who prefers mail (the person asked); otherwise `--unsent` skips `prefers_mail` and an opted-out
  document. `queue --method mail` lists the customers only.
- **The selection is fixed when a batch begins** (`targets`, `skipped` as JSON on the batch), so `next` resumes
  exactly. Each target is drafted through `delivery draft`'s own planner inside the batch's write; one that gained a
  message meanwhile, or can no longer be drafted, is skipped then and named (`in_outbox`, `sent`, `refused` with the
  refusal's words). A selection with nothing to draft keeps no batch (`state: nothing`). Skip reasons: `no_address`,
  `prefers_mail`, `not_email` (statements with `--method email`), `opted_out`, `in_outbox`, `sent`, `excluded`
  (`exclude`, the browser's unticked boxes), `refused`.
- **Missing specifics are refused, naming what to give** (no selection, no kind for `unsent`, no period for statements,
  `ready` for notices, statement-only filters elsewhere); the dry run lists exactly who gets what and who is skipped and
  why, and the agent guide tells an agent to show it before a broad batch.
- **Statements** take the customers with a balance over `balance_over` (default 0) from `report
  customer-balance-summary` at the period's end, read as the principal; a job is its own customer, as the statement
  report has it.
- **`--combine`** puts one customer's documents of one kind in one message, one PDF each; `document.number` lists every
  number and the other document fields are kept only where the documents agree (a warning names the rest). A combined
  message's dedupe key lists every source; a single source keeps increment 1's key. A refresh redraws every document.
- **A batch is cancelled as one unit:** every message it drafted that was never released or exported is cancelled
  (event `cancelled` naming the batch), and the ones that block are named and stay (`released`, `exported`, `sent`);
  drafting stops. Cancel writes in one transaction (200 statements: 2.6 s). **For R186:** a delivery batch maps onto the
  shared batch ledger as one entry whose reversal is this cancel; nothing later depends on a batch's drafts (a later
  batch only skipped what this one held, and cancelling puts those documents back in the queue), so newest-first holds.
  `delivery send --batch` releases the drafts a chunk per call, naming how many wait.

**After the review (notes/reviews/r112-inc3-review.md), 2026-10-27.** Two rules govern: *a go covers exactly what was
reviewed* (increment 2), and *the person confirms a broad batch's exact details once, from the preview, and the run
does exactly what the preview showed* (Kabe).

- **A row's key covers its whole group.** A combined row's key is `G-` and a hash of its documents; excluding it leaves
  out every document in it. A document id still leaves out that document alone.
- **The go is decided once, when the batch begins** (`delivery_batches.go_by`): a person who asks to `send` gives it;
  an agent's batch has none, and a later continue by anyone never adds one (`BatchOutput.go`).
- **Approve and send by batch act only on messages as the batch left them.** The batch records each message's
  version, a hash of its recipients and a hash of its content (`drafted`), and re-records it after its own release or
  approval; anything else is skipped and named with why. A cancelled batch is refused by name. `approve` takes 100 per
  call and says when more wait; `batch` with `messages` is refused.
- **Opt-outs win mid-batch.** Each chunk rechecks its targets: an unflag (`opted_out`), a customer switched to mail
  (`prefers_mail`), a queued document no longer to be emailed (`not_queued`), a document revised or recipients changed
  since the batch began (`changed`), and a member of a combined row that gained a message meanwhile (only that member
  is skipped, with its message).
- **The run is tied to the preview.** The dry run returns a `fingerprint` of the exact set (each row's documents,
  revisions, recipients, period and balance; excluded rows included) and one per row; a run carrying it is refused
  (`E_WORK_DEPENDENCY`) when the set changed, naming the new or changed rows when `seen` gives the preview's rows. The
  batch keeps its fingerprint, and a continue carrying another is refused. The agent guide says to show the preview
  and run with its fingerprint.
- **Getting back to a batch.** `delivery batch-query` (a read, in `delivery-batch-v1`) lists unfinished batches
  (drafting, or with drafts to send or messages waiting for a go) with the commands that continue or cancel them; the
  Outbox lists them with Continue and Cancel; `/delivery/batch?batch=B-1001` opens one; a dry run over the selection
  of a batch still drafting names it (`open_batches`) and the page offers to continue it. The batch view always has
  Continue drafting and Cancel while drafting; a refusal on continue, send or cancel keeps the batch view with the
  refusal in place and stops the automatic loop; a network failure shows a note and leaves Continue.
- **Email later in the browser:** unticking clears the flag (`delivery unflag --clear`, back to neutral); *Never
  email this* is the lasting opt-out. `delivery queue` with `document` returns that document's `flag`.
- A continue that echoes default fields (`send: false`, `exclude: []`) is accepted.

**Measured** (in process, a fresh company, `tests/test_delivery_batch.py` with `BF_DELIVERY_LARGE=1000`, printed with
`-s`): 1,000 unsent invoices for 200 customers: selection (dry run) 1.2 s; the whole batch 92 s in 100 calls, a chunk
of 10 at 0.88 s median and 2.53 s at most (the first call 1.75 s), 0.09 s per draft; 200 statements: selection 3.1 s,
the batch 44 s, 1.99 s per chunk. The chunk stays 10, the dispatcher's.

**Not in this increment:** schedules (increment 4; `delivery_batches.schedule_run_id` waits for them); the demo seed's
messages and the closing trial (increment 5).

**Tests:** `tests/test_delivery_batch.py` (the queue from flags, the date and a job's inherited method; each selection
against hand-worked sets with its skips; combine and its refresh; statements and notices; refusals naming the missing
detail; chunks, resume, a skip found while drafting and the cancel with its blockers; send and the batch release; the
demo company's queue batched and rerun; the 1,000-document measurement), `tests/test_delivery_batch_browser.py` (Email
later ticked on a demo invoice; the batch preview, one left out, drafted through the htmx loop and cancelled, at 1440 and
390), `tests/test_delivery_seeded.py` (batches: five seeds of 30 steps of flags, dates, methods, batches, reruns and
cancels; after every step the queue equals the hand-worked rule, no document has two live messages, every skip is named,
and a cancelled batch keeps only drafts exported before it).

## As built: increment 4 (2026-10-27)

**What exists.** co0110 (on co0109; `delivery_schedules`, `delivery_schedule_runs` unique on `(schedule_id,
slot_date)`, both guarded against delete), eight commands under the `delivery-schedule-v1` layer (on
`delivery-batch-v1`): `delivery-schedule create`, `update`, `live`, `pause`, `resume`, `run`, `show`, `query`. Code:
`company/delivery_schedules.py`, `company/delivery_schedule_models.py`; the host's tick calls
`delivery_schedules.tick` before the delivery claim (`core/host.py`), so a live run's releases can leave on the same
tick. The browser has Email schedules (from Email and sending): each schedule with its state, cadence, next slot and
last run, Run now, Stop / Resume, Edit, a New schedule form, and *Turn on…*, the make-live check page.

**Decisions taken in the build, each reversible:**

- **Making live is its own command, `delivery-schedule live`,** people-only until R187 (an admin action like
  `delivery approve`; the refusal names `automate.delivery` in `details.needs`). `update --mode send` by anyone sets the
  mode and leaves the schedule a draft. Each R187 place says `automate.delivery` in a comment: the catalog layer, the
  live and update planners, `authority_lost`, and `people_only.R187_CAPABILITY`.
- **One check of the exact details (Kabe's rule for broad changes).** `live` with `dry_run` returns `check`: the
  selection and cadence in words, the first run, the period, the template, whose authority it runs under, who it would
  reach if it ran today (and who is left out, why), and a fingerprint of the operative fields and that exact set. The
  act needs `confirm` equal to it (`E_VALIDATION` without, `E_WORK_DEPENDENCY` when anything changed). `show --check`
  gives an agent the same check to put to its principal. No second "are you sure".
- **An operative change** (kind, selection, template, combine, frequency, start, stop, runs, mode, catch-up window)
  returns a live or paused schedule to draft, unless a person passes the changed schedule's check as `confirm` (then it
  stays live and the editor becomes the establisher). An agent passing `confirm` is refused like `live`. A name change
  is not operative.
- **Whose authority.** A run drafts as `run_as`, the last actor to make an operative change (an agent with its
  principal and epoch, checked as R111's entries are), through `delivery-schedule run` under the tick's day
  (`clock.as_of_day`); interface `system`, client `scheduler`, reason "On schedule: <name> for <slot>". A live run's
  go is the establisher's, decided once when its batch begins: the establisher must still pass `go_lost` (the interim
  `delivery.send`/`automate.delivery` rule: an active person with a standing membership at standard not denied
  `customer`); otherwise the schedule is paused with the reason, one to-do is raised (per pause), and the run drafts
  and releases with no go, so its messages wait. The dispatcher's own check at the claim still applies.
- **An actor who can no longer lend authority** (deactivated, an agent unassigned, suspended or re-epoched), or any
  refusal of the step, pauses the schedule as the host, skips the due slots (`no_authority`), fails an open run, and
  raises the one to-do, so a failing schedule never retries every minute.
- **A send schedule that is not live** still runs: it releases what it drafts with no go, and one to-do per run
  ("N email(s) from <schedule> wait for your go") goes to the establisher, else `run_as` if a person, else the agent's
  principal, else an active administrator.
- **Slots.** A step claims the latest due slot as a run if it is no older than `catch_up_days` (default 7, at most 31)
  and records every other due slot `skipped` (`superseded` inside the window, `outside_window` beyond it; at most 200
  per step). The claim and the first chunk are one write; later steps draft one chunk each (the batch's `CHUNK`). The
  run is the batch's `schedule_run_id`. `remaining_runs` counts runs, not skips. A month-end start keeps month end
  (anchor 31: 31 Jan, 28 Feb, 31 Mar; 29 Feb 2028 to 31 Mar); a 30th is clamped in February and returns to the 30th.
- **Statement periods** for a slot: `previous-month`, `month-to-date` (the 1st to the slot) and `since-last-run` (the
  day after the last done run's period end, else the previous month).
- **`pause` stops** (`active` false; nothing runs); `resume` restarts from today, recording slots that passed while
  stopped as `skipped` (`stopped`), and brings a paused schedule back as a draft.
- **The idle cost** is one statement (`has_due`: an active schedule due by today, or a drafting run of an active one).
- The hook is not an after-apply dispatch hook (none was added), and no guard trigger counts rows.

**Not in this increment:** `automation list/show/pause` and R187's capabilities; the shared batch ledger entry for a
run (R186); the demo seed's schedules and the closing trial (increment 5).

**Measured:** the idle scheduler tick for a company with no schedules, through the host (median of 25): 8.92 ms with
the schedules hook, 9.20 ms without; the hook's one statement on the writer, 40.0 µs (`tests/test_delivery_schedules.py`,
printed with `-s`). Within the run-to-run noise: nothing measurable.

**Tests:** `tests/test_delivery_schedules.py` (month-end slots across February and a leap year, missing details
refused, a month-end statement schedule once per slot with a second run the same day finding it; the catch-up rule
for a daily schedule off nineteen days (only the 20th runs; seven superseded, twelve outside the window), a weekly one
whose latest slot is outside its window (nothing runs), two days off inside it, and a stop and resume skipping what
passed; a restart mid-run continuing from its batch with no duplicate; on the host: an agent's send schedule leaving
every message waiting with one to-do and nothing sent, the agent refused `live` naming `automate.delivery`, a
person's check and confirm, an agent's edit returning it to draft, a person's confirmed change keeping it live, and a
live run's message sent on the same tick; an establisher deactivated and one without the authority each pausing it
with one to-do and nothing with a go; the idle-tick measurement), `tests/test_delivery_schedule_browser.py` (the form
saved by a real htmx submit, Turn on's check read and confirmed, at 1440 and 390), `tests/test_delivery_seeded.py`
(schedules: five seeds of 30 steps on a test clock with runs, stops, resumes, re-establishing and the establisher
losing and regaining authority; after every step one run per slot, no run claimed past its window, and no go on a run
begun without authority or its messages), and `tests/test_delivery_surfaces.py` (the eight commands on all four
surfaces).
