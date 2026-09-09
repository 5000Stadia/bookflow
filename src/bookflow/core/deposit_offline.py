"""Local public deposit detail execution under the authenticated reader's root lock.

Call only on the offline dispatch path, after hosted forwarding and before any
other RootLock is acquired. The hosted route has its own sibling branch in
``adapters/http/execution`` and its own retained publication permit.
"""
from bookflow.company import deposit_public_authority as pa
from bookflow.core import publication_deposit
from bookflow.core.deposit_request import prepare
from bookflow.core.errors import BookflowError
from bookflow.core.identity_admin_binding import offline_reader
from bookflow.hub.identity_admin import AdministrationError


def run_command(root, cmd, raw, ctx, selector, source, dry_run=False):
    """Produce the closed public response before releasing local read ownership.

    The binding handed to deposit execution is the genuine OS producer this
    reader authenticated with, taken from the private execution bridge. It is
    never rebuilt from a ReaderIdentity, and the financial owners revalidate it
    themselves. Revalidation completes while the local writer exclusion is still
    held; no open session or reader escapes into the return value.
    """
    try:
        with offline_reader(root, request_id=ctx.request_id, principal=ctx.on_behalf_of) as reader:
            binding = reader.execution_binding()
            audience = pa.audience(reader, binding)
            request = prepare(reader, audience, cmd, raw, ctx, selector, source, dry_run)
            output, proof = publication_deposit.execute_detail(reader, request, binding, ctx=ctx)
            publication_deposit.revalidate_proof(reader, proof, binding, ctx=ctx)
            if proof.failure is not None:
                raise proof.failure.error()
            return output
    except AdministrationError:
        raise BookflowError('E_UNAUTHENTICATED') from None
