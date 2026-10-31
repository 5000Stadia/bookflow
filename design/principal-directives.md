# One permission model (R187)

> **Replaces the earlier draft of the same day (2026-10-27).** That draft built a second authority system beside
> permissions: per-agent reason/directive/blocked policies, directive confirmation, one-off directives, a shared
> `automation_basis` table with fingerprints, and principal taps. Kabe judged it more architecture than the problem
> needs and chose one model instead. The three designs that cited the draft ([bank feeds](bank-feeds.md) R108,
> [document delivery](document-delivery.md) R112, [bulk import](bulk-import.md) R107) are re-aligned in the same change.

**Direction (Kabe, 2026-10-27):** "I like this direction that you are framing, however in deploying it, we need to
consider the most elegant shape for the roles of agent user, human user that uses the UI and whatever this principal
entity is and how it interfaces. Ultimately, an elegant approach from my perspective is establishing a permission level
that is independent of the interface used such that theoretically a human could use the command line interface or the UI
and vice versa, with an agent in essence multiple methods to establish the same work with certain permissions and
permission levels about what work can be done. I think that this is a little more elegant than building too much extra
architecture about nested mandates and some slight guidance on set up would help establish mindfulness in the deploying
principal entity about how mandates should be governed from their initial framing. At the end of the day, the
responsibility pretty much lies on their shoulders."

**Reading.** One question decides every act: *does this actor hold the capability?* Humans and agents are asked the same
way, whether they arrive by browser, CLI, HTTP or MCP. The **principal** is whoever deploys Bookflow and grants levels; they
choose each actor's level and answer for it. Bookflow does not rank their reasons or re-ask them. It records who did what, by
which interface, on whose behalf and why, and every batch can be reversed.

## 1. The model

- **Roles and capabilities are the only gate.** Roles (`readonly`, `standard`, `admin`, `owner`), membership grants and
  denies, and the layered catalogs (`hub/permission_*_catalog.py`, `permission_runtime.py`, [permission.md](../docs/cli/permission.md))
  decide what work an actor can do. An agent's effective authority stays the intersection of its own and its assigned
  principal's ([permission-resolution](permission-resolution.md)), so no agent exceeds the human it acts for.
- **The check is adapter-independent.** It runs in the write gate after admission, for every adapter. Nothing asks what kind of
  actor is calling except the defaults below. A human with a token on the CLI or MCP, and an agent, pass the same test.
  Whether an agent may use the browser is a login question (Q3), not a permission one.
- **Defaults differ by actor kind; the mechanism does not.** Humans keep exactly the rights they have today. Agents get none of
  the capabilities in §2 and §4 until the principal grants them (`membership grant --grants`).
- **No self-escalation.** Holding `membership.grant`, `user.manage` or `agent.manage` lets an actor give only what it holds, to a
  role no higher than its own, and never to change its own membership. Existing `admin:members:<role>` keys remain as this ceiling.
- **Language.** "Principal" replaces "person" where the authority over an agent is meant ("ask your principal"). Identifiers, catalog
  keys and `E_PERMISSION` stay; refusals carry `capability` and `reason: capability_not_granted`, with `principal_only: true`
  and `people_only` kept on the few acts in §3.

## 2. People-only commands become capabilities

Today the catalog's human-only list holds 37 commands and `admin:company:closing-date` (`hub/people_only.py`,
`permission_human_admin_catalog.py` and later layers), plus 10 `admin:` rule keys behind users, agents and memberships.
Sorted: **(a) 33 become grantable, (b) 5 stay.** Each (a) capability is held by the same human roles as today, so no human
gains or loses anything, and by no agent until granted.

| Capability (working name; the registry fixes the key) | Commands | Humans hold it at |
|---|---|---|
| `party.merge` | customer merge, unmerge; vendor merge, unmerge | standard |
| `list.merge` | account merge, unmerge; item merge, unmerge | standard |
| `restore.document` | journal restore; invoice restore | standard |
| `item.convert` | item convert | standard |
| `check.print-confirm` | check print-confirm, print-cancel, reprint | standard |
| `clock.link` | clock link | standard |
| `accountant.manage` | accountant add, remove; accountant-copy cancel, import | admin |
| `costing.method` | costing-method set, withdraw | admin |
| `company.closing-date` | `company update` changing `closing_date` | admin |
| `backup.schedule` | backup schedule | hub admin |
| `demo.reset` | demo reset (its `force` guard for real books stays) | hub admin |
| `user.manage` | user add, activate, deactivate | hub admin |
| `membership.grant` | membership grant, revoke (ceiling in §1) | per `admin:members:*` |
| `agent.manage` | agent create, activate, deactivate, unassign | hub admin |

