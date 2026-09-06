"""One host-owned MCP intent registry, using shared authorization and execution."""

from dataclasses import dataclass

from bookflow.core import registry
from bookflow.core.context import Context
from bookflow.core.context_options import normalize_options
from bookflow.core.dispatch import authorize, validate_context, validate_input
from bookflow.core.errors import BookflowError
from bookflow.core.publication import PublicationPermit, publication_reader, _actor, _membership
from bookflow.adapters.http.execution import run_hosted

from .intents import OwnedIntents


def require_parser():
    try:
        import ijson  # noqa: F401
    except ImportError:
        raise BookflowError("E_USAGE", message="Install bookflow-core[mcp] in the Bookflow host environment to enable MCP file and JSON transport.") from None


def unknown(reason="intent_unavailable"):
    return BookflowError("E_IO", details={"reason": reason, "outcome": "unknown"})


def owner(credential):
    return credential.token_id, credential.user_id, credential.on_behalf_of or credential.user_id


def admission_authorize(cmd, ctx, session, selector, source, dry_run):
    normalize_options(cmd, company=selector, reason=ctx.reason, source_ref=ctx.source_ref,
                      directive=ctx.directive_id, idempotency_key=ctx.idempotency_key, dry_run=dry_run)
    validate_context(ctx)
    return authorize(cmd, ctx, session, company_selector=selector, company_source=source,
                     dry_run=dry_run, read_only=True)


@dataclass(repr=False)
class AdmissionRejection:
    host: object
    credential: object
    cmd: object
    ctx: object
    selector: object
    source: str
    dry_run: bool
    document: dict

    def check(self, **_kwargs):
        # Re-establish the identical pure admission rejection. No planner, input
        # body, execution, intent or business receipt exists at this boundary.
        with publication_reader(self.host, self.credential) as session:
            self.credential.revalidate(session.hub)
            try:
                admission_authorize(self.cmd, self.ctx, session, self.selector, self.source, self.dry_run)
            except BookflowError as exc:
                if exc.to_dict() == self.document:
                    return
        raise BookflowError('E_PERMISSION', details={'stage': 'publication', 'outcome': 'unknown'})


