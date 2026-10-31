# Reversible batches (R186)

Kabe, 2026-10-27: "Maintaining this reversability for all large batch imports or modifications is quite helpful.
Consider customer batch changes that have unexpected results or are incorrectly described from the user originally."
The rule it set: a batch is reversed as one unit, newest first, for as long as nothing later depends on it, and a
refusal names what blocks it (colony rule ue550cb).

R107 and R108 built the shared ledger (`company/batches.py`: `batches`, `batch_effects`, adapters, planned and sliced
reversal; [bulk-import](bulk-import.md) §8a). Imports, bulk edit, openings, bank feeds and bank sorting already use it.
R186 brings the older batch operations onto it. The survey behind this design (every command that writes many records
in one call or run, with file and line) was taken on main 593933bd; its findings are summarised here.

## 1. What a batch is, and the one door

- **A batch is what one call or one run wrote**, recorded inside each write's own transaction (`batches.record_effect`),
  so an interrupted run is still a complete record of what landed. Recording after a run finishes is not allowed: it
  misses interrupted runs.
- **One door: `batch reverse <batch>`** (with `--dry-run`), beside the existing `batch show/query`. It plans the whole
  unit first (later batches, later edits, references, each inverse's own dry-run), refuses naming every blocker, and runs
  in slices that resume. Domain doors that already exist (`import reverse`, `bulk-edit reverse`, `bank-feed reverse`,
  `opening-draft reverse`) stay and call the same engine.
- **Authority:** reversing needs the reverser's own grant for every inverse command it runs (a batch reversal grants
  nothing). An agent's reversal is a damaging act the principal did not plainly name unless they did; the agent guide
  teaches one check of the dry run's list before it runs.
- **A reversal registry independent of importability.** Today the document inverses come only from `import_kinds`.
  R186 adds a registry of inverse steps per record family (delete, void, unapply-then-void, restore prior fields,
  delegate to a native aggregate inverse), so statement charges, deposits, counts and the like can be reversed without
  being importable.

## 2. What cannot be undone, said plainly

- **Mail already sent, cheques already printed, files already exported** cannot be recalled. A batch holding any of
  them is not reversed as a whole: the refusal lists them. What can still be cancelled (drafts, pending runs) is
  cancelled by the operation's own cancel command, which stays.
- **Partial inverses are not reversal.** An existing cancel or unmerge that keeps some effects (a delivery batch-cancel
  that keeps sent messages; an account unmerge that keeps changed references) is never reported as a complete reversal:
  the batch plan refuses before it changes anything.
- **History stays.** Reversal deletes or voids what the batch made and restores what it changed; audit events,
  depreciation links, assessment bases and similar history remain.

## 3. Adopters, in build order

Order is by regret and by how clean the inverse is (usage was not measured).

