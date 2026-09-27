"""How a list page lays out its rows, and what its controls read as changed.

Presentation only, and the workbench's own preference. Core list metadata keeps its defaults
(it also drives the CLI tables); here a list opens on the few columns a bookkeeper reads first,
and Customize list still adds any column. Nothing is computed: money and dates are shown as
the command returned them, through the display filters.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from bookflow.adapters.workbench.list_paging import CARRIED

# Parameters that are not list state: paging, and the notices a redirect lands with.
_NOT_STATE = (*CARRIED, "flash", "deleted")


def state(params: Any, defaults: Mapping[str, str], quiet: Iterable[str] = ()) -> dict[str, Any]:
    """Whether the list differs from how it opens, and how many filters are set.

    ``quiet`` names controls that change the list without filtering it (search, sort,
    columns): they show Reset but are not counted on the phone's Filters button.
    """
    quiet = set(quiet)
    changed = {key for key, value in params.multi_items()
               if key not in _NOT_STATE and value.strip() and value != defaults.get(key)}
    return {"changed": bool(changed), "filters": len(changed - quiet)}
