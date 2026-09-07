from dataclasses import asdict, replace
import pytest
from bookflow.hub import identity_admin as b, permission_admin_audit as au, permission_catalog as c
from bookflow.core.audit import decode_snapshot
from bookflow.core.ids import is_ulid
from bookflow.storage.engine import open_database
from tests.permission_admin_support import *


@pytest.fixture
def path(tmp_path):return make_root(tmp_path/'hub.db')


def test_prepared_catalog_only_event_and_complete_decoded_envelopes(path,tmp_path,monkeypatch):
    with open_database(path,writable=True) as db:
        db.raw.execute('BEGIN IMMEDIATE');old=s.load_root(db,catalog=BUNDLE);old_state=rows(db.raw,'permission_state')[0];before=snapshot(db.raw);old_tokens=tokens(db.raw)
        new_descriptor=replace(old.catalog,version='test-v2');new_bundle=replace(BUNDLE,descriptor=new_descriptor,source_commit='a'*40)
        prepared=[];prepare=au.prepare_audit;write=b._write_mutation
        def capture(*args,**kwargs):
            value=prepare(*args,**kwargs)
            assert snapshot(db.raw)==before  # IDs/time/seq/encoded entries all fixed before DML.
            prepared.append(value);return value
        def first_write(*args,**kwargs):
            assert prepared;return write(*args,**kwargs)
        monkeypatch.setattr(au,'prepare_audit',capture);monkeypatch.setattr(b,'_write_mutation',first_write)
        result=b.apply_edit(db,binding=binding(path),intent=b.ReplaceCatalog(old.stamp.catalog_sha256,new_bundle,old.role_defaults),catalog=BUNDLE,visibility=VISIBILITY,audit=CONTEXT)
        events=rows(db.raw,'audit_events');entries=rows(db.raw,'audit_entries')
        assert len(events)==len(entries)==1 and entries[0]['record_id']=='1'
        event=events[0];entry=entries[0]
        assert is_ulid(event['id']) and is_ulid(entry['id']) and entry['event_id']==event['id']==result.event_id
        assert event==dict(id=event['id'],seq=1,at=AT,command='permission catalog replace',actor_id='H',actor_kind='human',on_behalf_of=None,interface='cli',client_name='owned-b2',client_version='1',client_host='owned',session_id='SESSION',request_id='REQUEST',idempotency_key=None,reason='authorized edit',directive_id=None,directive_code=None,source_ref=None,summary='Permission administration updated.')
        assert event==asdict(prepared[0].event) and entry==asdict(prepared[0].entries[0])
        before_row={key:old_state[key] for key in ('id','generation','mode','catalog_version','catalog_sha256','updated_at','updated_by','updated_via')}
        after_row=dict(before_row,generation=2,catalog_version='test-v2',catalog_sha256=c.catalog_manifest(new_descriptor).descriptor_sha256,updated_at=AT,updated_by='H',updated_via='cli')
        expected_before=dict(format=1,kind='permission_state',state_key=1,row=before_row,catalog=asdict(old.catalog),defaults=[asdict(x) for x in old.role_defaults])
        expected_after=dict(format=1,kind='permission_state',state_key=1,row=after_row,catalog=asdict(new_descriptor),defaults=[asdict(x) for x in old.role_defaults])
        # JSON changes tuples into arrays; expected data is independently declared above.
        expected_before=json.loads(json.dumps(expected_before));expected_after=json.loads(json.dumps(expected_after))
        assert decode_snapshot(entry['before'])==expected_before and decode_snapshot(entry['after'])==expected_after
        assert (entry['record_type'],entry['action'],entry['version_before'],entry['version_after'])==('permission_state','update',1,2)
        assert tokens(db.raw)==old_tokens
        receipt(tmp_path/'catalog-audit.json',expected_before=expected_before,observed_before=decode_snapshot(entry['before']),expected_after=expected_after,observed_after=decode_snapshot(entry['after']),event=event)