| Inc | Operations | Inverse |
|---|---|---|
| 1 | The door and registry; **batch invoicing** (post, retry: each run its own batch), **bill pay** (created payments: unapply then void; pending print blocks), **fixed-asset depreciate** (journals), **finance-charge assess** (statement charges, new void mapping; a Finance Charge item it created is retired after) | Existing document inverses |
| 2 | **barcode generate**, **cycle-count classify** (item before-images; seeded classes retired), **bank-rule apply** (prior proposals and rule ids on waiting lines), **notice generate** (dismiss ready notices; the period's dedupe is released so a corrected run can regenerate) | Field before-images and small adapters |
| 3 | **Memorized transactions and groups** (`memorized process`, `memorized-group enter`, `memorized enter/retry`, scheduler runs): the run's child documents and its occurrence, hold and next-date state are one unit; reversal deletes or voids the documents and restores the occurrences and schedule, refused once a later run of the same schedule exists | New run adapter |
| 4 | **Merges** (customer, vendor, account, item): the batch reverses through the existing unmerge, and is refused before any change when unmerge would keep anything (changed or deleted references, a barcode collision, later topology changes) | Existing unmerge, strict |
| 5 | **Inventory counts** (count void or cancel), **pending check-print runs** (cancel while nothing printed), **delivery batches** (only while every message is still a draft) | Native aggregate inverses with strict preflight |

**Considered, not in R186** (each needs its own restore semantics and is rarely a regretted bulk act): timesheet saves
and clock-outs, raw `cutover apply` (openings already reverse through `opening-draft`), accountant-copy import,
reconciliation drafts and certificates, costing-method switches, chart and profile application, budgets, POS layout
edits, hierarchy cascades (eligible events already have `undo`). Single aggregates (a deposit, a payment applied across
invoices, a landed cost) keep their native void.

## 4. Done when

Each increment: every listed operation records its batch inside its own writes; `batch reverse --dry-run` lists
exactly what would change and every blocker; a reversal leaves the books equal to before the batch (balances and open
amounts worked by hand), or is refused naming the blocker with nothing changed; an interrupted reversal resumes; the
agent guide and `batch reverse` help say what cannot be undone. The R186 closing trial: a fresh agent over MCP, told
"that batch of invoices went to the wrong customers" and "undo last month's finance charges", reverses each correctly
after one check, and refuses plainly when a payment has since been applied.

## As built: increment 1

- `batch reverse <batch> [--dry-run]` is the company-wide door over the existing sliced reversal engine. It returns
  the complete remaining effect list, a fingerprint and the next command. It preflights the whole unit before any
  reversal commits, names blockers, and resumes from committed inverse effects. A live batch with nothing left to
  reverse is refused explicitly. Reversal batches cannot themselves be reversed.
- `company/reversal_registry.py` owns document inverse declarations and list restore/retire steps independently of
  import kinds. Existing import declarations remain compatible. Statement charges use `statement-charge void`;
  bill payments unapply before voiding. Batch adapters retain the native aggregate planner/runner extension point.
  A fresh process lazily loads the adapter for the batch it reads or reverses. Native import, bank, opening and
  bulk-edit doors retain their aggregate grants and run lifecycle through registry delegation.
- Every remaining inverse is dry-run under the caller's authority on every call, including resumed slices and calls
  carrying a fingerprint. The outer command requires only the `import` member capability used by batch show/query.
  `reversible-batch-v1` is a new permission layer on `pos-layout-v1`; accepted layers are unchanged. Help and the agent
  guide mark reversal confirm-first unless the principal plainly named the act, and explain irreversible outputs.
- Invoice post and retry each create a separate shared batch linked by `source_type=invoice_batch` and `source_id`
  to their invoice run. Each successful invoice's effect is inside `_write_one`'s savepoint. The existing output
  `batch_id` still names the invoice run; `reversal_batch_id` names the shared batch. Show/query expose that link too.
- Bill pay records each created payment inside its payment transaction and returns `batch_id`. Pending print runs,
  already printed checks and finished reconciliations block reversal with their identities. Later settlement edits
  also block. Void checks the closed period even while applications are still standing, before any unapply runs.
- Depreciation and finance-charge assessment insert their batch and effects inside their existing command transaction.
  Their output `batch` identifies it. Neither has a separate native run table; their `source_type` names the command.
  Depreciation links and assessment bases remain history. A live disposal blocks reversal of its depreciation
  journals; voided later assessments do not block retirement of the shared Finance Charge item. An assessment-created Finance Charge item is recorded
  before its charges and renamed/deactivated only after they have been voided. A pre-existing item is retained.
- Reversible choices: depreciation and assessments use strict company-wide scopes per operation, so later runs unwind
  first. Independent invoice and bill-payment batches use record dependencies. Native adopter reversals retain document
  numbers; import reversals retain their existing number-release behavior for corrected files. No-op previews/runs
  for depreciation and charges return no batch. No migration is needed: the existing ledger holds all new effects.
- Tests cover hand-worked balances, open amounts and unchanged inventory; exact plans including more than fifty
  invoices; payment, reconciliation, print, later-run and closed-period blockers; missing inverse grants; interrupted
  and sliced reversal; and rollback when effect recording fails. The generic door also has a four-adapter witness.

The demo previews and reverses a temporary customer-note batch edit, restoring the original note and leaving balances unchanged.