**(b) Stays tied to a human credential or to the root of trust: 5 commands.** Each is here because no capability could
make it safe, not because it is sensitive.

1. `user set-password`: sets the secret a human signs in with. An actor who sets it can become that human.
2. `token issue`: mints a bearer secret that acts as a user or agent; minting is acting as someone.
3. `agent assign`: carries `--confirm-permitted-use`, the human's own statement that the agent may act for them with their
   rights. That attestation is where the principal's responsibility is accepted; an agent cannot make it for them.
4. `agent authorize`: the same attestation, repeated after any suspension.
5. `permission activate`: names an installation's first administrators, which must exist before any level can be granted.

Outside the list and unchanged: the local bootstrap commands (`setup`, `init`, `owner-link`, `serve`, `company use`) are bound
to the operating-system login and no agent can reach them. `token revoke` stays open to an actor's own tokens.

## 3. Establishing an automation is a capability

An **automation** is a rule that writes with nobody asking at the time. Whoever holds the capability for its kind may make it live:

| Capability | Makes live |
|---|---|
| `automate.bank-rule` | a bank rule's `auto_add`, or an `ignore` rule (R108) |
| `automate.delivery` | a `send`-mode delivery schedule (R112) |
| `automate.invoice` | a recurring or criteria-based invoice schedule ("invoice every customer meeting X, Y and Z"; later) |

- **Anyone with the ordinary right to write the rule can draft it** (state `draft`, nothing runs). Making it `live` needs `automate.<kind>`.
- **A live automation runs under its establisher's authority.** `established_by` and `established_at` sit on the domain row. At each
  firing, Bookflow checks the automation is `live`, its establisher is active, and the establisher still holds the capability.
  Otherwise it is `paused` with the reason and one to-do is raised. This is R111's rule for memorized entries, applied once.
- **An operative edit by someone without the capability returns it to `draft`.** An edit by a holder keeps it live and makes the
  editor the establisher. Pausing is open to any editor of the rule; stopping is safe.
- **Its writes carry the rule and the establisher.** System writes read "On rule: <name>" with `established_by`, so `audit list`
  shows the chain: the actor who acted, the rule, who established it, and who they act for. Every effect lands in a batch
  ([bulk-import](bulk-import.md) §8a), so one reversal undoes a misread rule.
- **Built:** `automation list` and `automation show` (member), `automation pause` (editor of the rule). Each domain's own
  command does the establishing (`bank-rule update --live`, `delivery-schedule update --mode send`), checked against the capability.
  No shared table, fingerprints or taps.

## 4. Capabilities the consumers add

Named here so the three designs cite one list. Humans hold each at the role shown; agents hold none by default.

| Capability | Gives | Humans at |
|---|---|---|
| `delivery.send` | release a message to leave the machine, and approve another actor's release (R112) | standard |
| `delivery.mark-sent` | record that a document was sent outside Bookflow | standard |
| `delivery.connect` | write or check a delivery connection | admin |
| `bank-feed.connect` | write or check a bank-feed connection, enable a fetch, pass `confirm_account`, the first live backfill | admin |
| `import.approve` | approve an import or grid edit that waits for approval, `accept_differences`, `create_missing` past a look-alike (R107) | standard |

## 5. Directives and reasons: the record, not a gate

A directive and a reason say *why* work was done. They never decide *whether* it may be done; capabilities do.

- **Unchanged:** `directive add/list/show/deactivate`, `--directive` and `--reason` on writes, and `E_REASON_REQUIRED` for agent
  and system writes. A reason should name who asked and what ("Jordan: bill Maple St $1,200"); `--source-ref` carries the message or ticket id.
  No migration.
- **Dropped:** per-agent `reason`/`directive`/`blocked` policies and `agent policy`, `E_DIRECTIVE_REQUIRED`, `E_DIRECTIVE_UNCONFIRMED`,
  `uses_allowed` and one-off spending, `covers`, `given_by`, `automation_basis`, `automation establish`, fingerprints, taps, and nested mandates.
