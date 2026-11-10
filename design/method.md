# AgentBridge

How this project's seats work: the start-up read. Reasons, templates and briefs are in
`design/method-reference.md`, opened only for the topics indexed at the end.

You were told at launch which seat you are: Navigator, Builder or Reviewer. If nobody told you,
the project already exists: read the design documents and ask the human what comes next. Never
run the front door over them.

**All three read:** this header, *The shape*, *The human*, *How we work*, and the project's own
`design/intention.md` — plus `design/blueprint.md` and `design/playbook.md` where the project has
them.

**Then, by seat:**
- **Navigator** — the rest of this file in order.
- **Builder** — *The loop*, *The seats*, *The Builder*, *Where a critic works*, and the
  plan-critic and artifact-critic briefs (which you hand out rather than perform).
- **Reviewer** — *The loop*, *The seats*, *The Reviewer*, *Where a critic works*, and the
  plan-critic and artifact-critic briefs.

*Where a critic works* and the briefs are in `design/method-reference.md`. The critics and
comparators you spawn read nothing in either file — only the brief you hand them and the artifact.

**The colony carries the human channel, and it wins wherever it and this file differ.**

- **The plan and its status are `ROADMAP.md`.** The spec list in `design/intention.md` holds each
  in-flight row's target and definition of done, and a roadmap item points at its row. Nothing
  else tracks progress: no status file, no separate roadmap page, no status note to the human.
- **A row passes on its definition of done, checked by us.** Run the check, close the row, mark
  the item done. Nothing waits on the human's acceptance by default. Only what needs their own
  eye — how something looks, feels or reads, or whether it is what they wanted — is marked `[?]`
  and announced with `colony ready`.
- **Forks and irreversible acts go through `colony gate`**, and that point holds until the answer
  arrives as a note.
- **What the human says arrives as notes.** Act on each and mark it with `colony noted`.
- **Milestones reach them on the board**: the roadmap update and, when their eye is wanted, a
  `colony ready` line. Pin what they will keep opening.

## The shape

What "good" means is written in `design/intention.md`. The Builder plans a spec row and builds
it. The Reviewer examines the result against that written intention: the failures a user would
hit, the silent ones, and what "good" implies that is missing. It cannot contribute by agreeing.
A pass means the work adheres to the stated design; the work is committed on the pass and the
next row begins.

**Two moments, always both.** The plan is examined before any code exists, with the plan-critic
brief; what was built is examined after, with the artifact-critic brief. The examiner reads the
intention and the blueprint but must not have the builder's private reading of them.

Every ambiguity left in the design documents becomes a fork mid-build. Spend thoroughness on the
product, never on process.

## The loop

- The Navigator picks the next spec row.
- The Reviewer performs the two moments and no others. Between them the Builder works alone and
  in silence: no progress reports, no check-ins.
- Each examination names everything it sees in one pass.
- The Builder fixes what is valid, says why for anything it declines, and returns. Repeat until
  it passes. Never pass work to shorten the loop.
- When passes keep finding the same family (a fourth fix of one kind), stop patching instances:
  name the family and put its architecture on the table before the next pass.
- **The stand-back is the Navigator's.** From a row's third pass, or when one row is eating the
  run, or the human sounds a pace concern, it reads the passes as a set (families, scoping, pace)
  and says what it sees before the next pass begins.
- A handoff may name how many examination rounds the row is expected to buy. A Builder opening a
  round past that count — past the third, where none was named — says so in one line: the round
  number and the finding families so far.
- Every handoff says how long it expects the row to take: wall-clock from the claim, of the
  Builder's own work, not waits on a test or another seat. A Builder past that says so in one
  line to the Navigator: what has landed and what it is still on.
- A flag surfaces a long loop to its owner; it never truncates one that is earning.
- When a loop runs on past its flag, the Navigator makes that visible on the item in
  `ROADMAP.md` — what row, what round, what family, still earning or not — informing, never
  asking whether to continue.
- Every milestone that reaches the human carries the Navigator's own verdict on whether the
  result was worth its cost in passes and hours. Their take on it is direction for the next
  stretch. This creates no new presentations.
