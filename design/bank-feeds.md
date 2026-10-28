# Bank feeds

This contract covers roadmap item R108: bringing a bank's or card's transactions into a bank inbox from statement files
at any time, sorting them against the books, and undoing a whole import when it was wrong, with a live-feed connection
wired but not connected. It builds on `reconcile import` ([docs/cli/reconcile.md](../docs/cli/reconcile.md)): the same
parser, matcher and saved CSV mappings, a new inbox beside the reconciliation, and the reversible-batch mechanism of
[bulk import](bulk-import.md) §8a.

**Scope (the person, 2026-10-27, "full approved"):** import OFX, QFX and CSV statements at any time without ever
duplicating an overlap; match each line to an entry, add it in one click, ignore it or leave it waiting; rules for repeat
payees, written or learned; transfers between the business's own accounts recognised; manual reconciliation stays; a
live-feed connection wired but not connected, to be chosen by a person later and never with keys in a file. **Every
import is reversible**: "one issue with QuickBooks that comes up as if there is a large bank feed import that turns out
to be the incorrect account ... a stupid amount of manual work to resolve ... I would at the very least like to build this
with the last import being reversible." Widened the same day to every batch import or modification (bulk-import.md §8a).

> **Changed 2026-10-27 (R187, reshaped the same day).** Builds to [one permission model](principal-directives.md). Replaces
> the earlier note that had auto-add established by a cited, confirmed directive and a principal's tap. **Auto-add is an
> automation:** anyone who may write a bank rule drafts it; an actor holding the capability **`automate.bank-rule`** makes it
> live (§5, §8, K2) and it then runs under that actor's authority. Connection writes need **`bank-feed.connect`**. An agent holds
> neither unless its principal granted it. There is no directive citation, confirmation, tap, `automation establish` or agent
> policy; `bank-rule enable-auto-add` is not built.

**What exists.** `reconcile import` parses OFX, QFX and CSV (`statement_files.py`: `parse`, `line_id`, `match`,
`cleared_without_line`), names each line `matched`, `suggested`, `unmatched` or `reconciled`, and ticks matches on a
statement reconciliation. It stores `statement_imports` and `statement_lines` **per reconciliation draft**, append-only
by trigger (`draft_id` is required), plus `statement_csv_mappings` per account, and dedupes by `known_line_ids` (FITID, or
a hash with an occurrence number). It has no inbox, no rules, no state a line can be in once decided, and no way to take
an import back: its rows cannot be deleted. Nothing reads an account number from an OFX file.

Sources for the anchor: **[M]** the supplied Missing Manual (2016), printed page numbers; **[E]** the anchor's Enterprise
24.0 in-depth guide, page numbers. The guides describe screens, not mechanics; where silent this document says so, and the
rule given is Bookflow's.

## 1. The anchor's behaviour, and where Bookflow differs

**Matched.**

- **Two ways in:** a direct connection, or a file the bank offers ("Web Connect") imported by hand [M 619-621]. Files
  first; Bookflow's connection is the seam of §7.
- **A list to work through:** the Bank Feeds Center's Transaction List counts *Auto-Matched*, *Need Your Review*, *Added*
  and *Changed by Rules*; the list filters by status, type and dates [M 625-626]. The first download looks at cleared and
  uncleared entries, later ones only at what has not cleared [M 626]. Bookflow's inbox has the same counts and filters.
- **Match, add or ignore:** a line matches an existing entry (a payment waiting in Undeposited Funds, an open invoice, an
  entered check, unpaid bills) or is added with a payee and account by *Quick Add*, with *Add More Details* for splits and
  memo [M 627-630]; several lines add at once; with no check number a line matches the oldest same-amount entry [M 627].
- **Ignore:** a line already dealt with is deleted from the list, one or in a batch [M 631-632].
- **Renaming rules** replace the bank's payee text with a short name or categorise to an account; they are made
  automatically when the user renames, with a preference to ask first or not make them; tests are *contains*, *starts with*
  or *matches exactly*; the user edits or deletes them [M 633]. Enterprise adds batch editing of payee, account and
  class, and rules searched and defined with criteria [E 75].

**Deliberate differences.**

- **The inbox is a record that can be undone.** The anchor's *Ignore* deletes the line for good [M 631], and nothing in
  the guides undoes an import; Enterprise lists "mistakenly import the same transactions more than once" among its repair
  jobs [E 79]. Here nothing is ever deleted from the inbox; an import is one reversible unit (§6).
- **The file is checked against the account.** The anchor trusts the account the user picked. Bookflow reads the account
  number and kind a file carries (OFX `ACCTID`, statement or card) and warns when they differ from what that account
  last received (§3).
- **Matching is explained.** A line shows why it matched (amount, days apart, check number) or why a rule or history
  proposed what it did; the anchor's rules are named `_AutoGeneratedRule-1` [M 633].
- **Rules propose; a live automation acts.** A rule fills a line's payee, account and class. Only a rule whose *auto-add*
  was made live by an actor holding `automate.bank-rule` creates entries unattended (§5).
- **One engine for both modes.** The anchor has Express and Classic modes with separate rules and aliases [M 619-620];
  Bookflow has one inbox, one set of rules, and the account register stays one click away.
- **No suspense account.** Lines never touch the books until a person or agent adds one, or an auto-add rule made live by a holder of `automate.bank-rule` does.

**Outside this item:** a connected live feed or any aggregator (the seam only); online payments, bank messages and
transfers sent to the bank [M 621-623]; multi-currency feeds beyond refusing a file in another currency (as
`reconcile import` does); receipt photos; `.xlsx` statements (bulk-import K2); changing manual reconciliation.

**Prior art** (research `r7b03a7`; from knowledge, not re-checked against current releases). *GnuCash:* a matcher scoring
amount, date and description, the bank id stored per split, a Bayesian word store suggesting the account from past
choices. *Firefly III:* ordered rules with a stop flag, a hash per imported line, the import's entries tagged. *Odoo:* a
unique import id per line, reconciliation models (conditions, actions), a "to check" state, lines held on a suspense
account, a statement deletable while nothing is reconciled. *Akaunting:* CSV import and a tick-to-reconcile page, no
rules. *Adopted:* the bank id stored per line; a window-and-amount score; learning from accepted choices, but visible as
a rule; ordered first-match rules with a stop; a hash for files without ids; every import marked so everything it made
is findable; "delete only while nothing depends on it". *Rejected:* an opaque word-probability store; regex and
free-form rule actions (a rule an agent can edit must not run code or hang); the suspense account; undo by hunting a tag.

