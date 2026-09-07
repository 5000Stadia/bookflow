"""Private actual-owner routing; admission closure is deliberately not installed yet.

The host supplies one writer-owned hook. Offline sessions use their exclusive
root lifecycle. No command input, environment setting or activation switch can
select a different publication policy. Increment 2 will bind these fixed seams
only together with response-wide F1 and current local publication.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
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
    "config.flush": Impact.PROJECTION,
}


@dataclass
class CommitOperation:
    owner: str
    databases: dict[int, "Database"] = field(default_factory=dict)
    committed: int = 0
    external: bool = False
    failed: bool = False


class CommitHooks:
    def __init__(self, admission: "Admission | None" = None):
        self.admission = admission
        self._operation: CommitOperation | None = None
        self._depth = 0

    @contextmanager
    def operation(self, owner: str, *databases: "Database | None"):
        OWNERS[owner]
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

    def _before(self, operation: CommitOperation, owner: str, impact: Impact):
        """Fixed increment-2 seam, before visibility; currently nonactivating."""

    def _after(self, operation: CommitOperation, outcome: Outcome):
        """Fixed increment-2 outcome seam; never requires event-loop progress."""

    def _watch(self, db: "Database"):
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
