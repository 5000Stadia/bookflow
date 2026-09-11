#!/usr/bin/env python3
"""Resolve a Bookflow integration merge by RECOMPUTING every pinned value.

Never resolves a keyed list to a side: unions it. Never keeps a derived value from
either branch: measures it from the merged tree. Run from the integration worktree.
"""
import ast, pathlib, re, subprocess, sys

ROOT = pathlib.Path('.').resolve()
CONFLICT = re.compile(r'<<<<<<< HEAD\n(?P<head>.*?)\n=======\n(?P<theirs>.*?)\n>>>>>>> [^\n]+\n', re.S)


def union_line_conflicts(path):
    """A conflict whose two sides are lines of one sorted, keyed list -> union them."""
    p = pathlib.Path(path)
    if not p.exists():
        return 0
    s, n = p.read_text(), 0

    def repl(m):
        nonlocal n
        head = [l for l in m.group('head').split('\n') if l.strip()]
        theirs = [l for l in m.group('theirs').split('\n') if l.strip()]
        n += 1
        return '\n'.join(sorted(set(head + theirs))) + '\n'

    p.write_text(CONFLICT.sub(repl, s))
    return n


def scan_require_resource_sites():
    sites = {}
    for path in sorted((ROOT / 'src/bookflow/company').rglob('*.py')):
        tree = ast.parse(path.read_text())
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not ((isinstance(fn, ast.Name) and fn.id == 'require_resource')
                    or (isinstance(fn, ast.Attribute) and fn.attr == 'require_resource')):
                continue
            a, names = node, []
            while a in parents:
                a = parents[a]
                if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.append(a.name)
            key = '.'.join(path.relative_to(ROOT / 'src').with_suffix('').parts) + '.' + '.'.join(reversed(names))
            sites.setdefault(key, set()).add((str(path.relative_to(ROOT)), node.lineno))
    return sites


def fix_call_sites():
    sites = scan_require_resource_sites()
    p = ROOT / 'src/bookflow/hub/permission_catalog.py'
    s, fixed = p.read_text(), 0

    def repl(m):
        nonlocal fixed
        owner = m.group('owner')
        if owner not in sites:
            return m.group(0)
        new = '(' + ''.join(f"({f!r}, {l})," for f, l in sorted(sites[owner])) + ')'
        if new != m.group('cs'):
            fixed += 1
        return m.group(0).replace(m.group('cs'), new, 1)

    p.write_text(re.sub(
        r"ResourceSource\(owner='(?P<owner>[^']+)', call_sites=(?P<cs>\(\([^)]*\),?\)|\(\([^)]*\),\)), ",
        repl, s))
    return fixed


def regenerate_manifest():
    from bookflow.hub import permission_catalog as c
    m = c.catalog_manifest(c.FROZEN_CATALOG, c.FROZEN_MANIFEST.standalone_names)
    p = ROOT / 'src/bookflow/hub/permission_catalog.py'
    p.write_text(re.sub(r'^FROZEN_MANIFEST = CatalogManifest\(.*$',
                        lambda _: 'FROZEN_MANIFEST = ' + repr(m), p.read_text(), count=1, flags=re.M))
    t = ROOT / 'tests/test_permission_catalog.py'
    t.write_text(re.sub(r"FROZEN_DESCRIPTOR_SHA256 = '[0-9a-f]+'",
                        f"FROZEN_DESCRIPTOR_SHA256 = '{m.descriptor_sha256}'", t.read_text(), count=1))
    return len(m.command_names), m.descriptor_sha256


def regenerate_counts():
    sys.path.insert(0, str(ROOT))
    for mod in [k for k in sys.modules if k.startswith('bookflow')]:
        del sys.modules[mod]
    from tests.mcp_coverage import execution_map
    rows = execution_map()
    n = len(rows)
    four = sum(r['coverage'] == 'four_surface_scenario' for r in rows)
    local = sum(r['coverage'] == 'local_lifecycle_scenario' for r in rows)
    p = ROOT / 'tests/test_mcp_coverage.py'
    s = p.read_text()
    s = re.sub(r'assert len\(rows\) == \d+', f'assert len(rows) == {n}', s, count=1)
    s = re.sub(r'assert sum\(row\["coverage"\] == "four_surface_scenario" for row in rows\) == \d+',
               f'assert sum(row["coverage"] == "four_surface_scenario" for row in rows) == {four}', s, count=1)
    s = re.sub(r"assert sum\(row\['coverage'\] == 'local_lifecycle_scenario' for row in rows\) == \d+",
               f"assert sum(row['coverage'] == 'local_lifecycle_scenario' for row in rows) == {local}", s, count=1)
    p.write_text(s)
    return n, four, local


def parse_sweep():
    bad = []
    out = subprocess.run(['git', 'diff', '--name-only', 'HEAD', '--', '*.py'],
                         capture_output=True, text=True).stdout.split()
    for f in out:
        fp = ROOT / f
        if not fp.exists():
            continue
        try:
            ast.parse(fp.read_text())
        except SyntaxError as e:
            bad.append(f'{f}:{e.lineno} {e.msg}')
    return bad


if __name__ == '__main__':
    for f in ['tests/mcp_coverage.py', 'src/bookflow/hub/permission_catalog.py']:
        print(f'unioned {union_line_conflicts(f)} hunk(s) in {f}')
    left = subprocess.run(['grep', '-rln', '^<<<<<<<', 'src', 'tests'],
                          capture_output=True, text=True).stdout.split()
    if left:
        print('MANUAL CONFLICTS REMAIN:', *left, sep='\n  ')
        sys.exit(1)
    print('call-site tuples corrected:', fix_call_sites())
    print('manifest: %d commands, sha %s' % regenerate_manifest())
    print('execution ledger: total=%d four_surface=%d local=%d' % regenerate_counts())
    bad = parse_sweep()
    print('PARSE FAILURES:', bad) if bad else print('parse sweep clean')