## 2. Lines, states and how the inbox relates to `statement_lines`

**Decision: a separate inbox table, shared code.** `statement_lines` is a per-draft, append-only proof of what a
reconciliation was built from; it cannot take a state or be taken back. The inbox needs both, so it has its own
`bank_lines`, and **nothing is forked**: the inbox calls `statement_files.parse`, `line_id` and `match` unchanged,
reads and saves CSV mappings in `statement_csv_mappings` (so a mapping saved by either door serves both), and the dedupe
test `statement_store.known_line_ids` is widened to also read live inbox lines. A line already in a draft's
`statement_lines` is not offered again as waiting. The two meet at one place: `reconcile import --from-inbox` (increment
6) copies chosen inbox lines into `statement_lines` through `statement_store.store`, recording `bank_line_id` on the
copy. `reconcile import` itself is unchanged and gives the same answers. Signs: the inbox keeps the bank's sign as parsed
(positive is money in, or a card payment); the matcher is handed the reconciliation sign exactly as `_prepare` does (a
card's flipped).

| State | Meaning |
|---|---|
| `waiting` | Imported; no decision. May carry a **proposal**: a suggested entry, or a payee, account and class from a rule or from history, with the reason |
| `matched` | Linked to an existing entry. `how`: `auto` (the matcher's unique strong match at import), `person`, `agent` or `rule`. The entry is untouched |
| `added` | An entry was created from the line; the line holds its id. This is the only state that can block a reversal |
| `transfer` | Linked to a transfer entry between two of the company's accounts, with the other end's line when it is in the inbox (§4) |
| `ignored` | Set aside with a reason (`already_entered`, `not_ours`, `duplicate`, `other`); can return to `waiting` |
| `settled` | The matcher found the entry already cleared by a certified statement; nothing to do |

| Act | From → to | What it records (event, audit, columns) |
|---|---|---|
| `bank-feed import` | → `waiting`, `matched`, `settled`, `transfer` | `imported`: batch, import, the count by state, warnings |
| `bank-line match` | `waiting`, `matched` → `matched` | `matched`: entry, `how`, score reason |
| `bank-line unmatch` | `matched`, `transfer` (a match, not a created entry) → `waiting` | `unmatched`: who, the entry it left |
| `bank-line add` | `waiting` → `added` | `added`: entry id and kind, payee, account, class, the proposal it came from, `rule_id` |
| `bank-line transfer` | `waiting` → `transfer` | `transferred`: entry, pair line, created or matched |
| `bank-line ignore` / `unignore` | `waiting` ↔ `ignored` | `ignored`: reason; `unignored` |
| reversal | any → hidden with its import | `reversed`: the reversal's batch event; the row and its events stay |

`bank_line_events` is append-only; the row is the current state; `bank-line history` answers who did what and why. An
`added` or created-`transfer` line is never moved by unmatch or ignore: take the entry back with its own command
(`check delete` and kin) or reverse the import; a line whose entry was since voided or deleted shows `entry_gone`
and may return to `waiting` by `bank-line release`.

## 3. Importing, and why an overlap never duplicates

`bank-feed import` takes `account`, a file as `content` or `attachment` (as `reconcile import` does), `format`, the CSV
mapping (`csv_mapping`, `mapping_name`, `save_mapping`) and the windows. It is two calls on one stored preview, like
bulk-import §5: `--dry-run` reads, dedupes, matches and applies rules, writing nothing; the real call re-derives the
same result inside the write and records it.

**Line identity (`line_key`), unique per account among live lines.**

- **OFX and QFX:** `fitid:<FITID>`. The same FITID with the same amount is the same line, even when the date moved (banks
  re-date a pending item when it posts; the date drift is noted in the line's event). The same FITID with a *different*
  amount is not hidden: it enters as `fitid:<id>!<amount hash>` with the warning `fitid_reused`, since some banks reuse
  ids and a silent drop loses money.
- **CSV and OFX without ids:** `hash:` + 24 hex of SHA-256 over date, amount, payee, memo, number and an **occurrence
  number among identical lines in the file** (existing `line_id`). Two 4.50 coffees on one day are two lines; a second file
  that overlaps the first and carries the same two matches both, and a later file with a third adds one. Overlap is by
  design, never an error.
- **Soft duplicates.** A hash line that is new but has an unclaimed live line in the same account with the same amount,
  a date within 3 days and a payee sharing most words is probably the same transaction with text the bank cleaned up. It is
  imported `waiting` with `possible_duplicate` naming the other line; the person ignores it as `duplicate` or keeps it.
  Never auto-dropped.
- **Whole file.** `file_sha256` of a file already imported to this account (live import) answers "this exact file came in on
  3 Oct (import X)" and refuses without `again`. The preview of any file says: date span, lines in the file, new, already
  in the inbox, already in a reconciliation's statement, soft duplicates, and the sum, so an overlapping download reads
  "44 lines: 3 new, 41 already in".
- **Reversed imports release their keys.** `bank_lines.live` is cleared by a reversal, and the unique index is
  partial (`WHERE live = 1`), so the same file can be imported again, to this account or another.

**The right account.** The preview shows the account large with the date of its last import and, for OFX, compares the
file's bank id, last four digits of `ACCTID` and statement or card kind with what this account last received
(`bank_feed_accounts`, learned from a person's first confirmed import). A mismatch refuses with `E_ACCOUNT_MISMATCH`
naming both and the account that last received this number, and a person passes `confirm_account` to go on. A currency
different from the account's refuses (`reconcile import`'s existing rule). CSV carries no id, so its preview shows the date
span, the first and last lines and the overlap with each of the company's other feed accounts ("38 of these 44 lines are
already in Savings"), which is the signal that catches a CSV sent to the wrong account.

**At import**, in one write: the import row, its lines, auto-matches (the matcher's unique strong matches, with `match_days`
default 4 and `suggest_days` 14 as today), rule proposals (§5), transfer proposals (§4), and its batch with its effects.
`company update --bank-auto-match` (default on, the anchor's behaviour [M 625]) turns off the automatic link; matches
then arrive as proposals.

## 4. Matching, adding and transfers

**Matching** is `statement_files.match` unchanged, run against the account's reconcilable movements (`candidates` in
`reconcile_import_cmds`), minus entries already linked to a live inbox line. The anchor's "oldest same-amount entry" [M
627] is what Bookflow's tie rule refuses to do: two equally good entries make a suggestion listing both, never a guess.
The match dialog shows candidates with the match reason and a search over the register for the rest; matching never
changes the entry and does not clear it for reconciliation (manual reconciliation stays, §9).

**Adding** runs the account's own register command with the line's values, so the entry is exactly what a person would have
entered: `register post` for a category or splits on a bank or card account (its help: unsure goes to Uncategorized
Expense or Income with a memo), `check post` when the line carries a check number and the person asks for it. Input:
payee (an existing vendor, customer or other name; *create as an other name* in the same savepoint on confirmation),
account or splits, class, memo, date (default the line's), number. One click is the Add button on a row that has a
proposal; the command runs as the caller with the line as source reference `bank-line:<id>`. A closed period, a locked
account or an inactive reference refuses with the command's own code and leaves the line `waiting`.
*Add More Details* flows that the anchor has [M 627-629] are increment 5: a deposit built from selected Undeposited
Funds payments (`deposit post`, difference remaining shown), and bills marked paid (`bill-payment`); both are created
entries and reversible like any add.

**Transfers.** A transfer is two lines in two accounts for one movement. Proposed when two `waiting` lines of different
accounts have opposite bank signs (a bank outflow and the card or savings inflow), the same absolute amount, dates within
5 days (`bank-transfer-days`) and no other line fitting equally; several fits are listed, never chosen. The pair is shown
as one row "Transfer Checking to Visa, 500.00". Accepting (`bank-line transfer`) matches both lines to an existing
`transfer` entry of those accounts, amount and nearby date, or posts one with `transfer post` and links both to it. A
line whose other end is not yet imported can be sent as a transfer to a named account now (`transfer post`, `state
transfer`, `pair` empty); when the other account's file arrives, its line matches that entry like any other and the pair
completes. A rule may declare `transfer to <account>` (payee text such as "ONLINE TRANSFER TO SAV"). Lines of a pair
live in separate imports; the entry's batch is the one that created it (blockers, §6).

## 5. Rules and learning

A **rule** (`bank_rules`) is one row: a name, `account_id` (null = every feed account), `direction` (`in`, `out`, `any`),
conditions (a list; all must hold) on `payee`, `memo`, `number` or `amount` with `contains`, `starts_with`, `equals`
(case-folded, whitespace collapsed) and `between` for amounts; an action: **categorise** (a payee to show, an account or
splits, a class, a memo), **transfer** (to an account) or **ignore** (with a reason); `auto_add` (default false; a *request*, live only once established, below);
`priority`; `source` `written` or `learned`; `state` `active`, `suggested`, `disabled` or `retired`; and a version. No
regular expressions and no code.

**Order.** For each `waiting` line at import, or on `bank-rule apply`, active rules for its account (then rules for all
accounts) are tried by `priority` ascending, then by specificity (`equals` before `starts_with` before `contains`; more
conditions first), then oldest first. **The first rule that matches wins and stops**; the line records `rule_id` and the
reason ("rule 'Shell fuel': payee contains SHELL"). Written rules have priority 100 by default, learned rules 1000, so a
hand-written rule always beats a learned one. A rule fills the proposal (displayed payee, account, class, memo) and acts
no further, except a **live** `auto_add` (add now, in the import's batch), a transfer rule (proposes the transfer) or a
live ignore rule (reason `rule`; ignoring lines is an automation too). A rule never touches a line that is already decided; `bank-rule apply
--dry-run` lists which `waiting` lines a new or edited rule would change.

**History proposals.** With no rule, the line is compared with earlier answers: the **payee key** (upper-case, digits, store
numbers, `#` runs, dates and punctuation removed, trailing noise words dropped) is looked up among lines that account's
people added or matched with a name. If at least 2 earlier lines share the key and one answer (payee, account, class) is
at least two thirds of them, it is proposed with "from history: 4 of 5 times". Fewer, or a split, proposes nothing.

**Learning.** When a person adds a line (or an agent does) and the same payee key now has at least two lines added with
the same payee, account and class and no different answer in between, a rule is created in state `suggested` with
`source learned`, `learned_from_batch` the batch of the line that tipped it, shown in the inbox as "Always do this for
SHELL? (2 times so far)". A person (or an agent) accepts it (`bank-rule accept`, becomes `active`), edits it or declines it;
declined keys are remembered and not suggested again. The *Always do this* checkbox on the Add dialog makes the rule at
once, as the anchor makes one when the payee is renamed [M 629, 633], and the company preference `ask|always|never` governs
whether the dialog offers it (default `ask`). Learning never requests `auto_add`.

**Auto-add is an automation** ([principal directives](principal-directives.md) §3). A rule's `auto_add` (or `ignore`
action) is a request, state `draft` until made live. Anyone who may write a rule, an agent included, drafts it. An actor
holding **`automate.bank-rule`** makes it live (`bank-rule update --live`), seeing the lines it would have added to the
current waiting set. The rule then runs under that actor's authority (`established_by`): at each import it acts only while it
is live, its establisher is active, and the establisher still holds the capability; otherwise it is `paused` with the reason,
one to-do is raised, and its lines wait for a decision as any line does. An operative edit (conditions, action, accounts, class,
memo, `auto_add`) by an actor without the capability returns the rule to `draft`; by a holder it stays live under the editor.
Each entry an auto-add creates cites the rule, its establisher and the line, inside the import's batch, so one reversal undoes it.

## 6. Reversing an import, and moving it

An import is a **batch** of kind `bank-import` (bulk-import.md §8a). Its effects are the entries it created, the matches it
made, the rules learned from it, and its lines; the account is its **scope**, and **the scope rule is strict**: an import
can be reversed only when no later import on the same account is live. The reason is real: a later file skipped lines
this one brought in, so reversing the earlier would leave the later one's overlap pointing at nothing.

**What blocks it** (each blocker is a `code`, the thing it names, and the command that clears it):

| Blocker | Meaning | Clear it with |
|---|---|---|
| `later_import` | A live import on this account came after: its id, date, lines | reverse that one first, or `--through <this>` to reverse the newer ones with it in one plan |
| `entry_reconciled` | An entry this import created is ticked on a reconciliation draft or cleared by a certified statement | undo that reconciliation step (`reconcile` commands) |
| `entry_deposited` | A created receipt or payment was included in a deposit | `deposit void` |
| `entry_applied` | Something was applied against a created entry (a payment to a bill, a credit used) | `bill-payment unapply`, `payment unapply` |
| `entry_edited` | A created entry has a revision after the one the import made | delete it yourself, or leave it and ignore the import |
| `entry_linked` | A created transfer entry is matched by a line of another account's live import | reverse that import first |
| `statement_copy` | A line was copied into a reconciliation's `statement_lines` | not clearable while that draft exists; the plan names it |
| `no_authority` | The caller lacks the delete grant (`transaction.<family>.delete`) for a created entry's family | a person with the grant |

Everything else is not a blocker: decisions on lines that created nothing (match, ignore, unmatch) are undone with the
import. The dependency checks beyond `later_import`, `entry_edited` and `statement_copy` are not new logic: the plan
**dry-runs the inverse commands** (each family's own `delete` or `void`), so reconciled, deposited, applied and closed-period
refusals are those commands' own, reported by name. A refusal is `E_BATCH_BLOCKED` carrying up to 50 blockers (and the
total) in `details.blockers`, so an agent sees exactly what to do.

**What a reversal does, in one unit:** deletes each created entry with preserved history (the family's `delete` command, so a restore
from preserved history stays possible, R151), unmatches every match, hides every line of the import and
clears `live` (releasing the keys), and retires the rules whose `learned_from_batch` is this one. A rule a person edited,
confirmed after editing, or wrote by hand is kept and listed as "kept". The import row, its lines, events and the batch
stay, marked `reversed` with who, how, for whom and why; `bank-feed show --include-reversed` and `batch show` read them.
Nothing is physically erased. A reversal is atomic up to 200 created entries (measured, §10); a larger one runs in slices
in state `reversing`: the account takes no new import, nothing can edit the batch, the plan was checked whole first, and
the same command resumes it. A blocker appearing between slices stops it `blocked`, naming it; the entries already
removed were this import's own.

**Move this import to another account** (`bank-feed move <import> --to <account>`) is a reversal followed by an import of
the **stored lines** (no file needed; the reversed import keeps its parsed lines), as one act: the plan shows the
reversal and, for the target, the new/duplicate/transfer counts and the account check (same currency, kind, id hint). It is
all-or-nothing in the common case (nothing created yet); when entries must be deleted in slices, the move completes the
re-import after the last slice. The new import records `moved_from`. Moving after entries were added deletes those entries
and re-sorts the lines in the target, with rules applied; entries a person edited block it like any reversal.

**What keeping this costs, measured.** A throwaway SQLite database with the proposed `bank_lines` (22 columns, five
indexes including the partial unique one) and one event row per fully sorted line: **about 820 bytes per line** (1,800
lines: 1.49 MB; 20,000 lines: 16.4 MB, so 0.82 KB at both sizes). A dedupe lookup of 500 keys took 9 ms against 30,000
live lines; clearing `live` on a 2,000-line import took 21 ms. The same medium benchmark company holds 2,732 transactions
in 135 MB, **about 49 KB per transaction on average** (an average over every table and proof, not a marginal cost). So a
line costs under 2 percent of the entry it creates. A small business with three accounts and a monthly file of 150 lines
adds about 4.4 MB a year; a heavy one (five accounts, 20 lines a day, 36,500 lines) about 30 MB a year, against about 1.8
GB for the entries if all were added. **Decision: keep every import reversible and never purge lines; no count bound.**
What bounds a reversal is the blockers, not storage: an old import is ordinarily blocked by newer ones and by
reconciliation, which is correct. The inbox page shows each account's newest three imports with an Undo button and the
rest under History, which is the shape Kabe asked for without discarding the older ones.

## 7. The live-feed seam

Mirrors [document delivery](document-delivery.md) §4. `bookflow/bankfeed/source.py` defines a protocol and a registry:

```python
class FeedSource(Protocol):
    kind: str                                   # "plaid-mcp", "direct"...: a string, no schema change per kind
    def check(self, conn) -> CheckResult        # can it connect with its secret?
    def accounts(self, conn) -> list[RemoteAccount]       # id, name, last4, kind, currency
    def fetch(self, conn, remote_account, since_cursor) -> FeedPage   # lines + next cursor + balance
```

`FeedPage` carries lines (provider transaction id, date, amount in bank sign, payee, memo, number), a cursor, the
reported balance and its date. **Only posted lines**: an adapter must not emit pending items, whose ids change on
posting. A kind is registered in code; **this build registers none**, so `bank-feed-connection add` refuses an unknown
kind, listing the available ones (none). Tests register a fake; a real one implements the protocol and passes the shared
conformance suite (`tests/bankfeed_source_contract.py`, built in increment 8: a first page, a next page by cursor, an
empty page, a replayed page that adds nothing, a permanent failure, a changed-amount id, no pending lines).

**Connections** (`bank_feed_connections`, person-set only): a name, `kind`, non-secret `settings` (JSON the kind
validates, server command pinned to a version), `secret_ref` naming an entry in the operating system's keyring (or an
environment variable the host reads), never the secret, `enabled`, `enabled_host`, and one row per remote account
mapped to a Bookflow account with its cursor (`bank_feed_connection_accounts`). A company file copied or restored arrives
with every connection disabled, so a restored backup cannot fetch twice.

**Fetch is the host's, never a command's**, and never holds the writer during a network call: the host reads the cursor,
calls the source outside the writer, then one write records the page **as an ordinary import** (`format feed`,
`line_key = fitid:<provider id>`). So a feed import has the same dedupe, rules, auto-match, batch and **reversal** as a
file, which matters most on the first connect: a mapping to the wrong account is one reversal. `bank-feed fetch
--dry-run` shows what a pass would bring in; with no enabled connection the tick does one `EXISTS` and returns. A person
approves the first backfill of an account (its preview), and a mismatch of the remote account's last four digits with the
local account's hint refuses as for files. No keys, tokens or bank credentials are stored in the company file, the
repository or a config file; a later item chooses a provider, which is a person's decision.

## 8. Agents and permissions

New capability `bank-feed` (reads at `member`, writes at `standard`), checked beside each command's own capability
(`ledger.post` for `add`, `transfer`, the delete grants for reversal). An agent's authority is the intersection with its
bound human's. Permission layers chained per increment: `bank-feed-v1`, `bank-line-v1`, `bank-reverse-v1`,
`bank-rule-v1`, `bank-source-v1`; none changes an existing capability or role default.

| Acts | Who |
|---|---|
| `bank-feed show`, `query`, `bank-line query`, `show`, `history`, `bank-rule show`, `query`, `suggested`, `batch show`, `query`, `bank-feed-connection query` | member |
| `bank-feed import`, `bank-line match`, `unmatch`, `add`, `transfer`, `ignore`, `unignore`, `release`, `bank-rule` create, update, accept, decline, `apply`, `bank-feed reverse`, `move` | standard, `bank-feed` plus each act's command authority; **an agent may do all of it** |
| making a bank rule's `auto_add` or `ignore` live (`bank-rule update --live`) | standard plus capability **`automate.bank-rule`** |
| every `bank-feed-connection` write and `check`; `bank-feed fetch` enabling for an account; `confirm_account` on an account-mismatch refusal; the first live backfill | admin plus capability **`bank-feed.connect`** |

- **An agent can sort the inbox:** import a file, accept the matcher's matches, add lines that have a proposal, ignore the
  obvious, pair transfers, write and accept rules, draft an auto-add rule, and
  report in words what it could not sort. Unless its principal granted it `automate.bank-rule` it cannot make a rule act, and
  without `bank-feed.connect` it cannot connect a feed or override an account-mismatch warning; the refusal names the capability.
- **Reversal is the caller's authority.** An agent that imported into the wrong account can reverse and move its own
  import while nothing created exists, or whenever its principal holds the delete grants for the families created.
  Without them the refusal says `no_authority` and names the grant. Reversal is never granted by `bank-feed` alone.
- **Agent adds carry the line.** Every entry an agent adds names the bank line and the import, so "what did the agent do on
  the 3rd" is one `bank-feed show` or `batch show`.
- **Isolation.** A principal who cannot read an account cannot see its imports, lines or rules (what exists but is invisible
  reads as absent); files, lines, connections and secrets-by-reference are per company.
- **Discovery.** The command finder answers "bank", "statement", "download", "bank feed", "import transactions", "rule",
  "wrong account" and "undo import"; the import preview names the next command.

## 9. Surfaces

### Storage and migrations

One company migration per increment, numbered after main's head when it merges (co0104 today; others are adding
co0105 and later); each keeps every existing row, index and trigger. The batch ledger's two tables are bulk-import's
increment 0 and are not repeated here.

| Increment | Tables and columns |
|---|---|
| 1 Import | `bank_feed_imports` (id, `batch_id`, account, format, `file_sha256`, `attachment_id`, mapping name, date span, line counts by outcome, `moved_from`, reason, who, how, when; never updated), `bank_lines` (id, import, ordinal, account, `line_key`, fitid, date, amount, payee, memo, number, `state`, `live`, `version`), partial unique index `(account_id, line_key) WHERE live = 1`, `bank_line_events` (append-only), `bank_feed_accounts` (account, id hint, kind, last import) |
| 3 Sort | `bank_lines`: `entry_id`, `link_how`, `created`, `pair_line_id`, `payee_display`, `proposal` (JSON), `rule_id`, `ignore_reason` |
| 4 Rules | `bank_rules` (as §5; append-only versions like `import_mappings`), `bank_rule_declines` |
| 6 Reconcile | `statement_lines.bank_line_id` (nullable, added; the table stays append-only) |
| 8 Seam | `bank_feed_connections`, `bank_feed_connection_accounts` |

Line rows change only `state`, link columns and `live`; events are never changed or removed; imports and batches are
never deleted. Uploaded files, when given as attachments, are company data in its attachment store; a pasted file is not
kept (the parsed lines are).

### Commands

| Command | Does |
|---|---|
| `bank-feed import` | §3; `--dry-run` is the preview |
| `bank-feed show`, `query` | an import with its counts and state, lines by state; imports per account newest first, with `reversible` and the blockers |
| `bank-feed reverse`, `move` | §6 (`--dry-run` is the plan; `--through`) |
| `bank-feed fetch`, `bank-feed-connection add`, `enable`, `disable`, `check`, `query` | §7 |
| `bank-line query`, `show`, `history` | the inbox, filtered by account, state, date, text, amount, rule |
| `bank-line match`, `unmatch`, `add`, `transfer`, `ignore`, `unignore`, `release` | §2, §4; each takes one line or `--lines` (several, as one `bank-sort` batch) |
| `bank-rule create`, `update`, `deactivate`, `show`, `query`, `apply`, `accept`, `decline` (auto-add goes live through `bank-rule update --live`, needing `automate.bank-rule`) | §5 |
| `batch show`, `query` | any batch, from bulk-import §8a |
| `reconcile import` | gains `--from-inbox` (increment 6); otherwise unchanged |

All are registry commands: CLI, HTTP, MCP and browser reach them identically, each with a dry run, idempotency on writes,
and a worked example in its help. Bulk sorting acts (`--lines`, "Add all with proposals") form a batch of kind `bank-sort`,
reversible by record-level rules (§8a): reversing one deletes only the entries it added and releases the matches.

### Browser

- **Banking → Bank inbox** (the anchor's Transaction List). A tab per feed account with its last import date; chips for
  Waiting, Matched, Added, Transfers, Ignored with counts; filters for dates, text, amount. A row: date, the bank's text
  (and the shown payee when a rule renamed it), amount, status, the proposal with its *why*, and buttons: **Add**
  (primary, prefilled), **Match** (candidate dialog), **Transfer** (shown on a pair), **Ignore**. Select rows for
  *Add all with proposals*, *Ignore*, *Set account/class* [E 75]. Forms use htmx; each tested with a real Chrome submit.
- **Import.** *File* (choose, drop or paste), *Account* (large, with the last import and the check of §3), *Check*
  (counts, overlap, soft duplicates, warnings, what rules and matches will do), *Done* (a card with the counts and **Undo
  this import**). Each account's imports are listed newest first.
- **Undo / Move dialog.** "Imported by Kabe, 3 Oct, 44 lines to Checking: 31 matched, 9 added, 3 ignored, 1 waiting."
  Below: what will happen ("delete 9 entries" with a list, "unmatch 31", "retire 2 rules"), then any blockers in red, each
  with how to clear it. Buttons: **Undo this import**, **Move to another account…** (an account picker of the same
  currency, with the target's check shown before confirming). A reversal in slices shows progress and resumes after a
  reload.
- **Rules page** (Banking → Bank rules): written and suggested rules, hits, *Apply to waiting*, a test box ("what would
  this payee do").
- **Phone (390 px).** The inbox is cards, one line each (payee, amount, date, status chip) with the primary action full
  width; selecting turns on a bar with the bulk actions; the match dialog is a full-screen sheet; import is a stepper
  taking a file from the phone's downloads; the Undo dialog is a sheet with blockers first. Every screen works at 1440 and
  390.

### An agent's first time

"Here is September's checking statement": `bookflow bank-feed import --account Checking --attachment <id> --dry-run
--json` returns the overlap ("44 lines, 3 new"), the account check, the matches, and `next`; the same call without
`--dry-run` imports. `bank-line query --state waiting` then `bank-line add --lines ...` for those with proposals, and the
report says which five it left. "That went into the wrong account": `bank-feed reverse <import> --dry-run` names what
would be undone or what blocks it; `bank-feed move <import> --to Savings` does it.

## 10. Measures and acceptance criteria

What R108 is for: the month's bank activity is in the books quickly, with the owner knowing every entry came from a
line the bank showed, and a mistake in the import, above all the wrong account, costs a minute rather than an afternoon.

1. **Overlap never duplicates.** Three overlapping downloads (OFX with ids; CSV without; CSV where the bank cleaned the
   payee text) imported in any order leave each transaction once; the text-changed case is flagged `possible_duplicate`,
   not dropped and not silently kept. A file already imported refuses without `again`.
2. **The wrong account costs one step.** A 150-line file imported to the wrong account, then 31 auto-matched, 9 added, 3
   ignored and 1 rule learned: `bank-feed reverse` leaves books, inbox and rules as before the import (the books' report
   totals and postings equal the snapshot taken before it; the deleted entries remain in preserved history); `move` yields
   the same lines sorted in the right account.
3. **Refusals name their blockers.** Each blocker of §6 is reproduced (reconciled, deposited, applied, edited, later
   import, linked, copy, no authority); the error lists the entry or import and the command that clears it, and no
   partial change is left.
4. **The right account is checked.** An OFX file whose account number differs refuses; a CSV with 86 percent overlap with
   Savings shows it before importing to Checking.
5. **One click is the same as typing.** An entry added from a line has the same postings as the same inputs through
   `register post` (audit differs only in `source_ref`); a closed period refuses with the same code.
6. **Rules are legible.** Order is by priority, then specificity, first match wins; a line shows its rule; a learned rule
   appears only after two consistent answers; a rule never changes a decided line; an actor without `automate.bank-rule` cannot make auto-add act, and an establisher who loses the capability stops it.
7. **Transfers are one entry.** Two files from two accounts, in either import order, end with one transfer entry linked
   to both lines.
8. **Manual reconciliation is untouched.** With no inbox in use, `reconcile import` and a full reconciliation give the
   answers they give today (golden outputs); `--from-inbox` produces the same draft as the same file imported directly.
9. **Nothing leaves.** With no connection the host makes no call; a fake source passes the conformance suite; a restored
   backup arrives with every connection disabled; no secret is in any table or file.
10. **A phone does it.** Import, sort and undo an import on a 390 px screen.
11. **Scale, recorded.** A 5,000-line file previews and imports; the inbox page of 100 lines, a 200-entry reversal and a
    move are timed and the numbers recorded in the as-built notes.

**Measure** (for the roadmap): closing-trial prompts done correctly on the first attempt, from the trial record.

| Prompt | Expected |
|---|---|
| "Here's September's checking statement" | `bank-feed import --dry-run` then the import; counts and overlap reported |
| "That went into the wrong account" | `bank-feed reverse --dry-run`, then `move --to` the right one; blockers reported if any |
| "Sort the inbox and tell me what you couldn't" | `bank-line query`, matches, adds, a list of the rest |
| "Always put Shell under Fuel" | `bank-rule create`, `apply --dry-run`, reported as a proposal-only rule |
| "Is the card payment a transfer?" | the pair proposed and accepted as one transfer entry |
| "Connect my bank" | explained as not available yet; no secret requested |

## 11. Build increments

Builder time: S 30-60 min, M 1.5-2.5 h, L 4-6 h. A company that never imports a statement reads exactly as before.
**Prerequisite:** bulk-import.md's increment 0 (the batch ledger); whichever of R107 and R108 starts first builds it.

1. **Import, lines, dedupe, auto-match (L).** Migration 1; `bank-feed import` over the shared parser and matcher;
   `line_key`, soft duplicates, whole-file check, overlap report, account hint from OFX and the account check; batch
   created with its effects; `bank-feed show`, `query`; `bank-line query`. *Done when* criteria 1 and 4 hold over CLI and
   MCP and `reconcile import`'s golden outputs are unchanged. *Tests:* §3 fixtures (re-dated pending lines, reused FITIDs,
   identical coffees across overlapping files, a hash file where payee text changed, a card file in OFX card sign).
2. **Reverse and move (L).** Blocker computation by dry-running the inverse commands; `reverse`, `move`, `--through`,
   slices, `reversing` and `blocked` states, key release; the 200-entry atomic bound measured. *Done when* criteria 2 and 3
   hold. *Tests:* every blocker; reversal during the next import; a kill between slices and resume; moving to a card and to
   a different-currency account (refused); property: random sequences of import, sort and reverse keep books equal to a
   replay without the reversed imports.
3. **Sort: match, add, ignore, transfers (L).** Migration 3; `bank-line` acts, `--lines` batches, transfer pairing and
   one-sided transfers; proposals with reasons. *Done when* criteria 5 and 7 hold. *Tests:* each add kind against its typed
   command; closed period; tie lines never guessed; both transfer import orders.
4. **Rules and learning (M).** Migration 4; `bank-rule` commands; order, history proposals, learning and decline memory;
   auto-add through the `live` check of principal-directives §3 (R187 increments 1 and 2 land first); `learned_from_batch` retirement on reversal.
   *Done when* criterion 6 holds and an auto-add rule drafted by an agent adds nothing until a holder of `automate.bank-rule` makes it live, adds on the
   next import, and stops adding when its establisher loses the capability. *Tests:*
   order and specificity, learned below written, auto-add in a batch and reversed with it, an edited learned rule kept,
   an edit by an actor without the capability returning a live rule to draft, entries citing the rule and its establisher.
5. **Add more details (M).** A deposit from selected Undeposited Funds payments and bills marked paid from a line.
   *Done when* a deposit line matches three received payments with difference 0.00 and reversal returns them to
   Undeposited Funds. *Tests:* difference remaining, a payment already deposited refused.
6. **Reconcile from the inbox (S).** Migration 6; `reconcile import --from-inbox`; `statement_copy` blocker. *Done when*
   criterion 8 holds.
7. **Inbox pages and phone (L).** The browser surfaces of §9 at both widths; undo/move dialog with slices progress; rules
   page. *Done when* criterion 10 holds by a real Chrome submit, with screenshots at 1440 and 390.
8. **Live-feed seam (M).** Migration 8; the protocol, empty registry, connections, host fetch tick, conformance suite with
   a fake, keyring `secret_ref`, restore disables. *Done when* criterion 9 holds. *Tests:* a replayed page adds nothing; a
   feed import reversed in one step.
9. **Agent pass and close (S).** Finder terms, agent guide chapter, regenerated docs, README, demo (a month's file with
   an overlap and a wrong-account import to undo), criterion 11 numbers. *Done when* the closing trial passes.

**Closing trial.** A fresh agent with only Bookflow's help and MCP does §10's prompts on a demo company with two
overlapping files and a card; checked afterwards as the person: the books, the inbox counts, the undo's effect, the rule's
behaviour. One more run starts on a phone.

## 12. Open questions

Each default stands if nobody decides. K1 to K3 are Kabe's.

- **K1. Strict newest-first.** Any later live import on the account blocks a reversal. *Default:* yes (simple to explain;
  later files skipped this one's lines). The alternative blocks only imports that overlap it.
- **K2. Who may auto-add. Answered (Kabe, 2026-10-27), reshaped the same day.** Whoever holds `automate.bank-rule`, human or
  agent, the same way ([principal directives](principal-directives.md) §3); agents do not hold it until their principal grants
  it. *Default for the open part:* a holder's edit keeps a live rule live under the editor. Admin can disable auto-add company-wide.
- **K3. A live provider.** Which aggregator or direct connection, if any, and whether a third party holding bank
  credentials is acceptable. *Default:* none chosen; the seam stays empty.

For the builder, defaults standing unless review finds otherwise:

1. **Matching does not clear.** *Default:* a matched line leaves the entry uncleared until a reconciliation; the register
   shows "matched to bank line".
2. **Auto-match.** *Default:* on (the anchor's), `company update --bank-auto-match`.
3. **Transfer window.** *Default:* 5 days, `bank-transfer-days`.
4. **Learning threshold.** *Default:* 2 consistent answers; history proposal at two thirds of at least 2.
5. **Reversal slice.** *Default:* 200 created entries atomically; set from the measurement in increment 2.
6. **Retention.** *Default:* every import and line kept for ever; no purge (§6).
7. **Partial reversal.** *Default:* none; a bank import reverses whole or not at all (bulk edits may opt in, §8a).
8. **Pasted files.** *Default:* not stored; the parsed lines are, which is all `move` needs.

## As built: increment 1

- **Migration co0111** (on co0109; co0110 is taken by the import and delivery branches, so whoever merges re-chains it)
  adds `bank_feed_imports`, `bank_lines`, `bank_line_events` and `bank_feed_accounts`. The partial unique index
  `(account_id, line_key) WHERE live = 1` is what makes an overlap never duplicate. Guards: an import and an event
  never change; a line keeps what the bank printed (identity, date, amount, text, key), is never deleted and never
  comes back to life once `live` is cleared; an account's record is never deleted. Every `*_id` column is a foreign
  key, so R107's soft-reference scan needs no new entry. Decided: `entry_id`, `movement`, `link_how` and
  `match_reason` come now, not with increment 3, because auto-match is this increment's and its link has to be kept;
  increment 3 adds only the proposal and rule columns. `possible_duplicate_of` and `warnings` are on the line too.
- **One parser, one matcher, one set of mappings.** `bank-feed import` reads with `statement_files.parse`, keys by its
  `line_id`, matches with `statement_files.match` over the candidates `reconcile_import_cmds.movement_candidates`
  builds (the builder `candidates` uses), in reconciliation sign, a card's flipped. Entries a live inbox line is
  already linked to are left out by `entry_id`, so an entry edited to a new revision is never matched twice; a
  second movement of a linked transaction on the same account then waits rather than risking a double. The
  candidates are read by `window_candidates` for the new lines' dates plus `suggest_days` only (the only entries the
  matcher can pair), from the stored current effects after the materialization check and with
  `payment_authority.authorize` over the transactions they name; the inbox posts nothing, so it does not re-prove the
  account's ledger as a reconciliation does. It reads and saves CSV mappings in
  `statement_csv_mappings`, so a mapping saved by either door serves both. Nothing is forked. The OFX account hint
  (`BANKID`, the last four of `ACCTID`, bank or card) is read with the parser's own tokeniser. `known_line_ids` is
  widened to the live inbox lines, which changes only `previously_imported` in `reconcile import`'s answer; a test
  imports the sample statement into the inbox both ways and checks every other field of `reconcile import` is as
  before, and the R167 golden tests run unchanged.
- **Keys.** A FITID line is the same line while its amount is the same and its date moved no more than 5 days
  (`DRIFT_DAYS`: banks post a pending line within one to three business days, five calendar days with a weekend; a
  weekly charge at seven days is already beyond it). A re-dated line gets a `seen_again` event on the line it repeats,
  naming both dates, written only by an import that brings a line, inside its batch. The same FITID for another
  amount, or for the same amount further apart (a monthly subscription under one id), in one file or across files, is
  a new line flagged `fitid_reused`, keyed `fitid:<id>!<amount hash>` (then `.<date>`) when the plain key is held.
  Lines are keyed in date order, not file order. A hash line is the parser's hash with its occurrence number.
- **A reconciliation's statement** holds a line (`in_statement`) only when it is the same line: the same id, the same
  amount in reconciliation sign and a date within the drift, on a standing draft (open, or consumed by a certificate
  that is still active). The same FITID there for anything else imports with `fitid_reused`; a discarded draft's
  lines hold nothing.
- **Soft duplicates.** Any new line, FITID or hash, with the same amount and a date within 3 days of a live line this
  file does not itself hold is imported `waiting` with `possible_duplicate` naming that line (each claimed once),
  unless their payees plainly name different merchants. Payees are compared by merchant name: letters and digits,
  casefolded, known bank short forms written out (AMZN, MKTP, SBUX, WMT...), processor words (POS, DEBIT, SQ, TST,
  PAYPAL...) and reference numbers dropped; the same when the names lead with the same word, one runs on from the
  other, or they read alike letter by letter (ratio 0.75). A payee it cannot read is flagged, not passed. So a pending
  line posted under a new FITID is kept and flagged; never dropped.
- **All or nothing.** A file the parser refuses is read again row by row (OFX transaction by transaction) through the
  same parser, and the refusal lists every problem (`details.fields`, up to 50, and `total`); nothing is written. A
  file whose `file_sha256` an unreversed import of the account holds refuses with `E_ALREADY_IMPORTED` naming it,
  unless `again`. A file with nothing new writes nothing (no import, no batch).
- **The account check.** An OFX file is compared with what the account last received, or, before its first import,
  with the account's own `institution_account_last4`; a card statement into a bank account (or the reverse), a
  different number, or a number another account received refuses with `E_ACCOUNT_MISMATCH` naming both and that
  account. `confirm_account` goes on and teaches the account the new number; it needs capability
  `bank-feed.connect` (admin; held by admin and owner). A first numbered file shows the number it will teach
  (`account_check.learns`, and a warning) and learns it on the run with no separate confirmation. A file with no
  number (CSV) is checked by content: when more than half its lines are already in another account's inbox, or match
  another account's entries (same amount in that account's sign within `match_days`) where this account matches
  fewer, it refuses with `E_ACCOUNT_MISMATCH` ("This looks like Savings's statement: ...") unless the caller passes
  `account_confirmed` (no admin capability: it teaches nothing). `overlap_elsewhere` lists each other account holding
  its lines; 50 percent or more of the file already in this account is a warning only, since a repeat adds nothing.
- **The batch.** Each import that brings a line is one batch (kind `bank-import`, ordering `strict`, scope
  `bank-account:<account id>`) written in the import's own transaction with its audit event: effects for the import
  row, each line (`created`), each auto-match (`linked`, the transaction) and the account record `bank_feed_account`
  (`created`, or `updated` with `before`: its number, bank, kind, last import and version), so a reversal restores
  the number a first or confirmed import taught. A CSV mapping saved with `save_mapping` stays outside the batch: it is
  shared with `reconcile import`, so reversing an import never removes it. A file with nothing new writes nothing but
  such a mapping. No adapter is registered yet: `batch
  show` reads it and reports it not reversible until increment 2 registers the reversal. Auto-match is on per import
  (`auto_match`, default true); the company preference `--bank-auto-match` waits for increment 3, where the sorting
  acts it governs are built. Matched and settled lines never touch the books.
- **Commands and permissions.** `bank-feed import` (standard), `bank-feed show`, `bank-feed query`, `bank-line query`
  (member), each with `ledger.read` member beside the new capability `bank-feed`. Layer `bank-feed-v1` on
  `delivery-batch-v1` (810 commands, descriptor `107637da`). `bank-feed import` is classified `not-a-record` for
  imports. `plan_in_write`: the real import reuses the plan made inside its own write transaction.
- **Not built here** (later increments): reversal and move (the batch is recorded, the adapter is increment 2's), the
  sorting acts and the company's auto-match preference, rules, the browser pages, the live-feed seam.

**Measured** (`tools/measure_bank_feeds.py`, one process per size, the root NVMe, a fresh company on the general chart
whose bank account holds 200 journal entries; an OFX file whose first 200 lines are those entries and the rest new;
seconds, 2026-10-28):

| Lines | Parse | Preview (`--dry-run`) | Import | Same file again (`again`, all already in) | Per line, import | Peak RSS |
|---|---|---|---|---|---|---|
| 1,000 | 0.03 | 0.40 | 1.35 | 0.19 | 1.4 ms | 304 MB |
| 10,000 | 0.31 | 7.55 | 17.96 | 1.36 | 1.8 ms | 343 MB |

All 200 entries matched at both sizes. The import plans inside its own write, so its time is the preview's work once
plus the writing: a line, its `imported` event and its batch effect. The preview grows faster than the file (19 times
for ten times the lines): the shared matcher pairs every line with every same-account entry, which increment 9's
5,000-line criterion will look at; nothing here changes that matcher.

**Seeded check** (`tests/test_bank_feeds_seeded.py`, seeds 1087 and 2026, 14 rounds each over two accounts: Checking
by OFX with re-dated pending lines, reused FITIDs and identical coffees; the other bank by CSV): 15 imports, 1 exact
repeat refused, 183 transactions shown and 183 live lines, so none twice; every import one live batch with one effect
per import row, line and match; the books unchanged after every round; `reconcile import` of the sample statement
equal before and after in every field but `previously_imported`.
