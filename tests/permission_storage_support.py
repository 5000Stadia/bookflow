"""Small ordinary owned hub fixtures and exact storage observations for B1."""
import hashlib
import struct
from pathlib import Path
from alembic import command
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import _config


def create_hub(path, revision='hub0011', *, suspended=True, malformed=False):
    with open_database(path, writable=True, create=True) as db:
        command.upgrade(_config('hub',db.conn),revision)
        raw=db.raw
        common=dict(version=7,created_at='2026-01-01',created_by='H',created_via='cli',updated_at='2026-01-02',updated_by='H',updated_via='http')
        def insert(table,values):
            raw.execute('INSERT INTO '+table+' ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
        for identifier,kind,active,admin in [('H','human',1,1),('Q','human',1,0),('I','human',0,0),('G','agent',1,0),('J','agent',0,0),('S','system',1,0)]:
            insert('users',dict(id=identifier,**common,kind=kind,username=identifier,display_name=identifier,owner_user_id='H' if kind=='agent' else None,password_hash='private-password-hash' if kind=='human' else None,hub_admin=admin,active=active))
        for identifier in ('O','Z'):
            insert('organizations',dict(id=identifier,**common,display_name=identifier,name_key=identifier,path=identifier,is_demo=0))
        for identifier,organization in [('C','O'),('D','O'),('E','Z')]:
            insert('companies',dict(id=identifier,**common,organization_id=organization,display_name=identifier,name_key=identifier,path=identifier,legal_name=identifier,home_currency='USD',schema_revision='co0017',is_demo=0))
        for identifier,user,scope,target,revoked in [('M','H','organization','O',None),('N','Q','company','C',None),('P','G','company','C',None),('R','I','company','E','2026-01-03')]:
            insert('memberships',dict(id=identifier,user_id=user,scope_type=scope,scope_id=target,role='standard',grants='not-json\0raw' if malformed else None,denies='[]',granted_by='H',granted_at='2026-01-01',revoked_at=revoked))
        for agent in ('G','J'):
            insert('agent_authority',dict(agent_user_id=agent,epoch=9 if agent=='G' else 2,suspended_at='2026-01-03' if suspended else None,suspension_reason='old reason' if suspended else None))
        for principal,revoked in [('H',None),('I',None),('Q','2026-01-02')]:
            insert('agent_principals',dict(agent_user_id='G',principal_user_id=principal,assigned_by='H',assigned_at='2026-01-01',revoked_at=revoked))
        for identifier,user,principal,revoked in [('T','G','H',None),('U','G','Q','2026-01-02'),('V','H',None,None)]:
            insert('api_tokens',dict(id=identifier,**common,user_id=user,on_behalf_of=principal,kind='bearer',token_hash=identifier*64,label='private',expires_at='2020-01-01',revoked_at=revoked,authority_epoch=9 if user=='G' else None))
        raw.execute("DELETE FROM role_capabilities WHERE role='standard' AND capability='ledger.post'")
        raw.execute('ALTER TABLE memberships ADD COLUMN local_extension BLOB')
        raw.execute('UPDATE memberships SET local_extension=?',(b'\x00\xffopaque',))
        raw.execute('ALTER TABLE agent_authority ADD COLUMN local_extension TEXT')
        raw.execute('UPDATE agent_authority SET local_extension=?',('kept\0suffix',))
        raw.execute('CREATE TABLE local_records (label TEXT, payload BLOB, value)')
        raw.execute('INSERT INTO local_records(rowid,label,payload,value) VALUES (77,?,?,?)',('text\0suffix',b'\0\x80\xff',1.25))
        raw.execute('CREATE INDEX local_records_label ON local_records(label)')
        raw.execute('CREATE VIEW local_view AS SELECT rowid,label,payload,value FROM local_records')
        raw.execute('CREATE TABLE local_counter (value INTEGER)')
        raw.execute('INSERT INTO local_counter VALUES (0)')


def quote(name):return '"'+name.replace('"','""')+'"'


def snapshot(raw, columns=None):
    ddl=tuple(raw.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
    selected=columns or {name:tuple(x[1] for x in raw.execute('PRAGMA table_xinfo('+quote(name)+')')) for kind,name,_,_ in ddl if kind=='table'}
    tables={}
    for name,cols in selected.items():
        expressions=['rowid']
        for col in cols:expressions.extend(('typeof('+quote(col)+')',quote(col),'CAST('+quote(col)+' AS BLOB)'))
        rows=[]
        for row in raw.execute('SELECT '+','.join(expressions)+' FROM '+quote(name)+' ORDER BY rowid'):
            rows.append(tuple(('float64',struct.pack('>d',x)) if type(x) is float else x for x in row))
        tables[name]=tuple(rows)
    return dict(ddl=ddl,columns=selected,tables=tables)


def digest(snapshot):return hashlib.sha256(repr(snapshot).encode()).hexdigest()
