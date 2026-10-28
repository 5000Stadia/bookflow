"""The camera scanner's decoder order (R190, design/point-of-sale.md §7).

The host setting `scan_decoders` is a comma-separated list of decoder names, tried in order in the browser: the first
whose `available()` is true there runs. It comes from `bookflow serve --scan-decoders`, else the environment variable
BOOKFLOW_SCAN_DECODERS, else the default, and is rendered into every workbench page
(`<meta name="bookflow-scan-decoders">`). Nothing on the server knows how a decoder works: these are names only, and
each name is one adapter file under the workbench's static folder (`scan-decoder-<name>.js`).
"""
from __future__ import annotations

import os
import re

from bookflow.core.errors import BookflowError

ENV = "BOOKFLOW_SCAN_DECODERS"
DEFAULT = "native,zxing-wasm"
# The adapters the workbench ships. Adding one is one adapter file, its vendored files, and its name here.
KNOWN = ("native", "zxing-wasm")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def normalize(text: str, *, field: str = "scan_decoders") -> str:
    """`" zxing-wasm , native"` -> `"zxing-wasm,native"`; refuses an empty list, an unknown name or a repeat."""
    names = [part.strip().lower() for part in (text or "").split(",") if part.strip()]
    problem = None
    if not names:
        problem = "name at least one decoder"
    else:
        for name in names:
            if not _NAME.match(name) or name not in KNOWN:
                problem = f"unknown decoder {name!r}; the decoders are {', '.join(KNOWN)}"
                break
        else:
            if len(set(names)) != len(names):
                problem = "a decoder is named twice"
    if problem:
        raise BookflowError("E_VALIDATION", details={"fields": [{"field": field, "problem": problem}]})
    return ",".join(names)


def resolve(explicit: str | None = None) -> str:
    """The flag, else the environment, else the default; always normalized."""
    if explicit is not None and explicit.strip():
        return normalize(explicit)
    env = os.environ.get(ENV, "")
    if env.strip():
        return normalize(env, field=ENV)
    return DEFAULT
