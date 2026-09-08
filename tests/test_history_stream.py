"""Real hosted history frames through publication middleware and partial delivery."""
import asyncio
import json
from starlette.concurrency import run_in_threadpool
from bookflow.core.context import Context
from bookflow.core import registry
from bookflow.hub.audit_projection import HistorySelection
from bookflow.adapters.http.history_stream import drain
from bookflow.adapters.http.publication import PublicationMiddleware
from tests.test_history_cursors import world
from tests.test_audit_projection_publication import credential


def context():
    registry.load_all()
    return Context.new('http','History stream witness').model_copy(update={'request_id':'REQUEST'})


def fields(frame):
    return dict(line.split(': ',1) for line in frame.splitlines() if ': ' in line)


def test_disconnect_after_first_delivered_frame_resumes_at_second(world):
    host,_,first,second=world
    cred=credential(host,'R');ctx=context()
    selection=HistorySelection(mode='tail',limit=2,scan_limit=2,empty_start=True)
    delivered=[]
    class Disconnected(Exception):pass
    async def app(scope,receive,send):
        batch=await run_in_threadpool(drain,host,selection,ctx,cred)
        await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
        for frame in batch.frames:
            await send({'type':'http.response.body','body':frame.encode(),'more_body':True})
    async def send(message):
        if message['type']=='http.response.body':
            delivered.append(message['body'].decode())
            raise Disconnected()
    async def receive():return {'type':'http.disconnect'}
    async def exercise():
        try:await PublicationMiddleware(app,host=host)({'type':'http','path':'/hub-events'},receive,send)
        except Disconnected:pass
    asyncio.run(exercise())
    assert len(delivered)==1
    frame=fields(delivered[0]);assert frame['event']=='audit'
    assert json.loads(frame['data'])['id']==first
    follow=drain(host,selection.model_copy(update={'empty_start':False}),ctx,cred,frame['id'])
    assert [json.loads(fields(f)['data'])['id'] for f in follow.frames if fields(f)['event']=='audit']==[second]
    follow.document.check()
    assert host._readers_attached==0


def test_filtered_scan_checkpoint_advances_without_audit_frames(world):
    host,_,first,second=world
    cred=credential(host,'R');ctx=context()
    # Real events were initiated by A. R can see them but this business filter
    # selects none; the visible scan budget must still make bounded progress.
    selection=HistorySelection(mode='tail',limit=1,scan_limit=1,empty_start=True,actor='R')
    batch=drain(host,selection,ctx,cred)
    assert len(batch.frames)==1 and fields(batch.frames[0])['event']=='checkpoint'
    assert batch.document['items']==[] and batch.document['scanned_count']==1 and batch.more
    next_batch=drain(host,selection.model_copy(update={'empty_start':False}),ctx,cred,batch.next_cursor)
    assert next_batch.document['scanned_count']==1 and next_batch.document['items']==[]
    assert next_batch.next_cursor!=batch.next_cursor and not next_batch.more
    idle=drain(host,selection.model_copy(update={'empty_start':False}),ctx,cred,next_batch.next_cursor)
    assert idle.frames==() and idle.next_cursor==next_batch.next_cursor
    assert host._readers_attached==0


def test_hidden_write_keeps_idle_stream_bookmark_and_frames_unchanged(world):
    from bookflow.hub import identity_admin as admin
    from tests.test_audit_projection_publication import apply
    host,path,*_=world
    cred=credential(host,'R');ctx=context()
    selection=HistorySelection(mode='tail',limit=2,scan_limit=2)
    before=drain(host,selection,ctx,cred)
    assert len(before.frames)==1 and fields(before.frames[0])['event']=='checkpoint'
    apply(host,path,admin.SetUserActive('U',5,False),'H')
    after=drain(host,selection,ctx,cred,before.next_cursor)
    assert after.frames==() and after.next_cursor==before.next_cursor
    assert after.document==before.document
    before.document.check()
    assert host._readers_attached==0
