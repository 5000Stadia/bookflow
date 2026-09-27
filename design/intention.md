# Bookflow — intention

## What we're making

A multi-company double-entry accounting system for small businesses, with a browser UI as the primary human surface and one accounting core exposed through Python, the CLI, HTTP, and MCP so every interface posts to the same books. In the human's words: "a CLI interface and package with a clear set of functions an AI could easily use", so that "a plumber with very little accounting knowledge knows to just send everything to their agent to post" and "next time they pull up their Bookflow gui they see that the entry they asked their agent to post is posted."

## What "good" means here

Picture the plumber at 9pm asking their agent to post today's receipts, then opening the GUI the next morning. Every line below is what has to be true for that person.

- An agent that has never seen Bookflow can read the docs and post a correct transaction on its first attempt, with no source reading and no guessing at syntax.
- A fresh agent completes ordinary business directives through the actual MCP interface using its discoverable help, without an implementation briefing. Observed task results and a follow-up usability interview identify unclear commands, missing information and unnecessary clarification; material findings are corrected and retested.
- Every command works identically from the CLI, the MCP adapter, the HTTP host, and the browser workbench, with the same inputs, outputs, and errors.
- Agent and human workflows cooperate: each can inspect and continue the other's work through shared business identities and state. Agents discover typed commands, preview changes and retry safely without reading source or automating browser clicks; users see the resulting records and attributed activity in the GUI. Representative cross-interface workflows are part of completion, with conventional command names and actionable structured errors.
- Every write records who did it, through which interface, on whose behalf, and why. The owner can list what their agent did this week from any surface.
- Two writers never silently destroy each other's changes. A rejected write says who changed the record, how long ago, and which fields.
- The books always balance. No command can post an unbalanced entry or post into a closed period.
- A user or agent with access to one organization cannot see that another organization or its companies exist.
- Money is exact. No amount is ever a float.
- A company file can be copied to another machine and opened there with nothing lost.
- Every field, option, and behavior the anchor offers on the equivalent list, form, or report exists here. Put side by side, nothing is missing.

**Anchor:** The desktop accounting application the human runs daily; the human names it and runs the comparison. The bar is its list design, its transaction forms, and its reports: what it lets a bookkeeper do without thinking about debits and credits, and what its audit trail and closing date protect.

## What everything passes through

- **Declarative ground truth.** Every document in this repo is written for an agent with no prior understanding of the project. It states what exists, what each command takes and returns, and what each table holds. It never explains philosophy, history, or reasoning. Enforced: schema and command references are generated from code and a test fails when they are stale.
- **One command contract.** Every operation is a named command with a typed input, a typed output, a stable error code, and a JSON form. Adapters translate; they never contain logic.
- **Conventional bookkeeping vocabulary.** Lists, items, jobs, classes, terms, memorized transactions. When Bookflow names a thing, it uses the name a bookkeeper already knows.
- **Integer minor units plus currency code** for every amount, everywhere.
- **The demo company exercises everything.** A change that adds a table or command extends the demo seed in the same change, and the demo is what testing runs against.

## What it must never do

- Never erase transaction history or audit rows. User-facing transaction deletion is permitted separately from voiding, subject to explicit per-user deletion permission configured in user setup; preserve the original facts and an attributed deletion record, with balanced cancellation of accounting effects.
- Never post an entry whose debits and credits differ.
- Never post into a period closed by the company's closing date.
- Never return, list, or reveal an organization or company the acting principal has no membership in.
- Never open a company database over a network file share. Multi-machine access goes through the host process.
- Never store an amount as a floating-point number.
- Never send data outside the machine. No telemetry, no rate fetching, no email, unless a command explicitly does so and the docs say it does.

## Where it goes

Public GitHub repository `5000Stadia/bookflow`, branch `main`. `design/` travels with the code. `notes/` never does. Pushing is standing authority at milestones that are implemented and tested: a passed spec row, or a design change the human has agreed to.

Irreversible acts registry: pushing to `main` (standing, at tested milestones), force-pushing or rewriting published history (asked), deleting any company data directory (asked), spending money (none authorized).

## The spec list

| # | What to build now | What done looks like |
|---|---|---|
| 7 | Identity and isolation. `user add`, `membership grant` and `revoke`, hashed revocable agent tokens bound to a human principal and authority epoch, memberships with roles (owner, admin, standard, readonly), current permission checks on every command, company listing limited to memberships, workbench buttons limited by role. Assigned shared-agent principals have equal effective permissions; authorized access reductions suspend affected agent authority and revoke its tokens atomically without blocking the human change. | An agent token with membership in organization A cannot list, open, or infer organization B or its companies; a token with membership in one company cannot see its siblings. Readonly members cannot write on any surface. Revocation succeeds even when it breaks shared-principal equality; stale queued work and streams cannot publish through revoked authority, and restoring membership cannot revive old tokens. Tests cover these paths and explicit reauthorization. |
| 22 | Customer payments and invoice settlement. Receive new cash across compatible parent/job invoices, retain explicitly owned unapplied credit, apply/unapply, correct, void and inspect immutable settlement history through shared commands and browser workflows. | Exact receipt and allocation effects reconcile to the ledger; balances, original/current facts, dated history, multi-document versions, permanent retries and permissions remain correct through corrections and concurrency. Desktop/phone and agent/human continuation journeys pass. Complete plan: design/specs/22-customer-payments.md. |
| 24 | Captured sales-tax policies. User: "Were setup to adhere to this kind of detail right?" — exact fractional-cent intermediates, half-cent-up rounding and line versus invoice calculation. Add explicit combined-rate calculation policies and preserve existing historical rules across all implemented sales/work paths. | Company and document policy choices, exact reconciled tax attribution, history-preserving migration, corrections/reversals, progress billing and payment settlement work through every surface, with independent arithmetic, migration and browser/actual-MCP evidence. Complete plan: design/specs/24-sales-tax-policy.md. |


**Next ID:** 25
