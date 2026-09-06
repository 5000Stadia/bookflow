"""Publication observer for shared HostedTransfer, usable by HTTP and MCP delivery."""

from bookflow.core.errors import BookflowError
from bookflow.core.publication import PublicationPermit
from bookflow.core.transfers import HostedTransfer
from .execution import PublishedDocument
from .publication import protect


class PublishedTransfer(HostedTransfer):
    def __init__(self, *args, credential, before_execute=None, **kwargs):
        self.credential = credential
        self.before_execute = before_execute
        self._publication_document = None
        super().__init__(*args, **kwargs)

    def finish_input(self):
        try:
            return super().finish_input()
        finally:
            # The host writer does not inherit the HTTP request ContextVar.
            # Register its completed guard back on the request's delivery worker.
            if self._publication_document is not None:
                protect(self._publication_document)

    def _execute(self, session, **options):
        self.credential.revalidate(session.hub)
        if self.before_execute is not None:
            self.before_execute(session)
        permit = PublicationPermit.capture(self.cmd, self.raw, self.ctx, session, self.credential,
                                             self.selector, self.source, self.dry_run)
        try:
            result = super()._execute(session, **options)
        except BookflowError as exc:
            permit.finish(session, succeeded=False)
            self._publication_document = PublishedDocument(exc.to_dict(), permit, self.host, self.credential)
            protect(self._publication_document)
            exc.publication_document = self._publication_document
            raise
        try:
            permit.finish(session, result=result)
        except Exception:
            document = PublishedDocument(BookflowError("E_IO", details={"stage": "publication",
                "outcome": "unknown", "reason": "receipt_certificate"}).to_dict(), permit, self.host, self.credential)
            protect(document)
            self._publication_document = document
            raise BookflowError("E_IO", details={"stage": "publication", "outcome": "unknown"}) from None
        document = PublishedDocument(result, permit, self.host, self.credential)
        self._publication_document = document
        protect(document)
        return document
