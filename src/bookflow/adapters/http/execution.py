"""Shared hosted command executor and publication boundary."""

from bookflow.core.context_options import normalize_options
from bookflow.core.dispatch import _close, execute, guard
from bookflow.core.errors import BookflowError
from bookflow.core.publication import PublicationPermit


class PublishedDocument(dict):
    """A plain JSON document carrying only internal, non-serialized authority state."""

    def __init__(self, document, permit, host, credential):
        super().__init__(document)
        self.permit, self.host, self.credential = permit, host, credential

    def check(self, *, original_response=True):
        self.permit.check(self.host, self.credential, original_response=original_response)


def run_hosted(host, cmd, raw, ctx, cred, selector, source, dry_run):
    normalize_options(cmd, company=selector if cmd.scope == "company" else None, reason=ctx.reason,
                      source_ref=ctx.source_ref, directive=ctx.directive_id,
                      idempotency_key=ctx.idempotency_key, dry_run=dry_run)
    permit = None

    def authenticated(session):
        nonlocal permit
        cred.revalidate(session.hub)
        permit = PublicationPermit.capture(cmd, raw, ctx, session, cred, selector, source, dry_run)
        try:
            result = execute(cmd, raw, ctx, session, company_selector=selector, company_source=source, dry_run=dry_run)
        except BookflowError:
            permit.finish(session, succeeded=False)
            raise
        permit.finish(session, result=result)
        return result

    try:
        if cmd.is_write and not dry_run or cmd.kind == "advisory":
            result = host.run_write(cred.user_id, cred.login, authenticated)
        else:
            session = host.reader_session(cred.user_id, cred.login)
            try:
                result = authenticated(session)
            finally:
                try:
                    guard(lambda: _close(session), cred.hub_admin)
                finally:
                    host.reader_done()
    except BookflowError as exc:
        if permit is not None:
            permit.check(host, cred, original_response=True)
            from bookflow.adapters.http.publication import protect
            protect(PublishedDocument(exc.to_dict(), permit, host, cred))
        raise
    document = PublishedDocument(result, permit, host, cred)
    from bookflow.adapters.http.publication import protect
    protect(document)
    try:
        document.check()
    except BookflowError as exc:
        raise BookflowError(exc.code, message=exc.message,
                            details={"stage": "publication", "outcome": "unknown"}) from None
    return document
