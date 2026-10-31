"""Public tracked content uses neutral accounting-product descriptions."""
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_PATHS = ('src', 'docs', 'design', 'README.md', 'tools', 'tests')
# Compressed binaries (fonts, images, wasm) hold arbitrary bytes that can spell two letters by chance.
OPAQUE_SUFFIXES = ('.ttf', '.otf', '.woff', '.woff2', '.wasm', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf')
PATTERN = re.compile(r'quickbooks|(?<![a-z])(?:intuit|qbo|qbd|qb)(?![a-z])', re.IGNORECASE)
# No product-name/report-title exceptions are needed. This exact extension matcher
# must keep recognizing bank-download files; it is a format identifier, not prose.
# Build the suffix from bytes so the guard's own allowlist does not repeat the name.
MATCHER_LITERALS = {
    'src/bookflow/commands/attachment_cmds.py': ("'" + bytes((46, 113, 98, 111)).decode() + "'",),
}


def test_no_anchor_name_in_tracked_public_files():
    tracked = subprocess.check_output(
        ['git', 'ls-files', '-z', '--', *PUBLIC_PATHS], cwd=ROOT,
    ).decode().split('\0')
    violations = []
    seen = set()
    for name in filter(None, tracked):
        if name.lower().endswith(OPAQUE_SUFFIXES):
            continue
        # Latin-1 preserves every byte, including ASCII names in binary fixtures.
        content = (ROOT / name).read_bytes().decode('latin-1')
        for literal in MATCHER_LITERALS.get(name, ()):
            assert content.count(literal) == 1, f'Stale or expanded matcher exception: {name}'
            assert any(line.startswith('TEXT_EXTENSIONS = frozenset((') and literal in line
                       for line in content.splitlines()), f'Exception is outside matcher data: {name}'
            content = content.replace(literal, '"bank-format"', 1)
            seen.add(name)
        for number, line in enumerate(content.splitlines(), 1):
            # Only this test's pattern declaration may spell the forbidden names.
            if name == 'tests/test_no_anchor_name.py' and line.startswith('PATTERN = re.compile('):
                continue
            if PATTERN.search(line) or PATTERN.search(name):
                violations.append(f'{name}:{number}: {line}')
    assert seen == set(MATCHER_LITERALS), 'Matcher allowlist must refer to tracked public files'
    assert not violations, 'Anchor references in public files:\n' + '\n'.join(violations)
