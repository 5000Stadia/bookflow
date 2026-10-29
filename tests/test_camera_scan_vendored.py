"""R190: the camera scanner's vendored decoder files and the scan_decoders host setting.

Every vendored file is listed in its folder's VENDORED.md with the SHA-256 it must have, and nothing else is in the
folder; the adapters fetch nothing from outside the host (no CDN: the page works on a LAN with no internet); and the
decoder order resolves from the flag, else BOOKFLOW_SCAN_DECODERS, else native,zxing-wasm, refusing unknown names.
"""
import hashlib
import http.client
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from bookflow.commands.host_cmds import ServeInput
from bookflow.core import scan_decoders
from bookflow.core.errors import BookflowError
from tests.test_row5_browser_acceptance import browser_site  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / 'src' / 'bookflow' / 'adapters' / 'workbench' / 'static'
VENDOR = STATIC / 'vendor'
ROW = re.compile(r'^\| `([^`]+)` \| `[^`]+` \| `([0-9a-f]{64})` \|$', re.M)


def test_every_vendored_file_has_the_hash_its_vendored_md_gives():
    # Everything the vendor route could serve sits in a <package>/<version> folder that has a VENDORED.md listing it.
    for path in VENDOR.rglob('*'):
        if path.is_dir():
            continue
        parts = path.relative_to(VENDOR).parts
        assert len(parts) == 3, f'{path} is not in a <package>/<version> folder'
        assert (path.parent / 'VENDORED.md').is_file(), f'{path.parent} has no VENDORED.md'
    folders = sorted(p.parent for p in VENDOR.glob('*/*/VENDORED.md'))
    assert [f.relative_to(VENDOR).as_posix() for f in folders] == ['zxing-wasm/3.1.5']
    for folder in folders:
        listed = dict(ROW.findall((folder / 'VENDORED.md').read_text()))
        assert listed, folder
        present = {p.name for p in folder.iterdir() if p.is_file()} - {'VENDORED.md'}
        assert present == set(listed), (present, set(listed))
        for name, digest in listed.items():
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest, name
        assert (folder / 'LICENSE').exists()


def test_the_reader_names_the_wasm_it_was_built_with():
    folder = VENDOR / 'zxing-wasm' / '3.1.5'
    declared = re.search(r'ZXING_WASM_SHA256=`([0-9a-f]{64})`', (folder / 'zxing-reader.js').read_text()).group(1)
    assert hashlib.sha256((folder / 'zxing_reader.wasm').read_bytes()).hexdigest() == declared


def test_the_camera_files_fetch_nothing_from_outside_the_host():
    for name in ('camera-scan.js', 'scan-decoder-native.js', 'scan-decoder-zxing-wasm.js', 'camera-scan.css'):
        text = (STATIC / name).read_text()
        assert not re.search(r'https?://', text), name
    adapter = (STATIC / 'scan-decoder-zxing-wasm.js').read_text()
    # The module's default locateFile points at a CDN; the adapter replaces it and instantiates the wasm itself.
    assert "BASE = '/static/vendor/zxing-wasm/3.1.5/'" in adapter
    assert 'locateFile' in adapter and 'instantiateWasm' in adapter and 'arrayBuffer()' in adapter


def test_the_decoder_order_comes_from_the_flag_then_the_environment_then_the_default(monkeypatch):
    monkeypatch.delenv('BOOKFLOW_SCAN_DECODERS', raising=False)
    assert scan_decoders.resolve(None) == 'native,zxing-wasm'
    monkeypatch.setenv('BOOKFLOW_SCAN_DECODERS', ' ZXING-WASM ')
    assert scan_decoders.resolve(None) == 'zxing-wasm'
    assert scan_decoders.resolve('zxing-wasm , native') == 'zxing-wasm,native'
    for bad, problem in (('quagga2', 'unknown decoder'), ('native,native', 'named twice'), (' , ', 'at least one'),
                         ('../x', 'unknown decoder')):
        with pytest.raises(BookflowError) as refused:
            scan_decoders.normalize(bad)
        assert refused.value.code == 'E_VALIDATION'
        assert problem in refused.value.details['fields'][0]['problem']
    monkeypatch.setenv('BOOKFLOW_SCAN_DECODERS', 'nope')
    with pytest.raises(BookflowError) as refused:
        scan_decoders.resolve(None)
    assert refused.value.details['fields'][0]['field'] == 'BOOKFLOW_SCAN_DECODERS'
    assert ServeInput(scan_decoders='zxing-wasm').scan_decoders == 'zxing-wasm'
    # Each name the host accepts is one adapter file the Scan field loads.
    for name in scan_decoders.KNOWN:
        assert f"name: '{name}'" in (STATIC / f'scan-decoder-{name}.js').read_text()


