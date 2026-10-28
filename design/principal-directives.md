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
