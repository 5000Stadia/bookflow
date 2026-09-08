"""Bounded retained inputs for execution-owned history continuation checks."""
from dataclasses import dataclass
from bookflow.hub.audit_projection import HistorySelection


@dataclass(frozen=True)
class HistoryRequest:
    selection: HistorySelection
    bookmark: str | None
    malformed: bool = False

    @classmethod
    def capture(cls, selection, bookmark):
        from bookflow.core.history_cursors import MAX_LENGTH
        if type(selection) is not HistorySelection:
            raise TypeError('closed history selection required')
        if bookmark is not None and (type(bookmark) is not str or not 1 <= len(bookmark) <= MAX_LENGTH):
            # A syntax-only rejection needs no arbitrary-sized caller object.
            return cls(selection, None, True)
        return cls(selection, bookmark)

    def resolve(self, reader, *, ctx=None):
        from bookflow.core import history_cursors, publication_audit
        if ctx is not None:
            publication_audit.open_selected(reader, self.selection, ctx)
        if self.malformed:
            history_cursors.invalid()
        if self.bookmark is None:
            return self.selection
        return history_cursors.resume(reader, self.selection, self.bookmark)
