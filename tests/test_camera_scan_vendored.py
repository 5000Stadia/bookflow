"""R190: the camera scanner's vendored decoder files and the scan_decoders host setting.

Every vendored file is listed in its folder's VENDORED.md with the SHA-256 it must have, and nothing else is in the
folder; the adapters fetch nothing from outside the host (no CDN: the page works on a LAN with no internet); and the
decoder order resolves from the flag, else BOOKFLOW_SCAN_DECODERS, else native,zxing-wasm, refusing unknown names.
"""
import hashlib
import re
from pathlib import Path

import pytest

from bookflow.commands.host_cmds import ServeInput
from bookflow.core import scan_decoders
from bookflow.core.errors import BookflowError

STATIC = Path(__file__).resolve().parents[1] / 'src' / 'bookflow' / 'adapters' / 'workbench' / 'static'
VENDOR = STATIC / 'vendor'
ROW = re.compile(r'^\| `([^`]+)` \| `[^`]+` \| `([0-9a-f]{64})` \|$', re.M)


def test_every_vendored_file_has_the_hash_its_vendored_md_gives():
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
