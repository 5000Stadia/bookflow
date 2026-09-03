"""The CLI, generated from the registry (blueprint 5.1, 15.1)."""

from __future__ import annotations

import inspect
import os
import sys
import types
from enum import Enum
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import typer

from typer import _click as click  # typer vendors its click fork
from typer._click import exceptions as click_exceptions, globals as click_globals, termui as click_termui
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from bookflow.adapters.cli.render import emit_error, render_output
from bookflow.core import registry
from bookflow.core.context import Context, Interface
from bookflow.core.errors import BookflowError
from bookflow.core.ids import new_id

GLOBAL_FLAGS = ("json_", "data_root", "dry_run", "company", "reason", "source_ref", "interactive")


def _leaf_type(annotation: Any) -> tuple[type, list[str] | None]:
    """Reduce a field annotation to (python type for the parser, choices)."""
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _leaf_type(args[0]) if args else (str, None)
    if origin is Literal:
        return str, [str(a) for a in get_args(annotation)]
    if origin is Annotated:
        return _leaf_type(get_args(annotation)[0])
    if inspect.isclass(annotation) and issubclass(annotation, Enum):
        return str, [e.value for e in annotation]
    if annotation in (int, float, bool, str):
        return annotation, None
    return str, None


def _flatten(model: type[BaseModel], prefix: str = "") -> list[tuple[str, str, Any, str, bool, Any]]:
    """Yield (input path with dots, flag name, annotation, help, required, default) per leaf field."""
    out = []
    for name, f in model.model_fields.items():
        ann = f.annotation
        base = ann
        origin = get_origin(ann)
        if origin is Union or origin is types.UnionType:
            args = [a for a in get_args(ann) if a is not type(None)]
            base = args[0] if args else str
        if inspect.isclass(base) and issubclass(base, BaseModel):
            out += _flatten(base, prefix + name + ".")
            continue
        required = f.is_required()
        default = None if required or f.default is PydanticUndefined else f.default
        out.append((prefix + name, (prefix + name).replace(".", "-").replace("_", "-"), ann, f.description or "", required, default))
    return out


def _help_text(help_: str, py_t: type, choices: list[str] | None, required: bool, default: Any) -> str:
    parts = [help_.rstrip(".") + "."] if help_ else []
    if choices:
        parts.append("One of: " + ", ".join(choices) + ".")
    if required:
        parts.append("Required.")
    elif default is not None and default != "" and not (isinstance(default, bool) and default is False):
        parts.append(f"Default: {default}.")
    return " ".join(parts)


_METAVAR = {int: "INT", float: "DECIMAL", str: "TEXT", bool: "BOOL"}