- **`directive confirmed_by` does not survive.** It existed only to gate; it was never built. A directive an agent records is its
  own account of the instruction, as a reason is. A principal who wants a rule on paper has a directive; one who wants a limit has a capability.

## 6. Setup guidance

Light and in words, so the principal frames an agent's mandate before it works. `bookflow setup` (R182) gains one step after the
agent is created; the agent guide gains the matching chapter ("Your level and how to ask for more"). Neither blocks anything.

- **Which level:** three presets, each saying what it cannot do. *Bookkeeper* (default): standard role, no automations, no
  sending, no merges or restores, no approving large imports. *Read-only*: reports and drafts. *Trusted*: Bookkeeper plus
  `delivery.send`, `import.approve` and the `automate.*` you pick. A preset is a list of grants the principal can edit.
- **What the agent may automate:** one line per `automate.*` capability, naming what runs unattended (adds bank lines, sends
  email, raises invoices) and that it runs as the establisher.
- **Every batch is reversible,** by one command; the guidance names it.
- **The trail shows the chain:** `audit list` gives actor, interface, principal and reason. "You are answerable for what you grant."
- **An agent refused for a missing capability** says which one, and "ask your principal to grant it if this is yours to do."

## 7. Increments

S 30-60 min, M 1.5-2.5 h. 1 lands before R108 increment 4, R112 increments 3 and 4 and R107 increment 4; 2 before the consumers' automation steps.

1. **Capabilities from the people-only list (M).** A new catalog layer clears `human_only` on the 33 entries and registers the §2
   capabilities at today's human thresholds; the ceiling rule; refusal text and details; `tests/test_people_only.py` rewritten. *Done when*
   the b-list is exactly the five commands; for each (a) entry an agent is refused with the capability named, succeeds once granted
   (and only if its principal also holds it), and a human at the old role is unchanged; a grant above the granter's own is refused;
   every human's effective permissions are identical before and after the layer, and no agent is suspended or token revoked by it.
2. **Automation (M).** `automate.*` capabilities, the `live` check at firing, establisher fields, draft-on-foreign-edit, `automation
   list/show/pause`, audit rendering. *Done when* a fake automation drafted by an agent runs nothing, goes live when a holder
   makes it so and writes citing the rule and establisher, pauses with a to-do when the establisher loses the capability or is
   deactivated, and returns to draft when a non-holder edits it.
3. **Consumers.** R108, R112 and R107 build to §3 and §4 as their designs say and add no authority mechanism. *Done when* each design's
   criterion for it passes.
4. **Guidance and close (S).** The setup step, agent guide chapter, README, `docs/`. *Done when* `docs check` finds 0, and a fresh agent told
   "auto-add Shell fuel to Fuel" under Bookkeeper drafts the rule and is told which capability to ask for; after the grant it goes live and the next import adds the line.

## 8. Open questions

Each default stands if nobody decides. Q1 is Kabe's.

- **Q1. Is agent default "none of the former people-only acts" right?** *Default:* yes; Bookkeeper preset in §6. The principal grants more.
- **Q2. One `automate` capability or one per kind?** *Default:* per kind (§3), so a principal can allow bank rules and not sending.
- **Q3. An agent in the browser.** *Default:* not built; the check is the same when it is.
- **Q4. R111 memorized entries.** *Default:* not retrofitted; they already run under their setter's authority.
- **Q5. Who may reverse a batch.** *Default:* a holder of the capability that made the writes, or any admin; [bulk-import](bulk-import.md) §8a settles it.
- **Q6. A non-human principal.** *Default:* not built. `_is_person` remains the one seam for the five (b) commands.
- **Q7. Renaming identifiers** (`people_only.py`, catalog keys). *Default:* never; words only.

## Addendum (2026-10-27): impact, not quantity

Kabe: "I really don't think the quantity of items is what identifies something sensitive or not. If the agent has the
permission to do so and the directive, it should do it ... generalize overall impact to simply behaviorally confirm
before damaging: 'just to be clear you want XYZ'." So no size thresholds anywhere (R107's are dropped). Commands whose
effect could damage carry a `confirm_first` mark in the registry (deletes, voids, reversals of batches, sends and
mark-sent, permission and capability changes, broad bulk edits); `help` and the MCP tool list show it. The agent guide
says: before running a confirm-first command, say in plain words what it will do and get the principal's yes in the
conversation, unless the principal's directive already names that act; use `--dry-run` to show exactly what would
change. Bookflow does not enforce the conversation; permissions and reversible batches remain the hard guarantees.

