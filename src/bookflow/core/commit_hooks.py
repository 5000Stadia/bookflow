"""Actual-owner commit ordering against the host's bounded transport admission.

Offline operations retain their exclusive root lifecycle. This is transport
ordering, not granular policy activation or a projected-output authority proof.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from threading import current_thread
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookflow.storage.engine import Database
    from bookflow.core.publication_admission import Admission


class Outcome(str, Enum):
    COMMITTED = "committed"
    PARTIAL = "partial"
    ROLLED_BACK = "rolled_back"
    UNCHANGED = "unchanged"


class Impact(Enum):
    CONSERVATIVE = "potential_authority_reduction"
    PROJECTION = "projection_only"
    LIVENESS = "liveness_extension"
    EXPIRED = "expired_token_cleanup"


# Actual owners, not command names or SQL-driver interception. Unknown owners
# fail closed; conservative owners include source/work/recovery graph changes.
OWNERS = {
    "dispatch.apply": Impact.CONSERVATIVE,
    "dispatch.open_company": Impact.CONSERVATIVE,
    "dispatch.record_migration": Impact.PROJECTION,
    "dispatch.complete_trash": Impact.CONSERVATIVE,
    "http.issue_session": Impact.CONSERVATIVE,
    "http.revoke": Impact.CONSERVATIVE,
    "http.refresh_token": Impact.LIVENESS,
    "company.rename": Impact.CONSERVATIVE,
    "hub.init": Impact.CONSERVATIVE,
    "hub.upgrade": Impact.CONSERVATIVE,
    "hub.org_rename": Impact.CONSERVATIVE,
    "hub.attach_projection": Impact.PROJECTION,
    "hub.company_new": Impact.CONSERVATIVE,
    "hub.demo_reset": Impact.CONSERVATIVE,
    "host.migrate_everything": Impact.CONSERVATIVE,
    "host.sweep": Impact.EXPIRED,
    "moves.company": Impact.CONSERVATIVE,
    "moves.org": Impact.CONSERVATIVE,
    "migration.head": Impact.CONSERVATIVE,
    "migration.company": Impact.CONSERVATIVE,
    "rollout.company": Impact.CONSERVATIVE,
    "profiles.standard": Impact.CONSERVATIVE,
    "attachments.finish": Impact.CONSERVATIVE,
    "attachments.collect": Impact.CONSERVATIVE,
    "memorized.enter": Impact.CONSERVATIVE,
    "config.flush": Impact.PROJECTION,
}


@dataclass
class CommitOperation:
    owner: str
    databases: dict[int, "Database"] = field(default_factory=dict)
    committed: int = 0
    external: bool = False
    failed: bool = False
    barrier: object | None = None


class CommitHooks:
    def __init__(self, admission: "Admission | None" = None, *, writer=None):
        self.admission = admission
        self._operation: CommitOperation | None = None
        self._depth = 0
        self._writer_thread = writer

    @contextmanager
    def operation(self, owner: str, *databases: "Database | None"):
        OWNERS[owner]
        self._confined()
        if self._operation is not None and self._depth == 0:
            raise RuntimeError("Previous commit outcome is unresolved")
        outer = self._operation is None
        if outer:
            self._operation = CommitOperation(owner)
        self._depth += 1
        for db in databases:
            if db is not None:
                self._watch(db)
        try:
            yield self._operation
        except BaseException:
            if outer:
                self._operation.failed = True
            raise
        finally:
            self._depth -= 1
            if outer:
                self.resolve()

    def _confined(self):
        if self.admission is not None:
            if self._writer_thread is None or current_thread() is not self._writer_thread:
                raise RuntimeError("Hosted commit hooks require their owning writer thread")

    def _before(self, operation: CommitOperation, owner: str, impact: Impact):
        self._confined()
        if operation is not self._operation or self._depth == 0:
            raise RuntimeError("Commit lost its outer owner")
        if self.admission is not None and impact is Impact.CONSERVATIVE and operation.barrier is None:
            operation.barrier = self.admission.close_for_commit()

    def _after(self, operation: CommitOperation, outcome: Outcome):
        self._confined()
        if operation.barrier is not None:
            self.admission.finish_commit(operation.barrier,
                committed=outcome in (Outcome.COMMITTED, Outcome.PARTIAL))
            operation.barrier = None

    def _watch(self, db: "Database"):
        self._confined()
        operation = self._operation
        if operation is None or self._depth == 0:
            raise RuntimeError("Commit has no active actual owner")
        operation.databases[id(db)] = db
        return operation

    def commit(self, db: "Database", owner: str):
        operation = self._watch(db)
        self._before(operation, owner, OWNERS[owner])
        db.raw.execute("COMMIT")
        operation.committed += 1

    @contextmanager
    def autocommit(self, db: "Database", owner: str):
        operation = self._watch(db)
        if db.write_transaction:
            raise RuntimeError("Autocommit owner entered inside a transaction")
        self._before(operation, owner, OWNERS[owner])
        yield
        operation.committed += 1

    def publishing(self, owner: str):
        operation = self._operation
        if operation is None or self._depth == 0:
            raise RuntimeError("Publication has no active actual owner")
        self._before(operation, owner, OWNERS[owner])
        # Filesystem publication can have durable effects even if it raises.
        operation.external = True

    def resolve(self):
        """Called at scope exit and after the host's existing rollback cleanup."""
        self._confined()
        operation = self._operation
        if operation is None or self._depth:
            return
        if any(not getattr(db, '_closed', False) and db.write_transaction
               for db in operation.databases.values()):
            # Keep ownership until the existing owner/host resolves transactions.
            return
        outcome = (Outcome.PARTIAL if operation.committed or operation.external else Outcome.ROLLED_BACK) if operation.failed else (Outcome.COMMITTED if operation.committed or operation.external else Outcome.UNCHANGED)
        self._after(operation, outcome)
        self._operation = None
