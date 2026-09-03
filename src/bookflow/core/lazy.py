"""Import a module on first attribute access, so the CLI can build itself without loading SQLAlchemy."""

from __future__ import annotations

import importlib
from typing import Any


class LazyModule:
    def __init__(self, name: str):
        self.__dict__["_name"] = name
        self.__dict__["_mod"] = None

    def __getattr__(self, attr: str) -> Any:
        mod = self.__dict__["_mod"]
        if mod is None:
            mod = importlib.import_module(self.__dict__["_name"])
            self.__dict__["_mod"] = mod
        return getattr(mod, attr)


def lazy(name: str) -> Any:
    return LazyModule(name)