Kabe, same day: no "are you sure?" on every little thing. "Wonderful, post that invoice to John and send it to him" is
itself the confirmation: the principal named the act, so the agent posts and sends with no question. The agent asks
only when it would do a damaging act the principal did not plainly name: it is inferring the act ("tidy up the
customer list" read as deleting 40 customers), the act reaches wider than what was said, or the request is ambiguous
about which records. The confirm-first mark tells the agent which acts need that care; it is never a prompt by itself.

Kabe, sharpened: "We DON'T want over-paranoia on straightforward tasks. Pulling off a one-time change import to all
customers which impacts how we work forever deserves a double check our details are exactly as we want to live with."
So there are two cases. Straightforward work, even a send or a void the principal named, just runs. A broad change
that sets how the business works from then on (new terms or price levels on every customer, a chart restructure, a
bulk import that rewrites records) gets one double-check even when named: the agent shows the dry run's exact details
("212 customers, terms Net 30 to Net 15, from today") and runs it on the principal's yes. One check, of the details,
never a repeated "are you sure".

And ambiguity always gets a question, on any task: "When the guys are done send the invoice and take the payment over
email!" leaves out which job, which customer, the amount, when "done" is and how the payment is taken, so the agent
asks for those specifics and never fills them in by guessing. The agent guide says so plainly, with this example.

## For R187's build: wording found in R112's closing trial (2026-10-28)

- `delivery mark-sent`'s refusal names the capability `customer` (people_only) instead of `delivery.mark-sent`.
- `delivery-schedule live`'s help says "a person". Both hide that a principal can grant these; R187 increment 0 fixes
  the wording when it converts people-only acts to capabilities.

## As built: increment 1 (2026-10-28)

- **Layer `capabilities-v1`** on `bank-line-v1` (`hub/permission_capabilities_catalog.py`, 834 commands, descriptor
  `f639c12b`; re-chained after main brought `bank-feed-reverse-v1`, `import-documents-v1` and `bank-line-v1`, whose
  new commands, `bank-feed reverse` and `move` and `bank-line match`, `unmatch`, `ignore`, `add`, `transfer` and
  `undo`, were never people-only, so none was converted). It registers 18 capabilities: §2's 14 and the four R112 acts that were people-only (`delivery.send`,
  `delivery.mark-sent`, `delivery.connect`, `automate.delivery`), each with role defaults at the threshold that ran the
  command before (hub capabilities at `hub_admin`). It clears `human_only` on every admin action except the five
  `admin:people-only:` keys of §2 b, so `people_only_commands()` is exactly those five. No existing command, default,
  company action or threshold changes; only the authorization text of the commands that said "human".
- **The gate** is `hub/capabilities.py`, asked only of a non-human actor (a person's admission is untouched): permissions
  activated; an explicit grant of the capability on the agent's own live membership covering the scope (a role
  default never suffices for an agent) and no deny of it; then the usual actor/principal intersection through one
  declared `require_resource` site (`company/capability_gate.py`). Installation-wide capabilities need the agent's
  grant and an active hub-administrator principal who is not denied it. It runs at admission
  (`access.require_command_activation`), in `dispatch.authorize` once the company resolves, in the publication
  re-check, and in the domain checks that used to refuse non-persons (merges, restores, conversion, costing method,
  accountant, closing date, schedule live, backup schedule).
- **Ceilings (§1).** `hub/identity_admin.py` now admits an agent's own token for membership put/revoke, user active and
  assignment removal only, under `_agent_admission`: its grant and its principal's (the principal must administer the
  scope, as owner for owner changes); never its own membership or status or any of its principals' (whichever token
  it holds), never a hub administrator's (status or membership: no demotion, deny or revocation); no role above its
  own. Every membership change it makes is checked for widening: a capability it adds, a deny it removes, and every
  deny on a membership it revokes (revoking lifts them). Each widened capability needs the agent's own explicit grant
  and a principal who could give it: an active hub administrator for an installation-wide one, and never one the
  agent or the principal is denied at that scope. An organization-scope edit (a grant, role change, deny lift or
  revoke) reaches every company in the organization, so a deny of that capability on the agent's or the principal's
  live membership at any of those companies refuses it too (`identity_admin._carve_outs`).
  Activation, authorization and catalog replacement stay human at that layer too. A person who is not an installation
  administrator cannot give a hub capability. An agent's `user add` sets no password and never `hub_admin` (setting a
  password stays a person's act), and an agent's `agent create` is owned by its principal.
