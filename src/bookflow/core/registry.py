"""The command registry. Every adapter is generated from it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from bookflow.core.context import CONTEXT_FIELD_NAMES
from bookflow.core.errors import ALL_CODES

Role = str  # "member", "admin", "owner", "hub_admin", or None for any actor


@dataclass
class Touched:
    record_type: str
    record_id: str
    action: str  # create, update, delete, deactivate, migrate, baseline
    version_before: int | None
    version_after: int | None
    after: dict[str, Any] | None
    before: dict[str, Any] | None = None
    db: str = "hub"  # which database's event this entry belongs to: "hub" or "company"


@dataclass
class Plan:
    """What `apply` will do. ``preview`` is the output a real run would return."""

    preview: BaseModel
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Applied:
    output: BaseModel
    touched: list[Touched]
    summary: str
    audited: bool = False  # True when apply wrote its own audit events
    after_commit: Callable[[], None] | None = None  # runs once the command's own transactions are durable (demo seed history)


@dataclass
class Command:
    name: str
    scope: str  # "hub" or "company"
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    plan: Callable[..., Plan]
    apply: Callable[..., Applied] | None
    writes: frozenset[str] = frozenset()  # subset of {"hub", "company", "config"}
    required_role: Role | None = None
    positional: list[str] = field(default_factory=list)
    error_codes: list[str] = field(default_factory=list)
    bootstrap: bool = False
    kind: str = "read"  # read, write, advisory; derived from writes unless advisory is declared
    truth: str = "hub"  # the database whose transaction commits first and holds the idempotency row
    accepts_idempotency_key: bool = False
    clearable: bool = False  # update commands whose fields may be set to null (--clear)
    streams: bool = False  # commands with a CLI-only --follow form
    ledger: bool = False
    capability: str | None = ""  # dotted area a membership may be granted or denied; None for standalone local tooling
    feature: str | None = None  # a per-company unlockable service this command needs (blueprint 4.3b)
    local_only: bool = False  # acts on the calling process or its OS login; never routed over HTTP
    version_source: tuple[str, str | None, str] | None = None  # (show command, identifying positional or None, output field) for expected_version
    standalone_runner: Callable[..., dict[str, Any]] | None = None  # explicit rootless runner: no data root, lock, actor, or forwarding
    authorization: str | None = None  # exact human-readable rule when required_role alone cannot express it

    @property
    def is_write(self) -> bool:
        return self.kind == "write"

    @property
    def standalone(self) -> bool:
        return self.standalone_runner is not None

    @property
    def permissioned(self) -> bool:
        return not self.standalone

    @property
    def authorization_requirement(self) -> str:
        if self.standalone:
            return "none"
        return self.authorization or self.required_role or "authenticated"

    @property
    def noun(self) -> str:
        """Everything but the last word: `company`, or `hub audit`."""
        parts = self.name.split(" ")
        return " ".join(parts[:-1]) if len(parts) > 1 else parts[0]

    @property
    def verb(self) -> str:
        parts = self.name.split(" ")
        return parts[-1] if len(parts) > 1 else ""


REGISTRY: dict[str, Command] = {}


def command(name: str, *, scope: str, description: str, input_model: type[BaseModel], output_model: type[BaseModel],
            writes: set[str] | frozenset[str] = frozenset(), required_role: Role | None = None,
            positional: list[str] | None = None, error_codes: list[str] | None = None, bootstrap: bool = False,
            kind: str | None = None, truth: str | None = None, accepts_idempotency_key: bool = False,
            clearable: bool = False, streams: bool = False, capability: str | None = None, feature: str | None = None,
            local_only: bool = False, version_source: tuple[str, str | None, str] | None = None,
            standalone_runner: Callable[..., dict[str, Any]] | None = None, authorization: str | None = None):
    """Register ``plan`` (and, via ``.apply``, the apply function) under ``name``."""
    bad = set(input_model.model_fields) & CONTEXT_FIELD_NAMES
    if bad:
        raise ValueError(f"{name}: input model declares context field(s) {sorted(bad)}")
    error_codes = list(error_codes or [])
    # codes the pipeline itself can raise for this command (idempotency lookup, directive resolution) are part of its contract
    if accepts_idempotency_key:
        error_codes.append("E_IDEMPOTENCY_MISMATCH")
    if scope == "company" and writes and (kind or "write") == "write":  # advisory commands reject a directive as E_USAGE
        error_codes += ["E_DIRECTIVE_NOT_FOUND", "E_DIRECTIVE_INACTIVE"]
    error_codes = list(dict.fromkeys(error_codes))
    for code in error_codes:
        if code not in ALL_CODES:
            raise ValueError(f"{name}: unknown error code {code}")
    if scope not in ("hub", "company"):
        raise ValueError(f"{name}: bad scope {scope}")
    derived_kind = "write" if writes else "read"
    if kind is None:
        kind = derived_kind
    elif kind == "advisory":
        if "company" not in writes:
            raise ValueError(f"{name}: advisory commands must open the company writable")
    elif kind != derived_kind:
        raise ValueError(f"{name}: declared kind {kind!r} disagrees with writes {sorted(writes)}")
    if truth is None:
        truth = "company" if scope == "company" and "company" in writes else "hub"
    if truth not in ("hub", "company"):
        raise ValueError(f"{name}: bad truth {truth}")
    if standalone_runner is not None:
        if not bootstrap or not local_only:
            raise ValueError(f"{name}: a standalone command must be bootstrap and local-only")
        if scope != "hub":
            raise ValueError(f"{name}: a standalone command must use the hub scope without company selection")
        if writes or required_role is not None or capability is not None or feature is not None or authorization is not None:
            raise ValueError(f"{name}: a standalone command cannot declare database writes, a role, a capability, or a feature")
        if any((accepts_idempotency_key, clearable, streams, version_source is not None)):
            raise ValueError(f"{name}: a standalone command cannot declare database command behavior")

    def register(plan_fn: Callable[..., Plan]) -> Command:
        resolved_capability = None if standalone_runner is not None else (capability or name.split(" ")[0])
        cmd = Command(name=name, scope=scope, description=description, input_model=input_model, output_model=output_model,
                      plan=plan_fn, apply=None, writes=frozenset(writes), required_role=required_role,
                      positional=list(positional or []), error_codes=list(error_codes or []), bootstrap=bootstrap,
                      kind=kind, truth=truth, accepts_idempotency_key=accepts_idempotency_key, clearable=clearable, streams=streams,
                      capability=resolved_capability, feature=feature, local_only=local_only, version_source=version_source,
                      standalone_runner=standalone_runner, authorization=authorization)
        REGISTRY[name] = cmd

        def applier(apply_fn: Callable[..., Applied]) -> Callable[..., Applied]:
            if cmd.standalone:
                raise ValueError(f"{name}: a standalone command cannot declare an apply function")
            cmd.apply = apply_fn
            return apply_fn

        cmd.applier = applier  # type: ignore[attr-defined]
        register.applier = applier  # type: ignore[attr-defined]
        register.cmd = cmd  # type: ignore[attr-defined]
        return cmd

    return register


def get(name: str) -> Command | None:
    """Look a command up, importing its module on a miss so a command that runs other commands never sees None."""
    cmd = REGISTRY.get(name)
    if cmd is None:
        load_all(" ".join(name.split(" ")[:-1]) or name)
        cmd = REGISTRY.get(name)
    return cmd


def all_commands(*, include_standalone: bool = False) -> list[Command]:
    """Registered database commands, plus rootless local tooling when explicitly requested.

    Standalone commands do not participate in database role-capability seeds or routed surfaces.
    The CLI and documentation reference request them explicitly.
    """
    return [REGISTRY[k] for k in sorted(REGISTRY) if include_standalone or not REGISTRY[k].standalone]


# Which module registers which nouns. The CLI loads only the module it needs, so cold start does not grow
# with the command count; tests/test_registry.py asserts this index matches what the modules register.
NOUN_MODULES: dict[str, list[str]] = {
    "bookflow.commands.hub_cmds": ["init", "upgrade", "organization", "company", "demo"],
    "bookflow.commands.company_cmds": ["company", "directive", "presence"],
    "bookflow.commands.audit_cmds": ["audit", "hub audit"],
    "bookflow.commands.host_cmds": ["serve", "user", "token"],
    "bookflow.commands.docs_cmds": ["docs"],
    "bookflow.commands.account_cmds": ["account"],
    "bookflow.commands.chart_cmds": ["chart"],
    "bookflow.commands.custom_field_cmds": ["custom-field"],
    "bookflow.commands.profile_cmds": [
        "profile", "item-category", "class", "term", "payment-method",
        "sales-tax-code", "customer-type", "vendor-type", "job-type",
        "sales-rep", "ship-method", "customer-message",
    ],
    "bookflow.commands.unit_pricing_cmds": ["price-level", "unit-of-measure"],
    "bookflow.commands.party_cmds": ["customer", "vendor", "employee", "other-name"],
    "bookflow.commands.item_cmds": ["item"],
    "bookflow.commands.undo_cmds": ["undo"],
}

_loaded: set[str] = set()
_loading_target: str | None = None


def loading_target() -> str | None:
    """Return the noun path whose command module is currently being imported.

    Command modules with several nouns use this hint to construct only the
    requested CLI surface. A direct import or a full registry load receives
    ``None`` and registers the complete module.
    """

    return _loading_target


def load_all(target: str | None = None) -> None:
    """Import the command modules; with ``target`` (a noun path), only the modules that register it."""
    import importlib
    global _loading_target
    for module, nouns in NOUN_MODULES.items():
        if target is not None and not any(target == n or target.startswith(n + " ") or n.startswith(target + " ") for n in nouns):
            continue
        previous = _loading_target
        _loading_target = target
        try:
            imported = importlib.import_module(module)
        finally:
            _loading_target = previous
        incremental_loader = getattr(imported, "_load_target", None)
        if incremental_loader is not None:
            incremental_loader(target)
        _loaded.add(module)


def all_nouns() -> list[str]:
    return sorted({n for nouns in NOUN_MODULES.values() for n in nouns})


# Overrides where a noun's record type or identifier differs from the derivation (record type = noun; identifier = the
# show command's first positional). `company show` takes no positional: the record is the selected company.
NOUN_META_OVERRIDES: dict[str, dict[str, str | None]] = {
    "company": {"record_type": "company_info", "identifier": None},
    "audit": {"record_type": "audit_event", "identifier": "event"},
    "hub audit": {"record_type": "audit_event", "identifier": "event"},
}


def noun_meta(noun: str) -> dict[str, Any]:
    """Project routing metadata, including an authoritative Row 5 list definition when available."""
    if noun in NOUN_META_OVERRIDES:
        return dict(NOUN_META_OVERRIDES[noun])
    show = REGISTRY.get(f"{noun} show")
    identifier = show.positional[0] if show and show.positional else None
    from bookflow.company.lists import get_list_definition

    definition = get_list_definition(noun)
    if definition is None:
        return {"record_type": noun.replace(" ", "_"), "identifier": identifier}
    return {
        "record_type": definition.record_type,
        "identifier": identifier,
        "output_identifier": definition.identifier,
        "definition": definition,
        "route_slug": definition.route_slug,
        "singular_label": definition.singular_label,
        "plural_label": definition.plural_label,
        "display_field": definition.display_field,
        "ui_group": definition.ui_group,
        "ui_order": definition.ui_order,
        "primary_collection_action": definition.primary_collection_action,
        "collection_actions": definition.collection_actions,
        "record_actions": definition.record_actions,
        "hierarchy": definition.hierarchy,
        "columns": definition.all_columns,
        "filters": definition.filters,
        "runtime_field_provider": definition.runtime_field_provider,
    }


def routed_commands() -> list[Command]:
    """Commands an HTTP host exposes: everything but local-only ones."""
    return [c for c in all_commands() if not c.local_only]
