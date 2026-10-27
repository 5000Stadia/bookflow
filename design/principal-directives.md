# Principal directives

This contract covers roadmap item R187: an agent acts *for* a principal and only within the principal's directives. It
settles the language, what an agent's write must carry, how an automation is established, and what only the principal
decides. [Bank feeds](bank-feeds.md) (R108), [document delivery](document-delivery.md) (R112) and
[bulk import](bulk-import.md) (R107) are written to it.

**Direction (Kabe, 2026-10-27):** "Absolutely agree that the agents should have the power, but not make those judgment
calls automatically. In the distant future, there may be an entity that is not the human that is the orchestrated
entity, engaging with the agent that is using book flow, which is respected as the end user who is technically not
human, so it might be worth considering our framing with our language. However, let us frame the initiating user as
the principal entity who gates the most fundamental sensitive or broad decisions, in regards to what work flows are
entered or accepted into book flow. And similarly even a singular invoice should not be posted if the directive of the
principal entity is not for an agent to post said invoice. If a rule is established to automatically invoice every
customer under a criteria XY&Z then the rule extracted out of that type of language should be all that is necessary to
establish the book flow automation as such this would expand to the topic that we are having right now."

**Reading.** Agents keep their power; what changes is whose words authorise it. (1) The initiating user is the
*principal*, a role and not a kind of person. (2) A write is the principal's instruction carried out, so it can be
traced to words. (3) Work that repeats on its own is established by the principal's words and by nothing else. (4) The
principal's words are *all* that establishes an automation: no separate technical review, but Bookflow shows the rule it
read from them, so a misreading is seen at the one tap.

## 1. Language

A **principal** is the entity an agent works for. Today that is the signed-in user an agent is assigned to
(`agent assign --principal`, already so named); later it may be a non-human orchestrating entity. Bookflow's only test is
`is_principal(actor)`: an actor in its own right, not an agent acting for someone. Today that means a signed-in user.
The test lives in one place (`hub/people_only.py`, `_is_person`) so a non-human principal is a change to it and nothing else.

| Today | Becomes |
|---|---|
| "people-only", "a person's go", "ask a person" | **principal-only**, **the principal's go**, "ask your principal" |
| `human` in agent text ("Human the agent will act on behalf of") | "the principal the agent acts for" |
| A person's standing instruction | a **directive** given by the principal |

- **A rename of words now; of identifiers never.** Designs written from today say principal. At R187 increment 1 the
  words users and agents read change (refusal messages, MCP and CLI help, the agent guide, `docs/`), and the refusal's
  details carry **both** `principal_only: true` and the existing `people_only`. The catalog keys `admin:people-only:*`,
  the `human_only` flag, `src/bookflow/hub/people_only.py`, `tests/test_people_only.py` and the layer hashes pinned on
  them **stay**: renaming them re-pins every permission layer for no user-visible gain. Error code stays `E_PERMISSION`.
- **Additive only.** Every new input is optional, every new error code is new, no command or field is renamed. A client
  written before R187 behaves as before until a principal sets a policy (§2) or an automation is established (§3).
- **Person versus principal.** "Person" stays where a human is meant (a customer's contact, a user's login). "Principal"
  is used wherever the thing is the authority over an agent.

## 2. Instructions, directives and what a write carries

**One-off instruction versus standing directive.** "Bill Maple Street $1,200" is an instruction for one act. "Every month,
invoice the retainer customers" stands until withdrawn. Both are recorded as a **directive** (blueprint 5.8), differing in
`uses_allowed`: 1 for a one-off (spent by the first write that cites it; unspent ones lapse after 7 days), empty for a standing one.

**Today** an agent's write carries a `reason` or a `directive` (`E_REASON_REQUIRED` otherwise). That stays the default
and means a company that never sets a policy sees no change. Two additions:

- **A reason quotes the instruction.** Agent guide rule, not a code check: a reason names who asked and what ("Jordan:
  bill Maple St $1,200", 140 characters), and `--source-ref` carries the message or ticket id when the client has one.
  The check cannot judge a quotation; the audit trail can show it.
- **A principal can require a directive for kinds of work.** `agent policy set AGENT --require-directive invoice,payment,
  deposit` (principal-only; `agent policy show`; an `AGENT` of `*` sets the company default). Per kind the level is
  `reason` (default), `directive`, or `blocked` (the agent may not write that kind at all). A **kind** is a capability
  family the registry already names (`transaction.invoice`, `transaction.payment`, `import`, `bulk-edit`, `delivery`,
  `bank-feed`); the builder takes the list from the registry, not from this page.

**Directive fields added** (migration; existing rows keep meaning):

| Field | Meaning |
|---|---|
| `given_by` | the principal whose words these are |
| `recorded_by` | who typed it, the principal or an agent |
| `covers` | the kinds it authorises, set when recorded and shown at confirmation; empty = a reason only, never enough for a gated kind |
| `uses_allowed`, `uses` | one-off or standing, and how often it was spent |
| `confirmed_by`, `confirmed_at` | the principal's confirmation; null until given |

**Who may record, and what makes it trustworthy (recommended).** *An agent may record a directive from the principal's
words (`directive add --given-by jordan --covers invoice`, `given_by` defaulting to its only principal), but it is
unconfirmed until the principal confirms it once (`directive confirm SI-7`, principal-only).* A principal recording their
own directive is confirmed by that act. Rejected: principal-only recording (an agent could not capture "from now on..."
the moment it is said, and the principal would retype what they already told it) and trusting the agent's claim (Bookflow
cannot hear the conversation, so an unconfirmed claim would authorise anything). An unconfirmed directive can still be
cited as a *reason* exactly as today; it is not enough for a gated kind, for crossing a size threshold, or for an
automation. Directive text never changes after recording (a change is a new directive and the old one is deactivated),
so what the principal confirmed is what is cited. Existing directives: a user's are confirmed; an agent's (their
`given_by` is the agent) are unconfirmed notes and stay valid reasons.

