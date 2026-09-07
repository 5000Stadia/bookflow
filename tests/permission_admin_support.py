"""Small explicit conditional policy_v1 fixtures, not an OS/visibility producer."""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import hashlib
import json
from bookflow.hub import identity_admin as b, permission_snapshot as s, permission_catalog as c, credentials
from bookflow.hub.permission_admin_audit import AuditContext
from bookflow.storage.engine import open_database
from bookflow.storage.migrate import migrate_to_head
from tests.test_permission_policy import catalog as small_catalog
from tests.test_permission_snapshots import SuppliedVisibility
from tests.permission_storage_support import snapshot

OLD='2026-01-01T00:00:00Z';AT='2026-09-07T05:00:00Z';REVOKED='2026-01-02T00:00:00Z'
CATALOG=small_catalog()  # Complete explicit TEST build, never a partial production inventory claim.
BUNDLE=s.CatalogBundle('f'*40,CATALOG,(),'e'*64)
CONTEXT=AuditContext(AT,'cli','owned-b2','1','owned','SESSION','REQUEST',reason='authorized edit')
VISIBILITY=SuppliedVisibility(True)  # Explicit supplied test policy only.


def insert(raw,table,values):
    raw.execute('INSERT INTO main.'+table+' ('+','.join('"'+x+'"' for x in values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))


def make_root(path):
    with open_database(path,writable=True,create=True) as db:
        migrate_to_head(db,'hub',None)
        common=dict(version=5,created_at=OLD,created_by='H',created_via='cli',updated_at=OLD,updated_by='H',updated_via='cli')
        for identifier,kind,active,admin in [('H','human',1,1),('A','human',1,0),('W','human',1,0),('P','human',1,0),('Q','human',1,0),('R','human',1,0),('I','human',0,0),('RO','human',1,0),('B','human',1,0),('G','agent',1,0),('J','agent',1,0),('U','human',1,0),('S','system',1,0)]:
            insert(db.raw,'users',dict(id=identifier,**common,kind=kind,username=identifier,display_name=identifier,owner_user_id='H' if kind=='agent' else None,password_hash='private-password-'+identifier,hub_admin=admin,active=active))
        for identifier in ('O','Z'):
            insert(db.raw,'organizations',dict(id=identifier,**common,display_name=identifier,name_key=identifier,path=identifier,is_demo=0))
        for identifier,parent in [('C','O'),('D','O'),('E','Z')]:
            insert(db.raw,'companies',dict(id=identifier,**common,organization_id=parent,display_name=identifier,name_key=identifier,path=identifier,legal_name=identifier,home_currency='USD',schema_revision='co0018',is_demo=0))
        for user,role,org in [('A','admin','O'),('W','owner','O'),('P','standard','O'),('Q','standard','O'),('R','standard','O'),('I','standard','O'),('RO','readonly','O'),('B','standard','O'),('G','standard','O'),('J','standard','Z'),('U','standard','Z')]:
            insert(db.raw,'memberships',dict(id='M-'+user,user_id=user,scope_type='organization',scope_id=org,role=role,grants=None,denies='[]',granted_by='H',granted_at=OLD,revoked_at=None,version=1))
        for agent,epoch,version in [('G',7,4),('J',3,2)]:
            insert(db.raw,'agent_authority',dict(agent_user_id=agent,epoch=epoch,version=version,authorized_at=OLD,authorized_by='H',permitted_use_at=OLD))
        for agent,person in [('G','P'),('G','Q'),('G','I'),('J','U')]:
            insert(db.raw,'agent_principals',dict(agent_user_id=agent,principal_user_id=person,assigned_by='H',assigned_at=OLD,revoked_at=None))
        for person in ('H','A','W','P','Q','R','RO','B','U'):
            insert(db.raw,'api_tokens',dict(id='T-'+person,**common,user_id=person,on_behalf_of=None,kind='bearer',token_hash=credentials.token_hash('secret-'+person),label='private-label',expires_at=None,revoked_at=None,authority_epoch=None,last_used_at=OLD))
        for identifier,user,person,kind,epoch,expires,revoked in [
            ('GP-live','G','P','bearer',7,'2999-01-01T00:00:00Z',None),
            ('GQ-live','G','Q','session',7,'2999-01-01T00:00:00Z',None),
            ('GP-expired','G','P','bearer',7,'2000-01-01T00:00:00Z',None),
            ('GQ-old','G','Q','bearer',6,None,None),
            ('GI-null','G','I','session',None,None,None),
            ('GP-revoked','G','P','bearer',7,None,REVOKED),
            ('JU-live','J','U','session',3,'2999-01-01T00:00:00Z',None)]:
            insert(db.raw,'api_tokens',dict(id=identifier,**common,user_id=user,on_behalf_of=person,kind=kind,token_hash=credentials.token_hash('secret-'+identifier),label='private-label',expires_at=expires,revoked_at=revoked,authority_epoch=epoch,last_used_at=OLD))
        db.raw.execute('DELETE FROM role_capabilities')
        for entry in CATALOG.defaults:insert(db.raw,'role_capabilities',dict(role=entry.role,capability=entry.requirement.capability,required_role=entry.requirement.threshold))
        encoded=s.encode_catalog(CATALOG);manifest=c.catalog_manifest(CATALOG)
        db.raw.execute("UPDATE permission_state SET mode='policy_v1',catalog_version=?,catalog_sha256=?,catalog_json=?",(CATALOG.version,manifest.descriptor_sha256,encoded))
    return path


def binding(path,person='H',request='REQUEST'):
    return b.TokenBinding('secret-'+person,'T-'+person,person,'bearer',None,path,request)


def apply(db,intent,person='H',visibility=VISIBILITY,context=CONTEXT,catalog=BUNDLE):
    return b.apply_edit(db,binding=binding(db.path,person,context.request_id),intent=intent,catalog=catalog,visibility=visibility,audit=context)


def rows(raw,table):
    names=tuple(x[1] for x in raw.execute('PRAGMA main.table_info('+table+')'))
    return tuple(dict(zip(names,row)) for row in raw.execute('SELECT '+','.join('"'+x+'"' for x in names)+' FROM main.'+table+' ORDER BY rowid'))


def tokens(raw):return {x['id']:x for x in rows(raw,'api_tokens')}


def expected_tokens(before,revoked_ids):
    # The caller supplies a literal independently expected ID set, never A/B2 results.
    return {key:dict(value,version=value['version']+1,revoked_at=AT,updated_at=AT,updated_by='H',updated_via='cli') if key in revoked_ids else value for key,value in before.items()}


G_REVOKED={'GP-live','GQ-live','GP-expired','GQ-old','GI-null'}


def receipt(path,**data):
    def encode(value):
        if isinstance(value,bytes):return {'hex':value.hex()}
        raise TypeError(type(value).__name__)
    path.write_text(json.dumps(data,default=encode,indent=2)+'\n')
