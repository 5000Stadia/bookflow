"""Every command an error hint or warning tells a caller to run is a command the registry has.

A refusal that points at a command that does not exist sends an agent down a path that cannot be
followed (the blind July trial: `payment void` on a deposited receipt said `deposit coordinate`).
This reads the source, not the running refusals, so it covers every hint, not only the ones a test
happens to trigger: a backticked command in any message string, the value of a `next` detail, and
the command named in a `held_next(..., 'command')` call.
"""
import ast
import re
from pathlib import Path

from bookflow.core import registry

SOURCE = Path(__file__).resolve().parent.parent / 'src' / 'bookflow'
SHAPE = re.compile(r'[a-z][a-z-]*( [a-z][a-z-]*){1,3}')
PLACEHOLDER = re.compile(r'[<>/*|]')


def _strings(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node, node.value


def _is_command(text, names):
    """The words name a command, or a group of them (`bill payment`), or start with one (`reconcile mark --all`)."""
    words = text.split()
    return (any(' '.join(words[:k]) in names for k in range(1, min(4, len(words)) + 1))
            or any(name.startswith(text + ' ') for name in names))


def unknown_commands(source=SOURCE):
    registry.load_all()
    names = {c.name for c in registry.all_commands(include_standalone=True)}
    nouns = {name.split()[0] for name in names}
    found = []
    for path in sorted(source.rglob('*.py')):
        if any('migrations' in part for part in path.parts) or path.name.startswith('permission_'):
            continue
        tree = ast.parse(path.read_text())
        where = lambda node: f'{path.relative_to(source)}:{node.lineno}'
        for node, value in _strings(tree):
            for segment in re.findall(r'`([^`\n]+)`', value):
                words = segment.split()
                if (len(words) >= 2 and words[0] in nouns and not PLACEHOLDER.search(segment)
                        and not _is_command(segment, names)):
                    found.append((where(node), segment))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value == 'next' and isinstance(value, ast.Constant)
                            and isinstance(value.value, str) and SHAPE.fullmatch(value.value)
                            and value.value.split()[0] in nouns and not _is_command(value.value, names)):
                        found.append((where(value), value.value))
            if (isinstance(node, ast.Call) and getattr(node.func, 'id', '') == 'held_next'
                    and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value not in names):
                found.append((where(node), node.args[1].value))
    return found


def test_every_command_a_hint_names_exists():
    assert unknown_commands() == []


def test_the_walk_finds_a_command_that_does_not_exist(tmp_path):
    """Negative control: the checker is not blind to the failure it exists for."""
    (tmp_path / 'hints.py').write_text(
        "A = {'next': 'deposit coordinate'}\n"
        "B = 'Run `company verify` first, then `reconcile mark --all` and `bill payment unapply`.'\n"
        "C = held_next('payment', 'payment cancel')\n"
        "D = {'next': 'deposit delete'}\n")
    assert sorted(text for _, text in unknown_commands(tmp_path)) == ['company verify', 'deposit coordinate', 'payment cancel']