- **Demo reset** stays grantable (`demo.reset`). It refuses, for every actor and whatever `force` says, while the demo
  organization holds a company the demo did not create (`real_company_in_demo_organization`); `force` still covers
  real books in other organizations, which a reset never touches. No refusal message names a path: for a
  non-administrator, `dispatch.redact_error` now strips paths from the message as well as the details: any absolute
  path, and a details value under a `path` key only when it is shaped like a filesystem path (absolute, `~/`, a drive,
  or holding a separator), so an import's field path (`billing_address.town`) reads as written.
- **A person's deny of a capabilities-v1 act** is accepted, and `membership grant` says plainly that it binds only the
  agents acting for that person, who still holds it at their role.
- **Refusals.** An (a) act: `capability`, `reason: capability_not_granted` (or `principal_lacks_capability`,
  `activation_required`), the act and capability named and "ask your principal to grant it if this is yours to do".
  The five: `reason: people_only` (kept for existing clients) with `principal_only: true`.
- **R112 wording (the note above):** `delivery mark-sent` now names `delivery.mark-sent`; `delivery-schedule live`,
  `create`, `update` and `delivery approve` help name the capability instead of "a person".
- **Proved:** the policy engine, `bank-feed-v1` against `capabilities-v1` (measured before the re-chain;
  `test_people_only` now proves the same against `bank-line-v1`), gives 252,627 identical command x role x
  deny admissions for people (787 company commands; 4 roles plus installation administrators; 64 deny combinations
  over each command's own capabilities) and suspends none of the agents; a root activated at `bank-feed-v1` upgrades
  with no agent suspended, no token revoked, and the same answers for the agent and the person; over HTTP every one of
  the 41 commands (§2's 32 and R112's 9) and the closing date refuses an ungranted agent naming the capability, admits it once granted, and
  refuses it when its principal does not hold the capability, while a person is never asked.
- **Review fixes (2026-10-28):** every chain in notes/reviews/r187-inc1-review.md is a regression test over HTTP
  (`tests/test_capability_ceilings.py`): revoking to lift a deny, a hub capability the principal could not give, a hub
  administrator's membership, the other principal's membership, demo reset beside real books, and a person's deny.
  Round 2 (notes/reviews/r187-inc1-verify.md) adds an organization-wide grant stepping over the giver's company
  carve-out, and import messages garbled by redaction.
- **Left for later increments:** an agent holding `delivery.send` or `automate.delivery` passes the gate, but
  delivery's own go (`delivery_dispatch.go_lost`) still counts only a person's, so its approval gives no go and a
  schedule it tries to make live is refused for want of one: nothing leaves on an agent's say until the consumer step
  (increment 3) counts a holder's go. A person's deny of a new capability does not yet bind that person's own commands
  (it binds their agents); memorized entries still exclude every former people-only act; `agent show`/`list` stay
  installation-administrator reads.

## As built: increment 2 (automation) (2026-10-28)

- **Layer `automation-v1`** on `user-home-v1` (`hub/permission_automation_catalog.py`, 853 commands, descriptor
  `4df1e05f`; re-chained from `bulk-edit-v1` as main brought `bank-line-details-v1` and then `user-home-v1`). It registers `automate.bank-rule` and `automate.invoice` (held by a person at standard, as
  `automate.delivery` from increment 1 is; by an agent only by grant), and `automation list`, `show` (member) and
  `pause` (standard) under a new capability `automation` (member and standard, as list reads and writes). Each rule is
  then checked against its own kind's commands (`company/automation.require_command`: the kind's `show` to read it, its
  `update` to pause it), so `automation` grants nothing; `automation list` leaves out the kinds the caller cannot read.
  The capability gate's declared requirements grow by the two capabilities. No existing capability, default or
  threshold changes; no migration (company or hub).