class Runtime:
    @classmethod
    def for_host(cls, host):
        with host._readers_lock:
            runtime = getattr(host, "_mcp_runtime", None)
            if runtime is None:
                runtime = cls(host)
                host._mcp_runtime = runtime
            return runtime

    def __init__(self, host):
        require_parser()
        from .limits import json_seconds
        self.json_seconds = json_seconds()
        self.host = host
        self.intents = OwnedIntents(host)

    def admit(self, cmd, ctx, credential, selector, source, dry_run):
        """Scope admission precedes reading a caller's input JSON or binary file."""
        if cmd.local_only or cmd.standalone:
            raise BookflowError("E_USAGE", details={"boundary": "local_only"})
        with publication_reader(self.host, credential) as session:
            credential.revalidate(session.hub)
            try:
                ctx = admission_authorize(cmd, ctx, session, selector, source, dry_run)
            except BookflowError as exc:
                exc.admission_rejection = AdmissionRejection(self.host, credential, cmd, ctx, selector, source, dry_run, exc.to_dict())
                raise
            header = {"command": cmd.name, "context": ctx.model_dump(mode="json"),
                      "selector": session.company_row["id"] if cmd.scope == "company" else None,
                      "source": source, "dry_run": dry_run, "actor": _actor(session),
                      "memberships": frozenset(_membership(row) for row in session.memberships)}
        return self.intents.admit(owner(credential), header=header)

    def _header_authority(self, intent, credential):
        header = intent.header
        cmd = registry.get(header["command"])
        if cmd is None:
            raise unknown("registry_changed")
        with publication_reader(self.host, credential) as session:
            credential.revalidate(session.hub)
            if _actor(session) != header["actor"] or frozenset(_membership(row) for row in session.memberships) != header["memberships"]:
                raise BookflowError("E_PERMISSION", details={"stage": "publication", "outcome": "unknown"})
            authorize(cmd, Context.model_validate(header["context"]), session,
                      company_selector=header["selector"], company_source=header["source"],
                      dry_run=header["dry_run"], read_only=True)

    def lookup(self, reference, credential):
        intent = self.intents.observe(reference, owner(credential))
        if intent is None:
            raise unknown()
        if intent.publication is not None:
            PublicationPermit.from_retained(intent.publication).check(self.host, credential)
        else:
            self._header_authority(intent, credential)
        return intent

    def input_permit(self, intent, raw, credential):
        """Shared preparation yields guarded command rejections, not protocol errors."""
        from bookflow.adapters.http.execution import PublishedDocument
        header = intent.header
        cmd = registry.get(header['command'])
        self._header_authority(intent, credential)
        rejection = None
        with publication_reader(self.host, credential) as session:
            credential.revalidate(session.hub)
            ctx = authorize(cmd, Context.model_validate(header['context']), session,
                company_selector=header['selector'], company_source=header['source'],
                dry_run=header['dry_run'], read_only=True)
            permit = PublicationPermit.capture(cmd, raw, ctx, session, credential,
                header['selector'], header['source'], header['dry_run'])
            permit.company = ((session.company_row['id'], session.company_row['organization_id'])
                              if session.company_row is not None else None)
            if permit.input_error is not None:
                rejection = BookflowError(permit.input_error['code'], message=permit.input_error['message'], details=permit.input_error['details'])
            else:
                try:
                    if cmd.authorize_input is not None:
                        cmd.authorize_input(permit.inp, ctx, session)
                    if cmd.transfer is not None:
                        cmd.transfer.prepare(permit.inp, ctx, session)
                    permit.authorize_initial_input(session)
                except BookflowError as exc:
                    rejection = exc
        if rejection is not None:
            permit.check(self.host, credential)
            rejection.publication_document = PublishedDocument(rejection.to_dict(), permit, self.host, credential)
            rejection.preparation_rejection = True
            raise rejection
        return permit

    def prepare_json(self, intent, raw, credential, *, retain=True, deliver_rejection=False):
        """Validate/freeze ordinary input; no planner or business execution runs here."""
        try:
            return self._prepare_json(intent, raw, credential, retain=retain)
        except BookflowError as exc:
            # A verified rejection has a delivery owner; it must not be released
            # before that owner's terminal and cleanup. Other failures abandon it.
            if (not deliver_rejection or getattr(exc, 'publication_document', None) is None) and intent.completed is None:
                self.intents.release(intent.reference, owner(credential))
            raise
        except BaseException:
            if intent.completed is None:
                self.intents.release(intent.reference, owner(credential))
            raise

    def _prepare_json(self, intent, raw, credential, *, retain):
        header = intent.header
        cmd = registry.get(header["command"])
        if cmd.transfer is not None:
            raise BookflowError("E_USAGE", message="Use this command's binary preparation.")
        self.input_permit(intent, raw, credential)
        with self.intents.preparation_worker(intent):
            with publication_reader(self.host, credential) as session:
                credential.revalidate(session.hub)
                ctx = authorize(cmd, Context.model_validate(header["context"]), session,
                                company_selector=header["selector"], company_source=header["source"],
                                dry_run=header["dry_run"], read_only=True)
                inp = validate_input(cmd, raw)
                if cmd.authorize_input is not None:
                    cmd.authorize_input(inp, ctx, session)
                permit = PublicationPermit.capture(cmd, raw, ctx, session, credential,
                    header["selector"], header["source"], header["dry_run"])
                permit.company = ((session.company_row["id"], session.company_row["organization_id"])
                                  if session.company_row is not None else None)
                # This marks successful authorization, not a committed write.
                permit.execution_succeeded = True
                state = permit.retained()
            permit.check(self.host, credential)
            payload = {"input": raw, "publication": state}
            self.intents.ready(intent, payload, retain=retain)
            intent.publication = intent.frozen["publication"] if retain else state
            return payload

    def execute_json(self, intent, credential, *, direct=None):
        """Queue once; repeated reference aliases only observe the existing state."""
        self.lookup(intent.reference, credential)
        if not self.intents.queue(intent):
            return None
        header = intent.header
        cmd = registry.get(header["command"])
        payload = direct if direct is not None else intent.frozen
        if payload is None:
            raise unknown("input_unavailable")
        # The actual host callback claims execution after its fresh credential
        # check. A queue timeout/release cannot turn this reference into new work.
        try:
            return run_hosted(self.host, cmd, payload["input"], Context.model_validate(header["context"]),
                              credential, header["selector"], header["source"], header["dry_run"],
                              before_execute=lambda session: self.intents.start(intent))
        finally:
            with self.intents.lock:
                intent.execution_returned = True
                intent.progress = self.intents.clock()

    def prepare_transfer(self, intent, raw, credential, *, deliver_rejection=False):
        """Acquire the registered transfer before the launcher opens its file."""
        from bookflow.adapters.http.published_transfer import PublishedTransfer
        header = intent.header
        try:
            self.input_permit(intent, raw, credential)
        except BookflowError as exc:
            if not deliver_rejection or getattr(exc, 'publication_document', None) is None:
                self.intents.release(intent.reference, owner(credential))
            raise
        try:
            with self.intents.preparation_worker(intent):
                transfer = PublishedTransfer(self.host, registry.get(header["command"]), raw,
                    Context.model_validate(header["context"]), credential.user_id, credential.login,
                    header["selector"], header["source"], header["dry_run"],
                    authorize_session=lambda s: credential.revalidate(s.hub),
                    credential=credential, defer_output=True,
                    before_execute=lambda s: self.intents.start(intent))
                # The intent owns the resource until its real preparation/execution/
                # delivery worker exits. The sweeper never closes a pinned worker.
                intent.transfer = transfer
                def cleanup():
                    transfer.close()
                    intent.transfer = None
                intent.cleanup = cleanup
                self._transfer_permit(intent, raw, credential)
                with self.intents.lock:
                    self.intents.ready(intent, {"input": raw, "publication": intent.publication})
                    transfer.raw = intent.frozen["input"]
                    intent.publication = intent.frozen["publication"]
                    if transfer.cmd.transfer.direction == "input":
                        intent.state = "receiving"
                return transfer
        except BaseException:
            self.intents.release(intent.reference, owner(credential))
            raise

    def _transfer_permit(self, intent, raw, credential):
        header, transfer = intent.header, intent.transfer
        with publication_reader(self.host, credential) as session:
            credential.revalidate(session.hub)
            ctx = authorize(transfer.cmd, Context.model_validate(header["context"]), session,
                company_selector=header["selector"], company_source=header["source"],
                dry_run=header["dry_run"], read_only=True)
            permit = PublicationPermit.capture(transfer.cmd, raw, ctx, session, credential,
                header["selector"], header["source"], header["dry_run"])
            permit.company = session.company_row["id"], session.company_row["organization_id"]
            permit.execution_succeeded = True
            info = transfer.resource.info
            permit.projection["transfer"] = (str(transfer.resource.store),
                info.sha256 if info is not None else None, info.size_bytes if info is not None else 0)
        permit.check(self.host, credential)
        intent.publication = permit.retained()

    def seal_transfer(self, intent, credential, digest, size):
        """Only the delivered client computes these values from its stable source."""
        self.lookup(intent.reference, credential)
        with self.intents.preparation_worker(intent):
            transfer = intent.transfer
            if transfer.cmd.transfer.direction != "input":
                raise BookflowError("E_USAGE")
            transfer.body.complete()
            info = transfer.resource.info
            if info.sha256 != digest or info.size_bytes != size:
                raise BookflowError("E_IO", details={"reason": "source_digest", "outcome": "not_submitted"})
            self._transfer_permit(intent, transfer.raw, credential)
            self.intents.ready(intent, {"input": transfer.raw, "publication": intent.publication})
            transfer.raw = intent.frozen["input"]
            intent.publication = intent.frozen["publication"]

    def execute_transfer(self, intent, credential):
        self.lookup(intent.reference, credential)
        if not self.intents.queue(intent):
            return None
        transfer = intent.transfer
        try:
            return transfer.finish_input() if transfer.cmd.transfer.direction == "input" else transfer.start_output()
        finally:
            with self.intents.lock:
                intent.execution_returned = True
                intent.progress = self.intents.clock()

    def reopen_output(self, intent, credential):
        """Reopen only the original verified body for delivery; never call execute."""
        from bookflow.adapters.http.published_transfer import PublishedTransfer
        header = intent.header
        permit = PublicationPermit.from_retained(intent.publication)
        permit.check(self.host, credential)
        transfer = PublishedTransfer(self.host, permit.cmd, permit.inp.model_dump(mode="json", exclude_unset=True),
            permit.ctx, credential.user_id, credential.login, header["selector"], header["source"], False,
            authorize_session=lambda s: credential.revalidate(s.hub), credential=credential, defer_output=True)
        try:
            original = permit.projection["transfer"]
            info = transfer.resource.info
            if (str(transfer.resource.store), info.sha256, info.size_bytes) != original:
                raise unknown("download_changed")
        except BaseException:
            transfer.close()
            raise
        intent.transfer = transfer
        def cleanup():
            transfer.close()
            intent.transfer = None
        intent.cleanup = cleanup
        return transfer
