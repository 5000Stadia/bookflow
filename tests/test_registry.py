from bookflow.core import registry
from bookflow.core.context import CONTEXT_FIELD_NAMES
from bookflow.core.errors import ALL_CODES


def test_every_command_is_complete():
    registry.load_all()
    cmds = registry.all_commands()
    assert {c.name for c in cmds} >= {"init", "upgrade", "organization new", "organization list", "organization show", "organization rename",
                                     "company new", "company list", "company use", "company attach", "company detach", "company show",
                                     "company rename", "demo reset", "hub audit list", "hub audit show"}
    for c in cmds:
        assert c.description.endswith(".")
        assert c.scope in ("hub", "company")
        assert not (set(c.input_model.model_fields) & CONTEXT_FIELD_NAMES)
        for code in c.error_codes:
            assert code in ALL_CODES
        for pos in c.positional:
            assert pos in c.input_model.model_fields
        if c.is_write and not c.bootstrap:
            assert c.apply is not None


def test_noun_index_matches_modules():
    import importlib
    from bookflow.core.registry import NOUN_MODULES, REGISTRY
    for module, nouns in NOUN_MODULES.items():
        importlib.import_module(module)
    by_module = {}
    for cmd in REGISTRY.values():
        by_module.setdefault(cmd.plan.__module__, set()).add(cmd.noun)
    for module, nouns in NOUN_MODULES.items():
        assert by_module.get(module, set()) == set(nouns), (module, by_module.get(module), nouns)


def test_multi_noun_modules_load_incrementally_for_one_cli_target():
    """A cold CLI builds one noun, while later in-process loads remain complete."""

    import subprocess
    import sys

    witness = """
import sys
from bookflow.core import registry

registry.load_all('customer list')
assert 'customer show' in registry.REGISTRY
assert 'vendor show' not in registry.REGISTRY
assert 'term show' not in registry.REGISTRY
assert 'bookflow.commands.query_cmds' not in sys.modules

registry.load_all('customer query')
assert 'customer query' in registry.REGISTRY
assert 'vendor query' not in registry.REGISTRY

registry.load_all('vendor list')
assert 'vendor show' in registry.REGISTRY
assert 'employee show' not in registry.REGISTRY
assert 'vendor query' not in registry.REGISTRY

registry.load_all('term list')
assert 'term show' in registry.REGISTRY
assert 'payment-method show' not in registry.REGISTRY
assert 'term query' not in registry.REGISTRY

registry.load_all('term')
assert 'term query' in registry.REGISTRY

registry.load_all()
assert 'employee show' in registry.REGISTRY
assert 'payment-method show' in registry.REGISTRY
assert 'payment-method query' in registry.REGISTRY
"""
    completed = subprocess.run(
        [sys.executable, "-c", witness],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