def _set_path(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def _build_command(cmd: registry.Command):
    leaves = _flatten(cmd.input_model)
    params: list[inspect.Parameter] = []
    for path, flag, ann, help_, required, dflt in leaves:
        py_t, choices = _leaf_type(ann)
        is_positional = path in cmd.positional
        text = _help_text(help_, py_t, choices, required, dflt)
        pname = "f__" + path.replace(".", "__")
        if is_positional:
            default = typer.Argument(None, help=text, metavar=path.upper())
            annotation = str | None
        elif py_t is bool:
            default = typer.Option(None, f"--{flag}/--no-{flag}", help=text)
            annotation = bool | None
        else:
            default = typer.Option(None, f"--{flag}", help=text, metavar=_METAVAR.get(py_t, "TEXT"))
            annotation = str | None
        params.append(inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation))
    params.append(inspect.Parameter("json_", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(False, "--json", help="Print the output as one JSON object"), annotation=bool))
    params.append(inspect.Parameter("data_root", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--data-root", help="Data root; else BOOKFLOW_DATA_ROOT, else ~/.bookflow", metavar="TEXT"), annotation=str | None))
    if cmd.is_write:
        params.append(inspect.Parameter("dry_run", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(False, "--dry-run", help="Validate and preview; write nothing"), annotation=bool))
        params.append(inspect.Parameter("reason", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--reason", help="Why, in one short phrase (at most 140 characters)", metavar="TEXT"), annotation=str | None))
        params.append(inspect.Parameter("source_ref", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--source-ref", help="What triggered this write, e.g. an email or attachment id", metavar="TEXT"), annotation=str | None))
        params.append(inspect.Parameter("interactive", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(False, "--interactive", help="Prompt for fields not given as options"), annotation=bool))
        if cmd.scope == "company":
            params.append(inspect.Parameter("directive", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--directive", help="Standing instruction this write follows, by code (SI-3) or id", metavar="TEXT"), annotation=str | None))
    if cmd.accepts_idempotency_key:
        params.append(inspect.Parameter("idempotency_key", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--idempotency-key", help="A key of your choosing; a retry with the same key and input returns the first result instead of writing again", metavar="TEXT"), annotation=str | None))
    if cmd.clearable:
        params.append(inspect.Parameter("clear", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--clear", help="Set a field to null; repeatable; a nested name clears every child (e.g. --clear phone, --clear address, --clear address-line2)", metavar="FIELD"), annotation=list[str] | None))
    if cmd.streams:
        params.append(inspect.Parameter("follow", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(False, "--follow", help="Keep polling every two seconds and print each new event; stop with Ctrl-C"), annotation=bool))
    if cmd.scope == "company":
        params.append(inspect.Parameter("company", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--company", help="Company id, Organization/Company, or display name; else BOOKFLOW_COMPANY, else the saved default", metavar="TEXT"), annotation=str | None))

    def run(**kw: Any) -> None:
        ctx_obj = click_globals.get_current_context().obj or {}
        as_json = kw.pop("json_", False) or ctx_obj.get("json", False)
        local_root = kw.pop("data_root", None)
        if local_root and ctx_obj.get("data_root") and local_root != ctx_obj["data_root"]:
            raise BookflowError("E_USAGE", message="--data-root was given twice with different values")
        data_root = local_root or ctx_obj.get("data_root")

        def merged(name: str, local: Any, applicable: bool) -> Any:
            root_v = ctx_obj.get(name)
            if root_v not in (None, False) and not applicable:
                raise BookflowError("E_USAGE", message=f"--{name.replace('_', '-')} does not apply to `{cmd.name}`")
            if root_v not in (None, False) and local not in (None, False) and root_v != local:
                raise BookflowError("E_USAGE", message=f"--{name.replace('_', '-')} was given twice with different values")
            return local if local not in (None, False) else root_v

        dry_run = bool(merged("dry_run", kw.pop("dry_run", False), cmd.is_write))
        reason = merged("reason", kw.pop("reason", None), cmd.is_write)
        source_ref = merged("source_ref", kw.pop("source_ref", None), cmd.is_write)
        interactive = kw.pop("interactive", False)
        directive = merged("directive", kw.pop("directive", None), cmd.is_write and cmd.scope == "company")
        idempotency_key = merged("idempotency_key", kw.pop("idempotency_key", None), cmd.accepts_idempotency_key)
        clears = kw.pop("clear", None) or []
        follow = kw.pop("follow", False)
        company = merged("company", kw.pop("company", None), cmd.scope == "company")
        raw: dict[str, Any] = {}
        for path, flag, ann, help_, required, dflt in leaves:
            v = kw.get("f__" + path.replace(".", "__"))
            if v is not None:
                _set_path(raw, path, v)
        known = {p for p, *_ in leaves} | {p.split(".")[0] for p, *_ in leaves}
        for name in clears:
            dotted = name.replace("-", ".", 1) if "." not in name and any(p.startswith(name.split("-")[0] + ".") for p in known) else name
            dotted = dotted.replace("-", "_")
            if dotted not in known:
                raise BookflowError("E_VALIDATION", details={"fields": [{"field": name, "problem": "not a clearable field of this command"}]})
            top = dotted.split(".")[0]
            if top in raw and raw.get(top) is not None and (dotted == top or dotted in {k for k in raw}):
                raise BookflowError("E_VALIDATION", details={"fields": [{"field": name, "problem": "given both a value and --clear"}]})
            if "." in dotted:
                _set_path(raw, dotted, None)
            else:
                raw[top] = None
        if interactive:
            if not sys.stdin.isatty():
                raise BookflowError("E_USAGE", message="--interactive needs a terminal")
            for path, flag, ann, help_, required, dflt in leaves:
                if kw.get("f__" + path.replace(".", "__")) is not None:
                    continue
                py_t, choices = _leaf_type(ann)
                label = path
                if help_:
                    label += f" ({help_})"
                if choices:
                    label += " [" + "/".join(choices) + "]"
                if required:
                    label += " [required]"
                elif dflt not in (None, ""):
                    label += f" [default: {dflt}]"
                v = click_termui.prompt(label, default="", show_default=False, err=True, type=str)
                if v != "":
                    _set_path(raw, path, v)
        source = "option"
        if cmd.scope == "company" and company is None:
            env = os.environ.get("BOOKFLOW_COMPANY")
            if env:
                company, source = env, "env"
            else:
                from bookflow.core.config import Config, os_login
                from bookflow.storage.paths import resolve_data_root
                cfg = Config.load(resolve_data_root(data_root) / "config.toml")
                table = cfg.user_table(os_login()) or {}
                if table.get("default_company"):
                    company, source = table["default_company"], "default"
        from bookflow.core.dispatch import run as dispatch_run
        ctx = Context.new(Interface.cli, "bookflow-cli", session_id=ctx_obj.get("session_id") or new_id(), reason=reason, source_ref=source_ref, directive_id=directive, idempotency_key=idempotency_key)
        if follow:
            import time as _time
            after = raw.get("after")
            while True:
                try:
                    out = dispatch_run(cmd, {**raw, **({"after": after} if after is not None else {})}, ctx, data_root=data_root, company_selector=company, company_source=source, dry_run=dry_run)
                except BookflowError as e:
                    if e.code != "E_DB_BUSY":
                        raise
                    out = {"items": [], "next_after": after}
                for item in out.get("items", []):
                    typer.echo(render_output(item, as_json) if as_json else render_output({"items": [item], "count": 1}, False))
                if out.get("next_after") is not None:
                    after = out["next_after"]
                elif after is None:
                    after = raw.get("after") or 0
                try:
                    _time.sleep(2)
                except KeyboardInterrupt:
                    return
        out = dispatch_run(cmd, raw, ctx, data_root=data_root, company_selector=company, company_source=source, dry_run=dry_run)
        for w_ in (out.get("warnings") or []) if isinstance(out, dict) else []:
            typer.echo(f"warning: {w_}", err=True)
        typer.echo(render_output(out, as_json))

    run.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    run.__name__ = cmd.verb or cmd.noun
    run.__doc__ = cmd.description
    return run


def _target_noun(argv: list[str]) -> str | None:
    """The noun path the invocation names (words before the first option after the program name), or None for the root."""
    words = []
    for tok in argv[1:]:
        if tok.startswith("-"):
            if words:
                break
            continue
        words.append(tok)
    return " ".join(words) if words else None


def build_app(target: str | None = None, full: bool = False) -> typer.Typer:
    """Build the CLI. With ``target``, only that noun's module is loaded and built and other groups are registered empty so help still lists them; ``full`` builds everything."""
    if full:
        registry.load_all()
    elif target is not None:
        registry.load_all(target)
    app = typer.Typer(add_completion=False, no_args_is_help=True, help="Bookflow: multi-company double-entry accounting.", rich_markup_mode=None)

    @app.callback()
    def root(ctx: typer.Context,
             json_: Annotated[bool, typer.Option("--json", help="Print output as one JSON object")] = False,
             data_root: Annotated[str | None, typer.Option("--data-root", help="Data root; else BOOKFLOW_DATA_ROOT, else ~/.bookflow")] = None,
             dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate and preview; write nothing (writing commands only)")] = False,
             company: Annotated[str | None, typer.Option("--company", help="Company selector (company-scoped commands only)")] = None,
             reason: Annotated[str | None, typer.Option("--reason", help="Why, in one short phrase (writing commands only)")] = None,
             source_ref: Annotated[str | None, typer.Option("--source-ref", help="What triggered this write (writing commands only)")] = None,
             directive: Annotated[str | None, typer.Option("--directive", help="Standing instruction, by code or id (company-scoped writes only)")] = None,
             idempotency_key: Annotated[str | None, typer.Option("--idempotency-key", help="Retry-safe key (create commands only)")] = None) -> None:
        ctx.obj = {"json": json_, "data_root": data_root, "dry_run": dry_run, "company": company, "reason": reason, "source_ref": source_ref, "directive": directive, "idempotency_key": idempotency_key, "session_id": new_id()}

    groups: dict[str, typer.Typer] = {}

    def group_for(path: str) -> typer.Typer:
        if path in groups:
            return groups[path]
        parent_path, _, leaf = path.rpartition(" ")
        parent = group_for(parent_path) if parent_path else app
        sub = typer.Typer(no_args_is_help=True, help=f"{path} commands", rich_markup_mode=None)
        groups[path] = sub
        parent.add_typer(sub, name=leaf)
        return sub

    single = {"init": "Create the data root, the system user, and the first hub-admin user mapped from the OS login.", "upgrade": "Migrate the hub database and every company database the acting user may write to the current schema revision."}
    built = set()
    for cmd in registry.all_commands():
        if not cmd.verb:
            app.command(cmd.noun, help=cmd.description)(_build_command(cmd))
            built.add(cmd.noun)
            continue
        group_for(cmd.noun).command(cmd.verb, help=cmd.description)(_build_command(cmd))
        built.add(cmd.noun)
    for noun in registry.all_nouns():
        if noun in built:
            continue
        if noun in single:
            app.command(noun, help=single[noun])(lambda: None)
        else:
            group_for(noun)
    return app


def main() -> None:
    target = _target_noun(sys.argv)
    known = target is not None and any(n == target or n.startswith(target + " ") or target.startswith(n + " ") for n in registry.all_nouns())
    # a root-level option value can masquerade as the noun; when the guess is unknown, build everything
    app = build_app(target if known else None, full=(target is not None and not known))
    as_json = "--json" in sys.argv
    try:
        app(standalone_mode=False)
    except BookflowError as e:
        sys.exit(emit_error(e, as_json))
    except click_exceptions.UsageError as e:
        if type(e).__name__ == "NoArgsIsHelpError":
            typer.echo(e.format_message())
            sys.exit(0)
        sys.exit(emit_error(BookflowError("E_USAGE", message=e.format_message()), as_json))
    except typer.Exit as e:
        sys.exit(e.exit_code)
    except (typer.Abort, KeyboardInterrupt):
        sys.exit(0)
    except Exception as e:  # noqa: BLE001
        sys.exit(emit_error(BookflowError("E_INTERNAL", message=f"{type(e).__name__}: {e}"), as_json))


if __name__ == "__main__":
    main()