**Refusals** (new codes, additive):

| Code | When | Says |
|---|---|---|
| `E_DIRECTIVE_REQUIRED` | an agent write of a kind the policy gates cites none, or one that does not list the kind in `covers`, or a spent one-off | the kind, the policy and who set it; "record the instruction with `directive add --text ... --covers <kind>` and ask your principal to confirm it, or ask your principal" |
| `E_DIRECTIVE_UNCONFIRMED` | the cited directive has no confirmation | its code, who recorded it, and the confirm command |
| `E_DIRECTIVE_INACTIVE` (exists) | deactivated | unchanged |

The check sits beside `E_REASON_REQUIRED` in the write gate, after permission admission, so it applies to every adapter
(CLI, HTTP, MCP, workbench for an agent). A dry run reports the refusal without writing.

## 3. Automations established by directive

An **automation** is any rule that makes writes with nobody asking at the time: R108 bank auto-add, R112 recurring
sends in `send` mode, and "automatically invoice every customer meeting X, Y and Z" (a criteria-based invoice schedule,
a later kind built on this contract). R111's memorized entries are the first of the family and keep their own model (Q4).

**The rules.**

1. An automation's record **cites the directive whose words establish it** (`--directive SI-9`, required to go live).
2. **An agent may draft it and propose it** (state `proposed`), reading the rule from the principal's words.
3. **It goes live only by a principal's act:** `automation establish KIND ID` (principal-only), or the principal's
   confirmation of the cited directive while the automation is attached to it. The one tap shows the directive's words
   beside the rule as Bookflow renders it (conditions, action, who it touches, how often), never as the agent worded it.
   An agent can attach a proposal to a directive that is already confirmed, but only the principal's tap makes it live;
   this closes an agent citing an unrelated confirmed directive. The directive must be active, confirmed, and list the
   automation's kind in `covers`.
4. **It stays live only while all of these hold,** checked at each firing and never stored as a flag:
   the directive is active; its confirming principal still holds authority (R111's lesson, `principal_authority_loss`);
   and the automation's **fingerprint** (a hash of the operative fields: conditions, action, scope, template, mode,
   frequency) equals the fingerprint the principal saw. An edit by anyone drops it to `paused` (reason `changed`) until the
   principal taps again. A pause is shown, never silent, and the first firing after it raises one to-do for the principal.
5. **Deactivating the directive pauses every automation citing it** (`directive deactivate` lists them first in its
   preview). A deactivated directive cannot be re-activated; the principal records or confirms a new one and the
   automation is pointed at it (`automation attach`) and established again.
6. **An automation's writes cite the directive** (system or importing actor, reason "On rule: <name>", `directive_id`
   set), so `audit list` answers "which words allowed this" for every entry. A system write from an automation with no
   live basis is refused, never downgraded to a reason.
7. **Reversal is the net, not the gate.** Every effect of an automation lands in a batch ([bulk-import](bulk-import.md)
   §8a), so a misread rule is one reversal; this is why the tap shows words and does not ask for a second review.

**The shared mechanism** (`automation_basis`, one table, like the batch ledger; whichever of R108 and R112 starts first
builds it): `kind`, `record_id`, `directive_id`, `drafted_by`, `established_by`, `established_at`,
`established_fingerprint`. `automation.status(kind, id)` returns `live` or `paused` with the reason and is the only way a
host loop or importer decides to act; commands `automation list`, `show` (member), `attach` (standard: an agent may point
a proposal at a directive), `establish` and `pause` (principal-only; an agent may *pause*, never resume: stopping is safe).
A domain's own verbs that used to turn automation on (`bank-rule enable-auto-add`, `delivery-schedule authorize`) are
not built; the domain row carries only the *request* (`auto_add`, `mode send`).

**Not covered by an automation:** a learned bank rule never requests auto-add; a rule that only proposes a payee, account or
class is a suggestion and needs nothing. A rule that *ignores* lines waiting for a decision acts on the books' inbox and
is an automation (it needs the tap).

## 4. What stays principal-only

Principal-only means no agent can do it by any directive; a directive can authorise *work*, never *authority*.

