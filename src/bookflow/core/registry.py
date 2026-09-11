"""The command registry. Every adapter is generated from it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from bookflow.core.context import CONTEXT_FIELD_NAMES
from bookflow.core.errors import ALL_CODES

# These capability contracts have no role-default activation. Keep independent
# from the frozen, pure permission catalog and from command import order.
EXPLICIT_GRANT_ONLY_CAPABILITIES = frozenset(
    'transaction.' + family + '.delete'
    for family in ('journal_entry', 'invoice', 'sales_receipt', 'payment'))


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
    finalized: bool = False  # apply durably completed its own audit and idempotency transaction
    audited: bool = False  # True when apply wrote its own audit events
    after_commit: Callable[[], None] | None = None  # runs once the command's own transactions are durable (demo seed history)


@dataclass(frozen=True)
class MatchedRecovery:
    """Currently authorized exact permanent recovery; no write pipeline runs."""
    output: BaseModel


@dataclass(frozen=True)
class TransferDescriptor:
    """One out-of-band body and an authorization-time business preparation callback."""

    direction: str
    prepare: Callable[..., Any]

    def __post_init__(self):
        if self.direction not in ("input", "output") or not callable(self.prepare):
            raise ValueError("a transfer requires input/output direction and a preparation callback")


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
    protocol_stdout: bool = False  # standalone runner owns stdout through shutdown; no CLI result renderer
    transfer: TransferDescriptor | None = None
    authorization: str | None = None  # exact human-readable rule when required_role alone cannot express it
    replay: Callable[..., dict[str, Any]] | None = None  # read-only refresh after normal authorization and matching request-cache lookup
    permanent_recovery: Callable[..., MatchedRecovery | None] | None = None  # narrowly opted-in exact recovery before new-write reason/directive gates

    explicit_grant_only: bool = False  # unavailable until reviewed live granular admission

    resource_requirements: tuple[tuple[str, str], ...] = ()  # additional (capability, role) checks
    authorize_input: Callable[..., None] | None = None  # conditional resources, before replay or plan

    @property
    def requires_explicit_grant(self) -> bool:
        return (self.explicit_grant_only or self.capability in EXPLICIT_GRANT_ONLY_CAPABILITIES
                or any(capability in EXPLICIT_GRANT_ONLY_CAPABILITIES
                       for capability, _ in self.resource_requirements))

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
        rule = self.authorization or self.required_role or "authenticated"
        return rule + "; explicit grant required (not activated)" if self.requires_explicit_grant else rule

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
            standalone_runner: Callable[..., dict[str, Any]] | None = None, authorization: str | None = None,
            protocol_stdout: bool = False, explicit_grant_only: bool = False,
            transfer: TransferDescriptor | None = None):
    """Register ``plan`` (and, via ``.apply``, the apply function) under ``name``."""
    if type(explicit_grant_only) is not bool:
        raise ValueError(f"{name}: explicit_grant_only must be a bool")
    if (explicit_grant_only or capability in EXPLICIT_GRANT_ONLY_CAPABILITIES) and (scope != "company" or bootstrap or standalone_runner is not None):
        raise ValueError(f"{name}: explicit grants require a nonbootstrap company command")
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
    if protocol_stdout and standalone_runner is None:
        raise ValueError(f"{name}: protocol stdout requires a standalone runner")
    if standalone_runner is not None:
        if not bootstrap or not local_only:
            raise ValueError(f"{name}: a standalone command must be bootstrap and local-only")
        if scope != "hub":
            raise ValueError(f"{name}: a standalone command must use the hub scope without company selection")
        if writes or required_role is not None or capability is not None or feature is not None or authorization is not None:
            raise ValueError(f"{name}: a standalone command cannot declare database writes, a role, a capability, or a feature")
        if any((accepts_idempotency_key, clearable, streams, version_source is not None)):
            raise ValueError(f"{name}: a standalone command cannot declare database command behavior")

    if transfer is not None:
        if scope != "company" or standalone_runner is not None or bootstrap or streams:
            raise ValueError(f"{name}: transfers require a routed company command")
        if (transfer.direction == "input") != bool(writes):
            raise ValueError(f"{name}: input transfers write; output transfers read")

    def register(plan_fn: Callable[..., Plan]) -> Command:
        resolved_capability = None if standalone_runner is not None else (capability or name.split(" ")[0])
        cmd = Command(name=name, scope=scope, description=description, input_model=input_model, output_model=output_model,
                      plan=plan_fn, apply=None, writes=frozenset(writes), required_role=required_role,
                      positional=list(positional or []), error_codes=list(error_codes or []), bootstrap=bootstrap,
                      kind=kind, truth=truth, accepts_idempotency_key=accepts_idempotency_key, clearable=clearable, streams=streams,
                      capability=resolved_capability, feature=feature, local_only=local_only, version_source=version_source,
                      standalone_runner=standalone_runner, protocol_stdout=protocol_stdout, authorization=authorization, transfer=transfer,
                      explicit_grant_only=explicit_grant_only)
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
    "bookflow.commands.note_cmds": ["note"],
    "bookflow.commands.attachment_cmds": ["attachment"],
    "bookflow.commands.journal_cmds": ["journal"],
    "bookflow.commands.sales_cmds": ["invoice", "sales-receipt"],
    "bookflow.commands.statement_charge_cmds": ["statement-charge"],
    "bookflow.commands.deposit_cmds": ["deposit"],
    "bookflow.commands.payment_recovery_cmds": ["payment recovery"],
    "bookflow.commands.payment_cmds": ["payment", "payment selection", "invoice", "application", "payment operation", "payment preview", "payment settlement"],
    "bookflow.commands.work_cmds": ["proposal", "estimate", "work-order"],
    "bookflow.commands.billing_cmds": ["estimate", "work-order"],
    "bookflow.commands.register_cmds": ["register"],
    "bookflow.commands.check_cmds": ["check", "card-charge"],
    "bookflow.commands.credit_memo_cmds": ["credit-memo"],
    "bookflow.commands.credit_settlement_cmds": ["customer-credit"],
    "bookflow.commands.refund_cmds": ["customer-refund"],
    "bookflow.commands.bill_cmds": ["bill"],
    "bookflow.commands.bill_payment_cmds": ["bill", "bill payment"],
    "bookflow.commands.vendor_credit_cmds": ["vendor-credit"],
    "bookflow.commands.transfer_cmds": ["transfer"],
    "bookflow.commands.sales_tax_cmds": ["sales-tax", "sales-tax payment"],
    "bookflow.commands.report_cmds": ["report"],
    "bookflow.commands.inventory_cmds": ["inventory"],
    "bookflow.commands.rate_cmds": ["rate"],
    "bookflow.commands.activity_cmds": ["activity"],
    "bookflow.commands.compact_cmds": ["company"],
    "bookflow.commands.host_cmds": ["serve", "user", "membership", "token"],
    "bookflow.commands.docs_cmds": ["docs"],
    "bookflow.commands.mcp_cmds": ["mcp"],
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
    "bookflow.commands.query_cmds": [
        "account", "customer", "vendor", "employee", "other-name", "item", "item-category",
        "class", "term", "payment-method", "sales-tax-code", "customer-type", "vendor-type",
        "job-type", "sales-rep", "ship-method", "customer-message", "price-level", "unit-of-measure", "custom-field",
    ],
}

# Additive verb modules need not be imported for a concrete sibling command.
# Full registry loads and noun-level help still include every declared verb.
MODULE_VERBS: dict[str, frozenset[str]] = {
    "bookflow.commands.query_cmds": frozenset({"query"}),
    "bookflow.commands.billing_cmds": frozenset({"invoice", "sales-receipt", "billing"}),
    "bookflow.commands.compact_cmds": frozenset({"compact"}),
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
        verbs = MODULE_VERBS.get(module)
        if target is not None and verbs is not None and not any(
            target == noun or noun.startswith(target + " ") or any(
                target == f"{noun} {verb}" or target.startswith(f"{noun} {verb} ") for verb in verbs
            ) for noun in nouns
        ):
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
    "payment": {"record_type": "transaction", "identifier": "payment", "output_identifier": "id", "ui_group": "Customers and sales", "display_field": "number", "singular_label": "Customer payment", "plural_label": "Customer payments"},
    "invoice": {"record_type": "transaction", "identifier": "invoice", "output_identifier": "id", "ui_group": "Customers and sales", "display_field": "number", "singular_label": "Invoice", "plural_label": "Invoices"},
    "sales-receipt": {"record_type": "transaction", "identifier": "sales_receipt", "output_identifier": "id", "ui_group": "Customers and sales", "display_field": "number", "singular_label": "Sales receipt", "plural_label": "Sales receipts"},
    # No ui_group: there is no statement-charge page behind one yet, and a group with no
    # page is a tile that goes nowhere. It joins "Customers and sales" with its window.
    "statement-charge": {"record_type": "transaction", "identifier": "statement_charge", "output_identifier": "id", "display_field": "number", "singular_label": "Statement charge", "plural_label": "Statement charges"},
    "company": {"record_type": "company_info", "identifier": None},
    "journal": {"record_type": "transaction", "identifier": "journal", "output_identifier": "id", "ui_group": "Accounting", "display_field": "number", "singular_label": "Journal", "plural_label": "Journals"},
    "register": {"record_type": None, "identifier": None, "ui_group": "Accounting"},
    "credit-memo": {"record_type": "transaction", "identifier": "credit_memo", "output_identifier": "id", "ui_group": "Customers and sales", "display_field": "number", "singular_label": "Credit memo", "plural_label": "Credit memos"},
    "customer-credit": {"record_type": None, "identifier": None, "ui_group": "Customers and sales"},
    "customer-refund": {"record_type": "transaction", "identifier": "refund", "output_identifier": "id", "ui_group": "Customers and sales", "display_field": "number", "singular_label": "Customer refund", "plural_label": "Customer refunds"},
    "bill": {"record_type": "transaction", "identifier": "bill", "output_identifier": "id", "ui_group": "Vendors and purchases", "display_field": "number", "singular_label": "Bill", "plural_label": "Bills"},
    "vendor-credit": {"record_type": "transaction", "identifier": "credit", "output_identifier": "id", "ui_group": "Vendors and purchases", "display_field": "number", "singular_label": "Vendor credit", "plural_label": "Vendor credits"},
    "bill payment": {"record_type": "transaction", "identifier": "payment", "output_identifier": "id", "ui_group": "Vendors and purchases", "display_field": "number", "singular_label": "Bill payment", "plural_label": "Bill payments"},
    "check": {"record_type": "transaction", "identifier": "check", "output_identifier": "id", "ui_group": "Vendors and purchases", "display_field": "number", "singular_label": "Check", "plural_label": "Checks"},
    "card-charge": {"record_type": "transaction", "identifier": "card_charge", "output_identifier": "id", "ui_group": "Vendors and purchases", "display_field": "number", "singular_label": "Credit card charge", "plural_label": "Credit card charges"},
    "transfer": {"record_type": "transaction", "identifier": "transfer", "output_identifier": "id", "ui_group": "Accounting", "display_field": "number", "singular_label": "Transfer", "plural_label": "Transfers"},
    "report": {"record_type": None, "identifier": None, "ui_group": "Accounting"},
    "sales-tax": {"record_type": None, "identifier": None, "ui_group": "Accounting"},
    "sales-tax payment": {"record_type": "transaction", "identifier": "payment", "output_identifier": "id", "ui_group": "Accounting", "display_field": "number", "singular_label": "Sales tax payment", "plural_label": "Sales tax payments"},
    "rate": {"record_type": "exchange_rate", "identifier": "rate_id", "output_identifier": "id", "ui_group": "Accounting"},
    "inventory": {"record_type": "transaction", "identifier": "adjustment", "output_identifier": "id", "ui_group": "Items", "display_field": "number", "singular_label": "Inventory adjustment", "plural_label": "Inventory adjustments"},
    "audit": {"record_type": "audit_event", "identifier": "event"},
    "hub audit": {"record_type": "audit_event", "identifier": "event"},
}


def noun_meta(noun: str) -> dict[str, Any]:
    """Project routing metadata, including an authoritative Row 5 list definition when available."""
    if noun in NOUN_META_OVERRIDES:
        meta = dict(NOUN_META_OVERRIDES[noun])
        if noun in ('invoice', 'sales-receipt'):
            from bookflow.company.sales_contract import FORM_DEFINITIONS
            meta['form_definition'] = FORM_DEFINITIONS[noun]
        if noun == 'report':
            from bookflow.company.report_contract import FORM
            meta['form_definition'] = FORM
        if noun in ('check', 'card-charge'):
            from bookflow.company.check_contract import FORM_DEFINITIONS
            meta['form_definition'] = FORM_DEFINITIONS[noun]
        if noun == 'bill':
            from bookflow.company.bill_contract import FORM
            meta['form_definition'] = FORM
        if noun == 'transfer':
            from bookflow.company.transfer_contract import FORM
            meta['form_definition'] = FORM
        return meta
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