- **One module, no shared table** (`company/automation.py`). A kind registers a `Kind` (where its rules live, how one is
  viewed and paused, and for a timed kind `due`, `act`, a once-per-slot `claim` and its failure count). The model is
  `require_establish` (making live), `on_edit`, `firing_check`, `source_ref`/`render` (the citation), and `fire`,
  `effect` and `tick` (timed kinds on the host's tick, after delivery schedules; a no-op while no kind is timed).
  `columns()` gives the standard columns (`state`, `established_by`, `established_for`, `established_epoch`,
  `established_at`, `paused_reason`, `paused_at`, `draft_reason`, `fired_slot`, `failed_firings`) and `table_kind` a
  `Kind` over a table carrying them.
- **Who holds `automate.<kind>`** (`automation.lost`, the establisher test asked at making live and at every firing):
  a person who is active, has a live membership covering the company at the threshold, is not denied the capability
  there, and is admitted by the activated policy; an agent that is active, unsuspended, still bound to the principal it
  established for (`established_for`) at the authority epoch it had then (`established_epoch`, recorded at
  establishment and compared at each firing, as R111 records `authority_epoch` for memorized entries: any suspension
  since, which moves the epoch, pauses the rule until it is made live again), explicitly granted the capability with
  no deny, admitted with that principal by the policy, and whose principal holds it as a person.
- **Authority that cannot be settled now never pauses a rule** (`automation.Unsettled`). A transient failure reading
  the policy skips the firing (a timed rule: `skipped`; a delivery run: nothing drafted this tick, the run goes on
  next time) and refuses a command with `E_DB_BUSY`. On a root whose stored catalog predates the capability (activated
  before capabilities-v1 or this layer), a person is decided by the membership test alone, as their role decided
  before, so no person gains or loses anything; an agent cannot make a rule live there (`reason:
  catalog_upgrade_required`, "upgrade it (permission activate)") and an existing agent-established rule is skipped,
  not paused.
- **Decided: a person's deny of `automate.<kind>` binds that person too.** Increment 1 made a person's deny of a
  capabilities-v1 act bind only their agents. Establishing is the exception: a principal who wants a standard person to
  stop running unattended email denies `automate.delivery`, and that person can no longer make one live, and their
  live schedules pause at the next firing. `membership grant` says so; it also says that a deny of `delivery.send`
  binds the person (`go_lost` withdraws their go), which increment 1's note had wrong.
- **Edits.** An operative edit of a live rule by a holder keeps it live under the editor; by anyone else it returns it
  to draft; a paused rule edited by a holder stays paused. A kind may need a holder to confirm the changed rule's
  check (`Kind.edit_needs_confirm`, `on_edit(confirmed=)`): delivery does (the fingerprint), and R108's auto-add can,
  for a broad lasting change. Resuming a stopped live delivery schedule is making it live: a holder with a go resumes
  it under them; anyone else's resume brings it back as a draft (its open run's go dropped). Pausing stays open to
  any editor.
- **Citing the rule.** An automation's writes carry `source_ref = automation:<kind>:<rule id>:<establisher>:<name>`
  (the rule's name when it wrote, percent-encoded; a rule acting on a line appends `;<inner ref>`, for R108's
  `bank-line:<id>`). Decided: the name at write time is in the citation, so a later rename does not rewrite past
  events. `audit list`, `tail` and `show` render it as `on_rule` (kind, rule id, name, establisher and their name,
  `text`: "On rule: Daily invoices, established by clerk") only for a system-interface event whose actor is the cited
  establisher; any other interface passing a source reference with the `automation:` prefix is refused
  (`dispatch.validate_context`, `E_VALIDATION` on `source_ref`). No audit schema change.
- **`would_pause`** (on `automation list` and `show`) names another user's missing authority only to that user, an
  installation administrator, or a holder of `membership.grant`; any other reader sees "its establisher no longer
  holds the authority".
- **Batches.** The `automation` batch adapter is registered when the module loads. A timed rule's first effect opens
  the firing's batch inside its own transaction (a firing that writes nothing leaves no batch), and `automation.effect`
  records each write in it in the same transaction: a created record as such; an updated one only with its prior
  values (`prior=`, the update command's input shape), else the write is refused. `batches.reverse` undoes a firing as
  one unit under the caller's own grants (§8 Q5's default). Each slot fires once (`claim`, a conditional update of
  `fired_slot`). An effect that fails 3 firings in a row (`FAILURES_BEFORE_PAUSE`) pauses the rule with a to-do; a
  success resets the count; a refusal for authority pauses at once. A delivery run drafts in its own delivery batch,
  which `delivery batch-cancel` undoes before anything leaves (a sent email cannot be recalled). R108's auto-add
  records into its import's batch (bank-feeds.md §5). No user-facing `reverse` for an `automation` batch yet: the first
  timed consumer (invoice schedules) adds its own, as `import reverse` and `bulk-edit reverse` are.
