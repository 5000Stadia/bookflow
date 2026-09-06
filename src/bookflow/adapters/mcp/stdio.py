"""Stop protocol output once the caller closes its stdio input."""

import anyio
from anyio.abc import ObjectReceiveStream, ObjectSendStream


def eof_fenced_streams(read, write):
    """Retain SDK stream context and cleanup, but never send after observed EOF.

    The SDK may otherwise emit a connection-closed error for an interrupted
    request while draining its handlers. EOF ends this launcher's output lifetime;
    it does not promise rollback of a write already submitted to the shared host.
    """
    closed = False

    class Read(ObjectReceiveStream):
        @property
        def last_context(self):
            return getattr(read, 'last_context', None)

        async def receive(self):
            nonlocal closed
            try:
                return await read.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                closed = True
                raise

        async def aclose(self):
            nonlocal closed
            closed = True
            await read.aclose()

    class Write(ObjectSendStream):
        async def send(self, item):
            if closed:
                await anyio.lowlevel.checkpoint()
                return
            await write.send(item)

        async def aclose(self):
            await write.aclose()

    return Read(), Write()
