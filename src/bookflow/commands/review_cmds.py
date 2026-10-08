"""`review mark`: the owner says an entry on the entries-to-review list has been looked at.

The mark is an audit event and nothing else. The entry is not edited and its version does not
move; the event names the revision and the flags the owner saw (`company/entry_review.py`), so
a later correction or a new flag puts the entry back on the list. Marking is a person's step:
an agent that could clear the list it is checked against would make the list meaningless.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.context import Context
from bookflow.core.errors import BookflowError
from bookflow.core.registry import Applied, Plan, Touched, command


class ReviewMarkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction: str = Field(min_length=1, max_length=26, description="Transaction id of an entry on the entries-to-review list.")
    note: str | None = Field(None, max_length=1000, description="What the owner found, kept with the mark.")


class ReviewMarkOutput(BaseModel):
    transaction_id: str
    revision_id: str
    flags: list[str]
    note: str | None
    changed: bool


review_mark = command("review mark", scope="company",
    description="Mark one entry on the entries-to-review list (`report entries-to-review`) as reviewed by the owner. Records an audit event naming the entry's current revision and the flags it carries; the entry itself is not changed. It leaves the list until it is corrected or gains a new flag. A person's step: an agent is refused.",
    input_model=ReviewMarkInput, output_model=ReviewMarkOutput, writes={"company"}, required_role="admin",
    capability="company", accepts_idempotency_key=True, positional=["transaction"],
    error_codes=["E_RECORD_NOT_FOUND", "E_PERMISSION", "E_VALIDATION"])


def _current(s, transaction):
    from bookflow.company import entry_review as review
    from bookflow.company import records
    identity = records.resolve(s, "transaction", transaction)
    flags = review.flagged(s, transactions=[identity]).get(identity, {})
    doc = review.documents(s, [identity])[identity]
    return identity, doc, [f for f in review.FLAGS if f in flags], review.reviews(s, [identity]).get(identity)


@review_mark
def plan_review_mark(inp: ReviewMarkInput, ctx: Context, s) -> Plan:
    from bookflow.company import entry_review as review
    if s.actor is not None and s.actor.kind != "human":
        raise BookflowError("E_PERMISSION", message="Marking an entry reviewed is the owner's own step; an agent cannot mark entries reviewed.",
                            details={"reason": "person_only", "next": "Tell the owner what you found; they mark it."})
    identity, doc, flags, mark = _current(s, inp.transaction)
    if not flags:
        raise BookflowError("E_VALIDATION", message="This entry is not on the entries-to-review list.",
                            details={"fields": [{"field": "transaction", "problem": "not on the entries-to-review list"}]})
    changed = not review.covered(mark, doc["revision_id"], flags) or (inp.note or None) != (mark or {}).get("note")
    return Plan(ReviewMarkOutput(transaction_id=identity, revision_id=doc["revision_id"], flags=flags,
                                 note=inp.note, changed=changed),
                {"identity": identity, "doc": doc, "flags": flags, "changed": changed})


@review_mark.applier
def apply_review_mark(plan: Plan, ctx: Context, s) -> Applied:
    from bookflow.company import entry_review as review
    d = plan.data
    if not d["changed"]:
        return Applied(plan.preview, [], "no change")
    snapshot = {"transaction_id": d["identity"], "revision_id": d["doc"]["revision_id"],
                "flags": d["flags"], "note": plan.preview.note}
    return Applied(plan.preview, [Touched(review.REVIEW_RECORD, d["identity"], "review", None, None, snapshot, db="company")],
                   f"marked {d['doc']['type'].replace('_', ' ')} {d['doc']['number']} reviewed")