- **Who may do what:** roles and permissions, `agent assign`, `authorize`, `agent policy`, principals, the closing date,
  directive confirmation, `automation establish`, turning an automation back on. (Existing: the catalog's human-admin set.)
- **What leaves the machine, and what happened outside it:** every `delivery-connection` and `bank-feed-connection` write
  and check, `delivery approve`, `delivery mark-sent`, `check print-confirm`, `check reprint`, `confirm_account`, the first
  backfill of a live feed.
- **What cannot be undone or reshapes the books:** merges and unmerges, restores, `item convert`, `costing-method set`,
  designating the accountant, importing an accountant's copy.
- **Broad runs past the principal's size:** `import approve`, `bulk-edit approve` (bulk-import §8): an agent over a
  threshold needs the principal's go for the run, *or* a confirmed directive that covers the kind and is cited (the
  directive is the standing go). Thresholds are company settings, principal-set.

Everything else is an agent's to do within its policy. Adding to this list is the principal's call (roadmap rule), not an
agent's.

## 5. What exists, what R187 builds

| Exists | Where |
|---|---|
| `directive add/list/show/deactivate`, `--directive` on writes, reason-or-directive rule | blueprint 5.8, `company/directives.py`, `docs/cli/directive.md` |
| `agent assign --principal`, `principal_authority_loss`, authority epochs | `docs/cli/agent.md`, `scheduling_schema.py` |
| principal-only acts (E_PERMISSION `people_only`) | `hub/people_only.py`, `tests/test_people_only.py` |
| unattended memorized entries bound to their setter's authority | R111, `company/scheduling*.py` |

Increments (S 30-60 min, M 1.5-2.5 h). 0 to 3 land before R108 increment 4, R112 increments 3 and 4 and R107 increment 4.

0. **Wording (S).** Designs and this contract (done); then the guide, help and refusal text; both detail keys. *Done when*
   an agent refused a principal-only act gets `principal_only` and `people_only` and the words "ask your principal";
   `tests/test_people_only.py` passes unchanged; `docs check` finds 0.
1. **Directive provenance and confirmation (M).** Migration; `--given-by`, `--covers`, `--once`; `directive confirm`;
   `E_DIRECTIVE_UNCONFIRMED`. *Done when* an agent records a directive for Jordan, it shows "awaiting Jordan", Jordan's
   confirm makes it confirmed, and a directive recorded before the migration still cites as a reason.
2. **Agent policy (M).** `agent policy`, the gate and `E_DIRECTIVE_REQUIRED`, one-off spending. *Done when* with invoices set to
   `directive` an agent's invoice post is refused, succeeds citing a confirmed one-off covering `transaction.invoice`,
   and a second post on the same directive is refused; with no policy nothing changes.
3. **Automation basis (M).** The table, `automation.status`, the commands, pause by deactivation, fingerprint, authority
   loss. *Done when* a fake automation goes live only by a principal's tap, deactivating its directive pauses it and its
   next firing writes nothing, an edit by an agent pauses it, and the audit shows the directive on its writes.
4. **Consumers.** R108 (auto-add), R112 (send schedules, agent release) and R107 (thresholds) build on 0 to 3 as their
   designs say; they add no authority mechanism of their own. *Done when* each design's criterion for it passes.
5. **Close (S).** Agent guide chapter ("what you may do without being told twice"), README, `docs/`, and a fresh agent told
   "auto-add Shell fuel to Fuel" drafts and proposes, posts nothing, and posts after the principal's tap; the principal
   deactivates the directive and the next import adds nothing.

## 6. Open questions

Each default stands if nobody decides. K1 and K2 are Kabe's.

- **K1. Settled (Navigator, 2026-10-27, from Kabe's "the rule extracted out of that type of language should be all
  that is necessary").** The principal's words establish the automation in one step: when the principal confirms a
  directive, the confirmation shows the rule or rules the agent extracted from those words beside them, and that one
  confirmation makes them live. No second tap. A rule drafted later under an already-confirmed directive, or one that
  differs from what the confirmation showed, needs the principal's tap, so an agent can never switch a rule on by citing
  an unrelated or older directive.
- **K2. The default policy for a new agent.** *Default:* `reason` for every kind (today), with `bookflow setup` offering an
  "Ask first" preset: `directive` for invoice, payment, deposit, bill, import and delivery. Flip the default only if
  Kabe wants new companies strict.
- 1. **One-off life.** *Default:* single use, lapsing after 7 days unspent.
- 2. **Confirmation strength.** *Default:* a signed-in principal's tap; no re-authentication.
- 3. **A non-human principal.** *Default:* not built; `is_principal` is the only seam. The workbench stays for people.
- 4. **R111's memorized entries.** *Default:* not retrofitted; they remain bound to their setter's authority and cite no
  directive. A later item may move them onto `automation_basis`.
- 5. **Renaming identifiers** (`people_only.py`, catalog keys). *Default:* never; words only (§1).
- 6. **Meaning checks.** *Default:* none. Bookflow checks active, confirmed, covers and fingerprint; it does not judge whether
  words fit a write. The tap and reversal are the answer.