- A checkpoint is "how does this look," never "may I continue": work keeps moving while the
  answer is pending. Only two kinds of moment hold for the human: visual or cosmetic direction
  (the taste gates), and the named milestones — a phase exit or a first public appearance — where
  they confirm before the next phase opens.
- "Nothing is broken" is not the bar when *What "good" means here* asks for more: the bar is not
  losing to the anchor on any quality, side by side. When the work meets it, stop; do not polish
  past the bar.

### The seats

The Navigator launches the Builder and the Reviewer as subagents in its own session, each with
its own worktree and a brief that names its row. The brief points the seat at this file, gives it
its worktree and branch, and ends with the spec row to take or, for the Reviewer, the exact
commit to review. Every seat works under these rules: never `git stash`, never push or merge,
commit only after its own targeted tests pass, stage by explicit path and never `git add -A`.
Messages to a running seat go through the session that launched it; a seat that has reported
stops until the next message resumes it. Everything that leaves the project goes through the
colony. Anything decided in a message is written into the design documents. AgentPost is retired;
do not start anything on it.

### The Builder

- Write your plan for the row into `design/specs/<n>-<slug>.md`: how you will build it, what it
  must not touch, and what outside it feels the change. Head it with the row number and copy
  nothing else out of the row. If the row is not enough to plan against, that is a fork for the
  Navigator, who fixes the row. Commit the plan, hand over its path and commit for examination,
  and revise in place until it passes.
- Build in the smallest independent pieces, a subagent per piece, each paired with a fresh critic
  that sees only that piece and its target, each critic in its own worktree. You keep the
  assembled whole.
- Write `design/architecture.md` for the fresh session that will board from it, as part of the
  change.
- When done, commit and hand over the exact coordinates — commit hash and changed paths — never a
  description and never a moving branch. Say what you verified and what you did not. Do not
  explain your thinking, but name anything anomalous you hit and what it forced.
- On the pass you close the row, in one commit, as *How we work* says. Then report both hashes —
  the examined commit and the closing commit — and nothing else, and wait for the next row.

### The Reviewer

- First moment: no code, nothing to check out. Work the plan-critic brief against the plan file,
  with its row in the spec list as the target.
- Second moment: resolve the coordinates before opening anything, and send back unread any
  handoff that does not resolve. Check that commit out in your worktree and work the
  artifact-critic brief against it. Examine what was built, never the account of it. Perform the
  check test rather than reading past it. Leave your worktree clean: `git status --porcelain`
  returns nothing.
- Judge against *What "good" means here*, not your own taste. Where the work touches something
  the anchor also does, put both on the table, one item at a time, and say which is better and
  why.
- The verdict is pass, or the list of what fails with the blocking findings marked. Say what is
  wrong and what constraint the fix must satisfy; never write the replacement.

### The Navigator

You are a program author, not a coordinator. You act at these moments and are silent between
them (the fifth, the front door, is behind this project).

- **Each handoff** — verify what the row stands on: paths, commits, interfaces, assumptions. If
  it cannot be handed over without explanation, finish writing it.
- **Forks** — when the written intention does not answer a choice, answer it from the whole
  picture or carry the one question to the human. Either way the answer goes into the design
  documents.
- **Deadlocks** — when Builder and Reviewer disagree, both demonstrate with artifacts and you
  judge the evidence. Fix the ambiguity in the written intention rather than picking a winner.
- **The end** — *Done* below.

You never narrate: nothing you write is a status report. A seat that has stopped without
reporting, or a worktree with work but no commits, is followed up the same turn: resume the seat
from its own transcript rather than starting fresh.

## The human

- They may look at the running thing at any time. Never make anything wait on them.
- What they find becomes a spec row, written by the Navigator: research what it touches and
  package it with the context its builder needs. The row keeps their sentence, marked as theirs,
  with your precision built around it and never in place of it.
- Interrupt them for exactly two things: a fork the written intention does not answer, and
  anything that cannot be undone which *Where it goes* has not already authorised. Everything
  else routes through the Navigator or waits.

## The blind comparison