def test_compound_audit_full_safe_payloads_and_order(path,tmp_path):
    with open_database(path,writable=True) as db:
        original_tokens=tokens(db.raw);old_assign=rows(db.raw,'agent_principals');old_auth=next(x for x in rows(db.raw,'agent_authority') if x['agent_user_id']=='G')
        db.raw.execute('BEGIN IMMEDIATE');result=apply(db,b.SetAssignments('G',4,7,('Q','I'),False))
        entries=rows(db.raw,'audit_entries')
        assert [(x['record_type'],x['record_id']) for x in entries]==[('agent_authority','G'),*[('api_token',x) for x in sorted(G_REVOKED)],('permission_state','1')]
        decoded=[]
        safe_token=('id','version','created_at','created_by','created_via','updated_at','updated_by','updated_via','user_id','on_behalf_of','kind','expires_at','revoked_at','authority_epoch')
        for entry in entries:
            before=decode_snapshot(entry['before']);after=decode_snapshot(entry['after']);decoded.append((before,after))
            if entry['record_type']=='api_token':
                original=original_tokens[entry['record_id']]
                expected_before=dict(format=1,kind='api_token',row={k:original[k] for k in safe_token})
                expected_after=dict(format=1,kind='api_token',row=dict(expected_before['row'],version=6,revoked_at=AT,updated_at=AT,updated_by='H',updated_via='cli'))
                assert before==expected_before and after==expected_after
                assert (entry['version_before'],entry['version_after'])==(5,6)
            if entry['record_type']=='agent_authority':
                old_authority=dict(old_auth,fresh_context_required=False)
                expected_before=dict(format=1,kind='agent_authority',row=old_authority,
                    assignments=[next(x for x in old_assign if x['agent_user_id']=='G' and x['principal_user_id']==person) for person in ('I','P','Q')],transition_reasons=[])
                expected_after=dict(format=1,kind='agent_authority',row=dict(old_authority,epoch=8,version=5,suspended_at=AT,suspension_reason='binding_loss',fresh_context_required=True,updated_at=AT,updated_by='H',updated_via='cli'),
                    assignments=[dict(next(x for x in old_assign if x['agent_user_id']=='G' and x['principal_user_id']==person),revoked_at=AT if person=='P' else None) for person in ('I','P','Q')],transition_reasons=['binding_loss'])
                assert before==expected_before and after==expected_after
                assert (entry['version_before'],entry['version_after'])==(4,5)
        encoded=json.dumps(decoded)
        for forbidden in ('token_hash','password_hash','secret-','private-label','last_used_at'):
            assert forbidden not in encoded and forbidden not in repr(result.private)
        assert not any(x['token_hash'] in encoded for x in original_tokens.values())
        receipt(tmp_path/'compound-audit.json',decoded=decoded)


@pytest.mark.parametrize('stage',['initiating','authority_G','authority_J','token_G','token_J','state','defaults','audit_encode','audit_insert','final_check','actual_trigger'])
def test_whole_service_rolls_back_after_every_group(path,stage,monkeypatch,tmp_path):
    with open_database(path,writable=True) as db:
        if stage=='actual_trigger':db.raw.execute("CREATE TEMP TRIGGER abort_audit BEFORE INSERT ON main.audit_entries BEGIN SELECT RAISE(ABORT, 'controlled audit failure'); END")
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw);local=snapshot(db.raw,schema='temp');old=s.load_root(db,catalog=BUNDLE)
        defaults=tuple(x for x in old.role_defaults if not (x.role=='standard' and x.requirement.capability=='ledger.post'))
        intent=b.SetUserActive('P',5,False) if stage=='initiating' else b.ReplaceCatalog(old.stamp.catalog_sha256,BUNDLE,defaults)
        reached=[];write=b._write_mutation;encode=au.encode_snapshot;insert_audit=au.insert_audit;verify=b._verify_final
        def writing(tx,mutation):
            write(tx,mutation)
            identifier=dict(mutation.key).get('agent_user_id',dict(mutation.key).get('id'))
            hit=(stage=='initiating' and mutation.table=='users' or stage=='authority_G' and mutation.table=='agent_authority' and identifier=='G' or stage=='authority_J' and mutation.table=='agent_authority' and identifier=='J' or stage=='token_G' and mutation.table=='api_tokens' and identifier=='GP-live' or stage=='token_J' and mutation.table=='api_tokens' and identifier=='JU-live' or stage=='state' and mutation.table=='permission_state' or stage=='defaults' and mutation.table=='role_capabilities')
            if hit:reached.append(stage);raise RuntimeError('controlled group failure')
        def encoding(value):
            if stage=='audit_encode':reached.append(stage);raise RuntimeError('controlled encoding failure')
            return encode(value)
        def inserting(*args):
            insert_audit(*args)
            if stage=='audit_insert':reached.append(stage);raise RuntimeError('controlled insertion failure')
        def checking(*args):
            verify(*args)
            if stage=='final_check':reached.append(stage);raise RuntimeError('controlled final check failure')
        monkeypatch.setattr(b,'_write_mutation',writing);monkeypatch.setattr(au,'encode_snapshot',encoding);monkeypatch.setattr(au,'insert_audit',inserting);monkeypatch.setattr(b,'_verify_final',checking)
        with pytest.raises(Exception):apply(db,intent)
        if stage!='actual_trigger':assert reached==[stage]
        assert db.raw.in_transaction and snapshot(db.raw)==before and snapshot(db.raw,schema='temp')==local
        receipt(tmp_path/'rollback.json',stage=stage,complete_main_equal=True,complete_temp_equal=True)


