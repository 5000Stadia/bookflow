"""Local history execution under the authenticated reader's root lock."""
from bookflow.core import publication_audit, history_wire
from bookflow.core.history_request import HistoryRequest
from bookflow.core.identity_admin_binding import offline_reader
from bookflow.core.errors import BookflowError
from bookflow.hub.identity_admin import AdministrationError


def read(root, selection, ctx, *, bookmark=None):
    """Produce the shared closed response before releasing local read ownership.

    Call only on the offline dispatch path, before acquiring any other RootLock.
    The hosted forwarding path uses its existing retained publication permit.
    """
    request = HistoryRequest.capture(selection, bookmark)
    try:
        with offline_reader(root, request_id=ctx.request_id, principal=ctx.on_behalf_of) as reader:
            _, semantic = publication_audit.execute_history(reader, selection, ctx=ctx, request=request)
            output, proof = history_wire.bind(reader, semantic)
            # Complete the same authority check while the local writer exclusion
            # is still held; no open session/reader escapes into the return value.
            publication_audit.revalidate_proof(reader, proof, ctx=ctx)
            if proof.failure is not None:
                raise proof.failure.error()
            return output
    except AdministrationError:
        raise BookflowError('E_UNAUTHENTICATED') from None