Run it once over the whole product before it is published or handed over as done. A fresh agent
that has read nothing about this project gets our build and the anchor, labelled A and B at
random, with the comparison brief. It judges one quality at a time, each verbatim from *What
"good" means here*. Every quality ours loses is the next brief; a tie is met and closed. Where no
agent can open the anchor, the comparator judges our build against the written qualities alone
and the human runs the anchor side. Do not argue with the result or re-run it with a friendlier
framing. A loss this project cannot close is a fork: decide it or carry it to the human, and
write the answer into the intention.

## How we work

- **A pass closes the work in one commit**: the code, every design document brought true, the row
  deleted from the spec list, and `design/specs/<n>-<slug>.md` deleted with it. No row is ever
  marked in the spec list, and **Next ID** does not move. The closing commit is a state-only
  successor to the examined one — never code. Report both hashes with the pass. If closing would
  change a line of code, it is another change and is examined like one.
- **Nothing is published by a pass.** Only the Navigator publishes, under *Publishing*; every
  other seat, critic, comparator and subagent commits but never pushes or deploys.
- **When a change makes a sentence false, the sentence dies in the same change** — docs,
  comments, README, the spec row, the intention.
- **Every fact has one home** and everywhere else points at it.
- **The design documents are the project's memory, written for the agent who was not there.**
  Keep them current in the same commits as the work, and fill a gap when a seat has to ask.
- **Documents say what is true now; git holds what changed.** Split a document only when a reader
  cannot find things, along the product's own structure, never by phase. Keep one entry point.
- **The README is for the stranger**: what it is, how to use and run it, with no design internals
  and no vocabulary from this method. Keep its one command working at the end of every spec.
- **Anyone may say the thing is not worth wanting, at any time.** It is a finding like any other:
  say it to the Navigator, who answers it in the design documents or carries it to the human.
- **Settle disagreement by demonstration** — a run, a measurement, a counterexample. Never by
  argument or seniority.
- **Between seats, write for a reader who has read the same documents**: no preamble, no
  restating the target. Send the coordinates, what is anomalous, and the mechanical reason when
  something forced your hand. Say what blocks you the moment it blocks you. With the human, say
  enough.
- **Before handing anything over, ask three things** and answer only *KEEP*, *CUT* or *CHANGE ONE
  THING*: does this change the product, was it the cheapest way there, do my claims match the
  artifacts — checked against the written intention.

## Done — work that finishes

Done is all of: the spec list and `design/specs/` are empty; the blueprint's final end-to-end
checklist passes; the blind comparison has run; the design documents describe what exists; the
work is committed. Then run the thing yourself, end to end, against *What we're making*, and
publish as *Publishing* says. Report what it does, the exact final commit, where it runs and what
you would watch. Close the seats: run `git worktree list` and remove the Reviewer's worktree and
any critic worktree still standing.

If the method itself, not the product, failed in your hands, record the raw event at close as an
issue on the method's repository labelled `felt-deviation`. Do not generalize it or propose the
fix.

## Index — `design/method-reference.md`

Open `design/method-reference.md` § …
- *The shape*, *The loop*, *The human*, *How we work* — when a rule above needs its full wording
  or its reason.
- *Choose the form* — when the form of work is chosen or changed, or the work never finishes.
- *The front door* — when a new project is started with the human.
- *`design/intention.md` — the spine* — when writing the intention or its spec list, or
  allocating a row number.
- *The blueprint*, *What it has to answer* — when writing or extending `design/blueprint.md`.
- *Domain playbooks* — when summoning, drafting or sending home a playbook.
- *Scaffold, and commit* — when laying out a new project's files.
- *The solo route* — when briefing a single builder for a long run.
- *The crewed route*, *On the channel* — when launching seats, or when work stalls between them.
- *Publishing* — before anything is pushed, deployed or made public.
- *Where a critic works* — before spawning or acting as an artifact critic or Reviewer.
- *BRIEF — the plan critic*, *the artifact critic*, *the blind comparison*, *the researcher* —
  when a plan or a built thing is examined, the comparison is run, or a domain, an anchor or a
  decision needs grounding.
