"""The Python surface: connect() and Client (blueprint 2.0, plan 'Library surface')."""

from __future__ import annotations

from typing import Any

from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.dispatch import run as dispatch_run
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id


class _Noun:
    def __init__(self, client: "Client", noun: str):
        self._client, self._noun = client, noun

    def __getattr__(self, verb: str):
        name = f"{self._noun} {verb.replace('_', '-')}"
        if registry.get(name) is None:
            if any(c.name.startswith(name + " ") for c in registry.all_commands()):
                return _Noun(self._client, name)
            raise BookflowError("E_USAGE", message=f"unknown command {name!r}")

        def call(**kwargs: Any) -> dict[str, Any]:
            dry_run = kwargs.pop("dry_run", False)
            reason = kwargs.pop("reason", None)
            source_ref = kwargs.pop("source_ref", None)
            fields = registry.get(name).input_model.model_fields
            directive = kwargs.pop("directive", None) if "directive" not in fields else None
            idempotency_key = kwargs.pop("idempotency_key", None)
            clear = kwargs.pop("clear", None)
            input_stream = kwargs.pop("input_stream", None)
            output_stream = kwargs.pop("output_stream", None)
            company = None
            if registry.get(name).scope == "company" and "company" not in fields:
                company = kwargs.pop("company", None)
            return self._client.run(name, kwargs, company=company, dry_run=dry_run, reason=reason, source_ref=source_ref, directive=directive, idempotency_key=idempotency_key, clear=clear, input_stream=input_stream, output_stream=output_stream)
        return call


class Client:
    def __init__(self, data_root: str | None = None, client_name: str = "python"):
        registry.load_all()
        self.data_root = data_root
        self.client_name = client_name
        self.session_id = new_id()
        self._company: str | None = None
        self._login: str | None = None  # the OS login is taken from the process; tests may override this private attribute

    def use_company(self, selector: str | None) -> None:
        self._company = selector

    def run(self, name: str, input: dict[str, Any] | None = None, *, company: str | None = None, dry_run: bool = False,
            reason: str | None = None, source_ref: str | None = None, directive: str | None = None, idempotency_key: str | None = None,
            clear: list[str] | None = None, input_stream=None, output_stream=None) -> dict[str, Any]:
        cmd = registry.get(name)
        if cmd is None:
            raise BookflowError("E_USAGE", message=f"unknown command {name!r}")
        input = dict(input or {})
        if clear:
            if not cmd.clearable:
                raise BookflowError("E_USAGE", message=f"`{name}` has no clearable fields.")
            from bookflow.core.clearing import apply_clears
            apply_clears(cmd, input, list(clear))
        ctx = Context.new(Interface.python, self.client_name, session_id=self.session_id, reason=reason, source_ref=source_ref, directive_id=directive, idempotency_key=idempotency_key)
        selector, source = company, "option"
        if selector is None and self._company is not None and cmd.scope == "company":
            selector, source = self._company, "option"
        if selector is None and cmd.scope == "company":
            import os
            env = os.environ.get("BOOKFLOW_COMPANY")
            if env:
                selector, source = env, "env"
            else:
                from bookflow.core.config import Config, os_login
                from bookflow.storage.paths import resolve_data_root
                cfg = Config.load(resolve_data_root(self.data_root) / "config.toml")
                table = cfg.user_table(self._login or os_login()) or {}
                if table.get("default_company"):
                    selector, source = table["default_company"], "default"
        return dispatch_run(cmd, input or {}, ctx, data_root=self.data_root, company_selector=selector, company_source=source, dry_run=dry_run, _login=self._login, input_stream=input_stream, output_stream=output_stream)

    def __getattr__(self, noun: str):
        if noun.startswith("_"):
            raise AttributeError(noun)
        if registry.get(noun) is not None:  # single-word command such as `init` or `upgrade`
            def call(**kwargs: Any) -> dict[str, Any]:
                dry_run = kwargs.pop("dry_run", False)
                reason = kwargs.pop("reason", None)
                source_ref = kwargs.pop("source_ref", None)
                cmd = registry.get(noun)
                company = kwargs.pop("company", None) if cmd.scope == "company" and "company" not in cmd.input_model.model_fields else None
                return self.run(noun, kwargs, company=company, dry_run=dry_run, reason=reason, source_ref=source_ref,
                                idempotency_key=kwargs.pop("idempotency_key", None), directive=kwargs.pop("directive", None))
            return call
        return _Noun(self, noun)


def connect(data_root: str | None = None, client_name: str = "python") -> Client:
    return Client(data_root=data_root, client_name=client_name)
