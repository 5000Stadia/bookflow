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


def _flatten(model: type[BaseModel], prefix: str = "") -> list[tuple[str, str, Any, str, bool]]:
    """Yield (input path with dots, flag name, annotation, help, required) per leaf field."""
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
        out.append((prefix + name, (prefix + name).replace(".", "-").replace("_", "-"), ann, f.description or "", required))
    return out


def _set_path(d: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def _build_command(cmd: registry.Command):
    leaves = _flatten(cmd.input_model)
    params: list[inspect.Parameter] = []
    for path, flag, ann, help_, required in leaves:
        py_t, choices = _leaf_type(ann)
        is_positional = path in cmd.positional
        if choices:
            help_ = (help_ + " " if help_ else "") + "One of: " + ", ".join(choices) + "."
        pname = "f__" + path.replace(".", "__")
        if is_positional:
            default = typer.Argument(None, help=help_, metavar=path.upper())
            annotation = str | None
        elif py_t is bool:
            default = typer.Option(None, f"--{flag}/--no-{flag}", help=help_)
            annotation = bool | None
        else:
            default = typer.Option(None, f"--{flag}", help=help_)
            annotation = str | None
        params.append(inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation))
    params.append(inspect.Parameter("json_", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(False, "--json", help="Print the output as one JSON object"), annotation=bool))
    params.append(inspect.Parameter("data_root", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--data-root", help="Data root; else BOOKFLOW_DATA_ROOT, else ~/.bookflow"), annotation=str | None))
    if cmd.is_write:
        params.append(inspect.Parameter("dry_run", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(False, "--dry-run", help="Validate and preview; write nothing"), annotation=bool))
        params.append(inspect.Parameter("reason", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--reason", help="Why, in one short phrase (at most 140 characters)"), annotation=str | None))
        params.append(inspect.Parameter("source_ref", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--source-ref", help="What triggered this write, e.g. an email or attachment id"), annotation=str | None))
        params.append(inspect.Parameter("interactive", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(False, "--interactive", help="Prompt for fields not given as options"), annotation=bool))
    if cmd.scope == "company":
        params.append(inspect.Parameter("company", inspect.Parameter.KEYWORD_ONLY, default=typer.Option(None, "--company", help="Company id, Organization/Company, or display name; else BOOKFLOW_COMPANY, else the saved default"), annotation=str | None))

    def run(**kw: Any) -> None:
        ctx_obj = click_globals.get_current_context().obj or {}
        as_json = kw.pop("json_", False) or ctx_obj.get("json", False)
        data_root = kw.pop("data_root", None) or ctx_obj.get("data_root")
        dry_run = kw.pop("dry_run", False)
        reason = kw.pop("reason", None)
        source_ref = kw.pop("source_ref", None)
        interactive = kw.pop("interactive", False)
        company = kw.pop("company", None)
        raw: dict[str, Any] = {}
        for path, flag, ann, help_, required in leaves:
            v = kw.get("f__" + path.replace(".", "__"))
            if v is not None:
                _set_path(raw, path, v)
        if interactive:
            if not sys.stdin.isatty():
                raise BookflowError("E_USAGE", message="--interactive needs a terminal")
            for path, flag, ann, help_, required in leaves:
                if kw.get("f__" + path.replace(".", "__")) is not None:
                    continue
                py_t, choices = _leaf_type(ann)
                label = f"{path}" + (f" ({'/'.join(choices)})" if choices else "") + (" [required]" if required else "")
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
        ctx = Context.new(Interface.cli, "bookflow-cli", session_id=ctx_obj.get("session_id") or new_id(), reason=reason, source_ref=source_ref)
        out = dispatch_run(cmd, raw, ctx, data_root=data_root, company_selector=company, company_source=source, dry_run=dry_run)
        typer.echo(render_output(out, as_json))

    run.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    run.__name__ = cmd.verb or cmd.noun
    run.__doc__ = cmd.description
    return run


def build_app() -> typer.Typer:
    registry.load_all()
    app = typer.Typer(add_completion=False, no_args_is_help=True, help="Bookflow: multi-company double-entry accounting.", rich_markup_mode=None)

    @app.callback()
    def root(ctx: typer.Context,
             json_: Annotated[bool, typer.Option("--json", help="Print output as one JSON object")] = False,
             data_root: Annotated[str | None, typer.Option("--data-root", help="Data root; else BOOKFLOW_DATA_ROOT, else ~/.bookflow")] = None) -> None:
        ctx.obj = {"json": json_, "data_root": data_root, "session_id": new_id()}

    nouns: dict[str, typer.Typer] = {}
    for cmd in registry.all_commands():
        fn = _build_command(cmd)
        if not cmd.verb:
            app.command(cmd.noun, help=cmd.description)(fn)
            continue
        sub = nouns.get(cmd.noun)
        if sub is None:
            sub = typer.Typer(no_args_is_help=True, help=f"{cmd.noun} commands", rich_markup_mode=None)
            nouns[cmd.noun] = sub
            app.add_typer(sub, name=cmd.noun)
        sub.command(cmd.verb, help=cmd.description)(fn)
    return app


def main() -> None:
    app = build_app()
    as_json = "--json" in sys.argv
    try:
        app(standalone_mode=False)
    except BookflowError as e:
        sys.exit(emit_error(e, as_json))
    except click_exceptions.UsageError as e:
        sys.exit(emit_error(BookflowError("E_USAGE", message=e.format_message()), as_json))
    except typer.Exit as e:
        sys.exit(e.exit_code)
    except typer.Abort:
        sys.exit(emit_error(BookflowError("E_USAGE", message="aborted"), as_json))
    except Exception as e:  # noqa: BLE001
        sys.exit(emit_error(BookflowError("E_INTERNAL", message=f"{type(e).__name__}: {e}"), as_json))


if __name__ == "__main__":
    main()
