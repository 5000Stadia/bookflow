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

    def __deepcopy__(self, memo):
        # Copy business values, retaining the same live publication guard. Host
        # locks/streams and credentials are not part of the JSON value graph.
        from copy import deepcopy
        result = type(self)({}, self.permit, self.host, self.credential)
        memo[id(self)] = result
        result.update((deepcopy(key, memo), deepcopy(value, memo)) for key, value in self.items())
        return result


def run_hosted(host, cmd, raw, ctx, cred, selector, source, dry_run, *, before_execute=None):
    normalize_options(cmd, company=selector if cmd.scope == "company" else None, reason=ctx.reason,
                      source_ref=ctx.source_ref, directive=ctx.directive_id,
                      idempotency_key=ctx.idempotency_key, dry_run=dry_run)
    permit = None

    def finish(session, **values):
        try:
            permit.finish(session, **values)
        except Exception:
            raise BookflowError("E_IO", details={"stage": "publication", "outcome": "unknown",
                                                  "reason": "receipt_certificate"}) from None

    def authenticated(session):
        nonlocal permit
        cred.revalidate(session.hub)
        if before_execute is not None:
            before_execute(session)
        permit = PublicationPermit.capture(cmd, raw, ctx, session, cred, selector, source, dry_run)
        try:
            result = execute(cmd, raw, ctx, session, company_selector=selector, company_source=source, dry_run=dry_run)
        except BookflowError:
            finish(session, succeeded=False)
            raise
        finish(session, result=result)
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
            from bookflow.adapters.http.publication import protect
            protect(PublishedDocument(exc.to_dict(), permit, host, cred))
            try:
                permit.check(host, cred, original_response=True)
            except BookflowError as denied:
                raise BookflowError(denied.code, details={"stage": "publication", "outcome": "unknown"}) from None
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
