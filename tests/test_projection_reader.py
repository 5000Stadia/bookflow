"""Actual hosted snapshot readers: concurrency, lifetime, bounded cancellation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from bookflow.core import identity_admin_binding as readers
from bookflow.core.host import Host
from bookflow.core.context import client_version
from bookflow.core.publication_admission import AdmissionCancelled
from bookflow.hub import identity_admin as b
from tests.test_permission_runtime import path
from tests.permission_admin_support import snapshot


def binding(path):
    return b.TokenBinding('secret-GP-live','GP-live','G','bearer','P',path,'REQUEST')


@pytest.fixture
def hosted(path):
    host=Host(path.parent,version=client_version());host.start()
    try:yield host,binding(path)
    finally:host.stop()


def test_actual_concurrent_readers_and_writer_progress(hosted):
    host,admitted=hosted;barrier=Barrier(3)
    def read():
        with readers.hosted_reader(host,admitted,request_id='REQUEST') as reader:
            identity=reader.authenticate()
            barrier.wait(timeout=10)
            barrier.wait(timeout=10)
            return identity
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(read);second=pool.submit(read)
        barrier.wait(timeout=10)
        assert host.submit(lambda: 'writer completed',timeout=5)=='writer completed'
        assert host._readers_attached==2
        barrier.wait(timeout=10)
        assert first.result().actor==second.result().actor=='G'
    assert host._readers_attached==0


@pytest.mark.parametrize('ending',['commit','rollback','thread','exit'])
def test_reader_lifetime_not_just_transaction_boolean(hosted,ending):
    host,admitted=hosted
    with readers.hosted_reader(host,admitted,request_id='REQUEST') as reader:
        before=snapshot(reader.session.hub.raw)
        if ending in ('commit','rollback'):
            reader.session.hub.raw.execute(ending.upper())
            reader._tx.raw.execute('BEGIN')
            with pytest.raises(b.AdministrationError):reader.authenticate()
        elif ending=='thread':
            with ThreadPoolExecutor(max_workers=1) as pool:
                with pytest.raises(b.AdministrationError):pool.submit(reader.authenticate).result()
            assert reader.authenticate().principal=='P'
        else:
            assert snapshot(reader.session.hub.raw)==before
    with pytest.raises(b.AdministrationError):reader.authenticate()
    assert host._readers_attached==0


@pytest.mark.parametrize('crossings',[2,3])
def test_fresh_construction_bound_and_cleanup_with_real_commit(hosted,monkeypatch,crossings):
    host,admitted=hosted;original=readers._file;calls=[]
    def capture(root):
        value=original(root);calls.append(value)
        if len(calls)<=crossings:
            def writer():
                with host._commit_hooks.operation('http.revoke',host._hub):
                    host._hub.raw.execute('BEGIN IMMEDIATE')
                    host._hub.raw.execute("UPDATE users SET updated_at=updated_at WHERE id='H'")
                    host._commit_hooks.commit(host._hub,'http.revoke')
            host.submit(writer)
        return value
    monkeypatch.setattr(readers,'_file',capture)
    entered=[]
    def attempt():
        with readers.hosted_reader(host,admitted,request_id='REQUEST') as reader:
            entered.append(reader.authenticate())
    if crossings==3:
        with pytest.raises(AdmissionCancelled):attempt()
        assert entered==[]
    else:
        attempt();assert entered[0].actor=='G'
    assert len(calls)==3 and host._readers_attached==0


def test_pending_config_same_connection_without_config_reload(path,monkeypatch):
    from bookflow.core.config import Config, os_login, dump
    from bookflow.core.publication import OSBinding
    from bookflow.storage.engine import open_database
    host=Host(path.parent,version=client_version());host.start()
    try:
        admitted=OSBinding.capture(host,os_login())
        # Existing owner publishes recoverable pending contents; the file is
        # intentionally left stale. All data is the test's ordinary owned root.
        def publish():
            cfg=Config(path.parent/'config.toml');cfg.set_user(os_login(),'H')
            with host._commit_hooks.operation('config.flush',host._hub):
                host._hub.raw.execute('BEGIN IMMEDIATE')
                host._hub.raw.execute("INSERT INTO pending_config(id,token,request_id,contents) VALUES(1,'reader-config','REQUEST',?)",(dump(cfg.data),))
                host._commit_hooks.commit(host._hub,'config.flush')
        host.submit(publish)
        (path.parent/'config.toml').write_text('[users."'+os_login()+'"]\nuser_id="A"\n')
        def forbidden(*args,**kwargs):raise AssertionError('separate Config.load forbidden')
        monkeypatch.setattr(Config,'load',forbidden)
        with readers.hosted_reader(host,admitted,request_id='REQUEST') as reader:
            assert reader.authenticate().actor=='H'
            assert reader.session.config.user_table(os_login())['user_id']=='H'
        assert host._readers_attached==0
    finally:host.stop()


def test_no_command_retry_after_yield(hosted,monkeypatch):
    host,admitted=hosted;original=readers._file;captures=[]
    def capture(root):captures.append(root);return original(root)
    monkeypatch.setattr(readers,'_file',capture)
    entered=[]
    with pytest.raises(AdmissionCancelled):
        with readers.hosted_reader(host,admitted,request_id='REQUEST'):
            entered.append(1)
            raise AdmissionCancelled('existing cancellation')
    assert len(captures)==1 and entered==[1] and host._readers_attached==0


def test_offline_actual_rootlock_and_full_preservation(path):
    from bookflow.storage.engine import open_database
    with open_database(path,writable=False) as db:
        before=snapshot(db.raw)
    with readers.offline_reader(path.parent,request_id='OFFLINE') as reader:
        assert reader.authenticate().actor=='H'
        assert reader._gate is None
        assert reader.session.hub.raw.in_transaction
    with open_database(path,writable=False) as db:
        assert snapshot(db.raw)==before


@pytest.mark.parametrize('crossing',['clear','replace_then_clear','aba'])
def test_actual_config_commit_crossings_restart_whole_reader(path,monkeypatch,crossing):
    from bookflow.core.config import Config,os_login
    from bookflow.core.publication import OSBinding
    host=Host(path.parent,version=client_version());host.start()
    try:
        admitted=OSBinding.capture(host,os_login())
        def stage(who):
            cfg=Config(path.parent/'config.toml');cfg.set_user(os_login(),who)
            with host._commit_hooks.operation('dispatch.apply',host._hub):
                host._hub.raw.execute('BEGIN IMMEDIATE')
                cfg.stage_pending(host._hub,request_id='REQUEST')
                host._commit_hooks.commit(host._hub,'dispatch.apply')
        def flush():
            Config(path.parent/'config.toml').flush_pending(host._hub,commits=host._commit_hooks)
        if crossing!='aba':
            host.submit(lambda:stage('A'))
            admitted=OSBinding.capture(host,os_login())
            if crossing=='replace_then_clear':
                def replace_copy():
                    # Actual publication boundary stopped just before clearing
                    # its existing pending intent; no authority mutation here.
                    with host._commit_hooks.operation('config.flush',host._hub):
                        host._commit_hooks.publishing('config.flush')
                        cfg=Config(path.parent/'config.toml');cfg.set_user(os_login(),'A');cfg.save()
                host.submit(replace_copy)
        original=readers._file;captures=[]
        def capture(root):
            value=original(root);captures.append(value)
            if len(captures)==1:
                def publish():
                    if crossing=='aba':stage('A')
                    flush()
                    if crossing=='aba':stage('H');flush()
                host.submit(publish)
            return value
        monkeypatch.setattr(readers,'_file',capture)
        with readers.hosted_reader(host,admitted,request_id='REQUEST') as reader:
            assert reader.authenticate().actor==('H' if crossing=='aba' else 'A')
        assert len(captures)==(1 if crossing=='replace_then_clear' else 2)
        assert host._readers_attached==0
    finally:host.stop()


def test_real_token_revocation_between_capture_and_snapshot_never_executes(hosted,monkeypatch):
    host,admitted=hosted;original=readers._file;calls=[]
    def capture(root):
        value=original(root);calls.append(root)
        if len(calls)==1:
            def revoke():
                with host._commit_hooks.operation('http.revoke',host._hub):
                    host._hub.raw.execute('BEGIN IMMEDIATE')
                    host._hub.raw.execute("UPDATE api_tokens SET revoked_at='2026-09-07T00:00:00Z' WHERE id='GP-live'")
                    host._commit_hooks.commit(host._hub,'http.revoke')
            host.submit(revoke)
        return value
    monkeypatch.setattr(readers,'_file',capture)
    from bookflow.core.errors import BookflowError
    with pytest.raises(BookflowError) as caught:
        with readers.hosted_reader(host,admitted,request_id='REQUEST'):
            pytest.fail('revoked binding executed')
    assert caught.value.code=='E_UNAUTHENTICATED'
    assert len(calls)==2 and host._readers_attached==0


def test_actual_pending_flush_cannot_reauthenticate_stale_file_actor(path,monkeypatch):
    """Real pending publication, no synthetic config.flush authority mutation."""
    from bookflow.core.config import Config,os_login
    from bookflow.core.publication import OSBinding
    from bookflow.core.errors import BookflowError
    host=Host(path.parent,version=client_version());host.start()
    try:
        old=OSBinding.capture(host,os_login())
        def stage():
            cfg=Config(path.parent/'config.toml');cfg.set_user(os_login(),'A')
            with host._commit_hooks.operation('dispatch.apply',host._hub):
                host._hub.raw.execute('BEGIN IMMEDIATE')
                cfg.stage_pending(host._hub,request_id='REQUEST')
                host._commit_hooks.commit(host._hub,'dispatch.apply')
        host.submit(stage)
        assert OSBinding.capture(host,os_login()).user_id=='A'
        original=readers._file;captures=[]
        def capture(root):
            value=original(root);captures.append(value)
            if len(captures)==1:
                host.submit(lambda:Config(root/'config.toml').flush_pending(host._hub,commits=host._commit_hooks))
            return value
        monkeypatch.setattr(readers,'_file',capture)
        with pytest.raises((b.AdministrationError,BookflowError)):
            with readers.hosted_reader(host,old,request_id='REQUEST'):
                pytest.fail('stale pre-flush file reauthenticated the revoked OS mapping')
        assert host._readers_attached==0
    finally:host.stop()


def test_sustained_real_writer_allows_reader_in_open_interval(hosted,monkeypatch):
    """Writer remains alive and commits again after one completed read."""
    from queue import Queue
    from threading import Event,Thread
    host,admitted=hosted
    requests=Queue();ack=Queue();open_interval=Event();completed=[]
    def worker():
        while True:
            item=requests.get()
            if item is None:return
            def write():
                with host._commit_hooks.operation('dispatch.apply',host._hub):
                    host._hub.raw.execute('BEGIN IMMEDIATE')
                    host._hub.raw.execute("UPDATE users SET updated_at=updated_at WHERE id='H'")
                    host._commit_hooks.commit(host._hub,'dispatch.apply')
            try:host.submit(write);completed.append(item);ack.put(None)
            except BaseException as exc:ack.put(exc);return
    writer=Thread(target=worker);writer.start()
    def commit(item):
        requests.put(item)
        result=ack.get(timeout=10)
        if result is not None:raise result
    original=readers._file;captures=[]
    def capture(root):
        value=original(root);captures.append(value)
        if len(captures)<=2:commit(len(captures))
        else:open_interval.set()
        return value
    monkeypatch.setattr(readers,'_file',capture)
    try:
        with readers.hosted_reader(host,admitted,request_id='REQUEST') as reader:
            assert open_interval.is_set() and writer.is_alive()
            assert reader.authenticate().actor=='G'
        assert completed==[1,2] and len(captures)==3
        commit(3);commit(4)
        assert completed==[1,2,3,4] and host._readers_attached==0
    finally:
        requests.put(None);writer.join(timeout=10)
        assert not writer.is_alive()
