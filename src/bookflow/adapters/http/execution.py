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


def run_hosted(host, cmd, raw, ctx, cred, selector, source, dry_run, *, before_execute=None, _semantic_history=None, _wire_history=False, _history_bookmark=None):
    normalize_options(cmd, company=selector, reason=ctx.reason,
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
        if _semantic_history is not None:
            from bookflow.core.identity_admin_binding import hosted_reader
            from bookflow.core import publication_audit
            from bookflow.hub.audit_projection import HistorySelection
            from bookflow.hub.identity_admin import AdministrationError
            selection = _semantic_history
            if type(selection) is not HistorySelection:
                raise BookflowError('E_VALIDATION')
            expected = ('hub audit ' if selection.company is None else 'audit ') + selection.mode
            if selection.mode == 'activity':
                expected = 'activity'
            if cmd.name != expected or cmd.is_write or dry_run:
                raise BookflowError('E_VALIDATION')
            try:
                with hosted_reader(host, _reader_binding(host, cred, ctx.request_id),
                                   request_id=ctx.request_id) as reader:
                    identity = reader.authenticate()
                    if ctx.on_behalf_of is not None and ctx.on_behalf_of != identity.principal:
                        raise BookflowError('E_UNAUTHENTICATED')
                    session = reader.session
                    if before_execute is not None:
                        before_execute(session)
                        reader.authenticate()
                    # This private service has its own closed selection model.
                    # Registered public models remain unchanged until cutover.
                    permit = PublicationPermit(cmd, None, ctx,
                        (identity.actor, identity.actor_kind, identity.hub_admin),
                        frozenset(), None, None)
                    request = None
                    if _wire_history:
                        from bookflow.core.history_request import HistoryRequest
                        request = HistoryRequest.capture(selection, _history_bookmark)
                    result, proof = publication_audit.execute_history(reader, selection,ctx=ctx,request=request)
                    if _wire_history:
                        from bookflow.core.history_wire import bind
                        result, proof = bind(reader, proof)
                    finish(session, succeeded=proof.failure is None,result=result, audit_proof=proof)
                    if proof.failure is not None:
                        raise proof.failure.error()
            except AdministrationError:
                raise BookflowError('E_UNAUTHENTICATED') from None
        elif cmd.is_write and not dry_run or cmd.kind == "advisory":
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
            rejected = PublishedDocument(exc.to_dict(), permit, host, cred)
            protect(rejected)
            try:
                permit.check(host, cred, original_response=True)
            except BookflowError as denied:
                raise BookflowError(denied.code, details={"stage": "publication", "outcome": "unknown"}) from None
            exc.publication_document = rejected
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


def _reader_binding(host, cred, request_id):
    """Only server-owned admitted credentials reach this adapter boundary."""
    from bookflow.core.publication import OSBinding
    from bookflow.hub.identity_admin import TokenBinding
    if type(cred) is OSBinding:
        return cred
    from bookflow.adapters.http.app import Credential
    if type(cred) is not Credential:
        raise BookflowError('E_UNAUTHENTICATED')
    return TokenBinding(cred._secret, cred.token_id, cred.user_id, cred.kind,
                        cred.on_behalf_of, host.data_root / 'hub.db', request_id)


def run_history(host, selection, ctx, cred, *, wire=False, bookmark=None):
    """Private semantic service using actual execution/publication owners.

    No registered endpoint calls this until the inseparable cursor wire cutover.
    """
    from bookflow.core import registry
    name = ('hub audit ' if selection.company is None else 'audit ') + selection.mode
    if selection.mode == 'activity':
        name = 'activity'
    return run_hosted(host, registry.get(name), {}, ctx, cred,
                      selection.company, 'option', False, _semantic_history=selection,
                      _wire_history=wire, _history_bookmark=bookmark)
