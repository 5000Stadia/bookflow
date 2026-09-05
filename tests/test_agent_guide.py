"""The hand-authored agent journey is literal, classified, and executable."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from importlib import resources

from tests.test_row3_host import hosted, live  # noqa: F401


MARKER = re.compile(r"<!-- bookflow-example: (executable|illustrative) -->")
ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Fence:
    classification: str
    language: str
    body: str


def resource_text(name: str) -> str:
    return (
        resources.files("bookflow.documentation")
        .joinpath("resources")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def classified_fences(text: str) -> list[Fence]:
    lines = text.splitlines()
    fences: list[Fence] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        delimiter = line[:3]
        if delimiter not in ("```", "~~~"):
            index += 1
            continue
        assert line != delimiter, f"unexpected closing fence on line {index + 1}"
        language = line[3:].strip()
        assert language, f"untyped opening fence on line {index + 1}"
        marker = MARKER.fullmatch(lines[index - 1]) if index else None
        assert marker, f"unclassified opening fence on line {index + 1}"
        close = index + 1
        while close < len(lines) and lines[close] != delimiter:
            close += 1
        assert close < len(lines), f"unclosed fence on line {index + 1}"
        fences.append(Fence(marker.group(1), language, "\n".join(lines[index + 1:close]) + "\n"))
        index = close + 1
    return fences


def test_every_hand_authored_fence_is_classified():
    fences = []
    for name in ("concepts.md", "agent-guide.md", "reference-year.md"):
        fences.extend(classified_fences(resource_text(name)))
    assert [(fence.classification, fence.language) for fence in fences] == [
        ("illustrative", "python"),
        ("illustrative", "bash"),
        ("executable", "python"),
        ("illustrative", "bash"),
        ("illustrative", "sh"),
        ("illustrative", "sh"),
    ]


def test_agent_guide_covers_a_source_checkout_and_the_complete_trial_lifecycle():
    guide = resource_text("agent-guide.md")
    for expected in (
        "BOOKFLOW=(uv run --frozen --no-sync --project",
        "command -v bookflow",
        "[ -f uv.lock ]",
        "set -euo pipefail",
        'UV_PROJECT_ENVIRONMENT="$BOOKFLOW_TRIAL_ROOT/venv"',
        "printf 'export UV_PROJECT_ENVIRONMENT=%q",
        'uv sync --frozen --project "$BOOKFLOW_CHECKOUT"',
        '--no-sync --project "$BOOKFLOW_CHECKOUT"',
        '"${BOOKFLOW[@]}" init --json',
        '"${BOOKFLOW[@]}" demo reset --json',
        'export BOOKFLOW_STATE="$BOOKFLOW_TRIAL_ROOT/agent-guide.env"',
        "nohup",
        "chmod 600",
        'response.status == 200',
        'kill -INT "$BOOKFLOW_HOST_PID"',
        "To rerun it from the beginning",
        "company show",
        "audit tail",
        "items[0].seq == cursor",
        "audit show",
        "X-Bookflow-Client-Name",
        "Idempotency-Key",
    ):
        assert expected in guide


def test_literal_agent_guide_journey_against_real_host(hosted, live):
    executable = [
        fence for fence in classified_fences(resource_text("agent-guide.md"))
        if fence.classification == "executable"
    ]
    assert len(executable) == 1
    program = executable[0].body
    assert not ULID.search(program), "the executable journey must discover ids"
    assert "BOOKFLOW_URL" in program and "BOOKFLOW_TOKEN" in program

    imported_roots = set()
    for node in ast.walk(ast.parse(program)):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.partition(".")[0])
    assert imported_roots <= sys.stdlib_module_names

    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
        env={"BOOKFLOW_URL": live, "BOOKFLOW_TOKEN": hosted.secret},
    )
    assert completed.returncode == 0, completed.stderr
    receipt = json.loads(completed.stdout)
    assert receipt["company_id"] == hosted.company_id
    assert receipt["directive_code"].startswith("SI-")
    assert receipt["version"] >= 4
    assert receipt["cursor"] > 0
