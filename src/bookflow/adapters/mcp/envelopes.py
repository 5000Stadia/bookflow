"""Public tool envelopes. Business inputs are validated only by the command core."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from bookflow.core.errors import BookflowError


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListArguments(Envelope):
    prefix: str | None = None
    limit: int = Field(100, ge=1, le=200)
    cursor: str | None = None


class HelpArguments(Envelope):
    command: str = Field(min_length=1)


class Files(Envelope):
    input_file: str | None = None
    output_file: str | None = None
    input_json_file: str | None = None
    result_file: str | None = None
    prepare_only: bool = False

    @model_validator(mode="after")
    def present_files(self):
        for name in self.model_fields_set - {"prepare_only"}:
            value = getattr(self, name)
            if value is None or not value:
                raise ValueError("file arguments require nonempty paths")
        return self


class RunArguments(Envelope):
    command: str = Field(min_length=1)
    input: dict[str, Any] | None = None
    company: str | None = None
    reason: str | None = None
    source_ref: str | None = None
    directive: str | None = None
    idempotency_key: str | None = None
    dry_run: bool = False
    transport: Files = Field(default_factory=Files)

    @model_validator(mode="after")
    def input_source(self):
        has_input = "input" in self.model_fields_set
        if has_input == bool(self.transport.input_json_file) or (has_input and self.input is None):
            raise ValueError("supply exactly one input object or transport.input_json_file")
        return self


class RecoveryArguments(Envelope):
    operation_ref: str | None = None
    input_ref: str | None = None
    action: Literal["execute", "status", "release", "inspect"]
    result_file: str | None = None
    output_file: str | None = None
    pointer: str = ""
    limit: int = Field(100, ge=1, le=200)
    cursor: str | None = None

    @model_validator(mode="after")
    def branch(self):
        if bool(self.operation_ref) == bool(self.input_ref):
            raise ValueError("supply exactly one intent reference")
        for name in ("operation_ref", "input_ref", "result_file", "output_file", "cursor"):
            if name in self.model_fields_set and not getattr(self, name):
                raise ValueError("explicit references and destinations cannot be null or empty")
        allowed = {"action", "operation_ref", "input_ref"}
        if self.action == "execute":
            allowed |= {"result_file", "output_file"}
        elif self.action == "inspect":
            allowed |= {"pointer", "limit", "cursor"}
        if self.model_fields_set - allowed:
            raise ValueError("arguments do not apply to this recovery action")
        return self


RUN = TypeAdapter(RunArguments | RecoveryArguments)
TOOLS = {
    "bookflow_list_commands": (ListArguments, "Discover registered Bookflow commands and their scope."),
    "bookflow_help": (HelpArguments, "Read a command's documentation, typed inputs, outputs and errors."),
    "bookflow_run": (RUN, "Run a discovered Bookflow command. Files use transport.input_file/output_file on the calling machine; the adapter handles all bytes. Recover an existing intent by its reference without resubmitting it."),
}


def validate(name, arguments):
    entry = TOOLS.get(name)
    if entry is None:
        raise BookflowError("E_USAGE", message="Unknown MCP tool")
    try:
        if name == "bookflow_run" and isinstance(arguments, dict):
            model = RunArguments if "command" in arguments else RecoveryArguments
            unknown = set(arguments) - set(model.model_fields)
            if unknown:
                raise BookflowError("E_USAGE", details={"arguments": sorted(unknown)})
            return model.model_validate(arguments)
        return (entry[0].validate_python(arguments) if isinstance(entry[0], TypeAdapter)
                else entry[0].model_validate(arguments))
    except ValidationError as exc:
        # Never echo the original values or validator context (which may contain secrets).
        errors = exc.errors(include_input=False, include_context=False, include_url=False)
        raise BookflowError("E_VALIDATION", details={"reason": "malformed_transport", "fields": [
            {"field": ".".join(map(str, item["loc"])), "problem": item["type"]} for item in errors
        ]}) from None