def test_serve_takes_the_flag_through_the_same_check(tmp_path):
    """`bookflow serve --scan-decoders` is resolved before anything else is opened: a bad name is refused, a good one
    goes on (here to the empty data root's refusal)."""
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
    env.pop('BOOKFLOW_SCAN_DECODERS', None)

    def serve(*args):
        run = subprocess.run([sys.executable, '-c', 'from bookflow.bootstrap import main; main()', 'serve',
                              '--data-root', str(tmp_path), '--json', *args], capture_output=True, text=True,
                             env=env, timeout=120)
        return run.stdout + run.stderr

    refused = serve('--scan-decoders', 'quagga2')
    assert 'E_VALIDATION' in refused and 'unknown decoder' in refused, refused
    accepted = serve('--scan-decoders', 'zxing-wasm')
    assert 'E_VALIDATION' not in accepted and 'E_NOT_INITIALIZED' in accepted, accepted


def _get(base_url, path):
    where = urlsplit(base_url)
    connection = http.client.HTTPConnection(where.hostname, where.port, timeout=30)
    connection.putrequest('GET', path, skip_accept_encoding=True)   # the path goes out exactly as written
    connection.endheaders()
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, response.getheader('content-type'), body


def test_the_static_routes_serve_only_listed_kinds_of_regular_files(browser_site):
    base = browser_site.base_url
    folder = VENDOR / 'zxing-wasm' / '3.1.5'
    status, media, body = _get(base, '/static/vendor/zxing-wasm/3.1.5/zxing_reader.wasm')
    assert (status, media) == (200, 'application/wasm') and body[:4] == b'\0asm'
    assert _get(base, '/static/vendor/zxing-wasm/3.1.5/zxing-reader.js')[:2] == (200, 'application/javascript')
    assert _get(base, '/static/vendor/zxing-wasm/3.1.5/LICENSE')[0] == 200
    made = [folder / 'notes.txt', folder / 'tool.py', folder / 'README', folder / 'linked.js', VENDOR / 'zxing-wasm' / 'linked']
    try:
        for path in made[:3]:
            path.write_text('x')
        made[3].symlink_to(STATIC / 'scan.js')
        made[4].symlink_to(folder, target_is_directory=True)
        for path in ('/static/vendor/zxing-wasm/3.1.5/notes.txt', '/static/vendor/zxing-wasm/3.1.5/tool.py',
                     '/static/vendor/zxing-wasm/3.1.5/README',                       # suffixes not on the list
                     '/static/vendor/zxing-wasm/3.1.5/linked.js',                    # a link to a file
                     '/static/vendor/zxing-wasm/linked/zxing-reader.js',              # a link on the way
                     '/static/vendor/zxing-wasm/3.1.5/..%2F..%2F..%2Fscan.js',       # encoded separators
                     '/static/vendor/zxing-wasm/3.1.5/..%5Cscan.js',
                     '/static/vendor/zxing-wasm/3.1.5/%2e%2e',
                     '/static/vendor/zxing-wasm/%2e%2e/%2e%2e',
                     '/static/vendor/zxing-wasm/3.1.5/../../../scan.js',
                     '/static/vendor/zxing-wasm/3.1.5/',
                     '/static/vendor/zxing-wasm/3.1.5/.hidden.js',
                     # the flat route: folders and parents are not files
                     '/static/vendor', '/static/..', '/static/%2e%2e', '/static/.', '/static/vendor%2Fzxing-wasm'):
            status, _, body = _get(base, path)
            assert status == 404, (path, status, body[:80])
    finally:
        for path in made:
            if path.is_symlink() or path.exists():
                path.unlink()
    assert _get(base, '/static/scan.js')[0] == 200
