"""Local documentation generation command; rendering machinery is loaded only when it runs."""

from __future__ import annotations

import importlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from bookflow.core.context import Context
from bookflow.core.registry import Plan, command


class DocsGenerateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    output: str = Field("docs", min_length=1, description="Directory that receives the complete documentation tree")
    check: bool = Field(False, description="Compare the directory with a fresh render and write nothing")


class DocsGenerateOutput(BaseModel):
    output: str = Field(description="Selected documentation directory")
    mode: Literal["generate", "check"] = Field(description="Operation performed")
    file_count: int = Field(description="Number of files in the complete documentation tree")
    files: list[str] = Field(description="Sorted paths relative to the documentation directory")


def run_docs_generate(_cmd, inp: DocsGenerateInput, _ctx: Context) -> dict:
    generator = importlib.import_module("bookflow.documentation.generate")
    files = sorted(set(str(path) for path in generator.generate_docs(inp.output, inp.check)))
    return DocsGenerateOutput(
        output=inp.output,
        mode="check" if inp.check else "generate",
        file_count=len(files),
        files=files,
    ).model_dump(mode="json")


def _plan_docs_generate(inp: DocsGenerateInput, ctx: Context, session) -> Plan:  # never called; standalone runner owns the path
    raise NotImplementedError


docs_generate = command(
    "docs generate",
    scope="hub",
    description="Generate the complete command, schema, concepts, and agent-guide documentation tree.",
    input_model=DocsGenerateInput,
    output_model=DocsGenerateOutput,
    error_codes=["E_DOCS_STALE"],
    bootstrap=True,
    local_only=True,
    standalone_runner=run_docs_generate,
)(_plan_docs_generate)
