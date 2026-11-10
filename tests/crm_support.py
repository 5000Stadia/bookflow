"""Minimal CRM company fixture; optionally reuse a verified, sealed permission cache."""
import os
from pathlib import Path
import shutil
import bookflow


def company(tmp_path, monkeypatch, zone='UTC'):
    root = tmp_path / 'books'
    template = os.environ.get('BOOKFLOW_TEST_CRM_ROOT')
    if template:
        shutil.copytree(Path(template), root)
        monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
        monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
        client = bookflow.connect(data_root=str(root))
        identifier = client.company.list()['items'][0]['id']
        current = client.run('company show', {}, company=identifier)
        if current['info']['timezone'] != zone:
            client.run('company update', {'timezone': zone, 'expected_version': current['info_version']}, company=identifier, reason='Set fixture timezone')
        return type('Books', (), {'client': client, 'company': identifier, 'root': root,
            'run': staticmethod(lambda name, raw=None, **context: client.run(name, raw or {}, company=identifier, **context))})
    cache = os.environ.get('BOOKFLOW_TEST_CATALOG_CACHE')
    if cache:
        # This is the normal runtime's sealed cache, not a bypass of verification.
        # Source hashes and accepted descriptor pins are checked on every load.
        shutil.copytree(Path(cache), root / 'cache')
    monkeypatch.setenv('BOOKFLOW_DATA_ROOT', str(root))
    monkeypatch.delenv('BOOKFLOW_COMPANY', raising=False)
    client = bookflow.connect(data_root=str(root))
    client.init()
    client.organization.new(name='CRM organization')
    identifier = client.company.new(legal_name='CRM books', home_currency='USD', timezone=zone,
        organization='CRM organization', chart='none')['company_id']
    return type('Books', (), {'client': client, 'company': identifier, 'root': root,
        'run': staticmethod(lambda name, raw=None, **context: client.run(name, raw or {}, company=identifier, **context))})
