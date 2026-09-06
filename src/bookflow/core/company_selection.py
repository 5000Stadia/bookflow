"""Read-only company preference lookup on the launcher machine."""

import os

from bookflow.core.config import Config, os_login
from bookflow.core.errors import BookflowError
from bookflow.storage.paths import resolve_data_root


def company_selection(scope, explicit=None, *, selection_root=None, login=None, command_name=None):
    if scope != "company":
        if explicit is not None:
            raise BookflowError("E_USAGE", details={"argument": "company", **({"command": command_name} if command_name is not None else {})})
        return None, "none"
    if explicit is not None:
        return explicit, "option"
    if value := os.environ.get("BOOKFLOW_COMPANY"):
        return value, "env"
    config = Config.load(resolve_data_root(selection_root) / "config.toml")
    default = (config.user_table(login or os_login()) or {}).get("default_company")
    if default is not None and not isinstance(default, str):
        raise BookflowError("E_CONFIG_INVALID", details={"field": "default_company"})
    return (default, "default") if default else (None, "none")