- **Delivery schedules are the first kind** (`delivery_schedules.KIND`: `delivery`, `automate.delivery`). Only
  send-mode schedules are automations; a draft-mode schedule drafts under its `run_as`, as R111's memorized entries do
  (§8 Q4: not retrofitted). A draft or paused send schedule still drafts and releases messages that wait for a go: it
  sends nothing. `established_for`/`established_epoch` are the schedule's `run_as_for`/`run_as_epoch` when it was made
  live by its `run_as`.
  - Making live: `delivery-schedule live`, and now `delivery-schedule update --mode send --confirm <fingerprint>` on a
    draft or paused send schedule (its dry run shows the check to a holder). Both ask `require_establish` (an agent's
    refusal names `automate.delivery` and its principal; a person denied it gets `reason: capability_not_held`), then
    the go check (`go_lost`, still a person's until increment 3: an agent granted `automate.delivery` is refused with
    `no_send_authority`), and keep the one check of the exact details.
  - At each firing `_go` asks `firing_check` and the go check before a run's first chunk, and `_keep_go` asks both
    again before every later chunk of a run that carries a go: a deny, a demotion or a deactivation mid-run pauses the
    schedule with one to-do and drops the batch's go, so the next chunk's releases wait. A live schedule's tick run
    carries the rule's `source_ref`.
  - `automation pause` sets `state = paused` ("paused by <who>: <reason>"), raises one to-do for the establisher and
    drops the go of an open run's releases. `delivery-schedule pause` (stop: nothing runs) is unchanged; a paused
    schedule comes back through `live`, `update --mode send --confirm`, or `resume` (as a draft).
- **Seam for R108 increment 4 (not built here):** the bank rule table takes `automation.columns()`, registers a
  `table_kind(kind='bank-rule', capability='automate.bank-rule', edit_needs_confirm=..., ...)` and adds its read and
  update requirements in its own layer (the `automation.require_command` source); `bank-rule update --live` calls
  `require_establish` and stores `establisher(s)`; `bank-rule update` calls `on_edit`; each import asks
  `firing_check` per live rule (skipping on `Unsettled`), pauses with `Kind.pause` otherwise, and cites
  `source_ref(..., inner=f'bank-line:{line}')` on each entry, in the import's batch.
- **§8 as it bears here:** Q2 per kind (three capabilities); Q4 memorized entries not retrofitted; Q5 a batch is
  reversed under the reverser's own grants; Q6 unchanged.
- **Proved** (`tests/test_automation.py`): a test-only timed kind (its own table, `automate.invoice`, due every day in
  any state) drafted by an agent is refused by `fire` itself and opens no batch; the agent cannot make it live
  (refused naming `automate.invoice` and its principal); granted, it makes it live (its epoch recorded) and the next
  tick creates a customer as the agent for its principal, the same slot never fires twice, the audit event cites the
  rule and the agent, the customer is in the rule's batch, and one reversal takes it back; a forged citation over HTTP
  is refused and one written anyway does not render; the grant withdrawn, `automation show` names why, the tick pauses
  it with one to-do and later ticks raise no second; an agent's operative edit without the grant, or a holder's
  unconfirmed edit where the kind needs a check, returns it to draft, a holder's keeps it live under them;
  `automation pause` by an editor pauses it with a to-do; the agent deactivated, the tick pauses it with a to-do; an
  effect failing three firings in a row pauses it with a to-do and leaves no batch. A real delivery schedule: an agent
  drafts it and is refused making it live; its draft run gives no go; a person denied `automate.delivery` is refused
  naming it; an agent granted it is refused by the go check; the clerk makes it live by `update`; its run carries the
  clerk's go and its audit renders "On rule: Daily invoices, established by clerk"; the agent's edit returns it to
  draft; a person who may edit it but not make it live pauses it with one to-do; paused to live through `update`; a
  read-only member sees only that it would pause; the clerk denied, the next firing pauses it with a second to-do; a
  non-holder's resume of a stopped live schedule makes it a draft. Mid-run (one message a chunk): `automation pause`
  and then a deny each stop the go of the next chunk, pause the schedule with one to-do and nothing more leaves. A root
  activated at `bank-feed-reverse-v1`: an owner makes a schedule live and the tick leaves it live.
  `tests/test_automation_surfaces.py` runs list, show and pause through Python, the CLI, HTTP and MCP.
