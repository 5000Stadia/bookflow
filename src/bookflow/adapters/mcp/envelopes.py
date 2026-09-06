"""Public tool envelopes. Business inputs are validated only by the command core."""

from typing import Any, Literal
from copy import deepcopy
import re

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from bookflow.core.errors import BookflowError
from bookflow.core.dispatch import CONTEXT_LIMITS

REFERENCE_PATTERN = r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$"


def intent_reference(value):
    if not isinstance(value, str) or len(value) > 128 or re.fullmatch(REFERENCE_PATTERN, value) is None:
        raise BookflowError("E_VALIDATION", details={"reason": "invalid_intent_reference"})
    return value


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListArguments(Envelope):
    prefix: str | None = None
    limit: int = Field(20, ge=1, le=200, description="Commands per page. Start with a noun prefix; follow next_cursor for the complete catalog.")
    cursor: str | None = None


class HelpArguments(Envelope):
    command: str = Field(min_length=1)
    view: Literal["usage", "input_schema", "output_schema", "full"] = "usage"


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
            if not value.startswith("/"):
                raise ValueError("file arguments require absolute paths")
        return self


class RunArguments(Envelope):
    command: str = Field(min_length=1)
    input: dict[str, Any] | None = None
    company: str | None = None
    reason: str | None = Field(None, max_length=CONTEXT_LIMITS["reason"], description="Short audit reason for a write; omit on reads. An agent write needs reason or directive.")
    source_ref: str | None = Field(None, max_length=CONTEXT_LIMITS["source_ref"])
    directive: str | None = None
    idempotency_key: str | None = Field(None, max_length=CONTEXT_LIMITS["idempotency_key"])
    dry_run: bool = False
    transport: Files = Field(default_factory=Files)

    @model_validator(mode="after")
    def input_source(self):
        has_input = "input" in self.model_fields_set
        if has_input == bool(self.transport.input_json_file) or (has_input and self.input is None):
            raise BookflowError("E_VALIDATION", message="Supply input: {} for a command without business arguments, or supply its input object; input_json_file replaces that object.",
                details={"reason": "input_source", "fields": [{"field": "input", "problem": "supply exactly one non-null input object or transport.input_json_file"}]})
        return self


class RecoveryArguments(Envelope):
    operation_ref: str | None = Field(None, min_length=1, max_length=128, pattern=REFERENCE_PATTERN)
    input_ref: str | None = Field(None, min_length=1, max_length=128, pattern=REFERENCE_PATTERN)
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
        if not re.fullmatch(r"(?:/(?:[^~]|~[01])*)*", self.pointer):
            raise ValueError("pointer requires JSON Pointer syntax")
        for name in ("result_file", "output_file"):
            if getattr(self, name) is not None and not getattr(self, name).startswith("/"):
                raise ValueError("file arguments require absolute paths")
        return self


RUN = TypeAdapter(RunArguments | RecoveryArguments)
TOOLS = {
    "bookflow_list_commands": (ListArguments, "Discover registered Bookflow commands and their scope."),
    "bookflow_help": (HelpArguments, "Read concise usage and complete input constraints by default. Select view=output_schema for complete output fields, input_schema for inputs, or full for both schemas and the entire command reference. All views include context and errors."),
    "bookflow_run": (RUN, "Run a discovered Bookflow command. Files use transport.input_file/output_file on the calling machine; the adapter handles all bytes. Recover an existing intent by its reference without resubmitting it."),
}


def tool_schema(name):
    """Presence/action rules are part of discoverable JSON Schema, not hidden validators."""
    if name != "bookflow_run":
        return TOOLS[name][0].model_json_schema()
    schema = RUN.json_schema()
    definitions = schema["$defs"]
    file_schema = {"type": "string", "minLength": 1, "pattern": "^/",
                   "description": "Absolute calling-machine business-file path beneath an operator-configured directory; never a host path or URL."}
    for field in ("input_file", "output_file", "input_json_file", "result_file"):
        definitions["Files"]["properties"][field] = deepcopy(file_schema)
    run = definitions["RunArguments"]
    run["properties"]["input"] = {"type": "object", "additionalProperties": True}
    run["allOf"] = [{"oneOf": [
        {"required": ["input"], "not": {"required": ["transport"], "properties": {"transport": {"required": ["input_json_file"]}}}},
        {"required": ["transport"], "properties": {"transport": {"required": ["input_json_file"]}}, "not": {"required": ["input"]}},
    ]}]
    recovery = definitions.pop("RecoveryArguments")
    branches = [{"$ref": "#/$defs/RunArguments"}]
    for action in ("execute", "status", "release", "inspect"):
        branch = deepcopy(recovery)
        allowed = {"operation_ref", "input_ref", "action"}
        if action == "execute":
            allowed |= {"result_file", "output_file"}
        if action == "inspect":
            allowed |= {"pointer", "limit", "cursor"}
        branch["properties"] = {key: value for key, value in branch["properties"].items() if key in allowed}
        branch["properties"]["action"] = {"const": action}
        for field in ("operation_ref", "input_ref", "cursor"):
            if field in allowed:
                branch["properties"][field] = {"type": "string", "minLength": 1}
                if field != "cursor":
                    branch["properties"][field].update(maxLength=128, pattern=REFERENCE_PATTERN)
        for field in ("result_file", "output_file"):
            if field in allowed:
                branch["properties"][field] = deepcopy(file_schema)
        if "pointer" in allowed:
            branch["properties"]["pointer"] = {"type": "string", "default": "", "pattern": r"^(?:/(?:[^~]|~[01])*)*$"}
        branch["oneOf"] = [{"required": ["operation_ref"]}, {"required": ["input_ref"]}]
        branches.append(branch)
    return {"type": "object", "$defs": definitions, "oneOf": branches}


def validate(name, arguments):
    entry = TOOLS.get(name)
    if entry is None:
        raise BookflowError("E_USAGE", message="Unknown MCP tool")
    try:
        if name != "bookflow_run" and isinstance(arguments, dict):
            unknown = set(arguments) - set(entry[0].model_fields)
            if unknown:
                raise BookflowError("E_USAGE", details={"arguments": sorted(unknown)})
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
            {"field": ".".join(map(str, item["loc"])), "problem": item["msg"]} for item in errors
        ]}) from None