def test_unexpected_local_side_effect_is_rejected_not_recomputed(path):
    with open_database(path,writable=True) as db:
        db.raw.execute('CREATE TABLE local_records (value BLOB)')
        db.raw.execute('INSERT INTO local_records VALUES (?)',(b'\x00\xffraw',))
        db.raw.execute("CREATE TRIGGER side_effect AFTER UPDATE ON memberships BEGIN UPDATE local_records SET value=X'0102'; END")
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        with pytest.raises(b.AdministrationError,match='allowed_write_set'):apply(db,b.PutMembership('R',c.ScopeKey('organization','O'),b.Version(1),'readonly'))
        assert snapshot(db.raw)==before


@pytest.mark.parametrize('counter',['user','membership','authority_version','epoch','token','generation','audit_sequence'])
def test_overflow_at_every_counter_has_zero_raw_mutation(path,counter):
    with open_database(path,writable=True) as db:
        maximum=9223372036854775807
        if counter=='user':db.raw.execute("UPDATE users SET version=? WHERE id='R'",(maximum,))
        if counter=='membership':db.raw.execute("UPDATE memberships SET version=? WHERE id='M-R'",(maximum,))
        if counter=='authority_version':db.raw.execute("UPDATE agent_authority SET version=? WHERE agent_user_id='G'",(maximum,))
        if counter=='epoch':db.raw.execute("UPDATE agent_authority SET epoch=? WHERE agent_user_id='G'",(maximum,))
        if counter=='token':db.raw.execute("UPDATE api_tokens SET version=? WHERE id='GP-live'",(maximum,))
        if counter=='generation':db.raw.execute('UPDATE permission_state SET generation=?',(maximum,))
        if counter=='audit_sequence':
            db.raw.execute('BEGIN IMMEDIATE');apply(db,b.PutMembership('B',c.ScopeKey('company','C'),b.Absent(),'standard'));db.raw.execute('COMMIT')
            db.raw.execute('UPDATE audit_events SET seq=?',(maximum,))
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw)
        if counter=='user':intent=b.SetUserActive('R',maximum,False)
        elif counter=='membership':intent=b.PutMembership('R',c.ScopeKey('organization','O'),b.Version(maximum),'readonly')
        else:intent=b.SetAssignments('G',maximum if counter=='authority_version' else 4,maximum if counter=='epoch' else 7,('Q','I'),False)
        with pytest.raises(b.AdministrationError,match='integer_overflow'):apply(db,intent)
        assert snapshot(db.raw)==before
        # No-op at these maxima does not perform unrelated repair/increment.
        if counter=='user':noop=b.SetUserActive('R',maximum,True)
        elif counter=='membership':noop=b.PutMembership('R',c.ScopeKey('organization','O'),b.Version(maximum),'standard')
        else:noop=b.SetAssignments('G',maximum if counter=='authority_version' else 4,maximum if counter=='epoch' else 7,('P','Q','I'),False)
        assert not apply(db,noop).visible.changed and snapshot(db.raw)==before


def test_complete_raw_failure_receipt_and_external_files_preserved(path,tmp_path,monkeypatch):
    # Small owned external witnesses. B2 receives only the existing hub handle.
    external={tmp_path/'config.toml':b'[owned]\nvalue = "unaltered"\n',
              tmp_path/'company.db':b'SQLite format 3\x00owned-company-sentinel',
              tmp_path/'attachment.bin':b'\x00\xffunchanged-attachment'}
    for target,data in external.items():target.write_bytes(data)
    with open_database(path,writable=True) as db:
        db.raw.execute('CREATE TABLE owned_preservation (raw_blob BLOB, raw_real REAL, raw_text TEXT)')
        db.raw.execute('INSERT INTO owned_preservation VALUES (?, ?, ?)',(b'\x00\xff',0.125,'NUL\x00text'))
        db.raw.execute('CREATE TEMP TABLE local_preservation (raw BLOB)')
        db.raw.execute('INSERT INTO local_preservation VALUES (?)',(b'\xff\x00',))
        db.raw.execute('BEGIN IMMEDIATE');before=snapshot(db.raw);local=snapshot(db.raw,schema='temp')
        insert=au.insert_audit;reached=[]
        def fail_after_insert(*args):
            insert(*args);reached.append(True);raise RuntimeError('owned post-insert abort')
        monkeypatch.setattr(au,'insert_audit',fail_after_insert)
        with pytest.raises(RuntimeError,match='owned post-insert abort'):apply(db,b.SetAssignments('G',4,7,('Q','I'),False))
        after=snapshot(db.raw);local_after=snapshot(db.raw,schema='temp')
        assert reached==[True] and before==after and local==local_after
        actual={str(target):target.read_bytes() for target in external}
        assert actual=={str(target):data for target,data in external.items()}
        receipt(tmp_path/'complete-raw-preservation.json',expected_main=before,observed_main=after,expected_temp=local,observed_temp=local_after,expected_external={str(target):data for target,data in external.items()},observed_external=actual)
